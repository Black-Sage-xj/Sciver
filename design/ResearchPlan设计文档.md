# TheoryMaster Research Plan 设计文档
> **图谱 V2 状态说明**：Research Plan 的 Goal / Research Contract / Final Gate 仍属人工维护的任务级权威；每个已裁决 Cycle 唯一 Outcome、历史 H 的多 Cycle Outcomes、分支 active/parked/closed 属于 [ResearchGraph设计文档.md](./ResearchGraph设计文档.md) 的 SQLite 科研图谱，不应回写成 Research Plan 顶层重复状态。任务 1 已完成科研图谱生产迁移；Research Plan 的人工维护权限仍不变。

> 状态：现行正式规范
> 范围：`research_plan.md` 的格式、语义、维护权与读写边界
> SDK 运行与事务语义见 [SDK设计文档.md](./SDK设计文档.md)；科学谱系与 Canvas 格式见 [Canvas设计文档.md](./Canvas设计文档.md)。

---

## 1. 定位

`research_plan.md` 是一个 Task 唯一的人类可读长期科学输入与当前科学认识文档。

它只回答两类问题：

1. **这个研究任务长期要解决什么、遵守什么、何时算完成？**
2. **截至现在，对这个问题最精简、仍然成立的科学认识是什么？**

它不是：

- Workflow runtime state；
- Cycle plan；
- Frontier / continuation state；
- Evidence Registry；
- per-Cycle report；
- 实验 protocol 仓库；
- 历史探索流水账。

当前正式格式由本文件唯一规定。旧格式不向后兼容。

---

## 2. 标准模板

当前标准模板以：

`笔记区/探索台账/矩阵腔体Green低信息理论/research_plan.md`

的结构为基准。

持久化后的 `research_plan.md` **恰好包含 5 个同级 H1**，并且顺序固定：

```markdown
# Goal

<长期最终科学问题>

# Research Contract

<长期科学约束>

# Final Gate

<整个 Task 真正完成的判据>

# Required Context

<少量、跨 Cycle 长期稳定的导航入口>

# Scientific Model

<一段当前最新、仍成立的科学认识>
```

### 2.1 H1 不变量

五个正式字段全部使用一级标题：

```text
# Goal
# Research Contract
# Final Gate
# Required Context
# Scientific Model
```

不允许：

```text
# Task Title
## Goal
## Research Contract
...
```

也不允许额外新增其它 H1。

如某个字段内部确实需要结构化内容，可以使用普通段落、列表，必要时可以使用 `##` 或更深层级；这些下级标题属于该字段正文，不形成新的 Research Plan 顶层字段。

---

## 3. 五个正式字段

### 3.1 `Goal`

回答：

> 这个长期研究最终要解决什么科学问题？

性质：

- Human-owned；
- 默认固定；
- 不因 Cycle、Hypothesis、frontier 或中间结果自动改变；
- Scientific Agent 不得自行改写。

内容应是长期目标，而不是当前阶段计划。

不得写入：

- 当前 H；
- 当前 Cycle；
- 下一步实验；
- 当前 blocker；
- runtime 状态。

### 3.2 `Research Contract`

回答：

> 为了使最终结论科学有效，这个 Task 长期必须遵守哪些约束？

适合保存：

- 科学对象定义；
- 可接受/禁止的数据使用方式；
- 统计单位；
- 因果边界；
- 不允许改变的物理口径；
- 长期方法学约束。

性质：

- Human-owned；
- 极低频变化；
- Agent 可以指出冲突，但不得自动修订。

### 3.3 `Final Gate`

回答：

> 什么情况下整个 Task 才能被判定为真正完成？

它是 Task-level completion criterion，不等于某个 Cycle 的 `decisive_test`，也不等于某个 Hypothesis 被支持。

性质：

- Human-owned；
- 极低频变化；
- Goal completion Judge 只能判断是否满足，不能改写。

### 3.4 `Required Context`

回答：

> 哪些长期稳定入口是理解和继续这个 Task 所必需的？

它只保存**稳定导航入口**，例如：

- canonical 理论约束文档；
- canonical context index；
- 长期 source-lock；
- 稳定数据/定义入口。

不得不断追加：

- Cycle report；
- 当前 result JSON；
- 当前 Hypothesis；
- 当前 Decision；
- 临时脚本；
- 每次运行新产生的 artifact。

性质：

- Human-owned / operator-maintained；
- 低频变化；
- 机器可以建议，但默认不自动改写。

### 3.5 `Scientific Model`

回答：

> 截至现在，这个问题最精简、仍然成立的完整科学认识是什么？

它必须是**纯 Markdown 的一段当前状态总结**。

推荐内容只包括：

1. 已稳定建立的核心认识；
2. 关键适用范围或边界；
3. 当前决定性的未解决核心。

不得写入：

- Cycle-by-Cycle chronology；
- 失败探索轨迹；
- 已被推翻的旧解释；
- Evidence ledger；
- artifact 路径清单；
- Frontier；
- 下一步计划；
- JSON / YAML / hidden machine payload；
- 仅为了“保留历史”而复制的旧文本。

Scientific Model 不是 append-only 文档。研究认识发生变化时，应**整体重写、合并和压缩**。

---

## 4. Authority 与维护权

| Section | Authority | 默认变化频率 | 自动维护 |
| --- | --- | ---: | --- |
| `Goal` | Human | 几乎不变 | 禁止 |
| `Research Contract` | Human | 极低频 | 禁止 |
| `Final Gate` | Human | 极低频 | 禁止 |
| `Required Context` | Human / operator | 低频 | 默认禁止 |
| `Scientific Model` | Research Lead 提议，SDK 校验并提交 | 可随 Cycle 变化 | 允许 |

任何 Agent 都不得借 Scientific Model 更新顺便修改前四个字段。

Goal / Research Contract / Final Gate 始终是 Task-level completion 的原始 authority，不得由隐藏 runtime goal-gap / planner state 创建第二套长期任务定义。

---

## 5. Scientific Model 的机器状态

`research_plan.md` 本身保持纯 Markdown。

机器需要的 Scientific Model 元数据独立保存在：

```text
.theory-master/research/scientific_model_state.json
```

该文件保存：

- `schema`
- `version`
- `updated_from_cycle`
- `goal_id`
- `provenance`

**不重复保存 `summary`。**

因此：

```text
research_plan.md
= 人类可读科学内容

scientific_model_state.json
= 机器版本/来源元数据
```

两者不得重新合并成 hidden JSON block。

---

## 6. Frontier 与 Research Plan 的边界

跨 Cycle branch activity authority 在 Canvas 的 `frontier_status`，不在 Research Plan。

```text
.theory-master/research/frontier_state.json
```

只保存 workflow runtime pointer：恢复时应指向哪一个 Canvas 节点或当前 Cycle artifact。它不回答科学上还缺什么，也不属于 Research Plan。

Research Plan 中禁止出现：

```text
# Frontier State
continuation_context
current Cycle plan
runtime/recovery state
```

Canvas 持有可追踪的探索结构与当前 frontier，其 authority 与 Research Plan 分离。细则见 [Canvas设计文档.md](./Canvas设计文档.md)。

---

## 7. 创建与 Bootstrap

### 7.1 人工新建输入

新任务在 bootstrap 前允许只写前四块：

```text
# Goal
# Research Contract
# Final Gate
# Required Context
```

顺序必须正确，全部使用 H1，不允许额外 H1。

### 7.2 Bootstrap 完成后

SDK 自动补齐：

```text
# Scientific Model

尚未形成稳定的科学总结。
```

同时创建独立的：

```text
scientific_model_state.json
frontier_state.json
```

从这一刻起，持久化后的 Research Plan 必须完整满足五字段格式。

---

## 8. 解析规则

当前 parser 必须遵守：

1. 只把精确 H1 识别为 Research Plan 顶层字段；
2. 五个字段名称大小写与空格按规范精确匹配；
3. 持久化任务必须恰好存在五个 H1；
4. 顺序必须固定；
5. 每个字段唯一；
6. 前四个字段正文不得为空；
7. `Scientific Model` 必须为纯 Markdown；
8. `Scientific Model` 中出现旧 TheoryMaster machine marker / embedded JSON 应 fail closed；
9. 不做旧 `## Goal`、frontmatter、embedded-state schema 的兼容读取。

---

## 9. Scientific Model 更新事务

Research Lead 的 `cycle_synthesis` 只能做：

```text
keep
replace
```

### `keep`

当前科学认识没有 durable change：

- Markdown 不变；
- model version 不变。

### `replace`

存在 durable scientific change：

1. Research Lead 基于当前完整认识重写一段 `summary`；
2. 删除被推翻/取代的内容；
3. 不追加 chronology；
4. SDK 验证 output contract；
5. SDK 更新 `# Scientific Model` Markdown；
6. SDK 独立更新 `scientific_model_state.json`；
7. 历史版本写入 append-only scientific model history。

更新 Scientific Model 不授予修改 Goal Contract 的权限。

### 9.1 Research Plan 与 Scientific Model 文本历史分离（2026-10-24）

当前 Markdown 仍保留五字段，历史按内容范围独立编号：

- `plan` 仅包含 Goal、Research Contract、Final Gate、Required Context；
- `model` 仅包含 Scientific Model；SDK 科学认识更新只增长 model 文本历史，不增长 plan 历史；
- 网页分别显示两套版本、原因、范围内差异和文本；恢复只替换所选范围，并保留另一范围的最新内容。并发冲突按所选范围校验；
- 旧 `research-plan-versions/history.jsonl` 和完整快照保留不改。首次受锁访问时，按历史顺序投影为 `plan-history.jsonl` 与 `model-history.jsonl`，各自只记录内容变化，并保留 `legacy_version`、原时间和原因；
- 两套文本历史与机器 `scientific_model_state.json` 的科学版本号各有职责；不改机器版本、Cycle 归属或历史科学裁决。网页的 model_version 表示此处的 Scientific Model 文本历史编号；
- 保存相同内容不新增版本，直接文件修改仅在下次受管访问时记录，不能追认未观测到的中间编辑。


---

## 10. 维护原则

### 10.1 长期保持小

Research Plan 的目标不是“保存所有信息”，而是让新的 Research Lead 用很低的信息成本理解：

- Goal；
- 长期约束；
- 完成判据；
- 稳定入口；
- 当前科学认识。

详细历史应留在 Canvas、Cycle report、Evidence 和 notes。

### 10.2 不保存历史错误

Scientific Model 只保存**现在仍相信什么**。

如果 A 被 B 推翻，应改成 B，而不是：

```text
最初认为 A；
后来 C3 怀疑 A；
C5 又发现……
最终 B。
```

历史路径属于 Canvas / Cycle artifact。

### 10.3 不把计划写成状态数据库

任何为了 routing、resume、版本控制、provenance、frontier 所需的机器字段，都应进入 SDK runtime state，而不是为了机器方便污染 Research Plan。

---

## 11. 与 SDK / Canvas 的关系

三份设计文档的 authority 分工：

- [SDK设计文档.md](./SDK设计文档.md)：Workflow、Agent、transaction、routing、runtime、Evidence、recovery；
- [Canvas设计文档.md](./Canvas设计文档.md)：Canvas node/edge、scientific lineage、scientific/frontier status 与维护权；
- **本文**：Research Plan 五字段格式、Scientific Model 维护和 Plan/runtime 边界。

SDK 只应保存 Research Plan 的系统接口摘要；Research Plan 格式冲突时，以本文为准。
