from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from pydantic import ValidationError

from intent_router_harness.contracts import (
    AssistantTraceEvent,
    PlannerOutput,
    RouterMessageRequest,
    SessionState,
)
from intent_router_harness.llm import LLMClient, LLMRequestError
from intent_router_harness.runtime import PromptHarness
from intent_router_harness.trace import emit_trace

logger = logging.getLogger(__name__)

ASSISTANT_STATUS_VALUES = [
    "running",
    "waiting_user_input",
    "ready_for_dispatch",
    "waiting_assistant_completion",
    "completed",
    "cancelled",
    "failed",
]


class PlannerError(RuntimeError):
    """Raised when planner output cannot be produced or validated."""


class MessagePlanner(Protocol):
    """Planner boundary for message requests."""

    def plan_message(
        self,
        request: RouterMessageRequest,
        session: SessionState,
    ) -> PlannerOutput:
        """Return a structured planner output."""


class LLMMessagePlanner:
    """Spec-driven planner that renders a harness surface and calls an LLM."""

    def __init__(
        self,
        *,
        harness: PromptHarness,
        llm_client: LLMClient,
        surface: str = "task_planning",
        scene_surface: str = "scene_selection",
        max_tokens: int = 1200,
    ) -> None:
        self.harness = harness
        self.llm_client = llm_client
        self.surface = surface
        self.scene_surface = scene_surface
        self.max_tokens = max_tokens

    def plan_message(
        self,
        request: RouterMessageRequest,
        session: SessionState,
    ) -> PlannerOutput:
        """Render task-planning prompt and validate the LLM JSON output."""
        include_trace = request.debugTrace
        logger.info(
            "llm.plan.start session_id=%s execution_mode=%s text=%s session_status=%s session_slot_memory=%s current_task=%s",
            request.sessionId,
            request.executionMode,
            _truncate_for_log(request.txt, 300),
            session.status,
            session.slot_memory,
            session.current_task.model_dump(mode="json") if session.current_task else None,
        )
        active_context = session.active_context if isinstance(session.active_context, dict) else {}
        loaded_skill_names = tuple(_string_list(active_context.get("skill_names")))
        requested_reference_ids = tuple(_string_list(active_context.get("reference_ids")))
        variables = {
            "message": request.txt,
            "execution_mode": request.executionMode,
            "session_state_json": session.model_dump_json(exclude_none=True),
            "recommend_task_json": json.dumps(request.recommendTask, ensure_ascii=False),
            "recent_messages_json": json.dumps(request.currentDisplay, ensure_ascii=False),
            "config_variables_json": json.dumps(
                [item.model_dump(mode="json") for item in request.config_variables],
                ensure_ascii=False,
            ),
            "planner_output_schema_json": _planner_output_schema_json(),
        }
        trace_events: list[dict[str, Any]] = []
        if not loaded_skill_names:
            loaded_skill_names = self._select_scene_skills(
                request=request,
                variables=variables,
                include_trace=include_trace,
                trace_events=trace_events,
            )
        prompt = self._render_prompt(
            request=request,
            variables=variables,
            loaded_skill_names=loaded_skill_names,
            requested_reference_ids=requested_reference_ids,
        )
        _record_prompt_trace(
            request=request,
            prompt=prompt,
            include_trace=include_trace,
            trace_events=trace_events,
            title="最终提示词加载",
        )
        raw_response, content = self._call_llm(request, prompt)
        _record_raw_response_trace(
            request=request,
            raw_response=raw_response,
            content=content,
            include_trace=include_trace,
            trace_events=trace_events,
        )
        payload, plan = _parse_plan_payload(request, content)

        if plan.requested_references:
            requested_reference_ids = tuple(
                _merge_strings((*requested_reference_ids, *tuple(plan.requested_references)))
            )
            logger.info(
                "llm.plan.reference_request session_id=%s requested_references=%s",
                request.sessionId,
                list(requested_reference_ids),
            )
            if include_trace:
                event = AssistantTraceEvent(
                    stage="reference_request_received",
                    title="LLM请求加载Reference",
                    summary=f"requested_references={list(requested_reference_ids)}",
                    data={
                        "requested_references": list(requested_reference_ids),
                        "first_pass_completion_reason": plan.completion_reason,
                    },
                )
                trace_events.append(event.model_dump(mode="json"))
                emit_trace(event)
            prompt = self._render_prompt(
                request=request,
                variables=variables,
                loaded_skill_names=tuple(prompt.loaded_skills),
                requested_reference_ids=requested_reference_ids,
            )
            _record_prompt_trace(
                request=request,
                prompt=prompt,
                include_trace=include_trace,
                trace_events=trace_events,
                title="Reference补充后提示词加载",
            )
            raw_response, content = self._call_llm(request, prompt)
            _record_raw_response_trace(
                request=request,
                raw_response=raw_response,
                content=content,
                include_trace=include_trace,
                trace_events=trace_events,
            )
            payload, plan = _parse_plan_payload(request, content)

        logger.info(
            "llm.plan.validated session_id=%s mode=%s status=%s intent_code=%s completion_reason=%s slot_memory=%s task_count=%d output=%s",
            request.sessionId,
            plan.mode,
            plan.status,
            plan.intent_code,
            plan.completion_reason,
            plan.slot_memory,
            len(plan.task_list),
            plan.output,
        )
        logger.info(
            "core.trace step=llm_analysis session_id=%s intent_code=%s mode=%s status=%s completion_reason=%s slot_memory=%s current_task=%s message=%s loaded_skills=%s loaded_references=%s",
            request.sessionId,
            _effective_intent_code(plan),
            plan.mode,
            plan.status,
            plan.completion_reason,
            plan.slot_memory,
            _task_for_log(plan.current_task),
            _truncate_for_log(plan.message, 500),
            list(prompt.loaded_skills),
            list(prompt.loaded_references),
        )
        diagnostics = dict(plan.diagnostics)
        diagnostics["_router_context"] = {
            "agent_contexts": list(prompt.agent_contexts),
            "metadata_skills": list(prompt.metadata_skills),
            "skill_names": list(prompt.loaded_skills),
            "reference_ids": list(prompt.loaded_references),
        }
        if include_trace:
            event = AssistantTraceEvent(
                stage="llm_analysis",
                title="LLM结构化分析",
                summary=(
                    f"intent_code={_effective_intent_code(plan)}，"
                    f"status={plan.status}，reason={plan.completion_reason}"
                ),
                data={
                    "mode": plan.mode,
                    "status": plan.status,
                    "completion_reason": plan.completion_reason,
                    "intent_code": _effective_intent_code(plan),
                    "slot_memory": plan.slot_memory,
                    "task_list": [task.model_dump(mode="json") for task in plan.task_list],
                    "current_task": plan.current_task.model_dump(mode="json")
                    if plan.current_task
                    else None,
                    "message": plan.message,
                    "output": plan.output,
                    "parsed_json": payload,
                    "router_context": diagnostics["_router_context"],
                },
            )
            trace_events.append(event.model_dump(mode="json"))
            emit_trace(event)
            diagnostics["_router_trace_events"] = trace_events
        plan = plan.model_copy(update={"diagnostics": diagnostics}, deep=True)
        return plan

    def _select_scene_skills(
        self,
        *,
        request: RouterMessageRequest,
        variables: dict[str, Any],
        include_trace: bool,
        trace_events: list[dict[str, Any]],
    ) -> tuple[str, ...]:
        if self.scene_surface not in self.harness.spec.surfaces:
            return ()

        try:
            prompt = self.harness.render(
                surface=self.scene_surface,
                variables=variables,
                domain_codes=("finance",),
                capabilities=("routing", "slots", "planning"),
            )
        except (KeyError, ValueError) as exc:
            raise PlannerError(f"scene selection surface failed: {self.scene_surface}: {exc}") from exc

        _record_prompt_trace(
            request=request,
            prompt=prompt,
            include_trace=include_trace,
            trace_events=trace_events,
            title="场景Skill选择提示词加载",
        )
        raw_response, content = self._call_llm(request, prompt)
        _record_raw_response_trace(
            request=request,
            raw_response=raw_response,
            content=content,
            include_trace=include_trace,
            trace_events=trace_events,
        )
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise PlannerError(f"scene selection output is not JSON: {exc}") from exc

        selected = _merge_strings(tuple(_string_list(payload.get("skill_names"))))
        allowed = set(prompt.metadata_skills)
        invalid = [name for name in selected if name not in allowed]
        if invalid:
            raise PlannerError(
                f"scene selection returned unknown skills: {invalid}; allowed={sorted(allowed)}"
            )
        logger.info(
            "llm.scene_selection.result session_id=%s selected_skills=%s available_skills=%s reason=%s",
            request.sessionId,
            selected,
            list(prompt.metadata_skills),
            _truncate_for_log(str(payload.get("reason") or ""), 500),
        )
        if include_trace:
            event = AssistantTraceEvent(
                stage="scene_skill_selected",
                title="业务场景Skill选择",
                summary=f"selected_skills={selected}",
                data={
                    "selected_skills": selected,
                    "available_skills": list(prompt.metadata_skills),
                    "reason": payload.get("reason"),
                },
            )
            trace_events.append(event.model_dump(mode="json"))
            emit_trace(event)
        return tuple(selected)

    def _render_prompt(
        self,
        *,
        request: RouterMessageRequest,
        variables: dict[str, Any],
        loaded_skill_names: tuple[str, ...],
        requested_reference_ids: tuple[str, ...],
    ):
        try:
            return self.harness.render(
                surface=self.surface,
                variables=variables,
                domain_codes=("finance",),
                capabilities=("routing", "slots", "planning"),
                loaded_skill_names=loaded_skill_names,
                requested_reference_ids=requested_reference_ids,
            )
        except (KeyError, ValueError) as exc:
            raise PlannerError(f"planning surface is not configured or reference loading failed: {self.surface}: {exc}") from exc

    def _call_llm(self, request: RouterMessageRequest, prompt):
        logger.debug(
            "llm.plan.prompt_system session_id=%s content=%s",
            request.sessionId,
            _truncate_for_log(prompt.system, 12000),
        )
        logger.debug(
            "llm.plan.prompt_human session_id=%s content=%s",
            request.sessionId,
            _truncate_for_log(prompt.human, 8000),
        )
        try:
            raw_response = self.llm_client.chat(prompt.messages(), max_tokens=self.max_tokens)
            content = str(raw_response["choices"][0]["message"]["content"]).strip()
            return raw_response, content
        except (KeyError, IndexError, TypeError, LLMRequestError) as exc:
            raise PlannerError(f"LLM planner request failed: {exc}") from exc


def _planner_output_schema_json() -> str:
    schema = {
        "required": ["mode", "status", "completion_reason"],
        "status_values": ASSISTANT_STATUS_VALUES,
        "rules": [
            "Use only status_values for PlannerOutput.status, every task_list item status, and current_task.status.",
            "Never output pending, queued, todo, incomplete, or input_required as a status.",
            "Do not copy schema helper keys such as enum, required, fields, rules, description, or examples into the output.",
            "If a required slot is missing, use status=waiting_user_input and completion_reason=router_waiting_user_input.",
            "If all required slots are complete in router_only mode, use status=ready_for_dispatch and completion_reason=router_ready_for_dispatch.",
            "Use canonical intent_code values from the loaded skills; do not invent display labels.",
            "When session state has an active waiting task, interpret short replies as slot values for that task and preserve existing slot_memory.",
            "If a loaded skill exposes Available References and more context is required, set requested_references to allowed reference ids, status=running, and completion_reason=router_reference_required.",
            "Never request a reference id that is not listed in Available References.",
        ],
        "fields": {
            "mode": "single_task | multi_task | slot_filling | cancel | replan | failed",
            "status": {"enum": ASSISTANT_STATUS_VALUES},
            "completion_state": "0 for in progress, 1 for assistant confirmation required, 2 for terminal",
            "completion_reason": "stable machine-readable reason",
            "intent_code": "selected business intent code, if any",
            "recognition": {
                "intent_code": "selected business intent code",
            },
            "slot_memory": "object with stable slot keys",
            "task_list": [
                {
                    "taskId": "stable task id",
                    "intent_code": "business intent code",
                    "status": {"enum": ASSISTANT_STATUS_VALUES},
                    "title": "short display title",
                    "slot_memory": "object",
                    "output": "object",
                }
            ],
            "current_task": {
                "taskId": "same taskId as the active task_list item",
                "intent_code": "same intent_code as the active task_list item",
                "status": {"enum": ASSISTANT_STATUS_VALUES},
                "title": "same title as the active task_list item",
                "slot_memory": "object",
                "output": "object",
            },
            "graph": "null or explicit multi-task dependency graph",
            "actions": "optional graph/action-flow actions",
            "requested_references": "optional list of allowed reference ids to load before final planning",
            "message": "user-facing message",
            "output": "protocol output object; never include slot_memory inside output",
            "diagnostics": "debug object",
        },
        "examples": {
            "missing_transfer_slots": {
                "mode": "slot_filling",
                "status": "waiting_user_input",
                "completion_state": 0,
                "completion_reason": "router_waiting_user_input",
                "intent_code": "AG_TRANS",
                "recognition": {
                    "intent_code": "AG_TRANS",
                },
                "slot_memory": {},
                "task_list": [
                    {
                        "taskId": "task_001",
                        "intent_code": "AG_TRANS",
                        "status": "waiting_user_input",
                        "title": "转账",
                        "slot_memory": {},
                        "output": {},
                    }
                ],
                "current_task": {
                    "taskId": "task_001",
                    "intent_code": "AG_TRANS",
                    "status": "waiting_user_input",
                    "title": "转账",
                    "slot_memory": {},
                    "output": {},
                },
                "message": "请提供收款人和转账金额",
                "output": {},
            },
            "active_transfer_payee_reply": {
                "mode": "slot_filling",
                "status": "waiting_user_input",
                "completion_state": 0,
                "completion_reason": "router_waiting_user_input",
                "intent_code": "AG_TRANS",
                "recognition": {
                    "intent_code": "AG_TRANS",
                },
                "slot_memory": {"payee_name": "小明"},
                "task_list": [
                    {
                        "taskId": "task_001",
                        "intent_code": "AG_TRANS",
                        "status": "waiting_user_input",
                        "title": "转账",
                        "slot_memory": {"payee_name": "小明"},
                        "output": {},
                    }
                ],
                "current_task": {
                    "taskId": "task_001",
                    "intent_code": "AG_TRANS",
                    "status": "waiting_user_input",
                    "title": "转账",
                    "slot_memory": {"payee_name": "小明"},
                    "output": {},
                },
                "message": "请提供转账金额",
                "output": {},
            }
        },
    }
    return json.dumps(schema, ensure_ascii=False)


def _record_prompt_trace(
    *,
    request: RouterMessageRequest,
    prompt: Any,
    include_trace: bool,
    trace_events: list[dict[str, Any]],
    title: str,
) -> None:
    logger.info(
        "llm.plan.prompt_rendered session_id=%s surface=%s agent_contexts=%s metadata_skills=%s loaded_skills=%s loaded_references=%s system_chars=%d human_chars=%d",
        request.sessionId,
        prompt.surface,
        list(prompt.agent_contexts),
        list(prompt.metadata_skills),
        list(prompt.loaded_skills),
        list(prompt.loaded_references),
        len(prompt.system),
        len(prompt.human),
    )
    logger.info(
        "core.trace step=prompt_loaded session_id=%s surface=%s system_contains=surface_rules+agent_context+spec_context+loaded_skill_bodies+loaded_references human_contains=user_message+session_state+output_schema loaded_skills=%s loaded_references=%s system_chars=%d human_chars=%d",
        request.sessionId,
        prompt.surface,
        list(prompt.loaded_skills),
        list(prompt.loaded_references),
        len(prompt.system),
        len(prompt.human),
    )
    if not include_trace:
        return

    for raw_event in prompt.trace_events:
        event = AssistantTraceEvent.model_validate(raw_event)
        trace_events.append(event.model_dump(mode="json"))
        emit_trace(event)
    event = AssistantTraceEvent(
        stage="prompt_loaded",
        title=title,
        summary=(
            "system prompt 包含 surface 规则、agent 根指令、spec 上下文、"
            "已加载 skill 和已加载 reference；human prompt 包含用户消息、session 状态和输出 schema"
        ),
        data={
            "surface": prompt.surface,
            "agent_contexts": list(prompt.agent_contexts),
            "metadata_skills": list(prompt.metadata_skills),
            "loaded_skills": list(prompt.loaded_skills),
            "loaded_references": list(prompt.loaded_references),
            "system_chars": len(prompt.system),
            "human_chars": len(prompt.human),
            "system_prompt": prompt.system,
            "human_prompt": prompt.human,
        },
    )
    trace_events.append(event.model_dump(mode="json"))
    emit_trace(event)


def _record_raw_response_trace(
    *,
    request: RouterMessageRequest,
    raw_response: dict[str, Any],
    content: str,
    include_trace: bool,
    trace_events: list[dict[str, Any]],
) -> None:
    logger.info(
        "llm.plan.raw_response session_id=%s model=%s finish_reason=%s usage=%s content=%s",
        request.sessionId,
        raw_response.get("model"),
        _finish_reason(raw_response),
        raw_response.get("usage"),
        _truncate_for_log(content, 4000),
    )
    if not include_trace:
        return

    event = AssistantTraceEvent(
        stage="llm_raw_response",
        title="LLM原始分析结果",
        summary=f"model={raw_response.get('model')}，finish_reason={_finish_reason(raw_response)}",
        data={
            "model": raw_response.get("model"),
            "finish_reason": _finish_reason(raw_response),
            "usage": raw_response.get("usage"),
            "content": content,
        },
    )
    trace_events.append(event.model_dump(mode="json"))
    emit_trace(event)


def _parse_plan_payload(
    request: RouterMessageRequest,
    content: str,
) -> tuple[dict[str, Any], PlannerOutput]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise PlannerError(f"LLM planner output is not JSON: {exc}") from exc

    logger.info(
        "llm.plan.parsed_json session_id=%s payload=%s",
        request.sessionId,
        _truncate_for_log(json.dumps(payload, ensure_ascii=False), 4000),
    )
    try:
        return payload, PlannerOutput.model_validate(payload)
    except ValidationError as exc:
        raise PlannerError(f"LLM planner output failed schema validation: {exc}") from exc


def _finish_reason(raw_response: dict) -> str | None:
    try:
        return raw_response["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def _effective_intent_code(plan: PlannerOutput) -> str | None:
    if plan.intent_code is not None:
        return plan.intent_code
    if plan.recognition is not None:
        return plan.recognition.intent_code
    return None


def _task_for_log(task: object | None) -> str:
    if task is None:
        return "null"
    if hasattr(task, "model_dump"):
        return _truncate_for_log(json.dumps(task.model_dump(mode="json"), ensure_ascii=False), 1200)
    return _truncate_for_log(str(task), 1200)


def _string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _merge_strings(values: tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _truncate_for_log(value: str, limit: int) -> str:
    value = value.replace("\r", "\\r").replace("\n", "\\n")
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}...[truncated]"
