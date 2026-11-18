"""Bounded navigation to prior input limitations, separate from scientific Evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def recent_execution_limits(root: Path, *, current_cycle: str, limit: int = 6) -> list[dict[str, Any]]:
    """Expose recorded prerequisites to a new Lead without inventing an Outcome."""
    from .specification import list_cycle_dirs
    from .scientific_objective import frozen_science_digest, frozen_science_view

    records = []
    if limit <= 0:
        return records
    for cycle in list_cycle_dirs(root):
        if cycle.name == current_cycle or cycle.is_symlink():
            continue
        closure_path = cycle / "operational_closure.json"
        state_path = cycle / "research_state.json"
        try:
            if any(p.is_symlink() or not p.is_file() or p.stat().st_size > 1_048_576
                   for p in (closure_path, state_path)):
                continue
            closure = json.loads(closure_path.read_text(encoding="utf-8"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(closure, dict) or not isinstance(state, dict):
                continue
            report = closure.get("execution_limit")
            if (closure.get("kind") != "input_support_unavailable"
                    or not isinstance(report, dict)
                    or report.get("kind") != "input_support_unavailable"
                    or state.get("status") != "over"
                    or state.get("assessment_state") != "not_adjudicated"
                    or state.get("scientific_verdict") is not None
                    or state.get("operational_only_closure") != closure):
                continue
            lock = state.get("hypothesis_lock") or {}
            science = frozen_science_view(lock)
            if (len(science) != 3
                    or report.get("frozen_objective_sha256") != frozen_science_digest(science)):
                continue
            records.append({
                "cycle_id": cycle.name,
                "hypothesis_id": lock.get("id"),
                "scope_excerpt": str(science["scope"])[:2000],
                "frozen_objective_sha256": report["frozen_objective_sha256"],
                "reason": str(report.get("reason") or "")[:2000],
                "unexecuted_test": str(report.get("unexecuted_test") or "")[:2000],
                "required_dependency": str(report.get("required_dependency") or "")[:2000],
                "source_path": str(closure_path.resolve()),
                "assessment_state": "not_adjudicated",
                "evidence_status": "Investigator-authored execution limitation; no scientific verdict",
            })
            if len(records) >= limit:
                break
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            continue
    return records
