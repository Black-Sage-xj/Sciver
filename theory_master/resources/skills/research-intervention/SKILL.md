---
name: research-intervention
description: Carry out authorized edits, conjectures and runtime actions.
---

# 调整研究路径

## 规范操作

使用消息中提供的真实 `search_root` 和 selected task 对象（`observer_id`、`task_root`、`research_root`），从稳定任务 binding/config 解析计划、图数据库及笔记库位置。不把聊天保存目录当作任务目录，不编辑 Canvas、claim ledger 或 locked Cycle JSON 来改变科研。

### 修改 Research Plan

使用 `tm_observer.research_plan_editor.read_plan(search_root, task)` 读取五个 sections。在返回的 section["text"] 上改动有关正文，保留段首换行和段落间隔，再调用 `save_section(search_root, task, {"section": name, "text": new_text, "expected_revision": section["revision"], "change_reason": human_reason})`。保留其他部分和科学历史，注明来源与意图；CAS 冲突后重新读并整合。原题与计划不符时，核对题面、公开评分说明或用户授权版本，在 change_reason 标明出处、差异及生效范围。按授权版本纠正擅加或遗漏的要求，保留旧版本及已有裁决。不能因当前证据或结果恰好未达标而修改原判据。

### 单独判断 Scientific Model 是否需要更正

Scientific Model 是当前科学认识的总结，与 Goal/Contract 等计划条目及下一轮猜想分别判断。先读当前完整内容、有关 Judge/Cycle 证据和版本，说明哪句错误、遗漏或越界，拟改成什么及来源；只调整受影响认识并保留仍有效的发现。可以据证据纠错、补充边界或恢复不确定性；确需长期保留的模型假设须经授权并明确标为待检验及其人工来源。不能因为用户偏好某条路线就宣称其成立，不能把待检验猜想混成已证实结论，也不能以改 Model 改写图谱事实、Judge verdict 或已冻结 Hypothesis。使用上节 `save_section` 的 `section="Scientific Model"`、该节当前 `revision` 和包含人的更正意图/证据来源的 `change_reason`；遵守当前 Model 格式与长度校验。仅恢复模型历史时指定 `scope="model"`，保留计划其他节。核对并发 Cycle 和生效范围；不能直接重写运行 metadata 或假造一个科学模型版本，也不能把编辑历史版本与运行时科学模型版本混报。

读取响应的 `model_version`（编辑历史版本）、`scientific_model_version`（运行时 CAS 版本）、`scientific_model_source` 和 `scientific_model_status` 核对结果。`human_revision_not_evidence` 表示人工修订，不是新增科学证据。当前 Worker 可能仍持有旧模型输入；说明新版本的生效范围，不宣称已冻结调查自动采用了修订。

### 暂停或关闭研究路径

先核对 canonical frontier 和 live Cycle claims。经明确用户授权后，按锁顺序取得 `theory_master.goal_terminal.science_commit_barrier(research_root, "shared")`，调用 `assert_goal_open(research_root, database, spec=spec)`，再取得 `theory_master.multi_worker.canvas_write_lock(research_root)` 和 `ScienceGraphStore(configured_database)` 事务。在其中使用 `set_frontier(branch_id=existing["id"], hypothesis_id=h_id, status="parked" or "closed", anchor_outcome=existing["anchor_outcome"], reason="human intervention; session …; reason …")`。这是显式操作员路线整理；不调用 `close_all_frontiers_for_goal`，不写 Outcome/Judge verdict，不把 closed 描述成 rejected。用事务处理同一批变更，读取 `science_canvas_export.canvas_projection(store)` 验证规范投影；若当前 SDK 提供 `vault_canvas.refresh_registered_canvases(database)` 则刷新登记的显示投影。Goal 已封存时停止，不绕过封存守卫。

修改 frontier 不会自动停止持有该 H 的 Worker。目标路径存在 live owner 时，先说明当前工作与用户干预的关系；若用户要求停止或切换，使用 `theory_master.worker_runtime.worker_action(spec, worker_id, operation)` 安全暂停并核实已停止，再应用相关方向变更。operation 包括 `pause`、`kill`、`release`、`resume`，遵守各自前置条件与安全边界，保留历史与证据。仅要求改变后续路径时可保留当前 Cycle，但要明确生效范围和仍在运行的内容。不要直接信号杀进程。关闭其他空闲路径通常不需要中断当前 Cycle。

### 从用户新猜想开始

先将猜想、可区分检验、与 Goal 的关系和要替代/保留的路径写清楚，读取现有人工猜想及投递状态。有其他 pending 猜想时说明先后；用户要求取代某条待投递猜想才传其 `replace_external_id`，不能把追加误报成已取代。使用 `tm_observer.research_chat_intervention.record_conjecture(search_root, task, hypothesis=exact_proposition, decisive_test=exact_test, reason=human_reason, expected_revision=required_context_section["revision"], source_session=session_id, timing="next_proposal", replace_external_id=authorized_replacement_or_empty, source_references=verified_sources)`。接口只记录 canonical External 及投递事件，保留 Research Plan 每节原文；`expected_revision` 核对讨论时使用的 Required Context，并不要求把一次性指令追加到稳定上下文。使用图谱选段时按 research-evidence 核对 `source_references`。

默认 `timing="next_proposal"` 可在当前 Cycle 继续运行时登记，不为下一轮建议擅自中断当前工作。SDK 将待投递项交给下一次全新 `frontier_proposal`，同一项只分配给一个提案过程；同一 Cycle 的重试/恢复沿用已领取的快照，不在以后 Cycle 重复注入。用 `theory_master.operator_direction.direction_status(store, external_id)` 核对 `pending`、`claimed`、`consumed`、`revoked`、`superseded`；`consumed` 仅表示交给该提案，不表示资格通过、调查开始或科学成立。明确撤销待投递项用 `revoke_conjecture(search_root, task, external_id=external_id, reason=human_reason, source_session=session_id)`；已领取项不能撤销或替换，不直接修改事件账本。需要长期研究方向时另提 Research Plan 变更。

历史无生命周期的猜想可用 `list_conjectures(database, status=None)` 查看，标为 `reconciliation_required`；它们不自动进入新队列。说明其来源和内容，经用户确认后才重新登记成 pending。只有 Plan marker、没有规范 External 的旧部分操作也须明确核对，不能把它当作等待执行的命题或静默恢复。

用户明确要求立即转向时，先核对授权是否包含中断正在运行的工作，使用 Worker 控制安全暂停并验证 Runner/Worker 实际停止，再以 `timing="immediate"` 登记。此参数仅增加已停止检查，不改写旧 checkpoint，不自动启动科研；要按另行明确授权启动支持的全新提案流程。不能把恢复已冻结 H 的旧 Cycle 描述成应用了新猜想，也不能删除/解锁旧 Cycle 来强行转向。

检查 receipt 的 `status`、`node_recorded`、`plan_unchanged`、`delivery_status`、`effective_at`、`execution_started` 与 `projection_refreshed`。若 canonical 节点已保存但投影失败，说明部分完成并复核已有 External ID，不重复登记新节点。External 人类猜想仍是输入；正式 Hypothesis 由 Research Lead → Challenger 资格流程冻结后 SDK 分配 H ID。不能预创建一个看似已资格通过的 H，也不能直接跳到 Investigator。资格不合格或与 Goal 冲突时如实说明；只有看到实际锁定的命题和 H ID 后才称为“已从该猜想开始研究”。Preferred 和 `--preferred-hypothesis` 不会定向调度，不能用其保证执行目标。

### 启动、恢复与核对

从 `TaskBinding.load(task_root)` 和 `TaskSpec.from_config(binding.config_path)` 取得 spec。使用已支持的 `worker_action`、`start_scheduler(spec, verified_slots)` 或 `theory_master.operator_control.dispatch` 完成明确请求；容量未知时先读取配置，不能随意扩容。公开命令 `python -m theory_master workers TASK_ID start --resource-slots N` 启动调度，`pause|resume|release --worker worker-N` 操作指定 Worker。NEEDS_ATTENTION 的说明可用 `Workflow.submit_attention_guidance(text, source="human")` 后再按授权恢复；此接口不适用于任意活跃 Cycle。

完成操作后重读回执，逐项说明哪些变更已保存、哪些失败或仍待生效，并给出失败步骤的实际原因。
