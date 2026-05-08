from __future__ import annotations

from abc import ABC, abstractmethod

from router_app.core.schemas import ReferenceBody, SkillBody, SkillIndex, TraceEvent


class ConfigSourceError(RuntimeError):
    """Base config source error."""


class ConfigValidationError(ConfigSourceError):
    """Raised when a fetched config payload is structurally invalid."""


class ConfigSource(ABC):
    @abstractmethod
    async def refresh_index(self, trace: list[TraceEvent] | None = None) -> SkillIndex | None:
        """Refresh and return the last-known-good skill index.

        Returns None when no valid index has ever been loaded.
        """

    @abstractmethod
    async def get_last_good_index(self) -> SkillIndex | None:
        """Return the cached last-known-good skill index without network I/O."""

    @abstractmethod
    async def load_skill_body(self, skill_id: str, version: str, trace: list[TraceEvent] | None = None) -> SkillBody:
        """Load the body for a routed skill."""

    async def load_skill_body_by_id(
        self,
        skill_id: str,
        *,
        trace: list[TraceEvent] | None = None,
    ) -> SkillBody:
        return await self.load_skill_body(skill_id, "v1", trace)

    @abstractmethod
    async def load_reference(
        self,
        reference_key: str,
        version: str,
        trace: list[TraceEvent] | None = None,
    ) -> ReferenceBody:
        """Load an authorized reference for the current task."""

    @abstractmethod
    async def healthcheck(self) -> bool:
        """Return whether the source can serve requests."""
