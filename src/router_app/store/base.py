from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator

from router_app.core.schemas import SessionState


@dataclass(frozen=True)
class LoadSessionResult:
    state: SessionState | None
    expired: bool = False


class SessionStore(ABC):
    @abstractmethod
    async def load_session(self, session_id: str, *, now: datetime, ttl_seconds: int) -> LoadSessionResult:
        """Load a session, deleting and reporting it when it is idle-expired."""

    @abstractmethod
    async def save_session(self, state: SessionState, *, ttl_seconds: int) -> None:
        """Persist a session with an idle TTL."""

    @abstractmethod
    async def delete_session(self, session_id: str) -> None:
        """Delete a session."""

    @abstractmethod
    async def healthcheck(self) -> bool:
        """Return whether the store is usable."""

    @asynccontextmanager
    async def session_lock(self, session_id: str) -> AsyncIterator[None]:
        yield

