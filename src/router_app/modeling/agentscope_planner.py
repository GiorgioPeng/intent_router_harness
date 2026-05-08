from __future__ import annotations

import json
import logging
import re
from inspect import isawaitable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from router_app.core.errors import PlannerModelError
from router_app.core.schemas import PlannerResult, SessionState, SkillBody, SkillIndex, TraceEvent, TaskState
from router_app.modeling.base import Planner

logger = logging.getLogger("uvicorn.error")
AGENT_RULES_PATH = Path(__file__).with_name("agent.md")


class AgentScopePlanner(Planner):
    """AgentScope planner backed by an OpenAI-compatible chat model.

    The state machine treats this as advisory output only. All skill/intent/task
    transitions are validated after the model returns.
    """

    def __init__(
        self,
        *,
        model_name: str,
        api_key: str,
        base_http_api_url: str | None = None,
        retry_count: int = 1,
    ) -> None:
        self._model_name = model_name
        self._api_key = api_key
        self._base_http_api_url = base_http_api_url
        self._retry_count = retry_count
        self._model: Any | None = None
        self._formatter: Any | None = None

    async def plan(
        self,
        *,
        user_text: str,
        skill_index: SkillIndex,
        session: SessionState,
        model_session_id: str,
        active_skill_body: SkillBody | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> PlannerResult:
        # 首轮只把 skill metadata 放进 prompt；只有已命中当前任务后，才注入该任务对应的 SKILL body 做补槽。
        prompt = self._build_prompt(
            user_text=user_text,
            skill_index=skill_index,
            session=session,
            model_session_id=model_session_id,
            active_skill_body=active_skill_body,
        )
        logger.info("message.model_input=%s", prompt)
        if trace is not None:
            trace.append(
                TraceEvent(
                    stage="intent.prompt_prepared",
                    detail={
                        "modelSessionId": model_session_id,
                        "skillCount": len(skill_index.skills),
                        "hasActiveTask": session.active_task() is not None,
                        "hasActiveSkillBody": active_skill_body is not None,
                    },
                ),
            )

        last_error: str | None = None
        for attempt in range(self._retry_count + 1):
            content = await self._call_model(prompt, model_session_id=model_session_id, last_error=last_error)
            try:
                # LLM 只提供候选计划，后续仍由 RouterService 做确定性校验和状态推进。
                parsed = PlannerResult.model_validate_json(_extract_json(content))
                if trace is not None:
                    trace.append(
                        TraceEvent(
                            stage="intent.plan_parsed",
                            detail={"action": parsed.action, "intentCount": len(parsed.intents), "attempt": attempt},
                        ),
                    )
                return parsed
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)[:600]
                if trace is not None:
                    trace.append(
                        TraceEvent(
                            stage="intent.plan_parse_failed",
                            detail={"attempt": attempt, "reason": last_error},
                        ),
                    )

        return PlannerResult(
            action="CLARIFY",
            message="我还需要再确认一下你的具体诉求。",
        )

    async def _call_model(self, prompt: str, *, model_session_id: str, last_error: str | None = None) -> str:
        await self._ensure_model()
        from agentscope.message import Msg

        system = f"本次模型调用 sid 是 {model_session_id}。根规则见用户消息中的 agentContext.rootRules。"
        if last_error:
            system += f"\n上次输出未通过校验，错误：{last_error}\n请严格按 schema 重新输出。"
        messages = [
            Msg(name="system", content=system, role="system", invocation_id=model_session_id),
            Msg(name="user", content=prompt, role="user", invocation_id=model_session_id),
        ]
        formatted_result = self._formatter.format(messages)
        formatted = await formatted_result if isawaitable(formatted_result) else formatted_result
        try:
            response_result = self._model(
                formatted,
                extra_headers={"X-Router-Model-Session-Id": model_session_id},
            )
            response = await response_result if isawaitable(response_result) else response_result
        except Exception as exc:
            raise PlannerModelError(_model_error_message(exc)) from exc
        return _response_text(response)

    async def _ensure_model(self) -> None:
        if self._model is not None and self._formatter is not None:
            return
        if not self._api_key:
            raise PlannerModelError("OpenAI-compatible API key is not configured")
        if not self._model_name or self._model_name == "unset-model":
            raise PlannerModelError("OpenAI-compatible model is not configured")
        from agentscope.formatter import OpenAIChatFormatter
        from agentscope.model import OpenAIChatModel

        self._model = OpenAIChatModel(
            model_name=self._model_name,
            api_key=self._api_key,
            stream=False,
            client_kwargs={"base_url": _openai_base_url(self._base_http_api_url)},
        )
        self._formatter = OpenAIChatFormatter()

    def _build_prompt(
        self,
        *,
        user_text: str,
        skill_index: SkillIndex,
        session: SessionState,
        model_session_id: str,
        active_skill_body: SkillBody | None = None,
    ) -> str:
        # 对 LLM 只渐进暴露 AgentScope 风格的一级 SKILL name/description；内部路由字段不放进上下文。
        skills = [
            {
                "name": skill.skill_id,
                "description": skill.summary,
            }
            for skill in skill_index.skills
        ]
        tasks = [_task_summary(task) for task in session.tasks]
        active_skill = _active_skill_context(active_skill_body) if active_skill_body else None
        schema_hint = {
            "action": "CREATE_TASKS | UPDATE_CURRENT_TASK | SWITCH_TASK | CANCEL_TASK | CANCEL_ALL | CLARIFY | NO_INTENT",
            "intents": [
                {
                    "name": "one available skill name",
                    "order": 0,
                    "confidence": 0.0,
                    "extractedSlots": {"slot_name": "value"},
                },
            ],
            "slotUpdates": {"slot_name": "value"},
            "targetTaskId": "task id or null",
            "requestedReferenceKeys": [],
            "interruptCurrentTask": False,
            "message": "optional clarification message",
        }
        return json.dumps(
            {
                "agentContext": {
                    "modelSession": {
                        "sid": model_session_id,
                        "stateless": True,
                        "contextManagedBy": "router_app",
                    },
                    "rootRules": _agent_rules(),
                },
                "availableSkills": skills,
                "session": {
                    "sid": model_session_id,
                    "currentTaskId": session.current_task_id,
                    "tasks": tasks,
                    "conversationHistory": _conversation_history(session),
                },
                "activeSkill": active_skill,
                "userText": user_text,
                "outputSchema": schema_hint,
            },
            ensure_ascii=False,
        )


def _agent_rules() -> str:
    try:
        return AGENT_RULES_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PlannerModelError(f"failed to load agent rules: {AGENT_RULES_PATH}") from exc


def _task_summary(task: TaskState) -> dict[str, Any]:
    return {
        "taskId": task.task_id,
        "name": task.skill_id,
        "status": task.status,
        "todoVisible": task.todo_visible,
        "slots": task.slots,
        "missingSlots": task.missing_slots,
        "interruptedReason": task.interrupted_reason,
    }


def _conversation_history(session: SessionState) -> list[dict[str, Any]]:
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


def _active_skill_context(body: SkillBody) -> dict[str, Any]:
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


def _extract_json(content: str) -> str:
    content = content.strip()
    if content.startswith("{"):
        return content
    # 兼容模型偶发输出解释文本的情况，但仍要求正文里能提取出一个 JSON object。
    match = re.search(r"\{.*\}", content, flags=re.S)
    if not match:
        raise ValueError("model output did not contain a JSON object")
    return match.group(0)


def _response_text(response: Any) -> str:
    content_attr = getattr(response, "content", None)
    if content_attr is not None:
        response = content_attr
    if isinstance(response, str):
        return response
    if isinstance(response, list | tuple):
        texts = [
            str(block["text"])
            for block in response
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block
        ]
        if texts:
            return "\n".join(texts)
    if not isinstance(response, dict):
        return str(response)
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    texts = [
                        str(block["text"])
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text" and "text" in block
                    ]
                    if texts:
                        return "\n".join(texts)
            text = first.get("text")
            if isinstance(text, str):
                return text
    return str(response)


def _chat_completions_url(base_url: str | None) -> str:
    root = (base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    if root.endswith("/chat/completions"):
        return root
    return f"{root}/chat/completions"


def _openai_base_url(base_url: str | None) -> str:
    root = (base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    suffix = "/chat/completions"
    if root.endswith(suffix):
        return root[: -len(suffix)]
    return root


def _model_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    detail = f": {message[:300]}" if message else ""
    return f"AgentScope OpenAI-compatible model call failed ({type(exc).__name__}){detail}"
