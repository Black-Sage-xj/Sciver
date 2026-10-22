---
name: proof-writer
description: >-
  当 Investigator 的 theory-investigation 遇到 theorem、lemma、proposition、corollary
  或严格 proof obligation 时使用。用于判断原命题是否真的可证、补全证明或精确暴露 blocker；
  不负责修改 TheoryMaster hypothesis 或裁决 Cycle。
---

# Proof Writer

用于冻结 Hypothesis 内的严格数学证明。目标是 mathematically honest proof，而不是把不可证明的命题改写后伪装成成功。

## 方法

- 先原样冻结 `original claim`：结论、quantifiers、显式 assumptions、notation、definitions 与 scope。内部等价 normalization 可以使用，但不得静默替换原命题。
- 在正式写 proof 前先做 feasibility triage：检查结论是否真由 assumptions 推出、所用 theorem/inequality 的适用条件、量词顺序、边界/退化情形，以及是否存在直接 counterexample 或只支持 weaker claim。
- 为非平凡证明建立 dependency map：main claim、必要 lemmas、已知 results/inequalities、每一步依赖的 assumptions 与需要单独处理的 boundary cases。依赖不得循环；未证明 lemma 不得作为已知事实使用。
- 选择与问题匹配的 proof strategy（如 direct、contradiction、induction、construction、reduction 等），但不要为了套模板强行改变问题结构。
- 每个 nontrivial implication 都必须给出真实理由；不得用 “clearly / obviously / standard argument / similarly” 隐藏关键缺口。检查 inequality direction、quantifier order、符号定义和标准结论的前提是否满足。
- 完成后反向核对：实际证明出的 statement 是否与 original claim 完全一致；是否偷偷加入新 assumption、缩小 domain、排除 edge case 或依赖未证 lemma。
- 若 original claim 不能按当前 assumptions 证明，不得 fabricate proof，也不得把 weaker/corrected claim 的证明记成 original claim 的成功。精确指出 blocker、missing assumption / lemma / implication，必要时给 counterexample direction。
- 可以提出 corrected / weakened claim 或 minimal extra assumption，并可在有价值时证明该 revised claim；但它只是 advisory finding。即使 revised claim 被证明，original claim 仍必须明确记为未建立，除非 original claim 本身已有独立完整证明。
- corrected / weakened claim 不得覆盖 active TheoryMaster Hypothesis，不得改变 Cycle scope、criterion、Scientific Model、Canvas 或其他 upstream scientific state，也不得决定 TheoryMaster 是否采用该修正版。

## 结论表达

应区分原命题及其已证明/未证明状态、严谨证明或具体 blocker、修正命题和必要限制。证据类型与机器元数据由 SDK 决定；研究说明不另定义正式 Finding 字段。

## 边界

本 skill 是 Investigator 的按需证明能力，不是独立 workflow role，也不是 Judge。它不能通过“换一个更容易证明的命题”关闭原 proof obligation；不能修改 Hypothesis/Cycle；不能自行发起外部 scholarly 检索。若证明还需要普通解析推导，继续使用 `theory-investigation` / `symbolic`；若需要可执行反例或数值检查，可在同一 Investigator round 内使用 `numerical-investigation`。
