# intent_router_harness

`intent_router_harness` is a standalone project for building intent-routing
capabilities with a DeepAgents-style harness pattern.

It does not import, patch, or configure any production router project. The
project owns its own specs, skills, prompt surfaces, and tests.

## Core Model

- A spec file defines named prompt surfaces.
- Skill metadata is indexed first.
- Full `SKILL.md` bodies are loaded only when a binding matches the current
  surface and runtime context.
- Rendered prompts are plain data, so they can be used by any LLM client or
  eval runner.

## Example

```python
from intent_router_harness import load_prompt_harness

harness = load_prompt_harness("examples/finance-router-harness.toml")
prompt = harness.render(
    surface="intent_recognition",
    variables={
        "message": "transfer 500 to Alice",
        "candidate_intents_json": "[]",
        "recommend_task_json": "[]",
    },
    domain_codes=("finance",),
    intent_codes=("transfer",),
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

The service layer exposes the same harness runtime over a small stdlib HTTP
server:

- `GET /health`: harness name, version, and configured surfaces.
- `GET /surfaces`: surface metadata without prompt bodies.
- `POST /render`: render a prompt surface. Set `stream: true` for SSE, or
  `stream: false` for a regular JSON response.
- `POST /llm/render`: render a prompt surface and call the configured
  OpenAI-compatible LLM. It also supports `stream: true`.
- `POST /api/v1/message`: spec-driven assistant protocol message entrypoint.
- `POST /api/v1/task/completion`: assistant task completion callback.
- `GET /regression/suite`: assistant protocol regression suite summary.
- `GET /regression/cases/{case_id}`: one structured regression case.
- `POST /regression/validate`: validate an SSE transcript against one step or case.

SSE request:

```bash
curl -s http://127.0.0.1:8765/render \
  -H 'Content-Type: application/json' \
  -d '{
    "surface": "intent_recognition",
    "stream": true,
    "variables": {
      "message": "transfer 500 to Alice",
      "candidate_intents_json": "[]",
      "recommend_task_json": "[]"
    },
    "domain_codes": ["finance"],
    "intent_codes": ["transfer"]
  }'
```

The SSE response uses `event: message` for the rendered prompt payload and ends
with:

```text
event: done
data: [DONE]
```

For non-streaming JSON, send `"stream": false` or omit the field.

LLM-backed service request:

```bash
curl -s http://127.0.0.1:8765/llm/render \
  -H 'Content-Type: application/json' \
  -d '{
    "surface": "intent_recognition",
    "stream": false,
    "max_tokens": 512,
    "variables": {
      "message": "给小明转账200元",
      "candidate_intents_json": "[]",
      "recommend_task_json": "[]",
      "recent_messages_json": "[]"
    },
    "domain_codes": ["finance"],
    "capabilities": ["routing"]
  }'
```

Assistant protocol service request:

```bash
curl -s http://127.0.0.1:8765/api/v1/message \
  -H 'Content-Type: application/json' \
  -d '{
    "sessionId": "assistant_demo_001",
    "txt": "给小明转账200元",
    "stream": true,
    "executionMode": "router_only",
    "candidate_intents": [
      {"intent_code": "AG_TRANS", "name": "转账", "required_slots": ["payee_name", "amount"]}
    ]
  }'
```
