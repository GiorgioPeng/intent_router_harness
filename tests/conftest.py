from __future__ import annotations

import pytest

from router_app.app import create_app
from router_app.config_source.static import StaticConfigSource
from router_app.core.schemas import (
    HandoffContract,
    SkillBody,
    SkillIndex,
    SkillMetadata,
    SlotSpec,
)
from router_app.core.service import RouterService
from router_app.modeling import HeuristicPlanner, Planner
from router_app.settings import Settings
from router_app.store import InMemorySessionStore


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        qwen_api_key="test-key",
        qwen_model="test-model",
        qwen_base_http_api_url="http://model.test/api/v1",
        config_source_base_url="http://config.test",
        session_ttl_seconds=1800,
    )


@pytest.fixture
def skill_index() -> SkillIndex:
    skills = [
        SkillMetadata(
            skillId="skill_transfer",
            intentCode="transfer",
            summary="办理转账汇款，需要收款人和金额。",
            priority=10,
            version="v1",
            bodyKey="body/transfer",
            allowedReferenceKeys=["transfer/limits"],
        ),
        SkillMetadata(
            skillId="skill_balance",
            intentCode="balance_query",
            summary="查询账户余额。",
            priority=20,
            version="v1",
            bodyKey="body/balance",
            allowedReferenceKeys=[],
        ),
    ]
    for idx in range(99):
        skills.append(
            SkillMetadata(
                skillId=f"skill_dummy_{idx}",
                intentCode=f"dummy_{idx}",
                summary=f"低频测试意图 {idx}",
                priority=100 + idx,
                version="v1",
                bodyKey=f"body/dummy/{idx}",
                allowedReferenceKeys=[],
            ),
        )
    return SkillIndex(version="idx-v1", etag="etag-v1", skills=skills)


@pytest.fixture
def skill_bodies() -> dict[tuple[str, str], SkillBody]:
    return {
        ("skill_transfer", "v1"): SkillBody(
            skillId="skill_transfer",
            version="v1",
            rulesMd="# 转账规则\n不得在首轮全局 prompt 中出现。",
            slotContract=[
                SlotSpec(name="recipient", required=True, prompt="请补充收款人。"),
                SlotSpec(name="amount", required=True, prompt="请补充转账金额。"),
            ],
            handoffContract=HandoffContract(target="transfer_assistant"),
        ),
        ("skill_balance", "v1"): SkillBody(
            skillId="skill_balance",
            version="v1",
            rulesMd="# 查余额规则",
            slotContract=[],
            handoffContract=HandoffContract(target="balance_assistant"),
        ),
    }


@pytest.fixture
def service(settings: Settings, skill_index: SkillIndex, skill_bodies) -> RouterService:
    return make_service(settings, skill_index, skill_bodies)


def make_service(
    settings: Settings,
    skill_index: SkillIndex,
    skill_bodies: dict[tuple[str, str], SkillBody],
    *,
    planner: Planner | None = None,
) -> RouterService:
    return RouterService(
        settings=settings,
        config_source=StaticConfigSource(skill_index, skill_bodies),
        store=InMemorySessionStore(),
        planner=planner or HeuristicPlanner(),
    )


@pytest.fixture
def app(service: RouterService):
    return create_app(service=service)
