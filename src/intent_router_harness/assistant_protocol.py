from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


ASSISTANT_PROTOCOL_TOP_LEVEL_FIELDS = frozenset(
    {
        "ok",
        "status",
        "intent_code",
        "completion_state",
        "completion_reason",
        "stage",
        "details",
        "output",
        "slot_memory",
        "message",
        "task_list",
        "current_task",
        "errorCode",
        "graph",
        "actions",
    }
)


class ProtocolAssertionError(AssertionError):
    """Raised when an assistant protocol transcript violates a regression rule."""


@dataclass(frozen=True, slots=True)
class SSEEvent:
    """One parsed SSE event."""

    event: str
    data: Any

    @property
    def is_message(self) -> bool:
        return self.event == "message"

    @property
    def is_done(self) -> bool:
        return self.event == "done" and self.data == "[DONE]"


def parse_sse_text(text: str) -> list[SSEEvent]:
    """Parse a minimal SSE transcript into events."""
    events: list[SSEEvent] = []
    current_event = "message"
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal current_event, data_lines
        if not data_lines:
            current_event = "message"
            return
        raw_data = "\n".join(data_lines)
        events.append(SSEEvent(event=current_event, data=_decode_sse_data(raw_data)))
        current_event = "message"
        data_lines = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            flush()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            current_event = line.removeprefix("event:").strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())

    flush()
    return events


def message_payloads(events: list[SSEEvent]) -> list[dict[str, Any]]:
    """Return JSON payloads from `event: message` frames."""
    payloads: list[dict[str, Any]] = []
    for event in events:
        if event.event != "message":
            continue
        if not isinstance(event.data, dict):
            raise ProtocolAssertionError("message event data must be a JSON object")
        payloads.append(event.data)
    return payloads


def business_payloads(events: list[SSEEvent]) -> list[dict[str, Any]]:
    """Return non-recognition assistant protocol message payloads."""
    return [
        payload
        for payload in message_payloads(events)
        if payload.get("stage") != "intent_recognition"
    ]


def recognition_payloads(events: list[SSEEvent]) -> list[dict[str, Any]]:
    """Return intent-recognition message payloads."""
    return [
        payload
        for payload in message_payloads(events)
        if payload.get("stage") == "intent_recognition"
    ]


def assert_done_event(events: list[SSEEvent]) -> None:
    """Require the stream to end with `event: done` and `data: [DONE]`."""
    if not events or not events[-1].is_done:
        raise ProtocolAssertionError("SSE transcript must end with event: done / data: [DONE]")


def assert_intent_recognized_before_business(events: list[SSEEvent]) -> None:
    """Require the first business payload to be preceded by an intent recognition frame."""
    payloads = message_payloads(events)
    if not payloads:
        raise ProtocolAssertionError("SSE transcript has no message frames")
    first_business_index = next(
        (index for index, payload in enumerate(payloads) if payload.get("stage") != "intent_recognition"),
        None,
    )
    if first_business_index is None:
        return
    first_payload = payloads[0]
    if first_payload.get("stage") != "intent_recognition":
        raise ProtocolAssertionError("first message before business must be an intent_recognition frame")
    if first_payload.get("completion_reason") != "intent_recognized":
        raise ProtocolAssertionError("recognition frame must use completion_reason=intent_recognized")
    if first_business_index <= 0:
        raise ProtocolAssertionError("business frame was not preceded by recognition frame")


def assert_no_intent_recognized(events: list[SSEEvent]) -> None:
    """Require that no misleading successful recognition frame is emitted."""
    for payload in recognition_payloads(events):
        if payload.get("completion_reason") == "intent_recognized":
            raise ProtocolAssertionError("transcript must not contain intent_recognized")


def assert_top_level_contract(events: list[SSEEvent]) -> None:
    """Require all message frames to stay within the assistant protocol top-level contract."""
    for payload in message_payloads(events):
        extra = set(payload) - ASSISTANT_PROTOCOL_TOP_LEVEL_FIELDS
        if extra:
            raise ProtocolAssertionError(f"assistant protocol payload has unsupported top-level fields: {sorted(extra)}")


def assert_output_slot_memory_not_exposed(events: list[SSEEvent]) -> None:
    """Require internal slot memory to stay outside `output`."""
    for payload in message_payloads(events):
        output = payload.get("output")
        if isinstance(output, dict) and "slot_memory" in output:
            raise ProtocolAssertionError("output.slot_memory must not be exposed")


def assert_final_business_status(
    events: list[SSEEvent],
    *,
    status: str,
    completion_reason: str | None = None,
) -> None:
    """Require the last business frame to match a status and optional reason."""
    business = business_payloads(events)
    if not business:
        raise ProtocolAssertionError("SSE transcript has no business frames")
    final_payload = business[-1]
    if final_payload.get("status") != status:
        raise ProtocolAssertionError(f"final business status must be {status!r}")
    if completion_reason is not None and final_payload.get("completion_reason") != completion_reason:
        raise ProtocolAssertionError(f"final business completion_reason must be {completion_reason!r}")


def assert_business_frame(
    events: list[SSEEvent],
    *,
    status: str | None = None,
    completion_reason: str | None = None,
    intent_code: str | None = None,
    slot_contains: dict[str, Any] | None = None,
    output_contains: dict[str, Any] | None = None,
) -> None:
    """Require at least one business frame matching the provided constraints."""
    for payload in business_payloads(events):
        if status is not None and payload.get("status") != status:
            continue
        if completion_reason is not None and payload.get("completion_reason") != completion_reason:
            continue
        if intent_code is not None and payload.get("intent_code") != intent_code:
            continue
        if slot_contains is not None:
            slot_memory = payload.get("slot_memory") or {}
            if not isinstance(slot_memory, dict):
                continue
            if any(slot_memory.get(key) != value for key, value in slot_contains.items()):
                continue
        if output_contains is not None:
            output = payload.get("output") or {}
            if not isinstance(output, dict):
                continue
            if any(output.get(key) != value for key, value in output_contains.items()):
                continue
        return
    raise ProtocolAssertionError("no business frame matched the expected constraints")


def assert_task_list_min(events: list[SSEEvent], minimum: int) -> None:
    """Require at least one business frame with a task list of the requested size."""
    for payload in business_payloads(events):
        task_list = payload.get("task_list")
        if isinstance(task_list, list) and len(task_list) >= minimum:
            return
    raise ProtocolAssertionError(f"no business frame has task_list length >= {minimum}")


def assert_non_empty_output(
    events: list[SSEEvent],
    *,
    status: str | None = None,
    intent_code: str | None = None,
) -> None:
    """Require at least one matching business frame to expose a non-empty output object."""
    for payload in business_payloads(events):
        if status is not None and payload.get("status") != status:
            continue
        if intent_code is not None and payload.get("intent_code") != intent_code:
            continue
        output = payload.get("output")
        if isinstance(output, dict) and bool(output):
            return
    raise ProtocolAssertionError("no matching business frame has non-empty output")


def assert_current_task_in_task_list(events: list[SSEEvent]) -> None:
    """Require business frames with both current_task and task_list to keep them consistent."""
    checked = False
    for payload in business_payloads(events):
        current_task = payload.get("current_task")
        task_list = payload.get("task_list")
        if current_task is None or not isinstance(task_list, list):
            continue
        checked = True
        current_id = _task_id(current_task)
        if current_id is None:
            raise ProtocolAssertionError("current_task does not expose a task id")
        task_ids = {_task_id(task) for task in task_list}
        if current_id not in task_ids:
            raise ProtocolAssertionError("current_task must be one of task_list")
    if not checked:
        raise ProtocolAssertionError("no business frame exposed both current_task and task_list")


def assert_recognition_order_matches_task_list(events: list[SSEEvent]) -> None:
    """Require the recognized intent to align with the first task_list item."""
    recognition = recognition_payloads(events)
    if not recognition:
        raise ProtocolAssertionError("SSE transcript has no recognition frames")
    recognition_codes = _recognition_intent_codes(recognition[0])
    if not recognition_codes:
        raise ProtocolAssertionError("recognition frame has no intent codes")

    task_codes = _first_task_list_intent_codes(events)
    if not task_codes:
        raise ProtocolAssertionError("SSE transcript has no task_list intent codes")

    if recognition_codes[0] != task_codes[0]:
        raise ProtocolAssertionError("recognized intent must match first task_list intent")


def assert_graph_nodes_order_matches_task_list(events: list[SSEEvent]) -> None:
    """Require graph node order to align with task_list order when both are present."""
    task_codes = _first_task_list_intent_codes(events)
    if not task_codes:
        raise ProtocolAssertionError("SSE transcript has no task_list intent codes")

    graph_codes = _first_graph_node_intent_codes(events)
    if not graph_codes:
        raise ProtocolAssertionError("SSE transcript has no graph node intent codes")

    if _ordered_intersection(graph_codes, task_codes) != task_codes:
        raise ProtocolAssertionError("graph node order must match task_list order")


def _decode_sse_data(raw_data: str) -> Any:
    if raw_data == "[DONE]":
        return raw_data
    try:
        return json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise ProtocolAssertionError(f"SSE data is not valid JSON: {raw_data[:80]!r}") from exc


def _task_id(task: Any) -> str | None:
    if isinstance(task, str):
        return task
    if not isinstance(task, dict):
        return None
    for key in ("taskId", "task_id", "id", "node_id", "nodeId"):
        value = task.get(key)
        if value is not None:
            return str(value)
    return None


def _intent_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("intent_code", "intentCode"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return None


def _recognition_intent_codes(payload: dict[str, Any]) -> list[str]:
    return [code] if (code := _intent_code(payload)) else []


def _first_task_list_intent_codes(events: list[SSEEvent]) -> list[str]:
    for payload in business_payloads(events):
        task_list = payload.get("task_list")
        if not isinstance(task_list, list):
            continue
        codes = [_intent_code(task) for task in task_list]
        result = [code for code in codes if code is not None]
        if result:
            return result
    return []


def _first_graph_node_intent_codes(events: list[SSEEvent]) -> list[str]:
    for payload in business_payloads(events):
        nodes = _graph_nodes(payload)
        if not isinstance(nodes, list):
            continue
        codes = [_intent_code(node) for node in nodes]
        result = [code for code in codes if code is not None]
        if result:
            return result
    return []


def _ordered_intersection(source: list[str], allowed: list[str]) -> list[str]:
    allowed_set = set(allowed)
    return [item for item in source if item in allowed_set]


def _graph_nodes(payload: dict[str, Any]) -> Any:
    graph = payload.get("graph")
    if isinstance(graph, dict) and isinstance(graph.get("nodes"), list):
        return graph["nodes"]

    output = payload.get("output")
    if not isinstance(output, dict):
        return None

    output_graph = output.get("graph")
    if isinstance(output_graph, dict) and isinstance(output_graph.get("nodes"), list):
        return output_graph["nodes"]

    graph_card = output.get("graph_card")
    if isinstance(graph_card, dict) and isinstance(graph_card.get("nodes"), list):
        return graph_card["nodes"]

    interaction = output.get("interaction")
    if not isinstance(interaction, dict):
        return None
    if interaction.get("type") == "graph_card" and interaction.get("card_type") == "dynamic_graph":
        if isinstance(interaction.get("nodes"), list):
            return interaction["nodes"]
        interaction_graph = interaction.get("graph")
        if isinstance(interaction_graph, dict) and isinstance(interaction_graph.get("nodes"), list):
            return interaction_graph["nodes"]
    return None
