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
    LLMRequestError,
    OpenAICompatibleLLMClient,
    load_llm_settings,
)
from intent_router_harness.service import (
    IntentRouterHarnessService,
    RegressionValidationRequest,
    RenderLLMRequest,
    RenderPromptRequest,
    ServiceConfigurationError,
)


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
        if path == "/health":
            self._write_model(200, self.harness_service.health())
            return
        if path == "/surfaces":
            self._write_json(
                200,
                {
                    "surfaces": [
                        surface.model_dump(mode="json")
                        for surface in self.harness_service.surfaces()
                    ]
                },
            )
            return
        if path == "/regression/suite":
            try:
                self._write_model(200, self.harness_service.regression_summary())
            except ServiceConfigurationError as exc:
                self._write_error(503, "regression_suite_not_loaded", str(exc))
            return
        if path.startswith("/regression/cases/"):
            case_id = path.removeprefix("/regression/cases/")
            try:
                self._write_model(200, self.harness_service.regression_case(case_id))
            except ServiceConfigurationError as exc:
                self._write_error(503, "regression_suite_not_loaded", str(exc))
            except KeyError:
                self._write_error(404, "regression_case_not_found", case_id)
            return
        if path == "/":
            self._write_json(
                200,
                {
                    "service": "intent_router_harness",
                    "endpoints": [
                        "GET /health",
                        "GET /surfaces",
                        "POST /render",
                        "POST /llm/render",
                        "POST /api/v1/message",
                        "POST /api/v1/task/completion",
                        "GET /regression/suite",
                        "GET /regression/cases/{case_id}",
                        "POST /regression/validate",
                    ],
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

            self._write_assistant_result(
                request.stream,
                response.frames,
                trace_events=response.trace_events if request.debugTrace else [],
            )
            return

        if path == "/regression/validate":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                request = RegressionValidationRequest.model_validate(payload)
                response = self.harness_service.validate_regression(request)
            except ValidationError as exc:
                self._write_error(400, "validation_error", exc.errors())
                return
            except ServiceConfigurationError as exc:
                self._write_error(503, "regression_suite_not_loaded", str(exc))
                return

            self._write_model(200 if response.ok else 422, response)
            return

        if path == "/llm/render":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                request = RenderLLMRequest.model_validate(payload)
                response = self.harness_service.render_llm(request)
            except ValidationError as exc:
                self._write_request_error(payload, 400, "validation_error", exc.errors())
                return
            except KeyError as exc:
                self._write_request_error(payload, 400, "unknown_surface", str(exc))
                return
            except ServiceConfigurationError as exc:
                self._write_request_error(payload, 503, "llm_not_configured", str(exc))
                return
            except LLMRequestError as exc:
                self._write_request_error(payload, 502, "llm_request_failed", str(exc))
                return

            if request.stream:
                self._write_sse_message(response.model_dump(mode="json"))
                return
            self._write_model(200, response)
            return

        if path != "/render":
            self._write_error(404, "not_found", f"unknown endpoint: {path}")
            return

        payload = self._read_json_body()
        if payload is None:
            return
        try:
            request = RenderPromptRequest.model_validate(payload)
            response = self.harness_service.render(request)
        except ValidationError as exc:
            self._write_request_error(payload, 400, "validation_error", exc.errors())
            return
        except KeyError as exc:
            self._write_request_error(payload, 400, "unknown_surface", str(exc))
            return

        if request.stream:
            self._write_sse_message(response.model_dump(mode="json"))
            return
        self._write_model(200, response)

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
