---
name: analyze-results
description: >-
  Investigator 在当前冻结 Hypothesis/criterion 下需要比较实验或计算结果、做统计汇总、
  识别趋势/异常并形成可审计解释时使用。只分析 Evidence，不负责调度下一阶段或裁决 Cycle。
---

# Analyze Results

用于当前 Investigator assignment 内已经产生或已提供的 numerical / local-source 结果。目标是把原始输出变成可核查的 finding，而不是把趋势包装成结论。

## 方法

- 先确认当前冻结的 scientific question、criterion / decisive test、比较对象、输入 provenance 与主要指标；不要因为结果文件里出现更多字段就扩大研究问题。
- 定位当前 `cycle_workspace` 或 handoff 明确提供的结果 artifact。区分原始数据、派生统计、图表与旧 run；不要把旧 artifact 当作当前 Evidence。
- 建立最小比较表：关键 independent variables、primary/secondary metrics、baseline，以及与 baseline 的绝对/相对差异。只有当相对改进有科学意义且分母稳定时才报告百分比。
- 有重复 run / seeds 时报告样本数、mean、spread（如 std / interval）与 reproducibility；参数扫描时检查 monotonic / non-monotonic / plateau 等真实趋势，并标出 outlier、缺失值、失败 run 与可疑数据。
- 每个重要 finding 按 `Observation -> Interpretation -> Implication` 分层：
  - `Observation` 只写数据直接显示的事实；
  - `Interpretation` 写可能机制，并明确替代解释与不确定性；
  - `Implication` 只说明它对当前 criterion / hypothesis 的证据意义，不替 Judge 下 verdict。
- 不把相关性、有限样本趋势或拟合优度自动升级为因果、机制或一般规律。若统计能力不足，明确写出 scope / limitation。
- 如当前结果不能区分两个合理解释，先判断所需 follow-up 是否仍属于当前 frozen objective、scientifically necessary、当前可执行且预期能增加 discriminating evidence。四者都满足时，它不是“未来建议”，而是当前 Investigator 同一 Investigation Round 内应继续完成的最小分析/计算增量。只有超出当前 objective、依赖新的外部 scholarly evidence、需要新的 Hypothesis/Cycle/Stage 或当前不可执行时，才作为 `possible discriminating follow-up evidence` / remaining gap 返回。

## 结论表达

把原始输出与重要比较、异常、误差和适用限制写进科学陈述或关联研究文件。不要为方法核查或单独的脚本诊断凭空生成独立科学 Finding；交付格式由 SDK Setup 控制。

## 边界

不修改 active Hypothesis、criterion、Cycle scope、Scientific Model、Canvas 或其他 upstream state；不决定下一 Stage、是否结束 Cycle、是否 accept/reject；不自行调用其他 Agent。当前 frozen objective 内必要、可执行且有判别力的 follow-up 属于 Investigator 自己的同一 Round 科研执行，不是 workflow routing；跨 scope、跨 Agent、跨 Stage/Cycle 的工作才交回 TheoryMaster workflow。
