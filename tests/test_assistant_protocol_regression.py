from __future__ import annotations

import pytest

from intent_router_harness.assistant_protocol import ProtocolAssertionError, parse_sse_text
from intent_router_harness.regression import load_regression_suite, validate_step_transcript


SUITE_PATH = "regressions/assistant_protocol_v0_6.json"


def test_v0_6_suite_covers_documented_cases() -> None:
    suite = load_regression_suite(SUITE_PATH)

    assert suite.version == "0.6"
    assert suite.primary_mode == "sse"
    assert suite.case_ids() == {f"TC-S{index:02d}" for index in range(1, 16)}
    assert all(step.stream for case in suite.cases for step in case.steps)


def test_tc_s01_transcript_matches_intent_then_waiting_user_input() -> None:
    suite = load_regression_suite(SUITE_PATH)
    step = suite.case_by_id("TC-S01").steps[0]
    events = parse_sse_text(
        """
event: message
data: {"ok": true, "status": "running", "intent_code": "AG_TRANS", "completion_state": 0, "completion_reason": "intent_recognized", "stage": "intent_recognition", "output": {}}

event: message
data: {"ok": true, "status": "waiting_user_input", "intent_code": "AG_TRANS", "completion_state": 0, "completion_reason": "router_waiting_user_input", "slot_memory": {"payee_name": "小明"}, "message": "请提供金额", "output": {}}

event: done
data: [DONE]
"""
    )

    validate_step_transcript(step, events)


def test_tc_s04_router_only_ready_does_not_require_handover_output() -> None:
    suite = load_regression_suite(SUITE_PATH)
    step = suite.case_by_id("TC-S04").steps[-1]
    events = parse_sse_text(
        """
event: message
data: {"ok": true, "status": "ready_for_dispatch", "completion_state": 1, "completion_reason": "router_ready_for_dispatch", "slot_memory": {"payee_name": "小红", "amount": "200"}, "output": {}}

event: done
data: [DONE]
"""
    )

    validate_step_transcript(step, events)


def test_tc_s09_requires_recognition_task_and_graph_order_alignment() -> None:
    suite = load_regression_suite(SUITE_PATH)
    step = suite.case_by_id("TC-S09").steps[0]
    events = parse_sse_text(
        """
event: message
data: {"ok": true, "status": "running", "intent_code": "AG_QUERY_BALANCE", "completion_reason": "intent_recognized", "stage": "intent_recognition", "output": {}}

event: message
data: {"ok": true, "status": "waiting_assistant_completion", "completion_reason": "assistant_confirmation_required", "task_list": [{"taskId": "task_balance", "intent_code": "AG_QUERY_BALANCE"}, {"taskId": "task_transfer", "intent_code": "AG_TRANS"}], "current_task": {"taskId": "task_balance", "intent_code": "AG_QUERY_BALANCE"}, "output": {"interaction": {"type": "graph_card", "card_type": "dynamic_graph", "nodes": [{"id": "task_balance", "intent_code": "AG_QUERY_BALANCE"}, {"id": "task_transfer", "intent_code": "AG_TRANS"}]}}}

event: done
data: [DONE]
"""
    )

    validate_step_transcript(step, events)


def test_tc_s09_rejects_top_level_recognition_that_differs_from_first_task() -> None:
    suite = load_regression_suite(SUITE_PATH)
    step = suite.case_by_id("TC-S09").steps[0]
    events = parse_sse_text(
        """
event: message
data: {"ok": true, "status": "running", "intent_code": "AG_TRANS", "completion_reason": "intent_recognized", "stage": "intent_recognition", "output": {}}

event: message
data: {"ok": true, "status": "waiting_assistant_completion", "completion_reason": "assistant_confirmation_required", "task_list": [{"taskId": "task_balance", "intent_code": "AG_QUERY_BALANCE"}, {"taskId": "task_transfer", "intent_code": "AG_TRANS"}], "current_task": {"taskId": "task_balance", "intent_code": "AG_QUERY_BALANCE"}, "graph": {"nodes": [{"id": "task_balance", "intent_code": "AG_QUERY_BALANCE"}, {"id": "task_transfer", "intent_code": "AG_TRANS"}]}, "output": {}}

event: done
data: [DONE]
"""
    )

    with pytest.raises(ProtocolAssertionError, match="recognized intent"):
        validate_step_transcript(step, events)


def test_tc_s06_rejects_output_slot_memory_leak() -> None:
    suite = load_regression_suite(SUITE_PATH)
    step = suite.case_by_id("TC-S06").steps[0]
    events = parse_sse_text(
        """
event: message
data: {"ok": true, "status": "waiting_assistant_completion", "completion_state": 1, "completion_reason": "assistant_confirmation_required", "output": {"message": "done", "slot_memory": {"amount": "200"}}}

event: done
data: [DONE]
"""
    )

    with pytest.raises(ProtocolAssertionError, match="output.slot_memory"):
        validate_step_transcript(step, events)


def test_tc_s12_failure_transcript_must_not_emit_successful_recognition() -> None:
    suite = load_regression_suite(SUITE_PATH)
    step = suite.case_by_id("TC-S12").steps[0]
    events = parse_sse_text(
        """
event: message
data: {"ok": false, "status": "failed", "completion_state": 2, "completion_reason": "router_error", "errorCode": "ROUTER_LLM_UNAVAILABLE", "message": "意图识别服务暂不可用，请稍后重试。", "output": {}}

event: done
data: [DONE]
"""
    )

    validate_step_transcript(step, events)


def test_tc_s12_rejects_misleading_recognition_before_error() -> None:
    suite = load_regression_suite(SUITE_PATH)
    step = suite.case_by_id("TC-S12").steps[0]
    events = parse_sse_text(
        """
event: message
data: {"ok": true, "status": "running", "completion_reason": "intent_recognized", "stage": "intent_recognition", "output": {}}

event: message
data: {"ok": false, "status": "failed", "completion_state": 2, "completion_reason": "router_error", "output": {}}

event: done
data: [DONE]
"""
    )

    with pytest.raises(ProtocolAssertionError, match="intent_recognized"):
        validate_step_transcript(step, events)
