"""Preserve an already-authorized technical repair until the Runner claims it."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .invocations import stable_hash
from .public_submission import read_bounded_regular_file, strict_json
from .supervisor_recovery import SCHEMA as JOB_SCHEMA, failure_details, incident_key, inputs, path_for, stop_reason

AUTHORIZATION = "supervisor_runtime_resume_authorization"
_SCHEMA = "theory-master/runtime-repair-resume/v1"
_OWNER_FIELDS = ("boot_id", "launch_id", "research_root", "master_id", "supervisor_pid", "supervisor_start_ticks")
_BINDING_FIELDS = ("boot_id", "launch_id", "research_root", "master_id")


def launch_options(*, model=None, provider=None, routes=None, cycle_audit=None, error_repair=None,
                   attention_takeover=None, check_literature=False) -> dict:
    """Keep explicitly selected options when the same authorization is dispatched."""
    options = {key: value for key, value in (("model", model), ("provider", provider), ("routes", routes)) if value}
    options.update({key: value for key, value in (("cycle_audit", cycle_audit), ("error_repair", error_repair),
                                                ("attention_takeover", attention_takeover)) if value is not None})
    if check_literature:
        options["check_literature"] = True
    return options


def dispatch_owner(root: Path, registry: dict) -> dict | None:
    """Recognize the single-owner runtime before it runs the repair review."""
    from .runtime_manager import host_boot_id, process_start_ticks, unit_name

    master = registry.get("master_id")
    if (registry.get("launcher") != "systemd-user" or registry.get("research_root") != str(root)
            or type(master) is not int or master < 0 or registry.get("systemd_unit") != unit_name(root, master)
            or not registry.get("launch_id") or not registry.get("boot_id") or registry["boot_id"] != host_boot_id()
            or registry.get("supervisor_pid") != os.getpid() or not registry.get("supervisor_start_ticks")
            or registry["supervisor_start_ticks"] != process_start_ticks(os.getpid())
            or not _single_owner(root, registry)):
        return None
    return {**{key: registry[key] for key in _OWNER_FIELDS}, "options": registry.get("runtime_repair_options", {})}


def _single_owner(root: Path, registry: dict) -> bool:
    from .supervisor_activation import _qualification_wait_control
    from .worker_mode import read_mode
    from .worker_store import _live_cycle_claims

    for key in ("retained_quota_attempt", "delivery_reassessment_attempt", "input_support_attempt"):
        attempt = registry.get(key)
        if (isinstance(attempt, dict) and attempt.get("launch_id") == registry.get("launch_id")
                and (key == "retained_quota_attempt" or attempt.get("status") == "launching")):
            return False
    control = _qualification_wait_control(root)
    if control.exists() or control.is_symlink():
        request = strict_json(read_bounded_regular_file(control, 64_000))
        if not isinstance(request, dict) or (request.get("launch_id") == registry.get("launch_id")
                                             and request.get("action") == "run"):
            return False
    return not read_mode(root)["enabled"] and not _live_cycle_claims(root)


def authorize_dispatch(cycle: Path, state: dict, owner: dict, *, repair_credit: str | None) -> dict | None:
    """Build the grant for the same state write that books the resume credit."""
    if not _operational_stop(cycle, state):
        return None
    job, _ = _read_job(cycle)
    if (not _matches_job(cycle, state, job) or job.get("status") != "working" or not job.get("action_id")
            or job.get("owner_pid") != owner["supervisor_pid"]
            or job.get("owner_start_ticks") != owner["supervisor_start_ticks"]):
        return None
    return {"schema": _SCHEMA, **owner, "cycle": cycle.name, "incident_key": job["incident_key"],
            "action_id": job["action_id"], "session_id": state["supervisor_runtime_review"]["session_id"],
            "state_binding": _state_binding(state), "inputs": inputs(cycle.parent, state),
            "repair_credit": repair_credit}


def _state_binding(state: dict) -> str:
    return stable_hash({key: value for key, value in state.items() if key != AUTHORIZATION})


def _operational_stop(cycle: Path, state: dict) -> bool:
    control = cycle / "recovery_control.json"
    return (str(state.get("cycle_id")) == cycle.name and not state.get("worker_id") and not state.get("execution_active")
            and not stop_reason(state) and (state.get("recovery") or {}).get("action") not in {"kill", "abandon_cycle"}
            and not control.exists() and not control.is_symlink())


def _read_job(cycle: Path) -> tuple[dict, str]:
    raw = read_bounded_regular_file(path_for(cycle), 4_000_000)
    job = strict_json(raw)
    if not isinstance(job, dict) or job.get("schema") != JOB_SCHEMA:
        raise ValueError("Invalid technical repair job")
    return job, hashlib.sha256(raw).hexdigest()


def _matches_job(cycle: Path, state: dict, job: dict) -> bool:
    return (job.get("cycle") == cycle.name and job.get("recovery_owner") == "sdk:runtime_supervisor"
            and job.get("incident_key") == incident_key(state) and job.get("failure") == failure_details(state))


def _origin(root: Path, cycle_name: str) -> dict | None:
    from .goal_terminal import recovery_goal_blocker
    from .runtime_manager import latest_cycle_name
    from .supervisor_intervention import supervisor_enabled
    from .supervisor_repair_receipts import inspect_repair_receipt, repair_credit_key

    try:
        if (not isinstance(cycle_name, str) or not cycle_name.isascii() or not cycle_name.isdecimal()
                or cycle_name != latest_cycle_name(root)):
            return None
        cycle = root / cycle_name
        raw = read_bounded_regular_file(cycle / "research_state.json", 32 * 1024 * 1024)
        state = strict_json(raw)
        if not isinstance(state, dict):
            return None
        auth = state.get(AUTHORIZATION)
        review = state.get("supervisor_runtime_review") or {}
        if (not isinstance(auth, dict) or auth.get("schema") != _SCHEMA or "claimed_by" in auth
                or auth.get("research_root") != str(root) or auth.get("cycle") != cycle_name
                or not isinstance(review, dict) or review.get("action") != "resume"
                or not isinstance(auth.get("session_id"), str) or not auth["session_id"].startswith("SUP-")
                or auth["session_id"] != review.get("session_id") or not _operational_stop(cycle, state)
                or not supervisor_enabled(root) or recovery_goal_blocker(root)
                or auth.get("state_binding") != _state_binding(state)):
            return None
        job, job_sha = _read_job(cycle)
        if (not _matches_job(cycle, state, job) or auth["incident_key"] != job["incident_key"]
                or not auth.get("action_id") or auth["action_id"] != job.get("action_id")):
            return None
        if job.get("status") == "working":
            if (job.get("owner_pid") != auth.get("supervisor_pid")
                    or job.get("owner_start_ticks") != auth.get("supervisor_start_ticks")):
                return None
        elif (job.get("status") != "resume_authorized" or job.get("owner_pid") is not None
                or job.get("resume_session_id") != auth["session_id"]):
            return None
        if auth.get("inputs") != inputs(root, state):
            return None
        if credit := auth.get("repair_credit"):
            if credit != repair_credit_key(state) or credit not in state.get("supervisor_consumed_repairs", ()):
                return None
            inspect_repair_receipt(cycle, state)
        return {**{key: auth[key] for key in _OWNER_FIELDS}, "cycle": cycle_name, "authorization": auth,
                "state_sha256": hashlib.sha256(raw).hexdigest(), "job_sha256": job_sha}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return None


def boot_origin(root: Path, registry: dict) -> dict | None:
    from .runtime_manager import unit_name

    master = registry.get("master_id")
    if (registry.get("launcher") != "systemd-user" or registry.get("state") not in {
            "SUPERVISING", "RECOVERING", "STARTING", "RUNNING"}
            or type(master) is not int or master < 0 or registry.get("systemd_unit") != unit_name(root, master)):
        return None
    origin = _origin(root, registry.get("current_cycle"))
    if (origin is None or not all(origin[key] == registry.get(key) for key in _BINDING_FIELDS)
            or origin["authorization"].get("options") != registry.get("runtime_repair_options", {})):
        return None
    try:
        return origin if _single_owner(root, registry) else None
    except (OSError, ValueError, RuntimeError):
        return None


def verify_origin(root: Path, origin: dict) -> bool:
    from .runtime_manager import load_registry

    if not isinstance(origin, dict) or _origin(root, origin.get("cycle")) != origin:
        return False
    try:
        registry = load_registry(root)
        return (origin["authorization"].get("options") == registry.get("runtime_repair_options", {})
                and _single_owner(root, registry) and _single_owner(root, {**registry, "launch_id": origin["launch_id"]}))
    except (OSError, ValueError, RuntimeError):
        return False


def unclaimed_in(state: dict, registry: dict) -> bool:
    auth = state.get(AUTHORIZATION)
    ticket = registry.get("boot_resume")
    origin = ticket.get("origin") if isinstance(ticket, dict) else None
    prior = origin.get("launch_id") if isinstance(origin, dict) else None
    return (isinstance(auth, dict) and auth.get("schema") == _SCHEMA and "claimed_by" not in auth
            and bool(auth.get("launch_id")) and auth["launch_id"] in (registry.get("launch_id"), prior))


def current_origin(root: Path, registry: dict, launch_id: str, *, child: bool = False) -> dict | None:
    from .runtime_manager import host_boot_id, process_identity_alive, process_start_ticks
    from .supervisor_boot_recovery import RUNTIME_REPAIR_RESUME, verify_boot_resume

    parent = os.getppid() if child else os.getpid()
    runner, ticks = registry.get("runner_pid"), registry.get("runner_start_ticks")
    if (registry.get("launch_id") != launch_id or registry.get("supervisor_pid") != parent
            or registry.get("boot_id") != host_boot_id() or not registry.get("supervisor_start_ticks")
            or not process_identity_alive(parent, registry["supervisor_start_ticks"])
            or (child and (runner != os.getpid() or not ticks or ticks != process_start_ticks(runner)))
            or (not child and process_identity_alive(runner, ticks))):
        return None
    ticket = registry.get("boot_resume") or {}
    if ticket.get("kind") == RUNTIME_REPAIR_RESUME:
        verified = verify_boot_resume(root, launch_id, managed_child=child)
        return verified["origin"] if verified else None
    return boot_origin(root, registry)


def verify_child(root: Path, launch_id: str, cycle_name: str) -> dict | None:
    from .runtime_manager import load_registry

    origin = current_origin(root, load_registry(root), launch_id, child=True)
    return origin if origin and origin["cycle"] == cycle_name else None


def child_resume_lock(root: Path):
    from .runtime_manager import _file_lock

    return _file_lock(root / ".theory-master" / "runtime-repair-resume.lock")
