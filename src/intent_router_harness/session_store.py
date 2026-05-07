from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from intent_router_harness.contracts import SessionState


class SessionOwnershipError(RuntimeError):
    """Raised when one session id is reused by a different user."""


@dataclass(frozen=True, slots=True)
class SessionLoadResult:
    """Loaded session plus lifecycle metadata for trace output."""

    session: SessionState
    expired: bool = False
    user_bound: bool = False


class InMemorySessionStore:
    """Small in-memory session store for the harness service."""

    def __init__(
        self,
        *,
        idle_timeout: timedelta = timedelta(minutes=30),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions: dict[str, SessionState] = {}
        self.idle_timeout = idle_timeout
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def load(self, session_id: str, *, user_id: str | None = None) -> SessionLoadResult:
        """Return a session snapshot and enforce ownership/idle expiration."""
        now = self._now()
        session = self._sessions.get(session_id)
        expired = session is not None and self._is_expired(session, now)
        if expired:
            self._sessions.pop(session_id, None)
            session = None

        user_bound = False
        if session is None:
            session = SessionState(
                session_id=session_id,
                user_id=user_id,
                created_at=now,
                last_active_at=now,
                expires_at=now + self.idle_timeout,
            )
            user_bound = user_id is not None
        else:
            if user_id and session.user_id and session.user_id != user_id:
                raise SessionOwnershipError(
                    f"session_id {session_id!r} is already bound to another user"
                )
            if user_id and not session.user_id:
                session = session.model_copy(update={"user_id": user_id}, deep=True)
                user_bound = True
            session = self._refresh(session, now)

        self._sessions[session_id] = session.model_copy(deep=True)
        return SessionLoadResult(
            session=session.model_copy(deep=True),
            expired=expired,
            user_bound=user_bound,
        )

    def get_or_create(self, session_id: str) -> SessionState:
        """Return an existing session or create an empty one."""
        return self.load(session_id).session

    def save(self, session: SessionState) -> None:
        """Persist a session snapshot."""
        now = self._now()
        self._sessions[session.session_id] = self._refresh(session, now).model_copy(deep=True)

    def _refresh(self, session: SessionState, now: datetime) -> SessionState:
        return session.model_copy(
            update={
                "last_active_at": now,
                "expires_at": now + self.idle_timeout,
            },
            deep=True,
        )

    def _is_expired(self, session: SessionState, now: datetime) -> bool:
        return session.expires_at is not None and session.expires_at <= now

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)
