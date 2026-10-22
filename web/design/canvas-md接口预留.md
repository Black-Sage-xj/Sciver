# canvas-md 接口约定（Research Explorer，只读已实现）

**状态：READ-ONLY IMPLEMENTED，2026-10-11。** 原先为预留合同，现由独立 canvas-md 实现模块和 Observatory 统一 HTTP handler 接入；写入/编辑仍未实现。原始 Run 分工见 [Run控制台设计.md](Run控制台设计.md) 第 8 节。

## 命名与集成位置

- 正式名称：`canvas-md`。
- HTTP 前缀固定为：`/api/canvas-md/tasks/{observer_id}/...`。仅下述三个 GET action 已注册；未知 action、无效 observer_id 一律拒绝，不返回假 200。
- 宿主位置：Run 详情页的 **Canvas / MD** 区域。有受信任 Canvas 时显示可点击的 Research Explorer 入口；没有时继续显示不可用标识。独立网页为 `/research-explorer/?observer_id={observer_id}`。
- 任务身份：Run 提供稳定 `observer_id`；由服务端根据已发现的 task 索引查找 canonical `research_root`；不接收任意绝对文件路径。
- Canvas 读取、Markdown 导航索引和只读预览由 `tm_observer/canvas_md.py` 独立负责；`canvas_md_routes.py` 负责 GET 协议。编辑、文件写入、版本冲突解决与协同暂不提供。Run 仅传入 observer_id 和可用性判断。
- 不允许外部插件越过 SDK Canvas 权限修改 Hypothesis、Decision、Evidence 或系统路由。

## 双方开发边界

| Run Web UI 拥有 | canvas-md 实现者拥有 |
| --- | --- |
| Run 的独立任务清单、任务详情、任务 ID 导航 | Canvas / Markdown 内容索引、读取、安全校验和预览/编辑决策 |
| 显示待集成入口；传递 stable observer_id | canvas-md 内部真实 handler、数据协议和 API 测试 |
| 统一应用导航和最终页面集成测试 | 文档访问范围、文件更新、历史及并发冲突控制 |

**已集成接缝**：`web.py` 只注册一次只读前缀并在现有认证检查之后分派至 `canvas_md_routes.py`；未让 Run 直接实现解析。接口响应现在冻结如下：

| GET 路径 | 返回 |
| --- | --- |
| `/api/canvas-md/tasks/{observer_id}/index` | JSON `observer_id, canvas, nodes[], edges[], documents[], aliases{}, references{}, backlinks{}, stats{}` |
| `/api/canvas-md/tasks/{observer_id}/document?path=nodes/xxx.md` | JSON `id, title, content` |
| `/api/canvas-md/tasks/{observer_id}/search?q=关键字` | JSON `results[]`，每项为 `kind, id, label, excerpt` |

`node` 与 `doc` 在引用索引中分别使用 `node:{id}` 和 `doc:{root-relative-path}` 身份。Canvas 的 `edges[]` 仅来自源 Canvas，不把 `[[链接]]` 转换为科学演化边。Node ID 使用 Canvas 原始 id，文档使用 vault 根下规范化相对路径。源文件改变后刷新页面即可重新读取，不额外创建知识数据库。

服务端先从 Run 快照中按 **stable observer_id** 找到 canonical research_root，再定位 Run 声明的笔记 task_root，并交叉核对现有 task config 中的 research_root/branch_canvas。仅在 repo 下的笔记区任务范围内允许读取 `exploration.canvas`、`research_plan.md`、`notes/**/*.md`、`nodes/**/*.md`、`literature/**/*.md`，禁止读取任意绝对路径、越界相对路径、vault 外的 symlink 或 raw 研究数据。未配置有效 Canvas 的任务返回 404，不按重复的 Master ID 猜测目录。

所有 GET 在现有 Observatory 认证之后调用；返回 `Cache-Control: no-store`。写入权限全部不存在，系统不改变 Agent、SDK、Hypothesis 或科研状态。Markdown 在前端经安全清理后渲染，公式使用 KaTeX；当前浏览器从 jsDelivr 加载只读渲染库，离线时回退显示转义后的原文。
