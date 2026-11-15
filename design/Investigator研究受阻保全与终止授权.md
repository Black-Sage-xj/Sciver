# Investigator blocked → Supervisor 决策 → 科研成果保全（常规生命周期）

日期：2026-10-13。状态：**SDK 与保全 Stage 已实现；Supervisor 自动分流已通过静态测试，尚待下一真实原生 blocked 事件回归**。

## 原则

1. Investigator 可以停止单个无判别价值的方法，但不得单方面放弃冻结的 Hypothesis 或 decisive_test。
2. **2026-10-14 起**，Investigator 不得主动发送 Native Goal `blocked`/stall_request。新 native `blocked` 不是合法科研停机申请：无 Provider 故障时 SDK 有界重入同一冻结 Goal（最多两次），连续重现视为运行故障，不能由此触发 Scientist 停滞审查或自动保全；只有真实 Investigator Token 预算边界才能触发 Budget Reviewer 的投入价值审查。下文描述的旧科学阻断审查/保全流程只适用于既有 `investigator_native_goal_blocked` 历史检查点，存量语义不追改。
3. 已做科研必须保全，无须 Judge 审核未完成的整个 decisive_test，但不得冒充经过 Judge 裁决的 Finding。
4. 使用现有 Canvas、笔记和不可变运行证据，不新增隐藏的前沿状态。

## 推荐流程

Investigator · investigate
→ Native blocked（停滞申请，不是放弃许可）
→ **Supervisor OFF**：原样 NEEDS_ATTENTION，等待人工操作；不调用内部 Stop Reviewer
→ **Supervisor ON**：现有 Supervisor Attention Profile 直接审查
  - continue：有明确可行、未重复、符合冻结 H/scope/test 的判别路径；SDK 限定本 Cycle 一次自动继续，仍然受阻则不循环；
  - preserve：已有方法无法负责地支撑继续投入；由 Supervisor 授权触发 Investigator · preserve_research；
  - hold：来源含混、需要改变冻结科学内容、涉及大计算/安全/配额或无法批准时，保持 NEEDS_ATTENTION。
→ preserve_research 将已有观察、失败边界、未完成 decisive_test、原始证据归档；SDK 核验后幂等保存 SQLite 保全记录并投影 Markdown + Canvas
→ H 前沿设 parked；保全记录为 unadjudicated，scientific_verdict 为空，不生成 Judge open 裁决
→ 该 Cycle 记 OVER（未裁决保全退出，非 COMPLETE），Research Lead 在后续 Cycle 自由选下一个 Goal-aligned 前沿。

## preserve_research 输出

**A · Scientific（Investigator，同一 Stage）**：先从已有原始成果中整理 provisional observations、适用条件、复现证据路径、已试方法的有限失败边界、remaining_gap、未执行的 decisive_test 部分。不追加新数值实验，不伪造 Findings/Judge 结论。

**B · Presentation（仍由该 Investigator 在同一个回答中完成，不单独调用模型）**：仅将 A 压缩为 `canvas_title`（≤48 字符）与 `canvas_summary`（≤160 字符），准确表达适用范围及“尚未裁决”；不产生 A 以外的新科学事实。

**C · SDK（纯确定性，不调用模型）**：检查本 Cycle 文件及 SHA-256、验证 A/B 合同和未裁决属性、生成完整 MD、维护 SQLite 中唯一的未裁决保全记录及 Canvas 投影、令原 Hypothesis `frontier_status=parked`、写入正式 `OVER` 收束及恢复备份。重复投递若已有完整保全结果则复用，不能再重复花费模型 Turn。

**不设置独立 Stop Reviewer，也不从 preserve_research 额外调用 Reporter、Judge、Literature 或 Research Lead。** 现有 Supervisor 负责 `continue/preserve/hold` 的停滞运行决策，不负责科学裁决。保全 Stage 的完整 MD 就是未裁决科研留存报告；未来若需要跨 Cycle 综合报告，可独立请求 Reporter，但不属于本条自动停止路线。下一 Cycle 的 Research Lead 在新运行中读取 Canvas/MD，自由规划，而非由保全 Stage 越权推荐下一候选。

## 额度与循环

不能以“Token 还有剩余”强迫没有判别收益的探索无限循环。额度接近用尽时可走现有 Budget Reviewer/Supervisor；明确的重复空转也要触发科研投入价值审查。Quota/Provider 错误仍严格沿原有恢复分支处理；不得转成科学 blocked。

## 确定性回归标准

使用临时目录手写一个尚未完成的字符串规范化命题、被冻结的检验、两个有限输入示例和匹配的 Native Goal blocked 检查点；这些数据只用于测试 SDK 约束，不来自真实科研轨迹。

- 未获明确批准时拒绝保全；Supervisor 批准必须同时匹配 Cycle、冻结 H 和已返回的会话凭据。
- 只接受本 Cycle 内的真实普通文件，校验来源 SHA-256；路径穿越、符号链接、缺失文件及保存后篡改均拒绝。
- dry-run 不写文件；正式保全幂等，不修改冻结命题和科学裁决，不生成 Judge 或 Reporter 输出。
- 同一份已保存的 Stage Output 只归档一次，重复执行复用原始响应，不新增模型调用。
- 正式关闭前备份两个原始状态文件；关闭后标记 OVER、不可自动恢复，不创建下一 Cycle，不伪造整个检验已完成。

这些检查验证状态机与来源保护，不证明模型的科研判断质量。

## 保全与正式收束入口

`investigator/preserve_research` 提供结构化科学观察合同和最小输入投影，SDK `research_preservation.py` 负责确定性验证与持久化。Runner 的显式 `--operation preserve --approve-preservation` 会在没有可复用响应时发起一次新的真实 Investigator 模型调用；普通 `--operation resume` 不等同于批准保全。

入口读取允许保全的原始检查点，原科研线程不重启。归档需要操作者明确批准，或同一 Cycle 的 Supervisor Attention 保全凭据 `preservation/supervisor_authorization.json`。原生 blocked 不自行授权退出。SDK 在 SQLite 中保存未裁决保全记录，将 H 的活跃前沿改为 parked，并生成 Markdown 报告与只读 Canvas 投影，不制造 Judge Decision。

显式 preserve 在 Stage 成功后可以完成正式关闭；若 Stage Output 已完整保存而状态尚未收束，`--operation close-preserved --approve-preservation --identifier <cycle-id>` 先核对来源及响应哈希，再不调用模型地补齐关闭。同一确定性校验也服务于 Supervisor preserve 路径。未执行完的 decisive_test 不得到科学 verdict，保全文档只保留有条件的观察与未完成部分。

## 单 Stage A/B 与 SDK C 的职责

路线为 `Supervisor → Investigator.preserve_research(A→B, 同一次模型 Stage) → SDK(C, 确定性) → Cycle OVER`。实际调用的 Stage 指令要求先完成有来源和限制的 A，再给出忠实的简短 B；合同限制 B 的长度。SDK 独占归档、SQLite/Canvas 投影和正式关闭，不进入后续 Reporter/Judge/Literature，也不自行启动新的 Cycle。已完整保存的 Stage Output 在恢复路径中优先验证并复用，不重新调用 Investigator。
