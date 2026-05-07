from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from intent_router_harness.contracts import RouterMessageRequest, TaskCompletionRequest
from intent_router_harness.llm import (
    LLMConfigurationError,
    OpenAICompatibleLLMClient,
    load_llm_settings,
)
from intent_router_harness.service import (
    IntentRouterHarnessService,
    ServiceConfigurationError,
)
from intent_router_harness.session_store import SessionOwnershipError


def create_server(
    service: IntentRouterHarnessService,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> HTTPServer:
    """Create a stdlib HTTP server for the harness service."""

    class HarnessRequestHandler(_HarnessRequestHandler):
        harness_service = service

    return HTTPServer((host, port), HarnessRequestHandler)


def serve(
    spec_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    skill_roots: list[str] | None = None,
    regression_suite_path: str | Path | None = None,
    llm_env_file: str | Path | None = ".env.local",
) -> None:
    """Run the harness HTTP service until interrupted."""
    llm_client = _load_optional_llm_client(llm_env_file)
    service = IntentRouterHarnessService.from_spec(
        spec_path,
        skill_roots=skill_roots,
        regression_suite_path=regression_suite_path,
        llm_client=llm_client,
    )
    server = create_server(service, host=host, port=port)
    print(f"intent_router_harness serving {spec_path} on http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nintent_router_harness stopped")
    finally:
        server.server_close()


class _HarnessRequestHandler(BaseHTTPRequestHandler):
    harness_service: IntentRouterHarnessService

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            self._write_json(200, {"status": "ok"})
            return
        if path == "/readyz":
            self._write_json(
                200,
                {
                    "ready": True,
                    "service": "intent_router_harness",
                    "llm_configured": self.harness_service.llm_client is not None,
                },
            )
            return
        self._write_error(404, "not_found", f"unknown endpoint: {path}")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/v1/message":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                request = RouterMessageRequest.model_validate(payload)
                response = self.harness_service.handle_message(request)
            except ValidationError as exc:
                self._write_request_error(payload, 400, "validation_error", exc.errors())
                return
            except ServiceConfigurationError as exc:
                self._write_request_error(payload, 503, "assistant_not_configured", str(exc))
                return
            except SessionOwnershipError as exc:
                self._write_request_error(payload, 403, "session_user_mismatch", str(exc))
                return

            self._write_assistant_result(
                request.stream,
                response.frames,
                trace_events=response.trace_events if request.debugTrace else [],
            )
            return

        if path == "/api/v1/task/completion":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                request = TaskCompletionRequest.model_validate(payload)
                response = self.harness_service.handle_task_completion(request)
            except ValidationError as exc:
                self._write_request_error(payload, 400, "validation_error", exc.errors())
                return
            except ServiceConfigurationError as exc:
                self._write_request_error(payload, 503, "assistant_not_configured", str(exc))
                return
            except SessionOwnershipError as exc:
                self._write_request_error(payload, 403, "session_user_mismatch", str(exc))
                return

            self._write_assistant_result(
                request.stream,
                response.frames,
                trace_events=response.trace_events if request.debugTrace else [],
            )
            return

        self._write_error(404, "not_found", f"unknown endpoint: {path}")

    def log_message(self, format: str, *args: Any) -> None:
        """Keep test output and local service logs quiet by default."""
        return

    def _read_json_body(self) -> Any | None:
        try:
            content_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._write_error(400, "bad_request", "Content-Length must be an integer")
            return None

        raw_body = self.rfile.read(content_length).decode("utf-8")
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError as exc:
            self._write_error(400, "invalid_json", f"request body must be JSON: {exc.msg}")
            return None

    def _write_model(self, status: int, model: Any) -> None:
        self._write_json(status, model.model_dump(mode="json"))

    def _write_error(self, status: int, code: str, message: Any) -> None:
        self._write_json(status, {"error": {"code": code, "message": message}})

    def _write_request_error(self, payload: Any, status: int, code: str, message: Any) -> None:
        if isinstance(payload, dict) and payload.get("stream") is True:
            self._write_sse_error(status, code, message)
            return
        self._write_error(status, code, message)

    def _write_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_sse_message(self, payload: Any) -> None:
        self._write_sse_events(
            200,
            [
                ("message", payload),
                ("done", "[DONE]"),
            ],
        )

    def _write_assistant_result(
        self,
        stream: bool,
        frames: list[Any],
        *,
        trace_events: list[Any] | None = None,
    ) -> None:
        payloads = [frame.protocol_dump() for frame in frames]
        if stream:
            events = [
                ("trace", trace.model_dump(mode="json"))
                for trace in (trace_events or [])
            ]
            events.extend(("message", payload) for payload in payloads)
            events.append(("done", "[DONE]"))
            self._write_sse_events(200, events)
            return
        self._write_json(200, payloads[-1] if payloads else {})

    def _write_sse_error(self, status: int, code: str, message: Any) -> None:
        self._write_sse_events(
            status,
            [
                ("error", {"error": {"code": code, "message": message}}),
                ("done", "[DONE]"),
            ],
        )

    def _write_sse_events(self, status: int, events: list[tuple[str, Any]]) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for event, data in events:
            self.wfile.write(f"event: {event}\n".encode("utf-8"))
            if data == "[DONE]":
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                encoded = json.dumps(data, ensure_ascii=False)
                self.wfile.write(f"data: {encoded}\n\n".encode("utf-8"))
            self.wfile.flush()


def _load_optional_llm_client(env_file: str | Path | None) -> OpenAICompatibleLLMClient | None:
    try:
        settings = load_llm_settings(env_file or ".env.local")
    except LLMConfigurationError:
        return None
    return OpenAICompatibleLLMClient(settings)
