---
name: experiment-plan
description: >-
  Investigator 需要把当前冻结 scientific claim/criterion 转成可执行的实验或数值验证设计时使用。
  负责当前 investigation 内的 claim-to-evidence 规划，不负责论文叙事、Cycle stop/go 或跨 Agent 调度。
---

# Experiment Plan

用于当前冻结 Hypothesis 和 Investigator assignment 内设计最小、可判别、可复现的实验/计算方案。计划服务于 Evidence，不拥有 TheoryMaster lifecycle 权力。

## 方法

- 从当前 Investigator assignment 的冻结命题、交付与约束出发，选择能回答原问题的最小检验。设计方法的题目按授权交付方案。
- 明确决定性输入、判断依据和结果可解释的范围。对照、重复、误差分析或成本估计只在题目要求或当前推断确实需要时采用。
- 多步实验按实际依赖安排执行顺序。资源不足时，说明受影响的检验及可行范围。
- diagnostic/evidence criterion 描述实际结果能说明什么。通过当前 Stage 已有的交付接口报告；需要保留的计算方案可写入当前 `cycle_workspace`。

## 边界

本 skill 只能规划当前 Investigator assignment 内“怎么检验”。它不能替换 active Hypothesis、改变 criterion/Cycle scope、选择下一 TheoryMaster Stage、启动新 round、调用其他 Agent、关闭 Cycle 或下 accept/reject verdict。所有 routing 与 lifecycle 决策属于 TheoryMaster SDK。
