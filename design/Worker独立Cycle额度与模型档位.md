# Worker 独立 Cycle 配额与预设模型档位

## 分配语义

- 一个 Scheduler 批次的 `max_cycles` 是**每个 Worker 的新 Cycle 分配额度**。例如 `desired_agents=2`、`max_cycles=2`，对应 worker-1 和 worker-2 各有 2 次新 Cycle 分配机会，合计 4 次，而不是共享 2 次。
- `.theory-master/worker_scheduler.json` 持久化 `quota_mode=per_worker`、`max_cycles_per_worker`、`worker_ids`、`cycles_started_by_worker`。只有确实成功创建的新 Worker Cycle 会计入相应 Worker；已持有 Claim 的失败 Cycle 恢复由 `dispatch_pending` 处理，不再消耗新 Cycle 额度。
- 调度器按 `eligible_worker_ids` 过滤已达额度的 Worker；相同 Worker 名可在前次 Cycle 终态后重新使用。额度全部分配后调度器进入 `STOPPED`，**已经启动的科研进程继续完成**。科研 Goal 通过才是 `COMPLETE`，不能把额度用完等同于 Goal 完成。
- 只有当旧 Scheduler 元数据已明确记录 `quota_mode=per_worker`、兼容的 Worker 集合、`max_cycles_per_worker`，且 `resume_batch=true` 时，重启调度器沿用未消耗的批次额度；否则启动新批次。历史共享计数需先核验真实归属，再单次有备份迁移，不自动臆测。
- 共享账号策略下，**既存 Worker 优先沿用其已冻结 Codex 账号身份**，不可因另一个账号剩余额度较高而悄然更换 Worker 的私有 CODEX_HOME；账号暂不可用时跳过该 Worker。

## 档位来源与界面语义

- 预设档位通过 `backend_config.MODEL_PROFILE_LEVELS` 和 `model_profiles/*.toml` 定义：`test` 一档、`codex` 二档、`advanced` 三档。
- Worker 专属预设保存在 `.theory-master/workers/worker-N/model_profile.json`。路由解析先取该预设（否则当前全局默认），再沿原有 task Web route、Worker 独立 role route 覆盖叠加。已冻结 Cycle 不被追溯改写。
- Web「Agent 状态 → MODEL」在模型选择器右侧显示每个角色的档位；同一 Worker 的手工路由覆盖以 `兼容覆盖` 标注，未选择独立预设而已有 Worker 角色覆盖时显示 `自定义`；本轮冻结的模型与新配置不一致时标记 `下轮生效`。
- 档位标记不是重新推断模型能力的算法，也不等于接口确认支持某个预设的模型 ID。兼容覆盖保留独立说明，避免错误声称该模型与原始预设 ID 完全相同。

## 相关回归

- `tests/test_worker_per_worker_cycle_quota_20261018.py`
- `web/tests/test_worker_model_tier_badge_20261018.py`
