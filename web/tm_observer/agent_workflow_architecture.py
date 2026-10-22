from __future__ import annotations

import html
import json
import re
from typing import Any

from .stage_flow import stage_detail_catalog, stage_workflow_mermaid


ROLE_LABELS = {
    "research_lead": "Research Lead",
    "challenger": "Challenger",
    "investigator": "Investigator",
    "literature": "Literature",
    "judge": "Judge",
    "reporter": "Reporter",
}

SDK_DETAILS: dict[str, dict[str, Any]] = {
    "sdk:qualification": {
        "role_label": "SDK / deterministic layer",
        "stage": "Qualification Router",
        "objective": "Route candidates from the current Challenger review using deterministic qualification rules.",
        "may_do": "Select a sole qualified candidate; request Lead selection when multiple candidates qualify; allow one bounded repair path when the review returns revise.",
        "must_not": "Treat all-rejected candidates as qualified, silently ignore invalid contracts, or bypass frozen-boundary validation.",
        "exit": "A validated candidate proceeds to Hypothesis Lock; unresolved qualification gets at most three different-angle proposal rounds before no_qualified_candidate, while invalid contracts stop safely.",
        "contract_rules": [
            "More than one qualified candidate requires Research Lead candidate_selection.",
            "selection_repair is bounded to one attempt.",
            "cannot_repair preserves the original Candidate and review; it is not itself a scientific rejection.",
        ],
    },
    "sdk:qualification-retry": {
        "role_label": "SDK / lifecycle",
        "stage": "Bounded qualification re-proposal",
        "objective": "Give the Research Lead a different scientific angle when the current qualification round has no qualified candidate.",
        "may_do": "Preserve prior proposal/review history and allow a new proposal only while the three-round qualification limit remains.",
        "must_not": "Reopen the same repaired Candidate indefinitely, bypass Challenger verification, or continue after the proposal-round limit.",
        "exit": "A remaining round returns to frontier_proposal; an exhausted limit closes as no_qualified_candidate.",
    },
    "sdk:needs-attention": {
        "role_label": "SDK / lifecycle",
        "stage": "No qualified candidate / safe stop",
        "objective": "Close qualification when the bounded proposal budget is exhausted or a contract/frozen-boundary failure requires intervention.",
        "may_do": "Preserve the candidate/review history and expose the explicit recovery condition.",
        "must_not": "Silently bypass a failed contract, fabricate a qualified candidate, or trigger an unbounded automatic repair loop.",
        "exit": "The task remains safely closed until a supported retry condition or explicit operator action is available.",
    },
    "sdk:judge-packet-gate": {
        "role_label": "SDK / deterministic layer",
        "stage": "Judge Packet Gate",
        "objective": "Materialize a Judge packet only after accepted, criterion-bound Evidence is ready for adjudication.",
        "may_do": "Check evidence closure, admissibility, and transport readiness before routing to terminal_assessment.",
        "must_not": "Send an empty or blocked packet to Judge, or treat an operational stop as a scientific verdict.",
        "exit": "Ready evidence routes to Judge; otherwise use the appropriate non-scientific closeout or authorized next executor.",
    },
    "sdk:identity-fail-closed": {
        "role_label": "SDK / deterministic layer",
        "stage": "H Identity / Lineage Fail-Closed",
        "objective": "Stop when stable H identity or lineage validation conflicts with the persisted scientific graph.",
        "may_do": "Preserve the conflicting evidence and enter the bounded safe-stop/recovery path.",
        "must_not": "Create a second identity, guess a parent, or continue with an inconsistent Canvas projection.",
        "exit": "The Cycle does not proceed until the authoritative identity and lineage checks pass.",
    },
    "sdk:hypothesis-lock": {
        "role_label": "SDK / deterministic layer",
        "stage": "Hypothesis Lock",
        "objective": "Freeze the scientific object that this Cycle is allowed to test.",
        "may_do": "Resolve stable H identity, validate lineage, bind hypothesis and scope, and freeze decisive_test before Investigation.",
        "must_not": "Let an Agent invent the durable H ID, continue through an identity conflict, or silently substitute a different decisive_test after lock.",
        "exit": "A consistent, locked H and criterion are available to the Investigation Router.",
        "contract_rules": [
            "Continuation without a typed evolution operator reuses the existing H identity.",
            "A typed evolution operator creates a derived H with a validated lineage edge.",
            "H identity is SQLite-backed; the Canvas is a checked projection, not a second authority.",
        ],
    },
    "sdk:context-router": {
        "role_label": "SDK / deterministic layer",
        "stage": "Investigation Context + Router",
        "objective": "Build the minimal context for the locked objective and route only the work needed for the current Cycle.",
        "may_do": "Supply the frozen hypothesis/scope/decisive_test, previous scientific delta when applicable, authorized Judge objections, and the task-root / Cycle-workspace boundaries.",
        "must_not": "Treat an empty Evidence Registry as proof that no inputs exist, inject a redundant source manifest, or delegate next-Cycle hypothesis choice to Investigator.",
        "exit": "The correct Investigator stage starts with the same frozen scientific objective and bounded writable workspace.",
        "contract_rules": [
            "Task root is the scientific read anchor; Cycle workspace is the write root.",
            "Only the current frozen objective is authoritative during Investigation and rework.",
        ],
    },
    "sdk:evidence-closure": {
        "role_label": "SDK / deterministic layer",
        "stage": "Semantic Closure · Evidence · Readiness",
        "objective": "Turn accepted Agent findings into criterion-bound evidence and decide whether a scientific Judge packet can be materialized.",
        "may_do": "Validate finding references, register Evidence, bind hypothesis/Cycle/criterion and producer invocation, then check closure and readiness.",
        "must_not": "Invent a scientific verdict, manufacture missing Evidence, or send a packet with transport or admissibility blockers to Judge.",
        "exit": "Adjudicable evidence routes to Judge; non-adjudicable work uses a non-scientific closeout or a source-authorized next executor.",
        "contract_rules": [
            "Judge sees the scientific meaning of findings, not SDK transport and routing bookkeeping.",
            "A no-Evidence / transport-blocked closeout does not create a scientific Decision or commit a new Scientific Model.",
        ],
    },
    "sdk:rework-gate": {
        "role_label": "SDK / deterministic layer",
        "stage": "Rework Authorization Gate",
        "objective": "Enforce the bounded, evidence-directed Judge rework contract.",
        "may_do": "Bind the objection to the frozen criterion, derive the allowed executor, and check executability, budget, and external-scholarship eligibility.",
        "must_not": "Allow open-ended retries, silently change the frozen objective, or route unavailable/duplicate work into an unbounded loop.",
        "exit": "Authorized work returns to the same H/test; denied, exhausted, or unavailable rework resolves to terminal open.",
        "contract_rules": [
            "A rework request contains one local scientific objection.",
            "Investigator handles non-external scientific resolution; Literature is used only for an authorized external scholarly gap.",
        ],
    },
    "sdk:forced-open": {
        "role_label": "SDK / deterministic layer",
        "stage": "Forced terminal open",
        "objective": "Close a rework path that cannot be safely or legitimately executed.",
        "may_do": "Record an open terminal outcome when rework is denied, budget is exhausted, or required resources are unavailable.",
        "must_not": "Present an unexecuted rework as evidence or claim the decisive test was resolved.",
        "exit": "Research Lead synthesis receives the terminal open decision and records the actual epistemic result.",
    },
    "sdk:model-commit": {
        "role_label": "SDK / deterministic layer",
        "stage": "Scientific Model Commit",
        "objective": "Persist a validated synthesis atomically after the terminal scientific decision.",
        "may_do": "Validate Evidence references and the complete replacement model, preserve the current version on keep, and atomically advance the version on replace.",
        "must_not": "Accept an incomplete model, lose the previous version on validation failure, or let Reporter determine scientific conclusions.",
        "exit": "The authoritative Scientific Model is unchanged or committed at a new version.",
    },
    "sdk:frontier-maintenance": {
        "role_label": "SDK / deterministic layer",
        "stage": "Frontier Maintenance Boundary",
        "objective": "Keep frontier labels separate from scientific outcomes and only maintain existing frontier tips.",
        "may_do": "Validate the current Research Lead status updates against the active H/status contract.",
        "must_not": "Invent new hypotheses or treat frontier status as a scientific Judge verdict.",
        "exit": "Existing tips have validated active/parked/closed status before human-readable closeout.",
    },
    "sdk:canvas-close": {
        "role_label": "SDK / deterministic layer",
        "stage": "Canvas Semantic Closure",
        "objective": "Persist the human-readable Decision projection while preserving SQLite-backed scientific identity and Evidence provenance.",
        "may_do": "Validate and apply the Decision node, attach real Evidence references, update scientific-state annotations, and write the continuation anchor.",
        "must_not": "Let Reporter choose node IDs, parent identity, Evidence references, or the next Hypothesis.",
        "exit": "Cycle status becomes OVER after its scientific artifacts and continuation facts are safely closed.",
        "contract_rules": [
            "The Decision belongs to the currently locked H.",
            "The next Cycle starts from the previous Decision anchor; it does not inherit a preselected next H.",
        ],
    },
    "sdk:cycle-over": {
        "role_label": "SDK / lifecycle",
        "stage": "Cycle OVER",
        "objective": "Mark this Cycle as ended after its closeout contract is satisfied.",
        "may_do": "Persist Cycle terminal state and preserve the continuation facts needed by a later Cycle.",
        "must_not": "Conflate the end of a Cycle with completion of the research Goal.",
        "exit": "Cycle status is OVER; Goal status remains a separate state axis.",
    },
    "sdk:goal-gate": {
        "role_label": "SDK / deterministic layer",
        "stage": "Master Goal Gate",
        "objective": "Evaluate Goal completion only at its authorized global gate.",
        "may_do": "Invoke Judge goal_completion when the terminal scientific verdict triggers the gate, then persist the separate Goal status.",
        "must_not": "Infer Goal completion merely because a Cycle is OVER or because a branch-level result is supported.",
        "exit": "Goal is passed or remains active; Cycle status remains OVER.",
    },
    "sdk:non-scientific-close": {
        "role_label": "SDK / deterministic layer",
        "stage": "Non-scientific Closeout",
        "objective": "Safely stop a Cycle when the submitted work cannot support a scientific adjudication.",
        "may_do": "Preserve valid operational evidence and close the invocation without inventing a scientific Judge verdict.",
        "must_not": "Commit a new Scientific Model or create a scientific Canvas Decision from inadmissible evidence.",
        "exit": "Operational/non-scientific closeout is explicit; no false scientific verdict is created.",
    },
    "sdk:preserve-close": {
        "role_label": "SDK / deterministic layer",
        "stage": "Authorized Research Preservation",
        "objective": "Archive existing findings and close the Cycle through the SDK when preserve_research is explicitly authorized.",
        "may_do": "Persist the existing findings and display material under the bounded preservation contract.",
        "must_not": "Pretend preservation is a scientific verdict or run Judge, Literature, Lead synthesis, or Reporter downstream.",
        "exit": "Cycle reaches OVER through the preservation path without a Judge verdict.",
    },
    "sdk:cycle-budget": {
        "role_label": "SDK / lifecycle",
        "stage": "Cycle Budget Gate",
        "objective": "Determine whether this invocation may start another Cycle.",
        "may_do": "Start the next Cycle only when the invocation's remaining Cycle budget permits it.",
        "must_not": "Create an extra Cycle after the budget is exhausted.",
        "exit": "Continue with next-Cycle context or end the invocation.",
    },
    "sdk:next-context": {
        "role_label": "SDK / deterministic layer",
        "stage": "Next-Cycle Context",
        "objective": "Carry forward established state without pre-authorizing the next scientific hypothesis.",
        "may_do": "Provide the Scientific Model, Hypothesis Catalog, previous Decision anchor, and compact prior-Cycle scientific state.",
        "must_not": "Pre-generate or select the next H before the new Research Lead proposal.",
        "exit": "A fresh frontier_proposal begins from the actual persisted scientific state.",
    },
}


def _short(value: object, limit: int = 124) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    shortened = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return (shortened or text[: limit - 1]) + "…"


def _node_key(role: str, stage: str) -> str:
    return f"{role}__{stage}"


def _stage_node(
    details: dict[str, dict[str, Any]],
    active_source: str,
    role: str,
    stage: str,
    *,
    title: str | None = None,
    summary: str | None = None,
    conditional: bool = False,
    compact: bool = False,
) -> str:
    key = _node_key(role, stage)
    item = details.get(key)
    present = item is not None and key in active_source
    role_label = str(item.get("role_label") if item else ROLE_LABELS.get(role, role))
    label = title or stage
    copy = summary or (item.get("objective") if item else "") or "Stage metadata is not available in the current source."
    if not present:
        copy = "Current Stage Contract is missing from the live workflow source; review the overview before relying on this path."
    classes = "aa-node aa-agent"
    if compact:
        classes += " aa-node-compact"
    if not present:
        classes += " aa-node-missing"
    cond_attr = "true" if conditional else "false"
    disabled = " disabled" if not present else ""
    return (
        f'<button type="button" class="{classes}" data-aa-detail-key="{html.escape(key, quote=True)}" '
        f'data-aa-stage-key="{html.escape(key, quote=True)}" data-role="{html.escape(role, quote=True)}" '
        f'data-aa-conditional="{cond_attr}" aria-pressed="false" '
        f'aria-label="{html.escape(role_label + ", " + label, quote=True)}"{disabled}>'
        f'<span class="aa-node-meta"><span>{html.escape(role_label)}</span><span class="aa-node-kind">AGENT</span></span>'
        f'<strong>{html.escape(label)}</strong>'
        f'<span class="aa-node-copy">{html.escape(_short(copy, 100 if compact else 124))}</span>'
        '</button>'
    )


def _sdk_node(
    key: str,
    title: str,
    summary: str,
    *,
    conditional: bool = False,
    terminal: bool = False,
    compact: bool = False,
) -> str:
    classes = "aa-node aa-sdk"
    if terminal:
        classes += " aa-terminal"
    if compact:
        classes += " aa-node-compact"
    conditional_value = "true" if conditional else "false"
    return (
        f'<button type="button" class="{classes}" data-aa-detail-key="sdk:{html.escape(key, quote=True)}" '
        f'data-aa-conditional="{conditional_value}" aria-pressed="false" '
        f'aria-label="SDK, {html.escape(title, quote=True)}">'
        f'<span class="aa-node-meta"><span>SDK / Runtime</span><span class="aa-node-kind">DETERMINISTIC</span></span>'
        f'<strong>{html.escape(title)}</strong><span class="aa-node-copy">{html.escape(_short(summary))}</span>'
        '</button>'
    )


def _route(nodes: list[str], *, label: str = "normal route") -> str:
    parts: list[str] = []
    for index, node in enumerate(nodes):
        if index:
            parts.append('<span class="aa-arrow" aria-hidden="true"><span>→</span></span>')
        parts.append(node)
    return f'<div class="aa-route" aria-label="{html.escape(label, quote=True)}">' + "".join(parts) + "</div>"


def _branch(title: str, condition: str, description: str, nodes: list[str], *, wide: bool = False) -> str:
    branch_class = "aa-branch-card aa-branch-wide" if wide else "aa-branch-card"
    return (
        f'<article class="{branch_class}">'
        f'<div class="aa-branch-condition">{html.escape(condition)}</div>'
        f'<h4>{html.escape(title)}</h4>'
        f'<p>{html.escape(description)}</p>'
        f'<div class="aa-branch-nodes">{_route(nodes, label=title)}</div>'
        '</article>'
    )


def _branches(branches: list[str]) -> str:
    if not branches:
        return ""
    return (
        '<div class="aa-branches">'
        '<div class="aa-branches-head"><span>CONDITIONAL PATHS</span><span>仅在对应条件满足时进入</span></div>'
        '<div class="aa-branch-grid">' + "".join(branches) + '</div>'
        '</div>'
    )


def _phase(number: str, title: str, description: str, routes: list[str], branches: list[str] | None = None) -> str:
    return (
        f'<section class="aa-phase" aria-labelledby="aa-phase-{number}">'
        '<div class="aa-phase-head">'
        f'<span class="aa-phase-number">{number}</span>'
        '<div><h2 id="aa-phase-' + number + '">' + html.escape(title) + '</h2>'
        '<p>' + html.escape(description) + '</p></div></div>'
        '<div class="aa-phase-routes">' + "".join(routes) + '</div>'
        + _branches(branches or [])
        + '</section>'
    )


def agent_workflow_architecture_body() -> str:
    """Render the current Agent workflow as a legible in-app diagram, not an Archify artifact."""
    stage_rows = stage_detail_catalog()
    details = {str(item["node"]): item for item in stage_rows if item.get("node")}
    active_source = stage_workflow_mermaid()

    def stage(role: str, name: str, **kwargs: Any) -> str:
        return _stage_node(details, active_source, role, name, **kwargs)

    def sdk(key: str, title: str, summary: str, **kwargs: Any) -> str:
        return _sdk_node(key, title, summary, **kwargs)

    phase_1 = _phase(
        "01",
        "Qualification · 资格审查",
        "先判定候选是否真正可检验，再冻结研究对象。资格修订与文献旁路是有界分支，不是无限循环。",
        [
            _route([
                stage("research_lead", "frontier_proposal", title="frontier_proposal", summary="提出 1–3 个候选；说明范围、目标推进和可判别的 decisive_test。"),
                stage("challenger", "candidate_review", title="candidate_review", summary="一次性审查全部候选；区分真正 blocker 与 Investigator 可完成的工作。"),
                sdk("qualification", "Qualification Router", "按 qualified / revise / reject 结果进行确定性分流。"),
            ], label="Qualification main path")
        ],
        [
            _branch(
                "唯一合格候选",
                "qualified count = 1",
                "SDK 直接推进锁定；不额外调用 Lead 选择。",
                [sdk("hypothesis-lock", "Hypothesis Lock", "冻结 H 身份、范围与 decisive_test。", compact=True)],
            ),
            _branch(
                "多个合格候选",
                "qualified count > 1",
                "Lead 只在已合格候选中选择；SDK 冻结选中候选的 decisive_test。",
                [stage("research_lead", "candidate_selection", summary="选择已合格候选并说明选择理由。", compact=True)],
            ),
            _branch(
                "有限资格修订",
                "review = revise",
                "Lead 只修订一次；resolved 经 SDK 验证后由 Challenger 的 repair_verification 独立核验一次。confirmed 才恢复资格；unresolved / cannot_repair 进入有界换角重提。",
                [
                    stage("research_lead", "selection_repair", summary="只处理冻结 blocker ledger 内可修补的问题。", compact=True),
                    stage("challenger", "repair_verification", summary="核验原始 blocker 是否真正关闭；confirmed 仅表示可进入 Investigator。", compact=True),
                ],
            ),
            _branch(
                "资格未通过",
                "all rejected / repair unresolved / cannot_repair",
                "SDK 按不同科学角度重提候选，最多 3 个 proposal rounds；耗尽后结束为 no_qualified_candidate。契约或冻结边界错误仍走安全停止。",
                [
                    sdk("qualification-retry", "Bounded re-proposal", "有剩余 proposal rounds 时返回 Lead frontier_proposal。", conditional=True, compact=True),
                    sdk("needs-attention", "no_qualified_candidate", "达到上限后关闭资格流程。", conditional=True, compact=True),
                ],
            ),
            _branch(
                "Lead 文献旁路",
                "external scholarly gap",
                "Research Lead 明确请求时可调用一次 Literature detour，结果返回 Lead 后继续资格审查。",
                [
                    stage("literature", "landscape", summary="概览相关研究路线与已知缺口。", compact=True),
                    stage("literature", "targeted_source", summary="核查指定的外部学术来源。", compact=True),
                    stage("literature", "novelty_check", summary="检查新颖性与已有工作的关系。", compact=True),
                ],
                wide=True,
            ),
        ],
    )

    phase_2 = _phase(
        "02",
        "Hypothesis Lock · 冻结研究对象",
        "锁定后的 H / scope / decisive_test 是本 Cycle 的科学权威边界；展示层不能反过来修改科学命题。",
        [
            _route([
                sdk("hypothesis-lock", "Hypothesis Lock", "解析稳定 H 身份，冻结 hypothesis、scope、decisive_test 与 C-1。"),
                sdk("context-router", "Context Assembly + Router", "组装最小科学上下文；Task root 负责读取，Cycle workspace 负责写入。"),
            ], label="Hypothesis lock and context")
        ],
        [
            _branch(
                "可选的命题展示",
                "display copy only",
                "hypothesis_display 只做科学命题的忠实压缩，保留主张与条件，不强化、不扩展、不裁决结果。",
                [stage("research_lead", "hypothesis_display", summary="将已冻结的 hypothesis / scope / decisive_test 转为可读展示。", compact=True)],
            ),
            _branch(
                "身份或谱系冲突",
                "identity conflict",
                "H catalog 与 SQLite / Canvas 投影不一致时 fail closed；不创建第二套身份。",
                [sdk("identity-fail-closed", "Fail closed", "保留证据并进入安全停止 / 恢复流程。", conditional=True, compact=True)],
            ),
        ],
    )

    phase_3 = _phase(
        "03",
        "Investigation · 证据研究与闭合",
        "Investigator 在冻结目标内自主组合理论、数值和本地来源审查；SDK 决定证据是否足以进入科学裁决。",
        [
            _route([
                stage("investigator", "investigate", title="investigate", summary="在冻结目标和允许输入内完成当前必要研究，提交可供 Judge 审查的 findings。"),
                sdk("evidence-closure", "Semantic Closure + Readiness", "绑定 finding / Evidence / H / Cycle / criterion，确定是否可裁决。"),
                sdk("judge-packet-gate", "Judge packet gate", "只有存在可裁决的 criterion-bound Evidence，才物化 Judge packet。"),
            ], label="Investigation main path")
        ],
        [
            _branch(
                "经授权的文献任务",
                "specific scholarly gap",
                "Literature 只处理明确的外部学术缺口；targeted_source 可将来源证据交回 Investigator 完成整合。",
                [
                    stage("literature", "targeted_source", summary="仅处理被授权的外部学术来源缺口。", compact=True),
                    stage("investigator", "integrate_literature", summary="将文献结果整合进同一冻结目标的证据链。", compact=True),
                ],
            ),
            _branch(
                "非科学性收口",
                "no adjudicable Evidence / transport blocked",
                "不把技术阻断包装成科学结论；不调用 Judge，不提交 Scientific Model，不创建科学 Decision。",
                [sdk("non-scientific-close", "Non-scientific Closeout", "显式关闭不可裁决的运行路径。", conditional=True, compact=True)],
            ),
            _branch(
                "授权成果保全",
                "preserve_research authorized",
                "只归档已有 findings / display material；这是保全退出，不经过 Judge 或下游科研收口。",
                [
                    stage("investigator", "preserve_research", summary="保全已有成果，不补造科学结论。", compact=True),
                    sdk("preserve-close", "SDK preserve closeout", "保全后直接结束本 Cycle。", conditional=True, compact=True),
                ],
            ),
        ],
    )

    phase_4 = _phase(
        "04",
        "Independent Judge · 独立裁决与有界返工",
        "Judge 只评估当前冻结 decisive_test 和可裁决证据；返工必须有明确请求、授权、预算与可执行路径。",
        [
            _route([
                stage("judge", "terminal_assessment", title="terminal_assessment", summary="只根据冻结判据、当前科学 findings 与适用的既往裁决语义作判断。"),
                sdk("rework-gate", "Terminal / Rework Gate", "终局判定直接收口；返工则进入确定性授权与预算检查。"),
            ], label="Judge terminal / rework routing")
        ],
        [
            _branch(
                "Investigator 返工",
                "one objection · authorized",
                "SDK 授权后回到同一个冻结 H / decisive_test；返工不是新假设，也不允许开放式循环。",
                [
                    stage("investigator", "judge_rework", summary="按 requested_resolution 补齐指定证据或解决局部科学缺口。", compact=True),
                    sdk("evidence-closure", "Evidence closure", "返工结果重新进入确定性闭合与 readiness 检查。", conditional=True, compact=True),
                ],
            ),
            _branch(
                "外部学术返工",
                "external scholarship = true",
                "只有 Judge 授权的外部学术缺口才交给 Literature；结果返回 Investigator 并重新闭合证据。",
                [
                    stage("literature", "targeted_source", summary="完成被授权的外部学术来源核查。", compact=True),
                    stage("investigator", "integrate_literature", summary="整合来源证据，再交回 SDK 闭合。", compact=True),
                ],
            ),
            _branch(
                "返工不可执行",
                "denied / exhausted / unavailable",
                "返工被拒绝、预算耗尽或资源不可用时，终局为 open；不能声称未执行的工作已解决判据。",
                [sdk("forced-open", "Forced terminal open", "将真实未解决状态送入 Lead synthesis。", conditional=True, compact=True)],
            ),
        ],
    )

    phase_5 = _phase(
        "05",
        "Post-Judge Closeout · 科研状态与产物收口",
        "科学结论、Scientific Model、Reporter 文本和 Canvas / SQLite 投影各有明确所有者；SDK 负责验证与原子提交。",
        [
            _route([
                stage("research_lead", "cycle_synthesis", summary="输出本 Cycle 的 established conclusions、open questions 和 Model keep/replace 决策。"),
                sdk("model-commit", "Scientific Model Commit", "验证 Evidence 引用和模型结构；keep 不变，replace 原子提交新版本。"),
                stage("research_lead", "frontier_maintenance", summary="维护现有前沿 tip 的 active / parked / closed 状态，不创建新 H。"),
            ], label="Scientific synthesis and model commit"),
            '<div class="aa-route-break"><span>科学状态已确定，进入人类可读产物收口</span><span aria-hidden="true">↓</span></div>',
            _route([
                stage("reporter", "report", title="report + canvas_maintenance", summary="根据已裁决科学状态写报告和 Decision 的人类可读标题 / 摘要；不决定 H ID、parent 或 Evidence refs。"),
                sdk("canvas-close", "Canvas Semantic Closure", "验证并应用 Decision / Evidence refs / continuation anchor。"),
                sdk("cycle-over", "Cycle OVER", "Cycle 结束；Goal 状态独立记录。", terminal=True),
            ], label="Reporter and Cycle closeout"),
        ],
        [
            _branch(
                "Goal Completion 独立关口",
                "terminal verdict = supported",
                "只有满足触发条件时才调用 Judge goal_completion。Goal passed 与 Cycle OVER 是两个独立状态，不得互相推断。",
                [
                    sdk("goal-gate", "Master Goal Gate", "确定是否触发全局 Goal Completion。", conditional=True, compact=True),
                    stage("judge", "goal_completion", summary="按 Research Plan 和图谱证据判断 Goal 是否真正完成。", compact=True),
                ],
            ),
        ],
    )

    phase_6 = _phase(
        "06",
        "Next Cycle · 从已保存事实继续",
        "只有剩余 Cycle budget 允许时才继续；新 Cycle 从当前 Decision anchor 与已保存科学状态开始，而不是预选下一 H。",
        [
            _route([
                sdk("cycle-budget", "Cycle Budget Gate", "剩余预算允许才开启下一 Cycle；否则结束 invocation。"),
                sdk("next-context", "Next-Cycle Context", "提供 Scientific Model、H Catalog、previous Decision anchor 和 compact prior state。"),
                stage("research_lead", "frontier_proposal", title="next frontier_proposal", summary="基于当前真实科学状态提出下一批候选；下一 H 不会被 SDK 预先选定。"),
            ], label="Next Cycle continuation")
        ],
    )

    agent_payload: dict[str, dict[str, Any]] = {}
    for key, item in details.items():
        agent_payload[key] = {
            "role_label": item.get("role_label", item.get("role", "Agent")),
            "stage": item.get("stage", key),
            "objective": item.get("objective", ""),
            "may_do": item.get("may_do", ""),
            "must_not": item.get("must_not", ""),
            "exit": item.get("exit", ""),
            "procedure": item.get("procedure", []),
            "role_guidance": item.get("role_guidance", []),
            "contract_rules": item.get("contract_rules", []),
        }
    all_payload = {**agent_payload, **SDK_DETAILS}
    payload_json = json.dumps(all_payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    phases = phase_1 + phase_2 + phase_3 + phase_4 + phase_5 + phase_6
    used_keys = set(re.findall(r'data-aa-stage-key="([^"]+)"', phases))
    source_warnings = [key for key in sorted(used_keys) if key not in details or key not in active_source]
    warning_html = ""
    if source_warnings:
        warning_html = (
            '<div class="aa-source-warning" role="alert"><strong>Workflow source mismatch</strong> — '
            + html.escape(", ".join(source_warnings))
            + ". The affected node(s) are disabled until this overview is reconciled with the live Stage Contract.</div>"
        )

    return f"""
<style>
.agent-architecture {{ min-width:0; color:var(--tm-text); }}
.aa-hero {{ border:1px solid var(--tm-border); border-radius:16px; padding:22px 24px; margin-bottom:15px; background:var(--tm-panel); }}
.aa-eyebrow {{ color:var(--tm-accent); font-size:10px; letter-spacing:.15em; font-weight:750; margin-bottom:8px; }}
.aa-hero h1 {{ margin:0 0 9px; font-size:clamp(23px,2.4vw,31px); }}
.aa-hero p {{ color:var(--tm-muted); max-width:920px; margin:0; font-size:13px; }}
.aa-stats {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }}
.aa-stat {{ border:1px solid color-mix(in srgb,var(--tm-border) 85%,transparent); border-radius:999px; padding:5px 10px; font-size:11px; color:var(--tm-muted); background:color-mix(in srgb,var(--tm-panel) 80%,var(--tm-bg)); }}
.aa-toolbar {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:0 0 14px; }}
.aa-toolbar button {{ padding:7px 11px; font-size:12px; }}
.aa-toolbar button[aria-pressed="true"] {{ border-color:var(--tm-accent); background:#17283b; color:#e7f3ff; }}
.aa-toolbar-note {{ margin-left:auto; color:var(--tm-muted); font-size:11px; }}
.aa-layout {{ display:grid; grid-template-columns:minmax(0,1fr) 305px; gap:15px; align-items:start; }}
.aa-flow {{ min-width:0; }}
.aa-phase {{ min-width:0; border:1px solid var(--tm-border); border-radius:15px; background:var(--tm-panel); padding:16px; }}
.aa-phase-head {{ display:flex; align-items:flex-start; gap:12px; margin-bottom:14px; }}
.aa-phase-number {{ display:grid; place-items:center; flex:0 0 34px; width:34px; height:34px; border:1px solid #46617f; border-radius:10px; color:var(--tm-accent); font-size:12px; font-weight:800; background:#121d2a; }}
.aa-phase-head h2 {{ margin:1px 0 4px; font-size:16px; }}
.aa-phase-head p {{ margin:0; color:var(--tm-muted); font-size:12px; }}
.aa-phase-routes {{ display:grid; gap:10px; }}
.aa-route {{ display:flex; align-items:stretch; gap:8px; min-width:0; }}
.aa-node {{ display:flex; flex:1 1 0; flex-direction:column; align-items:stretch; justify-content:flex-start; text-align:left; gap:7px; min-width:0; min-height:128px; padding:12px; border:1px solid #3f3f46; border-radius:11px; background:#151519; color:var(--tm-text); transition:border-color .14s,transform .14s,background .14s; white-space:normal; }}
.aa-node:hover {{ transform:translateY(-1px); background:#1b1b21; border-color:#7187a2; }}
.aa-node[aria-pressed="true"] {{ border-color:var(--tm-accent); box-shadow:0 0 0 1px color-mix(in srgb,var(--tm-accent) 45%,transparent); background:#172230; }}
.aa-node-meta {{ display:flex; align-items:center; justify-content:space-between; gap:6px; color:var(--tm-muted); font-size:10px; font-weight:650; letter-spacing:.02em; }}
.aa-node-kind {{ flex:none; font-size:9px; letter-spacing:.08em; opacity:.8; }}
.aa-node strong {{ font-size:13px; line-height:1.3; overflow-wrap:anywhere; }}
.aa-node-copy {{ display:block; color:var(--tm-muted); font-size:11px; line-height:1.45; font-weight:450; }}
.aa-agent[data-role="research_lead"] {{ border-top:3px solid #a78bfa; }}
.aa-agent[data-role="challenger"] {{ border-top:3px solid #fb7185; }}
.aa-agent[data-role="investigator"] {{ border-top:3px solid #60a5fa; }}
.aa-agent[data-role="literature"] {{ border-top:3px solid #fbbf24; }}
.aa-agent[data-role="judge"] {{ border-top:3px solid #4ade80; }}
.aa-agent[data-role="reporter"] {{ border-top:3px solid #2dd4bf; }}
.aa-sdk {{ border-top:3px solid #7c8da3; background:#121820; }}
.aa-terminal {{ border-color:#287a5a; background:#10231b; }}
.aa-node-compact {{ min-height:102px; padding:10px; }}
.aa-node-compact strong {{ font-size:12px; }}
.aa-node-compact .aa-node-copy {{ font-size:10px; }}
.aa-node-missing {{ border:1px solid #f87171!important; opacity:.8; }}
.aa-arrow {{ display:flex; align-items:center; justify-content:center; flex:0 0 22px; color:#8294aa; font-size:20px; font-weight:500; }}
.aa-route-break {{ display:flex; align-items:center; justify-content:center; gap:10px; padding:4px; color:var(--tm-muted); font-size:11px; }}
.aa-route-break span:last-child {{ color:var(--tm-accent); font-size:17px; }}
.aa-phase-link {{ display:flex; align-items:center; justify-content:center; gap:10px; padding:7px 0; color:var(--tm-muted); font-size:10px; }}
.aa-phase-link span:first-child {{ color:var(--tm-accent); font-size:16px; line-height:1; }}
.aa-branches {{ margin-top:15px; padding-top:12px; border-top:1px dashed #394656; }}
.aa-branches-head {{ display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:7px; margin-bottom:9px; color:var(--tm-muted); font-size:10px; }}
.aa-branches-head span:first-child {{ color:#f0c674; letter-spacing:.1em; font-weight:800; }}
.aa-branch-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px; }}
.aa-branch-card {{ min-width:0; border:1px dashed #4a5360; border-radius:11px; padding:11px; background:color-mix(in srgb,var(--tm-panel) 85%,var(--tm-bg)); }}
.aa-branch-wide {{ grid-column:1 / -1; }}
.aa-branch-condition {{ color:#e7c77d; font-size:9px; text-transform:uppercase; letter-spacing:.06em; font-weight:800; margin-bottom:4px; }}
.aa-branch-card h4 {{ margin:0 0 5px; font-size:12px; }}
.aa-branch-card p {{ margin:0 0 9px; color:var(--tm-muted); font-size:10px; line-height:1.5; }}
.aa-branch-nodes .aa-route {{ gap:5px; }}
.aa-branch-nodes .aa-arrow {{ flex-basis:14px; font-size:15px; }}
.aa-branch-nodes .aa-node {{ min-width:0; }}
.aa-source-warning {{ border:1px solid #b45353; border-radius:10px; padding:10px 12px; margin-bottom:12px; color:#fecaca; background:#271719; font-size:12px; }}
.aa-detail {{ position:sticky; top:14px; min-width:0; border:1px solid var(--tm-border); border-radius:15px; background:var(--tm-panel); overflow:hidden; }}
.aa-detail-head {{ padding:15px; border-bottom:1px solid var(--tm-border); }}
.aa-detail-kicker {{ color:var(--tm-accent); font-size:9px; font-weight:800; letter-spacing:.12em; margin-bottom:5px; }}
.aa-detail-head h3 {{ margin:0 0 5px; font-size:15px; overflow-wrap:anywhere; }}
.aa-detail-head p {{ margin:0; color:var(--tm-muted); font-size:11px; }}
.aa-detail-body {{ padding:14px 15px 16px; }}
.aa-detail-empty {{ color:var(--tm-muted); font-size:12px; }}
.aa-detail-section {{ margin-bottom:13px; }}
.aa-detail-section:last-child {{ margin-bottom:0; }}
.aa-detail-section h4 {{ margin:0 0 4px; font-size:11px; color:#d8e6f5; }}
.aa-detail-section p,.aa-detail-section li {{ color:var(--tm-muted); font-size:11px; line-height:1.55; overflow-wrap:anywhere; }}
.aa-detail-section p {{ margin:0; }}
.aa-detail-section ul {{ margin:0; padding-left:17px; }}
.aa-detail-links {{ padding:12px 15px; border-top:1px solid var(--tm-border); font-size:11px; }}
.aa-legend {{ display:flex; flex-wrap:wrap; gap:8px 12px; margin:12px 0 0; color:var(--tm-muted); font-size:10px; }}
.aa-legend span {{ display:inline-flex; align-items:center; gap:5px; }}
.aa-legend i {{ display:inline-block; width:15px; height:4px; border-radius:99px; background:#7c8da3; }}
.aa-legend .aa-legend-branch i {{ height:0; border-top:2px dashed #d4b56f; background:transparent; }}
.aa-footnote {{ color:var(--tm-muted); font-size:10px; margin:12px 0 0; }}
.agent-architecture[data-view="main"] .aa-branches {{ display:none; }}
.agent-architecture[data-view="main"] .aa-route-break {{ display:none; }}
@media(max-width:1120px) {{
  .aa-layout {{ grid-template-columns:minmax(0,1fr); }}
  .aa-detail {{ position:static; }}
  .aa-detail-body {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }}
  .aa-detail-section {{ margin-bottom:0; }}
}}
@media(max-width:760px) {{
  .aa-hero {{ padding:17px; }}
  .aa-phase {{ padding:12px; }}
  .aa-route {{ flex-direction:column; }}
  .aa-arrow {{ flex:0 0 16px; height:16px; transform:rotate(90deg); }}
  .aa-node {{ flex:none; width:100%; min-height:0; }}
  .aa-branch-grid {{ grid-template-columns:minmax(0,1fr); }}
  .aa-branch-nodes .aa-route {{ flex-direction:column; }}
  .aa-detail-body {{ display:block; }}
  .aa-detail-section {{ margin-bottom:12px; }}
  .aa-toolbar-note {{ width:100%; margin-left:0; }}
}}
@media(prefers-reduced-motion:reduce) {{
  .aa-node {{ transition:none; }}
  .aa-node:hover {{ transform:none; }}
}}
</style>
<div class="agent-architecture" data-aa-root data-view="all">
  <div class="aa-hero">
    <div class="aa-eyebrow">SCIVER · CURRENT SOURCE</div>
    <h1>Agent Workflow Architecture</h1>
    <p>一个 Cycle 的完整科研执行框架：候选资格审查 → Hypothesis Lock → 证据研究 → 独立裁决 → 科研状态与产物收口。Agent 阶段详情来自当前 Stage Registry / Contract；条件分支单独展示，避免把修订、返工和安全停止误画成主路径。</p>
    <div class="aa-stats">
      <span class="aa-stat">6 个正式 Agent 角色</span>
      <span class="aa-stat">SDK 确定性关口独立显示</span>
      <span class="aa-stat">SQLite H 身份 · Canvas 投影</span>
      <span class="aa-stat">Cycle OVER ≠ Goal complete</span>
    </div>
  </div>
  {warning_html}
  <div class="aa-toolbar" role="group" aria-label="Workflow display options">
    <button type="button" data-aa-view="all" aria-pressed="true">完整流程</button>
    <button type="button" data-aa-view="main" aria-pressed="false">仅看主路径</button>
    <span class="aa-toolbar-note">点击任意 Agent / SDK 节点，查看职责、边界和退出条件。</span>
  </div>
  <div class="aa-layout">
    <div class="aa-flow">
      {phase_1}
      <div class="aa-phase-link"><span aria-hidden="true">↓</span><span>候选通过资格审查</span></div>
      {phase_2}
      <div class="aa-phase-link"><span aria-hidden="true">↓</span><span>H / scope / decisive_test 已冻结</span></div>
      {phase_3}
      <div class="aa-phase-link"><span aria-hidden="true">↓</span><span>存在可裁决 Evidence 时进入 Judge</span></div>
      {phase_4}
      <div class="aa-phase-link"><span aria-hidden="true">↓</span><span>终局科学状态已确定</span></div>
      {phase_5}
      <div class="aa-phase-link"><span aria-hidden="true">↓</span><span>Cycle 已 OVER；检查剩余预算</span></div>
      {phase_6}
      <div class="aa-legend" aria-label="Diagram legend">
        <span><i></i> Agent / SDK 主路径节点</span>
        <span class="aa-legend-branch"><i></i> 条件分支 / 有界返工</span>
        <span>彩色上边线区分 Agent 角色；灰蓝色节点表示 SDK / Runtime 所有的确定性步骤。</span>
      </div>
      <p class="aa-footnote">来源：当前 stage_registry.py、contract_stages.py 与 stage_workflow_mermaid()。此图是可读的工作流总览；字段级输入 / 返回合同仍以 <a href="/workflow-stage-preview/">Workflow Stage Preview</a> 为准，完整判定链可在 <a href="/current-flow/">Current Flow</a> 查看。</p>
    </div>
    <aside class="aa-detail" data-aa-detail-panel aria-live="polite">
      <div class="aa-detail-head">
        <div class="aa-detail-kicker">NODE INSPECTOR</div>
        <h3 data-aa-detail-title>查看节点详情</h3>
        <p data-aa-detail-subtitle>选择左侧任一节点</p>
      </div>
      <div class="aa-detail-body" data-aa-detail-body>
        <p class="aa-detail-empty">点击 Agent 节点可查看当前目标、允许动作、禁止事项、退出条件与合同检查。SDK 节点会显示其确定性控制边界。</p>
      </div>
      <div class="aa-detail-links"><a href="/workflow-stage-preview/">打开字段级 Stage Contract →</a></div>
    </aside>
  </div>
  <script type="application/json" id="aa-agent-details">{payload_json}</script>
  <script>
  (() => {{
    const root = document.querySelector('[data-aa-root]');
    if (!root) return;
    const detailTitle = root.querySelector('[data-aa-detail-title]');
    const detailSubtitle = root.querySelector('[data-aa-detail-subtitle]');
    const detailBody = root.querySelector('[data-aa-detail-body]');
    const payloadNode = root.querySelector('#aa-agent-details');
    let details = {{}};
    try {{ details = JSON.parse(payloadNode ? payloadNode.textContent : '{{}}'); }} catch (_) {{ details = {{}}; }}

    function addSection(title, value) {{
      if (value === undefined || value === null || value === '' || (Array.isArray(value) && value.length === 0)) return;
      const section = document.createElement('section');
      section.className = 'aa-detail-section';
      const heading = document.createElement('h4');
      heading.textContent = title;
      section.appendChild(heading);
      if (Array.isArray(value)) {{
        const list = document.createElement('ul');
        value.forEach((entry) => {{
          const item = document.createElement('li');
          item.textContent = String(entry);
          list.appendChild(item);
        }});
        section.appendChild(list);
      }} else {{
        const paragraph = document.createElement('p');
        paragraph.textContent = String(value);
        section.appendChild(paragraph);
      }}
      detailBody.appendChild(section);
    }}

    root.querySelectorAll('[data-aa-detail-key]').forEach((button) => {{
      button.addEventListener('click', () => {{
        const key = button.dataset.aaDetailKey;
        const item = details[key];
        if (!item) return;
        root.querySelectorAll('[data-aa-detail-key][aria-pressed="true"]').forEach((selected) => selected.setAttribute('aria-pressed', 'false'));
        button.setAttribute('aria-pressed', 'true');
        detailTitle.textContent = item.stage || 'Workflow node';
        detailSubtitle.textContent = item.role_label || 'SDK / Runtime';
        detailBody.replaceChildren();
        addSection('Purpose', item.objective);
        addSection('May do', item.may_do);
        addSection('Must not', item.must_not);
        addSection('Exit condition', item.exit);
        addSection('Contract rules', item.contract_rules);
        addSection('Procedure', item.procedure);
        addSection('Role guidance', item.role_guidance);
        if (!detailBody.childElementCount) {{
          const empty = document.createElement('p');
          empty.className = 'aa-detail-empty';
          empty.textContent = 'No additional detail is declared for this node.';
          detailBody.appendChild(empty);
        }}
      }});
    }});

    root.querySelectorAll('[data-aa-view]').forEach((button) => {{
      button.addEventListener('click', () => {{
        const view = button.dataset.aaView === 'main' ? 'main' : 'all';
        root.dataset.view = view;
        root.querySelectorAll('[data-aa-view]').forEach((choice) => choice.setAttribute('aria-pressed', String(choice === button)));
      }});
    }});
  }})();
  </script>
</div>
"""
