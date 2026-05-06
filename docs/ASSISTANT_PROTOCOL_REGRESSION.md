# 助手协议回归 v0.5 实现说明

本文说明 `docs/router-service-助手协议回归测试用例-v0.5.md` 在本独立项目中的落地方式。

## 实现范围

当前已实现四类资产：

- `regressions/assistant_protocol_v0_5.json`：从 v0.5 文档提取的 16 个详细回归用例，包含 TC-S04B。
- `intent_router_harness.assistant_protocol`：SSE transcript parser 和助手协议校验器。
- `intent_router_harness.regression`：回归 suite / case / step / expectation loader。
- `intent_router_harness.service` / `server`：通过 HTTP 暴露 suite 查询和 transcript 校验。
- `intent_router_harness.assistant_service`：task-first assistant protocol 服务入口，可通过 `/api/v1/message` 和 `/api/v1/task/completion` 产生协议帧。

默认测试不访问旧 `router-service`，也不访问真实大模型。它验证的是：

- v0.5 文档中的 TC-S01 到 TC-S15 和 TC-S04B 已结构化保存。
- SSE 必须以 `event: done` / `data: [DONE]` 收尾。
- 可识别场景必须先推 `intent_recognition` / `intent_recognized`。
- 业务帧和识别帧必须分开判定。
- message payload 顶层字段必须符合助手协议字段集合。
- `output.slot_memory` 不能外泄。
- 识别失败场景不能先推误导性 `intent_recognized`。
- router_only ready 场景必须返回非空 handover output。
- 多意图场景可校验 `current_task`、`task_list`、识别帧和图节点顺序一致性。
- 文档矩阵中的 S16 映射到结构化用例 TC-S15；S17 / PR #8 顺序要求映射到 TC-S09 的顺序断言。

## 运行

```bash
python -m pytest -q
```

查看 suite 摘要：

```bash
python -m intent_router_harness show-suite regressions/assistant_protocol_v0_5.json
```

启动 HTTP harness 服务后可以查询 suite 并校验 transcript：

```bash
PYTHONPATH=src python -m intent_router_harness serve examples/finance-router-harness.toml
curl -s http://127.0.0.1:8765/regression/suite
curl -s http://127.0.0.1:8765/regression/validate \
  -H 'Content-Type: application/json' \
  -d '{"case_id":"TC-S01","step_name":"message_missing_amount","sse_text":"event: done\ndata: [DONE]\n\n"}'
```

## 与真实服务的关系

本项目不内置旧 `router-service` 运行时。后续如果要接真实服务，需要新增一个 external runner：

1. 读取 `regressions/assistant_protocol_v0_5.json`。
2. 按 step 发送 HTTP SSE 请求。
3. 将响应 body 交给 `parse_sse_text()`。
4. 调用 `validate_step_transcript()`。

这样能复用同一份 v0.5 用例和协议断言，同时保持当前项目独立。

## 大模型连通性

`.env.local` 可以用于可选的 OpenAI-compatible smoke test。该命令只验证模型接口可连通，不参与默认测试：

```bash
python -m intent_router_harness llm-smoke --env-file .env.local
```

命令会读取：

- `ROUTER_LLM_API_BASE_URL`
- `ROUTER_LLM_API_KEY`
- `ROUTER_LLM_MODEL`
- `ROUTER_LLM_TEMPERATURE`
- `ROUTER_LLM_TIMEOUT_SECONDS`

不会输出 API key。
