"""Recover enrolled Supervisor waits and Runner continuations after reboot."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

SCHEMA = "theory-master/supervisor-boot-wait/v1"
DURABLE_RUNNER_PROTOCOL = "durable-invocations-v1"
RUNTIME_REPAIR_RESUME = "runtime_repair_resume"
RUNTIME_REPAIR_ACTIVE_RESUME = "runtime_repair_active_resume"
_ROUTES = frozenset({"qualification_wait", "qualification", "input_support_wait",
                     "input_support", "goal_reassessment"})
_MAX_BOOT_ATTEMPTS = 3
_TERMINAL_DELIVERY = frozenset({"finished", "failed", "unconfirmed"})


def wait_receipt(registry: dict, wait: dict) -> dict | None:
    """Bind a published wait to the host boot and its managing launch."""
    from .runtime_manager import valid_boot_id

    technical = wait.get("kind") == "runtime_recovery"
    operation = registry.get("requested_operation")
    target = registry.get("takeover_cycle")
    if (not valid_boot_id(registry.get("boot_id"))
            or (operation not in {"run", "resume", "takeover"} if technical else operation != "takeover")
            or not registry.get("launch_id") or not registry.get("research_root")
            or type(registry.get("master_id")) is not int or registry["master_id"] < 0
            or wait.get("launch_id") != registry["launch_id"]
            or not wait.get("cycle") or wait["cycle"] != (registry.get("current_cycle") if technical else target)):
        return None
    return {"schema": SCHEMA, "boot_id": registry["boot_id"], "launch_id": registry["launch_id"],
            "research_root": registry["research_root"], "master_id": registry["master_id"],
            "cycle": wait["cycle"], "qualification_wait": wait,
            **({"requested_operation": operation, "takeover_cycle": target} if technical else {})}


def _origin(root: Path, registry: dict, boot: str) -> tuple[dict, int] | None:
    from .invocations import stable_hash
    from .runtime_manager import valid_boot_id

    delivery = registry.get("delivery_reassessment_attempt")
    if (registry.get("runner_operation") == "reassess" and isinstance(delivery, dict)
            and delivery.get("status") == "launching" and delivery.get("launch_id") == registry.get("launch_id")):
        return None  # An admitted delivery now owns this boundary.
    receipt = registry.get("boot_wait")
    if receipt is None and isinstance(registry.get("qualification_wait"), dict):
        receipt = wait_receipt(registry, registry["qualification_wait"])
    if (not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA
            or not valid_boot_id(receipt.get("boot_id")) or receipt["boot_id"] == boot
            or receipt.get("research_root") != str(root) or registry.get("research_root") != str(root)
            or type(receipt.get("master_id")) is not int or receipt["master_id"] != registry.get("master_id")
            or not isinstance(receipt.get("qualification_wait"), dict)):
        return None
    wait = receipt["qualification_wait"]
    technical = wait.get("kind") == "runtime_recovery"
    if (wait.get("cycle") != receipt.get("cycle")
            or not (wait.get("launch_id") and wait["launch_id"] == receipt.get("launch_id"))
            or not (receipt.get("cycle") and registry.get("current_cycle") == receipt["cycle"])):
        return None
    original_owner = (registry.get("boot_id") == receipt["boot_id"] and registry.get("launch_id") == receipt["launch_id"])
    if technical and original_owner:
        if (receipt.get("requested_operation") not in {"run", "resume", "takeover"}
                or receipt["requested_operation"] != registry.get("requested_operation")
                or receipt.get("takeover_cycle") != registry.get("takeover_cycle")):
            return None
    elif registry.get("requested_operation") != "takeover" or registry.get("takeover_cycle") != receipt["cycle"]:
        return None
    if original_owner:
        if registry.get("state") not in {"RECOVERY_WAIT", "STARTING"}:
            return None
        return receipt, 1
    pending = registry.get("boot_recovery")
    if (not isinstance(pending, dict) or pending.get("origin_sha256") != stable_hash(receipt)
            or not registry.get("launch_id") or pending.get("launch_id") != registry["launch_id"]
            or not valid_boot_id(pending.get("boot_id")) or pending["boot_id"] != registry.get("boot_id")
            or registry.get("state") not in {"LAUNCHING", "STARTING"}
            or type(pending.get("attempt")) is not int or not 1 <= pending["attempt"] <= _MAX_BOOT_ATTEMPTS):
        return None
    attempt = pending["attempt"] + 1 if pending["boot_id"] == boot else 1
    return (receipt, attempt) if attempt <= _MAX_BOOT_ATTEMPTS else None


def eligible_boot_wait(root: Path, *, registry: dict | None = None) -> dict | None:
    """Find a prior boot wait with an exact and still-authorized dependency."""
    from .runtime_manager import host_boot_id, load_registry, runtime_health
    from .supervisor import _matches_retained_dependency_wait
    from .supervisor_activation import latest_recovery
    from .supervisor_intervention import supervisor_enabled
    from .worker_mode import read_mode
    from .worker_store import _live_cycle_claims

    root = Path(root).resolve()
    boot = host_boot_id()
    if not boot:
        return None
    registry = load_registry(root) if registry is None else registry
    origin = _origin(root, registry, boot)
    if not origin or runtime_health(root).get("active") or not supervisor_enabled(root):
        return None
    if read_mode(root)["enabled"] or _live_cycle_claims(root):
        return None
    receipt, attempt = origin
    target = latest_recovery(root, include_wait=True)
    wait = receipt["qualification_wait"]
    technical = wait.get("kind") == "runtime_recovery"
    allowed_routes = {"runtime"} if technical else _ROUTES
    if not target or target[0].name != receipt["cycle"] or target[2] not in allowed_routes:
        return None
    if target[1].get("worker_id"):
        return None
    review = target[1].get("supervisor_qualification") or {}
    if (wait.get("session_id") and "direction_fingerprint" in wait
            and wait["direction_fingerprint"] != review.get("direction_fingerprint")):
        return None
    origin_registry = {**registry, "launch_id": receipt["launch_id"], "qualification_wait": wait}
    if technical:
        origin_registry.update(requested_operation="takeover", takeover_cycle=receipt["cycle"])
    if not _matches_retained_dependency_wait(root, origin_registry, target):
        return None
    if registry["launch_id"] != receipt["launch_id"]:
        current_wait = {**wait, "launch_id": registry["launch_id"]}
        if not _matches_retained_dependency_wait(root, {**registry, "qualification_wait": current_wait}, target):
            return None
    return {"origin": receipt, "boot_id": boot, "prior_launch_id": registry["launch_id"],
            "cycle": receipt["cycle"], "route": target[2], "attempt": attempt}


def recover_task_wait(spec: Any) -> dict:
    """Enter the existing managed takeover for one explicitly permitted task."""
    from .runtime_manager import launch_managed_runtime

    plan = eligible_boot_wait(spec.research_root)
    if not spec.task_mode or plan is None or plan["origin"]["master_id"] != spec.master_id:
        return {"status": "skipped"}
    return launch_managed_runtime(
        spec, operation="takeover", identifier=str(spec.master_id),
        takeover_cycle=plan["cycle"], boot_wait=plan,
    )


def rebind_runtime_wait(root: Path, registry: dict) -> dict | None:
    """Pin the same queued incident on a replacement Supervisor in this boot."""
    from .runtime_manager import host_boot_id, process_identity_alive
    from .supervisor import _matches_retained_dependency_wait
    from .supervisor_activation import latest_recovery
    from .supervisor_intervention import supervisor_enabled
    from .worker_mode import read_mode

    receipt = registry.get("boot_wait") or {}
    wait = receipt.get("qualification_wait") or {}
    if (wait.get("kind") != "runtime_recovery" or registry.get("boot_id") != host_boot_id()
            or registry.get("research_root") != str(root) or wait_receipt(registry, wait) != receipt
            or process_identity_alive(registry.get("runner_pid"), registry.get("runner_start_ticks"))
            or not supervisor_enabled(root) or read_mode(root)["enabled"]):
        return None
    fields = {"requested_operation": "takeover", "takeover_cycle": receipt["cycle"], "qualification_wait": wait}
    bound = {**registry, **fields}
    target = latest_recovery(root, include_wait=True)
    if target is None or not _matches_retained_dependency_wait(root, bound, target):
        return None
    return {**fields, "boot_wait": wait_receipt(bound, wait)}


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _goal_reassessment_boot_evidence(root: Path, master: int, attempt: dict) -> dict | None:
    from .goal_reassessment_recovery import goal_reassessment_recovery_pin
    from .goal_terminal import recovery_goal_blocker
    from .public_submission import read_bounded_regular_file, strict_json
    from .specification import TaskSpec, latest_cycle_dir
    from .supervisor_activation import _qualification_wait_control
    from .supervisor_goal_reassessment import delivery_reassessment_plan, reassessment_launch_identity
    from .supervisor_intervention import supervisor_enabled
    from .task_progression import local_replan_reason
    from .worker_mode import read_mode
    from .worker_store import _live_cycle_claims

    try:
        if (not supervisor_enabled(root) or recovery_goal_blocker(root)
                or read_mode(root)["enabled"] or _live_cycle_claims(root)):
            return None
        stopped, anchor = (attempt.get(key) for key in ("stopped_cycle", "anchor_cycle"))
        if not all(isinstance(cycle, str) and cycle.isascii() and cycle.isdecimal() for cycle in (stopped, anchor)):
            return None
        if int(anchor) < 1 or int(anchor) >= int(stopped) or root / stopped != latest_cycle_dir(root, task_mode=True):
            return None
        release = _qualification_wait_control(root)
        if release.exists() or release.is_symlink():
            request = strict_json(read_bounded_regular_file(release, 64_000))
            if not isinstance(request, dict) or (request.get("launch_id") == attempt["launch_id"]
                                                  and request.get("action") == "run"):
                return None
        for cycle in (root / stopped, root / anchor):
            control = cycle / "recovery_control.json"
            if control.exists() or control.is_symlink():
                return None
            state_bytes = read_bounded_regular_file(cycle / "research_state.json", 4_000_000)
            state = strict_json(state_bytes)
            recovery = state.get("recovery") or {}
            if (state.get("worker_id") or state.get("abandoned_cycle") or state.get("cycle_id") != cycle.name
                    or recovery.get("action") in {"pause", "interrupt", "kill", "abandon_cycle"}):
                return None
            if cycle.name == stopped:
                if state.get("execution_active") or local_replan_reason(state) != "no_qualified_candidate":
                    return None
                stopped_sha = hashlib.sha256(state_bytes).hexdigest()
        spec = TaskSpec.from_task_root(root)
        if not spec.task_mode or spec.research_root != root or spec.master_id != master:
            return None
        plan = delivery_reassessment_plan(spec)
        pin = None
        if plan and attempt == {**reassessment_launch_identity(plan, attempt["launch_id"]), "status": "launching"}:
            pin = goal_reassessment_recovery_pin(spec, plan)
        # A review that may have dispatched without a durable result can
        # restore its waiting owner. The old revision remains attempted.
        return {"stopped_state_sha256": stopped_sha, "recovery_pin": pin}
    except (OSError, ValueError, TypeError, AttributeError, KeyError, RuntimeError):
        return None


def _goal_reassessment_boot_origin(root: Path, registry: dict) -> dict | None:
    from .runtime_manager import process_identity_alive

    attempt = registry.get("delivery_reassessment_attempt")
    if not isinstance(attempt, dict):
        return None
    status = attempt.get("status")
    if not isinstance(status, str):
        return None
    operation = registry.get("runner_operation")
    interrupted = status == "launching" and operation == "reassess"
    finishing = status in _TERMINAL_DELIVERY and operation is None
    if (registry.get("launcher") != "systemd-user" or registry.get("requested_operation") != "takeover"
            or not (interrupted or finishing)
            or registry.get("runner_checkpoint_protocol") != DURABLE_RUNNER_PROTOCOL
            or registry.get("state") not in {"STARTING", "RUNNING", "RECOVERING", "FAILED"}
            or process_identity_alive(registry.get("runner_pid"), registry.get("runner_start_ticks"))
            or attempt.get("launch_id") != registry["launch_id"]
            or not (attempt.get("stopped_cycle") == registry.get("current_cycle") == registry.get("takeover_cycle"))
            or not all(isinstance(attempt.get(key), str) and attempt[key] for key in
                       ("stopped_cycle", "anchor_cycle", "reassessment_of", "delivery_revision"))
            or re.fullmatch(r"[a-f0-9]{64}", attempt["delivery_revision"]) is None):
        return None
    evidence = _goal_reassessment_boot_evidence(root, registry["master_id"], attempt)
    if evidence is None:
        return None
    return {"boot_id": registry["boot_id"], "launch_id": registry["launch_id"],
            "research_root": str(root), "master_id": registry["master_id"],
            "cycle": attempt["stopped_cycle"], "delivery_attempt": attempt, **evidence}


def _pinned_goal_reassessment(root: Path, origin: dict) -> bool:
    evidence = _goal_reassessment_boot_evidence(root, origin["master_id"], origin["delivery_attempt"])
    return evidence is not None and all(key in origin and origin[key] == value for key, value in evidence.items())


def _active_evidence(root: Path, origin: dict, *, claimed_runtime_repair: bool = False) -> dict | None:
    """Pin a committed boundary or one authenticated, still-existing native Goal."""
    from .goal_terminal import recovery_goal_blocker
    from .invocations import LEDGER_SCHEMA, TERMINAL_STATES
    from .public_submission import read_bounded_regular_file, strict_json
    from .runtime_manager import host_boot_id, process_identity_alive
    from .specification import CYCLE_MARKER, latest_cycle_dir
    from .supervisor_intervention import supervisor_enabled
    from .worker_mode import read_mode
    from .worker_store import _live_cycle_claims

    cycle = latest_cycle_dir(root, task_mode=True)
    runner_ticks = origin.get("runner_start_ticks") if (claimed_runtime_repair and origin.get("boot_id") == host_boot_id()) else None
    if (cycle is None or cycle.name != origin.get("cycle") or cycle.is_symlink()
            or not (cycle / CYCLE_MARKER).is_file() or (cycle / CYCLE_MARKER).is_symlink()
            or not supervisor_enabled(root) or recovery_goal_blocker(root)
            or read_mode(root)["enabled"] or _live_cycle_claims(root)
            or process_identity_alive(origin.get("runner_pid"), runner_ticks)):
        return None
    control = cycle / "recovery_control.json"
    if control.exists() or control.is_symlink():
        return None
    try:
        state_data = read_bounded_regular_file(cycle / "research_state.json", 16 * 1024 * 1024)
        state = strict_json(state_data)
        if (not isinstance(state, dict) or state.get("status") not in {"active", "running"}
                or state.get("execution_active") is not True or state.get("resumable") is not True
                or state.get("cycle_id") != cycle.name or state.get("master_id") != origin.get("master_id")
                or state.get("owner_pid") != origin.get("runner_pid")
                or state.get("owner_start_ticks") not in {None, origin.get("runner_start_ticks")}
                or state.get("worker_id") or state.get("worker_cycle_claim")
                or state.get("abandoned_cycle") or state.get("human_attention") or state.get("attention")
                or state.get("protocol_health") == "violation" or state.get("goal_submission_gap")
                or state.get("phase") in {"abandon_requested", "control_requested"}
                or (state.get("recovery") or {}).get("action") in {"pause", "interrupt", "kill", "abandon_cycle"}
                or not _positive_int(state.get("invocation_cycle_index"))):
            return None
        if claimed_runtime_repair:
            authorization = state.get("supervisor_runtime_resume_authorization") or {}
            claim = authorization.get("claimed_by") if isinstance(authorization, dict) else None
            if claim != {"launch_id": origin["launch_id"], "pid": origin["runner_pid"],
                         "start_ticks": origin["runner_start_ticks"]}:
                return None
        goal = state.get("native_goal")
        open_goal = bool(goal) and (not isinstance(goal, dict)
                                  or str(goal.get("status")).lower() not in {"completed", "complete", "success"})
        checkpoint = Path(state["last_safe_checkpoint"])
        if checkpoint.parent != cycle / "checkpoints" or checkpoint.suffix != ".json":
            return None
        checkpoint_data = read_bounded_regular_file(checkpoint, 16 * 1024 * 1024)
        saved = strict_json(checkpoint_data)
        if (not isinstance(saved, dict) or saved.get("cycle_id") != cycle.name
                or saved.get("master_id") != origin.get("master_id")
                or not _positive_int(saved.get("owner_pid"))
                or not _positive_int(saved.get("last_seq")) or not _positive_int(state.get("last_seq"))
                or saved["last_seq"] > state["last_seq"]
                or any(saved.get(key) != state.get(key)
                       for key in ("attempt_id", "hypothesis_lock", "hypothesis_id", "hypothesis_locked"))):
            return None
        ledger_data = read_bounded_regular_file(cycle / "invocations.jsonl", 16 * 1024 * 1024)
        invocations: dict[str, dict] = {}
        for line in ledger_data.splitlines():
            record = strict_json(line)
            if (not isinstance(record, dict) or record.get("schema") != LEDGER_SCHEMA
                    or not isinstance(record.get("invocation_id"), str) or not record["invocation_id"]):
                return None
            invocations.setdefault(record["invocation_id"], {}).update(record)
        native_pin = None
        if open_goal or state.get("active_invocation_id") or state.get("current_turn_id"):
            from .boot_native_goal import native_goal_boot_pin
            from .investigator_budget import budget_status

            native_pin = native_goal_boot_pin(cycle, state, invocations)
            if native_pin is None:
                return None
            budget = budget_status(cycle, root, state)
            if budget["exhausted"] or budget["rejected"]:
                return None
        else:
            if not invocations or any(
                row.get("state") not in TERMINAL_STATES or row.get("state") == "completed"
                or (row.get("state") == "committed" and row.get("commit_state") != "committed")
                or (row.get("state") in {"interrupted", "superseded"}
                    and (row.get("native_goal_identity") or (row.get("role") == "investigator"
                         and row.get("stage") in {"investigate", "judge_rework", "integrate_literature"})))
                for row in invocations.values()
            ):
                return None
            last_invocation = state.get("last_invocation_id")
            if last_invocation and invocations.get(last_invocation, {}).get("state") != "committed":
                return None
    except (OSError, ValueError, TypeError, AttributeError, KeyError, RuntimeError):
        return None
    return {"state_sha256": hashlib.sha256(state_data).hexdigest(),
            "checkpoint_sha256": hashlib.sha256(checkpoint_data).hexdigest(),
            "ledger_sha256": hashlib.sha256(ledger_data).hexdigest(),
            "invocation_cycle_index": state["invocation_cycle_index"],
            **({"native_goal": native_pin} if native_pin is not None else {})}


def _pinned_active_evidence(root: Path, origin: dict) -> bool:
    evidence = _active_evidence(root, origin)
    return bool(evidence and evidence.get("native_goal") == origin.get("native_goal")
                and all(origin.get(key) == value for key, value in evidence.items()))


def _runtime_repair_options_match(registry: dict, origin: dict) -> bool:
    from .supervisor_runtime_resume import launch_options

    authorization = origin.get("authorization")
    if not isinstance(authorization, dict) or not authorization:
        return False
    options = authorization.get("options", {})
    if not isinstance(options, dict) or options != registry.get("runtime_repair_options", {}):
        return False
    try:
        return launch_options(**options) == options
    except (TypeError, ValueError):
        return False


def _runtime_repair_boot_origin(root: Path, registry: dict) -> dict | None:
    if (registry.get("launcher") != "systemd-user"
            or registry.get("state") not in {"STARTING", "RUNNING", "SUPERVISING", "RECOVERING"}):
        return None
    from .supervisor_runtime_resume import boot_origin

    origin = boot_origin(root, registry)
    if (not isinstance(origin, dict)
            or any(origin.get(key) != registry.get(key) for key in ("boot_id", "launch_id", "research_root", "master_id"))
            or type(origin.get("master_id")) is not int or not isinstance(origin.get("cycle"), str)
            or not origin["cycle"] or origin["cycle"] != registry.get("current_cycle")
            or not _runtime_repair_options_match(registry, origin)):
        return None
    return origin


def _claimed_runtime_state(root: Path, registry: dict) -> tuple[dict, str | None]:
    from .public_submission import read_bounded_regular_file, strict_json
    from .specification import latest_cycle_dir

    cycle = latest_cycle_dir(root, task_mode=True)
    if cycle is None or cycle.name != registry.get("current_cycle"):
        return {}, None
    data = read_bounded_regular_file(cycle / "research_state.json", 16 * 1024 * 1024)
    state = strict_json(data)
    if (not isinstance(state, dict) or state.get("status") not in {"active", "running"}
            or state.get("execution_active") is not True):
        return {}, None
    return state, hashlib.sha256(data).hexdigest()


def was_claimed_runtime(root: Path, retained_registry: dict) -> bool:
    """Keep a recognizable claimed repair away from ordinary Task Resume."""
    from .supervisor_runtime_resume import AUTHORIZATION

    ticket = retained_registry.get("boot_resume")
    typed = isinstance(ticket, dict) and ticket.get("kind") == RUNTIME_REPAIR_ACTIVE_RESUME
    if not typed and retained_registry.get("runner_operation") != "resume":
        return False
    try:
        state, _ = _claimed_runtime_state(Path(root).resolve(), retained_registry)
        if not state:
            return False
        if typed and ticket.get("cycle") == retained_registry.get("current_cycle"):
            return True
        auth = state.get(AUTHORIZATION)
        claim = auth.get("claimed_by") if isinstance(auth, dict) else None
        return bool(isinstance(claim, dict) and claim.get("launch_id")
                    and claim["launch_id"] == retained_registry.get("launch_id"))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _claimed_runtime_pinned(root: Path, registry: dict, origin: dict) -> bool:
    from .runtime_manager import host_boot_id, process_identity_alive
    from .supervisor_runtime_resume import AUTHORIZATION, _single_owner

    if not _runtime_repair_options_match(registry, origin):
        return False
    previous_owner = origin.get("supervisor_pid"), origin.get("supervisor_start_ticks")
    if (origin.get("boot_id") == host_boot_id() and process_identity_alive(*previous_owner)
            and previous_owner != (registry.get("supervisor_pid"), registry.get("supervisor_start_ticks"))):
        return False
    auth = origin["authorization"]
    if origin.get("options") != auth.get("options", {}):
        return False
    evidence = _active_evidence(root, origin, claimed_runtime_repair=True)
    if (not evidence or evidence.get("native_goal") != origin.get("native_goal")
            or any(origin.get(key) != value for key, value in evidence.items())):
        return False
    state, state_sha = _claimed_runtime_state(root, registry)
    return (state_sha == evidence["state_sha256"] and state.get(AUTHORIZATION) == auth
            and _single_owner(root, registry) and _single_owner(root, {**registry, "launch_id": auth["launch_id"]}))


def _bound_claimed_runtime(root: Path, registry: dict, ticket: dict) -> bool:
    from .runtime_manager import unit_name, valid_boot_id

    origin = ticket.get("origin")
    if not isinstance(origin, dict):
        return False
    boot, launch, master = registry.get("boot_id"), registry.get("launch_id"), registry.get("master_id")
    same_launch = origin.get("launch_id") == launch
    prior_launch = ticket.get("prior_launch_id")
    return (registry.get("launcher") == "systemd-user" and registry.get("requested_operation") in {"run", "resume", "takeover"}
            and valid_boot_id(boot) and ticket.get("boot_id") == boot and valid_boot_id(origin.get("boot_id"))
            and isinstance(launch, str) and bool(launch) and ticket.get("launch_id") == launch
            and isinstance(origin.get("launch_id"), str) and bool(origin["launch_id"])
            and (origin["boot_id"] == boot) == same_launch
            and isinstance(prior_launch, str) and bool(prior_launch) and (prior_launch == launch) == same_launch
            and (same_launch or registry.get("requested_operation") == "resume")
            and origin.get("research_root") == registry.get("research_root") == str(root)
            and type(master) is int and master >= 0
            and type(origin.get("master_id")) is int and origin["master_id"] == master
            and registry.get("systemd_unit") == unit_name(root, master)
            and bool(ticket.get("cycle")) and ticket["cycle"] == origin.get("cycle") == registry.get("current_cycle")
            and origin.get("runner_checkpoint_protocol") == DURABLE_RUNNER_PROTOCOL
            and all(_positive_int(origin.get(key)) for key in ("supervisor_pid", "supervisor_start_ticks",
                                                               "runner_pid", "runner_start_ticks", "invocation_cycle_index"))
            and _positive_int(ticket.get("attempt")) and ticket["attempt"] <= _MAX_BOOT_ATTEMPTS
            and _runtime_repair_options_match(registry, origin))


def _claimed_runtime_plan(root: Path, registry: dict, boot: str, *, current_owner: bool = False) -> dict | None:
    from .runtime_manager import process_identity_alive, process_start_ticks, unit_name
    from .supervisor_runtime_resume import AUTHORIZATION, _SCHEMA

    try:
        launch, master = registry.get("launch_id"), registry.get("master_id")
        if (registry.get("launcher") != "systemd-user" or registry.get("research_root") != str(root)
                or not isinstance(launch, str) or not launch or type(master) is not int or master < 0
                or registry.get("systemd_unit") != unit_name(root, master)):
            return None
        boot_changed = registry.get("boot_id") != boot
        supervisor, supervisor_ticks = registry.get("supervisor_pid"), registry.get("supervisor_start_ticks")
        runner, runner_ticks = registry.get("runner_pid"), registry.get("runner_start_ticks")
        if current_owner:
            if (boot_changed or supervisor != os.getpid() or not _positive_int(supervisor_ticks)
                    or supervisor_ticks != process_start_ticks(supervisor)):
                return None
        elif not boot_changed and process_identity_alive(supervisor, supervisor_ticks):
            return None
        if not boot_changed and process_identity_alive(runner, runner_ticks):
            return None
        cycle = registry.get("current_cycle")
        pending = registry.get("boot_resume") or {}
        if pending.get("kind") == RUNTIME_REPAIR_ACTIVE_RESUME:
            if not _bound_claimed_runtime(root, registry, pending) or pending["attempt"] >= _MAX_BOOT_ATTEMPTS:
                return None
            if (registry.get("state") in {"LAUNCHING", "STARTING", "RUNNING", "RECOVERING"}
                    and _claimed_runtime_pinned(root, registry, pending["origin"])):
                prior = launch if boot_changed else pending["prior_launch_id"]
                return {**pending, "boot_id": boot, "prior_launch_id": prior, "attempt": pending["attempt"] + 1}
        if (registry.get("state") not in {"RUNNING", "RECOVERING"} or registry.get("runner_operation") != "resume"
                or registry.get("runner_checkpoint_protocol") != DURABLE_RUNNER_PROTOCOL
                or not all(_positive_int(value) for value in (supervisor, supervisor_ticks, runner, runner_ticks))):
            return None
        state, state_sha = _claimed_runtime_state(root, registry)
        auth = state.get(AUTHORIZATION)
        review = state.get("supervisor_runtime_review") or {}
        if (not isinstance(auth, dict) or auth.get("schema") != _SCHEMA or auth.get("research_root") != str(root)
                or auth.get("master_id") != master or auth.get("cycle") != cycle
                or not isinstance(auth.get("session_id"), str) or not auth["session_id"].startswith("SUP-")
                or review.get("action") != "resume" or review.get("session_id") != auth["session_id"]
                or auth.get("claimed_by") != {"launch_id": launch, "pid": runner, "start_ticks": runner_ticks}):
            return None
        attempt = auth.get("active_resume_attempt", 0)
        if type(attempt) is not int or not 0 <= attempt < _MAX_BOOT_ATTEMPTS:
            return None
        if pending.get("kind") == RUNTIME_REPAIR_ACTIVE_RESUME:
            old = pending["origin"]["authorization"]
            mutable = {"claimed_by", "active_resume_attempt"}
            if (attempt != pending["attempt"] or {k: v for k, v in old.items() if k not in mutable}
                    != {k: v for k, v in auth.items() if k not in mutable}):
                return None
        elif pending:
            if (pending.get("kind") != RUNTIME_REPAIR_RESUME or not _bound_boot_resume(registry, pending, root)
                    or pending["origin"]["authorization"] != {key: value for key, value in auth.items() if key != "claimed_by"}):
                return None
        elif not attempt and (auth.get("boot_id"), auth.get("launch_id")) != (registry["boot_id"], launch):
            return None
        origin = {key: registry[key] for key in ("boot_id", "launch_id", "research_root", "master_id",
                                                "supervisor_pid", "supervisor_start_ticks", "runner_pid",
                                                "runner_start_ticks", "runner_checkpoint_protocol")}
        origin.update(cycle=cycle, authorization=auth, options=auth.get("options", {}))
        evidence = _active_evidence(root, origin, claimed_runtime_repair=True)
        if not evidence or evidence["state_sha256"] != state_sha:
            return None
        origin.update(evidence)
        if _claimed_runtime_pinned(root, registry, origin):
            return {"kind": RUNTIME_REPAIR_ACTIVE_RESUME, "boot_id": boot, "launch_id": launch,
                    "prior_launch_id": launch, "cycle": cycle, "attempt": attempt + 1, "origin": origin}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return None
    return None


def claimed_runtime_resume(root: Path, retained_registry: dict, *, launch_id: str,
                           current_owner: bool = False) -> dict | None:
    """Plan continuation of the exact active repair within its current service launch."""
    from .runtime_manager import host_boot_id

    boot = host_boot_id()
    if not boot or retained_registry.get("boot_id") != boot or retained_registry.get("launch_id") != launch_id:
        return None
    return _claimed_runtime_plan(Path(root).resolve(), retained_registry, boot, current_owner=current_owner)


def eligible_boot_resume(root: Path, *, registry: dict | None = None) -> dict | None:
    """Find exact continuation authority from a prior boot or a pending launch."""
    from .runtime_manager import host_boot_id, load_registry, process_identity_alive, runtime_health, unit_name, valid_boot_id

    root = Path(root).resolve()
    boot = host_boot_id()
    registry = load_registry(root) if registry is None else registry
    master = registry.get("master_id")
    if (not boot or registry.get("research_root") != str(root)
            or type(master) is not int or master < 0
            or not isinstance(registry.get("launch_id"), str) or not registry["launch_id"]
            or registry.get("systemd_unit") != unit_name(root, master)
            or not valid_boot_id(registry.get("boot_id")) or runtime_health(root).get("active")):
        return None
    attempt = 1
    pending = registry.get("boot_resume")
    kind = None
    runtime_pending = isinstance(pending, dict) and pending.get("kind") == RUNTIME_REPAIR_RESUME
    active_repair = isinstance(pending, dict) and pending.get("kind") == RUNTIME_REPAIR_ACTIVE_RESUME
    if active_repair or (registry["boot_id"] != boot and was_claimed_runtime(root, registry)):
        # Same-boot warm continuations keep the current systemd launch, whereas
        # a pending physical-boot continuation may still rebind a failed launch.
        if registry["boot_id"] == boot and (pending.get("origin") or {}).get("boot_id") == boot:
            return None
        plan = _claimed_runtime_plan(root, registry, boot)
        return {**plan, "prior_launch_id": registry["launch_id"]} if plan else None
    if (runtime_pending and registry.get("state") in {"LAUNCHING", "STARTING", "RUNNING", "SUPERVISING", "RECOVERING"}
            and (registry["boot_id"] != boot
                 or not process_identity_alive(registry.get("runner_pid"), registry.get("runner_start_ticks")))
            and _bound_boot_resume(registry, pending, root) and _pinned_boot_resume(root, pending)):
        origin = pending["origin"]
        kind = RUNTIME_REPAIR_RESUME
        attempt = pending["attempt"] + 1 if pending["boot_id"] == boot else 1
    elif not runtime_pending and registry["boot_id"] != boot and (origin := _runtime_repair_boot_origin(root, registry)) is not None:
        kind = RUNTIME_REPAIR_RESUME
    elif not runtime_pending and registry["boot_id"] != boot and (origin := _goal_reassessment_boot_origin(root, registry)) is not None:
        kind = "goal_reassessment"
    elif (registry.get("launcher") == "systemd-user" and registry["boot_id"] != boot
            and registry.get("runner_checkpoint_protocol") == DURABLE_RUNNER_PROTOCOL
            and registry.get("runner_operation", registry.get("requested_operation")) in {"run", "resume"}
            and not any((registry.get(key) or {}).get("launch_id") == registry["launch_id"]
                        for key in ("retained_quota_attempt", "delivery_reassessment_attempt"))
            and registry.get("state") in {"RUNNING", "RECOVERING"}
            and (not runtime_pending or _bound_boot_resume(registry, pending, root))
            and _positive_int(registry.get("runner_pid"))
            and _positive_int(registry.get("runner_start_ticks"))):
        origin = {key: registry[key] for key in ("boot_id", "launch_id", "research_root", "master_id",
                                                "runner_pid", "runner_start_ticks", "runner_checkpoint_protocol")}
        origin["cycle"] = registry.get("current_cycle")
        evidence = _active_evidence(root, origin, claimed_runtime_repair=runtime_pending)
        if evidence is None:
            return None
        origin.update(evidence)
    elif (not runtime_pending and registry.get("state") in {"LAUNCHING", "STARTING"} and not registry.get("runner_pid")
            and _bound_boot_resume(registry, pending, root) and _pinned_boot_resume(root, pending)):
        origin = pending["origin"]
        kind = pending.get("kind")
        attempt = pending["attempt"] + 1 if pending["boot_id"] == boot else 1
    else:
        return None
    if attempt > _MAX_BOOT_ATTEMPTS:
        return None
    return {"origin": origin, "boot_id": boot, "prior_launch_id": registry["launch_id"],
            "cycle": origin["cycle"], "attempt": attempt,
            **({"kind": kind} if kind else {})}


def _bound_boot_resume(registry: dict, receipt: Any, root: Path) -> bool:
    from .runtime_manager import unit_name, valid_boot_id

    if not isinstance(receipt, dict) or not isinstance(receipt.get("origin"), dict):
        return False
    if receipt.get("kind") == RUNTIME_REPAIR_ACTIVE_RESUME:
        return _bound_claimed_runtime(root, registry, receipt)
    origin = receipt["origin"]
    master = origin.get("master_id")
    common = (bool(registry.get("launch_id")) and receipt.get("launch_id") == registry["launch_id"]
            and registry.get("boot_id") == receipt.get("boot_id")
            and valid_boot_id(origin.get("boot_id")) and origin["boot_id"] != receipt["boot_id"]
            and origin.get("research_root") == registry.get("research_root") == str(root)
            and type(master) is int and master >= 0 and master == registry.get("master_id")
            and registry.get("systemd_unit") == unit_name(root, master)
            and bool(origin.get("launch_id")) and origin["launch_id"] != receipt["launch_id"]
            and bool(receipt.get("cycle")) and receipt["cycle"] == origin.get("cycle") == registry.get("current_cycle")
            and _positive_int(receipt.get("attempt")) and receipt["attempt"] <= _MAX_BOOT_ATTEMPTS)
    if not common:
        return False
    if receipt.get("kind") == RUNTIME_REPAIR_RESUME:
        prior_launch = receipt.get("prior_launch_id")
        return (registry.get("launcher") == "systemd-user"
                and registry.get("requested_operation") == "takeover" and registry.get("takeover_cycle") == receipt["cycle"]
                and isinstance(prior_launch, str) and bool(prior_launch) and prior_launch != receipt["launch_id"]
                and isinstance(origin.get("launch_id"), str) and isinstance(origin.get("cycle"), str)
                and _runtime_repair_options_match(registry, origin))
    if receipt.get("kind") == "goal_reassessment":
        attempt = origin.get("delivery_attempt")
        existing = registry.get("delivery_reassessment_attempt")
        if (registry.get("requested_operation") != "takeover" or registry.get("takeover_cycle") != receipt["cycle"]
                or not isinstance(attempt, dict) or attempt.get("stopped_cycle") != receipt["cycle"]
                or attempt.get("launch_id") != origin["launch_id"]):
            return False
        if attempt.get("status") == "launching":
            return existing in (attempt, {**attempt, "launch_id": registry["launch_id"]})
        return (isinstance(attempt.get("status"), str) and attempt["status"] in _TERMINAL_DELIVERY
                and existing == attempt
                and "recovery_pin" in origin and origin["recovery_pin"] is None)
    return (receipt.get("kind") is None and registry.get("requested_operation") == "resume"
            and origin.get("runner_checkpoint_protocol") == DURABLE_RUNNER_PROTOCOL
            and _positive_int(origin.get("runner_pid")) and _positive_int(origin.get("runner_start_ticks")))


def _pinned_boot_resume(root: Path, receipt: dict) -> bool:
    if receipt.get("kind") == RUNTIME_REPAIR_RESUME:
        from .supervisor_runtime_resume import verify_origin

        return verify_origin(root, receipt["origin"])
    if receipt.get("kind") == "goal_reassessment":
        return _pinned_goal_reassessment(root, receipt["origin"])
    return _pinned_active_evidence(root, receipt["origin"])


def verify_boot_resume(root: Path, launch_id: str, *, managed_child: bool = False,
                       allow_lost_delivery_watch: bool = False) -> dict | None:
    """Recheck the original files and the exact live Supervisor before continuation."""
    from .runtime_manager import host_boot_id, load_registry, process_identity_alive

    root = Path(root).resolve()
    registry = load_registry(root)
    receipt = registry.get("boot_resume")
    supervisor_pid = os.getppid() if managed_child else os.getpid()
    boot = host_boot_id()
    if (not boot or registry.get("boot_id") != boot
            or registry.get("launch_id") != launch_id or registry.get("supervisor_pid") != supervisor_pid
            or not _positive_int(registry.get("supervisor_start_ticks"))
            or not process_identity_alive(supervisor_pid, registry.get("supervisor_start_ticks"))
            or registry.get("state") not in {"STARTING", "RUNNING", "RECOVERING"}
            or not _bound_boot_resume(registry, receipt, root)):
        return None
    if receipt.get("kind") in {RUNTIME_REPAIR_RESUME, RUNTIME_REPAIR_ACTIVE_RESUME}:
        runner_pid, runner_ticks = registry.get("runner_pid"), registry.get("runner_start_ticks")
        if ((runner_pid is None and runner_ticks is not None)
                or (runner_pid is not None and (not _positive_int(runner_pid) or not _positive_int(runner_ticks)))
                or (managed_child and runner_pid != os.getpid())
                or process_identity_alive(runner_pid, runner_ticks) != managed_child):
            return None
    if receipt.get("kind") == RUNTIME_REPAIR_ACTIVE_RESUME:
        try:
            return receipt if _claimed_runtime_pinned(root, registry, receipt["origin"]) else None
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
            return None
    if _pinned_boot_resume(root, receipt):
        return receipt
    if (managed_child or not allow_lost_delivery_watch or receipt.get("kind") != "goal_reassessment"
            or registry.get("runner_operation") != "reassess"
            or process_identity_alive(registry.get("runner_pid"), registry.get("runner_start_ticks"))):
        return None
    origin = receipt["origin"]
    attempt = registry["delivery_reassessment_attempt"]
    if attempt.get("launch_id") != launch_id:
        return None
    evidence = _goal_reassessment_boot_evidence(root, origin["master_id"], attempt)
    if evidence is None or evidence["stopped_state_sha256"] != origin.get("stopped_state_sha256"):
        return None
    # This recovered child already owned the same revision. A changed Judge
    # checkpoint cannot authorize a further dispatch under the same ticket.
    return {**receipt, "origin": {**origin, "recovery_pin": None}}


def recover_task_active(spec: Any) -> dict:
    """Request a managed Resume of the existing Cycle after a host boot."""
    from .runtime_manager import launch_managed_runtime

    plan = eligible_boot_resume(spec.research_root)
    if not spec.task_mode or plan is None or plan["origin"]["master_id"] != spec.master_id:
        return {"status": "skipped"}
    if plan.get("kind") == RUNTIME_REPAIR_ACTIVE_RESUME:
        return launch_managed_runtime(spec, operation="resume", identifier=plan["cycle"], boot_resume=plan,
                                      **plan["origin"]["options"])
    if plan.get("kind") in {"goal_reassessment", RUNTIME_REPAIR_RESUME}:
        options = (plan["origin"]["authorization"].get("options", {})
                   if plan["kind"] == RUNTIME_REPAIR_RESUME else {})
        return launch_managed_runtime(spec, operation="takeover", identifier=str(spec.master_id),
                                      takeover_cycle=plan["cycle"], boot_resume=plan, **options)
    return launch_managed_runtime(spec, operation="resume", identifier=plan["cycle"], boot_resume=plan)
