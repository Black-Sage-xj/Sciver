---
name: numerical-investigation
description: >-
  Investigator 需要可复现计算、数值对照、统计/数据检查或 task-local source review 时加载。
  负责数值方法与复现产物，不负责 workflow routing 或 Judge 裁决。
---

# Numerical Investigation

用于当前冻结 Hypothesis 内的可复现执行与本地证据核验。

## 方法

- **先做决定性输入/provenance gate，后做任何下游分析。** 在读取大文件、计数、参数扫描、构建派生链或迭代规则前，写下冻结 decisive test 所不可替代的最小输入，以及每项所需的字段、身份和 provenance（例如用于确认对象身份的原始字段）。
- 按以下顺序执行，不以可做但不能裁决的 downstream 工作替代此 gate：
  1. **存在性**：在任务已提供的输入、metadata、source 和已有 artifact 中确认决定性字段/记录实际存在，且能对应到本次 decisive test。
  2. **可恢复性**：若不存在，立即做一个有界的 source-recoverability audit：定位权威 builder/source，确认它是否保留或能以已授权本地工具确定性重建该字段，并记录所需输入、版本和限制。
  3. **分支**：仅当字段已存在或可认证地恢复时，才批量计算或继续下游 counting / statistics / iterative-rule work；若不能恢复，停止与该 decisive test 无关的下游工作，将结果精确报告为 `external_artifact` / 缺失输入 gap，而不是以 proxy 或 negative downstream evidence 裁决 Hypothesis。
- 已在当前 investigation 的前序 turn/cycle 认证的决定性缺口，除非输入资产、source revision、可用 builder 或 decisive test 已变化，不得重新全面审计旧资产。复用已记录的证据和范围，只检查上述变化或直接 smoke 新的可恢复路径。
- 先确认其余输入、参数、数据来源和验收条件。

## 既有科研资产复用与证据隔离

- **先查再算，复用优先。** 在生成高成本数据或重复计算前，先有界检索本任务获准访问的现有输入、历史 Cycle、benchmark、metadata、缓存和产物；缺少统一资产登记不构成重新生成的理由。
- **复用必须核对科学身份。** 依据研究对象、独立统计单位、采样/制备方式、参数、版本、边界条件、收敛与来源记录，判断是否符合当前冻结 Hypothesis 和 decisive test；不同协议或未经核验的证据不可直接混用。具体领域的物理口径由当前任务的 task-local Skill 定义。
- **探索先做有界小样本。** 公式/机制诊断、smoke 和误差检查可以从已知来源的代表性独立样本中固定规则抽取，只补算缺失量；不得按已看到的结果挑选样本，不能将同一原始对象的多次观测或派生量伪装成独立样本。
- **开发、验证与最终评价分离。** 在接触评价标签前，按真实独立统计单位预先固定数据用途、去重规则与 Train/Validation/Test 划分（适用时保留独立最终测试集），并冻结预测和参数后才评分。已暴露标签仅能作为如实标注的回顾性证据，重新抽样、换路径或新增 SDK 登记均不能把它升级为独立盲测。
- **新增数据必须提供增量科学收益。** 先衡量现有资产、解析推导或小规模鉴别试验是否足够；只有关键输入不可替代、当前 Research Contract 允许且资源审批满足时，才提出最小新增方案。不得通过事后扩样改变冻结判据或掩盖证据泄漏。
- **记录选择，不扩大交付合同。** 在现有 Cycle README/分析记录中简要记下查验范围、选用资产的身份与来源、随机种子/划分规则、哪些量复用或补算及适用限制；不为此创造新 Agent 角色、结构化字段或 SDK 科研资格自动判定。
- 对同一 artifact、dataset 或 task-local source 的多个确定性 schema、字段、计数或 invariant probe，先列出本次需要确认的事实，再合并为**一次**可审计的 batch probe（脚本或结构化命令）。该 probe 应一次输出紧凑 JSON（包含各检查的值、pass/fail 与必要定位信息），随后基于这一份结果作科学判断。禁止把这类已知的独立 bit/字段查询拆成连续 shell `exit code` 或单字段 `exec` 调用；只有前一批结果真正改变下一个问题时，才开始新的批次。
- 优先将 batch probe 脚本与其紧凑 JSON 结果保存在独立计算目录；一次性检查不降低科学核验标准，只把确定性 execution orchestration 从对话往返移入可复现脚本。
- 围绕冻结的 decisive test 迭代脚本、运行与结果核验，直到形成可复现、可审计的 numerical 或 local-source Evidence；研究完成条件由真实 Native Goal 决定，round/continuation 生命周期由 SDK workflow 决定。
- **机制判别先于无目标的精度竞赛。** 先明确可检验的因果或数学机制，再选择能区分相竞争解释的最小计算；某项不确定性限制对应定量结论，不自动否定较弱且可独立检验的机制命题。严格精度门槛应服务于冻结的最终判据。
- **高成本方法先判科学收益，及时淘汰无效路径。** 对昂贵的总体统计、数值大批量、参数扫描或严格误差证书，先用现有数据/理论作数量级估计和代表性小规模试验，比较预期科学判别力、所需样本/时间/内存/存储与可用资源。已证明的最坏情况上界所要求的样本数，不等于科学对象的固有必要样本数。若所选方法明显不经济，停止该**方法**，先在冻结边界内有限度探索更紧的物理界、合适的统计假设或更高效的有效对照；不要为排除所有可能方法陷入无限理论搜索。显著资源缺口必须量化并记录；小试验可作方法诊断，但不能伪装成完成预定总体检验。若合理路径暂时均受阻，记录已试方法、确切缺口和可复现实证，继续寻找合法的冻结范围内替代路径；不得主动让 Native Goal `blocked`、申请科学停机或以局部 Finding / `inconclusive` 冒充未做的决定性检验。是否继续投入只在实际达到 SDK Investigator Token 预算时由独立 Budget Reviewer 审查；Provider 额度和操作中断由 SDK 处理。
- “文件尚不存在”本身不是 blocker。只有当生成该 derived data 对冻结检验是必要的、可由已授权的本地工具可复现地产生、且成本与资源占用和当前 investigation 相称时，才在 `cycle_workspace` 中生成；否则精确报告缺失输入或资源边界。
- `local_source_review` 只检查任务已提供的本地文件、数据、metadata、benchmark/source code 与 provenance；它不是 Literature。
- 每个独立计算目录写 README，说明目的、冻结输入/参数、执行入口、输出、验收与复现方式。
- 在把计算扩大到批量执行前，先估计独立计算单元数与串行墙时；若独立计算单元达到 10 个，或根据代表性 smoke 估计串行可能超过 10 分钟，必须在扩大计算规模前先加载 `compute-dispatch` 并完成一次执行成本评估，不要等批量计算已经开始后再判断。**若工作量从小样本扩大为更多独立数据单元或参数扫描，必须重新评估剩余工作负载和 CPU/memory/disk；可有效并行时默认使用受限并行或复用 `compute-dispatch/scripts/parallel_units.py`，不能沿用已失效的「小批量串行」理由。**若串行更快或共享状态/盲测边界不允许拆分，需说明量化理由，且不得强制并行。正式作图加载 `plotting`。**当前任务的专用领域 Skill 仅按 developer whitelist 中 Task-local Master skills 加载**；涉及本任务特定的数据身份、物理口径或输入复用时，即使只读取缓存且无需专用工具也须按需加载。未列出的任务不得跨 Master 借用。

## 默认 Python

使用 workflow/runtime 提供的科学 Python 环境；当环境 provenance 会影响复现时记录 `sys.executable`，不要在 skill 中假定固定宿主机解释器路径。

## 结论表达

记录可复核的 numerical / local-source 科学方法、结果和来源资格；正式交付遵守 SDK Setup 的最简 Finding 接口，不新增证据分类字段。

## 独立正确性检查

- 可复现性、同一实现的重复运行、以及多个派生输出彼此一致，只能证明执行稳定或内部自洽，不能单独证明实现正确。
- 对会支撑 substantive numerical finding 的实现，在成本合理时，把结果视为已验证并提交 Evidence 前，必须主动寻找一个**不依赖主要结果本身**、且实现写错时有实际机会失败的独立正确性检查。优先顺序是：任务/数据源明确给出的 canary、reference 或 known answer；exact small case 或解析结果；极限情形或退化到可信的简单方法；不变量、守恒律或对称性；最后才是真正独立的复算或第二实现。
- 选择当前适用且成本最低、判别力足够的检查并实际执行。不要为了流程形式制造无信息的验证；若没有合理的独立检查，必须在科学结论正文中说明这一验证边界，不能把 reproducibility 或 cross-file consistency 写成 correctness。
- 检查可以落在 assertion、单元测试、已有结果文件或可审计 stdout 中；不要求额外生成固定名称的 validation.json，也不新增 validation artifact role。

## 测试集身份与 provenance

- holdout、gate、benchmark-provided / benchmark test 属于需要独立认证的**受保护证据身份**。自然语言或 finding.provenance 的 source/split/declaration_ref 均只属于 Agent 声明；路径位于 workspace 外或登记时文件哈希匹配不证明当前数值结果产生前已冻结。没有 SDK 独立可信的事前冻结记录时，必须保持 `unverified`，不得据此宣称已认证的 protected identity。
- agent_generated 数据不能事后升级成 holdout 或 gate；rework 中临时生成的参数扫描应如实表述为 agent-generated/post-hoc validation。

## 边界

不改 Main Hypothesis、理论定义、decisive test、Scientific Model、Canvas 或其它无写入授权的研究文档/个人笔记库；不把旧 audit 冒充当前 rework。
