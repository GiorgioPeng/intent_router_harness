---
name: follow_up
description: 在一个业务任务完成后，向用户追问下一步还想办理什么。
---
# 完成后追问

任务已完成，你还想进行什么操作呢？是想查余额？还是再转一笔？

## References 读取时机

- 本 SKILL 不参与首轮意图识别。
- 当一个业务任务最终完成且没有后续等待任务时，Router 按 skillId 加载本 SKILL body，生成完成后追问。
