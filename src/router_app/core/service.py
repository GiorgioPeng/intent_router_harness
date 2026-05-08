from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx

from router_app.config_source import ConfigSource, ConfigSourceError
from router_app.core.errors import PlannerRejectedError, SessionOwnershipError
from router_app.core.schemas import (
    BusinessFrame,
    ConversationTurn,
    ContextLease,
    ExecutionMode,
    HandoffFrame,
    HandoffRequest,
    IntentPlan,
    MessageRequest,
    ModelSessionMapping,
    PlannerResult,
    RouterStatus,
    SessionState,
    SkillBody,
    SkillIndex,
    SkillMetadata,
    TaskFrame,
    TERMINAL_TASK_STATUSES,
    TodoItem,
    TaskState,
    TaskStatus,
    TraceEvent,
    utc_now,
)
from router_app.modeling import Planner
from router_app.settings import Settings
from router_app.store import SessionStore

logger = logging.getLogger("uvicorn.error")
FOLLOW_UP_SKILL_ID = "skill_follow_up"


@dataclass
class RouteOutcome:
    status: RouterStatus = RouterStatus.CLARIFYING
    messages: list[str] = field(default_factory=list)
    handoff_payload: dict[str, Any] | None = None
    mock_handoff_result: dict[str, Any] | None = None


class RouterService:
    def __init__(
        self,
        *,
        settings: Settings,
        config_source: ConfigSource,
        store: SessionStore,
        planner: Planner,
    ) -> None:
        self._settings = settings
        self._config_source = config_source
        self._store = store
        self._planner = planner

    async def handle_message(self, request: MessageRequest) -> BusinessFrame:
        trace: list[TraceEvent] = []
        emit(trace, "request.received", {"path": "/api/v1/message", "sessionId": request.session_id})
        index = await self._refresh_index(trace)
        now = utc_now()

        async with self._store.session_lock(request.session_id):
            session = await self._load_or_create_session(
                session_id=request.session_id,
                cust_no=request.cust_no,
                now=now,
                trace=trace,
            )
            session.last_activity_at = now
            _append_history(
                session,
                role="user",
                text=request.txt,
                event="message.received",
                metadata={"debugTrace": request.debug_trace, "executionMode": request.execution_mode},
            )

            if session.current_task_id is None and session.waiting_tasks():
                session.schedule_next_waiting()
                emit(trace, "task.scheduled", {"taskId": session.current_task_id})

            active_skill_body = await self._active_skill_body_for_planning(session, index, trace)
            model_session_id = _create_model_session_mapping(
                session,
                purpose="message_planning",
                trace=trace,
            )
            _log_message_context(request, index, session, active_skill_body, model_session_id)
            plan = await self._planner.plan(
                user_text=request.txt,
                skill_index=index,
                session=session,
                model_session_id=model_session_id,
                active_skill_body=active_skill_body,
                trace=trace,
            )
            outcome = await self._apply_plan(
                session=session,
                index=index,
                plan=plan,
                user_text=request.txt,
                execution_mode=request.execution_mode,
                trace=trace,
            )
            if active_skill_body is None and plan.action == "CREATE_TASKS" and session.active_task() is not None:
                refined = await self._refine_active_task_with_skill_body(
                    request=request,
                    index=index,
                    session=session,
                    execution_mode=request.execution_mode,
                    trace=trace,
                )
                if refined is not None:
                    outcome = refined
            session.last_activity_at = utc_now()
            _append_history(
                session,
                role="assistant",
                text="\n".join(outcome.messages),
                event="message.responded",
                metadata={"status": outcome.status, "currentTaskId": session.current_task_id},
            )
            await self._store.save_session(session, ttl_seconds=self._settings.session_ttl_seconds)

        frame = self._build_frame(session, outcome, trace if request.debug_trace else None)
        _log_todo_list(frame.todo_list)
        return frame

    async def handle_completion(self, request) -> BusinessFrame:
        trace: list[TraceEvent] = []
        emit(trace, "request.received", {"path": "/api/v1/task/completion", "sessionId": request.session_id})
        index = await self._refresh_index(trace)
        now = utc_now()

        async with self._store.session_lock(request.session_id):
            load_result = await self._store.load_session(
                request.session_id,
                now=now,
                ttl_seconds=self._settings.session_ttl_seconds,
            )
            if load_result.expired:
                emit(trace, "session.expired_cleaned", {"sessionId": request.session_id})
            if load_result.state is None:
                outcome = RouteOutcome(status=RouterStatus.FAILED, messages=["任务不存在或会话已过期。"])
                session = SessionState(sessionId=request.session_id, cust_no=request.cust_no)
                return self._build_frame(session, outcome, trace if request.debug_trace else None)

            session = load_result.state
            self._assert_owner(session, request.cust_no, trace)
            task = session.task_by_id(request.task_id)
            if task is None or task.is_terminal():
                emit(
                    trace,
                    "completion.rejected",
                    {
                        "taskId": request.task_id,
                        "reason": "missing_or_terminal",
                    },
                )
                outcome = RouteOutcome(status=RouterStatus.FAILED, messages=["任务不存在、已完成或已取消，不能重复确认。"])
                session.last_activity_at = utc_now()
                await self._store.save_session(session, ttl_seconds=self._settings.session_ttl_seconds)
                frame = self._build_frame(session, outcome, trace if request.debug_trace else None)
                _log_todo_list(frame.todo_list)
                return frame

            if request.completion_signal == 1:
                task.status = TaskStatus.DOING
                task.updated_at = utc_now()
                outcome = RouteOutcome(status=RouterStatus.AWAITING_COMPLETION, messages=["已收到阶段性完成确认。"])
                emit(trace, "completion.stage_confirmed", {"taskId": task.task_id})
            else:
                if task.status != TaskStatus.DOING:
                    emit(
                        trace,
                        "completion.rejected",
                        {
                            "taskId": request.task_id,
                            "reason": "task_not_doing",
                            "status": task.status,
                        },
                    )
                    outcome = RouteOutcome(status=RouterStatus.FAILED, messages=["任务尚未进入执行中，不能确认完成。"])
                    session.last_activity_at = utc_now()
                    await self._store.save_session(session, ttl_seconds=self._settings.session_ttl_seconds)
                    frame = self._build_frame(session, outcome, trace if request.debug_trace else None)
                    _log_todo_list(frame.todo_list)
                    return frame
                task.status = TaskStatus.COMPLETED
                task.updated_at = utc_now()
                task.release_context()
                emit(trace, "context.released", {"taskId": task.task_id, "reason": "completed"})
                emit(trace, "completion.final_confirmed", {"taskId": task.task_id})
                if session.current_task_id == task.task_id:
                    next_task = session.schedule_next_waiting()
                    if next_task:
                        emit(trace, "task.scheduled", {"taskId": next_task.task_id})
                        outcome = await self._advance_current_task(
                            session=session,
                            index=index,
                            execution_mode=ExecutionMode.ROUTE_ONLY,
                            trace=trace,
                        )
                    else:
                        outcome = await self._completion_follow_up_outcome(trace)
                else:
                    outcome = await self._completion_follow_up_outcome(trace)

            session.last_activity_at = utc_now()
            _append_history(
                session,
                role="system",
                text="\n".join(outcome.messages),
                event="task.completion",
                metadata={
                    "taskId": request.task_id,
                    "completionSignal": request.completion_signal,
                    "status": outcome.status,
                },
            )
            await self._store.save_session(session, ttl_seconds=self._settings.session_ttl_seconds)

        frame = self._build_frame(session, outcome, trace if request.debug_trace else None)
        _log_todo_list(frame.todo_list)
        return frame

    async def handle_handoff(self, request: HandoffRequest) -> HandoffFrame:
        trace: list[TraceEvent] = []
        emit(trace, "request.received", {"path": "/api/v1/task/handoff", "sessionId": request.session_id})
        index = await self._refresh_index(trace)
        now = utc_now()

        async with self._store.session_lock(request.session_id):
            load_result = await self._store.load_session(
                request.session_id,
                now=now,
                ttl_seconds=self._settings.session_ttl_seconds,
            )
            if load_result.expired:
                emit(trace, "session.expired_cleaned", {"sessionId": request.session_id})
            if load_result.state is None:
                return HandoffFrame(
                    sessionId=request.session_id,
                    taskId=request.task_id,
                    status="failed",
                    accepted=False,
                    messages=["任务不存在或会话已过期。"],
                    trace=trace if request.debug_trace else None,
                )

            session = load_result.state
            self._assert_owner(session, request.cust_no, trace)
            task = session.task_by_id(request.task_id)
            if task is None or task.is_terminal():
                emit(trace, "handoff.rejected", {"taskId": request.task_id, "reason": "missing_or_terminal"})
                frame = HandoffFrame(
                    sessionId=session.session_id,
                    taskId=request.task_id,
                    status="failed",
                    accepted=False,
                    currentTaskId=session.current_task_id,
                    todoList=_build_todo_list(session),
                    messages=["任务不存在、已完成或已取消，不能交接。"],
                    trace=trace if request.debug_trace else None,
                )
                _log_todo_list(frame.todo_list)
                return frame

            metadata = index.by_skill_id().get(task.skill_id)
            if metadata is None or metadata.intent_code != task.intent_code:
                emit(trace, "handoff.rejected", {"taskId": task.task_id, "reason": "skill disappeared or mismatched"})
                task.status = TaskStatus.EXCEPTED
                task.updated_at = utc_now()
                await self._store.save_session(session, ttl_seconds=self._settings.session_ttl_seconds)
                frame = HandoffFrame(
                    sessionId=session.session_id,
                    taskId=task.task_id,
                    status=TaskStatus.EXCEPTED,
                    accepted=False,
                    currentTaskId=session.current_task_id,
                    todoList=_build_todo_list(session),
                    messages=["任务配置已变化，当前任务无法交接。"],
                    trace=trace if request.debug_trace else None,
                )
                _log_todo_list(frame.todo_list)
                return frame

            body = await self._load_skill_for_task(task, metadata, trace)
            missing = _missing_required_slots(body, task.slots)
            task.missing_slots = missing
            task.updated_at = utc_now()
            emit(trace, "slots.evaluated", {"taskId": task.task_id, "missingSlots": missing})
            if missing:
                await self._store.save_session(session, ttl_seconds=self._settings.session_ttl_seconds)
                frame = HandoffFrame(
                    sessionId=session.session_id,
                    taskId=task.task_id,
                    status=TaskStatus.WAITING,
                    accepted=False,
                    currentTaskId=session.current_task_id,
                    todoList=_build_todo_list(session),
                    target=body.handoff_contract.target,
                    messages=_slot_prompts(body, missing),
                    trace=trace if request.debug_trace else None,
                )
                _log_todo_list(frame.todo_list)
                return frame

            payload = _handoff_payload(task, body)
            task.updated_at = utc_now()
            emit(trace, "handoff.prepared", {"taskId": task.task_id, "target": body.handoff_contract.target})

            dispatch_result = None
            accepted = True
            status = TaskStatus.WAITING
            messages = ["任务信息已完整，可以交接执行。"]
            if request.dispatch:
                if request.mock_dispatch:
                    dispatch_result = {
                        "ok": True,
                        "mock": True,
                        "taskId": task.task_id,
                        "target": body.handoff_contract.target,
                    }
                    task.status = TaskStatus.DOING
                    task.updated_at = utc_now()
                    status = TaskStatus.DOING
                    messages = ["mock 子智能体已接受任务，等待完成回调。"]
                    emit(trace, "handoff.mock_dispatched", {"taskId": task.task_id})
                elif not request.endpoint_url:
                    accepted = False
                    status = "failed"
                    messages = ["dispatch=true 时必须提供 endpointUrl。"]
                    emit(trace, "handoff.dispatch_rejected", {"taskId": task.task_id, "reason": "missing endpointUrl"})
                else:
                    dispatch_result = await self._dispatch_handoff(request, payload, trace)
                    accepted = bool(dispatch_result.get("ok"))
                    if accepted:
                        task.status = TaskStatus.DOING
                        task.updated_at = utc_now()
                        status = TaskStatus.DOING
                        messages = ["子智能体已接受任务，等待完成回调。"]
                    else:
                        task.status = TaskStatus.EXCEPTED
                        task.updated_at = utc_now()
                        task.release_context()
                        status = TaskStatus.EXCEPTED
                        messages = ["子智能体接口调用失败。"]
                        emit(trace, "task.excepted", {"taskId": task.task_id, "reason": "dispatch_failed"})

            session.last_activity_at = utc_now()
            _append_history(
                session,
                role="system",
                text="\n".join(messages),
                event="task.handoff",
                metadata={"taskId": task.task_id, "status": status, "dispatch": request.dispatch},
            )
            await self._store.save_session(session, ttl_seconds=self._settings.session_ttl_seconds)
            frame = HandoffFrame(
                sessionId=session.session_id,
                taskId=task.task_id,
                status=status,
                accepted=accepted,
                currentTaskId=session.current_task_id,
                todoList=_build_todo_list(session),
                target=body.handoff_contract.target,
                messages=messages,
                handoffPayload=payload,
                dispatchResult=dispatch_result,
                trace=trace if request.debug_trace else None,
            )
            _log_todo_list(frame.todo_list)
            return frame

    async def ready(self) -> dict[str, Any]:
        store_ok = await self._store.healthcheck()
        index = await self._config_source.get_last_good_index()
        if index is None:
            try:
                index = await self._config_source.refresh_index()
            except ConfigSourceError:
                index = None
        agentscope_ok = self._can_import_agentscope()
        ready = bool(
            store_ok
            and index is not None
            and agentscope_ok
            and self._settings.ready_model_configured
            and self._settings.ready_config_source_configured
        )
        return {
            "status": "ready" if ready else "not_ready",
            "checks": {
                "agentscope": agentscope_ok,
                "modelConfigured": self._settings.ready_model_configured,
                "configSourceConfigured": self._settings.ready_config_source_configured,
                "store": store_ok,
                "skillIndex": index is not None,
            },
        }

    async def _refresh_index(self, trace: list[TraceEvent]) -> SkillIndex:
        index = await self._config_source.refresh_index(trace)
        if index is None:
            emit(trace, "config.no_last_good", {})
            raise ConfigSourceError("no valid skill index is available")
        return index

    async def _completion_follow_up_outcome(self, trace: list[TraceEvent]) -> RouteOutcome:
        try:
            body = await self._config_source.load_skill_body_by_id(FOLLOW_UP_SKILL_ID, trace=trace)
        except ConfigSourceError:
            emit(trace, "follow_up.unavailable", {"skillId": FOLLOW_UP_SKILL_ID})
            return RouteOutcome(status=RouterStatus.COMPLETED, messages=["任务已完成。"])
        message = _follow_up_message(body)
        emit(trace, "follow_up.prepared", {"skillId": body.skill_id})
        return RouteOutcome(status=RouterStatus.COMPLETED, messages=[message])

    async def _refine_active_task_with_skill_body(
        self,
        *,
        request: MessageRequest,
        index: SkillIndex,
        session: SessionState,
        execution_mode: ExecutionMode,
        trace: list[TraceEvent],
    ) -> RouteOutcome | None:
        active_skill_body = await self._active_skill_body_for_planning(session, index, trace)
        if active_skill_body is None:
            return None
        emit(trace, "intent.progressive_refine_start", {"taskId": session.current_task_id})
        model_session_id = _create_model_session_mapping(
            session,
            purpose="progressive_skill_refine",
            trace=trace,
        )
        _log_message_context(request, index, session, active_skill_body, model_session_id)
        refined_plan = await self._planner.plan(
            user_text=request.txt,
            skill_index=index,
            session=session,
            model_session_id=model_session_id,
            active_skill_body=active_skill_body,
            trace=trace,
        )
        if refined_plan.action != "UPDATE_CURRENT_TASK":
            emit(trace, "intent.progressive_refine_ignored", {"action": refined_plan.action})
            return None
        return await self._apply_plan(
            session=session,
            index=index,
            plan=refined_plan,
            user_text=request.txt,
            execution_mode=execution_mode,
            trace=trace,
        )

    async def _load_or_create_session(
        self,
        *,
        session_id: str,
        cust_no: str,
        now,
        trace: list[TraceEvent],
    ) -> SessionState:
        load_result = await self._store.load_session(
            session_id,
            now=now,
            ttl_seconds=self._settings.session_ttl_seconds,
        )
        if load_result.expired:
            emit(trace, "session.expired_cleaned", {"sessionId": session_id})
        if load_result.state is None:
            emit(trace, "session.created", {"sessionId": session_id, "custNo": cust_no})
            return SessionState(sessionId=session_id, cust_no=cust_no)
        self._assert_owner(load_result.state, cust_no, trace)
        emit(
            trace,
            "session.loaded",
            {
                "sessionId": session_id,
                "taskCount": len(load_result.state.tasks),
                "currentTaskId": load_result.state.current_task_id,
            },
        )
        return load_result.state

    def _assert_owner(self, session: SessionState, cust_no: str, trace: list[TraceEvent]) -> None:
        if session.cust_no != cust_no:
            emit(
                trace,
                "session.owner_rejected",
                {"sessionId": session.session_id, "boundCustNo": session.cust_no},
            )
            raise SessionOwnershipError("sessionId is already bound to another cust_no")

    async def _active_skill_body_for_planning(
        self,
        session: SessionState,
        index: SkillIndex,
        trace: list[TraceEvent],
    ) -> SkillBody | None:
        task = session.active_task()
        if task is None:
            return None
        metadata = index.by_skill_id().get(task.skill_id)
        if metadata is None or metadata.intent_code != task.intent_code:
            return None
        body = await self._load_skill_for_task(task, metadata, trace)
        emit(
            trace,
            "intent.active_skill_loaded",
            {"taskId": task.task_id, "skillId": body.skill_id, "slotCount": len(body.slot_contract)},
        )
        return body

    async def _apply_plan(
        self,
        *,
        session: SessionState,
        index: SkillIndex,
        plan: PlannerResult,
        user_text: str,
        execution_mode: ExecutionMode,
        trace: list[TraceEvent],
    ) -> RouteOutcome:
        emit(trace, "intent.plan_received", {"action": plan.action, "intentCount": len(plan.intents)})

        if plan.action == "CANCEL_ALL":
            cancelled = []
            for task in session.tasks:
                if not task.is_terminal():
                    _remove_task_from_todo(task, reason="cancel_all", trace=trace)
                    cancelled.append(task.task_id)
            session.current_task_id = None
            emit(trace, "task.cancelled_all", {"taskIds": cancelled})
            return RouteOutcome(status=RouterStatus.CANCELLED, messages=[plan.message or "已取消全部任务。"])

        if plan.action == "CANCEL_TASK":
            target_task_id = plan.target_task_id or session.current_task_id
            task = session.task_by_id(target_task_id) if target_task_id else None
            if task is None or task.is_terminal():
                emit(trace, "task.cancel_rejected", {"taskId": target_task_id})
                return RouteOutcome(status=RouterStatus.CLARIFYING, messages=["没有可取消的进行中任务。"])
            _remove_task_from_todo(task, reason="cancelled", trace=trace)
            emit(trace, "task.cancelled", {"taskId": task.task_id})
            if session.current_task_id == task.task_id:
                session.schedule_next_waiting()
                if session.current_task_id:
                    return await self._advance_current_task(
                        session=session,
                        index=index,
                        execution_mode=execution_mode,
                        trace=trace,
                    )
            return RouteOutcome(status=RouterStatus.CANCELLED, messages=[plan.message or "已取消该任务。"])

        if plan.action == "SWITCH_TASK":
            task = session.task_by_id(plan.target_task_id) if plan.target_task_id else None
            if task is None or task.is_terminal():
                emit(trace, "task.switch_rejected", {"taskId": plan.target_task_id})
                return RouteOutcome(status=RouterStatus.CLARIFYING, messages=["我还不能确定要切换到哪个任务。"])
            session.current_task_id = task.task_id
            emit(trace, "task.switched", {"taskId": task.task_id})
            return await self._advance_current_task(
                session=session,
                index=index,
                execution_mode=execution_mode,
                trace=trace,
            )

        if plan.action == "UPDATE_CURRENT_TASK":
            task = session.active_task()
            if task is None:
                return RouteOutcome(status=RouterStatus.CLARIFYING, messages=["当前没有可补充的任务，请先说明要办理什么。"])
            metadata = index.by_skill_id().get(task.skill_id)
            if metadata is None or metadata.intent_code != task.intent_code:
                return RouteOutcome(status=RouterStatus.FAILED, messages=["任务配置已变化，当前任务无法继续。"])
            body = await self._load_skill_for_task(task, metadata, trace)
            _reject_undeclared_slot_updates(task, plan.slot_updates, body, trace)
            task.slots.update(_clean_slot_updates(plan.slot_updates, allowed_slots=_slot_names(body)))
            task.updated_at = utc_now()
            emit(
                trace,
                "slots.updated",
                {"taskId": task.task_id, "slotNames": sorted(plan.slot_updates.keys())},
                )
            await self._load_authorized_references(task, index, plan.requested_reference_keys, trace)
            _drop_undeclared_slots(task, body, trace)
            return await self._advance_current_task(
                session=session,
                index=index,
                execution_mode=execution_mode,
                trace=trace,
            )

        if plan.action == "CREATE_TASKS":
            if not plan.intents:
                emit(trace, "intent.no_match", {})
                return RouteOutcome(
                    status=RouterStatus.CLARIFYING,
                    messages=[plan.message or "我还没识别出明确业务意图，请再说明一下。"],
                )
            active_before_create = session.active_task()
            if active_before_create is None:
                _archive_terminal_todos(session, reason="new_user_request", trace=trace)
            selected = await self._create_tasks(session, index, plan.intents, trace)
            if not selected:
                return RouteOutcome(status=RouterStatus.FAILED, messages=["意图识别结果未通过服务端校验。"])
            active = active_before_create
            if active is None:
                session.current_task_id = selected[0].task_id
                emit(trace, "task.scheduled", {"taskId": session.current_task_id})
                return await self._advance_current_task(
                    session=session,
                    index=index,
                    execution_mode=execution_mode,
                    trace=trace,
                )
            if selected[0].task_id == active.task_id:
                return await self._advance_current_task(
                    session=session,
                    index=index,
                    execution_mode=execution_mode,
                    trace=trace,
                )
            if plan.interrupt_current_task or _looks_like_interrupt(user_text):
                _remove_task_from_todo(active, reason="user_interrupted", trace=trace)
                emit(
                    trace,
                    "task.interrupted",
                    {"taskId": active.task_id, "replacementTaskId": selected[0].task_id},
                )
            else:
                active.status = TaskStatus.WAITING
                active.updated_at = utc_now()
                active.release_context()
                emit(
                    trace,
                    "context.released",
                    {"taskId": active.task_id, "reason": "switched_to_new_intent"},
                )
            session.current_task_id = selected[0].task_id
            emit(
                trace,
                "task.switched",
                {"fromTaskId": active.task_id, "toTaskId": session.current_task_id},
            )
            return await self._advance_current_task(
                session=session,
                index=index,
                execution_mode=execution_mode,
                trace=trace,
            )

        if plan.action in {"CLARIFY", "NO_INTENT"}:
            emit(trace, "clarification.required", {"reason": plan.action})
            return RouteOutcome(
                status=RouterStatus.CLARIFYING,
                messages=[plan.message or "我需要再确认一下你的具体诉求。"],
            )

        raise PlannerRejectedError(f"unsupported planner action: {plan.action}")

    async def _create_tasks(
        self,
        session: SessionState,
        index: SkillIndex,
        intents: list[IntentPlan],
        trace: list[TraceEvent],
    ) -> list[TaskState]:
        metadata_by_skill = index.by_skill_id()
        selected: list[TaskState] = []

        def sort_key(plan: IntentPlan) -> tuple[int, int]:
            metadata = metadata_by_skill.get(plan.skill_id) if plan.skill_id else _metadata_for_plan(index, plan)
            return (plan.order, metadata.priority if metadata else 10_000)

        for plan in sorted(intents, key=sort_key):
            metadata = _metadata_for_plan(index, plan)
            if metadata is None:
                emit(
                    trace,
                    "intent.validation_failed",
                    {
                        "skillId": plan.skill_id,
                        "intentCode": plan.intent_code,
                        "reason": "skill/name mismatch",
                    },
                )
                continue
            existing = _find_non_terminal_task(session, metadata)
            if existing is not None:
                session.current_task_id = existing.task_id
                existing.updated_at = utc_now()
                selected.append(existing)
                emit(
                    trace,
                    "task.reused",
                    {"taskId": existing.task_id, "intentCode": existing.intent_code, "skillId": existing.skill_id},
                )
                continue
            task = TaskState(
                intentCode=metadata.intent_code,
                skillId=metadata.skill_id,
                skillVersion=metadata.version,
                bodyKey=metadata.body_key,
                slots=_clean_slot_updates(plan.extracted_slots),
            )
            session.tasks.append(task)
            body = await self._load_skill_for_task(task, metadata, trace)
            _drop_undeclared_slots(task, body, trace)
            selected.append(task)
            emit(
                trace,
                "task.created",
                {
                    "taskId": task.task_id,
                    "intentCode": task.intent_code,
                    "skillId": task.skill_id,
                    "skillVersion": task.skill_version,
                },
            )
        return selected

    async def _advance_current_task(
        self,
        *,
        session: SessionState,
        index: SkillIndex,
        execution_mode: ExecutionMode,
        trace: list[TraceEvent],
    ) -> RouteOutcome:
        task = session.active_task()
        if task is None:
            return RouteOutcome(status=RouterStatus.COMPLETED, messages=["当前没有待处理任务。"])

        metadata = index.by_skill_id().get(task.skill_id)
        if metadata is None or metadata.intent_code != task.intent_code:
            task.status = TaskStatus.EXCEPTED
            task.release_context()
            emit(trace, "task.excepted", {"taskId": task.task_id, "reason": "skill disappeared or mismatched"})
            return RouteOutcome(status=RouterStatus.FAILED, messages=["任务配置已变化，当前任务无法继续。"])

        body = await self._load_skill_for_task(task, metadata, trace)
        _drop_undeclared_slots(task, body, trace)
        missing = _missing_required_slots(body, task.slots)
        task.missing_slots = missing
        task.updated_at = utc_now()
        emit(trace, "slots.evaluated", {"taskId": task.task_id, "missingSlots": missing})

        if missing:
            prompts = _slot_prompts(body, missing)
            return RouteOutcome(status=RouterStatus.COLLECTING_SLOTS, messages=prompts)

        handoff_payload = _handoff_payload(task, body)
        if execution_mode == ExecutionMode.MOCK_HANDOFF:
            task.status = TaskStatus.DOING
            mock_result = {
                "mock": True,
                "accepted": True,
                "taskId": task.task_id,
                "executor": body.handoff_contract.target,
            }
            emit(trace, "handoff.mock_created", {"taskId": task.task_id})
            return RouteOutcome(
                status=RouterStatus.AWAITING_COMPLETION,
                messages=["任务信息已完整，已生成 mock 交接结果。"],
                handoff_payload=handoff_payload,
                mock_handoff_result=mock_result,
            )

        emit(trace, "handoff.ready", {"taskId": task.task_id, "target": body.handoff_contract.target})
        return RouteOutcome(
            status=RouterStatus.HANDOFF_READY,
            messages=["任务信息已完整，可以交接执行。"],
            handoff_payload=handoff_payload,
        )

    async def _load_skill_for_task(
        self,
        task: TaskState,
        metadata: SkillMetadata,
        trace: list[TraceEvent],
    ) -> SkillBody:
        if task.lease is None:
            task.lease = ContextLease(
                skillId=metadata.skill_id,
                skillVersion=metadata.version,
                bodyKey=metadata.body_key,
            )
            emit(
                trace,
                "context.lease_created",
                {"taskId": task.task_id, "skillId": metadata.skill_id, "skillVersion": metadata.version},
            )
        return await self._config_source.load_skill_body(metadata.skill_id, metadata.version, trace)

    async def _load_authorized_references(
        self,
        task: TaskState,
        index: SkillIndex,
        requested_keys: list[str],
        trace: list[TraceEvent],
    ) -> None:
        if not requested_keys:
            return
        metadata = index.by_skill_id().get(task.skill_id)
        if metadata is None:
            return
        if task.lease is None:
            task.lease = ContextLease(
                skillId=metadata.skill_id,
                skillVersion=metadata.version,
                bodyKey=metadata.body_key,
            )
        allowed = set(metadata.allowed_reference_keys)
        for reference_key in requested_keys:
            if reference_key not in allowed:
                emit(
                    trace,
                    "reference.rejected",
                    {
                        "taskId": task.task_id,
                        "referenceKey": reference_key,
                        "reason": "not declared by skill",
                    },
                )
                continue
            await self._config_source.load_reference(reference_key, metadata.version, trace)
            if reference_key not in task.lease.loaded_reference_keys:
                task.lease.loaded_reference_keys.append(reference_key)
            emit(trace, "reference.allowed", {"taskId": task.task_id, "referenceKey": reference_key})

    def _build_frame(
        self,
        session: SessionState,
        outcome: RouteOutcome,
        trace: list[TraceEvent] | None,
    ) -> BusinessFrame:
        task_frames = [
            TaskFrame(
                taskId=task.task_id,
                intentCode=task.intent_code,
                skillId=task.skill_id,
                status=task.status,
                slots=task.slots,
                missingSlots=task.missing_slots,
                todoVisible=task.todo_visible,
                interruptedReason=task.interrupted_reason,
            )
            for task in session.tasks
        ]
        current_task = next((task for task in task_frames if task.task_id == session.current_task_id), None)
        todo_list = _build_todo_list(session)
        return BusinessFrame(
            sessionId=session.session_id,
            status=outcome.status,
            currentTaskId=session.current_task_id,
            tasks=task_frames if trace is not None else [],
            currentTask=current_task,
            todoList=todo_list,
            messages=outcome.messages,
            handoffPayload=outcome.handoff_payload,
            mockHandoffResult=outcome.mock_handoff_result,
            trace=trace,
        )

    @staticmethod
    def _can_import_agentscope() -> bool:
        try:
            import agentscope  # noqa: F401

            return True
        except Exception:
            return False

    async def _dispatch_handoff(
        self,
        request: HandoffRequest,
        handoff_payload: dict[str, Any],
        trace: list[TraceEvent],
    ) -> dict[str, Any]:
        body = {
            "sessionId": request.session_id,
            "cust_no": request.cust_no,
            "taskId": request.task_id,
            "handoffPayload": handoff_payload,
            "extraPayload": request.extra_payload,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.handoff_request_timeout_seconds,
                trust_env=False,
            ) as client:
                response = await client.post(str(request.endpoint_url), json=body)
        except httpx.HTTPError as exc:
            emit(trace, "handoff.dispatch_failed", {"taskId": request.task_id, "reason": type(exc).__name__})
            return {"ok": False, "error": type(exc).__name__}

        response_text = response.text[:1000]
        emit(
            trace,
            "handoff.dispatched",
            {"taskId": request.task_id, "statusCode": response.status_code},
        )
        return {
            "ok": 200 <= response.status_code < 300,
            "statusCode": response.status_code,
            "body": response_text,
        }


def emit(trace: list[TraceEvent], stage: str, detail: dict[str, Any]) -> None:
    trace.append(TraceEvent(stage=stage, detail=detail))


def _build_todo_list(session: SessionState) -> list[TodoItem]:
    visible_tasks = [task for task in session.tasks if task.todo_visible]
    return [
        TodoItem(
            order=idx + 1,
            taskId=task.task_id,
            name=task.intent_code,
            status=task.status,
            current=task.task_id == session.current_task_id,
            missingSlots=task.missing_slots,
        )
        for idx, task in enumerate(visible_tasks)
    ]


def _remove_task_from_todo(task: TaskState, *, reason: str, trace: list[TraceEvent]) -> None:
    task.todo_visible = False
    task.interrupted_reason = reason
    task.updated_at = utc_now()
    task.release_context()
    emit(trace, "context.released", {"taskId": task.task_id, "reason": reason})
    emit(trace, "todo.removed", {"taskId": task.task_id, "reason": reason})


def _archive_terminal_todos(session: SessionState, *, reason: str, trace: list[TraceEvent]) -> None:
    archived = []
    for task in session.tasks:
        if task.todo_visible and TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
            task.todo_visible = False
            task.updated_at = utc_now()
            task.release_context()
            archived.append(task.task_id)
    if archived:
        emit(trace, "todo.archived", {"taskIds": archived, "reason": reason})


def _append_history(
    session: SessionState,
    *,
    role: str,
    text: str,
    event: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not text and not metadata:
        return
    session.conversation_history.append(
        ConversationTurn(
            role=role,
            text=text,
            event=event,
            metadata=metadata or {},
        ),
    )


def _create_model_session_mapping(
    session: SessionState,
    *,
    purpose: str,
    trace: list[TraceEvent],
) -> str:
    model_session_id = f"model_{uuid4().hex}"
    session.model_session_mappings.append(
        ModelSessionMapping(
            projectSessionId=session.session_id,
            modelSessionId=model_session_id,
            purpose=purpose,
        ),
    )
    emit(
        trace,
        "model_session.created",
        {
            "projectSessionId": session.session_id,
            "modelSessionId": model_session_id,
            "purpose": purpose,
        },
    )
    return model_session_id


def _log_todo_list(todo_list: list[TodoItem]) -> None:
    lines = []
    for item in todo_list:
        mark = ">" if item.current else " "
        missing = f" missing={','.join(item.missing_slots)}" if item.missing_slots else ""
        lines.append(f"{mark} {item.order}. {item.name} [{item.status}]{missing}")
    logger.info("message.todo_list=%s", "\n".join(lines))


def _looks_like_interrupt(text: str) -> bool:
    compact = "".join(text.split())
    if not compact:
        return False
    explicit_markers = (
        "不办了",
        "不用了",
        "不要了",
        "先不",
        "不需要了",
        "改成",
        "换成",
        "换一个",
        "算了",
    )
    if any(marker in compact for marker in explicit_markers):
        return True
    return bool(("不" in compact or "取消" in compact) and ("我想" in compact or "我要" in compact))


def _clean_slot_updates(slots: dict[str, Any], *, allowed_slots: set[str] | None = None) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in slots.items()
        if key is not None
        and value is not None
        and (allowed_slots is None or str(key) in allowed_slots)
        and (not isinstance(value, str) or value.strip())
    }


def _slot_names(body: SkillBody) -> set[str]:
    return {slot.name for slot in body.slot_contract}


def _drop_undeclared_slots(task: TaskState, body: SkillBody, trace: list[TraceEvent]) -> None:
    allowed = _slot_names(body)
    dropped = sorted(name for name in task.slots if name not in allowed)
    if not dropped:
        return
    task.slots = {name: value for name, value in task.slots.items() if name in allowed}
    emit(trace, "slots.rejected", {"taskId": task.task_id, "slotNames": dropped, "reason": "not declared by skill"})


def _reject_undeclared_slot_updates(
    task: TaskState,
    slot_updates: dict[str, Any],
    body: SkillBody,
    trace: list[TraceEvent],
) -> None:
    allowed = _slot_names(body)
    rejected = sorted(str(name) for name in slot_updates if str(name) not in allowed)
    if rejected:
        emit(trace, "slots.rejected", {"taskId": task.task_id, "slotNames": rejected, "reason": "not declared by skill"})


def _missing_required_slots(body: SkillBody, slots: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for spec in body.slot_contract:
        if not spec.required:
            continue
        value = slots.get(spec.name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(spec.name)
    return missing


def _slot_prompts(body: SkillBody, missing: list[str]) -> list[str]:
    prompts = []
    specs = {spec.name: spec for spec in body.slot_contract}
    for name in missing:
        prompts.append(specs[name].prompt or f"请补充{name}。")
    return prompts or ["请补充必要信息。"]


def _handoff_payload(task: TaskState, body: SkillBody) -> dict[str, Any]:
    return {
        "taskId": task.task_id,
        "intentCode": task.intent_code,
        "skillId": task.skill_id,
        "skillVersion": task.skill_version,
        "slots": task.slots,
        "handoffContract": body.handoff_contract.model_dump(by_alias=True),
    }


def _metadata_for_plan(index: SkillIndex, plan: IntentPlan) -> SkillMetadata | None:
    if plan.skill_id:
        metadata = index.by_skill_id().get(plan.skill_id)
        if metadata is not None and (plan.intent_code is None or metadata.intent_code == plan.intent_code):
            return metadata
    if plan.intent_code:
        return index.by_intent_code().get(plan.intent_code) or index.by_skill_id().get(plan.intent_code)
    return None


def _find_non_terminal_task(session: SessionState, metadata: SkillMetadata) -> TaskState | None:
    for task in session.tasks:
        if task.skill_id == metadata.skill_id and task.intent_code == metadata.intent_code and not task.is_terminal():
            return task
    return None


def _follow_up_message(body: SkillBody) -> str:
    for line in body.rules_md.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped.lstrip("- ").strip()
    return "任务已完成，你还想进行什么操作呢？"


def _log_message_context(
    request: MessageRequest,
    skill_index: SkillIndex,
    session: SessionState,
    active_skill_body: SkillBody | None,
    model_session_id: str,
) -> None:
    loaded_skill_bodies = []
    if active_skill_body is not None:
        loaded_skill_bodies.append(_skill_body_model_context(active_skill_body))
    context = {
        "request": request.model_dump(by_alias=True, mode="json"),
        "projectSessionId": session.session_id,
        "currentModelSessionId": model_session_id,
        "modelSessionMappings": _model_session_mapping_context(session),
        "availableSkills": _available_skill_context(skill_index),
        "session": _session_model_context(session),
        "conversationHistory": _conversation_history_context(session),
        "skillBodyLoadPolicy": {
            "loadedForMessagePlanning": active_skill_body is not None,
            "reason": "active_task_slot_filling" if active_skill_body else "initial_intent_recognition_uses_metadata_only",
        },
        "loadedSkillBodies": loaded_skill_bodies,
    }
    logger.info("message.full_context=%s", json.dumps(context, ensure_ascii=False, default=str))


def _available_skill_context(skill_index: SkillIndex) -> list[dict[str, str]]:
    return [
        {
            "name": skill.skill_id,
            "description": skill.summary,
        }
        for skill in skill_index.skills
    ]


def _session_model_context(session: SessionState) -> dict[str, Any]:
    return {
        "currentTaskId": session.current_task_id,
        "tasks": [
            {
                "taskId": task.task_id,
                "name": task.skill_id,
                "status": task.status,
                "todoVisible": task.todo_visible,
                "slots": task.slots,
                "missingSlots": task.missing_slots,
                "interruptedReason": task.interrupted_reason,
            }
            for task in session.tasks
        ],
    }


def _model_session_mapping_context(session: SessionState) -> list[dict[str, Any]]:
    return [
        {
            "projectSessionId": item.project_session_id,
            "modelSessionId": item.model_session_id,
            "purpose": item.purpose,
            "createdAt": item.created_at.isoformat(),
        }
        for item in session.model_session_mappings
    ]


def _conversation_history_context(session: SessionState) -> list[dict[str, Any]]:
    return [
        {
            "role": turn.role,
            "text": turn.text,
            "event": turn.event,
            "metadata": turn.metadata,
            "ts": turn.ts.isoformat(),
        }
        for turn in session.conversation_history
    ]


def _skill_body_model_context(body: SkillBody) -> dict[str, Any]:
    return {
        "body": body.rules_md,
        "rulesMd": body.rules_md,
        "slotContract": [
            {
                "name": slot.name,
                "required": slot.required,
                "prompt": slot.prompt,
                "description": slot.description,
            }
            for slot in body.slot_contract
        ],
    }
