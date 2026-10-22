# TheoryMaster 多 Worker 并行科研架构

状态：**V1.1 实施合同已落地（Goal 封存、Cycle Claim、Proposal Gate、按容量调度、串行选题、CAS 待整合）。真实多 Worker 科研仍待 `TMA-20261020-0097` 授权。权威接口见 [多Worker并行V1.1冻结合同.md](./多Worker并行V1.1冻结合同.md)。**

当前规则：只串行选题；Worker 认领 Cycle 而不是历史 H；Preferred 只展示、不调度；Goal Passed 是唯一全局终态。下文第 2 节起关于「按活跃 H 认领」和「preferred 优先」的描述已被该合同取代，仅作历史记录。

# 历史：动态容量 V2

状态：**动态 Worker Runtime、CLI 调度器、Canvas 受控并发、独立 Codex 认证及 Web Agent 标签页已经代码落地，完成离线 Mock/静态测试。尚未切换任何正在执行的 Master，未执行真实多 Worker 科研 Cycle 验证，科研效果审计仍 pending。**
来源：用户批准 2026-10-16 落地；并发数由当前 Canvas 可探索的分支和资源决定，不固定 2/3。
修改登记：`TMC-20261016-9841c61352e3`；本次 `TMC-20261016-9713e5fede77`；真实 Cycle 后续 `TMA-20261016-0084`。

## 1. 不变量与容量策略

- 仍然是一个 Master、一份 Canvas、一个 Research Plan、共享 Scientific Model 和唯一 Goal Completion；Research Plan 的 Goal / Research Contract / Final Gate 维持 Human 权限。
- 每个 Worker 是完整的 Research Lead → Challenger → Investigator → Literature → Branch Judge → Reporter Cycle 运行体，**不是新增科研角色**。
- 多 Worker 数量由 **可独占、活跃的科学前沿数量** 和 **已核实可用的 Codex / 计算资源**共同决定。不得设定永久最大数=2、3 或任意硬编码规模。
- 目标并行数 `desired = eligible_unclaimed_frontiers`；允许运行数 `runnable = min(desired, verified_resource_slots)`。若资源条件无法核实，则 `runnable = 0`，fail closed。
- 只有一条活跃前沿就不强行创建多个 Worker，更不得为了填满 Worker 数而凭空派生新 Hypothesis。
- `frontier_status=active` 的 Hypothesis 或 Decision tip 均可以作为入口，Decision 沿科学谱系查找唯一最近的 H，若 H 身份有歧义则留给 Lead 规划，不能使用文字相似度猜测；同一 H 的多个 tip 不重复认领。
- 一个显式 active H 如果仍然是另一个 active H 的祖先，不额外占用一个并行席位。共享祖先只读；同一时刻每个 H 至多一个有效 owner。
- `priority=preferred` 排在普通目标前；若用户明确选择一条可用 H，则尝试该 H；失效或冲突时应返回错误，不偷偷替换为其他分支。

## 2. Canvas 分支认领（已实施控制平面）

`theory_master/multi_worker.py` 提供 `BranchClaims`：

- `status`：返回活跃 H、当前 claim、Canvas SHA256 版本。
- `plan(available_resource_slots)`：按当前前沿和外部真实资源容量动态计算，不使用固定 Worker 数上限。
- `claim(worker-N, hypothesis_id?)`：持有 `branch_claims.lock` 原子认领；支持任意正整数 N 的 Worker ID、人工指定 H、Canvas 乐观版本校验。
- `update(action=heartbeat|pause|resume|release)`：维护 24 小时租约、暂停时保留认领；释放时先检查 owner PID/启动标识。
- `reclaim(isolated=True)`：仅在租约已过期、旧执行体已被确实隔离且进程不存活时安全回收；**时间过期本身不等于有权回收**。
- `assert_writer`：校验 H/Worker/claim ID/递增 fencing epoch，拒绝暂停、过期或过时的持有者。
- 运行期元数据位于 Master 任务的 `.theory-master/branch_claims.json`，不写入 Canvas 的科研文本或研究结论。

**旧机制兼容护栏**：现有单 Owner `runtime_manager` 启动时与 Branch Claim 共享互斥锁。如果存在任何 claim，则拒绝旧单 Owner 的 `run/resume`；如果旧 Runner 仍在运行，拒绝新 claim，容量规划返回阻断标识。以上是迁移期安全要求，不代表已经可以同时执行多个 Workflow。

CLI 只读观察：`python -m theory_master workers 1 status`、`python -m theory_master workers 1 plan --resource-slots 4`。`--resource-slots` 是外部**已核验容量输入**而非强迫启动 4 个 Worker；以上命令不会执行真实模型。

## 3. 代码落地的运行层与仍需观察的边界

1. **WorkerRuntime 与动态调度器（已实现）**：`theory_master/worker_runtime.py` 持久保存每个 worker-N 的 PID、状态、目标 H、独立 Cycle、日志及账户归属。CLI `tm workers <master> start --resource-slots N` 显式启动调度器（不创建固定 N 个 Worker），循环读取 Canvas 活跃前沿，分支不足则不补位，自动匹配空闲资源席位。`stop` 只停止新分支分配，现有 Worker 自行完成 Cycle；`pause/resume/release --worker worker-N` 只操作该 Worker。旧单 Owner Master 正在运行时 `start` 会拒绝，不会热迁移运行中的科研回合。
2. **全局编号（已实现）**：Worker 使用带互斥锁和递增账本的 `reserve_cycle_number`、`reserve_hypothesis_id`，保持现有 `N-decision`、`H-xxxx` 形式；编号分配先后不保证科研完成先后。
3. **Canvas 并发写（已实现 Worker 互斥，仍有边界）**：已连接现有正式科研 Canvas projection / Reporter 提交路径，Worker 必须出示认领 fencing token，进入 `canvas_write.lock` 后二次校验，才可以读-改-写同一 Canvas。不会让两个合作 Worker 并发整图覆盖；**外部非 TM 人工编辑尚无同一事务 API，不应宣称跨所有外部 Writer 完全串行**。现有其他跨 Cycle 仪表盘/人工脚本需在未来上线审计中核查。
4. **Scientific Model 与 Goal Gate（已实现 CAS/互斥，真实验收待定）**：Scientfic Model 提交使用 read-version CAS；冲突时保留 Lead 生成的原始 Cycle synthesis 和证据，标为 deferred，不覆盖其他 Worker 的最新总结。Goal Completion 使用 Master 唯一 `goal_gate.lock`；supported 仅触发全局检查，PASS 前检查 Canvas / 模型版本是否在评判时改变。避免把 Worker OVER 当作 Master COMPLETE。
5. **Codex / 模型路由（已实现基础隔离）**：两个 Codex 账户各占一个独立执行席位，每个 Worker 有私有 CODEX_HOME 并固定指向一张账户的认证资产，不在别的 Worker 科研中调用全局 `use_account`。显式配置的外部 Provider 可按人工核验可用资源席位扩展超过三个。分支个数、可用账户数和 `--resource-slots` 联合决定并行数量。每 Worker 的模型偏好独立文件保存，只在该 Worker 下一个 Cycle 生效；尚未内建自动 Slurm 作业配额隔离，配置 `resource-slots` 前应核实真实本地/HPC 资源。
6. **知识同步（已实现周期边界 + 确定性通知）**：每个新 Cycle 从最新 Canvas 和 Research Plan 初始化，冻结本轮 Hypothesis/decisive_test；运行中定期检测 Research Plan hash、认领前沿被移除或 closed/rejected 的情况，在 Worker registry 记录 `critical_knowledge_notice`，不擅自进行科学矛盾裁决，不因普通新增 Finding 打断其他 Worker。
7. **Web UI（已实现）**：在真正存在的 Worker 注册记录中自动生成 `Agent 1/2/3/...` 顶层标签，不显示假的空 Worker 标签。标签各自投影对应 `cycle_dir`；按钮、角色模型设置提交精确 worker_id，Web/SDK 双层校验，保留旧单 Worker 控制页面。工作区外的 Explorer 链接、部分历史次级视图仍按 Task 聚合，后续需精确 UI 回归确认是否需附加 worker 参数。
8. **暂停/恢复及异常（已实现基础功能）**：调用运行中的 `recovery_control.json` 到安全点暂停，暂停保留 claim，显式 Release 或强隔离后的超时回收，Resume 必须回到同一 Worker 的冻结 Cycle。启动前失败可安全撤销认领；启动后进程存活不确定则保留，以避免重复科研。**多 Worker 已提供显式 Supervisor 开关下的「故障 Worker 已退出且其他 Worker 无运行进程」隔离工程修复入口；默认不覆盖任务级关闭设置，也不热替换正在运行的 Worker。完整跨 Worker 动态修复、共享配额切换与工程故障后的真实运行验收仍受保护并待独立审查。**

**操作入口**（以下命令只是说明能力，尚未用于当前活跃 Task）:

## 4. 科研职责

SDK 负责 deterministic claim / fencing / ID / snapshot / commit / quota / 运行恢复；Agent 负责科研提案、推导、实验、证据和 Judge 判定，不在 Prompt 中引入分布式锁协议。多个 Worker 可以复用 Task 原有网络资产，保持独立的冻结训练/测试证据资格及独立工作区。禁止以同一批数据在两条分支中分别声称“独立盲测”。Reporter 只能登记本轮结论，不能决定其他 Worker 的后续 Cycle。

## 5. 完成验收闸门

**版本上线纪律**：离线模拟不等于真实科研验证。当前活跃 Task 的旧 Runner 不热升级、不重启、不抢锁；待原 Runner 自然结束并进行安全切换后，需另行明确授权首批真实 LLM 并行 Cycle 回归，TMA-20261016-0084 保持 pending。

## 6. 用户操作：任务级「多 Agent 模式」开关（2026-10-16）

- 直接进入对应任务的 **Task Console 页面顶部「多 Agent」管理栏**。Settings 页面不再放置这组开关、数量和启动按钮。关闭时顶部只显示精简开关，Agent 1/2/3 标签完全隐藏；开启后展开数量及运行管理操作。
- 默认为关闭（既不自动启动 Worker，也不在 Task Console 显示 Agent 1/2/3 标签）。开启开关后可填「最多同时运行的 Agent」，允许 1–1024 的整数，保存到该 Master 的 `.theory-master/worker_mode.json`；这是安全的配置上限，并不代表有同等数量的科研独立分支或可用配额。
- 顶部的开关改变会立即保存任务级意图，不会中断/重启既有科研；修改最大并行数量时点击 **保存**。点击 **启动多 Agent** 才显式启动 Worker Scheduler；如果旧的单 Runner 仍在执行，拒绝启动并提示等待安全收束。已运行的调度器每 8 秒读取新的任务配置，所以直接改数字即可调整下一轮的新增 Worker 数，无需停止已在运行的 Cycle。
- 点击 **停止新分配** 只停止调度；关闭顶部开关会停止调度并立即隐藏任务控制台的多 Agent 标签，只保留精简模式开关。已经运行的科研 Worker 不会被强制停止；其认领仍会在完成或显式 Release 时释放。在 Worker 尚未完成前，旧单 Runner 的复用入口仍受 claim guard 阻断。
- 实际并行度仍由 `min(配置的 Agent 数, 空闲独立 Canvas 前沿, 可用独立 Provider/计算资源)` 限制。Codex 账户当前有两个独立认证席位时，即使填写 4 个也最多创建两个使用账户认证的 Worker；不为了填满设置数量凭空制造新 Hypothesis。
- 配置 API：`set_worker_mode`（原子持久化 `{enabled, desired_agents}`）；`start_worker_scheduler`、`stop_worker_scheduler` 需用现有的 Web 操作鉴权、Origin 校验、请求随机 ID、task/Cycle/launch 乐观前置条件。前端没有调用任意 shell 或传入任务路径的权限。
- 为了与旧科研运行隔离，当前尚不支持把运行中的单 Runner 无损即时拆成 N 个 Worker：该任务会保留在原运行模式直到其正常结束；首次真实多 Agent 科研的数学正确性仍列在 `TMA-20261016-0084` 审计中。

## 7. 真实旧任务停机迁移（任务 1，2026-10-16）

经 Human 明确授权停止运行中的旧 Runner 后，已将任务 1 转到新 WorkerRuntime，而不是让新旧版本同时写 Canvas：

**待检验**：首轮恢复后 Agent 产出的科学发现/Canvas commit/Goal Gate 仍须审计 `TMA-20261016-0084`；目前自动新前沿生成（没有第二条活跃前沿时的自主规划）并未通过真正的并行科研实测，不能把最大并行数 2 等同为始终两个运行进程。

## 8. Task Console 的一键新增 Agent 交互（2026-10-16）

- 第 6 节描述的顶部多 Agent 设置框已被简化界面取代，不再显示开关、最大并行数、保存、启动、停止新分配。
- Agent 1 卡片右上角使用红色 `+` 作为唯一可见的新增入口。点击一次由高风险、带请求去重与 Cycle/launch 乐观锁的 `add_worker` 操作记录新增一个 Agent 的意图，并确保调度器启动或继续工作；不触碰正在运行的 Agent。
- 原先最大并行数字段仍由 SDK 维护作内部并发上限，但不暴露给用户。首次 `+` 从已登记 Worker 数量建立意图，后续每次点击加一。已申请但未成功分配的席位明确显示「待分配」，不伪造 RUNNING。
- Agent 实际启动仍受独立科学前沿、Codex 账号和资源、Master Cycle 预算与 claim 安全边界约束。由 scheduler daemon 执行分配而不是网页前端或同步绕开其预算/锁；旧单 Agent Runner 仍在运行则拒绝新增。
- 历史的 CLI 和旧 operator action 保留兼容，隐藏的页面元数据只用于安全检查，不重新生成顶部可见控制栏。

### Cycle 级并行来源追溯（2026-10-17）

新 Cycle 的 `research_state.json` 必须携带创建时 `cycle_execution_context`（模式、worker_id、实际观测到的活跃 Worker、资源槽配置和观测时刻）；并行 Worker 在 `worker_concurrency_events.jsonl` 里按 Worker 集合变化记录追加事件。`concurrent_workers_at_start` 仅为观测时刻快照，不承诺整个 Cycle 始终保持此并行度；老 Cycle 无来源时保持 `unknown_legacy`。Research Graph V2 的 `cycle_memory_index` 为此提供 H → Cycle → 原始日志/证据的持久索引，详细语义见 [ResearchGraph设计文档.md](./ResearchGraph设计文档.md)。
