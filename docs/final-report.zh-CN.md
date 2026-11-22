# 完成后的科研总报告

总报告是任务完成后的独立处理环节。它只读原题、SQLite 图谱、逐轮 Outcome、Evidence 及完成凭据，以原题的要求组织关键结果与研究链路。

真正提交 passed Goal seal 后，Sciver 排队并启动独立消费者。作者使用任务的 Reporter 模型配置；报告审核员使用任务的 Judge 模型配置，均开启全新会话。报告审核检查文档与来源的一致性，不产生新的科学裁决。

报告生成、独立审核、冻结来源和阅读组件内置于 Sciver，安装与运行均无需 `graph-research`。任务的明确目录绑定、模型与账号路由，以及独立报告作业状态由 Sciver 管理。现有科研图谱结构、Cycle、科学裁决与封存合同继续由原模块管理。

## 在 Sciver 阅读

只有任务已 complete 才提供总报告。未完成任务在 Task Console、Task List 和图谱工具栏均不显示报告文件入口；单轮 Cycle 结束或任务停止不视为任务完成。完成后正在排队、生成中或失败时仅显示状态文字，报告可读后才显示文件图标与链接。Task Console 顶部中央和 Task List 的「最终总报告」列共用入口，点击后直接打开原文档阅读区；Console 自动刷新时同步更新入口。

总报告正文按 Cycle 标注来源，同段同轮引用合并，底部「研究轮次」每个 Cycle 只列一项；点击可阅读该轮冻结报告。原来的文件别名清单、逐文件索引和调试元数据不出现在默认阅读界面，执行与复现章节默认折叠。完整原稿、文件与细粒度证据映射保存在后台，独立审核仍使用完整材料。版本选择、章节目录、手机阅读和待修订标识继续可用。

总报告与右侧普通文档、Cycle 报告共用 `SciverMarkdown`，使用 Marked、DOMPurify、KaTeX 和 Prism，离线报告 HTML 也使用这一内核。图片支持 `![图注](路径)` 和 `![[路径|宽度]]`，可显示 PNG、JPEG、GIF、WebP、SVG，单张上限 20 MiB。图片与来源读取受当前任务的冻结清单及 hash 校验约束。

「研究主线概览」按关键阶段单列展示问题、检验、发现和转向。一次阶段可合并多轮工作，影响最终回答的失败作为转折点保留；箭头表示叙事推进。新报告使用独立的 `research_story` 合同，由作者选择阶段并由审核员核对来源、关键反证和结论范围。旧报告从已有的主线、转向及其主前序生成只读展示，新图使用 `research-story.svg`，保留原 `trajectory.svg` 与已审核原稿。

默认版本目录中的完成作业会自动出现在入口。存放在其他目录的已有报告，可由可信本地调用 `theory_master.final_report_publication.publish_report(research_root, source_dir)` 登记；导入会核对任务绑定和原稿 hash，复制已登记产物并保留原报告，不公开模型调用日志。

## 历史任务

已完成任务的 Task Console「最终总报告」卡片提供「生成总报告」按钮，可手动启动尚未生成或排队中的报告。生成期间按钮禁用，失败后可点击「重新生成」；完成后自动切换为阅读入口。此操作沿用任务的 Reporter/Judge 配置，不启动科研 Cycle。

对已完成任务排队，不调用模型：

```sh
sciver-final-report --task /path/to/registered-task
```

执行一轮作者和一轮独立审核：

```sh
sciver-final-report --task /path/to/registered-task --generate
```

根据反馈修改最终汇总提示词后生成新版本：

```sh
sciver-final-report --task /path/to/registered-task --generate --regenerate
```

这些调用只处理报告，不启动或恢复科研 Cycle。普通完成状态查询仅补齐排队记录。报告失败不改变已经提交的完成状态；离线 FakeBackend 不自动派发真实模型。

## 保存与检查

作业保存在任务 `research_root/.theory-master/final-reports/`，按完成凭据与尝试版本隔离。HTML、Markdown、完整冻结源、检索覆盖、结构化报告、审核意见和调用记录分别保存。新版本不会覆盖人工正文或旧报告。

报告状态包括 `queued/running/ready/needs_revision/failed`。`needs_revision` 保留可读草稿和具体审核意见；`ready` 要求机械校验和各项独立评审均通过。新版评审增加可读性维度，核对初读者能否从报告中理解问题、答案、决定性证据与适用范围。报告完成与科研任务完成分别显示和判断。

作者会将关键主张映射到真实命题、Outcome 和来源，并保留适用范围、负结果和重要转向。大规模原始数组和逐行 CSV 保存在完整冻结源中；模型取得可见性明确的检索提要和索引，不能把索引材料冒充已核验正文。

反馈应修订最终汇总层的通用提示词，再使用同一份冻结材料生成并独立复审。具体题目答案不写入通用提示词，报告审核也不改写原科学 verdict。
