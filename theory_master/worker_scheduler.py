"""Worker allocation and bounded scheduler lifecycle."""
from __future__ import annotations
import fcntl
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any


def _recover_proposal_gate(root: Path) -> dict[str, Any] | None:
    """G2: reclaim the Proposal Gate only from a provably dead holder."""
    from . import multi_worker
    recovered = multi_worker.ProposalGate(root).recover_dead_holder()
    if recovered is None:
        return None
    return {key: recovered.get(key) for key in
            ("worker_id", "claim_id", "fencing_epoch", "gate_token", "owner_pid", "acquired_at")}


def schedule_once(spec: Any, *, resource_slots: int,
                  preferred_hypothesis: str | None = None,
                  eligible_worker_ids: frozenset[str] | None = None,
                  supervisor_recovery_only: bool = False) -> dict[str, Any]:
    """One bounded allocation transaction; each Worker independently owns 1 Cycle.

    Workers claim Cycles, not historical H: capacity never depends on active
    H frontiers, and ``preferred_hypothesis`` is accepted but ignored (I-12).
    """
    from . import goal_terminal, multi_worker, runtime_manager, worker_direction, worker_launch, worker_routes, worker_store
    if resource_slots < 1:
        raise ValueError("resource_slots must be >=1")
    root = Path(spec.research_root).resolve()
    scheduler_lock = root / ".theory-master" / "scheduler.lock"
    scheduler_lock.parent.mkdir(parents=True, exist_ok=True)
    with open(scheduler_lock, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        notes: dict[str, Any] = {}
        recovered = _recover_proposal_gate(root)
        if recovered is not None:
            notes["proposal_gate_recovered"] = recovered
        if preferred_hypothesis:
            notes["preferred_hypothesis_ignored"] = {
                "value": preferred_hypothesis,
                "deprecation": "Preferred no longer influences scheduling (V1.1 I-12)"}
        if runtime_manager.runtime_health(root).get("active"):
            return {"started": [], "reason": "legacy_runtime_active", **notes}
        if worker_store._master_complete(root, spec=spec):
            return {"started": [], "reason": "master_complete", **notes}
        worker_store._ensure_legacy_claims_migrated(root)
        registry = multi_worker.CycleClaims(root, worker_store._graph_path(spec))
        capacity, accounts = worker_routes._route_capacity(spec, resource_slots)
        sharing = worker_routes._share_enabled(root)
        active = worker_store._living_workers(root)
        woken, dormant, direction = worker_direction._partition_waiting_workers(spec, worker_store.list_workers(root))
        recovery_quota = None
        if supervisor_recovery_only:
            from .supervisor_activation import worker_recovery_ready
            from .supervisor_intervention import supervisor_enabled
            from .worker_mode import read_mode
            mode = read_mode(root)
            targets = frozenset(worker["worker_id"] for worker in woken
                                if worker_recovery_ready(root, worker))
            if not supervisor_enabled(root) or (mode["configured"] and not mode["enabled"]) or not targets:
                return {"started": [], "reason": "supervisor_recovery_target_changed", **notes}
            eligible_worker_ids = (targets if eligible_worker_ids is None
                                   else eligible_worker_ids & targets)
            quota_path = root / ".theory-master" / "worker_scheduler.json"
            quota = multi_worker._read_json(quota_path)
            if quota.get("quota_mode") == "per_worker":
                counts = quota.get("cycles_started_by_worker")
                if (not isinstance(counts, dict)
                        or any(type(count) is not int or count < 0 for count in counts.values())):
                    return {"started": [], "reason": "worker_recovery_quota_unavailable", **notes}
                eligible_worker_ids = frozenset(worker for worker in eligible_worker_ids
                                                if worker in counts and counts[worker] < spec.max_cycles)
                if not eligible_worker_ids:
                    return {"started": [], "reason": "cycle_budget_exhausted", **notes}
                recovery_quota = (quota_path, quota, counts)
        preferences = worker_routes.worker_account_preferences(root)
        occupied_accounts = {w.get("account") for w in [*active, *dormant] if w.get("account")}
        account_required = any(a is not None for a in accounts)
        available_accounts = ([a for a in accounts if a is not None]
                              if sharing else [a for a in accounts if a is not None and a not in occupied_accounts])
        concurrency = max(0, capacity - len(active) - len(dormant))
        dormant_ids = frozenset(w["worker_id"] for w in dormant)
        woken_by_id = {w["worker_id"]: w for w in woken}
        started = []
        chosen_ids: set[str] = set()
        for _slot in range(concurrency):
            # A completed Worker retains its immutable account binding across
            # Cycle allocations. Choose a usable Worker/account PAIR, rather
            # than selecting the highest-quota account first and failing on
            # the historical pinned CODEX_HOME identity.
            skipped: set[str] = set()
            while True:
                try:
                    worker = worker_store.available_worker_name(root,
                        exclude=frozenset(chosen_ids | skipped | dormant_ids),
                        eligible_ids=eligible_worker_ids)
                except RuntimeError:
                    worker = None
                    break
                previous_account = worker_store.read_worker(root, worker).get("account")
                desired_account = preferences.get(worker)
                options = ([desired_account] if desired_account else
                           [previous_account] if previous_account else available_accounts)
                if sharing and account_required:
                    account = worker_routes._shared_account_candidate(options, [*active, *dormant], started)

                elif account_required:
                    account = next((a for a in options if a in available_accounts), None)
                else:
                    account = None
                if not account_required or account is not None:
                    break
                skipped.add(worker)
            if worker is None:
                break
            if not sharing and account_required:
                available_accounts.remove(account)
            chosen_ids.add(worker)
            claim = None
            launch_attempted = False
            launch_accepted = False
            try:
                try:
                    claim = registry.claim_cycle(worker)
                except goal_terminal.GoalSealedError:
                    return {"started": started, "reason": "master_complete", **notes}
                directory = worker_store.worker_dir(root, worker)
                directory.mkdir(parents=True, exist_ok=True)
                if account and previous_account and account != previous_account:
                    worker_routes._rebind_idle_worker_home(root, worker, str(previous_account), account)
                env = worker_launch._task_scoped_process_env(root)
                if account:
                    home = worker_routes._pinned_codex_home(root, worker, account)
                    env["THEORYMASTER_WORKER_CODEX_HOME"] = str(home)
                    env["THEORYMASTER_WORKER_PINNED_ACCOUNT"] = account
                wake = woken_by_id.get(worker)
                input_wake = (wake or {}).get("input_support_wake")
                if wake is not None:
                    retry_path = worker_direction._retry_direction_path(root, worker)
                    retry = worker_direction._pending_retry_direction(root, worker)
                    if retry is not None:
                        multi_worker._save_json(retry_path, {**retry, "status": "consumed",
                                                "consumed_at": multi_worker.utcnow().isoformat(),
                                                "consumed_by_claim_id": claim["claim_id"]})
                worker_store.save_worker(root, worker, status="RESERVED", dismissed=False,
                            target_h_id=None, entry_node_id=None,
                            claim_id=claim["claim_id"], epoch=claim["fencing_epoch"],
                            account=account, current_cycle=None,
                            pid=None, pid_start_ticks=None,
                            resource_slots_configured=resource_slots,
                            error_kind=None, critical_knowledge_notice=None, heartbeat_at=None,
                            wake_fingerprint=None, waiting_since=None,
                            input_support_admission=({**input_wake, "claim_id": claim["claim_id"],
                                "epoch": claim["fencing_epoch"]} if input_wake else None),
                            last_wake_reason=(wake or {}).get("wake_reason"))
                output = open(directory / "runner.log", "a", encoding="utf-8")
                try:
                    args = [sys.executable, "-m", "theory_master.worker_runtime", "run-one",
                            "--research-root", str(root), "--worker", worker,
                            "--claim-id", claim["claim_id"], "--epoch", str(claim["fencing_epoch"])]
                    launch_attempted = True
                    worker_launch._launch_detached_service(args, root=root, env=env, output=output, kind=worker)
                    launch_accepted = True
                finally:
                    output.close()
                # The scope runner registers its own real PID. Never overwrite
                # that with the short-lived systemd-run launcher PID.
                if recovery_quota is not None:
                    quota_path, quota, counts = recovery_quota
                    counts[worker] += 1
                    multi_worker._save_json(quota_path, {**quota, "cycles_started_by_worker": counts,
                        "cycles_started": sum(counts.values()),
                        "resume_batch": any(count < spec.max_cycles for count in counts.values())})
                current = worker_store.read_worker(root, worker)
                if current.get("status") == "RESERVED":
                    worker_store.save_worker(root, worker, status="STARTING", pid=None, pid_start_ticks=None)
                started.append({"worker_id": worker, "claim_id": claim["claim_id"],
                                "epoch": claim["fencing_epoch"],
                                "pid": worker_store.read_worker(root, worker).get("pid"), "account": account,
                                "wake_reason": (wake or {}).get("wake_reason")})
            except BaseException:
                # If no child was created, safely undo the reservation. Once
                # Popen succeeded the OS child may be doing science and must
                # retain the claim even if our registry update fails.
                current = worker_store.read_worker(root, worker)
                if claim is not None and not launch_accepted and (not launch_attempted or current.get("pid") is None):
                    try:
                        registry.update(worker, claim["claim_id"], claim["fencing_epoch"], action="release")
                        if current.get("claim_id") == claim["claim_id"]:
                            worker_store.save_worker(root, worker, status="FAILED_START", claim_id=None,
                                                     epoch=None, pid=None, pid_start_ticks=None)
                    except (OSError, ValueError):
                        pass
                raise
        result = {"started": started, "concurrency": concurrency, "resource_slots": capacity,
                  "waiting_workers": sorted(dormant_ids), **notes}
        if dormant and not active and not started:
            result["all_waiting_notice"] = worker_direction._notify_all_waiting(root, dormant, direction)
        return result


def scheduler_status(root: Path) -> dict[str, Any]:
    from . import multi_worker, runtime_manager
    path = Path(root).resolve() / ".theory-master" / "worker_scheduler.json"
    info = multi_worker._read_json(path)
    alive = runtime_manager.process_identity_alive(info.get("pid"), info.get("start_ticks"))
    if str(info.get("status") or "").upper() == "RUNNING" and not alive:
        # A dead PID cannot remain presented as a live scheduler. Preserve the
        # previous identity for diagnosis and fail closed rather than auto-restart.
        info = {**info, "status": "FAILED", "last_pid": info.get("pid"),
                "last_start_ticks": info.get("start_ticks"), "pid": None,
                "start_ticks": None, "ended_at": multi_worker.utcnow().isoformat(),
                "error_kind": "SchedulerProcessExited",
                "error_detail": "Scheduler metadata said RUNNING but its PID identity is not alive",
                "runtime_source_provenance": info.get("runtime_source_provenance") or runtime_manager.runtime_source_provenance()}
        multi_worker._save_json(path, info)
    return {**info, "process_alive": alive}


def _validate_scheduler_skills(spec: Any) -> None:
    """Fail before launch on mixed SDK sources or missing system/task Skills."""
    from . import runtime_manager
    provenance = runtime_manager.runtime_source_provenance()
    if not provenance.get("source_coherent", True):
        details = "; ".join(provenance.get("source_coherence_errors") or [])
        raise RuntimeError(f"Mixed TheoryMaster source roots detected: {details[:700]}")
    from .skills import ROLE_SKILLS, role_skills
    config_path = Path(spec.config_path).expanduser().resolve()
    for role in ROLE_SKILLS:
        role_skills(role, task_config_path=config_path)


def _record_scheduler_start_failure(root: Path, exc: Exception) -> None:
    from . import multi_worker, runtime_manager
    path = Path(root).resolve() / ".theory-master" / "worker_scheduler.json"
    previous = multi_worker._read_json(path)
    multi_worker._save_json(path, {**previous, "status": "FAILED", "pid": None,
        "start_ticks": None, "ended_at": multi_worker.utcnow().isoformat(),
        "error_kind": type(exc).__name__, "error_detail": str(exc)[:1000],
        "runtime_source_provenance": runtime_manager.runtime_source_provenance()})


def start_scheduler(spec: Any, slots: int, *, preferred_hypothesis: str | None = None) -> dict[str, Any]:
    """Explicit operator launcher, refuses coexistence with old Runner."""
    from . import runtime_manager, worker_launch, worker_routes
    if slots < 1:
        raise ValueError("scheduler needs at least one verified resource slot")
    from .worker_mode import read_mode
    configured = read_mode(spec.research_root)
    if configured["configured"]:
        if not configured["enabled"]:
            raise ValueError("multi-Agent mode is OFF for this task")
        slots = configured["desired_agents"]
    root = Path(spec.research_root).resolve()
    if runtime_manager.runtime_health(root).get("active"):
        raise ValueError("legacy Master runtime is still running; safe cutover required")
    if scheduler_status(root).get("process_alive"):
        return {"status": "already_running", **scheduler_status(root)}
    try:
        _validate_scheduler_skills(spec)
    except Exception as exc:
        _record_scheduler_start_failure(root, exc)
        raise
    # Catch the wrong Python environment before the scheduler claims a science
    # frontier: the child inherits this interpreter and needs the Codex SDK.
    import importlib.util
    from .backend_config import ACCOUNT_PROVIDERS
    from .backends import resolve_routes
    try:
        routes = resolve_routes(task_route_override_root=spec.research_root)
        if (any(route.provider in ACCOUNT_PROVIDERS for route in routes.values())
                and importlib.util.find_spec("openai_codex") is None):
            raise RuntimeError("Codex SDK is missing from the launcher Python; use the TheoryMaster SDK environment")
        _capacity, _accounts = worker_routes._route_capacity(spec, slots)  # validate before detached work
        env = worker_launch._task_scoped_process_env(root)
    except Exception as exc:
        _record_scheduler_start_failure(root, exc)
        raise
    path = root / ".theory-master" / "worker_scheduler.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as log:
        cmd = [sys.executable, "-m", "theory_master.worker_runtime", "daemon",
               "--research-root", str(root), "--resource-slots", str(slots)]
        if preferred_hypothesis:
            cmd += ["--preferred-hypothesis", preferred_hypothesis]
        try:
            worker_launch._launch_detached_service(cmd, root=root, env=env, output=log, kind="scheduler")
        except Exception as exc:
            _record_scheduler_start_failure(root, exc)
            raise
    actual = scheduler_status(root)
    return {"status": "starting", "pid": actual.get("pid") if actual["process_alive"] else None,
            "resource_slots": slots}


def scheduler_worker_ids(root: Path, desired: int) -> tuple[str, ...]:
    from . import worker_store
    workers = worker_store.list_workers(root)
    names = [w["worker_id"] for w in workers if not w.get("dismissed")]
    next_id = max([int(w["worker_id"].split("-")[1]) for w in workers] + [0]) + 1
    while len(names) < desired:
        names.append(f"worker-{next_id}")
        next_id += 1
    return tuple(names)


def _acquire_scheduler_owner(root: Path, lock: Any) -> bool:
    """Give an explicitly started Scheduler time to outwait a Worker SPV tick."""
    from . import multi_worker, runtime_manager
    deadline = time.monotonic() + 5
    while True:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            owner = multi_worker._read_json(root / ".theory-master" / "worker_supervisor.json")
            if (not runtime_manager.process_identity_alive(owner.get("pid"), owner.get("start_ticks"))
                    or time.monotonic() >= deadline):
                return False
            time.sleep(0.05)
        else:
            return True


def scheduler_daemon(spec: Any, slots: int, preferred: str | None = None) -> int:
    """Poll Canvas at Cycle boundaries, assigning new independent Worker processes."""
    from . import multi_worker, runtime_manager, worker_preservation, worker_quota, worker_state, worker_store
    root = Path(spec.research_root).resolve()
    lock_file = root / ".theory-master" / "scheduler_owner.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_file, "a+", encoding="utf-8") as lock:
        if not _acquire_scheduler_owner(root, lock):
            return 0  # Another daemon already owns the pool
        metadata_path = root / ".theory-master" / "worker_scheduler.json"
        source_provenance = runtime_manager.runtime_source_provenance()
        from .worker_mode import read_mode
        mode = read_mode(root)
        worker_count = mode["desired_agents"] if mode["configured"] else slots
        worker_ids = scheduler_worker_ids(root, worker_count)
        previous = multi_worker._read_json(metadata_path)
        max_cycles = int(spec.max_cycles)
        # A stopped, not-yet-exhausted batch is safe to resume without
        # resetting already consumed Worker allocations.
        previous_max = previous.get("max_cycles_per_worker")
        # Increasing a per-Worker batch limit must preserve already consumed
        # allocations; never reset historical counts just because the cap grew.
        resume = (previous.get("quota_mode") == "per_worker" and
                  previous.get("status") in {"STOPPED", "FAILED", "STALLED", "RUNNING"} and
                  isinstance(previous_max, int) and 0 < previous_max <= max_cycles and
                  previous.get("resume_batch") is True)
        raw_counts = previous.get("cycles_started_by_worker") if resume else {}
        raw_counts = raw_counts if isinstance(raw_counts, dict) else {}
        counts = {name: min(max_cycles, max(0, int(raw_counts.get(name, 0))))
                  for name in worker_ids}
        def save_scheduler(status: str, **extra: Any) -> None:
            multi_worker._save_json(metadata_path, {
                "pid": os.getpid(), "start_ticks": runtime_manager.process_start_ticks(os.getpid()),
                "status": status, "quota_mode": "per_worker",
                "cycles_started": sum(counts.values()),
                "cycles_started_by_worker": dict(counts),
                "max_cycles_per_worker": max_cycles,
                "worker_ids": list(worker_ids), "resource_slots": slots,
                "resume_batch": any(n < max_cycles for n in counts.values()),
                "runtime_source_provenance": source_provenance,
                **({"preferred_hypothesis_ignored": preferred,
                    "preferred_hypothesis_deprecation": "Preferred no longer influences scheduling (V1.1 I-12)"}
                   if preferred else {}),
                **extra})
        save_scheduler("RUNNING", started_at=multi_worker.utcnow().isoformat())
        no_progress_since: float | None = None
        stopping = threading.Event()
        def handle_signal(_signum: int, _frame: Any) -> None:
            stopping.set()
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)
        stop_reason = "stop_requested"
        while not stopping.is_set():
            if worker_store._master_complete(root):
                stop_reason = "goal_completed"
                break
            from .worker_mode import read_mode
            mode = read_mode(root)
            if mode["configured"] and not mode["enabled"]:
                stop_reason = "multi_agent_mode_off"
                break
            desired = mode["desired_agents"] if mode["configured"] else slots
            from .specification import TaskSpec
            fresh = TaskSpec.from_task_root(root)
            # A Worker gets max_cycles of its OWN new allocations per batch.
            # Completed/failed cycles consume a slot once they were started;
            # resumptions are routed by dispatch_pending and consume no slot.
            worker_ids = scheduler_worker_ids(root, desired)
            for name in worker_ids:
                counts.setdefault(name, 0)
            eligible = frozenset(name for name in worker_ids
                                 if counts[name] < max_cycles
                                 and not worker_store.read_worker(root, name).get("close_requested"))
            try:
                recovered = _recover_proposal_gate(root)
                if recovered is not None:
                    save_scheduler("RUNNING", last_proposal_gate_recovery={
                        **recovered, "recovered_at": multi_worker.utcnow().isoformat()})
                repaired_watches = worker_quota._repair_missing_quota_watchers(fresh)
                if repaired_watches:
                    save_scheduler("RUNNING", last_quota_watch_repairs=repaired_watches,
                                   last_quota_watch_repair_at=multi_worker.utcnow().isoformat())
                orphaned_preservations = worker_preservation._flag_orphaned_preservation_jobs(fresh)
                if orphaned_preservations:
                    save_scheduler("RUNNING",
                                   preservation_orphan_alerts=orphaned_preservations,
                                   preservation_orphan_alert_at=multi_worker.utcnow().isoformat())
                # A budget stop is not a runtime FAILED incident. Only an
                # exact SDK budget marker may enter the guarded Preserve path.
                from .budget_auto_preservation import dispatch_auto_budget_preservation
                if dispatch_auto_budget_preservation(fresh):
                    no_progress_since = None
                    stopping.wait(8)
                    continue
                if worker_quota._dispatch_exhausted_budget_reviewer_transport(fresh):
                    no_progress_since = None
                    stopping.wait(8)
                    continue
                from .worker_supervisor_dispatch import dispatch_pending
                if dispatch_pending(root):
                    no_progress_since = None
                    stopping.wait(8)
                    continue
                if not eligible:
                    stop_reason = "cycle_budget_exhausted"
                    break
                outcome = schedule_once(fresh, resource_slots=desired,
                                        eligible_worker_ids=eligible)
                started = outcome.get("started", ())
                for worker in started:
                    name = worker["worker_id"]
                    if name not in eligible:
                        raise RuntimeError("scheduler allocated a Worker beyond its Cycle quota")
                    counts[name] += 1
                if started:
                    no_progress_since = None
                    save_scheduler("RUNNING", last_progress_at=multi_worker.utcnow().isoformat(),
                                   last_started_workers=[w.get("worker_id") for w in started])
                else:
                    # During account migration the old PID may be gone before
                    # the replacement registers. Durable claims also reserve
                    # capacity for a paused/recovering Worker.
                    live = [w for w in worker_store.list_workers(root) if w.get("process_alive")]
                    claimed = worker_store._living_workers(root)
                    manual_recovery = [
                        worker for worker in claimed
                        if not worker.get("process_alive")
                        and str(worker.get("status") or "").upper()
                            in {"FAILED", "NEEDS_ATTENTION", "INTERRUPTED"}
                    ]
                    if manual_recovery:
                        # Keep the claim and slot reserved, but do not report a
                        # dead failed Worker as if it were actively progressing.
                        # Operator action is required; never auto-release or
                        # silently replay a frozen scientific Cycle.
                        no_progress_since = None
                        details = ", ".join(
                            f"{worker.get('worker_id') or 'unknown'} "
                            f"{str(worker.get('status') or 'UNKNOWN').upper()}"
                            + (f" Cycle {worker.get('current_cycle')}"
                               if worker.get('current_cycle') else "")
                            for worker in manual_recovery
                        )
                        save_scheduler(
                            "RUNNING",
                            last_no_progress_at=multi_worker.utcnow().isoformat(),
                            last_no_progress_reason=(
                                f"workers_require_manual_recovery: {details}; "
                                "claim retained; explicit Resume/Release required"
                            )[:500],
                            workers_requiring_manual_recovery=[
                                {
                                    "worker_id": str(worker.get("worker_id") or ""),
                                    "status": str(worker.get("status") or "").upper(),
                                    "cycle": str(worker.get("current_cycle") or ""),
                                    "error_kind": str(worker.get("error_kind") or "")[:120],
                                }
                                for worker in manual_recovery
                            ],
                        )
                    elif live or claimed:
                        no_progress_since = None
                        save_scheduler("RUNNING", last_no_progress_at=multi_worker.utcnow().isoformat(),
                                       last_no_progress_reason="workers_active_or_claimed",
                                       workers_requiring_manual_recovery=[])
                    elif outcome.get("waiting_workers") and not outcome.get("reason"):
                        # G6: dormant Workers wake only on a changed direction
                        # signal or explicit retry_direction; no model calls.
                        no_progress_since = None
                        save_scheduler("RUNNING", last_no_progress_at=multi_worker.utcnow().isoformat(),
                                       last_no_progress_reason="awaiting_research_direction",
                                       waiting_workers=list(outcome["waiting_workers"]),
                                       workers_requiring_manual_recovery=[],
                                       remaining_cycles_by_worker={
                                           name: max(0, max_cycles-count) for name, count in counts.items()})
                    elif outcome.get("eligible") == 0 and not outcome.get("reason"):
                        # No active research frontier is not Goal Completion,
                        # nor grounds to silently discard the remaining quota.
                        # Poll until a frontier is made eligible or stopped.
                        no_progress_since = None
                        save_scheduler("RUNNING", last_no_progress_at=multi_worker.utcnow().isoformat(),
                                       last_no_progress_reason="awaiting_eligible_frontier",
                                       workers_requiring_manual_recovery=[],
                                       remaining_cycles_by_worker={
                                           name: max(0, max_cycles-count) for name, count in counts.items()})
                    else:
                        # A single empty PID snapshot must not terminate the
                        # batch: account failover can span several polls.
                        now = time.monotonic()
                        if no_progress_since is None:
                            no_progress_since = now
                        reason = outcome.get("reason") or "no_worker_started_and_no_live_worker"
                        remaining = {name: max(0, max_cycles-count) for name, count in counts.items()}
                        no_progress_seconds = max(0.0, now - no_progress_since)
                        if no_progress_seconds < worker_state.SCHEDULER_NO_PROGRESS_GRACE_SECONDS:
                            save_scheduler("RUNNING", last_no_progress_at=multi_worker.utcnow().isoformat(),
                                           last_no_progress_reason=str(reason)[:500],
                                           workers_requiring_manual_recovery=[],
                                           no_progress_seconds=no_progress_seconds,
                                           remaining_cycles_by_worker=remaining)
                        else:
                            save_scheduler("STALLED", ended_at=multi_worker.utcnow().isoformat(),
                                           stall_reason=str(reason)[:500],
                                           workers_requiring_manual_recovery=[],
                                           no_progress_seconds=no_progress_seconds,
                                           remaining_cycles_by_worker=remaining)
                            return 2
            except Exception as exc:
                save_scheduler("FAILED", error_kind=type(exc).__name__,
                               error_detail=str(exc)[:500])
                return 1
            stopping.wait(8)
        save_scheduler("STOPPED", ended_at=multi_worker.utcnow().isoformat(), stop_reason=stop_reason)
    return 0


def stop_scheduler(spec: Any) -> dict[str, Any]:
    """Stop allocation only; existing Worker Cycles continue unaffected."""
    status = scheduler_status(spec.research_root)
    if status["process_alive"]:
        os.kill(int(status["pid"]), signal.SIGTERM)
    return {"status": "stop_requested" if status["process_alive"] else "not_running",
            "existing_worker_cycles_unaffected": True}
