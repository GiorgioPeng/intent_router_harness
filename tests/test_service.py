from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
from threading import Thread

from intent_router_harness.server import create_server
from intent_router_harness.service import IntentRouterHarnessService, RenderPromptRequest


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


def test_http_server_exposes_only_health_and_business_routes(tmp_path: Path) -> None:
    service = IntentRouterHarnessService.from_spec(_write_demo_harness(tmp_path))
    server = create_server(service, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn: HTTPConnection | None = None
    try:
        host, port = server.server_address
        conn = HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/healthz")
        healthz_response = conn.getresponse()
        healthz_payload = json.loads(healthz_response.read().decode("utf-8"))

        conn.request("GET", "/readyz")
        readyz_response = conn.getresponse()
        readyz_payload = json.loads(readyz_response.read().decode("utf-8"))

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
        render_response = conn.getresponse()
        render_payload = json.loads(render_response.read().decode("utf-8"))

        conn.request("GET", "/regression/suite")
        regression_response = conn.getresponse()
        regression_payload = json.loads(regression_response.read().decode("utf-8"))
    finally:
        if conn is not None:
            conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert healthz_response.status == 200
    assert healthz_payload == {"status": "ok"}
    assert readyz_response.status == 200
    assert readyz_payload["ready"] is True
    assert render_response.status == 404
    assert render_payload["error"]["code"] == "not_found"
    assert regression_response.status == 404
    assert regression_payload["error"]["code"] == "not_found"
