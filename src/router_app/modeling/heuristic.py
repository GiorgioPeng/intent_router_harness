from __future__ import annotations

import re
from difflib import SequenceMatcher

from router_app.core.schemas import IntentPlan, PlannerResult, SessionState, SkillBody, SkillIndex, SkillMetadata, TraceEvent
from router_app.modeling.base import Planner


class HeuristicPlanner(Planner):
    """Deterministic metadata-only planner for tests and local smoke runs.

    Production routing should use AgentScopePlanner. This planner deliberately
    avoids business keywords and only scores user text against SKILL metadata,
    so adding/removing skills does not require code changes.
    """

    async def plan(
        self,
        *,
        user_text: str,
        skill_index: SkillIndex,
        session: SessionState,
        model_session_id: str = "",
        active_skill_body: SkillBody | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> PlannerResult:
        if trace is not None:
            trace.append(TraceEvent(stage="intent.heuristic_used", detail={"mode": "metadata_similarity"}))

        text = user_text.strip()
        if not text:
            return PlannerResult(action="CLARIFY", message="我还没识别出明确业务意图，请再说明一下。")

        command = _route_command(text, session)
        if command is not None:
            return command

        active = session.active_task()
        slot_updates = _extract_named_slot_updates(text, active_skill_body, active.missing_slots if active else [])
        if active and slot_updates:
            return PlannerResult(action="UPDATE_CURRENT_TASK", slotUpdates=slot_updates)

        intents = _match_intents(text, skill_index.skills, trace)
        if intents:
            return PlannerResult(action="CREATE_TASKS", intents=intents)
        if active:
            return PlannerResult(action="CLARIFY", message="这条补充信息要用于当前任务吗？")
        return PlannerResult(action="CLARIFY", message="我还没识别出明确业务意图，请再说明一下。")


def _route_command(text: str, session: SessionState) -> PlannerResult | None:
    if any(word in text for word in ("取消全部", "都取消", "全部取消")):
        return PlannerResult(action="CANCEL_ALL", message="已取消全部任务。")
    if "取消" in text and session.active_task():
        return PlannerResult(action="CANCEL_TASK", targetTaskId=session.current_task_id, message="已取消当前任务。")
    return None


def _match_intents(
    text: str,
    skills: list[SkillMetadata],
    trace: list[TraceEvent] | None = None,
) -> list[IntentPlan]:
    used_skill_ids: set[str] = set()
    intents: list[IntentPlan] = []
    for segment in _split_user_text(text):
        scored = [
            (_metadata_score(segment, skill), skill)
            for skill in skills
            if skill.skill_id not in used_skill_ids
        ]
        if not scored:
            continue
        score, skill = max(scored, key=lambda item: (item[0], -item[1].priority))
        if trace is not None:
            trace.append(
                TraceEvent(
                    stage="intent.heuristic_scored",
                    detail={"segment": segment, "skillId": skill.skill_id, "score": round(score, 4)},
                ),
            )
        if score < 0.18:
            continue
        used_skill_ids.add(skill.skill_id)
        intents.append(
            IntentPlan(
                intentCode=skill.intent_code,
                skillId=skill.skill_id,
                order=len(intents),
                confidence=min(0.95, max(0.5, score)),
                extractedSlots={},
            ),
        )
    return intents


def _metadata_score(text: str, skill: SkillMetadata) -> float:
    query_terms = _terms(text)
    metadata = " ".join([skill.skill_id, skill.intent_code, skill.summary])
    metadata_terms = _terms(metadata)
    overlap = query_terms & metadata_terms
    overlap_score = len(overlap) / max(min(len(query_terms), len(metadata_terms)), 1)
    substring_score = _substring_score(text, metadata)
    ratio_score = SequenceMatcher(None, _normalize(text), _normalize(metadata)).ratio()
    return max(overlap_score, substring_score) * 0.85 + ratio_score * 0.15


def _substring_score(text: str, metadata: str) -> float:
    cjk_query = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    if not cjk_query:
        return 0.0
    metadata_normalized = _normalize(metadata)
    longest = 0
    for term in _terms(text):
        if len(term) >= 2 and term in metadata_normalized:
            longest = max(longest, len(term))
    return longest / max(len(cjk_query), 1)


def _split_user_text(text: str) -> list[str]:
    parts = re.split(r"(?:，|,|。|；|;|\s+然后\s*|\s*再\s*|\s*并且\s*)", text)
    return [part.strip() for part in parts if part.strip()]


def _terms(text: str) -> set[str]:
    normalized = _normalize(text)
    ascii_terms = set(re.findall(r"[a-zA-Z0-9_]+", normalized))
    cjk_text = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    cjk_terms = {
        cjk_text[idx : idx + size]
        for size in (2, 3, 4)
        for idx in range(0, max(len(cjk_text) - size + 1, 0))
    }
    return ascii_terms | cjk_terms


def _normalize(text: str) -> str:
    return text.lower().strip()


def _extract_named_slot_updates(
    text: str,
    active_skill_body: SkillBody | None,
    missing_slots: list[str],
) -> dict[str, str]:
    if not missing_slots:
        return {}
    updates: dict[str, str] = {}
    allowed_slots = {slot.name for slot in active_skill_body.slot_contract} if active_skill_body else set(missing_slots)
    for slot in missing_slots:
        if slot not in allowed_slots:
            continue
        match = re.search(rf"(?:{re.escape(slot)})\s*[=:：]\s*([^,，;；\s]+)", text)
        if match:
            updates[slot] = match.group(1)
    if not updates and len(missing_slots) == 1:
        updates[missing_slots[0]] = text
    return updates
