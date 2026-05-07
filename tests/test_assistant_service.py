from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
from threading import Thread

from intent_router_harness.assistant_protocol import parse_sse_text
from intent_router_harness.contracts import (
    PlannedTask,
    PlannerOutput,
    RecognitionPlan,
    RouterMessageRequest,
    SessionState,
    TaskCompletionRequest,
)
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
        session: SessionState,
    ) -> PlannerOutput:
        return self.output


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
    session = service.assistant.sessions.get_or_create("lease_session")

    assert session.active_context["task_id"] == "task_transfer"
    assert session.active_context["skill_names"] == ["finance-routing"]
    assert session.active_context["reference_ids"] == ["ref_001"]

    service.handle_task_completion(
        TaskCompletionRequest(
            sessionId="lease_session",
            taskId="task_transfer",
            completionSignal=2,
            stream=False,
        )
    )

    assert service.assistant.sessions.get_or_create("lease_session").active_context == {}


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
    saved = service.assistant.sessions.get_or_create("expiring_session")
    assert saved.slot_memory == {"payee_name": "小明"}
    assert saved.context_leases

    now = now + timedelta(minutes=31)
    expired = service.assistant.sessions.load("expiring_session", user_binding_id="cust_001")

    assert expired.expired is True
    assert expired.session.slot_memory == {}
    assert expired.session.task_list == []
    assert expired.session.current_task is None
    assert expired.session.active_context == {}
    assert expired.session.context_leases == []


def test_task_completion_rejects_terminal_task_replay(tmp_path: Path) -> None:
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
    assert second.final_frame.errorCode == "TASK_ALREADY_TERMINAL"
