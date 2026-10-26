# Research Lead / Challenger 指令与资格边界

当前生产入口为 `workflow_invocation._call`，经 `stage_context` 和 `build_initial_prompt/build_continuation_prompt` 装配。Role Identity 只维护跨 Stage 共用的权限与数据边界，新线程加载一次；完整当前阶段定义由 Stage Card 与其内嵌 procedure 一次渲染；Contract projection 维护输出字段及条件语义。没有独立 Procedure 注册表，也不把完整 Role Identity 重发到各 Stage。

Lead 负责提案、合格候选选择、Scientific Model 综合及既有 H 前沿判断，各 Stage 只执行当前授权。Challenger 独立审查 SDK 当前指定的资格对象：原审查核对候选，修订复核比较父候选、修订对象及冻结 blocker。Challenger 不选 winner、不修改候选、不做科研真假裁决。SQLite 是科学图谱权威，Canvas 是投影。

提案与原审查核对具体科学主张、相对已有工作的实质增量、适用的更简单解释或已有方法、给定设计下的推断能力。研究尚未完成不等于缺少资格；要求明确科学命题和可信范围内路径，不要求预先求解。逻辑或统计上不可能达到的必需判别结果是设计缺陷；不确定、负结果及诚实不可判明均可有科研价值。

范围义务来自实际 Goal / Research Contract / Final Gate 与 decisive_test。代际或壳层术语不自动形成全序列禁令，联合支持与匹配只在真实经验比较需要时检查。历史重构覆盖相关已裁决和未裁决来源，保留有效子结果；所有仍相关的 Goal Gate 失败理由须与当前交付核对，不能把后来遗漏当已解决。

qualified 进入已合格集合；selection 只选其中一个，不重开资格审查。revise 只允许一次保持原 hypothesis/scope/estimand 的有限修订，完整 decisive_test 必须包含所有绑定澄清；原 blocker ledger 是审计依据。Challenger 的修订复核只查原 blocker 和固定必要可行性条件，不能追加下一轮修复。cannot_repair 或 unresolved 不得被升级为合格，也不能进入 Investigator。

Contract Repair 修正当前输出合同，只保留已建立且属于当前 Stage 权限的内容。候选 ID 和 blocker ID 仍是 SDK 封闭选项，不从旧线程或历史 Cycle 替换对象。Judge verdict 和 Hypothesis Lock 只在实际已存在的对应阶段作为固定来源，不凭格式修复创造。

静态与 FakeBackend 测试验证字段、身份绑定、冻结对象保真、路由与恢复和实际装配；科学 oracle 保留正反例。短语存在仅证明规则送达，不证明 Agent 科学判断正确。真实科研效果仍按登记的后续 Cycle 审计核验。
