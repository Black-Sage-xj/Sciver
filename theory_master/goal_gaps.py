"""Minimal unmet requirements, without granting Goal Judge planning authority."""
from __future__ import annotations

from typing import Any

GAP_SCHEMA = {
    "kind": "scientific|delivery|unavailable_material",
    "requirement": "exact unmet requirement of the current Goal Contract",
    "basis": "existing adjudication/delivery/input references establishing this gap; no proposed next H",
}


def validate_gaps(gaps: Any, verdict: str) -> None:
    if not isinstance(gaps, list) or (verdict == "not_passed" and not gaps):
        raise ValueError("not_passed requires nonempty gaps; passed permits only []")
    if verdict == "passed" and gaps:
        raise ValueError("passed cannot carry unresolved gaps")
    for row in gaps:
        if not isinstance(row, dict) or set(row) != set(GAP_SCHEMA):
            raise ValueError("Goal gap requires exactly kind/requirement/basis")
        if row["kind"] not in {"scientific", "delivery", "unavailable_material"}:
            raise ValueError("invalid Goal gap kind")
        if any(not isinstance(row[field], str) or not row[field].strip() for field in ("requirement", "basis")):
            raise ValueError("Goal gap requirement and basis must be nonempty")


def stop_without_candidate(session: Any) -> None:
    """An explicit Lead exit avoids inventing a scientific H for a delivery/input gap."""
    disposition = session.frontier_output.get("no_new_science")
    if disposition is None:
        return
    validate_gaps([disposition], "not_passed")
    if disposition["kind"] == "scientific":
        raise ValueError("a scientific gap cannot use no_new_science")
    from .workflow_support import RecoveryStop
    session.state.setdefault("role_outputs", {})["research_lead_frontier"] = session.frontier_output
    session.state.pop("qualification_working_set", None)

    session.state.update(
        status="needs_attention", phase="qualification", scientific_phase="qualification",
        qualification_status="no_new_science", hypothesis_locked=False,
        hypothesis_lock={"status": "unlocked"}, execution_active=False, resumable=False,
        attention={"source": "research_lead", "code": "no_new_science", "reason": disposition["requirement"],
                           **disposition, "requested_action": (
                               "Use delivery repair on an existing terminal Cycle" if disposition["kind"] == "delivery"
                               else "Provide or authorize the indispensable missing material")},
    )
    session.owner._emit(session.state, "qualification.no_new_science", disposition)
    session.owner._persist(session.state)
    raise RecoveryStop("Lead found no necessary new science: " + disposition["requirement"])
