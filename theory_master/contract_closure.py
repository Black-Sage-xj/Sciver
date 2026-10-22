"""SDK-owned semantic closure and deterministic contract normalization.

Agent-facing contracts intentionally contain only scientific/semantic judgments.
This module expands those minimal outputs into the stable internal shapes consumed
by the existing Registry, Router, Judge rework and Canvas transition code.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .contract_stages import LITERATURE_GAP_KIND_TO_TASK
from .cycle_criteria import CURRENT_CYCLE_CRITERION_ID


_LITERATURE_GAP_KINDS = {
    "literature_landscape",
    "literature_targeted",
    "literature_novelty",
}
_GAP_TO_EXECUTOR = {
    "theoretical": "investigator",
    "numerical": "investigator",
    "local_source_review": "investigator",
    "literature_landscape": "literature",
    "literature_targeted": "literature",
    "literature_novelty": "literature",
}

def _stable_id(prefix: str, *parts: Any, ordinal: int | None = None) -> str:
    material = "\x1f".join(str(part or "").strip() for part in parts)
    if ordinal is not None:
        material += f"\x1f{ordinal}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _source_gap_id(search: Mapping[str, Any]) -> str:
    return _stable_id(
        "external-gap",
        search.get("search_target"),
        search.get("why_external_needed"),
        search.get("expected_external_evidence"),
    )


def _close_external_search(kind: str, value: Any) -> dict[str, Any] | None:
    if kind not in _LITERATURE_GAP_KINDS:
        return None
    source = value if isinstance(value, dict) else {}
    task_type = LITERATURE_GAP_KIND_TO_TASK[kind]
    closed = {
        "source_gap_id": _source_gap_id(source),
        "task_type": task_type,
        "search_target": str(source.get("search_target") or "").strip(),
        "why_external_needed": str(source.get("why_external_needed") or "").strip(),
        "expected_external_evidence": str(source.get("expected_external_evidence") or "").strip(),
    }
    lead = str(source.get("new_search_lead") or "").strip()
    if lead:
        closed["new_search_lead"] = lead
    return closed


def _close_lead_literature_request(output: Mapping[str, Any]) -> dict[str, Any] | None:
    if output.get("needs_literature") is not True:
        return None
    request = output.get("literature_request")
    if not isinstance(request, Mapping):
        return None
    return {
        "needs_literature": True,
        "literature_request": {
            key: str(request.get(key) or "").strip()
            for key in (
                "task_type", "search_target", "why_external_needed",
                "expected_external_evidence", "new_search_lead",
            )
            if request.get(key) not in (None, "")
        },
    }


def _close_frontier(output: Mapping[str, Any]) -> dict[str, Any]:
    literature = _close_lead_literature_request(output)
    if literature is not None:
        return literature
    candidates: list[dict[str, Any]] = []
    for index, raw in enumerate(output.get("candidates") or [], 1):
        item = dict(raw)
        hypothesis = str(item.get("hypothesis") or "").strip()
        item["id"] = _stable_id("cand", hypothesis, item.get("decisive_test"), ordinal=index)
        candidates.append(item)
    return {"candidates": candidates}


def _close_candidate_selection(output: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "selected_candidate_id": str(output.get("selected_candidate_id") or "").strip(),
        "decision": str(output.get("selection_reason") or "").strip(),
    }


def _close_selection_repair(output: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    qerr = context.get("qualification_error") if isinstance(context.get("qualification_error"), dict) else {}
    candidates = context.get("candidates") if isinstance(context.get("candidates"), list) else []
    selected_parent = str(output.get("selected_parent_candidate_id") or qerr.get("candidate_id") or "")
    if not selected_parent and len(candidates) == 1 and isinstance(candidates[0], Mapping):
        selected_parent = str(candidates[0].get("id") or "")
    parent = next(
        (item for item in candidates if isinstance(item, Mapping) and str(item.get("id") or "") == selected_parent),
        None,
    )
    if parent is None:
        raise ValueError("selection repair requires one supplied frozen parent candidate")
    reviews = context.get("candidate_reviews") if isinstance(context.get("candidate_reviews"), list) else []
    selected_review = next(
        (item for item in reviews if isinstance(item, dict) and str(item.get("candidate_id") or "") == selected_parent),
        {},
    )
    blockers = selected_review.get("blocking_issues") or qerr.get("blocking_issues") or []
    addressed = [
        str(item.get("id"))
        for item in blockers
        if isinstance(item, dict) and item.get("id")
    ]
    outcome = str(output.get("outcome") or "").strip()
    if outcome == "cannot_repair":
        return {
            "outcome": "cannot_repair",
            "selected_parent_candidate_id": selected_parent,
            "reason": str(output.get("cannot_repair_reason") or "").strip(),
        }
    repair = output.get("repair") if isinstance(output.get("repair"), Mapping) else {}
    decisive_test = str(repair.get("repaired_decisive_test") or "").strip()
    if not decisive_test or " ".join(decisive_test.split()) == " ".join(str(parent.get("decisive_test") or "").split()):
        raise ValueError("resolved selection repair requires a revised frozen decisive_test")
    # Scientific clarifications must travel with the single frozen test, not
    # only in provenance that downstream science projections intentionally omit.
    for key, label in (
        ("hypothesis_clarification", "Binding hypothesis clarification"),
        ("estimand_or_operational_definition", "Binding operational definition"),
    ):
        clarification = str(repair.get(key) or "").strip()
        if clarification and " ".join(clarification.split()) not in " ".join(decisive_test.split()):
            decisive_test += f"\n\n{label}:\n{clarification}"
    item = dict(parent)
    # Legacy Candidate identity pointers are readable during recovery, but they
    # must never be propagated into a newly written replacement Candidate.
    item.pop("hypothesis_id", None)
    item.pop("parent_hypothesis_ids", None)
    item["id"] = _stable_id(
        "cand",
        selected_parent,
        decisive_test,
        qerr.get("repair_attempt") or qerr.get("repair_count") or context.get("repair_budget"),
        ordinal=1,
    )
    # The scientific target is SDK-owned during repair.  Only the decisive-test
    # specification and bounded clarification metadata may change.
    item["hypothesis"] = str(parent.get("hypothesis") or "").strip()
    item["goal_advance"] = str(parent.get("goal_advance") or "").strip()
    item["scope"] = str(parent.get("scope") or "")
    item["title"] = str(parent.get("title") or item["hypothesis"]).strip()
    item["decisive_test"] = decisive_test
    item["parent_candidate_id"] = selected_parent
    item["evolution_operator"] = str(parent.get("evolution_operator") or "")
    closures = repair.get("blocker_closures") if isinstance(repair.get("blocker_closures"), list) else []
    item["addressed_issue_ids"] = [
        str(row.get("blocker_id") or "") for row in closures if isinstance(row, Mapping)
    ] or addressed
    item["repair_details"] = {
        key: str(repair.get(key) or "").strip()
        for key in (
            "hypothesis_clarification",
            "estimand_or_operational_definition",
        )
        if repair.get(key) not in (None, "")
    }
    item["blocker_closures"] = [
        {"blocker_id": str(row.get("blocker_id") or ""),
         "resolution": str(row.get("resolution") or "")}
        for row in closures if isinstance(row, Mapping)
    ]
    result = {"outcome": "resolved", "replacement_candidates": [item], "selected_parent_candidate_id": selected_parent}
    return result


def _close_cycle_synthesis(output: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "mode": "cycle_end",
        "scientific_synthesis": output.get("scientific_synthesis") or {},
        "scientific_model_update": output.get("scientific_model_update") or {},
        **({"pending_dispositions": list(output["pending_dispositions"])}
           if isinstance(output.get("pending_dispositions"), list) else {}),
    }


def _close_challenger(output: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    candidates = context.get("candidates") if isinstance(context.get("candidates"), list) else []
    reviews = output.get("reviews") if isinstance(output.get("reviews"), list) else []
    by_id = {str(raw.get("candidate_id") or ""): dict(raw) for raw in reviews}
    # Validation rejects missing, duplicate and unknown IDs. Only reorder after
    # matching explicit SDK-supplied identities; never zip implicit positions.
    if (len(by_id) != len(reviews) or len(reviews) != len(candidates)
            or set(by_id) != {str(c.get("id") or "") for c in candidates}):
        raise ValueError("challenger closure requires a one-to-one candidate_id match")
    return {"reviews": [by_id[str(c["id"])] for c in candidates]}


def _close_remaining_gap(output: Mapping[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    gap = output.get("remaining_gap") if isinstance(output.get("remaining_gap"), dict) else {}
    if str(gap.get("resolvability") or "actionable_now") == "structurally_unidentifiable":
        return None, None
    kind = str(gap.get("kind") or "none").strip()
    requested = _GAP_TO_EXECUTOR.get(kind)
    if requested != "literature":
        return requested, None
    external = {
        "search_target": str(gap.get("search_target") or gap.get("description") or "").strip(),
        "why_external_needed": str(gap.get("why_external_needed") or gap.get("description") or "").strip(),
        "expected_external_evidence": str(gap.get("expected_external_evidence") or "").strip(),
        "new_search_lead": str(gap.get("new_search_lead") or "").strip(),
    }
    return "literature", {
        "gap_id": _source_gap_id(external),
        "task_type": LITERATURE_GAP_KIND_TO_TASK[kind],
        **external,
    }


def _close_specialist(output: Mapping[str, Any]) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    rows = output.get("findings") if "findings" in output else output.get("evidence")
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        # Preserve Investigator statements verbatim; no science is inferred by SDK.
        if "statement" in raw:
            item = {"summary": raw["statement"]}
            for key in ("method", "result", "provenance"):
                if key in raw:
                    item[key] = raw[key]
        else:
            item = dict(raw)  # historical internal evidence closure
        if "artifacts" in item and "artifact_pointers" not in item:
            item["artifact_pointers"] = item.pop("artifacts")
        item.pop("evidence_id", None)
        item.pop("issue_ids", None)
        evidence.append(item)
    # Current Investigator never decides its own workflow continuation.
    return {"evidence": evidence}


def _load_transport_papers(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    transport = context.get("sdk_scholarly_retrieval")
    if not isinstance(transport, dict):
        return []
    path_text = str(transport.get("papers_json") or "").strip()
    if not path_text:
        return []
    path = Path(path_text)
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _paper_for_ref(source_ref: Any, papers: list[dict[str, Any]]) -> dict[str, Any]:
    ref = str(source_ref or "").strip()
    if ref.startswith("paper:"):
        try:
            index = int(ref.split(":", 1)[1])
        except ValueError:
            index = -1
        if 0 <= index < len(papers):
            return papers[index]
    lowered = ref.lower()
    for row in papers:
        keys = (
            row.get("doi"),
            row.get("url"),
            row.get("title"),
            row.get("id"),
        )
        if lowered and any(str(value or "").strip().lower() == lowered for value in keys):
            return row
    return {}


def _source_metadata(source_ref: Any, papers: list[dict[str, Any]]) -> dict[str, Any]:
    ref = str(source_ref or "").strip()
    paper = _paper_for_ref(ref, papers)
    authors = paper.get("authors") or paper.get("author") or []
    if isinstance(authors, str):
        authors = [authors]
    fallback_locator = ref if ("://" in ref or re.match(r"^10\.\d{4,9}/", ref)) else ""
    return {
        "paper_title": str(paper.get("title") or ref).strip(),
        "authors": authors if isinstance(authors, list) else [],
        "year": paper.get("year") or paper.get("publication_year"),
        "doi_or_url": str(paper.get("doi") or paper.get("url") or fallback_locator).strip(),
        "local_original_path": str(
            paper.get("local_original_path") or paper.get("pdf_path") or paper.get("local_path") or ""
        ).strip(),
    }


def _enrich_source_rows(values: Any, papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in values if isinstance(values, list) else []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        metadata = _source_metadata(item.get("source_ref"), papers)
        item.update({key: value for key, value in metadata.items() if value not in (None, "", [], {})})
        rows.append(item)
    return rows


def _close_literature(output: Mapping[str, Any], context: Mapping[str, Any], stage: str) -> dict[str, Any]:
    papers = _load_transport_papers(context)
    task = context.get("literature_task") if isinstance(context.get("literature_task"), Mapping) else {}
    objection_id = str(task.get("objection_id") or "").strip()
    records: list[dict[str, Any]] = []
    for raw in output.get("evidence_records") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        metadata = _source_metadata(item.pop("source_ref", None), papers)
        item = {**metadata, **item}
        item.pop("evidence_id", None)
        if objection_id:
            item["objection_ids"] = list(dict.fromkeys([*(item.get("objection_ids") or []), objection_id]))
        records.append(item)
    remaining_question = str(output.get("remaining_scientific_question") or "").strip()
    closed: dict[str, Any] = {
        "evidence_records": records,
        "requested_next_executor": "investigator" if (remaining_question and records) else None,
        "literature_request": None,
        "remaining_scientific_question": remaining_question,
    }
    if stage == "landscape":
        landscape = dict(output.get("landscape") or {})
        landscape["representative_papers"] = _enrich_source_rows(landscape.get("representative_papers"), papers)
        for route in landscape.get("major_routes") or []:
            if isinstance(route, dict):
                route["representative_papers"] = _enrich_source_rows(route.get("representative_papers"), papers)
        closed["landscape"] = landscape
    if stage == "novelty_check":
        positioning = dict(output.get("positioning") or {})
        positioning["closest_prior_work"] = _enrich_source_rows(positioning.get("closest_prior_work"), papers)
        closed["positioning"] = positioning
    return closed


def _close_judge(output: Mapping[str, Any]) -> dict[str, Any]:
    """Expand Judge v7's minimal scientific decision into SDK-owned routing state."""
    verdict = output.get("verdict")
    if verdict == "null":
        verdict = None
    rationale = str(output.get("rationale") or "").strip()
    raw_objection = output.get("objection")
    objection: dict[str, Any] | None = None
    target: str | None = None
    if verdict is None and isinstance(raw_objection, Mapping):
        requested = str(raw_objection.get("requested_resolution") or "").strip()
        external = raw_objection.get("needs_external_scholarship")
        target = "literature" if external is True else "investigator" if external is False else None
        objection = {
            "objection_id": "J-0001",
            "criterion_id": CURRENT_CYCLE_CRITERION_ID,
            "reason": rationale,
            "requested_resolution": requested,
        }
        if target:
            objection["executor_role"] = target
        if target == "literature":
            objection["source_gap_id"] = _source_gap_id({
                "search_target": requested,
                "why_external_needed": rationale,
                "expected_external_evidence": requested,
            })

    rework_requested = verdict is None
    closed: dict[str, Any] = {
        "assessment_state": "rework_requested" if rework_requested else "terminal",
        "verdict": verdict,
        "missing_evidence": str((objection or {}).get("reason") or ""),
        "objections": [objection] if objection else [],
        "rationale": output.get("rationale"),
        "rework_plan": ({
            "target_executor": target,
            "expected_output": str((objection or {}).get("requested_resolution") or "one new discriminating result"),
        } if rework_requested and objection and target else None),
    }
    return closed


def close_agent_output(
    role: str,
    stage: str,
    output: Mapping[str, Any],
    context: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Expand one validated minimal Agent output into the internal transition shape."""
    # Direct SDK callers (including migration utilities) may not run through
    # InvocationMixin.  Preserve a deterministic offline closure path for them;
    # production workflow invocations inject their configured Jev/Agent-B backend
    # before reaching this function.
    if role == "research_lead":
        if stage == "frontier_proposal":
            return _close_frontier(output)
        if stage == "candidate_selection":
            return _close_candidate_selection(output)
        if stage == "selection_repair":
            return _close_selection_repair(output, context)
        if stage == "cycle_synthesis":
            return _close_cycle_synthesis(output, context)
        return dict(output)
    if role == "challenger":
        if stage == "repair_verification":
            return {key: str(output[key]).strip() for key in ("candidate_id", "verdict", "reason")}
        return _close_challenger(output, context)
    if role == "investigator":
        return _close_specialist(output)
    if role == "literature" and stage in {"landscape", "targeted_source", "novelty_check"}:
        return _close_literature(output, context, stage)
    if role == "judge":
        return dict(output) if stage == "goal_completion" else _close_judge(output)
    return dict(output)


def bind_registered_evidence(output: dict[str, Any], refs: list[str]) -> dict[str, Any]:
    """Attach SDK-generated Evidence IDs to the closed internal output only."""
    rows: list[dict[str, Any]] = []
    for field in ("evidence", "evidence_records"):
        values = output.get(field)
        if isinstance(values, list):
            rows.extend(item for item in values if isinstance(item, dict))
    for item, evidence_id in zip(rows, refs):
        item["evidence_id"] = evidence_id

    by_criterion: dict[str, list[str]] = {}
    for item in rows:
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id:
            continue
        for criterion_id in item.get("criterion_ids") or []:
            by_criterion.setdefault(str(criterion_id), []).append(evidence_id)
    for criterion_id, update in (output.get("criterion_updates") or {}).items():
        if isinstance(update, dict):
            update["evidence_refs"] = list(dict.fromkeys(by_criterion.get(str(criterion_id), [])))

    all_refs = [str(value) for value in refs if str(value).strip()]
    for update in (output.get("objection_updates") or {}).values():
        if isinstance(update, dict) and update.get("status") in {"resolved", "partially_resolved"}:
            update["evidence_refs"] = all_refs
    return output
