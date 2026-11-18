---
name: research-progress
description: Explain current research paths, evidence, failures, and blockers, then propose an actionable plan for the operator to accept or revise.
---

# 研究进展与科学性

先读 Research Plan 和 canonical SQLite graph，再结合各路径的 Hypothesis 详情、Cycle 报告、Judge 原因与最新 Worker 身份。以图谱中的研究问题和演化关系组织说明，不要把最近几个 Cycle 的日志当作全项目进度。

1. 概括当前 Goal、主要研究路径及路径数量。区分真正不同的问题/方法与同一路径的多次 Cycle；注明 active/parked/closed。紧凑图只显示局部时继续读取，不能从截断视图声称精确全局数量。
2. 逐条说明每条主要路径研究了什么、目前推进到哪里、有哪些可靠结果、卡在哪里。带上相关 H ID、Cycle 与来源，解释历史分叉和当前路线的关系。
3. 解释已经失败的尝试及失败原因。Judge 的 rejected 是对指定命题/范围的科学裁决；FAILED、Provider 错误、INTERRUPTED 是执行问题。只有来源明确写明原因时才当作事实；否则标注推断及待核实证据。不能把未完成工作当成失败结论，也不能把旧 Worker 当作正在运行。
4. 结合判据、证据来源、独立样本、校准/验证区分、误差边界与竞争解释，回答当前结论有多可靠；只展开与用户问题有关的内容。
5. 有具体可执行方案时，在末尾按 1、2、3 编号列出实际变更：哪段 Research Plan 要怎么改；下一轮精确猜想与判别检验；Scientific Model 是否需要有证据的更正；哪些路径保留/暂停/关闭；是否暂停或启动运行。只列适用项，说明各项原因、受影响 H/当前工作及生效时点，再询问是否执行或如何修改。若用户仅问一个进展事实且尚无行动方案，直接回答，不机械添加执行确认。

本轮默认只读，建议本身不授权写入。用户之后接受具体编号方案、选择其中几项、修改并接受，或直接要求调整路径时，切换并读取 `research-intervention/SKILL.md`，执行已授权范围，无需再问同一个确认；同意改计划不自动授权启动。下一轮猜想默认只交给下一次全新提案一次，不改写已冻结 Cycle；新猜想不能直接写成 Scientific Model 的已知结果。执行后按接受的项目逐项说明已改、待生效、已运行、失败或部分完成及记录链接。不要因为会话最初选了进展卡而一直只做总结。对话中的方案是上下文，计划和图谱仍为系统权威。
