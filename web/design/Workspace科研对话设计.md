# Workspace 科研对话设计

> 2026-10-15；来源：用户提出的全局 Loquix 对话入口。实现状态以代码和测试为准。

## 定位与入口

Workspace 侧栏首位提供独立的科研对话页。它面向整个平台和当前已有项目，区别于只处理故障接管的 Supervisor 会话。首次进入显示三个起点：迁移已有工作、了解某项工作的前沿、讨论项目并提出方向干预。每个起点都进入同一个有历史记录的对话界面；用户可以随时切换任务。默认打开最近活跃的会话；历史与项目选择收在按需展开的菜单中。新会话入口放在菜单里，避免一直占据聊天区域。

聊天区以居中消息流和底部输入框为主。每个会话可选择 `gpt-6-sol`、`gpt-6-luna` 或 `gpt-6.1-sol`，其中新会话默认 `gpt-6-sol`。模型调用在后台运行，回复增量显示；Loquix Stop 会中断当前 Codex turn，并保留已经生成的部分内容。

**模型兼容性边界（2026-10-18）**：有些 ChatGPT Codex 账号对 `gpt-6.1-sol` 返回明确的 `HTTP 400 invalid_request_error: model is not supported`；这不意味着所有 400、鉴权、额度或超时都应该降级。只有准确识别 `CodexProviderRequestRejected` 且提示 `not supported when using Codex with a ChatGPT account`，并且本轮使用该模型、独立聊天后端时，才自动切换至 `gpt-6-sol` **重试一次**。切换清空旧模型 SDK thread ID，但保留整段可追溯会话、最新问题和 reasoning effort，并在消息流留下提示。手动切换模型也会清空旧 thread ID。若重试仍失败，保持明确错误状态，不能无限重试或制造成功回复。预览型草稿另有人工确认闸门，不越权写入科研记录。

## 权限与数据流

```text
Loquix 页面 → 受认证保护的 Workspace API → 独立会话与工作草稿
                                      ├→ 已发现任务的只读 Research Plan / Canvas 摘要
                                      ├→ 显式确认后 task.bootstrap 初始化新任务
                                      └→ 显式确认后在 Canvas 记录 human ResearchFocus / preferred
```

对话中的自由文本只是讨论或草稿，不自动启动 Cycle、改写 Research Plan、修改已有科学结论或更动 Judge/Evidence。所有持久化写入有独立动作、预览和来源记录。服务端从已发现的任务索引解析任务身份，不接受浏览器传入的研究根路径作为已有任务身份。迁移目录必须在配置的 Workspace 搜索根内，限制扫描范围、文件类型和大小，不能执行目录中的代码。

## 迁移已有工作

默认提示先询问已有工作目录和长期科研目标，再有界读取既有资料。建项对话依次收集四项由用户确认的内容：长期目标、研究边界与证据要求、最终完成判据、资源与必要背景；无需用户预先写好 Research Plan。第五节 `Scientific Model` 由系统给出待核对初稿，已有计划中的模型只作为待验证内容带入。资料不足时在草稿中标出待确认之处，不能编造已有结论。生成的 `research_plan.md` 必须恰好具有 `Goal`、`Research Contract`、`Final Gate`、`Required Context`、`Scientific Model` 五个一级标题，先通过 SDK `scientific_sections` 校验并展示可修订预览；修订后重新校验并要求再次确认。用户确认后写入新任务目录，调用 `task.bootstrap`，刷新任务索引使它出现在 Task List；不自动运行任务。

## 项目前沿与方向

选择已有任务后，助手可根据 Research Plan、Canvas 和有界的关联资料解释当前前沿、证据来源与未决点。回答应明确区分已有记录、可检验的推断与用户新想法。方向干预使用 Canvas 现行 §31 的 `External` / `ResearchFocus` 人类来源节点和 `external_reference`；选择感兴趣的活动线路时，仅对真实 active 科学 tip 设置 `priority: preferred`。这不是 `operator_preferences` 的模型/开关配置，也不替代 Research Lead 的资格判断。无符合条件的活动 tip 时先保留建议，不伪造 preferred。

**实时运行态边界（2026-10-18）**：聊天提问当前 Worker 是否仍运行时，应在每次消息建立 Prompt 时通过本机受核验的 Worker 进程身份登记查询获取 `process_alive`、`effective_status`、`current_cycle` 和 UTC 观测时间，并区分一次批次的 `worker_scheduler=STOPPED` 与仍运行中的科研 Worker。只读最近 Cycle 目录的 `run_state.json` 可能为旧快照，不能单独当作实时证据；当身份检查不可用时明确说明，不根据旧记录猜测。该投影不启动或停止科研进程，不修改 Canvas 或 Judge 结论。

## 默认助手指令

你是 TheoryMaster Workspace 的科研协作助手。先判定用户是在迁移工作、了解已有任务，还是提出方向。迁移时收集目录和长期目标，聚焦阅读已有文件，指出缺失信息；仅在用户明确确认后初始化符合正式格式的 Research Plan 和任务。了解前沿时先读取用户指定任务的正式入口，给出可追溯摘要与不确定性。对新方向先讨论科学动机、与 Goal 的关系及可检验性；在用户明确选择“记录到 Canvas”后，建议写为 `External` / `ResearchFocus` 并用 `external_reference` 关联当前科学 tip；在用户明确选择“设为 Preferred”且该 tip 为真实 active 科学 tip 后，建议设置 `priority: preferred`。Preferred 只是优先审视的建议，不能覆盖 Goal、Research Contract、Final Gate、Challenger 或 Judge。不得声称聊天中的建议已经通过 Judge、成为 Evidence 或被正在运行的 Cycle 采用。任务运行状态和科学结论均以 SDK 正式记录为准。

## 验证边界

静态与 mock 验证覆盖导航、会话隔离、目录校验、五节 Research Plan、确认写入和任务发现。流式显示与停止需要通过 Codex 客户端完成端到端交互验收。真实模型对话质量与下一次科研 Cycle 是否正确使用方向偏好，须用单独授权的真实模型测试或后续 Cycle 轨迹复核；离线检查不能代替这些证据。
