---
name: research-preparation
description: Draft, review or migrate a plan and create the approved Task.
---

# 项目筹备

筹备工作区尚无正式 Task，在此拟定新项目计划。使用本轮提供的输入、材料和草稿。已有题目按原题、公开附件和评分说明转写，保留允许方法、数据用途、提交物、阈值及由谁评分。对照原文检查每项硬要求及遗漏，核对对照表是建题工作，不写成研究者的交付物。用户明确改题时注明版本与范围；数据集上的自拟问题注明由谁提出。找不到原题或条件有冲突时记录具体缺口，不能用旧计划冒充原题。

从兴趣或观察开始时，帮助用户明确想解决的问题，只问影响当前选择的缺项。需要文献时使用 research-literature；文献调研或方法建议须有问题依据，不能自动进入 Final Gate。

## 先计划、立项，再执行科研

小规模试跑、复现等产生新科研证据的工作须在正式 Task 的已授权 SDK Cycle 内进行。若先前已建议在筹备空间直接试跑，向用户纠正该建议，从尚缺的计划、审阅或立项步骤继续。

在用户要求或接受具体草稿修改后保存 `research_plan.md`。草稿恰好四个 H1，依次为 Goal（问题）、Research Contract（原有条件）、Final Gate（原有完成判据）、Required Context（已确认的资料和约束）。未确定的内容标明待澄清。通过 `tm_observer.research_preparation.snapshot(Path(state_dir), session_id)` 读取草稿和 revision，再调用 `research_preparation.update(Path(state_dir), session_id, {'expected_revision': revision, 'draft': draft})`。并发冲突后重新读取并整合。

用户可在网页「审阅计划并创建项目」，或明确接受具体版本的立项提案。对已审阅版本仅调用 `tm_observer.research_preparation.create_project(Path(state_dir), Path(search_root), session_id, {'confirm': True, 'expected_revision': reviewed_revision})`。审阅后发生修改须展示差异、审阅新版本。服务保存材料快照，初始化空白 Scientific Model 和图谱；以回执中的真实 Task ID 和链接进入任务。讨论、保存草稿、创建和运行分别依用户授权处理。

迁移已有工作目录时，Web 提供含 Scientific Model 的五节计划预览，经用户确认后立项。已返回 `created_task` 的会话按该绑定读取正式任务的当前记录，冻结的筹备快照保留为来源。
