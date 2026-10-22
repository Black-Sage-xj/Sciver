# ResearchGraph：每个科学 Cycle 一个 Outcome

> 2026-10-18 规范。旧版“每 H 唯一 Outcome”是历史实现，不再是图谱阅读和新 Cycle 的语义。`science_graph.sqlite3` 仍为事实来源，`exploration.canvas` 是只读投影。未经单独授权不启动真实模型 Cycle。

## 科学身份与裁决

- 每个**新**科学 Cycle 锁定一个新的、可独立裁决的冻结 H。新 H 可从一个或多个旧 H 产生，须说明相对来源的可证伪科学增量，并以有类型的 H→H 边记录来源。换字、换方法或重复检验本身不构成增量。
- 同一 Cycle 的 Investigator 轮次、Judge rework、恢复与重试复用其已锁定 H，不创建第二个 H。`hypothesis_cycle_owners` 记录新规则下的 Cycle↔H 一对一身份。
- 每个有终局科学裁决的 Cycle 恰有一条 **Cycle Outcome**。它含原 Judge verdict、冻结检验、理由、发现、问题、边界、Evidence、报告链接和完整 Markdown 汇总。`supported/rejected/open` 只覆盖该轮冻结命题及 scope。一个 Cycle 的多次尝试和中间决定是过程历史，不产生多个终局 Outcome。
- 运行中、失败、中断、取消或仅运维收尾而无科学裁决的 Cycle 保留运行记忆，但没有科学 Outcome；不得用 `open` 补齐。Goal Completion 是独立任务级裁决。
- 历史宽泛 H 可以关联多个不同 Cycle Outcome。H 的整体 `scientific_status` 是单独的历史/全命题判断，不能从最新或最强局部裁决自动推断。一个旧 H 下的 supported 与 rejected 可以同时存在，分别指向不同冻结命题。

图谱节点只有 H 与 External。Outcome 嵌入 H 的阅读卡片，不成为独立科学图节点；旧 Decision ID 保留为来源 ID。前沿 `active/parked/closed` 与科学裁决正交，H 有后代不自动失去研究来源资格。External 没有 Judge 权威。

**兼容与文案界限：** Reporter `canvas_maintenance` 的输出合同仍命名为 `decision.title/summary`，但它只负责本轮 Outcome 的**图谱短标题/摘要**（120/900 字符上限），不代表在 V2 图中新增 Decision 节点，也不限制完整 Markdown 报告。External 时间到 Cycle 的 Web 估计不赋予它科学身份，不进入 SQLite 正式 Cycle 字段。

## 当前存储的兼容结构

- `cycle_evaluations` 保存终局 Cycle Outcome 的结构化原文，并对 `cycle_id` 设唯一索引；重放同一内容幂等，第二个不同 Outcome 拒绝。新 H 的锁定归属由 `hypothesis_cycle_owners` 约束。
- `frontier_branches` 独立保存每个 H 的 `active/parked/closed` 研究机会状态；它不由 H 的图度数或 Outcome verdict 推导。`scientific_edges` 保存有类型的 H→H 演化关系，External 及其关联关系存于独立表。Agent 读取统一的 SQLite 只读视图，不解析 Canvas 文件。
- 旧 `outcomes` 表保留每 H 一行，作为历史全 H 判断与旧前沿代码的兼容字段；它**不是**新规范下的 Cycle Outcome 列表。旧 H 的局部 Decision 导入时只进入 `cycle_evaluations`，不改这行的科学命运。新 H 与单一冻结 Cycle 完全同范围，终局裁决同步其 H 状态。
- `notes/outcomes/<Cycle ID>.md` 由已存 Judge 数据确定性生成，逐项列出科学结论、原 Decision 正文和原报告；完整结构化记录单独放在同名 `.json` 文件。Markdown 与 JSON 均有来源/渲染完整性标记。只有可验证的 SDK 生成文件允许随模板升级，人工编辑或来源不明的文件必须保留并报告冲突。
- `cycle_memory_index` 继续索引 Cycle 运行目录、状态和过程记录。原始 Canvas 的字节备份、`legacy_records`、`legacy_edges`、`legacy_conversion_map`、Evidence 和旧报告保持可追溯。

## 历史 Canvas 的整理规则

1. 先备份数据库（含 WAL 一致快照）、画布与 notes，冻结旧写入者并确认没有运行中的科学 Cycle 正在切换权威。
2. 按真实 Cycle ID 聚合旧 Decision；原始 H→Decision 边、`hypothesis_lock`、Judge 记录和报告互相校验。每个已裁决 Cycle 生成一条 Outcome 和一个文档。原始文字、裁决范围、非标准 ID、坐标与边全部保留。
3. H-0051、H-0089 等多轮旧 H 先保留为历史来源节点，展示其全部 Cycle Outcomes。只有在冻结命题和科学来源可确认时才把某一轮整理成独立 H；不能按 Cycle 编号机械拆分，也不能臆造 H→H 谱系。无法核对的归属登记异常并保留原资料。
4. 原 `milestone_advanced` 保留其原始类型和正文；兼容投影若需 `open` 只作显示归一，不把它说成原 Judge 的 `open`。无 Judge 授权的 Finding/StageSummary 仍归 External。
5. 在隔离副本比对 Outcome 数量、Cycle 唯一性、H 归属、全部历史字节、连线、文档及 Web 阅读，然后才原子切换生产投影。任一冲突停止切换，不覆盖原件。

图谱字段、Frontier 权限、External 及 Reporter 维护细则以现行 [Canvas设计文档.md](./Canvas设计文档.md) 为准。旧 H/Decision/Finding/StageSummary 原规范现归档于 [Canvas设计文档-历史V1.md](./Canvas设计文档-历史V1.md)；迁移保全证据见 [Canvas双节点与Outcome数据库迁移规范.md](./Canvas双节点与Outcome数据库迁移规范.md) 和 Git 历史。

## 节点摘要与档案生成契约（2026-10-20）

- H 的完整科学命题在锁定时冻结。`hypothesis_display` 只负责阅读导览，不得修改冻结命题、范围或决定性检验；候选提案阶段不得提供 `canvas_title`、`canvas_summary`、`canvas_claim` 或 `canvas_conditions` 来绕过该阶段。
- 展示模型返回三个独立字段：`canvas_title`、`canvas_claim`、`canvas_conditions`。SDK 检查长度与非空约束，再确定性组合为 `主张：…；条件：…；状态：待检验。`。初始认识状态由 SDK 给出，不由模型臆测；后续 `supported/rejected/open` 标签由 SQLite 的整体 H 裁决派生，并明确限定在冻结范围内。
- 语义压缩必须保留具体解释/预测主张与不可缺少的适用条件。若输出结构错误、越界或无法安全压缩，科研流程继续，但节点必须显示有界的“摘要暂不可用”提示；不得把完整 Hypothesis 或 rationale 直接塞入摘要。完整原文与详情入口必须保留。
- 标题最多 48 字符，主张最多 78 字符，条件最多 54 字符；SDK 组合的摘要最多 160 字符。摘要回退不声称完成语义压缩，不能替代完整档案。
- 每个 SDK H 档案保留原样冻结快照与来源索引。`lineage_anchor_node_ids` 中的每个上游 H 和对应演化类型都必须进入档案，不能只写第一个父节点。历史回填逐字保留 SQLite 字段与 `legacy_records` 原文；没有记录的 Scope/Decisive Test 等字段不得推断补写。
- SQLite 是科学图谱权威，`exploration.canvas` 仅是只读投影。旧档案中关于 Canvas 是裁决/活动状态权威的生成器声明须安全迁移；更新仅限可识别的 SDK 生成区块，人工注释不得覆盖。
- H 卡片应优先展示短命题导览、当前整体科学状态、最新 Cycle Outcome、一个未解决问题和必要的 parked 原因。完整证据、所有历史结论与复现材料通过档案/Outcome 链接展开。
- Outcome Markdown 负责阅读；独立 JSON 保存完整机器记录。生成器版本、完整输入摘要与渲染摘要必须能识别模板变更；只有可验证的 SDK 生成文档允许自动升级，任何人工编辑或未知格式都必须拒绝覆盖。
