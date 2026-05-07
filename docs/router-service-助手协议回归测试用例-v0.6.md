# router-service 助手协议回归测试用例 v0.6

更新时间：2026-05-07

## 1. 版本定位

本版本基于当前 `intent_router_harness` 项目重新整理。旧版 v0.5 只作为协议格式和历史场景参考，不再沿用旧 router-service 的 backend 路径、`primary/candidates`、`candidate_intents`、agent 代理透传等概念。

当前服务的核心验收对象是：

```text
agent.md 根指令
+ spec surface
+ skill metadata / skill body / reference 渐进式加载
+ LLM planner 结构化输出
+ task-first 运行态
+ SSE 助手协议输出
```

本版本测试目标：

1. 主链路以 `POST /api/v1/message` + `stream=true` 验证。
2. 非流式 `stream=false` 必须与流式最终业务帧语义一致。
3. `POST /api/v1/task/completion` 负责模拟下游任务完成，并验证任务上下文释放。
4. session 只作为用户绑定和 30 分钟 idle TTL，不参与任务核心状态建模。
5. 槽位规则来自 skill，不允许通过服务层正则、关键词兜底或 hard code 修补。
6. debugTrace 用于观察 agent、spec、skill、reference、LLM 分析和上下文释放。

## 2. 当前接口基线

### 2.1 消息入口

```http
POST /api/v1/message
```

请求基线：

```json
{
  "sessionId": "assistant_case_001",
  "txt": "我要转账",
  "stream": true,
  "debugTrace": false,
  "executionMode": "router_only",
  "custId": "C0001",
  "config_variables": [
    {"name": "custID", "value": "C0001"},
    {"name": "sessionID", "value": "assistant_case_001"},
    {"name": "currentDisplay", "value": "transfer_page"},
    {"name": "agentSessionID", "value": "assistant_case_001"}
  ],
  "recommendTask": [],
  "currentDisplay": []
}
```

流式响应：

```text
event: message
data: {...}

event: done
data: [DONE]
```

当 `debugTrace=true` 且 `stream=true` 时，允许在业务 `message` 前输出：

```text
event: trace
data: {...}
```

非流式响应只返回最终业务帧，不返回识别前置帧和 trace。

### 2.2 任务完成回调

```http
POST /api/v1/task/completion
```

请求基线：

```json
{
  "sessionId": "assistant_case_001",
  "taskId": "task_001",
  "completionSignal": 2,
  "stream": true,
  "debugTrace": false
}
```

`completionSignal`：

| 值 | 含义 | 期望状态 |
| --- | --- | --- |
| `1` | 阶段性完成 | `waiting_assistant_completion` / `assistant_stage_done` |
| `2` | 最终完成 | `completed` / `assistant_final_done` |

### 2.3 探活与验证 UI

| 接口 | 作用 | 验收 |
| --- | --- | --- |
| `GET /healthz` | 进程存活 | 返回 `{"status":"ok"}` |
| `GET /readyz` | 服务就绪 | 返回 `ready=true` 和 `llm_configured` |
| `GET /`、`GET /validator` | 浏览器验证台 | 可以发起流式消息和完成回调 |

## 3. 协议断言规则

### 3.1 SSE 帧顺序

可识别业务消息的流式响应必须满足：

1. 可选多个 `trace` 帧，仅在 `debugTrace=true` 时出现。
2. 第一个业务 `message` 帧是意图识别帧。
3. 最后一个业务 `message` 帧是当前轮最终业务状态。
4. 最后一帧必须是 `event: done` / `data: [DONE]`。

意图识别帧：

```json
{
  "ok": true,
  "status": "running",
  "intent_code": "AG_TRANS",
  "completion_state": 0,
  "completion_reason": "intent_recognized",
  "stage": "intent_recognition",
  "output": {},
  "slot_memory": {},
  "task_list": []
}
```

业务状态帧：

```json
{
  "ok": true,
  "status": "waiting_user_input",
  "intent_code": "AG_TRANS",
  "completion_state": 0,
  "completion_reason": "router_waiting_user_input",
  "slot_memory": {},
  "message": "请提供收款人和转账金额",
  "output": {},
  "task_list": [
    {
      "taskId": "task_001",
      "intent_code": "AG_TRANS",
      "status": "waiting_user_input",
      "slot_memory": {}
    }
  ],
  "current_task": {
    "taskId": "task_001",
    "intent_code": "AG_TRANS",
    "status": "waiting_user_input",
    "slot_memory": {}
  }
}
```

### 3.2 禁止旧字段

当前协议不应依赖或要求以下旧字段：

```text
primary
candidates
candidate_intents
primary_intents
```

如 trace 里展示 LLM 原始分析，也不应把这些旧字段作为业务协议必需结构。

### 3.3 状态枚举

所有 `status` 只能使用：

```text
running
waiting_user_input
ready_for_dispatch
waiting_assistant_completion
completed
cancelled
failed
```

不得出现：

```text
pending
queued
todo
incomplete
input_required
```

## 4. 测试场景矩阵

| ID | 优先级 | 场景 | 主接口 | 核心断言 | 自动化现状 |
| --- | --- | --- | --- | --- | --- |
| TC-H01 | P0 | 探活与就绪 | `/healthz`、`/readyz` | health ok，ready true，暴露 llm_configured | 已覆盖 |
| TC-H02 | P0 | 验证台可访问 | `/`、`/validator` | 返回 HTML，包含 SSE 调用与完成按钮 | 已覆盖 |
| TC-M01 | P0 | SSE 意图识别前置帧 | `/api/v1/message` | 识别帧先于业务帧，最后 done | 已覆盖 |
| TC-M02 | P0 | 非流式返回最终业务帧 | `/api/v1/message` | `stream=false` 只返回最终业务状态 | 已覆盖 |
| TC-M03 | P0 | 流式与非流式语义一致 | `/api/v1/message` | 最终 `status/slot_memory/output` 一致 | 待结构化 |
| TC-D01 | P0 | debugTrace 渐进式加载可观测 | `/api/v1/message` | trace 包含请求、session、spec、skill、LLM、业务结果 | 已覆盖核心 |
| TC-D02 | P0 | 非 debug 不输出 trace | `/api/v1/message` | `debugTrace=false` 只有 message/done | 待结构化 |
| TC-SK01 | P0 | agent.md 默认加载 | prompt render | 每次请求加载根指令 | 已覆盖 |
| TC-SK02 | P0 | skill metadata 用于场景选择 | prompt render / trace | scene selection 只依赖 metadata，不加载所有 skill body | 已覆盖 |
| TC-SK03 | P0 | 命中后加载 skill body | `/api/v1/message` | task_planning 加载 `finance-routing` 正文 | 已覆盖 |
| TC-SK04 | P0 | 无 description 的下层 skill 不进入识别索引 | prompt render | 只能显式加载 | 已覆盖 |
| TC-SK05 | P0 | 一个 skill 不允许多个 intent_code | skill load | 加载失败 | 已覆盖 |
| TC-SK06 | P1 | reference 只能由已加载 skill 授权加载 | prompt render | 合法 reference 可加载，非法 id 拒绝 | 已覆盖 |
| TC-R01 | P0 | 首轮转账缺槽 | `/api/v1/message` | `我要转账` -> `waiting_user_input`，询问收款人和金额 | 已真实验证 |
| TC-R02 | P0 | 短人名补槽 | `/api/v1/message` | `我要转账` -> `小明`，写入 `payee_name`，继续问金额 | 待结构化 |
| TC-R03 | P0 | 金额补槽后 ready | `/api/v1/message` | 已有收款人后输入 `200`，进入 `ready_for_dispatch` | 已覆盖 |
| TC-R04 | P0 | 同句补多个槽位 | `/api/v1/message` | 任意收款人 + 任意金额表达一次补齐 | 已真实验证 |
| TC-R05 | P0 | 槽位纠错覆盖 | `/api/v1/message` | 用户修正收款人或金额，只更新有依据槽位 | 待结构化 |
| TC-R06 | P0 | 不用服务层正则兜底 | 源码 / 行为 | 槽位规则只在 skill/spec/prompt 中表达 | 待结构化 |
| TC-MT01 | P0 | 多转账意图拆多个任务 | `/api/v1/message` | 两笔转账生成两个 `AG_TRANS` task，当前任务为第一笔 | 已覆盖 |
| TC-MT02 | P0 | 多任务顺序补槽 | `/api/v1/message` | “第一次给100元”补到 `task_list[0]` | 已覆盖 |
| TC-MT03 | P0 | 同一时刻只有一个 current_task | `/api/v1/message` | 后续任务保留在 task_list，不并行推进 | 已覆盖 |
| TC-MT04 | P0 | 当前任务完成后推进下一任务 | `/api/v1/task/completion` | 返回 completed 后继续下一个 waiting/ready task | 已覆盖 |
| TC-C01 | P0 | 单任务最终完成释放上下文 | `/api/v1/task/completion` | runtime task_list/current_task/slot_memory/context 清空 | 已覆盖 |
| TC-C02 | P0 | 重复完成回调拒绝 | `/api/v1/task/completion` | 第二次同 taskId 返回 `TASK_NOT_FOUND` | 已覆盖 |
| TC-C03 | P1 | 阶段性完成不清理任务 | `/api/v1/task/completion` | `completionSignal=1` 仍等待助手完成 | 待结构化 |
| TC-C04 | P1 | 取消任务释放上下文 | `/api/v1/message` | `取消/不转了` -> `cancelled`，清理 runtime | 已覆盖单元 |
| TC-SE01 | P0 | session 用户绑定 | `/api/v1/message` | 同 session 不允许换用户 | 已覆盖 |
| TC-SE02 | P0 | session idle 30 分钟过期 | session store | 过期后任务运行态清空 | 已覆盖 |
| TC-SE03 | P0 | session 与任务状态解耦 | session store / message | session 不保存 task 状态，task runtime 独立 | 已覆盖 |
| TC-REC01 | P1 | 推荐任务只作用当前轮 router | `/api/v1/message` | 未采纳推荐时不污染 task_state | 待结构化 |
| TC-ERR01 | P0 | LLM planner 失败 | `/api/v1/message` | 返回 `failed/router_error`，不先发成功识别帧 | 已覆盖旧协议校验 |
| TC-ERR02 | P0 | LLM 输出未声明 intent_code | planner | 拒绝未由 loaded skill 声明的 intent | 已覆盖 |

## 5. 详细测试用例

### TC-M01 SSE 意图识别前置帧

请求：

```json
{
  "sessionId": "tc_m01",
  "txt": "我要转账",
  "stream": true,
  "executionMode": "router_only",
  "custId": "C0001"
}
```

期望：

1. 至少两个 `message` 帧。
2. 第一个业务帧：
   - `stage="intent_recognition"`
   - `status="running"`
   - `completion_reason="intent_recognized"`
   - `intent_code="AG_TRANS"`
3. 最终业务帧：
   - `status="waiting_user_input"`
   - `completion_reason="router_waiting_user_input"`
   - `slot_memory={}`
   - `output={}`
4. 结束帧为 `[DONE]`。

### TC-M02 非流式返回最终业务帧

请求与 TC-M01 相同，但：

```json
{
  "stream": false
}
```

期望：

1. HTTP JSON 直接返回最终业务帧。
2. 不返回 `stage="intent_recognition"` 的前置识别帧。
3. 最终业务语义与 TC-M01 最后一条业务帧一致。

### TC-D01 debugTrace 渐进式加载可观测

请求：

```json
{
  "sessionId": "tc_d01",
  "txt": "我要转账",
  "stream": true,
  "debugTrace": true,
  "executionMode": "router_only"
}
```

期望 trace 至少包含：

| stage | 断言 |
| --- | --- |
| `request_received` | 记录 session、stream、executionMode、txt |
| `session_loaded` | 记录 session 生命周期 |
| `task_runtime_loaded` | 记录 task_count、current_task、slot_memory |
| `agent_context_loaded` | 记录 `agent.md` 加载 |
| `spec_progressive_load` | 记录 metadata skill、body skill、reference |
| `scene_skill_selected` | LLM 选择 `finance-routing` |
| `skill_body_loaded` | task_planning 加载 `finance-routing/SKILL.md` |
| `llm_raw_response` | 展示 LLM 原始结构化 JSON |
| `llm_analysis` | 展示解析后的 intent/status/slot/task |
| `slot_and_skill_result` | 展示最终补槽或交接结果 |
| `assistant_protocol_frames` | 展示业务帧数量和最终状态 |

业务 `message` 帧仍必须满足 TC-M01。

### TC-SK02 skill metadata 用于场景选择

目标：验证第一层只用 `name + description` 做场景 skill 选择。

步骤：

1. 在测试 harness 中准备两个 skill：
   - `finance-routing`：有 `description`，声明 `AG_TRANS`。
   - `hidden-helper`：无 `description`，不应出现在识别索引。
2. 渲染 `scene_selection`。

期望：

1. `metadata_skills` 只包含有 description 且匹配 surface/domain/capability 的 skill。
2. `loaded_skills` 为空。
3. prompt 中不包含任意 skill body。

### TC-SK03 命中后加载 skill body

请求：

```json
{
  "sessionId": "tc_sk03",
  "txt": "我要转账",
  "stream": true,
  "debugTrace": true,
  "executionMode": "router_only"
}
```

期望：

1. scene selection 选择 `finance-routing`。
2. task_planning 的 `spec_progressive_load` 中：
   - `metadata_skills` 包含 `finance-routing`
   - `loaded_skill_bodies` 包含 `finance-routing`
   - `available_references` 包含 `ref_001`
3. 不加载未授权 reference body，除非 planner 显式请求。

### TC-R01 首轮转账缺槽

请求：

```json
{
  "sessionId": "tc_r01",
  "txt": "我要转账",
  "stream": true,
  "executionMode": "router_only"
}
```

期望最终业务帧：

```json
{
  "status": "waiting_user_input",
  "intent_code": "AG_TRANS",
  "completion_reason": "router_waiting_user_input",
  "slot_memory": {},
  "output": {}
}
```

`message` 应只询问缺失槽位，不给出虚假执行结果。

### TC-R02 短人名补槽

步骤：

1. `sessionId=tc_r02` 输入 `我要转账`。
2. 同一 session 输入 `小明`。

期望第二轮最终业务帧：

```json
{
  "status": "waiting_user_input",
  "completion_reason": "router_waiting_user_input",
  "slot_memory": {
    "payee_name": "小明"
  }
}
```

`message` 只询问金额，不再重复询问收款人。

### TC-R03 金额补槽后 ready

步骤：

1. `sessionId=tc_r03` 输入 `我要转账`。
2. 输入 `小明`。
3. 输入 `200元`。

期望第三轮最终业务帧：

```json
{
  "status": "ready_for_dispatch",
  "completion_reason": "router_ready_for_dispatch",
  "slot_memory": {
    "payee_name": "小明",
    "amount": "200"
  },
  "output": {
    "ishandover": true,
    "handOverReason": "router_only_ready_for_dispatch"
  }
}
```

### TC-R04 同句补多个槽位

步骤：

1. `sessionId=tc_r04` 输入 `我要转账`。
2. 同一 session 输入以下任意表达之一：
   - `给赵六转200元`
   - `转10000元给刘京生`
   - `给客户甲打款三百元`

期望第二轮最终业务帧：

1. `status="ready_for_dispatch"`。
2. `completion_reason="router_ready_for_dispatch"`。
3. `slot_memory.payee_name` 来自明确收款人实体。
4. `slot_memory.amount` 为不带单位的金额字符串。
5. 不得只写入收款人后继续询问金额。

本用例验证通用提示词规则，不允许靠服务层正则或固定样例满足。

### TC-R05 槽位纠错覆盖

步骤：

1. 输入 `我要转账`。
2. 输入 `小刚`。
3. 输入 `收款人改成小红`。
4. 输入 `200`。

期望：

1. 第三轮后 `payee_name="小红"`。
2. 第四轮后 `slot_memory={"payee_name":"小红","amount":"200"}`。
3. 纠错只更新用户明确修正的槽位，不清空无关槽位。

### TC-MT01 多转账意图拆多个任务

请求：

```json
{
  "sessionId": "tc_mt01",
  "txt": "我要先给王阳明转账，再给李正义转账",
  "stream": true,
  "executionMode": "router_only"
}
```

期望最终业务帧：

1. `status="waiting_user_input"`。
2. `task_list` 至少两个任务，且顺序与用户表达一致。
3. `task_list[0].intent_code="AG_TRANS"`，`slot_memory.payee_name="王阳明"`。
4. `task_list[1].intent_code="AG_TRANS"`，`slot_memory.payee_name="李正义"`。
5. `current_task.taskId == task_list[0].taskId`。
6. 同一时刻只询问当前任务缺失金额。

### TC-MT02 多任务顺序补槽

前置：TC-MT01 后继续同一 session。

输入：

```text
第一次给100元
```

期望：

1. 金额写入 `task_list[0].slot_memory.amount="100"`。
2. 不写入第二个任务。
3. 当前任务进入 `ready_for_dispatch`。
4. 顶层 `slot_memory` 表示当前任务槽位。

### TC-MT04 当前任务完成后推进下一任务

前置：当前 session 中：

```json
{
  "task_list": [
    {
      "taskId": "task_001",
      "status": "ready_for_dispatch",
      "slot_memory": {"payee_name": "王阳明", "amount": "100"}
    },
    {
      "taskId": "task_002",
      "status": "waiting_user_input",
      "slot_memory": {"payee_name": "李正义"}
    }
  ],
  "current_task": {"taskId": "task_001"}
}
```

请求：

```json
{
  "sessionId": "tc_mt04",
  "taskId": "task_001",
  "completionSignal": 2,
  "stream": true
}
```

期望 SSE：

1. 第一条业务帧：
   - `status="completed"`
   - `completion_reason="assistant_final_done"`
   - `current_task.taskId="task_001"`
2. 第二条业务帧：
   - `status="waiting_user_input"`
   - `completion_reason="router_waiting_user_input"`
   - `current_task.taskId="task_002"`
   - `slot_memory.payee_name="李正义"`
3. 保存后的 runtime 只保留未完成任务。

### TC-C01 单任务最终完成释放上下文

步骤：

1. 创建一个 `ready_for_dispatch` 或 `waiting_assistant_completion` 的转账任务。
2. 调用 `/api/v1/task/completion`，`completionSignal=2`。

期望：

1. 响应 `completed`。
2. 保存后的 `TaskRuntimeState`：
   - `task_list=[]`
   - `current_task=null`
   - `slot_memory={}`
   - `active_context={}`
   - `context_leases=[]`
3. debugTrace 中出现 `context_released`。

### TC-C02 重复完成回调拒绝

步骤：

1. 对当前任务调用 `completionSignal=2`。
2. 再次使用相同 `taskId` 调用完成回调。

期望第二次返回：

```json
{
  "ok": false,
  "status": "failed",
  "completion_reason": "assistant_task_not_found",
  "errorCode": "TASK_NOT_FOUND"
}
```

### TC-SE01 session 用户绑定

步骤：

1. `sessionId=tc_se01`，`cust_no=cust_001` 输入 `我要转账`。
2. 同一 `sessionId`，`cust_no=cust_002` 再次请求。

期望：

1. 第二次请求被拒绝。
2. HTTP 层返回 `403`，错误码 `session_user_mismatch`。
3. 不合并两个用户的任务状态。

### TC-SE02 session idle 过期清理

步骤：

1. 构造 session 内存在等待补槽任务和 context lease。
2. 时间推进到最近活动时间 30 分钟之后。
3. 再次加载该 session。

期望：

1. `expired=true`。
2. `TaskRuntimeState` 全部清空。
3. 相同 session 后续请求按新会话处理。

### TC-ERR01 LLM planner 失败

前置：模拟 LLM 请求失败、JSON 不可解析或 planner validation 失败。

期望：

```json
{
  "ok": false,
  "status": "failed",
  "completion_state": 2,
  "completion_reason": "router_error",
  "errorCode": "ROUTER_PLANNER_ERROR"
}
```

不得先输出误导性的 `intent_recognized` 成功帧。

### TC-ERR02 LLM 输出未声明 intent_code

前置：

1. 已加载 skill 只声明 `AG_TRANS`。
2. LLM 输出 `intent_code="AG_BALANCE"`。

期望：

1. planner 拒绝该输出。
2. 返回失败，不创建未授权任务。
3. 日志或 trace 可定位为 intent validation failure。

## 6. 自动化命令

当前项目默认自动化：

```bash
PYTHONPATH=src pytest -q
```

查看旧版结构化 suite：

```bash
PYTHONPATH=src python -m intent_router_harness show-suite regressions/assistant_protocol_v0_5.json
```

启动本地 ASGI 服务：

```bash
PYTHONPATH=src python -m intent_router_harness serve-asgi --host 127.0.0.1 --port 8877
```

打开验证台：

```text
http://127.0.0.1:8877/validator
```

真实 LLM smoke 需要 `.env.local`：

```bash
PYTHONPATH=src python -m intent_router_harness llm-smoke --env-file .env.local
```

## 7. 下一步结构化建议

建议新增 `regressions/assistant_protocol_v0_6.json`，按本文 P0 用例优先结构化：

1. `TC-M01`、`TC-M02`、`TC-M03`：锁定流式/非流式协议语义。
2. `TC-D01`、`TC-D02`：锁定 debugTrace 可观测性和默认无 trace。
3. `TC-R01` 到 `TC-R04`：锁定转账单任务补槽主链路。
4. `TC-MT01`、`TC-MT02`、`TC-MT04`：锁定多任务串行推进。
5. `TC-C01`、`TC-C02`：锁定任务完成与上下文释放。
6. `TC-SE01`、`TC-SE02`：锁定 session 生命周期和用户隔离。
7. `TC-ERR01`、`TC-ERR02`：锁定失败路径。

旧 `assistant_protocol_v0_5.json` 暂不删除，作为历史兼容校验；新开发和验收以 v0.6 文档为准。
