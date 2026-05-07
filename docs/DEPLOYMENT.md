# Deployment

This project has two service entrypoints:

- `intent-router-harness serve`: lightweight stdlib HTTP server for local harness work.
- `intent-router-harness serve-asgi`: deployable ASGI service backed by FastAPI and Uvicorn.

Use the ASGI entrypoint for shared test environments.

## Local ASGI

```bash
PYTHONPATH=src python -m intent_router_harness serve-asgi --host 0.0.0.0 --port 8765
```

Health and readiness:

```bash
curl -s http://127.0.0.1:8765/healthz
curl -s http://127.0.0.1:8765/readyz
```

## Configuration

ASGI settings use the `INTENT_ROUTER_HARNESS_` environment prefix:

```bash
INTENT_ROUTER_HARNESS_SPEC_PATH=examples/finance-router-harness.toml
INTENT_ROUTER_HARNESS_REGRESSION_SUITE_PATH=regressions/assistant_protocol_v0_6.json
INTENT_ROUTER_HARNESS_LLM_ENV_FILE=.env.local
```

The LLM env file must provide:

```bash
ROUTER_LLM_API_BASE_URL=...
ROUTER_LLM_API_KEY=...
ROUTER_LLM_MODEL=...
```

Do not commit `.env.local`. Keep secrets in the target platform's environment
or secret manager.

## Current Limits

- Session storage is in-memory; use one replica or replace it before multi-instance deployment.
- LLM calls are synchronous; size workers and timeouts accordingly.
- Authentication, authorization, rate limiting, and persistent audit logging are not implemented yet.
- External agent execution is not implemented; current service is strongest for `router_only` and protocol validation paths.
