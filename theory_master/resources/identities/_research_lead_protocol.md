# Research Lead 阶段索引

本文件是设计/维护索引，不在 thread-init 自动加载。Research Lead 的稳定 Authority 以 `research_lead.md` 为准；具体操作要求由当前 Stage Card 与 contract 渐进披露。

一个 Cycle 内可能依次调用：

- `frontier_proposal`：调查当前前沿并提出候选。
- `selection_repair`：仅在 Challenger `revise` 后补齐 frozen Candidate 的定义/判据；Hypothesis、goal_advance 与 scientific scope 由 SDK 保持不变。
- `candidate_selection`：只从 qualified candidates 中作科学选择。
- `cycle_synthesis`：terminal Judge 后综合科学认识并决定 Scientific Model keep/replace。
- `frontier_maintenance`：Scientific Model commit 之后、Reporter 之前，只给当前 Canvas branch tip 标注 active/parked/closed。不提出 Candidate，不决定下一 Cycle。

Qualification/selection 默认复用 Research Lead thread；`cycle_synthesis` 是独立的新 epistemic episode。上一 Cycle 只留下 terminal Judge 结论、Scientific Model 与 scientific synthesis 等 continuation context；下一 Cycle 的 `frontier_proposal` 自己生成候选，随后通过 Challenger 和 selection 获得 Hypothesis Lock。SDK 负责 Hypothesis Lock、criteria、routing、状态与 projection。
