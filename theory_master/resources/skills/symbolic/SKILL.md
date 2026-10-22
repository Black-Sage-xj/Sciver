---
name: symbolic
description: >-
  当 Investigator 的 theory-investigation 需要正式符号推导时使用：定义、假设、极限、恒等变形。
  推导文件只写 cycle_workspace。不用于普通机制讨论。
---

# symbolic

## 做法

- 先写清符号、定义域、守恒量与已冻结假设。
- 每步标明用了哪条假设；极限分析写清取极限的变量与其余量是否固定。
- Cycle 内可保留 working model 与失败思路，不必写入 Canvas。
- 需要落盘时写 `cycle_workspace/analysis/` 或 `cycle_workspace/result/<name>/`，附最短 README：假设、主公式、未闭合处。

## 禁止

- 用未定义符号跳步。
- 把数值拟合曲线直接当成解析恒等。
- 更换 Main Hypothesis、关闭 Cycle、写 `notes_dir` 或 Canvas。
- 调用 `kill` / `pkill` / `killall`。

若当前 Master 的 task-local whitelist 定义了领域符号、算子或数据量，推导必须遵守该任务给出的定义；未声明时不得挪用其它 Master 的领域定义，也不得私设同名量。
