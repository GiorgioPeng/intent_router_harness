from __future__ import annotations

from intent_router_harness.contracts import SessionState


class InMemorySessionStore:
    """Small in-memory session store for the harness service."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def get_or_create(self, session_id: str) -> SessionState:
        """Return an existing session or create an empty one."""
        session = self._sessions.get(session_id)
        if session is None:
            session = SessionState(session_id=session_id)
            self._sessions[session_id] = session
        return session.model_copy(deep=True)

    def save(self, session: SessionState) -> None:
        """Persist a session snapshot."""
        self._sessions[session.session_id] = session.model_copy(deep=True)
