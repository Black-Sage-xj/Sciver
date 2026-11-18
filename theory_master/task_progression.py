"""Local research route failures that may return to Lead within the Cycle budget.

This decides only whether a fresh proposal may be requested. It neither qualifies
a candidate nor upgrades a closed attempt to a scientific result.
"""
from __future__ import annotations

from typing import Any

from .scientific_objective import frozen_science_digest, frozen_science_view


def local_replan_reason(state: dict[str, Any]) -> str | None:
    """Recognize completed local failures while preserving independent stop guards."""
    if not isinstance(state, dict):
        return None
    for field in ("goal_completion", "recovery", "attention", "hypothesis_lock",
                  "investigator_budget", "cycle_budget", "frontier_review",
                  "operational_only_closure", "execution_limit_handoff"):
        if state.get(field) is not None and not isinstance(state[field], dict):
            return None
    if ((state.get("goal_completion") or {}).get("verdict") == "passed"
            or state.get("abandoned_cycle") or state.get("phase") == "abandon_requested"
            or state.get("execution_active") or state.get("protocol_health") != "valid"
            or (state.get("recovery") or {}).get("action") in {
                "pause", "interrupt", "kill", "abandon_cycle", "error", "contract_repair_failed"}):
        return None
    for field in ("investigator_budget", "cycle_budget"):
        budget = state.get(field) or {}
        if isinstance(budget, dict) and (budget.get("exhausted") or budget.get("rejected")):
            return None
    status = str(state.get("status") or "").lower()
    attention = state.get("attention") or {}
    lock = state.get("hypothesis_lock") or {}
    if (status == "no_qualified_candidate"
            and state.get("phase") == "qualification"
            and state.get("qualification_status") == "no_qualified_candidate"
            and attention.get("code") == "no_qualified_candidate"
            and lock.get("status") == "unlocked"
            and (state.get("frontier_review") or {}).get("status") == "unqualified"
            and state.get("qualification_attempts")):
        return "no_qualified_candidate"
    closure = state.get("operational_only_closure") or {}
    if (status not in {"over", "completed"}
            or state.get("assessment_state") != "not_adjudicated"
            or state.get("scientific_verdict") is not None
            or closure.get("kind") != "input_support_unavailable"):
        return None
    report = closure.get("execution_limit")
    delivered = state.get("execution_limit_handoff") or {}
    science = frozen_science_view(lock)
    if (not isinstance(report, dict) or report.get("kind") != "input_support_unavailable"
            or len(science) != 3 or lock.get("status") != "locked"
            or not report.get("invocation_id")
            or delivered.get("invocation_id") != report["invocation_id"]
            or delivered.get("execution_limit") != report
            or not delivered.get("handoff_path")
            or report.get("frozen_objective_sha256") != frozen_science_digest(science)):
        return None
    return "input_support_unavailable"
