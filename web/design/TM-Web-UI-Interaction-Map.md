# TM Web UI Interaction Map

> 2026-10-16，依据 Web 端路由与客户端代码核对。属于 **Web 视图/操作语义契约**，不改变 TM Runtime、科研 Router、科学 Agent 的权限或状态。TMC-20261016-225e917aa1f2。

## 1. 共同导航模型

- **科研任务身份**只使用 Observer 的 16 位小写十六进制 `observer_id`。Master ID/Cycle/路径绝不可充当任务深链身份。规范 Task Console 是 `/run/<observer_id>/`；Explore map 是 `/research-explorer/?observer_id=<observer_id>`；Supervisor 是 `/run/<observer_id>/supervisor`。
- **明确的父页面**优于猜测上次页面。页面头部的“返回”是安全的任务/工作区父级链接；浏览器 Back/Forward 是用户真实的访问历史，不能靠 cookie 代替。`tm_last_task_console` 仅用于网站首页/服务页的历史任务快捷入口，不是事件来源或 Back context。
- **Incident 属于任务**：从服务端已定位的 `research_root` 匹配已发现的 Run，生成 Task Console / Supervisor / Explorer 返回路径。禁止从不可信 `return=` 参数构造任意跳转；无匹配时退回 Task List。
- **Explorer 的阅读位置**是 `(observer_id, kind, id, anchor)`，其 URL 负责可分享定位；浏览器 `pushState` 记录主动选择，`replaceState` 用于恢复/刷新当前项，`popstate` 恢复选中节点、模式、折叠、相机与滚动。保留既有 `#<encode(kind:id#anchor)>` 以兼容历史链接，后续再考虑标准化路径。节点正文与关联 Markdown 是**不同阅读实体**；点击文档链接才打开文档。
- **视图状态不是科研真相**：浏览器历史/折叠/焦点/滚动只影响阅读。任何刷新不得写入科研 Canvas、改 Stage、重启任务或重复执行操作 API。

## 2. 状态/刷新模型

| 数据/视图 | 当前实际来源与刷新 | 展示/失败语义 | 历史承诺 |
|---|---|---|---|
| Run 列表 | SSR Observer `StateCache`（默认 TTL 5 秒）；点击“刷新”重新导航 | 读到的是 Observer 采样，不是实时进程直连 | 不提供逐秒历史 |
| Task Console `/run/<id>/` | SSR 初始快照；`/api/run/<id>/view` 每 8 秒（后台标签暂停）；前端尽量差量更新 | 标注最近页面获取时间；网络失败保留旧内容并告知。安全控制状态跨 Cycle 变化时仍可能整页加载 | Runtime 数据来源，不是页面历史 |
| Research Explorer | `/api/canvas-md/tasks/<id>/index`、`/document`、`/search`；用户主动“重新读取当前文件” | 显示当前文件读取时间；加载失败明确；继续保留已显示的文档（若有） | **没有 Explorer 独立 30 秒文件版本历史**。Observer/Run 历史不能冒充 Markdown 文件版本 |
| Codex Usage（Services） | 已持久化历史快照首屏；浏览器每 60 秒请求缓存端点，服务端对 Provider 低频查询 | “历史记录 / 已同步快照（可能缓存） / 查询失败保留历史”，年龄优先使用 Provider `queried_at` | 存在后台历史，但每次浏览器 GET **不等于**新 Provider 实时查询 |
| Workflow / Stage Preview | 页面按 `view` / `workflow_version` / `request` 获取静态/已保存状态 | 参数是可分享视图选择，Guide/Detail/Overview 不应暗改当前请求 | 无 UI 自身时间回放 |
| Supervisor / Incident | Supervisor 页面查询 session；Incident chat POST 明确触发，回复等待时页面短轮询 | 等待/已关闭/请求失败是不同状态，不能把空消息当成功 | 会话消息是审计持久记录，不是实时刷新缓存 |

刷新原则：UI 可以由不同频率的**业务源**驱动，但同一页面必须告知“来自哪里、何时取得、当前是否陈旧”。禁止多个互不知情的客户端定时器重复触发同一个资源的 Provider 查询；必要的整页重载只限身份/控制权限变更或现有兼容路径。`StateCache` 的 5 秒不保证每个下游数据源仅更新一次。

## 3. 核心页面 Interaction Map

| 页面/入口 | 页面状态及可点击元素 | 动作 → 页面/API | 返回路径 | 刷新后位置/异常/空态 |
|---|---|---|---|---|
| `/` | 首页（无直接任务选择） | 已知上次 Console 则跳转，否则 `/run/` | 浏览器历史 | Cookie 只作首页快捷入口；目标无效退回 `/run/` |
| `/run/` Task List | `q`、`status`、`page`；任务名链接、筛选、清除、上下页、刷新 | GET `/run/<id>/` 或当前带参数列表 | Task Console 的父级 `/run/` | 筛选参数保留在 URL；无任务/无匹配专用表格空态；**不再整行点击** |
| `/run/<id>/` Task Console | Task/Cycle、Agent、事件、当前状态、额度、操作控件、Explore map、Supervisor、Settings | GET `/api/run/<id>/view`；入口到 `/research-explorer/`、`/run/<id>/supervisor`、`/settings/?observer_id=`；明确操作走 `/api/run/<id>/action` | 父级 `/run/`；二级研究/Events 返回当前 Console | 初始 SSR + 8 秒采样；保留研究阅读/表单的局部状态；Cycle/控制权限变化可能整页重载；网络失败标旧 |
| Console Events/Research | `?tab=events` / `?tab=research`；Stage 选择、复制、事件过滤、返回主屏 | `/api/run/<id>/conversation`、`/api/run/<id>/view` | `/run/<id>/` | URL 保留具体 tab；长文本阅读区不应被常规轮询清掉 |
| Research Explorer（默认 G6） | `observer_id`、图谱模式、折叠、镜头、节点/文档阅读、图谱搜索、前后视图、重新读取 | `/api/canvas-md/tasks/<id>/{index,document,search}`；节点/文档点击修改 URL 浏览器历史 | 来自任务则 `/run/<id>/`；浏览器 Back/Forward 按访问记录 | 保留深链和浏览历史；关联 Markdown 为独立跳转；无权限/缺 Canvas 返回不可用而非空白科研结论 |
| Research Explorer `view=legacy` | 旧 SVG 视图、同一任务和文档模型 | 相同 Canvas/Markdown API 与深链 | `/run/<id>/` | 保留向后兼容的阅读行为；不自动插入关联文档内容 |
| /research-plan/ | Task Research Plan 五字段编辑、必填更正原因、Plan / Scientific Model 独立历史、范围内差异与显式恢复 | `/api/run/<id>/research-plan` GET/POST；`/api/run/<id>/research-plan/history` GET，`?scope=plan|model&version=N` 查看范围内差异和文本 | Task Console / Workspace | 按 observer_id 绑定且校验，冲突 409；只增长变化范围的版本；范围内恢复保留另一部分并校验并发修订 |
| `/run/<id>/supervisor` | Supervisor 状态、Session、Review/trace 入口 | `/api/run/<id>/supervisor`、进入 `/supervisor-session/<sid>` | `/run/<id>/` | 无 Session 与服务端读失败需区别处理 |
| `/supervisor-session/<sid>` | 审计对话、图片、待回复、关闭/解决按钮 | `/api/supervisor-session/<sid>/{message,resolve,close}`；图片 `/image/<id>` | 服务端定位的任务 Console / Supervisor / Explorer；未知归属回 `/run/` | 已关闭只读保留历史；等待回复短轮询；发送失败不消除草稿/假称成功 |
| `/research-chat/` | 会话、模型选择、消息/停止、研究动作 | `/api/research-chat/{session,<sid>/message,<sid>/stop,<sid>/model,<sid>/action}`、`/progress` | Workspace 级导航（不是任意 task-cookie Back） | 草稿/会话与当前请求状态分开；API 错误须保留可见提示 |
| `/workflow-stage-preview/` | Overview/Detail/Guide、版本选择、请求选择、变更比较 | `?view=`、`?workflow_version=`、`?request=`；field notes API | Workflow 上下文内切换，离开后按真实 browser Back | Query 深链需能直接复现；不可用的版本必须 404，不暗切到其他版本 |
| `/architecture/` | 体系架构图、静态页面导航 | 站内 Overview/Detail 入口 | Workspace 导航 | 缺静态构建资源时需明确不可用 |
| `/services/` | Codex Usage、账户切换、重置卡、手动刷新；状态年龄 | `/codex-usage.json`、`/codex-account/switch`、`/codex-account/reset-credit` | 有已验证任务快捷入口则 Console，否则 Workspace | 明确缓存/Provider 查询年龄；失败仍展示 last-good 且标记 |
| `/settings/` | Workspace 与任务设置，`observer_id` 选择、开关 | 相应 Settings 和 Run 控制 API | Workspace / 显式选择的任务 | 无效 observer_id：404，不静默映射到其他任务 |

### Research Plan / Scientific Model 独立版本边界（2026-10-24）

- Plan 四个人工字段与 Scientific Model 各自保存不可变内容快照、时间和原因，独立递增。SDK 更新 Scientific Model 不再生成新的 Plan 版本。
- 历史原始时间戳保留 UTC；列表日期和详情统一转换为 Asia/Shanghai 并明确标注北京时间，避免午夜跨日显示错误。
- 历史窗口通过范围选择器切换；diff、文本、恢复与版本提示均属于所选范围。恢复只更改所选范围，另一范围保持当前内容，并按范围修订哈希处理冲突。
- 原混合 `history.jsonl` 与完整快照不删除；首次读取按内容范围去重迁移，保留旧版本来源编号。直接文件编辑仅在下次受管访问被发现，未知原因不补造。
- Markdown 五字段结构与科学权威不变，旧文本不重新注入已经冻结的 Cycle。机器 Scientific Model 元数据版本与这两套文本历史分别保留。

## 4. 按因果分组的缺陷及落地阶段

1. **导航硬伤（P1）**：Run 列表双重点击；Explorer 使用 `replaceState` 吞掉阅读历史；Incident 无明确所属任务父链；页面 Back cookie/固定父级混用。解决策略：稳定 Observer ID、原生链接、浏览器历史和服务端校验 task owner。
2. **状态硬伤（P1/P2）**：Explorer 没有自己的 30 秒文件历史；Runner Snapshot/Provider 缓存/浏览器请求时间混淆；多个刷新周期。解决策略：显示数据源/年龄/错误回退；**版本历史需要单独的 Snapshot Store 与保留/权限方案，不把 `reload` 称历史**。
3. **交互语义硬伤（P1/P2）**：节点和关联 Markdown 混排；刷新后阅读跳转；空态/不存在/权限失败没有统一组件；Workflow query 状态分叉。解决策略：阅读实体分离、深链可复现、统一空态分类。

## 5. 下一轮未覆盖的真实风险（不应标记已修）

- 尚无全站统一 error/empty/retry 组件；一些 URL 的纯文本 404 仍在。需将 `not_found`、`empty`、`unavailable`、`failed` 作为四种独立状态逐页迁移。
- Explorer 的 **30 秒历史留存**尚不是文件版本存储；需规定快照大小、访问权限、采样速率、保留周期、节点与 Markdown 一致性，不能只在前端缓存 30 秒并冒充权威历史。
- 全站轮询未收敛到一个调度器；目前复用各业务源刷新频率并公开最新读取状态，不应在此重构 Provider 或科研进程。
- `run_detail_html` 是未被当前 HTTP router 引用的旧只读 fact view；当前实际 Task Console 是 `run_detail_body` + `/api/run/<id>/view`。不要为解决“实时状态”而给死路由添加伪轮询。
- 仅代码/Mock 验证不等于真实浏览器端到端验证，CDN 依赖、Tab 恢复和多人同时操作仍需独立测试。

## 6. 2026-10-18 Web UI 修复状态

- **已修复（当前工作树）**：Supervisor 自动轮询会持续重试；详情展开、输入聚焦或 Loquix shadow-DOM 中存在未发送草稿时推迟整页刷新，避免草稿丢失。
- **已修复（当前工作树）**：Services 自动刷新读取共享额度快照，显式刷新和重置卡操作前的实时核验仍走 Provider 查询；QuotaHistory 的丰富展示字段只保留在进程内存，磁盘历史仍只写最小额度字段。
- **已修复（当前工作树）**：Explorer Canvas/Markdown 索引与搜索均设置单次请求总文档读取预算；搜索索引按任务缓存，并通过源文件 mtime/size 变化失效。超预算会明确标记结果不完整，不把部分扫描伪装为完整结果。
- **已修复（当前工作树）**：Explorer 浏览历史使用稳定序号而非被裁剪数组的索引；内存仍最多保留 70 个最近快照，浏览器历史状态可恢复已淘汰的旧条目。
- **仍未实现**：Explorer 的真实 Markdown/Canvas 文件版本历史。浏览器导航历史不等于文件内容版本存档，不能用当前文件重读冒充历史快照。
- **仍未完成全站统一化**：其他页面的 empty/not_found/unavailable/failed 组件尚需逐页迁移；本轮只补充了 Explorer 搜索空结果和部分索引提示。
- **浏览器验证**：新增 opt-in Firefox + geckodriver smoke test，使用标准库 WebDriver 请求，无 Selenium/Playwright 依赖。运行命令：`TM_BROWSER_E2E=1 pytest -q web/tests/test_webui_browser_smoke_20261018.py`。该测试覆盖真实浏览器中的详情展开、shadow-DOM 草稿保护和安全恢复刷新；它不替代 CDN、标签页恢复及多人并发的全站 E2E。
