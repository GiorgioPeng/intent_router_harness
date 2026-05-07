# intent_router_harness

`intent_router_harness` is a standalone project for building intent-routing
capabilities with a DeepAgents-style harness pattern.

It does not import, patch, or configure any production router project. The
project owns its own specs, skills, prompt surfaces, and tests.

## Core Model

- A spec file defines named prompt surfaces.
- `agent.md` is the default root instruction layer.
- Skill metadata is indexed first.
- Full business `SKILL.md` bodies are loaded only after scene selection or an
  explicit runtime request.
- Lower-level helper skills without `description` stay out of the recognition
  index and can still be loaded explicitly.
- Long or low-frequency rules should be exposed as skill-owned references.
- Rendered prompts are plain data, so they can be used by any LLM client or
  eval runner.

## Example

```python
from intent_router_harness import load_prompt_harness

harness = load_prompt_harness("examples/finance-router-harness.toml")
prompt = harness.render(
    surface="scene_selection",
    variables={
        "message": "transfer 500 to Alice",
        "recommend_task_json": "[]",
        "task_state_json": "{}",
        "recent_messages_json": "[]",
        "config_variables_json": "[]",
    },
    domain_codes=("finance",),
    capabilities=("routing", "slots", "planning"),
)

print(prompt.messages())
```

## Layout

- `src/intent_router_harness`: harness runtime and skill loading code.
- `examples`: sample harness specs.
- `regressions`: structured regression suites extracted from docs.
- `skills`: sample DeepAgents-style skills.
- `tests`: standalone tests.

## Design Notes

See [docs/DESIGN.md](docs/DESIGN.md) for the current architecture, DeepAgents
harness references, implementation model, and next steps.

See [docs/ASSISTANT_PROTOCOL_REGRESSION.md](docs/ASSISTANT_PROTOCOL_REGRESSION.md)
for the `router-service-助手协议回归测试用例-v0.5` implementation.

See [docs/router-service-助手协议回归测试用例-v0.6.md](docs/router-service-助手协议回归测试用例-v0.6.md)
for the current project-aligned regression case plan.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for ASGI deployment instructions.

## Commands

```bash
python -m pytest -q
PYTHONPATH=src python -m intent_router_harness show-suite regressions/assistant_protocol_v0_5.json
PYTHONPATH=src python -m intent_router_harness llm-smoke --env-file .env.local
PYTHONPATH=src python -m intent_router_harness serve examples/finance-router-harness.toml --port 8765
PYTHONPATH=src python -m intent_router_harness serve-asgi --host 0.0.0.0 --port 8765
```

After installing the package, the same commands are available through
`intent-router-harness`.

## HTTP Service

The service layer exposes the assistant protocol over HTTP:

- `GET /healthz`: liveness check.
- `GET /readyz`: readiness check and LLM configuration visibility.
- `GET /` or `/validator`: browser validation UI for streaming message and task completion flows.
- `POST /api/v1/message`: spec-driven assistant protocol message entrypoint.
- `POST /api/v1/task/completion`: assistant task completion callback.

SSE request:

```bash
curl -s http://127.0.0.1:8765/api/v1/message \
  -H 'Content-Type: application/json' \
  -d '{
    "sessionId": "assistant_demo_001",
    "txt": "给小明转账200元",
    "stream": true,
    "executionMode": "router_only"
  }'
```

The SSE response uses `event: message` for business frames and ends
with:

```text
event: done
data: [DONE]
```

For non-streaming JSON, send `"stream": false` or omit the field.

Assistant protocol service request:

```bash
curl -s http://127.0.0.1:8765/api/v1/message \
  -H 'Content-Type: application/json' \
  -d '{
    "sessionId": "assistant_demo_001",
    "txt": "给小明转账200元",
    "stream": true,
    "executionMode": "router_only"
  }'
```
