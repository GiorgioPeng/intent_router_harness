from __future__ import annotations

from pathlib import Path

import pytest

from intent_router_harness import load_prompt_harness


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
                "Do not split one transfer request into separate recipient or amount intents.",
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
                'description = "Harness surfaces for finance intent routing"',
                f'skill_roots = ["{skills_root.as_posix()}"]',
                "max_skill_body_chars = 2000",
                "",
                "[surfaces.intent_recognition]",
                'system = "Classify the message."',
                'human = "Message: {message}\\nContext: {missing_context}"',
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


def test_prompt_harness_loads_skill_body_only_when_binding_matches(tmp_path: Path) -> None:
    harness = load_prompt_harness(_write_demo_harness(tmp_path))
    assert harness is not None

    transfer_prompt = harness.render(
        surface="intent_recognition",
        variables={
            "message": "transfer 500 to Alice",
        },
        intent_codes=("transfer",),
        domain_codes=("finance",),
        capabilities=("routing",),
    )

    assert transfer_prompt.metadata_skills == ("transfer-routing",)
    assert transfer_prompt.loaded_skills == ("transfer-routing",)
    assert "finance-router-harness@2026.04" in transfer_prompt.system
    assert "transfer-routing: Transfer routing rules" in transfer_prompt.system
    assert "Treat recipient names" in transfer_prompt.system
    assert "Message: transfer 500 to Alice" in transfer_prompt.human

    other_prompt = harness.render(
        surface="intent_recognition",
        variables={
            "message": "check balance",
        },
        intent_codes=("balance",),
        domain_codes=("finance",),
        capabilities=("routing",),
    )

    assert other_prompt.loaded_skills == ()
    assert "Treat recipient names" not in other_prompt.system


def test_unknown_template_variables_are_preserved(tmp_path: Path) -> None:
    harness = load_prompt_harness(_write_demo_harness(tmp_path))
    assert harness is not None

    prompt = harness.render(
        surface="intent_recognition",
        variables={"message": "hello"},
    )

    assert "{missing_context}" in prompt.human
    assert prompt.messages()[0]["role"] == "system"


def test_prompt_harness_loads_agent_and_authorized_reference(tmp_path: Path) -> None:
    agent_path = tmp_path / "agent.md"
    agent_path.write_text("Root router contract.", encoding="utf-8")
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "transfer-routing"
    reference_dir = skill_dir / "references"
    reference_dir.mkdir(parents=True)
    (reference_dir / "ref_001.md").write_text("Detailed transfer slot rules.", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: transfer-routing",
                "description: Transfer routing rules for finance intents",
                'surfaces: ["intent_recognition"]',
                'domain_codes: ["finance"]',
                'capabilities: ["routing"]',
                'references: [{"id": "ref_001", "path": "references/ref_001.md", "purpose": "Transfer slot detail"}]',
                "---",
                "# Transfer Routing",
                "Treat recipient names as slots.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    spec_path = tmp_path / "harness.toml"
    spec_path.write_text(
        "\n".join(
            [
                'name = "reference-test"',
                'version = "2026.05"',
                f'agent_paths = ["{agent_path.as_posix()}"]',
                f'skill_roots = ["{skills_root.as_posix()}"]',
                "",
                "[surfaces.intent_recognition]",
                'system = "Classify."',
                'human = "Message: {message}"',
                "",
                "[[bindings]]",
                'skill = "transfer-routing"',
                'surfaces = ["intent_recognition"]',
                'domain_codes = ["finance"]',
                'load = "body"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    harness = load_prompt_harness(spec_path)
    assert harness is not None

    prompt = harness.render(
        surface="intent_recognition",
        variables={"message": "transfer"},
        domain_codes=("finance",),
        capabilities=("routing",),
        requested_reference_ids=("ref_001",),
    )

    assert prompt.agent_contexts == (agent_path.as_posix(),)
    assert prompt.loaded_skills == ("transfer-routing",)
    assert prompt.loaded_references == ("ref_001",)
    assert "Root router contract." in prompt.system
    assert "Detailed transfer slot rules." in prompt.system
    assert any(event["stage"] == "agent_context_loaded" for event in prompt.trace_events)
    assert any(event["stage"] == "reference_body_loaded" for event in prompt.trace_events)

    with pytest.raises(ValueError):
        harness.render(
            surface="intent_recognition",
            variables={"message": "transfer"},
            domain_codes=("finance",),
            capabilities=("routing",),
            requested_reference_ids=("not_allowed",),
        )
