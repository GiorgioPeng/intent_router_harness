# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.11+ package using a `src/` layout. Core package code lives in `src/intent_router_harness/`, including prompt runtime, service layers, ASGI/server entrypoints, LLM integration, regression validation, and assistant protocol contracts. Tests live in `tests/` and use pytest. Example harness specs are in `examples/`; sample skills and references are in `skills/`; structured regression data is in `regressions/`; architecture and deployment notes are in `docs/`; Kubernetes manifests are in `k8s/`.

## Build, Test, and Development Commands

- `python -m pip install -e '.[test]'`: install the package locally with test dependencies.
- `python -m pytest -q`: run the full pytest suite.
- `PYTHONPATH=src python -m intent_router_harness show-suite regressions/assistant_protocol_v0_5.json`: inspect the assistant protocol regression suite.
- `PYTHONPATH=src python -m intent_router_harness serve examples/finance-router-harness.toml --port 8765`: run the stdlib HTTP service locally.
- `PYTHONPATH=src python -m intent_router_harness serve-asgi --host 0.0.0.0 --port 8765`: run the ASGI app for FastAPI/uvicorn-style deployment.

After installation, the console script `intent-router-harness` can replace `PYTHONPATH=src python -m intent_router_harness`.

## Coding Style & Naming Conventions

Follow the existing Python style: 4-space indentation, type annotations for public interfaces, `from __future__ import annotations`, Pydantic models for request/response contracts, and small functions with explicit error types. Use `snake_case` for functions, variables, files, and test names; use `PascalCase` for classes and Pydantic models. Keep imports grouped as standard library, third-party, then local package imports. No formatter or linter config is currently checked in, so keep edits consistent with nearby code.

## Testing Guidelines

Use pytest. Place tests in `tests/` with filenames like `test_service.py` and functions like `test_service_renders_prompt_response`. Prefer focused tests that exercise public behavior: prompt rendering, HTTP/ASGI endpoints, assistant protocol parsing, and regression validation. For changes affecting stream behavior, cover both SSE and non-stream responses when practical.

## Commit & Pull Request Guidelines

Git history currently contains a single initial commit, so use concise imperative commit messages such as `Add assistant protocol regression validation`. Pull requests should include a short description, affected modules or endpoints, test commands run, and any changes to examples, regression fixtures, environment variables, or deployment manifests. Include screenshots only for documentation or UI-rendered artifacts.

## Security & Configuration Tips

Copy `.env.example` to a local env file when needed and do not commit secrets. Keep LLM provider credentials, base URLs, and model settings outside source files. When adding skills or regression fixtures, avoid embedding private customer data.
