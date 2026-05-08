from __future__ import annotations

import pytest
from pydantic import ValidationError

from router_app.core.schemas import SkillIndex, SkillMetadata


def test_rejects_skill_with_multiple_intents() -> None:
    with pytest.raises(ValidationError):
        SkillMetadata.model_validate(
            {
                "skillId": "bad",
                "intentCode": ["a", "b"],
                "summary": "bad",
                "priority": 1,
                "version": "v1",
                "bodyKey": "body/bad",
                "allowedReferenceKeys": [],
            },
        )


def test_rejects_duplicate_skill_or_intent(skill_index: SkillIndex) -> None:
    duplicate = skill_index.skills[0].model_copy()
    with pytest.raises(ValidationError):
        SkillIndex(version="bad", skills=[*skill_index.skills, duplicate])


def test_rejects_illegal_reference_key() -> None:
    with pytest.raises(ValidationError):
        SkillIndex(
            version="bad",
            skills=[
                SkillMetadata(
                    skillId="bad",
                    intentCode="bad_intent",
                    summary="bad",
                    priority=1,
                    version="v1",
                    bodyKey="body/bad",
                    allowedReferenceKeys=["../secret"],
                ),
            ],
        )

