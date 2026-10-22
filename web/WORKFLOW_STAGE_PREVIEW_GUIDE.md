# Workflow Stage Preview 显示与维护规则

`/workflow-stage-preview/` 的用途不是展示 TheoryMaster 的全部内部状态，而是给人审查 **Agent 真正看到了什么、必须返回什么，以及少量真正控制流程的 SDK 状态**。

## 1. 图上必须显示什么

### A. Agent receives / Context

必须逐字段显示实际序列化进 Agent Prompt 的输入字段。

- 来源：`tool/theory_master/theory_master/prompt_stages.py::STAGE_CONTEXT_FIELDS`，并与 `stage_context()` 保持一致。
- 这里只允许出现 Agent 实际收到的字段。
- 条件字段仍然列出；运行时没有值时可以省略。
- `mode`、`phase`、round counter、router bookkeeping、rework counter 等如果只由 SDK 用于选择 Stage / 路由 / 恢复，就不能列在 Agent Input 里。
- Agent 已经通过 `Current task: <stage>`、Stage Card 和 Procedure Card 知道自己处在哪个 Stage，因此 SDK 不应再把纯 Stage 标签重复塞进 Context。

### B. Agent must return

必须显示当前 Stage 的正式输出 contract 字段。

- 来源：`PHASE_CONTRACTS`。
- 显示 Agent 被要求直接生成的字段，而不是 SDK 后处理后才产生的字段。
- `◌` 表示 optional / 条件输出。
- `☑` 表示确定性选择字段：Agent/SDK 不是自由写任意值，而是从固定枚举或运行时有限候选集中选择。
- 固定枚举字段的弹窗必须列出完整允许值及每个值的含义；允许值优先从当前 `PHASE_CONTRACTS` / 同源 contract note 自动解析。
- 运行时动态选择字段（例如 `selected_candidate_id`）同样标 `☑`，但弹窗显示“候选来源”（如 `qualified_candidates[].id`），禁止为了展示而伪造固定候选值。
- 没有 `☑` 的正式输出字段按自由回答字段理解；`◌` 与 `☑` 可同时存在，分别表示“可省略”和“取值受限”。

### C. ◆ SDK critical controls

允许显示少量 **不发送给 Agent、但会显著改变流程推进或科学权威边界** 的 SDK 控制项。

这些行必须统一带 `◆` 标记，明确它们不是 Agent Input / Output。只保留以下类型：

- Stage / next-executor / qualification / rework / terminal routing；
- stable identity 分配与合法性校验，如 `candidate_id`、`hypothesis_id`、`objection_id`；
- Hypothesis Lock、冻结边界、repair scope enforcement；
- Evidence 注册、adjudicability / criterion-objection binding 中会改变 Judge 可见证据的关键步骤；
- rework authorization / budget gate；
- Scientific Model 原子提交、关键版本推进；
- SQLite Hypothesis 身份、H→H 谱系、Cycle Outcome 归属与 Evidence refs 等关键完整性约束；Canvas 仅是由 SQLite 生成的可视化投影。

## 2. 默认不要显示什么

以下内容即使 SDK 内部存在，也默认不进入这张图：

- 仅用于日志、观测、恢复、缓存、调用账本、token 统计的字段；
- 纯 workflow counter、内部 `mode` / `phase`、重复的 Stage 名称；
- 可以从已显示对象确定性推出的副本；
- 不改变 Agent 决策，也不改变流程路由/authority 的普通 helper 字段；
- transport、文件物化、布局等实现细节，除非失败会直接改变该 Stage 的科学/流程结果。

如果某个非 Agent 字段确实值得显示，必须放在 `◆ SDK critical controls` 下，并说明它影响的是 routing、authority、identity、evidence admissibility、budget 还是 atomic commit。

## 3. 维护原则

1. 修改 Agent Prompt Context 时，先改 `stage_context()` 与 `STAGE_CONTEXT_FIELDS`；网页从同源声明读取，不再手抄第二份 Input 清单。
2. 修改 Agent 输出 contract 时，改 `PHASE_CONTRACTS`；网页直接解析 contract。
3. 只有经过上述“关键 SDK 控制”判据的非 Agent 状态，才允许加入 `SDK_OWNERSHIP_FIELDS`。
4. 每次修改 Stage 输入/输出或关键 routing/authority，都必须检查 `/workflow-stage-preview/` 的对应节点。
5. 回归测试必须阻止网页重新出现已经移出 Agent Prompt 的 routing/bookkeeping 字段。

## 4. Workflow 历史版本

Workflow Stage Preview 同时保留 `Current · live` 和冻结历史版本。

- `Current · live` 继续直接读取当前 TheoryMaster 的 `STAGE_CONTEXT_FIELDS`、`PHASE_CONTRACTS` 与 Stage Card 元数据。
- 每次结构性调整前，应把上一版完整 Workflow 保存为源码内的只读历史快照；历史快照至少冻结 Mermaid、字段元数据和 Stage Card 信息，因此不会随着当前源码继续漂移。
- 历史版本只用于架构审计与比较：字段备注不可在历史版本上编辑，当前 Change Request 也不会投影红框到历史图上。
- 当前字段的伴随式弹窗应显示复杂 Context 字段真实进入 Prompt 后的内部结构；单击 Stage 名则显示完整 Stage Card、Procedure 与重要 Contract 条件规则，而不是把这些长文本直接塞进 Mermaid。

## 5. 字段说明编辑

图中 `Agent receives / Context`、`Agent must return` 和 `◆ SDK critical controls` 下的每一个字段都是可维护对象。

- 单击字段名会在该字段旁边打开一个轻量伴随式备注弹窗；点击其它位置即消失，键盘聚焦后按 Enter/Space 也可以打开。
- 系统为每个当前可见字段提供一条简短中文默认说明；默认说明只用于帮助人工审查，不改变 TheoryMaster runtime contract。
- 人工编辑后的说明保存到 Observatory 自己的 state 目录，按 `role + stage + section + field` 稳定绑定；不会写回 TheoryMaster 源码或科学状态。
- 每个字段保留完整说明 revision 历史；人工保存标记为“人工修改”，代码默认说明新增或变化标记为“机器自动更新”，每条都记录时间。
- 不提供“恢复默认说明”按钮；新的机器默认只进入历史，不会静默覆盖已经存在的人工说明。
- 服务器只允许编辑当前 Workflow 中真实存在、正在显示的字段；任意伪造字段 key 必须 fail closed。
- 字段弹窗除说明/历史外还要显示字段类型。确定性字段显示 `☑`、选择模式（固定枚举/运行时有限候选集）、候选值或候选来源；自由回答字段明确显示“自由回答字段”。

## 6. 审查这张图时应该回答的三个问题

对每个 Stage，只需要检查：

1. **Agent 收到了什么？** —— `Agent receives / Context`
2. **Agent 必须返回什么？** —— `Agent must return`
3. **还有哪些不是 Agent 字段、但 SDK 会用它们改变流程或权威状态？** —— `◆ SDK critical controls`

除此之外的内部实现细节不属于这张图的默认展示范围。
