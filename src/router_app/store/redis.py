from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator
from uuid import uuid4

import redis.asyncio as redis
from redis.exceptions import RedisError

from router_app.core.schemas import SessionState
from router_app.store.base import LoadSessionResult, SessionStore


class RedisSessionStore(SessionStore):
    def __init__(self, redis_url: str, *, lock_ttl_ms: int = 10_000, prefix: str = "router") -> None:
        self._redis = redis.from_url(redis_url, decode_responses=False)
        self._prefix = prefix
        self._lock_ttl_ms = lock_ttl_ms

    async def load_session(self, session_id: str, *, now: datetime, ttl_seconds: int) -> LoadSessionResult:
        raw = await self._redis.get(self._key(session_id))
        if raw is None:
            return LoadSessionResult(state=None)
        state = SessionState.model_validate_json(raw)
        idle_seconds = (now - state.last_activity_at).total_seconds()
        if idle_seconds >= ttl_seconds:
            await self.delete_session(session_id)
            return LoadSessionResult(state=None, expired=True)
        return LoadSessionResult(state=state)

    async def save_session(self, state: SessionState, *, ttl_seconds: int) -> None:
        raw = state.model_dump_json(by_alias=True)
        await self._redis.set(self._key(state.session_id), raw, ex=ttl_seconds)

    async def delete_session(self, session_id: str) -> None:
        await self._redis.delete(self._key(session_id))

    async def healthcheck(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except RedisError:
            return False

    @asynccontextmanager
    async def session_lock(self, session_id: str) -> AsyncIterator[None]:
        lock_key = self._lock_key(session_id)
        token = uuid4().hex
        while True:
            acquired = await self._redis.set(lock_key, token, nx=True, px=self._lock_ttl_ms)
            if acquired:
                break
            await asyncio.sleep(0.03)
        try:
            yield
        finally:
            await self._release_lock(lock_key, token)

    async def aclose(self) -> None:
        await self._redis.aclose()

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}:session:{session_id}"

    def _lock_key(self, session_id: str) -> str:
        return f"{self._prefix}:session-lock:{session_id}"

    async def _release_lock(self, lock_key: str, token: str) -> None:
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        await self._redis.eval(script, 1, lock_key, token)
