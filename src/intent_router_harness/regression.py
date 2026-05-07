from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from intent_router_harness.assistant_protocol import (
    ProtocolAssertionError,
    SSEEvent,
    assert_business_frame,
    assert_current_task_in_task_list,
    assert_done_event,
    assert_final_business_status,
    assert_graph_nodes_order_matches_task_list,
    assert_intent_recognized_before_business,
    assert_no_intent_recognized,
    assert_non_empty_output,
    assert_output_slot_memory_not_exposed,
    assert_recognition_order_matches_task_list,
    assert_task_list_min,
    assert_top_level_contract,
)


ExpectationKind = Literal[
    "done",
    "intent_recognized_before_business",
    "no_intent_recognized",
    "top_level_contract",
    "no_output_slot_memory",
    "business_frame",
    "final_business_status",
    "current_task_in_task_list",
    "graph_nodes_order_matches_task_list",
    "non_empty_output",
    "recognition_order_matches_task_list",
    "task_list_min",
]


class RegressionExpectation(BaseModel):
    """One transcript assertion from a regression case."""

    kind: ExpectationKind
    status: str | None = None
    completion_reason: str | None = None
    intent_code: str | None = None
    slot_contains: dict[str, Any] | None = None
    output_contains: dict[str, Any] | None = None
    minimum: int | None = Field(default=None, gt=0)


class RegressionStep(BaseModel):
    """One request step in a regression case."""

    name: str
    endpoint: str
    stream: bool = True
    method: str = "POST"
    request: dict[str, Any] = Field(default_factory=dict)
    expectations: list[RegressionExpectation] = Field(default_factory=list)


class RegressionCase(BaseModel):
    """Assistant protocol regression case."""

    id: str
    title: str
    purpose: str
    status: str = "needs_external_runner"
    tags: list[str] = Field(default_factory=list)
    steps: list[RegressionStep] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RegressionSuite(BaseModel):
    """Versioned assistant protocol regression suite."""

    version: str
    source_document: str
    primary_mode: str = "sse"
    event_filter: list[str] = Field(default_factory=lambda: ["message", "done"])
    cases: list[RegressionCase] = Field(default_factory=list)

    def case_ids(self) -> set[str]:
        return {case.id for case in self.cases}

    def case_by_id(self, case_id: str) -> RegressionCase:
        for case in self.cases:
            if case.id == case_id:
                return case
        raise KeyError(case_id)


def load_regression_suite(path: str | Path) -> RegressionSuite:
    """Load a regression suite JSON file."""
    suite_path = Path(path).expanduser()
    return RegressionSuite.model_validate(json.loads(suite_path.read_text(encoding="utf-8")))


def validate_step_transcript(step: RegressionStep, events: list[SSEEvent]) -> None:
    """Validate a parsed SSE transcript against one step."""
    for expectation in step.expectations:
        validate_expectation(expectation, events)


def validate_case_transcripts(
    case: RegressionCase,
    transcripts: dict[str, list[SSEEvent]],
) -> None:
    """Validate transcripts keyed by step name for one regression case."""
    for step in case.steps:
        if step.name not in transcripts:
            raise ProtocolAssertionError(f"missing transcript for step {step.name!r}")
        validate_step_transcript(step, transcripts[step.name])


def validate_expectation(expectation: RegressionExpectation, events: list[SSEEvent]) -> None:
    """Apply one expectation to parsed SSE events."""
    if expectation.kind == "done":
        assert_done_event(events)
        return
    if expectation.kind == "intent_recognized_before_business":
        assert_intent_recognized_before_business(events)
        return
    if expectation.kind == "no_intent_recognized":
        assert_no_intent_recognized(events)
        return
    if expectation.kind == "top_level_contract":
        assert_top_level_contract(events)
        return
    if expectation.kind == "no_output_slot_memory":
        assert_output_slot_memory_not_exposed(events)
        return
    if expectation.kind == "business_frame":
        assert_business_frame(
            events,
            status=expectation.status,
            completion_reason=expectation.completion_reason,
            intent_code=expectation.intent_code,
            slot_contains=expectation.slot_contains,
            output_contains=expectation.output_contains,
        )
        return
    if expectation.kind == "final_business_status":
        if expectation.status is None:
            raise ProtocolAssertionError("final_business_status requires status")
        assert_final_business_status(
            events,
            status=expectation.status,
            completion_reason=expectation.completion_reason,
        )
        return
    if expectation.kind == "task_list_min":
        if expectation.minimum is None:
            raise ProtocolAssertionError("task_list_min requires minimum")
        assert_task_list_min(events, expectation.minimum)
        return
    if expectation.kind == "non_empty_output":
        assert_non_empty_output(
            events,
            status=expectation.status,
            intent_code=expectation.intent_code,
        )
        return
    if expectation.kind == "current_task_in_task_list":
        assert_current_task_in_task_list(events)
        return
    if expectation.kind == "recognition_order_matches_task_list":
        assert_recognition_order_matches_task_list(events)
        return
    if expectation.kind == "graph_nodes_order_matches_task_list":
        assert_graph_nodes_order_matches_task_list(events)
        return
    raise ProtocolAssertionError(f"unsupported expectation kind: {expectation.kind}")
