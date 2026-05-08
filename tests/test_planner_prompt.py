from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from httpx import Response

from router_app.core.schemas import ConversationTurn, SessionState, SkillBody, SkillIndex, SlotSpec, TaskState
from router_app.modeling.agentscope_planner import (
    AgentScopePlanner,
    _agent_rules,
    _chat_completions_url,
    _response_text,
)


def test_agent_prompt_uses_metadata_not_skill_body(skill_index: SkillIndex) -> None:
    planner = AgentScopePlanner(model_name="m", api_key="k")
    session = SessionState(sessionId="project-session-1", cust_no="c1")

    prompt = planner._build_prompt(
        user_text="我要转账",
        skill_index=skill_index,
        session=session,
        model_session_id="model_test_1",
    )

    assert "办理转账汇款" in prompt
    assert "availableSkills" in prompt
    assert "rootRules" in prompt
    assert "Router Agent 根规则" in prompt
    assert "model_test_1" in prompt
    assert "project-session-1" not in prompt
    assert '"name": "skill_transfer"' in prompt
    assert '"description": "办理转账汇款，需要收款人和金额。"' in prompt
    assert "skillMetadata" not in prompt
    assert "skillId" not in prompt
    assert "intentCode" not in prompt
    assert "summary" not in prompt
    assert "priority" not in prompt
    assert "version" not in prompt
    assert "skillCount" not in prompt
    assert "不得在首轮全局 prompt 中出现" not in prompt
    assert len(skill_index.skills) >= 100


def test_root_rules_live_in_agent_md_not_python_source() -> None:
    rules = _agent_rules()
    assert "首轮/全局识别只能依据" in rules
    source = Path("src/router_app/modeling/agentscope_planner.py").read_text(encoding="utf-8")
    assert "首轮/全局识别只能依据" not in source
    assert "任务执行状态由 Router 管理" not in source


def test_agent_response_extracts_text_block_content() -> None:
    class Response:
        content = [{"type": "text", "text": '{"action":"CLARIFY","intents":[]}'}]

    assert _response_text(Response()) == '{"action":"CLARIFY","intents":[]}'


def test_chat_completions_url_accepts_root_or_full_path() -> None:
    assert _chat_completions_url("https://example.test/v1") == "https://example.test/v1/chat/completions"
    assert (
        _chat_completions_url("https://example.test/v1/chat/completions")
        == "https://example.test/v1/chat/completions"
    )


@pytest.mark.asyncio
@respx.mock
async def test_agent_planner_calls_openai_compatible_chat_completions(skill_index: SkillIndex) -> None:
    route = respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"action":"CLARIFY","intents":[],"message":"请说明业务意图。"}',
                        },
                    },
                ],
            },
        ),
    )
    planner = AgentScopePlanner(model_name="qwen-test", api_key="test-key", base_http_api_url="https://llm.test/v1")

    result = await planner.plan(
        user_text="你好",
        skill_index=skill_index,
        session=SessionState(sessionId="s-openai", cust_no="c1"),
        model_session_id="model_openai_style",
    )

    assert result.action == "CLARIFY"
    assert route.called
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.headers["x-router-model-session-id"] == "model_openai_style"
    payload = json.loads(request.content)
    assert payload["model"] == "qwen-test"
    assert payload["stream"] is False
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"


def test_agent_prompt_includes_active_skill_body_for_slot_filling(skill_index: SkillIndex) -> None:
    planner = AgentScopePlanner(model_name="m", api_key="k")
    session = SessionState(sessionId="s1", cust_no="c1")

    active = TaskState(
        intentCode="transfer",
        skillId="skill_transfer",
        skillVersion="v1",
        bodyKey="body/transfer",
        missingSlots=["recipient"],
    )
    session.tasks.append(active)
    session.current_task_id = active.task_id
    body = SkillBody(
        skillId="skill_transfer",
        version="v1",
        rulesMd="只能输出 recipient，不要输出 receiver。",
        slotContract=[SlotSpec(name="recipient", required=True, prompt="请补充收款人。")],
    )

    prompt = planner._build_prompt(
        user_text="小明",
        skill_index=skill_index,
        session=session,
        model_session_id="model_test_2",
        active_skill_body=body,
    )

    assert "activeSkill" in prompt
    assert "只能输出 recipient" in prompt
    assert "recipient" in prompt
    assert "receiver" in prompt
    assert '"body": "只能输出 recipient，不要输出 receiver。"' in prompt
    assert "skillId" not in prompt
    assert "version" not in prompt


def test_agent_prompt_includes_managed_conversation_history(skill_index: SkillIndex) -> None:
    planner = AgentScopePlanner(model_name="m", api_key="k")
    session = SessionState(sessionId="s1", cust_no="c1")
    session.conversation_history.append(
        ConversationTurn(role="user", text="我要转账", event="message.received"),
    )
    session.conversation_history.append(
        ConversationTurn(role="assistant", text="请补充收款人。", event="message.responded"),
    )

    prompt = planner._build_prompt(
        user_text="不转账了，我想查询余额",
        skill_index=skill_index,
        session=session,
        model_session_id="model_test_3",
    )

    assert "conversationHistory" in prompt
    assert "model_test_3" in prompt
    assert "我要转账" in prompt
    assert "请补充收款人" in prompt
    assert "interruptCurrentTask" in prompt
