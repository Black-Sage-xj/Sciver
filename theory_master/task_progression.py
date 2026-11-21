"""Local research route failures that may return to Lead within the Cycle budget.

This decides only whether a fresh proposal may be requested. It neither qualifies
a candidate nor upgrades a closed attempt to a scientific result.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .scientific_objective import complete_science, frozen_science_digest, frozen_science_view


def _known_direction(value: Any) -> bool:
    from .worker_direction import _direction_values
    return _direction_values(value) is not None


def qualification_replan_allowed(spec: Any, state: dict[str, Any], *,
                                 current_fingerprint: Any = None) -> bool:
    """Allow one fresh Lead after a closed failure at the same known direction.

    Archived attempts supply the counter, so a new process or scheduler tick
    cannot reset it. Explicit Run/retry can still start one new attempt; this
    function only controls the automatic continuation after that attempt.
    """
    from .specification import list_cycle_dirs
    from .worker_direction import direction_changed, research_direction_fingerprint
    if local_replan_reason(state) != "no_qualified_candidate":
        return False
    if (state.get("supervisor_qualification") or {}).get("status") in {"delivery_planned", "delivery_reviewed"}:
        return False
    offered = state.get("wake_fingerprint")
    if not _known_direction(offered):
        return False
    try:
        current = (research_direction_fingerprint(spec) if current_fingerprint is None
                   else current_fingerprint)
    except (OSError, ValueError):
        return False
    if not _known_direction(current):
        return False
    if direction_changed(offered, current):
        return True
    if (state.get("supervisor_qualification") or {}).get("status") in {"waiting", "review_exhausted"}:
        return False
    own = str(state.get("cycle_id") or "")
    if not own:
        return False
    root = Path(spec.research_root)
    seen_current = False
    for cycle in list_cycle_dirs(root, task_mode=getattr(spec, "task_mode", False))[:32]:
        if cycle.name == own:
            seen_current = True
            continue
        if not seen_current:
            continue
        path = cycle / "research_state.json"
        try:
            if cycle.is_symlink() or path.is_symlink() or path.stat().st_size > 2_000_000:
                return False
            with path.open("rb") as handle:
                raw = handle.read(2_000_001)
            if len(raw) > 2_000_000:
                return False
            previous = json.loads(raw)
        except (OSError, ValueError, UnicodeError):
            return False
        if not isinstance(previous, dict):
            return False
        if previous.get("worker_id") != state.get("worker_id"):
            continue
        if local_replan_reason(previous) != "no_qualified_candidate":
            lock = previous.get("hypothesis_lock")
            return ((isinstance(lock, dict) and lock.get("status") == "locked" and bool(lock.get("id")))
                    or previous.get("status") in {"over", "completed"}
                    and previous.get("scientific_verdict") in {"supported", "rejected", "open"})
        fingerprint = previous.get("wake_fingerprint")
        if not _known_direction(fingerprint):
            return False
        # A changed direction starts a new bounded episode. An equal historical
        # stop means this attempt already was the permitted fresh-Lead retry.
        return direction_changed(fingerprint, offered)
    return seen_current



def input_support_replan_allowed(spec: Any, state: dict[str, Any]) -> bool:
    """Try one alternate test before waiting on repeated missing input."""
    from .public_submission import read_bounded_regular_file
    from .specification import cycle_number, list_cycle_dirs
    from .supervisor_input_support import input_support_recovery
    from .worker_store import _live_cycle_claims

    if spec is None or local_replan_reason(state) != "input_support_unavailable":
        return False
    own = str(state.get("cycle_id") or "")
    if not own.isascii() or not own.isdecimal():
        return False
    root = Path(spec.research_root).resolve()
    worker_id = state.get("worker_id") or None
    available = input_support_recovery(root / own, state, spec=spec, worker_id=worker_id)
    if available is None:
        return False
    if available["route"] == "input_support":
        return True
    other_claims = {str(claim["cycle_id"]) for claim in _live_cycle_claims(root)
                    if claim.get("cycle_id") and claim.get("worker_id") != worker_id} if worker_id else set()
    saw_current = False
    for cycle in list_cycle_dirs(root, task_mode=spec.task_mode):
        if (cycle_number(cycle.name) or 0) > int(own):
            continue
        if cycle.name == own:
            saw_current = True
            continue
        if not saw_current or cycle.name in other_claims:
            continue
        try:
            prior = json.loads(read_bounded_regular_file(cycle / "research_state.json", 2_000_000))
        except (OSError, ValueError):
            return False
        if not isinstance(prior, dict):
            return False
        if prior.get("worker_id") != state.get("worker_id"):
            continue
        if (prior.get("status") in {"over", "completed"}
                and prior.get("scientific_verdict") in {"open", "supported", "rejected"}):
            break
        if local_replan_reason(prior) == "input_support_unavailable":
            prior_support = input_support_recovery(cycle, prior, spec=spec, worker_id=worker_id)
            return bool(prior_support and prior_support["route"] == "input_support")
    return saw_current

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
            and state.get("scientific_verdict") is None
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
            or not complete_science(science) or lock.get("status") != "locked"
            or not report.get("invocation_id")
            or delivered.get("invocation_id") != report["invocation_id"]
            or delivered.get("execution_limit") != report
            or not delivered.get("handoff_path")
            or report.get("frozen_objective_sha256") != frozen_science_digest(science)):
        return None
    return "input_support_unavailable"
