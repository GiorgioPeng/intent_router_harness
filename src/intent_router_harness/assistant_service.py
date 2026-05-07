from __future__ import annotations

import json
import logging
from typing import Any

from intent_router_harness.contracts import (
    AssistantProtocolFrame,
    AssistantServiceResult,
    AssistantTraceEvent,
    PlannedTask,
    PlannerOutput,
    RouterMessageRequest,
    TaskCompletionRequest,
    TaskRuntimeState,
)
from intent_router_harness.planner import MessagePlanner, PlannerError
from intent_router_harness.session_store import InMemorySessionStore
from intent_router_harness.trace import emit_trace

logger = logging.getLogger(__name__)


class AssistantProtocolService:
    """Task-first assistant protocol runtime backed by spec-driven planning."""

    def __init__(
        self,
        *,
        planner: MessagePlanner,
        sessions: InMemorySessionStore | None = None,
    ) -> None:
        self.planner = planner
        self.sessions = sessions or InMemorySessionStore()

    def handle_message(self, request: RouterMessageRequest) -> AssistantServiceResult:
        """Plan one user message and return assistant protocol frames."""
        trace_events: list[AssistantTraceEvent] = []
        if request.debugTrace:
            trace_events.append(
                AssistantTraceEvent(
                    stage="request_received",
                    title="请求进入Router",
                    summary=f"session={request.sessionId}，executionMode={request.executionMode}",
                    data={
                        "session_id": request.sessionId,
                        "stream": request.stream,
                        "execution_mode": request.executionMode,
                        "text": request.txt,
                        "user_binding_id": _request_user_binding_id(request),
                    },
                )
            )
            emit_trace(trace_events[-1])
        logger.info(
            "core.message.start session_id=%s stream=%s execution_mode=%s text=%s",
            request.sessionId,
            request.stream,
            request.executionMode,
            _truncate_for_log(request.txt, 300),
        )
        user_binding_id = _request_user_binding_id(request)
        session_load = self.sessions.load(request.sessionId, user_binding_id=user_binding_id)
        session = session_load.session
        task_state = session_load.task_state
        logger.info(
            "core.session.loaded session_id=%s user_binding_id=%s expired=%s user_bound=%s expires_at=%s",
            request.sessionId,
            session.user_binding_id,
            session_load.expired,
            session_load.user_bound,
            session.expires_at.isoformat() if session.expires_at else None,
        )
        logger.info(
            "core.task_state.loaded session_id=%s slot_memory=%s current_task=%s task_count=%d active_context=%s",
            request.sessionId,
            _json_for_log(task_state.slot_memory, 1000),
            _task_for_log(task_state.current_task),
            len(task_state.task_list),
            _json_for_log(task_state.active_context, 1000),
        )
        if request.debugTrace:
            trace_events.append(
                AssistantTraceEvent(
                    stage="session_loaded",
                    title="Session生命周期读取",
                    summary=f"expired={session_load.expired}，expires_at={session.expires_at.isoformat() if session.expires_at else ''}",
                    data={
                        "session_id": session.session_id,
                        "user_binding_id": session.user_binding_id,
                        "expired": session_load.expired,
                        "user_bound": session_load.user_bound,
                        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
                    },
                )
            )
            emit_trace(trace_events[-1])
            trace_events.append(
                AssistantTraceEvent(
                    stage="task_runtime_loaded",
                    title="任务运行态读取",
                    summary=f"task_count={len(task_state.task_list)}",
                    data=_task_state_trace_data(task_state),
                )
            )
            emit_trace(trace_events[-1])
            if session_load.user_bound:
                trace_events.append(
                    AssistantTraceEvent(
                        stage="session_user_bound",
                        title="Session用户绑定",
                        summary=f"session={request.sessionId} 已绑定用户",
                        data={
                            "session_id": request.sessionId,
                            "user_binding_id": session.user_binding_id,
                        },
                    )
                )
                emit_trace(trace_events[-1])
            if session_load.expired:
                trace_events.append(
                    AssistantTraceEvent(
                        stage="session_memory_cleared",
                        title="Session过期清理",
                        summary="旧 session 已过期，关联任务运行态已清理",
                        data={
                            "session_id": request.sessionId,
                            "user_binding_id": session.user_binding_id,
                        },
                    )
                )
                emit_trace(trace_events[-1])
        try:
            plan = self.planner.plan_message(request, task_state)
        except PlannerError as exc:
            logger.exception(
                "core.planner.failed session_id=%s error=%s",
                request.sessionId,
                exc,
            )
            failed = AssistantProtocolFrame(
                ok=False,
                status="failed",
                completion_state=2,
                completion_reason="router_error",
                errorCode="ROUTER_PLANNER_ERROR",
                message=str(exc),
                output={},
            )
            logger.info(
                "core.sse.frames session_id=%s frame_count=1 frame_statuses=%s frame_reasons=%s",
                request.sessionId,
                ["failed"],
                ["router_error"],
            )
            if request.debugTrace:
                trace_events.append(
                    AssistantTraceEvent(
                        stage="planner_error",
                        title="Planner执行失败",
                        summary=str(exc),
                        data={"error": str(exc)},
                    )
                )
                emit_trace(trace_events[-1])
            return AssistantServiceResult(frames=[failed], trace_events=trace_events)

        if request.debugTrace:
            trace_events.extend(_trace_events_from_plan(plan))

        logger.info(
            "core.plan.received session_id=%s mode=%s status=%s completion_reason=%s intent_code=%s task_count=%d has_graph=%s action_count=%d diagnostics=%s",
            request.sessionId,
            plan.mode,
            plan.status,
            plan.completion_reason,
            _effective_intent_code(plan),
            len(plan.task_list),
            plan.graph is not None,
            len(plan.actions),
            _json_for_log(_diagnostics_for_log(plan), 1000),
        )

        frames: list[AssistantProtocolFrame] = []
        recognition = _recognition_frame(plan)
        if recognition is not None:
            frames.append(recognition)
            logger.info(
                "core.intent.recognized session_id=%s intent_code=%s stage=%s completion_reason=%s",
                request.sessionId,
                recognition.intent_code,
                recognition.stage,
                recognition.completion_reason,
            )
            logger.info(
                "core.trace step=intent_recognition session_id=%s result=recognized intent_code=%s",
                request.sessionId,
                recognition.intent_code,
            )
            if request.debugTrace:
                trace_events.append(
                    AssistantTraceEvent(
                        stage="intent_recognition",
                        title="意图识别结果",
                        summary=f"识别到 intent_code={recognition.intent_code}",
                        data={
                            "intent_code": recognition.intent_code,
                            "completion_reason": recognition.completion_reason,
                        },
                    )
                )
                emit_trace(trace_events[-1])
        else:
            logger.info(
                "core.intent.not_recognized session_id=%s status=%s completion_reason=%s",
                request.sessionId,
                plan.status,
                plan.completion_reason,
            )
            logger.info(
                "core.trace step=intent_recognition session_id=%s result=not_recognized status=%s completion_reason=%s",
                request.sessionId,
                plan.status,
                plan.completion_reason,
            )
            if request.debugTrace:
                trace_events.append(
                    AssistantTraceEvent(
                        stage="intent_recognition",
                        title="意图识别结果",
                        summary="未识别出可派发业务意图",
                        data={
                            "status": plan.status,
                            "completion_reason": plan.completion_reason,
                        },
                    )
                )
                emit_trace(trace_events[-1])
        business = _business_frame(plan)
        frames.append(business)

        logger.info(
            "core.slot.result session_id=%s status=%s slot_memory=%s message=%s",
            request.sessionId,
            business.status,
            _json_for_log(business.slot_memory, 1000),
            _truncate_for_log(business.message or "", 500),
        )
        logger.info(
            "core.trace step=slot_and_skill_result session_id=%s status=%s slot_memory=%s ask_user=%s",
            request.sessionId,
            business.status,
            _json_for_log(business.slot_memory, 1000),
            _truncate_for_log(business.message or "", 500),
        )
        if request.debugTrace:
            trace_events.append(
                AssistantTraceEvent(
                    stage="slot_and_skill_result",
                    title="Skill提槽/业务结果",
                    summary=f"status={business.status}，ask_user={business.message or ''}",
                    data={
                        "status": business.status,
                        "completion_reason": business.completion_reason,
                        "slot_memory": business.slot_memory,
                        "message": business.message,
                        "output": business.output,
                        "task_list": business.task_list,
                        "current_task": business.current_task,
                    },
                )
            )
            emit_trace(trace_events[-1])
        logger.info(
            "core.task.result session_id=%s current_task=%s task_count=%d output=%s",
            request.sessionId,
            _json_for_log(business.current_task, 1200),
            len(business.task_list),
            _json_for_log(business.output, 1000),
        )
        logger.info(
            "core.trace step=assistant_protocol_frames session_id=%s frame_count=%d final_status=%s final_reason=%s",
            request.sessionId,
            len(frames),
            business.status,
            business.completion_reason,
        )
        if request.debugTrace:
            trace_events.append(
                AssistantTraceEvent(
                    stage="assistant_protocol_frames",
                    title="SSE业务帧生成",
                    summary=f"生成 {len(frames)} 个 message frame，最终状态 {business.status}",
                    data={
                        "frame_count": len(frames),
                        "frames": [frame.protocol_dump() for frame in frames],
                    },
                )
            )
            emit_trace(trace_events[-1])
        logger.info(
            "core.sse.frames session_id=%s frame_count=%d frame_statuses=%s frame_reasons=%s",
            request.sessionId,
            len(frames),
            [frame.status for frame in frames],
            [frame.completion_reason for frame in frames],
        )

        updated_task_state = _apply_plan(task_state, plan)
        self.sessions.save_task_state(request.sessionId, updated_task_state)
        logger.info(
            "core.task_state.saved session_id=%s slot_memory=%s current_task=%s task_count=%d active_context=%s",
            request.sessionId,
            _json_for_log(updated_task_state.slot_memory, 1000),
            _task_for_log(updated_task_state.current_task),
            len(updated_task_state.task_list),
            _json_for_log(updated_task_state.active_context, 1000),
        )
        if request.debugTrace and task_state.active_context and not updated_task_state.active_context:
            trace_events.append(
                AssistantTraceEvent(
                    stage="context_released",
                    title="任务上下文释放",
                    summary="当前任务已结束，释放 skill/reference lease",
                    data={
                        "session_id": request.sessionId,
                        "released_context": task_state.active_context,
                    },
                )
            )
            emit_trace(trace_events[-1])
        return AssistantServiceResult(frames=frames, trace_events=trace_events)

    def handle_task_completion(self, request: TaskCompletionRequest) -> AssistantServiceResult:
        """Apply assistant completion signal to current task state."""
        trace_events: list[AssistantTraceEvent] = []
        if request.debugTrace:
            trace_events.append(
                AssistantTraceEvent(
                    stage="task_completion_received",
                    title="任务完成回调进入Router",
                    summary=f"taskId={request.taskId}，completionSignal={request.completionSignal}",
                    data={
                        "session_id": request.sessionId,
                        "task_id": request.taskId,
                        "completion_signal": request.completionSignal,
                    },
                )
            )
            emit_trace(trace_events[-1])
        logger.info(
            "core.task_completion.start session_id=%s task_id=%s completion_signal=%s stream=%s",
            request.sessionId,
            request.taskId,
            request.completionSignal,
            request.stream,
        )
        session_load = self.sessions.load(request.sessionId)
        session = session_load.session
        task_state = session_load.task_state
        logger.info(
            "core.session.loaded session_id=%s user_binding_id=%s expired=%s expires_at=%s",
            request.sessionId,
            session.user_binding_id,
            session_load.expired,
            session.expires_at.isoformat() if session.expires_at else None,
        )
        logger.info(
            "core.task_state.loaded session_id=%s slot_memory=%s current_task=%s task_count=%d active_context=%s",
            request.sessionId,
            _json_for_log(task_state.slot_memory, 1000),
            _task_for_log(task_state.current_task),
            len(task_state.task_list),
            _json_for_log(task_state.active_context, 1000),
        )
        if request.debugTrace:
            trace_events.append(
                AssistantTraceEvent(
                    stage="session_loaded",
                    title="Session生命周期读取",
                    summary=f"expired={session_load.expired}，expires_at={session.expires_at.isoformat() if session.expires_at else ''}",
                    data={
                        "session_id": session.session_id,
                        "user_binding_id": session.user_binding_id,
                        "expired": session_load.expired,
                        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
                    },
                )
            )
            emit_trace(trace_events[-1])
            trace_events.append(
                AssistantTraceEvent(
                    stage="task_runtime_loaded",
                    title="任务运行态读取",
                    summary=f"task_count={len(task_state.task_list)}",
                    data=_task_state_trace_data(task_state),
                )
            )
            emit_trace(trace_events[-1])
            if session_load.expired:
                trace_events.append(
                    AssistantTraceEvent(
                        stage="session_memory_cleared",
                        title="Session过期清理",
                        summary="旧 session 已过期，关联任务运行态已清理",
                        data={
                            "session_id": request.sessionId,
                            "user_binding_id": session.user_binding_id,
                        },
                    )
                )
                emit_trace(trace_events[-1])
        original_context = task_state.active_context
        existing_task = next((task for task in task_state.task_list if task.taskId == request.taskId), None)
        if existing_task is None:
            return _failed_completion_result(
                request,
                task_state,
                trace_events,
                completion_reason="assistant_task_not_found",
                error_code="TASK_NOT_FOUND",
                message=f"taskId {request.taskId!r} does not exist in session",
            )
        if existing_task.status in {"completed", "cancelled", "failed"}:
            return _failed_completion_result(
                request,
                task_state,
                trace_events,
                completion_reason="assistant_task_already_terminal",
                error_code="TASK_ALREADY_TERMINAL",
                message=f"taskId {request.taskId!r} is already {existing_task.status}",
            )
        task_list = [
            task.model_copy(update={"status": "completed"})
            if task.taskId == request.taskId and request.completionSignal == 2
            else task
            for task in task_state.task_list
        ]
        completed_task = next((task for task in task_list if task.taskId == request.taskId), None)
        completed_frame = AssistantProtocolFrame(
            ok=True,
            status="completed" if request.completionSignal == 2 else "waiting_assistant_completion",
            intent_code=completed_task.intent_code if completed_task else None,
            completion_state=2 if request.completionSignal == 2 else 1,
            completion_reason="assistant_final_done"
            if request.completionSignal == 2
            else "assistant_stage_done",
            output=completed_task.output if completed_task else {},
            slot_memory=completed_task.slot_memory if completed_task else task_state.slot_memory,
            task_list=[task.model_dump(mode="json") for task in task_list],
            current_task=completed_task.model_dump(mode="json") if completed_task else None,
            graph=task_state.graph,
        )

        frames = [completed_frame]
        next_task = (
            _next_active_task_after(task_list, request.taskId)
            if request.completionSignal == 2
            else None
        )
        if next_task is not None and next_task.status == "ready_for_dispatch":
            next_task = next_task.model_copy(update={"status": "waiting_assistant_completion"})
            task_list = [
                next_task if task.taskId == next_task.taskId else task
                for task in task_list
            ]
            frames.append(
                AssistantProtocolFrame(
                    ok=True,
                    status="waiting_assistant_completion",
                    intent_code=next_task.intent_code,
                    completion_state=1,
                    completion_reason="assistant_confirmation_required",
                    output=next_task.output,
                    slot_memory=next_task.slot_memory,
                    task_list=[task.model_dump(mode="json") for task in task_list],
                    current_task=next_task.model_dump(mode="json"),
                    graph=task_state.graph,
                )
            )
        elif next_task is not None and next_task.status == "waiting_user_input":
            frames.append(
                AssistantProtocolFrame(
                    ok=True,
                    status="waiting_user_input",
                    intent_code=next_task.intent_code,
                    completion_state=0,
                    completion_reason="router_waiting_user_input",
                    message=_waiting_task_message(next_task),
                    output=next_task.output,
                    slot_memory=next_task.slot_memory,
                    task_list=[task.model_dump(mode="json") for task in task_list],
                    current_task=next_task.model_dump(mode="json"),
                    graph=task_state.graph,
                )
            )

        current_task = next_task if next_task is not None else None
        active_context = (
            _lease_for_next_task(task_state.active_context, current_task)
            if request.completionSignal == 2 and current_task is not None
            else task_state.active_context
        )
        if request.completionSignal == 2 and current_task is None:
            active_context = {}
        updated_task_state = task_state.model_copy(
            update={
                "slot_memory": frames[-1].slot_memory,
                "task_list": task_list,
                "current_task": current_task,
                "active_context": active_context,
                "context_leases": [active_context] if active_context else [],
            },
            deep=True,
        )
        self.sessions.save_task_state(request.sessionId, updated_task_state)
        logger.info(
            "core.sse.frames session_id=%s frame_count=%d frame_statuses=%s frame_reasons=%s",
            request.sessionId,
            len(frames),
            [frame.status for frame in frames],
            [frame.completion_reason for frame in frames],
        )
        logger.info(
            "core.task_state.saved session_id=%s slot_memory=%s current_task=%s task_count=%d active_context=%s",
            request.sessionId,
            _json_for_log(updated_task_state.slot_memory, 1000),
            _task_for_log(updated_task_state.current_task),
            len(updated_task_state.task_list),
            _json_for_log(updated_task_state.active_context, 1000),
        )
        if request.debugTrace and original_context and not updated_task_state.active_context:
            trace_events.append(
                AssistantTraceEvent(
                    stage="context_released",
                    title="任务上下文释放",
                    summary="任务完成回调已结束当前任务，释放 skill/reference lease",
                    data={
                        "session_id": request.sessionId,
                        "task_id": request.taskId,
                        "released_context": original_context,
                    },
                )
            )
            emit_trace(trace_events[-1])
        if request.debugTrace:
            trace_events.append(
                AssistantTraceEvent(
                    stage="assistant_protocol_frames",
                    title="SSE业务帧生成",
                    summary=f"生成 {len(frames)} 个 message frame，最终状态 {frames[-1].status}",
                    data={
                        "frame_count": len(frames),
                        "frames": [frame.protocol_dump() for frame in frames],
                    },
                )
            )
            emit_trace(trace_events[-1])
        return AssistantServiceResult(frames=frames, trace_events=trace_events)


def _recognition_frame(plan: PlannerOutput) -> AssistantProtocolFrame | None:
    intent_code = _effective_intent_code(plan)
    if intent_code is None:
        return None
    return AssistantProtocolFrame(
        ok=True,
        status="running",
        intent_code=intent_code,
        completion_state=0,
        completion_reason="intent_recognized",
        stage="intent_recognition",
        output={},
    )


def _business_frame(plan: PlannerOutput) -> AssistantProtocolFrame:
    task_list = _normalized_task_list(plan)
    current_task = _normalized_current_task(plan, task_list)
    status = _effective_protocol_status(plan, current_task)
    return AssistantProtocolFrame(
        ok=status != "failed",
        status=status,
        intent_code=plan.intent_code,
        completion_state=_completion_state(status),
        completion_reason=plan.completion_reason,
        output=plan.output,
        slot_memory=_effective_slot_memory(plan, current_task),
        message=plan.message or None,
        task_list=[task.model_dump(mode="json") for task in task_list],
        current_task=current_task.model_dump(mode="json") if current_task else None,
        graph=plan.graph,
        actions=plan.actions,
    )


def _apply_plan(task_state: TaskRuntimeState, plan: PlannerOutput) -> TaskRuntimeState:
    task_list = _normalized_task_list(plan) or task_state.task_list
    current_task = _normalized_current_task(plan, task_list) or _first_active_task(task_list)
    slot_memory = dict(task_state.slot_memory)
    slot_memory.update(_effective_slot_memory(plan, current_task))
    active_context = _context_lease(task_state, plan, current_task)
    context_leases = [active_context] if active_context else []
    return task_state.model_copy(
        update={
            "slot_memory": slot_memory,
            "task_list": task_list,
            "current_task": current_task,
            "graph": plan.graph,
            "active_context": active_context,
            "context_leases": context_leases,
        },
        deep=True,
    )


def _context_lease(
    task_state: TaskRuntimeState,
    plan: PlannerOutput,
    current_task: PlannedTask | None,
) -> dict[str, Any]:
    if plan.status in {"completed", "cancelled", "failed"} or current_task is None:
        return {}

    router_context = plan.diagnostics.get("_router_context")
    if not isinstance(router_context, dict):
        router_context = task_state.active_context if isinstance(task_state.active_context, dict) else {}

    skill_names = _string_list(router_context.get("skill_names"))
    reference_ids = _string_list(router_context.get("reference_ids"))
    agent_contexts = _string_list(router_context.get("agent_contexts"))
    metadata_skills = _string_list(router_context.get("metadata_skills"))
    if not skill_names and not reference_ids and not agent_contexts:
        return {}

    return {
        "task_id": current_task.taskId,
        "intent_code": current_task.intent_code,
        "skill_names": skill_names,
        "reference_ids": reference_ids,
        "agent_contexts": agent_contexts,
        "metadata_skills": metadata_skills,
    }


def _first_active_task(task_list: list[PlannedTask]) -> PlannedTask | None:
    for task in task_list:
        if task.status not in {"completed", "cancelled", "failed"}:
            return task
    return None


def _next_active_task_after(
    task_list: list[PlannedTask],
    task_id: str,
) -> PlannedTask | None:
    seen_current = False
    for task in task_list:
        if task.taskId == task_id:
            seen_current = True
            continue
        if seen_current and task.status not in {"completed", "cancelled", "failed"}:
            return task
    return _first_active_task(task_list)


def _waiting_task_message(task: PlannedTask) -> str:
    if task.title:
        return f"请继续补充{task.title}所需信息"
    return "请继续补充当前任务所需信息"


def _lease_for_next_task(
    active_context: dict[str, Any],
    task: PlannedTask,
) -> dict[str, Any]:
    if not active_context:
        return {}
    return {
        "task_id": task.taskId,
        "intent_code": task.intent_code,
        "skill_names": _string_list(active_context.get("skill_names")),
        "reference_ids": _string_list(active_context.get("reference_ids")),
        "agent_contexts": _string_list(active_context.get("agent_contexts")),
        "metadata_skills": _string_list(active_context.get("metadata_skills")),
    }


def _completion_state(status: str) -> int:
    if status == "waiting_assistant_completion":
        return 1
    if status in {"completed", "cancelled", "failed"}:
        return 2
    return 0


def _normalized_task_list(plan: PlannerOutput) -> list[PlannedTask]:
    if plan.current_task is None:
        return plan.task_list
    current_status = _effective_current_task_status(plan)
    return [
        task.model_copy(update={"status": current_status})
        if task.taskId == plan.current_task.taskId
        else task
        for task in plan.task_list
    ]


def _normalized_current_task(
    plan: PlannerOutput,
    task_list: list[PlannedTask],
) -> PlannedTask | None:
    if plan.current_task is None:
        return None
    for task in task_list:
        if task.taskId == plan.current_task.taskId:
            return task
    return plan.current_task.model_copy(update={"status": _effective_current_task_status(plan)})


def _effective_current_task_status(plan: PlannerOutput) -> str:
    if plan.current_task is None:
        return plan.status
    if plan.status == "running" and plan.current_task.status != "running":
        return plan.current_task.status
    if plan.current_task.status == "running" and plan.status != "running":
        return plan.status
    return plan.current_task.status


def _effective_protocol_status(
    plan: PlannerOutput,
    current_task: PlannedTask | None,
) -> str:
    if plan.status == "running" and current_task is not None and current_task.status != "running":
        return current_task.status
    return plan.status


def _effective_slot_memory(
    plan: PlannerOutput,
    current_task: PlannedTask | None,
) -> dict[str, Any]:
    slot_memory = dict(plan.slot_memory)
    if current_task is not None:
        slot_memory.update(current_task.slot_memory)
    return slot_memory


def _effective_intent_code(plan: PlannerOutput) -> str | None:
    if plan.intent_code is not None:
        return plan.intent_code
    if plan.recognition is not None:
        return plan.recognition.intent_code
    return None


def _request_user_binding_id(request: RouterMessageRequest) -> str | None:
    variables = {item.name: item.value for item in request.config_variables}
    for name in ("cust_no", "custNo", "custNO"):
        value = variables.get(name)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _failed_completion_result(
    request: TaskCompletionRequest,
    task_state: TaskRuntimeState,
    trace_events: list[AssistantTraceEvent],
    *,
    completion_reason: str,
    error_code: str,
    message: str,
) -> AssistantServiceResult:
    frame = AssistantProtocolFrame(
        ok=False,
        status="failed",
        completion_state=2,
        completion_reason=completion_reason,
        errorCode=error_code,
        message=message,
        slot_memory=task_state.slot_memory,
        task_list=[task.model_dump(mode="json") for task in task_state.task_list],
        current_task=task_state.current_task.model_dump(mode="json")
        if task_state.current_task
        else None,
        graph=task_state.graph,
    )
    if request.debugTrace:
        trace_events.append(
            AssistantTraceEvent(
                stage="task_completion_rejected",
                title="任务完成回调被拒绝",
                summary=message,
                data={
                    "session_id": request.sessionId,
                    "task_id": request.taskId,
                    "completion_reason": completion_reason,
                    "error_code": error_code,
                },
            )
        )
        emit_trace(trace_events[-1])
    return AssistantServiceResult(frames=[frame], trace_events=trace_events)


def _trace_events_from_plan(plan: PlannerOutput) -> list[AssistantTraceEvent]:
    raw_events = plan.diagnostics.get("_router_trace_events")
    if raw_events is None:
        return []
    if not isinstance(raw_events, list):
        return []
    return [AssistantTraceEvent.model_validate(event) for event in raw_events]


def _diagnostics_for_log(plan: PlannerOutput) -> dict[str, Any]:
    return {
        key: value
        for key, value in plan.diagnostics.items()
        if key != "_router_trace_events"
    }


def _task_for_log(task: PlannedTask | None) -> str:
    if task is None:
        return "null"
    return _json_for_log(task.model_dump(mode="json"), 1200)


def _task_state_trace_data(task_state: TaskRuntimeState) -> dict[str, Any]:
    return {
        "slot_memory": task_state.slot_memory,
        "task_list": [task.model_dump(mode="json") for task in task_state.task_list],
        "current_task": task_state.current_task.model_dump(mode="json")
        if task_state.current_task
        else None,
        "graph": task_state.graph,
        "active_context": task_state.active_context,
        "context_leases": task_state.context_leases,
    }


def _json_for_log(value: Any, limit: int) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        rendered = str(value)
    return _truncate_for_log(rendered, limit)


def _string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _truncate_for_log(value: str, limit: int) -> str:
    value = value.replace("\r", "\\r").replace("\n", "\\n")
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}...[truncated]"
