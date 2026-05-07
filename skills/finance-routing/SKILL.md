---
name: finance-routing
description: "识别并处理转账/汇款意图 AG_TRANS 的补槽与交接规则。"
surfaces: ["scene_selection", "intent_recognition", "slot_extraction", "graph_planning", "task_planning"]
intent_codes: ["AG_TRANS"]
domain_codes: ["finance"]
capabilities: ["routing", "slots", "graph", "planning"]
references: [{"id": "ref_001", "path": "references/ref_001.md", "purpose": "AG_TRANS 槽位与 router_only 交接细则"}]
---

# 转账意图路由规则

本 skill 仅用于转账/汇款业务意图 `AG_TRANS`。

## 意图边界

- 一次转账或汇款诉求是一个业务意图。
- 收款人、金额、账号、银行卡尾号、币种、账期等是槽位，不是独立意图。
- 不要因为一句话里出现多个槽位值，就把一次转账拆成多个意图。
- 只有用户明确表达多个独立业务动作、先后顺序、条件关系或重复办理时，才识别为多意图。
- 如果用户在补槽过程中取消当前转账任务，应将当前节点结束为 `assistant_cancel`，不要继续追问旧槽位。
- 如果用户在补槽过程中切换到新的业务目标，应围绕新目标重新规划，不要把新诉求塞进旧任务槽位。

## 标准业务意图

- 转账、汇款、给某人转钱、我要转账等表达，统一使用 `AG_TRANS`。
- 不要输出 `transfer`、`payment`、中文展示名等非标准 `intent_code`。
- `AG_TRANS` 必填槽位是 `payee_name` 和 `amount`。
- 如果 `AG_TRANS` 缺少任一必填槽位，返回 `status="waiting_user_input"` 和 `completion_reason="router_waiting_user_input"`。
- 缺槽时保留已知槽位到 `slot_memory`，`output` 保持为空，只在 `message` 中询问缺失槽位。
- 用户说“我要转账”且没有收款人和金额时，应识别 `AG_TRANS`，并同时询问收款人和金额。
- 任务标题只是展示文本，不能代替槽位；凡是用户消息中已经明确给出的收款人或金额，都必须写入对应任务的 `slot_memory`。

## 槽位依据

- 只抽取当前消息、明确上下文、推荐任务或允许的历史策略中有依据的值。
- 槽位绑定优先依据槽位语义、标签、别名和来源片段，不要只靠字符串相似度。
- 多个槽位可能共享同一值类型时，必须根据语义判断归属。
- `amount` 保存为不带单位的数字字符串，除非未来 skill 明确引入独立币种槽位。
- 当 session 中存在等待补槽的 `AG_TRANS` 任务时，短人名回复如“小明”“张三”“小红吧”应填入缺失的 `payee_name`。
- 当 session 中存在等待补槽的 `AG_TRANS` 任务时，数字或金额表达如“200”“200元”“两百”应填入缺失的 `amount`。
- 多任务补槽时，用户用“第一笔/第一次/第一个/前一笔”“第二笔/第二次/第二个/后一笔”等顺序表达引用任务，应按 `task_list` 当前顺序绑定到对应 `AG_TRANS` 任务；例如当前有两个等待转账任务，用户说“第一次给100元”表示给 `task_list[0]` 补充 `amount="100"`。
- 如果用户明确用顺序表达指向非当前任务，可以更新被指向任务的 `slot_memory`，但仍保持同一时刻只有一个 `current_task`；被指向任务槽齐后，按串行流程等待交接或继续处理当前应处理任务。
- 等待中的补槽任务优先于首轮识别示例；最新输入填充一个缺失槽位后，不要重启任务，也不要重复询问已有槽位。
- 当前 `AG_TRANS` 缺少 `payee_name` 时，如果最新消息是短的非数字人名表达，应更新 `slot_memory.payee_name`，下一句只询问剩余缺失的 `amount`。
- 当前 `AG_TRANS` 缺少 `amount` 时，如果最新消息是数字或金额表达，应更新 `slot_memory.amount`，必要时只询问剩余缺失的 `payee_name`。
- 必须保留当前任务已收集的槽位，只合并最新消息中有依据的新槽位。
- 填完一个缺失槽位后，如果仍缺少其他必填槽位，继续保持 `waiting_user_input`。
- 在 `router_only` 模式下，如果 `AG_TRANS` 的必填槽位都已齐全，返回 `ready_for_dispatch` 和必需的交接输出。
- 多个 `AG_TRANS` 任务同时出现时，必须按用户表达片段分别归属槽位：如“先给收款人甲转账，再给收款人乙转账”应生成两个任务，第一任务 `slot_memory.payee_name="收款人甲"`，第二任务 `slot_memory.payee_name="收款人乙"`；如果金额缺失，只询问当前任务缺失的金额，不要再询问已明确的收款人。
- 多任务场景的顶层 `slot_memory` 表示当前活跃任务槽位；每个任务自己的槽位必须放在该任务的 `task_list[].slot_memory` 中。

## 任务图规划

- 一个完整转账动作只生成一个任务节点。
- 只有明确重复办理、多个独立目标、顺序关系或条件关系时，才生成多个节点。
- 优先使用 `task_list` 和 `current_task` 表达规划结果。
- 仅当存在依赖、顺序、图确认、取消或重规划语义时，才使用 `graph`。
- 多任务串行推进时，同一时刻只有一个 `current_task`；顶层 `status` 应反映当前任务状态。当前任务缺槽时返回 `waiting_user_input`，后续任务可以保留为 `waiting_user_input`，等待轮到该任务再继续补槽或派发。
- 每个图节点必须暴露标准业务 `intent_code`。
- 不要用 `intent`、`intentCode`、`name` 或展示名代替 `intent_code`。
- 条件阈值应放在边条件中，不要写入节点槽位。
- 意图识别帧、`task_list` 和 graph card 节点必须保持同一执行顺序。
- 如果图编排改变了模型原始识别顺序，应按编排后的图节点顺序输出识别帧。

## Router-Only 上下文

- `recommendTask` 和 `currentDisplay` 只属于 router 的识别与规划上下文。
- 它们可以影响意图识别和任务图规划 prompt。
- 不要把 `recommendTask` 传给下游 agent 的任务输入上下文。
- 不要用 `currentDisplay` 替代下游 agent 的最近消息；agent 上下文应由 router 自己的 session 历史控制。

## 交接语义

- 在 `router_only` 模式下，ready 节点必须返回非空交接输出，且 `ishandover=true`、`handOverReason="router_only_ready_for_dispatch"`。
- 如果业务 agent 返回 `ishandover=true` 但 `output` 为空，不视为成功结果；应将同一任务路由到配置的兜底意图或 agent。
