from __future__ import annotations

from collections.abc import Callable
import json
import logging
from queue import Queue
from threading import Thread
from typing import Any, Iterable

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from intent_router_harness.config import AppSettings
from intent_router_harness.contracts import (
    AssistantServiceResult,
    AssistantTraceEvent,
    RouterMessageRequest,
    TaskCompletionRequest,
)
from intent_router_harness.service import (
    IntentRouterHarnessService,
    ServiceConfigurationError,
)
from intent_router_harness.service_factory import build_service
from intent_router_harness.session_store import SessionOwnershipError
from intent_router_harness.trace import trace_sink

logger = logging.getLogger(__name__)
_SSE_SENTINEL = object()
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def create_app(
    settings: AppSettings | None = None,
    service: IntentRouterHarnessService | None = None,
) -> FastAPI:
    """Create the deployable ASGI application."""
    resolved_settings = settings or AppSettings()
    _configure_logging(resolved_settings.log_level)
    logger.info(
        "creating ASGI app spec_path=%s regression_suite_path=%s llm_env_file=%s skill_roots=%s",
        resolved_settings.spec_path,
        resolved_settings.regression_suite_path,
        resolved_settings.llm_env_file,
        resolved_settings.skill_roots,
    )
    resolved_service = service or build_service(resolved_settings)

    app = FastAPI(title="intent_router_harness", version="0.1.0")
    app.state.settings = resolved_settings
    app.state.service = resolved_service
    logger.info(
        "ASGI app ready surfaces=%s llm_configured=%s regression_suite_loaded=%s",
        resolved_service.health().surfaces,
        resolved_service.llm_client is not None,
        resolved_service.regression_suite is not None,
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, Any]:
        return {
            "ready": True,
            "service": "intent_router_harness",
            "llm_configured": resolved_service.llm_client is not None,
        }

    @app.post("/api/v1/message")
    def message(request: RouterMessageRequest):
        if request.stream and request.debugTrace:
            return _assistant_sse_response(lambda: resolved_service.handle_message(request))
        try:
            result = resolved_service.handle_message(request)
        except ServiceConfigurationError as exc:
            raise HTTPException(status_code=503, detail={"code": "assistant_not_configured", "message": str(exc)}) from exc
        except SessionOwnershipError as exc:
            raise HTTPException(status_code=403, detail={"code": "session_user_mismatch", "message": str(exc)}) from exc
        payloads = [frame.protocol_dump() for frame in result.frames]
        if request.stream:
            trace_payloads = (
                [event.model_dump(mode="json") for event in result.trace_events]
                if request.debugTrace
                else []
            )
            return _sse_response(payloads, trace_payloads=trace_payloads)
        return payloads[-1] if payloads else {}

    @app.post("/api/v1/task/completion")
    def task_completion(request: TaskCompletionRequest):
        if request.stream and request.debugTrace:
            return _assistant_sse_response(lambda: resolved_service.handle_task_completion(request))
        try:
            result = resolved_service.handle_task_completion(request)
        except ServiceConfigurationError as exc:
            raise HTTPException(status_code=503, detail={"code": "assistant_not_configured", "message": str(exc)}) from exc
        except SessionOwnershipError as exc:
            raise HTTPException(status_code=403, detail={"code": "session_user_mismatch", "message": str(exc)}) from exc
        payloads = [frame.protocol_dump() for frame in result.frames]
        if request.stream:
            trace_payloads = (
                [event.model_dump(mode="json") for event in result.trace_events]
                if request.debugTrace
                else []
            )
            return _sse_response(payloads, trace_payloads=trace_payloads)
        return payloads[-1] if payloads else {}

    return app


def app() -> FastAPI:
    """Uvicorn factory target."""
    return create_app()


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(levelname)s:%(name)s:%(message)s",
    )
    logging.getLogger("intent_router_harness").setLevel(level)


def _sse_response(
    payloads: Iterable[dict[str, Any]],
    *,
    trace_payloads: Iterable[dict[str, Any]] = (),
) -> StreamingResponse:
    return StreamingResponse(
        _sse_events(payloads, trace_payloads=trace_payloads),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def _sse_events(
    payloads: Iterable[dict[str, Any]],
    *,
    trace_payloads: Iterable[dict[str, Any]] = (),
):
    trace_count = 0
    for index, payload in enumerate(trace_payloads, start=1):
        trace_count = index
        logger.info(
            "sse.emit.trace index=%d stage=%s title=%s",
            index,
            payload.get("stage"),
            payload.get("title"),
        )
        yield "event: trace\n"
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    count = 0
    for index, payload in enumerate(payloads, start=1):
        count = index
        logger.info(
            "sse.emit.frame index=%d status=%s completion_reason=%s intent_code=%s stage=%s",
            index,
            payload.get("status"),
            payload.get("completion_reason"),
            payload.get("intent_code"),
            payload.get("stage"),
        )
        yield "event: message\n"
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    logger.info("sse.emit.done trace_count=%d frame_count=%d", trace_count, count)
    yield "event: done\n"
    yield "data: [DONE]\n\n"


def _assistant_sse_response(
    worker: Callable[[], AssistantServiceResult],
) -> StreamingResponse:
    return StreamingResponse(
        _assistant_sse_events(worker),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def _assistant_sse_events(worker: Callable[[], AssistantServiceResult]):
    queue: Queue[tuple[str, dict[str, Any]] | object] = Queue()

    def push_trace(event: AssistantTraceEvent) -> None:
        queue.put(("trace", event.model_dump(mode="json")))

    def run_worker() -> None:
        try:
            with trace_sink(push_trace):
                result = worker()
            for frame in result.frames:
                queue.put(("message", frame.protocol_dump()))
        except Exception as exc:
            logger.exception("assistant stream worker failed error=%s", exc)
            queue.put(
                (
                    "error",
                    {
                        "error": {
                            "code": "assistant_stream_error",
                            "message": str(exc),
                        }
                    },
                )
            )
        finally:
            queue.put(_SSE_SENTINEL)

    Thread(target=run_worker, daemon=True).start()

    trace_count = 0
    message_count = 0
    while True:
        item = queue.get()
        if item is _SSE_SENTINEL:
            break
        event, payload = item
        if event == "trace":
            trace_count += 1
            logger.info(
                "sse.emit.trace index=%d stage=%s title=%s",
                trace_count,
                payload.get("stage"),
                payload.get("title"),
            )
        elif event == "message":
            message_count += 1
            logger.info(
                "sse.emit.frame index=%d status=%s completion_reason=%s intent_code=%s stage=%s",
                message_count,
                payload.get("status"),
                payload.get("completion_reason"),
                payload.get("intent_code"),
                payload.get("stage"),
            )
        yield f"event: {event}\n"
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    logger.info(
        "sse.emit.done trace_count=%d frame_count=%d",
        trace_count,
        message_count,
    )
    yield "event: done\n"
    yield "data: [DONE]\n\n"
