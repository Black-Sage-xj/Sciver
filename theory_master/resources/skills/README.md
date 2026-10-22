# Sciver optional skills

Skill = 某角色本回合才可能触发的能力。每轮必加载的职能写在 `tool/theory_master/identities/`，不是 skill。

Skill registry 分两层：

- **System-level Sciver skills**：位于 `theory_master/resources/skills/`（唯一权威版本，随 Python 包发布），由 `ROLE_SKILLS` 定义，所有 Master 共享。
- **Task-local Master skills**：位于该任务配置目录的 `.theory-master/skills/<name>/SKILL.md`，并由同一 `theory-master.config.json` 的 `task_skills.{role}` 显式白名单声明。它只对该 Master 生效，不能覆盖同名 system skill。

例如某个任务可以声明 `"task_skills": {"investigator": ["domain-analysis"]}`，并在其配置目录放置 `.theory-master/skills/domain-analysis/SKILL.md`；其它任务不会因此获得该领域能力。系统级 Skill 仅保留可跨科研领域复用的通用方法、权限及证据规则；实验协议、专有工具、具体路径和项目样本身份必须留在 task-local Skill。

## 复用

科研图谱的权威数据由 SDK 维护（如任务中的 `science_graph.sqlite3`）；Canvas/`exploration.canvas` 是展示投影，不是 Agent 的第二数据来源。节点谱系、Outcome 与前沿状态依 SDK/SQLite 权威记录。

| 本目录名 | 来源 | 谁偶发使用 |
|---|---|---|
| `compute-dispatch` | Sciver 本地 Skill | Investigator 计算估时、并行、Slurm/HPC、监控恢复与产物验收；不承担 Agent/Canvas/lifecycle 调度 |
| `report-writing` | Sciver-native | Reporter 的 human-first 科研叙事、Obsidian 公式与图注规范；不拥有笔记路径、文件写入、Canvas 或科学裁决 |
| `tm-scholar` | Sciver 原生实现（不包含第三方 Skill 源码） | Literature 元数据检索、引用追踪、开放全文读取与证据级别标注 |
| `novelty-check` | 基于 ARIS 同名 skill 去 reviewer/verdict/workflow 后的 Sciver 适配 | Literature 最近邻、重叠、剩余 delta 与检索覆盖审计 |
| `proof-audit` | 历史保留的 ARIS `proof-checker` 只读审计能力 | 当前 Judge 不加载；复杂 proof correctness 属于 Investigator 研究/验证阶段 |
| `result-to-claim` | 历史保留的 ARIS claim–evidence 审计能力 | 当前 Judge 不加载；Judge 直接评价 SDK 提供的 compact Investigation Result |

Reporter 的 `report-writing` Skill 按 Stage 需要加载；写作清晰度、套话删减及证据措辞规则参考 [Academic Humanizer](https://github.com/AIScientists-Dev/academic-humanizer) v0.3.3（MIT）的通用方法，不引入独立 reviewer 或 report 交付过程。

Sciver 系统级 Skill 只在此目录维护，不使用软链接或硬链接，不维护额外的 `skills/` 开发镜像；上游 Skill 更新须按 TM 的角色与权限边界显式复核。
Literature 是唯一允许使用 scholarly Skill 写文献中间产物的证据角色；其工作目录固定为 Cycle `workspace/literature/`，不得写 Canvas、任务外个人笔记库或 Scientific Model。

## 白名单

| 角色 | system-level optional skills |
|---|---|
| Research Lead / Challenger | （无） |
| Investigator | `theory-investigation`, `numerical-investigation`, `symbolic`, `proof-writer`, `analyze-results`, `experiment-plan`, `compute-dispatch`, `plotting` |
| Literature | `tm-scholar`, `pdf-reading`, `research-lit`, `novelty-check` |
| Judge | （无；保持轻量，只执行 terminal_assessment） |
| Reporter | `report-writing` |

`formula-derivation` 不作为独立 optional skill：其 target/assumption/dependency-path/closure/limiting-case 等通用公式推导方法已经并入 `theory-investigation`；正式代数/符号操作再按需加载 `symbolic`，严格 theorem/lemma 证明加载 `proof-writer`。

## 注入

`theory_master.skills.ROLE_SKILLS` 只表示系统级机器白名单；运行某个 Master 时，`role_skills(..., task_config_path=...)` 再把该任务声明的 task-local 白名单合并进来，并保留 `scope=system|task`。身份由 `theory_master.identity` 必加载。Codex 不再预加载 Skill body；新线程的 developer instructions 只暴露两层白名单及具体 `SKILL.md` 路径，Agent 按需读取。
