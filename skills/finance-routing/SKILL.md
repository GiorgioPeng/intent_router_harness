---
name: finance-routing
description: "Finance transfer/remittance routing rules for AG_TRANS slot filling and handoff."
surfaces: ["scene_selection", "intent_recognition", "slot_extraction", "graph_planning", "task_planning"]
intent_codes: ["AG_TRANS"]
domain_codes: ["finance"]
capabilities: ["routing", "slots", "graph", "planning"]
references: [{"id": "ref_001", "path": "references/ref_001.md", "purpose": "Detailed AG_TRANS slot and router-only handoff constraints"}]
---

# Finance Routing

Use this skill when the active candidate is the transfer/remittance intent `AG_TRANS`.

## Intent Boundaries

- A single transfer or remittance action is one business intent.
- Recipient, amount, account number, card suffix, currency, bill period, and similar values are slots.
- Do not split one transfer action into extra intents only because multiple slot values appear.
- Multiple intents require explicit independent actions, sequencing, parallelism, or conditions.
- If the user cancels the active finance action during slot filling, end the active node with `assistant_cancel` instead of continuing to request old slots.
- If the user switches to a new finance goal during slot filling, replan around the new goal instead of stuffing the new utterance into the old slot schema.

## Canonical Finance Intent

- Use `AG_TRANS` for transfer/remittance requests such as "转账", "汇款", "给某人转钱", or "我要转账".
- Do not output generic labels such as `transfer`, `payment`, or Chinese display names as `intent_code`.
- For `AG_TRANS`, required slots are `payee_name` and `amount`.
- If an `AG_TRANS` request is missing any required slot, return `status="waiting_user_input"` and `completion_reason="router_waiting_user_input"`.
- For missing `AG_TRANS` slots, keep known slots in `slot_memory`, keep `output` empty, and ask only for missing required values in `message`.
- For "我要转账" with no payee and no amount, recognize `AG_TRANS` and ask for both收款人 and金额.

## Slot Grounding

- Extract only values supported by the current message, source fragment, recommendation payload, or allowed history policy.
- Prefer registered slot semantics over raw string similarity.
- When several slots share the same value type, bind by label, aliases, semantic definition, and source fragment.
- For `amount`, store the numeric amount as a string without currency units unless a dedicated currency slot exists.
- When session state has an active `AG_TRANS` task waiting for input, treat a short person-name reply such as "小明", "张三", or "小红吧" as `payee_name` if `payee_name` is missing.
- When session state has an active `AG_TRANS` task waiting for input, treat a numeric or money-like reply such as "200", "200元", or "两百" as `amount` if `amount` is missing.
- Active waiting slot filling has priority over first-turn examples. If the latest message fills one missing slot, do not restart the task or ask again for slots that are already filled.
- For active `AG_TRANS` with missing `payee_name`, a short non-numeric person-like reply must update `slot_memory.payee_name` and the next `message` should ask only for `amount`.
- For active `AG_TRANS` with missing `amount`, a numeric or money-like reply must update `slot_memory.amount` and the next `message` should ask only for `payee_name` if that slot is still missing.
- Preserve already collected active-task slots from session state and merge only newly grounded values from the latest message.
- After filling one missing `AG_TRANS` slot, remain `waiting_user_input` if any required slot is still missing.
- If all `AG_TRANS` required slots are present in `router_only`, return `ready_for_dispatch` with the required handover output.

## Graph Planning

- One complete business action should produce one node.
- Use multiple nodes only for explicit repeated actions, independent goals, or structured relations.
- Prefer `task_list` and `current_task` as the primary planning output.
- Use `graph` only when dependencies, ordering, graph confirmation, cancellation, or replan semantics must be represented.
- Each graph node must expose the selected business intent as `intent_code`.
- Do not use `intent`, `intentCode`, `name`, or display labels as a replacement for `intent_code`.
- Condition thresholds belong in edge conditions, not node slot memory.
- Preserve one execution order across the recognition frame, `task_list`, and any graph-card nodes.
- If graph compilation changes the model's raw recognition order, emit the recognition frame in compiled graph-node order.

## Router-Only Context

- `recommendTask` and `currentDisplay` are router-only recognition and planning context.
- They may influence intent recognition and graph planning prompts.
- Do not pass `recommendTask` into downstream agent task input context.
- Do not replace downstream agent recent messages with `currentDisplay`; use router-owned session history for agent context.

## Handover Semantics

- In `router_only`, a ready node should return a non-empty handover output with `ishandover=true` and `handOverReason="router_only_ready_for_dispatch"`.
- A business agent result with `ishandover=true` and an empty `output` is not a successful result; route the same task to the configured fallback intent or agent.
