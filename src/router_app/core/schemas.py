from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        use_enum_values=True,
        extra="forbid",
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RouterStatus(StrEnum):
    CLARIFYING = "clarifying"
    COLLECTING_SLOTS = "collecting_slots"
    HANDOFF_READY = "handoff_ready"
    AWAITING_COMPLETION = "awaiting_completion"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskStatus(StrEnum):
    WAITING = "waiting"
    DOING = "doing"
    COMPLETED = "completed"
    EXCEPTED = "excepted"


TERMINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.EXCEPTED,
}


class ExecutionMode(StrEnum):
    ROUTE_ONLY = "ROUTE_ONLY"
    MOCK_HANDOFF = "MOCK_HANDOFF"


class MessageRequest(CamelModel):
    session_id: str = Field(validation_alias=AliasChoices("sessionId", "session_id"))
    cust_no: str = Field(validation_alias=AliasChoices("cust_no", "custNo"))
    txt: str
    stream: bool = False
    debug_trace: bool = Field(default=False, validation_alias=AliasChoices("debugTrace", "debug_trace"))
    execution_mode: ExecutionMode = Field(
        default=ExecutionMode.ROUTE_ONLY,
        validation_alias=AliasChoices("executionMode", "execution_mode"),
    )
    recommended_tasks: list[dict[str, Any]] | None = Field(
        default=None,
        validation_alias=AliasChoices("recommendedTasks", "recommended_tasks"),
    )
    display_state: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("displayState", "display_state"),
    )
    variables: dict[str, Any] | None = None

    @field_validator("session_id", "cust_no", "txt")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class CompletionRequest(CamelModel):
    session_id: str = Field(validation_alias=AliasChoices("sessionId", "session_id"))
    cust_no: str = Field(validation_alias=AliasChoices("cust_no", "custNo"))
    task_id: str = Field(validation_alias=AliasChoices("taskId", "task_id"))
    completion_signal: Literal[1, 2] = Field(
        validation_alias=AliasChoices("completionSignal", "completion_signal"),
    )
    stream: bool = False
    debug_trace: bool = Field(default=False, validation_alias=AliasChoices("debugTrace", "debug_trace"))

    @field_validator("session_id", "cust_no", "task_id")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class HandoffRequest(CamelModel):
    session_id: str = Field(validation_alias=AliasChoices("sessionId", "session_id"))
    cust_no: str = Field(validation_alias=AliasChoices("cust_no", "custNo"))
    task_id: str = Field(validation_alias=AliasChoices("taskId", "task_id"))
    dispatch: bool = False
    mock_dispatch: bool = Field(
        default=False,
        validation_alias=AliasChoices("mockDispatch", "mock_dispatch"),
    )
    endpoint_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("endpointUrl", "endpoint_url", "subAgentUrl", "sub_agent_url"),
    )
    extra_payload: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("extraPayload", "extra_payload"),
    )
    debug_trace: bool = Field(default=False, validation_alias=AliasChoices("debugTrace", "debug_trace"))

    @field_validator("session_id", "cust_no", "task_id")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class SlotSpec(CamelModel):
    name: str
    required: bool = True
    prompt: str | None = None
    description: str | None = None


class HandoffContract(CamelModel):
    target: str = "mock_executor"
    payload_schema: dict[str, Any] = Field(default_factory=dict)


class SkillMetadata(CamelModel):
    skill_id: str = Field(validation_alias=AliasChoices("skillId", "skill_id"))
    intent_code: str = Field(validation_alias=AliasChoices("intentCode", "intent_code"))
    summary: str
    priority: int = 100
    version: str
    body_key: str = Field(validation_alias=AliasChoices("bodyKey", "body_key"))
    allowed_reference_keys: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("allowedReferenceKeys", "allowed_reference_keys"),
    )

    @model_validator(mode="before")
    @classmethod
    def reject_multiple_intents(cls, data: Any) -> Any:
        if isinstance(data, dict):
            raw = data.get("intentCode", data.get("intent_code"))
            if isinstance(raw, list):
                raise ValueError("a skill can declare exactly one intentCode string")
        return data

    @field_validator("skill_id", "intent_code", "summary", "version", "body_key")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("body_key")
    @classmethod
    def safe_body_key(cls, value: str) -> str:
        return validate_config_key(value, "body key")


class SkillBody(CamelModel):
    skill_id: str = Field(validation_alias=AliasChoices("skillId", "skill_id"))
    version: str
    rules_md: str = Field(validation_alias=AliasChoices("rulesMd", "rules_md"))
    slot_contract: list[SlotSpec] = Field(
        default_factory=list,
        validation_alias=AliasChoices("slotContract", "slot_contract"),
    )
    handoff_contract: HandoffContract = Field(
        default_factory=HandoffContract,
        validation_alias=AliasChoices("handoffContract", "handoff_contract"),
    )

    @field_validator("skill_id", "version", "rules_md")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class ReferenceBody(CamelModel):
    reference_key: str = Field(validation_alias=AliasChoices("referenceKey", "reference_key"))
    version: str
    body_md: str = Field(validation_alias=AliasChoices("bodyMd", "body_md"))

    @field_validator("reference_key")
    @classmethod
    def safe_reference_key(cls, value: str) -> str:
        return validate_reference_key(value)


class SkillIndex(CamelModel):
    version: str
    skills: list[SkillMetadata]
    etag: str | None = None

    @field_validator("skills")
    @classmethod
    def validate_unique_and_authorized(cls, skills: list[SkillMetadata]) -> list[SkillMetadata]:
        skill_ids: set[str] = set()
        intent_codes: set[str] = set()
        for skill in skills:
            if skill.skill_id in skill_ids:
                raise ValueError(f"duplicate skillId: {skill.skill_id}")
            skill_ids.add(skill.skill_id)
            if skill.intent_code in intent_codes:
                raise ValueError(f"duplicate intentCode: {skill.intent_code}")
            intent_codes.add(skill.intent_code)
            for reference_key in skill.allowed_reference_keys:
                validate_reference_key(reference_key)
        return skills

    def by_skill_id(self) -> dict[str, SkillMetadata]:
        return {skill.skill_id: skill for skill in self.skills}

    def by_intent_code(self) -> dict[str, SkillMetadata]:
        return {skill.intent_code: skill for skill in self.skills}


def validate_reference_key(value: str) -> str:
    return validate_config_key(value, "reference key")


def validate_config_key(value: str, label: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{label} must not be blank")
    if value.startswith("/") or ".." in value or "\\" in value:
        raise ValueError(f"illegal {label}: {value}")
    return value


class TraceEvent(CamelModel):
    stage: str
    detail: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=utc_now)


class ContextLease(CamelModel):
    skill_id: str = Field(validation_alias=AliasChoices("skillId", "skill_id"))
    skill_version: str = Field(validation_alias=AliasChoices("skillVersion", "skill_version"))
    body_key: str = Field(validation_alias=AliasChoices("bodyKey", "body_key"))
    loaded_reference_keys: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("loadedReferenceKeys", "loaded_reference_keys"),
    )


class ConversationTurn(CamelModel):
    role: Literal["user", "assistant", "system"]
    text: str
    event: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=utc_now)


class ModelSessionMapping(CamelModel):
    project_session_id: str = Field(validation_alias=AliasChoices("projectSessionId", "project_session_id"))
    model_session_id: str = Field(validation_alias=AliasChoices("modelSessionId", "model_session_id"))
    purpose: str
    created_at: datetime = Field(
        default_factory=utc_now,
        validation_alias=AliasChoices("createdAt", "created_at"),
    )


class TaskState(CamelModel):
    task_id: str = Field(default_factory=lambda: f"task_{uuid4().hex[:12]}")
    intent_code: str = Field(validation_alias=AliasChoices("intentCode", "intent_code"))
    skill_id: str = Field(validation_alias=AliasChoices("skillId", "skill_id"))
    skill_version: str = Field(validation_alias=AliasChoices("skillVersion", "skill_version"))
    body_key: str = Field(validation_alias=AliasChoices("bodyKey", "body_key"))
    status: TaskStatus = TaskStatus.WAITING
    slots: dict[str, Any] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    lease: ContextLease | None = None
    todo_visible: bool = Field(
        default=True,
        validation_alias=AliasChoices("todoVisible", "todo_visible"),
    )
    interrupted_reason: str | None = Field(
        default=None,
        validation_alias=AliasChoices("interruptedReason", "interrupted_reason"),
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def is_terminal(self) -> bool:
        return (not self.todo_visible) or TaskStatus(self.status) in TERMINAL_TASK_STATUSES

    def release_context(self) -> None:
        self.lease = None


class SessionState(CamelModel):
    session_id: str = Field(validation_alias=AliasChoices("sessionId", "session_id"))
    cust_no: str = Field(validation_alias=AliasChoices("cust_no", "custNo"))
    tasks: list[TaskState] = Field(default_factory=list)
    conversation_history: list[ConversationTurn] = Field(
        default_factory=list,
        validation_alias=AliasChoices("conversationHistory", "conversation_history"),
    )
    model_session_mappings: list[ModelSessionMapping] = Field(
        default_factory=list,
        validation_alias=AliasChoices("modelSessionMappings", "model_session_mappings"),
    )
    current_task_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("currentTaskId", "current_task_id"),
    )
    created_at: datetime = Field(default_factory=utc_now)
    last_activity_at: datetime = Field(default_factory=utc_now)

    def active_task(self) -> TaskState | None:
        if not self.current_task_id:
            return None
        for task in self.tasks:
            if task.task_id == self.current_task_id and not task.is_terminal():
                return task
        return None

    def task_by_id(self, task_id: str) -> TaskState | None:
        return next((task for task in self.tasks if task.task_id == task_id), None)

    def waiting_tasks(self) -> list[TaskState]:
        return [
            task
            for task in self.tasks
            if task.todo_visible and TaskStatus(task.status) == TaskStatus.WAITING
        ]

    def schedule_next_waiting(self) -> TaskState | None:
        next_task = next(
            (
                task
                for task in self.tasks
                if task.todo_visible and TaskStatus(task.status) == TaskStatus.WAITING
            ),
            None,
        )
        self.current_task_id = next_task.task_id if next_task else None
        return next_task


class IntentPlan(CamelModel):
    intent_code: str | None = Field(
        default=None,
        validation_alias=AliasChoices("intentCode", "intent_code", "name"),
    )
    skill_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("skillId", "skill_id"),
    )
    order: int = 0
    confidence: float = 0.0
    extracted_slots: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("extractedSlots", "extracted_slots"),
    )


class PlannerResult(CamelModel):
    action: Literal[
        "CREATE_TASKS",
        "UPDATE_CURRENT_TASK",
        "SWITCH_TASK",
        "CANCEL_TASK",
        "CANCEL_ALL",
        "CLARIFY",
        "NO_INTENT",
    ] = "CREATE_TASKS"
    intents: list[IntentPlan] = Field(default_factory=list)
    slot_updates: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("slotUpdates", "slot_updates"),
    )
    target_task_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("targetTaskId", "target_task_id"),
    )
    requested_reference_keys: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("requestedReferenceKeys", "requested_reference_keys"),
    )
    interrupt_current_task: bool = Field(
        default=False,
        validation_alias=AliasChoices("interruptCurrentTask", "interrupt_current_task"),
    )
    message: str | None = None


class TaskFrame(CamelModel):
    task_id: str = Field(validation_alias=AliasChoices("taskId", "task_id"))
    intent_code: str = Field(validation_alias=AliasChoices("intentCode", "intent_code"))
    skill_id: str = Field(validation_alias=AliasChoices("skillId", "skill_id"))
    status: TaskStatus
    slots: dict[str, Any] = Field(default_factory=dict)
    missing_slots: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("missingSlots", "missing_slots"),
    )
    todo_visible: bool = Field(
        default=True,
        validation_alias=AliasChoices("todoVisible", "todo_visible"),
    )
    interrupted_reason: str | None = Field(
        default=None,
        validation_alias=AliasChoices("interruptedReason", "interrupted_reason"),
    )


class TodoItem(CamelModel):
    order: int
    task_id: str = Field(validation_alias=AliasChoices("taskId", "task_id"))
    name: str
    status: TaskStatus
    current: bool = False
    missing_slots: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("missingSlots", "missing_slots"),
    )


class BusinessFrame(CamelModel):
    session_id: str = Field(validation_alias=AliasChoices("sessionId", "session_id"))
    status: RouterStatus
    current_task_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("currentTaskId", "current_task_id"),
    )
    tasks: list[TaskFrame] = Field(default_factory=list)
    current_task: TaskFrame | None = Field(
        default=None,
        validation_alias=AliasChoices("currentTask", "current_task"),
    )
    todo_list: list[TodoItem] = Field(
        default_factory=list,
        validation_alias=AliasChoices("todoList", "todo_list"),
    )
    messages: list[str] = Field(default_factory=list)
    handoff_payload: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("handoffPayload", "handoff_payload"),
    )
    mock_handoff_result: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("mockHandoffResult", "mock_handoff_result"),
    )
    trace: list[TraceEvent] | None = None


class HandoffFrame(CamelModel):
    session_id: str = Field(validation_alias=AliasChoices("sessionId", "session_id"))
    task_id: str = Field(validation_alias=AliasChoices("taskId", "task_id"))
    status: str
    accepted: bool = False
    current_task_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("currentTaskId", "current_task_id"),
    )
    todo_list: list[TodoItem] = Field(
        default_factory=list,
        validation_alias=AliasChoices("todoList", "todo_list"),
    )
    target: str | None = None
    messages: list[str] = Field(default_factory=list)
    handoff_payload: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("handoffPayload", "handoff_payload"),
    )
    dispatch_result: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("dispatchResult", "dispatch_result"),
    )
    trace: list[TraceEvent] | None = None


def validate_skill_index_payload(payload: Any, etag: str | None = None) -> SkillIndex:
    if isinstance(payload, list):
        payload = {"version": etag or "unknown", "skills": payload}
    if not isinstance(payload, dict):
        raise ValueError("skill index payload must be an object or a skill list")
    if etag and "etag" not in payload:
        payload = {**payload, "etag": etag}
    return SkillIndex.model_validate(payload)
