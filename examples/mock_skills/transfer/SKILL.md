---
name: transfer
description: 办理转账汇款，需要收款人和金额。
---
# 转账规则

本 SKILL 只负责识别和补全转账任务。转账任务需要两个必填槽位：

- recipient：收款人、收款方、接收方。
- amount：转账金额。

## 槽位提取规则

- 只能输出 slotContract 中声明的槽位名：recipient、amount。
- 用户表达收款人、接收方、给某人、转给某人、转到某人账户时，统一映射为 recipient。
- 用户表达金额、转账数额、数字加元、数字加块、人民币金额时，统一映射为 amount。
- “给 X 转 Y 元”“转 Y 元给 X”“向 X 转账 Y”“帮我转 Y 到 X”这类表达必须同时提取 recipient 和 amount。
- 多意图句子中，只提取与转账有关的片段；例如“然后查余额”属于其他任务，不要写入 transfer 的槽位。
- 不要输出 receiver、payee、name 等未声明槽位。
- 如果用户只说“转 200 元”，只输出 amount，不要猜 recipient。
- 如果用户只说“给小明转账”，只输出 recipient，不要猜 amount。

## 槽位归一化

- recipient 保留用户说出的姓名或收款方文本，去掉“给”“转给”“收款人是”等引导词。
- amount 保留金额数字或简短金额文本，去掉“转”“转账”“金额是”等动作词；可以去掉“元”“块”“人民币”等币种单位。

## 示例

用户：“给小明转 200 元”
输出 slotUpdates：`{"recipient":"小明","amount":"200"}`

用户：“给小明转 200 元，然后查一下余额”
输出 slotUpdates：`{"recipient":"小明","amount":"200"}`

用户：“转200给小明”
输出 slotUpdates：`{"recipient":"小明","amount":"200"}`

用户：“向张三转账 500”
输出 slotUpdates：`{"recipient":"张三","amount":"500"}`

用户：“转 200 元”
输出 slotUpdates：`{"amount":"200"}`

用户：“给小明转账”
输出 slotUpdates：`{"recipient":"小明"}`

## References 读取时机

- 首轮全局意图识别只读取本文件 frontmatter 中的 name 和 description，不读取 body 或 references。
- 命中 transfer 且进入当前任务后，Router 读取本文件 body 和 `references/slot_contract.json` 用于补槽。
- 只有模型请求且 skill metadata 允许时，才读取 `references/limits.md` 等普通参考资料。
- 准备交接子智能体时，Router 读取 `references/handoff_contract.json` 生成 handoff payload。
