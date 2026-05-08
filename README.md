# AgentScope Router Intent

一个面向助手入口的通用意图识别 Router 服务。它使用 AgentScope 的 DashScope/Qwen
model/formatter 作为结构化 LLM 适配层，业务推进由服务端状态机控制：按需加载 skill body
和 reference、任务隔离、session TTL、debug trace、流式/非流式一致。

## Quick Start

```bash
conda create -n agentscope-router-py312 python=3.12 -y
conda activate agentscope-router-py312
pip install -e ".[dev]"
```

启动服务：

```bash
export DASHSCOPE_API_KEY=sk-e397438bb5ae45debf9bb46625e500b6
export DASHSCOPE_MODEL=qwen-plus
export DASHSCOPE_BASE_HTTP_API_URL=https://dashscope.aliyuncs.com/api/v1
export CONFIG_SOURCE_BASE_URL=http://localhost:18080


export export DASHSCOPE_MODEL=qwen3.6-35b-a3b
export DASHSCOPE_BASE_HTTP_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
uvicorn router_app.main:app --reload
```

也支持等价别名：`QWEN_API_KEY`、`QWEN_MODEL`、`QWEN_BASE_HTTP_API_URL`。

本地 mock 配置库：

```bash
uvicorn examples.mock_config_server:app --port 18080
```

mock 配置库默认扫描平级目录 `examples/mock_skills/<skill_name>/SKILL.md`，便于 `os.listdir`
直接检索。`SKILL.md` 只使用标准 frontmatter：`name`、`description` 加 Markdown body。
结构化槽位契约放在同级 `references/slot_contract.json`，handoff 契约放在
`references/handoff_contract.json`，普通参考资料放在 `references/*.md`。`follow_up` 是内部完成后
追问 SKILL，可按 `skill_follow_up` 加载，但不进入意图识别 index。

测试：

```bash
pytest
```

## HTTP Interfaces

- `POST /api/v1/message`
- `POST /api/v1/task/completion`
- `GET /healthz`
- `GET /readyz`

`/api/v1/message` 默认返回精简业务帧：当前任务 `currentTask`、消息、状态和可交接 payload。
同时始终返回 `todoList`，用于命令行或前端按顺序展示多意图任务。历史任务详情 `tasks` 和
`trace` 仅在请求传 `debugTrace=true` 时填充，便于调试。
`/debug` 页面支持自动执行 TODO：接口返回 `handoff_ready` 时会自动准备 handoff 并提交 completion，
继续推进下一个 waiting task；遇到 `collecting_slots` 会停止等待用户补充槽位。

SSE 流式响应使用独立事件：

- `message`：业务帧
- `trace`：debug trace，仅 `debugTrace=true`
- `done`：最终业务帧

## External Skill Config Contract

配置库需要提供：

- `GET /v1/router/skills/index`，支持 `ETag` / `If-None-Match`
- `GET /v1/router/skills/{skillId}/body?version=...`
- `GET /v1/router/references/{referenceKey}?version=...`

LLM 上下文采用 AgentScope 风格的渐进式披露：首轮只暴露所有一级 SKILL 的 `name` 和
`description`，不向模型暴露 `skillId`、`intentCode`、`summary`、`priority`、`version` 等内部路由字段。
模型命中某个 SKILL name 后，服务端再映射到内部 task；进入当前任务补槽阶段后，才额外加载该
SKILL 的正文内容和 `references/slot_contract.json`。
每次调用 `/api/v1/message` 时，服务端会在 uvicorn 日志中打印 `message.full_context`：
request、`availableSkills`、session、`skillBodyLoadPolicy` 和 `loadedSkillBodies`。首轮意图识别
只加载一级 SKILL 的 name/description，所以 `loadedSkillBodies` 为空；命中 SKILL 后会在同一次
message 流程里渐进加载当前 SKILL body，再打印第二条带 body 的上下文。`message.model_input`
是实际发送给模型的 JSON prompt。
