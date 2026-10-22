---
name: experiment-plan
description: >-
  Investigator 需要把当前冻结 scientific claim/criterion 转成可执行的实验或数值验证设计时使用。
  负责当前 investigation 内的 claim-to-evidence 规划，不负责论文叙事、Cycle stop/go 或跨 Agent 调度。
---

# Experiment Plan

用于当前冻结 Hypothesis 和 Investigator assignment 内设计最小、可判别、可复现的实验/计算方案。计划服务于 Evidence，不拥有 TheoryMaster lifecycle 权力。

## 方法

- 先冻结当前 hypothesis、criterion / decisive test、已知 constraints、数据/计算资源与必须保持不变的 scientific assumptions；不得自行改写研究目标来让实验更容易。
- 把目标拆成少量 `claim -> required evidence` obligations。每个实验块都必须对应当前 obligation；与当前 criterion 无关的“顺便做一下”默认删除。
- 为每个必要实验块明确：
  - tested claim / question；
  - control、baseline、comparison 或 ablation；
  - 输入、dataset/split/parameter regime 与固定条件；
  - primary metric 和必要的 secondary diagnostics；
  - stochastic variance 需要时的 repeats/seeds；
  - `evidence criterion`：什么观察会支持、削弱或无法区分当前 claim；
  - negative / null result 的科学解释边界；
  - 主要 confounder、failure mode 与最小 mitigation。
- 优先设计能区分竞争解释的对照，而不是堆 benchmark。若简单 baseline 已足以检验 claim，不因为“更完整”而增加复杂组件。
- 对当前 assignment 内需要多步执行的实验，给出局部 run order：先 sanity/provenance，再 baseline/control，再 decisive test，最后才是必要 robustness。该顺序只是当前 Investigator 的执行计划，不是 TheoryMaster Stage 顺序。
- 对成本显著的方案给出粗粒度 compute/data/time/resource 约束；若资源不足，报告 blocker 或可缩减但不改变科学问题的方案。
- 原上游 skill 中的 stop/go 决策在此一律改为 `diagnostic/evidence criterion`：它描述结果如何解释，不授权结束 Cycle、进入下一 Stage 或放弃 Hypothesis。
- 可以把计划写成当前 `cycle_workspace` 内的可复现 artifact，但不得写 notes_dir、Canvas、Scientific Model 或其他角色 workspace。

## 输出

保持紧凑，优先给：

1. current claim / criterion；
2. minimal experiment blocks；
3. controls / baselines / ablations；
4. metrics 与 evidence criteria；
5. local run order；
6. compute/data constraints；
7. failure interpretations 与 unresolved risks。

不要生成 paper-story 的 Main/Appendix/Cut 决策，也不要触发 run-experiment、review loop、research-refine pipeline 或任何其他 Agent。

## 边界

本 skill 只能规划当前 Investigator assignment 内“怎么检验”。它不能替换 active Hypothesis、改变 criterion/Cycle scope、选择下一 TheoryMaster Stage、启动新 round、调用其他 Agent、关闭 Cycle 或下 accept/reject verdict。所有 routing 与 lifecycle 决策属于 TheoryMaster SDK。
