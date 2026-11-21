---
name: research-evidence
description: Verify graph records and cited sources.
---

# 核对结论与证据

从当前任务绑定获得配置的图数据库，按问题读取相关记录。使用 `python -m theory_master.science_graph_tools --database <configured-db>` 的 `view`、`hypothesis-detail`、`cycle-detail`、`report-read` 或 `search`。摘要不足时读取原始报告、数据或产物；图谱不可用时说明缺少的证据。

定位命题、H ID、对应 Cycle、原判据和 Judge 理由，解释证据支持的范围。引用选段时使用服务核验的任务、对象 ID、section、source_version、quote 和 href。基于选段提出建议或更正时保留这些出处，版本已变则重新读取当前记录。
