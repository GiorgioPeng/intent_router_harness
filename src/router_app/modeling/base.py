from __future__ import annotations

from abc import ABC, abstractmethod

from router_app.core.schemas import PlannerResult, SessionState, SkillBody, SkillIndex, TraceEvent


class Planner(ABC):
    @abstractmethod
    async def plan(
        self,
        *,
        user_text: str,
        skill_index: SkillIndex,
        session: SessionState,
        model_session_id: str,
        active_skill_body: SkillBody | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> PlannerResult:
        """Return a structured route/update plan."""
