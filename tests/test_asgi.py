from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from intent_router_harness.asgi import create_app
from intent_router_harness.config import AppSettings
from intent_router_harness.contracts import (
    PlannedTask,
    PlannerOutput,
    RecognitionPlan,
    RouterMessageRequest,
    SessionState,
)
from intent_router_harness.service import IntentRouterHarnessService


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
                'name = "asgi-test"',
                'version = "2026.04"',
                "",
                "[surfaces.intent_recognition]",
                'system = "Classify."',
                'human = "Message: {message}"',
                "",
                "[surfaces.task_planning]",
                'system = "Plan."',
                'human = "Message: {message}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return spec_path


def test_asgi_health_ready_and_render(tmp_path: Path) -> None:
    settings = AppSettings(
        spec_path=_write_minimal_harness(tmp_path),
        regression_suite_path=None,
        llm_env_file=None,
    )
    app = create_app(settings)
    client = TestClient(app)

    healthz = client.get("/healthz")
    readyz = client.get("/readyz")
    render = client.post(
        "/render",
        json={
            "surface": "intent_recognition",
            "variables": {"message": "hello"},
        },
    )

    assert healthz.status_code == 200
    assert healthz.json() == {"status": "ok"}
    assert readyz.status_code == 200
    assert readyz.json()["llm_configured"] is False
    assert render.status_code == 200
    assert render.json()["messages"][1]["content"] == "Message: hello"


def test_asgi_message_stream_uses_assistant_protocol_service(tmp_path: Path) -> None:
    task = PlannedTask(
        taskId="task_transfer",
        intent_code="AG_TRANS",
        status="ready_for_dispatch",
        slot_memory={"payee_name": "小明", "amount": "200"},
        output={"ishandover": True, "handOverReason": "router_only_ready_for_dispatch"},
    )
    service = IntentRouterHarnessService.from_spec(
        _write_minimal_harness(tmp_path),
        message_planner=StaticPlanner(
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
        ),
    )
    app = create_app(
        AppSettings(spec_path=_write_minimal_harness(tmp_path), regression_suite_path=None),
        service=service,
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/message",
        json={
            "sessionId": "asgi_session_001",
            "txt": "给小明转账200",
            "stream": True,
            "executionMode": "router_only",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count("event: message") == 2
    assert "event: done" in response.text
    assert "router_ready_for_dispatch" in response.text
