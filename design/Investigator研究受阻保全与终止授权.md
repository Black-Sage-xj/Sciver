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
→ preserve_research 将已有观察、失败边界、未完成 decisive_test、原始证据归档；SDK 核验后幂等投影 Markdown + Canvas StageSummary
→ H 状态仅设 parked，科学真值仍 open，无 Judge 裁决
→ 该 Cycle 记 OVER（未裁决保全退出，非 COMPLETE），Research Lead 在后续 Cycle 自由选下一个 Goal-aligned 前沿。

## preserve_research 输出

**A · Scientific（Investigator，同一 Stage）**：先从已有原始成果中整理 provisional observations、适用条件、复现证据路径、已试方法的有限失败边界、remaining_gap、未执行的 decisive_test 部分。不追加新数值实验，不伪造 Findings/Judge 结论。

**B · Presentation（仍由该 Investigator 在同一个回答中完成，不单独调用模型）**：仅将 A 压缩为 `canvas_title`（≤48 字符）与 `canvas_summary`（≤160 字符），准确表达适用范围及“尚未裁决”；不产生 A 以外的新科学事实。

**C · SDK（纯确定性，不调用模型）**：检查本 Cycle 文件及 SHA-256、验证 A/B 合同和未裁决属性、生成完整 MD、维护唯一且连通的 Canvas StageSummary、令原 Hypothesis `frontier_status=parked`、写入正式 `OVER` 收束及恢复备份。重复投递若已有完整保全结果则复用，不能再重复花费模型 Turn。

**不设置独立 Stop Reviewer，也不从 preserve_research 额外调用 Reporter、Judge、Literature 或 Research Lead。** 现有 Supervisor 负责 `continue/preserve/hold` 的停滞运行决策，不负责科学裁决。保全 Stage 的完整 MD 就是未裁决科研留存报告；未来若需要跨 Cycle 综合报告，可独立请求 Reporter，但不属于本条自动停止路线。下一 Cycle 的 Research Lead 在新运行中读取 Canvas/MD，自由规划，而非由保全 Stage 越权推荐下一候选。

## 额度与循环

不能以“Token 还有剩余”强迫没有判别收益的探索无限循环。额度接近用尽时可走现有 Budget Reviewer/Supervisor；明确的重复空转也要触发科研投入价值审查。Quota/Provider 错误仍严格沿原有恢复分支处理；不得转成科学 blocked。

## 回归标准

本轮已进行一次人工保全（H-0056 对应 external-c86-preservation-20261013），不代表新的自动工作流已经实现。

## 2026-10-13 已落地的第一阶段（仅确定性回放验收）

已新增 `investigator/preserve_research` 的 Stage、结构化科学观察合同、最小输入投影，以及 SDK `research_preservation.py` 独立执行入口。Runner 已提供显式 `--operation preserve --approve-preservation` 命令，但该命令会**发起一次新的真实 Investigator 模型调用**；不能将普通 `--operation resume` 等同于保全。

入口读取原始 `investigator_native_goal_blocked` 检查点，原科研线程不重启；归档必须有用户显式授权（手工保全测试）或同一 Cycle 的 Supervisor Attention 保全批准凭据 `preservation/supervisor_authorization.json`。原生 blocked 不能自行授权结束。SDK 校验 Cycle 本地证据与哈希、验证未裁决合同，幂等维护一张 Canvas StageSummary 来源节点和 MD 文档；绝不制造 Judge Decision。

## 2026-10-13 已完成 C86 正式收束并统一入口

现在显式 `--operation preserve` 在 Stage 成功后自动收束；若先前 Stage 已保存，使用 `--operation close-preserved --approve-preservation --identifier 86`，**不调用模型**地补齐遗漏收束。现有 Supervisor `preserve` 分支共享同一确定性校验。结果仍不调用 Judge 或 Reporter：未执行 decisive_test 无法裁决，而保全文档/Canvas StageSummary 已提供必要科学报告；下一 Cycle 的 Research Lead 通过 Research Plan 与 Canvas 读取该研究脉络，自主提出候选。未来真实 SPV 自动切换链仍属于 TMA-20261013-0081 的待观测项。

## 2026-10-13 最终选择：单 Stage A/B + SDK C

用户确认最终路线为 `Supervisor → Investigator.preserve_research(A→B, 同一次模型 Stage) → SDK(C, 确定性) → Cycle OVER`。Agent Registry 的 `may_do/must_not/exit` 与真实调用时的 Stage 指令均要求先完成有来源且带限制的 A，再写忠实而简短的 B；Stage Contract 对 B 的长度实施硬约束。SDK 唯一处理归档、Canvas 来源连线与正式关闭，确保不再进入后续 Reporter/Judge/Literature，也不自行启动新的 Cycle。上一次 Stage 已完整保存但主状态未关闭时，恢复路径优先验证并复用既有 Stage Output，不重新调用 Investigator。
