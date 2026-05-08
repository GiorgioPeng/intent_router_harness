---
name: bill-payment-routing
description: "识别并处理水电费、话费等缴费意图 AG_PAY_BILL 的补槽与交接规则。"
surfaces: ["scene_selection", "intent_recognition", "slot_extraction", "graph_planning", "task_planning"]
intent_codes: ["AG_PAY_BILL"]
domain_codes: ["finance"]
capabilities: ["routing", "slots", "graph", "planning"]
---

# 缴费意图路由规则

本 skill 仅用于缴费业务意图 `AG_PAY_BILL`。

## 意图边界

- 水电费缴费、话费充值、交话费、缴水电费等表达，统一使用 `AG_PAY_BILL`。
- 一个缴费名目加一笔金额构成一个缴费任务。
- 缴费名目和缴费金额是槽位，不是独立意图。
- 不要把缴费识别为转账，也不要输出 `AG_TRANS`。
- 不要输出 `payment`、`bill_payment`、中文展示名等非标准 `intent_code`。

## 标准槽位

- `AG_PAY_BILL` 必填槽位是 `payment_item` 和 `amount`。
- `payment_item` 只能是 `水电费` 或 `话费`。
- 用户表达“水费”“电费”“水费电费”“水电”“水电费”时，归一化为 `payment_item="水电费"`。
- 用户表达“手机费”“电话费”“充话费”“话费充值”时，归一化为 `payment_item="话费"`。
- `amount` 是缴费金额，保存为不带单位的数字字符串。
- 中文金额表达包括小写数字和财务大写数字，也包括“元/圆/块”等常见单位。

## 补槽规则

- 如果两个必填槽位都缺失，应同时询问缴费名目和缴费金额。
- 如果只缺少 `payment_item`，只询问缴费名目，并提示当前支持水电费和话费。
- 如果只缺少 `amount`，只询问缴费金额。
- 当前等待中的 `AG_PAY_BILL` 任务优先于首轮识别示例；最新输入填充缺失槽位后，不要重启任务。
- 补槽时必须整体解析最新消息；如果同一句话同时给出缴费名目和金额，应一次性写入两个槽位。
- 同句补槽示例：“缴水电费200元”“充话费100”“帮我交水费三百元”都应同时写入 `payment_item` 和 `amount`，并在槽位齐全时进入 `ready_for_dispatch`。

## 任务规划

- 多个缴费任务必须串行处理；同一时刻只有一个 `current_task`。
- 用户一次表达多个缴费任务时，按表达顺序生成多个 `AG_PAY_BILL` task，并把每个片段中的 `payment_item` 和 `amount` 写入对应 `task_list[].slot_memory`。
- 顶层 `slot_memory` 表示当前活跃任务槽位；每个任务自己的槽位必须放在该任务的 `task_list[].slot_memory` 中。
- 在 `router_only` 模式下，如果 `AG_PAY_BILL` 的必填槽位都齐全，返回 `ready_for_dispatch`。
