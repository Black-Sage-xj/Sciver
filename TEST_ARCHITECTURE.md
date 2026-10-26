# Sciver 测试架构

> 状态：现行测试系统指导原则
> 范围：`tool/theory_master/tests/` 与 `tool/theory_master/scripts/run_tests.py`
> 设计权威：Sciver 当前设计按 scope 分为 `design/SDK设计文档.md`、`design/Canvas设计文档.md`、`design/ResearchPlan设计文档.md`；本文只规定“如何测试当前设计”。

## 1. 核心原则

测试的职责是保护正确性，不是冻结历史实现。

Sciver 处于持续演化阶段。一次有意的架构修改可以合法改变 stage、contract、router、字段或内部模块边界。因此：

1. **系统不变量高于当前实现。** 恢复、证据可追溯、权限边界、状态完整性等不变量一旦失败，应优先视为真实缺陷。
2. **Architecture 测试描述当前设计，不定义永久设计。** 当对应 scope 的 `design/*.md` 规范已有意改变架构时，应在同一变更中同步更新 Architecture 测试，而不是为了让旧测试继续通过而把新架构改回去。
3. **Regression 测试保护失败模式，不保护旧 API 形状。** 历史 bug 的根因仍需被覆盖；若实现路径改变，应重写回归测试，使其继续证明“同类事故不会回来”。
4. **UI / implementation 测试权威最低。** TUI、CLI 展示、observability、provider adapter 等测试主要保护用户表面和局部实现，不得反向决定科研 Workflow 架构。
5. **合并前仍要求全量通过。** 分类不是允许长期保留红灯；它用于解释失败、缩短重构反馈回路，并决定应该修代码还是更新测试。

一句话：**设计原则决定测试；测试验证设计。旧测试不能自动成为新设计的上位约束。**

## 2. 四类测试

### 2.1 `invariant` — 系统宪法

用于保护跨架构版本应长期成立的性质，例如：

- recovery / resume 不丢失权威状态；
- Evidence、read trace、invocation 可追溯；
- Hypothesis Lock 与运行权限边界不被绕过；
- 非法写路径、历史重写、进程控制被拒绝；
- Task/Cycle 身份与持久化状态保持一致；
- runtime owner / liveness / recovery 语义正确。

修改此类测试必须说明：**系统原则本身为什么改变**。不能仅以“新实现不方便通过”为理由削弱测试。

### 2.2 `architecture` — 当前架构契约

用于描述当前三份 `design/*.md` 规范的实现，例如：

- A / B / C 分层；
- Agent stage 与 contract；
- router / handoff；
- Scientific Model 更新方式；
- prompt projection；
- system-level / task-local 两层 Skill registry、research reading、task mode 的当前接线。

这类测试在架构重构中允许改变。失败时首先回答：

> 是实现违背了新设计，还是测试仍在描述旧设计？

若属于后者，测试应和代码、设计文档一起更新。

### 2.3 `regression` — 历史事故防回归

用于固定已经发生过的故障模式，例如某次 Cycle 的 contract repair、Judge rework、JSON normalization 或 audit 事故。

好的 Regression 测试应该表达：

> 输入触发了哪种历史风险，以及系统现在必须保证什么结果。

避免永久绑定旧函数名、旧字段布局、旧 stage 数量等非本质细节。

### 2.4 `ui` — UI / implementation surface

用于 TUI、CLI、observability、cost/pricing、provider event 等用户表面或局部实现。

它们仍然必须通过，但在架构设计冲突时不具有上位权威。优先重写适配层测试，而不是让核心 Workflow 迁就旧 UI 结构。

## 3. 分类机制

分类注册表位于：

`tests/suite_policy.py`

每个 `test_*.py` 必须有一个主类别。注册表自检会阻止新增测试文件未分类。

历史 `test_core.py` 同时包含架构行为和关键不变量。本次不做大规模物理拆文件，而通过 `INVARIANT_TEST_IDS` 把其中稳定的不变量额外加入 invariant gate。以后逐步拆分 `test_core.py` 时，应减少这个 overlay，而不是继续扩大单个大文件。

因此分类是“测试视角”，不要求四类严格互斥。

## 4. 推荐运行方式

查看分类规模：

```bash
PYTHONPATH=tool/theory_master python tool/theory_master/scripts/run_tests.py --list
```

架构重构时优先跑：

```bash
PYTHONPATH=tool/theory_master python tool/theory_master/scripts/run_tests.py --category invariant
PYTHONPATH=tool/theory_master python tool/theory_master/scripts/run_tests.py --category architecture
```

修历史故障时：

```bash
PYTHONPATH=tool/theory_master python tool/theory_master/scripts/run_tests.py --category regression
```

最终验收：

```bash
PYTHONPATH=tool/theory_master python tool/theory_master/scripts/run_tests.py --category all
PYTHONPATH=tool/theory_master python -m theory_master smoke
```

原来的 `unittest discover` 仍然有效；分类运行器只是增加语义层，不替换 Python 标准测试机制。

## 5. 架构重构时如何处理红灯

按以下顺序判断：

1. **Invariant 失败**：默认先怀疑代码破坏了系统原则；除非设计原则本身被明确修改，否则修代码。
2. **Architecture 失败**：对照对应 scope 的最新 `design/SDK设计文档.md`、`design/Canvas设计文档.md` 或 `design/ResearchPlan设计文档.md`。若测试陈旧，更新测试；若实现偏离设计，修代码。
3. **Regression 失败**：确认历史失败模式是否仍可能发生。若风险仍在，修代码或重写同等保护的测试；若问题已被架构消除，可删除旧测试，但必须由新的更高层测试覆盖该风险。
4. **UI 失败**：优先修表面/适配层；禁止为了旧展示结构反向污染核心 Workflow。

禁止使用以下做法：

- 为“让测试变绿”恢复已废弃的 stage 或字段；
- 把 Architecture 测试偷偷提升成永久不变量；
- 删除 Regression 测试却不说明历史风险由什么新测试覆盖；
- 用大量 snapshot 锁死 prompt 全文、JSON 字段顺序或内部函数布局；
- 因单个局部测试方便而复制生产逻辑到测试代码。

## 6. 新增测试的要求

新增测试时先选择其存在理由：

- 保护跨版本系统原则 → `invariant`
- 验证现行 SDK 架构 → `architecture`
- 防止一个已知事故复发 → `regression`
- 验证 UI / CLI / adapter / observability → `ui`

新增 `test_*.py` 后必须同步登记到 `tests/suite_policy.py`。若一个模块越来越混合，应拆文件；不要长期依赖越来越大的 override 清单。

测试名应描述行为与保证，而不是只描述函数。例如优先：

`test_recovery_resume_reuses_cycle_and_skips_completed_roles`

而不是：

`test_resume_helper`

## 7. Test Architecture Audit

当出现以下任一情况，应做一次测试架构审查：

- 一个大架构改动导致大量测试同时失败；
- 为通过测试不得不恢复已经废弃的设计；
- `test_core.py` 或其他单文件继续明显膨胀；
- Regression 测试数量持续增加但没有合并；
- 测试大量断言内部函数、字段顺序、完整 prompt 文本。

审查目标不是提高测试数量，而是确认每个红灯都能回答：

> 它是在报告“系统原则被破坏”，还是“当前实现与当前设计不一致”？

只有这个问题清楚，测试系统才会促进 Sciver 演化，而不是阻碍它。

## 8. 架构变化与失败分类（Test Conflict Disposition）

每一个失败必须有独立的原因与证据，不能因架构升级、失败数量太多或希望合并 Git 而默认放行。

| 裁决 | 含义 | 必须采取的动作 |
| --- | --- | --- |
| `REAL_BUG` | 有效设计、不变量或行为契约被新实现破坏 | 修生产代码，补会复现该风险的回归 |
| `OBSOLETE_TEST` | 旧断言保护已被正式废止的实现形状 | 记录旧目的、现行设计依据、等价新测试及差异后迁移/删除 |
| `FIXTURE_MISMATCH` | 旧研究计划、序列化字段、Mock/私有初始化路径不符合当前构造契约 | 迁移 Fixture，然后重新核对预期；不能把真实问题顺手忽略 |
| `ENVIRONMENT_ERROR` | 有可验证的解释器、扩展模块、依赖或外部资源问题 | 修环境/选择适配解释器，保留非零退出和未运行统计 |
| `UNRESOLVED` | 暂时不能证明是旧测试还是代码缺陷 | 保留红灯、完整堆栈和审计条目；不得改写为通过 |

**必要记录字段**：触发的测试 ID、变更对应的 TMA、原测试要防的风险、当前规范与代码、复现命令及 Python 环境、失败症状/堆栈、裁决及证据、取代它的新测试（若适用）、尚未运行的真实 Cycle 验证。

发现 `unittest` 模块导入失败时，分类运行器仍应展示其他可加载测试的清单，但 **返回非零退出**；单个类别未命中该模块并不意味着整个测试发现成功。不要把 `unittest.loader._FailedTest` 算作已经执行通过的测试。缺少 `_ssl`、`numpy`、`openai_codex` 时先核查解释器与相应环境，不能根据异常字符串直接断言全是环境错误。


## 9. 工程契约验证与科学判别验证分层

- **确定性工程层**：单测、权限、不变量、生产接口、快照一致性、旧数据恢复；可频繁运行且不调用付费模型。任何失败仍按 §8 裁决。
- **科学参考层**：`tests/fixtures/scientific_discrimination_canaries.json` 保存带科学参考答案的反例/正例，包括不可达精确检验、可达但不能推论功效充分、数值误差不能分辨符号、数学反例无须统计显著性。其 `test_scientific_canary_reference.py` 仅验证**参考 oracle 和测试用例自身**，不是 Lead/Challenger/Judge 的真实行为成绩。
- **真实 Agent 行为层**：对 Lead/Challenger/Investigator/Judge 用相同科研问题分别执行新鲜、独立的真实运行；向 Agent 提供问题与对应任务合同，**不得把参考答案或预期裁决泄漏到 Prompt/输入/工作目录**；归档完整 Prompt、固定 Goal/H/decisive test、模型版本、响应、Evidence、Judge/Goal Gate、审计者依据与成本。独立审核能否发现不可达目标、是否错拒可行方案、能否区分反例/数值不确定/证据缺失。未执行不得标为通过。
- **双层合并门槛**：确定性工程层必须先通过；只有需要证明 Agent 科研判断能力的变更才额外要求真实运行审查。真实模型答案有波动，不使用整段回复文本精确相等作为科学评判。

历史测试迁移时不得仅因 Prompt 内出现“统计可达性”“参考基线”就宣称科学判断通过；也不得把 SAB/Test1 的冻结数值参数、上述科学 oracle 或特定判别式写进通用 Agent Prompt。

## 10. 真实模型 Prompt 测试授权闸门

**本节只是测试设计规则，不代表已经执行或批准真实 LLM 测试。** 根据 `tool/.codex/skills/tm-change/SKILL.md` §6.0 和 `tool/.codex/skills/tm-audit-review/SKILL.md`：在执行任何真实模型参与的 Prompt/Stage/Agent 评估、TMLab/TBH 对照运行、LLM-as-a-judge 或作为测试的真实 Cycle 前，须向用户展示具体目标、冻结案例与反例、评估标准、模型/Provider、调用次数与 Token/费用风险、工具及文件边界，并获得**本次明确批准**。通常的“接受修改”“运行测试”或 TMA pending 不能代替授权；不得自动触发付费回归、扩大调用或重复付费重试。

未批准时可继续静态装配、Fixture、Mock、离线轨迹复审及科学参考 oracle，并把真实效果标为 `PENDING user authorization / NOT RUN`。是否批准独立 LLM 评委也须单独纳入同次明确授权。此闸门不改变用户已经明确主动发起的正常科研任务运行授权，但不能把测试伪装成正常科研运行。
