from __future__ import annotations

import html
import re


CURRENT_FLOW_MERMAID = r"""flowchart TD

    %% =========================================================
    %% 1. Hypothesis Qualification
    %% =========================================================

    LEAD["Research Lead<br/>frontier_proposal<br/>─────────────<br/>读取：Goal / Research Contract<br/>Scientific Model<br/>compact prior Cycle scientific state<br/>previous Decision + Canvas / reports 按需导航<br/><br/>输出 1~N Candidates：<br/>• hypothesis<br/>• scope<br/>• why_now<br/>• goal_advance<br/>• decisive_test<br/>• optional lineage_anchor_node_id<br/>• optional typed evolution_operator<br/><br/>active = 默认前沿，不是硬起点<br/>全局 pivot 时：why_now 解释原因<br/>新 H 必须给 anchor + operator<br/>SDK 建 edge；Lead 不指定 H ID<br/><br/>新 decisive_test 必须增加判别信息<br/>不得只为 strengthen confidence<br/>重复 terminal-supported 等价测试"]

    LEAD_LIT{"Lead 是否存在<br/>必须补充的 external scholarly gap？"}

    LEAD_LIT_TASK["SDK Scholarly Retrieval<br/>＋ Literature LLM<br/>─────────────<br/>landscape / targeted_source<br/>同一 Lead stage 最多一次<br/>fresh Literature thread"]

    CHALLENGER["Challenger<br/>candidate_review<br/>─────────────<br/>一次 exhaustive audit<br/>覆盖全部 Candidates<br/><br/>尚未推导/操作化的 prerequisite<br/>若属于冻结 decisive_test，且可在读目标响应前<br/>于固定 Hypothesis / scope / estimand / inputs 内解决<br/>→ Investigator 工作，不是 blocker<br/><br/>只有命题含义不确定、可 post-hoc 泄漏、<br/>没有真实可证伪结果、越界才能解决，<br/>或是必须先独立裁决的新命题时才阻断<br/><br/>科学等价重复已 supported test<br/>且无新增判别信息 → 不得 qualified"]

    HAS_Q{"存在 qualified<br/>Candidate？"}

    MULTI_Q{"qualified > 1？"}

    SELECT["Research Lead<br/>candidate_selection<br/>─────────────<br/>Agent receives：<br/>• qualified_candidates<br/>  （continuation 仅 id / title）<br/><br/>Agent returns：<br/>• selected_candidate_id<br/>• selection_reason<br/><br/>decisive_test 不重写<br/>由 SDK 从 selected Candidate 冻结"]

    AUTO["SDK<br/>确定性自动选择<br/>─────────────<br/>接受：<br/>• Challenger 已 qualified Candidate<br/>• selection_repair resolved 且验证通过的 replacement<br/>• cannot_repair 时保留并继续原 Candidate<br/><br/>保留原 decisive_test 与 Challenger 审查记录"]

    HAS_REVISE{"存在 revise？"}

    REPAIR["Research Lead<br/>selection_repair<br/>─────────────<br/>最多一次；只看 repair_options<br/>每项：candidate_id / title / blockers<br/><br/>Agent returns：<br/>• resolved + blocker_closures<br/>• 或 cannot_repair + reason<br/>• clarification / estimand / decisive_test patch 仅按需<br/><br/>cannot_repair = 超出有限修补权限<br/>保留原 Candidate、decisive_test 与审查记录<br/>继续进入 Investigation；不等于科学不合格<br/><br/>不得改变 hypothesis / scope / goal_advance / estimand"]

    STOP_Q["NEEDS_ATTENTION<br/>─────────────<br/>自动登记 incident + fresh Codex handoff<br/>在现有 Task / frozen science / allowed inputs 内<br/>形成正式角色的一次性 guidance<br/><br/>安全 checkpoint + 无 active owner → 自动 resume<br/>科学判断交给正常 Research Lead / Investigator / Literature / Judge<br/>不扩权、不编造数据、不由 Repair Codex 写 verdict"]

    LEAD --> LEAD_LIT

    LEAD_LIT -- "是" --> LEAD_LIT_TASK
    LEAD_LIT_TASK --> LEAD

    LEAD_LIT -- "否" --> CHALLENGER

    CHALLENGER --> HAS_Q

    HAS_Q -- "是" --> MULTI_Q
    MULTI_Q -- "是" --> SELECT
    MULTI_Q -- "否" --> AUTO

    HAS_Q -- "否" --> HAS_REVISE
    HAS_REVISE -- "是" --> REPAIR
    REPAIR -- "resolved + SDK validation 通过" --> AUTO
    REPAIR -- "cannot_repair；原 Candidate 保留" --> AUTO
    REPAIR -- "非法 contract / 冻结边界校验失败" --> STOP_Q
    HAS_REVISE -- "否 / 全 reject" --> STOP_Q
    STOP_Q -- "需重新形成 / 审查 Candidate；安全 resume" --> LEAD
    STOP_Q -- "继续冻结目标 Investigation；安全 resume" --> INV_CTX


    %% =========================================================
    %% 2. Stable Hypothesis Identity + Lock
    %% =========================================================

    SELECT --> LOCK
    AUTO --> LOCK

    LOCK["SDK：Hypothesis Lock<br/>─────────────<br/>冻结本 Cycle：<br/>• id = H-xxxx<br/>• hypothesis<br/>• scope<br/>• decisive_test<br/><br/>从 continuation Decision 的唯一 H parent 解析身份：<br/>• 无 evolution_operator → 复用 H id<br/>• 有 typed operator → 分配新 H id<br/>• 首 Cycle → 分配新 H id<br/><br/>只写一个 cycle criterion：C-1<br/>Catalog 只保存 {id, hypothesis, scope}<br/>之后才进入 Canvas / Investigation"]

    LOCK --> H_DISPLAY

    H_DISPLAY["Research Lead<br/>hypothesis_display<br/>─────────────<br/>读取冻结的 hypothesis / scope / decisive_test<br/>输出 canvas_title / canvas_claim / canvas_conditions<br/><br/>科学命题的忠实压缩，不是文案润色或新判断<br/>保留具体主张与适用条件<br/>不强化、不扩展、不裁决科研结果<br/>当前认识状态由 SDK 添加"]

    H_DISPLAY --> CANVAS_H

    CANVAS_H["SDK：Canvas Hypothesis Projection<br/>─────────────<br/>Hypothesis node：<br/>type: Hypothesis<br/>id: H-xxxx<br/><br/>H node id 就是长期 identity<br/>延续时复用原节点；派生时创建新节点<br/>新派生 H 必须有 incoming lineage edge<br/>lineage anchor 校验失败 → fail closed<br/>child 按 parent-local 最近 lane 布局<br/>Evidence / history 用 hypothesis_ref 引用 node id<br/><br/>catalog / Canvas identity 冲突<br/>→ fail closed"]


    %% =========================================================
    %% 3. Investigation
    %% =========================================================

    CANVAS_H --> INV_CTX

    INV_CTX["SDK：组装 Investigator Context<br/>─────────────<br/>Scientific payload 仍最小：<br/>• hypothesis<br/>• scope（若有）<br/>• decisive_test<br/><br/>按需 handoff：<br/>• cannot_repair reason + frozen blockers（仅适用时）<br/>• previous_scientific_delta（跨 executor / fresh recovery）<br/>• Judge objection（仅 rework）<br/>• actionable-no-progress corrective directive（仅首次重试）<br/><br/>研究仍受 frozen_objective 与 allowed inputs 约束<br/><br/>Runtime navigation boundary：<br/>• Task root = scientific read anchor<br/>• Cycle workspace = write root<br/>• 空 Evidence Registry ≠ 输入缺失<br/><br/>不注入文件清单 / source manifest payload<br/>不注入 hypothesis_ref / C-1 / Evidence refs"]

    INV_A["Investigator LLM<br/>investigate / integrate_literature / judge_rework<br/>─────────────<br/>一次调用内完成当前必要且可执行研究<br/>自主组合理论 / 数值 / local source review<br/>本地材料从 Task root 聚焦发现<br/>新计算/产物只写 Cycle workspace<br/><br/>提交可供 Judge 审查的 findings：<br/>• findings[].statement（唯一必需）<br/>• 不重复声明类型、边界和本地引用列表<br/>• strong identity → optional provenance<br/>• Final Formatting 保留 A 层 Finding 数量/顺序<br/><br/>不输出 remaining_scientific_question<br/>不选择下一 executor"]

    CLOSE_C["C：SDK Deterministic Closure<br/>─────────────<br/>• 绑定 finding refs<br/>• 注册 Evidence<br/>• hypothesis_ref + cycle_id + criterion_id<br/>• producer invocation linkage<br/>• 更新 compact Evidence / Coverage<br/>• 保存 Investigator 提交与确定性闭合"]



    LIT_Q["SDK：queue Literature task<br/>＋ Scholarly Retrieval<br/>─────────────<br/>仅 Judge objection 授权的 external scholarly gap"]

    LIT["Literature LLM<br/>─────────────<br/>landscape / targeted_source / novelty_check<br/>每个独立 task fresh thread"]

    LIT_C["SDK Literature Closure<br/>─────────────<br/>验证并注册<br/>criterion-bindable<br/>Literature Evidence"]

    NONSCI["SDK Non-scientific Closure<br/>─────────────<br/>不调用 / 不伪造 Judge scientific verdict<br/>不修改 Scientific Model<br/>不创建 Canvas Decision<br/><br/>已 Lock 的 stable H id<br/>与 durable identity index 必须保留"]

    READY["SDK：materialize Judge packet<br/>Compact Investigation Result<br/>─────────────<br/>Investigator 完整提交后物化<br/>仅投影 Judge 所需科学语义<br/>无 Evidence / transport blocked<br/>由 SDK 确定性收口"]

    INV_CTX --> INV_A
    INV_A --> CLOSE_C
    CLOSE_C --> READY
    INV_A -- "transport blocked" --> NONSCI
    CLOSE_C -- "无可裁决 Evidence" --> NONSCI
    LIT_Q --> LIT
    LIT --> LIT_C
    LIT_C --> INV_CTX


    %% =========================================================
    %% 4. Independent Judge
    %% =========================================================

    READY --> JUDGE

    JUDGE["Judge<br/>terminal_assessment · v7<br/>─────────────<br/>只读取最小科学语义：<br/>• hypothesis / scope? / decisive_test<br/>• current scientific findings<br/>• prior objection 语义（若 reassessment）<br/>• same-H 已裁决 scientific outcomes<br/><br/>不读取 C-1 / Evidence-objection IDs / attempt-round<br/>不读取历史 Evidence ledger / routing bookkeeping"]

    J_OUT{"Judge 判断"}

    TERMINAL["Terminal scientific verdict<br/>─────────────<br/>• supported<br/>• rejected<br/>• milestone_advanced<br/>• open"]

    OBJECTION["Bounded scientific rework request<br/>─────────────<br/>verdict = null<br/>+ exactly one objection<br/><br/>Agent 只描述：<br/>• requested_resolution<br/>• needs_external_scholarship<br/><br/>不再分类 theoretical / numerical / local-source<br/>不再给 search hints"]

    AUTH["SDK Rework Closure + Authorization<br/>─────────────<br/>自动派生：<br/>• C-1 binding + reason=rationale<br/>• objection_id / source_gap_id<br/>• false→Investigator / true→Literature<br/>• Literature task / rework_plan<br/><br/>再检查 executability + budget"]

    REWORK_TARGET{"target executor"}

    FORCE_OPEN["SDK terminalize → open<br/>─────────────<br/>rework 未授权 /<br/>budget exhausted /<br/>resource unavailable"]

    JUDGE --> J_OUT

    J_OUT -- "terminal" --> TERMINAL
    J_OUT -- "需要一个局部科学缺口" --> OBJECTION

    OBJECTION --> AUTH

    AUTH -- "授权" --> REWORK_TARGET
    AUTH -- "拒绝 / budget exhausted" --> FORCE_OPEN

    REWORK_TARGET -- "needs_external_scholarship=false" --> INV_CTX
    REWORK_TARGET -- "needs_external_scholarship=true" --> LIT_Q


    %% =========================================================
    %% 5. Cross-Cycle H semantics
    %% =========================================================

    TERMINAL --> H_STATE
    FORCE_OPEN --> H_STATE

    H_STATE["SDK：Hypothesis Lifecycle<br/>─────────────<br/>Catalog 是 identity index：<br/>{id, hypothesis, scope}<br/><br/>scientific_status 与 frontier_status<br/>由 Canvas 节点分别维护<br/>不再保存 live / terminal 第三套状态"]


    %% =========================================================
    %% 6. Scientific Synthesis
    %% =========================================================

    H_STATE --> SYNTH

    SYNTH["Research Lead<br/>cycle_synthesis<br/>─────────────<br/>fresh epistemic episode<br/><br/>最小输入：<br/>• active_hypothesis<br/>• judge_decision<br/>• scientific_access<br/><br/>按需自由读取：<br/>• Scientific Model path<br/>• Evidence Manifest path<br/><br/>输出本 Cycle 科研推进：<br/>• conclusions[] → Established<br/>• open_questions[] → Unresolved<br/>• action=keep|replace<br/>• model（仅 replace）<br/><br/>不生成下一 Frontier"]

    MODEL_ACT{"Scientific Model action"}

    KEEP["keep<br/>─────────────<br/>长期 Scientific Model 不变"]

    REPLACE["replace<br/>─────────────<br/>生成完整新版<br/>Scientific Model"]

    MODEL_COMMIT["SDK Scientific Model Commit<br/>─────────────<br/>• Evidence ref validation<br/>• model validation<br/>• atomic persist<br/>• version update"]

    SYNTH --> MODEL_ACT
    MODEL_ACT -- "keep" --> KEEP
    MODEL_ACT -- "replace" --> REPLACE

    KEEP --> MODEL_COMMIT
    REPLACE --> MODEL_COMMIT


    %% =========================================================
    %% 7. Continuation context
    %% =========================================================

    MODEL_COMMIT --> CONTEXT

    CONTEXT["SDK：记录 continuation_context<br/>─────────────<br/>只保存已发生事实：<br/>• source Cycle / hypothesis_ref<br/>• Judge verdict / rationale<br/>• Scientific Model version<br/>• scientific_synthesis<br/>• Cycle scientific state digest<br/><br/>明确清空 next-candidate authority<br/>此处不选择下一 Hypothesis"]


    %% =========================================================
    %% 8. Reporter + Canvas
    %% =========================================================

    CONTEXT --> REPORT

    REPORT["Reporter<br/>report<br/>─────────────<br/>根据已裁决科学状态<br/>写本 Cycle 人类可读科研报告<br/><br/>不生成下一 Frontier"]

    CANVAS_TEXT["同一个 Reporter thread<br/>canvas_maintenance<br/>─────────────<br/>只负责 human-readable copy：<br/>• Decision title<br/>• Decision summary / rationale<br/><br/>不决定 node ID / parent / Evidence refs"]

    CANVAS_CLOSE["SDK Canvas Semantic Closure<br/>＋ Validator / Applier<br/>─────────────<br/>• 创建本 Cycle Decision Node<br/>• parent = 当前稳定 H Node<br/>• 绑定真实 Evidence refs / note link<br/>• 追加 Cycle scientific state：<br/>  Established / Unresolved / Boundary<br/>• status<br/>• 历史 numeric Decision → historical-cN-decision<br/>• child 按 lineage parent 相邻布局<br/><br/>不创建下一 H"]

    ANCHOR["SDK Frontier State Closure<br/>─────────────<br/>continuation_anchor = 当前 Decision<br/>candidates = []<br/>selected_candidate = null<br/>next_start_node_id = null"]

    CLOSED(["Cycle N OVER<br/>─────────────<br/>Cycle status = over<br/>Goal 状态独立：goal_status = active | passed"])

    REPORT --> CANVAS_TEXT
    CANVAS_TEXT --> CANVAS_CLOSE
    CANVAS_CLOSE --> ANCHOR
    ANCHOR --> CLOSED


    %% =========================================================
    %% 9. Next Cycle
    %% =========================================================

    CLOSED --> BUDGET{"同一次 invocation<br/>还有 Cycle budget？"}

    BUDGET -- "否" --> END(["Invocation 结束"])

    BUDGET -- "是" --> NEXT_CTX["Cycle N+1 runtime 初始化<br/>─────────────<br/>SDK 提供：<br/>• Scientific Model<br/>• Hypothesis Catalog<br/>• previous Decision anchor<br/>• compact prior Cycle scientific state<br/><br/>没有预生成下一 H"]

    NEXT_CTX --> LEAD"""


def _mermaid_node_ids(source: str) -> tuple[str, ...]:
    ids: list[str] = []
    seen: set[str] = set()
    for line in source.splitlines():
        match = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*[\[\{\(]", line)
        if match and match.group(1) not in seen:
            seen.add(match.group(1))
            ids.append(match.group(1))
    return tuple(ids)


def flow_node_ids() -> tuple[str, ...]:
    """Stable Mermaid node IDs that change requests may target."""
    return _mermaid_node_ids(CURRENT_FLOW_MERMAID)


def _after_flow_mermaid(request: dict) -> str:
    projection = request.get("after_flow_projection") if isinstance(request.get("after_flow_projection"), dict) else None
    if not projection:
        return CURRENT_FLOW_MERMAID
    remove_nodes = set(projection.get("remove_nodes", ()))
    replace_nodes = projection.get("replace_nodes") if isinstance(projection.get("replace_nodes"), dict) else {}
    lines: list[str] = []
    for line in CURRENT_FLOW_MERMAID.splitlines():
        node_match = re.match(r"^(\s*)([A-Z][A-Z0-9_]*)\s*[\[\{\(]", line)
        if node_match:
            node_id = node_match.group(2)
            if node_id in remove_nodes:
                continue
            replacement = replace_nodes.get(node_id)
            if isinstance(replacement, str) and replacement.strip():
                lines.append(node_match.group(1) + replacement.strip())
                continue
        if "-->" in line and any(re.search(rf"\b{re.escape(node)}\b", line) for node in remove_nodes):
            continue
        lines.append(line)
    add_edges = projection.get("add_edges") if isinstance(projection.get("add_edges"), list) else []
    if add_edges:
        lines.append("")
        lines.append("    %% After Change projection edges")
        for edge in add_edges:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("from") or "").strip()
            target = str(edge.get("to") or "").strip()
            label = str(edge.get("label") or "").strip()
            if source and target:
                lines.append(f'    {source} -- "{label}" --> {target}' if label else f"    {source} --> {target}")
    return "\n".join(lines)


def current_flow_body(requests: tuple[dict, ...] = (), selected_request_id: str | None = None, *, view: str = "current") -> str:
    """Render Current Flow plus one selected formal change-request impact or after-change preview."""
    selected = next((item for item in requests if item.get("request_id") == selected_request_id), None)
    affected = tuple(selected.get("affected_flow_nodes", ())) if selected else ()
    has_after = bool(selected and isinstance(selected.get("after_flow_projection"), dict))
    after_mode = view == "after" and has_after
    # Keep the graph canonical while highlighting this request's affected nodes.
    current_overlay = CURRENT_FLOW_MERMAID
    current_nodes = set(flow_node_ids())
    current_affected = tuple(node for node in affected if node in current_nodes)
    if current_affected:
        current_overlay += "\n\n    classDef requestImpact fill:#ffe3e3,stroke:#c22,stroke-width:4px,color:#4a0000;\n"
        current_overlay += "    class " + ",".join(current_affected) + " requestImpact;\n"
    def diagram_panel(title: str, source: str, raw_source: str, *, extra_class: str = "") -> str:
        panel_class = "flow-version" + (f" {extra_class}" if extra_class else "")
        return (
            f'<section class="{panel_class}"><div class="flow-version-head"><h2>{html.escape(title)}</h2>'
            '<div class="diagram-toolbar" aria-label="Diagram zoom controls">'
            '<button type="button" data-zoom-action="out" title="Zoom out">−</button>'
            '<button type="button" data-zoom-action="reset" class="zoom-value" title="Reset zoom">100%</button>'
            '<button type="button" data-zoom-action="in" title="Zoom in">＋</button>'
            '</div></div><div class="mermaid-wrap" title="Drag to pan; Ctrl/⌘ + wheel to zoom"><pre class="mermaid">'
            + html.escape(source)
            + '</pre></div><details class="mermaid-source"><summary>'
            + html.escape(title)
            + ' Mermaid 源码</summary><pre>'
            + html.escape(raw_source)
            + '</pre></details></section>'
        )

    if after_mode and selected:
        after_base = _after_flow_mermaid(selected)
        after_nodes = set(_mermaid_node_ids(after_base))
        changed_after_nodes = tuple(node for node in affected if node in after_nodes)
        after_overlay = after_base
        if changed_after_nodes:
            after_overlay += "\n\n    classDef afterImpact fill:#d7f8de,stroke:#198754,stroke-width:4px,color:#063d1d;\n"
            after_overlay += "    class " + ",".join(changed_after_nodes) + " afterImpact;\n"
        diagrams = diagram_panel("Current Workflow · 实际运行", current_overlay, CURRENT_FLOW_MERMAID) + diagram_panel(
            f"Proposal Preview · {selected.get('request_id')}", after_overlay, after_base, extra_class="after-version"
        )
    else:
        diagrams = diagram_panel("Current Workflow · 实际运行", current_overlay, CURRENT_FLOW_MERMAID)
    flow_stack_class = "flow-stack comparison-mode" if after_mode else "flow-stack"
    terminal_statuses = {"validated", "rejected", "cancelled", "superseded", "closed"}
    active_requests = tuple(item for item in requests if str(item.get("status", "")).strip().lower() not in terminal_statuses)
    historical_requests = tuple(item for item in requests if str(item.get("status", "")).strip().lower() in terminal_statuses)

    def request_rows(items: tuple[dict, ...]) -> str:
        return "".join(
            f'<a class="request-row{" selected" if item.get("request_id") == selected_request_id else ""}" href="/current-flow/?request={html.escape(str(item.get("request_id")), quote=True)}"><strong>{html.escape(str(item.get("request_id")))}</strong><span>{html.escape(str(item.get("priority")))}</span><small>{html.escape(str(item.get("title")))}</small></a>'
            for item in items
        )

    active_request_links = request_rows(active_requests) or '<div class="muted request-empty">暂无未结束修改提请。</div>'
    historical_request_links = request_rows(historical_requests) or '<div class="muted request-empty">暂无历史提请。</div>'
    selected_is_historical = bool(
        selected and str(selected.get("status", "")).strip().lower() in terminal_statuses
    )
    history_open = " open" if selected_is_historical else ""
    if selected:
        problem = selected.get("problem") if isinstance(selected.get("problem"), dict) else {}
        impacts = "".join(
            f'<button type="button" class="impact-link" data-flow-node="{html.escape(node, quote=True)}">{html.escape(node)}</button>'
            for node in affected
        ) or '<div class="no-impact">无 Scientific Current Flow 节点影响；本提请仅涉及基础设施或可观测性。</div>'
        request_id = html.escape(str(selected.get("request_id")), quote=True)
        view_switch = ""
        if has_after:
            current_class = " active" if not after_mode else ""
            after_class = " active" if after_mode else ""
            view_switch = f'<div class="view-switch"><a class="view-button{current_class}" href="/current-flow/?request={request_id}">真实 Current</a><a class="view-button{after_class}" href="/current-flow/?request={request_id}&amp;view=after">Proposal Preview</a></div>'
        preview_note = ""
        if after_mode:
            note = str(selected.get("after_flow_projection", {}).get("note") or "")
            preview_note = f'<div class="after-note"><strong>Proposal Preview</strong><br>上半部分仍是唯一真实 Current Workflow；下半部分只是当前提请的修改后投影，不代表 runtime 已实施。{("<br>" + html.escape(note)) if note else ""}</div>'
        current_failure = html.escape(str(selected.get("current_failure_mode", "")))
        design_motivation = html.escape(str(selected.get("design_motivation", "")))
        proposal = html.escape(str(selected.get("proposal", "")))
        cost = selected.get("cost_audit") if isinstance(selected.get("cost_audit"), dict) else {}
        risk = selected.get("risk_audit") if isinstance(selected.get("risk_audit"), dict) else {}
        validation_items = selected.get("validation") if isinstance(selected.get("validation"), list) else []
        validation_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in validation_items)
        cost_html = "<br>".join(f"<strong>{html.escape(str(key))}</strong>：{html.escape(str(value))}" for key, value in cost.items())
        risk_html = "<br>".join(f"<strong>{html.escape(str(key))}</strong>：{html.escape(str(value))}" for key, value in risk.items())
        detail = f"""<div class="request-detail"><h2>{html.escape(str(selected.get("request_id")))}</h2><p><span class="badge">{html.escape(str(selected.get("priority")))}</span> <span class="badge">{html.escape(str(selected.get("status")))}</span></p><h3>{html.escape(str(selected.get("title")))}</h3>{view_switch}{preview_note}<p>{html.escape(str(selected.get("summary")))}</p><h4>当前作用</h4><p><strong>Agent / SDK：</strong>{html.escape(str(problem.get("owner", "")))}<br><strong>环节：</strong>{html.escape(str(problem.get("stage", "")))}<br>当前由上述 Agent / SDK 在该环节承担对应的流程职责与 authority。</p><h4>目前问题</h4><p>{html.escape(str(problem.get("issue", "")))}</p><p>{current_failure}</p><h4>原设计动机</h4><p>{design_motivation}</p><h4>修改 / 修正方案</h4><p>{proposal}</p><h4>受影响流程位置</h4><div class="impact-list">{impacts}</div><h4>成本审计</h4><p>{cost_html}</p><h4>风险审计</h4><p>{risk_html}</p><h4>验证</h4><ul>{validation_html}</ul><p><a href="/current-flow/">返回 Workflow</a> · <a href="/change-requests/">管理修改提请</a></p></div>"""
    else:
        detail = '<div class="request-detail"><h2>修改提请</h2><p>选择提请查看详情；有修改后投影时，可进入 Proposal Preview 对照当前流程。</p><p><a href="/change-requests/">创建 / 管理修改提请</a></p></div>'
    return f"""
<style>
.flow-layout {{ --request-panel-width:360px; display:grid; grid-template-columns:minmax(460px,1fr) 18px minmax(280px,var(--request-panel-width)); gap:0; align-items:start; }}
.flow-main {{ min-width:0; height:calc(100vh - 32px); height:calc(100dvh - 32px); min-height:520px; position:sticky; top:16px; }}
.flow-stack {{ height:100%; min-height:0; display:grid; grid-template-rows:minmax(0,1fr); gap:0; }}
.flow-stack.comparison-mode {{ grid-template-rows:minmax(0,1fr) minmax(0,1fr); gap:10px; }}
.flow-version {{ min-width:0; min-height:0; display:grid; grid-template-rows:auto minmax(0,1fr) auto; overflow:hidden; }}
.flow-version-head {{ min-width:0; display:flex; align-items:center; justify-content:space-between; gap:10px; margin:0 0 8px; }}
.flow-version-head h2 {{ margin:0; font-size:1.05rem; }}
.after-version {{ padding-top:8px; border-top:2px solid color-mix(in srgb, #198754 50%, transparent); }}
.diagram-toolbar {{ display:flex; align-items:center; gap:5px; flex:0 0 auto; }}
.diagram-toolbar button {{ min-width:34px; height:30px; padding:3px 8px; border:1px solid color-mix(in srgb, currentColor 25%, transparent); border-radius:7px; background:Canvas; color:CanvasText; cursor:pointer; }}
.diagram-toolbar .zoom-value {{ min-width:58px; font-variant-numeric:tabular-nums; }}
.flow-splitter {{ position:sticky; top:16px; align-self:start; width:18px; height:calc(100vh - 32px); height:calc(100dvh - 32px); min-height:520px; padding:0; border:0; background:transparent; cursor:col-resize; touch-action:none; display:flex; align-items:center; justify-content:center; color:color-mix(in srgb, currentColor 62%, transparent); }}
.flow-splitter::before {{ content:""; width:4px; height:72px; border-radius:999px; background:currentColor; opacity:.55; transition:opacity .15s, width .15s; }}
.flow-splitter:hover::before, .flow-splitter:focus-visible::before, .flow-splitter.is-resizing::before {{ width:6px; opacity:1; }}
.flow-splitter:focus-visible {{ outline:2px solid Highlight; outline-offset:-2px; }}
.request-panel {{ position:sticky; top:16px; max-height:calc(100vh - 32px); overflow:auto; border:1px solid color-mix(in srgb, currentColor 20%, transparent); border-radius:12px; padding:12px; }}
.request-section-title {{ margin:4px 0 8px; font-size:.92rem; opacity:.78; }}
.request-empty {{ padding:8px 4px 12px; }}
.request-history {{ margin-top:14px; border-top:1px solid color-mix(in srgb, currentColor 18%, transparent); padding-top:10px; }}
.request-history > summary {{ cursor:pointer; font-weight:650; opacity:.78; padding:4px 0 8px; }}
.request-history[open] > summary {{ opacity:1; }}
.request-row {{ display:grid; grid-template-columns:1fr auto; gap:3px 8px; padding:9px; margin:5px 0; border-radius:8px; text-decoration:none; border:1px solid color-mix(in srgb, currentColor 14%, transparent); }}
.request-row small {{ grid-column:1 / -1; opacity:.72; }}
.request-row.selected {{ border-color:#d11; box-shadow:0 0 0 2px color-mix(in srgb, #d11 25%, transparent); }}
.impact-list {{ display:flex; gap:6px; flex-wrap:wrap; }}
.impact-link {{ border:1px solid #d11; border-radius:999px; padding:5px 9px; cursor:pointer; }}
.view-switch {{ display:flex; gap:8px; margin:10px 0 12px; }}
.view-button {{ text-decoration:none; border:1px solid currentColor; border-radius:8px; padding:7px 10px; font-weight:650; }}
.view-button.active {{ box-shadow:0 0 0 2px color-mix(in srgb, currentColor 22%, transparent); }}
.after-note {{ margin:10px 0 12px; padding:10px; border:1px solid #c68a00; border-radius:8px; background:color-mix(in srgb, #c68a00 10%, transparent); }}
.no-impact {{ padding:10px; border:1px dashed currentColor; border-radius:8px; opacity:.78; }}
.mermaid-wrap {{ width:100%; height:100%; min-height:0; overflow:auto; overscroll-behavior:contain; border:1px solid color-mix(in srgb, currentColor 20%, transparent); border-radius:12px; background:Canvas; padding:16px; box-sizing:border-box; cursor:grab; }}
.mermaid-wrap.is-panning {{ cursor:grabbing; user-select:none; }}
.mermaid-wrap .mermaid {{ min-width:1100px; display:flex; justify-content:center; transform-origin:top center; }}
.mermaid-wrap svg {{ max-width:none !important; height:auto; }}
.flow-focus {{ filter:drop-shadow(0 0 10px #d11); }}
details.mermaid-source {{ margin-top:8px; max-height:35%; overflow:auto; border:1px solid color-mix(in srgb, currentColor 20%, transparent); border-radius:10px; padding:7px 10px; }}
details.mermaid-source pre {{ overflow:auto; white-space:pre; font-size:.82rem; line-height:1.45; }}
@media (max-width:1000px) {{ .flow-layout {{ grid-template-columns:1fr; gap:16px; }} .flow-main {{ position:static; height:calc(100dvh - 24px); min-height:440px; }} .flow-splitter {{ display:none; }} .request-panel {{ position:static; max-height:none; }} }}
</style>
<h1>Sciver Workflow</h1>
<p class="small">这里展示当前生效的科学流程。右侧 TMCR 用于查看修改提请；进入 Proposal Preview 时可与拟议变更后的流程并排对照。</p>
<div class="flow-layout"><div class="flow-main"><div class="{flow_stack_class}">{diagrams}</div></div><button type="button" class="flow-splitter" aria-label="调整流程图与修改提请区域宽度" title="拖动调整左右宽度；双击恢复默认；方向键微调"></button><aside class="request-panel"><h3>修改提请</h3><div class="request-section-title">进行中</div>{active_request_links}<details class="request-history"{history_open}><summary>历史提请（{len(historical_requests)}）</summary>{historical_request_links}</details>{detail}</aside></div>
<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
const dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
mermaid.initialize({{startOnLoad:false,theme:dark ? 'dark' : 'default',securityLevel:'strict',flowchart:{{htmlLabels:true,useMaxWidth:false,curve:'basis'}}}});
await mermaid.run({{querySelector:'.mermaid'}});
const flowLayout = document.querySelector('.flow-layout');
const flowSplitter = document.querySelector('.flow-splitter');
if (flowLayout && flowSplitter) {{
  const defaultPanelWidth = 360;
  const splitterWidth = 18;
  const minPanelWidth = 280;
  const minFlowWidth = 460;
  const clampPanelWidth = (width) => {{
    const maxPanelWidth = Math.max(minPanelWidth, flowLayout.clientWidth - minFlowWidth - splitterWidth);
    return Math.min(maxPanelWidth, Math.max(minPanelWidth, width));
  }};
  const currentPanelWidth = () => {{
    const value = parseFloat(getComputedStyle(flowLayout).getPropertyValue('--request-panel-width'));
    return Number.isFinite(value) ? value : defaultPanelWidth;
  }};
  const setPanelWidth = (width) => {{
    const next = clampPanelWidth(width);
    flowLayout.style.setProperty('--request-panel-width', next + 'px');
    flowSplitter.setAttribute('aria-valuetext', Math.round(next) + 'px right panel');
  }};
  let resizePointerId = null;
  flowSplitter.addEventListener('pointerdown', (event) => {{
    if (event.button !== 0) return;
    resizePointerId = event.pointerId;
    flowSplitter.setPointerCapture(event.pointerId);
    flowSplitter.classList.add('is-resizing');
    event.preventDefault();
  }});
  flowSplitter.addEventListener('pointermove', (event) => {{
    if (resizePointerId !== event.pointerId) return;
    const rect = flowLayout.getBoundingClientRect();
    setPanelWidth(rect.right - event.clientX);
  }});
  const stopResize = (event) => {{
    if (resizePointerId !== event.pointerId) return;
    resizePointerId = null;
    flowSplitter.classList.remove('is-resizing');
  }};
  flowSplitter.addEventListener('pointerup', stopResize);
  flowSplitter.addEventListener('pointercancel', stopResize);
  flowSplitter.addEventListener('dblclick', () => setPanelWidth(defaultPanelWidth));
  flowSplitter.addEventListener('keydown', (event) => {{
    if (event.key === 'ArrowLeft') {{
      setPanelWidth(currentPanelWidth() + 24);
      event.preventDefault();
    }} else if (event.key === 'ArrowRight') {{
      setPanelWidth(currentPanelWidth() - 24);
      event.preventDefault();
    }} else if (event.key === 'Home') {{
      setPanelWidth(defaultPanelWidth);
      event.preventDefault();
    }}
  }});
  window.addEventListener('resize', () => setPanelWidth(currentPanelWidth()));
  setPanelWidth(defaultPanelWidth);
}}
for (const pane of document.querySelectorAll('.flow-version')) {{
  const viewport = pane.querySelector('.mermaid-wrap');
  const diagram = pane.querySelector('.mermaid');
  const zoomValue = pane.querySelector('.zoom-value');
  if (!viewport || !diagram || !zoomValue) continue;
  let zoom = 1;
  const setZoom = (next) => {{
    zoom = Math.min(2.5, Math.max(0.4, next));
    diagram.style.zoom = String(zoom);
    zoomValue.textContent = Math.round(zoom * 100) + '%';
  }};
  pane.querySelector('[data-zoom-action="out"]')?.addEventListener('click', () => setZoom(zoom - 0.1));
  pane.querySelector('[data-zoom-action="reset"]')?.addEventListener('click', () => setZoom(1));
  pane.querySelector('[data-zoom-action="in"]')?.addEventListener('click', () => setZoom(zoom + 0.1));
  viewport.addEventListener('wheel', (event) => {{
    if (!(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    setZoom(zoom + (event.deltaY < 0 ? 0.1 : -0.1));
  }}, {{passive:false}});

  let drag = null;
  viewport.addEventListener('pointerdown', (event) => {{
    if (event.button !== 0 || event.target.closest('button,a,summary')) return;
    drag = {{x:event.clientX, y:event.clientY, left:viewport.scrollLeft, top:viewport.scrollTop, pointerId:event.pointerId}};
    viewport.setPointerCapture(event.pointerId);
    viewport.classList.add('is-panning');
    event.preventDefault();
  }});
  viewport.addEventListener('pointermove', (event) => {{
    if (!drag || drag.pointerId !== event.pointerId) return;
    viewport.scrollLeft = drag.left - (event.clientX - drag.x);
    viewport.scrollTop = drag.top - (event.clientY - drag.y);
  }});
  const stopPan = (event) => {{
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag = null;
    viewport.classList.remove('is-panning');
  }};
  viewport.addEventListener('pointerup', stopPan);
  viewport.addEventListener('pointercancel', stopPan);
}}
function findNode(name) {{ return Array.from(document.querySelectorAll('.mermaid-wrap svg g.node')).find(el => {{ const id = el.id || ''; return id === name || id.includes('-' + name + '-'); }}); }}
for (const button of document.querySelectorAll('.impact-link')) {{ button.addEventListener('click', () => {{ document.querySelectorAll('.flow-focus').forEach(el => el.classList.remove('flow-focus')); const node = findNode(button.dataset.flowNode); if (!node) return; node.classList.add('flow-focus'); node.scrollIntoView({{behavior:'smooth',block:'center',inline:'center'}}); }}); }}
</script>
"""
