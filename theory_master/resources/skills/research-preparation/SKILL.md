---
name: research-preparation
description: Help form a research question, delegate bounded Literature work, and save a human-reviewed four-section Research Plan before Task creation; numerical pilots and reproductions then run only through the formal Task's SDK Cycle.
---

# 项目筹备

当前是独立筹备空间，没有正式 Task。使用本轮提供的输入、材料和草稿。已有题目按原题、公开附件和评分说明转写，保留允许方法、数据用途、提交物、阈值及由谁评分。对照原文检查每项硬要求及遗漏，核对对照表是建题工作，不写成研究者的交付物。用户明确改题时注明版本与范围；数据集上的自拟问题注明由谁提出。找不到原题或条件有冲突时记录具体缺口，不能用旧计划冒充原题。

从兴趣或观察开始时，帮助用户明确想解决的问题，只问影响当前选择的缺项。需要了解相关研究时按手册调用共享 Literature 服务；文献调研或方法建议须有问题依据，不能自动进入 Final Gate。

## 先计划、立项，再执行科研

在用户要求或接受具体草稿修改后保存 `research_plan.md`。草稿恰好四个 H1，依次为 Goal（问题）、Research Contract（原有条件）、Final Gate（原有完成判据）、Required Context（已确认的资料和约束）。未确定的内容标明待澄清。通过 `tm_observer.research_preparation.snapshot(Path(state_dir), session_id)` 读取草稿和 revision，再调用 `research_preparation.update(Path(state_dir), session_id, {'expected_revision': revision, 'draft': draft})`。并发冲突后重新读取并整合。

用户可在网页「审阅计划并创建项目」，或明确接受具体版本的立项提案。对已审阅版本仅调用 `tm_observer.research_preparation.create_project(Path(state_dir), Path(search_root), session_id, {'confirm': True, 'expected_revision': reviewed_revision})`。审阅后发生修改须展示差异、审阅新版本。服务保存材料快照，初始化空白 Scientific Model 和图谱；以回执中的真实 Task ID 和链接进入任务。讨论、保存草稿、创建和运行分别依用户授权处理。
