# Intent Router Harness 当前服务架构

本文记录当前服务的实际边界：这是一个 **意图识别 + spec 驱动 + skill 渐进式加载 + LLM planner + SSE 协议输出** 的服务。

当前没有 `primary/candidates` 输出，也没有 `candidate_intents` 输入。提槽规则放在 skill 中，服务层只负责 session 生命周期、任务运行态保存、prompt 渲染、LLM 调用和协议适配。

## 总体架构

```mermaid
flowchart LR
    Client[Client / Mock Assistant] -->|POST /api/v1/message\nstream=true| Ingress[Ingress\nai.intent-router.cc]
    Ingress --> ASGI[FastAPI ASGI\nasgi.py]
    ASGI --> Service[IntentRouterHarnessService\nservice.py]
    Service --> Assistant[AssistantProtocolService\nassistant_service.py]
    Assistant --> Session[InMemorySessionStore\nsession_store.py]
    Assistant --> Planner[LLMMessagePlanner\nplanner.py]
    Planner --> Runtime[PromptHarness\nruntime.py]
    Runtime --> Spec[Harness Spec\nexamples/finance-router-harness.toml]
    Runtime --> SkillLib[SkillLibrary\nskills.py]
    SkillLib --> Skill[finance-routing/SKILL.md\n提槽/领域规则]
    Planner --> LLM[OpenAI-compatible LLM\nllm.py]
    LLM --> Planner
    Planner --> Assistant
    Assistant -->|AssistantProtocolFrame[]| Service
    Service --> ASGI
    ASGI -->|SSE event: message / done| Client
```

## 请求处理流程

```mermaid
sequenceDiagram
    participant C as Client
    participant A as asgi.py
    participant S as service.py
    participant AP as assistant_service.py
    participant P as planner.py
    participant R as runtime.py
    participant SK as SKILL.md
    participant L as LLM
    participant SS as session_store.py

    C->>A: POST /api/v1/message, stream=true
    A->>S: handle_message(request)
    S->>AP: handle_message(request)
    AP->>SS: load(sessionId)
    SS-->>AP: SessionState + TaskRuntimeState
    AP->>P: plan_message(request, task_runtime_state)
    P->>R: render(surface=task_planning)
    R->>SK: progressive skill loading
    R-->>P: rendered prompt + loaded skill names
    P->>L: chat(messages)
    L-->>P: PlannerOutput JSON
    P-->>AP: validated PlannerOutput
    AP->>SS: save_task_state(updated task runtime)
    AP-->>S: protocol frames
    S-->>A: payload frames
    A-->>C: SSE message frames + done
```

## 模块图

```mermaid
flowchart TB
    subgraph API[API 层]
        asgi[asgi.py\nFastAPI / SSE]
        server[server.py\nstdlib HTTP fallback]
    end

    subgraph Service[服务编排层]
        service[service.py\n服务边界 / health / render / assistant / regression]
        factory[service_factory.py\n配置装配]
        config[config.py\n运行时配置]
    end

    subgraph Protocol[助手协议层]
        assistant[assistant_service.py\nsession + planner + frames]
        contracts[contracts.py\n请求/响应/PlannerOutput 模型]
        protocol[assistant_protocol.py\nSSE 协议校验]
        session[session_store.py\n内存 session]
    end

    subgraph Harness[Spec + Skill Runtime]
        runtime[runtime.py\nPromptHarness / surface render]
        schema[schema.py\nspec schema]
        skills[skills.py\nSkillLibrary / SKILL.md loader]
        spec[finance-router-harness.toml\nsurface / bindings]
        skill[finance-routing/SKILL.md\n意图边界 / 提槽规则]
    end

    subgraph LLM[LLM 调用层]
        planner[planner.py\nLLMMessagePlanner]
        llm[llm.py\nOpenAI-compatible client]
    end

    subgraph Tests[回归测试层]
        regression[regression.py\n回归用例加载和校验]
        cases[assistant_protocol_v0_5.json\n回归用例]
    end

    asgi --> service
    server --> service
    factory --> service
    config --> factory
    service --> assistant
    assistant --> contracts
    assistant --> session
    assistant --> planner
    planner --> runtime
    planner --> llm
    runtime --> schema
    runtime --> skills
    runtime --> spec
    skills --> skill
    service --> regression
    regression --> protocol
    regression --> cases
```

## 职责边界

| 层级 | 模块 | 职责 |
| --- | --- | --- |
| API | `asgi.py` | 暴露 `/api/v1/message`、`/api/v1/task/completion`、health、render、regression 接口；负责 SSE 输出。 |
| 服务 | `service.py` | 服务总入口，连接 prompt harness、assistant protocol、LLM、regression。 |
| 协议 | `assistant_service.py` | 读取 session 生命周期和 task runtime，调用 planner，把 `PlannerOutput` 转为协议帧。 |
| 会话 | `session_store.py` | 当前为进程内内存存储；session 只保存身份和 30 分钟空闲 TTL，任务状态在独立 `TaskRuntimeState` 中保存。 |
| Planner | `planner.py` | 渲染 `task_planning` surface，调用 LLM，校验 LLM JSON。 |
| Runtime | `runtime.py` | 根据 spec surface 和上下文渐进式加载 skill，生成 system/human prompt。 |
| Skill | `skills/finance-routing/SKILL.md` | 金融领域规则，包含 `AG_TRANS` 的提槽规则和槽位边界。 |
| LLM | `llm.py` | OpenAI-compatible chat completion client。 |
| 回归 | `regression.py` / `assistant_protocol.py` | 加载测试用例并校验 SSE 协议输出。 |

## 提槽位置

提槽规则在 `skills/finance-routing/SKILL.md` 中定义：

- `AG_TRANS` 必填槽位：`payee_name`、`amount`
- 短答 `"小明"` 这类文本填 `payee_name`
- 数字或金额表达 `"200"`、`"200元"` 填 `amount`
- 已有槽位从 `task_state_json` 中的当前任务运行态继承并合并
- 槽缺失时返回 `waiting_user_input`
- 槽齐且 `router_only` 时返回 `ready_for_dispatch`

服务层不硬编码正则提槽逻辑，只保存 LLM 返回的 `slot_memory`：

```python
slot_memory = dict(current_task.slot_memory)
slot_memory.update(plan.slot_memory)
```

## 部署视图

```mermaid
flowchart LR
    Browser[Local Client\nai.intent-router.cc] --> Tunnel[minikube tunnel\n127.0.0.1:80]
    Tunnel --> Ingress[ingress-nginx\nLoadBalancer]
    Ingress --> Service[ClusterIP Service\nintent-router-harness:8765]
    Service --> Pod[Deployment Pod\npython:3.12-slim]
    Pod --> Code[ConfigMap mounted code\napp.tar.gz]
    Pod --> Secret[Secret mounted env\n.env.local]
    Pod --> LLM[External LLM API]
```

## 关键日志

查看渐进式加载和 LLM 分析：

```bash
kubectl -n intent logs deploy/intent-router-harness -f | rg 'spec\.|llm\.plan'
```

典型日志：

```text
spec.progressive_load surface=task_planning metadata_skills=['finance-routing'] body_skills=['finance-routing']
spec.loaded_skill_body surface=task_planning skill=finance-routing ...
llm.plan.prompt_rendered session_id=...
llm.plan.raw_response session_id=... content=...
llm.plan.parsed_json session_id=...
llm.plan.validated session_id=... status=waiting_user_input intent_code=AG_TRANS
```
