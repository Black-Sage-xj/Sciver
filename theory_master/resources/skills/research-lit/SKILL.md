---
name: research-lit
description: >-
  Literature 只有在已授权 literature_task 需要跨多篇 scholarly sources 做结构化综合、landscape、
  方法/机制对照或 consensus-disagreement map 时使用。普通 targeted source verification 默认用 tm-scholar，
  单篇全文核验用 pdf-reading，novelty_check 用 novelty-check。本 skill 只产出 multi-paper scholarly synthesis。
---

# Research Literature

用于当前 SDK 已授权、且确实需要**多篇文献综合**的单个 `literature_task`。它不是 Literature 的默认搜索入口：普通定向来源/claim 核验使用 `tm-scholar`，单篇全文定位与原文抽取使用 `pdf-reading`，`task_type: novelty_check` 使用 `novelty-check`。本 skill 专注多源去重、结构化比较和综合；外部 transport/materialization 仍由 TheoryMaster SDK/runner 负责。

## Trigger boundary

只有当至少需要比较/综合多篇来源、建立领域 landscape、方法族/机制族对照、共识/分歧结构或跨文献 gap map 时加载。若一个已知来源或一个明确外部 claim 就能解决当前 `literature_task`，不要加载本 skill；使用基础 scholarly capability 即可。

## 检索与筛选

- 从 `literature_task` 的精确问题开始，生成少量互补 query families：核心术语、同义词/旧称、关键作者/方法、citation-tracing 线索与必要的 foundational query。不要把定向 source gap 扩成无边界 topic survey。
- 使用当前 Literature 已获授权的 scholarly retrieval 能力与 SDK 物化 artifacts；不要在本 skill 内直接实现新的网络 transport、下载器或 ARIS helper。
- 合并不同 scholarly sources 后按 DOI、arXiv ID、规范化题名/作者年份去重；保留来源 provenance，不因重复出现就把 secondary source 当 primary source。
- 为候选来源明确 verification level，例如 metadata-only / abstract-level / full-text-located / unverified。未知 DOI、页码、locator 或全文状态不得猜测。
- 重要 scientific claim 尽量追到原始论文或可定位全文；综述、搜索摘要与二手引用可以做 discovery，但不能伪装成已核原文。

## 逐篇抽取

对真正相关的 paper 记录最小结构：

- title / authors / year / venue / stable identifier；
- research problem；
- method / theoretical object；
- key result or claim；
- relation to current literature_task；
- exact locator / quote when full-text evidence is required and available；
- verification status 与 limitations。

逐篇抽取是 Literature 自己的工作，不得因为论文较多而自行 spawn subagents、创建 workflow tier、jury 或 reviewer。

## 综合

- 按方法/机制/结论分组，而不是按检索顺序堆论文。
- 区分 field consensus、documented disagreement、methodological difference 与尚未解决的 gap。
- 对当前 claim 明确哪些来源 `support / contradict / bound / contextualize / remain unverified`，并保持 evidence strength 与 source verification 一致。
- Novelty/priority 相关任务可以报告 nearest prior work、overlap、remaining delta 与 uncertainty，但不得因此决定 Hypothesis 是否继续、Cycle 是否关闭或 Judge verdict。
- 没有可靠来源时明确返回空 evidence 或 unresolved source gap；“没搜到”不等于“证明不存在”。

## Evidence 与产物

使用 TheoryMaster 现有 LiteratureEvidence / literature handoff contract。可复现 search log、retrieval metadata、citation trace、阅读摘录只写 `cycle_workspace/literature/`。不要维护独立 wiki/manifest/composed-mode，也不要写 Canvas、Scientific Model 或项目长期笔记。

## 边界

不承担普通本地 retrieval；不自行打开新的 Literature task；不递归请求 Literature；不 spawn 其他 Agent；不调用隐藏 reviewer/jury；不决定 workflow routing、rework、Stage transition、Cycle closure 或 accept/reject。所有外部 retrieval transport、Agent 调度和 lifecycle 权力属于 TheoryMaster SDK。
