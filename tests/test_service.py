from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import Any

from intent_router_harness.assistant_protocol import parse_sse_text
from intent_router_harness.server import create_server
from intent_router_harness.service import IntentRouterHarnessService, RenderPromptRequest

SUITE_PATH = "regressions/assistant_protocol_v0_5.json"


class FakeLLMClient:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(model="fake-model")

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        assert messages[0]["role"] == "system"
        assert max_tokens == 128
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"intent_code": "AG_TRANS", "diagnostics": {}}'
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"total_tokens": 42},
        }


def _write_demo_harness(tmp_path: Path) -> Path:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "transfer-routing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: transfer-routing",
                "description: Transfer routing rules for finance intents",
                'surfaces: ["intent_recognition"]',
                'intent_codes: ["transfer"]',
                'domain_codes: ["finance"]',
                'capabilities: ["routing"]',
                "---",
                "# Transfer Routing",
                "",
                "Treat recipient names, amount, account numbers, and card suffixes as slots.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    spec_path = tmp_path / "intent-router-harness.toml"
    spec_path.write_text(
        "\n".join(
            [
                'name = "finance-router-harness"',
                'version = "2026.04"',
                f'skill_roots = ["{skills_root.as_posix()}"]',
                "",
                "[surfaces.intent_recognition]",
                'system = "Classify the message."',
                'human = "Message: {message}"',
                "include_skill_index = true",
                "",
                "[[bindings]]",
                'skill = "transfer-routing"',
                'surfaces = ["intent_recognition"]',
                'intent_codes = ["transfer"]',
                'load = "body"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return spec_path


def test_service_renders_prompt_response(tmp_path: Path) -> None:
    service = IntentRouterHarnessService.from_spec(_write_demo_harness(tmp_path))

    health = service.health()
    response = service.render(
        RenderPromptRequest(
            surface="intent_recognition",
            variables={
                "message": "transfer 500 to Alice",
            },
            intent_codes=["transfer"],
            domain_codes=["finance"],
            capabilities=["routing"],
        )
    )

    assert health.name == "finance-router-harness"
    assert health.surfaces == ["intent_recognition"]
    assert response.messages[0]["role"] == "system"
    assert response.loaded_skills == ["transfer-routing"]
    assert "Treat recipient names" in response.system


def test_http_server_render_endpoint(tmp_path: Path) -> None:
    service = IntentRouterHarnessService.from_spec(_write_demo_harness(tmp_path))
    server = create_server(service, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn: HTTPConnection | None = None
    try:
        host, port = server.server_address
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            "/render",
            body=json.dumps(
                {
                    "surface": "intent_recognition",
                    "stream": False,
                    "variables": {
                        "message": "transfer 500 to Alice",
                    },
                    "intent_codes": ["transfer"],
                    "domain_codes": ["finance"],
                    "capabilities": ["routing"],
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        http_response = conn.getresponse()
        payload = json.loads(http_response.read().decode("utf-8"))
    finally:
        if conn is not None:
            conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert http_response.status == 200
    assert http_response.getheader("Content-Type") == "application/json; charset=utf-8"
    assert payload["loaded_skills"] == ["transfer-routing"]
    assert payload["messages"][1]["content"].startswith("Message: transfer 500 to Alice")


def test_http_server_render_endpoint_streams_sse_when_requested(tmp_path: Path) -> None:
    service = IntentRouterHarnessService.from_spec(_write_demo_harness(tmp_path))
    server = create_server(service, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn: HTTPConnection | None = None
    try:
        host, port = server.server_address
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            "/render",
            body=json.dumps(
                {
                    "surface": "intent_recognition",
                    "stream": True,
                    "variables": {
                        "message": "transfer 500 to Alice",
                    },
                    "intent_codes": ["transfer"],
                    "domain_codes": ["finance"],
                    "capabilities": ["routing"],
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        http_response = conn.getresponse()
        body = http_response.read().decode("utf-8")
    finally:
        if conn is not None:
            conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    events = parse_sse_text(body)

    assert http_response.status == 200
    assert http_response.getheader("Content-Type") == "text/event-stream; charset=utf-8"
    assert [event.event for event in events] == ["message", "done"]
    assert events[0].data["loaded_skills"] == ["transfer-routing"]
    assert events[-1].is_done


def test_http_server_llm_render_endpoint_uses_configured_client(tmp_path: Path) -> None:
    service = IntentRouterHarnessService.from_spec(
        _write_demo_harness(tmp_path),
        llm_client=FakeLLMClient(),
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
            "/llm/render",
            body=json.dumps(
                {
                    "surface": "intent_recognition",
                    "stream": False,
                    "max_tokens": 128,
                    "variables": {
                        "message": "transfer 500 to Alice",
                    },
                    "intent_codes": ["transfer"],
                    "domain_codes": ["finance"],
                    "capabilities": ["routing"],
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        http_response = conn.getresponse()
        payload = json.loads(http_response.read().decode("utf-8"))
    finally:
        if conn is not None:
            conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert http_response.status == 200
    assert payload["model"] == "fake-model"
    assert payload["json_valid"] is True
    assert payload["parsed_json"]["intent_code"] == "AG_TRANS"
    assert payload["prompt"]["loaded_skills"] == ["transfer-routing"]


def test_http_server_exposes_regression_suite_and_validation(tmp_path: Path) -> None:
    service = IntentRouterHarnessService.from_spec(
        _write_demo_harness(tmp_path),
        regression_suite_path=SUITE_PATH,
    )
    server = create_server(service, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn: HTTPConnection | None = None
    try:
        host, port = server.server_address
        conn = HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/regression/suite")
        suite_response = conn.getresponse()
        suite_payload = json.loads(suite_response.read().decode("utf-8"))

        conn.request(
            "POST",
            "/regression/validate",
            body=json.dumps(
                {
                    "case_id": "TC-S01",
                    "step_name": "message_missing_amount",
                    "sse_text": """
event: message
data: {"ok": true, "status": "running", "intent_code": "AG_TRANS", "completion_state": 0, "completion_reason": "intent_recognized", "stage": "intent_recognition", "output": {}}

event: message
data: {"ok": true, "status": "waiting_user_input", "intent_code": "AG_TRANS", "completion_state": 0, "completion_reason": "router_waiting_user_input", "slot_memory": {"payee_name": "小明"}, "message": "请提供金额", "output": {}}

event: done
data: [DONE]
""",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        validation_response = conn.getresponse()
        validation_payload = json.loads(validation_response.read().decode("utf-8"))
    finally:
        if conn is not None:
            conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert suite_response.status == 200
    assert suite_payload["version"] == "0.5"
    assert any(case["id"] == "TC-S04B" for case in suite_payload["cases"])
    assert validation_response.status == 200
    assert validation_payload == {
        "ok": True,
        "case_id": "TC-S01",
        "step_name": "message_missing_amount",
        "errors": [],
    }
