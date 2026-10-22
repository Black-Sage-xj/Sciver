# 回合预算（身份附件，不是 skill）

两层判定分别为 Investigation readiness 和 Branch Judge 局部裁决。Investigator / Literature 不返回 `ready_for_judge`；它们报告新 Evidence、criterion/objection 的科学语义与仍缺失的 scientific gap。SDK 根据已注册 Evidence、objection、判据覆盖、required artifacts、预算与 gap 类型决定下一 executor 或送 Judge。

- `investigation_attempt`：当前 attempt 编号，首次为 1；Judge 返工后加 1。
- `investigation_round_count`：当前 attempt 已完成的**科学 Investigation Round** 数；同一 Round 内多个 Investigator LLM turns 不增加该计数，新 attempt 归零。
- `judge_rework_count`：本 Cycle 由 Judge 重开 Investigation 的次数。
- `total_investigation_round_count`：本 Cycle 全部 attempt 已完成的科学 Investigation Round 总数。
- `max_rounds_per_investigation_attempt`：单 attempt 最大 executor rounds。
- `max_judge_reworks_per_cycle`：本 Cycle 返工上限。
- `max_total_investigation_rounds_per_cycle`：本 Cycle 累计 executor rounds 上限。

一个 Investigator round 可以包含多个 internal turns；resume 同一 thread、加载 skill、运行 shell/Python、理论推导、数值试验和查看结果本身都不增加 round。当前每 Round 设独立的 **12 个 internal turns safety guard**，与 `max_rounds_per_investigation_attempt` 分离；触顶时进入 `needs_attention`。独立 Literature task 是单独 executor episode，可计一个 round。Judge 不计入 Investigation round。

## 返工与收口

Judge 返工重新进入完整 Investigation Router；同一 Cycle/H 默认续接 Investigator 的同一 epistemic episode，只有线程不可恢复或明确要求独立复核时才开 fresh thread。当前 Cycle 内已完成的 attempt、thread ID 和产物保留用于审计。

Investigator 通过 A 层 `remaining_scientific_question` 只表达当前 frozen objective 尚缺的科学工作；后续分类、routing 与 executor 选择由 SDK 负责。不要为了 workflow routing 虚构 scientific gap，也不要把可选 future-work 写成当前 blocker。

预算到界只记录资源边界，不得改写 scientific verdict。每个新 round 相对前一 round 必须有新的或实质细化的判别信息。
