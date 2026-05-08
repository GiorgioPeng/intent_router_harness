from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from router_app.core.schemas import SessionState
from router_app.store.base import LoadSessionResult, SessionStore


class InMemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def load_session(self, session_id: str, *, now: datetime, ttl_seconds: int) -> LoadSessionResult:
        state = self._sessions.get(session_id)
        if state is None:
            return LoadSessionResult(state=None)
        idle_seconds = (now - state.last_activity_at).total_seconds()
        if idle_seconds >= ttl_seconds:
            del self._sessions[session_id]
            return LoadSessionResult(state=None, expired=True)
        return LoadSessionResult(state=state.model_copy(deep=True))

    async def save_session(self, state: SessionState, *, ttl_seconds: int) -> None:
        self._sessions[state.session_id] = state.model_copy(deep=True)

    async def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def healthcheck(self) -> bool:
        return True

    @asynccontextmanager
    async def session_lock(self, session_id: str) -> AsyncIterator[None]:
        lock = self._locks[session_id]
        async with lock:
            yield

