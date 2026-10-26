"""Stage routing and compact prompt construction for TheoryMaster roles."""
from __future__ import annotations

import json
from typing import Any, Mapping

from .evidence import adjudicable_manifest_records
from .cycle_criteria import criterion_rows
from .scientific_objective import SCIENCE_FIELDS, frozen_science_view, locked_science_view


from .stage_registry import (
    ROLE_STAGE_LABELS, STAGE_CARDS, procedure_card_text,
    stage_card_text, stage_definition_text, stage_for,
)


# Authoritative human-facing inventory of fields that may enter each active
# Agent prompt. TM Observatory reads this source declaration directly instead
# of maintaining a second copy. Keep it in lockstep with stage_context().
STAGE_CONTEXT_FIELDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("research_lead", "frontier_proposal"): (
        "goal", "research_contract", "final_gate", "scientific_model_summary",
        "agent_graph", "agent_graph_reuse", "science_graph_tools", "running_hypotheses",
        "operator_guidance",
        "qualification_feedback", "lead_literature_result", "workspace_access",
    ),
    ("research_lead", "hypothesis_display"): ("frozen_hypothesis",),
    ("research_lead", "candidate_selection"): (
        "qualified_candidates",
    ),
    ("research_lead", "selection_repair"): (
        "repair_options",
    ),
    ("research_lead", "cycle_synthesis"): (
        "active_hypothesis", "judge_decision", "scientific_access",
    ),
    ("research_lead", "frontier_maintenance"): (
        "goal", "research_contract", "final_gate", "scientific_model_summary",
        "judge_decision", "scientific_synthesis", "current_hypothesis", "agent_graph", "agent_graph_reuse", "science_graph_tools",
    ),
    ("challenger", "candidate_review"): (
        "goal", "research_contract", "final_gate", "candidate",
        "agent_graph", "agent_graph_reuse", "science_graph_tools", "workspace_access",
    ),
    ("challenger", "repair_verification"): (
        "goal", "research_contract", "final_gate", "original_candidate", "repaired_candidate",
        "original_blockers", "agent_graph", "agent_graph_reuse", "science_graph_tools", "workspace_access",
    ),
    ("investigator", "preserve_research"): (
        "frozen_objective", "blocked_reason", "source_cycle", "workspace_access",
    ),
    ("investigator", "investigate"): (
        "frozen_objective", "qualification_handoff", "previous_scientific_delta", "workspace_access",
    ),
    ("investigator", "integrate_literature"): (
        "frozen_objective", "qualification_handoff", "previous_scientific_delta", "workspace_access",
    ),
    ("investigator", "judge_rework"): (
        "frozen_objective", "qualification_handoff", "previous_scientific_delta", "judge_objections", "workspace_access",
    ),
    ("literature", "landscape"): (
        "search_target", "scholarly_retrieval_root",
    ),
    ("literature", "targeted_source"): (
        "search_target", "source_lock", "scholarly_retrieval_root",
    ),
    ("literature", "novelty_check"): (
        "search_target", "scholarly_retrieval_root",
    ),
    ("judge", "terminal_assessment"): (
        "hypothesis_lock", "investigation_result", "prior_objection",
        "prior_adjudicated_context",
    ),
    ("judge", "goal_completion"): (
        "research_plan_access", "agent_graph", "agent_graph_reuse",
    ),
    ("reporter", "report"): (
        "hypothesis", "judge_decision", "scientific_synthesis",
        "scientific_model_summary", "evidence_scope_digest",
    ),
}

def _workspace_access(context: Mapping[str, Any]) -> dict[str, Any]:
    """Expose stable roots, never an SDK-preselected reading list."""
    return {key: value for key, value in {
        "task_home": context.get("task_home"),
        "note_area": context.get("note_area"),
        "research_root": context.get("research_root"),
        "cycle_dir": context.get("cycle_dir"),
        "cycle_workspace": context.get("cycle_workspace"),
        "benchmark_root": context.get("benchmark_root"),
        "legacy_research_root": context.get("legacy_research_root"),
        "notes_dir": context.get("notes_dir"),
        "notes_vault_root": context.get("notes_vault_root"),
        "repo_root": context.get("repo_root"),
    }.items() if value not in (None, "", [], {})}


def _graph_access(context: Mapping[str, Any]) -> dict[str, Any]:
    """Require an SDK-derived SQLite view; Canvas is never an Agent data source."""
    graph = context.get("agent_graph")
    if isinstance(graph, Mapping):
        return {"agent_graph": graph}
    reuse = context.get("agent_graph_reuse")
    if isinstance(reuse, Mapping):
        return {"agent_graph_reuse": reuse}
    raise RuntimeError(
        "SQLite Agent graph context is missing"
    )


def _literature_search_target(value: Any) -> str:
    """Build one self-contained scientific Literature assignment from SDK task state."""
    if not isinstance(value, dict):
        return ""
    parts: list[str] = []
    target = str(value.get("search_target") or "").strip()
    if target:
        parts.append(target)
    for label, key in (
        ("Why external scholarship is needed", "why_external_needed"),
        ("Evidence needed", "expected_external_evidence"),
        ("Specific search lead", "new_search_lead"),
    ):
        text = str(value.get(key) or "").strip()
        if text and text.casefold() not in {part.casefold() for part in parts}:
            parts.append(f"{label}: {text}")
    return "\n".join(parts)


def _scholarly_retrieval_root(value: Any) -> str:
    """Expose one stable navigation root; Literature discovers needed artifacts itself."""
    if not isinstance(value, dict):
        return ""
    return str(value.get("output_dir") or "").strip()


def _judge_decision_digest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value.get(key) for key in (
        "verdict", "assessment_state", "rationale", "closure_reason", "resource_disposition",
    ) if value.get(key) not in (None, "", [], {})}


def _compact_model_rows(value: Any, *, limit: int = 3) -> list[Any]:
    rows = list(value or []) if isinstance(value, list) else []
    compact: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            compact.append(row)
            continue
        compact.append({key: val for key, val in {
            "id": row.get("id"), "status": row.get("status"),
            "statement": row.get("statement"),
            "question": row.get("question"),
            "scope": row.get("scope"),
            "limitations": row.get("limitations"),
            "evidence_refs": list(row.get("evidence_refs") or []),
        }.items() if val not in (None, "", [], {})})
    return compact


def _candidate_digest(value: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    selected = rows if limit is None else rows[-limit:]
    result: list[dict[str, Any]] = []
    for row in selected:
        if not isinstance(row, dict):
            continue
        background = {key: val for key, val in {
            "id": row.get("id"), "title": row.get("title"),
            "why_now": row.get("why_now"), "goal_advance": row.get("goal_advance"),
            "lineage_anchor_node_id": row.get("lineage_anchor_node_id"),
            "evolution_operator": row.get("evolution_operator"), "status": row.get("status"),
            "evidence_refs": list(row.get("evidence_refs") or []),
            "rationale": row.get("rationale"),
        }.items() if val not in (None, "", [], {})}
        candidate_id = background.pop("id", None)
        result.append({**({"id": candidate_id} if candidate_id else {}),
                       "frozen_objective": frozen_science_view(row),
                       "proposal_context": background})
    return result


def _scientific_synthesis_view(value: Any) -> dict[str, Any]:
    """Project historical synthesis facts without obsolete frontier authority."""
    if not isinstance(value, dict):
        return {}
    return {
        key: val for key, val in value.items()
        if key != "next_frontier_recommendation"
    }


def _repair_candidate_view(value: Mapping[str, Any]) -> dict[str, Any]:
    # Preserve the bounded review's old inputs; history/motivation belong to
    # candidate_review and must not reopen qualification during verification.
    science = {key: value[key] for key in ("id", *SCIENCE_FIELDS, "goal_advance") if key in value}
    return {**_candidate_digest([science])[0],
            **{key: value[key] for key in ("estimand", "parent_candidate_id", "repair_details", "blocker_closures")
               if key in value}}


def _candidate_index(value: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    selected = rows if limit is None else rows[-limit:]
    return [{key: val for key, val in {
        "id": row.get("id"), "title": row.get("title"),
    }.items() if val not in (None, "", [], {})} for row in selected if isinstance(row, dict)]


def _qualified_candidate_options(value: Any, *, continuation: bool) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        option = {
            "id": row.get("id"),
            "title": row.get("title"),
        }
        if not continuation:
            option.update({
                "hypothesis": row.get("hypothesis"),
                "scope": row.get("scope"),
                "why_now": row.get("why_now"),
                "goal_advance": row.get("goal_advance"),
                "decisive_test": row.get("decisive_test"),
            })
        result.append({key: val for key, val in option.items() if val not in (None, "", [], {})})
    return result


def _repair_options(
    candidate_value: Any,
    review_value: Any,
    *,
    continuation: bool,
    fallback_blockers: Any = None,
) -> list[dict[str, Any]]:
    candidates = candidate_value if isinstance(candidate_value, list) else []
    reviews = review_value if isinstance(review_value, list) else []
    review_by_id = {
        str(row.get("candidate_id") or ""): row
        for row in reviews
        if isinstance(row, dict) and row.get("candidate_id")
    }
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("id") or "")
        review = review_by_id.get(candidate_id, {})
        blockers = review.get("blocking_issues") if isinstance(review, dict) else []
        if not blockers and len(candidates) == 1 and isinstance(fallback_blockers, list):
            blockers = fallback_blockers
        option: dict[str, Any] = {
            "candidate_id": candidate_id,
            "title": candidate.get("title"),
            "blockers": [
                {key: val for key, val in {
                    "id": blocker.get("id"),
                    "problem": blocker.get("problem"),
                    "required_change": blocker.get("required_change"),
                }.items() if val not in (None, "", [], {})}
                for blocker in (blockers or [])
                if isinstance(blocker, dict)
            ],
        }
        if not continuation:
            option.update({
                "hypothesis": candidate.get("hypothesis"),
                "scope": candidate.get("scope"),
                "goal_advance": candidate.get("goal_advance"),
                "decisive_test": candidate.get("decisive_test"),
            })
        result.append({key: val for key, val in option.items() if val not in (None, "", [], {})})
    return result


def _review_items(value: Any, *, limit: int = 1) -> list[Any]:
    rows = value if isinstance(value, list) else []
    compact: list[Any] = []
    for row in rows:
        if isinstance(row, dict):
            compact.append({key: val for key, val in {
                "id": row.get("id"), "issue": row.get("issue"),
                "reason": row.get("reason"), "requirement": row.get("requirement"),
                "description": row.get("description"),
            }.items() if val not in (None, "", [], {})})
        else:
            compact.append(row)
    return compact


def _qualification_feedback(value: Any, *, continuation: bool = False) -> dict[str, Any]:
    """Project current qualification recovery feedback without old state aliases."""
    if not isinstance(value, Mapping):
        return {}
    reviews = _review_digest(value.get("reviews"))
    if reviews:
        return {"candidate_reviews": reviews}
    reason = str(value.get("reason") or "").strip()
    return {"reason": reason} if reason else {}


def _review_digest(value: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    selected = rows if limit is None else rows[-limit:]
    result: list[dict[str, Any]] = []
    for row in selected:
        if not isinstance(row, dict):
            continue
        result.append({key: val for key, val in {
            "candidate_id": row.get("candidate_id"),
            "verdict": row.get("verdict"),
            "reason": row.get("reason"),
            "blocking_issues": [
                {k: v for k, v in {
                    "id": issue.get("id"),
                    "problem": issue.get("problem"),
                    "required_change": issue.get("required_change"),
                }.items() if v not in (None, "", [], {})}
                for issue in (row.get("blocking_issues") or [])
                if isinstance(issue, dict)
            ],
        }.items() if val not in (None, "", [], {})})
    return result


def _literature_digest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    records = value.get("evidence_records") if isinstance(value.get("evidence_records"), list) else []
    landscape = value.get("landscape") if isinstance(value.get("landscape"), dict) else {}
    positioning = value.get("positioning") if isinstance(value.get("positioning"), dict) else {}
    return {key: val for key, val in {
        "status": value.get("status"),
        "reason": value.get("reason"),
        "evidence_records": [{
            k: row.get(k) for k in (
                "evidence_id", "source_ref", "paper_title", "year", "doi_or_url",
                "scientific_insight", "relation", "criterion_ids", "interpretation",
                "limitations", "source_access", "locator",
            ) if row.get(k) not in (None, "", [], {})
        } for row in records if isinstance(row, dict)],
        "landscape": {k: landscape.get(k) for k in (
            "field_scope", "major_routes", "representative_papers", "open_questions",
            "relation_to_current_research", "recommended_reading_order",
        ) if landscape.get(k) not in (None, "", [], {})},
        "positioning": {k: positioning.get(k) for k in (
            "assessment", "closest_prior_work", "priority_risks", "required_citations",
        ) if positioning.get(k) not in (None, "", [], {})},
    }.items() if val not in (None, "", [], {})}


def _model_summary(model: Any) -> dict[str, Any]:
    if not isinstance(model, dict):
        return {}
    return {
        "version": model.get("version"),
        "summary": model.get("summary") or "",
    }


def _model_authoring_view(model: Any) -> dict[str, Any]:
    """Complete long-term scientific content for Research Lead editing.

    Provenance and SDK persistence metadata are intentionally omitted; the Lead
    authors science, not version bookkeeping.
    """
    if not isinstance(model, dict):
        return {}
    return {
        "version": model.get("version"),
        "goal_id": model.get("goal_id"),
        "summary": model.get("summary") or "",
    }


def _adjudicable_evidence_refs(context: Mapping[str, Any]) -> list[str]:
    manifest = context.get("evidence_manifest")
    if isinstance(manifest, dict):
        return [
            str(item.get("evidence_id"))
            for item in adjudicable_manifest_records(manifest)
            if item.get("evidence_id")
        ]
    # Historical prompt contexts may contain only already-projected refs.
    refs = context.get("evidence_refs")
    return [str(item) for item in refs] if isinstance(refs, list) else []


def compact_hypothesis_view(value: Any) -> dict[str, Any]:
    """Prompt-only view of a locked hypothesis.

    The persistent lock intentionally keeps qualification/provenance/recovery
    detail. Scientific turns need one authoritative statement of H and its
    frozen local criteria; investigation planning is gap-driven after lock.
    """
    if not isinstance(value, dict):
        return {}
    progress = value.get("cycle_criteria") or value.get("cycle_progress_criteria") or []
    criteria = [
        {k: v for k, v in {
            "id": item.get("id") or item.get("criterion_id"),
            "criterion": item.get("criterion"),
        }.items() if v not in (None, "", [], {})}
        for item in criterion_rows(progress)
    ]
    return {key: val for key, val in {
        "hypothesis": value.get("hypothesis"),
        "scope": value.get("scope"),
        "decisive_test": value.get("decisive_test"),
        "cycle_criteria": criteria,
    }.items() if val not in (None, "", [], {})}

def _investigator_objective_view(value: Any) -> dict[str, Any]:
    """Only the frozen science the Investigator must reason about.

    Stable IDs and criterion objects remain available to SDK validation/B/C
    closure but do not enter the Investigator A prompt.
    """
    return locked_science_view(value)


def _qualification_handoff_view(value: Any) -> dict[str, Any] | None:
    """Expose only a cannot_repair review's deferred work to the Investigator."""
    if not isinstance(value, dict):
        return None
    qualification = value.get("qualification") if isinstance(value.get("qualification"), dict) else {}
    review = qualification.get("review") if isinstance(qualification.get("review"), dict) else {}
    if review.get("qualification_basis") != "cannot_repair_passthrough":
        return None
    blockers = review.get("deferred_challenger_blockers")
    return {key: item for key, item in {
        "cannot_repair_reason": review.get("cannot_repair_reason"),
        "deferred_blockers": blockers if isinstance(blockers, list) else [],
    }.items() if item not in (None, "", [], {})}


def _judge_hypothesis_view(value: Any) -> dict[str, Any]:
    """Only the frozen scientific claim Judge must adjudicate."""
    return locked_science_view(value)


def _judge_investigation_result_view(value: Any) -> dict[str, Any]:
    """Strip SDK bookkeeping from the persisted compact Investigation Result."""
    if not isinstance(value, dict):
        return {}
    findings: list[dict[str, Any]] = []
    for raw in value.get("findings") or []:
        if not isinstance(raw, dict):
            continue
        row = {key: raw.get(key) for key in (
            "statement", "scope", "limitations",
            "origin_cycle", "origin_cycles", "analysis_cycle", "freshness",
            "provenance", "source_access",
        ) if raw.get(key) not in (None, "", [], {})}
        # Optional Investigator-authored science is not SDK bookkeeping.
        # Preserve exact method text and arbitrary JSON result for independent Judge review.
        for key in ("method", "result"):
            if key in raw:
                row[key] = raw[key]
        if row:
            findings.append(row)
    return {"findings": findings} if findings else {"findings": []}


def _prior_objection_view(value: Any) -> dict[str, Any]:
    """Project only the unresolved scientific request from the prior Judge turn."""
    if not isinstance(value, dict):
        return {}
    return {key: val for key, val in {
        "rationale": value.get("rationale") or value.get("reason"),
        "requested_resolution": value.get("requested_resolution"),
    }.items() if val not in (None, "", [], {})}


def _cycle_synthesis_hypothesis_view(value: Any) -> dict[str, Any]:
    """Minimal frozen science needed to interpret the terminal Judge decision."""
    if not isinstance(value, dict):
        return {}
    return {key: val for key, val in {
        "hypothesis": value.get("hypothesis"),
        "scope": value.get("scope"),
        "decisive_test": value.get("decisive_test") or value.get("cycle_milestone"),
    }.items() if val not in (None, "", [], {})}


def _cycle_synthesis_judge_view(value: Any) -> dict[str, Any]:
    """Terminal scientific judgment only; routing/closure bookkeeping stays in SDK."""
    if not isinstance(value, dict):
        return {}
    return {key: val for key, val in {
        "verdict": value.get("verdict"),
        "rationale": value.get("rationale"),
    }.items() if val not in (None, "", [], {})}


def _cycle_synthesis_access(context: Mapping[str, Any]) -> dict[str, Any]:
    """Stable navigation anchors; scientific content is opened by the Lead on demand."""
    manifest = context.get("evidence_manifest")
    manifest_path = manifest.get("path") if isinstance(manifest, dict) else None
    return {key: val for key, val in {
        "scientific_model_path": context.get("scientific_model_path"),
        "evidence_manifest_path": manifest_path,
        "cycle_dir": context.get("cycle_dir"),
    }.items() if val not in (None, "", [], {})}

def _evidence_scope_digest(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Compact adjudicable Evidence with the scope fields synthesis must preserve."""
    manifest = context.get("evidence_manifest")
    digest: list[dict[str, Any]] = []
    for item in adjudicable_manifest_records(manifest):
        row = {key: value for key, value in {
            "evidence_id": item.get("evidence_id"),
            "relation": item.get("relation"),
            "source_access": item.get("source_access"),
            "provenance": item.get("provenance"),
            "summary": item.get("summary") or item.get("body"),
            "criterion_ids": item.get("criterion_ids"),
            "scope": item.get("scope"),
            "limitations": item.get("limitations"),
            "origin_cycle": item.get("origin_cycle"),
            "origin_cycles": item.get("origin_cycles"),
            "analysis_cycle": item.get("analysis_cycle"),
            "freshness": item.get("freshness"),
        }.items() if value not in (None, "", [], {})}
        if row:
            digest.append(row)
    return digest


def _adjudication_history_digest(value: Any) -> dict[str, Any]:
    """Project only relevant already-adjudicated predecessor results for Judge."""
    if not isinstance(value, dict):
        return {}
    cycles: list[dict[str, Any]] = []
    for raw_cycle in value.get("cycles") or []:
        if not isinstance(raw_cycle, dict):
            continue
        row = {key: val for key, val in {
            "decisive_test": raw_cycle.get("decisive_test"),
            "verdict": raw_cycle.get("verdict"),
            "established_result": raw_cycle.get("established_result"),
            "scope": raw_cycle.get("scope"),
            "limitations": raw_cycle.get("limitations"),
        }.items() if val not in (None, "", [], {})}
        if row:
            cycles.append(row)
    return {"cycles": cycles} if cycles else {}


def stage_context(role: str, stage: str, context: Mapping[str, Any], *, continuation: bool = False) -> dict[str, Any]:
    """Pure stage boundary: whitelist minimal in-memory inputs; never discover files or sources."""
    if role == "research_lead" and stage == "frontier_proposal":
        feedback = _qualification_feedback(context.get("qualification_feedback"), continuation=continuation)
        return {key: value for key, value in {
            "goal": context.get("goal"),
            "research_contract": context.get("research_contract"),
            "final_gate": context.get("final_gate"),
            "scientific_model_summary": _model_summary(context.get("scientific_model")),
            **_graph_access(context),
            "science_graph_tools": context.get("science_graph_tools"),
            "running_hypotheses": context.get("running_hypotheses"),
            "operator_guidance": context.get("operator_guidance"),
            "qualification_feedback": feedback,
            "lead_literature_result": _literature_digest(context.get("lead_literature_result")),
            "workspace_access": _workspace_access(context),
        }.items() if value not in (None, "", [], {})}
    if role == "research_lead" and stage == "hypothesis_display":
        lock = context.get("frozen_hypothesis") if isinstance(context.get("frozen_hypothesis"), dict) else {}
        return {"frozen_hypothesis": locked_science_view(lock)}
    if role == "research_lead" and stage == "selection_repair":
        error = context.get("qualification_error") if isinstance(context.get("qualification_error"), dict) else {}
        return {
            "repair_options": _repair_options(
                context.get("candidates"),
                context.get("candidate_reviews"),
                continuation=continuation,
                fallback_blockers=error.get("blocking_issues"),
            )
        }
    if role == "research_lead" and stage == "candidate_selection":
        # Qualification is already an SDK fact.  The continuing Lead thread
        # needs only stable candidate identities; a fresh recovery thread gets
        # the minimum candidate science in the same semantic field.
        return {
            "qualified_candidates": _qualified_candidate_options(
                context.get("candidates"),
                continuation=continuation,
            )
        }
    if role == "research_lead" and stage == "cycle_synthesis":
        return {
            "active_hypothesis": _cycle_synthesis_hypothesis_view(context.get("active_hypothesis")),
            "judge_decision": _cycle_synthesis_judge_view(context.get("judge_decision")),
            "scientific_access": _cycle_synthesis_access(context),
        }
    if role == "research_lead" and stage == "frontier_maintenance":
        return {key: value for key, value in {
            "goal": context.get("goal"),
            "research_contract": context.get("research_contract"),
            "final_gate": context.get("final_gate"),
            "scientific_model_summary": _model_summary(context.get("scientific_model")),
            "judge_decision": _cycle_synthesis_judge_view(context.get("judge_decision")),
            "scientific_synthesis": _scientific_synthesis_view(context.get("scientific_synthesis")),
            "current_hypothesis": _cycle_synthesis_hypothesis_view(context.get("active_hypothesis")),
            **_graph_access(context),
            "science_graph_tools": context.get("science_graph_tools"),
        }.items() if value not in (None, "", [], {})}
    if role == "challenger" and stage == "candidate_review":
        # Candidate lineage is supplied only from the canonical SQLite graph.
        graph_access = _graph_access(context)
        projected = {
            "goal": context.get("goal"),
            "research_contract": context.get("research_contract"),
            "final_gate": context.get("final_gate"),
            "candidate": [
                {**row, **({"id": str(source["id"])} if source.get("id") else {})}
                for row, source in zip(_candidate_digest(context.get("candidate")),
                                       context.get("candidate") or [])
                if isinstance(source, Mapping)
            ],
            **graph_access,
            "science_graph_tools": context.get("science_graph_tools"),
            "workspace_access": _workspace_access(context),
        }
        return {key: value for key, value in projected.items() if value not in (None, "", [], {})}
    if role == "challenger" and stage == "repair_verification":
        original = context.get("original_candidate") if isinstance(context.get("original_candidate"), Mapping) else {}
        repaired = context.get("repaired_candidate") if isinstance(context.get("repaired_candidate"), Mapping) else {}
        review = context.get("original_review") if isinstance(context.get("original_review"), Mapping) else {}
        graph_access = _graph_access(context)
        projected = {
            "goal": context.get("goal"),
            "research_contract": context.get("research_contract"),
            "final_gate": context.get("final_gate"),
            "original_candidate": _repair_candidate_view(original),
            "repaired_candidate": _repair_candidate_view(repaired),
            "original_blockers": list(review.get("blocking_issues") or []),
            **graph_access,
            "science_graph_tools": context.get("science_graph_tools"),
            "workspace_access": _workspace_access(context),
        }
        return {key: value for key, value in projected.items() if value not in (None, "", [], {})}
    if role == "investigator" and stage == "preserve_research":
        lock = context.get("hypothesis_lock") or context.get("branch")
        return {
            "frozen_objective": _investigator_objective_view(lock),
            "blocked_reason": str(context.get("blocked_reason") or "")[:4000],
            "source_cycle": str(context.get("source_cycle") or ""),
            "workspace_access": _workspace_access(context),
        }
    if role == "investigator":
        # Investigator A receives the frozen scientific objective, not the
        # lock's machine identity/criteria. Continuity is delta-only and only
        # replayed when the current thread could not already know the previous
        # executor's result (cross-executor or fresh-thread recovery).
        lock = context.get("hypothesis_lock") or context.get("branch")
        previous = (
            context.get("previous_scientific_delta")
            or context.get("previous_investigation_delta")
            or context.get("previous_executor_delta")
        )
        replay_previous = not (
            continuation and str(context.get("previous_executor") or "") == "investigator"
        )
        base = {
            "frozen_objective": _investigator_objective_view(lock),
            "qualification_handoff": _qualification_handoff_view(lock),
            "previous_scientific_delta": previous if replay_previous else None,
            "workspace_access": _workspace_access(context),
        }
        if stage == "judge_rework":
            base["judge_objections"] = context.get("judge_objections")
        return {key: value for key, value in base.items() if value not in (None, "", [], {})}
    if role == "literature":
        task = context.get("literature_task") if isinstance(context.get("literature_task"), dict) else {}
        return {key: value for key, value in {
            "search_target": _literature_search_target(task),
            "source_lock": (task.get("source_lock") if stage == "targeted_source" else None),
            "scholarly_retrieval_root": _scholarly_retrieval_root(context.get("sdk_scholarly_retrieval")),
        }.items() if value not in (None, "", [], {})}
    if role == "reporter":
        return {key: value for key, value in {
            "hypothesis": compact_hypothesis_view(context.get("branch")),
            "judge_decision": _judge_decision_digest(context.get("judge")),
            "scientific_synthesis": _scientific_synthesis_view(context.get("scientific_synthesis")),
            "scientific_model_summary": _model_summary(context.get("scientific_model")),
            "evidence_scope_digest": _evidence_scope_digest(context),
        }.items() if value not in (None, "", [], {})}
    if role == "judge" and stage == "goal_completion":
        plan_path = str(context.get("research_plan_path") or "").strip()
        return {key: value for key, value in {
            "research_plan_access": ({"path": plan_path} if plan_path else None),
            **_graph_access(context),
        }.items() if value not in (None, "", [], {})}
    if role == "judge":
        base = {
            "investigation_result": _judge_investigation_result_view(context.get("investigation_result")),
            "prior_objection": _prior_objection_view(context.get("prior_objection")),
        }
        lock = context.get("hypothesis_lock") or context.get("branch")
        if lock:
            base["hypothesis_lock"] = _judge_hypothesis_view(lock)
        prior = _adjudication_history_digest(context.get("prior_adjudicated_context"))
        if prior.get("cycles"):
            base["prior_adjudicated_context"] = prior
        return {key: value for key, value in base.items() if value not in (None, "", [], {})}
    # Unknown role/stage pairs must not bypass the stage boundary by
    # inheriting the whole workflow context.
    return {}


def build_initial_prompt(*, prefix: str, role: str, stage: str, contract_ref: str,
                         contract_schema: str, context: Mapping[str, Any], write_rule: str,
                         process_guard: str = "") -> str:
    task = ROLE_STAGE_LABELS.get(stage, stage.replace("_", " "))
    card = stage_definition_text(role, stage)
    return (
        f"{prefix} Start the assigned TheoryMaster {role} task.\n"
        f"Current task: {stage} — {task}.\n"
        f"{card}"
        f"Output contract: {contract_ref}. Return ONLY one JSON object matching this exact stage contract: {contract_schema}.\n"
        "Respect the frozen scientific state, evidence boundaries, and role authority. Do not add prose. "
        f"{' '.join(part for part in (write_rule, process_guard) if part)}\n"
        f"Context:\n{json.dumps(dict(context), ensure_ascii=False)}"
    )


def build_continuation_prompt(*, role: str, stage: str, contract_ref: str,
                              contract_schema: str, context: Mapping[str, Any]) -> str:
    task = ROLE_STAGE_LABELS.get(stage, stage.replace("_", " "))
    card = stage_definition_text(role, stage)
    return (
        f"Continue as TheoryMaster {role} in the existing thread.\n"
        f"Current task: {stage} — {task}.\n"
        f"{card}"
        "Use only the new inputs below plus context already retained in this thread. Do not reopen completed stages, "
        "change a frozen hypothesis, or repeat prior-cycle material.\n"
        f"Output contract: {contract_ref}. Return one JSON object matching this exact {stage} contract: {contract_schema}.\n"
        f"Context:\n{json.dumps(dict(context), ensure_ascii=False)}"
    )


def build_json_repair_prompt(stage: str, raw_output: str = "", parse_error: str = "") -> str:
    malformed = str(raw_output or "")
    error = str(parse_error or "")
    return (
        f"JSON parse repair for stage {stage}. Preserve the scientific content and frozen state. "
        "Correct syntax only; do not change scientific claims, verdicts, evidence boundaries, or restart the task.\n"
        f"Parser error: {error or 'invalid JSON object'}\n"
        "Malformed output to repair follows exactly. Use it as the source of truth and emit ONE complete JSON object only:\n"
        f"{malformed}\n"
        "Do not add prose, code fences, or a second object."
    )


def build_contract_repair_prompt(
    stage: str,
    errors: list[dict[str, Any]],
    contract_schema: str,
    context: Mapping[str, Any] | None = None,
    *,
    repair_attempt: int = 1,
    repair_limit: int = 1,
) -> str:
    context = context if isinstance(context, Mapping) else {}
    candidate_order: list[dict[str, Any]] = []
    if stage == "candidate_review":
        candidates = context.get("candidates", context.get("candidate"))
        if isinstance(candidates, list):
            candidate_order = [
                {"index": index, "candidate_id": str(item.get("id"))}
                for index, item in enumerate(candidates)
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            ]
    identity_guard = ""
    if candidate_order:
        identity_guard = (
            "Candidate identity is fixed by the SDK shortlist:\n"
            f"{json.dumps(candidate_order, ensure_ascii=False)}\n"
            "Return one review per SDK-provided candidate_id; copy each ID exactly, never invent one. "
            "Review array order may vary, but identities and rationales must remain associated.\n"
            "Do not substitute candidates from Frontier State, prior Cycles, thread history, or any other list.\n"
        )
    established_guards: list[str] = []
    locked_stages = {"hypothesis_display", "investigate", "integrate_literature", "judge_rework",
                     "terminal_assessment", "cycle_synthesis", "frontier_maintenance", "report", "preserve_research"}
    for key in ("hypothesis_lock", "frozen_hypothesis", "frozen_objective", "active_hypothesis") if stage in locked_stages else ():
        frozen = context.get(key)
        if isinstance(frozen, Mapping) and all(str(frozen.get(field) or "").strip()
                                               for field in ("hypothesis", "scope", "decisive_test")):
            established_guards.append("The supplied locked scientific object is established; preserve its hypothesis, scope and decisive_test.")
            break
    decision = context.get("judge_decision")
    if stage in {"cycle_synthesis", "frontier_maintenance", "report"} and isinstance(decision, Mapping) and decision.get("verdict"):
        established_guards.append("The supplied Judge decision is established source material; preserve it without emitting a new adjudication.")
    return (
        f"Contract validation failed for stage {stage}.\n"
        f"This is contract repair attempt {repair_attempt} of {repair_limit}.\n"
        "Preserve valid scientific content and decisions already established for this stage, within its current output authority. "
        "Do not invent a Judge verdict or a hypothesis lock, import stale decisions from earlier stages or Cycles, "
        "or turn a formatting correction into a new scientific decision. Preserve evidence boundaries. "
        "Correct only the contract violations below. Do not redo the scientific workflow. "
        "If a prior repair still failed, use the updated validation errors to make a concrete correction.\n"
        f"Validation errors:\n{json.dumps(errors, ensure_ascii=False, indent=2)}\n"
        + (" ".join(established_guards) + "\n" if established_guards else "")
        + f"{identity_guard}"
        f"Required stage contract: {contract_schema}\n"
        "Re-emit the complete stage JSON object only."
    )
