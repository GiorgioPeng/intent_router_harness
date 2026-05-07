from __future__ import annotations

import argparse
from pathlib import Path
import sys

from intent_router_harness.llm import OpenAICompatibleLLMClient, load_llm_settings
from intent_router_harness.regression import load_regression_suite
from intent_router_harness.server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="intent_router_harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    suite_parser = subparsers.add_parser("show-suite", help="Load and summarize a regression suite")
    suite_parser.add_argument("suite", type=Path)

    smoke_parser = subparsers.add_parser("llm-smoke", help="Run a tiny OpenAI-compatible LLM smoke test")
    smoke_parser.add_argument("--env-file", type=Path, default=Path(".env.local"))

    serve_parser = subparsers.add_parser("serve", help="Run the harness HTTP service")
    serve_parser.add_argument("spec", type=Path)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--skill-root", action="append", default=[])
    serve_parser.add_argument(
        "--regression-suite",
        type=Path,
        default=Path("regressions/assistant_protocol_v0_6.json"),
    )
    serve_parser.add_argument("--llm-env-file", type=Path, default=Path(".env.local"))

    asgi_parser = subparsers.add_parser("serve-asgi", help="Run the ASGI harness service with Uvicorn")
    asgi_parser.add_argument("--host", default="0.0.0.0")
    asgi_parser.add_argument("--port", type=int, default=8765)
    asgi_parser.add_argument("--reload", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "show-suite":
        suite = load_regression_suite(args.suite)
        print(f"{suite.version}: {len(suite.cases)} cases from {suite.source_document}")
        print(" ".join(sorted(suite.case_ids())))
        return 0

    if args.command == "llm-smoke":
        settings = load_llm_settings(args.env_file)
        client = OpenAICompatibleLLMClient(settings)
        result = client.smoke()
        print(f"model={settings.model} result={result!r}")
        return 0

    if args.command == "serve":
        serve(
            args.spec,
            host=args.host,
            port=args.port,
            skill_roots=args.skill_root,
            regression_suite_path=args.regression_suite,
            llm_env_file=args.llm_env_file,
        )
        return 0

    if args.command == "serve-asgi":
        import uvicorn

        uvicorn.run(
            "intent_router_harness.asgi:app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
