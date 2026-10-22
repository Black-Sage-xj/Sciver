# Web Run Operator / 开发落地与审计边界

更新：2026-10-11。关联审计：`TMA-20261011-0046`（M3）和 `TMA-20261011-0047`（M4）。

## 架构

- `Run` 页面由 `tm_observer.run_views` 渲染任务详情，只读监控数据走 `run_monitor`，每 8 秒刷新，不影响控制表单。
- `tm_observer.run_controls_ui` 仅提供显示、用户确认和固定动作请求；`canvas-md` 属于另一 AI，不共享 handler 或科研文件解析逻辑。
- HTTP `POST /api/run/{observer_id}/action` 在已有 session/basic auth 后，严格检查同源 `Origin`、自定义意图 Header、JSON 类型、请求大小与 task ID；所有文件路径从服务器任务索引获得，不接受用户路径。
- `tm_observer.run_control.execute()` 通过固定命令（无 shell）进入独立 SDK worker `theory_master.operator_control`。`tm_observer` 不导入 TheoryMaster 运行时包；worker 调用与 `tm tui` 同源的 `Workflow.request_control`, `set_max_cycles`, `submit_attention_guidance`, `set_attention_takeover`, `set_quota_auto_adjust`, `runtime_manager.launch_managed_runtime`。
- 请求必须携带唯一随机 `request_id`、预期 Cycle、预期 scientific status 和 Runner launch ID。服务端重新读取真实 state/runtime，持有任务目录本地操作锁，先创建 `pending` 幂等记录，再向 SDK 递交控制；即使进程在 SDK 写入后崩溃，也不自动重放潜在危险请求。
- `pause / interrupt / kill` 仅请求 SDK 下一个安全边界执行；返回已登记不表示进程已立即结束。`max_cycles` 下一 Cycle 生效；人工指导只在 NEEDS_ATTENTION 提交且下一次 resume 后使用。`run/resume` 调用 managed supervisor，拒绝重复 owner。
- 浏览器关闭或切换页面不会停止正在运行的科研任务。`canvas-md` 的独立模块与研究文件写权限不在此次改动范围。

## 可能风险和保守处理

- 账号密码远程登录本身不能替代操作授权：现阶段直接复用原 Observer 的单操作者认证及严格 Origin 校验；多用户角色/细粒度权限管理仍未实现，不要把它部署成对公众开放的多用户控制台。
- 通过同一用户身份登录后具有单机操作权限；公网应使用 HTTPS、可信代理、强口令，并按需启用 VPN。不得在无认证的公网地址上暴露写接口。
- 后台 SDK 操作超时或网络断连后，状态可能未定；`pending` 收据使相同请求 ID 不会自动执行第二次。应刷新状态并人工核查，再发新的合法请求。
- 长运行科学工作和 CLI 并发：SDK 管理器的启动锁保持唯一 owner，但页面状态可能过期，客户端看到 409 应重新加载，绝不能自动重复提交。
- 未在正在运行的科学任务上执行写操作，所有写路径只在模拟和 monkeypatch 测试验证。真实生产按钮语义仍应独立验收。

## 当前覆盖

- TUI 监控：Activity、Investigation、Judge、Usage、Events、Logs、Thread、Research、Artifacts，支持快照刷新与有限 tail。
- 控制：Pause、Interrupt、Kill（额外确认）、Max cycles、Attention 一次性指导、Attention takeover、Quota auto adjust、Run/Resume、下次启动的三项开关。
- 测试：SDK managed launch mock，HTTP origin/intent 拒绝，Cycle/launch 版本守卫，任务内持久幂等，请求碰撞，pending 未知处理，Web JS 静态解析，既有 Services/Web Workflow 回归。
- 未实现：多租户细粒度 RBAC、Web 端通用模型路由编排、编辑研究科学状态、Canvas/MD 解析或编辑；这些不被视作 TUI 完成的组成。
