# 路径锚点与访问边界

所有路径都从本次 Context 注入的锚点解析；锚点对应当前任务的实际位置，不得硬编码某个 clone 或 Obsidian vault 的路径。

| 锚点 | 含义 |
|---|---|
| `task_home` | Task 科学根；`research_plan.md` 的 canonical 所在根 |
| `note_area` | 人类可读、可能同步的文档区入口，含 plan、`exploration.canvas` 与 `notes/` |
| `research_root` | TM runtime canonical root；新 runtime 的写入根 |
| `cycle_dir` | 当前单个 Cycle 的 runtime 目录 |
| `cycle_workspace` | 当前 Agent 的 cwd 与可写 workspace |
| `benchmark_root` | 可选的 task-local 冻结/参考科学输入；未提供即不可用 |
| `legacy_research_root` | 仅供历史迁移读取；不得用于新 runtime 写入 |
| `notes_dir` | 人类可见的 `notes/` 目录 |
| `notes_vault_root` | 可确定时提供的 Obsidian vault 技术导航根，不扩大科研来源权限 |
| `repo_root` | 仓库技术导航根，不扩大科研来源权限 |

缺失的锚点不会出现在 Context 中；不得推测其值，应报告缺失的锚点。已提供的路径无法解析时，只能从已知锚点做聚焦查找；仍无法定位就报告具体缺口，不得猜测内容。

科研输入与历史产物的检索、目录枚举和读取限于本次获准的任务根及明确授权的外部来源。可按需追踪本任务的历史 H、Cycle、legacy 根与 Required Context 授权文件；文件不在预先列出的清单中不妨碍范围内的必要发现。共同父目录、仓库或笔记库可读，不构成搜索其他任务的授权；SDK、软件和库源码仅按技术用途访问。来源无法在此范围内恢复时，报告具体缺口或使用当前获准的 Literature 路径。

Required Context 是科学输入文本，SDK 不会从 prose 自动抽取路径、生成 `source_manifest` 或自动打开文件做字段/字面值核验。`[[nodes/某笔记|显示名]]` 中的显示名不是路径；裸节点名是图谱 ID，不是文件。

- 面向机器执行的命令和脚本参数使用绝对路径。
- 写入 Markdown 正文的笔记引用使用库内相对路径，以便同一份内容可在两个库中打开。

## 同步笔记与科学计算数据隔离

`task_home`、`note_area` 与 `notes_dir` 属于人类可读、可能同步的笔记区。这里只能放轻量的 `research_plan.md`、`exploration.canvas` 与 `notes/` 投影；不得写入原始网络、CSV、NPZ、`.data`/`.pickle`、长 JSON rows、运行事件、计算脚本或 checkpoint。Agent 对这些同步区域只读。

新计算、原始科学附件与机器产物写入非同步的 `research_root/cycle_workspace` 或 `benchmark_root`，并保留可审计的 Evidence 引用。`legacy_research_root` 仅用于读取既有历史来源，不能据此向 Vault 写入新大型文件。
