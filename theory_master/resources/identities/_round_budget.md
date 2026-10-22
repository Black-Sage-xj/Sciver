# 回合预算（身份附件，不是 skill）

现行 Investigation 在一次 Investigator invocation 内完成必要的科学工作；提交 Finding 后由 SDK 绑定 Evidence 并交 Judge 独立裁决。Investigator 不输出 `remaining_scientific_question`、`remaining_gap` 或 `gap_progress`，也不选择下一 executor；科学上的未解决条件在 `findings[].limitations` 中注明。

- `investigation_attempt`：当前 attempt 编号，首次为 1；只有 Judge 的有界 objection 可以授权后续 attempt。
- `investigation_round_count` 与 `total_investigation_round_count`：仅作为历史执行观察计数，不再限制 Investigator 的资源额度。
- `judge_rework_count`：本 Cycle 经 Judge 授权的科学返工数。
- Investigator 采用 **3+3 双层累计 Token tranche**：`THEORY_MASTER_INVESTIGATOR_BUDGET_TOKENS`（默认 50,000k）为一份，每个新 Cycle 初始获得第 1 份；第 1、2 份耗尽时由 SDK 内独立只读 Budget Reviewer（GPT-6 Luna / medium）分别决定是否追加第 2、3 份；第 3 份耗尽时由 Supervisor 一次性判断是否开启后 3 份，获准后由 SDK 逐份授予第 4、5、6 份。第 6 份耗尽不再续批，进入安全 Preserve。任何审核拒绝只有在完成安全核验后才能进入自动 Preserve，失败保留检查点和分支，且必须留持久通知。模型审批不使用 tmux/外部 Agent，不重启本 Cycle。旧 Cycle 保留旧账本合同，历史 Round 不限制资源。
- `max_judge_reworks_per_cycle`：Judge 科学返工授权（默认三次），独立于 Investigator Token 投资审查。

Codex 内部可以多次进行模型与工具操作，既没有 TM 额外 Scientific Turn，也没有 12 次 internal Turn guard。Token 护栏仅在开始下一次 Investigator 付费调用前检查，不能中断已运行的单次 Codex 调用；Budget Reviewer 无科学裁决权限。

## 返工与收口

Judge objection 才能进入新的 Investigation attempt；同一 Cycle/H 保持 Hypothesis 与 decisive_test 冻结，续批时从原 checkpoint/thread 恢复，不重置已耗 Token。额度拒绝时先保留 NEEDS_ATTENTION 和已冻结证据，只有新 3+3 Cycle 的 SDK 明确标记、授权与完整 Preserve 核验后才能自动结束并释放前沿；历史 Cycle 仍按原人工操作路径。已完成且可裁决的 Investigator Findings 正常进入 Judge，绝不由预算结束伪造科学判定。
