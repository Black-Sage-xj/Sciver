# TheoryMaster SDK 设计文档

## Runtime compatibility boundary (2026-10-20)

The runtime registers only current Stage Contracts. Historical contract lookup, Canvas-only graph access, Reporter Canvas patches, and Decision-node writes are not supported. A legacy workspace without `science_graph.sqlite3` fails closed and must be migrated explicitly before execution. One-time import tools may preserve historical provenance, but they are not runtime fallback paths.

> **Science Graph V2 迁移边界（2026-10-17）**：生产 V2 的节点/Outcome/数据库权威规则统一由 [ResearchGraph设计文档.md](./ResearchGraph设计文档.md) 维护；历史 Canvas SDK 写入的 H→Decision/Finding 投影属于 V1 兼容实现。任务 1 已在完整保全原 Canvas 后正式启用 SQLite 事实库与新 H/External 导出图；其他未迁移任务仍按旧兼容路径运行，不能用显示层转换伪装为科研写入端完成迁移。完整路径见 [Canvas双节点与Outcome数据库迁移规范.md](./Canvas双节点与Outcome数据库迁移规范.md)。

> **2026-10-18 Cycle Outcome 修订**：对 SQLite 科研图谱，每个新科学 Cycle 锁定一个新 H，并产生至多一条终局 Cycle Outcome；没有科学裁决的 Cycle 不产生 Outcome。历史宽泛 H 可包含多轮 Cycle Outcome。下文的 same-H continuation 仅解释未迁移的 V1 路径与历史检查点，不再是 SQLite 新 Cycle 的资格规则。

> **现行维护索引（2026-10-18）**：当前 Canvas 权威见 [Canvas设计文档.md](./Canvas设计文档.md)：SQLite 的 H/External、H→H 演化、每终局 Cycle 的 Outcome 与非叶子限定的 `frontier_branches`。下文出现的独立 `Decision/Finding` 图节点、tip→Decision frontier 搬迁、same-H 新 Cycle 与旧 Canvas commit 流程作为 **V1 历史路径**保留，不能据此操作已迁移 V2 的数据库。实际以 ScienceGraphStore、Stage Card、Contract、ResearchGraph 规范核对。

> 状态：现行设计
> 范围：`tool/theory_master/`
> 定位：长周期、开放式科学问题的可恢复科研 Workflow Engine

TheoryMaster 当前设计权威拆分为同目录三份：本文负责 SDK / Workflow / runtime；[Canvas设计文档.md](./Canvas设计文档.md) 负责 Canvas 格式与科学谱系；[ResearchPlan设计文档.md](./ResearchPlan设计文档.md) 负责 `research_plan.md` 格式、Scientific Model 与维护边界。`README.md` 只提供安装、命令和运行入口；`audit/` 是验收工具。旧设计由 Git 历史承担。

> 同步审计基线：SDK 只维护系统接口与事务语义；Canvas / Research Plan 的具体格式不在本文重复定义，分别索引到两份专门规范。

---

# 1. 系统目标

TheoryMaster 用于推进不能由一次对话或一个计算直接解决的科学问题。系统围绕 Main Hypothesis 组织长期探索，并把每个 Cycle 定义为针对当前科学前沿的一次 bounded adjudication transaction。对 SQLite V2 而言，每个新 Cycle 应冻结**一个新的 H** 及其可独立裁决的科学增量；同一 Cycle 的重试/返工才复用已锁定 H。历史宽泛 H 仍可追溯多个不同旧 Cycle 的局部 Outcomes，不能因此作为新 Cycle 同一 H 身份重跑。已裁决的进展再被压缩进跨 Cycle Scientific Model。

核心目标：

1. 每个新 Cycle 只绑定并裁决一个锁定的新 H；科学裁决的范围是冻结的命题及测试，只有终局科学裁决才记录 Cycle Outcome。
2. 假设在进入 Cycle 前充分评估，锁定后不偷换；旧 H 是下一新 H 的可核实科学来源，而不是可在下一 Cycle 复用的 H 身份。
3. 科学判断、运行调度和独立裁决由不同组件负责。
4. 重要结论能回指 Evidence、Cycle 和 producer invocation；计算产物由 SDK 维护 invocation-level artifact manifest，不要求 Scientific Agent 在 finding 中抄写文件路径。
5. Agent thread 丢失后，仍可由持久化状态重建必要上下文。
6. 协议、Evidence 和运行记录可独立演化；旧 Cycle 不由新代码静默重解释。

## 1.1 核心科学单位与术语

- **Hypothesis（H）**：可以跨若干 Cycle 积累 Evidence 的科学命题。H 的长期机器身份只有 Hypothesis 节点自己的 `id`（如 `H-0007`）；Canvas node ID 与 H identity 是同一个值，不再创建 `h-0007` 或独立 `hypothesis_id`。非 H 对象需要指向 H 时使用 `hypothesis_ref` 外键。系统不建立 `hypothesis_relation = new|continue` 这一独立 state，也不以 hypothesis/scope 的字符串完全相等作为科学身份判据。
- **Cycle**：针对当前科学前沿的一次 **bounded adjudication transaction**。每个 Cycle 只绑定一个锁定 H，并冻结本轮自己的 `decisive_test`；Cycle 的结束表示本轮事务闭合，不必表示 H 已经 terminal。
- **`decisive_test`**：本 Cycle 要解决的具体判别问题，是科学语义层的测试定义。它说明本轮真正要判别什么、必要 prerequisite 是什么，以及 matching / contrary / inconclusive 结果对当前 H 意味着什么。
- **Cycle criteria**：`decisive_test` 的稳定机器引用锚点，用于 Evidence、Judge objection、readiness 与 deterministic closure 的绑定。它不得创造新的科学命题或形成独立科学层级；长期命名应使用 `C-S* / C-F*` 之类的 Cycle 语义，而不是把 `M-S* / M-F*` 解释为独立 Milestone 对象。
- **`milestone_advanced`**：`decisive_test` 已成功产生有效科学推进，但现有 Evidence 尚不足以 `supported` 或 `rejected` 整个 H。它是 **Cycle outcome label**，不是独立的 Milestone scientific object。

因此目标科学层级是 `Hypothesis → decisive_test → Cycle criteria → Investigation → Judge`。其中 `decisive_test` 承载科学内容，Cycle criteria 只提供机器可引用的判定出口；TheoryMaster 不引入独立的 `Milestone` 科学对象。

## 1.2 Goal-priority hierarchy

**Goal completion has priority over frontier expansion.** TheoryMaster 区分“科学上值得继续的问题”和“当前为了完成 Task 必须解决的问题”；两者冲突时，后者具有主线优先级。Goal-priority 不依赖 goal gap。

Candidate 只有两类可以进入 Qualification：

1. 直接推进尚未满足的 Goal / Research Contract / Final Gate；
2. 解决该推进所**逻辑必需**的科学 prerequisite。

普通 mechanism explanation、optional robustness、confidence strengthening、broader generalization、额外 external validation，以及 purely interesting open science，不能因有趣抢主线。只有逻辑必需 prerequisite 才有主线优先级。

这是一条 **priority / eligibility rule**，不是 SDK 预先硬编码研究路线：SDK 不替 Lead 指定具体 Candidate，也不把 Goal 拆成任务队列或 planner state。Lead 仍负责判断“为完成尚未满足的 Goal / Final Gate，科学上真正必须先解决什么”。下一 Cycle 的 `frontier_proposal` 只读 Goal、Research Contract、Final Gate、Scientific Model 与 Canvas。默认只有 `frontier_status=active` 的 branch tip 属于当前 Research Frontier。`active` 只表示仍有资格参与选题，不等于最高优先级，也不等于下一 Cycle 必须选该 branch。priority 来自 Goal / Final Gate 与 Lead 判断。`Required Context` 不预灌入 Lead Prompt，继续通过稳定导航入口按需读取。

若当前 active branch 都不能合理推进 Goal / Final Gate，不要强行沿 active 继续。回看与未完成 Goal 最相关的历史 Decision，从该节点派生新 Hypothesis；新 H 成为新 active tip。这次派生属于 `frontier_proposal`。`frontier_maintenance` 只修枝，不建新 H。

---

# 2. 权力与职责

## 2.2 Investigator 单一可续批资源预算（2026-10-08）

## 2.1 SDK

SDK 是 Runtime Supervisor，负责：

- Workflow stage、transition 与 investigation executor dispatch；
- 角色调用、epistemic episode、provider/model routing；
- 三档 Codex 模型 profile（1=test、2=codex、3=advanced），并允许 provider 与 per-role route overlay；具体 Master 当前使用什么模型属于运行配置，不写死在设计文档；
- budget、retry、checkpoint 和 recovery；
- 文件边界、sandbox 和 approval gate；
- 机器状态的原子落盘；
- Scientific Model、Canvas 和 TUI 投影顺序。
- versioned Role Contract 的选择、验证、迁移和兼容性判断；
- remaining scientific gap / Judge objection 的合法化与确定性 executor dispatch；
- invocation、external job，以及 SDK-owned artifact manifest / reconciliation ledger。

SDK 不选择科学假设，不解释 Evidence，不代替 Judge 裁决。

Hypothesis identity resolution 不属于 Research Lead Candidate 字段，也不要求 Lead/Challenger 维护 H ID。frontier candidate 必须包含 `hypothesis / scope / why_now / goal_advance / decisive_test`；可选的既有 typed `evolution_operator` 仅描述新科学分支的谱系边，不是 identity relation 字段。Lock 按以下结构协议解析：候选没有 `evolution_operator` 时，若当前 Decision anchor 恰有一个 Hypothesis 父节点，则沿用该节点的 `id`；若没有唯一 H tip（例如首个 Cycle），分配新 ID。候选显式给出有效 `evolution_operator` 时，表示它是从当前 frontier 派生的独立新 H，SDK 分配新 ID，并由 Canvas edge 记录 Decision → 新 H 的演化关系。`repair` 只表示 Qualification candidate 修复过程，不作为 H 演化边；selection repair 保留 parent candidate 的 identity-resolution 结果。多父节点或图谱身份冲突时 fail closed，要求先修复结构，不按字符串相似度猜测。这个默认延续 + typed lineage 边协议不新增 `hypothesis_relation = new|continue`，也不引入额外 Agent call。

### 2.1.1 最小上下文与 Guided Exploration

TheoryMaster 的默认信息哲学是：**SDK 只保证最小必要上下文和可靠入口；Scientific Agent 在 Prompt 指导下自由探索。SDK 不预先枚举“你只能看这些”。**

职责边界如下：

```text
SDK
负责：
- 科学状态
- Stage 边界
- 最小 guaranteed context
- 稳定导航入口
- Evidence / provenance / invocation bookkeeping

Scientific Agent
负责：
- 判断自己还需要看什么
- 搜索
- 打开文件
- 沿 Canvas / report 导航
- 科学推理
```

因此，Stage context 的职责是提供**最小稳定 seed context**，而不是替 Agent 预解析其完整阅读集合、构造 per-turn 文件白名单或枚举全部可读 scientific inputs。每个 Agent 的 active Context 都要遵守 least-context 原则：workflow counter、router bookkeeping、重复 Evidence ID 列表、可由另一个已注入对象确定性推出的摘要、以及 SDK 预选的阅读邻域默认不进入 Agent Prompt；若 Agent 可以通过稳定入口自行定位，则优先给 `*_access` / path / anchor，而不是把内容包预先展开。Agent 可以根据当前 scientific question 按需读取本地文件、前序 Cycle 产物、Canvas 直接关联的 report 与其它相关材料；Prompt 负责约束探索方向，例如优先从当前 candidate / hypothesis 和稳定导航入口开始，避免无目的 repository-wide 扫描、重复读取已充分回答当前问题的材料，或重新发现 SDK 已明确提供的 workflow/runtime state。Reporter 的 Canvas maintenance 这类 authority-bounded 投影阶段是例外：为了防止越权改图，可继续由 SDK 提供 bounded `canvas_focus`，而不是授权全图自由探索。

文件系统可读性不自动等于科学结论成立；但这一点由 Agent 的 source attribution、Evidence/provenance 记录和后续 scientific adjudication约束，而不是由 SDK 在调用前生成 source allowlist。SDK 可以维护执行 sandbox、写入边界、hidden/gold/evaluator 隔离等运行安全规则，但这些规则不得被扩张成 Scientific Agent 的逐文件阅读授权表。

Research Lead 的 Qualification finalization 使用更严格的 **delta-only** 边界。`candidate_selection` 只接收已经由 SDK 筛成 qualified 的候选索引 `qualified_candidates`；正常 continuation 只需要 `id/title`，若因恢复而必须新开 thread，SDK 可在同一字段中补足候选科学内容，但不再注入 `previous_selection`、`candidate_reviews`、`mode/phase` 等重复或 workflow bookkeeping。Agent 只返回 `selected_candidate_id` 与简短 `selection_reason`；本 Cycle 真正测试什么始终来自 selected Candidate 已冻结的 `decisive_test`，禁止在 selection rationale 中再造第二份 test 定义。SDK 将 `selection_reason` 映射为内部 selection/qualification rationale，并继续负责 candidate lookup、Hypothesis identity、Lock 与 criteria。

`selection_repair` 同样只暴露一个最小 `repair_options[]` delta：每个 option 给出 `candidate_id`、便于识别的 `title` 与对应 frozen `blockers[{id, problem, required_change}]`；正常 continuation 不重复发送 parent Candidate 全文，恢复时可在同一 option 内补足不可从 thread 恢复的 parent science。Agent 不再接收并行重复的 `candidate_reviews`、`review_reason`、顶层 `blocking_issues`、`repair_budget/repair_guidance` 或 broad scientific context。resolved 输出必须逐 blocker 给出 `blocker_closures[{blocker_id,resolution}]`，resolved 必须返回实质修订且完整的 `repaired_decisive_test`；不能继承未修订的 parent test。可选的 `hypothesis_clarification` 与 `estimand_or_operational_definition` 只允许澄清原科学目标，SDK 将其中尚未包含的完整文本以绑定定义附入同一个 `decisive_test`，交由一次 Challenger verification 检查一致性和目标边界。该正文随后原样进入 Lock、dossier 和 Investigator/Judge 科学视图；`repair_details` 保留溯源并进入修复复核，但不承担唯一科学定义。不会追溯改写既有冻结 Lock。聚合性的 `why_blockers_resolved` 删除，因为其语义已由逐 blocker closure 完整承载。Parent hypothesis/scope/goal_advance/title、replacement ID/lineage 与 addressed issue IDs 仍全部由 SDK 冻结或派生。

`cycle_synthesis` 采用同样的 least-context 原则，但因为它负责长期科学记忆，只保留三个 Agent-facing 顶层输入：`active_hypothesis`（仅 `hypothesis/scope/decisive_test`；如需携带稳定 H 坐标则用 `hypothesis_ref`）、`judge_decision`（仅 terminal `verdict/rationale`）和 `scientific_access`（`scientific_model_path/evidence_manifest_path/cycle_dir`）。`mode=cycle_end`、完整 Evidence payload、完整 Scientific Model、版本/Goal/provenance 等都留在 SDK runtime/validation 层，不进入 Agent Prompt。Lead 可从这些稳定路径按科学需要自由读取材料；`keep` 不要求读取或回抄完整 Model，`replace` 则必须先读取完整旧 Model 后再返回完整长期内容。当前 synthesis 输出只保留 `conclusions[]` 与 `open_questions[]`；`scientific_model_update` 只含 `action=keep|replace`，且 `model` 仅在 `replace` 时出现。旧的 `learned/invalidated/strengthened/new_anomalies/resolved_questions/new_open_questions/cross_branch_implications` 与 `scientific_model_update.reason` 不再属于当前 Agent contract；历史 artifact 由兼容读取层继续解释。

### 2.1.2 SDK 内部工程边界

SDK 源码按**稳定入口 + 职责模块**组织，而不是把协议、状态机、transport、投影和 UI 继续堆叠进单个大文件。对外兼容入口应保持稳定；内部实现可按职责拆分，但不得借重构改变科学流程、持久化语义或历史 Contract 的解释。

长期遵守以下工程约束：

- **Orchestrator 只负责编排。** Workflow 主体管理 Cycle 生命周期和 transition；Agent invocation/repair、Qualification、Investigation/Judge、state/recovery/Evidence、Scientific Model/frontier 分属独立职责边界。
- **一个概念只有一个机器事实源。** Role/executor、Stage metadata/routing、versioned Contract、execution approval policy、JSON normalization 等不得在多个模块复制常量或平行实现；其它组件只引用权威 registry/policy。
- **副作用与投影分离。** Observability 的 event/state projection、历史恢复/计费、email/audit/repair relay 分开；Canvas 的科学语义 patch 与确定性 projection/layout 分开；backend 的 model routing、SDK transport、test fixture 与 execution policy 分开。
- **兼容 façade 不承载新业务逻辑。** 历史 import/API 可由轻量 façade 继续提供，但新增实现应进入对应职责模块，避免为了兼容再次形成 God Object。
- **结构重构必须行为保持。** 未显式升级 Contract/domain schema 时，不改变外部 API、Contract ref、恢复边界和持久化含义；重大拆分先由 characterization/transition/recovery 测试锁定行为，再迁移实现。

## 2.2 Research Lead

Research Lead 是 Scientific Supervisor，负责：

- 读取 Scientific Model 和 Theory Evolution Graph；
- 审查 frontier，生成或整理 candidate hypotheses；按 §1.2 的 Goal-priority hierarchy，Candidate 必须推进尚未满足的 Goal / Research Contract / Final Gate，或解决其逻辑必需 prerequisite；
- 根据 Challenger review、已有 Evidence、成本和信息增益确定 H；`frontier_proposal` 的候选必须相对上一 Cycle 已建立结论与已裁决 decisive test 带来新的判别信息，不能仅以“再确认一次 / strengthen confidence”为由重复已经 terminal-supported 的同一科学测试；只有出现新的不确定性、不同条件、独立复现风险或其它会改变科学判断的差异时，重复类检验才可成为新 Cycle 的 decisive test，并应在 `why_now / goal_advance` 中明确说明新增信息；当且仅当存在多个 qualified candidate 时执行显式 `candidate_selection`，只有一个 qualified candidate 时由 SDK 确定性选中；
- 在 Hypothesis Lock 前澄清、修复或放弃 candidate；
- Qualification 只输出科学判断：frontier 候选的 `hypothesis/why_now/goal_advance/decisive_test`；Challenger 只输出 verdict/reason/blockers；多 qualified 时 Lead 再输出 `selected_candidate_id/selection_reason`，SDK 将其闭合为内部 selection rationale。SDK 派生 stable ID、title、initial executor 和判据，并始终从 selected Candidate 的 `decisive_test` 冻结本轮局部目标；
- 在 terminal Judge 后完成 Scientific Synthesis，并通过 `scientific_model_update.action=keep|replace` 决定长期 Scientific Model 是否整体更新；
- 在 `cycle_synthesis` 之后执行 `frontier_maintenance`：只对现有 branch tip 给出 `frontier_status` 修枝。`scientific_synthesis` 留在本 Cycle，不写成下一 Cycle 的 `established` / `unresolved` / `boundary` 事实目录，也不生成或选择下一 Hypothesis。下一 Cycle 仍由 Research Lead 的 `frontier_proposal` 自己提出、审查并锁定 H。

Research Lead 不管理 runtime，不代替专业 Agent 获取 Evidence，不覆盖 Judge 的 verdict。

## 2.3 Challenger

Challenger 是 Hypothesis Qualification Reviewer。它在昂贵 Investigation 开始前独立审查 candidate shortlist，重点暴露：

- hidden assumptions；
- counterexamples 和 limit-behavior conflicts；
- identifiability problems；
- causal overclaim；
- 与 Scientific Model 或 Research Contract 的冲突；
- 与已关闭假设、已 terminal-supported decisive test 的科学等价重复，以及没有新增 information gain 的重复检验；
- weak falsifiability 和 weak discriminating power；
- missing evidence 和信息泄漏风险；
- Goal alignment：输入是 Goal、Research Contract、Final Gate 与 `candidate.goal_advance`。科学上优秀不自动进入 Cycle。候选必须满足其一：A. 推进尚未满足的 Goal / Final Gate；B. 是该推进的逻辑必需 prerequisite。不新增 `goal_gap_relation` 或其它 gap 关系字段；对齐结论写入既有 `verdict` / `reason`。

Challenger 按输入 shortlist 顺序对每个 candidate 只给出 `verdict=qualified|revise|reject` 和 `reason`；仅 `revise` 时增加结构化 `blocking_issues[{problem, required_change}]`。`candidate_id` 由 SDK 按输入顺序回绑，不要求 Challenger 回抄。旧的 critical/fatal/conflict/evidence/weakness/recommendation 多清单和 `required_revision` 不属于当前 Qualification 合同。

Qualification 审核的是 candidate hypothesis 与本 Cycle `decisive_test` 的配对：局部检验必须可行、可证伪，且其结果会改变对完整假设中某个必要或有判别力预测的判断；局部阳性结果不能单独证明完整假设，但这一点本身不构成 `reject`。Challenger 还必须把当前 decisive test 与 Canvas 上已经 terminal adjudicated 的测试比较：如果只是科学等价地重复一个已 supported 的检验，且没有新的不确定性、条件、独立复现风险或其它新增判别信息，不得 `qualified`。若同一 hypothesis/scope 内可以改成真正增加信息的测试则 `revise`；若候选的唯一价值就是重复已裁决工作则 `reject`。

尚未解决的 prerequisite 不能仅因尚未推导或操作化，就成为 qualification blocker。若它已明确纳入冻结的 `decisive_test`、Investigator 能在读取目标响应数据前解决，且不需要改变 Hypothesis、scientific scope、estimand 或 allowed inputs，该 prerequisite 属于 Investigation 工作；Challenger 不得仅据此给出 `revise` 或 `reject`。只有下列情形允许把 prerequisite 判为 qualification blocker：缺失定义使 Hypothesis 的科学含义不确定或不唯一；操作定义可在看到目标响应/结果后调整，造成 response leakage / post-hoc 定义；decisive_test 没有能改变 Hypothesis 判断的真实可证伪结果；解决它必须改变 Hypothesis、scope、scientific estimand 或 allowed inputs；或它实际是一个必须先独立裁决、否则当前 Hypothesis 无意义或不可执行的新科学命题。Challenger 必须区分“尚未完成的科研工作”与“科学命题本身未定义清楚或无法合法执行”。

可在不改变假设范围下修复的阈值、方向、对照或操作定义缺口仍可给出 `revise`，但不能把单纯尚待 Investigator 完成的 prerequisite 混入 blocker ledger。Challenger 必须区分局部进展与完整假设的 terminal 裁决。

Challenger 不选择 winner，不修改 candidate，不关闭 Cycle，不更新 Scientific Model。

Qualification 的 Research Lead 工作在 Canvas 层。下一 Cycle `frontier_proposal` 的科学读取只有 Goal、Research Contract、Final Gate、Scientific Model 与 Canvas。SDK 对 Lead 提供的最小入口是 `goal`、`research_contract`、`final_gate`、`scientific_model_summary`、`canvas_access{path, entry_node_id?}`，以及必要时的 `qualification_feedback` / `lead_literature_result` 和 workspace anchors。不注入上一 Cycle `continuation_context` 的 `scientific_synthesis`、`established`、`unresolved`、`boundary` 或 goal gap。默认当前 Research Frontier 只含 `frontier_status=active` 的 branch tip；`active` 不等于必须选中该 branch。`required_context` 仍可作为 Research Task 持久化字段供其它阶段或人工审计使用，但不再整包注入 Lead 的 active frontier/repair prompt；`research_reading`、`hypothesis_catalog`、`prior_cycle_summary`、`canvas_graph_overview`、`current_node_neighborhood` 与 `lead_literature_detour_used` 也不作为 Lead 的预制阅读包。隐藏 Hypothesis science catalog 不得成为跨 Cycle 科学事实源。Lead 必须先从 `canvas_access` 的最新/当前 frontier 入口开始，按科学相关性而非时间顺序阅读，只有在会改变候选生成或 Qualification 判断时才沿 Canvas 关系向外扩展；历史节点只用于 provenance、替代路线、失败路线和未决矛盾。SDK 不替 Lead 预选完整相关邻域，Lead 也不应为了重建全部历史而遍历 Canvas；当继续阅读已不太可能改变当前 Lead 决策时应停止探索。若 qualification 的科学判断确实依赖某个具体 artifact，由当前 Scientific Agent 沿 Canvas / workspace 的直接相关引用自行读取必要材料；这种读取是有目的的科学核验，不应退化成无目的 repo 扫描。

## 2.4 Investigation Executors

- **Investigator**：冻结 Hypothesis 下唯一的内部科研执行角色。它根据当前最关键的 unresolved scientific question 自主组合 `theory-investigation` 与 `numerical-investigation`，同一 Investigation Round 内允许多次理论推导 ↔ 数值计算 ↔ 本地 source review 往返；方法切换不是 workflow transition。新 Cycle 不建立 Evidence Requirement queue。Investigator **没有 per-turn 文件读取白名单**：SDK 不从 Required Context 自动生成本地文件索引；Agent 可按当前科学问题自由探索和读取本地文件、代码、前序 Cycle 产物及仓库材料，但应优先沿当前科学问题与直接引用有目的地展开。Investigator 的当前 Finding Contract 只要求自包含科学 `statement`；不单独输出 `scope` / `limitations` / `evidence_kind` / `source_refs`。科学方法、数据集资格、来源事实和适用边界在 `statement` 中自然交代；执行文件溯源通过 SDK Invocation/Artifact Manifest 自动维护。结构化 `provenance` 暂保留为可选的特殊受保护 Evidence 身份声明，但只能登记来源资格，不能代替 Judge 的科学判断。若 Hypothesis Lock 含合法 `cannot_repair` handoff，SDK 另将有限的 `cannot_repair_reason` 与原始 deferred blocker 条目作为条件性 `qualification_handoff` 提供给 Investigator，帮助其定位冻结目标中的未完成工作；它不是修改 Hypothesis、scope、estimand、allowed inputs 或 `decisive_test` 的授权。**不报告 artifact path、artifact role、hash、Evidence ID、criterion/objection 状态或其它 Judge-facing / workflow bookkeeping**。一次 invocation 实际创建/修改的文件由 SDK 在 invocation 边界自动生成 artifact manifest，并通过 `producer_invocation` 与 Evidence 间接关联。

  **Investigator 读写根必须显式分离。** 当前 Cycle `workspace/` 仍是唯一默认写入根与计算产物落盘边界；但 SDK 在 invocation 的 runtime/system handoff 中必须把 `task_root` 暴露为稳定的**科学读取导航起点**，使 Investigator 在需要 benchmark、数据、task-local source、research plan 或其它已物化科研材料时优先从 Task 根目录做聚焦搜索。这个 `task_root` 是导航能力，不是新的 scientific contract 字段，也不得恢复 Required Context 自动文件解析、source manifest 白名单或预制文件清单。当前 Cycle 的 `evidence_manifest.json` / `coverage_matrix.json` 是 Evidence Registry 的派生审计投影：其中 `records=[]` / `active_evidence_ids=[]` 只表示“本 Cycle 尚未登记可裁决 Evidence”，**绝不允许推断为 task-local scientific inputs 不存在或不可读取**。只有在从 `task_root` 做过与冻结 objective 相关的有目的查找后仍无法定位所需材料，Investigator 才可报告本地 scientific input gap。
  **正式科研提交边界（2026-10-09）**：Investigator 应独立持续完成当前冻结 decisive_test 所需的可执行前置推导和检验；局部定理、有限 toy check、源码入口核查不是正式研究交付，不能仅因此提交 findings[]。只在获得可独立科学裁决的证据（支持、反证或实际执行检验后的不可判别结果）时，一次性提交 findings[]。三个 Investigator Stage 的退出语义一致：Judge 的反对意见要求真实科研补足，而不是下一份局部进展报告。local_source Finding 仅用于新建立、可溯源、对冻结科学判别有实质作用的本地事实；普通阅读不是 Finding。scope 与 limitations 均按实际必要性可选，未提供 scope 不得推断为 H 全范围，limitations 用于说明成立结果的限制而非剩余任务清单。没有 disposition、ready_for_judge 或额外自检 Stage。

  **Investigator → Evidence → Judge（2026-10-09）**：Investigator 正式 findings[] 经 SDK 的确定性合同校验、原文保存、Evidence ID/H 归属、来源身份及文件完整性登记后，直接交独立 Judge。Investigator 路径不再运行 B-layer semantic annotator，不再由 B 对 supports/contradicts 等关系、criterion 关联、Judge objection 关联进行预分类，也不按这些语义标签屏蔽科学 Findings。SDK 只排除非 Investigator/Literature 科学记录、失活或技术完整性不合格的证据；Judge 在冻结 H 与 decisive_test 下阅读全部有效科学 Findings，独立判定相关性、科学真假、是否达成裁决条件。历史 Evidence 的旧 relation/criterion 标签留在 Registry 供兼容读取，但不作为 Judge 的预先科学评价；Literature 的来源验证通道不在本次删除范围。Coverage Matrix 不再以缺少 Investigator relation/criterion_id 为由推断无科学成果，历史分类统计仅是旧版审计投影而非科学权威。

- **Literature**：只处理需要新增**外部学术知识**的问题，不承担普通本地 retrieval。Investigator / Judge 可在 Investigation/Judge gap 中触发；Research Lead 仅可在新 Cycle 的 `frontier_proposal` 因外部 scholarly gap 无法负责任地完成方向决策时触发一次有界旁路。所有角色仍应自行读取本地模型、Evidence、代码、数据、笔记与已有 PDF。
- Theorist / Numerical 只作为历史 persisted Cycle 的兼容角色名保留，不属于新 Cycle 的 current role registry。

Literature 有三类任务：`landscape`、`targeted_source`、`novelty_check`。每次调用都必须说明 external gap、触发角色和期望获得的 scholarly evidence；相同 gap 在没有 new search lead 时不重复搜索。Lead 旁路只开放 `landscape | targeted_source`，不开放 `novelty_check`，且同一 Lead stage 最多一次；Literature 完成后必须返回原 Lead stage，而不是进入 Investigation。Lead 旁路输出作为 `frontier grounding` 持久化和审计，不自动注册为当前 Cycle 的 adjudicative Evidence，尤其不得在 terminal Judge / Scientific Model commit 之后反向改变本 Cycle Evidence Registry。只有通过正常 Evidence 路径进入冻结 Hypothesis 裁决的来源才升级为严格 Literature Evidence。

**Literature 的 external 只指 scholarly external。** 论文、综述、预印本、DOI/citation graph 属于 Literature；benchmark 文件、外部仓库、网页、数据集 provenance、未提供的 prediction template 等非学术外部记录属于 `external_artifact` resource gap。当前 SDK 没有通用 Web/Repository artifact transport，因此这类 gap 必须以 `external_artifact_source_unavailable` 做可审计的 non-scientific closure，保持 Scientific Model 不变，也不得拿 scholarly transport 代搜。未来若接入专门的 Web/Repository capability，只需把这一 gap 映射到对应 transport，不改变 Literature 的职责边界。

专业 Agent 可提出 provisional evolution idea，但不得在当前 Cycle 修改 Main Hypothesis。

## 2.5 Hypothesis Judge

Hypothesis Judge（以下亦称 Branch Judge）是**当前 Investigation Result 的独立科学评价者**，不是 Evidence auditor、文件审计员、Router，也不是第二个 Investigator。Judge 的 Agent-facing context 必须遵守 least-context：SDK 只投影冻结 H 的 `hypothesis / scope? / decisive_test`，当前 `investigation_result` 中真正用于科学判断的 finding 内容，以及必要时的 semantic `prior_objection` / `prior_adjudicated_context`。不得把 `hypothesis_ref`、`C-1`、Cycle/round/invocation 计数、Evidence/objection ID、executor/source-gap/routing 元数据、artifact path/hash、完整 Evidence Manifest、Coverage Matrix、transcript 或逐条 assessment ledger 暴露给 Judge。

`investigation_result` 给 Judge 的投影以每条自包含科学 `statement` 为主体；只附加 SDK 已确认的历史/当前来源新旧性、必要的特殊 `provenance`、Literature 的已核验 `source_access` 等非科学判决信息；旧 `scope/limitations` 仅在历史兼容读取时保留，不再生成分类字段或来源列表；`schema / investigation_attempt / rounds / criterion_ids / objection_ids` 属于 SDK bookkeeping，必须在 Agent boundary 前删除。reassessment 时的 `prior_objection` 只保留上一轮尚有效的 `rationale` 与 `requested_resolution`。`prior_adjudicated_context` 只表达同一 H 已经完成 adjudication 的科学结果：每个历史条目保留 `decisive_test / verdict / established_result` 以及实际存在的 `scope / limitations`；不得重新嵌入历史 Evidence ledger、Cycle/Hypothesis ID 或 adjudication-role bookkeeping。

Judge 只裁决：

- `supported`；
- `rejected`；
- `milestone_advanced`；
- `open`。

Judge 先判断：**当前 Investigation Result 是否完成本 Cycle 的 decisive test？** 若完成，再结合 `prior_adjudicated_context` 中同一 H 已经裁决过的局部进展，判断累计科学记录是否已足以 `supported / rejected` 整个 H。此前 `milestone_advanced` 可作为已裁决的局部科学推进；此前 `open` 只能作为未终结上下文，不能因为出现在 history 中就自动升级为支持 H 的 Evidence。Judge 不重新打开任何当前或历史原始 artifact 去复做 Investigation；它可以利用已有科学背景理解 supplied result，但不得把自己额外发现、尚未经过 Investigation/Literature → Evidence closure 的新信息直接升级为 terminal Evidence。

证据不足不是独立的 terminal scientific verdict。若且仅若仍缺一个保持 frozen H 与 decisive test 不变、科学上必要且有判别力的局部结果，Judge 可用 `verdict=null` 返回**一个**最小 objection。current v7 Agent contract 只要求 `requested_resolution`，以及一个二值 `needs_external_scholarship`：`false` 表示该缺口可交给 Investigator 自主选择 theory / numerical / local-source 等内部方法，`true` 表示确实需要新增外部 scholarly evidence。Judge 不再返回 `criterion_id`、重复的 `reason`、六分类 `scientific_gap.kind`、`search_target`、`expected_external_evidence`、`new_search_lead`、executor 或 rework plan。

SDK 在 Judge closure 中确定性补全机器语义：当前 Cycle objection 自动绑定唯一 `C-1`；内部 `reason` 直接使用 Judge `rationale`；`needs_external_scholarship=false|true` 分别映射 `investigator|literature`；SDK 派生 `objection_id / source_gap_id / rework_plan`，并负责 Literature task/search 结构、去重、budget、executability 与最终 authorization。Judge 不替 SDK 分类 theoretical/numerical/local-source，也不选择 landscape/targeted/novelty；若路由到 Literature，当前 Judge rework 只需把 `requested_resolution + rationale` 作为 external scholarly task 的最小语义种子，由 Literature/SDK 按其职责继续搜索。若 rework 未授权、重复外部 gap 无新可执行信息、资源不可用或 budget exhausted，SDK 以 protocol/resource 原因 terminalize 为 `open`，不得把这些运行限制伪装成反对 H 的科学证据。

`milestone_advanced` 表示本 Cycle 冻结的 `decisive_test` 已成功产生有效科学推进，但现有 Evidence 尚不足以使整个 H 获得 `supported` 或 `rejected`。它是 Cycle outcome label，不代表系统中存在一个独立的 Milestone scientific object。Judge 不得把局部阳性结果升级为完整 H 的 `supported`，也不得仅因完整 H 尚未完成而拒绝记录本轮有效推进。局部反向或零差异是否足以 `rejected` 整个 H，取决于冻结 `decisive_test` 对该结果的必要性与判别力。

Evidence Registry 仍是每个 Cycle 的 durable scientific record authority；新正式 Evidence 由 SDK 自动绑定 `hypothesis_ref + cycle_id + criterion_id`，其中 `hypothesis_ref` 必须解析到 Hypothesis 节点自己的 `id`。新 Cycle 只有一个稳定 criterion ID `C-1`，它仅锚定本 Cycle 的 `decisive_test`；这个机器锚点不再要求 Judge 回抄。跨 Cycle 聚合键为 `(hypothesis_ref, cycle_id, criterion_id)`，不会因不同 Cycle 都使用 `C-1` 而串绑。旧 Registry 的 `hypothesis_id` 仅作为有依据的兼容读取别名；新写入统一使用 `hypothesis_ref`。SDK 不建立 global Evidence Registry，也不复制旧 Evidence；需要跨 Cycle 裁决时，只读构造 compact history。Invocation Ledger / artifact manifest 继续负责执行与复现审计，原始机器记录不进入 Judge 的认知负担。Hypothesis Judge 不判断整个研究 Goal 是否完成，不更新 Scientific Model，也不选择下一个 H。

## 2.6 Reporter

Reporter 把已发生的 Qualification、Investigation Result、必要的 Evidence provenance 摘要、Judge verdict、Scientific Synthesis 和 Model Delta 组织成 Cycle Report；artifact manifest 仅作为文末机器复现索引，不进入科学叙事主体。

Reporter 不创造 Evidence，不修改 verdict，不选择或生成下一个 H，也不评估整个研究 Goal 是否完成。Cycle 尾部固定为 terminal Judge → Research Lead `cycle_synthesis`（科学综合与 Scientific Model keep/replace）→ Scientific Model commit → Research Lead `frontier_maintenance` → Reporter。SDK 持久化的跨阶段 runtime 只保留 recovery pointer、当前 Cycle candidate runtime、`continuation_anchor_decision_id` 与 invocation/phase 引用；这些值只能指向 Canvas 或当前 Cycle artifact。同 Cycle recovery 可以暂存 `scientific_synthesis`；Cycle 闭合后不得把它当作下一 Cycle 的科学事实源。SDK 没有下一 Hypothesis 的 authority。Scientific Model 提供跨 Cycle 科学上下文，但不保存结构化 evolution candidate 列表。

### Human-first 报告原则

所有面向人的科研文档统一遵守：**人类可读科学叙事在前，机器索引在后**。正文回答研究问题、结果、证据、意义、边界与下一步，不直接倾倒 JSON/YAML、schema、contract、Agent delivery、legacy ModelPatch 或 runtime 对象。

机器审计与恢复信息仍保存在 `research_state.json`、Evidence Registry、Scientific Model update/staged/out、events 等结构化文件中；其中 `research_state.json` 只承担 workflow / recovery，不是跨 Cycle 科学事实源。人类报告文末只保留**机器审计与复现索引**，不复制完整机器对象。Cycle Narrative 是阅读入口；JSON/JSONL 是机器审计入口。跨 Cycle 科学认识只以 Research Plan / Scientific Model 与 Canvas 为准。

---

# 3. 科学流程

## 3.1 总体流程

TheoryMaster 的长期运行结构分为四个概念层级，图和文档必须始终区分：

1. **Scientific Agent**：稳定的科学/裁决角色；当前为 Research Lead、Challenger、Investigator、Literature、Judge、Reporter。
2. **Stage**：同一个 Scientific Agent 在不同 scientific phase / mode / current task 下承担的阶段职责。Stage 不是新的 Agent，也不是独立进程。
3. **Auxiliary semantic model**：`Semantic Annotator` 是非科学 B-layer closed-choice 辅助模型，只能在 SDK 给定的封闭选项中做语义映射；它不是第七个 Scientific Agent，也没有 frontier、Evidence authoring、routing 或 state-transition authority。
4. **SDK deterministic layer**：Hypothesis Lock、Deterministic Closure、Evidence Registry、Capability Router、Readiness、Scientific Model Commit、Canvas Projection、Checkpoint / Recovery 等可验证流程机制。这些不是 Agent stage。

当前真实 Cycle 主路径与 `workflow.py` / `workflow_qualification.py` / `workflow_investigation.py` / `workflow_science.py` / `reporting.py` 一致：

```text
Research Goal
    ↓
Research Plan（Goal / Research Contract / Final Gate / Scientific Model）
                                                ← 压缩后的全局科学认识
Canvas（frontier_status = active 的 branch tips）
                                                ← 可追踪探索结构与当前 frontier
    ↓
Research Lead / frontier_proposal
    ├─ external scholarly blocker → Literature（最多一次）→ 回到同一 Lead stage
    ↓
Challenger / candidate_review                    ← 每个 Cycle 只做一次完整 adversarial audit
    ├─ no qualified + revise → Research Lead / selection_repair
    │                           ↓
    │                         SDK validates one frozen-blocker repair; 不重新打开 Challenger
    ↓ qualified set
    ├─ exactly one → SDK deterministic selection
    └─ multiple    → Research Lead / candidate_selection
    ↓
SDK / HYPOTHESIS LOCK
    ↓
Research Lead / hypothesis_display（仅最终选中的新冻结 H；可失败回退）
    ↓
SDK / Investigation Router
    ↓
Investigator | Literature                        ← internal work / external scholarly gap
    ├─ external_artifact → SDK resource-gap closure（当前无通用 Web/Repository transport）
    ↓
Scientific Turn（A）
→ Semantic Annotator（B；仅必要的 closed-choice 语义映射）
→ SDK Deterministic Closure（C）
→ Evidence Registry + producer invocation linkage
→ SDK Readiness + next-executor routing
    ├─ continue → Investigation Router
    ↓ ready
SDK materializes compact Investigation Result
    ↓
Judge / terminal_assessment（只评价 Investigation Result）
    ├─ one scientific objection → SDK validates/dispatches bounded rework → Investigation Router
    ↓ terminal verdict
Research Lead / cycle_synthesis
    ↓
SDK / Scientific Model validation + atomic commit
    ↓
Research Lead / frontier_maintenance          ← 只修 branch-tip frontier_status
    ↓
Reporter / report
    ↓
Reporter / canvas_maintenance
    ↓
SDK / Canvas commit
    ↓
Cycle CLOSED
    ├─ terminal Judge verdict != supported
    │      ↓ invocation budget / operator continuation
    │   Next Cycle / frontier_proposal → qualification → Hypothesis Lock
    └─ terminal Judge verdict == supported
           ↓ SDK event trigger（不增加 Agent request 字段）
       Judge / goal_completion [fresh]
           ├─ passed     → Task COMPLETE；当前 active/parked branch tips 全部 closed
           └─ not_passed → 允许下一 Cycle；不创建 planner state
                          verdict + rationale 投影到当前 Decision
```

跨 Cycle 科学权威只有两层：Research Plan / Scientific Model 是压缩后的全局科学认识；Canvas 是可追踪的探索结构与当前 frontier。`frontier_state.json` / `research_state.json` 只是 workflow runtime，不是第三层科学权威。

### 3.1.1 Agent → Stage 运行表

`stage_registry.py::STAGE_CARDS` 与 `contract_stages.py::PHASE_CONTRACTS` 定义当前运行 surface；两者由架构测试要求一一对应。当前结构为：

| Agent | 当前 Stage |
|---|---|
| Research Lead | `frontier_proposal`、`candidate_selection`、`selection_repair`、`cycle_synthesis`、`frontier_maintenance` |
| Challenger | `candidate_review` |
| Investigator | `investigate`、`integrate_literature`、`judge_rework` |
| Literature | `landscape`、`targeted_source`、`novelty_check` |
| Judge | `terminal_assessment`、`goal_completion`（仅由 `supported` 事件触发） |
| Reporter | `report`、`canvas_maintenance` |

`Literature / grounding`、`Literature / targeted_followup`、`Literature / judge_rework` 仅保留用于历史 persisted Cycle 的兼容恢复，不属于新任务的当前 Literature task taxonomy。

`local_source_review` 现在只是 Investigator 内部 method，不是 workflow gap、独立 Stage 或冻结 Requirement。它只检查任务已提供的本地文件、数据、metadata、benchmark/source code 与 provenance，；常规查阅不自动产出 Finding，只有新建立的、对冻结科学检验有判别意义的事实才形成 Finding，不再要求单独声明“本地来源型”。具体来源事实应在科学陈述或科研文件中自然说明，由执行记录自动追溯；Literature 的单数 `source_ref` 属于独立的外部学术来源验证合同，不属于本次删除的 Investigator 引用列表。它不是 Literature 检索。

一个 Stage 的权威选择由 `stage_for(role, state, context, contract_role=...)` 根据 `role + phase + mode + current task` 决定，而不是根据“第 N 次调用”推断。Hypothesis Lock、Readiness、Scientific Model Commit 等 SDK 节点不得伪装成 Agent stage。

### 3.1.2 阶段化增量 Prompt 与输出合同边界

线程承担角色长期记忆，单次 turn 只承担当前科学阶段的增量任务。信息严格分四层：`Role Core → Stage Card → Output Contract → on-demand Skill/Evidence`。Role Core 只放跨 thread 生命周期稳定的 Purpose/Authority/Boundary；Stage Card 每次调用才给 `Current objective / May do / Must not / Exit-handoff`；Contract 只定义当前 JSON；Skill 与材料只有本 stage 真需要时才读取。阶段由 `role + phase + mode + current task` 决定，禁止用“第 N 次调用”推断职责。Research Lead 的 Cycle 内阶段只有 `frontier_proposal`、`candidate_selection`、`selection_repair`、`cycle_synthesis` 与 `frontier_maintenance`。`frontier_maintenance` 是本 Cycle 收口时的 Canvas 修枝；`frontier_proposal` 是下一 Cycle 决定往哪长。Cycle closure 不调用下一轮的 `frontier_proposal`。Investigator、Literature 与 Judge 同样使用阶段合同。

**分层单一归属（Prompt 去冗余不变量）**：同一条规则只在一层出现，越靠后的层越窄。

- **Role Core** 只写该 Role 在其所有 stage 都成立的 Purpose / Authority / Boundary。只服务单一 stage 的方法规则（如 Lead 的 shortlist/qualification 原则、Scientific Model 编辑原则）属于对应 Stage Card；fresh-thread stage（如 `cycle_synthesis`、`goal_completion`）不得因 Role Core 而背负其它 stage 的规则。一个 Role 下认识论任务完全不同的 stage（如 Judge `terminal_assessment` 与 `goal_completion`）在 Role Core 中只保留共同的独立裁决边界。
- **Stage Card** 是当前 stage 科学判断规则的唯一归属。Procedure 只写 Stage Card 未覆盖的操作顺序（读什么、先后、何时停），不得重述 qualification / adjudication 哲学。
- **Contract note** 只解释 schema 字段语义与条件字段关系（如 tagged union、`verdict != null → objection omitted`），不教方法、不重复 Stage Card 的 must-not。
- **Skill** 承载按需方法学（如数值正确性检查层级），由 Stage Card / 路由规则决定何时加载；Role Core 与 Stage Card 只写触发条件，不复制 Skill 正文。
- **Runtime 强制约束**（sandbox / approval handler 已确定性执行的进程控制、仓库写保护）只在 developer instructions 中陈述一次，供 Agent 正常工作参考；不在每个 turn prompt 再注入第二份。
- **规则写成可泛化原则，而不是历史事故补丁**：用“Candidates 必须是对同一科学问题的可竞争解释，而非顺序执行步骤”这类原则，替代逐条点名过去某个 benchmark 出现过的错误形态；用“新 decisive test 必须对当前 H 带来判别性新增信息，纯 confidence strengthening 不构成新 Cycle，除非存在新的不确定性来源”统一表达重复测试、更多 split、外部泛化等情形。

新 thread 只注入 role core、Cycle metadata 与 optional-skill **白名单/路径**，Skill 正文按需读取。thread 的最外层生命周期是 Cycle-scoped；跨 Cycle 的科学认识只通过 Research Plan / Scientific Model 与 Canvas 延续。Evidence 与 Cycle report 是本轮记录，runtime handoff 只保留 recovery pointer，二者都不是第三套跨 Cycle scientific authority。Cycle 内按 **epistemic episode** 管理，而不是机械地“每 role 一个 thread”：Research Lead qualification/selection 复用，cycle synthesis fresh，`frontier_maintenance` 与 `cycle_synthesis` 同一 episode；Challenger 每次独立 review fresh；Investigator 的连续内部科学 episode 优先 resume；每个 external Literature task fresh；Judge branch adjudication→reassessment 复用；`Judge / goal_completion` 永远 fresh，不继承 H-level Judge thread；Reporter report→canvas maintenance 复用。

#### 两层 Skill Registry

Optional Skill 分为两个作用域，二者都只是**按需能力**，不是 Agent/Stage：

- **System-level TheoryMaster Skill**：位于 `theory_master/resources/skills/`（Python 包内的唯一权威资源目录），由 `theory_master.skills.ROLE_SKILLS` 定义，是所有 Master 共享的系统能力白名单。
- **Task-local Master Skill**：位于该任务 config 所在目录的 `.theory-master/skills/<name>/SKILL.md`，并由同一个 `theory-master.config.json` 的 `task_skills.{role}` 显式启用，只对该 Master 生效。

运行时 `role_skills(..., task_config_path=...)` 合并两层并保留 `scope=system|task`；task-local Skill **只能扩展、不能 shadow 同名 system Skill**。`TaskSpec`/`validate` 在运行前校验两层路径，developer instructions 分组暴露两层白名单，`role.started` 与 `SKILL_LOADED` 记录 scope。引擎不得用 `task_id` 硬编码某个专用 Skill；具体任务通过自己的 config 声明。

Agent 输出必须先通过机器合同边界，流程固定为：

```text
raw response
→ JSON parse
→ minimal phase contract validation / semantic reference validation
→ same-thread format repair at most once per repair class
→ validated Agent scientific output
→ Semantic Annotator（仅需要 closed-choice 语义判断时）
→ SDK Deterministic Closure（ID / refs / routing metadata / deterministic wiring）
→ Registry / Investigation Result projection / Router / Scientific state transition
```

JSON parse repair、contract repair 与 Judge scientific rework 是三个独立预算。JSON parse repair 最多一次，contract repair 最多三次；前两者只修机器格式，不改变科学结论，不增加 `judge_rework_count` / `scientific_rework_count`。`cycle_anomalies.jsonl` 是当前 Cycle 的统一异常登记表：任何确定性 JSON normalization、JSON parse repair retry 或 contract repair retry 一旦触发，都追加 `severity=minor` 记录；任何最终进入 `NEEDS_ATTENTION` 的停止追加 `severity=attention` 记录；任何 `FAILED / run.failed` 系统故障追加 `severity=error` 记录。记录至少包含稳定 `incident_id/incident_key`、Cycle、source event、role/stage/invocation/thread（若适用）、attention/error code、错误摘要、repair attempt、是否产生额外 LLM 调用，以及自动修复 handoff 的状态/target/model/reasoning effort。登记本身只属于 observability，不改变 scientific routing、`protocol_health`、Judge verdict 或 Canvas authority。已被 SDK 成功修复的 minor JSON/contract anomaly 只登记，不再启动第二个 Codex。耗尽对应预算后，Cycle 才以 `needs_attention / contract_repair_failed` 安全收口，并保存 raw/repaired outputs、JSON-Pointer errors、thread ID、stage 和 recovery prompt；不得把非法输出继续传到 Scientific Model 或其它权威状态。
Judge `terminal_assessment` 的最小合同采用互斥两模式：terminal verdict（包括 `open`）时 `objection=null`；请求一次局部科学返工时 `verdict=null` 且只给一个 `objection={criterion_id, reason, requested_resolution, scientific_gap?}`。Judge contract **不含** `evidence_assessments`、artifact/path/hash、`target_executor`、`required_evidence_ids`、`required_artifact_paths` 或完整 rework plan；这些字段不得通过 contract repair 重新引入。

`Judge / goal_completion` 是**事件触发的任务级终局审核**，不是每个 Cycle 的固定尾部成本，也不参与规划。SDK 不向任何 Agent 增加 `goal_gate_request` / hint 字段；唯一触发条件是当前 `Judge / terminal_assessment` 已给出 `verdict=supported`。`milestone_advanced | open | rejected` 均不触发。触发信号只先记在 SDK；必须等当前 Cycle 的 terminal scientific closure、Research Lead synthesis、Scientific Model commit、`frontier_maintenance`、Reporter report、Canvas maintenance 与 Canvas commit 全部完成后，才执行 fresh `Judge / goal_completion`。

Goal Judge 的 guaranteed prompt context 只包含两个稳定入口：`research_plan_access`（完整 `research_plan.md`）与 `canvas_access`（完整科学 Canvas）。这里不再引入单独的 `Goal Contract` 字段或对象；`research_plan.md` 中已经存在的 **Goal / Research Contract / Final Gate / Required Context** 四部分共同构成 Goal Judge 必须逐项阅读的任务级完成合同。Stage Card 必须要求 Judge 先读完整 research plan，明确区分“当前 Hypothesis 已被 supported”与“原始 Research Goal 已完成”，再用 Canvas 核对已完成的科学轨迹是否覆盖 Goal Contract 的每个 completion-critical 要求。Canvas 中存在 `supported` Decision 只能证明对应 H/Cycle 的科学裁决，绝不能单独推出 Goal completion。若 Goal/Research Contract 要求产生 prediction、transformation、指定结果或其它可检查 deliverable，而 Canvas 本身不能证明该 deliverable 已经实际获得并满足 Final Gate，则 Judge 必须沿 Canvas/plan 已给出的直接线索做最小必要的本地读取来确认，不能因当前 H supported 而默认通过；同时仍禁止无目的广搜。SDK 不另行拆包 `goal / research_contract / final_gate` 字段，不新增 Goal-level 输入字段，也不预枚举 artifact 清单。

Goal Judge 的判断程序必须保持 task-level：先从 Goal / Research Contract 提取“完成这个任务实际上必须成立的条件”，再用 Final Gate 判断这些条件是否已经被当前 trajectory 的事实满足，并用 Required Context 只解释任务材料边界；普通未来科学问题、额外 robustness、外部泛化或超出 Goal scope 的可选研究不得作为 `not_passed` 理由。相反，Cycle/Hypothesis 的 `supported`、局部 benchmark 指标变好或某个 decisive test 成功，也不得替代对原始 Goal deliverable 的核验。

`goal_completion` 只输出 `passed | not_passed` 与必需的 `rationale`。不再产出 gap，也不把判断写成下一步计划、requirement ledger 或 artifact inventory。`rationale` 必须简洁说明现有 trajectory 为何已经满足、或尚未满足 Goal / Research Contract / Final Gate。Goal Judge 不提出 Candidate、不修改 Scientific Model、不重做 Investigation、不改变既有 Branch Judge verdict，也不创建 planner state。`passed` 时 SDK 确定性把 Task 标为 `COMPLETED`，禁止再分配新 Cycle，并把当前所有 `active` / `parked` branch tip 的 `frontier_status` 改为 `closed`。`not_passed` 时允许下一 Cycle；SDK 只把 `goal.not_completed` 记为 `verdict`、`rationale`、`source_cycle`。两种结果的判断与理由都可见投影到当前 Decision 正文。Scientific Model 中仍存在普通 open question **不等于** Final Goal 未完成，Goal Judge 只按 `research_plan.md` 的原始 Goal / Research Contract / Final Gate 裁决。

### 3.1.3 Agent 输出最小化与三层职责分离（演进原则）

当前运行合同继续遵守最小认知边界：Research Lead qualification/synthesis、Challenger、Investigator、当前 Literature task、Judge 与 Reporter Canvas authoring 只暴露科学/语义字段；SDK 在 `contract_closure.py` 完成确定性 closure，并把原始 Agent 输出与 closed output 一同写入 `semantic_closure/` 供审计。Investigator 不再承担 artifact path bookkeeping；Judge 不再承担 Evidence/artifact audit。**单次 Agent turn 应围绕一个主要认知目标设计，而不是围绕“填完一个大 JSON”设计。**

**通用原则：Scientific Content First, Presentation Second（科学内容先行，展示表达后置）。** 它与现有的**渐进披露（Progressive Disclosure）**和 **Primary Cognition First** 相辅相成，但职责不同：渐进披露控制输入材料与 Skill 的加载时机；主要认知优先控制科学 A 层只做最重要的科学决策；科学先行、展示后置则约束**输出生成顺序和权威边界**。先产生、审查并冻结完整科学内容，再根据确有阅读价值的场景生成标题、摘要、报告或 Canvas 文案。展示层不得反向修改 Hypothesis、scope、decisive_test、证据、Judge verdict 或 Scientific Model 的科学认识，不得用字数限制或文风目标压缩掉必要科学限定。

展示后处理不是默认的 B 层 Semantic Annotation：B 仅作限定候选内的封闭语义映射；生成自由文本可由原科学角色的受限展示子任务或已授权 Reporter 完成。SDK 的 C 层负责确定性格式、稳定 ID、布局、版本、文件链接与持久化；不要求每个 Stage 都执行 A/B/C 三次模型调用。展示增强必须按需、最小上下文、仅处理最终确定的对象，支持失败回退，不能使已锁定的科学研究无法继续。

**Hypothesis 首次接入规则。** `Research Lead / frontier_proposal`（A）只生成 1–3 个候选的科学命题、范围、判别检验及必要谱系关系；Challenger 审查后锁定被选中的 H。SQLite 新 Cycle 的 H 首次进入 `Research Lead / hypothesis_display`，仅接收 frozen hypothesis/scope/decisive_test，以独立简短提示词返回 `canvas_title`/`canvas_summary`；同 Cycle 恢复已生成的兼容文案无需重复生成。SDK 仅将文案附在 Hypothesis Canvas 节点与 dossier 的展示区域，完整冻结科学文本原样保存，非科学展示失败回退默认中性标题，不影响 Investigation 或后续 Judge。Stage 独立于 B closed-choice；其合同不能创建或改写科学对象。

A/B/C 只用于确有语义闭包价值的地方。Research Lead qualification 的 A turn 直接返回最小 scientific candidate fields（hypothesis / scope / why_now / goal_advance / decisive_test，以及所需的来源关系），不产生 Canvas 文案、evidence plan，也不经过 requirement-kind B 分类；C 只生成 stable candidate ID 等确定性 metadata。冻结后另有按需的 hypothesis_display 展示调用（不是 B）。Investigator A 返回 scientific `findings` 与 `remaining_scientific_question`；SDK pre-closure 绑定 `finding:N` 等 invocation-local refs，独立 B backend（有凭据时默认 Jev，否则 Agent-B；离线测试为 deterministic backend）只把既有 finding/question 映射到 frozen criterion、Evidence relation、remaining-gap kind 与 progress class；最后由 C/Router 生成 Registry/routing/readiness/state。每个 `semantic_closure/*.json` 同时保留 A artifact、B decisions 和 C output。Jev 低置信度不会直接写入权威状态。

Investigation handoff 保留完整恢复/provenance 信息，但 **Investigator A prompt 只接收最小 guaranteed scientific context**：冻结 scientific objective 的 `hypothesis / scope? / decisive_test`；仅在当前 Investigator thread 无法自行拥有上一 executor 新信息时，注入 compact `previous_scientific_delta`（只含上一 executor 新形成的 scientific findings 与 `remaining_scientific_question`）；仅 `judge_rework` 时再注入当前 Judge objection。`hypothesis_ref`、Cycle criterion ID/object、active Evidence refs、Evidence ID、`investigation_attempt` / round / internal-turn / budget、Router/readiness state、criterion/objection update、`judge_feedback`、path/source manifest、`recent_gap_history` 都不进入 A 的 scientific payload。若同一 Investigator thread 连续执行且上一 executor 仍是 Investigator，不重复回灌它刚刚自己生成的 scientific delta；跨 Literature / fresh-thread recovery 等确需 handoff 时才提供。`recent_gap_history` 只进入 B/Semantic Annotator，用于对 A 写出的 remaining question 做封闭 `gap_progress = new_or_refined|repeated_no_progress` 判断。SDK 将 invocation artifact manifest 单独保存用于恢复/复现；同时在 runtime/system handoff 中提供稳定 `task_root` scientific read anchor，保持当前 Cycle `workspace/` 为写根。需要实际文件时，Investigator 从 `task_root` 围绕冻结科学问题自主搜索、打开和核验相关材料，而不是等待 SDK 预枚举文件，也不由上游 finding contract 抄路径。空的 Cycle Evidence Manifest/Coverage 只表达“尚无当前 Cycle Evidence”，不得作为“本地科研输入不存在”的证据。C/Router 在 Investigator 已对同一 gap 无新增量时停止重复 round。

Literature 遵守同一最小上下文哲学，但其稳定导航入口是 SDK 已物化的 scholarly corpus。三个 Literature Stage 的 A prompt 不再注入 `hypothesis_lock` 或 raw `literature_task`，也不枚举 `papers_json / report_md / topic_brief_md / search_log / errors_log / paper_count` 等 retrieval bookkeeping；只注入一个自包含 `search_target`、单一 `scholarly_retrieval_root`，以及仅在 `targeted_source` 真正锁定具体来源时出现的 `source_lock`。Stage 本身已表达 `landscape | targeted_source | novelty_check`，所以 `task_type` 不再重复进入 prompt。Agent 把 root 当作导航入口而不是阅读清单，可按科学需要自由 list/search/open 其中 materialized retrieval、reading、citation-trace 与 source-text artifacts。Literature A 只报告 source-grounded claim、source access、quote/locator、interpretation/limitations 与 remaining scientific question；不回抄 Hypothesis relation、criterion ID、objection ID 或 Evidence ID。对进入冻结 Hypothesis 裁决路径的 LiteratureEvidence，B/Semantic Annotator 绑定 Evidence relation 与 Cycle criterion，C/SDK 绑定稳定 objection/task identity、source metadata 与 Evidence ID。Research Lead 的 frontier-grounding Literature 仍不注册为当前 Cycle adjudicative Evidence，因此无需 B 绑定。

Agent 输出按职责分为三层：

1. **Scientific Turn**：由 Agent 完成必须依赖科学理解的工作，例如 hypothesis、mechanism、derivation、scientific finding、scope、limitation、decisive test、外部证据的解释，以及“还缺什么科学信息”。这一层应尽量保持自然的科学任务边界。
2. **Semantic Annotation（B layer）**：由辅助 `Semantic Annotator` 把已形成的科学结果映射到 frozen criterion、Judge objection、Evidence relation 或 unresolved scientific gap，只能在 SDK 提供的 closed options 中选择，不能增加科学 claim、Evidence、route 或 state transition。若只是复制已知 ID 或把同一结论换一种状态字段表达，不调用模型。
3. **Deterministic Closure（C layer）**：由 SDK/Registry/Validator 生成或校验能由现有状态唯一确定的字段，包括稳定 ID、引用绑定、已知 metadata、路径存在性、预算、固定 stage/mode、确定性状态聚合、Canvas wiring，以及可由 Evidence/criterion/objection 状态直接计算的 readiness/routing 信息。`semantic_closure/` 只是保存 A artifact、B decision 与 C output 的整条 closure 审计目录名，不代表一个 Agent 身份。

**Readiness 投影不变量**：Router 可以为 persisted attempt 保留历史兼容的 `criterion_coverage` 内部记录，但当前 `readiness` / `investigation.routing` 投影不得再把带有 `supported|failed|unresolved|not_evaluated` 等旧裁决语义的 `criterion_coverage` 暴露成当前权威状态。当前 readiness 只表达确定性可验证事实：`covered_criteria`、`uncovered_criteria`、`unanswered_objections`、budget、recommendation/selected executor 与 routing reason。criterion 是否科学上成立仍只由 Judge 裁决。

### 3.1.4 本地工具与大文件读取

TheoryMaster 使用 Codex 原生工具与其既有 sandbox；不再注入、配置或依赖 TheoryMaster Tool Gateway。工具调用本身不构成 Scientific Workflow stage，也不产生额外的 Hypothesis authority。

大文件读取由 Investigator Role Core 约束：先核对大小与结构，再聚焦读取。生产 Investigator 使用 Native Goal v2，不注入 Stage Card；Stage Card 只作观察与历史文档。昂贵计算前复用适用的历史结论、数值结果和 Workspace 产物，是 Role Core 的稳定职责：核对其对象、协议、定义域、单位与误差边界；适用则引用并继续未解决部分。验证方式服从科学对象：纯理论证明不必制造盲测数据；冻结测试若要求事前预测、数据隔离或误差检验，不得省略或用已暴露结果顶替。冻结 decisive_test 要求的独立核验，以及方法或误差缺陷所需的重算，不得因历史相似而跳过。重复计算须减少具体不确定性。复现成功不是新科学发现。Native Goal 只保留一句计算前成本与复用提醒，不重复整套规则。数值操作细则留在按需加载的 numerical-investigation。某一任务的私有限制不得写成全局 Investigator 规则。延续既有线程、Judge rework 或恢复执行时保留已有研究记录。科学 finding 的 provenance 由结构化字段表达，执行产物则由 SDK invocation artifact manifest 自动留痕，二者不得通过 Agent 手工路径清单混为一个合同。

遵守以下最小合同原则：

- **主要认知优先（Primary cognition first）。** A 层 Scientific Turn 应先完成本轮最重要的科学认知任务，并尽量保持弱结构化、少字段；不参与形成科学结论的分类、状态映射和 bookkeeping 应延后到 B/C 层，避免次要合同约束干扰主要推理。
- **科学对象完整性优先于字符级压缩。** Prompt projection 可以删除重复字段、历史副本和非必要 metadata，也可以对真正的历史摘要限制条目数量；但当前权威 scientific object（Goal / Research Contract / Required Context、hypothesis、decisive test、criterion、finding、scope/limitation、Challenger blocker、上一 executor 的 scientific delta）不得用字符切片生成半句话。若上下文确实过大，应改为更窄的字段投影、完整 artifact pointer 或语义上独立的 turn，而不是 `…[clipped]`。
- **唯一可推导值不询问 Agent。** 若某字段在 invocation 开始时或由本轮已验证产物可唯一确定，SDK 直接填充；不要要求 Agent 再次回抄并由 validator 检查“是否抄对”。
- **科学判断与流程调度分离。** Agent 优先表达“发现了什么、意味着什么、还缺什么”；SDK 再决定具体 executor、transition 和机器状态。不得仅为了 workflow routing 让科学角色承担额外表格字段。
- **跨 invocation 只传必要科学增量，而不是机器状态或只传 ID。** Evidence ID/path 是 provenance 坐标，不足以替代上一角色真正解决了什么，但同一 Agent thread 也不应重复接收自己刚刚生成的完整输出。Investigator 仅在 Judge 授权返工、fresh-thread recovery 或跨 executor handoff 时接收 compact `previous_scientific_delta`，其中只保留新的 scientific findings 和 limitations，不回抄 Evidence ID、criterion/objection update、gap taxonomy、role/round/invocation 或 router state。
- **局部正确不等于全局可推进。** Investigator 提交后的科学可判性由 Judge 判断；Judge 只能授权一次有界 rework，不能由旧的 B-gap classification 和 C executor routing 驱动 Investigator↔Literature 无限循环。
- **一个事实只保留一个权威来源。** Evidence metadata、stable IDs、Judge verdict、Research Lead frontier authority 等已有权威来源时，不在下游 contract 中重复生成第二份机器真相。artifact path/hash 只由 SDK invocation artifact manifest 拥有；Agent finding 不抄路径。protected holdout/gate 的身份只能由独立可信的事前冻结记录授权；结构化 provenance 与自然语言中的 `held-out/gate/benchmark` 都只是声明，不得反向创造已认证机器身份。
- **按科学权威而非 JSON 长度划分 invocation。** Investigator 一次完成可执行研究并完整提交；必要的 Evidence 绑定和 Deterministic Closure 属于 SDK bookkeeping，只有 Judge objection 才授权下一次 Investigation attempt。
- **LLM 建议不得替代 SDK 可验证事实。** readiness、引用有效性、artifact 是否存在、budget 是否耗尽、固定枚举映射等应以确定性检查为准；若保留 Agent 建议，只能作为非权威语义信号。
- **未来 contract 瘦身先测试后迁移。** 在删除、搬迁或自动生成现有字段前，先以 characterization / transition / recovery 测试锁定现行行为与持久化兼容边界，再修改 schema、Router 和 state transition；文档原则本身不要求当前立即改变运行行为。

目标不是追求最小 JSON 字节数，而是让每个 Agent turn 只承担其真正需要的认知责任，把 bookkeeping 和可确定性验证下沉到 SDK。

### 3.1.5 SQLite 科学图谱的 Agent 专用渐进读取接口

迁移到 ScienceGraphStore 的任务以 `science_graph.sqlite3` 为唯一科研图谱权威；`exploration.canvas` 是供 Obsidian / Research Explorer 使用的只读可视化投影，不能作为新路径的 Agent 输入源。JSON Canvas 的坐标、尺寸、颜色和展示元数据是可视化格式所需字段，但不应因此进入 Agent token context。

SDK 为 Agent 提供分层只读读取接口：

- `ScienceGraphStore.agent_view(entry_hypothesis_id)`：为 Lead 的前沿提案与维护输出紧凑图谱索引，包括 H ID/标题、全 H 裁决状态、显式 frontier 状态、Cycle 结果模式、H→H typed lineage、当前 active/parked 前沿和相关 External 的稳定引用。
- `ScienceGraphStore.agent_candidate_review_view(candidates)`：为 Challenger 输出当前候选引用的上游 H 档案与相邻 typed lineage；不把完整图谱或 Canvas 路径交给 Challenger。Challenger 另有只读 `search`、`hypothesis-detail`、`external-detail`，没有 `view`。检索先看摘要，命中且可能同一检验时才展开单节点；原始长报告不默认读入。
- `ScienceGraphStore.agent_hypothesis_detail(hypothesis_id)`：按需展开一个 H，返回命题、真实 Whole-H assessment、有限条最近 Cycle Outcomes、frontier 原因、相邻谱系与 External 引用；空的旧版 `open` 占位不当作真实裁决。
- `ScienceGraphStore.agent_goal_completion_view()`：为 Judge 的任务级 Final Gate 检查提供全 H/Cycle 轨迹摘要、真实 Whole-H assessment、近期 Cycle Outcomes、谱系与 External 引用；具体原文通过稳定 detail_ref 按需阅读。
- `ScienceGraphStore.agent_external_detail(external_id)`：按需读取一条 External 的完整正文、来源字段和引用关系。完整正文不预先灌入全图 context。

Research Lead、Challenger、Judge 的 Goal Completion 和 Reporter 的 Cycle Outcome 文案阶段，在存在 `science_graph.sqlite3` 时都从 SQLite 派生各自的只读上下文。不同角色的信息范围可以不同，但图谱事实必须来自同一权威库；不得再将 `exploration.canvas` 作为 Agent 的第二条科研图谱读取路径。Reporter 的输出由 SDK 验证后提交 SQLite，再重新生成 Canvas 投影。只有确实没有 SQLite 数据库的旧 Canvas-only 任务才保留隔离的 `canvas_access` 兼容分支。Research Plan 和 Investigator 所需的实验数据/代码文件仍是独立科研材料，不属于图谱副本。Lead 的同 Cycle/Stage continuation 仅在 SQLite 与 WAL 快照未变化时复用上下文；其他角色每次生成各自用途的 SQLite 视图。静态测试不能替代真实模型 / Cycle 行为复核。

## 3.2 Hypothesis Qualification

Hypothesis Qualification 发生在 Hypothesis Lock 之前：

```text
Research Lead / frontier_proposal
    ↓
Challenger / candidate_review        ← 单次完整 audit，冻结 blocker ledger
    ├─ no qualified + revise → Research Lead / selection_repair（最多一次）
    │                           ↓ SDK 按冻结 blocker ledger 校验；不重跑 Challenger
    ↓ qualified set
    ├─ exactly one → SDK deterministic selection
    └─ multiple    → Research Lead / candidate_selection
    ↓
SDK / Hypothesis Lock
```

此时 H 仍是 candidate。Research Lead 可以 generate、compare、discard、replace、clarify、refine、repair、specialize 或 generalize candidate，也可建立 alternative、combine、derive 或 counter-hypothesis。

在 Challenger audit **之前**，若修改改变命题的科学含义，应创建新 candidate 并重新形成待审 shortlist。Challenger audit **之后**不再允许用 `selection_repair` 偷换科学方向：repair 只能在被审过的同一 scientific direction 内解决冻结 blocker ledger；若需要真正改变假设边界，应放弃本次 lock，而不是在 repair 中生成未经审查的新方向。

Research Lead/SDK 最终只能 finalize 一个 qualified candidate。Challenger 在每个 Cycle 只执行一次完整 adversarial audit，并一次性返回当下可见的全部独立 blockers；若没有 qualified candidate 但存在 `revise`，Research Lead 只有 **1 次** bounded `selection_repair`。repair 使用互斥的结构化结果：`outcome=resolved` 时必须提交一个 scope-preserving replacement、非空可执行 `decisive_test`，并用 `blocker_closures` 对冻结 blocker ID 做逐项且无重复的完整覆盖；`outcome=cannot_repair` 时只提交失败原因。`cannot_repair` 的含义仅是当前 Challenger blocker 无法在 `selection_repair` 的有限修补权限内解决，不等价于 Candidate 科学上不合格，也不等价于 Cycle 应停止。SDK 保留原始 Candidate（包括原始 `decisive_test`）、完整 Challenger 审查记录及 blocker ledger，将原 Candidate 作为可进入 Investigation 的 Candidate 继续 Qualification finalization；不得要求 Lead 改写科学命题来伪造 `resolved`。若因此仅有一个可继续 Candidate，SDK 确定性选中并 Lock 原 Candidate，随后 Investigator 在冻结的 Hypothesis、scope、estimand 和 allowed inputs 边界内研究、解决尚未完成的 prerequisite。Challenger 原始 `revise` 与 blocker 必须作为 deferred qualification context 留档，并随 Investigation handoff 可追溯；它们不改写为“已解决”的科学结论。

`cannot_repair` 与 contract/边界错误必须区分：格式非法、缺失必要字段、重复/遗漏 blocker closure（`resolved` 路径）或试图越过冻结边界，仍按 contract validation 失败处理；这类失败不得伪装成 `cannot_repair`。合法 `cannot_repair` 不触发 `NEEDS_ATTENTION`。所有 candidate 被 `reject` 且没有可继续的 `cannot_repair` Candidate 时，仍按无合格候选安全停止。repair 后不重新打开 Challenger、也不允许移动 goalposts；多个可继续 Candidate 时仍由 Lead `candidate_selection` 选择。

若所有 candidate 被 `reject`，本次 Qualification 安全停止。以后在原 Cycle 执行 `resume` 时，SDK 启动新的 Qualification attempt，并把上次 Challenger 的 candidate-bound verdict/reason 明确注入 Research Lead 的 `frontier_proposal` 输入；不得只依赖同一 thread 的隐式记忆。此反馈只供形成新候选，不把旧审核当作新候选的当前 verdict。历史状态没有结构化 reviews 时，以已保存的完整拒绝理由作为兼容回退。

**Qualification 恢复不变量（2026-10-08）：** `qualification_status=pending` 且存在合法 `qualification_working_set` 是可续的正常断点，原 Candidate/Challenger reviews 与 repair 进度必须原样保留；不能用 `unqualified` 的清场规则处理它。只有真实 `qualification_status=unqualified` 才重新开 Qualification，旧版合法 `cannot_repair` checkpoint 继续走审查留痕 passthrough。Lead frontier 每 Stage 只允许一次 Literature detour；若返回后仍提出第二次搜索，SDK 允许至多一次 delta-only Lead 纠偏（重用已经返回的来源，不再启动 Literature），仍无法形成 Candidate 才按现有 Qualification 安全停机。Literature `novelty_check` 只在 Goal / Final Gate 对外部优先权判断有逻辑必要性时随上述一次 detour 按需触发，普通发表新颖性增强不增加流程或调用。Lead frontier-proposal Contract 从 v8 升级到 v9，旧 v8 在 historical registry 保持冻结；外部 novelty positioning 可在 Lead 续轮 compact context 中可见，不能仅生成却丢失。

### 3.2.1 Decisive-test 科学判别能力与增量价值（TMA-20261011-0044）

Lead 在 `frontier_proposal` 对每个 Candidate 的 `decisive_test` 负责：检验应与假说实际声称的科学增量相称。当平凡极限、已知理论、简单基线或更弱解释也能给出同样预期结果、且这种区分直接影响 Candidate 的主张时，Lead 应设计足以隔离增量贡献的对照，或明确将本 Cycle 的推断范围限制为**必要的局部预测**；没有相关替代解释时不强行添加 baseline。误差界、近似残差、置信区间或证明条件的“有信息量”，由它是否足以区分目标科学情形决定，不能只凭 `lower > 0`、有限、收敛或误差小于任意百分比认定。Lead 应在冻结前考虑所声称的支持与反驳出口是否具有实际可达性，具体判据因科学领域而异，不统一规定样本量、功效、显著性、精度或证明技术。

Challenger 在 `candidate_review` **独立**检查：由计划证据和不确定性处理，能否在逻辑、计算或统计上达到提议的关键决定性出口；每个声称的结果是否真的改变对应假说的认知状态；复杂候选声称的独立物理/理论/预测贡献是否与相关简单解释可区分；允许 `inconclusive` 的检验是否仍能形成具有正确范围的科学约束。若设计本身数学上不可能触发它所承诺的结论，属于 Qualification 的实际科学设计缺陷：按原 `qualified/revise/reject` 语义，根据是否可在不改变被冻结科学命题与 scope/estimand/allowed inputs 的前提下修补判断；不可把这种设计矛盾误判为 Investigator 尚未推导出的常规 prerequisite。合法的探索性、有限范围、信息性阴性/未定检验可 `qualified`，不以统一样本数、p 值或“必得二选一结果”作新的系统硬门槛。

### 3.2.2 科研资源可行性与有限范围退路（TMA-20261011-0054）

科研检验的可执行性不仅是数学上存在可触发的判定出口，也需要**与本 Task 的科学收益、资源和证明代价相称**。Lead 在 `frontier_proposal` 按需估算样本、计算、严格误差证书及分析难题的数量级；若普适总体均值、全范围严格证明或极致精度并非 Goal / Final Gate 的**逻辑必要条件**，可在**新 Candidate 冻结之前**主动选择范围更小、能形成有意义局部证据的科学问题，或转向更直接的 active 科学前沿。有价值的小规模实验不能偷换成普适结论；确实必要的高成本研究在资源路径可信时仍可入选，无统一计算量/样本量或统计阈值。

Challenger 在 `candidate_review` 独立检查检验的**实际判别可行性**：识别超宽支持界、未获证明的尾部/矩条件或规模灾难是否被当成无需代价的普通前置工作。凭具体证据可判断提案在既有科学目标与约束内缺少可行路径时，按原 `qualified/revise/reject` 资格语义区分设计缺陷与 Investigator 可完成的理论/实现前提；不因探索性、尚未证明或单纯成本较高就自动否决。不得要求 Challenger 事前完成整套研究，也不得把弱一点但仍有科学信息的局部检验视为不合格。

Investigator Native Goal / `investigate` / `judge_rework` 与 `numerical-investigation`、`compute-dispatch` 形成一致的**方法早停，不是科学任务早交付**准则：昂贵方法在执行前要有数量级成本与信息收益评估；明显不经济则及时放弃该**方法**，有限探索冻结条件内其它可信路径。无法经济完成某种分布无关最坏情况证明，不等于该科学问题本身要求同等规模，也不等于假说不可证明。可做小规模 smoke/诊断以判断路径，但未冻结允许时不可以替代完整检验或填入正式 Evidence 作为已完成结果。**Investigator 不再有主动把 Native Goal 标记 `blocked` 或请求提前停机的权限**。经有界探索缺少可执行路径时，保留已试方法、确切缺口与可复现实证，在冻结条件下继续合理探索，不得空转、伪造结论或把未执行检验当成 inconclusive；额度和暂停由 SDK/Provider/Operator 管理。若 Provider Native Goal 仍意外返回 `blocked` 且无真实 Provider 错误，SDK 在同一科学线程、同一冻结 Goal 上有界自动重入（最多两次），不触发科研停止审查；若连续出现异常则归类为运行故障，交由运行恢复机制处理，而非科学保全。**只有真实 Investigator Token 配额达到预算边界，才调用 SDK Budget Reviewer 评估科研投入是否继续**；Provider 使用额度、服务错误及操作指令单独由 SDK 处理。历史 `investigator_native_goal_blocked` 检查点的保全/审核语义仅作存量兼容，不追改既有科研记录。能实际完成的冻结检验须继续至 Judge；不得私自牺牲已经锁定的估计对象、样本资格或误差要求。

## 3.3 Hypothesis Lock

Hypothesis Lock 是一个 H 进入当前 Cycle 的科学边界。SDK 在此冻结：

- active hypothesis 的标题、完整命题和 scope；
- candidate ID 与 provenance；
- 本 Cycle 的非空 `decisive_test`（沿用被选 candidate 的同名字段）；
- `initial_investigation_intent`（直接来自 selected candidate 的 `decisive_test`，不是 Evidence queue）；
- `cycle_criteria = [{"id": "C-1", "criterion": decisive_test}]`：新 Cycle 只生成这一条稳定机器引用锚点，用于 Evidence / objection / readiness 绑定，不拥有独立科学含义。支持、反驳、限制适用范围或失效等方向由 Evidence `relation` 单独表达。`cycle_progress_criteria`、成对的 `C-S* / C-F*`、`success_criteria / failure_criteria`、`hypothesis_terminal_criteria` 与 `H-S* / H-F*` 只允许作为旧 persisted Cycle 的读取兼容字段，不得继续由新 Lock 写入。

整个 H 是否可 terminal `supported/rejected` 由 Judge 根据**完整 H 的命题与 scope + 当前 `decisive_test` + 单一 `C-1` 锚点 + Investigation Result**判断，不需要再建立任何平行的成功/失败机器判据。特别地，“未满足成功”不得由 SDK 自动解释为 `rejected`；它也可能对应 `open` 或一次有界 rework，具体科学含义属于 Judge authority。

`decisive_test` 必须是当前 Cycle 和预算内可判定的局部推进，不得把整个研究 Goal 原样复制为 Branch Judge 的成功标准。

Qualification 不再把 Challenger 自由文本拆成多组机器 issue，也不建立 Evidence Requirement / Investigation Requirement queue。被选 candidate 的 `decisive_test` 以同名字段冻结于 Hypothesis Lock，并派生本 Cycle criteria 与 `initial_investigation_intent`；不再另存同值的 `cycle_milestone`，也不把 Cycle criteria 当作独立 Milestone 科学对象。新 Cycle 的 initial executor 固定为统一 `investigator`。后续 Investigator/Literature/Judge 通过 findings、正式 Evidence 和 Judge objection contract 继续推进；现行科研路由不再依赖 remaining scientific gap。

**Hypothesis Lock 是 H identity 的 durable commit boundary。** Lock 最终对象直接包含 `id / hypothesis / scope / decisive_test`；`id` 就是 `H-xxxx`，并同时成为 Canvas node ID。SDK 必须在进入 Canvas projection / Investigation 之前，把新 H 的 `{id, hypothesis, scope}` 原子登记到 Catalog；延续已有 H 时保留其原 `id` 并更新当前 Cycle Lock，不创建别名。Catalog 只作为 identity index，不保存第三套 `live / terminal` 生命周期，也不得写成跨 Cycle 科学事实目录（还缺什么、相信什么、下一步科学方向）。科学裁决和研究活动分别以 Canvas 的 `scientific_status` 与 `frontier_status` 为准。后续 non-scientific closure、暂停、恢复或 Cycle 未进入 terminal Judge 都不得使已经分配的 H identity 从 Catalog 中消失。新 ID 分配必须检查 Catalog、Canvas `id` 与待提交 Lock；冲突时 fail closed。

Candidate identity-resolution 不依赖文本相似度：SDK 从 continuation Decision 的 Canvas incoming edges 找唯一 Hypothesis 父节点；Candidate 省略 `evolution_operator` 时复用该 H。Candidate 给出 typed `evolution_operator` 时，表示提出新的派生命题，SDK 在 Lock 时分配新 H `id`，并从 continuation Decision 创建演化 edge。初始 Cycle 或找不到唯一 H tip 且没有既有 lineage 时，为选中的 Candidate 分配新 ID。多 Hypothesis parent 或无法确定 continuation anchor 时不得静默选择；Qualification 必须 fail closed。

Lock 之后：

- 不得修改或替换 Main Hypothesis；
- 不得通过改名、收窄口径或更换定义规避反证；
- 新假设只能作为 provisional candidate 记录；
- 当前 Cycle 继续裁决原 H。

## 3.4 Investigation Loop

> 本节现行语义由 `TMCR-20261008-001` 确定；旧 Scientific Turn / remaining-gap 路由仅供历史状态只读解释。

Hypothesis Lock 固定 Main Hypothesis 与 decisive test 后，SDK 启动 Investigator。统一 Investigator 在一个 Codex invocation 中自主执行当前必要且可执行的 theory、numerical、task-local source、代码与工具研究；它不因发现自己可以继续完成的下一步而提交 JSON。提交边界是 Investigator 将自包含的科学 findings（及必要的 Evidence 身份材料）交给独立 Judge 审查。每次完整提交只登记一次 Evidence。B 层仅对已产出的 finding 做 Evidence 语义绑定，C 层只做合同闭合、登记、预算和持久化。

### 3.4.1 Finding 的科学自包含 Gate（2026-10-11，TMC-20261011-2d751f79233b）

**删除原因与用户意图（Gate G01）**：`scope`（适用范围）与 `limitations`（科研局限）都来自科学结论的成立条件、量词范围、前提与不确定性；它们应由 Finding 的完整科学命题 `statement` 自身蕴含、明确陈述或足以直接判读，而不应在同一模型输出里额外构造第二份声明。重复元字段增加 Final Formatting 的模型负担，可能把同一科学结论拆成相互矛盾的几份陈述，甚至将未做完的科研包装为 `limitations` 并提前提交。本次是**删除冗余呈现义务，不是取消科学严谨性**。

**原 v7 Contract 验收记录（Gate G02，现已由下一节 v8 替代）**：当时 Investigator `investigate / judge_rework / integrate_literature` 的 `findings[]` 仍要求 `statement`、`evidence_kind`，并在本地来源型材料中使用 `source_refs`；此两项现已取消，仅保留历史只读兼容；`provenance` 暂保留受保护数据身份的独立校验入口。`scope` / `limitations` 不得再以单独 Finding 字段出现，必要的科学适用条件、数据独立性、误差资格及外推限制必须在 `statement` 内说清，供 Judge 直接裁决。**Hypothesis Lock 本身的 `scope` 未删除**；不能把发现数据变成新独立验证。

### 3.4.2 Finding 数量不变与去分类 Gate（2026-10-12，TMC-20261012-25bfb7511767）

**G04 科学边界保全**：Final Formatting 属于 Investigator Stage 内部、Native Goal 完成后的同线程序列化动作，不是 B Semantic Annotation。格式化应当忠实保留科学 A 已经确定的 Finding 数量、顺序和各自独立论断。数学结论的计算核验、误差检查、重复验证不应为了登记而额外拆成一个 Finding；若科研 A 真正修订了独立科学命题，才允许依据它的科研记录改变数量。本 Gate 是 Prompt 科学责任约束，不能让确定性脚本对自然语言做“两个 Finding 等价吗”的伪科学判断。

**G05 精简当前 Contract**：Investigator `investigate`、`judge_rework`、`integrate_literature` 共用 v8 Finding 合同：仅强制输出 `statement`；`provenance` 暂保留条件性可选入口。`scope` / `limitations` / `evidence_kind` / `source_refs` 均不应作为 Agent 新输出。`source_refs` 不再参与新的科学登记或前沿裁决；当既无可核实当前资产记录也无历史资产记录时，SDK 应标记 `freshness=origin_unverified` 而非擅自推断 `current_cycle`，不得据此声称新独立验证。`local_source_review` 仍可作为 Investigator 内部科研方法，不产生单独“类型权限”。旧 v7/v6 输出通过只读 Contract 及 Registry 兼容，不在新科研生成链中复活类型。

**G06 SDK 与 Judge 权限**：Evidence Registry 根据 SDK 自身注入的 `producer_invocation.role`、注册事务、资料完整性及 Literature 阅读验证来决定可供 Judge 阅读的技术资格，不从 Finding 内容或文件名猜测 `theoretical`、`numerical`、`local_source`。Challenger/qualification/非科学角色不得因此升级为科学证据。Judge 独立决定科学正确性、独立性、支持程度及决定性检验是否达成；Registry 的 `adjudicable` 不是科学真实性 `true`。

**G07 身份声明待单独裁决**：`provenance` 最初只用于阻止事后生成/使用过的数据冒充预冻结 holdout/gate；现阶段仍是可选输出，所有 Investigator Finding 均接受同样的保护校验，删类型不能形成绕过。`declaration_ref` 路径唯一且在本 Cycle 工作区外，只是文件位置检查，**不能单凭它证明事前冻结或科学独立**。未来是否彻底移出模型自填 `provenance` 须单独设计可信任务预登记授权与迁移，不能在本次字段删除时靠脚本自动为其“判断 true”。

### 3.4.3 Investigator A 交付解耦：无 Final Formatting Turn（2026-10-12）

**G08 科研自由与唯一交付接口**：Investigator 的 Native Goal 继续只冻结 Hypothesis、scope、decisive_test 和科学完成条件，不要求在科研时生成 evidence_kind、scope、limitations 等管理字段，也不强制每条 Finding 都拆出 method 或 result；但这两个有独立科研价值的字段允许按实际需要明确提供，并必须原样保留。证明、数值、原始结果与自用 findings.json 可以在任意科学子目录中自主组织，也不成为 SDK 检索的隐式交付路径。

**G09 一次性确定性交付**：SDK 在 Goal 的独立 setup Context 中交给 Investigator 一个本次 invocation 专属的 workspace/.tm-handoff/<invocation_id>.json 交付路径，仅在 native complete 后读取。这个行政接口只要求 findings 数组，其成员可为 claim 完整科学陈述、statement 完整科学陈述或单一字符串，数量与科学独立性由 A 决定。method、result 可作为自愿提交的科研说明字段保留，前者为实际推导/计算/验证方法，后者为原样 JSON 科学结果，SDK 不得从其他文件推断、拼接或替换它们。SDK 仅按 Finding 数组原顺序将 claim 字面映射为 statement，并原样保留 method/result 经 Evidence Registry 供 Judge 读取，不借关键词、文件搜索或规则算法判断科学意义；原始科研材料仍保存在工作区。

**G09a v9 可选科研细节**：2026-10-12 用户明确要求保留 method 与 result，因为它们具有独立科研价值，不属于已取消的 scope、limitations 或 evidence_kind。当前 Investigator Contract 升至 v9，保持 statement 唯一必填项，method/result 是可按科学需要提交的原始内容。method 为非空科研方法说明，result 为原样 JSON 科研结果，可是对象、数组、数值或文字。不按固定学科方法强迫所有 Finding 填写，不由 SDK 从脚本/路径猜测其存在，也不由自动格式化模型重组。SDK 的 Evidence Registry 与 Investigation Result（Judge 输入）按原值传递。旧 v8/v7/v6 Contract 只读兼容，不更改历史材料。静态测试只能确认传递行为，实际科学充分性由后续真实 Cycle 审计。

**G09b Judge 真实输入审计（2026-10-12）**：Investigator 的可选 method/result 不仅进入 Registry 和 Investigation Result，也必须在 Judge terminal_assessment 的 `stage_context` 中保留原值（可为嵌套 JSON 或其他合法 JSON 类型），不得做静默裁剪。Stage Prompt 只消费冻结 H/decisive_test 与必要的语义 Finding；Evidence ID、criterion 机器管理留 SDK。Judge、Literature、Reporter 仍使用普通 Stage Turn（Developer Identity + Stage Card + Contract + Context），不是 Investigator Native Goal；新 Thread 的 Developer 指令、普通 Turn 与修复 Turn 应分层记录。Reporter report→canvas_maintenance 在有效保留 Thread 时可省略已由 Reporter 写出的 report_title，但继续显式传递 report_summary、证据边界、Canvas focus；fresh/recovery 必须完整恢复。Literature novelty_check 是可在 Goal/Final Gate 科学必要时经 Research Lead 受限旁路请求的当前 Stage，Observer 应显示其条件路由。

**G10 无额外 LLM 与失败保护**：生产链移除 format_goal_findings 和同线程额外 thread.run(formatting_prompt)。Goal 尚未 complete 绝不登记 Finding；已 complete 但交付缺失、JSON 错误、字段冲突或陈述为空时，不凭空重建、自动搜索或模型修复，而登记可恢复 needs_attention / investigator_handoff_invalid，不能送 Judge。旧 Cycle 的科研文件与合同快照保持原状；历史只读兼容不修改。

**2026-10-12｜provenance 预冻结权威边界修复（TMC-20261012-01160e04d8c1）**：`source/split/declaration_ref` 是 Agent 声明。声明路径存在、唯一、位于当前 Cycle workspace 外，以及在 Evidence 登记时有 SHA-256，都不能证明声明在当前 Cycle 开始前已存在、内容确实对应当前数据集，或确认 holdout/gate 的实验独立性。原 `frozen_before_current_cycle=true` 由外部路径检查导出的规则废止；新记录持久化 `declaration_sha256` 与 `freeze_status=unverified`、`protected_identity_verified=false`，只保护登记后完整性而不自动提升为事前冻结。登记后声明缺失/内容变化触发 Evidence integrity drift。历史 Registry 不重写；Manifest 投影把原布尔值降级为 `legacy_unverified`，历史普通科学 Evidence 仍可审查，但不拥有认证的强测试集身份。Judge 的紧凑 Investigation Result 用 `declared_source` / `declared_split` / `freeze_status` 明确区分声明与验证事实，不传旧冻结布尔值或机器路径。SDK 暂无可信 pre-Cycle 数据集绑定与冻结凭证机制，因此当前所有此类 Agent 提交的强身份均不自动通过；将来如需真正认证，需要另行设计独立的事前冻结来源、数据集身份绑定和不可篡改凭证，不能靠 `mtime` 或路径推断。验收仅用确定性代码测试，不发起 LLM 测试，不改变科学 Stage/Router 拓扑。

```text
Hypothesis Lock → Investigator 完整提交 → Evidence 登记 → Judge
                                                  ├─ terminal verdict → 收口
                                                  └─ 一个有界 objection
                                                       ├─ 无外部 scholarly dependency → Investigator rework
                                                       └─ 需要外部 scholarly evidence → Literature → Investigator rework
                                                                                         ↓
                                                                                       Judge
```

Judge 是唯一 scientific rework authority。Investigator 不输出 `remaining_scientific_question`、`remaining_gap.kind` 或 executor；科学局限保留在 `findings[].limitations`，由 Judge 判断是否阻碍当前 frozen decisive test。Research Lead 仍独占下一 Cycle 的选题；Research Lead→Literature 的 `frontier_proposal` 旁路不属于本节 Investigation routing。

Investigation 可有多个 attempt，只有 Judge 的非终局 objection 授权新的 attempt。原 H、decisive test 与 Cycle criteria 保持冻结。Attempt 是返工与审计边界；Round 暂作执行/预算计数，不是 Agent turn 或科学裁决边界。Invocation Ledger 管理调用与恢复；旧 `internal_turn` 和 gap 字段仅可在旧 persisted Cycle 读取，不得进入当前 prompt、路由或 TUI。

## 3.5 Investigation readiness

Investigator/Literature 不输出 `ready_for_judge`。Investigator 完整提交并完成确定性 Evidence 登记后，所有来源角色正确且通过技术完整性校验的科学 Findings 均直接纳入 Investigation Result 交给 Judge；不以 B 层分类的 criterion_ids 或 relation 进行科学筛选。Finding 是否回答 decisive_test、证据是否充分以及是否需要 rework，均由 Judge 独立判断。没有可裁决 Evidence 或发生真实 transport/resource blocker 时，由 SDK 做可审计的非科学收口，不伪造 scientific verdict。预算耗尽须有确定性终止记录。

## 3.6 Branch Judge 与有限返工

Judge 只在 Evidence Package 已构建后运行。若请求科学返工，SDK 只有在缺失证据仍可执行且预算允许时，才保留原 H / 本 Cycle `decisive_test` / Cycle criteria，持久化稳定 objection，并重新进入完整 Investigation Router。

rework 必须是绑定具体 criterion 的最小可执行增量；Evidence ID 与 artifact path 分属不同命名空间。预算耗尽仍无法裁决时可以 terminal `open`，但必须把资源耗尽与科学结论区分开。整个研究 Goal 尚未完成不能成为 Branch Judge 的返工理由。

## 3.7 Scientific Synthesis

terminal Judge 之后，Research Lead 进入独立 `cycle_synthesis` episode，根据 model version in、锁定 H、Evidence Package、Judge decision 和 Cycle artifacts，只生成：

- scientific synthesis；
- `scientific_model_update`：`keep | replace`。

`cycle_synthesis` **不生成 evolution candidates，也不决定 next frontier**。`keep` 表示长期 Scientific Model 完全不变；`replace` 必须提交完整的新 Scientific Model 内容。Scientific Synthesis 只回答“本 Cycle 改变了整个研究问题的哪些认识”，不再次裁决 H。

Scientific Synthesis 完成后、Reporter 之前，同一 Research Lead episode 进入独立的 `frontier_maintenance` Stage。这是 Canvas 修枝。`frontier_proposal` 才决定下一 Cycle 往哪长。

本 Stage 在主路径中的位置是：

```text
Branch Judge / terminal_assessment
    ↓
Research Lead / cycle_synthesis
    ↓
Scientific Model commit
    ↓
Research Lead / frontier_maintenance
    ↓
Reporter / report
    ↓
Reporter / canvas_maintenance
    ↓
SDK / Canvas commit
```

输入：Goal、Research Contract、Final Gate、Scientific Model summary、current Judge result、`scientific_synthesis`、`canvas_access`。

输出仅 `updates[]`。每项只含 `node_id`、`frontier_status`（`active | parked | closed`）与 `reason`。旧 tip 不再是 tip 时，移除其 `frontier_status`。

禁止：提出 Candidate、建立新 H、决定下一 Cycle、修改 `scientific_status`、修改 Judge verdict、修改 Scientific Model。

`frontier_status` 的现行语义以 [Canvas设计文档.md](./Canvas设计文档.md) 为准：Frontier 属于 SQLite 的分支身份，并非只属于图形叶子节点；下文 tip 字段迁移是 V1 历史规则。

`frontier_status: active` 是**默认研究前沿**，不是下一 Cycle 的强制起点。`frontier_proposal` 可以基于 Goal / Final Gate / Scientific Model / 完整 Canvas 做全局 pivot；但只要提出的是一个新的派生 Hypothesis，就必须同时给出 `lineage_anchor_node_id` 与闭集 `evolution_operator`，并在 `why_now` 中说明为什么偏离既有 active 路线。`lineage_anchor_node_id` 只用于本次 SDK 建 edge，不写入 Canvas Header；SDK 必须验证 anchor 已存在且属于可作为 scientific lineage 起点的 Hypothesis / Decision。除 Task 第一个无历史科学节点的 root H 外，新 H 不允许成为无 incoming scientific-lineage edge 的孤岛。

若本 Cycle 发生 pivot，`frontier_maintenance` 必须重新检查所有仍为 tip 的旧 active 路线并显式分类为 `active | parked | closed`；不得因没有选择它就机械关闭，也不得把已经明确放弃/暂停的旧主线静默留为 stale `active`。

## 3.8 Cycle closure 与投影

Scientific Model `keep | replace` 决策完成并形成明确 model version 后，当前 Cycle 的 H-level / branch scientific affairs 可以闭合，但这**不等于 Task Goal completion**：`Branch scientific closure ≠ Task Goal completion`。闭合顺序是 `frontier_maintenance` → Reporter `report` → `canvas_maintenance` → SDK Canvas commit。若当前 Branch Judge / `terminal_assessment` 的 terminal verdict 为 `supported`，SDK 必须保留该事件触发信号；Canvas commit 完成后仍必须按 §3.1.2 调用 fresh `Judge / goal_completion`。

`goal_completion` 只返回 `passed | not_passed` 与 `rationale`，不参与规划。`passed` 时 SDK 确定性把 Task 标为 `COMPLETED`，并把当前所有 `active` / `parked` branch tip 改为 `closed`。`not_passed` 时允许下一 Cycle，但不创建 planner state；`goal.not_completed` 只记录 `verdict`、`rationale`、`source_cycle`。两种结果的判断与理由都可见投影到当前 Decision 正文。

SDK 可持久化 terminal branch result、已提交 Scientific Model、`continuation_anchor_decision_id` 与 invocation/phase 引用。这些值只能指向 Canvas 或当前 Cycle artifact。`scientific_synthesis` 若因同 Cycle recovery 留下 transient copy，Cycle 闭合后不得再当作科学事实源，也不得改写成 `established` / `unresolved` / `boundary` 供下一 Cycle 读取。它不生成 evolution candidates，不选择 next frontier，也不创建下一 Cycle 的 Canvas Hypothesis。若 Goal Judge 未使任务进入 `COMPLETED`，下一 Cycle 的 `frontier_proposal` 只读 Goal、Research Contract、Final Gate、Scientific Model 与 Canvas，经完整 Qualification 后才锁定自己的 H。

**V2 路径**：Frontier 在 `frontier_branches` 中按 H/分支身份维护；有后继的 H 仍可 active。Reporter 不创建独立 Decision 图节点，只给该 Cycle Outcome 的短 `decision.title/summary` 文案；SDK 保存完整裁决和 H→H 谱系。上述旧的 `pre-Decision Canvas → 新 Decision tip → 删除父 H frontier_status` 算法属于 V1 兼容，不得用于已迁移的 SQLite V2。

Research Lead `frontier_maintenance` 维护现有分支的活跃状态后，Reporter 执行 `report` 生成完整科研叙事，再执行 `canvas_maintenance` 仅提供该轮终局 Cycle Outcome 的短文案。合同仍叫 `decision.title/summary`（120/900 字符），**不新增独立 Decision 图节点、不限制完整报告篇幅**；Reporter 没有 Judge/Frontier authority，也不能创建下一 Cycle H 或选择 next frontier。SDK 负责 Outcome 数据、Notes、Graph 投影和运行状态的确定性校验及落盘；投影失败的恢复不得重新科学裁决。

`canvas_maintenance.rationale` 是**可选诊断说明**，不是 Decision 科学内容或 Canvas authority。只有当 SDK 给定的 compact context 仍不足以忠实撰写 Decision copy 时，Reporter 才需要返回非空 rationale 说明缺口；正常成功投影可以省略或留空。SDK Closure 对省略/空 rationale 使用确定性 fallback，Validator 不得仅因 rationale 为空触发新的模型调用或 contract repair。

---

# 4. 四个时间尺度

TheoryMaster 同时维护四层时间尺度：Agent 内部 loop、当前 `decisive_test` 下的 Investigation、一次 bounded Cycle adjudication、以及跨 Cycle 的 Theory Discovery。前两层解决本轮局部科学问题，Cycle 负责形成可裁决事务，跨 Cycle 层负责 H 的持续证据积累、frontier 与理论演化。一个 H 可以跨多个 Cycle 继续存在；Qualification 是 Theory Discovery 向当前 Cycle 交付唯一 active H 与本轮 `decisive_test` 的边界。

---

# 5. Research Plan / Scientific Model 接口

`research_plan.md` 的**正式格式、五个 H1、字段 ownership、Scientific Model 纯 Markdown 规则、更新方法和 machine-state 分离**统一由以下规范定义：

> **[ResearchPlan设计文档.md](./ResearchPlan设计文档.md)**

本文只保留 SDK 接口边界：

- SDK 把 Research Plan 作为唯一人工长期科学输入；
- `Goal / Research Contract / Final Gate / Required Context` 不得被 Scientific Agent 隐式改写；
- Research Lead 在 `cycle_synthesis` 只能对 Scientific Model 提交 `keep|replace`；
- SDK 负责校验、版本、原子提交和 append-only history；
- Scientific Model 的机器版本元数据保存在 runtime root，不嵌入 Research Plan；`frontier_state.json` 只保存 workflow pointer，不是跨 Cycle scientific authority；
- Research Plan 格式冲突时，以 `ResearchPlan设计文档.md` 为准。

---

# 6. Hypothesis Frontier 与 Theory Evolution

Frontier 是当前可以进入 Qualification 的 candidate 集合，不等于“上一 Cycle 的下一个子节点”。Candidate ID 是 Cycle-local bookkeeping，不是 H identity。Agent-facing Candidate 必须有 `hypothesis / scope / why_now / goal_advance / decisive_test`；可选的 `evolution_operator` 是 typed Canvas lineage 边（如 refine、specialize、generalize、alternative、combine、derive、counter_hypothesis），只用于表达新派生 H，不携带 H ID。省略该字段表示继续当前唯一 active H。SDK 在 Qualification closure 生成 Candidate-local ID，并在 Hypothesis Lock 时解析最终 H identity。

Research Lead 每次 Qualification 都重新评估 frontier 的可证伪性、判别力、Evidence 相容性、Goal 信息增益与调查成本。rejected / closed path 作为科学历史保留，不能在没有新 Evidence 或新 scope 时换名重启。

下一 Cycle 的 `frontier_proposal` 不从 runtime frontier 文件读取科学事实。它只读 Goal、Research Contract、Final Gate、Scientific Model 与 Canvas。默认只有 `frontier_status=active` 的 branch tip 属于当前 Research Frontier。选题优先级与 active-branch fallback 见 §1.2。

---

# 7. Evidence Registry 与裁决视图

Evidence 必须先注册为 Canonical Evidence Record，才可能获得裁决资格。新原始记录由 SDK 写入 stable `evidence_id`、`hypothesis_ref`（值为 Hypothesis node `id`）、`cycle_id`、本 Cycle `criterion_ids`、支持或反驳关系、scope、artifact pointer 与 `producer_invocation`；Agent 不填写 H/Cycle identity。SDK 确定性写入 `integrity`（路径、hash、artifact-role 完整性），并在 Manifest projection 中派生 `adjudicable`。Evidence lifecycle 与来源验证属于 Registry/SDK authority，不由 Judge 逐条改写。Literature 另以 `source_access=metadata|abstract|full_text|unavailable` 记录实际阅读层级；其中 `unavailable` 只表示经 SDK 绑定的来源当前没有可用于科学判断的可读内容，即使 access classification 本身已被确认，也永远不得令该 Evidence 获得 `adjudicable=true`。`provenance` 不是所有 Evidence 共用的一套强制数据谱系：当前只对 Investigator 产生的 `numerical | local_source` Evidence 启用强来源身份校验；若其正文声称 `held-out` / `gate` / `benchmark-provided` 身份，必须提交匹配的结构化 provenance，且冻结声明必须唯一解析到当前 Cycle workspace 之外的既有 artifact。Literature 等其它 Evidence 不会因为自然语言出现这些词就自动继承 Investigator 的数据来源合同。

Artifact pointer / artifact role 只属于 SDK invocation manifest 与复现审计，不作为 Judge 输入。Prior Evidence 也不以旧 raw Registry record、CSV、脚本或 artifact 的形式重新进入 Judge；SDK 只暴露同一 `hypothesis_ref` 的 compact adjudication history，并明确保留每个 prior Cycle 的 `verdict / decisive_test / scope / limitations / Evidence summary`。旧 Evidence 不重新 register。

Evidence 不原地覆盖；生命周期与 Judge assessment 都是 append-only，使历史 Judge 能重建当时看到的证据集合及当时的裁决。

SDK 从当前 Cycle Registry 确定性生成 Evidence Manifest / Coverage 等审计与 closure projection；`lifecycle_status=active` 只表示该 Registry record 当前未被 supersede/retract/drift，不构成科学消费授权，进入 scientific closure 的 Evidence 仍统一要求 `adjudicable=true`。Judge 本身消费的是 compact `investigation_result`，不是完整 Manifest/Packet。跨 Cycle 时另由只读 `Hypothesis Adjudication History` 聚合**已经持久化**的 per-Cycle authority：同一 H 的每个条目保留 `cycle_id / decisive_test / verdict / established_result / Evidence summary / scope / limitations`，criterion 在聚合层按 `(hypothesis_ref, cycle_id, criterion_id)` 绑定。该 projection 不落盘为第二 Registry，不复制 Evidence，不改变 lifecycle，也不把 prior `open` 解释成 established support。

跨 Cycle `Hypothesis Adjudication History` **不得把持久化 `evidence_manifest.json` 当作当前 Evidence authority**。Manifest 只是某次 Registry rebuild 的派生快照；history builder 必须使用一个**无副作用的 current Registry projection**，从 append-only Registry / registration batch / lifecycle authority 与当前 artifact integrity 确定性计算“此刻仍可参与裁决”的 Evidence，而不写 lifecycle event、不刷新旧 Manifest、不改变旧 Cycle 状态。已经 retracted、superseded 或发生 integrity drift 的 prior Evidence 不得因为旧 Manifest 中曾经有 `adjudicable=true` 而重新进入 Judge。对缺少 `hypothesis_ref / cycle_id` 的历史 Evidence，兼容 fallback 只允许用于明确支持的 legacy schema 且 containing Cycle 能唯一确定 locked H；projection 应保留 compatibility basis，禁止把任意缺字段 record 静默升级为 stable-H ownership。

---

# 8. Canvas

Canvas 的**格式、节点类型、节点 Header、`scientific_status`、`frontier_status`、scientific branch tip、Hypothesis/Decision ID 规则、科学谱系 edge 与维护 Authority** 已拆分为独立正式规范：

> **[Canvas设计文档.md](./Canvas设计文档.md)**

本 SDK 文档不再重复维护 Canvas 数据格式，以避免两份规范漂移。Canvas 语义与格式如有冲突，以 `Canvas设计文档.md` 为准。

SDK 只保留与 Canvas 的系统边界：

- SDK 负责把已经拥有明确 authority 的科学状态安全持久化并提交 Canvas 写入；
- Judge 的正式裁决由 SDK 确定性投影为 Hypothesis 的科学状态；
- Research Lead 的 `frontier_maintenance` 只改 branch tip 的 `frontier_status`；该字段是跨 Cycle branch activity authority，语义见独立 Canvas 规范；
- Reporter 只负责人类可读的 Decision 标题、摘要与叙事，不拥有科学裁决权或下一研究方向权；
- Canvas edge 表达 scientific lineage；新派生 Hypothesis 必须通过经 SDK 验证的 `lineage_anchor_node_id` 建立 incoming lineage edge，除首个 root H 外不得形成孤立 scientific subgraph；Canvas 不作为 runtime log、Evidence Registry 或 Task board；
- 当前 Task 自己产生的 Decision 使用 `<Cycle>-decision` 且正文携带同值 `cycle_id`；导入/历史 Decision 不得占用该 namespace，迁移为 `historical-c<Cycle>-decision[-suffix]`，避免未来 Cycle ID collision；
- Canvas 布局由 SDK 以 lineage parent 为几何锚点确定性处理：child 默认放在 parent 邻近右侧，冲突时选择距离 parent 最近的可用 sibling lane；Agent 不输出坐标/尺寸，不允许新 H 因缺少 anchor 被放到远离主图的孤立位置；
- 当前规模下不引入 Canvas→Agent 中间过滤程序，Research Lead 通过 Canvas 路径直接读取，并按独立 Canvas 规范解释 active / parked / closed branch tips；
- Canvas projection / maintenance failure 属于投影失败，不得重新触发已经完成的 Judge、Scientific Synthesis 或 Scientific Model commit。

---

# 9. 持久化与信息层级

- L0 — Agent Thread / Working Context：短期工作记忆，可丢失，不是 source of truth。
- L1 — Research Plan / Scientific Model：压缩后的全局科学认识。
- L2 — Cycle Report / Artifacts：本轮论证、Evidence、失败和复现路径；不是跨 Cycle scientific authority。
- L3 — Canvas：可追踪的探索结构、裁决历史与当前 frontier。

任何长期认识都不得只存在 Agent thread 中。除 Research Plan 与 Canvas 外，不允许任何持久化 runtime 文件成为跨 Cycle scientific authority。不要在这两层之外再造第三层科学权威。

`frontier_state.json` / `research_state.json` 只是 workflow runtime。只允许 recovery pointer、当前 Cycle candidate runtime、`continuation_anchor_decision_id`、invocation/phase 引用。这些值只能指向 Canvas 或当前 Cycle artifact。

禁止跨 Cycle 保存并供下一 Cycle 当作科学事实源：`continuation_context.scientific_synthesis`、`established`、`unresolved`、`boundary`、goal gap、隐藏 Hypothesis science catalog，以及「还缺什么 / 相信什么 / 下一步科学方向」。同 Cycle recovery 可以保留 transient copy；Cycle 闭合后不得再把它当作科学事实源。

## 9.1 Authority Matrix

| Artifact | Authority scope | Writer | Human editable | Reverse write |
| --- | --- | --- | --- | --- |
| `research_plan.md` | Goal Contract + current Scientific Model；格式权威见 [ResearchPlan设计文档.md](./ResearchPlan设计文档.md) | human owns Goal Contract; Research Lead/SDK maintains Scientific Model | scoped by Research Plan spec | no transient frontier / Cycle / continuation / protocol state |
| `theory-master.config.json` | runtime budgets, scheduling, paths, task-local Skill whitelist | operator/SDK bootstrap | yes | no Canvas override |
| `.theory-master/task.json` | pointer to system config | SDK | no | no |
| task-local `.theory-master/skills/<name>/SKILL.md` | one Master's optional capability instructions | task/operator | yes | never promoted to system whitelist implicitly |
| Scientific Model | authoritative machine scientific state | atomic commit engine | no | not applicable |
| `.theory-master/research/frontier_state.json` | workflow runtime only：recovery pointer、当前 Cycle candidate runtime、`continuation_anchor_decision_id`、invocation/phase 引用；只能指向 Canvas 或当前 Cycle artifact | SDK | no | never a cross-Cycle scientific authority; never projected back into Research Plan |
| Evidence Registry | evidence authority | registry/ledger | no | append amendment event only |
| `research_state.json` | workflow runtime and same-Cycle recovery only | SDK | no | no |
| Cycle Narrative | canonical human-readable scientific record | Reporter; human copy edit | limited | never implicit |
| Canvas | traceable exploration structure + current frontier（`frontier_status` 为跨 Cycle branch activity authority）; format authority: [Canvas设计文档.md](./Canvas设计文档.md) | Judge→SDK projects scientific status; Research Lead `frontier_maintenance` owns frontier activity; Reporter authors Decision narrative; deterministic Canvas Validator/Applier validates/layouts/writes; controlled human edit | limited | explicit amendment only |
| `run_state.json` / TUI | runtime projection | collector/runtime renderer | no | no |

“权威”必须始终带 scope。Cycle Narrative 可以作为正式发表和阅读入口，但不得反向静默覆盖 Scientific Model 或 Evidence。人工科学修改必须形成 `ProposedModelAmendment`，经过 validation/approval 和显式 commit，产生新 model version。

## 9.2 Projection provenance

每个长期投影应记录其 derived source versions 或 hashes。投影可重建、可修复，但不可成为其上游机器状态的隐式输入。

---

# 10. 运行与落盘

## 10.1 任务入口

`research_plan.md` 是唯一人工科学任务输入载体；其格式、字段顺序、维护权和 Scientific Model 规则见 **[ResearchPlan设计文档.md](./ResearchPlan设计文档.md)**，SDK 不再重复维护第二份格式定义。系统只额外规定：`theory-master.config.json`（schema `theory-master/system/v1`）独占 runtime、预算、调度、路径以及 task-local Skill whitelist 权威；`.theory-master/task.json` 仅为 config 指针。Task-local Skill 正文属于任务配置资产，位于 config 目录下 `.theory-master/skills/`。

新任务把人类可见工作根正式称为**笔记区（note area）**。`research_plan.md`、唯一 `exploration.canvas`、`notes/` 必须在创建时位于同一个笔记区；`theory-master.config.json` 也留在该目录，机器运行状态默认隐藏在笔记区内的 `.theory-master/research/`。笔记区与 runtime root 的发现指针指向同一 config。每个 task 必须具有稳定、跨 task root 可区分的 **Task ID**：用户层只使用从 `0` 开始的紧凑非负整数，并持久化在 config 的 `task_id` 中；Task ID 分配在 repository/search scope 内串行化，优先使用最小未占用整数。新 Cycle 身份使用从 `1` 开始递增的自然数（`1`、`2`、`3`……），与 Task ID 独立计数；历史 `cycle-xxxx` 只做读取兼容。UUID/hash 不得作为新任务的人类可见 ID；短暂使用过的 canonical-root hash 只保留为兼容 lookup alias。**兼容原则：科学/路径资产不迁移。** 已有任务不改名、不搬目录，继续允许原 `research_root`、历史 Canvas 名称、`nodes/` 或外部 Canvas/notes 路径；旧 v2 任务迁移时，canonical `research_plan.md` 必须进入原 control Canvas 所在的人类 workspace，旧 runtime-root 路径最多保留为指向 canonical plan 的兼容 symlink，不形成第二份科学输入。

`max_cycles` 是**一次 run/resume invocation 的 Cycle 预算**：历史 Cycle 不消耗它；resume 原 Cycle 占第一个 slot；needs_attention 或 frontier exhausted 可以提前结束；预算耗尽只结束本次 invocation，不代表 Goal 完成。Investigation 内的 executor round、Judge rework、Cycle 总 Investigation round 预算与它正交。

## 10.2 Cycle 持久化边界

`research_state.json` 与 `frontier_state.json` 都只是 workflow runtime，不是跨 Cycle scientific authority。`research_state.json` 管本 Cycle recovery；`frontier_state.json` 只保存 recovery pointer、当前 Cycle candidate runtime、`continuation_anchor_decision_id` 与 invocation/phase 引用，且这些值只能指向 Canvas 或当前 Cycle artifact。`run_state.json` 是 TUI/runtime projection。一个 Cycle 需要保留：append-only events、checkpoint、Investigation handoff、Evidence 裁决视图、Invocation Ledger、Scientific Model input→synthesis→update→staged/out、Narrative、cost 与复现 artifacts。synthesis 等科学综合若留在 Cycle 目录，只作为同 Cycle recovery 的 transient copy；Cycle 闭合后不得再当作科学事实源。

Research Plan 保存当前 Scientific Model；runtime 根只保存 workflow pointer、append-only Scientific Model 历史、Research Lead 恢复元数据与 load snapshots。不同 authority/transaction boundary 不得混写。

## 10.3 正交状态

持久状态至少分为：

- `scientific_phase = qualification | locked | investigating | adjudicated | committed`；
- `scientific_verdict = null | supported | rejected | milestone_advanced | open`；
- `runtime_state = idle | running | paused | failed | cancelled | over`；
- `goal_status = active | passed`（任务级 Goal 轴，不属于 Cycle runtime 轴）；`passed` 表示 Task `COMPLETE`。`not_passed` 不另建 planner state，只留 `goal.not_completed = {verdict, rationale, source_cycle}`；
- `protocol_health = valid | degraded | violation | migration_required`。

旧 `status` / `phase` 仅作为兼容投影。Cycle 与 Goal 必须严格分轴：`over` 只表示**当前 Cycle 已正常收口**（包括 scientific closure、Reporter/Canvas/frontier closure 已完成，当前 Cycle 不再 resume），不表示整个 Research Goal 已实现；事件触发的 `Judge / goal_completion` 若返回 `passed`，只能写任务级 `goal_status=passed`（并可投影 `phase=goal_complete`），并由 SDK 确定性把当前 `active` / `parked` branch tips 全部改为 `closed`；**不得把已经结束的 Cycle 从 `status=over` / `runtime_state=over` 改写成 `completed`**。`not_passed` 只追加 `goal.not_completed` 的 `verdict`、`rationale`、`source_cycle`，并允许下一 Cycle。Task-level 可观测状态在 `goal_status=passed` 时投影为 `COMPLETE`；当前生产写路径不再产生 Cycle-level `completed/COMPLETED`，旧 `COMPLETED` 只允许作为历史输入读取并按其来源兼容归一。科学 terminal、Cycle runtime terminal 与 Goal terminal 不得互相覆盖；例如 Model 已 commit 而 Reporter 崩溃时，允许 `scientific_phase=committed`、`runtime_state=failed`，resume 只补投影。

Investigation attempt、单 attempt executor round、Judge rework 与 Cycle 总 Investigation round 必须分别计数，并分别受预算约束；一次 shell/Python/skill load 或 theory↔numerical 方法切换不单独计 round。任何预算到界都只记录资源边界，不得隐式改写 scientific verdict。

## 10.4 Versioned Role Contract Registry

Agent JSON 不直接等于 durable domain state。Role Contract 用 stable `contract_id/version/hash` 把 prompt schema、structural/semantic validation 与后续 state transition 隔离开；科学上下文优先通过只读 artifact ref 传递，而不是不断扩张巨型 DTO。

**运行时只注册当前 Stage Contract。** 生产调用必须经 `stage_for(...) → role_contract(role, phase=stage)` 取得 `contract_stages.py::PHASE_CONTRACTS`；无 `phase` 或未注册的旧 Stage 一律 fail closed。运行时不提供历史合同查询或旧输出兼容层；跨版本的持久化任务必须通过显式数据迁移完成，不得在执行路径中静默回退到旧合同。破坏性 schema 变化应升级当前 contract version，并用迁移工具处理历史状态。

Contract 的长期设计还包括稳定 input envelope、persistence/recovery compatibility 与 conformance validation。当前 Challenger contract 必须完整覆盖 shortlist，`verdict` 仅允许 `qualified|revise|reject`；`revise` 使用 `blocking_issues[{problem, required_change}]`。

---

# 11. Recovery 与交易边界

安全 checkpoint 位于科学边界和角色调用边界。恢复时从最近完整 checkpoint 继续，不把中途文本当成已完成产物。**Hypothesis Lock 是持久化的阶段边界**：一旦 `hypothesis_lock.status=locked`，resume 必须直接恢复 locked branch / Investigation，不得重新进入 Research Lead frontier proposal、Challenger candidate review、Literature qualification 或 candidate selection。`failed | interrupted | paused | needs_attention` 的 Cycle 在原 Cycle 内恢复；已经 `over | cancelled` 的 Cycle 不得重开。Goal 是否完成只读取独立的 `goal_status` / `goal_completion` 轴，不得从 Cycle `status` / `runtime_state` 推导。读取历史 Cycle 时仍兼容旧 `completed` 作为 legacy Cycle-over 表示并归一为 `over`，但任何当前生产写路径一律只写 `over`。

Codex inactivity watchdog 属于 **transport-level retry**，不是新的 Investigation round 或新的科学 Stage。watchdog 一旦先于正常 terminal completion 触发，随后由 `interrupt()` 引出的 `turn/completed`、此前的 assistant progress text 与 delta 都必须视为 partial/non-final，timeout authority 优先于这些 transport 事件；不得把中途文本送入 JSON normalization / contract closure / Evidence。重试应恢复同一个 server-side Agent thread，并发送最小 continuation 指令，要求从现有 thread/workspace 状态继续且不要重做已完成工作；只有拿到合法 final output 后才重新进入科学 closure。正常 terminal completion 若先于 watchdog callback，则 callback 必须失效，不能制造伪 timeout。

Scientific Model commit 只发生在 terminal Judge 之后。Research Lead 的 `keep|replace` 决策必须先完成 contract validation 和 staged preview，再原子提交到明确 model version；重试不得重复 commit，Reporter/Canvas failure 也不得再次修改 Scientific Model。

旧版 Judge Evidence assessment ledger 只作为 historical compatibility 数据读取，不再由 current `terminal_assessment` 写入，也不再参与 current scientific state。当前 Judge 的唯一 durable scientific output 是 branch verdict / rationale / optional single objection；其事务边界与 Agent invocation closure 一致。

thread 不可跨 Cycle soft-persistent。进入新 Cycle 时，科学上下文只从 Research Plan / Scientific Model 与 Canvas 重建。同 Cycle 的 synthesis / continuation transient copy 只服务未闭合 Cycle 的 recovery；Cycle 闭合后不得再当作科学事实源。Cycle 内 thread 生命周期由 epistemic episode 决定，而不是简单按 role 永久复用：Research Lead qualification/selection 复用一个 episode，但 model update / cycle synthesis fresh；Challenger 每次独立 review fresh；Investigator 的连续内部科研 episode resume；**每个独立 external Literature task fresh**，其 `task_type / triggered_by / gap_id` 必须进入事件日志；Reporter 复用；Judge 的 Branch adjudication→reassessment 复用，而任务级 `goal_completion` 必须 fresh。Research Lead 的 context-budget/provider/branch rollover metadata 只能用于审计，不能恢复上一 Cycle 的对话。

Investigation attempt 只是一条科学返工/审计边界，不是 live thread namespace。若 backend 无法恢复既有 episode，可从 checkpoint、Hypothesis Lock、Evidence refs 与 artifacts 建立 fallback thread；这种异常恢复必须留下 provenance。

## 11.1 跨版本恢复

每个 invocation、domain record 和 checkpoint 至少记录适用的 `contract_id/version/hash`、domain schema version、checkpoint format version、prompt template version、runtime build ID 和 repository commit（可获得时）。

恢复不得用最新 validator 静默重解释旧 artifact，也不承诺在没有冻结代码、依赖、模型和工具镜像时重放旧 runtime。恢复先做 compatibility inspection：

- `compatible`：无损读取并继续；
- `migratable`：保留原 artifact，显式迁移到当前 Canonical Domain，写 migration record 和新 recovery checkpoint；
- `incompatible`：设 `protocol_health=migration_required`，等待 recovery intervention。

## 11.2 Invocation 与 Artifact Ledger

Agent、local process、Slurm job 和 external service 调用统一登记：`invocation_id`、kind、contract ref、input hash、attempt、external process/job ID、state 和 commit state。对具有 task-local 写权限的 invocation，SDK 在调用边界记录 workspace 文件快照并生成 `invocation_artifacts/<invocation_id>.json`，至少包含 created/modified 文件的 canonical path、size 和 content hash；这一 manifest 是 artifact identity 的唯一机器事实源。Evidence 只通过 `producer_invocation.invocation_id` 间接关联这些产物，不要求 Agent 再声明 finding→path 映射。

执行语义是 at-least-once invocation + idempotent commit + artifact reconciliation，不承诺 exactly-once。恢复规则：

恢复、审计和异常自动修复彼此独立。system failure 与所有 `NEEDS_ATTENTION` 都必须先写入当前 Cycle 的统一 `cycle_anomalies.jsonl`，再进入 fresh-Codex repair handoff；post-Cycle audit 仍是独立只读审计且默认关闭。异常 repair handoff 默认启用，并以 `incident_key` 做幂等去重：同一 incident 只允许一个 active/started repair Codex，重复 lifecycle 投影只能复用既有 handoff，不得重复开窗。修复 Codex 的执行配置固定为官方 Codex `gpt-6-luna`、`model_reasoning_effort=medium`，不得继承当前科学 Agent 的模型档位或用户日常 Codex reasoning 配置。Repair Codex 可修复明确的 SDK/runtime/contract/tooling Bug；若异常触及科学问题，它不得直接代替正式角色输出 scientific verdict 或修改 Scientific Model、Hypothesis、Decision、Evidence、Canvas，而应将 unresolved work 转成一次性 task-local `attention/operator_guidance.md`，由安全恢复后的首个适当 Research Lead / Investigator / Literature / Judge 阶段依其既有 authority 处理。Attention takeover 开启代表任务所有者授权 AI 在该 Task 已有 Goal、冻结 Hypothesis / scope / estimand、allowed inputs、配置的工具能力及预算内继续处理并尝试恢复；它不授权扩大这些边界、访问未授权材料、伪造缺失数据或代签科学裁决。现有数据不足时应在边界内检索/推导；仍无法获得的数据明确记为限制或不可识别性，并交正常 Investigator/Judge 流程裁决，不因此由 Repair Codex 等待人工。只要恢复 checkpoint 与 runtime ownership 校验通过，handoff 必须给出下一科研 stage 的可执行 guidance 并自动 `resume`；只有状态损坏、身份/ownership 冲突、或不存在安全可恢复 checkpoint 等真实运行安全问题可保留 blocked/waiting。已被 SDK 当场成功修复的 minor JSON/contract anomaly 仅登记，不触发额外 Repair Codex。

Observability 恢复同样遵守 same-Cycle continuity：resume 必须继承并继续累计已有的 per-agent elapsed time、Cycle/project elapsed 与 agent timeline；不得把历史 agent 时间重置为 0。旧版本状态若已经丢失 per-agent elapsed，可用该 Cycle 的 `runtime_events.jsonl` 中完整的 `TURN_STARTED`/`TURN_COMPLETED` 与 terminal event 配对做确定性重建，且未闭合 turn 只能截止到 terminal event，不能把离线等待时间算入 Agent 执行时间。

`cycle_index` 是 **当前 Workflow invocation 内的观测序号**，不是 Cycle 的 durable identity，也不参与预算 authority。resume 一个未完成 Cycle 时，该 Cycle 消耗新 invocation 的第 1 个 slot，因此 collector 必须以当前 invocation 的计数刷新 `run_state.json` / `runtime_summary.json` / `CYCLE_STARTED` 投影，禁止从该 Cycle 旧 `run_state.json` 恢复历史 `cycle_index`。后续同一 invocation 新建 Cycle 依次为 2、3……；durable identity 继续由 `cycle_id` / `cycle_number` 提供，Cycle 预算仍只由 Workflow 的 `invocation_cycle_index` 决定。Observer、TMLab 与运行统计只能消费这一投影，不得反向用观测 `cycle_index` 驱动科学流程或预算。

- completed 且 input/hash/artifact validation 一致：reuse；
- external identity 仍运行：reattach；
- process 状态未知但 artifacts 完整：reconcile 后 reuse；
- partial、hash mismatch 或 validation failure：quarantine 并 rerun。

## 11.3 事务顺序

事务边界依次为 Cycle terminal Judge decision、Scientific Synthesis、Scientific Model keep/replace commit 和 projections。Cycle terminal decision 可以是 `milestone_advanced`，因此不必等价于整个 H terminal。每层必须可幂等恢复；下游失败不得重做已提交的上游事务。

---

# 12. Managed Runtime 与 TUI

task mode 的运行架构为：

```text
CLI run / resume
      ↓
Managed Runtime Launcher
      ↓
Supervisor
      ↓
Headless Runner
      ↓
Workflow / Agents
```

Codex **认证身份**与 TheoryMaster **运行配置**是两个独立边界。`~/.codex-accounts/account-1`、`account-2` 只保存各自认证身份，`current` 作为原子 selector；TheoryMaster 使用稳定的独立 `CODEX_HOME`（默认 `~/.codex-theory-master`，可由 `THEORYMASTER_CODEX_HOME` 覆盖），不继承日常 `~/.codex` 的 Skill、Plugin、rules 或 workspace 配置。切换账户只改变认证/额度来源，不改变 TheoryMaster 的 Skill、Plugin、历史线程或工作区语义；TM runtime 默认关闭 apps/plugins，并把 provider metadata 作为 account-neutral runtime 配置维护。

任务级 `.theory-master/runtime.json`、process identity、locks 与 heartbeat 共同定义运行所有权和 health。Supervisor 保持单一 owner，并可在 runner 非预期退出后进行有界 recovery/resume。

## 12.1 Codex 双账号额度自动调整（TMCR-20261008-002）

Codex 明确额度耗尽仍先由 Workflow 收口为 `status=paused / phase=provider_quota_exhausted / resumable=true`，并持久化失败 provider、失败 Codex account 与最近安全 checkpoint。该 PAUSED 是科研 Workflow 的安全事务边界；额度恢复控制器不得绕过这一持久化边界，也不得把额度耗尽升级为 `NEEDS_ATTENTION`、scientific failure 或 system repair。

额度自动调整是**可选的 task-level runtime control**，由任务 `.theory-master/env` 中 `THEORY_MASTER_QUOTA_AUTO_ADJUST=1|0` 持久化，默认关闭。开启后，由 Managed Runtime Supervisor 在 child Runner 已安全退出后接管 `provider_quota_exhausted`：若另一 Codex account 可用，则只原子切换 `~/.codex-accounts/current` 的认证 selector，并以同一 Task / Cycle 的 `resume` 重新启动 Headless Runner；不得切换 TheoryMaster `CODEX_HOME`、Skill、Plugin、rules、workspace、历史 thread 或其它 account-neutral runtime state。Supervisor 的额度恢复循环独立于普通 child-crash recovery budget，不计入 `MAX_CHILD_RECOVERIES` 一类有界故障重试。

额度可用性探测必须使用 account-local、无科研副作用的 Codex `account/rateLimits/read`，不得为“试额度”创建 Research Lead / Investigator / Judge 等科学 Agent 调用。每次显式额度失败必须记录**实际失败账号**，不得在失败后仅凭当时的全局 selector 猜测；Codex client/app-server 建立时冻结实际认证账号 provenance。最终 selector 复核与切换必须在同一个跨进程 account-switch lock 内完成，client/app-server 的账号快照与建立也必须受该 lock 保护，避免多个 Task 依据过期 selector 相互来回切号；纯 observability 读取不承担切换 authority。若另一个账号的探测结果明确可用，立即切换并 `resume`；若探测未知但账号认证有效且本轮尚未被显式判定耗尽，允许最多一次真实 resume 试用，该账号若同样返回明确额度耗尽即纳入本轮 exhausted set。

两个账号均不可用时，Supervisor 保持 task runtime owner，进入 `WAITING_QUOTA` 运行态，不启动科研 Runner，只按有界低频轮询读取两账号 rate-limit 状态；任一账号恢复后原子选中它并自动 `resume`。等待期间必须持续读取 task-level 开关：用户关闭“额度自调”时 Supervisor 干净停止自动等待，科研状态仍保持可人工 `resume` 的 quota PAUSED。等待/切换控制状态写入 `.theory-master/quota-auto-adjust.json`，仅属于 runtime observability/recovery，不进入 Scientific Model、Canvas scientific authority 或 Scientific Current Flow。

TUI 顶部 identity 行必须显示 `额度自调: 开启|关闭`，并提供热切换控制。TUI 只修改 task-level persisted setting，不直接承担账号选择或 resume authority；实际切号、等待与恢复仍由唯一 Supervisor owner 执行。TUI 可投影 `idle | switching | resuming | waiting_refresh | disabled | unsupported_provider` 等 controller 状态及下次探测时间，但这些均不得改变科研 `status/phase` 的含义。

- **WAITING_QUOTA 可见性（2026-10-08）**：当 task 的额度自调处于开启状态，并且 Supervisor 的 controller 快照 `status=waiting_refresh` 时，TUI 自动打开一次额度等待弹窗。弹窗只读取 `quota-auto-adjust.json` 中 Supervisor 上次查询结果，展示当前选中 Codex 账号、触发额度暂停的账号，以及 account-1/2 各自 `primary/secondary` 剩余百分比、可用性、恢复时间、查询时间与轮询间隔。未查到的额度应标记「不可用/未知」，不可显示假定剩余值；不查询 auth 或调用计费 API。等待期间已有弹窗内容随快照刷新，用户关闭后在同一 waiting episode 不重复弹出；状态离开 `waiting_refresh` 自动收起，再次进入时可以重新提醒。不允许弹窗覆盖用户正在操作的其它 modal，或改写 Supervisor 切号/恢复权限。Collector live snapshot 与 FileSource attach 都需投影最新 controller 数据。

TUI 是**独立 UI 进程**，但绑定同一个 task / Master runtime；正常 `theory_master tui` 应 reconnect/focus canonical TUI，而不是创建与真实运行脱节的只读副本。关闭或重开 TUI 不应等价于杀死科研 runner，TUI 也不是 scientific/runtime authority。`theory_master run/resume` 的 launcher 必须区分 **runtime launch** 与 **TUI attach** 两个结果：一旦 managed runtime 已经成功取得 owner/heartbeat 并进入 `RUNNING`，随后 TUI attach/startup 失败不得把整个启动命令伪装成“runtime 启动失败”；CLI 应明确报告“runtime 已启动、TUI attach 失败”，并提供可重连状态。只有 runtime 本身未成功建立时，run/resume 才以启动失败语义返回。
NEEDS_ATTENTION modal 绑定 `(cycle_id, attention_key)`，避免跨 Cycle 复用旧弹窗身份。Snapshot 离开 `NEEDS_ATTENTION` 时，TUI 只 dismiss 与该身份对应的 `AttentionScreen` 并清空 `_shown_attention`；其它 modal 不得因 attention 状态变化被关闭。若 AttentionScreen 暂时被另一 modal 覆盖，待其成为活动屏幕后再 dismiss。
Investigation 的 TUI timeline 以**科学 Round / handoff** 为单位，而不是以每次 Investigator LLM turn 为单位。同一 `attempt + round + thread` 中的多个 internal turns 合并为一行 Investigator，并可显示累计 `N turns`；只有真实 Round boundary 才产生新的 Investigator/Literature history row。高频 LLM/tool 活动仍保留在 Last action / runtime events 中用于审计。

pause、interrupt、kill computation 的语义必须真实：pause 在安全边界停止继续调度；interrupt 应取消当前 Agent invocation；kill 应真正终止目标 external process/job。若 backend 当前只能做到 safe-boundary control，CLI/TUI 必须显示真实语义，不能把“请求停止”描述成已经完成异步 cancel/kill。

---

# 13. Context、模型与成本

角色接收 bounded context projection，而不是反复注入完整 transcript。Research Goal、Research Contract、Required Context、locked H 与冻结判据属于关键上下文；Judge 只接收 locked H、冻结判据、optional prior objection、SDK 生成的 compact `investigation_result` 与同一 H 的 bounded `prior_adjudicated_context`，不得默认注入 Evidence Manifest、Coverage Matrix、artifact paths 或原始结果文件。跨 Cycle history 必须有显式上下文预算：优先保留每个 prior Cycle 的 `cycle_id / decisive_test / verdict / established_result / scope / limitations` 固定摘要，再只对当前裁决真正相关的 prior Evidence 展开有限 detail；不能让一个长寿命 H 因累计 Cycle 数量而无界线性回放全部 Evidence，也不能仅按“最近 N 个 Cycle”粗暴截断而静默丢失仍具决定性的早期反证或关键支持。

长 epistemic episode 优先 resume 原 thread，并使用 provider 可验证的 native context management / compaction；不能用普通“让 LLM 总结历史”的 prompt 冒充 compaction。compaction、token、cost、retry、provider/model 与 thread metadata 只进入 runtime observability，不进入 Scientific Model 或 Canvas 科学正文。

模型 routing 的官方 Codex 主梯度使用三档 profile：`1=test | 2=codex | 3=advanced`。profile 的具体 role→model 映射以 `theory_master/model_profiles/*.toml` 为机器权威，并允许显式 provider override / per-role route overlay；设计文档不复制每个模型常量，避免再次漂移。具体 Master 当前模型是运行配置，不属于科学设计常量。

---

# 14. 当前实现边界

本文件描述目标架构，不为了逐行贴合当前代码而降低设计要求。当前仍需继续收敛的主要边界是：interrupt/kill 的真实异步语义；`runtime_state/protocol_health` 逐步取代 legacy `status/phase`；更完整的跨版本 contract/build/checkpoint compatibility 与 migration；Invocation Ledger 对 local process/Slurm/external service 的统一覆盖；以及 `ProposedModelAmendment` 的正式人工科学修订流程。Cycle criterion 已收敛为新 Cycle 唯一的 `C-1`；历史 `M-S* / M-F*`、`C-S* / C-F*`、`hypothesis_terminal_criteria / H-S* / H-F*` 仅保留读取兼容。

这些属于实现欠账，不是应从设计中删除的原则。

---

# 15. 核心不变量

1. 同一时刻最多只有一个 active Main Hypothesis。
2. 只有通过 Qualification 并完成 Hypothesis Lock 的 H 才能进入 Investigation。
3. Lock 后的 H 在当前 Cycle 内不可修改。
4. Challenger 只负责 candidate review，不负责选择和裁决。
5. Judge 只评价当前 Investigation Result 是否足以裁决当前 H；不做 Evidence/artifact 审计，不更新长期模型，不选择下一个 H。
6. Scientific Model 只在 terminal Judge 后由 Research Lead 通过 `keep | replace` 协议维护。
7. Branch `supported` 只代表当前冻结 H/criterion 的裁决，不代表整个研究 Goal 已完成。
8. Candidate、finding 和 established finding 必须严格区分。
9. 计划、Agent 意见、命令输出和未验证文献不自动构成 Evidence。
10. 长期状态不得只依赖 Agent thread。
11. Canvas 不充当 runtime log 或数据库。
12. 所有科学结论必须在陈述中自包含必要的成立条件、适用边界和不确定性；冻结 Hypothesis.scope 仍保留。Finding 不单列 scope/limitations。受保护 Evidence 身份应由可信 provenance 校验，artifact 溯源由 producer invocation + SDK artifact manifest 自动提供，不让 Agent 手工维护路径。
13. Agent recommendation 不直接等于 runtime action；SDK 必须执行 dispatch policy。
14. Agent output、Canonical Domain Record 和 persistence record 是三个不同边界。
15. Evidence 不原地覆盖；修订通过 append-only lifecycle event。
16. Judge `verdict=null + objection` 只是一次有界 rework 请求，不是 terminal scientific verdict；未授权/无必要返工时必须落为 terminal `open`。
17. Judge 返工必须重新进入完整 Investigation Router；Judge 只描述科学缺口，不输出 executor、Evidence ID、artifact path 或 execution plan。
18. thread 按 epistemic episode 管理；Investigation attempt 不是 thread 边界，fallback/new thread 必须有明确科学或 recovery provenance。
19. Branch Judge 只使用当前 H、本 Cycle 冻结的 `decisive_test / Cycle criteria` 与 Investigation Result；整个研究 Goal 尚未完成不得成为当前 Investigation 返工理由，Cycle criteria 也不得被解释为独立 Milestone 科学对象。
20. 投影失败不得重做 Judge 或 Scientific Model commit 已提交事务。
21. 旧 artifact 必须经过 compatibility inspection，禁止被新协议静默重解释。
22. benchmark delivery、submission interface、文件格式/hash/canary 等交付验收属于 TBH 或其它外部适配层，不得反向污染 TM 的科学流程。
   - 对用于 **deterministic regression** 的长期 fixture，可以在 fixture 自己物化的 `research_plan.md` 中额外冻结足以唯一决定结果的数值协议（例如 CV folds、shuffle/seed、候选 grid、metric/weight、preprocessing 与 tie-break）。这类约束属于该 regression fixture 的 frozen task input，不得写入通用 Investigator / Lead / Judge Prompt。若 fixture 同时物化 `benchmark/specification.json`，应以独立 `regression_protocol` 或等价显式字段同步说明，并标明它不是公共 benchmark 原题语义；`scientific_source` 继续忠实保留公共题目。
23. 跨 Cycle scientific authority 只有两层：Research Plan / Scientific Model，以及 Canvas。`frontier_state.json` / `research_state.json` 只是指向 Canvas 或当前 Cycle artifact 的 workflow runtime pointer。
24. Goal Completion 只产出 `passed | not_passed` 与 `rationale`，不产出 gap，不创建 planner state。`goal.not_completed` 只记录 `verdict`、`rationale`、`source_cycle`。
25. SQLite V2 的 `frontier_maintenance` 维护分支身份的 `frontier_status`，不以图形叶子身份为限；`frontier_proposal` 才决定下一 Cycle 的新 H 候选。

---

## 15.1 系统修改与独立审计登记（CLI 当前规范）

**现行决定：旧 TMCR 正式提请机制已经废止。** 从本规范生效后，不再为新的 TM 修改分配 `TMCR-YYYYMMDD-NNN`，也不再要求创建 `change_requests/` 的 JSON manifest、三文件提请、待审查清单或 Observer 网页审批。原有 TMCR 编号、历史文件与旧版 Current Flow request 投影保留为**历史追溯资料**，不得作为新修改的管理入口、实施门槛或审计通过依据。

**SDK-first 不变**：修改会影响科研语义、Agent Prompt/Identity、Stage Contract、Routing、状态、恢复、成本预算或其他 runtime authority 时，先查明当前机制与问题、原设计动机和预期不变量，更新本设计文档或对应权威设计，再实施最小必要改动和针对性测试；代码改动及验收证据通过 Git 与审计记录可追溯。纯展示型 Observer UI 改动可按一般工程流程处理，不触发网页 TMCR。对科学运行不得篡改原 verdict、Evidence 和历史轨迹。

**独立审计待办统一交给 TMA CLI**，不是重新命名 TMCR：`tm-audit-review` 负责科学轨迹和系统回归的审计方法；用户要求登记或跟踪的未通过/未执行/阻塞事项写入 `tool/theory_master/audit/pending_reviews.json`，编号为 `TMA-YYYYMMDD-NNN`。这里的 TMA 是待审证据队列，**不是代码修改批准流程**；单纯讨论或实施系统修改不自动生成 TMA 记录。对于已登记的项目，只有全部适用检查项具备实测证据、审查结论通过，才能 `close`；失败和证据不足不得关闭，复发通过 `reopen` 处理。旧 TMCR 历史事项若需继续复审，可根据用户指令作为来源登记到 TMA，保留旧 ID 的引用但不生成新的 TMCR。

从仓库根目录使用下述管理入口（独立于科研任务的 `tm run/ls`）：

```bash
PYTHONPATH=tool/theory_master python -m theory_master.audit_queue list
PYTHONPATH=tool/theory_master python -m theory_master.audit_queue show <TMA-ID>
PYTHONPATH=tool/theory_master python -m theory_master.audit_queue add --title "..." --priority P1 --owner "..." --stage "..." --description "..." --source "..." --check "..."
PYTHONPATH=tool/theory_master python -m theory_master.audit_queue check <TMA-ID> C01 --result pass --evidence <evidence-path> --reviewer <reviewer>
PYTHONPATH=tool/theory_master python -m theory_master.audit_queue close <TMA-ID> --reviewer <reviewer> --summary "..."
PYTHONPATH=tool/theory_master python -m theory_master.audit_queue reopen <TMA-ID> --reviewer <reviewer> --reason "..."
```

不得把未执行的测试标为通过；验收范围、证据与签署人的真实性以 `tool/.codex/skills/tm-audit-review/SKILL.md` 和审计队列 CLI 校验为准。网页仍可保留历史可视化能力，但**不登记、不审批、不决定审计状态**。
---

# 16. 一句话架构

> **TheoryMaster 以 Research Lead 管方向、Challenger 管 Qualification、统一 Investigator 与外部 Literature 获取 Evidence、Judge 独立裁决、Scientific Model 跨 Cycle 压缩知识；SDK 用 versioned contracts、Judge 授权反攻、append-only ledgers、managed runtime 与幂等事务，把这些科学过程组织成可恢复、可审计、可持续演化的科研系统。**

## 源码与运行数据边界

TheoryMaster 源码树只保存全局 Engine 资产与 system-level Skills；所有 Task 运行数据必须写入具体 Task 目录。包括 Cycle state、events、artifacts、audit report\/log、recovery relay、supervisor batch。Task-local Skills 也归具体 Task 所有，放在该任务 config 目录的 `.theory-master/skills/`，不是 Engine 全局资产。SDK 对位于 TheoryMaster 源码树内部的 research_root 直接拒绝启动。

Task-mode 默认仍由 managed runtime / supervisor 托管。CLI 另提供通用的 `run/resume --foreground` 传输边界：它让同一个 headless Workflow 留在调用进程树中，便于外层 namespace、benchmark harness 或调试器控制生命周期；**foreground 只改变进程托管方式，不退出 TheoryMaster control plane**。foreground runner 必须登记同一 Task ID / Cycle 的 runtime lease，包括 runner PID/process identity、heartbeat、current Cycle、resume count 与 active-runtime segments。异常中断后再次 `resume --foreground` 时，新进程 episode 重新登记为 LIVE，并从持久化 checkpoint/thread 恢复；`active_elapsed_s` 只累计实际运行 segment，不把离线间隔计入科研运行时间，TUI/`ls` 据此恢复真实 LIVE/PID/计时状态。该机制不改变任何科学合同、Agent prompt、Router、Evidence 或状态机。

## 2026-10-08 Investigator 单次提交与 Judge 独占反攻（TMCR-20261008-001）

**当前作用。** 当前 Investigation 在一个 Round 内允许多个 `internal_turn`：Investigator 每次提交 `remaining_scientific_question`，B 层把它封闭为 `remaining_gap.kind`，SDK 据此继续同一个 Codex thread、转 Literature 或送 Judge。Turn 同时提供中间 checkpoint、进展保护与跨角色路由。

**目前问题。** 统一 Investigator 已能在同一 Codex invocation 内自主使用 theory、numerical、local-source 与工具。可执行的下一步仍经 JSON 交卷、B 分类、SDK 路由再 resume，同一研究动作被外围循环切断；`remaining_scientific_question` 容易成为提前交卷的工作流入口，Judge 之前形成预裁决。

**原设计动机。** 旧 Theorist/Numerical Specialist 分立时，数值工作需要有界 self-continuation；Scientific Turn 与语义封闭也让 Agent 只填科学内容，由 SDK 做 bookkeeping、checkpoint 与预算保护。统一长寿命 Investigator 后，这些旧假设已不再成立。

**目标语义与边界。** Hypothesis Lock 后，Investigator 在一次调用内完成当前可执行且必要的研究，自主选择方法，直到提交能被 Judge 审查的自包含科学 Findings（及必要的可验证 Evidence 身份）。科学不确定性、适用条件及不能外推的部分属于 `findings[].statement` 的内容，不再分别输出 `findings[].scope` / `findings[].limitations`；Investigator contract 也不输出 `remaining_scientific_question` 或 workflow executor。SDK 仍负责调用预算、恢复、Evidence 登记、确定性合同闭合和持久化，但不以 gap taxonomy 判断科学工作是否足够。提交后的下一科学 authority 是 Judge；Judge 的 terminal verdict 收口，非 terminal objection 才授权下一 `investigation_attempt`。`needs_external_scholarship=true` 时先 Literature 再 Investigator rework；否则直接 resume Investigator rework。Literature 不由 Investigator gap 主动路由。Research Lead 仍决定下一 Cycle。

**迁移与不变量。** 删除 `internal_turn` 的 TM workflow 语义、12-turn guard、same-round continuation、`gap_progress`、routing 用 `recent_gap_history`、`gap_resolvability` 和 current `remaining_gap.kind` taxonomy；旧状态只读兼容，不把旧字段带入新 prompt。保留 `investigation_attempt`、`judge_rework_count`、Invocation Ledger 与底层 provider turn telemetry。Round 暂留为预算或执行计数，不再表示科学权威。每次 Investigator 完整提交只登记一次 Evidence，再进入 Judge；Judge objection rework 必须有界，预算耗尽仍有确定性收口。验证要覆盖首次提交、Judge 普通反攻、外部文献反攻、恢复、预算耗尽、旧状态读取，以及 Observer 对新流程的显示。

**预算与 Attention 硬阻断。** 单次完整 Investigator invocation 仍可能消费大量底层模型 Token，Round/attempt 不是 Token 上限。SDK 应提供可配置的执行成本保护：在准备任何新的付费 scientific invocation 前依据已观测 Token/估算成本作确定性预算检查；对缺乏实时可信计量的单次长 invocation，只能提供事后/后续调用守卫，不得假称精确中断。已经存在安全 checkpoint 时，应优先复用 Evidence 进入 Judge，不因 budget stop 重启 Investigator。对同一 hard Budget/Turn guard，自动 Attention takeover 及普通 auto-resume 必须检查阻断事实是否已经改变；未改变则稳定保留 STOPPED/NEEDS_ATTENTION，并记录 suppressed 原因，不再产生重复 Repair Codex 或新的科研调用。旧 internal-turn-limit 若经新版本正式废止，可在重新验证已提交 Evidence 后合法迁移到 Judge。

**Investigator 独立预算审批（2026-10-08 修订）。** 默认初始与单次续批额度为 **50,000k tokens**（可通过 `THEORY_MASTER_INVESTIGATOR_BUDGET_TOKENS` 调整）；展示层采用 k 单位，账本及计量仍使用精确整数。达到额度时只暂停新的 Investigator 付费调用，保留原安全 checkpoint / thread，自动启动独立轻量 Budget Reviewer（GPT-6 Luna / medium），不让 Reviewer 取代科学 Judge。有效的新科学 Findings 或注册 Evidence 与合理的有界下一步足以续批；暂时未完成证明、仍有未解决问题不单独构成拒绝理由。严重流程/工具故障、科研边界违规、已证实的反复空转或没有可执行下一步则停止。续批只由 SDK 内部受约束的原子 ledger writer 提交，不允许伪造 Evidence/Findings 或对同一已审进展重复计额度。**2026-10-08 后续架构修订：Budget Reviewer 是 SDK 内部辅助角色，不是 tmux/Attention 外部接力进程**；SDK 在下一次 Investigator 付费 invocation 之前先冻结并持久化预算边界，在同一 Workflow 内使用只读 fresh 模型线程（GPT-6 Luna、medium）审核精简的科学进展、剩余目标和下一项有界测试。模型只返回结构化 `approve|reject` 与依据、注册进展 ID；SDK 负责严格校验、落盘并原线程继续，无新的 tmux、CLI 子进程或额外 `tm resume`。审批模型或格式失败时 fail-closed、记录 `investigator_budget_review_failed`，不偷偷发放额度；拒绝时保留 `NEEDS_ATTENTION`，且 Attention takeover 不会对预算审批进行外部接管。已经审批的 ledger 在重启后仍生效，不能重复续批；历史 v1 账本中的 tranche 保持原有冻结值。

**科研保全的不可逆边界**：Budget Reviewer 提前拒绝、Supervisor 不同意或第 6 份耗尽，须由 SDK 冻结合法终止原因与原检查点，只有新 Cycle 且没有已可送 Judge 的正式研究提交时才记录 `budget_auto_preserve` 授权标记。多 Worker Scheduler 仅对无运行进程且被精确标记的 `NEEDS_ATTENTION` 调用既有 `preserve_release`，预先写不可重复调用的 receipt；Archive Stage 是独立、受控的只读科研模型调用，完整结果/Canvas 验证成功后方能 Release（失败原 claim 不释放、不反复付费）。每次 Preserve 成功或失败都登记带 Cycle/Worker 的重要事件，作为持久可见提醒。Provider 账户额度与预算审批不同层，独立处理。模型审查失败或源证据不可读，一律保留检查点，不虚构资源许可或自动 Preserve。

### 2026-10-19 Budget Reviewer 引用修复、拒绝登记与科研收口（TMC-20261019-2537a5d6940b / 03236dd417a3 / cafb32f64a73）

## 2026-10-08 Qualification prerequisite 与 cannot_repair handoff（TMCR-20261008-003）

**当前作用。** Challenger 在 Hypothesis Lock 前独立审查 Candidate 与冻结 `decisive_test` 的 Goal alignment、可证伪性、可识别性和判别力；若给出 `revise`，Research Lead 最多调用一次 `selection_repair`，只能在原科学边界内尝试关闭冻结 blocker。SDK 负责保留 Candidate、冻结 `decisive_test`、审查结果及 Qualification 状态，并在成功后继续 Lock / Investigation。

**目前问题。** 旧语义把 `selection_repair` 返回 `cannot_repair` 直接等同于 Candidate 不合格并停止 Cycle，甚至触发 `NEEDS_ATTENTION`。这混淆了“当前有限修补权限内无法关闭 blocker”与“科学命题不可研究”。同时，尚未推导或操作化的 prerequisite 容易被 Challenger 提前提升为 qualification blocker，阻断本应由 Investigator 在冻结科学边界内完成的工作。

**原设计动机。** `selection_repair` 最初用于避免重复 Challenger audit 与移动 goalposts；将无法修复视为 fail-closed，曾保护 Lock 不接收未关闭的科学 blocker。该假设把 Qualification 当作必须消除所有前置工作后才可进入 Investigation 的门槛，没有区分修补权限与科研权限。

**目标语义与边界。** 已明确列入冻结 `decisive_test`、可在读取目标响应前解决、且不需改变 Hypothesis / scope / estimand / allowed inputs 的 prerequisite，属于 Investigation 工作，不能仅因尚未完成就要求 `revise` / `reject`。只在 Hypothesis 含义不确定或不唯一、允许 response leakage / post-hoc 定义、没有能改变 Hypothesis 判断的可证伪结果、必须改变冻结科学边界才能解决，或 prerequisite 本身是必须先独立裁决的新科学命题时，才允许因此阻断 Qualification。

合法 `cannot_repair` 仅表示 Challenger blocker 超出 `selection_repair` 的有限权限；它不构成 Candidate 的科学否定，不停止 Cycle，也不触发 `NEEDS_ATTENTION`。SDK 原样保留 Candidate、原始 `decisive_test`、Challenger 完整审查记录及 blocker ledger，将原 Candidate 继续送入 Hypothesis Lock / Investigation；Investigator 在冻结的 Hypothesis、scope、estimand 和 allowed inputs 内处理相关 prerequisite。原 Challenger 的 `revise` 与 blockers 以 deferred qualification context 保留，不得被改写成 blocker 已解决的科学结论。非法 contract、resolved closure 不完整或越过冻结边界仍按合同错误 fail closed。

**原设计迁移。** 本请求部分 supersede `TMCR-20261006-005` 中“cannot_repair 必须进入 NEEDS_ATTENTION / no qualified candidate、禁止 Hypothesis Lock”的语义；该旧行为只保留为历史解释。它不撤销对 malformed repair、未覆盖 blocker closure 或冻结边界漂移的确定性校验，也不增加 Challenger audit 或新的 Agent stage。

已有 Cycle 若在旧策略下以 `qualification_status=unqualified` 停止，但已持久化合法 `selection_repair outcome=cannot_repair`、原 Candidate 与 Challenger review，`resume` 应以该持久化材料建立相同的 passthrough handoff，继续原 Cycle 的 Lock / Investigation；不得重新生成 Candidate、重复 Challenger audit 或创建新 Cycle。无法验证这些持久化对象一一对应时，才走现有的安全 Qualification restart。

**流程影响。** `CHALLENGER` 增加 prerequisite 判定边界；`REPAIR` 的合法 `cannot_repair` 路由到 `AUTO`，以原 Candidate 进入 `LOCK` 和 `INV_CTX`；只有全部候选被 reject、无可继续 Candidate 或 contract/boundary validation 失败时才进入 `STOP_Q`。无新增科学裁决 authority。

**验证要求。** 检查未完成但符合冻结边界的 prerequisite 不会单独产生 blocker；五类允许阻断情形仍能给出 `revise` / `reject`；`cannot_repair` 后原 hypothesis / scope / `decisive_test` 与完整 Challenger 审查记录均可追溯，并继续 Lock / Investigator；全部 reject 与非法 contract 仍安全停止；Current Flow 对应节点与路由投影一致。

## 2026-10-08 Attention takeover 自动科研 handoff 与恢复（TMCR-20261008-004）

**当前作用。** `NEEDS_ATTENTION` 由 SDK 持久化安全停机并登记 incident；开启 takeover 时，独立 fresh Codex 检查 checkpoint、events、task artifacts 与运行代码。它可以修复明确的工程错误，或写一次性 `operator_guidance.md`，随后由常规 `resume` 的第一位 Agent 消费。

**目前问题。** 现有接管 prompt 要求：若还需要新的科学判断、数据或授权，就标记 blocked 并等待人工。这样即使当前任务角色、已配置工具与允许输入能在冻结科学边界内继续处理，Attention takeover 仍会把科研工作人为停在 relay 窗口，不能自动恢复。

**原设计动机。** Attention repair 与科学 authority 分离，是为了防止工程修复 Agent 伪造 scientific verdict、改写 Hypothesis / Scientific Model / Canvas，或越过 task input boundary。该 authority 分离仍然必要；问题在于把“Repair Codex 自己不能裁决科学问题”错误扩展成“不能把问题交给正式科学角色并自动恢复”。

**目标语义与边界。** 开启 Attention takeover 表示任务所有者授权 AI 在该 Task 已有 Goal、Research Contract / Final Gate、冻结 Hypothesis / scope / estimand、allowed inputs、已配置工具能力与预算内完成自动诊断、检索或推导、形成科学阶段 handoff 并尝试恢复。遇到新科学判断时，Repair Codex 必须指向具有既有 authority 的 Research Lead / Investigator / Literature / Judge，由其正式合同承接；Repair Codex 本身不写科学 verdict。缺失数据先在任务边界内查找或获取；确实不可用时明确表达为限制 / 不可识别条件并继续已有科学流程，不伪造数据。Attention 设置不允许自我授权扩大 allowed inputs、访问受限数据、修改假设范围或使用未配置能力；这种材料按不可用处理，而不是把 relay 留在人工等待。checkpoint、任务身份及 runtime ownership 校验通过时，handoff 必须写出下一 stage 的具体指导并自动 `resume`。只有状态损坏、active-owner 冲突或无安全 checkpoint 这类运行完整性问题允许继续 blocked/waiting。

**成本与风险。** 正常科研路径不增加调用；每个新的 attention incident 仍由既有幂等去重最多启动一个 `gpt-6-luna / medium` Repair Codex，之后可能自动恢复已有配置的科研调用预算。风险是错误 guidance 会导致无效计算或多余研究；缓解方式是只传 task-local 一次性指导、严格遵守冻结科学边界，且所有科学判断仍由原有角色合同和 Judge 负责。

**迁移与验证。** 更新 `TMCR-20261007-013` 建立的 attention relay blocked 条款；任务级开关语义继续保留。验证需覆盖：需要新科学判断、数据缺口、需要额外授权提示三种情况均形成有界科学 stage guidance 并在安全 checkpoint 自动 resume；真正超出 allowed inputs 的数据不得被获取或伪造；科学 verdict / Hypothesis / Scientific Model / Canvas 只能由既有正式角色及 closure 写入；active owner / corrupted checkpoint 不自动 resume；重复 incident 不重复 spawn。

## 2026-10-08 Attention 全面内置审查（后续正式修订，覆盖上节外部接力语义）

**权威改动。** 上节的“独立 fresh Codex / tmux 接管、自动修代码并外部 resume”已被明确废止。以后预算与非预算 NEEDS_ATTENTION 均为 SDK 内部执行控制，不经外部 tmux/Codex 或新启动脚本；FAILED 工程异常也不得再 spawn 外部修复 Codex。既有 `attention_relay`、`error_relay` 只做历史读兼容与 incident 登记，不能启动进程。Budget Reviewer 在 Investigator 下一次付费调用前，使用独立只读 SDK model turn 做证据+进展审计，再由 SDK ledger 续批，继续原 Thread，失败 fail-closed。

**非预算 Attention。** `no_qualified_candidate` 在同一 owning Workflow 内做一次独立只读辅助 `attention_reviewer` 检查：仅当未锁定假说、未使用重试额度、Reviewer 返回合法结构化 `retry_qualification` 且合乎现有 Goal/Contract/Final Gate 时，SDK 重置 attempt-local qualification 投影，保留 Challenger 的拒绝历史，交 Research Lead 重新提出新 Candidate，并由新的独立 Challenger 审核；最多一次 SDK 自动重提。不允许 reviewer 伪造资格通过、科学假设、证据或越权写入。再次拒绝或 Reviewer 格式错误 => `NEEDS_ATTENTION` 和内部审查记录，不能自旋。其他软 Attention 内审且保持安全 hold；硬资源 guard、损坏的运行状态、缺少安全 owner/checkpoint 等不得自动恢复，尤其不能靠模型文字绕过确定性约束。Supervisor 进程失去执行现场后仅登记和停机，正常恢复必须在合法 managed runner 内进行。

**字段守卫。** Challenger candidate_review 输出 `blocking_issues` 仅 `verdict=revise` 合法；`qualified/reject` 均省略属性。理由写在 `reason`；合同严格保持，使否决对象不因格式化问题重复运行审查。

**验收**：SDK 内审获准一次重选、同 Cycle 第二次拒绝停下、无预算重复发放、无 tmux spawn、异常输出 fail-closed、旧 handoff started 不可作为存活依据、用户一次性 guidance 不变成科学 Evidence。真实 provider 模型调用需随下次实际 Attention 触发验收，离线 Mock 不算真实科研证明。

### NEEDS_ATTENTION / FAILED 统一异常登记兜底（2026-10-08 后续规范）

`WorkflowStateMixin._persist` 是已持久化 Cycle 状态的直接登记入口：每当状态为 `needs_attention` 或 `failed`，即使没有 Collector / TUI / Supervisor、没有自动检修开关，也在本 Cycle 的 `cycle_anomalies.jsonl` 记录 `registered` 事件，保留 code、phase、reason、resume 信息及 Agent。Collector / Supervisor 兼容的原始事件登记仍存在，但复用统一 `incident_key` 不得产生重复首发记录。Registry 对完整的 read-deduplicate-append 事务加文件锁，避免多角色、Collector/Supervisor 交错写入丢失事件。

异常不能在内部自动复核后被静默删除；同一问题恢复时追加 `resume_attempt`（**不代表成功修复**），恢复后同一故障再次出现追加 `recurred`，两者共享同一 incident identity，便于审计问题和恢复路径。辅助 Reviewer 的 retry 只标 `retry_authorized` 而非 resolved。下游可由 Cycle 状态、最终科研裁决和后续事件判断是否真正闭环。

一次性指导在 `resume` 时从 `attention/operator_guidance.md` 加载为 `operator_guidance_pending`，由下一个正式 Agent `_call` 随 Stage Context 接收且成功调用后消费；它不是 Canvas 科学 Evidence，也不是独立 Stage。Canvas `External` 记录只承担非正式 context；未来若需要长期外部指导的可视化及定向加载，应在既有 `type: External` 下设计清晰的 `Guidance` 内容与 target/scope/active 状态，再以独立 SDK 投送机制连接到特定阶段，不得将其当成自动生效的 Judge / Research Lead 命令。当前未开启此新投送机制。

## 2026-10-08 停机告警与一次性方向 Guidance 的运行约定（最新版）

## TMCR-20261009-001：Human-visible Vault 与非同步科学数据隔离

**当前作用**：`task_home` / `research_plan.md` / `exploration.canvas` / `notes/` 组成可同步的知识笔记面；`research_root` 负责 checkpoint、Cycle workspace、runtime 与 Evidence 原始持久化；`benchmark_root` 负责复用的原始数据与科学参考产物。旧实现假定三者可以物理共址在 `task_home`，方便用一个指针发现全部任务资产。

**当前问题**：当 `task_home` 位于 Obsidian `笔记区`，`task_home/.theory-master/research` 与 `task_home/benchmark` 包含数百 MB 的 raw network、pickle/data、NPZ、实验事件逐行记录和 checkpoint；Obsidian 同步扫描整棵目录，重复搬运计算中间文件。Canvas 只应投影结论、节点与引用，绝非数据归档容器。

**原设计动机**：将 task_home 与 runtime 放在同一处曾经降低迁移成本、简化 discovery、保留历史 checkpoint 路径，也避免多路径状态漂移；但其“单一任务根即单一同步范围”的假设已经不成立。

**新路径契约**：
- `note_area / task_home` 可以位于 Obsidian Vault；只保留 `research_plan.md`、`exploration.canvas`、`notes/`、必要的简短说明、visible config 和 Task pointer。Canvas/notes 永不直接复制原始实验 rows/NPZ/CSV/data/pickle，科学结果以可稳定解析的非同步路径与摘要链接引用。
- `research_root` 是**非同步**目录，保存所有 Cycle、workspace、checkpoint、audit、Evidence、运行注册表、task-local skills。新任务的路径必须在 `note_area` 外；不能回退到 `note_area/.theory-master/research`。`research_root` 与 Task Home 通过显式 config/pointer 绑定，不能由 SDK 静默覆盖。
- `benchmark_root` 同样是**非同步**目录，原始网络、大型 benchmark 及科学附件都归其所有；默认位于 task data area。SDK Context 显式给出此路径，Agent 不可将大数据复制到笔记区。
- 对应 `theory-master/task-path/v2`，允许保留 v1 历史任务：不自动迁移运行中任务、不重新分配 Task ID、不改变科学状态或 Evidence provenance；历史 `research_root` 与 benchmark 原址在停机前保持可访问。迁移必须验证无活动 runtime owner、完整迁移与配对 `.data/.pickle`、更新可解析的 config/pointer、验证 resume/历史 artifact 后方可清理同步路径。未通过检查则 fail closed 并保留原件。
- `gs_study` Task 的非同步存储允许留在该 Task 的 `.theory-master/research`；位于 `笔记区` 的 Task 默认将非同步资产放至同一仓库的 `gs_study/.theory-master-tasks/<task-id>-<goal-id>/`（或显式指定的非 Vault 路径）。所有路径保持绝对路径，防止 cwd/Runner 漂移。
- 当前科研阶段、科学图节点、Judge 裁决语义均不改变。这是存储 authority / 生命周期边界修复，无 Scientific Current Flow 节点影响。

**验证**：新 Vault Task 初始化后无大数据目录；老 v1 Task 不静默改根；新 v2 Task 可从 Task Home 和 runtime 指针双向发现，resume 能找到旧 Evidence；迁移工具拒绝活动 owner、拒绝拆分 .data/.pickle、保全文件校验和与可追溯路径，且对重试幂等。

## Canvas frontier continuation / terminality consistency（2026-10-09）

- Lead 的默认可选研究前沿是具有 frontier_status=active 的 **scientific branch tips（Hypothesis 或 Decision）**；Decision tip 可沿唯一有效 scientific lineage 父 H 继续研究同一 H。上一轮 open 且仍有可执行判别前提，不能只因生成了 Decision 就机械创建新 H。
- **V1 历史兼容**：Candidate 不含 evolution_operator 曾表示 same-H continuation。SQLite 新 Cycle 必须提出有科学增量的新 H，非根候选需要真实来源 H 和 typed evolution_operator；SDK 分配 H ID，恢复只复用本 Cycle 已冻结 ID。历史同 H 多轮记录不能作为今后复用 H 身份的资格先例。
- Judge 终局 rejected 后 SDK 强制投影当前 H scientific_status=rejected 及终局 Decision tip frontier_status=closed，绝不能沿旧拒绝 H 隐式续接；不连带关闭独立 sibling。open 的 active/parked/closed 仍由 Lead 决策。Decision 新渲染 verdict，读取时兼容历史 status。
- 审查应观察新 H 冻结命题、Evidence→Judge→Cycle Outcome 的轨迹与真实 active/parked/closed 开关，不应把每轮 OVER 或新 H 数量作为科学进展指标。

### Investigator 预算用量快照与恢复的同步约束（TMCR-20261009-007）

- Budget Reviewer 的准入/额度权威仍由 budget_status 与追加式 investigator_reviews.jsonl 决定；investigator_budget 投影不是第二条批准额度途径。
- Collector 在调用期间接收 Token 使用量，因此执行前的预算检查可能早于最后一次 Investigator / Judge rework 的实际 Token 结算。每次科学状态持久化时，如果最新 Investigator 累计用量高于预算投影，则按现行账本重算 observed_tokens、remaining_tokens 与耗尽状态，同步到 research_state.json 与 run_state.json。
- tokens_total 已包括 cached input；不能再加 cached_input_tokens。恢复及 Judge rework 保持累计用量单调，不得刷新批准额度、重复付费或改变 Judge 科学权威。旧 Cycle 是历史不可写证据，不批量修改。

## TMCR-20261009-010｜Judge 返工、执行未完成与终局 open 的区别

针对冻结的同一 H 和 decisive_test，Judge 独立判断已经产生的 compact Investigation Result 是否完成必要判别；「存在有效 Finding」不等于「Judge objection 已解」或「决定性检验已完成」。执行 Judge objection 可以由 Investigator 在现有研究线程内开展多项必需的理论、数值及本地资料操作，但不得扩大假说、科学 scope、估计对象、准入输入或修改冻结 test。

## 2026-10-09：跨 Cycle 科学权威与多来源 Hypothesis（本次修改）

- `research_plan.md` 和 `exploration.canvas` 及其中明确引用的科学证据为下一 Cycle 唯一科学权威。新的 Cycle 一律重新调用 Research Lead 的 `frontier_proposal`，连续 `run`、上轮 `OVER` 后再 `run` 和 Master 级 `resume` 均不得从旧 Frontier State 选取默认 H/Decision。
- 同一 Hypothesis 的续研必须明确提供 `lineage_anchor_node_id` 指向当前 Canvas 合格的活动 Hypothesis/Decision tip；仅全新任务的第一个 root H 可省略。SDK 验证现有 H 身份，不得使用历史 Hypothesis Lock / branch_parent_id 作为备选身份。
- 真正新的 Hypothesis 必须携带 `evolution_operator` 和 `lineage_anchor_node_ids`，选择 1–3 个确有贡献、互不重复的现有 Canvas 来源；第一个是 H 或 Decision，最多两个补充来源可为 H / Decision / Finding。旧单来源 Candidate 兼容读取。
- SDK 对首个来源建立 `hypothesis_evolution` 主干边，对其他来源建立 `research_source` 边；补充边不会使来源节点失去科学 branch tip 身份，也不传播 preferred 关闭判定。多来源是研究形成轨迹，不自动等于数学逻辑蕴含。
- `frontier_state.json` 不再由新 Task 创建或由正常新 Cycle 加载/更新。现有历史文件原地保留用于审计，不作为当前科学权威。当前 Cycle 执行恢复继续依赖自己的 checkpoint / research_state / invocation ledger。
- Goal Completion 的缓存短路需要对照当前 Research Plan、Canvas 内容指纹及 Canvas 中的正式 passed Decision；历史无权威指纹缓存不会独立决定任务完成。
- 不在本次范围内修改 Investigator→Judge 直接 Findings 登记、`no_scientific_evidence` 判定或未裁决困难 Cycle 的状态机；该项由 `TMA-20261009-0011` 独立追踪。

审计登记：`TMA-20261009-0012`、`TMA-20261009-0013`，均待独立审查。

## 2026-10-10｜Investigator 原生 Codex Goal（实验性、默认关闭）

**目标**：与研究侧 H-0046 提前交付问题对应，研究一项冻结 `Hypothesis` 的完整决定性检验时，使用 Codex 的**线程级持久 Goal**，让科研探索可以越过普通模型 Turn 的结束，而不是反复向 Investigator Prompt 追加禁令。

**四层约束互不替代**：

1. **Role Identity / Developer Instructions**（固定）：科研职责、科学诚信、许可工具与 Skill、计算 Python、读写范围及科研限制。普通 Codex `thread_start` 时装配；Native Goal 不再重复身份和权限。
2. **Task Context**（在 Goal 启动前的独立普通初始化 Turn）：资格审查交接、最小必需科学导航、只读 Research Plan / Canvas 与允许的 Cycle Workspace；只完成信息传递，不视作科研结果。Judge rework 保留同一科研线程并在新初始化 Turn 中接收原异议。
3. **Native Goal Objective**（线程持久）：原文冻结 `hypothesis`、`scope`、`decisive_test`，并设定成功条件为“完成足供独立 Judge 裁决的完整冻结检验”。对 `judge_rework`，加入不可删改的 `requested_resolution` 作为当前要完成的科学问题。局部 Finding、未执行的检验和无原理证明的工具失败不是完成；真正反证/执行后科学未定可以完成。
4. **Findings Contract**：仅当 Codex 原生 `status=complete`，清除 Goal 并在**同一线程**运行一次无新增科研任务的 JSON 格式化 Turn；复用现行 `role_contract` / SDK 标准校验，再送独立 Judge。Goal 自称 `complete` 不是 Judge 的科学结论。格式修复仍归现有 SDK 合同功能。

**不可成功回退及原生配额衔接（2026-10-10）**：任一非 `complete` Goal 均不能格式化 Findings 或进入 Judge。`blocked`、`paused`、`budgetLimited` 和未知状态保持 `NEEDS_ATTENTION` / 可恢复科学停机；唯有确认的 `usageLimited` 映射成标准 `status=paused / phase=provider_quota_exhausted`，保存原始 Goal status、Codex thread ID、失败账号、provider、科研锁定状态和安全 checkpoint，再由已开启的 Supervisor quota-auto-adjust 执行双账号选择、等待或相同 Cycle 恢复。原 `usageLimited` Goal 已经终止：恢复会启动新的原生 Goal 操作，可以在相同 Codex 科研线程续接；跨账号/模型切换采用 SDK 显式 resume model，SDK 不支持的线程恢复必须注明新线程从科研产物接续，不能假称原生 Goal 无缝继续。不得设置科学墙钟时间限制；不得以 `findings=[]`、`OVER`、支持/反驳结论替代。`tm resume 1 --route investigator=gpt-6-luna@CodexHTTPS` 仅临时降低 Investigator 到 Luna/medium，其余 Agent 和全局模型档位保持不变。

**SDK 接口**：当前 `openai_codex==0.155.1` 的 Goal 尚未在高层 `Thread` 中公开 `run_goal`。适配器使用底层 `thread._client.start_goal_operation()` 和官方 `_GoalNotificationStream`，将一个逻辑 Goal 的多次真实 Turn 及工具事件按来源保留；SDK 版本升级时必须复核此接口。研究身份、Stage Contract、Judge 权限和 Canvas 设计未被 Native Goal 改写。

**审计**：`TMA-20261010-0017`；真实试验中的工具执行不等同最终科学完成，必须独立审查真正的 Findings 与 Judge 结果后才能判断科研效果。未完成项维持 Pending。

## 2026-10-12｜Codex ChatGPT Account 不支持模型的真实错误保全与显式恢复（TMC-20261012-6a7d03b06f70）

**G01 原始 Provider 权限**：当 Codex app-server 的 `error` / `turn/completed.error` 内明确是 `status=400`、`error.type=invalid_request_error`，Backend 将有限、可操作的 `message` 保留为非重试的 `CodexProviderRequestRejected`，在结果解析与“completed without final response”兜底前优先处理。不能把错误认作成功空文本，也不能重试相同被拒模型；真实连接断开、EOF、5xx、无错误的真正丢失 final response 保持原有 bounded transport-recovery。判错只基于 Provider 提供的结构化错误码，不用模型语言理解科学结论。

**G02 当时的临时 Profile 兼容调整（已于 2026-10-12 被新决议撤销；见下方 G05–G09）**：已核查本机 Codex `models_cache.json` 现有可见型号：`gpt-6-sol`、`gpt-6-luna` 等，未列出 `gpt-6.1-sol`。将 `codex.toml` 中 Research Lead、Challenger、Investigator、Judge 的默认模型恢复为 `gpt-6-sol`，各自 `high/medium/low/medium` 不变；Literature、Reporter 仍用 `gpt-6-luna`。不修改当前科学 Hypothesis/score/研究身份，不声称本机缓存是所有账号永久开放模型的证明。历史 Cycle 旧冻结模型记录保留。

**G03 恢复路由的显式控制**：原 `Workflow._restore_resumed_cycle_routes` 必须保持旧 Cycle 模型路由冻结，避免一般 Profile 修改静默影响未结束科研。但 Provider 明确拒绝时，用户授权的 `tm resume 1 --route research_lead=gpt-6-sol@CodexHTTPS` 是**例外的、显式、单角色**操作恢复。SDK 仅在当前 resume 调用从 `--route` 构建 `resume_route_overrides`，在从原 `runtime_role_routes` 恢复后，对指定角色覆盖 model/provider，未显式提供的 reasoning effort 继续沿用已冻结原值。状态新增 `runtime_resume_route_overrides` 保存 previous/effective model/provider/effort 和 `source=explicit_resume_cli`。原 `runtime_role_routes`、Judge Decision、Evidence、Canvas 和 Research Plan 不变。无 `--route` 则完全维持原被冻结路由，新 Cycle 单独读取已修复的最新 Profile。

## 2026-10-12｜本 Cycle 下一安全调用模型路由与二档预设归属（TMC-20261012-4992789abbab；TMA-20261012-0076）

**G05 配置所有权**：此前 `fcee787` 因某个 ChatGPT 账号对 `gpt-6.1-sol` 返回 HTTP 400，将共享 `model_profiles/codex.toml` 中四个科学 Agent 改为 `gpt-6-sol`；这混淆了用户长期二档预设与单账号的实时 Provider 权限。恢复 `codex.toml` 修改前的 `gpt-6.1-sol`（Research Lead high、Challenger medium、Investigator low、Judge medium；Literature/Reporter 仍 Luna low）。账号不支持型号时保持已有 `CodexProviderRequestRejected` 明确失败，不擅自编辑 Profile 或自动降档；用户可显式为单任务设置其他模型或使用授权账号。此恢复**不表示**账号二现在支持 GPT-6.1，也不改变已开启 Cycle 的线程。

**G06 当前 Cycle 热切换安全边界**：通过 `set_role_route` 保存任务级 model/provider/effort 覆盖，操作员必须可在原 `next_cycle` 与推荐 `next_invocation` 两个生效模式之间选择，后者只允许在同一 long-running SDK 的**下一次尚未启动的 Agent Invocation** 前生效，不改变正在执行的 Native Goal / Turn / 外部模型调用，也不把一次 Goal 内多个活动擅自视为安全切换点。`web-role-routes/v1` 中每个角色的 `activation` 为 `next_invocation|next_cycle`，旧格式缺字段按 `next_invocation` 处理；保存其他角色必须保留已有角色的生效模式。选择 `next_cycle` 时，本 Cycle 不进行热切换，新 Cycle 的常规路由解析仍使用该覆盖。在 `_call` 中首先排除复用的已提交 Invocation，读取其真实 task-local override，随后在创建新的 Invocation 及调用模型之前定点更换路由。新的 SDK Runner 通过 `sdk_runtime_capabilities.hot_role_route_v1=true` 标明能力；已有 Runner 未加载新版代码时应在网页明确标记「下次 Run/Resume」而不是虚报热生效。等待不意味着自动暂停、杀死或重启当前科研调用。

**G06 Worker Web 显示投影修正（TMC-20261021-2a33b6858267）**：保存档位只代表 Worker 本地目标配置已持久化；尚未启动 Invocation 的角色由该配置决定下次调用，Web 下拉框应直接展示目标配置，不得再因为 `run_state.json` / `runtime_role_routes` 保留旧 Cycle/已完成 Agent 模型而错误显示黄色待生效。真正活动的 Invocation 依据 SDK `research_state.active_invocation_id` 且 Worker 有活跃执行进程判断，对该角色保留本次调用模型并提示结束后切换；无活动调用时不应仅凭滞后的 Collector `running` 行造成误判。显式 `activation=next_cycle` 和 SDK 明确记录的跨 Provider 线程延期仍显示准确的待生效原因。科学快照与审计记录不可修改，Web 只改变配置/活动状态的投影。

**G07 科学身份、Provider 与对话延续**：同 Provider 但模型/effort 发生变化且同一 Codex Thread 已在当前 Runner 缓存时，于上一次调用完毕后 `thread_resume(thread_id,model=...,config=...)` 明确重新绑定模型与推理设置；SDK 拒绝则 fail-closed，回滚本地 route，不静默用旧模型继续。已用线程跨 Provider 切换延期，绝不偷换新 Thread 或宣称无缝继续；角色没有已用 Thread 时允许首次选用新 Provider。显式 CLI `--route` 优先于 Web task override。不能把配额账号的选择视作允许更改已选模型或 effort 的授权。

**G08 可核验路由与恢复**：原 `runtime_role_routes` 继续记录 Cycle 开始时冻结的路由、不做覆写；`runtime_hot_role_routes` 保存当前已生效的模型，`runtime_hot_route_history` 和 SDK `operator.route.applied/deferred/failed` 登记来源、新旧 model/provider/effort 和安全边界。同 Cycle 恢复先还原冻结 Cycle routes，再应用已经实际生效的 hot 快照，最后尊重显式 resume CLI 路由例外。Collector 的 Agent 模型投影与实际调用配置应同步；尚在执行中的调用始终按开始时原模型记账。

## 2026-10-12｜Native Goal 4000 字符预算与冻结科学完整性（TMC-20261012-eb36ba12373e）

**G01 零截断与去冗余**：Native Goal 的固定通用指令压缩成“输入资格 → 事前冻结预测与预算 → 完成决定性检验 → 独立核验 → 可裁决证据”的必要语义；原科研 Objective 的完整科学内容不缩短、不换估计量、不删方法与阈值、不替代 Judge。固定 Role 权限、方法 Skill、交付路径和文件读写规约由已有 Developer/Setup Context 保持，不在原生目标中重复堆叠。v2 的冻结科学直接注入时使用紧凑 JSON 序列化，原字符串逐字保留。

**G02 预先预算与安全兜底**：平台硬限制为 **4000 字符**；SDK 在创建新的 Investigator invocation/Role Started 和任何付费 setup/Goal Call 前渲染并核算长度，目标的预留安全预算 **3800 字符**。不足预算时不对 hypothesis / scope / decisive_test 截断、摘要、重述或任意去掉细节，而是让 Native Goal 提及**同一线程之前 setup Context 原文**中的 `frozen_objective`，附加针对其全三字段 SHA-256 的机器锚点。若实际 setup Content 的冻结对象与哈希不一致、JSON 不存在或引用无效，Codex Backend 在发起模型 setup Turn 前立即拒绝；不是根据脚本猜测科学是否合理。`run_native_goal` 最后也独立执行 4000 上限硬检查。

## 2026-10-10｜原生 Goal 目标编排 v2（默认）

`v2` 是生产 Investigator Native Goal 的默认编排；独立 `native_goal_trial` 可保留 v1/v2 对照能力。通用 Goal 模板按实际学科问题要求输入资格、必要的事前预测与控制、完整检验、独立校核和可复核证据，不包含 H-0046 专属实验细节。该链条是通用科研完成标准，不覆盖冻结的 Hypothesis、scope、decisive_test 或 Judge Rework 的 `requested_resolution`。Codex 4000 字符限制在发起 Goal 前校验，超长时明确失败，不截断冻结科学内容。

通用 `v2` 链条是：按冻结范围确认输入及操作定义；在问题需要时先说明预测、对照、比较规则与不确定度；完成完整决定性检验和必要比较；按学科选取独立校核；整理可复核证据。具体研究方法来自冻结科学问题和任务上下文，不能由模板预设。Goal `complete` 后仍只触发现有 `findings[]` Contract 与独立 Judge。

上面是**Investigator 内部自行管理的研究工作顺序**，不是工作流 Scientific Turn、SDK Finding 判据或额外审计 Agent。Judge Rework 时 Goal 保留已有 `requested_resolution`，不建立更强命题。原生 Token/用量与用户显式中断仍是运营出口；中途不能因未完成某一步就提交局部 Findings。此架构效果仍待真实轨迹验证。

## 2026-10-10｜Cycle 检查点与安全重放边界

## 2026-10-10｜冻结实验 Runner 层 3600 秒超时与持久科学接续

用户明确要求原生 Goal **只以科学完成和 Token/模型额度为运行约束**。隔离实验 v1 虽已取消自身 240 秒等定时限制，最初是经 WebCodex `run_shell(timeout_secs=3600)` 启动，约 1 小时后被外层 Runner 强制终止；不是原生 Goal `complete`、`budgetLimited` 或合法科学未定。外层限制没有被源代码中移除的 Timer 覆盖，必须将长科研进程交给**用户 systemd 持久服务**，服务的 `RuntimeMaxSec=infinity`，且用户 `Linger=yes`；不从 WebCodex Runner 的阻塞型 shell 执行长期科研。普通 `tm run` 和其他 Role 没有变化。

为保障科学可追溯，`native_goal_trial --resume-artifacts <旧隔离实验根目录> --goal-layout v2 --output <新的唯一目录>` 提供新的**只读科研证据接续**：通过 Linux bubblewrap `/` 只读挂载，原历史 H-0046 冻结输入、旧 v1 已生成网络与 `frozen_three_mode_protocol.json` 不可修改；新 Goal 在固定 Developer Instructions 和原始冻结 H/scope/decisive_test 下显式收到上一次实验的源路径、协议 SHA256、以及不得回改已冻结预测/误差预算/预选壳层的约束。旧 Codex 原线程临时 HOME 丢失，故**这是新线程从科学数据接续，不是 Codex 同线程 resume**；若发现科学资格失败应当如实裁决而非借此改变原预冻结协议。所有新代码、数据、goal status、event trace只写新隔离试验目录，`result.json` 在 Setup 期即记录新的 thread id，运行中 `progress.json` 每 10 个事件写入可核验进展；只在 Native Goal complete 后正式形成 Findings。

原先被平台中断的证据与其未完成状态永久保留，不允许把其 1h 运行当成真实科学失败；最终独立 Judge 裁决仍有单独的权限与合同，单靠 Goal complete 不能声称通过。相关审查 `TMA-20261010-0020` 保持 Pending，必须实际验证 service 生命周期、进度文件和独立科学评价后才能关闭。

**Codex Goal 本机协议上限**：安装的 Codex app-server 会拒绝长度超过 **4000 字符**的 `objective`，返回 JSON-RPC `-32600 goal objective must be at most 4000 characters`。v2 原冻结 H 编排约 3660 字符。科学接续的旧目录、预冻结协议 SHA256、文件清单和不改条件的详细说明必须放**单独的 setup Context Turn**，Native Goal 只写简短指向（该 H 当前约 3830 字符），并在调用前严格检查总长度；绝不截断或删改冻结科学目标。首次持久服务因重复追加全部接续 Context 而被上述协议拒绝，且在科研 Turn 开始前结束；该失败作为运行异常保留，不算科学尝试。修复后应建立新的输出目录和新的独立服务，旧失败文件留作溯源。

## 2026-10-11｜Investigator 独立计算并行调度（TMA-20261011-0027）

**权威来源**：SDK 的 role whitelist 未改变；系统级 resources/skills/compute-dispatch/SKILL.md 与 numerical-investigation/SKILL.md 是 Investigator 按需读取的执行约束（不是 always-on developer identity）；禁止修改冻结 H/scope/decisive_test、Canvas/ResearchPlan、计算/统计样本身份或伪造盲测。Lead/Judge 等其他 Agent 和 Goal Contract 不增加并行字段。外部 HPC/Slurm 可按项目资源使用，不新增 TM 工作流层 Scientific Turn、Agent 或自动审批。

**执行策略**：真实独立计算达到 10 单元或串行预计超 10 分钟时先 smoke、评估单元时间/峰值内存/IO、CPU affinity/cgroup/磁盘预算。**当计算量、单元成本、资源变化后，必须重估**。真正独立且有并行收益的 CPU-bound 单元优先采用 worker 级进程并发，可用不同等价 smoke 分组实测 1/8/16 workers 的吞吐、数值一致性及 IO；共享稀疏矩阵加载开销、存储带宽、输出冲突或强依赖导致并行无收益时，给出可核验串行理由。并发遵循 worker×内部线程≤实际配额，默认避免 BLAS/Omp 线程过订阅，设内存/磁盘余量。不可将同网络多个 root 误算为独立科学网络组。

**辅助执行器**：Skill 随附 scripts/parallel_units.py（Python 标准库 CLI），由当前 Investigator 主动按需使用，不是 SDK 强制科研工具。接受位于当前 Cycle write workspace 的不可变 JSON manifest，schema=theory-master/independent-compute-units/v1；每个 unit 声明唯一 id、彼此独立的 cwd、无 shell 的 argv、带 SHA256 的冻结文件与代码 inputs、单位 cwd 下独立 outputs。--dry-run 只校验输入/资源与提出 worker 计划；正式调用依 CPU affinity/cgroup 物理核、内存、磁盘按上限 16 workers 自适应收缩，强制每 worker BLAS/OMP 线程限额并记录实际路由。manifest、输入/程序与结果有 hash 证据，失败保留子任务日志及退出码，--retry-failed 只在输入一致时重跑失败工作，已完成且 hash 一致的输出不重算；变更/未认证旧产物/竞争写入/低资源则 fail closed。summary.status=complete 仅表示计算过程完成，仍需 Investigator 执行独立正确性与 frozen criterion 核验后方可交付 Findings/Judge。此工具不构成容器级 sandbox，不替代 TM 已有文件权限限制。

**回归依据**：tests/test_compute_dispatch_parallel.py 覆盖分批运行、真实并发、输出隔离、缓存重跑、失败恢复、文件篡改、冲突/错误输入及 Skill 开发者白名单路径。真实多核性能与运行内存指标尚待科研任务独立验证；严禁据合成通过声称 48 CPU 全满或科学检验已完成。

## 2026-10-11｜Native Goal 实时科研 Token 预算与任务级方向纠偏

### P0：原生 Goal 必须在运行中执行额度权威（TMA-20261011-0028）

**新不变量**：对于 Investigator 的每次 native thread/tokenUsage/updated，SDK 先读取真实 input+output Token（包含 cached input，但只计一次）、计算本次 Goal 开始以来的非负增量，结合冻结的前期 high-water，立刻将 tokens_total_live / investigator_budget.observed_tokens 持久化到 run_state.json；pre-invocation budget_status 额外读取历史 run_state raw_usage（同一个 Codex Thread 的全生命周期累计）与 research_state.json 的 observed_tokens 最高水位，进程重启、线程保留或热切换都不得把已花 Token 计回零。正式 Goal 未结束前的 usage 不是 Findings，也不是裁决。

**额度触发路径**：累计值达到当前 ledger allowance → SDK 在当次原生 Goal notification 回调抛出 NativeGoalBudgetExceeded → run_native_goal 取消正在执行的逻辑 Goal，而非将其伪装成 complete → Workflow 记 interrupted invocation / Native Goal budgetLimited / 当前科学原始 artifact / NEEDS_ATTENTION 的 budget_review_required → 立刻调用 SDK 内部独立 Budget Reviewer。Reviewer 有权 approve/reject，但不可改科学 Hypothesis / criterion；批准时原子 ledger 按实际 overrun high-water 加一 tranche，在同一个 Codex 科学线程开启新原生 Goal 操作，不是虚称被取消的逻辑 Goal 仍然运行；拒绝/错误则保持可恢复 Attention，绝不免审发额度或进入 Judge。每个 notification 会产生至多一次阈值触发，不增加科研墙钟时限、不设置定时上限。可能出现一次 Provider 通知粒度的 Token 超额，属于真实额度观察量，不可捏造严格逐 Token 截断。

**Native Goal 尚无官方 Findings 的评审来源**：此前内部 Reviewer 只接受已登记 Evidence 或官方 Findings，故 Native Goal 完成前天然无法通过评审。现增加且仅增加由当前 workspace 白名单位置（mechanics_identity, discovery_response, orientation_candidate, independent_discovery, matched_preparation）产生的 Markdown 科研 assessment/audit/derivation，带相对路径与文件 SHA-256 的临时进展引用 workspace-sha256:<path>:<digest>；在发放额度的同一原子事务内再次验算所有引用仍然存在且 hash 相符，并防止已审过的同一内容反复批准。Reviewer 看的是报告内容摘要和科学价值，文件本身不是 Evidence/Findings；重复无效探索、没有可执行下一测试以及单纯增加网络数量应拒绝。原 reviewer 身份和资源权限不变，额度审查不是 Judge 结论。

### 任务级原生 Goal 策略纠偏（TMA-20261011-0029）

### 2026-10-11｜预算审查输入误判的追加式纠正（TMA-20261011-0028）

**Reviewer 语义修复**：向独立 Reviewer 明确指出：合格的 workspace-sha256 进展引用无需附 Evidence ID，能否批准取决于其科学内容、可验证价值与可执行下一测试；不是看到文件就批准。近期异常必须携带时间戳，只提交最近三小时登记的有效异常；从前发生后已恢复的问题不得作为当前故障。当前单次用户指导摘要也传入 Reviewer，但不代表已获得资源授权。若内容仍是无意义重复，Reviewer 有完整 reject 权力。

**受限的审计纠错机制**：历史上已有错误拒绝时，系统默认仍禁止任何后续审批。只有操作者明确要求科研继续、拒绝包含已查实的「官方 Evidence 是唯一合法来源 + 把已解决的旧网络故障误判为最近执行」双重前提错误、原 SHA 成果仍可认证、进程处于已停止的预算 Attention，才允许管理员显式调用 invalidate_protocol_flawed_review 创建一条附原拒绝行 SHA、时间、缘由及具体证据的 append-only invalidate 记录。这个行政动作本身**零额度**，不覆盖原拒绝；且只将上轮误标记为已审的源哈希引用重新开放，让独立 Reviewer 重新判断。随后要么 Reviewer 真实 approve + 账本单个 tranche，才能恢复 Investigator；要么 Reviewer 真实 reject/失败则必须停止，不能再次自行纠偏同一拒绝。重审输出若原文件存在则写新的带 ledger 序号文件，不覆盖历史审计。这是输入/记录错误修复而不是绕过科学质量的拒绝机制。

**授权边界**：本次变更只涉及 Investigator 原生 Goal 的 Token Budget Reviewer 读取资料、提示词、只读 SDK 线程权限、出处验证与预算注册。**不修改 Investigator 的 Goal Prompt 或 native Goal complete 语义**；也不要求 Investigator 代 Lead 改造被冻结的 decisive_test。Lead/Challenger 对检验设计的资格负责，Judge 对科学 verdict 负责。资源 Reviewer 只判断下一批 Token 的科学投入是否值得。

**当前资料层次（原始对话为主线）**：
1. SDK 将本 Cycle 已持久化 `runtime_events.jsonl` 的**真实 Investigator 助手消息及 TOOL_ACTIVITY** 作为按时间排序的原始对话索引。源是实际日志，未预先替 Reviewer 判定哪些内容值得看；`TOKEN_USAGE` 等机器记账事件不作为新的科学进展。对话日志原本只保存助手消息与工具活动概要，**不是包含 stdout 的完整回放**，必须显式告知 Reviewer 该局限。
2. 在额度边界使用官方 Codex SDK 的 `thread/read(include_turns=True)` **只读获取同一 Investigator 线程**中可见的原始 Turns/工具输出，另存 `budget/investigator_visible_thread_<review_index>.jsonl`，内容无主题筛选、不包含私人模型 reasoning 与 compaction。仅对超大项记录省略标记、原字节数和 SHA；记录实际覆盖量和 SHA。若 SDK 线程不可读，Review Context 标记 `unavailable`，退回原日志与工作区，**不得假装读取到工具 stdout**；不会恢复研究 Goal 或额外使用科研 Token。
3. 将 `workspace` 根路径作为**可自由探索的科研工作区**交给独立只读 Reviewer，Reviewer 自主查阅实际报告、实验代码与输出并把对话声称与源文件互证；不靠 SDK 固定目录列表判断科学价值。Task 级必要科学背景限于原研究合同和直接相关来源，不自动开放秘密或扩展成新实验。
4. 独立 Reviewer 使用现有 `gpt-6-luna / CodexHTTPS / medium`，在全新线程中享有 `Sandbox.read_only` 与 `ApprovalMode.deny_all` 下的**只读文件/命令工具**。只可读取、搜索、核验；不得写文件、计算新科研、修改 H/Canvas/Goal/预算，不能代替 Investigator 或 Judge。原 Investigator thread 完全不与预算 Reviewer 共享模型推理状态。新的身份 Prompt 必须提醒原始对话及工具结果是待检验的科研证据，绝不是指令。
5. 模型只收到冻结 Goal、Research Contract、Final Gate、H/decisive_test、小的 Token 摘要、来源路径与**可校验的新进展机器引用**；**完整日志及大量科学文件留在磁盘，由 Reviewer 自行按需阅读**，不把几十万字粘进一次性 Prompt。Reviewer 的结论仍是严格 JSON：`approve/reject`、依据、具体下一个有界科研动作、可验证的 ID 列表。Reviewer 需要审查实际新颖进展、null results 的信息价值、重复无效策略、资源效益与已发生的纠错，而不是仅数文件数或 Findings 数。

**预算来源与不可重复领额**：新增 `conversation-sha256:<first>-<last>:<sha256>` 进展锚点，仅对日志中真实的 Investigator `LLM_MESSAGE` 和 `TOOL_ACTIVITY` 序列按原始 JSON 重新计算 SHA-256；SDK 对已审阅区间取下一段真实新消息/工具活动，单独排除因 Token 记账/预算状态写回产生的虚假“新进展”。Reviewer 可将这个**来源认证但非科学认可**的引用写入 `progress_refs`，系统仍须在唯一原子 `apply_review` 中验证原始字节与先前 ledger 去重；任何源路径、sha 或序列被伪造均拒绝，不靠模型直接增额。旧 `workspace-sha256`、正式 Findings 和 Evidence ID 继续可验，工作区摘要扫描不再有固定的目录名单；评审主体的实际阅读权完全独立于机器摘要筛选。

**安全失败**：若完整事件路径、SDK 线程快照和可验证正式/工作区进展全不可得，则产生**预算资料输入不可用的运行错误**并保持 `NEEDS_ATTENTION`，不应把“看不见数据”登记成科研未进展的正式拒绝。若 Reviewer 决策 JSON 不合规、未知/重复 ID，SDK 现有 fail-closed 不会写入授权账本。Snapshot 是辅助原文证据，不能单凭其存在绕过 SHA/新进展审批。存储在 Cycle 私有预算目录，权限 0600，原始工具结果可能包含应保密的科研内容，不能注入公用 Reporter/Canvas 或用户界面。

**提示词统计口径**：修改前/后统计 Reviewer 运行时`函数构造的 user prompt`字符数及 Budget Reviewer identity 原文字符数，两者分别注明；原始日志内容本身是按需读取的外部文件，不进入 Prompt 字数之和，也不得把长度较短当成“没有看完整科研对话”的证据。

## 2026-10-12 · Investigator 预算续批基准与人工放弃 Cycle

**预算续批权威**：Budget Reviewer 负责是否批准，不负责决定额度计算公式。初始批次以任务配置或冻结账本的 tranche_tokens 为准，审批产生下一批时新批准额度严格等于既有批准额度加一个批次，即 new_allowance = prior_allowance + tranche_tokens。审查时已使用量 observed_tokens 只用于检查是否到达预算边界、实际剩余额、登记消费证据，**不参与新批准额度的基数计算**。例如原额度 50,000,000，本轮越过额度至 50,122,468 后获批追加 50,000,000，新规则结果应为 100,000,000（而不是 100,122,468）。

**Kill（结束并永久作废当前 Cycle）**：Task Console 的 Kill 是唯一可见的不可恢复“结束当前 Cycle”操作，内部复用 `abandon_cycle` 的科研安全协议（历史 `abandon_cycle` 请求作为兼容别名保留）。RUNNING 状态仅在运行 Runner 已声明 `abandon_cycle_v1` 能力时受理，沿受保护 Web operator → SDK `Workflow.request_control` → `recovery_control.json` 请求下一 SDK 安全检查点停止；不异步强杀当前模型 Turn。PAUSED 且无活跃 Runner／未决控制时，Kill **同步**落盘作废，无需等待一个不会到来的安全检查点；Quota Supervisor 必须重新读取状态，不能在额度恢复后自动恢复已作废 Cycle。两种路径均以 `status=cancelled`、`phase=abandoned`、`resumable=false`、`invocation_stop_reason=human_abandoned_cycle`、`abandoned_cycle.requires_new_cycle=true`、`run.cycle_abandoned` 事件归档。完成后此 Cycle **永远禁止 Resume**；旧日志、Workspace、Evidence 原始记录可读，但未完成研究不得升级为 Judge verdict、Scientific Model 或 Canvas 科学进展。SDK 不自动创建下一个 Cycle，只有人类主动 **Run** 才分配新的 Cycle ID。Web PAUSED State 提供 Resume 和危险 Kill 两项，Kill 有两轮确认及不可撤销勾选；这是科研编排的不可恢复终止，不承诺操作系统强杀进程及取消已提交的远端模型请求。Pause 仍为可恢复的日常暂停，历史 `interrupted` 状态只用于兼容与恢复。以上均为操作状态，不得伪装科学 supported/rejected。

验证：预算增量及新旧账本只读回归、非科学放弃事务 FakeBackend/检查点测试、受保护 Web Operator 接口 mock 与独立确认弹窗测试；未经用户单独授权不会为了验收启动额外真实科研 Agent / Cycle。

**运行版本兼容保护**：发布新增恢复动作不能假定旧版的长期运行进程会自动热重载 Python。只有真正开始/恢复执行的 Runner 才写入 research_state.sdk_runtime_capabilities.abandon_cycle_v1=true（短命 Web SDK 操作进程不能补写）。Web / CLI controller 校验这个权威标志，缺失时拒绝 abandon_cycle，前端置灰并说明需要在安全点 Pause 后 Resume 更新 Runner；避免旧 Runner 把新命令当非法 recovery_control 而导致 Cycle Failure。预算公式的新实现同样要等旧运行进程重新加载代码后才能用于下一次审批；旧历史 Ledger 继续按原公式验签。

## 2026-10-12 · Investigator Native Goal 单一科研指令权威与实际输入审计

## 2026-10-12｜Codex 容量不足的 30 分钟限时重连（TMC-20261012-1c9693b9bdfb / TMA-20261012-0072）

**实现边界**：生产 Codex/ChatGPT/CodexHTTPS 账号通道的 Investigator Native Goal 监听失败通知的原始错误内容，仅对精确 `selected model is at capacity` / `model is at capacity` / `model_at_capacity` 且 Goal 未完成而 `blocked` 的组合允许自动再接入。每次 terminal blocked Goal 先清理其逻辑 Goal 标记，再在**同一 Codex 科学 Thread**上、以**原冻结科学 objective**发起下一次 Native Goal 操作（不是断言原来已经终结的 Goal 仍执行，也不是开启新 Cycle、重新 Lead/Judge 或重新提交正式 Findings）。模型、Effort、工作区及已有真实工具成果不变；SDK 不执行任何科学方法或重新挑选样本。新的真实研究输出发生后结束本次容量故障区间，下一次独立容量异常可重新计时。

**30 分钟上限**：从第一次明确的 capacity 错误记录 Unix 时间起，至多 1800 秒；重连间隔 5、10、20、40、60、60…秒，不超过剩余时间。按 Cycle workspace 的 .tm-handoff 持久化以 thread/model/provider 绑定的私有 reconnect receipt（首错时间、计数、最近错误），异常 Runner 重启继续使用原时间戳，不能因重启无限延长。容量恢复即清除 receipt。超过上限 SDK 不再尝试，保留 frozen thread/workspace 并产生可恢复 `needs_attention`，使用 `provider_model_capacity_timeout` 和原服务商错误提示，沿用现有任务终止邮件通知；启用 Attention reviewer 时该异常也是硬性 hold，不触发新的 LLM 科研或改资源。用户明确手动恢复时可开启新的重连窗口。

## 2026-10-12 · Resume 不得重复登记 Judge Rework（TMC-20261012-19d9fa9004fd）

## 2026-10-13｜Supervisor 接管：一个开关，两套 Profile（TMC-20261013-190fd39ff4f7）

- **开关**：`supervisor_intervention.supervisor_enabled`；新键 `THEORY_MASTER_SUPERVISOR` 优先，否则旧 `THEORY_MASTER_ATTENTION_TAKEOVER`/`THEORY_MASTER_ERROR_REPAIR`/prefs `error_repair` 任一显式关闭即关闭；默认开启。`attention_takeover` 模块与 operator 动作 `attention_takeover` 保留为兼容别名。
- **Attention Profile**（NEEDS_ATTENTION）：沿用 `internal_attention_review` 权限（只读、`retry_qualification` 白名单、一次性 guidance），新增会话行。
- **Runtime Profile**（FAILED）：`runtime_supervisor_review` + 身份 `runtime_supervisor`（只读辅助角色）。只做诊断并选择 `resume|hold` + `repair_suggestion` 文本；不写代码、不改科学产物。无有效安全检查点或已提交阶段、已放弃、协议损坏、预算硬守卫或超过本 Cycle 有界次数 → 确定性 hold；无效输出 fail-closed。resume 经 Runtime Supervisor 主循环，仍受 `MAX_CHILD_RECOVERIES` 约束；2026-10-13 起上限见下文可靠性修订。
- **会话**：`research/<cycle>/supervisor/session.jsonl` 追加式（opened/system/prompt/model_output/decision），`status.json` 记 active/hold/returned。锁、心跳、自动审计、额度自调、科学权威不变。

## 2026-10-13｜Supervisor 可靠性：实际故障证据、分类、恢复核验与新事件重审（TMC-20261013-ae468bac0a86 / TMA-20261013-0080）

**分层与权限**：
1. `CodexBackend.invoke_goal` 对实际终止 `LLM_FAILED/LLM_TURN_FAILED` 以事件来源判断为 Provider Runtime 失败，抛出 `NativeGoalRuntimeFailure`，由外层持久化 FAILED 并进入 Runtime Supervisor；不再将此失效信号误当 Investigator 的正式科学 `blocked`。如有后续真实 `LLM_MESSAGE/TOOL_ACTIVITY`，旧传输错误清除；`usageLimited`、`budgetLimited` 和原严格 `at capacity` 30min 机制保留独立路径。任一科学 Finding/decisive_test/Goal 成功都不得由 SDK 运行分类代造。
2. Attention/Runtime 共用只读 `supervisor_incident.incident_context`，有界读取 Cycle 的 `events.jsonl` 最新 Role invocation 和 `runtime_events.jsonl` 同 Thread/同 invocation 事件；传错误原文脱敏摘要、重连统计、当前预算、检查点、原运行阶段和状态，避免将旧运行的异常误作新因。大量研究报告、历史完整日志、秘密与模型私人 reasoning 不直接塞入 Prompt；每次 Supervisor Session 存储附来源的精简证据。硬性资源守卫依然可确定性 hold，无需为了“智能”耗费无效模型调用。
3. Runtime 模型可建议 `resume|hold`，但对纯运行故障的同 Cycle 恢复必须通过 SDK 确定性 guard：未 abandoned、状态 resumable、已存在安全检查点或已提交角色边界、无协议违反/预算拒绝，且最多 3 次模型批准的 Runtime 恢复与最多 3 次 Child 自动恢复。SDK 不因模型一句话而更改源代码、科学范围、研究资源额度或冻结判据；需要这些变更时 hold 并交给人决定。Attention 中的资格重提保留原单次权限，其他科研/资源阻断不自动冒用 Runtime 恢复能力。
4. Runtime Supervisor 的 `returned` **仅是允许并交还执行权**，不等于恢复成功。Supervisor 在启动持久 Runner 后确认新 `LLM_MESSAGE/TOOL_ACTIVITY`，以 `verification` 追加原会话的实际验收；Runner 退出而没有新模型活动则追加未验证。不得凭后台 PID、`RUNNING` 字段或日志中的旧活动虚报修复成功，不以固定短时间超时触发科研失败。
5. 新的 Resume 消耗旧停机状态但不修改科学锁，只清除 `internal_attention_review` 当前投影；已有 Supervisor 会话、incident 历史仍追加保留。下一次 `NEEDS_ATTENTION` 无论相同 Attention code 也要创建新的审查会话，不能由旧 hold 跳过。Observer 铃铛保留历史会话并标记当前待处理；真实验收进入会话消息。
6. 普通 `NEEDS_ATTENTION` 邮件只说当前停机事实与下一动作，不再笼统声称曾自动修复/重试；只有专门的确实耗尽重试类型使用“重试耗尽”文案。只有真正的资源/运行/科研文件来源支撑通知中的具体原因。

**审查与兼容**：离线单测检查提供商失败与科研 blocked 分流、相同线程跨 invocation 事件隔离、Mock 恢复 guard/数量、恢复后新 Attention、不调用实际模型的邮件内容与审查会话；旧 Cycle 科研证据和 Judge 状态保持只读。变更只有下一次启动的 Runner/Supervisor 才能加载，不能擅自将旧进程热替换或在当前科研阻断上强制重试。未来实际 Provider 连接异常、恢复成功率及邮件/铃铛一致性需要 TMA-20261013-0080 在获授权的后续真实 Cycle 中独立审计；**当前单测通过不代表真实在线恢复已验收**。

## 2026-10-14 · Runtime Supervisor 隔离式工程修复（TMC-20261014-db909653e435 / TMA-20261014-0082）

Runtime Profile 有两条互斥路径：偶发 Provider 故障仍由只读 Reviewer 决定 resume/hold；精确归因到 Canvas 引擎源码 traceback 的普通 Python 工程故障交给隔离式 Repair Codex 尝试受控补丁。protocol_health=violation 依旧禁止未经修复的直接 Resume，但不再阻断能获得离线修复证明的工程故障。超出白名单、无安全 checkpoint、权限/预算异常、状态损坏，或触及科学结论、数据、Hypothesis、冻结判据、核心 Workflow/Contract 的异常仍必须 Hold。

每 Cycle 最多一次工程修复，额度在调用模型前持久化。Repair Codex 只访问沙箱中的引擎副本，不拥有真实 Task 目录或科研文件写权；只允许变更由当前 traceback 确定的一个 Canvas Python 模块，变更任何其它文件、创建文件或符号链接均拒绝。通过 Python 编译和三套纯脚本 Canvas 回归后，必须先通过官方 CLI change_register 登记单次修复 TMC，再以源码原始字节 CAS 判断无并发修改才写回引擎，写回成功后用离线证据 verify 该 TMC；登记或核验失败不得自动 Resume。没有完整验证，不允许 Resume；当前不允许扩张自动修改范围，不自动执行 Git commit 或大型架构迁移。

离线补丁通过仅表示 validated_patch_pending_runtime_verification。恢复必须继续满足非协议类硬守卫、独占 Runner、Child 恢复次数与原安全 checkpoint；Supervisor 现有运行核验要求新的 LLM_MESSAGE 或 TOOL_ACTIVITY，不能仅凭 PID、命令退出码或 Runner RUNNING 文案宣称真实成功。会话按序记录修复、验证与恢复事件；历史 hold 在当前 LIVE 的网页上需作为历史事件展示，不得冒充当前故障。

## 2026-10-18｜异常性质路由与停止后多 Worker 工程修复（TMC-20261018-c59682ec702d）

`severity`（error/attention/minor/important）仍只表达异常等级；`observability.incident_routing.classify_incident` 额外为新 `important_events.jsonl` 的首次登记写入 `routing`（version/nature/owner/strategy/dispatch_status=classified_only），不改变历史事件，不把登记误认作接管。格式/Contract 可恢复事件由 SDK 自身处理；RUN_PAUSED/usageLimit 归额度控制器；硬预算与非法 Provider 模型/认证问题分别归预算控制器与人工配置；NEEDS_ATTENTION 走现有受限 Attention Profile；确定的工程 FAILED 走 Runtime Supervisor；科学里程碑仅登记。

多 Worker 调度器新增 `worker_supervisor_dispatch`：**仅在 Supervisor 配置为开、原 Worker 已退出、科研状态是 FAILED、当前 Worker 与冻结 Cycle 的所有权一致、没有其他活跃 Worker 进程时**，才为该失败身份派生一个 Worker 专属隔离 Supervisor helper。它使用原 Worker 固定账号和日志；共享 `scheduler.lock` 防止修补代码期间启动其他 Worker，并依靠 `worker_action('resume')` 复核 claim fencing、账号占用、检查点和 Worker 身份。分流到额度等确定性控制器的事件不会为此发起额外 LLM 调用。Supervisor 的直接改代码能力仍严格复用 `supervisor_runtime_repair` 的窄白名单、归因 traceback、沙箱单文件 Diff、离线测试、TMC 及 CAS 保护；这**不**授权 AI 任意改数据库、科研图、证据或科学身份。未归因、缺少安全 checkpoint、证据不明确时必须 hold，不得宣称成功。

Dispatcher 单独持久化 `<research_root>/.theory-master/workers/worker-N/supervisor_dispatch.json`，按错误指纹仅自动提交一次，记录 Helper PID、review session、hold/恢复启动及运行验证。恢复时必须观测到本次启动之后的真实 `LLM_MESSAGE/TOOL_ACTIVITY` 才追加 `runtime_activity_verified`；这只证明恢复了模型/工具活动，**不证明根因已彻底根除**。修复之后再次失败按新的失败身份重新分类，但仍受每 Cycle 的 Supervisor Resume/Repair 预算限制。旧 Worker/历史事件不会被自动回写为已修复。运行中的 daemon 不会自动热加载本次代码，已有 `THEORY_MASTER_SUPERVISOR=0` 保持不变。

### 2026-10-19 · preserve_research 扩展到预算拒绝和 Judge 返工阻断

- 沿用已有 Investigator preserve_research，只读汇总证据限定 observations、methods_exhausted、remaining_gap、unexecuted_test；SDK 严格校验路径及 SHA。
- 允许三类被核验的阻断：原 investigator_native_goal_blocked；SDK ledger 已拒绝的 investigator_budget_review_rejected（investigator:budget_denied、judge:rework_blocked）；有原非终局 Judge 请求的 judge_rework_budget_exhausted。Provider/格式异常不得伪装为科研阻断。
- 不改 Judge 权威：保存原非终局 Judge 意见及原 Findings，不冒充返工已完成；Canvas 以 External / StageSummary 的 unadjudicated 节点与 H 连线记录进展，同时登记 ScienceGraph v2 external_records、external_edges；不能用 append_outcome(open) 虚构 Judge 科学评价。
- Web Worker preserve_release 必须由操作员明确点击：SDK 核查 Worker/claim fencing、冻结 Cycle、已停止的 owner；后台独立 preserve-job 按 existing Workflow preserve 模式调用只读 Investigator Agent（可能消耗模型额度）。保全报告、旧 Canvas、v2 图谱、closure verified 之后，才释放 Worker/claim，允许 Scheduler 新 Cycle。失败保留原状态及文件，不静默改预算或直接释放。
- 按钮名“保全成果并结束本轮”；需要人工确认不可恢复与一次只读归档模型请求。其他原始 release 明确标记为不归档。
- 离线测试 TMC-20261019-8fc4149b0efc、TMC-20261019-be785bf1a00c、TMC-20261019-91ea71256b94；真实模型和跨 Cycle 行为待 TMA-20261019-0091 观察。

### 2026-10-19 · Preserve Worker 身份从科研权威状态核验

### 2026-10-19 · Preservation Outcome 不新增图谱节点

- Preserve 生成 H 的阶段性执行结果，而非 External/StageSummary 节点。SDK 仍调用已有 Investigator preserve_research Stage 只读整理事实；在完成证据 SHA 校验后记录到 cycle_preservations，Canvas 中只更新被冻结的 H 节点的 preserved_cycle_outcomes，报告在 notes/scientific_reports 下。
- 终局授权由 Judge 独占：preservation non-Judge unadjudicated 不能复用 append_outcome(open) 登记“未裁决”，也不能涂成 Supported/Rejected。保留原 Judge 返工意见及预算拒绝 code；保留旧有 status/closure 及 legacy Sidecar，迁移仅为显示结构升级。
- Worker 保全完成前仍持 claim，不改变资金审批记录；ScienceGraph v2 只新增依附既有 H 的存储表而不改变整个图谱原始科学连线。迁移提供备份、幂等性、记录同 Cycle 冲突拒绝和 H 所属校验。

### 2026-10-22 · 预算迁移错误分类与 Supervisor 复验

`ValueError` 只表示 Python 校验异常，不能据此认定 Agent 违反协议。预算台账读取与校验抛出 `BudgetLedgerError`，运行状态及 `run.failed` 事件分别保存 `budget_validation`、`runtime_validation`、`protocol` 或 `runtime` 分类；`last_runtime_failure` 保留原异常，不随后来的人工暂停丢失。输出规范化错误以及 Contract/Artifact 校验器的明确违规仍保持 `protocol_health=violation`。普通校验失败也须经过客观修复验证才能托管恢复；已存在的窄范围 Canvas 隔离修复可进入修复预检，资源、暂停与所有权守卫继续生效。

`python -m theory_master.budget_reconciliation <cycle>` 默认只读。`--apply` 在调度锁和预算锁内验证完整事件历史、失败前有效协议检查点、当前冻结 Hypothesis/检查点一致性、当前无执行所有者、原审批桥接哈希和完整预算台账。仅匹配已知的旧 3+3 台账异常时校正错误分类；出现其他失败、独立协议违规证据、预算不足、待执行操作或证据变化时拒绝。写入状态前保存含原文件哈希的凭证；只更新运行协议状态、预算投影和凭证引用，保留人工暂停、科研内容、预算审批、检查点、Claim 及重试计数。审计记录为 `repair_validated`，不等同于运行恢复；未完成的写入留凭证供人工检查，不自动覆盖重试。

Runtime Supervisor 在旧违规守卫前执行上述确定性复验，无需为哈希/台账验证调用模型。工程回合返回后，Dispatcher 也执行支持的独立验证器，通过后再走原 Worker 的 SDK 恢复检查，且先释放调度锁再启动 Worker。工程 Prompt 因此要求提交实际修复及验证证据，不在持有调度锁的回合中直接调用 Worker 控制。`diagnosed`、`repair_applied`、`repair_validated`、`runtime_activity_verified` 是不同证据阶段；其他未接入客观验证器的工程故障继续明确报告待验证，不能凭模型文字或文件变化直接恢复。

### 2026-10-22 · Supervisor 离线验收必须执行测试

### 2026-10-22 · 统一工程交接、修复凭证与恢复验证

单 Runner 与多 Worker 调用同一个 `review_and_repair_runtime_failure`：在各自取得 `scheduler.lock`、确认无活跃同伴之后执行诊断及工程交接；退出锁后由原启动器走官方恢复入口。只读容量探测不进入写工程链。相同故障与源码下已经执行的交接不再重复调用工程模型；每 Cycle 最多三次工程交接，恢复次数不重置。人工暂停、已作废、真实协议违规、预算硬限制与 Claim fencing 保留。

SDK 修复凭证采用 `theory-master/supervisor-repair/v1`，记录验证器版本、原异常 occurrence、冻结 Cycle/H/检查点、源码及测试文件哈希、Git HEAD、实际验收结果与来源；存于 Cycle `supervisor/repairs/`，state 只持有路径和摘要。使用前必须重新核对，源码、测试、检查点或故障身份改变即失效。现有验证器覆盖：已归因的单模块 Canvas 补丁（原三模块白名单）、有原始 traceback 的 Workflow 普通方法缺失（新解释器导入隔离副本，检查方法可用，不实例化或运行科研方法）、既有预算迁移桥接（保留其专用证据和受控状态转换）。方法已在当前代码中存在可记 `already_fixed`，无需制造补丁或再调用模型。方法探针只证明原缺失接口已可用；不证明该方法科研逻辑正确。其他故障，包括尚无独立配置验证器的 Provider 配置更改，报告具体缺少的验收能力，不能靠模型文字或通用 exit 0 恢复。

同一回合期间工作树的差异只能标为 `changes_observed / observed_unattributed`，不归功于该 Agent。SDK 应用配置或隔离补丁才有明确应用证据；独立验收后标 `repair_validated`，更新 incident 记录。`incident_supervisor` 保留写工程权限，但实际执行策略拒绝 Git commit；Prompt 与 AGENTS 的运行时例外明确要求先登记 TMC、返回精确 diff/哈希，并将 Git 收尾标为 `pending_review`，不要求 Agent 绕过权限。开发 Agent 仍完成本地 scoped commit。

### Qualification 的历史继承与外部开发复审

Lead 与 Challenger 按需核验最相近历史模型的定义、实际检验、裁决边界与关键反例，而非只读标题、近期摘要或声明父节点。用既有 rationale/reason 解释继承内容、新差异及新增判别；关键词零命中不等于新颖。资格阶段不要求完成 Investigator 的新研究。

外部开发审查采用 `../sciver-qualification-lab/review/qualification_review_rubric.json`，由独立 Agent 分别评估选题与完整确定流程。它不增加正式 Workflow Stage、模型调用或科学 verdict；每次新的在线科学测试仍需单独授权。

### 2026-10-22 · Supervisor 持续恢复责任与独立候选验收（当前运行策略）

本节替代前文将“三次恢复 / 一次窄修复 / 同指纹一次派发”作为整个异常处理寿命的限制。次数上限约束同一动作，不结束尚未解决的工程异常。科学性停止、人工暂停、预算、Claim 和检查点守卫仍然有效。

1. 两种启动器共用 `supervisor_recovery`。每个异常有 `supervisor/recovery.json`，保存逻辑负责人、行动标识、计划版本、历史失败、下一检查时间、等待条件和最后验证进展。失败后按 5 分钟起、最多 6 小时的退避重新规划；源码、配置、检查点、守卫或新失败证据变化可提前唤醒。运行中行动用 PID 加进程启动身份防止重复执行，进程退出后重新接管。单 Runner 在等待期间释放调度锁；Worker dispatcher 保留恢复调度责任，不再将历史三次失败永久转为人工结束。
2. `runtime_failure`、`supervisor_unrecoverable`、`provider_model_capacity_timeout`、`investigator_native_goal_unexpected_blocked`、`runtime_retry_exhausted`、`runtime_no_recoverable_checkpoint` 属于运行恢复入口；缺少安全锚点时仍不能执行恢复。正式科学 blocked、预算拒绝及普通科学 Attention 不转为工程放行。Attention 的确定性 hold 标明后续运行执行器。
3. 窄修复失败返回结构化交接和原因。后续自动工程技能在独立副本中提交最多六个已有 Python 文件的候选补丁、一个原异常复现测试、现有回归选择及修复理由。禁止候选改变 Supervisor/验收器、预算、执行权限、Worker 控制及现有测试。另一个只读模型回合审阅完整 diff、复现与测试选择；SDK 随后实际执行相同复现：基线必须失败且匹配原异常，候选必须通过。候选必须通过三套固定 Canvas 回归及选择的原有回归，所有套件都必须收集测试且无跳过。基线回归只允许已归因的原异常；无关失败拒绝验收。离线子进程清除继承凭据并禁用网络/子进程操作；它不是针对恶意 Python 的操作系统级隔离承诺。
4. 每次候选在修改前登记 TMC；拒绝的 diff、复现、计划和验收结果保存在 `supervisor/candidates/`，交给下一行动。部署前再次检查原异常/检查点绑定、当前引擎哈希和冻结测试；多文件 CAS 遇冲突回滚已应用文件。通过后生成 SDK 凭证，绑定原异常、源码和验收输入。自动运行不执行 Git commit，收尾保持 `pending_review`。
5. 配置修复采用只读规划与 SDK 的类型化路由提案。只针对有 traceback 归因的本地路由解析异常作离线通过证明；当前配置和旧 Cycle 的冻结路由必须分别验证。配置哈希写入修复凭证，变动即失效。远端模型不支持、认证或权限问题记录 `provider_probe_required`；本地合法配置不能证明服务已恢复，也不能悄悄替换未完成调用的冻结路由。诊断后端不依赖失败的科研路由或科研计划格式，避免配置异常同时禁用 Supervisor。
6. 普通恢复仍最多三次；超过之后，新的有效修复身份只授予一次额外恢复，历史次数不归零。同一修复的凭证重新生成或失败时间变化不能刷新额度。恢复授权和验收引用绑定到原会话，旧会话的活动消息不能关闭新行动。恢复后真实模型/工具活动仅标为 `runtime_activity_verified`，不宣称根因永久消失。
7. Web/TUI 展示开关、逻辑负责人、执行/等待、下一动作、检查时间、等待原因和已验证进展。会话 active 不代表有修复进程运行。额度自动调整仍是独立开关；本变更不启用它、不启动任务、不热替换现有进程。
