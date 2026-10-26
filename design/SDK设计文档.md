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
| Reporter | 完整报告与 Outcome 短文案 | 不改裁决、不选下一方向、不创建图谱科学对象 |
| Supervisor | 故障诊断、受控工程修复与恢复验证 | 不改科研合同、Evidence 或科学裁决，不越过资源和所有权守卫 |
| SDK | Stage 调度、合同闭合、身份、账本、持久化和投影 | 不选科学假设，不解释证据，不代签 verdict |

Semantic Annotator 是辅助 B 层模型，只在 SDK 给定的封闭选项中做语义映射。它没有证据创作、科学裁决或状态迁移权。Hypothesis Lock、Evidence Registry、Readiness 和 Commit 是 SDK 机制，不是 Agent。

科学选题以尚未满足的 Goal / Research Contract / Final Gate 为主线，或解决其逻辑必需的 prerequisite。Lead 判断科学方向，Challenger 独立审查资格。领域案例、具体选题禁令、实验方法和科学质量判据放在任务合同、Stage Card 或按需 Skill 中。

候选必须提出具体的科学主张及可区分后果，说明相对最近历史命题改变的关系、机制、结构假定、定理或模型族；重述 Goal、列出理想交付、扩展覆盖或限制参数个数均不能单独定义新机制。只有任务合同明确要求的覆盖范围才形成资格义务。Lead 不必预先完成证明或推导；Challenger 应区分尚未求解的明确命题与尚未定义的研究任务。存在性命题须界定对象族，有限搜索失败不构成一般反证。绑定定义、输入资格及判别规则须进入冻结科学正文，不能依赖提案理由补充。

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

### 资格审查与锁定

候选携带科学命题、范围、选题理由、Goal 推进和 `decisive_test`；候选 ID、H ID 和工作流字段由 SDK 管理。新派生 H 必须有合法科学来源和有类型的 H→H 谱系，首个 root H 除外。科学身份不依赖文本相似度。

Challenger 审查必须绑定被审候选的完整科学内容。`revise` 后只允许一次有界修复，并须由 Challenger 独立确认。`cannot_repair` 或未确认的修复不能被 SDK 升格为 qualified；恢复不能沿用已改变对象的旧审查。新的 Qualification attempt 保留前次拒绝反馈，反馈不自动成为新候选的当前 verdict。

Lock 原子登记 H identity，冻结 `id / hypothesis / scope / decisive_test` 及来源。当前 Cycle criteria 只生成一个 `C-1` 机器引用锚点，内容来自同一个 `decisive_test`；不另造科学目标或证据任务队列。Lock 后不换命题、估计对象或允许输入；改变科学目标应进入新候选流程。

### Investigation、Evidence 与裁决

统一 Investigator 在科研 episode 内自主选择方法，完成必要研究后交付自包含 Findings。科学主张及其前提、适用范围和不确定性写入科学正文；SDK 管理机器身份和产物溯源。交付使用 invocation 专属接口，由 SDK 校验、登记和交接，避免为登记重复科研或改变独立 Finding 数量。

科学 Agent 的输出、辅助语义映射和 SDK 确定性闭合分属 A / B / C 三层。B 层不代替科学角色判断证据充分性；C 层不根据格式正确推导科学结论。

Evidence 先由 Registry 登记，再生成裁决视图。SDK 负责 producer invocation、路径和内容 hash、来源访问层级及受保护数据身份的技术校验。调用产物 manifest 只能证明执行产物身份，不能单凭它证明事前冻结或 held-out / Final Gate 资格。`adjudicable` 表示技术上可交付裁决，不表示结论为真。不可读来源不能获得可裁决资格；历史或漂移产物不能伪装成本轮独立验证。

Judge 消费 compact `investigation_result` 和明确限定范围的历史裁决视图，独立决定 Evidence 是否回答冻结检验。历史视图从当前 Registry 权威无副作用地生成，不信任旧 Manifest 的资格快照，不重新登记旧证据。

Judge 的 objection 必须是冻结命题内的最小可执行增量；SDK 验证后按既有有限返工和资源边界调度。没有新正式提交的研究、预算否决或运行失败，均不能冒充新 Findings 或 terminal `open`。没有可裁决 Evidence 时保存非科学停止原因与检查点。

### 综合、前沿和任务完成

终局 Judge 之后，Lead 的 fresh `cycle_synthesis` 只综合认识并给出 Scientific Model `keep|replace`；`replace` 提交完整新正文。SDK 校验、预览并原子提交版本。同一 episode 的 `frontier_maintenance` 只维护现有分支的 `active / parked / closed`，不创建下一轮 H。

Reporter 生成完整报告，再提供 Outcome 的短标题和摘要。合同中的 `decision.title/summary` 是文案接口，不创建独立 Decision 图节点。SQLite 的 H / External、H→H 谱系与 Cycle Outcome 由 SDK 持久化；Canvas 是确定性展示投影。

Cycle 完成与 Task Goal 完成独立。仅由本轮 `supported` 事件触发 fresh `goal_completion`；其结果为 `passed|not_passed` 与理由，不生成规划状态。Goal Gate 评审以独立记录关联触发它的 Cycle Outcome；Outcome 的科学 verdict 不变。Lead 的精简图谱保留完整 H 索引，并显式展示历次 `not_passed` 的原文理由，即使来源 H 的 frontier 已关闭。Lead 对照 Final Gate 和已交付成果判断下一候选的必要性；Challenger 独立核对其 Goal 优先级。失败理由是历史任务裁决，不自动指定下一 H，也不因下一次理由未重述而自动解决。下一 Cycle 重新选题并审查，不能把收口时的临时 synthesis 当作新的长期科学权威。

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

transport timeout、inactivity watchdog 和进程异常属于调用恢复。中途 progress、delta 或 interrupt 引发的完成事件不能冒充合法 final output。恢复同一 thread 时给最小 continuation；只有合法最终输出才能进入合同闭合与 Evidence 登记。

运行恢复、工程修复和科学审计独立登记。恢复继续累计真实活动和调用计量，离线等待不算科研执行时间。观测序号和 UI 状态不得反向决定身份、资源预算或科学结论。

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

## 8. 工程维护边界

SDK 按编排、角色调用、合同、Registry、状态恢复、transport、投影与 UI 分模块。稳定入口可保留轻量兼容 façade；新增逻辑进入对应职责模块。同一机器概念只有一个权威 registry / policy，副作用与可重建投影分开。

系统修改先确定当前机制、目的和不变量，再同步对应权威设计与实现。修改原因、逐文件说明和验证证据写入 Git 与修改登记；静态可验收事项登记 TMC，必须未来真实科研 Cycle 观察的事项登记 TMA。具体流程由 `tm-change` / `tm-audit-review` 技能及相应 CLI 维护。

文档持续维护现行规则：原位更新对应章节，专题内容链接权威规范，历史方案与验收流水交给版本和审计记录。SDK 主文档不追加日期补丁、任务科研案例或测试报告。
