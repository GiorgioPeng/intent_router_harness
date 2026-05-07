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
                "description: 转账路由规则",
                'surfaces: ["intent_recognition"]',
                'intent_codes: ["transfer"]',
                'domain_codes: ["finance"]',
                'capabilities: ["routing"]',
                "---",
                "# 转账路由规则",
                "",
                "将收款人、金额、账号和银行卡尾号视为槽位。",
                "不要把一次转账请求拆成收款人或金额等独立意图。",
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
                'system = "识别用户消息。"',
                'human = "用户消息：{message}\\nContext: {missing_context}"',
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
    assert "transfer-routing: 转账路由规则" in transfer_prompt.system
    assert "将收款人" in transfer_prompt.system
    assert "用户消息：transfer 500 to Alice" in transfer_prompt.human

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
    assert "将收款人" not in other_prompt.system


def test_prompt_harness_rejects_skill_with_multiple_intents(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "bad-routing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: bad-routing",
                'intent_codes: ["AG_TRANSFER", "AG_BALANCE"]',
                "---",
                "# Bad Routing",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    spec_path = tmp_path / "harness.toml"
    spec_path.write_text(
        "\n".join(
            [
                'name = "invalid-skill-test"',
                'version = "2026.05"',
                f'skill_roots = ["{skills_root.as_posix()}"]',
                "",
                "[surfaces.intent_recognition]",
                'system = "识别。"',
                'human = "用户消息：{message}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="multiple intent_codes"):
        load_prompt_harness(spec_path)


def test_skill_without_description_is_hidden_from_metadata_but_can_be_loaded_explicitly(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    public_dir = skills_root / "transfer-routing"
    public_dir.mkdir(parents=True)
    (public_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: transfer-routing",
                "description: 转账路由规则",
                'surfaces: ["task_planning"]',
                'intent_codes: ["AG_TRANS"]',
                'domain_codes: ["finance"]',
                'capabilities: ["routing"]',
                "---",
                "# 转账路由规则",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    helper_dir = skills_root / "transfer-helper-detail"
    helper_dir.mkdir(parents=True)
    (helper_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: transfer-helper-detail",
                'surfaces: ["task_planning"]',
                'domain_codes: ["finance"]',
                'capabilities: ["routing"]',
                "---",
                "# 内部补充规则",
                "只在上层 skill 或 runtime 显式引用时加载。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    spec_path = tmp_path / "harness.toml"
    spec_path.write_text(
        "\n".join(
            [
                'name = "hidden-helper-test"',
                'version = "2026.05"',
                f'skill_roots = ["{skills_root.as_posix()}"]',
                "",
                "[surfaces.task_planning]",
                'system = "规划。"',
                'human = "用户消息：{message}"',
                "include_skill_index = true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    harness = load_prompt_harness(spec_path)
    assert harness is not None

    prompt = harness.render(
        surface="task_planning",
        variables={"message": "我要转账"},
        domain_codes=("finance",),
        capabilities=("routing",),
    )

    assert prompt.metadata_skills == ("transfer-routing",)
    assert "transfer-helper-detail" not in prompt.system

    explicit_prompt = harness.render(
        surface="task_planning",
        variables={"message": "我要转账"},
        domain_codes=("finance",),
        capabilities=("routing",),
        loaded_skill_names=("transfer-helper-detail",),
    )

    assert explicit_prompt.metadata_skills == ("transfer-routing",)
    assert explicit_prompt.loaded_skills == ("transfer-helper-detail",)
    assert "内部补充规则" in explicit_prompt.system


def test_finance_prompt_contains_ordinal_multi_task_slot_rule() -> None:
    harness = load_prompt_harness(Path("examples/finance-router-harness.toml"))
    assert harness is not None

    prompt = harness.render(
        surface="task_planning",
        variables={
            "message": "第一次给100元",
            "execution_mode": "router_only",
            "task_state_json": "{}",
            "recommend_task_json": "[]",
            "recent_messages_json": "[]",
            "config_variables_json": "[]",
            "planner_output_schema_json": "{}",
        },
        domain_codes=("finance",),
        capabilities=("routing", "slots", "planning"),
        loaded_skill_names=("finance-routing",),
    )

    assert "第一次给100元" in prompt.system
    assert "task_list[0]" in prompt.system
    assert "按 task_list 顺序" in prompt.system


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
    agent_path.write_text("根路由约束。", encoding="utf-8")
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "transfer-routing"
    reference_dir = skill_dir / "references"
    reference_dir.mkdir(parents=True)
    (reference_dir / "ref_001.md").write_text("详细转账槽位规则。", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: transfer-routing",
                "description: 转账路由规则",
                'surfaces: ["intent_recognition"]',
                'domain_codes: ["finance"]',
                'capabilities: ["routing"]',
                'references: [{"id": "ref_001", "path": "references/ref_001.md", "purpose": "Transfer slot detail"}]',
                "---",
                "# 转账路由规则",
                "将收款人 as slots.",
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
                'system = "识别。"',
                'human = "用户消息：{message}"',
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
    assert "根路由约束。" in prompt.system
    assert "详细转账槽位规则。" in prompt.system
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
