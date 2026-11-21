"""Operator activation of Supervisor recovery for an already stopped task."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

QUALIFICATION_WAIT_CHECK_SECONDS = 30
_DELIVERY_ATTEMPT_KEYS = ("stopped_cycle", "anchor_cycle", "delivery_revision")
_DELIVERY_TERMINAL_STATUSES = ("failed", "unconfirmed", "finished")


def same_delivery_attempt(plan: dict[str, Any], attempt: Any) -> bool:
    return isinstance(attempt, dict) and all(plan.get(key) and plan[key] == attempt.get(key)
                                              for key in _DELIVERY_ATTEMPT_KEYS)


def completed_delivery_attempts(registry: dict[str, Any], stopped_cycle: str) -> list[dict[str, Any]]:
    """Keep attempted revisions scoped to one exact closed qualification Cycle."""
    from .runtime_manager import process_identity_alive

    history = registry.get("delivery_reassessment_history") or []
    records = [row for row in history if isinstance(row, dict) and row.get("stopped_cycle") == stopped_cycle
               and row.get("status") in _DELIVERY_TERMINAL_STATUSES] if isinstance(history, list) else []
    current = registry.get("delivery_reassessment_attempt")
    if isinstance(current, dict) and current.get("stopped_cycle") == stopped_cycle:
        status = current.get("status")
        if status == "launching" and (current.get("launch_id") != registry.get("launch_id")
                or registry.get("requested_operation") != "takeover"
                or registry.get("takeover_cycle") != stopped_cycle
                or not process_identity_alive(registry.get("supervisor_pid"), registry.get("supervisor_start_ticks"))):
            status = "unconfirmed"
        if status in _DELIVERY_TERMINAL_STATUSES and not any(same_delivery_attempt(current, row) for row in records):
            records.append({**current, "status": status})
    return records


def takeover_runner_operation(route: str) -> str:
    if route in {"qualification", "qualification_wait", "input_support", "input_support_wait"}:
        return "run"
    if route == "goal_reassessment":
        return "reassess"
    if route == "budget_preserve":
        return "preserve"
    return "resume"


def input_support_target(cycle: Path, state: dict[str, Any], *, establish_baseline: bool = False,
                         registry: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Keep the exact closed dependency owned without reusing attempted inputs."""
    from .runtime_manager import RuntimeRegistryError, load_registry, process_identity_alive
    from .supervisor_input_support import input_support_recovery

    control = cycle / "recovery_control.json"
    if control.exists() or control.is_symlink():
        return None
    target = input_support_recovery(cycle, state, establish_baseline=establish_baseline)
    if not target or target["route"] != "input_support":
        return target
    try:
        if registry is None:
            registry = load_registry(cycle.parent)
    except RuntimeRegistryError:
        return None
    prior = registry.get("input_support_attempt")
    if (not isinstance(prior, dict) or prior.get("stopped_cycle") != cycle.name
            or prior.get("dependency_identity") != target["dependency_identity"]):
        return target
    attempted = prior.get("attempted_input_ids")
    if not isinstance(attempted, list) or any(not isinstance(value, str) for value in attempted):
        return None
    live_launch = (prior.get("status") == "launching" and prior.get("launch_id") == registry.get("launch_id")
                   and registry.get("requested_operation") == "takeover" and registry.get("takeover_cycle") == cycle.name
                   and process_identity_alive(registry.get("supervisor_pid"), registry.get("supervisor_start_ticks")))
    identified_launch = live_launch and prior.get("wake_identity") == target["wake_identity"]
    new_input = not live_launch and bool(set(target["added_input_ids"]) - set(attempted))
    if identified_launch or new_input:
        return target
    return {**target, "route": "input_support_wait", "attempt_status": prior.get("status")}


def stopped_recovery(cycle: Path, state: dict[str, Any], *, include_wait: bool = False,
                     establish_input_baseline: bool = False, worker_id: str | None = None) -> str | None:
    """Classify persisted stop evidence without granting Resume authority."""
    if not isinstance(state, dict) or not state:
        return None
    if str(state.get("cycle_id") or cycle.name) != cycle.name or state.get("execution_active"):
        return None
    status = str(state.get("status") or "").lower()
    recovery = state.get("recovery") or {}
    completion = state.get("goal_completion") or {}
    if not isinstance(recovery, dict) or not isinstance(completion, dict):
        return None
    control = cycle / "recovery_control.json"
    if (control.exists() or control.is_symlink() or state.get("abandoned_cycle")
            or state.get("phase") == "abandon_requested" or completion.get("verdict") == "passed"
            or recovery.get("action") in {"pause", "interrupt", "kill", "abandon_cycle"}):
        return None
    from .goal_terminal import recovery_goal_blocker
    if recovery_goal_blocker(cycle.parent):
        return None
    attention = state.get("attention")
    if status == "needs_attention" and state.get("budget_auto_preserve") and not state.get("worker_id"):
        from .budget_auto_preservation import single_budget_preservation_preflight

        preflight = single_budget_preservation_preflight(cycle, state)
        if preflight and preflight["status"] == "ready":
            return "budget_preserve"
    if (status == "needs_attention" and isinstance(attention, dict)
            and attention.get("code") == "public_submission_incomplete"):
        from .submission_rework import submission_takeover_preflight

        preflight = submission_takeover_preflight(cycle, state)
        return "submission" if preflight and preflight["status"] in {"ready", "ready_for_goal"} else None
    from .supervisor_recovery import maintenance_stop, operational_incident
    if operational_incident(state):
        try:
            return None if maintenance_stop(cycle, state) else "runtime"
        except (OSError, ValueError):
            return None  # A malformed recovery receipt cannot authorize takeover.
    from .task_progression import local_replan_reason
    reason = local_replan_reason(state)
    if reason == "input_support_unavailable":
        if (state.get("worker_id") or None) != worker_id:
            return None
        if worker_id:
            from .supervisor_input_support import input_support_recovery

            target = input_support_recovery(cycle, state, establish_baseline=establish_input_baseline,
                                            worker_id=worker_id)
        else:
            target = input_support_target(cycle, state, establish_baseline=establish_input_baseline)
        if target and (include_wait or target["route"] == "input_support"):
            return target["route"]
        return None
    if reason == "no_qualified_candidate":
        review = state.get("supervisor_qualification") or {}
        waiting = isinstance(review, dict) and review.get("status") in {"waiting", "review_exhausted"}
        completed = []
        if not state.get("worker_id"):
            from .runtime_manager import load_registry, RuntimeRegistryError

            try:
                completed = completed_delivery_attempts(load_registry(cycle.parent), cycle.name)
            except RuntimeRegistryError:
                return None
        if waiting or not state.get("worker_id"):
            from .specification import TaskSpec
            from .worker_direction import (direction_changed, omitted_qualification_input_ids,
                                           qualification_wait_fingerprint, research_direction_fingerprint)
            try:
                spec = TaskSpec.from_task_root(cycle.parent)
                if not state.get("worker_id"):
                    from .supervisor_goal_reassessment import delivery_reassessment_plan

                    delivery = delivery_reassessment_plan(spec)
                    if (delivery and delivery["stopped_cycle"] == cycle.name
                            and not any(same_delivery_attempt(delivery, attempt) for attempt in completed)):
                        return "goal_reassessment"
                if waiting:
                    current = research_direction_fingerprint(spec)
                    previous = qualification_wait_fingerprint(
                        cycle.parent, state, establish_from=current if establish_input_baseline else None)
                    if not direction_changed(previous, current):
                        from .prequalification_holdout import holdout_files

                        holdout = holdout_files(getattr(spec, "config_path", None))
                        if not omitted_qualification_input_ids(
                            cycle.parent, state, current,
                            excluded_sha256=frozenset(file.sha256 for file in holdout),
                        ):
                            return "qualification_wait" if include_wait else None
            except (OSError, ValueError):
                if waiting:
                    return "qualification_wait" if include_wait else None
        if completed and not waiting:
            return "qualification_wait" if include_wait else None
        return "qualification"
    if (status == "paused" and recovery.get("code") == "provider_quota_exhausted"
            and (state.get("resumable") or state.get("last_safe_checkpoint"))):
        from .quota_auto_adjust import quota_auto_adjust_enabled
        if quota_auto_adjust_enabled(cycle.parent):
            return "quota"
    return None


def _read_cycle_state(cycle: Path) -> dict[str, Any]:
    path = cycle / "research_state.json"
    if cycle.is_symlink() or path.is_symlink() or not path.is_file():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def cycle_recovery(cycle: Path, *, include_wait: bool = False,
                   establish_input_baseline: bool = False) -> tuple[Path, dict[str, Any], str] | None:
    state = _read_cycle_state(cycle)
    route = stopped_recovery(cycle, state, include_wait=include_wait,
                             establish_input_baseline=establish_input_baseline)
    return (cycle, state, route) if route else None


def latest_recovery(root: Path, *, include_wait: bool = False,
                    establish_input_baseline: bool = False) -> tuple[Path, dict[str, Any], str] | None:
    """Use the same Cycle identity as the single-owner managed launcher."""
    from .runtime_manager import latest_cycle_name
    name = latest_cycle_name(root)
    return cycle_recovery(root / name, include_wait=include_wait,
                          establish_input_baseline=establish_input_baseline) if name else None


def _dormant_worker_recovery(root: Path, worker: dict[str, Any], *, include_wait: bool = False,
                          establish_input_baseline: bool = False) -> tuple[str, str] | None:
    """Locate exactly the archived dependency of a dormant Worker."""
    from .runtime_manager import process_identity_alive
    from .worker_state import WAITING_RESEARCH_DIRECTION

    name = str(worker.get("current_cycle") or "")
    if (worker.get("status") != WAITING_RESEARCH_DIRECTION or worker.get("claim_id")
            or worker.get("epoch") is not None or worker.get("close_requested") or worker.get("dismissed")
            or worker.get("process_alive") or process_identity_alive(worker.get("pid"), worker.get("pid_start_ticks"))
            or not name.isascii() or not name.isdecimal() or int(name) < 1):
        return None
    cycle = root / name
    state = _read_cycle_state(cycle)
    if state.get("worker_id") != worker.get("worker_id"):
        return None
    route = stopped_recovery(cycle, state, include_wait=include_wait,
                             establish_input_baseline=establish_input_baseline,
                             worker_id=worker.get("worker_id"))
    if route not in {"qualification", "qualification_wait", "input_support", "input_support_wait"}:
        return None
    from .multi_worker import _read_json
    from .specification import TaskSpec
    scheduler = _read_json(root / ".theory-master" / "worker_scheduler.json")
    counts = scheduler.get("cycles_started_by_worker") or {}
    count = counts.get(worker.get("worker_id")) if isinstance(counts, dict) else None
    if scheduler.get("quota_mode") == "per_worker":
        if type(count) is not int or not 0 <= count < TaskSpec.from_task_root(root).max_cycles:
            return None
    return name, route


def dormant_qualification_cycle(root: Path, worker: dict[str, Any]) -> str | None:
    target = _dormant_worker_recovery(root, worker)
    return target[0] if target and target[1] == "qualification" else None


def _pending_worker_direction(root: Path, worker: dict[str, Any]) -> bool:
    from .worker_direction import _pending_retry_direction
    cycle = dormant_qualification_cycle(root, worker)
    if cycle is None:
        return False
    request = _pending_retry_direction(root, worker["worker_id"]) or {}
    return (request.get("source") == "supervisor" and request.get("waiting_cycle") == cycle
            and request.get("wake_fingerprint_at_request") == worker.get("wake_fingerprint"))



def worker_recovery_ready(root: Path, worker: dict[str, Any]) -> bool:
    if _pending_worker_direction(root, worker):
        return True
    target = _dormant_worker_recovery(root, worker)
    return bool(target and target[1] == "input_support")


def request_stopped_takeover(spec: Any) -> dict[str, Any] | None:
    """Wake the owning recovery service without generically resuming a task."""
    from .goal_terminal import recovery_goal_blocker
    from .runtime_manager import launch_managed_runtime, runtime_health
    from .supervisor_intervention import supervisor_enabled
    from .worker_scheduler import scheduler_status
    from .worker_store import _live_cycle_claims, list_workers

    root = Path(spec.research_root).resolve()
    if not spec.task_mode or not supervisor_enabled(root):
        return None
    if recovery_goal_blocker(root, spec=spec):
        return None
    if runtime_health(root).get("active"):
        return None
    from .worker_mode import read_mode
    from .worker_actions import worker_action
    from .worker_supervisor_dispatch import _eligible

    workers = list_workers(root)
    claims = _live_cycle_claims(root)
    mode = read_mode(root)
    worker_targets = {}
    if not mode["configured"] or mode["enabled"]:
        for worker in workers:
            target = _dormant_worker_recovery(root, worker, include_wait=True)
            if target is not None:
                worker_targets[worker["worker_id"]] = target
    if claims or mode["enabled"] or worker_targets:
        requested = any(route != "qualification" for _, route in worker_targets.values())
        for worker_id, (cycle, route) in worker_targets.items():
            if route != "qualification":
                continue
            try:
                worker_action(spec, worker_id, "retry_direction", supervisor_cycle=cycle)
            except ValueError:
                continue  # An operator or Scheduler changed this Worker during admission.
            requested = True
        if not requested and not any(_eligible(root, worker) is not None for worker in workers):
            return None
        if scheduler_status(root).get("process_alive"):
            return {"status": "scheduler_owned"}
        return _launch_worker_recovery(root)
    latest = latest_recovery(root, include_wait=True)
    if latest is None:
        from .runtime_manager import latest_cycle_name
        name = latest_cycle_name(root)
        return record_policy_hold(root / name) if name else None
    cycle, state, route = latest
    if state.get("worker_id"):
        return None  # A released parallel Cycle must keep its Worker identity.
    launched = launch_managed_runtime(spec, operation="takeover", identifier=str(spec.master_id),
                                      takeover_cycle=cycle.name)
    return {"status": launched["status"], "launch_id": launched.get("launch_id"), "cycle": cycle.name,
            "route": route}


def _qualification_wait_control(root: Path) -> Path:
    return root / ".theory-master" / "supervisor_qualification_wait_control.json"


def qualification_wait_owned(health: dict[str, Any]) -> bool:
    registry = health.get("registry") or {}
    wait = registry.get("qualification_wait")
    return bool(health.get("state") == "RECOVERY_WAIT" and health.get("supervisor_alive")
                and not health.get("runner_alive") and registry.get("requested_operation") == "takeover"
                and isinstance(wait, dict) and wait.get("launch_id") == registry.get("launch_id")
                and wait.get("cycle") == registry.get("current_cycle"))


def release_qualification_wait_for_run(root: Path) -> None:
    """Hand a quiet Supervisor back to an explicit Run without signaling a Runner."""
    from .runtime import write_json
    from .runtime_manager import process_identity_alive, runtime_health

    health = runtime_health(root)
    if not qualification_wait_owned(health):
        return
    registry = health["registry"]
    write_json(_qualification_wait_control(root), {"launch_id": registry["launch_id"], "action": "run"},
               sync_directory=True)
    deadline = time.monotonic() + 5
    while (time.monotonic() < deadline
           and process_identity_alive(registry.get("supervisor_pid"), registry.get("supervisor_start_ticks"))):
        time.sleep(0.1)


def wait_for_qualification_change(root: Path, cycle: Path, launch_id: str, stop: Any) -> bool:
    """Own the recorded dependency until its exact stopped route becomes ready."""
    from .runtime_manager import load_registry, update_registry
    from .supervisor_boot_recovery import wait_receipt
    from .supervisor_intervention import supervisor_enabled
    from .goal_terminal import recovery_goal_blocker
    from .worker_store import _live_cycle_claims

    published = False
    next_snapshot_check = 0.0
    try:
        while not stop.is_set() and supervisor_enabled(root):
            control = _qualification_wait_control(root)
            if control.is_file() and not control.is_symlink():
                try:
                    request = json.loads(control.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return False
                if (isinstance(request, dict) and request.get("action") == "run"
                        and request.get("launch_id") == launch_id):
                    return False
            cycle_control = cycle / "recovery_control.json"
            if cycle_control.exists() or cycle_control.is_symlink():
                return False
            now = time.monotonic()
            if now >= next_snapshot_check:
                next_snapshot_check = now + QUALIFICATION_WAIT_CHECK_SECONDS
                if _live_cycle_claims(root) or recovery_goal_blocker(root):
                    return False
                target = latest_recovery(root, include_wait=True, establish_input_baseline=True)
                if target is None or target[0] != cycle or target[2] not in {
                        "qualification_wait", "qualification", "goal_reassessment", "input_support_wait", "input_support"}:
                    return False
                if target[2] not in {"qualification_wait", "input_support_wait"}:
                    return True
                if not published:
                    review = (input_support_target(cycle, target[1]) if target[2] == "input_support_wait"
                              else target[1].get("supervisor_qualification") or {})
                    if target[2] == "input_support_wait":
                        if review is None:
                            return False
                        if review["route"] == "input_support":
                            return True
                    elif not isinstance(review, dict) or review.get("status") not in {"waiting", "review_exhausted"}:
                        completed = completed_delivery_attempts(load_registry(root), cycle.name)
                        if not completed:
                            return False
                        receipt = completed[-1]
                        reason = {
                            "failed": "The delivery-only reassessment failed for this revision.",
                            "unconfirmed": "The previous delivery-only reassessment has no confirmed exit.",
                            "finished": "The delivery-only reassessment ended for this revision.",
                        }[receipt["status"]]
                        review = {"reason": reason,
                                  "wake_condition": "A new verified public submission for the existing Goal assessment.",
                                  "delivery_revision": receipt.get("delivery_revision")}
                    wait = {
                        "launch_id": launch_id, "cycle": cycle.name,
                        "session_id": review.get("session_id"), "reason": review.get("reason"),
                        "wake_condition": review.get("wake_condition"),
                        "direction_fingerprint": review.get("direction_fingerprint"),
                        **({"delivery_revision": review["delivery_revision"]} if "delivery_revision" in review else {}),
                        **({"kind": "input_support_unavailable", "dependency_identity": review["dependency_identity"],
                            "invocation_id": review["invocation_id"], "attempt_status": review.get("attempt_status")}
                           if target[2] == "input_support_wait" else {}),
                    }
                    boot_wait = wait_receipt(load_registry(root), wait)
                    update_registry(root, state="RECOVERY_WAIT", current_cycle=cycle.name,
                                    qualification_wait=wait, boot_wait=boot_wait, boot_recovery=None)
                    published = True
            stop.wait(1)
    finally:
        if published:
            update_registry(root, qualification_wait=None)
    return False


def _held_takeover(cycle: Path) -> dict[str, Any] | None:
    from .supervisor_recovery import maintenance_stop

    state = _read_cycle_state(cycle)
    control = cycle / "recovery_control.json"
    recovery = state.get("recovery") or {}
    attention = state.get("attention") or {}
    if state.get("execution_active") or not isinstance(recovery, dict) or not isinstance(attention, dict):
        return None
    status = str(state.get("status") or "").lower()
    code = str(attention.get("code") or "")
    if (control.exists() or control.is_symlink() or state.get("abandoned_cycle")
            or recovery.get("action") in {"pause", "interrupt", "kill", "abandon_cycle"}
            or (status in {"paused", "interrupted"} and recovery.get("code") != "provider_quota_exhausted")):
        code = "operator_control"
        reason = "Existing operator stop or pause still applies"
    elif status == "needs_attention" and code == "public_submission_incomplete":
        from .submission_rework import submission_takeover_preflight

        preflight = submission_takeover_preflight(cycle, state)
        if not preflight or preflight["status"] != "hold":
            return None
        code, reason = preflight["code"], preflight["reason"]
    elif status == "needs_attention" and code in {
        "investigator_budget_review_rejected", "investigator_budget_supervisor_declined",
        "investigator_budget_cap_reached", "cycle_cost_budget_exhausted", "cycle_token_budget_exhausted",
        "investigation_round_budget_exhausted", "judge_rework_budget_exhausted",
        "investigator_internal_turn_limit",
    }:
        reason = str(attention.get("reason") or "The existing research budget decision still applies")
        if state.get("budget_auto_preserve") and not state.get("worker_id"):
            from .budget_auto_preservation import single_budget_preservation_preflight

            preflight = single_budget_preservation_preflight(cycle, state)
            if preflight and preflight["status"] == "ready":
                return None
            if preflight:
                reason = preflight["reason"]
    else:
        try:
            stop = maintenance_stop(cycle, state)
        except (OSError, ValueError):
            return None
        if stop is None:
            return None
        code = "maintenance_stop"
        reason = str(stop.get("blocking_dependency") or "Maintenance stopped automatic recovery for this incident")
    return {"status": "policy_hold", "cycle": cycle.name, "code": code, "reason": reason[:900]}


def record_policy_hold(cycle: Path) -> dict[str, Any] | None:
    """Project a confirmed policy hold without touching the scientific checkpoint."""
    from .runtime import now, write_json
    from .runtime_admission import admission_lock
    from .runtime_manager import _file_lock, latest_cycle_name, owner_lock_path, runtime_health
    from .supervisor_intervention import supervisor_enabled
    from .goal_terminal import recovery_goal_blocker
    from .worker_store import list_workers

    if _held_takeover(cycle) is None:
        return None
    root = cycle.parent
    try:
        with _file_lock(root / ".theory-master" / "scheduler.lock", nonblocking=True), admission_lock(root), \
                _file_lock(owner_lock_path(root), nonblocking=True):
            if (not supervisor_enabled(root) or latest_cycle_name(root) != cycle.name
                    or runtime_health(root).get("active") or recovery_goal_blocker(root)
                    or any(worker.get("process_alive") for worker in list_workers(root))):
                return None
            policy = _held_takeover(cycle)
            if policy is None:
                return None
            receipt = cycle / "supervisor" / "activation.json"
            if receipt.parent.is_symlink() or receipt.is_symlink():
                raise ValueError("Unsafe Supervisor activation record")
            write_json(receipt, {"schema": "theory-master/supervisor-activation/v1",
                                 "owner": "sdk:supervisor", "at": now(), **policy})
            projection = cycle / "run_state.json"
            if projection.is_file() and not projection.is_symlink():
                state = json.loads(projection.read_text(encoding="utf-8"))
                if isinstance(state, dict):
                    state.update(supervisor_enabled=True, attention_takeover_enabled=True,
                                 system_failure_actions_enabled=True)
                    state["attention_takeover_handoff"] = {
                        "status": "policy_hold", "target": "sdk:supervisor", "error": None,
                        "reason": policy["reason"], "code": policy["code"],
                    }
                    write_json(projection, state)
            return {**policy, "receipt": str(receipt)}
    except BlockingIOError:
        return None


def _launch_worker_recovery(root: Path) -> dict[str, Any]:
    from .multi_worker import _read_json, _save_json
    from .runtime_manager import _file_lock, process_identity_alive
    from .worker_launch import _launch_detached_service, _task_scoped_process_env

    directory = root / ".theory-master"
    marker = directory / "worker_supervisor.json"
    with _file_lock(directory / "worker_supervisor_launch.lock"):
        record = _read_json(marker)
        if (process_identity_alive(record.get("pid"), record.get("start_ticks"))
                or float(record.get("launch_pending_until") or 0) > time.time()):
            return {"status": "worker_recovery_owned"}
        with (directory / "worker_supervisor.log").open("a", encoding="utf-8") as output:
            _save_json(marker, {"launch_pending_until": time.time() + 30})
            try:
                _launch_detached_service(
                    [sys.executable, "-m", "theory_master.supervisor_activation", "--research-root", str(root)],
                    root=root, env=_task_scoped_process_env(root), output=output, kind="worker-supervisor",
                )
            except (OSError, RuntimeError):
                _save_json(marker, {})
                raise
    return {"status": "worker_recovery_requested"}


def _run_worker_recovery(root: Path) -> int:
    """Recover owned Workers when their normal scheduler is not running."""
    from .multi_worker import _save_json
    from .runtime_manager import _file_lock, process_start_ticks, runtime_health
    from .supervisor_intervention import supervisor_enabled
    from .specification import TaskSpec
    from .worker_actions import worker_action
    from .worker_mode import read_mode
    from .worker_scheduler import schedule_once
    from .goal_terminal import recovery_goal_blocker
    from .worker_store import list_workers
    from .worker_supervisor_dispatch import dispatch_pending, has_pending_recovery

    directory = root / ".theory-master"
    marker = directory / "worker_supervisor.json"
    try:
        with _file_lock(directory / "worker_supervisor_owner.lock", nonblocking=True):
            _save_json(marker, {"pid": os.getpid(), "start_ticks": process_start_ticks(os.getpid())})
            launched: dict[str, str] = {}
            try:
                while supervisor_enabled(root):
                    # The normal Scheduler can acquire this lock between ticks
                    # and immediately take over the Worker pool.
                    with _file_lock(directory / "scheduler_owner.lock", nonblocking=True):
                        if runtime_health(root).get("active") or recovery_goal_blocker(root):
                            return 0
                        spec = TaskSpec.from_task_root(root)
                        occupied = dispatch_pending(root)
                        mode = read_mode(root)
                        workers = list_workers(root)
                        directions = set()
                        waits = False
                        if not mode["configured"] or mode["enabled"]:
                            for worker in workers:
                                target = _dormant_worker_recovery(root, worker, include_wait=True,
                                                                establish_input_baseline=True)
                                if target is None:
                                    continue
                                cycle, route = target
                                if route in {"qualification_wait", "input_support_wait"}:
                                    waits = True
                                    continue
                                worker_id = worker["worker_id"]
                                if route == "input_support":
                                    directions.add(worker_id)
                                    continue
                                review = _read_cycle_state(root / cycle).get("supervisor_qualification") or {}
                                if (not _pending_worker_direction(root, worker)
                                        and isinstance(review, dict)
                                        and review.get("status") in {"waiting", "review_exhausted"}):
                                    try:
                                        worker_action(spec, worker_id, "retry_direction", supervisor_cycle=cycle)
                                    except ValueError:
                                        continue
                                if _pending_worker_direction(root, worker):
                                    directions.add(worker_id)
                        if directions and not occupied:
                            slots = mode["desired_agents"] if mode["configured"] else max(1, len(workers))
                            result = schedule_once(spec, resource_slots=slots,
                                                   eligible_worker_ids=frozenset(directions),
                                                   supervisor_recovery_only=True)
                            for row in result["started"]:
                                launched[row["worker_id"]] = row["claim_id"]
                            workers = list_workers(root)
                        launched = {worker["worker_id"]: launched[worker["worker_id"]] for worker in workers
                                    if worker["worker_id"] in launched and
                                    (worker.get("claim_id") == launched[worker["worker_id"]]
                                     or (worker.get("process_alive") and
                                         worker.get("last_claim_id") == launched[worker["worker_id"]]))}
                        if not (directions or waits or launched or has_pending_recovery(root)):
                            return 0
                    time.sleep(8)
            finally:
                _save_json(marker, {})
    except BlockingIOError:
        return 0  # Another recovery process or the normal Scheduler owns the pool.
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-root", type=Path, required=True)
    args = parser.parse_args(argv)
    return _run_worker_recovery(args.research_root.resolve(strict=True))


if __name__ == "__main__":
    raise SystemExit(main())
