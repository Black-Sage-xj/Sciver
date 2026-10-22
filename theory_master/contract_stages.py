"""Stage schemas, phase contracts, and versioned contract registry.

This module depends on role-level validators but owns the runtime phase surface.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .cycle_criteria import criterion_rows

from .contract_roles import (
    TERMINAL_BRANCH_VERDICTS, RoleContract,
    _noop, _reporter_structure, _text,
)

SCIENTIFIC_SYNTHESIS_SCHEMA = {
    "conclusions": [],
    "open_questions": [],
}

SCIENTIFIC_MODEL_CONTENT_SCHEMA = {
    "summary": "one concise Markdown paragraph of the latest valid scientific understanding and decisive unresolved core; omit superseded history, chronology, Evidence/claim ledgers, machine metadata, JSON, and next-step plans",
}

SCIENTIFIC_MODEL_UPDATE_SCHEMA = {
    "action": "keep|replace",
    "model": SCIENTIFIC_MODEL_CONTENT_SCHEMA,
}

CYCLE_SYNTHESIS_PROMPT_NOTE = (
    "scientific_synthesis has exactly conclusions and open_questions. scientific_model_update is exclusive: "
    "action=keep omits model; action=replace supplies only model.summary. "
    "If scientific_model_pending is nonempty, return exactly one pending_dispositions entry per supplied pending_id; "
    "otherwise omit the field or return []. Copy each pending_id and give a rationale: integrated carries still-valid content into the update; "
    "superseded is already covered; rejected is contradicted or beyond adjudicated evidence."
)

LITERATURE_GAP_KIND_TO_TASK = {
    "literature_landscape": "landscape",
    "literature_targeted": "targeted_source",
    "literature_novelty": "novelty_check",
}
CANDIDATE_SELECTION_SCHEMA = {
    "selected_candidate_id": "cand-1",
    "selection_reason": "brief reason for choosing among already-qualified candidates; do not restate decisive_test",
}

# ---------------------------------------------------------------------------
# Current minimal Agent-facing schemas.
#
# Current phase contracts ask only for scientific or semantic judgments; SDK code
# deterministically owns IDs, references, routing, and the Canvas projection.
# ---------------------------------------------------------------------------

LEAD_LITERATURE_REQUEST_SCHEMA = {
    "task_type": "landscape|targeted_source|novelty_check",
    "search_target": "external scholarly field, claim, or source to investigate",
    "why_external_needed": "why supplied local/project context is insufficient",
    "expected_external_evidence": "external scholarly evidence expected to resolve the stated gap",
    "new_search_lead": "optional source/search hint; required for targeted_source",
}

MINIMAL_FRONTIER_PROPOSAL_SCHEMA = {
    "candidates": [{
        "hypothesis": "...",
        "scope": "the scientific scope/boundary of this hypothesis",
        "why_now": "...",
        "goal_advance": "...",
        "decisive_test": "test definition for this hypothesis within its stated scope",
        "lineage_anchor_node_ids": "1–3 distinct existing SQLite H IDs for a derived H",
        "evolution_operator": "required typed lineage operator for a derived H; omit for root H",
        "reconsideration_trigger": "new Evidence/dependency/scope change/removed blocker when revisiting a parked source",
    }],
    "needs_literature": "optional boolean; true requires literature_request and omits candidates; false means no Literature detour; may be omitted",
    "literature_request": LEAD_LITERATURE_REQUEST_SCHEMA,
}

MINIMAL_FRONTIER_PROPOSAL_VALIDATION_SCHEMA = {
    **MINIMAL_FRONTIER_PROPOSAL_SCHEMA,
    "candidates": [{
        **MINIMAL_FRONTIER_PROPOSAL_SCHEMA["candidates"][0],
        "lineage_anchor_node_id": "legacy single-source anchor accepted for historical candidates/checkpoints only",
    }],
}

FRONTIER_PROPOSAL_PROMPT_NOTE = """Use evolution_operator only from: refine | specialize | generalize | alternative | combine | derive | counter_hypothesis. For a root H, omit all lineage fields. If revisiting a parked source, include that H in lineage_anchor_node_ids. Anchors are directly relevant lineage references, not evidence. The SDK owns H identity and creates lineage edges; do not infer identity from history. Closed/rejected H may source a distinct new H but is not reopened. Do not return canvas_title, canvas_summary, canvas_claim, or canvas_conditions; post-lock hypothesis_display owns display output."""

MINIMAL_HYPOTHESIS_DISPLAY_SCHEMA = {
    "canvas_title": "faithful scientific title, maximum 48 characters",
    "canvas_claim": "the concrete claim to explain or predict, maximum 78 characters; do not add conclusions",
    "canvas_conditions": "the indispensable scope/conditions, maximum 54 characters",
}

HYPOTHESIS_DISPLAY_PROMPT_NOTE = """Compress only the frozen Hypothesis, Scope, and Decisive Test into the three schema fields; do not change or adjudicate the proposition or imply support. The SDK appends the epistemic label. Do not use placeholders for the claim or conditions; if faithful compression is impossible, return the unavailable marker so the SDK uses its safe fallback."""


def _hypothesis_display_structure(output: dict[str, Any], _context: Mapping[str, Any]) -> None:
    expected = {"canvas_title", "canvas_claim", "canvas_conditions"}
    if set(output) != expected:
        raise ValueError("hypothesis_display must return exactly canvas_title, canvas_claim, and canvas_conditions")
    limits = (("canvas_title", 48), ("canvas_claim", 78), ("canvas_conditions", 54))
    for key, limit in limits:
        value = output.get(key)
        if not isinstance(value, str) or not value.strip() or len(" ".join(value.split())) > limit:
            raise ValueError(f"hypothesis_display {key} must be nonempty and at most {limit} characters")
    claim = " ".join(output["canvas_claim"].split())
    conditions = " ".join(output["canvas_conditions"].split())
    placeholders = {"摘要暂不可用", "见档案", "详见档案", "无法压缩", "unknown", "n/a"}
    if claim.casefold() in {value.casefold() for value in placeholders}:
        raise ValueError("hypothesis_display canvas_claim must contain the concrete scientific claim")
    if conditions.casefold() in {value.casefold() for value in placeholders}:
        raise ValueError("hypothesis_display canvas_conditions must contain the scientific scope")
    from .hypothesis_display import compose_hypothesis_summary
    compose_hypothesis_summary(claim, conditions)


MINIMAL_SELECTION_REPAIR_SCHEMA = {
    "selected_parent_candidate_id": "optional when exactly one revise candidate is supplied; required when multiple are supplied",
    "outcome": "resolved|cannot_repair",
    "repair": {
        "blocker_closures": [{"blocker_id": "exact frozen blocker id", "resolution": "how this blocker is closed inside the frozen scientific boundary"}],
        "hypothesis_clarification": "optional; only when a blocker needs a definition clarification preserving the frozen hypothesis/scope",
        "estimand_or_operational_definition": "optional; only when a blocker needs an estimand or operational definition",
        "repaired_decisive_test": "required for resolved: complete revised decisive_test with all binding clarifications and blocker resolutions; blocker_closures alone are insufficient",
    },
    "cannot_repair_reason": "required only for cannot_repair: why the blocker cannot close without changing frozen scope or repeating adjudicated work",
}

PENDING_DISPOSITION_SCHEMA = {
    "pending_id": "exact SDK-provided scientific_model_pending item id (copy, never create)",
    "status": "integrated|superseded|rejected",
    "rationale": "brief scientific reason for this disposition",
}

MINIMAL_CYCLE_SYNTHESIS_SCHEMA = {
    "scientific_synthesis": SCIENTIFIC_SYNTHESIS_SCHEMA,
    "scientific_model_update": SCIENTIFIC_MODEL_UPDATE_SCHEMA,
    "pending_dispositions": [PENDING_DISPOSITION_SCHEMA],
}

MINIMAL_CHALLENGER_SCHEMA = {
    "reviews": [{
        "candidate_id": "exact SDK-provided candidate ID (copy, never create)",
        "verdict": "qualified|revise|reject",
        "reason": "concise rationale for the verdict",
        "blocking_issues": "ONLY for verdict=revise: array of {problem, required_change}; OMIT ENTIRELY for qualified/reject",
    }]
}

MINIMAL_REPAIR_VERIFICATION_SCHEMA = {
    "candidate_id": "exact SDK-provided repaired Candidate ID",
    "verdict": "confirmed|unresolved",
    "reason": "concise rationale; unresolved must identify the original blocker or fatal design flaw",
}

MINIMAL_SCIENTIFIC_EVIDENCE_SCHEMA = {
    "summary": "...",
    "relation": "supports|contradicts|limits_scope|invalidates|neutral",
    "criterion_ids": ["C-1"],
    "scope": "...",
}

MINIMAL_SPECIALIST_STAGE_SCHEMA = {
    "findings": [{
        "statement": "scientific result established in this turn",
        "scope": "where the finding applies",
        "limitations": "important scientific limitations",
    }],
    "remaining_scientific_question": "natural-language unresolved question, or an empty string",
}

PRESERVE_RESEARCH_SCHEMA = {
    "observations": [{"statement": "specific provisional result", "conditions": "scope and assumptions", "evidence_paths": ["path to existing reproducible source"], "limitations": "limits on inference"}],
    "methods_exhausted": [{"method": "bounded method attempted", "limitation": "what failed, without generalizing to all methods"}],
    "remaining_gap": "scientific prerequisite still missing",
    "unexecuted_test": "portion of the frozen decisive_test not executed",
    "canvas_title": "faithful short title, <=48 characters",
    "canvas_summary": "faithful scoped, explicitly unadjudicated compression, <=160 characters",
}


def _preserve_research_structure(output: dict[str, Any], _context: Mapping[str, Any]) -> None:
    if set(output) != set(PRESERVE_RESEARCH_SCHEMA):
        raise ValueError("preserve_research requires exactly the preservation schema fields")
    rows = output.get("observations")
    if not isinstance(rows, list):
        raise ValueError("preserve_research.observations must be a list")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"statement", "conditions", "evidence_paths", "limitations"}:
            raise ValueError("preserve_research observation must include statement, conditions, evidence_paths, limitations")
        for field in ("statement", "conditions", "limitations"):
            _text(row.get(field), "preserve_research.observations." + field)
        paths = row.get("evidence_paths")
        if not isinstance(paths, list) or not paths or any(not isinstance(v, str) or not v.strip() for v in paths):
            raise ValueError("preserve_research evidence_paths must have at least one source")
    methods = output.get("methods_exhausted")
    if not isinstance(methods, list):
        raise ValueError("preserve_research.methods_exhausted must be a list")
    for row in methods:
        if not isinstance(row, dict) or set(row) != {"method", "limitation"}:
            raise ValueError("preserve_research method requires method and limitation")
        _text(row.get("method"), "preserve_research.methods_exhausted.method")
        _text(row.get("limitation"), "preserve_research.methods_exhausted.limitation")
    for field in ("remaining_gap", "unexecuted_test", "canvas_title", "canvas_summary"):
        _text(output.get(field), "preserve_research." + field)
    if len(output["canvas_title"].strip()) > 48 or len(output["canvas_summary"].strip()) > 160:
        raise ValueError("preserve_research B display requires title<=48 and summary<=160 characters")


MINIMAL_INVESTIGATOR_STAGE_SCHEMA = {
    "findings": [{
        "statement": "complete, scientifically qualified result established in this turn",
        "method": "optional: actual proof, computation, or validation approach; no placeholder",
        "result": {"optional": "actual scientific result as any JSON value, preserving structure"},
        "provenance": {
            "source": "optional declaration: benchmark_provided|pre_specified_synthetic|agent_generated|external_source; not verified source authority",
            "split": "optional declared split: none|fit|validation|holdout|gate; unverified holdout/gate cannot be treated as frozen",
            "declaration_ref": "required for benchmark_provided or pre_specified_synthetic; path alone never proves pre-Cycle freeze",
            "frozen_before_current_cycle": "optional boolean self-declaration only; SDK does not treat it as independent attestation",
        },
    }],
}

MINIMAL_LITERATURE_RECORD_SCHEMA = {
    "source_ref": "paper:0 or exact DOI/title from SDK retrieval",
    "scientific_insight": "source-grounded scientific claim established by this review",
    "exact_quote": "required when source_access is metadata, abstract, or full_text",
    "locator": {"page": "", "section": "", "figure": "", "equation": "", "paragraph": ""},
    "interpretation": "what the cited source establishes for the assigned search target",
    "limitations": "important source/verification/applicability limits",
    "source_access": "metadata|abstract|full_text|unavailable",
}

MINIMAL_LITERATURE_STAGE_SCHEMA = {
    "evidence_records": [MINIMAL_LITERATURE_RECORD_SCHEMA],
    "remaining_scientific_question": "work still required after the source review, or an empty string when the literature task is internally complete",
}

MINIMAL_LITERATURE_LANDSCAPE_SCHEMA = {
    **MINIMAL_LITERATURE_STAGE_SCHEMA,
    "landscape": {
        "field_scope": "...",
        "major_routes": [{"name": "...", "focus": "...", "representative_papers": [{"source_ref": "paper:0", "relevance": "...", "source_access": "metadata|abstract|full_text"}]}],
        "representative_papers": [{"source_ref": "paper:0", "relevance": "...", "source_access": "metadata|abstract|full_text"}],
        "open_questions": ["..."],
        "relation_to_current_research": "...",
        "recommended_reading_order": ["..."],
    },
}

MINIMAL_LITERATURE_NOVELTY_SCHEMA = {
    **MINIMAL_LITERATURE_STAGE_SCHEMA,
    "positioning": {
        "assessment": "known|partially_overlapping|apparently_distinct|uncertain, with explanation",
        "closest_prior_work": [{"source_ref": "paper:0", "overlap": "...", "difference": "...", "source_access": "metadata|abstract|full_text"}],
        "priority_risks": ["..."],
        "required_citations": ["..."],
    },
}

MINIMAL_JUDGE_SCHEMA = {
    "verdict": "supported|rejected|milestone_advanced|open for terminal; null only when requesting one bounded rework",
    "rationale": "scientific evaluation of the supplied Investigation Result against the frozen decisive test",
    "objection": {
        "requested_resolution": "required only for verdict=null: smallest new discriminating scientific result needed",
        "needs_external_scholarship": "required only for verdict=null: true only when new external scholarly evidence is scientifically necessary; otherwise false",
    },
}

MINIMAL_GOAL_COMPLETION_SCHEMA = {
    "verdict": "passed|not_passed",
    "rationale": "concise reason the completed Task does or does not satisfy the Final Gate; not a next step",
}

MINIMAL_FRONTIER_MAINTENANCE_SCHEMA = {
    "updates": [{
        "hypothesis_id": "existing H id in the canonical SQLite science graph",
        "status": "closed|parked|active",
        "reason": "why this existing H is active, parked, or closed relative to the unmet Goal",
    }],
}

FRONTIER_MAINTENANCE_PROMPT_NOTE = ""

INVESTIGATOR_FINDINGS_PROMPT_NOTE = (
    "Preserve the completed research's Finding count, order, and distinct claim boundaries; do not split one result "
    "into separate computation/checking/validation Findings or merge distinct results for presentation. "
    "A Finding is one substantive scientific claim, not a diagnostic or script; keep verification with its result "
    "unless research independently establishes a separate claim. method and result are optional authored content, "
    "not placeholders. Include provenance only for strong evidence-identity claims; it is a declaration, not "
    "independent attestation. Use only the schema fields."
)

JUDGE_INVESTIGATION_RESULT_PROMPT_NOTE = "Exclusive union: terminal verdicts omit objection; verdict=null requires exactly one objection containing only requested_resolution and boolean needs_external_scholarship."

def _reject_candidate_workflow_marker(text: Any, label: str) -> None:
    value = str(text or "").strip()
    if re.match(r"^(?:\[\s*recommended\s*\]|recommended\s*:|推荐\s*[:：])", value, flags=re.IGNORECASE):
        raise ValueError(f"{label} must contain only the scientific claim, not workflow recommendation/ranking markers")


def _validate_lead_literature_detour(output: dict[str, Any], *, normal_fields: set[str]) -> bool:
    needs_literature = output.get("needs_literature", False)
    if "needs_literature" in output and not isinstance(needs_literature, bool):
        raise ValueError("needs_literature must be boolean when supplied")
    requesting = needs_literature is True or "literature_request" in output
    if not requesting:
        return False
    if output.get("needs_literature") is not True:
        raise ValueError("literature_request requires needs_literature=true")
    if normal_fields & set(output):
        raise ValueError("Lead Literature detour is mutually exclusive with normal frontier output")
    request = output.get("literature_request")
    if not isinstance(request, dict):
        raise ValueError("needs_literature=true requires literature_request")
    forbidden = {"gap_id", "source_gap_id", "triggered_by", "objection_id"} & set(request)
    if forbidden:
        raise ValueError("Lead literature_request contains SDK-owned fields: " + ", ".join(sorted(forbidden)))
    for field in ("task_type", "search_target", "why_external_needed", "expected_external_evidence"):
        _text(request.get(field), f"literature_request.{field}")
    if request.get("task_type") not in {"landscape", "targeted_source", "novelty_check"}:
        raise ValueError("Lead literature_request.task_type must be landscape|targeted_source|novelty_check")
    lead = request.get("new_search_lead")
    if lead is not None and not isinstance(lead, str):
        raise ValueError("literature_request.new_search_lead must be a string when supplied")
    if request.get("task_type") == "targeted_source" and not str(lead or "").strip():
        raise ValueError("Lead targeted_source requires a concrete new_search_lead (DOI/title/author/source hint)")
    return True


def _known_hypothesis_ids(context: Mapping[str, Any]) -> set[str] | None:
    explicit = context.get("_known_hypothesis_ids")
    if isinstance(explicit, list):
        return {
            value for value in explicit
            if isinstance(value, str) and value and value.strip() == value
        }
    graph = context.get("agent_graph")
    if not isinstance(graph, Mapping):
        return None
    columns = graph.get("hypothesis_columns")
    rows = graph.get("hypotheses")
    if not isinstance(columns, list) or "id" not in columns or not isinstance(rows, list):
        return None
    id_index = columns.index("id")
    known: set[str] = set()
    for row in rows:
        value = row.get("id") if isinstance(row, Mapping) else (
            row[id_index] if isinstance(row, (list, tuple)) and id_index < len(row) else None
        )
        if isinstance(value, str) and value and value.strip() == value:
            known.add(value)
    return known


def _minimal_frontier_structure(output: dict[str, Any], context: Mapping[str, Any]) -> None:
    if _validate_lead_literature_detour(output, normal_fields={"candidates"}):
        return
    candidates = output.get("candidates")
    if not isinstance(candidates, list) or not (1 <= len(candidates) <= 3):
        raise ValueError("frontier_proposal requires 1..3 candidates; use the smallest sufficient candidate set")
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidates[{index}] must be an object")
        forbidden = {
            "id", "hypothesis_id", "parent_hypothesis_ids",
            "required_evidence", "evidence_requirements", "local_evidence_requirements",
            "recommended", "recommendation", "rank", "priority",
        } & set(candidate)
        if forbidden:
            raise ValueError("candidate contains SDK-owned fields: " + ", ".join(sorted(forbidden)))
        for field in ("hypothesis", "scope", "why_now", "goal_advance", "decisive_test"):
            _text(candidate.get(field), f"candidates[{index}].{field}")
        allowed_candidate_fields = {
            "hypothesis", "scope", "why_now", "goal_advance", "decisive_test",
            "lineage_anchor_node_id", "lineage_anchor_node_ids", "evolution_operator", "reconsideration_trigger",
        }
        unsupported = set(candidate) - allowed_candidate_fields - forbidden
        if unsupported:
            raise ValueError(
                f"candidates[{index}] contains unsupported fields: " + ", ".join(sorted(unsupported))
            )
        lineage_anchor = candidate.get("lineage_anchor_node_id")
        if "lineage_anchor_node_id" in candidate and (
            not isinstance(lineage_anchor, str) or not lineage_anchor.strip()
            or lineage_anchor != lineage_anchor.strip()
        ):
            raise ValueError(f"candidates[{index}].lineage_anchor_node_id must be a canonical nonempty H ID")
        lineage_anchors = candidate.get("lineage_anchor_node_ids")
        if lineage_anchors is not None:
            if (not isinstance(lineage_anchors, list) or not 1 <= len(lineage_anchors) <= 3
                    or any(not isinstance(anchor, str) or not anchor.strip() or anchor != anchor.strip()
                           for anchor in lineage_anchors)
                    or len(set(lineage_anchors)) != len(lineage_anchors)):
                raise ValueError(f"candidates[{index}].lineage_anchor_node_ids requires 1..3 unique canonical H IDs")
            if lineage_anchor:
                raise ValueError(f"candidates[{index}] must not mix singular and plural lineage anchors")
        evolution_operator = candidate.get("evolution_operator")
        if evolution_operator not in (None, ""):
            allowed_evolution_operators = {
                "refine", "specialize", "generalize", "alternative",
                "combine", "derive", "counter_hypothesis",
            }
            if not isinstance(evolution_operator, str) or evolution_operator not in allowed_evolution_operators:
                raise ValueError(f"candidates[{index}].evolution_operator is invalid")
            if not lineage_anchors and not lineage_anchor:
                raise ValueError(f"candidates[{index}].lineage_anchor_node_ids required for NEW H")
            known_h = _known_hypothesis_ids(context)
            if known_h is None:
                raise ValueError("frontier_proposal lineage requires canonical SQLite H identity context")
            anchors = lineage_anchors if lineage_anchors is not None else [lineage_anchor]
            unknown = sorted(set(anchors) - known_h)
            if unknown:
                raise ValueError("lineage anchors must identify existing SQLite H nodes: " + ", ".join(unknown))
        elif lineage_anchors is not None or lineage_anchor not in (None, ""):
            raise ValueError(f"candidates[{index}] lineage anchors require evolution_operator")
        reconsideration_trigger = candidate.get("reconsideration_trigger")
        if "reconsideration_trigger" in candidate:
            if not isinstance(reconsideration_trigger, str) or not reconsideration_trigger.strip():
                raise ValueError(f"candidates[{index}].reconsideration_trigger must be nonempty")
            if not lineage_anchor and not lineage_anchors:
                raise ValueError(f"candidates[{index}].reconsideration_trigger requires a lineage anchor")
        _reject_candidate_workflow_marker(candidate.get("hypothesis"), f"candidates[{index}].hypothesis")
    if "recommended_candidate_id" in output:
        raise ValueError("recommended_candidate_id is SDK-owned/obsolete; final selection happens after Challenger")


def _minimal_selection_repair_structure(output: dict[str, Any], context: Mapping[str, Any]) -> None:
    qerr = context.get("qualification_error") if isinstance(context.get("qualification_error"), dict) else {}
    if qerr.get("verdict") != "revise":
        raise ValueError("selection repair is allowed only for a Challenger revise verdict")
    candidates = context.get("candidates") if isinstance(context.get("candidates"), list) else []
    parent_ids = {str(item.get("id")) for item in candidates if isinstance(item, dict) and item.get("id")}
    selected_parent = str(output.get("selected_parent_candidate_id") or "")
    if len(parent_ids) > 1 and selected_parent not in parent_ids:
        raise ValueError("selection repair must select one supplied revise candidate")
    if len(parent_ids) == 1 and selected_parent and selected_parent not in parent_ids:
        raise ValueError("selected_parent_candidate_id must identify the supplied revise candidate")
    forbidden_top = {"replacement_candidates", "hypothesis", "goal_advance", "scope", "evolution_operator"} & set(output)
    if forbidden_top:
        raise ValueError("selection repair may not rewrite frozen candidate fields: " + ", ".join(sorted(forbidden_top)))
    _text(output.get("outcome"), "selection repair outcome")
    outcome = str(output.get("outcome") or "").strip()
    if outcome not in {"resolved", "cannot_repair"}:
        raise ValueError("selection repair outcome must be resolved or cannot_repair")
    allowed_top = {"selected_parent_candidate_id", "outcome", "repair", "cannot_repair_reason"}
    unsupported_top = set(output) - allowed_top
    if unsupported_top:
        raise ValueError("selection repair contains unsupported top-level fields: " + ", ".join(sorted(unsupported_top)))
    if outcome == "cannot_repair":
        _text(output.get("cannot_repair_reason"), "cannot_repair_reason")
        if output.get("repair") not in (None, {}):
            raise ValueError("cannot_repair selection repair must not include a repair object")
        return
    if output.get("cannot_repair_reason") not in (None, ""):
        raise ValueError("resolved selection repair must not include cannot_repair_reason")
    repair = output.get("repair")
    if not isinstance(repair, dict):
        raise ValueError("resolved selection repair requires one repair object")
    allowed = {
        "hypothesis_clarification", "repaired_decisive_test",
        "estimand_or_operational_definition", "blocker_closures",
    }
    forbidden = set(repair) - allowed
    if forbidden:
        raise ValueError("selection repair contains fields outside the bounded repair surface: " + ", ".join(sorted(forbidden)))
    # A repaired qualification must freeze executable science, not merely a
    # convincing narrative in blocker_closures or an orphaned definition.
    _text(repair.get("repaired_decisive_test"), "repair.repaired_decisive_test")
    parent = next((item for item in candidates if isinstance(item, dict)
                   and str(item.get("id") or "") == (selected_parent or next(iter(parent_ids), ""))), {})
    original = " ".join(str(parent.get("decisive_test") or "").split())
    updated = " ".join(str(repair["repaired_decisive_test"]).split())
    if original == updated:
        raise ValueError("resolved selection repair must materially update the frozen decisive_test; use cannot_repair when no in-scope correction exists")
    for field in ("hypothesis_clarification", "estimand_or_operational_definition"):
        if field in repair:
            _text(repair.get(field), f"repair.{field}")
    reviews = context.get("candidate_reviews") if isinstance(context.get("candidate_reviews"), list) else []
    selected_for_blockers = selected_parent
    if not selected_for_blockers and len(parent_ids) == 1:
        selected_for_blockers = next(iter(parent_ids))
    review = next((item for item in reviews if isinstance(item, dict)
                   and str(item.get("candidate_id") or "") == selected_for_blockers), {})
    blockers = review.get("blocking_issues") if isinstance(review, dict) else []
    blocker_ids = [str(item.get("id") or "") for item in blockers or [] if isinstance(item, dict) and item.get("id")]
    closures = repair.get("blocker_closures")
    if not isinstance(closures, list):
        raise ValueError("resolved selection repair requires blocker_closures")
    seen: list[str] = []
    for index, closure in enumerate(closures):
        if not isinstance(closure, dict):
            raise ValueError(f"repair.blocker_closures[{index}] must be an object")
        if set(closure) - {"blocker_id", "resolution"}:
            raise ValueError(f"repair.blocker_closures[{index}] contains unsupported fields")
        _text(closure.get("blocker_id"), f"repair.blocker_closures[{index}].blocker_id")
        blocker_id = str(closure.get("blocker_id") or "").strip()
        _text(closure.get("resolution"), f"repair.blocker_closures[{index}].resolution")
        seen.append(blocker_id)
    if len(seen) != len(set(seen)):
        raise ValueError("repair.blocker_closures must not contain duplicate blocker ids")
    if set(seen) != set(blocker_ids):
        raise ValueError("resolved selection repair must close every frozen Challenger blocker exactly once")


def _minimal_cycle_synthesis_structure(output: dict[str, Any], _context: Mapping[str, Any]) -> None:
    if "mode" in output:
        raise ValueError("cycle_synthesis mode is SDK-owned and must be omitted")
    synthesis = output.get("scientific_synthesis")
    if not isinstance(synthesis, dict):
        raise ValueError("scientific_synthesis must be an object")
    if set(synthesis) != {"conclusions", "open_questions"}:
        raise ValueError("scientific_synthesis accepts exactly conclusions and open_questions")
    for field in ("conclusions", "open_questions"):
        if not isinstance(synthesis.get(field), list):
            raise ValueError(f"scientific_synthesis.{field} must be a list")
    update = output.get("scientific_model_update")
    if not isinstance(update, dict) or update.get("action") not in {"keep", "replace"}:
        raise ValueError("scientific_model_update requires action=keep|replace")
    if set(update) - {"action", "model"}:
        raise ValueError("scientific_model_update accepts only action and conditional model")
    model = update.get("model")
    if update["action"] == "keep":
        if "model" in update:
            raise ValueError("scientific_model_update.model must be omitted when action=keep")
    else:
        if not isinstance(model, dict):
            raise ValueError("replace requires a complete scientific_model_update.model")
        if set(model) != {"summary"}:
            raise ValueError("scientific_model_update.model accepts exactly summary")
        _text(model.get("summary"), "scientific_model_update.model.summary")
    forbidden = {
        "evolution_candidates", "next_frontier_index", "next_frontier_candidate_id",
        "needs_literature", "literature_request",
    } & set(output)
    if forbidden:
        raise ValueError("cycle_synthesis no longer owns frontier planning: " + ", ".join(sorted(forbidden)))
    if "pending_dispositions" in output and not isinstance(output["pending_dispositions"], list):
        raise ValueError("pending_dispositions must be a list")


def require_mainline_goal_priority(
    candidate: Mapping[str, Any],
    *,
    goal: str,
    research_contract: str,
    final_gate: str,
    label: str = "Candidate",
) -> None:
    """Refuse a current mainline Candidate that does not carry goal_advance.

    Scientific excellence, falsifiability, and executability do not admit a
    Candidate into the Cycle. goal_advance is the only carrier: it must advance
    an unmet Goal / Research Contract / Final Gate, or a prerequisite logically
    required to complete those requirements. A mechanism hypothesis with no
    necessary relation to the Final Gate is not a current mainline Candidate.
    Do not add goal_gap_relation, priority_class, or mandatory_requirement_id.
    """
    # Old fixtures omit the Research Plan texts. Skip only then. When any of
    # Goal / Research Contract / Final Gate is present, goal_advance is mandatory.
    if not any(str(text or "").strip() for text in (goal, research_contract, final_gate)):
        return
    advance = str(candidate.get("goal_advance") or "").strip()
    if not advance:
        raise ValueError(
            f"{label} requires goal_advance that advances the unmet Goal, Research Contract, or Final Gate, "
            "or a prerequisite logically necessary to complete them"
        )


def _minimal_challenger_structure(output: dict[str, Any], context: Mapping[str, Any]) -> None:
    reviews = output.get("reviews")
    candidates = context.get("candidates") if isinstance(context.get("candidates"), list) else []
    if not isinstance(reviews, list) or len(reviews) != len(candidates):
        raise ValueError("challenger must return exactly one review per supplied candidate")
    expected = [str(item.get("id") or "") for item in candidates if isinstance(item, Mapping)]
    if len(expected) != len(candidates) or any(not x for x in expected) or len(set(expected)) != len(expected):
        raise ValueError("challenger must receive distinct SDK-owned candidate IDs")
    actual: list[str] = []
    for index, review in enumerate(reviews):
        if not isinstance(review, dict):
            raise ValueError(f"reviews[{index}] must be an object")
        _text(review.get("candidate_id"), f"reviews[{index}].candidate_id")
        candidate_id = str(review["candidate_id"]).strip()
        actual.append(candidate_id)
        verdict = review.get("verdict")
        if verdict not in {"qualified", "revise", "reject"}:
            raise ValueError(f"reviews[{index}].verdict is invalid")
        _text(review.get("reason"), f"reviews[{index}].reason")
        if verdict == "qualified" and any(
            str(context.get(key) or "").strip() for key in ("goal", "research_contract", "final_gate")
        ):
            candidate = next((item for item in candidates
                              if isinstance(item, Mapping) and str(item.get("id") or "") == candidate_id), {})
            require_mainline_goal_priority(
                candidate,
                goal=str(context.get("goal") or ""),
                research_contract=str(context.get("research_contract") or ""),
                final_gate=str(context.get("final_gate") or ""),
                label=f"reviews[{index}]",
            )
        blocking = review.get("blocking_issues")
        if verdict == "revise":
            if not isinstance(blocking, list) or not blocking:
                raise ValueError(f"reviews[{index}].blocking_issues must be non-empty for revise")
            for issue in blocking:
                if not isinstance(issue, dict):
                    raise ValueError("blocking issue must be an object")
                _text(issue.get("problem"), "blocking issue problem")
                _text(issue.get("required_change"), "blocking issue required_change")
        elif blocking not in (None, [], ""):
            raise ValueError("blocking_issues is only allowed for revise")
    if len(set(actual)) != len(actual) or set(actual) != set(expected):
        raise ValueError("challenger review candidate_id must match each SDK-provided candidate exactly once")


def _minimal_repair_verification_structure(output: dict[str, Any], context: Mapping[str, Any]) -> None:
    if set(output) != {"candidate_id", "verdict", "reason"}:
        raise ValueError("repair verification accepts only candidate_id, verdict and reason")
    candidate = context.get("repaired_candidate")
    original = context.get("original_candidate")
    review = context.get("original_review")
    if not isinstance(candidate, Mapping) or not isinstance(original, Mapping) or not isinstance(review, Mapping):
        raise ValueError("repair verification requires original and repaired candidates plus frozen review")
    _text(output.get("candidate_id"), "repair verification candidate_id")
    if str(output["candidate_id"]) != str(candidate.get("id") or ""):
        raise ValueError("repair verification candidate_id does not match repaired Candidate")
    if output.get("verdict") not in {"confirmed", "unresolved"}:
        raise ValueError("repair verification verdict must be confirmed or unresolved")
    _text(output.get("reason"), "repair verification reason")
    if str(review.get("verdict")) != "revise" or not review.get("blocking_issues"):
        raise ValueError("repair verification requires frozen revise blockers")
    if str(candidate.get("parent_candidate_id") or "") != str(original.get("id") or ""):
        raise ValueError("repair verification parent Candidate mismatch")
    for field in ("hypothesis", "scope", "goal_advance", "estimand"):
        if candidate.get(field, "") != original.get(field, ""):
            raise ValueError(f"repair verification changed frozen {field}")
    if " ".join(str(candidate.get("decisive_test") or "").split()) == " ".join(str(original.get("decisive_test") or "").split()):
        raise ValueError("repair verification requires a revised decisive_test")
    expected = {str(row.get("id") or "") for row in review["blocking_issues"] if isinstance(row, Mapping)}
    if not expected or "" in expected or set(candidate.get("addressed_issue_ids") or []) != expected:
        raise ValueError("repair verification lost original blocker identity")


def _minimal_specialist_structure(output: dict[str, Any], _context: Mapping[str, Any]) -> None:
    if "critical_issue_updates" in output:
        raise ValueError("critical_issue_updates is obsolete; return findings[] with self-contained scientific statements, not routing fields")

    forbidden = {"ready_for_judge", "requested_next_capability", "literature_request", "criterion_updates", "objection_updates",
                 "evidence", "criterion_assessments", "remaining_gap", "objection_responses"}
    present = forbidden & set(output)
    if present:
        raise ValueError("specialist output contains SDK-owned fields: " + ", ".join(sorted(present)))
    findings = output.get("findings")
    if not isinstance(findings, list):
        raise ValueError("specialist findings must be a list")
    for index, item in enumerate(findings):
        if not isinstance(item, dict):
            raise ValueError(f"findings[{index}] must be an object")
        _text(item.get("statement"), f"findings[{index}].statement")
        if "method" in item:
            _text(item["method"], f"findings[{index}].method")
        if "result" in item and item["result"] is None:
            raise ValueError(f"findings[{index}].result must not be null")
        for field in ("scope", "limitations"):
            if field in item:
                _text(item.get(field), f"findings[{index}].{field}")
        forbidden_finding_fields = {"artifacts", "artifact_pointers", "artifact_roles", "artifact_path", "path"} & set(item)
        if forbidden_finding_fields:
            raise ValueError(
                f"findings[{index}] contains SDK-owned artifact fields: "
                + ", ".join(sorted(forbidden_finding_fields))
            )
    forbidden_handoffs = {"remaining_scientific_question", "gap_progress", "requested_next_executor", "gap_resolvability"} & set(output)
    if forbidden_handoffs:
        raise ValueError("Investigator must submit findings to Judge, not workflow routing fields: " + ", ".join(sorted(forbidden_handoffs)))


def validate_investigator_provenance(value: Any, *, path: str = "provenance") -> None:
    """Validate Investigator-authored provenance declarations without attesting them."""
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    allowed = {"source", "split", "declaration_ref", "frozen_before_current_cycle"}
    extra = set(value) - allowed
    if extra:
        raise ValueError(f"{path} contains unsupported fields: " + ", ".join(sorted(extra)))
    if not value:
        raise ValueError(f"{path} must contain at least one declaration")
    sources = {"benchmark_provided", "pre_specified_synthetic", "agent_generated", "external_source"}
    splits = {"none", "fit", "validation", "holdout", "gate"}
    source = value.get("source")
    if source is not None and (not isinstance(source, str) or source not in sources):
        raise ValueError(f"{path}.source must be benchmark_provided|pre_specified_synthetic|agent_generated|external_source")
    split = value.get("split")
    if split is not None and (not isinstance(split, str) or split not in splits):
        raise ValueError(f"{path}.split must be none|fit|validation|holdout|gate")
    declaration_ref = value.get("declaration_ref")
    if declaration_ref is not None and (not isinstance(declaration_ref, str) or not declaration_ref.strip()):
        raise ValueError(f"{path}.declaration_ref must be nonempty text when supplied")
    frozen = value.get("frozen_before_current_cycle")
    if frozen is not None and not isinstance(frozen, bool):
        raise ValueError(f"{path}.frozen_before_current_cycle must be boolean when supplied")
    if source in {"benchmark_provided", "pre_specified_synthetic"} and not str(declaration_ref or "").strip():
        raise ValueError(f"{path}.declaration_ref is required for {source}")


def _minimal_investigator_structure(output: dict[str, Any], context: Mapping[str, Any]) -> None:
    _minimal_specialist_structure(output, context)
    for index, item in enumerate(output.get("findings") or []):
        obsolete_fields = {"scope", "limitations", "evidence_kind", "source_refs"} & set(item)
        if obsolete_fields:
            raise ValueError(
                f"findings[{index}]: the complete scientific result belongs in statement, not obsolete output fields: "
                + ", ".join(sorted(obsolete_fields))
            )
        if "provenance" in item:
            validate_investigator_provenance(item["provenance"], path=f"findings[{index}].provenance")


def _validate_literature_map_paper(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    _text(value.get("source_ref"), f"{path}.source_ref")
    _text(value.get("relevance"), f"{path}.relevance")
    access = value.get("source_access")
    if access is not None and (not isinstance(access, str) or access not in {"metadata", "abstract", "full_text"}):
        raise ValueError(f"{path}.source_access must be metadata|abstract|full_text")


def _minimal_literature_structure(output: dict[str, Any], context: Mapping[str, Any]) -> None:
    obsolete = {"critical_issue_updates", "criterion_assessments", "objection_responses", "remaining_gap"} & set(output)
    if obsolete:
        raise ValueError(
            "Literature semantic-state fields are obsolete; return evidence_records + remaining_scientific_question only: "
            + ", ".join(sorted(obsolete))
        )

    task = context.get("literature_task")
    if not isinstance(task, dict):
        raise ValueError("Literature invocation requires SDK literature_task metadata")
    records = output.get("evidence_records")
    if not isinstance(records, list):
        raise ValueError("evidence_records must be a list")
    source_access_values = {"metadata", "abstract", "full_text", "unavailable"}
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            raise ValueError(f"evidence_records[{index}] must be an object")
        sdk_owned = {
            "evidence_id", "paper_title", "authors", "year", "doi_or_url", "local_original_path",
            "criterion_ids", "relation", "relation_to_hypothesis", "objection_ids",
        } & set(item)
        if sdk_owned:
            raise ValueError("Literature binding/source metadata fields are SDK-owned: " + ", ".join(sorted(sdk_owned)))
        _text(item.get("source_ref"), f"evidence_records[{index}].source_ref")
        _text(item.get("scientific_insight"), f"evidence_records[{index}].scientific_insight")
        _text(item.get("interpretation"), f"evidence_records[{index}].interpretation")
        source_access = item.get("source_access")
        if not isinstance(source_access, str) or source_access not in source_access_values:
            raise ValueError(f"evidence_records[{index}].source_access is invalid")
        if source_access != "unavailable":
            _text(item.get("exact_quote"), f"evidence_records[{index}].exact_quote")
            locator = item.get("locator")
            if not isinstance(locator, dict) or not any(str(value).strip() for value in locator.values()):
                raise ValueError(f"evidence_records[{index}].locator must identify source location")
    if not isinstance(output.get("remaining_scientific_question"), str):
        raise ValueError("remaining_scientific_question must be a string")
    task_type = str(task.get("task_type") or "")
    if task_type == "landscape":
        landscape = output.get("landscape")
        if not isinstance(landscape, dict):
            raise ValueError("landscape task requires landscape output")
        for field in ("field_scope", "relation_to_current_research"):
            _text(landscape.get(field), f"landscape.{field}")
        for field in ("major_routes", "representative_papers", "open_questions", "recommended_reading_order"):
            if not isinstance(landscape.get(field), list):
                raise ValueError(f"landscape.{field} must be a list")
        for index, route in enumerate(landscape["major_routes"]):
            if not isinstance(route, dict):
                raise ValueError(f"landscape.major_routes[{index}] must be an object")
            _text(route.get("name"), f"landscape.major_routes[{index}].name")
            _text(route.get("focus"), f"landscape.major_routes[{index}].focus")
            papers = route.get("representative_papers")
            if not isinstance(papers, list):
                raise ValueError(f"landscape.major_routes[{index}].representative_papers must be a list")
            for paper_index, paper in enumerate(papers):
                _validate_literature_map_paper(paper, f"landscape.major_routes[{index}].representative_papers[{paper_index}]")
        for index, paper in enumerate(landscape["representative_papers"]):
            _validate_literature_map_paper(paper, f"landscape.representative_papers[{index}]")
        for field in ("open_questions", "recommended_reading_order"):
            for index, item in enumerate(landscape[field]):
                _text(item, f"landscape.{field}[{index}]")
    if task_type == "novelty_check":
        positioning = output.get("positioning")
        if not isinstance(positioning, dict):
            raise ValueError("novelty_check requires positioning output")
        allowed_assessments = {"known", "partially_overlapping", "apparently_distinct", "uncertain"}
        assessment = positioning.get("assessment")
        if not isinstance(assessment, str) or assessment not in allowed_assessments:
            raise ValueError("positioning.assessment must be known|partially_overlapping|apparently_distinct|uncertain")
        prior_work = positioning.get("closest_prior_work")
        if not isinstance(prior_work, list):
            raise ValueError("positioning.closest_prior_work must be a list")
        for index, item in enumerate(prior_work):
            if not isinstance(item, dict):
                raise ValueError(f"positioning.closest_prior_work[{index}] must be an object")
            for field in ("source_ref", "overlap", "difference"):
                _text(item.get(field), f"positioning.closest_prior_work[{index}].{field}")
            access = item.get("source_access")
            if access is not None and (not isinstance(access, str) or access not in {"metadata", "abstract", "full_text"}):
                raise ValueError(f"positioning.closest_prior_work[{index}].source_access is invalid")
        for field in ("priority_risks", "required_citations"):
            values = positioning.get(field)
            if not isinstance(values, list):
                raise ValueError(f"positioning.{field} must be a list")
            for index, item in enumerate(values):
                _text(item, f"positioning.{field}[{index}]")


def _minimal_judge_structure(output: dict[str, Any], context: Mapping[str, Any]) -> None:
    verdict = output.get("verdict")
    if verdict not in TERMINAL_BRANCH_VERDICTS | {None, "null"}:
        raise ValueError("judge verdict is invalid")
    _text(output.get("rationale"), "judge rationale")
    forbidden = {
        "objections", "evidence_assessments", "assessment_state", "missing_evidence", "rework_plan",
        "target_executor", "required_evidence_ids", "required_artifact_paths", "artifact_paths", "artifacts",
    } & set(output)
    if forbidden:
        raise ValueError("Judge output contains SDK-owned/obsolete fields: " + ", ".join(sorted(forbidden)))
    objection = output.get("objection")
    if verdict in (None, "null"):
        if not isinstance(objection, dict):
            raise ValueError("non-terminal Judge output requires exactly one objection object")
    elif objection not in (None, {}):
        raise ValueError("terminal Judge verdict must not request rework")
    if verdict not in (None, "null"):
        return
    allowed = {"requested_resolution", "needs_external_scholarship"}
    extra = set(objection) - allowed
    if extra:
        raise ValueError("Judge objection contains SDK-owned/obsolete fields: " + ", ".join(sorted(extra)))
    _text(objection.get("requested_resolution"), "objection.requested_resolution")
    if not isinstance(objection.get("needs_external_scholarship"), bool):
        raise ValueError("objection.needs_external_scholarship must be boolean")


def _frontier_maintenance_structure(output: dict[str, Any], context: Mapping[str, Any]) -> None:
    extra = set(output) - {"updates"}
    if extra:
        raise ValueError("frontier_maintenance contains unsupported fields: " + ", ".join(sorted(extra)))
    updates = output.get("updates")
    if not isinstance(updates, list):
        raise ValueError("frontier_maintenance updates must be a list")
    known_h = _known_hypothesis_ids(context)
    if updates and known_h is None:
        raise ValueError("frontier_maintenance requires canonical SQLite H identity context")
    seen: set[str] = set()
    for index, item in enumerate(updates):
        if not isinstance(item, dict):
            raise ValueError(f"frontier_maintenance updates[{index}] must be an object")
        item_extra = set(item) - {"hypothesis_id", "status", "reason"}
        if item_extra:
            raise ValueError(
                f"frontier_maintenance updates[{index}] contains unsupported fields: "
                + ", ".join(sorted(item_extra))
            )
        _text(item.get("hypothesis_id"), f"updates[{index}].hypothesis_id")
        hypothesis_id = str(item.get("hypothesis_id") or "")
        if hypothesis_id != hypothesis_id.strip():
            raise ValueError(f"updates[{index}].hypothesis_id must be a canonical H ID")
        if known_h is not None and hypothesis_id not in known_h:
            raise ValueError(f"frontier_maintenance unknown H id in canonical SQLite graph: {hypothesis_id}")
        if hypothesis_id in seen:
            raise ValueError(f"frontier_maintenance duplicate hypothesis_id: {hypothesis_id}")
        seen.add(hypothesis_id)
        status = item.get("status")
        if not isinstance(status, str) or status not in {"closed", "parked", "active"}:
            raise ValueError(f"updates[{index}].status must be closed|parked|active")
        _text(item.get("reason"), f"updates[{index}].reason")


def _goal_completion_structure(output: dict[str, Any], context: Mapping[str, Any]) -> None:
    """Final Gate verdict only. not_passed does not name a next scientific step."""
    allowed = {"verdict", "rationale"}
    extra = set(output) - allowed
    if extra:
        raise ValueError("goal_completion contains unsupported fields: " + ", ".join(sorted(extra)))
    verdict = output.get("verdict")
    if verdict not in {"passed", "not_passed"}:
        raise ValueError("goal_completion verdict must be passed|not_passed")
    _text(output.get("rationale"), "goal_completion rationale")


REPORTER_FORBIDDEN_STATUS_FIELDS = frozenset({"scientific_status", "frontier_status"})
LEAD_SDK_OWNED_FIELDS = frozenset({
    "hypothesis_id", "decision_id", "frontier_status", "scientific_status",
    "next_frontier_candidate_id", "next_start_node_id", "canvas_title", "canvas_summary",
})


def _reject_sdk_owned_lead_fields(output: dict[str, Any]) -> None:
    forbidden = sorted(LEAD_SDK_OWNED_FIELDS & set(output))
    if forbidden:
        raise ValueError("qualification output contains SDK-owned fields: " + ", ".join(forbidden))


def _lead_selection_structure(output: dict[str, Any], context: Mapping[str, Any]) -> None:
    _reject_sdk_owned_lead_fields(output)
    unsupported = set(output) - {"selected_candidate_id", "selection_reason"}
    if unsupported:
        raise ValueError("candidate_selection contains unsupported fields: " + ", ".join(sorted(unsupported)))
    _text(output.get("selected_candidate_id"), "selected_candidate_id")
    _text(output.get("selection_reason"), "selection_reason")
    candidates = context.get("candidates")
    if isinstance(candidates, list):
        known = {str(item.get("id")) for item in candidates if isinstance(item, dict) and item.get("id")}
        if known and str(output["selected_candidate_id"]) not in known:
            raise ValueError("selected_candidate_id must identify a known candidate")
    banned = {str(item) for item in (context.get("banned_candidate_ids") or [])}
    if str(output["selected_candidate_id"]) in banned:
        raise ValueError("selected candidate is not lockable")


PHASE_CONTRACTS: dict[tuple[str, str], RoleContract] = {
    ("research_lead", "frontier_proposal"): RoleContract(
        "theory-master/role/research-lead/frontier-proposal", 12,
        MINIMAL_FRONTIER_PROPOSAL_SCHEMA, _minimal_frontier_structure, _noop,
        FRONTIER_PROPOSAL_PROMPT_NOTE,
        validation_schema=MINIMAL_FRONTIER_PROPOSAL_VALIDATION_SCHEMA,
    ),
    ("research_lead", "hypothesis_display"): RoleContract(
        "theory-master/role/research-lead/hypothesis-display", 1,
        MINIMAL_HYPOTHESIS_DISPLAY_SCHEMA, _hypothesis_display_structure, _noop,
        HYPOTHESIS_DISPLAY_PROMPT_NOTE,
    ),
    ("research_lead", "candidate_selection"): RoleContract(
        "theory-master/role/research-lead/candidate-selection", 3,
        CANDIDATE_SELECTION_SCHEMA, _lead_selection_structure, _noop,
    ),
    ("research_lead", "selection_repair"): RoleContract(
        "theory-master/role/research-lead/selection-repair", 8,
        MINIMAL_SELECTION_REPAIR_SCHEMA, _minimal_selection_repair_structure, _noop,
    ),
    ("research_lead", "cycle_synthesis"): RoleContract(
        "theory-master/role/research-lead/cycle-synthesis", 7,
        MINIMAL_CYCLE_SYNTHESIS_SCHEMA, _minimal_cycle_synthesis_structure, _noop,
        CYCLE_SYNTHESIS_PROMPT_NOTE,
    ),
    ("research_lead", "frontier_maintenance"): RoleContract(
        "theory-master/role/research-lead/frontier-maintenance", 3,
        MINIMAL_FRONTIER_MAINTENANCE_SCHEMA, _frontier_maintenance_structure, _noop,
        FRONTIER_MAINTENANCE_PROMPT_NOTE,
    ),
    ("challenger", "candidate_review"): RoleContract(
        "theory-master/role/challenger/candidate-review", 5,
        MINIMAL_CHALLENGER_SCHEMA, _minimal_challenger_structure, _noop,
    ),
    ("challenger", "repair_verification"): RoleContract(
        "theory-master/role/challenger/repair-verification", 2,
        MINIMAL_REPAIR_VERIFICATION_SCHEMA, _minimal_repair_verification_structure, _noop,
    ),
    ("investigator", "preserve_research"): RoleContract(
        "theory-master/role/investigator/preserve-research", 1,
        PRESERVE_RESEARCH_SCHEMA, _preserve_research_structure, _noop,
    ),
    ("investigator", "investigate"): RoleContract(
        "theory-master/role/investigator/investigate", 10,
        MINIMAL_INVESTIGATOR_STAGE_SCHEMA, _minimal_investigator_structure, _noop,
        INVESTIGATOR_FINDINGS_PROMPT_NOTE,
    ),
    ("investigator", "integrate_literature"): RoleContract(
        "theory-master/role/investigator/integrate-literature", 10,
        MINIMAL_INVESTIGATOR_STAGE_SCHEMA, _minimal_investigator_structure, _noop,
        INVESTIGATOR_FINDINGS_PROMPT_NOTE,
    ),
    ("investigator", "judge_rework"): RoleContract(
        "theory-master/role/investigator/judge-rework", 10,
        MINIMAL_INVESTIGATOR_STAGE_SCHEMA, _minimal_investigator_structure, _noop,
        INVESTIGATOR_FINDINGS_PROMPT_NOTE,
    ),
    ("literature", "landscape"): RoleContract(
        "theory-master/role/literature/landscape", 5,
        MINIMAL_LITERATURE_LANDSCAPE_SCHEMA, _minimal_literature_structure, _noop,
    ),
    ("literature", "targeted_source"): RoleContract(
        "theory-master/role/literature/targeted-source", 5,
        MINIMAL_LITERATURE_STAGE_SCHEMA, _minimal_literature_structure, _noop,
    ),
    ("literature", "novelty_check"): RoleContract(
        "theory-master/role/literature/novelty-check", 5,
        MINIMAL_LITERATURE_NOVELTY_SCHEMA, _minimal_literature_structure, _noop,
    ),
    ("judge", "terminal_assessment"): RoleContract(
        "theory-master/role/judge/terminal-assessment", 8,
        MINIMAL_JUDGE_SCHEMA, _minimal_judge_structure, _noop,
        JUDGE_INVESTIGATION_RESULT_PROMPT_NOTE,
    ),
    ("judge", "goal_completion"): RoleContract(
        "theory-master/role/judge/goal-completion", 2,
        MINIMAL_GOAL_COMPLETION_SCHEMA, _goal_completion_structure, _noop,
    ),
    ("reporter", "report"): RoleContract(
        "theory-master/role/reporter", 3,
        {"title": "...", "summary": "..."}, _reporter_structure, _noop,
    ),
}


def role_contract(role: str, version: int | None = None, *, phase: str | None = None) -> RoleContract:
    """Resolve a current runtime contract.

    Current contracts are stage-specific by design; unsupported historical phases are not registered.
    """
    if phase is None:
        raise ValueError(
            "current TheoryMaster contracts are stage-specific; pass phase=... "
            "historical run contracts are not registered"
        )
    contract = PHASE_CONTRACTS.get((role, phase))
    if contract is None:
        raise ValueError(f"no phase contract registered for {role!r}/{phase!r}")
    if version is not None and version != contract.version:
        raise ValueError(f"no contract version {version} registered for {role!r}/{phase!r}")
    return contract


def contract_by_id(contract_id: str, version: int | None = None) -> RoleContract | None:
    """Resolve only current stage-contract IDs."""
    for contract in PHASE_CONTRACTS.values():
        if contract.contract_id == contract_id:
            return contract if version in (None, contract.version) else None
    return None
