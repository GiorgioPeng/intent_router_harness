from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz(app) -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_debug_page(app) -> None:
    client = TestClient(app)
    response = client.get("/debug")
    assert response.status_code == 200
    assert "Router Debug" in response.text
    assert "/api/v1/task/handoff" in response.text
    assert "执行 TODO 列表" in response.text
    assert "mockDispatch" in response.text
    assert "/v1/router/skills/index" in response.text
    assert "autoRun" in response.text


def test_message_api_non_stream(app) -> None:
    client = TestClient(app)
    response = client.post(
        "/api/v1/message",
        json={"sessionId": "api-1", "cust_no": "c1", "txt": "我要转账"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "collecting_slots"
    assert payload["currentTask"]["intentCode"] == "transfer"
    assert payload["todoList"][0]["name"] == "transfer"
    assert payload["todoList"][0]["current"] is True
    assert payload["tasks"] == []
    assert payload["trace"] is None


def test_stream_and_non_stream_final_semantics_match(app) -> None:
    client = TestClient(app)
    non_stream = client.post(
        "/api/v1/message",
        json={"sessionId": "api-2a", "cust_no": "c1", "txt": "转账 200 给小明"},
    ).json()
    stream_response = client.post(
        "/api/v1/message",
        json={"sessionId": "api-2b", "cust_no": "c1", "txt": "转账 200 给小明", "stream": True},
    )

    assert stream_response.status_code == 200
    body = stream_response.text
    assert "event: done" in body
    assert f'"status":"{non_stream["status"]}"' in body
    assert '"currentTask"' in body
    assert '"todoList"' in body


def test_session_owner_conflict(app) -> None:
    client = TestClient(app)
    client.post("/api/v1/message", json={"sessionId": "api-3", "cust_no": "c1", "txt": "我要转账"})

    response = client.post(
        "/api/v1/message",
        json={"sessionId": "api-3", "cust_no": "c2", "txt": "我要转账"},
    )

    assert response.status_code == 409
