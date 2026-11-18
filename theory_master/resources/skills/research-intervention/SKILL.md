---
name: research-intervention
description: Interpret operator steering depth and execute authorized plan changes, Scientific Model corrections, branch curation, human conjectures, and runtime actions through canonical interfaces.
---

# 调整研究路径

## 识别意图和授权

最近的用户意图优先于会话最初的卡片。先把用户希望改变的问题、深度、保留条件和相关路径说清楚，读取 Research Plan、canonical graph 的相关 H/来源及当前 Worker/Cycle 锁。按影响选择最小足够的干预：仅讨论；改局部计划/上下文；提出下一轮猜想；以证据更正 Scientific Model；park/close 指定路径；或启动/恢复运行。新猜想不自动要求重写整个计划或修改科学认识。

“分析一下”“给建议”“我不满意”只授权分析。有具体可执行方案时，在末尾按 1、2、3 编号列出适用变更：Research Plan 的具体段落与改法、下一轮精确猜想与判别检验、Scientific Model 的证据性更正、路径状态、运行操作。每项说明原因、影响哪些路径/当前工作及何时生效，再询问是否按这些项执行或修改。不凑不必要的改动，纯解释且没有方案时不机械确认。用户明确下达修改/执行指令，或接受了对话中具体可识别的编号方案或部分项，即授权该范围；采用用户追加的修改，无需重复确认。含糊的“好”且存在多个互斥方案时只澄清选择。接受修改计划不自动授权启动科研运行；明确“按方案继续研究/启动”才包含运行。

## 规范操作

使用消息中提供的真实 `search_root` 和 selected task 对象（`observer_id`、`task_root`、`research_root`），从稳定任务 binding/config 解析计划、图数据库及笔记库位置。不把聊天保存目录当作任务目录，不编辑 Canvas、claim ledger 或 locked Cycle JSON 来改变科研。

### 修改 Research Plan

使用 `tm_observer.research_plan_editor.read_plan(search_root, task)` 读取五个 sections。对所需 section 调用 `save_section(search_root, task, {"section": name, "text": new_text, "expected_revision": section["revision"], "change_reason": human_reason})`。保留其他部分和科学历史，注明来源与意图；CAS 冲突后重新读并整合。不要把用户猜想写成既有结果，不因为路线调整降低 Final Gate。

### 单独判断 Scientific Model 是否需要更正

Scientific Model 是当前科学认识的总结，与 Goal/Contract 等计划条目及下一轮猜想分别判断。先读当前完整内容、有关 Judge/Cycle 证据和版本，说明哪句错误、遗漏或越界，拟改成什么及来源；只调整受影响认识并保留仍有效的发现。可以据证据纠错、补充边界或恢复不确定性；确需长期保留的模型假设须经授权并明确标为待检验及其人工来源。不能因为用户偏好某条路线就宣称其成立，不能把待检验猜想混成已证实结论，也不能以改 Model 改写图谱事实、Judge verdict 或已冻结 Hypothesis。使用上节 `save_section` 的 `section="Scientific Model"`、该节当前 `revision` 和包含人的更正意图/证据来源的 `change_reason`；遵守当前 Model 格式与长度校验。仅恢复模型历史时指定 `scope="model"`，保留计划其他节。核对并发 Cycle 和生效范围；不能直接重写运行 metadata 或假造一个科学模型版本，也不能把编辑历史版本与运行时科学模型版本混报。

读取响应的 `model_version`（编辑历史版本）、`scientific_model_version`（运行时 CAS 版本）、`scientific_model_source` 和 `scientific_model_status` 核对结果。`human_revision_not_evidence` 表示人工修订，不是新增科学证据。当前 Worker 可能仍持有旧模型输入；说明新版本的生效范围，不宣称已冻结调查自动采用了修订。

### 暂停或关闭研究路径

先核对 canonical frontier 和 live Cycle claims。经明确用户授权后，按锁顺序取得 `theory_master.goal_terminal.science_commit_barrier(research_root, "shared")`，调用 `assert_goal_open(research_root, database, spec=spec)`，再取得 `theory_master.multi_worker.canvas_write_lock(research_root)` 和 `ScienceGraphStore(configured_database)` 事务。在其中使用 `set_frontier(branch_id=existing["id"], hypothesis_id=h_id, status="parked" or "closed", anchor_outcome=existing["anchor_outcome"], reason="human intervention; session …; reason …")`。这是显式操作员路线整理；不调用 `close_all_frontiers_for_goal`，不写 Outcome/Judge verdict，不把 closed 描述成 rejected。用事务处理同一批变更，读取 `science_canvas_export.canvas_projection(store)` 验证规范投影；若当前 SDK 提供 `vault_canvas.refresh_registered_canvases(database)` 则刷新登记的显示投影。Goal 已封存时停止，不绕过封存守卫。

修改 frontier 不会自动停止持有该 H 的 Worker。目标路径存在 live owner 时，先说明当前工作与用户干预的关系；若用户要求停止或切换，使用 `theory_master.worker_runtime.worker_action(spec, worker_id, operation)` 安全暂停并核实已停止，再应用相关方向变更。operation 包括 `pause`、`kill`、`release`、`resume`，遵守各自前置条件与安全边界，保留历史与证据。仅要求改变后续路径时可保留当前 Cycle，但要明确生效范围和仍在运行的内容。不要直接信号杀进程。关闭其他空闲路径通常不需要中断当前 Cycle。

### 从用户新猜想开始

先将猜想、可区分检验、与 Goal 的关系和要替代/保留的路径写清楚，读取现有人工猜想及投递状态。有其他 pending 猜想时说明先后；用户要求取代某条待投递猜想才传其 `replace_external_id`，不能把追加误报成已取代。使用 `tm_observer.research_chat_intervention.record_conjecture(search_root, task, hypothesis=exact_proposition, decisive_test=exact_test, reason=human_reason, expected_revision=required_context_section["revision"], source_session=session_id, timing="next_proposal", replace_external_id=authorized_replacement_or_empty, source_references=verified_sources)`。接口只记录 canonical External 及投递事件，保留 Research Plan 每节原文；`expected_revision` 核对讨论时使用的 Required Context，并不要求把一次性指令追加到稳定上下文。来自图谱节点/报告选段的来源使用服务端核验的 ID、版本和原文，不编造来源快照；来源过期时重新读取后处理。

默认 `timing="next_proposal"` 可在当前 Cycle 继续运行时登记，不为下一轮建议擅自中断当前工作。SDK 将待投递项交给下一次全新 `frontier_proposal`，同一项只分配给一个提案过程；同一 Cycle 的重试/恢复沿用已领取的快照，不在以后 Cycle 重复注入。用 `theory_master.operator_direction.direction_status(store, external_id)` 核对 `pending`、`claimed`、`consumed`、`revoked`、`superseded`；`consumed` 仅表示交给该提案，不表示资格通过、调查开始或科学成立。明确撤销待投递项用 `revoke_conjecture(search_root, task, external_id=external_id, reason=human_reason, source_session=session_id)`；已领取项不能撤销或替换，不直接修改事件账本。需要长期研究方向时另提 Research Plan 变更。

历史无生命周期的猜想可用 `list_conjectures(database, status=None)` 查看，标为 `reconciliation_required`；它们不自动进入新队列。说明其来源和内容，经用户确认后才重新登记成 pending。只有 Plan marker、没有规范 External 的旧部分操作也须明确核对，不能把它当作等待执行的命题或静默恢复。

用户明确要求立即转向时，先核对授权是否包含中断正在运行的工作，使用 Worker 控制安全暂停并验证 Runner/Worker 实际停止，再以 `timing="immediate"` 登记。此参数仅增加已停止检查，不改写旧 checkpoint，不自动启动科研；要按另行明确授权启动支持的全新提案流程。不能把恢复已冻结 H 的旧 Cycle 描述成应用了新猜想，也不能删除/解锁旧 Cycle 来强行转向。

检查 receipt 的 `status`、`node_recorded`、`plan_unchanged`、`delivery_status`、`effective_at`、`execution_started` 与 `projection_refreshed`。若 canonical 节点已保存但投影失败，说明部分完成并复核已有 External ID，不重复登记新节点。External 人类猜想仍是输入；正式 Hypothesis 由 Research Lead → Challenger 资格流程冻结后 SDK 分配 H ID。不能预创建一个看似已资格通过的 H，也不能直接跳到 Investigator。资格不合格或与 Goal 冲突时如实说明；只有看到实际锁定的命题和 H ID 后才称为“已从该猜想开始研究”。Preferred 和 `--preferred-hypothesis` 不会定向调度，不能用其保证执行目标。

### 启动、恢复与核对

从 `TaskBinding.load(task_root)` 和 `TaskSpec.from_config(binding.config_path)` 取得 spec。使用已支持的 `worker_action`、`start_scheduler(spec, verified_slots)` 或 `theory_master.operator_control.dispatch` 完成明确请求；容量未知时先读取配置，不能随意扩容。公开命令 `python -m theory_master workers TASK_ID start --resource-slots N` 启动调度，`pause|resume|release --worker worker-N` 操作指定 Worker。NEEDS_ATTENTION 的说明可用 `Workflow.submit_attention_guidance(text, source="human")` 后再按授权恢复；此接口不适用于任意活跃 Cycle。

核对修改后的计划与 Scientific Model 版本、图谱状态、External/H ID、Worker/PID 身份和实际 Cycle 阶段。按用户接受的编号项目逐项回执：已更改的内容与记录链接；尚待生效的内容及触发点；实际已启动的 Cycle/阶段；失败或部分完成及其原因。区别“已记录”“等待下一次全新提案”“已资格锁定 H”“已开始调查”和“已完成”。跨存储部分成功时保留并明确此前成功项，不能声称自动回滚或全部完成。操作成功不代表科学结论成立。不粘贴内部配置和长日志；剩余选择确实影响执行范围时才再次澄清。
