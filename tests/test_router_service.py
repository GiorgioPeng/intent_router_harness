from __future__ import annotations

from datetime import timedelta

import pytest

from router_app.config_source.static import StaticConfigSource
from router_app.core.schemas import (
    ExecutionMode,
    IntentPlan,
    HandoffRequest,
    MessageRequest,
    PlannerResult,
    SessionState,
    SkillBody,
    SkillIndex,
    TraceEvent,
    utc_now,
)
from router_app.core.service import RouterService
from router_app.modeling import Planner
from router_app.store import InMemorySessionStore
from tests.conftest import make_service


@pytest.mark.asyncio
async def test_single_intent_collects_missing_slots(service: RouterService) -> None:
    frame = await service.handle_message(
        MessageRequest(sessionId="s1", cust_no="c1", txt="我要转账", debugTrace=True),
    )

    assert frame.status == "collecting_slots"
    assert len(frame.tasks) == 1
    assert frame.tasks[0].intent_code == "transfer"
    assert set(frame.tasks[0].missing_slots) == {"recipient", "amount"}
    assert any(event.stage == "skill.body_loaded" for event in frame.trace or [])


@pytest.mark.asyncio
async def test_multi_intent_serializes_tasks(service: RouterService) -> None:
    frame = await service.handle_message(
        MessageRequest(sessionId="s2", cust_no="c1", txt="转账 200 给小明，再查余额", debugTrace=True),
    )

    assert frame.status == "collecting_slots"
    assert len(frame.tasks) == 2
    assert [task.intent_code for task in frame.tasks] == ["transfer", "balance_query"]
    assert frame.tasks[0].status == "waiting"
    assert frame.tasks[1].status == "waiting"
    assert [item.name for item in frame.todo_list] == ["transfer", "balance_query"]
    assert frame.todo_list[0].current is True
    assert frame.todo_list[1].current is False


@pytest.mark.asyncio
async def test_completion_releases_context_and_schedules_next(settings, skill_index: SkillIndex, skill_bodies) -> None:
    service = make_service(settings, skill_index, skill_bodies, planner=ReadyMultiTaskPlanner())
    first = await service.handle_message(
        MessageRequest(
            sessionId="s3",
            cust_no="c1",
            txt="转账 200 给小明，再查余额",
            executionMode="MOCK_HANDOFF",
            debugTrace=True,
        ),
    )
    task_id = first.tasks[0].task_id

    from router_app.core.schemas import CompletionRequest

    frame = await service.handle_completion(
        CompletionRequest(sessionId="s3", cust_no="c1", taskId=task_id, completionSignal=2, debugTrace=True),
    )

    assert frame.tasks[0].status == "completed"
    assert frame.current_task_id == frame.tasks[1].task_id
    assert frame.tasks[1].status == "waiting"
    assert frame.status == "handoff_ready"
    assert [item.name for item in frame.todo_list] == ["transfer", "balance_query"]
    assert frame.todo_list[1].current is True
    assert any(event.stage == "context.released" for event in frame.trace or [])


@pytest.mark.asyncio
async def test_repeated_completion_is_rejected(settings, skill_index: SkillIndex, skill_bodies) -> None:
    service = make_service(settings, skill_index, skill_bodies, planner=ReadyTransferPlanner())
    first = await service.handle_message(
        MessageRequest(sessionId="s4", cust_no="c1", txt="转账 200 给小明", executionMode="MOCK_HANDOFF", debugTrace=True),
    )
    task_id = first.tasks[0].task_id

    from router_app.core.schemas import CompletionRequest

    await service.handle_completion(
        CompletionRequest(sessionId="s4", cust_no="c1", taskId=task_id, completionSignal=2),
    )
    second = await service.handle_completion(
        CompletionRequest(sessionId="s4", cust_no="c1", taskId=task_id, completionSignal=2),
    )

    assert second.status == "failed"
    assert "不能重复确认" in second.messages[0]


@pytest.mark.asyncio
async def test_session_ttl_expiry_starts_new_session(settings, skill_index: SkillIndex, skill_bodies) -> None:
    store = InMemorySessionStore()
    old = SessionState(sessionId="s5", cust_no="c1")
    old.last_activity_at = utc_now() - timedelta(seconds=settings.session_ttl_seconds + 1)
    await store.save_session(old, ttl_seconds=settings.session_ttl_seconds)
    service = RouterService(
        settings=settings,
        config_source=StaticConfigSource(skill_index, skill_bodies),
        store=store,
        planner=NoIntentPlanner(),
    )

    frame = await service.handle_message(MessageRequest(sessionId="s5", cust_no="c1", txt="你好", debugTrace=True))

    assert frame.status == "clarifying"
    assert frame.tasks == []
    assert any(event.stage == "session.expired_cleaned" for event in frame.trace or [])


@pytest.mark.asyncio
async def test_unauthorized_reference_is_traced(settings, skill_index: SkillIndex, skill_bodies) -> None:
    service = make_service(settings, skill_index, skill_bodies, planner=ReferencePlanner())
    await service.handle_message(MessageRequest(sessionId="s6", cust_no="c1", txt="我要转账", debugTrace=True))

    frame = await service.handle_message(
        MessageRequest(sessionId="s6", cust_no="c1", txt="小明 200", debugTrace=True),
    )

    assert frame.status == "handoff_ready"
    assert any(event.stage == "reference.rejected" for event in frame.trace or [])


@pytest.mark.asyncio
async def test_handoff_prepares_payload_for_ready_task(settings, skill_index: SkillIndex, skill_bodies) -> None:
    service = make_service(settings, skill_index, skill_bodies, planner=ReadyTransferPlanner())
    frame = await service.handle_message(MessageRequest(sessionId="s7", cust_no="c1", txt="给小明转 200", debugTrace=True))
    task_id = frame.tasks[0].task_id

    handoff = await service.handle_handoff(
        HandoffRequest(sessionId="s7", cust_no="c1", taskId=task_id, debugTrace=True),
    )

    assert handoff.status == "waiting"
    assert handoff.accepted is True
    assert handoff.handoff_payload is not None
    assert handoff.handoff_payload["taskId"] == task_id
    assert handoff.handoff_payload["slots"] == {"recipient": "小明", "amount": "200"}
    assert any(event.stage == "handoff.prepared" for event in handoff.trace or [])


@pytest.mark.asyncio
async def test_mock_handoff_dispatch_moves_task_to_doing(settings, skill_index: SkillIndex, skill_bodies) -> None:
    service = make_service(settings, skill_index, skill_bodies, planner=ReadyTransferPlanner())
    frame = await service.handle_message(MessageRequest(sessionId="s7m", cust_no="c1", txt="给小明转 200", debugTrace=True))
    task_id = frame.tasks[0].task_id

    handoff = await service.handle_handoff(
        HandoffRequest(
            sessionId="s7m",
            cust_no="c1",
            taskId=task_id,
            dispatch=True,
            mockDispatch=True,
            debugTrace=True,
        ),
    )

    assert handoff.status == "doing"
    assert handoff.accepted is True
    assert handoff.dispatch_result == {
        "ok": True,
        "mock": True,
        "taskId": task_id,
        "target": "transfer_assistant",
    }
    assert handoff.todo_list[0].status == "doing"
    assert any(event.stage == "handoff.mock_dispatched" for event in handoff.trace or [])


@pytest.mark.asyncio
async def test_completion_uses_follow_up_skill_when_available(settings, skill_index: SkillIndex, skill_bodies) -> None:
    bodies = {
        **skill_bodies,
        ("skill_follow_up", "v1"): SkillBody(
            skillId="skill_follow_up",
            version="v1",
            rulesMd="# 完成后追问\n\n转账已完成，你还想进行什么操作呢？",
        ),
    }
    service = make_service(settings, skill_index, bodies, planner=ReadyTransferPlanner())
    first = await service.handle_message(
        MessageRequest(
            sessionId="s9",
            cust_no="c1",
            txt="给小明转 200",
            executionMode="MOCK_HANDOFF",
            debugTrace=True,
        ),
    )
    task_id = first.tasks[0].task_id

    from router_app.core.schemas import CompletionRequest

    completed = await service.handle_completion(
        CompletionRequest(sessionId="s9", cust_no="c1", taskId=task_id, completionSignal=2, debugTrace=True),
    )

    assert completed.status == "completed"
    assert completed.messages == ["转账已完成，你还想进行什么操作呢？"]
    assert any(event.stage == "follow_up.prepared" for event in completed.trace or [])


@pytest.mark.asyncio
async def test_undeclared_slots_are_rejected_by_skill_contract(settings, skill_index: SkillIndex, skill_bodies) -> None:
    service = make_service(settings, skill_index, skill_bodies, planner=WrongSlotPlanner())
    first = await service.handle_message(MessageRequest(sessionId="s8", cust_no="c1", txt="我要转账", debugTrace=True))

    second = await service.handle_message(MessageRequest(sessionId="s8", cust_no="c1", txt="小明", debugTrace=True))

    assert first.tasks[0].missing_slots == ["recipient"]
    assert "receiver" not in second.tasks[0].slots
    assert second.tasks[0].missing_slots == ["recipient"]
    assert any(event.stage == "slots.rejected" for event in second.trace or [])


@pytest.mark.asyncio
async def test_planner_can_create_task_with_skill_name_only(settings, skill_index: SkillIndex, skill_bodies) -> None:
    service = make_service(settings, skill_index, skill_bodies, planner=NameOnlyPlanner())

    frame = await service.handle_message(MessageRequest(sessionId="s10", cust_no="c1", txt="我要转账"))

    assert frame.status == "collecting_slots"
    assert frame.current_task is not None
    assert frame.current_task.intent_code == "transfer"
    assert frame.current_task.skill_id == "skill_transfer"


@pytest.mark.asyncio
async def test_interrupt_removes_old_task_from_todo_but_keeps_context(settings, skill_index: SkillIndex, skill_bodies) -> None:
    service = make_service(settings, skill_index, skill_bodies, planner=InterruptPlanner())

    first = await service.handle_message(MessageRequest(sessionId="s11", cust_no="c1", txt="我要转账", debugTrace=True))
    second = await service.handle_message(
        MessageRequest(sessionId="s11", cust_no="c1", txt="不转账了，我想查询余额", debugTrace=True),
    )

    assert first.todo_list[0].name == "transfer"
    assert len(second.tasks) == 2
    assert second.tasks[0].intent_code == "transfer"
    assert second.tasks[0].todo_visible is False
    assert second.tasks[0].interrupted_reason == "user_interrupted"
    assert [item.name for item in second.todo_list] == ["balance_query"]
    assert second.current_task is not None
    assert second.current_task.intent_code == "balance_query"
    assert any(event.stage == "task.interrupted" for event in second.trace or [])


@pytest.mark.asyncio
async def test_router_records_random_model_session_mapping(settings, skill_index: SkillIndex, skill_bodies) -> None:
    service = make_service(settings, skill_index, skill_bodies, planner=ReferencePlanner())

    frame = await service.handle_message(MessageRequest(sessionId="s12", cust_no="c1", txt="我要转账", debugTrace=True))

    events = [event for event in frame.trace or [] if event.stage == "model_session.created"]
    ids = [event.detail["modelSessionId"] for event in events]
    assert len(ids) == 2
    assert len(set(ids)) == 2
    assert all(model_session_id.startswith("model_") for model_session_id in ids)
    assert all(event.detail["projectSessionId"] == "s12" for event in events)

    stored = await service._store.load_session(  # noqa: SLF001 - verifies persisted router-owned context.
        "s12",
        now=utc_now(),
        ttl_seconds=settings.session_ttl_seconds,
    )
    assert stored.state is not None
    assert [item.model_session_id for item in stored.state.model_session_mappings] == ids


@pytest.mark.asyncio
async def test_new_request_archives_previous_completed_todos(settings, skill_index: SkillIndex, skill_bodies) -> None:
    service = make_service(settings, skill_index, skill_bodies, planner=TextAwareReadyPlanner())

    first = await service.handle_message(
        MessageRequest(sessionId="s13", cust_no="c1", txt="给小明转 200，然后查余额", executionMode="MOCK_HANDOFF", debugTrace=True),
    )

    from router_app.core.schemas import CompletionRequest

    first_done = await service.handle_completion(
        CompletionRequest(sessionId="s13", cust_no="c1", taskId=first.tasks[0].task_id, completionSignal=2, debugTrace=True),
    )
    await service.handle_handoff(
        HandoffRequest(
            sessionId="s13",
            cust_no="c1",
            taskId=first_done.current_task_id,
            dispatch=True,
            mockDispatch=True,
            debugTrace=True,
        ),
    )
    second_done = await service.handle_completion(
        CompletionRequest(sessionId="s13", cust_no="c1", taskId=first_done.current_task_id, completionSignal=2, debugTrace=True),
    )
    assert [item.name for item in second_done.todo_list] == ["transfer", "balance_query"]
    assert all(item.status == "completed" for item in second_done.todo_list)

    next_round = await service.handle_message(
        MessageRequest(sessionId="s13", cust_no="c1", txt="再给李四转 50，然后查余额", executionMode="MOCK_HANDOFF", debugTrace=True),
    )

    assert len(next_round.tasks) == 4
    assert [task.todo_visible for task in next_round.tasks[:2]] == [False, False]
    assert [item.name for item in next_round.todo_list] == ["transfer", "balance_query"]
    assert next_round.todo_list[0].order == 1
    assert any(event.stage == "todo.archived" for event in next_round.trace or [])


class NoIntentPlanner(Planner):
    async def plan(
        self,
        *,
        user_text: str,
        skill_index: SkillIndex,
        session: SessionState,
        model_session_id: str = "",
        active_skill_body: SkillBody | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> PlannerResult:
        return PlannerResult(action="CLARIFY", message="请说明业务意图。")


class ReferencePlanner(Planner):
    async def plan(
        self,
        *,
        user_text: str,
        skill_index: SkillIndex,
        session: SessionState,
        model_session_id: str = "",
        active_skill_body: SkillBody | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> PlannerResult:
        if not session.tasks:
            return PlannerResult(
                action="CREATE_TASKS",
                intents=[
                    IntentPlan(
                        intentCode="transfer",
                        skillId="skill_transfer",
                        order=0,
                        confidence=1,
                        extractedSlots={},
                    ),
                ],
            )
        return PlannerResult(
            action="UPDATE_CURRENT_TASK",
            slotUpdates={"recipient": "小明", "amount": "200"},
            requestedReferenceKeys=["unauthorized/ref"],
        )


class ReadyTransferPlanner(Planner):
    async def plan(
        self,
        *,
        user_text: str,
        skill_index: SkillIndex,
        session: SessionState,
        model_session_id: str = "",
        active_skill_body: SkillBody | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> PlannerResult:
        return PlannerResult(
            action="CREATE_TASKS",
            intents=[
                IntentPlan(
                    intentCode="transfer",
                    skillId="skill_transfer",
                    order=0,
                    confidence=1,
                    extractedSlots={"recipient": "小明", "amount": "200"},
                ),
            ],
        )


class ReadyMultiTaskPlanner(Planner):
    async def plan(
        self,
        *,
        user_text: str,
        skill_index: SkillIndex,
        session: SessionState,
        model_session_id: str = "",
        active_skill_body: SkillBody | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> PlannerResult:
        return PlannerResult(
            action="CREATE_TASKS",
            intents=[
                IntentPlan(
                    intentCode="transfer",
                    skillId="skill_transfer",
                    order=0,
                    confidence=1,
                    extractedSlots={"recipient": "小明", "amount": "200"},
                ),
                IntentPlan(
                    intentCode="balance_query",
                    skillId="skill_balance",
                    order=1,
                    confidence=1,
                ),
            ],
        )


class WrongSlotPlanner(Planner):
    async def plan(
        self,
        *,
        user_text: str,
        skill_index: SkillIndex,
        session: SessionState,
        model_session_id: str = "",
        active_skill_body: SkillBody | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> PlannerResult:
        if not session.tasks:
            return PlannerResult(
                action="CREATE_TASKS",
                intents=[
                    IntentPlan(
                        intentCode="transfer",
                        skillId="skill_transfer",
                        order=0,
                        confidence=1,
                        extractedSlots={"amount": "200"},
                    ),
                ],
            )
        return PlannerResult(action="UPDATE_CURRENT_TASK", slotUpdates={"receiver": "小明"})


class NameOnlyPlanner(Planner):
    async def plan(
        self,
        *,
        user_text: str,
        skill_index: SkillIndex,
        session: SessionState,
        model_session_id: str = "",
        active_skill_body: SkillBody | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> PlannerResult:
        return PlannerResult.model_validate(
            {
                "action": "CREATE_TASKS",
                "intents": [{"name": "transfer", "order": 0, "confidence": 1.0, "extractedSlots": {}}],
            },
        )


class InterruptPlanner(Planner):
    async def plan(
        self,
        *,
        user_text: str,
        skill_index: SkillIndex,
        session: SessionState,
        model_session_id: str = "",
        active_skill_body: SkillBody | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> PlannerResult:
        if "余额" in user_text:
            return PlannerResult(
                action="CREATE_TASKS",
                interruptCurrentTask=True,
                intents=[
                    IntentPlan(
                        intentCode="balance_query",
                        skillId="skill_balance",
                        order=0,
                        confidence=1,
                    ),
                ],
            )
        return PlannerResult(
            action="CREATE_TASKS",
            intents=[
                IntentPlan(
                    intentCode="transfer",
                    skillId="skill_transfer",
                    order=0,
                    confidence=1,
                ),
            ],
        )


class TextAwareReadyPlanner(Planner):
    async def plan(
        self,
        *,
        user_text: str,
        skill_index: SkillIndex,
        session: SessionState,
        model_session_id: str = "",
        active_skill_body: SkillBody | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> PlannerResult:
        recipient = "李四" if "李四" in user_text else "小明"
        amount = "50" if "50" in user_text else "200"
        intents = [
            IntentPlan(
                intentCode="transfer",
                skillId="skill_transfer",
                order=0,
                confidence=1,
                extractedSlots={"recipient": recipient, "amount": amount},
            ),
        ]
        if "余额" in user_text:
            intents.append(
                IntentPlan(
                    intentCode="balance_query",
                    skillId="skill_balance",
                    order=1,
                    confidence=1,
                ),
            )
        return PlannerResult(action="CREATE_TASKS", intents=intents)
