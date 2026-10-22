# Sciver 任务⑤ · Investigator Prompt 一致性与轻量去重（2026-10-22）

## 边界

- 来源：用户要求检查生产 Investigator Prompt 三层的重复、不一致和领域特化。保留 Native Goal v2，不改回 Stage Card 工作流。
- 生产装配：新线程 Developer Instructions（`investigator.md` + `_investigator_handoff.md`）只在 `thread_start` 发送；恢复线程不重发 Identity。每次 `investigate` / `judge_rework` / `integrate_literature` 发送 Setup Turn + Native Goal v2。`stage_registry.py` 的 Stage Card 不进入该链路。
- 运行中 Worker 不重启，也不要求已有线程重载 Identity。Goal 文案在下一次 Native Goal 生效；Identity 文案只作用于新线程。
- 未改 `stage_registry.py`、`contract_stages.py`、科学图谱、预算账本和 Supervisor。`workflow_invocation.py` 里的交付 JSON 句子不在本次编辑范围，仍由 Setup 逐次给出路径。
- 静态测试不证明模型会照做。真实轨迹见 `TMA-20261022-0110`，三项均 pending。

## 逐项审查表

| 问题 | 原保护意图 | 处理 | 保留的原因 |
| --- | --- | --- | --- |
| 冻结 H / scope / decisive_test 是否多次传入 | 零截断；超长时用 Setup 原文 + SHA-256，禁止摘要 | 短 Goal 仍逐字内嵌，Setup 去掉同一份 `frozen_objective`。超长才改 Setup 引用。Judge `requested_resolution` 仍同时在 Goal 与 Setup JSON | 异议记录可能带请求以外的字段；删 Setup 会改交付合同。冻结三字段本身不双写 |
| 不得自主 blocked | 方法失败不能当成科学停机 | 生产 Goal 保留一句 `Never self-terminate or request native blocked`。Identity 与 Handoff 不另写一遍。Stage Card 里的同义句不进入生产 Prompt | 已有线程不会重载 Identity，这句必须留在 Goal。它不是 SDK 状态机说明 |
| Judge 独立裁决 | 完成 Goal 不等于裁决 | Identity 保留「唯一裁决权」和「供 Judge 独立裁决」。Goal 保留「completion does not replace independent Judge」 | 两句分别挡住改裁决和把完成当裁决。Goal 那句对已有线程仍然可见 |
| 历史成果复用是否两处长篇复述 | 适用则继承；该独立核验的不得跳过 | 整段规则只在 Identity。Goal 仍只有一句 cite / do not skip | 一句提醒有防错价值，不再抄网络、力域细则 |
| 已暴露数据冒充新独立证据 | 历史相似不能取得冻结测试的证据资格 | Identity：历史相关不能代替冻结测试要求的有资格独立证据；已暴露样本不能冒充盲测或 Final Gate。Goal：exposed / post-hoc 不能冒充新独立证据；返工仍禁止把 post-hoc 当 holdout/gate | 统计隔离和一般「新独立证据」写在一起，不互相放宽 |
| 盲测、预测冻结、误差是否被当成一切研究的必经步骤 | 设计文档已要求按学科选择校核；代码却写成对所有任务 `freeze predictions ... before blind outcomes` | 删掉这句通用链。证明不必制造盲测数据。统计或数值检验仍须保留事前预测、数据隔离和误差检验，并完成冻结 decisive_test | 不把缺盲测数据当成证明失败，也不允许用已暴露结果替换冻结测试要求的隔离或误差检验 |
| Native Goal 里的 SDK 生命周期 | 额度、usageLimited、budgetLimited 由 SDK 分类 | Goal 删除 `preserve usageLimited and budgetLimited`。仍保留：不自主 blocked、只在真实 token 预算边界由 SDK 决定追加、资源限制不需要数学不可能证明、守卫是 token/usage/operator 而不是墙钟 | 这些句子阻止把缺口当成停机。状态名本身不是科学完成条件 |
| 凝胶领域词 | 全局 Investigator 不应写死某一任务的网络/力域 | 适用性核对改为对象、协议、定义域、单位与误差边界。数值操作仍指向 numerical-investigation | 与「任务私有限制不得写成全局规则」一致 |
| Handoff 与 Identity 同义段 | Setup 机制要让模型知道 3800 回退 | Handoff 去掉「SDK 负责生命周期、机器 Evidence 身份与路由」。保留 3800/哈希回退、有目的检索、交付接口、不得用旧 Stage Card 改冻结目标 | Identity 已写 SDK 的路由、Cycle 与 Evidence 机器身份 |

未强行删掉的近义句：`A gap is not an exit` 与「不得自主 blocked / never the Goal」重复，已删。`without looping` 为保住 v2 短于 v1 的既有长度合同而删；`continue in-scope` 仍在。

## 长度

3800 安全预算和 4000 接口上限仍满足。文献整合变长，是因为补上「证明不发明盲测、隔离比较不能用已暴露历史替换」；绝对长度仍远低于 3800。

## 登记与验证

- 静态：`TMC-20261022-be7f7e3da13c`。
- 未来真实 Cycle：`TMA-20261022-0110`，C01 证明任务不因 Prompt 发明盲测数据且须处理冻结证明义务；C02 要求事前预测、隔离或误差界的任务不得把已暴露或事后数据当成该独立证据；C03 不自主 blocked、不把部分工作当完成、裁决仍归 Judge。三项 pending。
- 静态命令（无模型）：`PYTHONPATH=. python -m pytest -q` 上列 Investigator 装配、长度、历史复用、Identity 与资源可行性 Goal 测试。结果 **51 passed，37 subtests passed**。`test_identity_contract_consistency.py` 里 Research Lead 的 `candidate frontier` 失败来自工作区里另一处未提交的 `research_lead.md`，不属于本次。`test_suite_policy.py` 的未登记模块同样来自其他未纳入本提交的测试文件；本次新测试已登记为 architecture。
- 无模型调用，无 Worker 重启，无 Push。
- `workflow_invocation.py` 的交付字段说明仍与 Identity 的 claim/method/result 有重叠。provenance 限制只在 Identity，不能为了去重删掉。
