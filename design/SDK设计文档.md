# TheoryMaster SDK 设计文档

> 状态：现行系统设计。范围：SDK、Workflow 与 runtime。
> 本文维护稳定的架构、权限和事务边界。具体科学判断由 Agent Stage Card 与任务科学合同承载；实现细节由对应模块维护；修改缘由和验收记录由 Git、TMC / TMA 保留。

## 1. 系统目标与设计边界

TheoryMaster 是推进长周期开放科学问题的可恢复科研 Workflow Engine。系统围绕 Hypothesis（H）组织探索，以 Cycle 完成一次冻结科学命题的有界裁决，再把认识压缩进长期 Scientific Model。

核心约束：

- 科学方向、证据生产、资格审查和终局裁决各有明确角色；SDK 负责组织、校验、登记和恢复。
- 每个新科学 Cycle 锁定一个新的 H 及其 `scope / decisive_test`。同 Cycle 的重试、返工与恢复复用原锁定对象。
- 一个 Cycle 有终局科学裁决时，保存一条 Cycle Outcome；运行失败、暂停或资源停止本身不产生科学裁决。
- 科学身份、证据、调用和提交可追溯；下游投影失败可独立恢复。
- 科研上下文按需读取，长期认识不依赖 Agent 对话存续。

SDK 不依据自然语言、文件名或关键词代做科学判断。系统约束执行权限、身份、合同和事务；具体科学价值、可判别性、充分性与真假由授权角色判断。

### 设计权威

| 范围 | 权威规范 |
| --- | --- |
| Workflow、角色边界、状态与恢复事务 | 本文 |
| 科学图谱、H / Cycle Outcome、SQLite 存储 | [ResearchGraph设计文档](./ResearchGraph设计文档.md) |
| 图谱展示、Frontier、External 与 Reporter 投影 | [Canvas设计文档](./Canvas设计文档.md) |
| 任务长期输入、Scientific Model 格式与维护权 | [ResearchPlan设计文档](./ResearchPlan设计文档.md) |
| 多 Worker、Claim、并行提交与调度 | [多Worker并行科研架构](./多Worker并行科研架构.md)、[V1.1冻结合同](./多Worker并行V1.1冻结合同.md) |
| Supervisor 与科研阻断授权 | [Supervisor设计文档](./Supervisor设计文档.md)、[Supervisor科研阻断决策授权](./Supervisor科研阻断决策授权.md) |
| 研究保全与终止 | [Investigator研究受阻保全与终止授权](./Investigator研究受阻保全与终止授权.md) |
| 账号额度与模型路由 | [Codex账号Cycle额度与模型路由](./Codex账号Cycle额度与模型路由.md)、[Worker独立Cycle额度与模型档位](./Worker独立Cycle额度与模型档位.md) |

专题格式与字段在其权威规范维护，SDK 只引用接口。历史 Canvas V1 和旧协议通过 Git、[历史规范](./Canvas设计文档-历史V1.md)及[迁移规范](./Canvas双节点与Outcome数据库迁移规范.md)追溯。

## 2. 角色与权限

| 角色 | 负责 | 交付与权限边界 |
| --- | --- | --- |
| Research Lead | 生成和选择候选；综合科学认识；维护分支活跃状态 | 决定研究方向和 Scientific Model 更新，不代替 Challenger 或 Judge |
| Challenger | 锁定前独立资格审查；修复后的独立复核 | 判断候选是否具备研究资格，不选择 winner，不裁决实验结果 |
| Investigator | 冻结命题内的理论、数值、本地材料和工具研究 | 交付自包含 Findings，不管理 Workflow 或改写冻结目标 |
| Literature | 获取并核查外部学术来源 | 如实区分实际阅读层级，不把不可读来源当作已验证证据 |
| Judge | 独立裁决本轮 H；必要时给出有界 objection；任务完成裁决 | 科学裁决不由预算或运行状态推导 |
| Reporter | 完整报告的标题与正文 | 不改裁决、不选下一方向、不创建图谱科学对象 |
| Research Chat | 与用户讨论证据和方向，执行明确批准的研究调整 | 独立工作区会话；使用 SDK 计划/图谱/运行接口，不代替资格审查或科学裁决 |
| Supervisor | 故障诊断、受控工程修复与恢复验证 | 不改科研合同、Evidence 或科学裁决，不越过资源和所有权守卫 |
| SDK | Stage 调度、合同闭合、身份、账本、持久化和投影 | 不选科学假设，不解释证据，不代签 verdict |

Semantic Annotator 是辅助 B 层模型，只在 SDK 给定的封闭选项中做语义映射。它没有证据创作、科学裁决或状态迁移权。Hypothesis Lock、Evidence Registry、Readiness 和 Commit 是 SDK 机制，不是 Agent。

科学选题以尚未满足的 Goal / Research Contract / Final Gate 为主线，或解决其逻辑必需的 prerequisite。Lead 判断科学方向，Challenger 独立审查资格。领域案例、具体选题禁令、实验方法和科学质量判据放在任务合同、Stage Card 或按需 Skill 中。

Lead 与 Challenger 从一手历史核对最近的竞争解释及已有不利对照，说明本轮能区分什么；同时改变多个假定的拟合改善只能支持相应模型族比较，不能自动归因为其中一个机制。任务要求的已知范围核验须与新科学推断分清；已有裁决足以补齐交付时，新增科学须说明为何应优先于补齐当前缺口，不以固定增量或样本阈值代替判断。

候选必须提出具体的科学主张及可区分后果，说明相对最近历史命题改变的关系、机制、结构假定、定理或模型族；重述 Goal、列出理想交付、扩展覆盖或限制参数个数均不能单独定义新机制。只有任务合同明确要求的覆盖范围才形成资格义务。Lead 不必预先完成证明或推导；Challenger 应区分尚未求解的明确命题与尚未定义的研究任务。存在性命题须界定对象族，有限搜索失败不构成一般反证。绑定定义、输入资格及判别规则须进入冻结科学正文，不能依赖提案理由补充。

每个 Candidate 只锁定一个可在当前 Cycle 判别的科学命题。Lead 在现有 `hypothesis / scope / decisive_test` 中写清允许输入及取得状态、已有相关对照或逻辑控制、最小决定性结果、支持／反驳／科学未定出口、必要前提和与任务相称的计划资源上界；正反结果均须对 Goal 有信息价值。若完整命题必须先发明模型、取得新数据并完成大范围评分，应提出其中一个有科学判别力的必要子命题，不能把准备工作本身当作科学结果。Challenger 审查完整前提链和最小结果在所报输入、对照或逻辑控制与资源内是否可达；单纯缺少执行资料属于资格或运行障碍，不是已执行检验后的科学未定。换命题才能收束时 `reject`，仅同一命题的定义、输入状态、对照、出口或上界可补足时 `revise`。尚未完成的有界证明、有效负结果和有信息价值的局部必要检验仍有资格；不设跨领域统一样本量或 token 阈值。此处资源上界用于资格审查，不改变 Investigator 现有运行时预算。上界须依据输入规模和方法成本；对廉价的现有只读输入，不设缺乏科学或人工依据的单次读取／扫描限制。方案应区分有效检验与无有效结果的技术性读取／解析失败，允许同一检验内有界修正重试，且不得越过用户明确设定的限制或改写冻结科学判据。

Lead 在提交候选前做有界的科学选题研究：核对本地一手历史、相关方法及一个有意义的对照，推得候选在命题层面的必要判别后果；这不要求先完成证明或决定性实验。外部文献若有助于辨认可行机制、检验关键假设、发现反例、比较最近前作或选出更窄的判别问题，Lead 可在 `frontier_proposal` 发起一次聚焦的 Literature detour，说明具体来源问题、本地已知内容及证据将怎样帮助选题；不必先证明文献缺口已阻断工作或一定会改变猜想。`landscape` 用于尚未辨清的方法版图，`targeted_source` 用于已知来源或具体主张，`novelty_check` 用于明确的前作重叠问题；本地证据足够时无需检索，也不每轮例行检索。Literature 只交付可追溯的外部来源、阅读层级和剩余问题；Lead 返回后自己决定科学命题，不能把文献摘要当作猜想或让 Literature 代选。修订复核须重新核查修订新增的模型、输入、验证单位及资源路线；只列取得计划或把缺输入标作科学未定，不构成阻碍关闭。

Lead 触发的文献检索材料存于当前 Cycle 的 `workspace/literature/sdk_retrieval/<source_gap_id>/`；结构化 Literature 输出另存入运行状态与事件。SDK 将来源记录、阅读层级、定位信息、领域图或前作定位及 `remaining_scientific_question` 精简后回传 Lead 的 `lead_literature_result`，不把论文全文灌入 Prompt。锁定前的这一路仅作选题依据，不自动登记为当前 Cycle 的裁决 Evidence，也不自动为每篇论文创建 SQLite `External` 节点。

资格阶段的 Literature detour 已消耗后，Lead 再次请求文献时，SDK 只允许一次使用已有结果的有界纠正；首次返回、候选被拒后的重新提案及恢复执行均使用同一处理路径。纠正后仍有文献需求，则保存最新请求并进入可恢复的 `needs_attention`（`lead_literature_detour_exhausted`），保持 Hypothesis 未锁定，等待补充来源或科学指导。文献请求不能作为空候选进入身份校验；普通 Resume 不授予额外检索额度，也不能将缺失来源视为已经取得的科学结果。新的指导可以启动新的提案尝试；调用账本使用单调递增的资格重启代数，避免有限历史截断后重放旧提案，历史任务没有该计数时兼容使用既有历史长度。

外部文件分流只检查请求目标、预期证据和具体检索线索，背景中的数据路径与数据集名称不能单独把学术检索变成文件获取。若旧校验曾将尚未执行的 Lead 请求误分流，修复后的 Resume 根据原中断、同一 source gap、请求历史和既有一次授权续接该请求，不清零计数、不增开 detour；已完成结果或实际传输失败不因旧误分类记录重复获取。正常的传输恢复仍须由工程恢复流程核实依赖条件。

范围与检验要求由当前 Goal / Research Contract / Final Gate 和候选 decisive_test 界定，不从代际、壳层等术语自动推断完整序列禁令。只有任务明确要求时才约束全序列覆盖或独立局部目标；联合支持、匹配和独立样本检查仅在实际经验比较需要时适用，纯理论或存在性证明不额外承担经验样本要求。提案与审查核对适用的更简单解释、已有方法及给定设计下逻辑／统计上可达的结果；合格候选选择不重新审查资格，有限修订保留父候选已绑定的条件。

## 3. Cycle 主流程

```text
任务长期输入 + Scientific Model + SQLite 科学图谱
  → Lead / frontier_proposal
      ↔ 必要的 Literature detour
  → Challenger / candidate_review
      ├─ qualified → 进入选择
      ├─ revise → Lead / selection_repair
      │            → Challenger / repair_verification
      │            → confirmed 才进入选择
      └─ 无合格候选 → 保留审查记录并停止当前资格流程
  → 单候选由 SDK 选择；多候选由 Lead / candidate_selection 选择
  → SDK / Hypothesis Lock
  → Lead / hypothesis_display（展示导览，失败可回退）
  → Investigator / investigate
  → SDK / Evidence 登记与 Investigation Result
  → Judge / terminal_assessment
      ├─ 有界 objection → 必要 Literature → Investigator / judge_rework → Judge
      └─ terminal verdict
  → Lead / cycle_synthesis
  → SDK / Scientific Model keep|replace commit
  → Lead / frontier_maintenance
  → Reporter / report
  → SDK / Cycle Outcome 与图谱投影提交
  → Cycle 收口
  → 若本轮 supported：fresh Judge / goal_completion
      ├─ passed → Task 完成，关闭 active / parked 分支
      └─ not_passed → 按运行预算和调度进入下一 Cycle
```

### 共同讨论与一次性人工方向

Research Chat 使用独立身份、运行手册和五个按需技能解释进展、讨论问题、核对证据、规划下一步和执行干预。讨论形成具体行动时，末尾编号列出所需 Plan 更正、下一轮猜想、Scientific Model 修订、路径整理或运行操作，说明各项原因、当前工作影响和生效时点，再询问用户是否执行。接受可识别方案或明确指令授权该范围，不重复确认；单纯讨论、选择图谱对象和接受计划修改不自动授权科研启动。

图谱节点、Cycle 结论和文档/报告选段可以作为来源加入聊天草稿，用户编辑后发送。服务端按规范任务边界读取当前来源，核对对象、章节、原文和版本；客户端标签不取得路径或科学权威。消息保存发送时的核验快照，历史快照不冒充当前事实。后续人类猜想可以携带相同已核验来源，保留讨论到新输入的追溯关系；来源引用不是 H→H 科学谱系或 Judge Evidence。

新人工猜想的精确命题、判别检验、原因和来源记录在同一规范图谱的 External ResearchFocus。投递生命周期使用图谱事件，不维护第二套科学图谱：`pending → claimed → consumed`，未领取项可明确 `revoked / superseded`。`consumed` 只表示已交给某轮提案，不能推断资格、执行或结论。登记检查当前 Required Context revision，保留整份 Plan 原文；多个待投递项按登记顺序，回执给出队列位置。替换仅作用于明确指定的 pending 项。

默认下一轮登记允许当前冻结 Cycle 继续运行。SDK 仅在全新的 `frontier_proposal` 领取一个待投递项，领取与 Cycle 绑定、快照先持久化，再进行 Lead 调用。同 Cycle 的重试、返工和崩溃恢复复用其快照，不在流程中途接收新方向；后续 Cycle 不重复领取。没有新生命周期的历史规范 External 显示为 `reconciliation_required`，不凭 Plan marker 自动执行；用户确认后重新登记为 pending。Plan-only 部分登记也不得静默执行。列表、队列位置与领取规则一致，已冻结命题和已有提案检查点保持原内容。

Lead 收到人工精确命题、检验和来源后，仍遵守当前 Goal/Contract/Final Gate 和 Challenger 资格审查；不合格时报告冲突，不由 SDK 强制锁定 H。用户明确要求立即转向时，先使用运行控制安全停止并核实实际 Runner/Worker 状态，再登记即时请求；登记 API 不自动启动新工作。恢复旧冻结 Cycle 仍是继续原命题。Goal 封存后拒绝写入和新的领取。

Chat 按用户接受的编号逐项核对 Plan/Model 版本、External/H/Cycle/Worker 及实际运行回执，区分已更改、待投递、已资格锁定、运行中、失败和部分完成。规范存储成功但显示投影失败时保留已成功结果并报告待修复投影，不重复登记新猜想。Model 的人工修订与并发保护见 [ResearchPlan设计文档](./ResearchPlan设计文档.md)。

正式立项前的两类问题形成与 Literature 流程见 [项目筹备与前置文献调研](./项目筹备与前置文献调研.md)。筹备与已有项目 Chat 可调用共享 `literature_service`，使用独立 Literature 会话、现有身份、技能、Stage 和 Contract，执行“发现 → 重要来源选读 → 正文物化 → 阅读比较 → 按缺口补查”。重点来源由 Literature 的领域输出和阅读顺序确定；SDK 处理网络、持久化和资源上限。默认最多 2 轮、6 个核心来源获取尝试，未解决的来源缺口在达到上限后暂停；恢复保留已完成获取与阅读。该作业不创建科学 Cycle、不修改 Research Plan 或 Scientific Model、不登记裁决 Evidence。模型作业回执与检索回执分别记录。

PDF 转换通过共享 `document_conversion` 组件执行，新安装默认关闭。在 `/settings` 显式选择本地 Marker 或在线 MinerU；Marker 仅使用配置路径或服务 PATH 上的 `marker_single`，不假定个人环境。MinerU 使用私有配置（0600）或 `MINERU_API_KEY`，执行转换时上传 PDF；Token 只发往配置的 API origin，预签名上传和结果下载不附加 Token。配置通过 revision 比较保存，默认位于 `$XDG_CONFIG_HOME/sciver/document-conversion.json`（否则 `~/.config/sciver/document-conversion.json`），`SCIVER_DOCUMENT_CONVERSION_CONFIG` 可覆盖。个人主机启用状态不改变发布默认值。

转换保留原 PDF 路径及 SHA、Markdown、图片、版面元数据和页数范围；默认最多前 20 页，0 为所有可处理页。资料可读、Literature 阅读、引用定位和科学主张核验分开记录；部分页数不可称为全文已核验。无法转换仍可获取题录、摘要与可访问的 HTML 正文。MinerU 远端作业持久化，恢复优先沿用原任务，含糊的提交结果不自动重新提交。工程行为以确定性和模拟测试检查，不据此宣称真实模型质量或在线 API 已验证。

### 资格审查与锁定

Lead 无法提出可辩护的范围内候选时，可以返回 `candidates=[]` 与非空 `blocking_reason`，说明缺少的前提或输入；该互斥出口立即进入既有 `no_qualified_candidate` 本地资格停止，不强迫格式重试，不冻结 H、产生科学 verdict 或证明新数据不存在。后续 Task 推进仍受原预算与操作控制约束。已记录的旧合同故障只能在 SDK 验证原始空候选输出、失败身份和已修源码后获得一次正常 Lead 交付机会；原输出不追认为合格候选。

候选携带科学命题、范围、选题理由、Goal 推进和 `decisive_test`；候选 ID、H ID 和工作流字段由 SDK 管理。新派生 H 必须有合法科学来源和有类型的 H→H 谱系，首个 root H 除外。科学身份不依赖文本相似度。

Lead 在生成新候选时提交 `transition.prior_gap` 与 `transition.current_change` 两段短说明，分别解释来源研究已知什么、留下什么关键问题，以及本候选针对该问题具体改变什么；复用 `why_now` 和 `goal_advance` 说明现在研究的理由与 Goal 推进。根 H 从任务原题的未知出发；前序已支持、被反驳、已裁决但未决或尚未裁决，须如实区分。来源取决于科学谱系，不按 Cycle 编号相邻推断。即使仅有一个合格候选，也不能以“唯一合格”替代科学动机。说明只解释选题，不增补或替代冻结科学正文、资格结论和 Judge 裁决。

Challenger 审查必须绑定被审候选的完整科学内容。`revise` 后只允许一次有界修复，并须由 Challenger 独立确认。`cannot_repair` 或未确认的修复不能被 SDK 升格为 qualified；恢复不能沿用已改变对象的旧审查。新的 Qualification attempt 保留前次拒绝反馈，反馈不自动成为新候选的当前 verdict。

Lock 原子登记 H identity，冻结 `id / hypothesis / scope / decisive_test` 及来源。当前 Cycle criteria 只生成一个 `C-1` 机器引用锚点，内容来自同一个 `decisive_test`；不另造科学目标或证据任务队列。Lock 后不换命题、估计对象或允许输入；改变科学目标应进入新候选流程。

SDK 同时冻结选中候选的承接说明、`why_now / goal_advance`、main/other 来源及锁定时的 Outcome 引用，保存到 SQLite 与 H 档案。图谱节点、Agent 读取和本轮主报告复用该记录；展示阶段与 Reporter 不重新编写选题因果。锁定后来源新增的结论不回写为当时的动机。旧 checkpoint 没有这些字段时继续读取已有记录，缺失项标明未记录，不调用模型补写。

H 档案发布前，将同份来源快照持久化到 `hypothesis_lock.research_transition` 检查点。若文件发布后 SQLite 事务回滚，恢复沿用检查点快照；已提交的 SQLite 记录也须与该快照一致。只追加的档案不因恢复期间来源新增结论而改写。

### Investigation、Evidence 与裁决

统一 Investigator 在科研 episode 内自主选择方法，完成必要研究后交付自包含 Findings。科学主张及其前提、适用范围和不确定性写入科学正文；SDK 管理机器身份和产物溯源。交付使用 invocation 专属接口，由 SDK 校验、登记和交接，避免为登记重复科研或改变独立 Finding 数量。

数值结论依赖区间或检验时，按所用方法独立校验关键不确定性运算，而不只校验拟合值。Investigator 在现有方法记录中区分冻结规则、拟合前的方法选择和看过结果后的调整；Judge 按实际时序解释结果，不把未冻结的选择追认为事前判据，也不增加跨任务统一统计公式或样本门槛。

科学 Agent 的输出、辅助语义映射和 SDK 确定性闭合分属 A / B / C 三层。B 层不代替科学角色判断证据充分性；C 层不根据格式正确推导科学结论。

Evidence 先由 Registry 登记，再生成裁决视图。SDK 负责 producer invocation、路径和内容 hash、来源访问层级及受保护数据身份的技术校验。调用产物 manifest 只能证明执行产物身份，不能单凭它证明事前冻结或 held-out / Final Gate 资格。`adjudicable` 表示技术上可交付裁决，不表示结论为真。不可读来源不能获得可裁决资格；历史或漂移产物不能伪装成本轮独立验证。

普通预分 train/validation 的方法隔离与受保护盲测身份分别处理。数据来自 benchmark、被称为 holdout，或来源追溯字段为 `origin_unverified`，均不自动增加 SDK 盲测认证门槛。Investigator 如实记录分组核验、方法固定、执行顺序和限制；强认证缺失限制相应强身份主张。任务或冻结检验明确要求的事前预测、隔离和 Final Gate 仍须满足，不能靠路径、登记时哈希或 Agent 自述升级已暴露/事后数据的身份。

Judge 消费 compact `investigation_result` 和明确限定范围的历史裁决视图，独立决定 Evidence 是否回答冻结检验。历史视图从当前 Registry 权威无副作用地生成，不信任旧 Manifest 的资格快照，不重新登记旧证据。

Judge 的 objection 必须是冻结命题内的最小可执行增量；SDK 验证后按既有有限返工和资源边界调度。没有新正式提交的研究、预算否决或运行失败，均不能冒充新 Findings 或 terminal `open`。没有可裁决 Evidence 时保存非科学停止原因与检查点。

Judge 在现有 rationale/requested_resolution 中说明所缺的必要冻结前提、现有结果为何不足和剩余可执行的科学比较。SDK 自动绑定的 `C-1` 仅是路由锚点；结构授权不声称证明异议内容的科学必要性。SDK 不按 holdout 关键词替代科学裁决，也不直接改写旧 Judge 决定。

### 评价材料身份与资格核验

经验候选使用 `evaluation_materials` 声明稳定数据单元/分区身份、用途、独立性要求、既往反馈及来源依据；理论候选可给空数组。科学限制同时写进冻结的 decisive_test。SDK 在资格审查绑定及 H Lock 中保留声明，在 SQLite 追加冻结，并向 Lead、Challenger、Goal 注入跨 H 历史及科学终局标记。已知终局使用或既往反馈不能再次获 protected_final 资格；重新锁定、改名和无标签处理不恢复独立性。普通任务允许的预设校准仍合法。该记录是声明与已知历史，不能证明访问隔离，也不能把旧任务未记录的使用认作未暴露；需核对原始报告、数据边界和实际来源。

Challenger 的 scientific_checks 分别记录来源忠实性、定义/公式一致性、检验区分力和证据身份及其具体依据；声明评价材料的新候选必须携带这些检查，qualified 不得保留 blocker。判据须适配科学问题，不能统一要求 p 值或样本数。来源的有/无标签方法、权重和 verdict 以原始材料为准。资格修复不得用不同 estimand 替换冻结目标。

### 综合、前沿和任务完成

普通显式 `repair-delivery` 重建可复用绑定当前 canonical assessment 的严格行政等价核对：后续已结束且没有 H/Evidence 的资格空轮及其他字段完全一致的 frontier 投影时间戳，不单独构成科学权威变化。该分支核对原快照、原源文件及 Model；真实 Plan、冻结命题、Outcome、Evidence 或其他 frontier 字段变化仍拒绝。等价绑定只传入本次显式修复及其 Goal 冻结/CAS，不为普通材料读取添加回退，不扩大 Goal 可评资格或自动触发评委。相同交付修订复用原评估。

终局 Judge 之后，Lead 的 fresh `cycle_synthesis` 直接取得上一版 Model 完整正文和版本，只综合认识并给出 Scientific Model `keep|replace`；`replace` 提交完整新正文。不同人群、响应量或模型的新结果不自动取代旧认识，仍有效且与 Goal 有关的比较和限制须合并保留。SDK 校验、预览并原子提交版本。同一 episode 的 `frontier_maintenance` 只维护现有分支的 `active / parked / closed`，不创建下一轮 H。active/parked 必须说明 H 自身可执行的 remaining_question；parked 另需 dependency；closed 重开必须给 source-grounded reopen_basis。整体 Goal 未完不构成 H 重开理由。完全相同的更新不写事件，状态不变但机会描述改变记 frontier.annotated，不刷新状态 changed_at。旧数据只读兼容，不回填科学含义。

Lead 可用互斥的 `no_new_science(kind, requirement, basis)` 表明仅剩交付修复或不可用必要材料；SDK 保持 H 未锁定并进入 needs_attention，不伪造 Candidate 或科学 open。交付修复由现有 terminal Cycle 的 repair-delivery 路径承担，不自动启动额外科研 Cycle。

Reporter 通过 `title/summary` 交付报告标题与完整 Markdown 正文，`summary` 承载全文。SDK 将同一科学叙事保存为报告笔记和对应 Cycle Outcome 的标题、正文，不创建独立 Decision 图节点。SQLite 的 H / External、H→H 谱系与 Cycle Outcome 由 SDK 持久化；Canvas 是确定性展示投影。

Reporter 的当前 Cycle 素材视图与 Goal Judge 的跨 Cycle 任务索引复用同一材料解析器；执行文件的 `same_invocation` 关联只说明同次执行来源，不声明逐 Finding 支持或科学独立性。Goal 调用前冻结包含材料状态和已登记报告版本 SHA 的内容修订；评估以独立 assessment ID 和 delivery revision 追加保存，旧回执和 not_passed 理由保留。`repair-delivery TASK [--cycle N]` 只在未封存 Goal 的已结束且有 supported / rejected / open 科学终局的原 Cycle 内重建索引；`--reassess` 才显式调用 Goal Judge。修复不得改变计划、Model、冻结命题、Outcome 或 Evidence 权威，不新增 H/Cycle；相同修订复用原评估，新修订可在同 Cycle 重评。已 sealed 的 Goal 拒绝重开。

已获操作员明确授权的公开接口交付缺口，可使用 `repair-delivery TASK --cycle N --author-request PATH [--reassess]` 补交。请求文件须在任务 SDK 私有 `.theory-master/` 中，使用 `theory-master/operator-delivery-request/v1`，绑定 `cycle_id`、当前 `not_passed` 的 `assessment_id`、已登记公开要求的 `required_material_ref` 及有限 `instructions`。这不是从自由理由自动推断工程修复，也不适用于补造科学证据。入口持有任务启动和运行独占锁并拒绝活跃 Runner/Supervisor；原 Investigator 以原线程普通 Turn、原累计预算和剩余返工次数补充文件，不新开 Native Goal 或 H。补交调用的文件单独保留 SDK 执行来源，不新增或改写科学 Evidence；原文件、冻结命题、裁决及 Model 必须保持。后续闭合且没有锁定命题/Evidence 的资格空轮可与原评估绑定；只有其他前沿字段完全相同的投影时间戳可按原快照对齐，真实科学变化仍拒绝。新交付修订可显式交正常 Goal Judge 追加评估，原失败记录保留；未完成补交不能先触发复评。

Reporter 的 `report` v5 正文依次组织为问题与必要条件、主要结果、解释与限制。SDK 在唯一主报告开头先呈现冻结的研究承接说明，再复用 `graph_summary`，加入折叠的正式 Judge 记录与复现索引，不再生成第二份人类阅读报告。主要结果按科学问题组织，相关已有图片嵌入对应论证位置并附解释性图注，已确认的数值可整理为精简对照表。补充 Markdown/章节可使用 `![[path.md#Section|Label]]`；SDK 保存来源哈希与所选文本，正文保持必要论证。纯理论结果无需装饰性图片或空表格。必要限定保留在影响解读的位置，完整限制集中说明。

SDK 从可用于本轮报告的 Evidence 生成完整素材目录，保存在 Cycle 的 `report_material_catalog.json`，不把逐文件路径与哈希校验清单注入 Reporter。初始 `report_materials` 只提供去重后的文件数量、空文件/日志/可用状态统计、有限的目录概览及只读 CLI 入口；范围与限制继续由 `evidence_scope_digest` 承载。Reporter 使用 `list` 按目录、查询和类型分页发现素材，再用 `read` 分页读取具体内容；大目录概览也可分页。默认目录列表隐藏空文件与日志，可用 `--include-auxiliary` 查看，缺失、歧义和变化状态保留。这些清洗规则仅影响导航，不改变 Evidence 准入、科学相关性或完整复现索引。

目录入口固定目录内容哈希，读取具体文件时再次检查其登记身份和内容哈希，拒绝已变化、未登记或有歧义的内容；图片返回可用图片入口，二进制材料交由原生查看器。Reporter 根据已建立的科学问题和实际内容判断是否需要某个素材，不因名称、文件非空或同次执行来源断言它支持某个 Finding。只读查看用于理解已有图片或转录已确立数值，不授予重新分析数据、增加证据或改变裁决的权限。完整素材身份和校验记录继续由 SDK 持有；SDK 不读取原始数组来推导新结果。

SDK 将已登记图片绑定到真实文件，报告笔记使用相对自身位置的图片路径，图谱 Outcome 正文使用不依赖笔记位置的图片路径；缺失、歧义和未登记引用明确标记。原始大表、数据文件、脚本和日志留在原处，复现索引只列路径与可用状态，机器审计记录折叠显示。历史报告不自动重写。

Cycle 完成与 Task Goal 完成独立。本轮 `supported / rejected / open` 科学终局均触发 fresh `goal_completion`；`milestone_advanced`、未执行完测试和操作性保全不触发。候选耗尽后，Supervisor 可以把新增的已登记 Cycle Outcome 或新增、变更的可访问交付材料交给 Goal Judge。复评核对最近的 `not_passed`、原冻结交付及当前停点，沿原受支持 Cycle 追加一条 Goal 评审，不分配新 Cycle。其结果为 `passed|not_passed` 与理由；`not_passed` 附仅针对实际未满足要求的 `gaps(kind, requirement, basis)`，区分 scientific / delivery / unavailable_material，不选择下一 H。Goal Gate 评审以独立记录关联触发它的 Cycle Outcome；Outcome 的科学 verdict 不变。Lead 的精简图谱保留完整 H 索引，并显式展示历次 `not_passed` 的原文理由，即使来源 H 的 frontier 已关闭。Lead 对照 Final Gate 和已交付成果判断下一候选的必要性；Challenger 独立核对其 Goal 优先级。失败理由是历史任务裁决，不自动指定下一 H，也不因下一次理由未重述而自动解决。下一 Cycle 重新选题并审查，不能把收口时的临时 synthesis 当作新的长期科学权威。

任务可在配置中显式声明 `submission_contract: "benchmark/submission_contract.json"`，由显式 `benchmark_root` 解析公开 JSON Schema 合同。SDK 不按题名、目录或历史任务推断合同，也不把答案与私有评估器加入材料。合同原始字节、SHA 与已登记交付共同进入冻结修订；Goal Judge 调用前及封存事务内均检查唯一合规的正式 JSON 产物。机器格式校验不代替科学裁决，SDK 不从数组或其他字段生成科学提交值。

格式缺口交回原 Investigator，通过原线程的普通 Turn 补充正式产物；不重开或替换原 Native Goal。补交付沿用冻结 H、原科学 verdict 与累计 token 预算，次数受当前 Cycle 剩余的 `max_judge_reworks_per_cycle` 额度约束。已冻结科学文件和公开合同的源哈希必须保持不变，新产物通过正常 Finding 与调用产物登记。预算、次数、线程或完整性守卫失败时保留明确缺口并停止，不能封存为 passed；恢复已提交科学的 Cycle 时直接重入交付门，不重做 Lead、科学 Judge 或报告。纯索引重评不授权新作者调用，已封存任务保留历史原件。

## 4. Prompt、Contract 与上下文

每次调用按以下层次装配：

| 层 | 唯一职责 |
| --- | --- |
| Role Core / Identity | 角色所有 Stage 共用的身份、权限、科学诚信与数据边界；新线程加载一次 |
| Stage Card | 当前阶段目标、适用科学判断、操作与交接条件；内嵌 procedure 与其共同构成一个阶段定义 |
| Output Contract | 当前输出结构、字段含义与条件关系 |
| 按需 Skill / Evidence | 方法、来源与研究材料 |

输出字段与限制由 Contract 维护，失败回退由 SDK 实现；Stage 不再复制展示数值或回退协议。Contract Repair 只保留当前阶段已建立、且属于当前输出权限的内容，不创造锁定或裁决、不继承过时决定。同一规则在适用层维护，Stage 可说明共用权限在当前动作中的具体限制。Stage 由角色与当前 phase / mode / task 决定，不按调用次数推断；真实阶段清单由 `stage_registry.py::STAGE_CARDS` 和 `contract_stages.py::PHASE_CONTRACTS` 维护。

SDK 提供最小必要上下文和稳定导航入口，Agent 按科学需要自行查阅。可从路径读取的完整材料不反复展开；selection / repair 等续轮只给必要 delta。读取与写入遵守执行 sandbox、隐藏数据隔离和审批边界。Stage 读取 SQLite 的 `agent_graph`、H 档案和 Outcome，不能解析 Canvas 作为科研事实源。

线程以 Cycle 内 epistemic episode 管理：资格提案与选择复用；综合 fresh；Investigator 连续研究优先 resume；外部 Literature task 独立；Judge 本轮裁决和 reassessment 复用，Goal Completion fresh。跨 Cycle 从 Research Plan、Scientific Model 与图谱重建认识。线程丢失可从持久化检查点重建，并记录恢复来源。

Contract 以 `contract_id/version/hash` 绑定输入、输出、校验与闭合。Agent JSON 经校验后才成为 durable state。当前 runtime 只注册当前 Stage Contracts，运行时不提供历史合同查询；旧记录保留来源，经显式迁移才能进入当前执行链。

Skill 分系统级和任务级 registry，均按配置白名单及稳定路径加载；任务 Skill 不自动升级为系统能力。系统运行配置和模型 profile 由配置与路由模块维护，不在 Prompt 或架构文档复制具体模型清单。

## 5. 数据权威与持久化

| 对象 | 权威与写入者 |
| --- | --- |
| `research_plan.md` | 人工拥有 Goal / Research Contract / Final Gate / Required Context；Lead 经 SDK 更新 Scientific Model |
| `science_graph.sqlite3` | H、External、谱系、分支状态、Cycle Outcome 及其关联的 Goal Gate 评审事实库；SDK 执行受权写入 |
| Evidence Registry / Invocation Ledger | SDK 管理证据、调用身份、产物完整性及追加式记录 |
| Cycle report / workspace / artifacts | 本轮论证、计算和复现材料；通过来源引用参与追溯 |
| `theory-master.config.json` | 运行预算、调度、路径、路由及任务 Skill 配置；operator / SDK 管理 |
| `research_state.json` / `frontier_state.json` | 当前工作流和恢复指针；SDK 管理，不成为长期科学认识 |
| Canvas、Notes、Manifest、TUI / Web | 从相应权威生成的投影；不反向决定科研状态 |

长期认识由 Scientific Model 和 SQLite 科学图谱共同承载；运行文件不另存一套“相信什么、还缺什么、下一步方向”。Evidence 与生命周期变更可追加追溯，不原地覆盖历史科学记录。

Task 有稳定整数 ID，Cycle 在 Task 内独立递增。新任务的 Research Plan、Canvas、Notes 和 config 位于同一笔记区，机器状态默认置于 `.theory-master/research/`。源码树只保存引擎资产，任务运行数据归任务目录；已有路径的迁移必须显式保全。

科学阶段、科学 verdict、runtime 状态、Goal 状态与 protocol health 相互独立。Cycle `over` 只表示本轮收口；Task 完成由任务级 Goal 轴决定。Model 已提交而投影失败时，可同时保留科学提交与运行故障。

## 6. 事务与恢复

执行语义为 **at-least-once invocation + idempotent commit + artifact reconciliation**。Invocation Ledger 记录调用身份、合同、输入 hash、attempt、外部进程 / job、状态与 commit state；SDK-owned artifact manifest 关联该调用创建或修改的文件及内容 hash。

恢复依次检查：

1. 合同、schema、checkpoint 和当前 build 的兼容性；不兼容时保留原件并要求显式迁移。
2. 运行所有权、锁、process identity、Claim、冻结 H 和安全检查点。
3. 已完成调用及产物是否一致：一致则复用，仍运行则 reattach，未知则 reconcile；不完整或校验失败则隔离并按受控路径重试。
4. 从第一个未提交事务继续；不得重复已提交 Evidence、裁决或 Scientific Model，也不得重复发起昂贵计算。

事务顺序为：终局 Judge → Scientific Synthesis → Scientific Model commit → Frontier / report / Outcome 和展示投影。各层可幂等恢复，下游失败不重做上游科学判断。

Reporter `report` v5 分别返回研究正文 `summary` 与唯一概览 `graph_summary`。导览以本轮结论、关键依据、必要适用范围及本节点未决问题组织阅读上下文，不产生科学裁决、前沿状态或下一轮计划。SDK 原样保存正文、导览、冻结的研究承接记录和固定版本补充材料 `report_context` 到 Cycle Outcome 的 `source_json`，装配 `notes/outcomes/<Cycle>.md`；概览不是正文前缀截取。Web 与 Agent 的 `report-read` 使用同一装配和章节索引。历史 Outcome、缓存旧报告和冻结命题保留原内容，不因合同升级而新增模型调用或回填科学解释。

transport timeout、inactivity watchdog 和进程异常属于调用恢复。中途 progress、delta 或 interrupt 引发的完成事件不能冒充合法 final output。恢复同一 thread 时给最小 continuation；只有合法最终输出才能进入合同闭合与 Evidence 登记。

运行恢复、工程修复和科学审计独立登记。恢复继续累计真实活动和调用计量，离线等待不算科研执行时间。观测序号和 UI 状态不得反向决定身份、资源预算或科学结论。

原生 Goal 在 SDK 预算审批后复接时，应把已落账的 Reviewer 批准作为资源状态更新投送到原线程，使用同一实际 Turn 的确认机制；旧累计用量与审批账本继续有效，资源通知本身不授予额度、算法或科学裁决。明确诊断为“等待已完成审批”的失败 blocked Goal，可通过 `resolve_approved_budget_dependency` 核对原审批、当前失败、未提交调用、Goal 代际和真实停止状态后转为操作暂停，保留原故障与全部预算历史，再经正常 Resume 继续；未批准、已耗尽、身份冲突或仍有外部所有者时不得这样恢复。

已安全暂停且保留原生 Goal 的 Investigator 可通过 `submit_paused_goal_guidance` 显式提交技术纠正，无需伪装为 NEEDS_ATTENTION。纠正绑定原线程、Goal 代际、冻结科学对象和既有累计预算；恢复只激活原 Goal，在实际最新活动 Turn 上调用带 `expectedTurnId` 的正式 `turn/steer`，确认返回同一 Turn 后才记为消费。安装 SDK 无原子“暂停中投送”接口，不能承诺激活与投送之间没有执行竞态；预算、Turn 或所有权变化优先处理，投送失败保留未消费记录并确认原操作停稳，不新增普通模型 Turn、不重建 Goal 或清零预算。无待投送纠正时沿用原恢复路径；实际模型是否正确采纳技术事实须由后续正常轨迹验证。

资格审查以 `no_qualified_candidate` 停止时，监控投影为 `NEEDS_ATTENTION`，保留科学状态、停止原因与原有恢复权限。读取历史任务时，已停止的科学状态优先于残留的 `run_state.RUNNING`；Task Console 的 Agent 页签与 State 卡片使用一致的生命周期和进程核验结果。

历史记录只读保留。缺少 `science_graph.sqlite3` 的 legacy workspace 必须显式迁移后执行；runtime 不以旧 Canvas 路径回退。迁移不静默重解释历史 Evidence、Outcome 或 Judge verdict。

## 7. Runtime、资源与 Supervisor

CLI 的 run / resume 经 managed runtime 托管。Task runtime 的 lease、PID / process identity、heartbeat 和锁共同定义运行所有权。`--foreground` 使用同一 Workflow 和控制面，只改变进程托管方式。Web / TUI 消费真实运行投影，不拥有调度和科学写权限。

资源边界彼此独立：

- `max_cycles` 限制一次 run / resume invocation；恢复原 Cycle 占首个 slot，耗尽不代表 Goal 完成。
- Investigator Token 预算按 Cycle 冻结策略与追加式账本执行；不以 round 数或 UI 估计替代。
- 当前新 Cycle 使用 `three_plus_three_v1`：前 3 份按预算审查流程投入；第 4 份须 Supervisor 一次授权；获授权后才可在真实阈值追加第 5、6 份，不发第 7 份。历史 Cycle 保留冻结策略。
- Budget Reviewer 只判断继续投入，不修改科学对象；SDK 校验进展引用、去重并原子记账。拒绝与审查失败分别登记，均不能虚构额度或科学 verdict。
- Provider 账号额度、模型容量、路由与科研 Token 预算独立；暂停新调用不等于取消已运行的合法 turn。

无新合法提交时，预算否决或耗尽保留原科学缺口与安全检查点；科研保全必须依照明确授权和独立归档合同执行，成功校验后才能释放 Worker Claim。保全不新增终局科学裁决或图谱科学节点。

Supervisor 对已授权任务持续持有恢复或等待责任。故障先登记异常并区分工程、Provider、资源、科学阻断和人工停止。相同行动去重，失败后保留证据并退避重查；重复失败不自动获得更大科学或工程权限。

工程修复在隔离副本中形成候选，独立审阅与离线复现 / 回归通过后，SDK 才能受控应用。候选不得更改自身验收器、预算、权限、Worker 控制或既有测试来取得通过。修复凭证绑定异常、冻结检查点、源码 / 配置 / 测试哈希与验收结果；输入变化即失效。应用前再核对，冲突拒绝或回滚。

恢复须经原启动器的所有权与检查点守卫；源码变化、模型自述或 PID 存活不足以证明恢复。启动后的新模型 / 工具活动才可记为 `runtime_activity_verified`，其含义仅为活动恢复。普通恢复次数与有效新修复的额外恢复授权分别核算，不清零历史计数。工程 Agent 的自动修复受执行策略限制，Git 收尾按开发流程处理。

人工暂停、终止、Goal 完成、真实预算限制、Claim 冲突及缺少安全检查点不能被自动恢复绕过。无法安全继续时保存负责人、等待原因、下一动作和检查时间，并如实展示。

### 静默技能维护

`skill_evolution` 是独立的轨迹方法存储与维护模块，不拥有科学状态写权限。用户在任务设置中开启后，Investigator / Literature 可以在正常调用的工作目录生成可选旁路经验；科学交付 JSON 保持原合同。SDK 在科学调用 committed 后处理旁路，检查格式、来源路径、内容摘要和精确 base_version，保存不可变技能包及提请。维护失败只保留本地诊断，不触发科研重试、独立学习 Agent、模型评分、效果评测或额外 Cycle。

每个逻辑调用固定一个习得技能清单；所有重试和 Resume 继续原绑定，新版本只在后续安全调用采用。兼容线程的 developer contract 允许 SDK 注入版本化的 task-level 扩展，名称、描述和路径按需披露，正文不例行预加载。旧线程保留原白名单。系统 Skill 与手动声明的任务 Skill 保持原目录；后台维护只写独立习得技能库。

用户开关在执行边界热读；关闭和发布共享偏好锁，关闭后停止新建与修订，保留已采用版本。SQLite 事务协调同机 Worker 的提请、活动索引和事件 outbox。重要事件引用确切提请及前后版本；它属于 record_only 里程碑，不伪造运行故障或科学 verdict。任务级、通用技能和官方 fixture 的隔离规则见 [使用说明](../docs/skill-evolution.zh-CN.md)。

## 8. 工程维护边界

SDK 按编排、角色调用、合同、Registry、状态恢复、transport、投影与 UI 分模块。稳定入口可保留轻量兼容 façade；新增逻辑进入对应职责模块。同一机器概念只有一个权威 registry / policy，副作用与可重建投影分开。

系统修改先确定当前机制、目的和不变量，再同步对应权威设计与实现。修改原因和验证证据写入 Git；普通 UI、确定性修复、文档及可直接验收的 Prompt 修改走常规开发验证，无需额外登记或运行审查文档。仅当具体 Agent/科研流程的行为或效果必须靠修改后的真实轨迹才能判断时，触发 `tm-change` 并登记 TMA；混合修改只登记待观察行为。触发详规与后续复审由 `tm-change` / `tm-audit-review` 技能维护，历史 TMC/TMA 保留。

文档持续维护现行规则：原位更新对应章节，专题内容链接权威规范，历史方案与验收流水交给版本和审计记录。SDK 主文档不追加日期补丁、任务科研案例或测试报告。


独立 Literature 作业的新检索规划、证据分层和 Research Chat 后台等待见 [Literature 工作流](../docs/literature-workflow.zh-CN.md)。正式 Cycle 继续使用现有 Stage/Contract；独立规划记录不构成科学 Evidence。


JSON 规范化在顶层数组漏写结束符、随后出现当前 Schema 已知根字段时，可在唯一可解析位置只插入一个 `]`，保留全部字面值；重复键、未知根字段、嵌套歧义和内容损失不走此修复。首次 Lead 交付若尚无科学检查点，仅在原失败/实际输出/未提交调用、旧源码及新解析器的离线逐字修复探针全部绑定后，才能取得一次受控新交付；不创建虚假科学检查点或 H 锁定。运行源码指纹包含 output_normalization 依赖；旧快照若未记录该依赖哈希，须核对仍固定在原提交的运行树和已记录导入方，明示此证据不等于历史依赖加载哈希。

已完成 Native Goal 未写指定交接时，原 Goal 不重新激活，也不由操作员将研究原文合成 Findings。显式操作员重交付授权须绑定冻结 H、原未提交调用、SDK 捕获原文哈希、GET 核验的 complete Goal 身份和一次性源码凭据；同一线程、同一 Cycle 在原预算内开启一次正常受管交付，由 Investigator 核查原文并自行撰写交接。原 Goal、原失败调用与原文保留；只排除该终局操作作为新调用的活跃 Goal 来源，新调用若中断仍按其自身来源正常恢复。该凭据不认证科学结论或绕过暂停、预算与并发约束。

完成 Goal 的清理必须在有效 Findings 或明确行政交付已读出后执行；交接缺失/无效时保留服务端 complete Goal。兼容旧 SDK 提前清理的历史故障时，只有 GET 明确返回空、原运行源码哈希证明提前清理顺序，且原 Codex 运行日志中的精确 update_goal(complete) 调用与正式工具响应、会话和原 Goal 身份全部吻合，才能以该历史完成响应验证原终局身份；助手自述、人工重建对象或其他调用输出无效。恢复前再次检查当前 Goal 槽位，发现外来/活跃身份即拒绝。

若同一冻结步骤在旧 SDK 中发生过正式 Token 续批，早代预算中断调用仅在原始中断事件、随后 SDK 批准事件、同线程/目标身份及后代完成时间相互吻合时，才与已完成终局代一并排除来源重附着。关闭时追加的普通 interrupted 时间戳不能代替原始预算边界。重交付准备若因该旧来源冲突在创建新 Goal/任何 Investigator Turn 之前失败，可以按已消费的原授权重新验证工程准备；不能掩盖已执行 Turn 或再发科学额度。原日志允许仅追加同线程设置事件，完成证据仍按原长度与哈希逐字核对；后续 Goal/模型活动会使该归档准备失效。

### 终局 Native Goal 后的操作员技术产物恢复

当原 Investigator 调用因 Native Goal blocked 失败且没有提交科研交付时，操作员可在该 Cycle 的 `attention/technical-recovery-authorization.json` 明确登记一次已完成的技术产物恢复。该授权位于 Agent 可写工作区之外，绑定原失败调用、线程、冻结科学对象、原脚本/恢复脚本和已恢复产物的 SHA-256，并记录必要的资源例外；必须同时向原 Investigator 提供待消费的操作员说明。SDK验证原 Goal 的终局身份、既有停机/预算/协议约束及全部文件身份，才生成一次性修复凭据并允许新 Goal 检查已恢复产物。旧失败、已花额度、原 Goal 身份和所有科学判据保留；凭据不代替 Investigator 交付或 Judge 裁决，不自行增加科研 Cycle 或额度，也不授权后续额外计算。没有完整操作员授权及产物证明时维持原有 hold。
