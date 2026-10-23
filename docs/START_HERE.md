# 从自己的科研项目开始 / Start with your own research

## 中文

Sciver 将一个长期科学问题组织成可持续推进的科研任务。**启动一个新任务所需的唯一正式输入是 `research_plan.md`**；配置、SQLite 科学图谱和运行目录由系统初始化。你可以自己准备这份文件，也可以在网页中与 Sciver 对话生成并修改它。没有现成的 Research Plan 也能从已有项目开始。

### Research Plan 要写什么

你需要向 Sciver 说明四件事，不必先懂文件格式：

| 部分 | 要回答的问题 |
| --- | --- |
| `Goal` | 长期要解决什么科学问题？研究对象是什么？ |
| `Research Contract` | 数据、方法、证据和科学定义有哪些必须遵守的边界？ |
| `Final Gate` | 哪些可核验的结果或交付物意味着整个任务完成？ |
| `Required Context` | 哪些已有知识、资料位置和资源限制需要跨轮次保留？ |

保存后的文件还有第五节 `Scientific Model`，记录当前科学认识和仍待检验的假设。首次建项时 Sciver 会给出初稿；如果导入的旧计划含有这一节，其内容会被标为待验证，而不会自动当成既定结论。正式文件按 `Goal → Research Contract → Final Gate → Required Context → Scientific Model` 排列五个一级标题。你可以在网页预览里修改所有内容，确认后才会写入任务。

### 已有项目如何导入

1. 安装并打开 Sciver，进入 **Workspace → Research Chat → 建立科研项目**。
2. 展开「我已有研究计划或项目资料」，填写**运行 Sciver 的电脑上**已有项目的绝对目录路径，点击「检查目录并选择资料」。
3. 勾选需要的 `research_plan.md`、笔记或数据，并允许 Sciver 读取选中资料。旧计划可作为新草稿的参考；没有旧计划也可以继续。原项目目录和文件不会被修改。
4. 在对话中回答长期目标、研究边界、完成标准和必要背景。基础引导不需要模型账号；只有主动开启模型辅助完善时，选中计划和笔记的文本摘录才会发送给模型，并可能消耗额度。
5. 检查五节 Research Plan 草稿与导入清单，必要时直接编辑；点击「确认建立项目」后，新任务才会出现在 **Task List**。这一步不会自动开始研究。

### 建项后如何运行

Research Plan 规定长期目标、约束和完成判据。确认建项后，Sciver 初始化任务配置与科学图谱，并把选中的资料复制到新任务的资料区。研究者可在 Task List 查看计划，决定何时启动研究。真正启动前需要配置可用的模型账号；启动操作可能调用模型并消耗额度。

研究按 Cycle 推进：Research Lead 提出候选问题，Challenger 审查资格，Investigator 开展研究并登记证据，Judge 独立裁决，Reporter 整理结果。系统保存各轮记录和科学图谱，研究者可以在 Web 界面查看进展、修改长期计划或提供方向意见。单轮命题的裁决与整个任务是否完成是两件不同的事；任务完成仍须对照 Research Plan 的 `Final Gate` 判断。

## English

Sciver organizes a long-term scientific question into a research task that can continue across cycles. **The only formal input needed to initialize a new task is `research_plan.md`**; Sciver creates the configuration, SQLite science graph, and runtime directories. You may prepare the file yourself or create and edit it through conversation in the Web UI. An existing project does not need to have a Research Plan yet.

### What goes into a Research Plan

Tell Sciver four things; you do not need to know the file format in advance:

| Part | Question to answer |
| --- | --- |
| `Goal` | What long-term scientific question are you trying to answer, and what is the subject? |
| `Research Contract` | What boundaries apply to data, methods, evidence, and scientific definitions? |
| `Final Gate` | Which checkable results or deliverables would establish completion of the whole task? |
| `Required Context` | Which existing knowledge, source locations, and resource limits must carry across cycles? |

The saved file has a fifth section, `Scientific Model`, for current scientific understanding and hypotheses that still need testing. Sciver drafts it during setup. If an imported plan has this section, its content remains provisional rather than becoming an established finding. The saved file has five top-level headings in this order: `Goal → Research Contract → Final Gate → Required Context → Scientific Model`. Review and edit the full preview before confirming creation.

### Import an existing project

1. Install and open Sciver, then go to **Workspace → Research Chat → Create research project**.
2. Expand **I have an existing research plan or project materials**. Enter the absolute directory path **on the computer running Sciver**, then choose **Check directory and select materials**.
3. Select the `research_plan.md`, notes, or data you want to import and allow Sciver to read them. An old plan informs a new draft; you can continue without one. Sciver preserves the original directory and files.
4. Answer the questions about your long-term goal, research boundaries, completion criteria, and necessary context. Basic setup needs no model account. Only if you enable model-assisted drafting are text excerpts from selected plans and notes sent to a model, which may consume quota.
5. Review or edit the five-section draft and import list. Choose **Confirm project creation** to add the task to **Task List**. This does not start research.

### What happens after setup

The Research Plan sets the long-term goal, constraints, and completion criteria. On confirmation, Sciver initializes task configuration and the science graph, and copies selected materials into the new task's data area. You can inspect the plan in Task List and decide when to start research. An available model account must be configured before real research; starting it may send model requests and consume quota.

Research proceeds in cycles: Research Lead proposes a candidate, Challenger checks its qualification, Investigator works and registers evidence, Judge independently assesses it, and Reporter records the result. Sciver preserves cycle records and the science graph. Researchers can review progress, revise the long-term plan, or provide direction in the Web UI. A verdict on one cycle's hypothesis is separate from completion of the whole task, which is checked against the Research Plan's `Final Gate`.
