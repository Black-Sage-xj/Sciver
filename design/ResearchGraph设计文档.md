# ResearchGraph：每个科学 Cycle 一个 Outcome

> 2026-10-18 规范。旧版“每 H 唯一 Outcome”是历史实现，不再是图谱阅读和新 Cycle 的语义。`science_graph.sqlite3` 仍为事实来源，`exploration.canvas` 是只读投影。未经单独授权不启动真实模型 Cycle。

## 科学身份与裁决

- 每个**新**科学 Cycle 锁定一个新的、可独立裁决的冻结 H。新 H 可从一个或多个旧 H 产生，须说明相对来源的可证伪科学增量，来源分为唯一 `lineage_main_node_id` 和 0–2 个 `lineage_other_node_ids`；由 Lead 选择 main，只有 main 的有类型 H→H 边参与图谱连线和分支布局，其它来源保留在 SQLite 与详情中。换字、换方法或重复检验本身不构成增量。
- 同一 Cycle 的 Investigator 轮次、Judge rework、恢复与重试复用其已锁定 H，不创建第二个 H。`hypothesis_cycle_owners` 记录新规则下的 Cycle↔H 一对一身份。
- 每个有终局科学裁决的 Cycle 恰有一条 **Cycle Outcome**。它含原 Judge verdict、冻结检验、理由、发现、问题、边界、Evidence、报告链接和完整 Markdown 汇总。`supported/rejected/open` 只覆盖该轮冻结命题及 scope。一个 Cycle 的多次尝试和中间决定是过程历史，不产生多个终局 Outcome。
- 运行中、失败、中断、取消或仅运维收尾而无科学裁决的 Cycle 保留运行记忆，但没有科学 Outcome；不得用 `open` 补齐。Goal Completion 是独立任务级裁决。
- 历史宽泛 H 可以关联多个不同 Cycle Outcome。H 的整体 `scientific_status` 是单独的历史/全命题判断，不能从最新或最强局部裁决自动推断。一个旧 H 下的 supported 与 rejected 可以同时存在，分别指向不同冻结命题。

图谱节点只有 H 与 External。Outcome 嵌入 H 的阅读卡片，不成为独立科学图节点；旧 Decision ID 保留为来源 ID。前沿 `active/parked/closed` 与科学裁决正交，H 有后代不自动失去研究来源资格。External 没有 Judge 权威。

**文案与权威界限：** 当前 Reporter 只有 `report` Stage，不再有 `canvas_maintenance` 或 Decision 节点交付。`title/summary` 保存完整人类可读报告，`graph_summary` 独立承担图谱导览；两者都不改变 Judge 裁决或前沿状态。External 时间到 Cycle 的 Web 估计不赋予它科学身份，不进入 SQLite 正式 Cycle 字段。

## 当前存储的兼容结构

- `cycle_evaluations` 保存终局 Cycle Outcome 的结构化原文，并对 `cycle_id` 设唯一索引；重放同一内容幂等，第二个不同 Outcome 拒绝。新 H 的锁定归属由 `hypothesis_cycle_owners` 约束。
- `frontier_branches` 独立保存每个 H 的 `active/parked/closed` 研究机会状态；它不由 H 的图度数或 Outcome verdict 推导。`scientific_edges` 保存有类型的 H→H 演化关系，External 及其关联关系存于独立表。Agent 读取统一的 SQLite 只读视图，不解析 Canvas 文件。
- 旧 `outcomes` 表保留每 H 一行，作为历史全 H 判断与旧前沿代码的兼容字段；它**不是**新规范下的 Cycle Outcome 列表。旧 H 的局部 Decision 导入时只进入 `cycle_evaluations`，不改这行的科学命运。新 H 与单一冻结 Cycle 完全同范围，终局裁决同步其 H 状态。
- `notes/outcomes/<Cycle ID>.md` 是该轮唯一主报告：同一份图谱概览、研究正文、折叠的 Judge/正式科学记录、补充材料和复现索引；完整结构化记录单独放在同名 `.json` 文件。Markdown 与 JSON 均有来源/渲染完整性标记。只有可验证的 SDK 生成文件允许随模板升级，人工编辑或来源不明的文件必须保留并报告冲突。
- `cycle_memory_index` 继续索引 Cycle 运行目录、状态和过程记录。原始 Canvas 的字节备份、`legacy_records`、`legacy_edges`、`legacy_conversion_map`、Evidence 和旧报告保持可追溯。

## 历史 Canvas 的整理规则

1. 先备份数据库（含 WAL 一致快照）、画布与 notes，冻结旧写入者并确认没有运行中的科学 Cycle 正在切换权威。
2. 按真实 Cycle ID 聚合旧 Decision；原始 H→Decision 边、`hypothesis_lock`、Judge 记录和报告互相校验。每个已裁决 Cycle 生成一条 Outcome 和一个文档。原始文字、裁决范围、非标准 ID、坐标与边全部保留。
3. H-0051、H-0089 等多轮旧 H 先保留为历史来源节点，展示其全部 Cycle Outcomes。只有在冻结命题和科学来源可确认时才把某一轮整理成独立 H；不能按 Cycle 编号机械拆分，也不能臆造 H→H 谱系。无法核对的归属登记异常并保留原资料。
4. 原 `milestone_advanced` 保留其原始类型和正文；兼容投影若需 `open` 只作显示归一，不把它说成原 Judge 的 `open`。无 Judge 授权的 Finding/StageSummary 仍归 External。
5. 在隔离副本比对 Outcome 数量、Cycle 唯一性、H 归属、全部历史字节、连线、文档及 Web 阅读，然后才原子切换生产投影。任一冲突停止切换，不覆盖原件。

图谱字段、Frontier 权限、External 及 Reporter 维护细则以现行 [Canvas设计文档.md](./Canvas设计文档.md) 为准。旧 H/Decision/Finding/StageSummary 原规范现归档于 用户的私有历史归档（不随公开源码分发）；迁移保全证据见 [Canvas双节点与Outcome数据库迁移规范.md](./Canvas双节点与Outcome数据库迁移规范.md) 和 Git 历史。

## 节点摘要与档案生成契约（2026-10-20）

- H 的完整科学命题在锁定时冻结。`hypothesis_display` 只负责阅读导览，不得修改冻结命题、范围或决定性检验；候选提案阶段不得提供 `canvas_title`、`canvas_summary`、`canvas_claim` 或 `canvas_conditions` 来绕过该阶段。
- 展示模型返回三个独立字段：`canvas_title`、`canvas_claim`、`canvas_conditions`。SDK 检查长度与非空约束，再确定性组合为 `主张：…；条件：…；状态：待检验。`。初始认识状态由 SDK 给出，不由模型臆测；后续 `supported/rejected/open` 标签由 SQLite 的整体 H 裁决派生，并明确限定在冻结范围内。
- 语义压缩必须保留具体解释/预测主张与不可缺少的适用条件。若输出结构错误、越界或无法安全压缩，科研流程继续，但节点必须显示有界的“摘要暂不可用”提示；不得把完整 Hypothesis 或 rationale 直接塞入摘要。完整原文与详情入口必须保留。
- 标题最多 48 字符，主张最多 78 字符，条件最多 54 字符；SDK 组合的摘要最多 160 字符。摘要回退不声称完成语义压缩，不能替代完整档案。
- 每个 SDK H 档案保留原样冻结快照与来源索引。`lineage_anchor_node_ids` 中的每个上游 H 和对应演化类型都必须进入档案，不能只写第一个父节点。历史回填逐字保留 SQLite 字段与 `legacy_records` 原文；没有记录的 Scope/Decisive Test 等字段不得推断补写。
- SQLite 是科学图谱权威，`exploration.canvas` 仅是只读投影。旧档案中关于 Canvas 是裁决/活动状态权威的生成器声明须安全迁移；更新仅限可识别的 SDK 生成区块，人工注释不得覆盖。
- H 卡片应优先展示短命题导览、当前整体科学状态、最新 Cycle Outcome、一个未解决问题和必要的 parked 原因。完整证据、所有历史结论与复现材料通过档案/Outcome 链接展开。
- Outcome Markdown 负责阅读；独立 JSON 保存完整机器记录。生成器版本、完整输入摘要与渲染摘要必须能识别模板变更；只有可验证的 SDK 生成文档允许自动升级，任何人工编辑或未知格式都必须拒绝覆盖。


## Main 与其它来源（2026-11-09）

新候选的 main 与其它来源必须互异、存在于 SQLite，派生类型仍由 `evolution_operator` 指定。SDK 在资格检查、选择及修订中保留来源角色，锁定时将角色写入 `scientific_edges.provenance_json.lineage_role`（`main` / `other`）。两类来源都保留可查询的科学关系；Canvas 导出与网页 SQLite 投影仅绘制 main，详情及 H 档案分别列出两类来源。根 H 省略来源字段。

历史 singular/plural 来源合同继续用于旧 checkpoint 恢复；没有角色的历史边保持原有连线，不根据顺序或布局推断 main，也不修改历史科研裁决。原有 1–3 个来源的总数上限不变。

## 图谱导览与报告分离（2026-11-15）

- Reporter `report` v4 的 `graph_summary` 包含四个正文字符串：`conclusion`（本轮科学增量）、`basis`（决定性比较、区间或证明依据）、`scope`（必要条件及资料来源身份）、`open_question`（本节点的具体未决缺口，已解决时为空）。不把继承知识冒充本轮新增发现，不把任务级长期目标重复当作每个 H 的未完成检验，不生成下一轮计划。
- 四字段合计 900 字符仅为防止超大交付的安全上限，不是写作目标；保持原有导览所需的科学细节。`summary` 继续保存完整报告，正式 `findings/open_questions/boundary` 仍由授权科学记录产生；导览不覆盖这些字段，也不推断裁决。
- 导览可选地保存在不可变 Cycle Outcome 的 `source_json` 中，不新增数据库权威或重写历史记录。新记录在 Canvas/Web 展开处完整展示导览，Agent 的焦点与活跃前沿上下文保留全部导览字段；图上短卡片以结论开头，完整依据经点击或悬停可读。旧记录继续兼容读取原报告预览，不自动调用模型补写。

## 单一研究报告（2026-11-16）

- Reporter `report` v5 只写一次 `graph_summary`；`summary` 是问题与必要条件、主要结果、解释与限制三节正文，不再另写结论摘要。SDK 将同一概览用于图谱/节点和主报告开头，加入正式裁决与复现索引；旧 `summary` 正文保留。
- SDK 不再生成第二份 `<Cycle>--<title>.md`。主报告固定为 `notes/outcomes/<Cycle>.md`，Reporter、H 档案索引、Web 和 Agent 详细引用沿用该入口。旧文档链接在 Web 解析到同轮只读报告视图，历史文件及 SQLite 裁决不改写。
- `![[path.md#Section|Label]]` 表示显式引用已有补充 Markdown/章节；SDK 在 `report_context` 保存原文件 SHA-256、选择的文本与来源，用折叠块展示。只读取任务资料区或已登记可用文档资产，最多 12 个、每个来源文件 256 KiB；较大文件使用普通链接。嵌套引用保留为链接，代码中的示例保持字面文本。核心论证仍在主报告正文。
- Agent `report-read <Cycle> [--section TITLE_OR_ID] [--offset N]` 与 Web `/report` 使用同一报告装配、章节索引和分页。概览不注入完整报告与附件；按需读取保留原科学权威和来源身份。历史未裁决保全报告明确标记其执行状态，不产生 Judge verdict。
- active、parked、closed 均展示已记录的分支原因，缺失时明确提示原因未记录。有 Cycle 裁决但无整体 H 评估时，阅读文案明确标注整体 H 尚未登记裁决，不以本轮结论改写整个 H 的科学状态。
- 猜想导览继续使用冻结科学定义，压缩时保留其中已声明的可判别区别及必要条件；完整动机和来源细节放在档案中。历史缺失摘要的整理属独立工作，本次不自动回填。
