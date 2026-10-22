# Research Lead / Challenger · 当前 Prompt 权威与 Qualification 合同

审查实施：2026-10-12。正式入口：`workflow_invocation._call` 经
`stage_context` → `build_initial_prompt/build_continuation_prompt` →
`backend.invoke`。两角色使用普通 Stage Turn **不是 Investigator Native Goal**。
Stage Card 和 Procedure 确实进入实际 Prompt；初始 Developer Instructions
仅在新 Codex Thread 建立时发送。

## 职责

- Research Lead 决定与尚未完成的 Goal、Research Contract、Final Gate
  有必要关系的当前科学问题，提出候选；选优仅发生在 Challenger
  Qualification 之后。Hypothesis 的 stable ID、Graph edge、Cycle criterion、
  route、锁定和持久化属于 SDK。Canvas `frontier_status` 只在
  `frontier_maintenance` 中由 Lead 判断；不得误当下一轮必走路线。
- Challenger 在 Candidate Lock 之前独立判断 Goal 对齐、可证伪性、
  命题连续性、推断辨识度及可执行性。尚未完成的合法推导/计算属于
  Investigator，不构成 `revise`。真正无法维持冻结目标或检验效力的
  缺陷不得被视为调查任务。Challenger 不选择 Winner、不判研究真假。
- 通用开发者身份只定义持续权限；每个 Stage 的执行标准写一次。
  Procedure 只定义必要的 Canvas/文献导航顺序，Contract 只定义
  Agent 输出字段与 SDK 闭合边界。避免多个层重复规定相同科学约束。
  不设虚构的 4000 字符限制；这两角色的普通 Turn 与 Native Goal 不同。

## 当前最小身份绑定（Challenger v4）

Challenger 的 `candidate_review` 返回每个 Candidate 的 **原样** SDK
`candidate_id`、`verdict`、`reason`，仅 revise 附带
`blocking_issues[].problem/required_change`。ID 不由模型生成，仅复制
SDK 的当前 shortlist。合同要求恰好一条 review 对应一个输入 ID；
拒绝未知、重复、缺失 ID。SDK 按 ID 绑定，绝不按数组位置绑定；
历史 v3（按数组次序）的 Schema 仍可读取，但不得用于新的调用。

## Qualification 修复边界

`qualified` 表示已经满足加入本轮检验的科学资格；
`revise` 表示阻断项全部可在当前冻结 H/scope/estimand/inputs 范围内修复；
`reject` 表示没有范围内合法修复路径。

Research Lead 的 `selection_repair` 可在冻住的候选上逐 blocker
提供必要的澄清和小型补丁。若输出 `cannot_repair`，SDK 必须保留原
Challenger blocker 和未锁定状态，转 `no_qualified_candidate`
Needs Attention（允许随后重启 Lead qualification），**不得合成
qualified** 或直接让 Investigator 运行。适合 Investigator 执行的
合法科研前置工作应一开始就被 Challenger 认定为合格，不能通过
`cannot_repair_passthrough` 偷渡。历史记录可读、不能重演旧放行行为。

## 实际输入审计与验证

Observer 展示 Lead/Challenger 的 `AGENT_INPUT` task/continuation 为
真实普通 Stage Turn；区别于旧 Investigator 的逻辑 Stage 投影。
测试必须核对真正拼装的 Context、Stage、Procedure、Schema。
候选身份错配、重复 ID、Cannot Repair 停止的静态行为可用 FakeBackend
验证；科研筛选质量与过度阻断率需要另获授权的真实 Cycle 轨迹，不得
启动未经批准的 LLM 测试。对应 TMC-20261012-e05b5faa0970、
TMC-20261012-8319195a48dd、TMA-20261012-0074、TMA-20261012-0075。
