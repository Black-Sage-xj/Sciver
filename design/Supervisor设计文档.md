# TheoryMaster Supervisor：单一身份与持续科研恢复

## 唯一职责

Supervisor 是一个运行保障角色，而非 Runtime / Incident 两个独立 Agent。
唯一使命：保障**已授权的**科研任务持续推进，直到 Goal 完成、用户主动停止，或资源/科学安全条件暂时无法满足。
`hold` 是内部技能间交接或带原因的等待状态，**不是默认的最终答复**。

## 技能划分（一个身份，渐进加载）

| Skill | 何时使用 | 实际权限 |
| --- | --- | --- |
| Runtime triage | Worker FAILED、provider 中断、线程繁忙 | 只读诊断；输出受控 resume/hold JSON；SDK 负责安全启动 |
| Engineering repair | 需要修改系统配置或工程代码 | 已授权工程环境中读写、离线回归、记录变更；不得篡改科学状态 |
| Attention adjudication | 资格耗尽或现有检查点的科研阻断 | 全任务审查、选定一项候选交 Challenger、核对 Goal 复评条件或记录等待依赖 |
| Quota control | Codex/provider 真实额度耗尽 | 检查其他可用账号，全部无额度时等待 reset 并自动重试 |
| Recovery verification | 自动 Resume 后 | 必须检查**后续模型/工具真实活动**，不能仅凭 PID 判断成功 |

为了兼容现有 Codex 后端与测试，内部仍保留 `runtime_supervisor`、`incident_supervisor`、`attention_reviewer` 三个调用键；他们共享 `resources/identities/supervisor.md`，只附加各自 Skill 的权限合同。**调用键不是三个独立人格**。

## 持续恢复状态机

```text
Worker FAILED / 白名单工程依赖 NEEDS_ATTENTION
  → Runtime triage
  → recoverable → guarded resume → 验证实际模型/工具活动
  → needs engineering → Engineering repair → 离线验证 → 重新 triage
  → provider transient → backed-off retry → resume
  → provider quota exhausted → account switch / wait for reset → resume
  → not safely recoverable → 记录等待与报警 → 定期重查（不修改科学状态）
```

工程 `hold` 后由 Worker 调度器保存带 fingerprint 的 `retry_wait` 回执，每次采用退避，间隔最高约 6 小时，并持续保留恢复意图（不再因为第 3 次重查失败而永久退出）。真正的账号 quota 由已有额度控制器负责，不能把真实额度耗尽伪装成工程无限重试。

进入 Engineering repair 的前提：无活跃同任务 Worker、锁定相同故障 fingerprint、存在安全 checkpoint 或已提交角色边界、未弃用 Cycle、未触发科学预算 guard；provider capacity 不能触发源码修改。工程动作和“真实科研恢复”必须分开记账，只有后续真实活动才能标记恢复。

原生 Goal 没有终端 Provider 错误而返回 blocked 时，SDK 记录原线程、工作区、Goal 摘要哈希和公开原因。若阻断来自可修复的计算，恢复回执核对原 Goal 的终态、检查点、修复材料或有界计算计划、调度锁及预算余量。回执只可消费一次，恢复使用同一 Cycle 的冻结 H 和剩余预算，保留原线程供核对。缺授权输入、锁未释放或回执校验失败时记录等待依赖。Provider 容量、账号额度和预算按各自规则处理。

### 冻结输入不可恢复的正式移交

Investigator 完成有界输入核查、确认冻结来源内没有可行恢复路径后，可以提交 `execution_limit`，种类限于 `input_support_unavailable`。交付须绑定本次 invocation、冻结科学对象摘要和工作区普通证据文件的 SHA-256，并说明已检查的替代路径、未执行的检验与所需依赖。方法失败、计算困难或成本高本身不满足该出口。SDK 只验证交付身份和文件一致性；“输入不足”的科学说明仍是 Investigator 声明。

只有本次正式交付验证通过时，SDK 才结束该执行尝试，记录 `not_adjudicated`、空科学 verdict 和完整执行限制。该路径不生成 Findings、不调用 Judge、不修改 Scientific Model，也不把未执行检验写成 `open`。Goal 未完成且本次调用尚有 Cycle 额度时，自动创建下一 Cycle，Lead 与 Challenger 收到带来源路径的近期执行限制，在新提议锁定前检查缺失依赖；这些记录不进入科学 Outcome。没有合法交付的原生 blocked 继续进入运行故障诊断；Provider 错误、额度、暂停和预算守卫优先。

官方 SDK 会将多个物理回合合并成一个 Goal 逻辑流。正式输入限制已写好时，TM 在同一 operation 已开始并成功结束的物理回合边界验证交付，再通过官方 cancel/close 接口结束行政移交，记录 SDK 的 `execution_limited` 状态；不等平台重复三轮 blocked 核查。这个独立边界回调不改变既有逻辑流和 Collector 的回合语义，也不在工具或模型回复执行途中消费文件。

旧版 Goal 因缺少该交付合同而 blocked 时，窄范围 SDK validator 核对失败 invocation、原线程、原 Goal 哈希、公开事件与新协议可用性，允许原线程用新合同重入一次。该回执只证明交付协议已修正，不判定输入确实不可恢复，不代写交付，也不授权重复重入已采用新合同的 Goal。具体行为效果由 `TMA-20261119-0145` 保持后续轨迹观察。

### 资格耗尽的接管边界

三轮资格提议被退回，或 Lead 明确报告没有可行候选时，自动接管使用 Attention 的全任务资格恢复审查。SPV 读取规范 SQLite 科研图谱、Scientific Model、Goal 裁决、相邻资格失败记录和正在调查的 H。它可以选定一个可执行、结果有判别价值的候选，提交已有 Challenger 资格审查。通过后由 SDK 冻结并交给 Investigator。没有合规检验时，SPV 记录具体缺失依赖和可观察的唤醒条件。候选被 Challenger 否决后，本次 SPV 审查停止。历史候选和 Judge 裁决继续保留。

如果此前已经有受支持的科学结果，Goal 只缺可从已登记结果复现的表格，SPV 可以提交交付计划。SDK 核对冻结输入、已裁决预测、行身份和输出列，保存可重放的补充交付回执，再沿原 Goal Judge 路径复评。该路径保留原 H 和裁决，使用既有预测数据，验收依赖原 Goal Judge。轨迹观察参见 TMA-20261121-0179。

对已停任务打开 Supervisor 会启动有唯一运行所有者的接管请求。恢复前再次核对源 Cycle、任务级 Goal 封存、人工停止命令及 Worker claim。工程故障继续从经过验证的检查点恢复。旧 Cycle 合法耗尽后的接管在新 Cycle 中进行，复用任务图谱和既有证据。明确报告缺失依赖，或 SPV 选定的候选被阻断后，输入指纹不变时等待。指纹包括正式 Outcome、Scientific Model、Plan 和小型规范输入清单。新增材料能否作为研究证据仍由相应审查阶段验证。

Challenger 获得绑定已提交调用的资格失败历史与原科学三元组。SDK 拦截完全重复的候选，保留过程记录，科学语义和试验价值继续由 Challenger 判定。任务计划、证据和 Judge 权威通过原有路径维护。真实轨迹观察参见 TMA-20261121-0178。

工程候选接收与失败 invocation 绑定的紧凑公开证据，包括冻结科学、最近相关交付和已观察加载 Skill 的当前规则/哈希；当前规则不冒充历史版本证明。记录不授予修改科学状态或受保护策略的权限。普通代码候选继续通过独立差分验收；`repair_plan.json` 也可返回 `needs_dependency / needs_rule_revision / needs_configuration`、具体原因和缺失依赖，此类诊断必须无源码/测试变更，不安装文件、不授权 Resume。缺计划文件即使进程退出 0 也属于未交付；脱敏退出信息和输出片段随候选证据保留。

明确依赖等待仍保留恢复 owner，排程定期做无模型条件检查。源代码、相关已加载 Skill、身份/配置、checkpoint 或恢复控制记录变化后可重新诊断；时间到达本身不触发相同的付费工程重试。外部输入变化可通过更新来源配置或 SDK recovery_control 通知恢复 owner。Console 对单任务及 Worker 均投影当前失败绑定的持久恢复记录，分别展示诊断、等待依赖、受控恢复及已核实活动；活跃对话和 PID 均不能单独证明科研已恢复。

`lead_literature_unavailable` 表示 Lead 已授权的检索没有交付结果，属于上述工程依赖白名单。Attention 阶段确定性记录 `hold` 并交回运行恢复 owner，不调用只能选择 `hold` 的模型审查。即使 Runner 正常退出 0，管理进程仍进入持久恢复循环：诊断、工程交接或带依赖的 `RECOVERY_WAIT`，由 SDK 安全恢复同一请求。这个分类不授予额外科学 Literature detour、替换来源或修改 Lead 决策的权限。`lead_literature_detour_exhausted`、未知 Attention、真实科学停止和预算停止不因此获得自动恢复权限；Supervisor 关闭、用户暂停/终止和既有恢复守卫仍然有效。

`contract_repair_failed` 同样属于工程交付白名单：角色输出在有界修复后仍未满足合同，Attention 确定性交接工程诊断并持续持有恢复责任。`protocol_health=violation` 保持生效，禁止未经独立修复验证的 plain resume；允许记录合同实现、准备上下文或依赖缺口，不能修改 Judge 科学裁决、冻结假设、证据或科研预算来消除故障。合同缺口尚未解决时持久等待，不把这次交付失败转为科学停止或伪造有效角色输出。

## 不可逾越的边界

- 不擅自恢复用户主动暂停/终止的任务，不在 Goal Completion 后重新开科研 Cycle。
- 不将没有安全 checkpoint 的 Cycle 强行重启，不绕过预算/所有权 guard。
- 不擅自改 Hypothesis、Canvas、Evidence、Judge Outcome、Research Contract 或科研数据。
- 不以修改 `research_state.json` 或伪造模型输出规避故障。
- 不主动打断仍有活动的原生 Codex Goal，不覆盖并行 Worker 修改。
- 故障不能安全自动修复时持续保留诊断/等待任务，通知用户，**不能谎称科研仍运行中**。

## 验收与仍未完成部分

新行为可用离线 Mock 与状态转移验证；**不等同于已经证明复杂真实故障都能自愈**。

用户期待的最终不变量是：**只要科学与资源前提允许，就不允许因纯工程故障永久放弃已授权的科研任务；不能安全继续时，Supervisor 继续持有有记录的修复/等待责任。**
