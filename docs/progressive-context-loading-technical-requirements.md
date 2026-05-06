# 可控分层加载技术需求文档

## 背景

当前服务定位为“意图识别 + spec 驱动 + skill 执行约束”的 router 服务。主要入口是助手协议消息接口，支持 SSE 流式和非流式两种返回方式。服务已经具备基本的 spec 渲染、skill 元数据索引、skill 正文加载、session 内存保存和 debug trace 能力。

新需求要求进一步控制上下文加载范围，避免所有业务知识一次性进入 LLM 上下文，同时让加载过程可以在 SSE trace 中被观察。分层加载需要与 DeepAgents 的 progressive disclosure 能力保持一致：默认上下文小，先用轻量 metadata 完成意图识别，命中后再加载技能正文，技能正文再显式引用更深层资料。

## 目标

1. 建立稳定的三层及以上上下文加载模型。
2. 默认加载根级 agent 指令，作为 router 服务的全局行为约束。
3. 业务意图 skill 通过 name 和 description 进入意图索引集合，意图识别阶段先选出 intent_code 和对应 skill，命中后才加载正文。
4. 第三层及后续资料不进入全局可发现集合，只能由上层 skill 显式声明并按需加载。
5. 任务完成、取消或失败后释放已加载 skill/reference 上下文，只保留必要的业务 session 状态。
6. SSE 中可以看到意图识别、spec 渐进式加载、skill 正文加载、reference 加载和释放事件。
7. 不通过兜底、正则或模糊匹配破坏框架设计。

## 非目标

1. 不把 skill 内部的提槽逻辑搬到 router 服务代码中。
2. 不在 router 服务里硬编码业务意图、槽位或业务文案。
3. 不把所有 Markdown 文件一次性拼进 system prompt。
4. 不依赖正则匹配来识别业务场景或补救 LLM 输出。
5. 不要求必须使用 LangGraph 作为 runtime；是否使用 graph 取决于任务编排需求。
6. 不在 session 中保存大段 Markdown 正文。

## 术语

Layer 1：根级 agent 指令。每次请求默认加载，承载服务身份、协议边界、输出纪律和全局安全约束。

Layer 2：业务意图 skill，也可以理解为意图索引卡片。它具备稳定 name 和面向意图识别的 description，用于在大量候选 skill 中识别用户意图，并给出 intent_code 与对应 skill。命中后加载 skill 正文。

Layer 3 及后续：reference 或更深层资料。它们不具备全局发现价值，不暴露有业务含义的 name/description，不进入全局 skill index。只能由上层 skill 显式声明允许引用，并在需要时加载。

Context lease：一次会话中与当前活跃任务绑定的轻量上下文引用记录。它只保存 skill 名称、reference 标识、所属任务和状态，不保存 Markdown 正文。

## 分层加载规则

### 第一层：agent.md 默认加载

agent.md 是服务级根指令。每次渲染 prompt 时默认加载。

agent.md 应描述：

1. 服务职责：意图识别、spec 驱动、skill 约束、助手协议输出。
2. 加载纪律：默认小上下文、命中后加载、引用按需加载。
3. 输出纪律：只输出目标 schema 允许字段，不复制 schema 辅助说明。
4. session 纪律：等待用户输入时继承当前任务和已收集槽位。
5. 模式纪律：根据 stream 布尔值支持流式和非流式，但业务语义一致。

agent.md 不应包含具体业务意图、槽位定义和业务场景规则。

### 第二层：业务意图 skill 渐进式加载

业务意图 skill 是 router 可发现的意图单元。Layer 2 的核心职责是意图识别，不承担提槽。

业务意图 skill 必须具备：

1. 稳定 name。
2. 面向意图识别的 description。
3. 适用 surface、domain 或 capability 约束。
4. 可以被意图识别阶段返回的稳定 intent_code。
5. 正文中的业务边界、槽位、任务和 handover 规则。

业务意图 skill 的加载过程分两步：

1. Metadata 阶段：只把 name、description 和必要的 intent_code 映射暴露给 LLM，让 LLM 在候选 skill 中完成意图识别。
2. Body 阶段：只有被意图识别选中的 skill，才加载 SKILL.md 正文。

业务意图 skill 的 description 是可发现入口。它应描述用户会怎样表达这个意图、这个意图与相邻意图的边界，以及何时不应该命中。description 不负责提槽，不承载长业务规则，不允许依赖业务代码里的兜底关键词匹配来补救。

目标粒度优先采用一个 Layer 2 skill 对应一个可派发业务意图。如果一个 skill 临时承载多个 intent_code，则必须在 metadata 阶段暴露可区分的意图条目，使识别结果仍然明确包含 intent_code 和 skill_name。长期设计上，应避免让一个大 skill 承担过多意图，否则会降低 100 个以上 skill 场景下的识别精度和上下文效率。

### 第三层及后续：reference 显式加载

reference 是 skill 私有资料，不进入全局可发现集合。

reference 必须满足：

1. 位于所属 skill 的受控目录内。
2. 由上层 skill 显式声明为可引用资料。
3. 通过稳定标识引用，不通过业务含义名称做全局检索。
4. 只在当前 planner 明确需要时加载正文。

reference 适合承载：

1. 长业务规则。
2. 大量槽位定义。
3. 复杂流程说明。
4. 少量场景才会用到的示例。
5. 后续更深层资料的引用说明。

reference 不适合承载全局行为约束。全局约束应放在 agent.md。

## 加载生命周期

一次消息请求的目标生命周期如下：

1. 读取 session 轻量状态。
2. 加载 agent.md。
3. 根据当前 surface、domain、capability 和已有 session 上下文生成 Layer 2 意图索引。
4. 基于 Layer 2 的 name、description 和 intent_code 映射完成意图识别，输出本轮选中的 intent_code 和 skill。
5. 加载被选中业务 skill 正文。
6. 展示该 skill 显式允许的 reference 列表。
7. 如 planner 判断当前任务需要更深层资料，则请求加载指定 reference。
8. harness 校验 reference 是否属于已加载 skill 的允许集合。
9. 加载 reference 正文后重新规划或继续规划。
10. 输出助手协议帧。
11. 保存 session 中必要业务状态和轻量 context lease。
12. 当任务 completed、cancelled 或 failed 时释放 context lease。

## 意图识别旅程示例

以下旅程用于说明 100 个 skill 场景下的目标行为。

假设系统中有 100 个 Layer 2 业务意图 skill，包括转账、查余额、还款、缴费、外汇、理财购买、信用卡账单查询等。每个 skill 都提供稳定 name、intent_code 和面向意图识别的 description。此时服务不会把 100 个 SKILL.md 正文全部加载给 LLM。

用户输入“我要转账”时，处理旅程如下：

1. 服务读取 session。如果这是新会话，session 中没有 active_context。
2. 服务加载 Layer 1 agent.md。agent.md 只提供 router 的全局行为纪律，不包含具体业务意图和槽位规则。
3. 服务生成 Layer 2 意图索引。此时 LLM 只看到 100 个候选 skill 的 name、description 和 intent_code 映射，不看到 skill 正文，也不看到 reference 正文。
4. LLM 基于 name 和 description 做意图识别。它判断“我要转账”命中转账意图，输出 intent_code 为转账对应的业务意图码，并输出对应 skill_name。
5. harness 校验 skill_name 必须来自当前 Available Skills，intent_code 必须来自该 skill 的 metadata 映射。不允许模型 invent 一个不存在的 skill 或泛化意图名。
6. 服务加载被选中 skill 的正文。此时 skill body 才进入 prompt，用于约束 canonical intent、槽位边界、等待用户输入规则、任务状态和 handover 行为。
7. planner 根据已加载 skill body 判断转账必填槽位缺失，输出等待用户输入，并询问收款人和金额。
8. session 保存业务状态和轻量 context lease。lease 只记录当前任务关联的 skill_name、intent_code、reference 标识等，不保存 Markdown 正文。
9. 用户继续输入“小明”。服务发现 session 中有活跃等待任务，直接基于 context lease 重新加载对应 skill body，不再重新在 100 个 skill 中做首轮意图识别。
10. planner 在 skill 规则约束下把“小明”作为当前转账任务的收款人槽位，只追问金额。
11. 用户输入“200”。planner 补齐金额，输出 ready_for_dispatch 和 handover 信息。
12. 任务完成回调进入服务后，服务释放该任务的 context lease。后续新请求重新从 Layer 2 意图索引开始。

这个旅程中，name 和 description 的作用就是意图识别：从大量候选 skill 中选出 intent_code 和对应 skill。提槽发生在 skill body 加载之后，由 skill 正文及其按需 reference 约束完成。

## Session 与上下文释放

session 只保存业务运行状态，不保存 Markdown 正文。

允许保存：

1. session_id。
2. 当前状态和 completion_reason。
3. slot_memory。
4. task_list。
5. current_task。
6. graph。
7. 当前任务绑定的轻量 context lease。

不允许保存：

1. agent.md 正文。
2. SKILL.md 正文。
3. reference 正文。
4. LLM 完整 prompt。

释放规则：

1. 单任务完成、取消或失败后，清理该任务关联的 skill/reference 引用。
2. 多任务中，单个任务完成后只释放该任务独占引用，仍活跃任务的引用继续保留。
3. 整个 session 无活跃任务时，context lease 必须为空。
4. 下一轮用户输入仍处于等待槽位状态时，可以基于轻量 lease 重新加载同一 skill/reference 正文，但正文不从 session 中读取。

## SSE 与日志可观测性

开启 debug trace 后，SSE 应展示核心过程，而不是只依赖服务日志。

必须可观察的阶段：

1. request_received：请求进入服务。
2. session_loaded：读取 session 状态。
3. agent_context_loaded：加载 agent.md。
4. spec_progressive_load：生成 Layer 2 意图索引和已加载 skill 列表。
5. intent_metadata_selected：基于 name 和 description 选出 intent_code 与 skill。
6. skill_body_loaded：加载命中的业务 skill 正文。
7. references_available：展示当前已加载 skill 允许的 reference 标识。
8. reference_body_loaded：加载指定 reference 正文。
9. prompt_loaded：最终 prompt 已构建。
10. llm_raw_response：LLM 原始返回。
11. llm_analysis：结构化 planner 分析结果。
12. intent_recognition：业务意图识别结果。
13. slot_and_skill_result：skill 约束后的提槽或业务结果。
14. context_released：任务结束后的上下文释放。
15. assistant_protocol_frames：助手协议帧生成。

日志中应保留同等阶段的结构化摘要，避免打印大段无用 JSON。大字段只在 debug trace 明确需要时进入 SSE。

## 深层 reference 请求规则

planner 可以请求加载 reference，但必须满足：

1. 请求的 reference 标识已经由当前已加载 skill 显式暴露。
2. 请求数量不超过服务配置上限。
3. 加载深度不超过服务配置上限。
4. reference 路径必须位于所属 skill 目录内。
5. 不允许路径穿越。
6. 未授权 reference 请求应作为 planner 错误或 trace 事件暴露，不做静默兜底。

为了控制成本，一次消息请求中的 reference 加载轮数应有限。超过轮数后，planner 必须在现有上下文内给出可验证输出，或返回失败状态。

## 与 DeepAgents 能力的关系

DeepAgents 的核心能力包括 instruction、skills、filesystem、subagents、summarization 和 LangGraph 编译 runtime。当前服务不需要为了支持分层加载而强制改成 graph runtime。

本服务应吸收 DeepAgents 的 progressive disclosure 思路：

1. 根指令常驻。
2. skill metadata 轻量常驻。
3. skill 正文按需加载。
4. reference 资源由 skill 显式引用后按需读取。
5. 长上下文通过释放和重新加载控制，而不是把全部历史永久塞进 session。

如果后续需要多步骤工具调用、跨 agent 协作、复杂任务图和自动摘要，再评估是否引入 DeepAgents 或 LangGraph runtime。当前阶段的 router 服务可以先保持独立 runtime，但接口和数据结构要为后续兼容留出边界。

## 安全与边界

1. 所有文件加载必须受 spec 和 skill 根目录约束。
2. reference 只能从所属 skill 的允许列表中读取。
3. 不能根据用户文本直接拼接文件路径。
4. 不能用正则兜底业务识别。
5. 不能在 LLM 输出 schema 外追加协议字段。
6. SSE debug trace 可以暴露 prompt 和上下文正文；生产环境应默认关闭或做权限控制。
7. 上下文长度必须有上限，包括 skill 正文、reference 正文、总 reference 数和加载轮数。

## 验收标准

1. 服务启动后可以加载 spec、agent.md 和 skill library。
2. 无 debugTrace 时，SSE 只输出业务 message 和 done。
3. 有 debugTrace 时，SSE 能看到 agent、spec、skill、reference 和 LLM 分析阶段。
4. “我要转账”类请求应先通过 Layer 2 metadata 识别出转账 intent_code 和对应 skill，再加载 skill body，并在缺槽时等待用户输入。
5. 同一 session 的后续槽位输入能继承前序 slot_memory。
6. 任务完成后，session 中不再保留已加载 skill/reference lease。
7. 非流式接口返回最终业务帧，语义与流式最终业务帧一致。
8. reference 未被上层 skill 声明时不能加载。
9. 100 个 Layer 2 skill 的场景下，意图识别阶段只加载 metadata，不加载未命中 skill body。
10. 测试用例覆盖 agent.md 加载、metadata 意图识别、skill 正文加载、reference 授权加载、context lease 保存和释放。
