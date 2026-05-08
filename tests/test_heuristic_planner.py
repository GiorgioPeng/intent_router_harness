from __future__ import annotations

import pytest

from router_app.core.schemas import SessionState, SkillIndex, SkillMetadata
from router_app.modeling.heuristic import HeuristicPlanner


@pytest.mark.asyncio
async def test_heuristic_matches_new_skill_from_metadata_only() -> None:
    planner = HeuristicPlanner()
    skill_index = SkillIndex(
        version="idx-v1",
        skills=[
            SkillMetadata(
                skillId="skill_invoice",
                intentCode="invoice_issue",
                summary="开具电子发票，需要订单号和抬头。",
                priority=10,
                version="v1",
                bodyKey="body/invoice",
            ),
        ],
    )

    result = await planner.plan(
        user_text="我要开发票",
        skill_index=skill_index,
        session=SessionState(sessionId="s1", cust_no="c1"),
    )

    assert result.action == "CREATE_TASKS"
    assert result.intents[0].intent_code == "invoice_issue"
    assert result.intents[0].skill_id == "skill_invoice"
    assert result.intents[0].extracted_slots == {}
