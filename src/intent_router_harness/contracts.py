from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


AssistantStatus = Literal[
    "running",
    "waiting_user_input",
    "ready_for_dispatch",
    "waiting_assistant_completion",
    "completed",
    "cancelled",
    "failed",
]

PlannerMode = Literal[
    "single_task",
    "multi_task",
    "slot_filling",
    "cancel",
    "replan",
    "failed",
]


class ConfigVariable(BaseModel):
    """One upstream config variable."""

    name: str
    value: Any


class RouterMessageRequest(BaseModel):
    """Assistant router message entrypoint request."""

    model_config = ConfigDict(extra="allow")

    sessionId: str
    txt: str
    stream: bool = False
    debugTrace: bool = False
    executionMode: str = "execute"
    custId: str | None = None
    config_variables: list[ConfigVariable] = Field(default_factory=list)
    recommendTask: list[dict[str, Any]] = Field(default_factory=list)
    currentDisplay: list[dict[str, Any]] = Field(default_factory=list)


class TaskCompletionRequest(BaseModel):
    """Assistant task completion callback request."""

    model_config = ConfigDict(extra="allow")

    sessionId: str
    taskId: str
    completionSignal: Literal[1, 2]
    stream: bool = False
    debugTrace: bool = False


class RecognitionPlan(BaseModel):
    """Planner-provided recognition payload."""

    intent_code: str | None = None


class PlannedTask(BaseModel):
    """One planned task in the assistant protocol task list."""

    model_config = ConfigDict(extra="allow")

    taskId: str
    intent_code: str
    status: AssistantStatus = "ready_for_dispatch"
    title: str = ""
    slot_memory: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)


class PlannerOutput(BaseModel):
    """Structured LLM planner output before assistant protocol adaptation."""

    mode: PlannerMode
    status: AssistantStatus
    completion_state: int = 0
    completion_reason: str
    intent_code: str | None = None
    recognition: RecognitionPlan | None = None
    slot_memory: dict[str, Any] = Field(default_factory=dict)
    task_list: list[PlannedTask] = Field(default_factory=list)
    current_task: PlannedTask | None = None
    graph: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    requested_references: list[str] = Field(default_factory=list)
    message: str = ""
    output: dict[str, Any] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @field_validator("slot_memory", "output", "diagnostics", mode="before")
    @classmethod
    def _none_to_dict(cls, value: Any) -> Any:
        if value is None:
            return {}
        return value

    @field_validator("task_list", "actions", "requested_references", mode="before")
    @classmethod
    def _none_to_list(cls, value: Any) -> Any:
        if value is None:
            return []
        return value


class AssistantProtocolFrame(BaseModel):
    """One assistant protocol message frame."""

    ok: bool = True
    status: AssistantStatus
    intent_code: str | None = None
    completion_state: int
    completion_reason: str
    stage: str | None = None
    details: dict[str, Any] | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    slot_memory: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    task_list: list[dict[str, Any]] = Field(default_factory=list)
    current_task: dict[str, Any] | None = None
    errorCode: str | None = None
    graph: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)

    def protocol_dump(self) -> dict[str, Any]:
        """Return protocol JSON without unset optional null fields."""
        return self.model_dump(mode="json", exclude_none=True)


class SessionState(BaseModel):
    """Session lifecycle metadata.

    Session is only an identity and idle-timeout boundary. It must not carry
    task status, slot memory, or planner runtime state.
    """

    session_id: str
    user_binding_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_active_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None


class TaskRuntimeState(BaseModel):
    """In-memory task runtime state owned by the router core."""

    slot_memory: dict[str, Any] = Field(default_factory=dict)
    task_list: list[PlannedTask] = Field(default_factory=list)
    current_task: PlannedTask | None = None
    graph: dict[str, Any] | None = None
    active_context: dict[str, Any] = Field(default_factory=dict)
    context_leases: list[dict[str, Any]] = Field(default_factory=list)


class AssistantTraceEvent(BaseModel):
    """One debug-only trace event emitted on SSE streams."""

    stage: str
    title: str
    summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class AssistantServiceResult(BaseModel):
    """HTTP-facing result from assistant protocol service methods."""

    frames: list[AssistantProtocolFrame]
    trace_events: list[AssistantTraceEvent] = Field(default_factory=list)

    @property
    def final_frame(self) -> AssistantProtocolFrame:
        return self.frames[-1]
