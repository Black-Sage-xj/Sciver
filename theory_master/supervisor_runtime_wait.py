"""Read-only proof of the stopped Cycle and its queued technical repair."""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from .public_submission import read_bounded_regular_file, strict_json
from .supervisor_recovery import SCHEMA, failure_details, incident_key, path_for, stop_reason


def runtime_wait_evidence(root: Path, cycle: Path, state: dict) -> dict | None:
    """Bind wait ownership without treating reboot as a repair action."""
    try:
        root, cycle = Path(root), Path(cycle)
        if (not root.is_absolute() or root.resolve() != root or cycle.parent != root
                or cycle.is_symlink() or not isinstance(state, dict)
                or str(state.get("cycle_id") or "") != cycle.name
                or state.get("execution_active") or state.get("worker_id") or stop_reason(state)):
            return None
        control = cycle / "recovery_control.json"
        recovery = state.get("recovery") or {}
        completion = state.get("goal_completion") or {}
        if (control.exists() or control.is_symlink() or not isinstance(recovery, dict)
                or recovery.get("action") in {"kill", "abandon_cycle"}
                or not isinstance(completion, dict) or completion.get("verdict") == "passed"):
            return None
        raw = read_bounded_regular_file(path_for(cycle), 4_000_000)
        job = strict_json(raw)
        key = incident_key(state)
        if (not isinstance(job, dict) or job.get("schema") != SCHEMA or job.get("cycle") != cycle.name
                or job.get("incident_key") != key or job.get("failure") != failure_details(state)
                or job.get("recovery_owner") != "sdk:runtime_supervisor" or job.get("status") != "waiting"
                or job.get("owner_pid", 0) is not None
                or job.get("current_action") in {"maintenance_stop", "respect_stop"}):
            return None
        deadline = job.get("next_wakeup_epoch")
        input_digest = job.get("input_digest")
        if (type(deadline) not in {int, float} or not math.isfinite(deadline) or deadline <= 0
                or type(job.get("dependency_wait", False)) is not bool
                or not isinstance(input_digest, str) or re.fullmatch(r"[a-f0-9]{64}", input_digest) is None):
            return None
        return {"kind": "runtime_recovery", "incident_key": key, "job_sha256": hashlib.sha256(raw).hexdigest()}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, OverflowError):
        return None
