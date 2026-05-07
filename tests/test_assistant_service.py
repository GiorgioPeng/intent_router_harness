from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
from threading import Thread

import pytest

from intent_router_harness.assistant_protocol import parse_sse_text
from intent_router_harness.contracts import (
    PlannedTask,
    PlannerOutput,
    RecognitionPlan,
    RouterMessageRequest,
    TaskCompletionRequest,
    TaskRuntimeState,
)
from intent_router_harness import load_prompt_harness
from intent_router_harness.planner import LLMMessagePlanner, PlannerError
from intent_router_harness.regression import load_regression_suite, validate_step_transcript
from intent_router_harness.server import create_server
from intent_router_harness.service import IntentRouterHarnessService
from intent_router_harness.session_store import InMemorySessionStore


SUITE_PATH = "regressions/assistant_protocol_v0_5.json"


class StaticPlanner:
    def __init__(self, output: PlannerOutput) -> None:
        self.output = output

    def plan_message(
        self,
        request: RouterMessageRequest,
        task_state: TaskRuntimeState,
    ) -> PlannerOutput:
        return self.output


class FakeLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def chat(self, messages, max_tokens=None):
        del messages, max_tokens
        content = self.responses.pop(0)
        return {
            "model": "fake-llm",
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": "stop",
                }
            ],
        }


def _write_minimal_harness(tmp_path: Path) -> Path:
    spec_path = tmp_path / "harness.toml"
    spec_path.write_text(
        "\n".join(
            [
                'name = "assistant-protocol-test"',
                'version = "2026.04"',
                "",
                "[surfaces.task_planning]",
                'system = "返回 planner JSON。"',
                'human = "用户消息：{message}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return spec_path


def test_v1_message_stream_emits_recognition_before_business(tmp_path: Path) -> None:
    task = PlannedTask(
        taskId="task_transfer",
        intent_code="AG_TRANS",
        status="waiting_user_input",
        title="给小明转账",
        slot_memory={"payee_name": "小明"},
    )
    planner = StaticPlanner(
        PlannerOutput(
            mode="slot_filling",
            status="waiting_user_input",
            completion_state=0,
            completion_reason="router_waiting_user_input",
            intent_code="AG_TRANS",
            recognition=RecognitionPlan(intent_code="AG_TRANS"),
            slot_memory={"payee_name": "小明"},
            task_list=[task],
            current_task=task,
            message="请提供金额",
        )
    )
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=planner,
    )
    server = create_server(service, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn: HTTPConnection | None = None
    try:
        host, port = server.server_address
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            "/api/v1/message",
            body=json.dumps(
                {
                    "sessionId": "assistant_tc_s01",
                    "txt": "给小明转账",
                    "stream": True,
                    "custId": "C0001",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        if conn is not None:
            conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    events = parse_sse_text(body)
    step = load_regression_suite(SUITE_PATH).case_by_id("TC-S01").steps[0]

    assert response.status == 200
    assert [event.event for event in events] == ["message", "message", "done"]
    validate_step_transcript(step, events)


def test_v1_message_stream_can_emit_debug_trace_events(tmp_path: Path) -> None:
    task = PlannedTask(
        taskId="task_transfer",
        intent_code="AG_TRANS",
        status="waiting_user_input",
        title="给小明转账",
    )
    planner = StaticPlanner(
        PlannerOutput(
            mode="slot_filling",
            status="waiting_user_input",
            completion_state=0,
            completion_reason="router_waiting_user_input",
            intent_code="AG_TRANS",
            recognition=RecognitionPlan(intent_code="AG_TRANS"),
            task_list=[task],
            current_task=task,
            message="请提供金额",
        )
    )
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=planner,
    )
    server = create_server(service, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn: HTTPConnection | None = None
    try:
        host, port = server.server_address
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            "/api/v1/message",
            body=json.dumps(
                {
                    "sessionId": "assistant_trace",
                    "txt": "给小明转账",
                    "stream": True,
                    "debugTrace": True,
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        if conn is not None:
            conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    events = parse_sse_text(body)
    trace_events = [event for event in events if event.event == "trace"]
    message_events = [event for event in events if event.event == "message"]

    assert response.status == 200
    assert len(trace_events) >= 4
    assert trace_events[0].data["stage"] == "request_received"
    assert any(event.data["stage"] == "intent_recognition" for event in trace_events)
    assert len(message_events) == 2
    assert events[-1].is_done


def test_v1_message_non_stream_returns_final_business_frame(tmp_path: Path) -> None:
    task = PlannedTask(
        taskId="task_transfer",
        intent_code="AG_TRANS",
        status="ready_for_dispatch",
        title="给小明转账200",
        slot_memory={"payee_name": "小明", "amount": "200"},
        output={"ishandover": True, "handOverReason": "router_only_ready_for_dispatch"},
    )
    planner = StaticPlanner(
        PlannerOutput(
            mode="single_task",
            status="ready_for_dispatch",
            completion_state=0,
            completion_reason="router_ready_for_dispatch",
            intent_code="AG_TRANS",
            recognition=RecognitionPlan(intent_code="AG_TRANS"),
            slot_memory={"payee_name": "小明", "amount": "200"},
            task_list=[task],
            current_task=task,
            output={"ishandover": True, "handOverReason": "router_only_ready_for_dispatch"},
        )
    )
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=planner,
    )
    server = create_server(service, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn: HTTPConnection | None = None
    try:
        host, port = server.server_address
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            "/api/v1/message",
            body=json.dumps(
                {
                    "sessionId": "assistant_non_stream",
                    "txt": "给小明转账200",
                    "stream": False,
                    "executionMode": "router_only",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        if conn is not None:
            conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert payload["status"] == "ready_for_dispatch"
    assert payload["completion_reason"] == "router_ready_for_dispatch"
    assert payload["output"]["ishandover"] is True
    assert "snapshot" not in payload


def test_task_completion_stream_confirms_current_task(tmp_path: Path) -> None:
    task = PlannedTask(
        taskId="task_transfer",
        intent_code="AG_TRANS",
        status="waiting_assistant_completion",
        title="给小明转账200",
        slot_memory={"payee_name": "小明", "amount": "200"},
        output={"message": "已向小明转账 200 CNY，转账成功"},
    )
    planner = StaticPlanner(
        PlannerOutput(
            mode="single_task",
            status="waiting_assistant_completion",
            completion_state=1,
            completion_reason="assistant_confirmation_required",
            intent_code="AG_TRANS",
            recognition=RecognitionPlan(intent_code="AG_TRANS"),
            slot_memory={"payee_name": "小明", "amount": "200"},
            task_list=[task],
            current_task=task,
            output={"message": "已向小明转账 200 CNY，转账成功"},
        )
    )
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=planner,
    )
    server = create_server(service, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn: HTTPConnection | None = None
    try:
        host, port = server.server_address
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            "/api/v1/message",
            body=json.dumps(
                {"sessionId": "assistant_tc_completion", "txt": "给小明转账200", "stream": False}
            ),
            headers={"Content-Type": "application/json"},
        )
        conn.getresponse().read()
        conn.request(
            "POST",
            "/api/v1/task/completion",
            body=json.dumps(
                {
                    "sessionId": "assistant_tc_completion",
                    "taskId": "task_transfer",
                    "completionSignal": 2,
                    "stream": True,
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        if conn is not None:
            conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    events = parse_sse_text(body)

    assert response.status == 200
    assert [event.event for event in events] == ["message", "done"]
    assert events[0].data["status"] == "completed"
    assert events[0].data["completion_reason"] == "assistant_final_done"
    assert events[-1].is_done


def test_final_task_completion_clears_active_runtime_memory(tmp_path: Path) -> None:
    task = PlannedTask(
        taskId="task_transfer",
        intent_code="AG_TRANS",
        status="waiting_assistant_completion",
        title="给小明转账200",
        slot_memory={"payee_name": "小明", "amount": "200"},
        output={"message": "done"},
    )
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=StaticPlanner(
            PlannerOutput(
                mode="single_task",
                status="waiting_assistant_completion",
                completion_state=1,
                completion_reason="assistant_confirmation_required",
                intent_code="AG_TRANS",
                recognition=RecognitionPlan(intent_code="AG_TRANS"),
                task_list=[task],
                current_task=task,
                output={"message": "done"},
                diagnostics={
                    "_router_context": {
                        "skill_names": ["finance-routing"],
                        "reference_ids": ["ref_001"],
                    }
                },
            )
        ),
    )
    assert service.assistant is not None

    service.handle_message(RouterMessageRequest(sessionId="final_clear_session", txt="给小明转账200"))
    result = service.handle_task_completion(
        TaskCompletionRequest(
            sessionId="final_clear_session",
            taskId="task_transfer",
            completionSignal=2,
        )
    )
    saved = service.assistant.sessions.get_task_state("final_clear_session")

    assert result.final_frame.status == "completed"
    assert saved.current_task is None
    assert saved.slot_memory == {}
    assert saved.task_list == []
    assert saved.active_context == {}
    assert saved.context_leases == []


def test_assistant_service_saves_and_releases_context_lease(tmp_path: Path) -> None:
    task = PlannedTask(
        taskId="task_transfer",
        intent_code="AG_TRANS",
        status="waiting_user_input",
        title="转账",
    )
    planner = StaticPlanner(
        PlannerOutput(
            mode="slot_filling",
            status="waiting_user_input",
            completion_state=0,
            completion_reason="router_waiting_user_input",
            intent_code="AG_TRANS",
            recognition=RecognitionPlan(intent_code="AG_TRANS"),
            task_list=[task],
            current_task=task,
            message="请提供金额",
            diagnostics={
                "_router_context": {
                    "agent_contexts": ["/tmp/agent.md"],
                    "metadata_skills": ["finance-routing"],
                    "skill_names": ["finance-routing"],
                    "reference_ids": ["ref_001"],
                }
            },
        )
    )
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=planner,
    )
    assert service.assistant is not None

    service.handle_message(
        RouterMessageRequest(
            sessionId="lease_session",
            txt="我要转账",
            stream=False,
            executionMode="router_only",
        )
    )
    task_state = service.assistant.sessions.get_task_state("lease_session")

    assert task_state.active_context["task_id"] == "task_transfer"
    assert task_state.active_context["skill_names"] == ["finance-routing"]
    assert task_state.active_context["reference_ids"] == ["ref_001"]

    service.handle_task_completion(
        TaskCompletionRequest(
            sessionId="lease_session",
            taskId="task_transfer",
            completionSignal=2,
            stream=False,
        )
    )

    saved_after_completion = service.assistant.sessions.get_task_state("lease_session")
    assert saved_after_completion.task_list == []
    assert saved_after_completion.active_context == {}


def test_terminal_message_plan_clears_persisted_current_task_and_context(tmp_path: Path) -> None:
    task = PlannedTask(
        taskId="task_transfer",
        intent_code="AG_TRANS",
        status="cancelled",
        title="转账",
        slot_memory={"payee_name": "小明"},
    )
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=StaticPlanner(
            PlannerOutput(
                mode="cancel",
                status="cancelled",
                completion_state=2,
                completion_reason="assistant_cancel",
                intent_code="AG_TRANS",
                recognition=RecognitionPlan(intent_code="AG_TRANS"),
                slot_memory={"payee_name": "小明"},
                task_list=[task],
                current_task=task,
                message="已取消转账任务",
                diagnostics={
                    "_router_context": {
                        "skill_names": ["finance-routing"],
                        "reference_ids": ["ref_001"],
                    }
                },
            )
        ),
    )
    assert service.assistant is not None

    result = service.handle_message(
        RouterMessageRequest(sessionId="cancel_session", txt="取消")
    )
    saved = service.assistant.sessions.get_task_state("cancel_session")

    assert result.final_frame.status == "cancelled"
    assert result.final_frame.current_task is not None
    assert saved.current_task is None
    assert saved.slot_memory == {}
    assert saved.task_list == []
    assert saved.active_context == {}
    assert saved.context_leases == []


def test_terminal_current_task_without_task_list_updates_runtime_state(tmp_path: Path) -> None:
    initial_task = PlannedTask(
        taskId="task_transfer",
        intent_code="AG_TRANS",
        status="waiting_user_input",
        title="转账",
        slot_memory={"payee_name": "小明"},
    )
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=StaticPlanner(
            PlannerOutput(
                mode="slot_filling",
                status="waiting_user_input",
                completion_state=0,
                completion_reason="router_waiting_user_input",
                intent_code="AG_TRANS",
                recognition=RecognitionPlan(intent_code="AG_TRANS"),
                task_list=[initial_task],
                current_task=initial_task,
                diagnostics={
                    "_router_context": {
                        "skill_names": ["finance-routing"],
                    }
                },
            )
        ),
    )
    assert service.assistant is not None
    service.handle_message(RouterMessageRequest(sessionId="terminal_without_list_session", txt="我要转账"))

    cancelled_task = initial_task.model_copy(update={"status": "cancelled"})
    service.assistant.planner = StaticPlanner(
        PlannerOutput(
            mode="cancel",
            status="cancelled",
            completion_state=2,
            completion_reason="assistant_cancel",
            intent_code="AG_TRANS",
            recognition=RecognitionPlan(intent_code="AG_TRANS"),
            current_task=cancelled_task,
            message="已取消转账任务",
        )
    )

    service.handle_message(RouterMessageRequest(sessionId="terminal_without_list_session", txt="取消"))
    saved = service.assistant.sessions.get_task_state("terminal_without_list_session")

    assert saved.current_task is None
    assert saved.slot_memory == {}
    assert saved.active_context == {}
    assert saved.task_list == []


def test_task_completion_advances_to_next_waiting_task(tmp_path: Path) -> None:
    first_task = PlannedTask(
        taskId="task_001",
        intent_code="AG_TRANS",
        status="ready_for_dispatch",
        title="转账给王阳明",
        slot_memory={"payee_name": "王阳明", "amount": "100"},
    )
    second_task = PlannedTask(
        taskId="task_002",
        intent_code="AG_TRANS",
        status="waiting_user_input",
        title="转账给李正义",
        slot_memory={"payee_name": "李正义"},
    )
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=StaticPlanner(
            PlannerOutput(
                mode="multi_task",
                status="ready_for_dispatch",
                completion_state=0,
                completion_reason="router_ready_for_dispatch",
                intent_code="AG_TRANS",
                recognition=RecognitionPlan(intent_code="AG_TRANS"),
                slot_memory={"payee_name": "王阳明", "amount": "100"},
                task_list=[first_task, second_task],
                current_task=first_task,
                output={
                    "ishandover": True,
                    "handOverReason": "router_only_ready_for_dispatch",
                },
                diagnostics={
                    "_router_context": {
                        "agent_contexts": ["/tmp/agent.md"],
                        "metadata_skills": ["finance-routing"],
                        "skill_names": ["finance-routing"],
                        "reference_ids": [],
                    }
                },
            )
        ),
    )
    assert service.assistant is not None

    service.handle_message(
        RouterMessageRequest(
            sessionId="multi_completion_session",
            txt="我要先给王阳明转账，再给李正义转账",
            executionMode="router_only",
        )
    )
    result = service.handle_task_completion(
        TaskCompletionRequest(
            sessionId="multi_completion_session",
            taskId="task_001",
            completionSignal=2,
        )
    )
    saved = service.assistant.sessions.get_task_state("multi_completion_session")

    assert [frame.status for frame in result.frames] == ["completed", "waiting_user_input"]
    assert result.final_frame.current_task is not None
    assert result.final_frame.current_task["taskId"] == "task_002"
    assert result.final_frame.slot_memory == {"payee_name": "李正义"}
    assert saved.current_task is not None
    assert saved.current_task.taskId == "task_002"
    assert saved.slot_memory == {"payee_name": "李正义"}
    assert [task.taskId for task in saved.task_list] == ["task_002"]
    assert saved.active_context["task_id"] == "task_002"


def test_current_task_status_is_not_downgraded_to_running(tmp_path: Path) -> None:
    current_task = PlannedTask(
        taskId="task_001",
        intent_code="AG_TRANS",
        status="waiting_user_input",
        title="转账给王阳明",
        slot_memory={"payee_name": "王阳明"},
    )
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=StaticPlanner(
            PlannerOutput(
                mode="multi_task",
                status="running",
                completion_state=0,
                completion_reason="router_waiting_user_input",
                intent_code="AG_TRANS",
                recognition=RecognitionPlan(intent_code="AG_TRANS"),
                slot_memory={"payee_name": "王阳明"},
                task_list=[
                    current_task,
                    PlannedTask(
                        taskId="task_002",
                        intent_code="AG_TRANS",
                        status="waiting_user_input",
                        title="转账给李正义",
                        slot_memory={"payee_name": "李正义"},
                    ),
                ],
                current_task=current_task,
                message="请提供给王阳明的转账金额",
            )
        ),
    )
    assert service.assistant is not None

    result = service.handle_message(
        RouterMessageRequest(
            sessionId="multi_status_session",
            txt="我要先给王阳明转账，再给李正义转账",
        )
    )
    saved = service.assistant.sessions.get_task_state("multi_status_session")

    assert result.final_frame.status == "waiting_user_input"
    assert result.final_frame.current_task is not None
    assert result.final_frame.current_task["status"] == "waiting_user_input"
    assert result.final_frame.current_task["slot_memory"] == {"payee_name": "王阳明"}
    assert result.final_frame.task_list[0]["status"] == "waiting_user_input"
    assert saved.current_task is not None
    assert saved.current_task.status == "waiting_user_input"


def test_current_task_slot_memory_updates_protocol_and_session(tmp_path: Path) -> None:
    current_task = PlannedTask(
        taskId="task_001",
        intent_code="AG_TRANS",
        status="ready_for_dispatch",
        title="转账给王阳明",
        slot_memory={"payee_name": "王阳明", "amount": "100"},
    )
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=StaticPlanner(
            PlannerOutput(
                mode="slot_filling",
                status="ready_for_dispatch",
                completion_state=0,
                completion_reason="router_ready_for_dispatch",
                intent_code="AG_TRANS",
                recognition=RecognitionPlan(intent_code="AG_TRANS"),
                slot_memory={"payee_name": "王阳明"},
                task_list=[current_task],
                current_task=current_task,
                output={
                    "ishandover": True,
                    "handOverReason": "router_only_ready_for_dispatch",
                },
            )
        ),
    )
    assert service.assistant is not None

    result = service.handle_message(
        RouterMessageRequest(
            sessionId="current_task_slots_session",
            txt="100元",
            executionMode="router_only",
        )
    )
    saved = service.assistant.sessions.get_task_state("current_task_slots_session")

    assert result.final_frame.slot_memory == {"payee_name": "王阳明", "amount": "100"}
    assert saved.slot_memory == {"payee_name": "王阳明", "amount": "100"}


def test_session_binds_to_single_user_and_rejects_mismatch(tmp_path: Path) -> None:
    task = PlannedTask(taskId="task_transfer", intent_code="AG_TRANS", status="waiting_user_input")
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=StaticPlanner(
            PlannerOutput(
                mode="slot_filling",
                status="waiting_user_input",
                completion_state=0,
                completion_reason="router_waiting_user_input",
                intent_code="AG_TRANS",
                recognition=RecognitionPlan(intent_code="AG_TRANS"),
                task_list=[task],
                current_task=task,
                message="请提供金额",
            )
        ),
    )
    assert service.assistant is not None

    service.handle_message(
        RouterMessageRequest(
            sessionId="owned_session",
            txt="我要转账",
            config_variables=[{"name": "cust_no", "value": "cust_001"}],
        )
    )

    try:
        service.handle_message(
            RouterMessageRequest(
                sessionId="owned_session",
                txt="我要转账",
                config_variables=[{"name": "cust_no", "value": "cust_002"}],
            )
        )
    except RuntimeError as exc:
        assert "already bound" in str(exc)
    else:
        raise AssertionError("session user mismatch should be rejected")


def test_session_expires_after_idle_timeout_and_clears_memory(tmp_path: Path) -> None:
    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)

    def clock() -> datetime:
        return now

    task = PlannedTask(taskId="task_transfer", intent_code="AG_TRANS", status="waiting_user_input")
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=StaticPlanner(
            PlannerOutput(
                mode="slot_filling",
                status="waiting_user_input",
                completion_state=0,
                completion_reason="router_waiting_user_input",
                intent_code="AG_TRANS",
                recognition=RecognitionPlan(intent_code="AG_TRANS"),
                slot_memory={"payee_name": "小明"},
                task_list=[task],
                current_task=task,
                message="请提供金额",
                diagnostics={
                    "_router_context": {
                        "skill_names": ["finance-routing"],
                        "reference_ids": ["ref_001"],
                    }
                },
            )
        ),
    )
    assert service.assistant is not None
    service.assistant.sessions = InMemorySessionStore(clock=clock)

    service.handle_message(
        RouterMessageRequest(
            sessionId="expiring_session",
            txt="给小明转账",
            config_variables=[{"name": "cust_no", "value": "cust_001"}],
        )
    )
    saved = service.assistant.sessions.get_task_state("expiring_session")
    assert saved.slot_memory == {"payee_name": "小明"}
    assert saved.context_leases

    now = now + timedelta(minutes=31)
    expired = service.assistant.sessions.load("expiring_session", user_binding_id="cust_001")

    assert expired.expired is True
    assert expired.task_state.slot_memory == {}
    assert expired.task_state.task_list == []
    assert expired.task_state.current_task is None
    assert expired.task_state.active_context == {}
    assert expired.task_state.context_leases == []


def test_task_completion_rejects_replay_after_runtime_task_is_cleared(tmp_path: Path) -> None:
    task = PlannedTask(
        taskId="task_transfer",
        intent_code="AG_TRANS",
        status="waiting_assistant_completion",
        output={"message": "done"},
    )
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=StaticPlanner(
            PlannerOutput(
                mode="single_task",
                status="waiting_assistant_completion",
                completion_state=1,
                completion_reason="assistant_confirmation_required",
                intent_code="AG_TRANS",
                recognition=RecognitionPlan(intent_code="AG_TRANS"),
                task_list=[task],
                current_task=task,
                output={"message": "done"},
            )
        ),
    )
    assert service.assistant is not None

    service.handle_message(RouterMessageRequest(sessionId="replay_session", txt="给小明转账200"))
    first = service.handle_task_completion(
        TaskCompletionRequest(
            sessionId="replay_session",
            taskId="task_transfer",
            completionSignal=2,
        )
    )
    second = service.handle_task_completion(
        TaskCompletionRequest(
            sessionId="replay_session",
            taskId="task_transfer",
            completionSignal=2,
        )
    )

    assert first.final_frame.status == "completed"
    assert second.final_frame.ok is False
    assert second.final_frame.errorCode == "TASK_NOT_FOUND"


def test_llm_planner_rejects_intent_not_declared_by_loaded_skill(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "transfer-routing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: transfer-routing",
                "description: 转账路由规则",
                'surfaces: ["scene_selection", "task_planning"]',
                'intent_codes: ["AG_TRANS"]',
                'domain_codes: ["finance"]',
                'capabilities: ["routing", "slots", "planning"]',
                "---",
                "# 转账",
                "只能输出 AG_TRANS。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    spec_path = tmp_path / "planner-harness.toml"
    spec_path.write_text(
        "\n".join(
            [
                'name = "planner-intent-validation"',
                'version = "2026.05"',
                f'skill_roots = ["{skills_root.as_posix()}"]',
                "",
                "[surfaces.scene_selection]",
                'system = "选择 skill。"',
                'human = "用户消息：{message}"',
                "include_skill_index = true",
                "",
                "[surfaces.task_planning]",
                'system = "输出 planner JSON。"',
                'human = "用户消息：{message}\\n任务运行态：{task_state_json}\\nSchema：{planner_output_schema_json}"',
                "include_skill_index = true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    harness = load_prompt_harness(spec_path)
    assert harness is not None
    planner = LLMMessagePlanner(
        harness=harness,
        llm_client=FakeLLMClient(
            [
                '{"skill_names":["transfer-routing"],"reason":"transfer"}',
                json.dumps(
                    {
                        "mode": "single_task",
                        "status": "waiting_user_input",
                        "completion_reason": "router_waiting_user_input",
                        "intent_code": "AG_BALANCE",
                        "recognition": {"intent_code": "AG_BALANCE"},
                        "task_list": [
                            {
                                "taskId": "task_balance",
                                "intent_code": "AG_BALANCE",
                                "status": "waiting_user_input",
                            }
                        ],
                        "current_task": {
                            "taskId": "task_balance",
                            "intent_code": "AG_BALANCE",
                            "status": "waiting_user_input",
                        },
                    }
                ),
            ]
        ),
    )

    with pytest.raises(PlannerError, match="not declared by loaded skills"):
        planner.plan_message(
            RouterMessageRequest(sessionId="invalid_intent_session", txt="我要转账"),
            TaskRuntimeState(),
        )
