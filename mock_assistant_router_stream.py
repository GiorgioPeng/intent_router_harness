#!/usr/bin/env python3
"""Minimal interface tester for POST /api/v1/message."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator


DEFAULT_BASE_URL = os.getenv("INTENT_ROUTER_BASE_URL", "http://ai.intent-router.cc")
DEFAULT_CUST_ID = "C0001"
DEFAULT_CURRENT_DISPLAY = "transfer_page"
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_SESSION_ID_PREFIX = "mock_assistant"


@dataclass(slots=True)
class SSEFrame:
    event: str
    data: str

    def json_data(self) -> Any:
        if self.data == "[DONE]":
            return self.data
        return json.loads(self.data)


def default_session_id() -> str:
    """Return a notebook/CLI friendly default assistant session id."""
    return f"{DEFAULT_SESSION_ID_PREFIX}_{int(time.time())}"


def build_assistant_to_router_payload(
    *,
    session_id: str,
    txt: str,
    current_display: str = DEFAULT_CURRENT_DISPLAY,
    cust_id: str = DEFAULT_CUST_ID,
    execution_mode: str = "router_only",
    agent_session_id: str | None = None,
    stream: bool = True,
    debug_trace: bool = True,
    slots_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the same payload shape assistant-service forwards to router."""
    config_variables: list[dict[str, Any]] = [
        {"name": "custID", "value": cust_id},
        {"name": "sessionID", "value": session_id},
        {"name": "currentDisplay", "value": current_display},
        {"name": "agentSessionID", "value": agent_session_id or session_id},
    ]
    if slots_data:
        config_variables.append(
            {
                "name": "slots_data",
                "value": json.dumps(slots_data, ensure_ascii=False),
            }
        )

    return {
        "sessionId": session_id,
        "txt": txt,
        "config_variables": config_variables,
        "executionMode": execution_mode,
        "custId": cust_id,
        "stream": stream,
        "debugTrace": debug_trace,
    }


def router_message_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/v1/message"


def _open_url(
    request: urllib.request.Request,
    *,
    timeout_seconds: int,
    use_system_proxy: bool,
):
    if use_system_proxy:
        return urllib.request.urlopen(request, timeout=timeout_seconds)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout_seconds)


def _request_json(
    *,
    url: str,
    payload: dict[str, Any],
    timeout_seconds: int,
    use_system_proxy: bool,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Connection": "close",
        },
        method="POST",
    )
    try:
        with _open_url(
            request,
            timeout_seconds=timeout_seconds,
            use_system_proxy=use_system_proxy,
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} calling {url}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to call {url}: {exc}") from exc


def _iter_sse_frames(
    *,
    url: str,
    payload: dict[str, Any],
    timeout_seconds: int,
    use_system_proxy: bool,
) -> Iterator[SSEFrame]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Connection": "close",
        },
        method="POST",
    )
    current_event: str | None = None
    data_lines: list[str] = []

    try:
        with _open_url(
            request,
            timeout_seconds=timeout_seconds,
            use_system_proxy=use_system_proxy,
        ) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    if current_event is not None and data_lines:
                        yield SSEFrame(event=current_event, data="\n".join(data_lines))
                    current_event = None
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                    continue
                if line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].lstrip())
            if current_event is not None and data_lines:
                yield SSEFrame(event=current_event, data="\n".join(data_lines))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} calling {url}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to call {url}: {exc}") from exc


def print_frames(frames: list[SSEFrame]) -> None:
    for index, frame in enumerate(frames, start=1):
        print(f"--- frame {index} ---")
        print(f"event: {frame.event}")
        if frame.data == "[DONE]":
            print("data: [DONE]")
            continue
        try:
            print(json.dumps(_compact_frame_payload(frame.event, frame.json_data()), ensure_ascii=False, indent=2))
        except Exception:
            print(frame.data)
        sys.stdout.flush()


def run_one_turn(
    *,
    session_id: str | None = None,
    txt: str,
    current_display: str = DEFAULT_CURRENT_DISPLAY,
    base_url: str = DEFAULT_BASE_URL,
    cust_id: str = DEFAULT_CUST_ID,
    execution_mode: str = "router_only",
    agent_session_id: str | None = None,
    stream: bool = True,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    slots_data: dict[str, Any] | None = None,
    debug_trace: bool = True,
    use_system_proxy: bool = False,
    full_json: bool = False,
    print_request: bool = True,
    print_response: bool = True,
) -> list[SSEFrame] | dict[str, Any]:
    """Run one assistant-like turn directly against router."""
    resolved_session_id = session_id or default_session_id()
    payload = build_assistant_to_router_payload(
        session_id=resolved_session_id,
        txt=txt,
        current_display=current_display,
        cust_id=cust_id,
        execution_mode=execution_mode,
        agent_session_id=agent_session_id,
        stream=stream,
        debug_trace=debug_trace,
        slots_data=slots_data,
    )
    url = router_message_url(base_url)

    if print_request:
        print("=== request ===")
        print(f"POST {url}")
        print(json.dumps(_compact_request_payload(payload) if not full_json else payload, ensure_ascii=False, indent=2))
        print()
        sys.stdout.flush()

    if not stream:
        response = _request_json(
            url=url,
            payload=payload,
            timeout_seconds=timeout_seconds,
            use_system_proxy=use_system_proxy,
        )
        if print_response:
            print("=== response ===")
            print(json.dumps(_compact_frame_payload("message", response) if not full_json else response, ensure_ascii=False, indent=2))
            sys.stdout.flush()
        return response

    frames: list[SSEFrame] = []
    start = time.time()
    for index, frame in enumerate(
        _iter_sse_frames(
            url=url,
            payload=payload,
            timeout_seconds=timeout_seconds,
            use_system_proxy=use_system_proxy,
        ),
        start=1,
    ):
        frames.append(frame)
        if not print_response:
            continue
        elapsed = time.time() - start
        if frame.data == "[DONE]":
            print(f"--- frame {index} @ +{elapsed:.3f}s | done ---")
            print("data: [DONE]")
            print()
            sys.stdout.flush()
            continue
        print(f"--- frame {index} @ +{elapsed:.3f}s | {frame.event}{_frame_stage_suffix(frame)} ---")
        try:
            data = frame.json_data()
            payload_to_print = data if full_json else _compact_frame_payload(frame.event, data)
            if frame.event == "trace" and isinstance(payload_to_print, dict):
                title = payload_to_print.pop("_title", "")
                summary = payload_to_print.pop("_summary", "")
                if title:
                    print(title)
                if summary:
                    print(summary)
                if payload_to_print:
                    print(json.dumps(payload_to_print, ensure_ascii=False, indent=2))
            else:
                print(json.dumps(payload_to_print, ensure_ascii=False, indent=2))
        except Exception:
            print(frame.data)
        print()
        sys.stdout.flush()
    return frames


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Call router POST /api/v1/message.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Router base URL. Default: {DEFAULT_BASE_URL}")
    parser.add_argument("--session-id", default=default_session_id(), help="Assistant session id.")
    parser.add_argument("--txt", required=True, help="User text.")
    parser.add_argument(
        "--current-display",
        default=DEFAULT_CURRENT_DISPLAY,
        help=f"Assistant currentDisplay. Default: {DEFAULT_CURRENT_DISPLAY}",
    )
    parser.add_argument("--cust-id", default=DEFAULT_CUST_ID, help=f"custId. Default: {DEFAULT_CUST_ID}")
    parser.add_argument(
        "--execution-mode",
        default="router_only",
        choices=("execute", "router_only"),
        help="Assistant executionMode.",
    )
    parser.add_argument("--agent-session-id", default=None, help="Optional agentSessionID override.")
    parser.add_argument("--slots-data", default=None, help="Optional JSON string passed as slots_data.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Timeout in seconds.")
    parser.add_argument("--no-stream", dest="stream", action="store_false", help="Use non-stream JSON mode.")
    parser.add_argument(
        "--no-debug-trace",
        dest="debug_trace",
        action="store_false",
        help="Disable debug trace events in SSE mode.",
    )
    parser.add_argument(
        "--use-system-proxy",
        action="store_true",
        help="Use urllib system proxy settings. Default bypasses proxies for local minikube testing.",
    )
    parser.add_argument(
        "--full-json",
        action="store_true",
        help="Print full raw JSON, including prompt bodies, skill bodies, parsed_json, and protocol frames.",
    )
    parser.set_defaults(stream=True, debug_trace=True, use_system_proxy=False)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    slots_data = json.loads(args.slots_data) if args.slots_data else None
    run_one_turn(
        session_id=args.session_id,
        txt=args.txt,
        current_display=args.current_display,
        base_url=args.base_url,
        cust_id=args.cust_id,
        execution_mode=args.execution_mode,
        agent_session_id=args.agent_session_id,
        stream=args.stream,
        debug_trace=args.debug_trace,
        timeout_seconds=args.timeout,
        slots_data=slots_data,
        use_system_proxy=args.use_system_proxy,
        full_json=args.full_json,
    )
    return 0


def _frame_stage_suffix(frame: SSEFrame) -> str:
    try:
        payload = frame.json_data()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    stage = payload.get("stage")
    if stage is None:
        return ""
    return f".{stage}"


def _compact_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    config = {
        item["name"]: item.get("value")
        for item in payload.get("config_variables", [])
        if isinstance(item, dict) and "name" in item
    }
    compact = {
        "sessionId": payload.get("sessionId"),
        "txt": payload.get("txt"),
        "executionMode": payload.get("executionMode"),
        "stream": payload.get("stream"),
        "debugTrace": payload.get("debugTrace"),
        "custId": payload.get("custId"),
    }
    if config:
        compact["config"] = config
    return _drop_empty(compact)


def _compact_frame_payload(event: str, payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if event == "trace":
        return _compact_trace_payload(payload)
    if event == "message":
        return _compact_message_payload(payload)
    return _drop_empty(payload)


def _compact_trace_payload(payload: dict[str, Any]) -> dict[str, Any]:
    stage = payload.get("stage")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    compact: dict[str, Any] = {
        "_title": payload.get("title"),
        "_summary": payload.get("summary"),
        "stage": stage,
    }

    if stage == "request_received":
        compact["data"] = _pick(data, ["session_id", "execution_mode", "text", "cust_id"])
    elif stage == "session_loaded":
        compact["data"] = _drop_empty(
            {
                "status": data.get("status"),
                "completion_reason": data.get("completion_reason"),
                "slot_memory": data.get("slot_memory"),
                "current_task": data.get("current_task"),
                "task_count": len(data.get("task_list") or []),
            }
        )
    elif stage == "spec_progressive_load":
        compact["data"] = _pick(
            data,
            [
                "surface",
                "metadata_skills",
                "loaded_skill_bodies",
                "inline_skills",
                "max_skill_body_chars",
            ],
        )
    elif stage == "skill_body_loaded":
        body = str(data.get("body") or "")
        compact["data"] = _drop_empty(
            {
                "surface": data.get("surface"),
                "skill": data.get("skill"),
                "path": data.get("path"),
                "body_chars": data.get("body_chars"),
                "body_preview": _preview(body, 360),
            }
        )
    elif stage == "prompt_loaded":
        compact["data"] = _drop_empty(
            {
                "surface": data.get("surface"),
                "loaded_skills": data.get("loaded_skills"),
                "system_chars": data.get("system_chars"),
                "human_chars": data.get("human_chars"),
                "system_prompt_preview": _preview(str(data.get("system_prompt") or ""), 500),
                "human_prompt_preview": _preview(str(data.get("human_prompt") or ""), 500),
            }
        )
    elif stage == "llm_raw_response":
        compact["data"] = _drop_empty(
            {
                "model": data.get("model"),
                "finish_reason": data.get("finish_reason"),
                "usage": data.get("usage"),
                "content_preview": _preview(str(data.get("content") or ""), 700),
            }
        )
    elif stage == "llm_analysis":
        compact["data"] = _drop_empty(
            {
                "mode": data.get("mode"),
                "status": data.get("status"),
                "completion_reason": data.get("completion_reason"),
                "intent_code": data.get("intent_code"),
                "slot_memory": data.get("slot_memory"),
                "current_task": data.get("current_task"),
                "message": data.get("message"),
                "output": data.get("output"),
                "task_count": len(data.get("task_list") or []),
            }
        )
    elif stage == "intent_recognition":
        compact["data"] = _pick(data, ["intent_code", "completion_reason"])
    elif stage == "slot_and_skill_result":
        compact["data"] = _drop_empty(
            {
                "status": data.get("status"),
                "completion_reason": data.get("completion_reason"),
                "slot_memory": data.get("slot_memory"),
                "message": data.get("message"),
                "current_task": data.get("current_task"),
                "output": data.get("output"),
            }
        )
    elif stage == "assistant_protocol_frames":
        frames = data.get("frames") or []
        compact["data"] = _drop_empty(
            {
                "frame_count": data.get("frame_count"),
                "frames": [_compact_message_payload(frame) for frame in frames if isinstance(frame, dict)],
            }
        )
    else:
        compact["data"] = _drop_empty(data)
    return _drop_empty(compact)


def _compact_message_payload(payload: dict[str, Any]) -> dict[str, Any]:
    task_list = payload.get("task_list") or []
    compact = {
        "ok": payload.get("ok"),
        "stage": payload.get("stage"),
        "status": payload.get("status"),
        "intent_code": payload.get("intent_code"),
        "completion_state": payload.get("completion_state"),
        "completion_reason": payload.get("completion_reason"),
        "message": payload.get("message"),
        "slot_memory": payload.get("slot_memory"),
        "current_task": payload.get("current_task"),
        "task_count": len(task_list) if task_list else None,
        "task_list": task_list if task_list else None,
        "output": payload.get("output"),
        "errorCode": payload.get("errorCode"),
    }
    return _drop_empty(compact)


def _pick(value: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return _drop_empty({key: value.get(key) for key in keys})


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := _drop_empty(item)) not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [_drop_empty(item) for item in value if _drop_empty(item) not in (None, "", [], {})]
    return value


def _preview(value: str, limit: int) -> str:
    value = value.replace("\r", "\\r")
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}...[truncated {len(value) - limit} chars]"


if __name__ == "__main__":
    raise SystemExit(main())
