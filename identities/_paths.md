# 路径解析（身份附件，不是 skill）

你的 cwd 是 cycle_workspace（本 Cycle 的 Agent 写入区），Required Context 却是为**两个 Obsidian 库**写的，所以里面是库内相对写法。context 给了展开它们需要的锚点，禁止写死任何一个 clone 的路径。

| 锚点 | 含义 |
|---|---|
| task_home | Task 科学根；research_plan.md 的 canonical 所在根 |
| note_area | 人类可见文档区域；plan / exploration.canvas / notes 的稳定入口 |
| research_root | TM runtime canonical root；新运行只写这里的 Cycle |
| cycle_dir | 当前单个 Cycle runtime 目录 |
| cycle_workspace | 当前 Agent 可写 workspace；也是 Agent cwd |
| benchmark_root | 可选 task-local 冻结/参考科学输入目录；不存在时不注入 |
| legacy_research_root | 仅历史迁移读取；不得作为新 runtime 写入根 |
| branch_canvas | 探索 Canvas 绝对路径 |
| notes_dir | 人类可见 notes 区域 |
| notes_vault_root | 笔记库根（若可确定） |
| repo_root | 仓库根 |

缺某个锚点时它不会出现在 context 里，此时不要猜，在输出里说明缺什么。

## Required Context 与路径

Required Context 是 Research Task 的科学输入文本。SDK **不会**从其中的 prose 自动抽取文件路径、生成 `source_manifest`，也不会据此自动打开文件做字段/字面值核验。

常见写法本身仍只是文本约定：

- `[[nodes/某笔记|显示名]]`：wikilink，竖线后面只是显示名，不是路径。
- `` `gs_study/…/x.md` ``：仓库内路径文本；需要读取时由 Agent 基于当前明确上下文解析。
- 裸节点名（如 `decision-Gg-handoff-20260916`）：Canvas **节点 id**，不是文件。

## 写路径时

- 面向机器执行的命令、脚本参数：用绝对路径。
- 写进 Markdown 正文的引用：用库内相对路径，因为同一份汇报要在两个库里打开。
