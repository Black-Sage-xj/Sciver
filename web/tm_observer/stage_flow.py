from __future__ import annotations

import ast
import html
import json
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode

from .field_notes import default_description, field_key
from .workflow_versions import load_workflow_snapshots, workflow_snapshot


REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE_PREVIEW_GUIDE = REPO_ROOT / "web" / "WORKFLOW_STAGE_PREVIEW_GUIDE.md"


def _core_source_dir(root: Path) -> Path:
    """Support standalone TheoryMaster or its original gel_network checkout."""
    for candidate in (root / "theory_master", root / "tool" / "theory_master" / "theory_master"):
        if (candidate / "stage_registry.py").is_file():
            return candidate
    return root / "theory_master"


def _assignment_map(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            result[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            result[node.target.id] = node.value
    return result


def _safe_source_value(node: ast.AST, symbols: dict[str, ast.AST], stack: tuple[str, ...] = ()) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_safe_source_value(item, symbols, stack) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_safe_source_value(item, symbols, stack) for item in node.elts)
    if isinstance(node, ast.Dict):
        result: dict[Any, Any] = {}
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                expanded = _safe_source_value(value_node, symbols, stack)
                if not isinstance(expanded, dict):
                    raise ValueError("schema dict expansion must resolve to a dict")
                result.update(expanded)
                continue
            key = _safe_source_value(key_node, symbols, stack)
            result[key] = _safe_source_value(value_node, symbols, stack)
        return result
    if isinstance(node, ast.Name):
        name = node.id
        if name in stack:
            raise ValueError(f"recursive source constant: {name}")
        target = symbols.get(name)
        if target is None:
            raise ValueError(f"unsupported source name: {name}")
        return _safe_source_value(target, symbols, stack + (name,))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _safe_source_value(node.left, symbols, stack) + _safe_source_value(node.right, symbols, stack)
    raise ValueError(f"unsupported source expression: {type(node).__name__}")


def _source_catalog(repo_root: Path | None = None) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], dict[str, str]], dict[str, str]]:
    root = (repo_root or REPO_ROOT).resolve()
    tm_root = _core_source_dir(root)
    stage_symbols = _assignment_map(tm_root / "stage_registry.py")
    contract_symbols = _assignment_map(tm_root / "contract_stages.py")

    cards_value = _safe_source_value(stage_symbols["STAGE_CARDS"], stage_symbols)
    labels_value = _safe_source_value(stage_symbols["ROLE_STAGE_LABELS"], stage_symbols)
    if not isinstance(cards_value, dict) or not isinstance(labels_value, dict):
        raise ValueError("Sciver stage metadata must be dictionaries")

    phase_node = contract_symbols.get("PHASE_CONTRACTS")
    if not isinstance(phase_node, ast.Dict):
        raise ValueError("Sciver PHASE_CONTRACTS must be a source dict")
    contracts: dict[tuple[str, str], dict[str, Any]] = {}
    for key_node, value_node in zip(phase_node.keys, phase_node.values):
        pair = _safe_source_value(key_node, contract_symbols)
        if not isinstance(pair, tuple) or len(pair) != 2 or not isinstance(value_node, ast.Call):
            continue
        if len(value_node.args) < 3:
            continue
        contract_id = _safe_source_value(value_node.args[0], contract_symbols)
        version = _safe_source_value(value_node.args[1], contract_symbols)
        schema = _safe_source_value(value_node.args[2], contract_symbols)
        role, stage = str(pair[0]), str(pair[1])
        contracts[(role, stage)] = {
            "ref": f"{contract_id}/v{version}",
            "prompt_schema": schema,
        }
    return contracts, cards_value, labels_value


ROLE_LABELS = {
    "research_lead": "Research Lead",
    "challenger": "Challenger",
    "investigator": "Investigator",
    "literature": "Literature",
    "judge": "Judge",
    "reporter": "Reporter",
}

ROLE_CLASS = {
    "research_lead": "leadStage",
    "challenger": "challengerStage",
    "investigator": "investigatorStage",
    "literature": "literatureStage",
    "judge": "judgeStage",
    "reporter": "reporterStage",
}


DISPLAYED_AGENT_STAGES: tuple[tuple[str, str], ...] = (
    ("research_lead", "frontier_proposal"),
    ("challenger", "candidate_review"),
    ("challenger", "repair_verification"),
    ("research_lead", "candidate_selection"),
    ("research_lead", "hypothesis_display"),
    ("research_lead", "selection_repair"),
    ("investigator", "investigate"),
    ("investigator", "preserve_research"),
    ("investigator", "integrate_literature"),
    ("investigator", "judge_rework"),
    ("literature", "landscape"),
    ("literature", "targeted_source"),
    ("literature", "novelty_check"),
    ("judge", "terminal_assessment"),
    ("research_lead", "cycle_synthesis"),
    ("research_lead", "frontier_maintenance"),
    ("reporter", "report"),
    ("judge", "goal_completion"),
)


# Agent-receives fields are runtime architecture, not Observer-owned display
# metadata. Read the authoritative source declaration beside stage_context()
# so prompt changes automatically appear in /workflow-stage-preview/.
def _source_context_fields(repo_root: Path | None = None) -> dict[tuple[str, str], tuple[str, ...]]:
    root = (repo_root or REPO_ROOT).resolve()
    path = _core_source_dir(root) / "prompt_stages.py"
    symbols = _assignment_map(path)
    value = _safe_source_value(symbols["STAGE_CONTEXT_FIELDS"], symbols)
    if not isinstance(value, dict):
        raise ValueError("Sciver STAGE_CONTEXT_FIELDS must be a dictionary")
    result: dict[tuple[str, str], tuple[str, ...]] = {}
    for pair, fields in value.items():
        if not isinstance(pair, tuple) or len(pair) != 2 or not isinstance(fields, tuple):
            raise ValueError("Sciver STAGE_CONTEXT_FIELDS contains an invalid entry")
        result[(str(pair[0]), str(pair[1]))] = tuple(str(field) for field in fields)
    return result


# Compatibility export for tests and read-only callers. The values are parsed
# from TheoryMaster source at import time; Observer does not own a second list.
STAGE_CONTEXT_FIELDS = _source_context_fields()


def _source_stage_details(repo_root: Path | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    root = (repo_root or REPO_ROOT).resolve()
    tm_root = _core_source_dir(root)
    symbols = _assignment_map(tm_root / "stage_registry.py")
    procedures = _safe_source_value(symbols["PROCEDURE_CARDS"], symbols)
    _contracts, cards, _labels = _source_catalog(root)
    investigator_guidance = [
        str(_safe_source_value(symbols[name], symbols))
        for name in (
            "INVESTIGATOR_SKILL_ROUTING",
            "INVESTIGATOR_CONTENT_BOUNDARY",
            "INVESTIGATOR_EXECUTION_PROTOCOL",
            "INVESTIGATOR_COMPLETION_CONSISTENCY",
        )
        if name in symbols
    ]
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for pair in DISPLAYED_AGENT_STAGES:
        card = cards.get(pair) or {}
        result[pair] = {
            "objective": str(card.get("objective") or ""),
            "may_do": str(card.get("may_do") or ""),
            "must_not": str(card.get("must_not") or ""),
            "exit": str(card.get("exit") or ""),
            "procedure": list(procedures.get(pair, ()) or ()),
            "role_guidance": investigator_guidance if pair[0] == "investigator" else [],
        }
    return result


CONTEXT_FIELD_ANNOTATIONS: dict[tuple[str, str], dict[str, str]] = {
    ("research_lead", "frontier_proposal"): {
        "goal": "From Research Plan",
        "research_contract": "From Research Plan",
        "scientific_model_summary": "From Research Plan",
        "final_gate": "From Research Plan",
        "agent_graph": "SQLite-derived H index, frontier state, Outcomes and lineage",
        "agent_graph_reuse": "Reuse the previously injected SQLite view in this Cycle/Stage",
    },
}


CONTEXT_FIELD_STRUCTURES: dict[tuple[str, str, str], tuple[str, ...]] = {
    ("research_lead", "frontier_proposal", "scientific_model_summary"): (
        "version", "current_scientific_picture", "established_knowledge[].id", "established_knowledge[].status",
        "established_knowledge[].statement", "established_knowledge[].scope", "established_knowledge[].limitations",
        "established_knowledge[].evidence_refs[]",
    ),
    ("research_lead", "frontier_proposal", "agent_graph"): ("schema", "hypotheses[]", "current_frontiers[]", "lineage[]", "focus_details[]", "external_sources[]"),
    ("research_lead", "frontier_proposal", "qualification_feedback"): ("candidate_reviews[].{candidate_id,verdict,reason,blocking_issues[]}", "reason"),
    ("research_lead", "frontier_proposal", "lead_literature_result"): (
        "status", "reason", "evidence_records[].{evidence_id,paper_title,year,doi_or_url,scientific_insight,relation,criterion_ids,interpretation,limitations,source_access}",
        "landscape.{field_scope,major_routes,representative_papers,open_questions,relation_to_current_research,recommended_reading_order}",
    ),
    ("research_lead", "candidate_selection", "qualified_candidates"): ("[].id", "[].title", "[].hypothesis*", "[].scope*", "[].hypothesis_id*", "[].why_now*", "[].goal_advance*", "[].decisive_test*", "* fresh/recovery thread only"),
    ("research_lead", "selection_repair", "repair_options"): ("[].candidate_id", "[].title", "[].blockers[].{id,problem,required_change}", "[].hypothesis*", "[].scope*", "[].goal_advance*", "[].decisive_test*", "* fresh/recovery thread only"),
    ("research_lead", "cycle_synthesis", "active_hypothesis"): ("hypothesis_id", "hypothesis", "scope", "decisive_test"),
    ("research_lead", "cycle_synthesis", "judge_decision"): ("verdict", "rationale"),
    ("research_lead", "cycle_synthesis", "scientific_access"): ("scientific_model_path", "evidence_manifest_path", "cycle_dir"),
    ("research_lead", "frontier_maintenance", "scientific_model_summary"): ("version", "summary"),
    ("research_lead", "frontier_maintenance", "judge_decision"): ("verdict", "rationale"),
    ("research_lead", "frontier_maintenance", "scientific_synthesis"): ("conclusions[]", "open_questions[]"),
    ("research_lead", "frontier_maintenance", "agent_graph"): ("schema", "hypotheses[]", "current_frontiers[]", "lineage[]", "focus_details[]"),
    ("challenger", "candidate_review", "candidate"): ("[].hypothesis", "[].scope", "[].hypothesis_id?", "[].why_now", "[].goal_advance", "[].decisive_test"),
    ("challenger", "candidate_review", "agent_graph"): ("schema", "candidate_sources[]", "source_hypotheses[]", "lineage[]"),
    ("challenger", "candidate_review", "workspace_access"): ("cycle_dir", "workspace_dir", "notes_vault_root", "repo_root"),
    ("investigator", "investigate", "frozen_objective"): ("hypothesis", "scope", "decisive_test"),
    ("investigator", "integrate_literature", "frozen_objective"): ("hypothesis", "scope", "decisive_test"),
    ("investigator", "judge_rework", "frozen_objective"): ("hypothesis", "scope", "decisive_test"),
    ("investigator", "investigate", "previous_scientific_delta"): ("findings[].{statement,provenance?}",),
    ("investigator", "integrate_literature", "previous_scientific_delta"): ("findings[].{statement,provenance?}",),
    ("investigator", "judge_rework", "previous_scientific_delta"): ("findings[].{statement,provenance?}",),
    ("investigator", "judge_rework", "judge_objections"): ("[].objection_id", "[].criterion_id", "[].reason", "[].requested_resolution", "[].evidence_that_would_resolve_it[]", "[].evidence_that_would_not_resolve_it[]", "[].linked_evidence_ids[]", "[].response_evidence_recorded"),
    ("literature", "targeted_source", "source_lock"): ("{SDK-frozen source identity; concrete keys depend on the locked DOI/title/author/source handle}",),
    ("judge", "terminal_assessment", "hypothesis_lock"): ("hypothesis", "scope", "decisive_test"),
    ("judge", "terminal_assessment", "investigation_result"): ("findings[].{statement,method?,result?,scope?,limitations?,provenance?,source_access?,origin_cycle?,analysis_cycle?,freshness?}",),
    ("judge", "terminal_assessment", "prior_objection"): ("rationale", "requested_resolution"),
    ("judge", "terminal_assessment", "prior_adjudicated_context"): ("cycles[].{decisive_test,verdict,established_result,scope,limitations}",),
    ("judge", "goal_completion", "research_plan_access"): ("path",),
    ("judge", "goal_completion", "agent_graph"): ("schema", "hypotheses[]", "lineage[]", "external_sources[]"),
    ("reporter", "report", "hypothesis"): ("hypothesis_id", "hypothesis", "scope", "decisive_test", "cycle_criteria[].{id,criterion}"),
    ("reporter", "report", "judge_decision"): ("verdict", "assessment_state", "rationale", "closure_reason", "resource_disposition"),
    ("reporter", "report", "scientific_synthesis"): ("conclusions[]", "open_questions[]"),
    ("reporter", "report", "scientific_model_summary"): ("version", "current_scientific_picture", "established_knowledge[]"),
    ("reporter", "report", "evidence_scope_digest"): ("[].{evidence_id,relation,source_access,provenance,summary,criterion_ids,scope,limitations}",),
}


STAGE_CONTRACT_RULES: dict[tuple[str, str], tuple[str, ...]] = {
    ("research_lead", "frontier_proposal"): (
        "正常模式返回 1–3 个 candidates；Literature 旁路模式必须改为 needs_literature=true + literature_request，且与 candidates 互斥。",
        "targeted_source 的 literature_request 必须提供 concrete new_search_lead；该 Lead stage 最多只允许一次 Literature detour。",
        "candidates[].hypothesis_id 只能引用已存在的 H ID；新 Hypothesis 必须省略，由 SDK 在 selection/lock 后分配。",
    ),
    ("research_lead", "selection_repair"): (
        "outcome=resolved 时必须关闭冻结 blocker ledger；outcome=cannot_repair 时必须说明 cannot_repair_reason。",
        "当输入只有一个 revise candidate 时 selected_parent_candidate_id 可省略；多个时必须明确选择一个。",
    ),
    ("challenger", "candidate_review"): (
        "verdict=revise 时 blocking_issues 必须列出当前一次审查可见的全部独立 blocker；qualified/reject 不得伪造 repair 工作。",
        "reviews[].candidate_id 由 SDK 按输入候选顺序恢复/校验，不是 Challenger 自由生成字段。",
    ),
    ("challenger", "repair_verification"): (
        "只核验一个 SDK-closed repaired Candidate 与原始冻结 blocker ledger；不得重新打开 candidate_review。",
        "最多执行一次；confirmed 只表示候选重新具备资格，不表示 decisive_test 已获得科学支持。",
        "unresolved 或 cannot_repair 不得直接进入 Hypothesis Lock；按剩余 proposal rounds 走不同科学角度重提或安全停止。",
    ),
    ("investigator", "investigate"): (
        "只要求自包含的 Finding statement；evidence_kind/source_refs/scope/limitations 均不再输出。",
        "Final Formatting 不为核验步骤另建 Finding；保留科研 A 已确定的独立结果数量、顺序和边界。",
        "provenance 仅为特殊受保护身份的可选声明；SDK 只做登记/完整性检查，不替 Judge 判断科学真伪。",
    ),
    ("investigator", "integrate_literature"): (
        "与 investigate 使用同一 finding contract；吸收 Literature 结果后形成完整提交，再交 Judge。",
    ),
    ("investigator", "judge_rework"): (
        "与 investigate 使用同一 finding contract，但本轮必须围绕已授权 Judge objection，不得改写冻结 Hypothesis。",
    ),
    ("literature", "landscape"): (
        "Evidence record 必须对应真实 scholarly source；metadata/abstract/full_text 访问级别下 exact_quote 按 validator 要求提供。",
    ),
    ("literature", "targeted_source"): (
        "只验证冻结 search target/source；source_access 决定可主张的核验强度，无法访问不得伪装成 verified evidence。",
    ),
    ("literature", "novelty_check"): (
        "novelty positioning 必须区分已知重叠与未检出；不得把搜索不到文献当成 exhaustively novel。",
    ),
    ("judge", "terminal_assessment"): (
        "终局模式：verdict=supported|rejected|milestone_advanced|open，objection 必须省略。",
        "返工模式：verdict=null，并且恰好返回一个 objection{requested_resolution, needs_external_scholarship}；executor、预算、rework_plan 均由 SDK 推导。",
    ),
    ("judge", "goal_completion"): (
        "只返回 verdict=passed|not_passed 与 rationale。not_passed 不要求下一步计划。",
    ),
    ("research_lead", "frontier_maintenance"): (
        "updates 只覆盖当前 Canvas branch tip。frontier_status 只能是 active、parked 或 closed。",
        "不再是 tip 的旧节点不要写入 updates；不要新增 removal 状态、Candidate、Hypothesis 或 Scientific Model。",
    ),
}


SDK_OWNERSHIP_FIELDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("research_lead", "frontier_proposal"): (
        "candidates[].id — stable candidate ID",
        "new hypothesis_id — allocated only after selection/lock",
        "existing hypothesis_id — validated against Hypothesis Catalog",
        "qualification / Literature detour routing",
        "does not inject prior-cycle continuation science or a goal-gap planner",
    ),
    ("research_lead", "candidate_selection"): (
        "selected candidate lookup — must be Challenger-qualified",
        "hypothesis_id validation / new H-* allocation",
        "Hypothesis Lock + C-1 cycle criterion",
        "durable Hypothesis Catalog + initial executor",
    ),
    ("research_lead", "selection_repair"): (
        "replacement candidate.id",
        "frozen hypothesis / scope / goal_advance / title preservation",
        "parent_hypothesis_ids + evolution_operator=repair",
        "addressed_issue_ids + qualified replacement routing",
    ),
    ("research_lead", "hypothesis_display"): (
        "SDK owns frozen_hypothesis; this Stage cannot change the selected science",
        "Canvas title / summary validation and safe fallback are SDK-owned",
        "display-only output cannot change scientific identity or routing",
    ),
    ("research_lead", "cycle_synthesis"): (
        "Evidence admissibility + Scientific Model evidence-ref validation",
        "Scientific Model validation + atomic commit + version metadata",
        "post-verdict persisted scientific state + cross-Cycle Established/Unresolved source",
    ),
    ("research_lead", "frontier_maintenance"): (
        "frontier_status=active|parked|closed on current branch tips",
        "clear frontier_status when a node is no longer a tip",
        "Canvas prune only — no Candidate, new Hypothesis, or next Cycle",
    ),
    ("challenger", "candidate_review"): (
        "reviews[].candidate_id — restored from input candidate order",
        "candidate/review pairing validation — controls qualification validity",
        "prior-Cycle information-gain policy remains semantic Challenger authority",
        "qualification routing: qualified / revise / reject",
    ),
    ("challenger", "repair_verification"): (
        "original Candidate + frozen blocker ledger — SDK supplied",
        "repaired Candidate + repair_hash — SDK identity and content validation",
        "confirmed → repaired Candidate qualifies for Hypothesis Lock",
        "unresolved → bounded different-angle proposal retry; no second repair/review",
    ),
    ("investigator", "investigate"): (
        "Task-root scientific read anchor + Cycle workspace write boundary",
        "empty Evidence Registry ≠ missing task-local input",
        "Evidence ID registration + criterion binding",
        "remaining_gap.kind — Cycle-internal next-executor route from remaining_scientific_question; not a next-Cycle scientific fact. theoretical, numerical, or local_source_review continues investigator (stage investigate, or judge_rework while objections remain); literature_landscape goes to literature landscape; literature_targeted goes to literature targeted_source; literature_novelty goes to literature novelty_check; external_artifact or kind none is readiness only (judge or non-scientific close)",
    ),
    ("investigator", "preserve_research"): (
        "Supervisor or operator authorization is required for research preservation",
        "SDK validates retained evidence and commits the archival Canvas projection",
        "Preservation closes OVER without downstream Judge, Lead, or Reporter",
    ),
    ("investigator", "integrate_literature"): (
        "Task-root scientific read anchor + Cycle workspace write boundary",
        "empty Evidence Registry ≠ missing task-local input",
        "Evidence ID registration + criterion binding",
        "remaining_gap.kind — Cycle-internal next-executor route from remaining_scientific_question; not a next-Cycle scientific fact. theoretical, numerical, or local_source_review continues investigator (stage investigate, or judge_rework while objections remain); literature_landscape goes to literature landscape; literature_targeted goes to literature targeted_source; literature_novelty goes to literature novelty_check; external_artifact or kind none is readiness only (judge or non-scientific close)",
    ),
    ("investigator", "judge_rework"): (
        "Task-root scientific read anchor + Cycle workspace write boundary",
        "empty Evidence Registry ≠ missing task-local input",
        "Evidence ID registration + objection binding",
        "remaining_gap.kind — Cycle-internal next-executor route from remaining_scientific_question; not a next-Cycle scientific fact. theoretical, numerical, or local_source_review continues investigator (stage investigate, or judge_rework while objections remain); literature_landscape goes to literature landscape; literature_targeted goes to literature targeted_source; literature_novelty goes to literature novelty_check; external_artifact or kind none is readiness only (judge or non-scientific close)",
        "rework authorization / budget gate",
    ),
    ("literature", "landscape"): (
        "scholarly transport / corpus materialization",
        "Evidence relation + C-1 binding via semantic closure",
        "source metadata / verification + Evidence ID registration",
        "next-executor routing",
    ),
    ("literature", "targeted_source"): (
        "scholarly transport / corpus materialization",
        "Evidence relation + C-1 / Judge-objection binding via semantic closure",
        "source metadata / verification + Evidence ID registration",
        "next-executor routing",
    ),
    ("literature", "novelty_check"): (
        "scholarly transport / corpus materialization",
        "Evidence relation + C-1 binding via semantic closure",
        "source metadata / verification + Evidence ID registration",
        "next-executor routing",
    ),
    ("judge", "terminal_assessment"): (
        "C-1 binding + objection reason from rationale",
        "objection_id / executor_role / source_gap_id / Literature task / rework_plan",
        "rework authorization + budget checks",
        "forced-open terminalization when rework cannot proceed",
    ),
    ("judge", "goal_completion"): (
        "trigger — terminal_assessment.verdict=supported only",
        "fresh Judge episode — no H-level thread reuse",
        "passed → goal_status=passed; Cycle remains OVER",
        "not_passed → allow next Cycle; verdict + rationale only; no planner state",
    ),
    ("reporter", "report"): (
        "frontier authority remains outside Reporter",
    ),
}

TMCR_STAGE_NODE_MAP: dict[str, tuple[str, ...]] = {
    "LEAD": ("research_lead__frontier_proposal",),
    "LEAD_LIT_TASK": ("literature__landscape", "literature__targeted_source", "literature__novelty_check"),
    "CHALLENGER": ("challenger__candidate_review", "challenger__repair_verification"),
    "SELECT": ("research_lead__candidate_selection",),
    "REPAIR": ("research_lead__selection_repair", "challenger__repair_verification"),
    "AUTO": ("QUALIFY", "LOCK"),
    "STOP_Q": ("STOP",),
    "INV_CTX": (
        "investigator__investigate",
        "investigator__integrate_literature",
        "investigator__judge_rework",
    ),
    "INV_A": (
        "investigator__investigate",
        "investigator__integrate_literature",
        "investigator__judge_rework",
    ),
    "LIT": (
        "literature__landscape",
        "literature__targeted_source",
        "literature__novelty_check",
    ),
    "JUDGE": ("judge__terminal_assessment",),
    "GOAL_GATE": ("judge__goal_completion",),
    "SYNTH": ("research_lead__cycle_synthesis",),
    "FRONTIER_STATUS": ("research_lead__frontier_maintenance",),
    "CONTEXT": ("research_lead__cycle_synthesis",),
    "REPORT": ("reporter__report",),
    # Legacy TMCR Canvas text/close events are now one SDK-owned projection commit.
    "CANVAS_TEXT": ("CANVAS_CLOSE",),
    "CANVAS_CLOSE": ("CANVAS_CLOSE",),
}

STAGE_SDK_NODE_IDS = {
    "QUALIFY",
    "QUALIFY_RETRY",
    "STOP",
    "LOCK",
    "CANVAS_H",
    "SEMANTIC",
    "LEAD_LIT_GUARD",
    "LEAD_LIT_KIND",
    "NONSCI",
    "OPERATIONAL_CLOSED",
    "JUDGE_ROUTE",
    "REWORK_TARGET",
    "FORCED_OPEN",
    "MODEL_COMMIT",
    "CANVAS_CLOSE",
    "CLOSED",
    "GOAL_TRIGGER",
    "GOAL_DONE",
    "NEXT_CYCLE",
}


def _node_id(role: str, stage: str) -> str:
    return f"{role}__{stage}"


def _flatten_schema(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            fields.extend(_flatten_schema(child, path))
        return fields
    if isinstance(value, list):
        if not value:
            return [(prefix + "[]", "list")]
        child = value[0]
        return _flatten_schema(child, prefix + "[]")
    return [(prefix, str(value))]


def _field_marker(hint: str) -> str:
    lowered = hint.lower()
    if "optional" in lowered or "omit" in lowered or "required only" in lowered:
        return " ◌"
    return ""


_CHOICE_ENUM_RE = re.compile(r"(?<![a-z0-9_])([a-z][a-z0-9_]*(?:\|[a-z][a-z0-9_]*)+)(?![a-z0-9_])")

_OPTION_MEANINGS: dict[str, str] = {
    "refine": "把原命题细化，但形成新的、可独立裁决的 Hypothesis。",
    "specialize": "把命题缩小到特定条件、参数区间或子域。",
    "generalize": "把命题扩展到更宽的条件或适用范围。",
    "alternative": "提出替代解释或替代科学命题。",
    "combine": "组合已有思路或结果，形成新的独立命题。",
    "derive": "由已有结果或命题推导出新的 Hypothesis。",
    "counter_hypothesis": "提出与已有命题形成直接竞争关系的对立 Hypothesis。",
    "landscape": "做领域/路线级文献地图，用于补足广义外部知识缺口。",
    "targeted_source": "围绕一个具体来源、DOI、题名、作者或窄问题做定向核验。",
    "qualified": "候选已满足进入选择与锁定阶段的资格要求。",
    "revise": "候选核心命题可保留，但存在可在原边界内修复的 blocker。",
    "reject": "候选不应进入当前 Cycle 的后续选择/锁定流程。",
    "resolved": "最小修复已在冻结科学边界内关闭 blocker。",
    "cannot_repair": "若不改变冻结科学边界就无法关闭 blocker，应停止本次修复。",
    "theoretical": "该 finding 的主要证据来自理论推导、证明或解析论证。",
    "numerical": "该 finding 的主要证据来自数值计算、仿真或程序复算。",
    "local_source": "该 finding 直接依赖任务本地已有材料，并需给出实际来源引用。",
    "benchmark_provided": "证据身份来自 benchmark 明确提供的数据或事实。",
    "pre_specified_synthetic": "证据身份来自事先规定的合成数据。",
    "agent_generated": "证据由当前 Agent 在任务执行中生成。",
    "external_source": "证据来自任务外部来源。",
    "none": "不使用数据切分身份。",
    "fit": "用于拟合/训练或参数确定的数据部分。",
    "validation": "用于验证选择、调参或中间判定的数据部分。",
    "holdout": "预留、未参与拟合/选择的独立评估数据。",
    "gate": "专门用于最终门槛或通过条件判断的数据。",
    "metadata": "只核验到来源元数据层级。",
    "abstract": "已核验到摘要层级。",
    "full_text": "已核验到全文层级。",
    "unavailable": "当前无法取得足以核验该来源的内容。",
    "known": "当前结果与既有工作基本已知/已有直接覆盖。",
    "partially_overlapping": "与已有工作存在实质重叠，但仍保留明确差异。",
    "apparently_distinct": "基于当前核验材料看起来与既有工作有清晰区别。",
    "uncertain": "现有检索不足以稳定判定 novelty/overlap。",
    "supported": "当前冻结 Hypothesis 已被本 Cycle 的决定性证据支持。",
    "rejected": "当前冻结 Hypothesis 被决定性证据否定。",
    "milestone_advanced": "当前命题得到实质推进，但其自身尚未达到 supported/rejected 的终局。",
    "open": "当前证据不足以终局裁决，Cycle 以开放状态结束。",
    "null": "不做终局 verdict，而是提出一次受限、可执行的 Judge rework objection。",
    "keep": "长期 Scientific Model 保持当前版本不变。",
    "replace": "用本 Cycle 综合后的完整新版本替换长期 Scientific Model。",
    "closed": "该 frontier tip 不再作为当前研究方向。",
    "parked": "暂时保留该 tip，但当前不作为正常 Candidate 来源。",
    "active": "该 tip 仍有资格作为后续 Candidate 来源，但不代表下一 Cycle 必选。",
    "passed": "整个 Task 已满足 Research Plan 的 Final Gate。",
    "not_passed": "整个 Task 尚未满足 Final Gate，可继续后续 Cycle。",
}

_FIELD_OPTION_MEANINGS: dict[tuple[str, str], str] = {
    ("needs_literature", "true"): "当前 frontier 判断确有外部学术知识缺口，改走一次 Literature 旁路。",
    ("needs_literature", "false"): "不需要 Literature 旁路，正常返回候选 Hypothesis。",
    ("objection.needs_external_scholarship", "true"): "解决该 Judge objection 必须获得新的外部学术证据。",
    ("objection.needs_external_scholarship", "false"): "该 objection 不依赖新的外部学术证据，可由当前本地/理论/数值路径解决。",
}

_DYNAMIC_CHOICE_SOURCES: dict[tuple[str, str, str], tuple[str, str]] = {
    ("research_lead", "candidate_selection", "selected_candidate_id"): (
        "qualified_candidates[].id",
        "只能从本轮 Challenger 已判定 qualified 的候选 ID 中选择一个。",
    ),
    ("research_lead", "selection_repair", "selected_parent_candidate_id"): (
        "repair_options[].candidate_id",
        "候选项来自当前 revise repair options；只有一个候选时该字段可省略。",
    ),
    ("research_lead", "selection_repair", "repair.blocker_closures[].blocker_id"): (
        "repair_options[].blockers[].id",
        "必须引用冻结 blocker ledger 中真实存在的 blocker ID。",
    ),
    ("research_lead", "frontier_maintenance", "updates[].node_id"): (
        "current Canvas branch-tip node IDs",
        "只能选择当前仍是 scientific branch tip 的 Canvas 节点。",
    ),
}


def _source_evolution_operator_values(repo_root: Path | None = None) -> tuple[str, ...]:
    root = (repo_root or REPO_ROOT).resolve()
    path = _core_source_dir(root) / "contract_stages.py"
    symbols = _assignment_map(path)
    note_node = symbols.get("FRONTIER_PROPOSAL_PROMPT_NOTE")
    if note_node is None:
        return ()
    note = str(_safe_source_value(note_node, symbols))
    match = re.search(r"Allowed values:\s*([^.]*)\.", note)
    if not match:
        return ()
    return tuple(part.strip() for part in match.group(1).split("|") if part.strip())


def _enum_values_from_hint(hint: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s*\|\s*", "|", hint.lower())
    match = _CHOICE_ENUM_RE.search(normalized)
    return tuple(match.group(1).split("|")) if match else ()


def _choice_spec(
    role: str,
    stage: str,
    section: str,
    field: str,
    *,
    hint: str = "",
    sdk_description: str = "",
    repo_root: Path | None = None,
) -> dict[str, Any]:
    dynamic = _DYNAMIC_CHOICE_SOURCES.get((role, stage, field)) if section == "output" else None
    if dynamic:
        source, description = dynamic
        return {
            "kind": "choice",
            "mode": "dynamic",
            "source": source,
            "source_description": description,
            "options": [],
        }

    values: tuple[str, ...] = ()
    if section == "output" and field == "candidates[].evolution_operator":
        values = _source_evolution_operator_values(repo_root)
    elif section == "output" and field == "needs_literature":
        values = ("true", "false")
    elif section == "output" and field == "objection.needs_external_scholarship":
        values = ("true", "false")
    else:
        source_text = hint if section == "output" else f"{field} {sdk_description}"
        values = _enum_values_from_hint(source_text)
        if section == "sdk" and not values and "qualification routing" in source_text.lower():
            values = ("qualified", "revise", "reject")

    if role == "judge" and stage == "terminal_assessment" and section == "output" and field == "verdict" and values:
        values = tuple(dict.fromkeys((*values, "null")))

    if not values:
        return {"kind": "free", "mode": "free", "source": "", "source_description": "", "options": []}

    options = [
        {
            "value": value,
            "description": _FIELD_OPTION_MEANINGS.get((field, value))
            or _OPTION_MEANINGS.get(value)
            or f"{value} 是当前 contract 允许的一个离散取值。",
        }
        for value in values
    ]
    return {"kind": "choice", "mode": "enum", "source": "", "source_description": "", "options": options}


def _field_annotation(_path: str, hint: str) -> str:
    # Render the source contract annotation without inferring a second identity
    # rule from a field-name suffix; canonical H identity is enforced by the SDK.
    return _field_marker(hint)


def _render_field_lines(values: tuple[str, ...] | list[str], annotations: dict[str, str] | None = None) -> str:
    notes = annotations or {}
    rows: list[str] = []
    for value in values:
        row = f"• <code>{html.escape(value)}</code>"
        note = notes.get(value)
        if note:
            row += f" ({html.escape(note)})"
        rows.append(row)
    return "<br/>".join(rows) or "• <i>none</i>"


def _render_sdk_lines(role: str, stage: str, values: tuple[str, ...] | list[str]) -> str:
    rows: list[str] = []
    for value in values:
        field, separator, description = value.partition(" — ")
        choice = _choice_spec(role, stage, "sdk", field, sdk_description=description if separator else "")
        prefix = "☑ " if choice["kind"] == "choice" else ""
        row = f"• ◆ {prefix}<code>{html.escape(field)}</code>"
        if separator:
            row += f" — {html.escape(description)}"
        rows.append(row)
    return "<br/>".join(rows) or "• <i>none</i>"


_CROSS_CYCLE_GAP_SENTENCE = re.compile(
    r"(?:(?<=^)|(?<=[.!?]\s))"
    r"(?=[^.!?\n]*\b(?:remaining_goal_gap|remaining_gap)\b)"
    r"[^.!?\n]*[.!?]?\s*"
)


def _hide_cross_cycle_gap_copy(text: str) -> str:
    """Drop preview sentences that still name removed cross-Cycle goal-gap fields."""
    cleaned = _CROSS_CYCLE_GAP_SENTENCE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def stage_detail_catalog(*, repo_root: Path | None = None) -> list[dict[str, Any]]:
    details = _source_stage_details(repo_root)
    rows: list[dict[str, Any]] = []
    for role, stage in DISPLAYED_AGENT_STAGES:
        detail = details.get((role, stage), {})
        rows.append({
            "node": _node_id(role, stage),
            "role": role,
            "role_label": ROLE_LABELS.get(role, role.replace("_", " ").title()),
            "stage": stage,
            "objective": _hide_cross_cycle_gap_copy(str(detail.get("objective") or "")),
            "may_do": _hide_cross_cycle_gap_copy(str(detail.get("may_do") or "")),
            "must_not": _hide_cross_cycle_gap_copy(str(detail.get("must_not") or "")),
            "exit": _hide_cross_cycle_gap_copy(str(detail.get("exit") or "")),
            "procedure": [_hide_cross_cycle_gap_copy(str(step)) for step in (detail.get("procedure") or [])],
            "role_guidance": [_hide_cross_cycle_gap_copy(str(step)) for step in (detail.get("role_guidance") or [])],
            "contract_rules": [
                _hide_cross_cycle_gap_copy(str(rule))
                for rule in STAGE_CONTRACT_RULES.get((role, stage), ())
            ],
        })
    return rows


def field_note_catalog(
    overrides: Mapping[str, Mapping[str, str]] | None = None,
    *,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    contracts, _cards, _labels = _source_catalog(repo_root)
    context_fields = _source_context_fields(repo_root)
    edited = overrides or {}
    items: list[dict[str, Any]] = []

    def add(role: str, stage: str, section: str, field: str, *, hint: str = "", sdk_description: str = "") -> None:
        key = field_key(role, stage, section, field)
        base = default_description(section, field, hint=hint, sdk_description=sdk_description)
        choice = _choice_spec(
            role,
            stage,
            section,
            field,
            hint=hint,
            sdk_description=sdk_description,
            repo_root=repo_root,
        )
        override = edited.get(key) if isinstance(edited.get(key), Mapping) else None
        human_value = str(override.get("human_value")) if override and override.get("human_value") else None
        history = list(override.get("history") or []) if override else []
        description = human_value or base
        current_actor = "human" if human_value else "machine"
        current_event = next(
            (entry for entry in reversed(history) if entry.get("actor") == current_actor and entry.get("description") == description),
            None,
        )
        edited_at = str(current_event.get("edited_at")) if current_event else None
        items.append({
            "key": key,
            "node": _node_id(role, stage),
            "role": role,
            "role_label": ROLE_LABELS.get(role, role.replace("_", " ").title()),
            "stage": stage,
            "section": section,
            "field": field,
            "description": description,
            "default_description": base,
            "edited_at": edited_at,
            "actor": current_actor,
            "is_override": bool(human_value),
            "history": history,
            "structure": list(CONTEXT_FIELD_STRUCTURES.get((role, stage, field), ())) if section == "input" else [],
            "contract_hint": hint if section == "output" else "",
            "field_kind": choice["kind"],
            "choice_mode": choice["mode"],
            "choice_source": choice["source"],
            "choice_source_description": choice["source_description"],
            "choice_options": choice["options"],
        })

    for role, stage in DISPLAYED_AGENT_STAGES:
        contract = contracts.get((role, stage))
        if contract is None:
            continue
        for field in context_fields.get((role, stage), ()):
            add(role, stage, "input", field)
        for field, hint in _flatten_schema(contract["prompt_schema"]):
            add(role, stage, "output", field, hint=hint)
        for value in SDK_OWNERSHIP_FIELDS.get((role, stage), ()):
            field, separator, description = value.partition(" — ")
            add(role, stage, "sdk", field, sdk_description=description if separator else "")
    return items


def field_note_keys(*, repo_root: Path | None = None) -> set[str]:
    return {item["key"] for item in field_note_catalog(repo_root=repo_root)}


def _compact_objective(role: str, stage: str, cards: dict[tuple[str, str], dict[str, str]], labels: dict[str, str]) -> str:
    card = cards.get((role, stage), {})
    objective = _hide_cross_cycle_gap_copy(
        str(card.get("objective") or labels.get(stage) or stage.replace("_", " ")).strip()
    )
    if len(objective) <= 220:
        return objective
    clipped = objective[:217].rsplit(" ", 1)[0].rstrip(" ,;:")
    return clipped + "…"


def _stage_label(role: str, stage: str, contracts: dict[tuple[str, str], dict[str, Any]], cards: dict[tuple[str, str], dict[str, str]], labels: dict[str, str], context_fields: dict[tuple[str, str], tuple[str, ...]]) -> str:
    contract = contracts[(role, stage)]
    fields = _flatten_schema(contract["prompt_schema"])
    field_lines = "<br/>".join(
        f"• {'☑ ' if _choice_spec(role, stage, 'output', path, hint=hint)['kind'] == 'choice' else ''}"
        f"<code>{html.escape(path)}</code>{html.escape(_field_annotation(path, hint))}"
        for path, hint in fields
    )
    pair = (role, stage)
    context_lines = _render_field_lines(
        context_fields.get(pair, ()), CONTEXT_FIELD_ANNOTATIONS.get(pair)
    )
    sdk_lines = _render_sdk_lines(role, stage, SDK_OWNERSHIP_FIELDS.get(pair, ()))
    role_label = ROLE_LABELS.get(role, role.replace("_", " ").title())
    objective = html.escape(_compact_objective(role, stage, cards, labels))
    return (
        f"<b>{html.escape(role_label)}</b><br/>"
        f"<strong>{html.escape(stage)}</strong><br/>"
        "─────────────<br/>"
        f"<b>Stage goal</b><br/>{objective}<br/>"
        "─────────────<br/>"
        f"<b>1 · Agent receives / Context</b><br/>{context_lines}<br/>"
        f"<b>2 · Agent must return</b><br/>{field_lines}<br/>"
        f"<b>3 · ◆ SDK critical controls (not Agent input/output)</b><br/>{sdk_lines}"
    )


def _stage_node(role: str, stage: str, contracts: dict[tuple[str, str], dict[str, Any]], cards: dict[tuple[str, str], dict[str, str]], labels: dict[str, str], context_fields: dict[tuple[str, str], tuple[str, ...]]) -> str:
    node = _node_id(role, stage)
    label = _stage_label(role, stage, contracts, cards, labels, context_fields).replace('"', "&quot;")
    role_class = ROLE_CLASS[role]
    return f'    {node}["{label}"]:::{role_class}'


def _impact_stage_nodes(selected_request: dict[str, Any] | None) -> tuple[str, ...]:
    if not selected_request:
        return ()
    result: list[str] = []
    for flow_node in selected_request.get("affected_flow_nodes", ()) or ():
        flow_id = str(flow_node)
        for stage_node in TMCR_STAGE_NODE_MAP.get(flow_id, ()):
            if stage_node not in result:
                result.append(stage_node)
        if flow_id in STAGE_SDK_NODE_IDS and flow_id not in result:
            result.append(flow_id)
    return tuple(result)


def stage_workflow_mermaid(selected_request: dict[str, Any] | None = None, *, repo_root: Path | None = None) -> str:
    contracts, cards, labels = _source_catalog(repo_root)
    context_fields = _source_context_fields(repo_root)
    def node(role: str, stage: str) -> str:
        return _stage_node(role, stage, contracts, cards, labels, context_fields)
    # Node bodies come from the stage registry. A stage absent from
    # PHASE_CONTRACTS is omitted, including its closeout edge.
    visible_stages = [
        (role, stage)
        for role, stage in DISPLAYED_AGENT_STAGES
        if (role, stage) in contracts
    ]
    nodes = [node(role, stage) for role, stage in visible_stages]
    body = [
        "flowchart TD",
        "",
        "    %% Agent stages: each stage is the smallest Agent invocation unit.",
        *nodes,
        "",
        '    QUALIFY{"SDK qualification routing"}:::sdkNode',
        '    QUALIFY_RETRY{"SDK bounded different-angle proposal retry<br/>max 3 rounds"}:::sdkNode',
        '    STOP["SDK safe stop / operator attention"]:::sdkNode',
        '    LOCK["SDK Hypothesis Lock + durable H identity + C-1"]:::sdkNode',
        '    CANVAS_H["SDK SQLite H registration + Canvas projection check"]:::sdkNode',
        '    SEMANTIC["B semantic annotation + C deterministic closure<br/>Evidence registration / binding"]:::sdkNode',
        '    LEAD_LIT_GUARD{"SDK Lead Literature detour budget<br/>unused? (max 1 per frontier_proposal)"}:::sdkNode',
        '    LEAD_LIT_KIND{"SDK Lead Literature detour<br/>landscape | targeted_source | novelty_check"}:::sdkNode',
        '    NONSCI["SDK non-scientific closure<br/>no Judge / no Model change / no Canvas Decision"]:::sdkNode',
        '    OPERATIONAL_CLOSED(["Operational / non-scientific Cycle closed<br/>invocation stops"]):::sdkNode',
        '    JUDGE_ROUTE{"SDK terminal / rework authorization"}:::sdkNode',
        '    REWORK_TARGET{"SDK authorized rework executor"}:::sdkNode',
        '    FORCED_OPEN["SDK forced terminal open<br/>rework denied / exhausted / unavailable"]:::sdkNode',
        '    MODEL_COMMIT["SDK Scientific Model validation + atomic commit"]:::sdkNode',
        '    CANVAS_CLOSE["SDK / Canvas commit"]:::sdkNode',
        '    CLOSED(["Scientific Cycle OVER<br/>status=over"]):::sdkNode',
        '    GOAL_TRIGGER{"SDK Goal Gate trigger?<br/>terminal verdict = supported only"}:::sdkNode',
        '    GOAL_DONE(["Research Goal passed<br/>goal_status=passed<br/>Cycle remains OVER"]):::sdkNode',
        '    NEXT_CYCLE["Next Cycle / frontier proposal"]:::sdkNode',
        "",
        '    research_lead__frontier_proposal -- "candidates" --> challenger__candidate_review',
        '    research_lead__frontier_proposal -. "needs_literature=true" .-> LEAD_LIT_GUARD',
        '    LEAD_LIT_GUARD -- "unused" --> LEAD_LIT_KIND',
        '    LEAD_LIT_GUARD -- "already used → needs_attention" --> STOP',
        '    LEAD_LIT_KIND -- "landscape" --> literature__landscape',
        '    LEAD_LIT_KIND -- "targeted_source" --> literature__targeted_source',
        '    LEAD_LIT_KIND -- "novelty_check" --> literature__novelty_check',
        '    literature__landscape -. "Lead detour result only" .-> research_lead__frontier_proposal',
        '    literature__targeted_source -. "Lead detour result only" .-> research_lead__frontier_proposal',
        '    literature__novelty_check -. "Lead detour result only" .-> research_lead__frontier_proposal',
        "",
        "    challenger__candidate_review --> QUALIFY",
        '    QUALIFY -- ">1 qualified" --> research_lead__candidate_selection',
        '    QUALIFY -- "revise only" --> research_lead__selection_repair',
        '    QUALIFY -- "one qualified" --> LOCK',
        '    QUALIFY -- "all rejected" --> QUALIFY_RETRY',
        "    research_lead__candidate_selection --> LOCK",
        '    research_lead__selection_repair -- "resolved + SDK validation passed" --> challenger__repair_verification',
        '    research_lead__selection_repair -- "cannot_repair" --> QUALIFY_RETRY',
        '    research_lead__selection_repair -- "invalid contract / frozen-boundary validation failure" --> STOP',
        '    challenger__repair_verification -- "confirmed; eligible for Investigation" --> LOCK',
        '    challenger__repair_verification -- "unresolved blocker / fatal design flaw" --> QUALIFY_RETRY',
        '    QUALIFY_RETRY -- "proposal rounds remain (max 3)" --> research_lead__frontier_proposal',
        '    QUALIFY_RETRY -- "limit exhausted" --> STOP',
        '    STOP -. "explicit retry after research-state change: new qualification" .-> research_lead__frontier_proposal',
        '    STOP -. "identity reconciled: frozen Investigation" .-> investigator__investigate',
        "",
        "    LOCK --> CANVAS_H",
        '    CANVAS_H -- "identity valid" --> investigator__investigate',
        '    CANVAS_H -- "identity conflict: fail closed" --> STOP',
        "",
        "    investigator__investigate --> SEMANTIC",
        "    investigator__integrate_literature --> SEMANTIC",
        "    investigator__judge_rework --> SEMANTIC",
        '    literature__targeted_source -- "Judge-authorized scholarship" --> investigator__integrate_literature',
        '    SEMANTIC -- "accepted criterion-bound Evidence" --> judge__terminal_assessment',
        '    SEMANTIC -- "no adjudicable Evidence / true blocker" --> NONSCI',
        '    SEMANTIC -. "remaining_gap.kind theoretical / numerical / local_source_review: Cycle-internal next-executor = investigator, not next-Cycle science" .-> investigator__investigate',
        '    SEMANTIC -. "remaining_gap.kind literature_landscape: Cycle-internal next-executor = literature landscape, not next-Cycle science" .-> literature__landscape',
        '    SEMANTIC -. "remaining_gap.kind literature_targeted: Cycle-internal next-executor = literature targeted_source, not next-Cycle science" .-> literature__targeted_source',
        '    SEMANTIC -. "remaining_gap.kind literature_novelty: Cycle-internal next-executor = literature novelty_check, not next-Cycle science" .-> literature__novelty_check',
        "    NONSCI --> OPERATIONAL_CLOSED",
        "",
        "    judge__terminal_assessment --> JUDGE_ROUTE",
        '    JUDGE_ROUTE -- "terminal verdict" --> research_lead__cycle_synthesis',
        '    JUDGE_ROUTE -- "authorized rework" --> REWORK_TARGET',
        '    JUDGE_ROUTE -- "rework denied / budget exhausted" --> FORCED_OPEN',
        '    REWORK_TARGET -- "Investigator" --> investigator__judge_rework',
        '    REWORK_TARGET -- "external scholarship: targeted_source only" --> literature__targeted_source',
        '    REWORK_TARGET -- "duplicate / unavailable external gap" --> FORCED_OPEN',
        "    FORCED_OPEN --> research_lead__cycle_synthesis",
        "",
        "    research_lead__cycle_synthesis --> MODEL_COMMIT",
        *(
            [
                "    MODEL_COMMIT --> research_lead__frontier_maintenance",
                "    research_lead__frontier_maintenance --> reporter__report",
            ]
            if ("research_lead", "frontier_maintenance") in contracts
            else ["    MODEL_COMMIT --> reporter__report"]
        ),
        "    reporter__report --> CANVAS_CLOSE",
        "    CANVAS_CLOSE --> CLOSED",
        "    CLOSED --> GOAL_TRIGGER",
        '    GOAL_TRIGGER -- "scientific verdict = supported" --> judge__goal_completion',
        '    GOAL_TRIGGER -. "otherwise" .-> NEXT_CYCLE',
        '    judge__goal_completion -- "passed" --> GOAL_DONE',
        '    judge__goal_completion -- "not_passed" --> NEXT_CYCLE',
        "    NEXT_CYCLE --> research_lead__frontier_proposal",
        "",
        "    classDef leadStage fill:#e8ddff,stroke:#6f42c1,stroke-width:3px,color:#24123d;",
        "    classDef challengerStage fill:#ffe0e6,stroke:#c7254e,stroke-width:3px,color:#4a0b1b;",
        "    classDef investigatorStage fill:#dceeff,stroke:#1677c8,stroke-width:3px,color:#082d4c;",
        "    classDef literatureStage fill:#fff1c9,stroke:#b77900,stroke-width:3px,color:#4a3100;",
        "    classDef judgeStage fill:#d9f5e5,stroke:#198754,stroke-width:3px,color:#07351f;",
        "    classDef reporterStage fill:#dff4f2,stroke:#0f766e,stroke-width:3px,color:#063936;",
        "    classDef sdkNode fill:#eceff3,stroke:#5f6875,stroke-width:2px,color:#20252b;",
    ]
    impacted = _impact_stage_nodes(selected_request)
    if impacted:
        body.extend([
            "    classDef tmcrImpact stroke:#d11,stroke-width:6px;",
            "    class " + ",".join(impacted) + " tmcrImpact;",
        ])
    return "\n".join(body)


def stage_workflow_guide_body() -> str:
    try:
        content = STAGE_PREVIEW_GUIDE.read_text(encoding="utf-8")
    except OSError:
        content = "Workflow Stage Preview guide is unavailable."
    return (
        '<h1>Workflow Stage Preview · 显示规则 / 维护说明</h1>'
        '<p><a href="/workflow-stage-preview/">← 返回 Stage Preview</a></p>'
        '<pre class="card" style="white-space:pre-wrap;line-height:1.55">'
        + html.escape(content)
        + '</pre>'
    )


def stage_workflow_overview_mermaid(source: str, stage_details: list[dict[str, Any]]) -> str:
    """Reduce visual node copy without changing any scientific edges or SDK routing.

    The original frozen/source-backed Mermaid is available in detailed mode.
    Only Agent node label bodies are shortened; node IDs, classes, edge labels,
    routing, and every SDK control node remain byte-for-byte unchanged.
    """
    known = {str(item.get("node")): item for item in stage_details if item.get("node")}
    matcher = re.compile(r'^(?P<indent>\s+)(?P<node>[a-z_]+__[a-z_]+)\[".*"\]:::(?P<cls>[a-zA-Z]+)$')
    result = []
    for line in source.split("\n"):
        match = matcher.fullmatch(line)
        item = known.get(match.group("node")) if match else None
        if item is None:
            result.append(line)
            continue
        role = html.escape(str(item.get("role_label") or item.get("role") or "Agent"))
        stage = html.escape(str(item.get("stage") or ""))
        # The compact view is intentionally a two-line stage index, not a
        # replacement for the original complete Agent/SDK contract diagram.
        label = f"<b>{role}</b><br/><strong>{stage}</strong>"
        result.append(f'{match.group("indent")}{match.group("node")}["{label}"]:::{match.group("cls")}')
    return "\n".join(result)


def stage_workflow_body(
    requests: tuple[dict[str, Any], ...] = (),
    selected_request_id: str | None = None,
    *,
    field_note_overrides: Mapping[str, Mapping[str, str]] | None = None,
    workflow_version: str | None = None,
    display_mode: str = "overview",
) -> str:
    selected = next((item for item in requests if item.get("request_id") == selected_request_id), None)
    snapshot = workflow_snapshot(workflow_version)
    read_only_history = snapshot is not None
    if snapshot is None:
        source = stage_workflow_mermaid(selected)
        field_metadata = field_note_catalog(field_note_overrides)
        stage_metadata = stage_detail_catalog()
        active_version = "current"
        version_title = "Current · live"
        version_summary = "直接读取当前 Sciver source；Agent 输入与输出字段会随生产代码更新。"
    else:
        source = str(snapshot["mermaid"])
        field_metadata = list(snapshot.get("field_metadata") or [])
        stage_metadata = list(snapshot.get("stage_details") or [])
        active_version = str(snapshot.get("version") or workflow_version)
        version_title = str(snapshot.get("title") or active_version)
        version_summary = str(snapshot.get("summary") or "冻结历史 Workflow 快照。")
    if display_mode not in {"overview", "full"}:
        raise ValueError("Unsupported Workflow display mode")
    if display_mode == "overview":
        source = stage_workflow_overview_mermaid(source, stage_metadata)
    field_notes_json = json.dumps(field_metadata, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    stage_details_json = json.dumps(stage_metadata, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    snapshots = load_workflow_snapshots()
    version_options = ['<option value="current"' + (' selected' if active_version == 'current' else '') + '>Current · live</option>']
    for item in snapshots:
        version = str(item.get("version") or "")
        title = str(item.get("title") or version)
        version_options.append(
            f'<option value="{html.escape(version, quote=True)}"{" selected" if version == active_version else ""}>{html.escape(title)}</option>'
        )
    version_selector = "".join(version_options)
    view_base = {"workflow_version": active_version}
    if selected_request_id:
        view_base["request"] = selected_request_id
    def view_link(mode: str) -> str:
        return "/workflow-stage-preview/?" + html.escape(urlencode({**view_base, "view": mode}), quote=True)
    terminal_statuses = {"validated", "implemented", "rejected", "cancelled", "superseded", "closed"}
    active_requests = tuple(
        item for item in requests
        if str(item.get("status", "")).strip().lower() not in terminal_statuses
    )
    historical_requests = tuple(
        item for item in requests
        if str(item.get("status", "")).strip().lower() in terminal_statuses
    )

    def request_rows(items: tuple[dict[str, Any], ...]) -> str:
        version_arg = "" if active_version == "current" else f"&workflow_version={html.escape(active_version, quote=True)}"
        return "".join(
            f'<a class="request-row{" selected" if item.get("request_id") == selected_request_id else ""}" '
            f'href="/workflow-stage-preview/?request={html.escape(str(item.get("request_id")), quote=True)}{version_arg}#change-requests">'
            f'<strong>{html.escape(str(item.get("request_id")))}</strong>'
            f'<span>{html.escape(str(item.get("priority") or ""))}</span>'
            f'<small>{html.escape(str(item.get("title") or ""))}</small></a>'
            for item in items
        )

    active_request_links = request_rows(active_requests) or '<div class="muted request-empty">暂无未结束修改提请。</div>'
    historical_request_links = request_rows(historical_requests) or '<div class="muted request-empty">暂无历史提请。</div>'
    selected_is_historical = bool(
        selected and str(selected.get("status", "")).strip().lower() in terminal_statuses
    )
    history_open = " open" if selected_is_historical else ""

    if selected is not None:
        problem = selected.get("problem") if isinstance(selected.get("problem"), dict) else {}
        impacted_nodes = () if read_only_history else _impact_stage_nodes(selected)
        impact_links = "".join(
            f'<button type="button" class="impact-link" data-stage-node="{html.escape(node, quote=True)}">'
            f'{html.escape(node)}</button>'
            for node in impacted_nodes
        ) or ('<div class="muted">历史 Workflow 为只读快照，不投影当前 Change Request 影响。</div>' if read_only_history else '<div class="muted">该提请没有可映射到当前 Workflow 的节点。</div>')
        affected = ", ".join(str(item) for item in selected.get("affected_flow_nodes", ()) or ()) or "none"
        request_detail = f"""
<div class="request-detail">
  <h2>{html.escape(str(selected.get("request_id")))}</h2>
  <p><span class="badge">{html.escape(str(selected.get("priority") or ""))}</span>
     <span class="badge">{html.escape(str(selected.get("status") or ""))}</span></p>
  <h3>{html.escape(str(selected.get("title") or ""))}</h3>
  <p>{html.escape(str(selected.get("summary") or ""))}</p>
  <h4>修改位置</h4>
  <div class="impact-list">{impact_links}</div>
  <p class="small">声明的 affected_flow_nodes：<code>{html.escape(affected)}</code>。左侧对应 Agent Stage 或 SDK 节点以红色外框标出。</p>
  <h4>定位</h4>
  <p><strong>{html.escape(str(problem.get("owner", "")))}</strong> → {html.escape(str(problem.get("stage", "")))}<br>
  {html.escape(str(problem.get("issue", "")))}</p>
  <h4>方案</h4>
  <p>{html.escape(str(selected.get("proposal") or ""))}</p>
  <p><a href="/change-requests/">管理 / 新建修改提请</a></p>
</div>"""
    else:
        request_detail = '<div class="request-detail"><p class="small">选择一条提请后，左侧 Workflow 会直接标出它修改的位置。</p><p><a href="/change-requests/">管理 / 新建修改提请</a></p></div>'
    return f"""
<style>
.stage-layout {{ display:block; width:100%; min-width:0; }}
.stage-main {{ min-width:0; }}
.stage-preview-head {{ display:flex; align-items:flex-start; justify-content:space-between; gap:18px; flex-wrap:wrap; }}
.stage-preview-actions {{ display:flex; gap:8px; flex-wrap:wrap; }}
.stage-preview-actions a {{ text-decoration:none; border:1px solid currentColor; border-radius:8px; padding:7px 10px; font-weight:650; }}
.workflow-version-form {{ display:flex; align-items:center; gap:7px; flex-wrap:wrap; margin-top:8px; }}
.workflow-version-form select {{ padding:5px 8px; border-radius:7px; border:1px solid color-mix(in srgb, currentColor 25%, transparent); background:Canvas; color:CanvasText; }}
.workflow-version-badge {{ font-size:.76rem; padding:2px 7px; border:1px solid currentColor; border-radius:999px; opacity:.72; }}
.stage-legend {{ display:flex; gap:8px; flex-wrap:wrap; margin:14px 0; }}
.stage-legend span {{ border:2px solid var(--c); border-radius:999px; padding:4px 9px; background:var(--bg); color:var(--fg); font-size:.86rem; }}
.stage-canvas-shell {{ position:relative; min-height:0; }}
.stage-canvas {{ height:calc(100vh - 255px); min-height:620px; overflow:auto; border:1px solid color-mix(in srgb, currentColor 20%, transparent); border-radius:12px; background:Canvas; padding:18px; box-sizing:border-box; cursor:default; overscroll-behavior:contain; }}
.stage-canvas.space-pan {{ cursor:grab; }}
.stage-canvas.is-panning {{ cursor:grabbing; user-select:none; }}
.stage-canvas g.node, .stage-canvas g.node * {{ user-select:text; -webkit-user-select:text; cursor:text; }}
.stage-canvas.space-pan g.node, .stage-canvas.space-pan g.node * {{ cursor:grab; }}
.stage-canvas.is-panning g.node, .stage-canvas.is-panning g.node * {{ cursor:grabbing; }}
.stage-zoom-space {{ position:relative; min-width:100%; min-height:100%; }}
.stage-canvas .mermaid {{ position:absolute; top:0; left:0; width:1500px; margin:0; display:flex; justify-content:center; }}
.stage-canvas svg {{ max-width:none !important; flex:none; }}
.stage-toolbar {{ position:absolute; top:10px; left:10px; z-index:12; display:flex; align-items:center; gap:5px; padding:5px; border:1px solid color-mix(in srgb, currentColor 20%, transparent); border-radius:9px; background:color-mix(in srgb, Canvas 88%, transparent); backdrop-filter:blur(6px); }}
.stage-toolbar button {{ min-width:34px; height:30px; padding:3px 8px; border:1px solid color-mix(in srgb, currentColor 25%, transparent); border-radius:7px; background:Canvas; color:CanvasText; cursor:pointer; }}
.stage-toolbar button[aria-pressed="true"] {{ outline:2px solid Highlight; outline-offset:1px; }}
.stage-toolbar .zoom-value {{ min-width:58px; font-variant-numeric:tabular-nums; }}
.stage-toolbar-separator {{ width:1px; height:22px; margin:0 2px; background:color-mix(in srgb, currentColor 25%, transparent); }}
body.stage-height-focus {{ max-width:none; margin:0; padding:0; overflow:hidden; }}
body.stage-height-focus > nav {{ display:none; }}
body.stage-height-focus .stage-layout {{ height:100vh; align-items:stretch; }}
body.stage-height-focus .stage-main {{ height:100vh; min-height:0; display:flex; flex-direction:column; }}
body.stage-height-focus .stage-preview-head,
body.stage-height-focus .stage-legend,
body.stage-height-focus .stage-meta {{ display:none; }}
body.stage-height-focus .stage-canvas-shell {{ flex:1 1 auto; min-height:0; height:100vh; }}
body.stage-height-focus .stage-canvas {{ height:100%; min-height:0; border-radius:0; border-top:0; border-bottom:0; }}
body.stage-height-focus .stage-splitter {{ top:0; height:100vh; min-height:0; }}
body.stage-height-focus .stage-request-panel {{ top:0; height:100vh; max-height:100vh; box-sizing:border-box; border-radius:0; border-top:0; border-bottom:0; }}
.stage-canvas-shell:fullscreen {{ width:100vw; height:100vh; background:Canvas; padding:0; }}
.stage-canvas-shell:fullscreen .stage-canvas {{ width:100%; height:100%; min-height:0; border:0; border-radius:0; }}
body.stage-fullscreen-fallback {{ overflow:hidden; }}
body.stage-fullscreen-fallback .stage-canvas-shell {{ position:fixed; inset:0; z-index:10000; width:100vw; height:100vh; background:Canvas; }}
body.stage-fullscreen-fallback .stage-canvas {{ width:100%; height:100%; min-height:0; border:0; border-radius:0; }}
.stage-meta {{ margin:10px 0 0; border:1px solid color-mix(in srgb, currentColor 18%, transparent); border-radius:10px; padding:8px 10px; }}
.stage-meta > summary {{ cursor:pointer; font-weight:650; }}
.stage-meta p {{ margin:8px 0 0; }}
.stage-splitter {{ position:sticky; top:16px; width:18px; height:calc(100vh - 32px); min-height:520px; padding:0; border:0; background:transparent; cursor:col-resize; touch-action:none; display:flex; align-items:center; justify-content:center; color:color-mix(in srgb, currentColor 62%, transparent); }}
.stage-splitter::before {{ content:""; width:4px; height:72px; border-radius:999px; background:currentColor; opacity:.55; transition:opacity .15s, width .15s; }}
.stage-splitter:hover::before, .stage-splitter:focus-visible::before, .stage-splitter.is-resizing::before {{ width:6px; opacity:1; }}
.stage-splitter:focus-visible {{ outline:2px solid Highlight; outline-offset:-2px; }}
.stage-request-panel {{ position:sticky; top:16px; max-height:calc(100vh - 32px); overflow:auto; border:1px solid color-mix(in srgb, currentColor 20%, transparent); border-radius:12px; padding:12px; }}
.request-panel-head {{ display:flex; align-items:center; justify-content:space-between; gap:10px; }}
.request-panel-head h3 {{ margin:0; }}
.request-panel-head a {{ font-size:.86rem; }}
.request-section-title {{ margin:14px 0 8px; font-size:.92rem; opacity:.78; }}
.request-empty {{ padding:8px 4px 12px; }}
.request-history {{ margin-top:14px; border-top:1px solid color-mix(in srgb, currentColor 18%, transparent); padding-top:10px; }}
.request-history > summary {{ cursor:pointer; font-weight:650; opacity:.78; padding:4px 0 8px; }}
.request-history[open] > summary {{ opacity:1; }}
.request-row {{ display:grid; grid-template-columns:1fr auto; gap:3px 8px; padding:9px; margin:5px 0; border-radius:8px; text-decoration:none; border:1px solid color-mix(in srgb, currentColor 14%, transparent); }}
.request-row small {{ grid-column:1 / -1; opacity:.72; }}
.request-row.selected {{ border-color:#d11; box-shadow:0 0 0 2px color-mix(in srgb, #d11 25%, transparent); }}
.request-detail {{ margin-top:16px; border-top:1px solid color-mix(in srgb, currentColor 18%, transparent); padding-top:12px; }}
.request-detail h2 {{ margin:0 0 8px; }}
.impact-list {{ display:flex; gap:6px; flex-wrap:wrap; }}
.impact-link {{ border:1px solid #d11; border-radius:999px; padding:5px 9px; cursor:pointer; background:Canvas; color:CanvasText; }}
.stage-focus {{ filter:drop-shadow(0 0 10px #d11); }}
.stage-canvas .field-token {{ cursor:pointer !important; text-decoration-line:underline; text-decoration-style:dotted; text-underline-offset:3px; text-decoration-thickness:1px; }}
.stage-canvas .field-token:hover, .stage-canvas .field-token:focus {{ text-decoration-style:solid; outline:none; filter:brightness(1.12); }}
.stage-canvas .stage-token {{ cursor:pointer !important; text-decoration-line:underline; text-decoration-style:dashed; text-underline-offset:4px; }}
.stage-canvas .stage-token:hover, .stage-canvas .stage-token:focus {{ text-decoration-style:solid; outline:none; filter:brightness(1.12); }}
.field-note-popover {{ position:fixed; z-index:12000; width:min(330px,calc(100vw - 24px)); box-sizing:border-box; border:1px solid color-mix(in srgb, currentColor 22%, transparent); border-radius:10px; padding:10px; background:Canvas; color:CanvasText; box-shadow:0 8px 24px rgba(0,0,0,.2); }}
.field-note-popover[hidden] {{ display:none; }}
.field-note-popover-head {{ display:flex; align-items:baseline; justify-content:space-between; gap:8px; margin-bottom:6px; }}
.field-note-popover-head code {{ font-weight:700; overflow-wrap:anywhere; }}
.field-note-context {{ font-size:.72rem; opacity:.58; white-space:nowrap; }}
.field-note-popover textarea {{ width:100%; min-height:82px; max-height:170px; resize:vertical; box-sizing:border-box; padding:7px 8px; border:1px solid color-mix(in srgb, currentColor 20%, transparent); border-radius:7px; background:Canvas; color:CanvasText; font:inherit; font-size:.86rem; line-height:1.4; }}
.field-note-time {{ margin-top:5px; font-size:.7rem; opacity:.56; }}
.field-note-footer {{ display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:6px; }}
.field-note-footer button {{ padding:4px 8px; border:1px solid color-mix(in srgb, currentColor 18%, transparent); border-radius:6px; background:Canvas; color:CanvasText; cursor:pointer; font-size:.76rem; }}
.field-note-error {{ min-height:1em; color:#c33; font-size:.7rem; }}
.field-note-history {{ margin-top:7px; border-top:1px solid color-mix(in srgb, currentColor 16%, transparent); padding-top:6px; }}
.field-note-history > summary {{ cursor:pointer; font-size:.75rem; opacity:.7; }}
.field-note-history-list {{ max-height:150px; overflow:auto; margin-top:5px; display:grid; gap:5px; }}
.field-note-history-item {{ padding:5px 6px; border-radius:6px; background:color-mix(in srgb, currentColor 5%, Canvas); font-size:.72rem; line-height:1.35; }}
.field-note-history-meta {{ display:flex; justify-content:space-between; gap:8px; opacity:.64; margin-bottom:2px; }}
.field-note-actor {{ font-weight:700; }}
.field-note-extra {{ margin-top:7px; border-top:1px solid color-mix(in srgb, currentColor 16%, transparent); padding-top:6px; }}
.field-note-extra > summary {{ cursor:pointer; font-size:.75rem; opacity:.72; }}
.field-note-extra-list {{ margin:5px 0 0 16px; padding:0; font-size:.74rem; line-height:1.4; }}
.field-note-contract-hint {{ margin-top:6px; font-size:.72rem; line-height:1.4; opacity:.75; white-space:pre-wrap; }}
.field-note-kind {{ margin-top:6px; font-size:.74rem; font-weight:700; }}
.field-note-choice-source {{ margin-top:5px; font-size:.72rem; line-height:1.4; opacity:.78; }}
.field-note-choice-list code {{ font-weight:700; }}
.stage-detail-popover {{ position:fixed; z-index:12001; width:min(420px,calc(100vw - 24px)); max-height:min(70vh,620px); overflow:auto; box-sizing:border-box; border:1px solid color-mix(in srgb, currentColor 22%, transparent); border-radius:10px; padding:11px; background:Canvas; color:CanvasText; box-shadow:0 8px 24px rgba(0,0,0,.2); }}
.stage-detail-popover[hidden] {{ display:none; }}
.stage-detail-popover h4 {{ margin:0 0 3px; font-size:.82rem; }}
.stage-detail-popover p {{ margin:0 0 9px; font-size:.78rem; line-height:1.45; }}
.stage-detail-popover ul, .stage-detail-popover ol {{ margin:4px 0 10px 19px; padding:0; font-size:.76rem; line-height:1.42; }}
.stage-detail-head {{ display:flex; justify-content:space-between; gap:10px; margin-bottom:8px; }}
.stage-detail-head strong {{ font-size:.9rem; }}
@media (max-width:1000px) {{ .stage-layout {{ grid-template-columns:1fr; gap:16px; }} .stage-splitter {{ display:none; }} .stage-request-panel {{ position:static; max-height:none; }} }}
@media (max-width:850px) {{ .stage-canvas {{ height:72vh; min-height:520px; }} .stage-canvas .mermaid {{ min-width:1250px; }} }}
</style>
<div class="stage-layout">
  <main class="stage-main">
    <div class="stage-preview-head">
      <form class="workflow-version-form" method="get" action="/workflow-stage-preview/">
        {f'<input type="hidden" name="request" value="{html.escape(str(selected_request_id), quote=True)}">' if selected_request_id else ''}
        <input type="hidden" name="view" value="{display_mode}">
        <label>版本 <select name="workflow_version" aria-label="Workflow 版本" onchange="this.form.submit()">{version_selector}</select></label>
        {"<span class='workflow-version-badge'>历史只读</span>" if read_only_history else ""}
      </form>
      <nav class="stage-view-toggle" aria-label="Workflow 显示模式">
        <a href="{view_link('overview')}"{' aria-current="page"' if display_mode == 'overview' else ''}>流程概览</a>
        <a href="{view_link('full')}"{' aria-current="page"' if display_mode == 'full' else ''}>字段详情</a>
      </nav>
      <a class="stage-help-link" href="#workflow-guide">使用说明</a>
    </div>
    <p class="stage-view-hint">{'点击节点查看职责 · 切换「字段详情」检查 Agent 输入、输出与 SDK 控制' if display_mode == 'overview' else '点击 Stage 名称查看职责，点击字段查看注释与历史 · 可返回「流程概览」'}</p>
    <details class="stage-legend-details"><summary>角色颜色说明</summary>
      <div class="stage-legend">
        <span style="--c:#6f42c1;--bg:#e8ddff;--fg:#24123d">Research Lead</span>
        <span style="--c:#c7254e;--bg:#ffe0e6;--fg:#4a0b1b">Challenger</span>
        <span style="--c:#1677c8;--bg:#dceeff;--fg:#082d4c">Investigator</span>
        <span style="--c:#b77900;--bg:#fff1c9;--fg:#4a3100">Literature</span>
        <span style="--c:#198754;--bg:#d9f5e5;--fg:#07351f">Judge</span>
        <span style="--c:#0f766e;--bg:#dff4f2;--fg:#063936">Reporter</span>
        <span style="--c:#5f6875;--bg:#eceff3;--fg:#20252b">SDK</span>
      </div>
    </details>
    <div class="stage-canvas-shell">
      <div class="stage-toolbar" aria-label="Diagram view controls">
        <button type="button" data-zoom-action="out" title="Zoom out" aria-label="缩小 Workflow">−</button>
        <button type="button" data-zoom-action="fit" title="将流程图适配可视宽度" aria-label="适应可视宽度">适应</button>
        <button type="button" data-zoom-action="reset" class="zoom-value" title="Reset zoom" aria-label="重置 Workflow 缩放">100%</button>
        <button type="button" data-zoom-action="in" title="Zoom in" aria-label="放大 Workflow">＋</button>
        <span class="stage-toolbar-separator" aria-hidden="true"></span>
        <button type="button" data-view-action="height" title="流程图适配屏幕高度" aria-label="切换左图撑满高度" aria-pressed="false">↕</button>
        <button type="button" data-view-action="fullscreen" title="Workflow 全屏" aria-label="切换 Workflow 全屏" aria-pressed="false">⛶</button>
      </div>
      <div class="stage-canvas" title="Select node text normally. Drag empty space, or hold Space + drag, to pan. Ctrl/⌘ + wheel to zoom"><div class="stage-zoom-space"><pre class="mermaid">{html.escape(source)}</pre></div></div>
      <div id="field-note-popover" class="field-note-popover" hidden>
        <div class="field-note-popover-head"><code id="field-note-field"></code><span id="field-note-context" class="field-note-context"></span></div>
        <textarea id="field-note-text" maxlength="4000" aria-label="字段备注"></textarea>
        <div id="field-note-edited-at" class="field-note-time"></div>
        <div id="field-note-contract-hint" class="field-note-contract-hint"></div>
        <div id="field-note-kind" class="field-note-kind"></div>
        <details id="field-note-choice" class="field-note-extra" hidden open><summary>☑ 待选项目</summary><div id="field-note-choice-source" class="field-note-choice-source"></div><ul id="field-note-choice-list" class="field-note-extra-list field-note-choice-list"></ul></details>
        <details id="field-note-structure" class="field-note-extra" hidden><summary>实际结构</summary><ul id="field-note-structure-list" class="field-note-extra-list"></ul></details>
        <div class="field-note-footer"><div id="field-note-error" class="field-note-error" aria-live="polite"></div><button type="button" id="field-note-save"><strong>保存</strong></button></div>
        <details class="field-note-history"><summary id="field-note-history-summary">历史</summary><div id="field-note-history-list" class="field-note-history-list"></div></details>
      </div>
      <div id="stage-detail-popover" class="stage-detail-popover" hidden>
        <div class="stage-detail-head"><strong id="stage-detail-title"></strong><span class="field-note-context">Stage Card</span></div>
        <h4>Objective</h4><p id="stage-detail-objective"></p>
        <h4>May do</h4><p id="stage-detail-may-do"></p>
        <h4>Must not</h4><p id="stage-detail-must-not"></p>
        <h4>Exit / handoff</h4><p id="stage-detail-exit"></p>
        <div id="stage-detail-guidance-wrap" hidden><h4>Role guidance</h4><ul id="stage-detail-guidance"></ul></div>
        <div id="stage-detail-procedure-wrap" hidden><h4>Procedure</h4><ol id="stage-detail-procedure"></ol></div>
        <div id="stage-detail-contract-wrap" hidden><h4>Contract 条件规则</h4><ul id="stage-detail-contract"></ul></div>
      </div>
    </div>
    <details class="stage-meta stage-policy" id="workflow-guide">
      <summary>显示规则 / 维护说明</summary>
      <p><strong>这张图只审查三类东西</strong><br>① <code>Agent receives</code>：真实进入 Prompt 的字段；② <code>Agent must return</code>：正式输出 contract；③ <code>◆ SDK critical controls</code>：不发送给 Agent，但会改变 routing / authority / identity / Evidence admissibility / budget / atomic commit 的关键控制。其它普通 SDK bookkeeping 不显示。</p>
      <p class="small"><code>Agent receives</code> 直接读取 Sciver <code>prompt_stages.STAGE_CONTEXT_FIELDS</code>；<code>Agent must return</code> 直接解析 <code>PHASE_CONTRACTS</code>。<code>◆</code> 明确表示“非 Agent 字段”；<code>◌</code> 表示 optional / 条件字段；<code>☑</code> 表示确定性选择字段（固定枚举或运行时有限候选集）。<a href="#workflow-guide">展开本页规则</a></p>
    </details>
  </main>
</div>
<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
const dark = true;
mermaid.initialize({{startOnLoad:false,theme:dark ? 'dark' : 'default',securityLevel:'strict',flowchart:{{htmlLabels:true,useMaxWidth:false,curve:'basis'}}}});
await mermaid.run({{querySelector:'.mermaid'}});
const fieldMetadata = {field_notes_json};
const stageMetadata = {stage_details_json};
const workflowReadOnly = {'true' if read_only_history else 'false'};
const fieldMetaByKey = new Map(fieldMetadata.map(item => [item.key, item]));
const fieldTokenByKey = new Map();
const fieldPopover = document.getElementById('field-note-popover');
const fieldNoteContext = document.getElementById('field-note-context');
const fieldNoteField = document.getElementById('field-note-field');
const fieldNoteText = document.getElementById('field-note-text');
const fieldNoteEditedAt = document.getElementById('field-note-edited-at');
const fieldNoteError = document.getElementById('field-note-error');
const fieldNoteHistorySummary = document.getElementById('field-note-history-summary');
const fieldNoteHistoryList = document.getElementById('field-note-history-list');
const fieldNoteContractHint = document.getElementById('field-note-contract-hint');
const fieldNoteKind = document.getElementById('field-note-kind');
const fieldNoteChoice = document.getElementById('field-note-choice');
const fieldNoteChoiceSource = document.getElementById('field-note-choice-source');
const fieldNoteChoiceList = document.getElementById('field-note-choice-list');
const fieldNoteStructure = document.getElementById('field-note-structure');
const fieldNoteStructureList = document.getElementById('field-note-structure-list');
const fieldNoteSave = document.getElementById('field-note-save');
const stagePopover = document.getElementById('stage-detail-popover');
const stageDetailTitle = document.getElementById('stage-detail-title');
const stageDetailObjective = document.getElementById('stage-detail-objective');
const stageDetailMayDo = document.getElementById('stage-detail-may-do');
const stageDetailMustNot = document.getElementById('stage-detail-must-not');
const stageDetailExit = document.getElementById('stage-detail-exit');
const stageDetailGuidanceWrap = document.getElementById('stage-detail-guidance-wrap');
const stageDetailGuidance = document.getElementById('stage-detail-guidance');
const stageDetailProcedureWrap = document.getElementById('stage-detail-procedure-wrap');
const stageDetailProcedure = document.getElementById('stage-detail-procedure');
const stageDetailContractWrap = document.getElementById('stage-detail-contract-wrap');
const stageDetailContract = document.getElementById('stage-detail-contract');
let activeFieldKey = null;
const wrap = document.querySelector('.stage-canvas');
const canvasShell = document.querySelector('.stage-canvas-shell');
const zoomSpace = wrap ? wrap.querySelector('.stage-zoom-space') : null;
const mermaidNode = wrap ? wrap.querySelector('.mermaid') : null;
const zoomValue = document.querySelector('.zoom-value');
const heightButton = document.querySelector('[data-view-action="height"]');
const fullscreenButton = document.querySelector('[data-view-action="fullscreen"]');
let zoom = 1;
let baseDiagramWidth = 1500;
let baseDiagramHeight = 900;
let baseSvgWidth = 1500;
let baseSvgHeight = 900;
const measureDiagram = () => {{
  if (!mermaidNode) return;
  const svg = mermaidNode.querySelector('svg');
  mermaidNode.style.left = '0px';
  const nodeRect = mermaidNode.getBoundingClientRect();
  const svgRect = svg ? svg.getBoundingClientRect() : {{width:0, height:0}};
  baseSvgWidth = Math.max(1, svgRect.width || 0);
  baseSvgHeight = Math.max(1, svgRect.height || 0);
  baseDiagramWidth = Math.max(1500, mermaidNode.scrollWidth || 0, nodeRect.width || 0, baseSvgWidth);
  baseDiagramHeight = Math.max(1, mermaidNode.scrollHeight || 0, nodeRect.height || 0, baseSvgHeight);
}};
const renderZoom = (preserveCenter = true) => {{
  if (!mermaidNode || !zoomSpace || !wrap) return;
  const oldWidth = Math.max(1, zoomSpace.scrollWidth || zoomSpace.offsetWidth || 1);
  const oldHeight = Math.max(1, zoomSpace.scrollHeight || zoomSpace.offsetHeight || 1);
  const centerX = (wrap.scrollLeft + wrap.clientWidth / 2) / oldWidth;
  const centerY = (wrap.scrollTop + wrap.clientHeight / 2) / oldHeight;
  const scaledWidth = Math.ceil(baseDiagramWidth * zoom);
  const scaledHeight = Math.ceil(baseDiagramHeight * zoom);
  const viewportWidth = Math.max(0, wrap.clientWidth - 36);
  const viewportHeight = Math.max(0, wrap.clientHeight - 36);
  const spaceWidth = Math.max(scaledWidth, viewportWidth);
  const spaceHeight = Math.max(scaledHeight, viewportHeight);
  zoomSpace.style.width = spaceWidth + 'px';
  zoomSpace.style.height = spaceHeight + 'px';
  mermaidNode.style.width = scaledWidth + 'px';
  mermaidNode.style.height = scaledHeight + 'px';
  mermaidNode.style.left = Math.max(0, (spaceWidth - scaledWidth) / 2) + 'px';
  const svg = mermaidNode.querySelector('svg');
  if (svg) {{
    svg.style.width = Math.ceil(baseSvgWidth * zoom) + 'px';
    svg.style.height = Math.ceil(baseSvgHeight * zoom) + 'px';
  }}
  if (zoomValue) zoomValue.textContent = Math.round(zoom * 100) + '%';
  requestAnimationFrame(() => {{
    if (!preserveCenter) return;
    wrap.scrollLeft = Math.max(0, centerX * spaceWidth - wrap.clientWidth / 2);
    wrap.scrollTop = Math.max(0, centerY * spaceHeight - wrap.clientHeight / 2);
  }});
}};
measureDiagram();
if ({'true' if display_mode == 'overview' else 'false'} && wrap) {{
  zoom = Math.max(0.35, Math.min(1, (wrap.clientWidth - 48) / baseSvgWidth));
}}
renderZoom(false);
document.querySelectorAll('[data-zoom-action]').forEach(button => button.addEventListener('click', () => {{
  const action = button.dataset.zoomAction;
  if (action === 'in') zoom = Math.min(2.5, zoom + 0.1);
  if (action === 'out') zoom = Math.max(0.35, zoom - 0.1);
  if (action === 'reset') zoom = 1;
  if (action === 'fit' && wrap) zoom = Math.max(0.35, Math.min(1.5, (wrap.clientWidth - 48) / baseSvgWidth));
  renderZoom(true);
}}));
const refreshViewport = () => requestAnimationFrame(() => renderZoom(true));
if (heightButton) {{
  heightButton.addEventListener('click', () => {{
    const active = document.body.classList.toggle('stage-height-focus');
    heightButton.setAttribute('aria-pressed', active ? 'true' : 'false');
    heightButton.title = active ? '恢复普通页面布局' : '流程图适配屏幕高度';
    refreshViewport();
  }});
}}
const fullscreenActive = () => document.fullscreenElement === canvasShell || document.body.classList.contains('stage-fullscreen-fallback');
const syncFullscreenButton = () => {{
  if (!fullscreenButton) return;
  const active = fullscreenActive();
  fullscreenButton.setAttribute('aria-pressed', active ? 'true' : 'false');
  fullscreenButton.title = active ? '退出 Workflow 全屏' : 'Workflow 全屏';
  refreshViewport();
}};
if (fullscreenButton && canvasShell) {{
  fullscreenButton.addEventListener('click', async () => {{
    if (document.fullscreenElement === canvasShell) {{
      await document.exitFullscreen();
      return;
    }}
    if (document.body.classList.contains('stage-fullscreen-fallback')) {{
      document.body.classList.remove('stage-fullscreen-fallback');
      syncFullscreenButton();
      return;
    }}
    if (canvasShell.requestFullscreen) {{
      try {{
        await canvasShell.requestFullscreen();
        return;
      }} catch (_error) {{}}
    }}
    document.body.classList.add('stage-fullscreen-fallback');
    syncFullscreenButton();
  }});
  document.addEventListener('fullscreenchange', syncFullscreenButton);
}}
window.addEventListener('keydown', event => {{
  if (event.key === 'Escape' && document.body.classList.contains('stage-fullscreen-fallback')) {{
    document.body.classList.remove('stage-fullscreen-fallback');
    syncFullscreenButton();
  }}
}});
window.addEventListener('resize', refreshViewport);
if (wrap) {{
  wrap.addEventListener('wheel', event => {{
    if (!(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    zoom = Math.min(2.5, Math.max(0.35, zoom + (event.deltaY < 0 ? 0.08 : -0.08)));
    renderZoom(true);
  }}, {{passive:false}});
  let dragging = false, startX = 0, startY = 0, startLeft = 0, startTop = 0, spaceHeld = false;
  const setSpacePan = value => {{
    spaceHeld = value;
    wrap.classList.toggle('space-pan', value && !dragging);
  }};
  window.addEventListener('keydown', event => {{
    if (event.code === 'Space' && !event.repeat && !(event.target.closest && event.target.closest('input, textarea, select, button'))) {{
      event.preventDefault();
      setSpacePan(true);
    }}
  }});
  window.addEventListener('keyup', event => {{
    if (event.code === 'Space') setSpacePan(false);
  }});
  window.addEventListener('blur', () => setSpacePan(false));
  wrap.addEventListener('pointerdown', event => {{
    if (event.button !== 0 || (event.target.closest && event.target.closest('button,a,summary,.stage-toolbar'))) return;
    const onNode = Boolean(event.target.closest && event.target.closest('g.node'));
    if (onNode && !spaceHeld) return;
    dragging = true; startX = event.clientX; startY = event.clientY;
    startLeft = wrap.scrollLeft; startTop = wrap.scrollTop;
    wrap.classList.remove('space-pan');
    wrap.classList.add('is-panning'); wrap.setPointerCapture(event.pointerId);
    event.preventDefault();
  }});
  wrap.addEventListener('pointermove', event => {{
    if (!dragging) return;
    wrap.scrollLeft = startLeft - (event.clientX - startX);
    wrap.scrollTop = startTop - (event.clientY - startY);
  }});
  const endDrag = event => {{
    dragging = false; wrap.classList.remove('is-panning');
    wrap.classList.toggle('space-pan', spaceHeld);
    try {{ wrap.releasePointerCapture(event.pointerId); }} catch (_error) {{}}
  }};
  wrap.addEventListener('pointerup', endDrag);
  wrap.addEventListener('pointercancel', endDrag);
}}
const findStageNode = name => Array.from(document.querySelectorAll('.stage-canvas svg g.node')).find(el => {{
  const id = el.id || '';
  return id === name || id.includes('-' + name + '-');
}});
const renderFieldHistory = item => {{
  const history = Array.isArray(item.history) ? item.history : [];
  fieldNoteHistorySummary.textContent = '历史（' + history.length + '）';
  fieldNoteHistoryList.replaceChildren();
  for (const entry of [...history].reverse()) {{
    const row = document.createElement('div');
    row.className = 'field-note-history-item';
    const meta = document.createElement('div');
    meta.className = 'field-note-history-meta';
    const actor = document.createElement('span');
    actor.className = 'field-note-actor';
    actor.textContent = entry.actor === 'human' ? '人工修改' : '机器自动更新';
    const time = document.createElement('span');
    time.textContent = entry.edited_at ? new Date(entry.edited_at).toLocaleString() : '';
    meta.append(actor, time);
    const text = document.createElement('div');
    text.textContent = entry.description || '';
    row.append(meta, text);
    fieldNoteHistoryList.append(row);
  }}
}};
const renderSimpleList = (target, values) => {{
  target.replaceChildren();
  for (const value of values || []) {{
    const li = document.createElement('li');
    li.textContent = String(value);
    target.append(li);
  }}
}};
const syncFieldPopover = key => {{
  const item = fieldMetaByKey.get(key);
  if (!item || !fieldPopover) return;
  activeFieldKey = key;
  const isChoice = item.field_kind === 'choice';
  fieldNoteField.textContent = (isChoice ? '☑ ' : '') + item.field;
  fieldNoteContext.textContent = item.role_label + ' · ' + item.stage;
  fieldNoteText.value = item.description;
  fieldNoteText.readOnly = workflowReadOnly;
  if (fieldNoteSave) fieldNoteSave.hidden = workflowReadOnly;
  const actorLabel = workflowReadOnly ? '历史快照 · 只读' : (item.actor === 'human' ? '人工修改' : '机器自动更新');
  fieldNoteEditedAt.textContent = item.edited_at
    ? actorLabel + ' · ' + new Date(item.edited_at).toLocaleString()
    : actorLabel;
  fieldNoteContractHint.textContent = item.contract_hint ? 'Contract hint: ' + item.contract_hint : '';
  fieldNoteKind.textContent = isChoice
    ? (item.choice_mode === 'dynamic' ? '☑ 确定性字段 · 运行时有限候选集' : '☑ 确定性字段 · 固定枚举')
    : '自由回答字段';
  const choiceOptions = Array.isArray(item.choice_options) ? item.choice_options : [];
  const choiceSource = item.choice_source || '';
  fieldNoteChoice.hidden = !isChoice;
  fieldNoteChoiceSource.textContent = choiceSource
    ? '候选来源：' + choiceSource + (item.choice_source_description ? '。' + item.choice_source_description : '')
    : '';
  fieldNoteChoiceList.replaceChildren();
  for (const option of choiceOptions) {{
    const li = document.createElement('li');
    const code = document.createElement('code');
    code.textContent = option.value || '';
    li.append(code);
    if (option.description) li.append(document.createTextNode(' — ' + option.description));
    fieldNoteChoiceList.append(li);
  }}
  if (isChoice && choiceOptions.length === 0 && choiceSource) {{
    const li = document.createElement('li');
    li.textContent = '候选值由运行时上下文提供，不在静态 Workflow 中伪造固定列表。';
    fieldNoteChoiceList.append(li);
  }}
  const structure = Array.isArray(item.structure) ? item.structure : [];
  fieldNoteStructure.hidden = structure.length === 0;
  renderSimpleList(fieldNoteStructureList, structure);
  fieldNoteError.textContent = '';
  renderFieldHistory(item);
}};
const positionCompanion = (popover, token) => {{
  if (!popover || !token) return;
  popover.hidden = false;
  const rect = token.getBoundingClientRect();
  const pop = popover.getBoundingClientRect();
  const gap = 7;
  let left = rect.right + gap;
  if (left + pop.width > window.innerWidth - 8) left = rect.left - pop.width - gap;
  left = Math.max(8, Math.min(left, window.innerWidth - pop.width - 8));
  let top = rect.top - 6;
  if (top + pop.height > window.innerHeight - 8) top = window.innerHeight - pop.height - 8;
  top = Math.max(8, top);
  popover.style.left = Math.round(left) + 'px';
  popover.style.top = Math.round(top) + 'px';
}};
const openFieldPopover = (key, token) => {{
  if (stagePopover) stagePopover.hidden = true;
  syncFieldPopover(key);
  positionCompanion(fieldPopover, token);
}};
const closeFieldPopover = () => {{
  activeFieldKey = null;
  if (fieldPopover) fieldPopover.hidden = true;
}};
const updateFieldNote = async description => {{
  if (!activeFieldKey || workflowReadOnly) return;
  fieldNoteError.textContent = '';
  try {{
    const response = await fetch('/workflow-field-notes/update', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{key:activeFieldKey,description}}),
    }});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || ('HTTP ' + response.status));
    const previous = fieldMetaByKey.get(payload.item.key) || {{}};
    const item = {{...previous, ...payload.item}};
    fieldMetaByKey.set(item.key, item);
    const token = fieldTokenByKey.get(item.key);
    if (token) token.title = '点击查看/编辑备注：' + item.description;
    syncFieldPopover(item.key);
    positionCompanion(fieldPopover, token);
  }} catch (error) {{
    fieldNoteError.textContent = '保存失败：' + error;
  }}
}};
const attachFieldEditors = () => {{
  for (const item of fieldMetadata) {{
    const node = findStageNode(item.node);
    if (!node) continue;
    const token = Array.from(node.querySelectorAll('code')).find(el => (el.textContent || '').trim() === item.field);
    if (!token) continue;
    token.classList.add('field-token');
    token.tabIndex = 0;
    token.setAttribute('role','button');
    token.dataset.fieldKey = item.key;
    token.title = (workflowReadOnly ? '点击查看历史字段说明：' : '点击查看/编辑备注：') + item.description;
    fieldTokenByKey.set(item.key, token);
    token.addEventListener('click', event => {{
      event.preventDefault();
      event.stopPropagation();
      openFieldPopover(item.key, token);
    }});
    token.addEventListener('keydown', event => {{
      if (event.key === 'Enter' || event.key === ' ') {{
        event.preventDefault();
        openFieldPopover(item.key, token);
      }}
    }});
  }}
}};
const syncStagePopover = item => {{
  if (!item || !stagePopover) return;
  stageDetailTitle.textContent = item.role_label + ' · ' + item.stage;
  stageDetailObjective.textContent = item.objective || '';
  stageDetailMayDo.textContent = item.may_do || '';
  stageDetailMustNot.textContent = item.must_not || '';
  stageDetailExit.textContent = item.exit || '';
  const guidance = Array.isArray(item.role_guidance) ? item.role_guidance : [];
  const procedure = Array.isArray(item.procedure) ? item.procedure : [];
  const contractRules = Array.isArray(item.contract_rules) ? item.contract_rules : [];
  stageDetailGuidanceWrap.hidden = guidance.length === 0;
  stageDetailProcedureWrap.hidden = procedure.length === 0;
  stageDetailContractWrap.hidden = contractRules.length === 0;
  renderSimpleList(stageDetailGuidance, guidance);
  renderSimpleList(stageDetailProcedure, procedure);
  renderSimpleList(stageDetailContract, contractRules);
}};
const attachStageEditors = () => {{
  for (const item of stageMetadata) {{
    const node = findStageNode(item.node);
    if (!node) continue;
    const token = Array.from(node.querySelectorAll('strong')).find(el => (el.textContent || '').trim() === item.stage);
    if (!token) continue;
    token.classList.add('stage-token');
    token.tabIndex = 0;
    token.setAttribute('role','button');
    token.title = '点击查看完整 Stage Card / Contract 条件';
    const open = event => {{
      event.preventDefault();
      event.stopPropagation();
      closeFieldPopover();
      syncStagePopover(item);
      positionCompanion(stagePopover, token);
    }};
    token.addEventListener('click', open);
    token.addEventListener('keydown', event => {{ if (event.key === 'Enter' || event.key === ' ') open(event); }});
  }}
}};
attachFieldEditors();
attachStageEditors();
document.addEventListener('click', event => {{
  const onField = event.target.closest && event.target.closest('.field-token');
  const onStage = event.target.closest && event.target.closest('.stage-token');
  if (fieldPopover && !fieldPopover.hidden && !fieldPopover.contains(event.target) && !onField) closeFieldPopover();
  if (stagePopover && !stagePopover.hidden && !stagePopover.contains(event.target) && !onStage) stagePopover.hidden = true;
}});
window.addEventListener('keydown', event => {{
  if (event.key === 'Escape') {{
    closeFieldPopover();
    if (stagePopover) stagePopover.hidden = true;
  }}
}});
fieldNoteSave?.addEventListener('click', () => updateFieldNote(fieldNoteText.value));
for (const button of document.querySelectorAll('.impact-link')) {{
  button.addEventListener('click', () => {{
    document.querySelectorAll('.stage-focus').forEach(el => el.classList.remove('stage-focus'));
    const node = findStageNode(button.dataset.stageNode);
    if (!node || !wrap) return;
    node.classList.add('stage-focus');
    const nodeRect = node.getBoundingClientRect();
    const wrapRect = wrap.getBoundingClientRect();
    wrap.scrollTo({{
      left: wrap.scrollLeft + nodeRect.left - wrapRect.left - (wrap.clientWidth - nodeRect.width) / 2,
      top: wrap.scrollTop + nodeRect.top - wrapRect.top - (wrap.clientHeight - nodeRect.height) / 2,
      behavior: 'smooth',
    }});
  }});
}}
</script>
"""


def stage_contract_inventory(repo_root: Path | None = None) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    contracts, _cards, _labels = _source_catalog(repo_root)
    rows: list[tuple[str, str, str, tuple[str, ...]]] = []
    for (role, stage), contract in contracts.items():
        rows.append((role, stage, str(contract["ref"]), tuple(path for path, _ in _flatten_schema(contract["prompt_schema"]))))
    return tuple(rows)
