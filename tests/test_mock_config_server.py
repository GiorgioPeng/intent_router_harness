from __future__ import annotations

from fastapi.testclient import TestClient

from examples.mock_config_server import app


def test_mock_skill_body_loads_from_skill_file() -> None:
    client = TestClient(app)

    response = client.get("/v1/router/skills/skill_transfer/body?version=v1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["skillId"] == "skill_transfer"
    assert payload["version"] == "v1"
    assert payload["slotContract"][0]["name"] == "recipient"


def test_mock_raw_skill_returns_original_skill_file() -> None:
    client = TestClient(app)

    response = client.get("/v1/router/skills/skill_transfer/raw")

    assert response.status_code == 200
    assert "name: transfer" in response.text
    assert "# 转账规则" in response.text


def test_mock_follow_up_skill_is_loadable_but_not_exposed() -> None:
    client = TestClient(app)

    index = client.get("/v1/router/skills/index")
    body = client.get("/v1/router/skills/skill_follow_up/body?version=v1")

    assert index.status_code == 200
    assert "skill_follow_up" not in index.text
    assert body.status_code == 200
    assert "你还想进行什么操作" in body.json()["rulesMd"]
