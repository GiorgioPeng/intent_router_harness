# intent_router_harness 设计说明

## 背景

`intent_router_harness` 是一个独立项目，用来沉淀新的意图识别 harness 能力。

它不直接依赖、导入、修改或运行 `intent_router/router-service`。生产服务可以作为理解意图识别问题域的参考，但本项目的边界是独立的 spec、skill、prompt surface、eval case 和 harness runtime。

当前目标不是把旧服务包装一层，而是先建立一个可演进的 harness 基础框架：

- 用 spec 描述意图识别、槽位抽取、图规划等 prompt surface。
- 用 DeepAgents 风格的 `SKILL.md` 管理可渐进加载的领域能力。
- 用明确的 runtime context 决定哪些 skill 只展示 metadata，哪些 skill 加载完整正文。
- 让渲染结果保持框架无关，后续可以接 OpenAI、LangChain、DeepAgents、离线 eval runner 或生产实验 runner。

## 借鉴 DeepAgents 的点

本项目借鉴的是 DeepAgents 的 harness 模式，而不是把所有能力直接绑死到 DeepAgents runtime 上。

### 1. Harness 作为能力实验边界

DeepAgents 的 better-harness 思路里，harness 负责定义可编辑 surface、variant、eval case 和运行布局。Agent 可以在受控 workspace 中提出修改，再通过测试或评估反馈筛选。

这里对应为：

- `SurfaceSpec`：一个可独立演进的 prompt surface。
- `HarnessSpec`：一组 surface、skill root 和 binding 的版本化定义。
- `Variant`：未来用于比较不同 spec 版本。
- `EvalCase`：未来用于描述输入、上下文、期望输出和标签。

### 2. Skill 渐进式加载

DeepAgents skill middleware 的核心经验是：先让模型看到 skill metadata，只在需要时加载完整 `SKILL.md`。

这里对应为：

- `skills/*/SKILL.md`：每个 skill 带 frontmatter metadata。
- `SkillLibrary.matching_metadata()`：按 surface、intent、domain、capability 选择可见 skill metadata。
- `SkillBinding`：决定哪些 skill 在某个上下文中加载完整 body。
- `PromptHarness.render()`：把 metadata index 和已加载 skill body 注入到 system prompt。

这样可以避免所有规则一次性塞进 prompt，也让能力能按领域、意图、阶段逐步增长。

### 3. Spec 驱动而不是代码驱动

DeepAgents harness 的价值在于把“实验对象”变成文件化 surface，而不是散落在业务代码中。

这里选择 TOML spec：

- prompt surface 可版本化。
- skill binding 可审查。
- offline agent 可以只编辑 spec 和 skill 文件。
- eval runner 可以加载任意 spec variant 做对比。

## 为什么当前不直接用 DeepAgents 框架实现全部 runtime

当前实现选择先做一个轻量、框架无关的 harness core，原因是：

- 这个项目首先要沉淀 intent router 的 spec、skill 和 eval 资产。
- 生产意图识别的输出 contract 必须稳定，核心 prompt 渲染需要可测试、可复用、可移植。
- DeepAgents 更适合作为外层自动改进者：读取失败案例，编辑 spec/skill，运行 eval，提交 proposal。
- 如果一开始把 prompt 渲染和 DeepAgents agent loop 绑死，后续接其他 runner 或生产实验会变困难。

因此当前架构是：

- 内核：`intent_router_harness` 自己负责 spec、skill、render。
- 外层：未来可以用 DeepAgents agent 驱动 spec/skill 的自动迭代。

换句话说，当前是“以 harness 为主”的框架实现，DeepAgents 是被借鉴和可接入的 agent/harness 编排层，不是必须常驻的 runtime 依赖。

## 当前实现

### 文件结构

```text
intent_router_harness/
  README.md
  pyproject.toml
  docs/
    DESIGN.md
  examples/
    finance-router-harness.toml
  regressions/
    assistant_protocol_v0_5.json
  skills/
    finance-routing/
      SKILL.md
  src/intent_router_harness/
    __init__.py
    __main__.py
    assistant_protocol.py
    llm.py
    regression.py
    runtime.py
    schema.py
    service.py
    server.py
    skills.py
  tests/
    test_assistant_protocol_regression.py
    test_prompt_harness.py
    test_service.py
```

### 核心模型

`schema.py` 定义数据结构：

- `HarnessSpec`：完整 harness spec。
- `SurfaceSpec`：一个 prompt surface。
- `SkillBinding`：skill body 加载规则。
- `HarnessContext`：一次 render 的匹配上下文。
- `EvalCase`、`Variant`、`ExperimentSpec`：为后续 eval 和 variant 对比预留。

`skills.py` 负责加载 skill：

- 扫描配置的 skill root。
- 读取一层目录下的 `SKILL.md`。
- 解析简单 frontmatter。
- 支持按 `surfaces`、`intent_codes`、`domain_codes`、`capabilities` 匹配。

`runtime.py` 负责渲染 prompt：

- `load_prompt_harness()` 从 TOML 加载 spec。
- `PromptHarness.render()` 根据 surface 和 context 渲染 prompt。
- `RenderedPrompt.messages()` 输出 OpenAI 风格 chat messages。

`assistant_protocol.py` 和 `regression.py` 负责助手协议回归：

- `parse_sse_text()` 解析 `event: message` / `event: done` transcript。
- 协议断言区分识别帧和业务帧。
- `load_regression_suite()` 加载结构化 v0.5 回归用例。
- `validate_step_transcript()` 将真实或 mock SSE transcript 套用到 case step。

`service.py` 和 `server.py` 负责服务层：

- `IntentRouterHarnessService` 封装 health、surface 查询和 prompt render。
- `RenderPromptRequest` / `RenderPromptResponse` 定义稳定的服务边界数据结构。
- `server.py` 用 Python 标准库暴露 `GET /health`、`GET /surfaces` 和 `POST /render`。
- `POST /render` 根据请求体里的 `stream` 判断响应形态：`true` 返回 SSE，`false` 返回普通 JSON。
- `POST /llm/render` 在服务层执行 render 后调用配置的 OpenAI-compatible LLM，并按 `stream` 返回 JSON 或 SSE。
- `POST /api/v1/message` 和 `POST /api/v1/task/completion` 暴露 task-first assistant protocol 服务入口。
- 服务层同时暴露 `GET /regression/suite`、`GET /regression/cases/{case_id}` 和 `POST /regression/validate`，用于复用同一份助手协议回归 suite 校验外部 SSE transcript。
- `python -m intent_router_harness serve ...` 可以在本地直接启动 HTTP 服务。
- `python -m intent_router_harness serve-asgi ...` 使用 FastAPI/Uvicorn 启动部署入口。

`contracts.py`、`planner.py`、`session_store.py` 和 `assistant_service.py` 负责 task-first 协议运行层：

- `contracts.py` 定义请求、planner output、task、session 和 assistant protocol frame。
- `planner.py` 用 `task_planning` surface 将 spec/skill 渲染为 LLM planner prompt，并只接受结构化 JSON。
- `session_store.py` 提供最小内存 session 状态。
- `assistant_service.py` 将 planner output 适配为助手协议帧；`task_list` / `current_task` 是主结构，`graph` 只作为可选扩展。

## 渲染流程

1. 调用方选择 surface，例如 `intent_recognition`。
2. 调用方传入变量，例如 message、candidate intents、recommend tasks。
3. harness 根据 surface 读取 `SurfaceSpec`。
4. harness 用 runtime context 匹配 skill metadata。
5. harness 根据 `SkillBinding` 决定是否加载完整 skill body。
6. harness 拼出 system prompt 和 human prompt。
7. 调用方把 `RenderedPrompt.messages()` 交给任意 LLM client 或 eval runner。

## 示例 spec

`examples/finance-router-harness.toml` 当前定义了三个 surface：

- `intent_recognition`
- `slot_extraction`
- `graph_planning`

并将 `finance-routing` skill 绑定到 finance domain 下的这三个 surface。

## 当前已验证能力

测试覆盖了以下关键行为：

- skill body 只有在 binding 命中时才加载。
- 未传入的模板变量会保留原样，便于分阶段填充和调试。
- 助手协议 v0.5 回归用例可以加载并验证 SSE transcript。
- 服务层可以直接调用，并可通过本地 HTTP `POST /render` 返回流式或非流式渲染结果。
- 服务层可以查询回归 suite，并对单 step 或整 case 的 SSE transcript 执行协议校验。
- `/api/v1/message` 可以通过 spec-driven planner 产生识别帧和业务帧。
- `/api/v1/task/completion` 可以确认当前任务，并通过 session state 更新协议状态。

运行方式：

```bash
python -m pytest -q
```

## 还未实现的部分

当前还没有实现：

- DeepAgents 外层 agent loop。
- variant 自动对比 runner。
- eval case 文件加载和评分器。
- 与任何生产服务的运行时集成。
- prompt 输出 JSON schema 的自动校验。
- 真实 HTTP SSE external runner。

这些是下一步可以继续落地的内容。

## 下一步建议

1. 增加 `experiments/`：定义 variants、cases、scorers。
2. 增加 `runners/`：实现本地 LLM runner、mock runner 和 JSON contract validator。
3. 增加 DeepAgents adapter：让 agent 只编辑 `examples/`、`skills/`、`experiments/`，再跑 eval。
4. 增加更多 domain skills：从 finance 扩展到客服、营销、任务推荐等领域。
5. 将 `RenderedPrompt` 的 contract 扩展为可声明输出 schema 和 parser。
