# TheoryMaster 多 Worker 并行 · V1.1 冻结实施合同

状态：**设计冻结，实施中**。V1.0 用户冻结方案 + 用户于 2026-10-20 确认的缺口决议 G1–G10。
本文件是各实施包之间的**接口与语义权威**；实施完成后由包 F 合并进 `多Worker并行科研架构.md`。
登记：MW-A `TMC-20261020-ea6d10f588c3`、MW-B `TMC-20261020-cb67fbd234df`、MW-C `TMC-20261020-e37fbe143f1c`、
MW-D `TMC-20261020-d70a23f809f0`、MW-E `TMC-20261020-2818c9329723`、MW-F `TMC-20261020-2e2526586d70`；
真实运行审计 `TMA-20261020-0097`（pending，需另行授权）。基线 Commit `b41618e`。

## 0. 总原则

一个全局科研任务、一套共享科学权威（Research Plan / SQLite Research Graph / Scientific Model）、
一个串行选题入口（Proposal Gate）、多个异步 Worker、唯一的任务级 Goal 终态。
**只串行选题；Investigator / Judge / Reporter 并行。** Worker 认领 Cycle，不认领历史 H。
H 父子关系 = 科学来源；Cycle Claim = 运行所有权；Preferred = 历史展示，无调度/排序作用。

## 1. 不变量

| 编号 | 不变量 |
| --- | --- |
| I-01 | 同一 Task 同一时刻最多一个 Proposal Gate 持有者 |
| I-02 | H 正式冻结前不得启动 Investigator |
| I-03 | H 冻结并保存检查点后立即释放 Proposal Gate |
| I-04 | 不允许两个 Cycle 拥有同一个新 H 身份 |
| I-05 | Worker 运行所有权通过 Cycle Claim + fencing token 校验 |
| I-06 | 普通 H/Outcome 更新不得自动终止其他已冻结 Cycle |
| I-07 | 全局科研状态写入必须检查 Goal 是否已完成 |
| I-08 | Scientific Model 版本冲突不得静默丢失科学总结 |
| I-09 | Goal Passed 全局唯一、持久，不可被晚到写入撤销 |
| I-10 | 恢复必须保留原 Cycle/H/decisive_test，不重复分配 |
| I-11 | N=1 与 N≥2 遵守同一套资格、裁决、Goal 规则与选题门 |
| I-12 | 历史 Preferred 不影响选题与调度 |

## 2. 缺口决议 G1–G10（已确认）

- **G1 Goal 判定屏障**：Goal Judge 全程持有 `science_commit_barrier` **独占**；其他 Worker 调查不停，只有科研写入在屏障处排队。PASS 后封存于 SQLite，排队写入一律 `GoalSealedError`，Cycle 记 `STOPPED_GOAL_COMPLETED`。Goal 有效性**只看 SQLite 终态**，不再比对整图摘要；旧任务的历史已验证回执作为 legacy 只读回落。
- **G2 Proposal Gate 租约**：持有者以 `worker_id + claim_id + fencing_epoch + gate_token + pid/start_ticks` 标识；暂停、额度耗尽、异常、RecoveryStop 必须释放；持有进程已死由调度器/Supervisor `recover_dead_holder` 回收；租约过期但进程存活**不**撤销（fail closed）。FIFO 排队；授予前检查 Goal 终态。
- **G3 重试计数**：1 轮 = 1 次 Lead 提案 + 1 次 Challenger 审查（含既有的 1 次冻结返工）。每次进入 Gate 最多 3 轮；Literature 绕行每次 Gate **总共最多 1 次**。删除 Worker 父 H 纠正调用。
- **G4 崩溃后候选复用**：未冻结候选在检查点处记录 `running_h_fingerprint`；重新获得 Gate 时若指纹变化则丢弃缓存候选重新提案，否则可复用。
- **G5 正在执行的 H**：Cycle Claim 已绑定 H 且 Claim 状态非终态（claimed / paused / needs_attention / interrupted / 可恢复 failed）；另含 SQLite 中 freeze 未完成（`freeze_incomplete`）的 H。
- **G6 等待方向唤醒**：`WAITING_RESEARCH_DIRECTION` 只在以下确定性信号变化时重新入队：已提交 Outcome 集合、Scientific Model version、`research_plan.md` sha256、Human 显式 `retry_direction`。全部存活 Worker 均处于等待时发 important 通知（去重）。
- **G7 预算**：保留 `max_cycles_per_worker`；Cycle 编号一旦分配即计入预算。
- **G8 中止 H 状态**：Goal 封存事务中，已冻结但无本 Cycle Outcome 的 H 前沿以 `goal_completed_stop` 原因关闭，不写 supported/rejected。
- **G9 人工引导**：Preferred 不再生效；Human 引导方向 = Research Plan 修改 + operator guidance，后者注入 Proposal Gate 的 Lead 上下文。
- **G10 运行中知识变化**：只检测 Research Plan 变化与本 Cycle 自身 H 被删除/关闭；父 H 或其他前沿变化不触发通知。

## 3. 接口合同（跨包冻结 API）

### 3.1 包 A：`theory_master/goal_terminal.py`（新）+ `science_graph_store.py` + `workflow_goal.py`

```python
class GoalSealedError(Exception): ...          # 不继承 ValueError，避免被宽泛 except ValueError 吞掉

@contextmanager
def science_commit_barrier(research_root: Path, mode: Literal["shared", "exclusive"]) -> Iterator[None]
# 文件 .theory-master/science_commit.lock，fcntl LOCK_SH / LOCK_EX；同进程可重入（不得自锁死）。

def goal_sealed(research_root: Path, graph_path: Path, *, spec: Any | None = None) -> dict | None
# 先读 SQLite task_goal_terminal；无则（仅当 spec 给出时）回落 verified_completed_goal_state，
# 返回值带 "source": "sqlite" | "legacy_receipt"。只读，不在读取时隐式写入。

def assert_goal_open(research_root: Path, graph_path: Path) -> None   # 已封存则 raise GoalSealedError
```

SQLite（SCHEMA_VERSION 不变，`CREATE TABLE IF NOT EXISTS` 追加；只读打开旧库缺表视为未封存）：

```sql
task_goal_terminal(goal_id TEXT PRIMARY KEY, verdict TEXT NOT NULL CHECK(verdict='passed'),
  source_cycle TEXT NOT NULL, sealed_at TEXT NOT NULL, research_plan_sha256 TEXT NOT NULL,
  judge_receipt_ref TEXT, detail_json TEXT NOT NULL)
```

- `ScienceGraphStore.goal_terminal() -> dict | None`
- `ScienceGraphStore.seal_goal(*, goal_id, source_cycle, research_plan_sha256, judge_receipt_ref, detail) -> dict`：幂等；已封存返回既有记录不覆盖；同一事务内关闭所有前沿（G8：无本 Cycle Outcome 的已冻结 H 用 `goal_completed_stop`）。
- `transaction()` 顶层 `BEGIN IMMEDIATE` 后若已封存且连接未设 `allow_after_goal_seal=True` → ROLLBACK 并 `GoalSealedError`。`allow_after_goal_seal` 仅供封存事务本身及非科学派生索引（如 `cycle_memory_index`）使用。
- 锁顺序（全系统统一，防死锁）：`science_commit_barrier` → `canvas_write_lock` → SQLite 事务。
- `_run_goal_completion`（N=1 与 N≥2 同一路径）：独占屏障内运行 Judge + 封存；屏障内图谱不可能变化，删除旧“评判期间版本未变”比较；先 SQLite 封存，再写 Canvas/展示文件（失败可重试，不撤销封存）。

### 3.2 包 B：`theory_master/multi_worker.py` + `worker_migration.py`

```python
class CycleClaims:   # .theory-master/cycle_claims.json, schema "theory-master/cycle-claims/v1"
    def __init__(self, research_root: Path): ...
    def claim_cycle(self, worker_id: str, *, owner_pid=None, owner_start_ticks=None, now=None) -> dict
        # 每 Worker 至多一个非终态 Claim；Goal 已封存 → GoalSealedError；不要求存在 active H
    def bind_cycle(self, worker_id, claim_id, epoch, cycle_id: str) -> dict          # I-04：cycle 唯一
    def bind_hypothesis(self, worker_id, claim_id, epoch, hypothesis_id: str) -> dict # I-04：H 唯一
    def attach_owner(...); def update(worker_id, claim_id, epoch, *, action) -> dict
        # action ∈ heartbeat|pause|resume|release|wait_direction|stopped_goal
    def assert_writer(self, worker_id, claim_id, epoch, *, cycle_id, hypothesis_id=None) -> None
    def reissue_for_resume(self, worker_id, claim_id, epoch, *, now=None) -> dict    # 新 token，保留 cycle/H（I-10）
    def finish(self, worker_id, claim_id, epoch, *, current_pid) -> None
    def running_hypotheses(self) -> list[dict]   # G5：{worker_id, cycle_id, hypothesis_id, status}
    def list_claims(self) -> list[dict]

class ProposalGate:  # .theory-master/proposal_gate.json + proposal_gate.lock
    def acquire(self, *, worker_id, claim_id, epoch, owner_pid, owner_start_ticks,
                wait=True, poll_seconds=2.0, timeout=None) -> str           # 返回 gate_token；FIFO；授予前 assert_goal_open
    def assert_holder(self, gate_token: str) -> None                        # 每次 Lead/Challenger 调用与冻结前
    def release(self, gate_token: str, *, reason: str) -> bool               # 幂等
    def recover_dead_holder(self) -> dict | None                             # 持有进程已死才回收
    def status(self) -> dict
    @contextmanager
    def holding(self, **kw) -> Iterator[str]                                 # finally 必 release
```

- N=1（legacy 单 Runner）使用 `worker_id="single"`，claim 字段可为 `None`，同样经过 Gate（I-11）。
- `BranchClaims` 保留为只读 legacy 读取 + 迁移源；`frontier_snapshot` 不再按 preferred 排序（字段保留展示）。
- `worker_migration.migrate_branch_claims(root)`：旧 H Claim → Cycle Claim，保留 cycle/H、发新 epoch，幂等，旧文件备份不删除。
- `reserve_cycle_number` / `reserve_hypothesis_id` / `canvas_write_lock` 接口不变。

### 3.3 包 C：`worker_runtime.py` + `worker_supervisor_dispatch.py`

- 调度按 `min(资源/账号容量, 期望 Agent 数) − 存活/保留 Worker` 与剩余 Cycle 预算分配，**不看 active H 数量**；`preferred_hypothesis` 参数忽略并记 deprecation。
- Worker 新状态：`WAITING_RESEARCH_DIRECTION`、`STOPPED_GOAL_COMPLETED`（均非 FAILED，Supervisor 不得当故障修复）。
- 等待唤醒按 G6 指纹；调度 tick 调用 `ProposalGate.recover_dead_holder()`。
- `detect_critical_knowledge_change` 按 G10。

### 3.4 包 D：`workflow_qualification.py` + `workflow.py` + `stage_registry.py` + `prompt_stages.py` + `workflow_invocation.py`

- 删除 worker_claimed_frontier 规则、纠正调用、rooted 过滤与 origin 校验；候选可来自任意合理历史 H、可多父。
- Gate 范围：从构建 Lead 上下文起，到 H 冻结检查点写完止；任何异常/暂停 finally 释放。
- **Goal 安全点**：每次模型调用前（`_call` 统一入口）`assert_goal_open`；封存则持久化 Cycle 为 `STOPPED_GOAL_COMPLETED`（`resumable=False`）并退出，证据不删除。
- Lead 上下文新增 `running_hypotheses`（H id、title、hypothesis、decisive_test、cycle、worker、status）与 operator guidance；Research Lead Prompt 写去重规则（避免实质重复的命题与决定性检验；同父 H 不算重复）；删除 Preferred 语义。
- G3 有界重试；连续失败 → Cycle 终态 `no_qualified_candidate`（记录尝试方向、失败原因、证据），释放 Gate，important 事件，返回给 C 进入等待。
- **冻结协议**（跨 SQLite / Cycle 文件可恢复）：Gate 内 + 共享屏障：`reserve_hypothesis_id` → 检查点 `hypothesis_lock.status="committing"` → SQLite 注册（幂等）→ `CycleClaims.bind_hypothesis` → 检查点 `locked` → 释放 Gate。恢复时 `committing` 一律补完或修复；`running_hypotheses` 把未完成者标 `freeze_incomplete`。

### 3.5 包 E：`scientific_model.py` + `workflow_science.py` + `canvas_projection.py`

- 所有科研写入：共享屏障 → `canvas_write_lock` → `CycleClaims.assert_writer(cycle_id, hypothesis_id)` → SQLite 事务；`GoalSealedError` 向上传播，不吞。
- `append_outcome` 以 `(cycle_id, hypothesis_id)` 幂等：重复提交返回既有结果，不重复写。
- CAS 冲突写入持久 `.theory-master/scientific_model_pending.json`（id、cycle、worker、base/current version、原始 update、status `pending|integrated|superseded|rejected`、disposition、resolved_by_cycle）；后续 cycle_synthesis 上下文注入待整合项，Lead 输出显式处置（`pending_dispositions`），校验未处置项不得被静默丢弃。Scientific Model 保存在共享屏障内检查 Goal 终态。

### 3.6 包 F：`__main__.py`、`research_focus.py`、`science_canvas_export.py`、`web/tm_observer/`、设计文档

- `--preferred-hypothesis` 保留解析但无效并提示；preferred focus 通知停用；Canvas/网页 preferred 仅作历史标签。
- Web 展示 Worker→Cycle→H 映射、Proposal Gate 持有者、`WAITING_RESEARCH_DIRECTION` / `STOPPED_GOAL_COMPLETED`；`retry_direction` 操作入口。
- 本合同并入 `多Worker并行科研架构.md`，删除旧 H Claim 权威描述。

## 4. 实施纪律

- 仅静态 / Mock / FakeBackend 测试；**禁止真实模型调用**。真实 N=2 运行由 `TMA-20261020-0097` 另行授权。
- 各包只改自己拥有的文件；共享文件（`contract_stages.py`、`output_validation.py`）只改本包语义相关 hunk。新测试命名 `tests/test_mw_<包>_*.py`；`tests/suite_policy.py` 由协调者统一登记。
- 历史 Claim / Preferred / Cycle / 科学裁决只读兼容，不改历史数据。
