# Research Lead SQLite Reading Rules

1. 先读 `research_plan.md` 的 Goal / Research Contract / Final Gate 与 Scientific Model。
2. 使用 SDK 注入的 `agent_graph` 读取 H 索引、显式 frontier 状态、近期 Cycle Outcome 和 typed lineage。
3. 需要完整 H 或 External 原文时，只按 `dossier_ref` / `detail_ref` 读取 SQLite 派生的稳定引用。
4. `exploration.canvas` 仅为可视化投影，不打开、不解析，也不把它当作科研数据来源。
5. 若 `agent_graph` 缺失，报告上下文错误；不得自行回退读取 Canvas。
