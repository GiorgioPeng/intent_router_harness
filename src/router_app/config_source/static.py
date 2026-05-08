from __future__ import annotations

from router_app.config_source.base import ConfigSource, ConfigValidationError
from router_app.core.schemas import ReferenceBody, SkillBody, SkillIndex, TraceEvent


class StaticConfigSource(ConfigSource):
    """In-process config source for tests and local smoke checks."""

    def __init__(
        self,
        index: SkillIndex,
        bodies: dict[tuple[str, str], SkillBody],
        references: dict[tuple[str, str], ReferenceBody] | None = None,
    ) -> None:
        self._index = index
        self._bodies = bodies
        self._references = references or {}

    async def refresh_index(self, trace: list[TraceEvent] | None = None) -> SkillIndex:
        if trace is not None:
            trace.append(
                TraceEvent(
                    stage="config.refresh_loaded",
                    detail={"version": self._index.version, "skillCount": len(self._index.skills)},
                ),
            )
        return self._index

    async def get_last_good_index(self) -> SkillIndex:
        return self._index

    async def load_skill_body(
        self,
        skill_id: str,
        version: str,
        trace: list[TraceEvent] | None = None,
    ) -> SkillBody:
        key = (skill_id, version)
        if key not in self._bodies:
            raise ConfigValidationError(f"missing skill body: {skill_id}@{version}")
        if trace is not None:
            trace.append(TraceEvent(stage="skill.body_loaded", detail={"skillId": skill_id, "version": version}))
        return self._bodies[key]

    async def load_skill_body_by_id(
        self,
        skill_id: str,
        *,
        trace: list[TraceEvent] | None = None,
    ) -> SkillBody:
        for (body_skill_id, version), body in self._bodies.items():
            if body_skill_id == skill_id:
                if trace is not None:
                    trace.append(TraceEvent(stage="skill.body_loaded", detail={"skillId": skill_id, "version": version}))
                return body
        raise ConfigValidationError(f"missing skill body: {skill_id}")

    async def load_reference(
        self,
        reference_key: str,
        version: str,
        trace: list[TraceEvent] | None = None,
    ) -> ReferenceBody:
        key = (reference_key, version)
        if key not in self._references:
            raise ConfigValidationError(f"missing reference: {reference_key}@{version}")
        if trace is not None:
            trace.append(TraceEvent(stage="reference.loaded", detail={"referenceKey": reference_key}))
        return self._references[key]

    async def healthcheck(self) -> bool:
        return True
