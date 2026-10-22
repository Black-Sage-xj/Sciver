# TheoryMaster Web Run 控制台设计

> 状态：设计基线 / 逐步落地。2026-10-11。变更依据：用户提出本地优先、`Run` 独立任务列表、选中任务进入 Web TUI，并由另一 AI 实现 `canvas-md` 接口。
> Git 保护点：`tm-run-web-baseline-20261011` → `9bfa5ab2897a30a504ed2e91d03a84009e358799`（仅保护当时已提交 HEAD；其余未提交研究数据和其他 Agent 工作树文件不在这个标签内）。
> 追踪：`TMA-20261011-0041`。本文件本身不是远程控制授权，也不改变 SDK 运行协议。

## 1. 产品边界和用户决策

1. **以本地控制为唯一核心能力**：网站与 TheoryMaster 运行在同一受信任主机，默认在 loopback 提供 HTTP 服务；即使没有公网、AL、SSH 隧道或登录插件，功能仍完整。
2. **公网是部署层插件**：可选 AL Caddy/反向 SSH 隧道或其他代理 + 账号密码身份验证；本地模式和公网模式**共用相同 Run 前端和 API**，不让业务接口依赖代理供应商。远程控制授权应在功能后续开放时作为部署策略单独评估，不能假设“配置过登录”就自动安全。
3. **新增第四个导航 `Run`**（用户明确修改此前仅三页的设计）：保留 `Workflow`（科研工作流设计）、`Architecture`（架构）、`Services`（账号/额度），`Run` 提供运行任务列表和 TUI 式单任务详情。
4. **真实科研权限归 SDK**：网页不是第二个 Runner/科学审判器；不得直接改 Canvas、Hypothesis、Judge、Evidence 或伪造结束状态。CLI/TUI/Web 最终共享相同 Operator 控制实现。
5. **`canvas-md` 预留给另一 AI，当前项目不实现**：见 §8；只约定名称/位置/任务身份/权限边界，禁止在这轮添加可用的路由、实际文件读写或伪造响应。

## 2. 已核对的真实实现

| 功能 | 真实代码 / 状态来源 | 约束 |
| --- | --- | --- |
| 任务发现和列表 | `tool/tm_observer/tm_observer/collector.py`: `collect_state()`, `collect_run()`, `TaskLocation` | 现有只读索引；包含稳定 `observer_id`、`task_name`、`task_root`、`research_root`、`cycle`、`state`、`last_activity_at`、`active_agent`、`tokens_total` |
| 终端 TUI | `tool/theory_master/theory_master/tui/app.py`, `render.py` | Textual 绘制层不搬进浏览器；保留语义和时机 |
| TUI 状态 | `theory_master.observability.collector.FileSource.snapshot()`, `runtime_events()`, `logs()` | 可能从最新 run_state 加入 runtime/research 状态修正；不要把 Observer 粗粒度列表当作完整 TUI 原始数据 |
| 任务生命周期 | `theory_master.runtime_manager.runtime_health()`, `launch_managed_runtime()`, Supervisor/owner/launch lock | 原 owner 检查和启动幂等语义必须复用，不得随意 shell 拼接启动命令 |
| 控制 | `Workflow.request_control()`, `WorkflowStateMixin.set_max_cycles()`, `submit_attention_guidance()`, `set_attention_takeover()`, `set_quota_auto_adjust()` | 停止是下一 SDK 安全检查点提交请求，非 OS SIGKILL；切换设置的生效时机不同 |
| 网站 | `tool/tm_observer/tm_observer/web.py`, `dashboard_ui.py`, `auth.py` | 当前 Observer 有 Cookie/Basic Auth、部分 Codex 操作 API，没有通用 Web TUI Operator API |

任务数较多时不得把所有任务的全部 Events/Logs 加载到列表。所有 UI 文案以真实字段为依据，不将 `OVER` 误显示为 `COMPLETE`；`COMPLETE` 表示最终 Goal 通过。

## 3. 信息架构（四个一级页面）

```text
TheoryMaster Observatory
├── Run                         ← 新增；任务列表和任务详情（默认是只读）
│   ├── /run/                    ← tm ls 风格、分页、过滤、搜索
│   └── /run/<observer_id>/      ← 任务当前 Cycle Web TUI
│       ├── Overview             ← 状态、Cycle、当前 Agent、模型、预算/耗时
│       ├── Agents               ← Lead/Challenger/Investigator/Literature/Judge/Reporter
│       ├── Activity             ← 当前活动、最近交付、错误或 NEEDS_ATTENTION
│       ├── Events / Logs        ← 事件/日志，服务端有界分页或增量推送
│       ├── Research / Artifacts ← 只读科研摘要与产物索引
│       └── Canvas / MD          ← canvas-md 接入占位；另一 AI 开发
├── Workflow                    ← 现有 Agent/SDK 流程架构图
├── Architecture                ← 现有源图
└── Services                    ← 现有双 Codex 账号额度 / 切换 / 重置卡
```

> `/run/` 是用户指定的新页面。旧 `/runs/` 被停用，禁止复活同名旧 Dashboard 业务契约；新路径由 `Run` 负责。不得把现有 `/services/` 的额度卡迁走。

### 3.1 任务清单（tm ls）

- 默认按最近活动排序，**每页 20 条**；可搜索任务名称/ID，筛选运行状态；支持分页而不是把 100+ 行全部铺开。
- 列建议：`Master ID / Task`、最新 `Cycle`、`State`、`Active Agent`、`Last Activity`。可用状态不等于可运行状态；仅以任务的 canonical research root 作为身份源。
- 导航使用已经存在的 `observer_id`（根据 canonical `research_root` 获得的固定、跨重复 Master ID 的身份）；Master ID 是显示/检索标签，**不能单独作为控制授权键**。
- 清单中的状态与 TUI 任务详情的状态可能来自不同时间的快照；显示更新时间、无法获取/过期状态，不用颜色掩盖冲突。

### 3.2 单任务 Web TUI 详情

**2026-10-11 用户最新确认：只借鉴原 `tm tui` 的区域布局、信息顺序与科研渲染逻辑，不继承终端 UI 的视觉风格。Web TUI 的颜色、字体、圆角、边框、面板和响应式结构统一遵守现有 Observatory（Workflow/Services）网页设计规范。** 不再采用独立于原布局的「指标卡片 + 六 Agent 卡片 + 九页签」，也不把原终端黑底/高亮青色线框/全等宽字体照搬到网页。

主界面严格沿用 `theory_master/tui/app.py:AppImpl.compose()`：

```text
┌────────────────── TheoryMaster · Task ID / Cycle ID / 四项开关 ───────────────┐
│ Process / State / Cycle / Phase / Elapsed / Started / Cost                   │
│ AGENT/STAGE      MODEL                 COST      TIME       STATE           │
│ Research Lead ... Challenger ... Investigator ... Literature ... Judge ... │
├─────────────────────────────┬──────────────────────────────────────────────┤
│ CURRENT ACTIVITY            │ INVESTIGATION                                │
│ Agent/State/Purpose/Output  │ Attempt/Round/Inv tokens/History             │
│ LAST / HANDOFFS             │ JUDGE                                        │
│ Time-ordered history        │ Branch verdict / Reworks / Objections       │
│ Last action (pinned)        │ USAGE DETAIL                                 │
│                             │ Current turn / Cycle / Project totals        │
├─────────────────────────────┴──────────────────────────────────────────────┤
│ [1] Logs [2] Thread [3] Events [4] Artifacts [r] Research [p/i/k/c/a/u] [q] │
└────────────────────────────────────────────────────────────────────────────┘
```

展示字符串直接调用 **同一份** `theory_master.tui.render` 内的 `render_identity/header/workflow/activity/last/last_action/investigation/judge/tokens`，数据来自原 `FileSource.snapshot()`。为了不让 Observer 导入科研运行时，使用固定 argv 的**只读独立 worker** `theory_master.tui.web_bridge`，Observer 侧 `run_tui_bridge` 仅接收已发现的规范研究目录。旧 `cycle-*` 使用相同渲染器兼容，不复制另一套 Web 计算规则。

次级 `Logs/Thread/Events/Artifacts/Research` 用快捷键或底部按钮打开另一屏；退出次级屏幕用 `Esc`、关闭 TUI 用 `q`（网页是返回 Run 清单，不停止后台计算）。`p/i/k/c/a/u` 定位到经过确认的安全操作面板，不能一按即执行。为了避免终端限制移植到网页，屏幕较窄时左右两列顺序堆叠，日志和完整事件有尾部读取与大小上限，浏览器页面刷新约每 8 秒一次。Canvas/MD 由另一 AI 负责，作为 TUI 下方的独立关联资源，不塞进原 Textual cockpit。

**历史设计（TMA-20261011-0053；下方 0056 节更新了保存时机和界面）**：在六 Agent 原表格的排列顺序上，将原 TUI 的 `model · effort` 分成可编辑 MODEL 下拉框和独立 EFFORT 列，每行有明确「保存」，并在修改与当前配置不同时显示「下次启动生效」。模型列表由 SDK `MODEL_PROVIDER_MAP/MODEL_PROFILES` 派生、effort 仅允许 `low/medium/high/xhigh`。写入只通过受保护的 `POST /api/run/{observer_id}/action`，使用当前 Cycle、状态、Runner launch ID 与 request_id 守卫，SDK Worker `save_task_role_override` 原子更新研究根目录 `.theory-master/web-role-routes.json`；`resolve_routes` 在**下一次 Run/Resume 构造 Runner**时读取它；当前 Agent 已创建的 thread 完全不热更。CLI 明确 `--model/--route` 比网页任务偏好有更高优先级，其他任务不受影响。

Process 的 `RUNNING/ACTIVE` 以绿色显示、`STOPPED/FAILED` 红色、其他状态用警示/中性色；这是**进程状态**而不是科研 Cycle 是否成功的判断。Cycle 摘要保持原来的已运行/最大 Cycle 信息，同时提供「修改上限」按钮，打开并聚焦已经受 SDK 校验的 `Workflow.set_max_cycles` 真实输入；新上限在下一个 Cycle 边界被 Runner 读取，不能混同模型的下次启动生效时间。

`NEEDS_ATTENTION` 与 `WAITING_QUOTA` 在原 `render_header` 内按真实状态显示，不把 Supervisor 等待误称成任务终结。原 CLI 的 Rich/Textual 边框不再模拟；Web 用 `--tm-panel/--tm-border/--tm-text/--tm-muted` 的圆角面板、指标格、原六 Agent 的语义 HTML 表格（不是六卡片）与中性快捷键键帽，普通文字沿用全站无衬线字体，仅需要对齐的用量明细等保留局部等宽。科研字段、数值与阶段身份仍由原 `render_*` 唯一产生，Web 只从原渲染字符串拆分语义行和列，不另外计算或推断科研信息。

## 4. 分层 / 模块

```text
浏览器 <Run 页面 / JS>      CLI Textual TUI
           │                   │
           ▼                   ▼
       Observer /api/run/*  共享 Operator 契约
           │                ▲
    Read projection adapter     │
           │           Workflow / runtime_manager
           ▼                │
   原 TM run_state / runtime_events / orchestrator.log
           ▲
      原 SDK Runner 和 Supervisor（唯一执行者）
```

建议模块隔离（名称按落地可调整）：

- `tm_observer/run_views.py`：**只读**任务列表和详情 HTML；对所有科研/日志字符串做 HTML escaping。
- `tm_observer/run_projection.py`：把已发现、允许访问的 research_root 映射为可视化快照。严格基于 discover_tasks/collect_state 的白名单解析，不接受 HTTP 自由文件路径。
- `theory_master/operator_control.py`（后续阶段）：SDK 级共享控制接口，CLI/TUI/Web 复用，实施动作时重新校验 Task/Cycle/Launch 身份。
- `tm_observer/run_api.py`（后续阶段）：校验受控命令、身份和版本后调用共享接口；不从浏览器发送任意 CLI 字符串。
- `canvas-md`：**独立模块与路由，另一个 AI 拥有，不归 Run 实现者维护**。

## 5. 操作语义与一致性

| TUI 能力 | Web 表现 | 写权限阶段与不变量 |
| --- | --- | --- |
| 快照/Agent/成本/Events/Logs | 初期只读 | 分页/增量读取，不允许访问任意路径 |
| `pause` | 高可见确认按钮 | P1：安全边界生效，不得声称“已立即停止” |
| `interrupt` | 二次确认 | P1：仅 SDK 控制请求，不能 OS kill |
| `kill computation` | 单独高级危险控制 | P2：再次确认，仍然按当前 SDK 安全控制语义 |
| `max_cycles` | 输入整数，明确下一 Cycle 边界生效 | P1：需权限、正整数检查、冲突保护 |
| `submit_attention_guidance` | NEEDS_ATTENTION 专用输入 | P1：一次性消费，关联准确 Cycle/Attention code，不能作为永久 Prompt |
| `attention_takeover` / `quota_auto_adjust` | 明确可热切换 | P1：真实设置 + Supervisor 生效提示 |
| `error_repair` / `cycle_audit` | 下次 run/resume 设置 | P2：当前 TUI 是显示项，不应虚构实时热开关 |
| `run` / `resume` | 有界生命周期操作 | P2：复用 `launch_managed_runtime()` 锁和 active 检查 |
| TUI `q` / 浏览器关闭 | 脱离观察 | 浏览器关闭**绝不停止 Runner** |

服务端 POST 必须携带 task 的稳定 `observer_id`、最新的 `cycle_id`、`launch_id`（有运行 owner 时）、期望状态/版本、唯一请求 ID；拒绝过期页面导致的误操作，成功响应区分 `accepted/requested/applied`，不直接伪报任务停止。

## 6. 两种部署方式（业务逻辑相同）

1. **Local-first（基础能力）**：服务绑定 `127.0.0.1`，本机浏览器直接访问。认证模式可配置为 trusted-local，且限制为 loopback；不需要公开 DNS、代理或 SSH。
2. **Remote（可选部署适配）**：可在外层接 HTTPS 反代 / 私网穿透 / Tailscale / 认证网关；当前用户选择 AL→反向 SSH→本机 Observer。Remote 必须提供身份认证、会话与请求来源保护，写操作阶段还需授权、CSRF 防护、操作审计和独立确认；**不能**因为本地默认信任而让公开入口不经验证直接写科学任务。
3. **部署分离**：不在 SDK 中硬编码域名、云服务商、IP；是否远程访问由部署适配层决定，不影响 URL 业务含义和科学状态。

## 7. 分阶段交付与验收

- **M0：基线 / 设计**：Git annotated tag 保护现有 HEAD；建立设计本文及审计项；不修改其他 Agent 的研究工作区。
- **M1：Run 列表 + 详情只读骨架**：新增第四入口 `/run/`，单任务详情使用稳定 ID；20 条分页、搜索、状态过滤；可展示来源明确的 TUI 顶部指标；状态和账号逻辑无写入。需要验证至少多个重复 Master ID 的正确导航。
- **M2：与真实 TUI 对齐**：Agent、Investigation、Judge、Usage、Events、Logs、Research、Artifacts 的显示和更新；限流、尾随、数据脱敏、缺失数据处理。逐字段校核 FileSource 与 Web 结果。
- **M3：受控操作**：共享 Operator 实现，Pause/Interrupt/Max cycles/Attention guidance/两项热开关；并发冲突、幂等、权限、CSRF、确认和审计验证。
- **M4：生命周期控制**：Run/Resume、高风险 Kill、异常恢复、多任务并发和 remote 部署验证。保证 GUI 不改变 SDK 行为。

M2 现行规范：**布局结构遵从 Textual，视觉样式遵从全站 Observatory**；功能语义与数值以原 TUI 的源数据及 render_* 为准。次级视图为网页面板和工具栏，科研控制及 Canvas/MD 保留各自的既有功能边界。每阶段 Git 单独提交、单独回归、TMA 审核，切勿重置其他 Agent 的未提交文件。

## 8. `canvas-md` 接口预留 / 外部 AI 分工（本轮不得实现）

**正式保留的集成名称：`canvas-md`**。在 Run 详情保留 `Canvas / MD` 入口（占位，未接入时显示“待集成”，不能假装返回内容）。接口定义由另一 AI 负责实现在独立模块，建议由下列路由命名空间承载：

```text
/api/canvas-md/tasks/{observer_id}/...   ← 保留前缀（没有 handler，当前必须返回 404）
```

为避免接口开发过程中冲突，约定跨团队最小契约：

- 传递 task `observer_id`，由服务端解析到可信的 **canonical `research_root`**，不接受用户传入文件绝对路径或 `../` 路径；处理 `.canvas` 和 `.md` 时要校验允许范围。
- 该模块可以读取用户任务的 Canvas 和 Markdown；目录索引、内容模型、写入权限、预览/编辑格式、历史/冲突解决，由负责 `canvas-md` 的 AI 与用户确认后决定。**Run 开发者不在本轮定义或抢实现读写协议**。
- 与 `Run` 的接缝：只需要任务标识、规范化的权限上下文和可插拔的页面入口。未来嵌入 iframe/组件/独立标签都可以，不能要求 Run 业务依赖 Canvas 读取才能工作。
- 模块权属与代码目录应保持可分离，避免两位 AI 同时修改 `web.py` 路由总表。建议另一 AI 独立建立 router 模块后再一次性集成/测试；本轮**不预占 Python handler、文件解析器或数据库**。
- SDK Canvas 的科学写权限独立于该接口：预览文件不表示获得修改 SDK 图结构、Hypothesis 继承或 Judge Decision 的权限。

## 9. 风险与回退

| 风险 | 保护方式 |
| --- | --- |
| Master ID 重复或旧 Task 目录迁移 | 先验收 task `observer_id` / canonical root；失效链接显示 404，不推断替代路径 |
| 任务状态与 Runner 状态不一致 | 区分 snapshot 时间、Cycle 状态、PID/heartbeat；控制时重新检查 owner |
| CLI、Web、自动 Supervisor 竞争 | 复用 launch/owner lock；控制请求需要幂等/expected version |
| 用户误以为网页关闭结束运行 | UI 明确 detach；任何停止必须是独立显式确认动作 |
| 日志中含密钥/路径/私有科研内容 | M2 加长度限制和字段脱敏，远程部署逐项评估 |
| 浏览器经公网控制本地研究计算 | remote 为可选增强；同业务接口的认证授权策略与 CSRF 写保护不能缺席 |
| 两个 AI 对 `canvas-md` 的归属重叠 | 在文档层预留，运行时实现与协议由另一 AI 维护，本阶段不开发 |
| 回归或性能变差 | `tm-run-web-baseline-20261011` 回滚定位；分阶段可单独撤销 Git commit，保留未提交他人工作区 |

## 2026-10-11 · Run UI 交互审查与调整（TMA-20261011-0055）

这次修复用户报告的 NEEDS_ATTENTION 重复截断、开关与模型默认值难辨、底部操作重复、Events 难以阅读和 Agent 颜色缺失等问题。以下是当前已落地的设计规范，不改变科研状态或 SDK 判决权限。

**异常与恢复**：旧终端 render_header 对 Reason/Action/Resume 使用 76 字符裁剪，网页不能重复引用这段被裁剪文字。新页面直接读取 FileSource.snapshot() 保存的完整 Attention Reason、Requested action、Resume behavior 与 Takeover，并只保留一份可展开说明。原有文本命令 theory-master resume <cycle> 不一定适用于任务模式，网页不再展示，仍由既有的安全 Resume 接口处理。Process STOPPED 不等于科学 FAILED。

**四项设置**：在任务主体下方单独的卡片中展示。错误排查、自动审计：保存为任务级下一次 Run/Resume 默认值；Attention 接管、额度自调：沿用现有 SDK 的热设置。所有 Web 设置动作仍使用登录、同源检查、Cycle/状态/launch 身份校验及持久幂等。模型选择必须来自真实调用，而非人为发明：
- 错误排查：现行 SDK 登记异常并安全停止，无独立模型调用，不提供模型选择。
- 自动审计：Cycle 结束后启动独立 Codex 审计窗口；允许任务级指定模型，默认继承 Codex CLI 当前配置，也能恢复默认。
- Attention 接管：SDK 内部 Attention Reviewer；允许选择模型，当前代码默认 GPT-6 Luna (CodexHTTPS)。
- 额度自调：确定性账号控制器，不调用 LLM，也不提供虚假的模型选择。

持久化文件为任务研究目录下的 .theory-master/web-operator-preferences.json；模型只影响后续对应内部服务调用，不修改正在执行的 Agent 线程。六个科学 Agent 的模型配置属于另一套原有任务级路由。

**主屏与 Events**：继续沿用 Textual 的信息布局与 Observatory 的统一网页视觉规范。底部仅保留 Logs、Thread、Events、Artifacts、Research 内容入口，重复的暂停/中断/终止/最大 Cycle/Attention/Quota 按钮移除。高级控制区准确解释三种停止的状态变化：Pause -> paused、Interrupt -> interrupted、Kill -> interrupted/computation_killed。三者在安全检查点生效，均不发送操作系统 Kill 信号。

Events 读取当前 Cycle 的只读 runtime_events.jsonl 最近 256 KiB，最多展示 100 条已分类的事件；按原始顺序呈现，并按 Agent、事件类型、内容关键词筛选，长文可展开，无用的复制全部按钮已移除。处理顺序为先过滤有效事件、后施加显示条数上限；否则会错误丢弃早期 Lead 等有意义内容。六个 Agent 拥有一致的专属颜色，Agent 表格和 Events 共用：Research Lead 蓝、Challenger 黄、Investigator 紫、Literature 青绿、Judge 玫红、Reporter 绿。系统事件使用中性灰。当历史源超过尾部读取限制时，界面明确注明不完整，不宣称全历史/完整会话。

**本次尚未实现、后续应单独评估**：跨 Cycle 的完整事件历史分页与科学对话索引；Supervisor 心跳/页面数据滞后的显著提示；Need Attention 代码分类及具体恢复前置条件提示；模型价格与当前账号实际可用性/Provider 配额。不能把这些尚未实现的能力写成已经落地。

## 2026-10-11 · Web Run 模型自动保存与下一 Cycle 生效（TMA-20261011-0056）

本节更新 0053 的“每行保存按钮与下一次 Run/Resume 生效”方案：Agent 表格保留 MODEL、EFFORT、COST、TIME、STATE，取消末尾操作列。未配置任务级覆写的模型与 Effort 后显示“默认”，有配置且与运行中模型一致时显示“已设置”，当前 Cycle 尚用旧模型时显示“已设置 · 下一 Cycle 生效”。改变模型或 Effort 立即弹确认框，说明仅从下一新 Cycle 生效。取消或请求失败恢复原值；只有确认才使用现有带任务与启动守卫的 SDK set_role_route 原子写入文件。

Managed Runner 的 Workflow 支持一个 route_resolver 参数，在新 Cycle 建立之前解析任务级路由；更新 Workflow、CodexBackend、标准 B-layer SemanticBackend、Collector，保持命令行显式 model/route 最高优先级，旧 Agent turn 不变。无法读取有效配置则不允许静默创建新 Cycle。FakeBackend 与未提供 resolver 的 SDK 流程不受影响。

同一 Cycle Resume 必须冻结路由。新 Cycle 将 runtime_role_routes 写入该 Cycle 的 research_state.json，断点恢复使用当时的 model/provider/effort，而非本次新修改的任务配置。历史 Cycle 未保存 runtime_role_routes 时，从 run_state.json 的六个 Agent 信息尝试恢复；缺失时不推断。恢复旧 Cycle 后开始全新 Cycle 时再次重读任务路由。升级前已启动的旧 Python Runner 未加载新代码，需要重新启动/恢复成新 Runner 才能使用跨 Cycle 热刷新，网页不能保证旧进程实时获得这一能力。

Started 与 Cycle start 将 Textual 产生的 MM-DDTHH:mm:ss 原样更换分隔符为 MM-DD HH:mm:ss；无时区计算。Research Explorer 通过选中的 Run observer_id 进入时，隐藏上方重复的“科研任务 Master…”选择器，仍由同一 Canvas-MD API 读取这一任务；直接从导航进入 Explorer 则保留跨任务切换。没有改变其他 AI 负责的 Canvas/MD 数据协议。

## 2026-10-11 · Events → Stage Conversation 与 Rework Context（TMA-20261011-0059）

**用户确认的交互**：Run 详情仍使用 `?tab=events`，但 Events 从逐条扁平审计事件升级为按真实执行顺序分章的 **Stage Conversation**。输入气泡靠右，模型消息与工具活动靠左；Stage 之间显示独立 SDK 路由/资格/异常事件，不混进 Agent 回复。首次进入默认打开首个 Stage；其余 Stage 可单独展开，长 Prompt 默认摘要、点击展开全部。每 Stage 显示 Agent 专色、Invocation ID、Thread ID、时间、阶段状态、消息数；普通/详细/原始审计三种阅读模式。保留 Logs、Thread、Artifacts、Research 原入口和其他 Task 控件。

**真实数据源**：只读 `research/<Cycle>/runtime_events.jsonl`（AGENT_STARTED、AGENT_INPUT、LLM_MESSAGE、TOOL_ACTIVITY、SKILL_LOADED、TOKEN_USAGE、AGENT_COMPLETED、Turn 等）、`invocations.jsonl`（Invocation prepared/running/completed/committed/interrupted、execution_mode、predecessor）及 `events.jsonl`（SDK role/qualification/Judge/rework/transition 事件）。用 `AGENT_STARTED.invocation_id` 识别章节；一个 Stage 内存在多个 Turn；本 Cycle 多次进入同一 Agent 仍是多次 Stage Round。Stage 状态以 Invocation Ledger 为权威，`LLM_TURN_COMPLETED` 不代表 Stage committed。部分历史 Invocation 没有运行时 `AGENT_STARTED` 时可以展示「只有 Ledger 的 Stage」，不伪造消息。Tool stdout/stderr 在源中未落盘则提示不存在，不补写内容。原始事件仍由 SDK 独立写入，网页只读投影。

**Rework**：重复进入 Investigator 或其他非 Judge Agent 时，先寻找当前 Stage 与该角色前次 Stage 之间实际登记的 Judge 裁决。明确记录的 Rework 与 Judge `role.completed.output.objection.requested_resolution` 分别标注「确认 Rework」与「Judge 记录了待解决问题，但 Rework 状态未单独确认」。新回合顶部显示 Judge 的原文 requested_resolution 与回合来源链接。真实的相同 `thread_id` / `resume_thread_id` 表示原生 Thread 连续；不同 Thread 表示新 Thread / Handoff；缺失时标为无法确认，不凭页面可见历史断言模型持有全部上下文，也不对压缩记录作无据判断。Judge 自身再次出现不因先前异议误称为 Judge Rework。

**只读 API 与性能**：使用 `/api/run/<observer_id>/conversation?mode=index&cycle=<Cycle>` 提供 Stage 目录和跨 Stage 的 SDK 节点，`mode=stage&cycle=...&stage=<invocation_id>&cursor=<offset>` 分批返回每 Stage 45 条实际消息，`mode=sdk&cycle=...&cursor=<seq>` 取指定 SDK 原始事件供深度审计。`cycle` 与 `stage` 必须分别匹配受控任务研究目录内的 Cycle 名和已索引 Invocation ID，不接受用户上传的文件路径。所有路由继承现有 Observatory 登录会话验证；输出文字必须转义并使用既有脱敏规则。原始文件逐行解析，32 MB/每源、每行 2 MB、35k 记录为**明确的安全上限**；超过会警示未完全覆盖，不伪称全量归档。支持按 Cycle/Stage 深链接、Agent 筛选、Stage 关键词搜索。没有修改科研任务、Canvas/MD 或其他 Agent 运行行为。

**已知限制（后续独立审查）**：长文件采用有界前段扫描、未做到基于文件偏移的超大文件索引；模型输出为安全文本和代码块展示，尚没有完整受控 Markdown/LaTeX 数学排版与工具 stdout/stderr 全量持久化；不同 Cycle 间的 Rework 尚未自动跨 Cycle 关联；与 Canvas 中 Hypothesis/Decision 节点的定位跳转由 Canvas/MD 的另一模块负责。不能把无法确认的上下文视为真正保留的模型 Token。

## 2026-10-11 · 当前 Cycle ID 实时身份同步（TMA-20261011-0061）

原 Web Run cockpit 将 `Task ID N / Cycle ID M` 放在 `.tm-tui-titlebar .tm-tui-identity`，而浏览器的 8 秒轮询只替换 `[data-tui-pane]` 和进程状态徽标；即使服务端已经读取下一个 Cycle，页面标题也会一直停留旧 Cycle。Stage Conversation 最初为防止已展开的科研对话被覆盖，甚至整体跳过后台轮询，因此在该阅读模式也不会更新运行身份。这是前端 UI 与只读投影同步缺口，不是 SDK Cycle 身份重复分配。

新规则：成功获取受现有 Observatory 登录保护的 `/api/run/<observer_id>/view?tab=...` 后，无论主屏还是 Stage Conversation，都必须增量更新顶部任务身份文字、运行状态徽标、`data-tui-cycle`/status。Stage Conversation 的已展开 Prompt / Agent / Tool / SDK 原始审计正文严禁因后台轮询而重建或折叠；若当前任务进入新 Cycle，但页面正在阅读旧 Cycle，则显示“当前任务已进入 Cycle X，正在阅读历史 Cycle Y”和“查看当前 Cycle”入口，保持用户所选旧 Cycle，不能暗自改换正在审查的历史。后台 StateCache TTL 约 5s、前端轮询 8s，故这是秒级*最终一致*更新，并非无延迟实时推送；网页切换回前台后从下一次轮询继续检查。HTTP 失败时保留旧视图，不产生推测性的 Cycle ID。此修改仅影响 Observatory 的只读显示与前端交互，完全不影响科学 Runner、Canvases、SDK 的运行或控制。

## 2026-10-11 · Observatory Workspace 导航栏收起按钮（TMA-20261011-0062）

在统一 `dashboard_shell` 的品牌区右侧新增可聚焦的收起/展开按钮。桌面默认展开，导航宽度 246px；用户点击收起后变为 72px，仅保留五个页面的图标，每个链接仍有可见浏览器 Tooltip、明确的 `aria-label` 以及原 URL，主体 `tm-main` 同步扩大。再次点击可展开。存储键 `tm-observer-sidebar-collapsed-v1` 使用浏览器本地 localStorage，跨 Run、Research Explorer、Workflow、Architecture、Services 保持状态；本地存储不可用时也能在当前页面正常开关。按钮通过 `aria-expanded`、动态 `aria-label`、键盘 focus 样式维护无障碍交互。

小于等于 900px 时保持原有横向导航且隐藏收起按钮；Workflow 的 `stage-height-focus` 全屏模式即使存有 collapsed 偏好也保持 `margin-left:0`。这是纯浏览器显示设置，不生成网络请求，不改变科学任务、连接器、Canvas/MD 或任何运行配置。

## 2026-10-11 · Stage Conversation Agent 专色、生命周期和 Canvas 更新（TMA-20261011-0063）

视觉：六个科研 Agent 各自固定专色，Stage 容器的左侧和右侧同时使用该颜色画两条贯穿整个 Stage 的竖线；Stage 标题、模型输出边框、工具调用边框与生命周期标签均沿用所属 Agent 颜色。修复之前 Stage 容器的灰色变量覆盖角色色的 CSS Bug。所有真实 AGENT_INPUT Prompt 统一显示在右侧蓝色气泡（不随 Agent 专色变色），模型消息和工具活动在左边，手机端保留蓝框和双侧 Stage 竖线。

生命周期：LLM_TURN_STARTED、LLM_TURN_COMPLETED、AGENT_STARTED、TOKEN_USAGE 等通常没有正文，因为它们表示事件而非消息。使用带角色、Stage、Turn、时间及真实 Token 计数的小型过程节点显示，隐藏原来误导性的“事件没有持久化正文”和泛化为 AGENT 回复、SDK 生命周期的称呼。Invocation 最终 state 仍以 Invocation Ledger 为准，Turn 结束不等于 Stage committed。

后续 Stage 与 Rework：真实 Cycle 58 的 Research Lead frontier_proposal → cycle_synthesis → frontier_maintenance 是不同 Stage，其中 cycle_synthesis 使用新的 Thread，不能因为同一角色再次出现就推断是 Judge Rework 或显示大段 Context Handoff。只有存在可信 Judge rework/requested_resolution 时，才在 Rework 上方显示相应的上下文衔接卡。相同 Stage、复用旧 Thread 但无 Judge 异议，仅显示简短「相同 Stage 再次调用」提示。

Canvas 结构化输出：当实际 LLM_MESSAGE 或 AGENT_COMPLETED 正文为可解析 JSON，且包含 updates 数组（每项提供 node_id，可能有 frontier_status、reason），展示为左侧的 Canvas 节点维护清单，逐项说明节点、实际状态和原始原因，点击可展开完整原文。所有不可信文本必须 HTML 转义；仅用于阅读解释，不能改变 Canvas 文件或 SDK 决策。

记录覆盖：运行日志源逐源读取有上限 32MB、每条记录上限 2MB、最多 35k 个事件。Stage 对话每次显示 45 条并能继续加载，现在界面提示已加载 X / Y 条，是否还有分页，以及当前源是否报告截断。正文超出接口限制时也应有警告。Cycle 58 的 runtime_events.jsonl 约 337KB、未触发文件读取上限，但现有 TOOL_ACTIVITY 一般仅存命令等调用参数，并非完整执行 stdout/stderr；工具卡按需显示这条数据边界提示，不得假称有工具结果。保留原始字段的可展开审计。

## 2026-10-11 · Agent 费用与 Token 分离展示（TMA-20261011-0064）

用户观察到 Web Run Agent 表中多数 COST 是 —，并且没有 Token 列。经检查 Master1 的 run_state.json，当前 Cycle 各 Agent 的 tokens_in/tokens_out/tokens_total 和缓存输入、推理输出事实上已完整记录；跨 Cycle 还有 project_agents、project_tokens_total。Web 原来只把 Textual 固定列宽的 COST/TIME/STATE 文字解析到表中，费用和 Token 完全混淆。

新增的只读结构化桥接 role_usage / usage_totals 从既有 FileSource.snapshot 读取项目累计与当前 Cycle 的六角色输入、输出、总 Token、缓存输入（是输入的子集）、推理 Token（是输出的子集），以及美元费用原始金额、币种、来源、置信度与已计价调用数量。Web 表格新增 TOKENS（累计）独立列、当前 Cycle 副数、可展开输入/输出/缓存/推理明细。顶部汇总项目累计和当前 Cycle Token、可计算的部分费用及已计价调用覆盖率。每8秒自动刷新时保留已打开的用量详情。

费用规则：记录有金额与合法币种才展示金额并注明估算；已发生调用但金额缺失标为“未计价”，没有调用为 —；Token 未记录不得显示为0。Master1 Cycle59 所用主要 CodexHTTPS / gpt-6.1-sol 具有真实 Token，但缺乏有效 pricing_rule_id，cost_records.amount=None、source=unavailable。项目估算费用仅覆盖 6/29 次调用，Reporter 的 ~$0.011 绝不能解释成所有模型总消费。本次不调整科学 SDK 计量、模型路由或定价规则；若将来要完整计费，需要单独验证 Provider 的价格表/API、账号套餐与价格有效期，不能凭 Token 随意推算美元费用。

安全：模型 Token 和费用均通过独立非负数值校验，未知字段和未记录值不伪造；原始 TUI render_workflow 文本仍保持权威同源（不改其固定排版），只有 Web 自身新增计量展示层。客户端只读 API 继承登录认证与原有路径约束。没有运行任何科研任务控制操作。

### 2026-10-11 · Stage Conversation 紧急回归修复（复用 TMA-20261011-0063）

生产中打开 Cycle58 Stage 时抛出 Cannot read properties of undefined (reading 'role')，直接根因是本次配色及生命周期重构把 eventCard 改为三个参数（item,density,stage），但真正 showStage.load 的 items.map 调用仍只有前两个参数。既有单元测试只直接调用 eventCard(...,stage)，或验证 JS 静态语法，未执行真实 Stage 展开的 lazy fetch 路径，因此未发现该集成错误。现在 showStage 在每一页消息映射时显式传递 stage，所有 Agent 标签与过程事件从其所属 Invocation 中取值。

另一个可见性回归：先前使用 3px Stage 左右边框和泛用变量继承，颜色条过细且在尚未展开对话时不明显。现在 Stage 容器左右都使用 6px 实色边框，并为六科研 Agent 在 .tm-stage-section.tm-role-* 上指定明确专色，贯穿完整 Stage（collapsed/expanded），Prompt 仍独立使用右侧蓝框。这样不会因为内部表格/标记改变变量或单个气泡是否加载而消失。

新增真实前端交互集成测试：以原生产 JS 的 mount / showStage / fetch 路径模拟一个 Research Lead Stage 首次读取及下一页读取，明确断言右侧 Prompt、生命周期角色和 Stage、左侧模型回复与工具调用都正常显示，且加载进度正确；测试必须在函数签名或映射实参不匹配时失败。另检查六角色容器 CSS、双边竖线宽度和 Prompt 蓝框。仅 Observer 展示层变化，不涉及运行中科研 SDK、模型路由或日志源。

## 2026-10-11 · Stage 脚本升级、Token 展开热区和 Events 专注模式（TMC-20261011-8cd271aa5eff）

**浏览器诊断**：旧版页面通过 history.replaceState + Fragment 直接切换 Events，不重新执行 Stage Conversation 内联 JS，因此原标签页可能一直保留先前带 stage.role 报错的渲染代码。Firefox 157 Headless/geckodriver 使用真实登录会话直接打开 Cycle58 的 Stage frontier_proposal，实测成功加载 26/26 条事件、无报错、左右两侧 6px 实际角色色。服务器页面设置 Cache-Control no-store，问题不是可靠确认的公网 HTTP 缓存，旧标签内存脚本仍是合理风险。

**修复**：进入或离开 Events 时使用整页导航，确保脚本按当前部署重新执行；其他普通监控标签仍保持现有局部切换。eventCard 对意外遗漏的 Stage 参数保底用事件元数据展示角色、阶段或未知，不再抛 TypeError 让整段对话失败；正常读取必须仍使用真实 Invocation 角色信息。测试覆盖完整 showStage -> request -> eventCard 而非只测 JS 语法。

**Token 快捷查看**：累计 Token 数字和本轮计数位于同一个原生 details/summary 热区，整块数字直接点击展开，键盘 Enter/Space 可用。累积输入、输出、其中缓存输入/推理 Token 在展开内容中；不另设狭窄的“拆分”按钮。页面自动刷新保留每个 Agent 已展开的详情，统计含义与实际来源均不变。

**Events 专注阅读**：当 tab=events 时，仅展示任务身份栏、Stage 对话及相关筛选/SDK 事件和返回主屏的导航；隐藏运行设置、任务控制、Canvas/MD 管理板块，但在 Run 主屏继续保留这些功能。由于从 Events 返回主屏使用整页导航，主屏的操作控件完整复原。所有调整只发生在 Observer 前端与只读界面，不更改科学 SDK、任务和用户已保存的运行配置。

## 2026-10-11 · Stage 消息标题使用真实 Agent 名称（TMC-20261011-5034639df06c）

Stage Conversation 不再使用泛称“AGENT · 模型回复”。对于 LLM_MESSAGE，按真实 Invocation.role 显示 Research Lead、Challenger、Investigator、Literature、Judge 或 Reporter，如“Investigator · 模型回复”。对于 AGENT_COMPLETED 显示对应角色的“结构化交付”，工具事件及右侧 Prompt 同样带具体 Agent 名称。无角色信息时显示未知 Agent，不按正文猜测。保留所有已有颜色、左右消息气泡、HTML 转义。

真实依据：Master1 Cycle60 2026-10-11 21:44:52 Investigator 的一条 LLM_MESSAGE 提及 workspace/first_shell/findings.json、results.md、final_audit.json，这属于模型的普通文字回复，不能等同于正式提交的 SDK Findings。其后 21:45:41 SDK role.completed.output.findings 有独立结构化交付，Invocation Ledger 记录 invocation.committed。科研状态以 SDK 和产物为准；页面只忠实标注消息来源和所属角色。

## 2026-10-11 · Hypothesis 首页直达与 Research 冻结对象（TMC-20261011-970eb19e6c53）

原问题：Web Run 主屏缺少可点击的冻结 Hypothesis 入口，Research 只按 CLI 原始文本展示 CHALLENGE 与 CURRENT HYPOTHESIS，没有完整 scope 与 decisive_test；THREAD/ARTIFACTS Web 页信息密度低，与事件对话和真实科研工作区重复。CLI 和科研归档原本是独立用途，不能连数据一起删除。

现设计：首页新增当前冻结 Hypothesis 的原生链接卡片（完整对象 ID，简要原文）；点击 tab=research 展示三段独立科研正文 Hypothesis、Scope、decisive_test，均直接来源于 FileSource.snapshot() 中 hypothesis_lock 的原始冻结字段，无模型改写、不借临时文案构造。Challenger 真实候选资格审查理由与 SDK 单候选选择理由分开存放在折叠区；不混同 qualified 与 supported。未锁定则显示待冻结，不虚构输入。Web 页退出 Thread/Artifacts 的快捷键和导航；旧 tab=thread 和 tab=artifacts 显示 Research 并自动规范当前 URL。SDK 原始 Thread 与 Invocation Artifact Registry、CLI Textual 的 render_thread/render_artifacts、Web 只读监控后台保持可用。

Research / Events 是阅读页，不在下面重复显示运行设置、任务控制、Canvas/MD；进入及离开这两页时全页导航，以重新加载当前 JS 并在回主页时恢复操作控件。Logs 仍保持旧局部展示方式。响应只读和 HTML 转义、防注入，不调用科研或模型。历史 Cycle/Legacy Tab、浏览器导航/刷新、移动端换行、完整数据来源需要静态及真实浏览器验收。

## 2026-10-12 · Run 二级页面全部专注阅读（TMC-20261012-82dd96ca5f9a）

用户指出在 Run 的二级页（尤其 Logs）底部仍重复出现「运行设置／任务级配置」「任务控制 · Run / Resume / Pause / Interrupt」「Canvas / MD」三块管理内容。此前只对 Events 和 Research 作隐藏，Logs 沿用主页底部面板，且原 Logs 的局部 TUI 切换不更新主屏容器外的管理区域：可能导致从主屏进入 Logs 时保留管理控件，甚至从二级页面返回主屏时面板没有恢复。

现行统一规则：**仅 Run 主屏（secondary 为空）**渲染 task settings、operator control panel 和 Canvas / MD 入口；Logs、Events、Research、兼容旧 Thread 和 Artifacts 链接一律不渲染这些主页管理面板。任何二级视图的导航（包括 Logs 快捷键与 Esc 返回）使用完整页面导航，令页面上的主屏控件与二级视图一致，避免遗留旧 Stage 脚本和 JavaScript 局部刷新造成不同步。旧 Thread 和 Artifacts 链接仍与前一版本一样兼容到 Research。页面中「返回主屏 / 复制内容 / 只读状态」等二级阅读器自身控制继续保留。底层科研状态、日志、归档、SDK / CLI 保持不变，未调用任务控制操作。

验收边界：统一扫描所有五种二级 tab（Logs、Events、Research、旧 Thread/Artifacts），无论 canvas 是否可用都不渲染管理 DOM；Run 主页三块面板仍在。浏览器实测主页到 Logs、Logs 返回主页和 Events/Research 查看，验证切换不会意外触发科学 Runner 控制；该变化纯静态可验证，不进入未来真实科研 Cycle 审计队列。

## 2026-10-12 · Run 最近进展双栏 Event 定位，退役 Logs / Events 详情页（TMC-20261012-6f4287b52868）

问题：主屏底部 1 Logs / 3 Events / R Research 快捷栏属于导航噪声。Logs 独立页面不再需要；Events 不再作为整页阅读器，而放到「最近进展 · 全部记录 · 区域内滚动」列表右边成为同步详情。点击完整历史的任一具体进展（Agent done、Skill loaded、Judge verdict、Cycle started 等）应当精确定位原始事件，而不是进入人工搜索页。

实施：删除主屏整个 TUI shortcut footer；Web tab=logs 与 tab=events 的旧书签都显示首页并归一 URL。tab=research 继续通过可点击 Hypothesis 卡片进入冻结科学对象。CLI Textual、原始 orchestrator.log/runtime_events.jsonl、SDK Invocation 以及进一步审计用 Stage Conversation API 保留。Run 主屏 Activity 之后用左右两栏：左「最近进展」全部记录（限定高度内部滚动），右「Events」原始事件及前后三条上下文（独立滚动）；下方继续显示 Hypothesis/Investigation/Judge/Token 以及运行设置、任务控制。

身份追溯：只读 TUI bridge 以同一份 action_history[-24:] 和 agent_timeline[-100:] 生成完整时间戳、角色、事件类型与提示，并验证与原 CLI render_last 输出逐项 HH:MM:SS 对齐，才渲染可点击行。点击后用已认证的 conversation mode=recent，在服务端发现的 research_root 下最近最多24个 Cycle 的有界 runtime_events.jsonl 按 timestamp、event kind、role 精确匹配。SKILL_LOADED 额外匹配 Skill 名与 load_index。Agent 时间轴的「done」显示时间实际来自 Agent started_at，因此对应原 AGENT_STARTED 事件，不能冒充完成时间。无法确认唯一来源时显示缺失或歧义，不用邻近事件代替。原始 JSON 脱敏后只当文本呈现，长度受限。选中事件高亮并在右栏内定位，旁边三条仅作上下文；8秒运行快照刷新保留列表滚动与当前选中高亮。所有科研文件与模型流程均不受影响。

验证边界：Web 无快捷栏、Logs/Events 不再是独立页、Research 保留；同秒不同 Skill 与重复 load_index 正确定位；无来源不得猜测；原 Event 脱敏与路径边界；Firefox 点击真实记录检查右侧高亮和滚动、管理面板及 Hypothesis 保留。此项仅静态 Web 与只读检索，不增加科学 Agent 结构化字段、不启动真实 Cycle。

## 2026-10-12 · 修正「最近进展」：在新标签进入原 Events Stage Conversation（TMC-20261012-2e1018bcb1b1）

用户明确：点击最近进展中的具体事件，应打开新浏览器标签页，进入原独立 Events / Stage Conversation 页面，定位正确历史 Cycle 和 Invocation 并展开原事件；不是在主页显示 JSON、也不是直接打开某个 Agent 整体 Stage。必须保留原有 Prompt/模型回复/Tool/Skill/SDK 的对话排版。之前删除的主屏快捷栏和独立 Logs 页面保持删除；Hypothesis → Research 不变。

主页记录生成原生新标签链接 target=_blank（含 noopener/noreferrer），源标识包含完整 timestamp、事件 kind、role 和 hint；服务器已认证的只读 mode=recent 从真实 runtime_events.jsonl 严格匹配同一事件并返回 Cycle、Stage id、Stage 内 offset 和原始 runtime 索引。同秒多个 Skill 用名称与 load_index 区分，无歧义时才自动跳转。原 Events 阅读页加载目标 Cycle、展开 Stage，并从 offset//45*45 直接读取指定消息页，高亮原 Event 并滚动到可视区域；过长 Stage 不必加载前面所有页。无法映射到 Agent Stage 的独立 SDK / 运行事件显示在同一 Events 时间轴中，但不伪造 Agent Invocation；未匹配则展示错误而非用其他事件代替。底层科研 Runtime、SDK、CLI 和归档均未改动。本次只做静态 Web 和只读来源检索，验收靠既有轨迹及浏览器，不开新科研 Cycle。

## 2026-10-12 · 最近进展同标签跳转 Events（TMC-20261012-c8146ef294f5）

用户将最近进展行的跳转目标从「新标签」改为「当前标签页」。仅在网页 Recent Progress 链接上移除 target=_blank、多余 rel 和新标签提示，继续使用原始事件的完整 timestamp/kind/role/hint URL 进入专属 Events / Stage Conversation，保留已有的 Cycle、Invocation、Stage 内分页展开、高亮定位和浏览器返回。底部快捷栏、独立 Logs 页面继续保持删除。未修改科学 SDK 或科研 Runner。

## 2026-10-12 · 点击 Process RUNNING 显示 Pause / Interrupt（TMC-20261012-8ff699201be9）

用户希望 Run 主屏「运行概况」中的 Process RUNNING 状态徽章可点击，弹出「暂停」「中断」按钮，不需要向下滚动到任务控制才能操作。SDK/TUI 原语义：Pause 在当前安全单元完成后暂停，不再启动下个 Agent 阶段；Interrupt 在下个 SDK 安全检查点停止当前编排，二者均不会异步强停当前 LLM Turn，均保留可恢复 checkpoint。Kill 是终止计算意图，仍单独保留在完整任务控制区，不在 Process 菜单中出现。

仅当 Process 文本为 RUNNING 时在原徽章处显示原生 details/summary 可展开菜单；STOPPED/FAILED/其他状态依旧是只读徽章。菜单操作不自行调用 SDK action 接口，必须转交给完整任务控制的原始按钮，通过该按钮已有的一次 confirm、任务 Cycle/Launch 状态校验和安全边界写入。原按钮禁用或不可用时不发送请求。菜单可通过键盘打开、Esc 或点击外部关闭，原始 Process 展示和轮询状态继续不变。验收使用 mock RUNNING、非RUNNING、前端 JS 语法及 Firefox 展开/关闭，不在任何真实科研任务上点击 Pause 或 Interrupt。

## 2026-10-12 · 最近进展标题文案精简（TMC-20261012-5b5d78c8110d）

依据用户反馈，最近进展的标题只保留「最近进展」，删除无价值的「全部记录 · 区域内滚动」副标题。仅影响 Web 标题文本，不调整列表的内部滚动、Cycle 折叠及原 Events 精确跳转。同步确认 Cycle70 中 Inv tokens 分母 100,122.5k 为 SDK 预算审批后存证的 allowance_tokens=100122468（初始批次 50000000，审查时已用 50122468，追加 50000000），不是模型 Context Window 或展示计算错误，故没有修改预算/数值 UI。该变更只做静态测试，不登记未来科研 Cycle 审计。

## 2026-10-12 · Investigator 预算续批标签 + Process 独立控制弹窗（TMC-20261012-952136d3bfb1）

Investigation 当前 Cycle 额度旁展示 SDK 状态对象 investigator_budget.reviews 指示的历史有效批准次数；0 次时不显示、1 次显示「已追加一次」，更高次数依序显示。额度继续使用 SDK 的真实 allowance_tokens，不由 Review 次数或显示时的已用 Token 推导。历史审批记录不追改，具体计算准则见 SDK 设计文档及 TMC-20261012-35358a705ff9。

Process RUNNING 点击控件不在狭窄的状态卡片里嵌入 Workflow 菜单，而是用居中、独立、可通过 Esc 关闭的原生 dialog 展示暂停、中断、结束本回合。点击任一操作先回到受保护的现有 operator 按钮和 Cycle/Launch 状态验证，随后进入**第二个独立确认 dialog**；确认前不发任何任务控制请求。结束本回合的警告明确显示「当前 Cycle 不能 Resume、未完成研究不出 Judge 科学判决、已保存工作区和日志保留、必须由人类修改后手动 Run」。Kill 的确认仍分两步，Kill 与放弃本回合均需额外勾选已知晓风险。取消弹窗、Esc、查看菜单不改变 SDK 运行状态。非 RUNNING 的 Process 仍只是只读状态标志。真实科研 Runner/Model 不应为了 Web UI 验收触发任何控制。

**当前 Runner 兼容提示**：主屏状态 RUNNING 不表示长期运行进程已经加载最新 SDK。未观察到研究状态中的真实 sdk_runtime_capabilities.abandon_cycle_v1 时，新“结束本回合”按钮不可提交且弹窗给出原因；其他已有 Pause / Interrupt 仍可使用。此检查不通过网页检测 Git HEAD 冒充 Runtime 能力，也不会隐式暂停或重启用户任务。

## 2026-10-12 · 修复独立弹窗在折叠任务控制内不可见（TMC-20261012-1c0a26321263）

上一版把两个原生 dialog 放在默认未展开的 details#tm-tui-controls 内；调用 showModal() 后浏览器顶层遮罩生效，但祖先折叠状态使弹窗内容仍然不可见，导致“没有弹窗、其他按钮也按不了”。已在原操作脚本初始化时将两个原生 dialog 移出折叠 details，放到主 Run 容器下，保持原风险确认、任务 API 和 SDK 操作权限不变。Firefox 验证真实屏幕的模态窗口位于视口中央、中心点命中，取消后其他事件标签可继续点击。不得再把原生 modal 嵌入默认折叠容器。

## 2026-10-12 · 运行概况 Process 与 Workflow 状态一致（TMC-20261012-09b1be4676d0）

Master1 Cycle70 额度耗尽时曾同时显示 State PAUSED 和 Process RUNNING。根因是观测快照的 execution_active 可能被仍活跃的 Supervisor、quota watcher 或管理进程健康度置为 true；这不证明科研 Agent Turn 仍在执行。与 SDK 共用的 CLI/TUI render_header 已更新：若科学状态 PAUSED、INTERRUPTED 则 Process 分别显示 PAUSED、INTERRUPTED；FAILED、NEEDS_ATTENTION、OVER、COMPLETE 等非执行状态则显示 STOPPED；仅在其他非终结状态下结合 execution_active 显示 RUNNING。Web 仍使用 canonical Textual renderer，不自行生成第二套状态判断。

Process 的科研可执行状态与 OS 中 supervisor 是否存活不同；本改动仅影响显示，不触发任何工作流恢复/暂停、不修改科学状态或 quota Controller。回归覆盖 PAUSED+managed active、FAILED+active、真实 RUNNING 与普通 STOPPED 场景。08:15:17 failed 所指具体来源未在指定 Cycle70 正式事件日志中被查证，不能将不同时间的 LLM_FAILED 事件武断等同。

## 2026-10-12 · 运行概况只保留 State 并内联 Codex 额度（TMC-20261012-b76e8849dd22）

Run 主屏用户反馈：Process 和 State 重复，Phase 的 provider_quota_exhausted 不应占用运行概况卡片，原 Codex 账号额度显示在卡片组下面显得脱节。Web 只保留一个「State」卡，以 SDK 提供的科学生命周期 State 为权威值，视觉沿用原 Process 彩色徽标；唯有 State=RUNNING 且 Process 也为 RUNNING 时才保留点击状态弹出安全 Workflow 控制的能力。删除多余的 State 和 Phase 显示卡，不更改 SDK Phase 或底层事件日志。

Codex 额度显示成为同一响应式网格内的普通卡片，仍通过原 /codex-usage.json 每分钟只读查询判断 active_account，使用真实 primary 5 小时与 secondary 7 天使用百分比、原 /services/ 详情链接和小进度条；不硬编码 Codex 1 及百分比。移除原卡片组下方的独立额度容器，避免重复。Web 的每 8 秒状态刷新会重绘卡片，因此每次刷新后重新基于最近一次成功的 quota snapshot 绘制用量，避免一直显示查询中。窄屏使用现成的 2 列响应式规则，CSS 卡片共享背景边框间距。以上均为静态 UI、Read-only、无真实科研 Cycle 审计。

## 2026-10-12 · Codex 卡片双周期额度横排（TMC-20261012-f46193f65467）

遵照用户要求，Run 概况内嵌 Codex 额度卡将「5 小时已用」和「7 天已用」改为同一行左、右布局，保留其下方的 5 小时进度条。使用原有 data-quota-value / data-quota-week 和每分钟只读额度查询，未修改百分比来源、账户切换、/services/ 详情链接或科研 Runner。宽度足够时左右并列，窄屏空间不够时自然换行，避免文字溢出。只做静态 UI TMC 登记，不建立未来 Cycle 审计。

## 2026-10-12 · Codex 额度避免每 8 秒闪烁（TMC-20261012-47e3c6813079）

旧版 Run Web 每 8 秒重新拉取一次状态视图；Elapsed 值持续变化，触发 header 的 innerHTML 整体替换，把嵌入其中的 Codex 额度卡一起销毁，再生成占位文字「查询中…」，最后使用客户端缓存额度快照恢复真实值和进度条。尽管额度 JSON 只需每 60 秒读一次，仍发生了每 8 秒 DOM 卸载/加载和宽度动画重启，用户看到反复闪动。

改为 reconcileHeaderPreservingQuota：比较新旧 header 中的 metric grid，逐张更新状态、Elapsed、Cost 等变化卡片；Codex 额度原 DOM 节点从不卸载，动态 fetch 结果与进度条得以保留。Attention/warning 内容仍可增删/替换，无法识别的头部结构才走原保守刷新路径。paintCodexQuota 对已有同值内容不再无意义地重写 textContent/style/aria 属性，避免看似相同数值但不停触发重绘。保持 State=RUNNING 操作入口、5 小时与 7 天两列百分比、60 秒读额度、8 秒刷科研状态和详情链接不变。静态UI TMC，无需真实科研 Cycle 审计，不改变任何科研 Runner。

## 2026-10-12 · 最近进展去除重复伪 Judge Rework（TMC-20261012-2da47a87868f）

用户看到17:57:33 Judge returned upstream stage for rework #1为最新事件，而同一时刻真正发生的是Cycle70恢复；17:57:34 Investigator进入judge_rework。先前于11:48:04已有唯一真实Judge返工，后续12:15、14:43、14:54、15:49、17:57的同一个#1都是旧Collector在Resume时错误重发。

WebTUI构造只读的action_history副本，按Cycle ID和Judge rework序号只展示同一Cycle第一条真实请求，并过滤与CYCLE_STARTED同秒的旧伪事件。recent_events精确定位元数据和render_last统一来自这个副本，避免展示与点击脱节。原始runtime_events.jsonl以及真实Events页面记录完全不修改；现有再次进入运行视觉约定保持不变。展示修复立刻生效，底层Collector修复必须由下次新Runner加载才生效。

## 2026-10-12 · Explore map 占据运行概况第一排第八张指标卡（TMC-20261012-5c4c71860102）

用户明确要求此前位于「运行概况」七张小卡片下面的 Explore map/Canvas Markdown 导航入口改为**同一指标网格中的第八张卡**，放在第一排。Run 首页宽屏四列的第一排依次为 State、Cycle、Explore map、Codex 当前账号额度；第二排为 Cost、Elapsed、Started、Cycle start。移动是 DOM 结构调整而不是给原独立大卡改颜色；链接仍精确携带当前 task observer_id，跳转 /research-explorer/?observer_id=...，仍展示「探索当前任务的 Canvas 图谱与 Markdown」，Canvas 无法提供时仍为不可点击的说明卡。二级 Research/Events 页面不放入口，也不在 Agent 或底部另放重复链接。

Explore map 卡片使用现有 tm-tui-kv-grid 的响应式四列/两列，视觉上与现有指标卡等距等宽并自适应行高。由于 Render 的 header 可以是不可解析的 TUI 文本，fallback 仍在网格中保留该卡（而非独立放到底部）。原 8 秒只读运行状态刷新会替换发生改变的卡片，Codex quota DOM 的保留策略继续有效。未更改科学状态、额度获取、Canvas 内容和任何任务控制 API。该 UI 改动通过静态测试及真实浏览器检查即可验收，不登记未来真实科研 Cycle 审计。

## 2026-10-12 · State PAUSED 弹出 Resume（TMC-20261012-bdd72488c07f）

Run 首页 State RUNNING 保留暂停、中断、结束本回合独立弹窗。State PAUSED 且现有受保护 operator-control 判断当前 Cycle 可恢复时，PAUSED 徽标成为可点击按钮，打开原生独立弹窗，只显示 Resume · 继续当前 Cycle。选择 Resume 后仍需在第二个原生确认 dialog 中明确确认，之后继续走已有任务控制按钮和受保护 API（预期 Cycle/status/launch、操作登记和幂等校验），不会另建新的任务运行路径。仅打开/取消不发控制请求，不改变科学 Runner。Resume 从现有 checkpoint 和 Investigator 线程恢复，不清空结果。过期状态、不可恢复、abandon_requested 均不出现有效 Resume 入口；RUNNING 的三个控制动作保持原行为。

为避免曾出现的不可见遮罩 BUG，modal 在初始化时继续移出默认折叠的任务控制详情区，在独立浏览器层展示。8 秒运行状态刷新后会依据新状态更新按钮。只做静态 UI TMC 记录和浏览器验收，不触发真实科研 Cycle 审计或 Resume。

## 2026-10-12 · Running Agent 整行绿色、Codex 整卡跳转与 Services 快照首屏（TMC-20261012-eb88305e5e69）

用户希望正在运行的科研 Stage 不再只依靠行内绿色 running 小字识别。Agent 状态表的六列在真实 Textual STATE=running 时一起呈现柔和绿色背景、左右边界强调，鼠标悬停仍保持绿色；未运行行正常灰暗，模型/Effort Select 对比度独立保证。不改变 Agent 当前状态判断、路由选择和科学 Workflow。

运行概况中 Codex 额度小卡片现在是整张原生链接到 `/services/`，和相邻 Explore map 同样可点击、可键盘聚焦。删除卡片内部重复「详情 ↗」二级跳转，不改变额度定期读取、账户选择或科研 Runner。

`/services/` 的初始 SSR 从现有持久化 `QuotaHistory` 读取每个账户最近一次验证的 5h/7d 剩余百分比和采样时间；无需增加另一套 Observer Snapshot 存储或修改 Research Explorer。原始缓存仅保留合法账号、百分比、查询时间（权限0600，不保存 token、信用额、重置卡数量）。首屏展示带虚线框的「历史快照」，注明更新年龄和时刻；**历史状态不展示切换账号/消耗重置卡按钮，也不将其冒充实时额度**。页面脚本随后异步调用既有 `/codex-usage.json` 完成真实查询，成功后填充完整账号、重置卡以及切换入口；失败时保留旧快照、显示错误更新状态，不能退回空白。相同值的定时轮询不反复重建卡片，UI 独立每秒更新最近采样年龄；保留原 `/services/` 一分钟自动轮询和手动刷新，不为了页面初次读取再触发 QuotaHistory 后台 Provider RPC（避免重复）。

未执行任何额度重置、账号切换或科学任务控制；已验证 UI mock 浏览器能先显示历史百分比、延迟返回后逐步替换实时值，关闭和重新打开页面不依赖 JS 存储，持久化快照在服务器重启后仍由历史仓库读取。静态 UI 修改，仅登记 TMC，不建未来 Cycle 审计。

## 2026-10-13 · Supervisor 接管合并（TMC-20261013-190fd39ff4f7）

- 「错误排查」「Attention 接管」合并为一个热开关 **Supervisor**（task env `THEORY_MASTER_SUPERVISOR`；未写新键时任一旧键显式关闭即关闭，默认开启）。设置卡只剩 Supervisor 与额度自调两项；模型选择 key `supervisor`（回落旧 `attention_review`，默认 GPT-6 Luna）。
- 独立页 `/run/<id>/supervisor/`（`supervisor_view.py`）：按 Cycle 读 `research/<cycle>/supervisor/session.jsonl`，以 Stage 气泡呈现停因 → Prompt → 模型回复 → 决策；旧 Cycle 的 `attention/sdk_internal_review_*.json` 只读回放。JSON：`/api/run/<id>/supervisor?cycle=`。非法 Cycle 404/400。
- 主屏：`run_detail_body` 首个常驻 pane `data-tui-pane="supervisor"`，空则隐藏；接管中（琥珀脉冲）/ 已停下（红）/ 停下未接管。任务清单状态旁同步 chip。`returned`（已交回）不再钉在主屏或任务列表；该会话留在右上角 Supervisor 消息里，见下条。

## 2026-10-13 · 已交回故障离开主屏、留在铃铛（TMC-20261013-305e0c6af068）

Supervisor 状态为 `returned` 时，Task Console 横幅和任务列表 chip 不再显示。接管中、已停下、未接管仍留在主屏。每条 Supervisor 会话（含 Runtime `run_failed` 已交回）继续出现在右上角「Supervisor 消息」，不因已阅读或已交回而删除；红点只计当前仍需处理的 hold。不改 Runner。

## 2026-10-13 · 左侧 Workspace 增加 Task Console（TMC-20261013-c3475a3e188e）

`dashboard_shell` 左侧 Workspace 在 Task List 后增加 **Task Console**。无具体任务上下文时链接 `/`，沿用既有 303：最近一次仍存在的 Task Console，否则 Task List。子页或 Explore map 已带 `/run/<16hex>/` 父级时，该入口直接指向该任务控制台并作为当前项。Task List 页仍高亮 Task List。不新增科研路由，不改 Runner。

## 2026-10-13 · Services 返回最近 Task Console（TMC-20261013-5a5d4c9dd1b6）

`/services/` 顶栏「返回」原先在未显式指定父级时落到 Workflow。现用已有 `tm_last_task_console` cookie：目标任务仍在快照中则返回该 `/run/<16hex>/`；无记录或任务已消失时仍回 Workflow。不改额度查询、账号切换或科研 Runner。

## 2026-10-13 · Skill 调用与工具调用同一折叠条（TMC-20261013-3cf86c2cf056）

Stage 对话里连续 `SKILL_LOADED` 不再铺成里程碑（角色、Stage、Turn、原始字段常显）。改为与工具调用相同的折叠条：`Skill 调用 · N 次 · 用时 …`；展开后每条是同样的气泡，标题为 skill 名，正文为 path、load 序号与检测方式。相邻工具调用仍单独成组。不改事件落盘。


### 2026-10-19 · 科研保全 Worker 操作

NEEDS_ATTENTION 显示“保全成果并结束本轮”，控制名 preserve_release。单击仅启动后台 PRESERVING；Worker/科学状态为停止、资源拒绝或 Judge 返工阻断且 claim fence 匹配时才允许。独立只读 Investigator preservation Stage 可调用一次模型，成功归档 StageSummary/Canvas/ScienceGraph v2 与 Cycle closure 后才释放 Worker；失败维持旧 claim/科学证据，恢复为 NEEDS_ATTENTION。其余状态的直接 release 标记“放弃当前占用（不归档）”。操作员须知当前 Cycle 成功保全后不可恢复，不能把未裁决阶段性记录当成 Judge Outcome。

### 2026-10-22 · Codex 额度窗口按实际时长显示（TMC-20261022-1ae78c82c09f）

Services 与 Run 额度卡片按上游 `window_duration_mins` 标注窗口：300 分钟为 5 小时，10080 分钟为 7 天，其他正整数按分钟显示；时长缺失时标明未知。只显示实际返回且剩余百分比有效的窗口。单个周窗口可以作为历史快照保存并在重启后恢复；磁盘快照只增加窗口时长，不保存套餐、重置卡或认证资料。旧快照没有时长时不推断 primary/secondary 的周期。
