"""The exact same-boot continuation of an interrupted managed Goal review."""

from __future__ import annotations

import os
from pathlib import Path

from .supervisor_activation import same_delivery_attempt
from .supervisor_boot_recovery import DURABLE_RUNNER_PROTOCOL, _goal_reassessment_boot_evidence


SCHEMA = "theory-master/supervisor-reassessment-retry/v1"
_IDENTITY = {"launch_id", "stopped_cycle", "anchor_cycle", "reassessment_of", "delivery_revision"}
_TICKET_FIELDS = {
    "schema", "launch_id", "boot_id", "attempt", "runner_pid", "runner_start_ticks",
    "stopped_state_sha256", "recovery_pin", "status",
}


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _attempt_identity(attempt: object) -> bool:
    return (isinstance(attempt, dict) and attempt.keys() == _IDENTITY | {"status"}
            and attempt["status"] == "launching"
            and all(isinstance(attempt[key], str) and attempt[key] for key in _IDENTITY))


def _bound_attempt(root: Path, registry: dict, launch_id: str, supervisor_pid: int) -> dict | None:
    from .runtime_manager import host_boot_id, process_identity_alive

    boot_id = host_boot_id()
    attempt = registry.get("delivery_reassessment_attempt")
    ticks = registry.get("supervisor_start_ticks")
    master = registry.get("master_id")
    if (not boot_id or registry.get("boot_id") != boot_id or registry.get("launch_id") != launch_id
            or registry.get("research_root") != str(root) or type(master) is not int or master < 0
            or registry.get("supervisor_pid") != supervisor_pid or not _positive_int(ticks)
            or not process_identity_alive(supervisor_pid, ticks)
            or registry.get("state") not in {"STARTING", "RUNNING", "RECOVERING"}
            or registry.get("requested_operation") != "takeover" or registry.get("boot_resume") is not None
            or registry.get("runner_operation") != "reassess"
            or registry.get("runner_checkpoint_protocol") != DURABLE_RUNNER_PROTOCOL
            or not _attempt_identity(attempt) or attempt["launch_id"] != launch_id
            or not (attempt["stopped_cycle"] == registry.get("current_cycle") == registry.get("takeover_cycle"))):
        return None
    history = registry.get("delivery_reassessment_history", [])
    if not isinstance(history, list) or any(same_delivery_attempt(attempt, row) for row in history):
        return None
    return attempt


def _pinned_evidence(root: Path, registry: dict, attempt: dict) -> dict | None:
    evidence = _goal_reassessment_boot_evidence(root, registry["master_id"], attempt)
    pin = evidence.get("recovery_pin") if evidence else None
    if pin is None or pin.get("mode") not in {"first_dispatch", "reuse_closure"}:
        return None
    return evidence


def goal_retry_plan(root: Path, launch_id: str) -> dict | None:
    """Propose one replacement for an exact, known-dead managed Runner."""
    from .runtime_manager import load_registry, process_identity_alive

    try:
        root = Path(root).resolve()
        registry = load_registry(root)
        attempt = _bound_attempt(root, registry, launch_id, os.getpid())
        runner_pid, runner_ticks = (registry.get(key) for key in ("runner_pid", "runner_start_ticks"))
        if (attempt is None or not _positive_int(runner_pid) or not _positive_int(runner_ticks)
                or process_identity_alive(runner_pid, runner_ticks)):
            return None
        prior = registry.get("goal_reassessment_retry")
        if prior is not None and (not isinstance(prior, dict) or prior.get("schema") != SCHEMA
                                  or not _attempt_identity(prior.get("attempt"))
                                  or same_delivery_attempt(attempt, prior["attempt"])):
            return None
        evidence = _pinned_evidence(root, registry, attempt)
        if evidence is None:
            return None
        return {"schema": SCHEMA, "launch_id": launch_id, "boot_id": registry["boot_id"],
                "attempt": attempt, "runner_pid": runner_pid, "runner_start_ticks": runner_ticks,
                **evidence, "status": "ready"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return None


def verify_goal_retry(root: Path, launch_id: str, *, managed_child: bool = False) -> dict | None:
    """Verify the durable retry at the Supervisor or managed child boundary."""
    from .runtime_manager import load_registry, process_identity_alive

    try:
        root = Path(root).resolve()
        registry = load_registry(root)
        supervisor_pid = os.getppid() if managed_child else os.getpid()
        attempt = _bound_attempt(root, registry, launch_id, supervisor_pid)
        ticket = registry.get("goal_reassessment_retry")
        if (attempt is None or not isinstance(ticket, dict) or ticket.keys() != _TICKET_FIELDS
                or ticket["schema"] != SCHEMA or ticket["launch_id"] != launch_id
                or ticket["boot_id"] != registry["boot_id"] or ticket["attempt"] != attempt
                or ticket["status"] != ("dispatching" if managed_child else "ready")):
            return None
        previous = (ticket["runner_pid"], ticket["runner_start_ticks"])
        if not all(_positive_int(value) for value in previous) or process_identity_alive(*previous):
            return None
        current = (registry.get("runner_pid"), registry.get("runner_start_ticks"))
        if current not in (previous, (None, None)):
            pid, ticks = current
            if (not managed_child or pid != os.getpid() or not _positive_int(ticks)
                    or not process_identity_alive(pid, ticks)):
                return None
        evidence = _pinned_evidence(root, registry, attempt)
        if evidence is None or any(ticket[key] != value for key, value in evidence.items()):
            return None
        return ticket
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return None
