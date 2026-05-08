---
name: balance_query
description: 查询账户余额。
---
# 查余额规则

无必填槽位。

## References 读取时机

- 首轮全局意图识别只读取本文件 frontmatter 中的 name 和 description。
- 命中 balance_query 且进入当前任务后，Router 读取本文件 body 和 `references/slot_contract.json` 判断是否需要补槽。
- 准备交接子智能体时，Router 读取 `references/handoff_contract.json` 生成 handoff payload。
