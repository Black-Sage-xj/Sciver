# Codex 账号：Cycle 边界额度及二档位模型路由

## 权威边界

## 二档位账号适配

| Role | Account 1 | Account 2 | Effort |
| --- | --- | --- | --- |
| Research Lead | gpt-6.1-sol | gpt-6-sol | high |
| Challenger | gpt-6.1-sol | gpt-6-sol | medium |
| Investigator | gpt-6.1-sol | gpt-6-sol | low |
| Judge | gpt-6.1-sol | gpt-6-sol | medium |
| Literature | gpt-6-luna | gpt-6-luna | low |
| Reporter | gpt-6-luna | gpt-6-luna | low |

## Provider 额度耗尽后的例外恢复

已冻结 Cycle 平时不切换认证或模型。唯有已持久化 provider_quota_exhausted 并由 Supervisor 选择另一账号恢复时，_restore_resumed_cycle_routes 按新账号的模型上限降低必要型号，记录 runtime_account_failover 和 runtime_hot_role_routes；不更改旧 Cycle 开始时的 runtime_role_routes。保留已提交科研阶段及证据。若跨账号旧线程因认证/服务端拒绝无法安全恢复，仍按实际错误安全停机，不擅自清理科研线程、改写 Hypothesis 或重放 Investigator。

## Codex App Server 统一生命周期（2026-10-15）

- Desktop SSH 的旧引导命令 `codex -c features.code_mode_host=true app-server --listen unix://` 会绕过现有 daemon 并常驻运行，Codex CLI 升级后独立服务器的 executable 可能变成 `(deleted)`；通过 `~/.local/bin/codex` 放置受控 wrapper，将这种独立监听启动重定向到 `codex app-server daemon start`。`~/.local/bin` 必须在远程 SSH 的 PATH 最前面；不可仅修改交互 shell 的 PATH。包装器版本源为 `scripts/codex-managed-wrapper.sh`。
- daemon 与 CLI 必须按受控升级流程对齐。`scripts/codex_safe_upgrade.py` 提供 `--check`、`--sync-daemon` 和 `--upgrade`：升级前检查活动 Codex CLI / Desktop proxy 会话、验证 daemon 归属，使用与账号切换共用的 `.codex-accounts/.switch.lock`；升级后核实 CLI / managed daemon / app-server 版本一致。升级使用 CLI 安装包同步 daemon（`app-server daemon update --from-cli --yes`），不得对有活动会话的环境强行升级。日常升级使用 `codex-safe-upgrade --upgrade`，避免裸 `npm install -g @openai/codex` 留下旧进程。
- 网页人工账号切换先争用同一跨进程锁，检测已连接的 Codex Desktop proxy 和 CLI `exec/resume` 会话；未能证明空闲则拒绝，不终止相关进程或改写认证。通过后验证所有共享运行时 auth 软链、受管 app-server 进程和 Codex 的非生成式 `account/read` 返回的账号身份；任一失败则重新选择原账号、重启 daemon 并验证回滚。不能验证回滚时必须人工介入，不返回成功。
- TheoryMaster 自动配额路由仍只在 Cycle 边界更换其认证选择器、重建自己的运行客户端，不应因这种选路操作重启无关 Desktop app-server。Web 人工切换和 TheoryMaster 配额切换通过现有 `codex_accounts.account_switch_lock` 的文件锁互斥；长期已有科研任务不因部署自动重启。
- 本次仅通过进程/版本探测、模拟事务与非生成式账号身份 RPC 验收；没有额外执行收费 LLM 科研测试。实际有人使用 Desktop 连接时，安全策略是返回占用冲突，而非强制切号。

## 2026-10-18 · 高优先级账号切换与多 Worker 独立恢复

- Web 用户手动切换全局默认账号不依赖正在运行的独立 Worker。必须确认共享 selector 归属并持有 .switch.lock；发现精确匹配的同一用户 Codex Desktop app-server proxy 时，仅对该 proxy 执行 SIGTERM 并核验退出，不使用模糊 pkill、SIGKILL、终止 CLI exec / 正在执行的科研 Turn，也不关闭拥有独立 WORKER_CODEX_HOME 的 Worker 进程。
- 已验证 daemon 可安全重启时重启并核验身份；daemon 缺席、版本不兼容或归属不明时**仅切换 selector**，不得碰未受 TM 管控的服务器，仍须完成新的 account/read 双向身份校验，失败则回滚原 selector。CLI tm codex-account 1|2 仅改变全局默认账号，不重启 Desktop daemon，避免干扰独立任务。
- Worker 模式 share_accounts 默认为 false。显式允许共享后，多个 Worker 可绑定到同一**账号的额度池**，但各自保有单独私有 CODEX_HOME、SDK 客户端、线程、Cycle、分支认领与进程身份；每个账号最多同时承载两个 Worker，并在新增分配前核实账号可用额度。共享额度不等于独立额度，已有 Worker 的真实用量不会重置。
- 若 Worker 因 provider_quota_exhausted 暂停，Worker 专属额度监视器优先选择已恢复的原账号；否则查找有效且有剩余额度的目标账号，在符合共享并发上限后调用 Worker 专属安全 Resume。目标 Worker 的**原进程必须已退出**、Cycle 处于 paused 且可恢复、Claim fencing 身份一致、目标认证和额度均有效；这些条件不满足时保持暂停，不触发新 Cycle。
- 目标账号不同于原账号时先完整备份原 research_state.json，保留锁定的 Hypothesis、decisive_test、科学输出和 H/Cycle 归属；清空原账号的 provider 原生 Thread ID，换用新账号新线程继续同一个冻结 Cycle。既往模型对话、证据和迁移记录在备份中可追溯。**严禁**从目标账号续接另一账号的原生线程。
- CLI 提供 tm workers MASTER status --share-accounts on|off 设置后续共享策略，以及 tm workers MASTER resume --worker worker-N --account 1|2 安全恢复。任务操作接口 set_worker_mode 允许可选 share_accounts；worker_control 的 Resume 允许可选目标账号。
- 两个现有 Worker 正在 RUNNING 时，不进行热迁移、不重启、不修改冻结模型 Turn。额度自动检查和安全恢复只在受控暂停之后生效。
