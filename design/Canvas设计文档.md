# TheoryMaster Canvas / ResearchGraph 现行维护规范

## Markdown 笔记库与自动 Canvas（2026-11-18）

- `vault_root` 明确指定人类可读笔记库；`branch_canvas` 固定在该目录根部。新任务的 `graph_database` 位于独立的 `research_root`，运行数据与笔记分别保存。任务身份由原配置和运行根目录绑定，移动笔记库保留任务 ID。
- `vault_canvas.refresh_canvas()` / `refresh_registered_canvases()` 从 SQLite 的一致快照生成标准 JSON Canvas。H 档案、Cycle 报告和 External 文档使用库内相对路径的 `file` 卡片，连线带关系名称。报告卡片属于阅读投影，科学图谱节点仍为 H 与 External。
- 图谱外层事务提交后刷新已登记全图和分支 Canvas；文档首次发布后的运行时刷新使卡片关联可见文件。单独刷新 Canvas 只读取既有 Markdown，不生成、改写或恢复 Markdown。输出经锁和原子替换发布；失败保留上一个完整文件，登记 pending，并在后续提交或手动刷新时重试。
- 全图文件为根目录的 `exploration.canvas`；用户创建的分支文件为同目录的 `branch-<H ID>.canvas`，范围为该 H 及后续分支、关联 External 和报告。Canvas 单向生成；个人布局可另存副本。
- Markdown 是唯一可编辑阅读正文。Outcome 和 External 首次发布后保留原文件；H 档案只为新 Cycle 追加独立块，重试保留已有字节。发布索引独立保存在 `.theory-master`。已删除的文档显示缺失，刷新不重建。
- Web `/document`、`/report` 和 Agent `report-read` 读取同一 Markdown。章节、分页与正文摘要来自文件；节点身份、连线、Whole-H / Cycle 裁决及前沿状态来自 SQLite。
- 图谱工具栏的“笔记库”提供路径、复制、Obsidian、同机打开文件夹、整库下载、全图/分支另存和输出目录选择。服务端整库移动只允许无活跃 Runner、Worker 或调度器的任务，保留唯一文档，更新路径绑定；本机另存产生副本。
- 用 `sciver new research_plan.md --vault-root /absolute/empty/folder` 选择新任务笔记目录。远程网页使用本机已登记的 Obsidian 库名；服务端文件管理器操作仅对同机桌面连接开放。

## Runtime data authority (2026-10-20)

- `science_graph.sqlite3` is the only authoritative store for H records, lineage, External records, frontier status, and Cycle Outcomes.
- `exploration.canvas` is a generated visualization projection. Runtime Agents do not read or write it as a scientific data source.
- The Reporter returns a human-readable report; the SDK records the Cycle Outcome in SQLite and regenerates the projection. There is no `canvas_maintenance` stage, Canvas patch, or Decision-node runtime path.
- A workspace that has a Canvas but no canonical SQLite database fails closed. Use the explicit one-time importer to migrate it; runtime does not create a placeholder database or fall back to Canvas.

> **2026-10-18 · 当前有效。** 本文件取代旧 H/Finding/Decision/StageSummary 的 Canvas V1 规则。旧版私有运行归档不随公开源码分发，不得用旧规则指导生产写入。逐 Cycle 科研裁决细节以 [ResearchGraph设计文档.md](./ResearchGraph设计文档.md) 与实际 SDK/SQLite 代码核对。

## 1. 研究资料的权威层

| 对象 | 作用及权威 |
| --- | --- |
| Research Plan | research_plan.md 中 Goal、Research Contract、Final Gate 由 Human 维护；Scientific Model 是授权流程形成的长期科学认识 |
| 科学图谱 | science_graph.sqlite3：H、External、H→H 科学演化、Cycle Outcomes、Frontier branches、历史迁移证据 |
| 运行记录 | research_state.json、事件、Cycle 目录：运行进展，不代表科学裁决 |
| exploration.canvas | SQLite 只读投影；不得向旧 Canvas 与新 SQLite 同时写入同一图 |
| Research Explorer | Web 只读图谱/Markdown 导航；位置、颜色、缩放、时间估计均非科研权威 |

Canvas 不是 Workflow 状态板、Evidence Registry、原始数据仓库或完整科研报告。

## 1.1 SQLite Agent 图谱工具层（现行）

`science_graph.sqlite3` 是科研事实的唯一权威来源；`exploration.canvas` 是从 SQLite 导出的只读可视化投影；Markdown dossier / outcome / External 详情是可追溯的阅读副本，不是第二权威存储。Agent 不应解析 Canvas 来重建已迁移的科研状态。

### 访问模式

- **启动上下文**：SDK 在 Research Lead 的 `frontier_proposal` 与 `frontier_maintenance` Stage 注入 `agent_view()` 的精简全局索引。它包含全部 H 的紧凑索引、当前 frontier、typed H→H lineage、External 来源/关系索引，以及有限的焦点 H 摘要；不注入所有 H/External 的完整正文。
- **按需详情**：统一只读工具层通过稳定的语义操作读取单个 H、External 及其谱系。工具返回结构化 JSON、稳定 ID、来源引用和明确的缺失/未评估状态。详情正文设长度上限；被截断时必须返回 `content_truncated` 和可继续读取的引用，不得静默丢失。
- **第一阶段运行接口**：提供 `python -m theory_master.science_graph_tools --database <SQLite路径> <operation> <ID>` 命令门面，内部只调用 `ScienceGraphStore` 只读方法。Lead 通过已有 shell/命令执行能力调用它；这是一层统一 SDK/CLI 门面，不宣称已经是 MCP Server。
- **预留 MCP**：未来若需要独立客户端/跨进程访问，可将同一工具函数封装为 MCP Server；MCP 只负责工具发现与调用协议，不引入第二数据库或第二套业务逻辑。
- **角色范围**：全局 `agent_view()` 仍只注入 Research Lead 的两个前沿 Stage。`frontier_proposal` 的只读工具增加 `search`。`frontier_maintenance` 仍只有 `view` 与按 ID 详情。Challenger `candidate_review` 不接收全图，也不获得 `view`；它保留声明上游视图，并可调用只读 `search`、`hypothesis-detail`、`external-detail`。Judge/Reporter 保持各自目的视图。Investigator 不接收全图。
- **权威与失败策略**：当前运行时的图谱 Stage 必须解析到有效的 canonical SQLite 数据库；缺失或读取失败时显式失败，禁止隐式退回 `canvas_access`。当前生产 SDK 不再启用 Canvas-only 运行模式；若未来确需迁移旧任务，必须新增显式登记、独立测试的 legacy adapter，不能由“SQLite 路径缺失”自动触发兼容。Canvas 永远不回写 SQLite。
- **返回语义**：H 不存在必须返回明确 `not_found`；尚无 Whole-H assessment 返回 `none`/空对象，不得映射成正式 `open`；局部 Cycle Outcome 不可自动升级为 Whole-H verdict。多父谱系必须完整返回，不得仅显示单个锚点。
- **安全边界**：工具只读、不提供任意 SQL；H/External ID 必须参数化查询。详情长度与最近 Outcome 数量有硬上限。原始计算文件仍由 Agent 根据科研需要通过现有文件读取能力自主探索。

### 工具契约

- `agent_view(entry_hypothesis_id?)`：全局紧凑索引，用于选择研究方向；它不是“只读一个 H”的过滤器。
- `agent_hypothesis_detail(hypothesis_id, recent_outcomes=...)`：返回命题、Whole-H assessment、最近 Cycle Outcomes、完整相邻 lineage、关联 External 引用和 dossier 引用。
- `agent_external_detail(external_id, max_content_chars=...)`：返回来源元数据、原文（有界）、关联 H/Outcome 和 External 间关系。
- `agent_search(query)`：按标题、命题、近期 Outcome 与 External 有界正文做全局只读检索。最多 8 条，每条只有 id、类型、标题、不超过 180 字的摘要和关联 ID。不返回全文，也不按候选直接上游过滤。
- 工具结果只用于科研读取；持久化变更仍必须经过已有 SDK 授权事务。对详情接口返回的 `none`、`unadjudicated`、`open` 等状态，必须遵循本文件第 3–4 节的语义区分。

### 正式投影、旧布局预览与 External 详情

- `exploration.canvas` 是当前 `science_graph.sqlite3` 的只读正式投影；当前核对结果为 117 个 H、68 个 External、185 个节点、260 条边。SQLite 是权威数据源，Canvas 不反向写入数据库。
- Web External 卡片显示数据库短预览，完整详情读取 `notes/external/<External ID>.md`。Canvas 使用同一文件的文档卡片。首次发布后保留用户编辑，图谱刷新只更新 Canvas。
- `exploration-v2.canvas` 是历史 staging 布局预览，节点/边集合与当前正式图谱不同；其元数据明确标为 `staging_preview_not_current`，不用于当前 Agent 上下文、科研状态判断或正式投影一致性核验。它也可以被可视化查看，但不是当前正式图的等价副本。为避免混合历史与当前数据，其 External 详情独立保存在 `notes/external-v2/<ID>.md`，不覆盖正式投影的 `notes/external/<ID>.md`。
- 正式投影元数据的 Outcome 语义必须表示“每个 H 有一个 Whole-H assessment，并可有零条或多条 Cycle Outcome”。旧 `one_outcome_per_hypothesis` 不符合现行模型；旧预览必须明确标注历史/非权威，不能冒充当前 schema。

## 2. 现行节点和连线

**当前正式科学图只有 Hypothesis（H）与 External（E）两种节点。**

- H 是稳定的科学命题身份；旧迁移记录的 H-0007 等数字不能直接推断 Cycle。新建立的冻结 H 按其 Cycle ID 编号；老 H 的身份与历史冻结 Cycle 必须按可靠来源映射，禁止仅凭数字强行重命名。
- External 保存外部人工资料、历史 Finding/Decision/StageSummary 和其他来源；content_type 只描述原文文体，不授予 Judge/Evidence 权威。
- **Outcome 不单独成为图节点**，属于对应 H 的阅读内容；旧 Decision ID 留作来源追溯。

- **Canvas 只负责可视化，不再作为已迁移 Agent 的图谱数据入口。** `science_graph.sqlite3` 是 H、Outcome、frontier 与 typed lineage 的唯一权威数据层。Research Lead、Challenger、Judge 的 Goal Completion 和 Reporter 的 Cycle Outcome 文案都由 SDK 从 SQLite 生成各自精简的只读上下文；SDK 将经过验证的修改提交 SQLite，再导出 Canvas。`canvas_access` 只允许用于确实没有 SQLite 的旧 Canvas-only 任务，不得在已迁移任务中与 SQLite 并列提供。
- 科学演化只能是有可核验来源、明确 evolution_operator 的 **H→H** 边；E 通过 **H→E** 或 **E→E** 外部引用连接，绝非 H→H 科学推理边。
- 不得因 Cycle 编号相邻、标题相似或页面布局推断父子 H，不得把历史迁入 Finding 自动升级为新的科学节点。

## 3. H、Cycle 和 Outcome

- **每个新科学 Cycle 都锁定一个新的、科学上有可裁决增量的 H。** 恢复、重试、Judge rework 都继续沿用同一 Cycle 的同一个 H。新 Cycle 的 Cycle↔H 归属由 hypothesis_cycle_owners 约束，不能重复占用。
- **每个真正终局科学裁决的 Cycle 恰有一个 Cycle Outcome**（supported / rejected / open）；未得到科学裁决的运行失败、取消、中断、纯运维收尾均不得填充虚假 open。
- 每条裁决只覆盖本轮冻结命题与检验范围；历史宽泛 H 可以对应多条不同 Cycle Outcomes，支持与反驳并存时不自动改变整个父 H 的命运。
- 数据库的旧 outcomes 表仍保持“每 H 一行”的**兼容/整体 H 状态**，不是每 H 只允许一次科研裁决。逐 Cycle 的科学记录属于 cycle_evaluations，详细只读文档为 notes/outcomes/<Cycle ID>.md。graph_view 同时保留兼容摘要和 cycle_outcomes[]。
- 只有**新 H 的命题与本轮冻结 Scope 完全一致**、经 SDK 授权投影时，终局结论才可同步其完整 H scientific_status；历史父 H 的局部 Decision 不能自动上推。
- scientific_status 为 none / open / supported / rejected，其中 none 表示没有登记完整 H 结论，**不是** Judge 已裁决 open。H ID、Cycle ID、Outcome ID 是不同身份。

## 4. Frontier 状态

- Frontier 由 SQLite 的 frontier_branches 持久化：active / parked / closed。
- **2026-10-18 规范化**：同一个 H 在 frontier_branches 中最多只能有一条当前记录（数据库 UNIQUE(hypothesis_id)）；历史 Decision 级的 frontier_status 仅保留于 legacy_frontier_archive，不能参与当前前沿计数或科学身份。Web 的 Active 数量按 H 去重，不是历史 Decision 数量，更不是 Worker 数量。
- **缺失 ≠ closed**：没有经核验的前沿状态时，frontier_view 返回空（UI 显示「未标记」），不能偷偷自动判定 Closed；运行时禁止以未知前沿作为已授权 same-H 研究起点。迁移中多个相互矛盾的 Decision 前沿、缺乏后续直接 H 维护证据的，保留为「待核验」，不擅自裁定 active、parked 或 closed。
- **历史迁移原则**：恢复 H 的当前状态优先使用其时间上最新的 Research Lead 显式 H 维护；原始 Decision 状态与细节不可覆盖 Lead 的较新决定。完整源于 backed-up SQLite + Cycle frontier_maintenance_out.json，执行入口 scripts/reconcile_science_frontiers.py，重复执行拒绝，历史归档可回溯。
- **Whole-H 占位 Open 的含义**：V1 的局部 Cycle Judge 结论可能被归入较宽泛的父 H。没有经过全 H 裁决的空 outcomes.verdict=open 是历史导入占位，不是 Judge 正式 Open。graph_view 的 legacy_identity_review 将存在明确终局 Cycle 与占位 whole-H 之间的归属缺口暴露给 Web；不能把局部 rejected/supported 自动升级成父 H 全命题的 verdict。

- active 为默认可研究来源，parked 表示暂缓，closed 表示不在默认研究前沿；三者与 scientific_status **正交**。
- Frontier **属于分支，不等于图形叶子节点**。已有后代的 H 仍可 active；不能把旧 H frontier_status 机械迁移给后继 Decision/子 H，也不能只因出现 outgoing edge 就删除它。
- Research Lead 负责研究方向和 frontier maintenance；Judge 负责 Cycle 科学裁决；SDK 负责数据验证/落盘；Reporter 只撰写经裁决的简短文案。active 不是下一 Cycle 的强制起点；历史 parked/closed H 仍可成为有明确 why_now 和新科学增量的派生 H 来源，但不能作为同一 H 复用重跑。

## 5. Reporter 图谱导览与完整报告

Reporter 的当前 `report` 合同为 v5：返回 `title`、三节研究正文 `summary` 与独立 `graph_summary`，没有 Canvas-writing Stage。正文从问题与必要条件开始；SDK 在唯一主报告开头复用概览，加入正式裁决、补充材料与复现索引。导览的四个正文字符串分别为本轮结论 `conclusion`、关键依据 `basis`、必要范围及资料来源身份 `scope`、本节点未决问题 `open_question`（无剩余问题时为空）。合计 900 字符是安全上限而非写作目标，保留判断结论所需的科学细节。

- 导览以本轮新增、排除或仍未判明的认识开头；继承知识保留背景身份。关键依据保留决定性数值、区间相对冻结门槛的位置或证明原因；不以核验完成替代科学结论。
- 完整实验步骤、证据清单、全局研究背景与复现材料通过档案和报告读取。未决问题优先表达本节点缺口，不机械重复整个任务的完整曲线、盲测等目标。
- SDK 将导览保存到 Cycle Outcome 的 `source_json`，不再从完整报告开头截取新节点导览。Web 的图上短卡片优先呈现结论，点击后显示完整导览，每轮只给出一个「阅读研究报告」入口；报告内按章节读取正文，展开正式裁决与固定版本补充文档。Canvas 与 Agent 上下文保留导览全部语义字段。图上的短预览本身不是完整科学结论。
- 导览不返回或覆盖 ID、Judge verdict、scientific_status、frontier_status、Evidence 引用、谱系、坐标或下一 Cycle 计划。正式科学字段仍来自 Judge、Lead synthesis 和 Evidence。文字润色不扩大局部结论范围。
- active、parked、closed 均展示已记录的分支原因，未记录时明确标注；整体 H 未评估而本轮已裁决时，展示文案明确区分两者。
- 历史 Outcome 缺少导览时兼容读取原报告；缓存旧 Reporter 交付可恢复，不自动调用模型生成新解释，也不改写冻结命题、历史报告或裁决。

实际合同见 `theory_master/contract_stages.py`，规则及运行时 Skill 见 `theory_master/resources/skills/report-writing/SKILL.md`。

## 6. External 的时间与 Cycle 推定

- 外部记录以 external_records 和原始 content/source 为准；迁移数据库中的 created_at 通常只是**迁移时间**，不能代替原资料发生时间。
- 显式 source_cycle 或归档 Cycle 来源可以显示 E90 等；只有原始可信时间和带明确 Cycle 的时间锚点时，Web 才显示 E≈80 等近似标记；时间超出锚点范围时使用 E≤55、E≥97 等边界。
- 这些编号是**资料时间/引用位置**，并非 External 获得 Judge 裁决的 Cycle。推定结果不得写回 science_graph.sqlite3 的正式 cycle/status，也不得改变 H→H 科学谱系。
- 无可核验来源时说明未知，不凭文件名、导入时间或图形位置制造精确科研事实。

## 7. 维护与迁移权限

- 科学 Agent 只读图谱并沿稳定 ID、vault-relative wikilink 和 Markdown 查看原文；不得把页面坐标、颜色、估计 Cycle 用作科学证据。
- 正式图谱修改只经过已授权的 SDK/ScienceGraphStore 事务；Web 仅支持安全 GET 阅读，不具备任意 Graph 写接口。Reporter 不拥有独立 Graph 写入权限。
- 迁移必须保全历史节点/边、原始字节、Decision ID、Evidence 与报告关联，在隔离副本审计后切换单一写入源。重复记录幂等；冲突不能静默覆盖；运行中的 Worker 与旧写入者不得越过切换隔离。
- 当前 Web 展示的 100% 固定基准、60% 以上详情视图、远景隐藏左侧入边索引只属于图谱阅读 UI，**不是**科研状态/SDK 的 schema 规则。
- Research Explorer 首次直接打开（无有效节点锚点）默认显示**完整图谱**，画面居中于有真实冻结 Cycle 的最新 H；只有缺少可信 H 时才回退历史带 Cycle 的科学节点或全图适配。显式节点深链优先于旧无锚点历史快照，浏览器 Back/Forward 与页内刷新保持原有阅读位置恢复功能。该定位仅操作 Web 摄像机，不更改科学状态。
- **节点时间戳回显（展示层）**：图谱里的 H/Outcome 上下双色卡片**不显示时间**；右侧节点详情标题下只放 H 的时间字段，右侧 Cycle Outcome 详情放相应裁决时间。不再在标题下显示重复的“Canvas 节点 · H-ID · 类型 · 状态”扁平元信息；ID、类型、科学状态、前沿状态等统一只在默认折叠的「节点标签」中查看。SQLite 迁移历史优先使用 legacy_records 原始 Canvas H/Decision 的 timestamp（保留源时区或旧简写），新原生 H/Outcome 使用数据库登记/记录时间且明确标注，不能把迁移入库时间当成历史事件发生时间；多轮 Outcome 或多来源时间不伪造单一时间。
- **Research Explorer 活跃分支 Worker 标记（展示层）**：点击「研究分支 · 活跃」弹层时，使用独立认证只读 GET 查询经过 PID、启动时钟滴答和 Cycle ownership 校验的 RUNNING Worker。先将 Worker 当前 Cycle 对应的数据库 hypothesis_cycle_owners 新冻结 H 作为标记目标；仅在 Cycle 尚无正式归属时回退其明确认领的 target_h_id。同一个活跃 H 可显示多个 Worker 徽标，点击原生链接直接打开 /run/{observer_id}/?worker=worker-N。OVER/PAUSED/ORPHANED、非活跃 H 不显示运行标记；每次展开重新核验，不写回科研数据库或节点标签。
- **Research Explorer 状态着色（展示层）**：≤60% 的数字远景默认按 Frontier 汇总状态为整个 H 填色，可在图谱工具栏切换为 Outcome 着色；>60% 的 H 详情上半部分按 Frontier 着色，下半部分按可信 Cycle Outcome 着色，未获 Judge 科学裁决的初始兼容 `open` 不当成 Open，历史多轮不一致的结论使用 Mixed，旧 `milestone_advanced` 等归一化 Open 使用中性历史状态（不冒充 Judge 的 Open），External 也保留独立中性色。聚合前沿优先 active→parked→closed，所有着色均不可用于推断正式科学状态。改变着色/缩放不得重新布局或改变科研连线。

## 8. 文档与代码对照

- **科研 Cycle Outcome 权威设计**：[ResearchGraph设计文档.md](./ResearchGraph设计文档.md)
- **原 Canvas 迁移保全**：[Canvas双节点与Outcome数据库迁移规范.md](./Canvas双节点与Outcome数据库迁移规范.md)
- **旧版规则归档**：留在用户自己的私有研究工作区，不随公开源码分发。
- **数据库实际行为**：theory_master/science_graph_store.py
- **Reporter Stage/Contract**：theory_master/stage_registry.py、theory_master/contract_stages.py
- **只读角色规则/维护边界**：identities/_canvas_read.md、identities/_canvas_renderer_policy.md
- **Web 阅读投影**：web/tm_observer/canvas_md.py、web/tm_observer/research_explorer_g6.js
- **Research Plan**：[ResearchPlan设计文档.md](./ResearchPlan设计文档.md)

如文档与运行契约出现偏差，以实测代码和数据库证据复核后更新文档；不得以旧文案擅自扩大 Agent 权限。

### 2026-10-19 · Preserve 阶段性结论归属原 Hypothesis 的 Outcome

- **Preserve 归属**：停止前保存的阶段性发现归入该 H 的 Cycle Outcome 历史，保留原报告、Evidence SHA 和已有 Judge 返工意见，不创建独立 External/StageSummary 节点。
- **裁决分离**：预算拒绝续批等未完成情况使用 `unadjudicated` 保全记录；正式 supported、rejected 或 Open 仍只由 Judge 产生。保全材料不得用成科学反证，也不得覆盖正式裁决。
- **阅读显示**：H 详情的 Outcome 部分可查看阶段性总结、未完成的冻结检验、缺口、终止原因和来源；节点仍为 Hypothesis，前沿是否继续由独立状态表达。
- **紧凑工具栏（展示层）**：分支、搜索、完整图谱、常用视图、统计、帮助和全屏集中在工具栏；浏览历史与缩放位于画布底角。菜单支持外部点击与 Esc 关闭，并返回键盘焦点。窄屏按剩余视口分配图谱与笔记空间。这些操作只影响阅读状态。
