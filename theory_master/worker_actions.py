"""Operator-authorized Worker lifecycle actions."""
from __future__ import annotations
import fcntl
import sys
from pathlib import Path
from typing import Any


def worker_action(
    spec: Any, worker_id: str, action: str, *, account: str | None = None,
    provider_fallback: str | None = None,
) -> dict[str, Any]:
    """Exact Worker-specific checkpoint control; never use Master-wide kill."""
    from . import goal_terminal, multi_worker, runtime_manager, worker_direction, worker_launch, worker_preservation, worker_quota, worker_routes, worker_state, worker_store
    if action == "close":
        if account is not None or provider_fallback is not None:
            raise ValueError("close does not accept account/provider migration")
        from .worker_close import close_worker
        return close_worker(spec, worker_id)
    if action == "preserve_release":
        if account is not None or provider_fallback is not None:
            raise ValueError("preservation does not accept account/provider migration")
        return worker_preservation.request_preserve_release(spec, worker_id)
    if action not in {"pause", "kill", "resume", "release", "dismiss", "retry_direction"}:
        raise ValueError("unknown Worker action")
    if provider_fallback not in {None, "Codex"} or (provider_fallback and action != "resume"):
        raise ValueError("provider fallback is valid only for a bounded Worker resume")
    root = Path(spec.research_root).resolve()
    scheduler_lock = root / ".theory-master" / "scheduler.lock"
    scheduler_lock.parent.mkdir(parents=True, exist_ok=True)
    with open(scheduler_lock, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        worker_store._ensure_legacy_claims_migrated(root)
        claims = multi_worker.CycleClaims(root, worker_store._graph_path(spec))
        w = worker_store.read_worker(root, worker_id)
        if not w or w.get("schema") != worker_state.SCHEMA:
            raise ValueError("Worker does not exist")
        if w.get("close_requested") or w.get("dismissed"):
            raise ValueError("Worker is closing or closed; automatic resume is disabled")
        if action == "retry_direction":
            if w.get("status") != worker_state.WAITING_RESEARCH_DIRECTION:
                raise ValueError("retry_direction applies only to a Worker waiting for research direction")
            if runtime_manager.process_identity_alive(w.get("pid"), w.get("pid_start_ticks")):
                raise ValueError("Worker is still live; wait for it to finish")
            request = {"schema": worker_state.RETRY_DIRECTION_SCHEMA, "status": "pending",
                       "worker_id": worker_id, "requested_at": multi_worker.utcnow().isoformat(),
                       "source": "operator",
                       "waiting_cycle": w.get("current_cycle"),
                       "wake_fingerprint_at_request": w.get("wake_fingerprint")}
            multi_worker._save_json(worker_direction._retry_direction_path(root, worker_id), request)
            return {"worker_id": worker_id, "action": action, "status": "RETRY_REQUESTED",
                    "effective_at": "next_scheduler_tick"}
        if action == "dismiss":
            if worker_id == "worker-1":
                raise ValueError("Agent 1 is the permanent primary Agent")
            if w.get("dismissed"):
                raise ValueError("Worker already hidden")
            if sum(not row.get("dismissed") for row in worker_store.list_workers(root)) <= 1:
                raise ValueError("Keep at least one visible Worker")
            if runtime_manager.process_identity_alive(w.get("pid"), w.get("pid_start_ticks")):
                raise ValueError("Cannot hide an active Worker; pause and release it first")
            if w.get("status") not in {"OVER", "COMPLETE", "RELEASED", "STOPPED", "CANCELLED",
                                       "FAILED_START", *worker_state.NON_INCIDENT_STOPS}:
                raise ValueError("Worker must finish or be released before hiding")
            # A terminal runtime label alone cannot prove that a Cycle claim
            # was released. A stranded claim must remain operable. Hold the
            # claim registry fence during the read and card change.
            with claims._locked() as claim_registry:
                owned_claims = (claim_registry.get("claims") or {}).values()
                if any(isinstance(claim, dict) and claim.get("worker_id") == worker_id
                       for claim in owned_claims):
                    raise ValueError("Worker still owns a research claim; release it first")
                worker_store.save_worker(root, worker_id, dismissed=True)
                from .worker_mode import reduce_one_worker_request
                visible = sum(not row.get("dismissed") for row in worker_store.list_workers(root))
                capacity = reduce_one_worker_request(root, registered_count=visible,
                                                     require_pending=False)
            return {"worker_id": worker_id, "action": action, "status": "hidden",
                    "history_preserved": True, **capacity}
        if action == "pause":
            if not runtime_manager.process_identity_alive(w.get("pid"), w.get("pid_start_ticks")):
                raise ValueError("Worker is not live; use resume or release")
            cycle = w.get("current_cycle")
            if not cycle or not str(cycle).isdigit():
                raise ValueError("Worker has no assigned Cycle yet; try at next safe boundary")
            path = root / cycle / "recovery_control.json"
            if path.exists() or path.is_symlink():
                raise ValueError("Worker already has a pending control action")
            multi_worker._save_json(path, {"action": "pause", "reason": "multi_worker_operator",
                              "requested_at": multi_worker.utcnow().isoformat()})
            worker_store.save_worker(root, worker_id, status="PAUSE_REQUESTED")
            return {"worker_id": worker_id, "action": action, "status": "PAUSE_REQUESTED"}
        if action == "kill":
            if w.get("status") != "RUNNING" or not runtime_manager.process_identity_alive(w.get("pid"), w.get("pid_start_ticks")):
                raise ValueError("Worker is not a verified RUNNING process")
            cycle = str(w.get("current_cycle") or "")
            if not cycle.isascii() or not cycle.isdigit() or int(cycle) < 1:
                raise ValueError("Worker has no valid Cycle to control")
            state = multi_worker._read_json(root / cycle / "research_state.json")
            if (not worker_store._checkpoint_matches_claim(state, worker_id, worker_store._worker_live_claim(claims, w), cycle)
                    or str(state.get("status") or "").lower() not in {"active", "running"}
                    or state.get("execution_active") is not True):
                raise ValueError("Worker/Cycle identity or execution state changed")
            if (state.get("sdk_runtime_capabilities") or {}).get("abandon_cycle_v1") is not True:
                raise ValueError("Worker Cycle lacks safe kill checkpoint capability")
            path = root / cycle / "recovery_control.json"
            if path.exists() or path.is_symlink():
                raise ValueError("Worker already has a pending control action")
            # The Worker consumes this at a scientific safe boundary.
            # Never signal a process or mutate another Worker/Master Cycle.
            multi_worker._save_json(path, {"action": "abandon_cycle", "source_action": action,
                              "reason": "multi_worker_operator",
                              "requested_at": multi_worker.utcnow().isoformat()})
            worker_store.save_worker(root, worker_id, status="STOP_REQUESTED")
            return {"worker_id": worker_id, "action": action, "status": "STOP_REQUESTED"}
        if runtime_manager.process_identity_alive(w.get("pid"), w.get("pid_start_ticks")):
            raise ValueError("Worker is still live; wait for it to finish")
        if action == "release":
            if not w.get("claim_id") and w.get("status") in worker_state.NON_INCIDENT_STOPS:
                # Waiting/goal-stopped Workers already archived their Cycle claim.
                worker_store.save_worker(root, worker_id, status="RELEASED", pid=None, pid_start_ticks=None,
                            wake_fingerprint=None, waiting_since=None)
                return {"worker_id": worker_id, "action": action, "status": "RELEASED",
                        "cycle_reconciliation": None}
            if not w.get("claim_id") or w.get("epoch") is None:
                raise ValueError("Worker has no Cycle claim to release")
            token = (w["claim_id"], int(w["epoch"]))
            reconciled = worker_store.reconcile_released_worker_cycle(root, w)
            claims.update(worker_id, *token, action="release")
            worker_store.save_worker(root, worker_id, status="RELEASED", claim_id=None, epoch=None,
                        pid=None, pid_start_ticks=None)
            return {"worker_id": worker_id, "action": action, "status": "RELEASED",
                    "cycle_reconciliation": reconciled}
        if not w.get("claim_id") or w.get("epoch") is None:
            raise ValueError("Worker has no Cycle claim to resume")
        token = (w["claim_id"], int(w["epoch"]))
        if action == "resume" and w.get("status") in worker_state.RUNNING:
            # Explicit orphan recovery, not a silent scheduler repair. Preserve
            # the exact unfinished Cycle and its frozen science for SDK resume.
            cycle_id = str(w.get("current_cycle") or "")
            if not cycle_id.isdecimal() or not cycle_id or int(cycle_id) < 1:
                raise ValueError("orphaned Worker lacks a valid Cycle")
            cdir = root / cycle_id
            if not cdir.is_dir() or cdir.is_symlink():
                raise ValueError("orphaned Worker Cycle path is unsafe")
            state_file, run_file = cdir / "research_state.json", cdir / "run_state.json"
            if state_file.is_symlink() or run_file.is_symlink():
                raise ValueError("orphaned Worker checkpoint is unsafe")
            state, run = multi_worker._read_json(state_file), multi_worker._read_json(run_file)
            if (not worker_store._checkpoint_matches_claim(state, worker_id, worker_store._worker_live_claim(claims, w), cycle_id)
                    or str(state.get("status") or "").lower() not in {"active", "running"}
                    or int(state.get("owner_pid") or 0) != int(w.get("pid") or 0)
                    or str(run.get("status") or "").upper() != "RUNNING"
                    or run.get("execution_active") is not True):
                raise ValueError("orphaned Worker checkpoint does not match its frozen Cycle")
            if runtime_manager.process_identity_alive(state.get("owner_pid"), w.get("pid_start_ticks")):
                raise ValueError("old scientific Worker is still alive")
            claims.update(worker_id, *token, action="pause")
            for path, data, backup in (
                (state_file, state, "research_state.before-orphan-resume.json"),
                (run_file, run, "run_state.before-orphan-resume.json"),
            ):
                backup_path = cdir / backup
                if backup_path.is_symlink():
                    raise ValueError("unsafe orphan recovery backup")
                if not backup_path.exists():
                    multi_worker._save_json(backup_path, data)
                data["status"] = "interrupted" if path == state_file else "INTERRUPTED"
                data["phase"] = "interrupted"
                data["execution_active"] = False
                data["resumable"] = True
                if path == state_file:
                    data["runtime_state"] = "interrupted"
                    data["recovery"] = {"kind": "orphaned_process",
                                        "reason": "old Worker PID identity absent; explicit Resume",
                                        "at": multi_worker.utcnow().isoformat()}
                multi_worker._save_json(path, data)
            worker_store.save_worker(root, worker_id, status="INTERRUPTED", pid=None,
                        pid_start_ticks=None, heartbeat_at=None)
            w = worker_store.read_worker(root, worker_id)
        if w.get("status") not in worker_state.RESUMABLE:
            raise ValueError("only an interrupted Worker can resume its frozen Cycle")
        cycle = w.get("current_cycle")
        # A startup/preflight failure may precede Workflow._bind_cycle. Recover
        # only a FAILED Worker's lost display pointer from its exact fenced
        # claim and a matching resumable scientific checkpoint.
        claim_record = worker_store._worker_live_claim(claims, w)
        if not cycle or not str(cycle).isascii() or not str(cycle).isdigit():
            cycle = worker_store._recover_failed_cycle_pointer(root, w, claim_record)
            if cycle is None:
                raise ValueError("cannot recover a Worker without Cycle identity")
            worker_store.save_worker(root, worker_id, current_cycle=cycle)
        # I-10: resume keeps the exact Cycle/H; only a live claim for THIS
        # Cycle may be reissued, never a stale or foreign fencing token.
        if claim_record is None:
            raise ValueError("paused scientific Cycle claim is stale or missing")
        old = multi_worker._read_json(root / cycle / "research_state.json")
        if not worker_store._checkpoint_matches_claim(old, worker_id, claim_record, cycle):
            raise ValueError("Worker and scientific Cycle identity mismatch")
        if old.get("abandoned_cycle") or old.get("phase") == "abandon_requested":
            raise ValueError("Cycle abandonment forbids Worker Resume")
        from .resume_preparation import snapshot_resume_control, consume_resume_control
        pending_resume_control = snapshot_resume_control(root / str(cycle))
        if (provider_fallback == "Codex" and str(old.get("status") or "").lower() == "paused"
                and (old.get("recovery") or {}).get("reason") == "multi_worker_operator"):
            from .service_provider_switch import selected_service_preference
            from .backend_config import ACCOUNT_PROVIDERS
            from .codex_accounts import account_rate_limits, quota_available
            if selected_service_preference() != "codex":
                raise ValueError("operator Codex resume requires the current Services Codex selection")
            requested = worker_routes.resolve_worker_routes(root, worker_id)
            if not requested or any(route.provider not in ACCOUNT_PROVIDERS for route in requested.values()):
                raise ValueError("operator Codex resume requires a complete Worker Codex profile")
            if str(w.get("account") or "") not in {"1", "2"} or quota_available(
                    account_rate_limits(str(w["account"]))) is not True:
                raise ValueError("operator Codex resume requires verified pinned Codex account quota")
        if account is not None and account != w.get("account"):
            if not worker_routes._share_enabled(root) and any(
                    other["worker_id"] != worker_id and other.get("account") == account
                    for other in worker_store._living_workers(root)):
                raise ValueError("target account is occupied and sharing is disabled")
            if sum(other["worker_id"] != worker_id and other.get("account") == account
                   for other in worker_store._living_workers(root)) >= worker_state.MAX_SHARED_ACCOUNT_WORKERS:
                raise ValueError("target account has reached shared Worker limit")
            from .codex_accounts import account_rate_limits, quota_available
            if quota_available(account_rate_limits(account)) is not True:
                raise ValueError("target account quota is unavailable or unverified")
            migration = worker_quota._migrate_paused_worker_account(root, w, account)
            w = worker_store.read_worker(root, worker_id)
        else:
            migration = None
        # The user requested Resume, but neither Codex account can accept
        # a Turn. Preserve this exact Cycle and wait for quota instead of
        # launching a doomed process or raising an unrelated Supervisor fault.
        if (action == "resume"
                and str(old.get("status") or "").lower() == "paused"
                and ((old.get("recovery") or {}).get("reason") == "multi_worker_operator"
                     or old.get("phase") == "provider_quota_exhausted")):
            resets = (worker_quota._verified_codex_quota_wait()
                      or worker_quota._verified_pinned_codex_quota_wait(str(w.get("account") or "")))
            if resets and worker_quota._persist_verified_quota_wait(
                    root, worker_id, str(cycle), claim_record, resets, "operator-resume-awaits-quota"):
                worker_store.save_worker(root, worker_id, status="PAUSED", error_kind=None)
                try:
                    watcher = worker_quota.start_worker_quota_watch(spec, worker_id)
                except (OSError, RuntimeError, ValueError) as error:
                    watcher = {"status": "launch_failed", "reason": type(error).__name__}
                return {"worker_id": worker_id, "action": action, "status": "PAUSED",
                        "quota_waiting": True, "quota_resets_at": resets, "watcher": watcher,
                        "cycle": str(cycle), "hypothesis_id": claim_record.get("hypothesis_id")}
        peers = [other for other in worker_store._living_workers(root)
                 if other["worker_id"] != worker_id and other.get("account") == w.get("account")]
        if w.get("account") and peers and not worker_routes._share_enabled(root):
            raise ValueError("pinned Codex account is occupied; resume after capacity is free")
        if w.get("account") and len(peers) >= worker_state.MAX_SHARED_ACCOUNT_WORKERS:
            raise ValueError("shared Codex account concurrency limit reached")
        try:
            refreshed = claims.reissue_for_resume(worker_id, *token)
        except goal_terminal.GoalSealedError as exc:
            raise ValueError(f"Goal already completed; Cycle {cycle} is not resumed: {exc}") from exc
        if refreshed.get("cycle_id") != claim_record.get("cycle_id") or \
                refreshed.get("hypothesis_id") != claim_record.get("hypothesis_id"):
            raise ValueError("reissued Cycle claim changed Cycle/H identity; manual recovery required")
        token = (refreshed["claim_id"], refreshed["fencing_epoch"])
        consumed_control = consume_resume_control(root / str(cycle), pending_resume_control,
            authorization={"action": "resume", "worker_id": worker_id, "cycle_id": str(cycle),
                           "claim_id": token[0], "fencing_epoch": token[1]})
        worker_store.save_worker(root, worker_id, claim_id=token[0], epoch=token[1], pid=None, pid_start_ticks=None)
        env = worker_launch._task_scoped_process_env(root)
        env.pop("THEORYMASTER_QUOTA_PROVIDER_FALLBACK", None)
        if provider_fallback:
            env["THEORYMASTER_QUOTA_PROVIDER_FALLBACK"] = provider_fallback
        if w.get("account"):
            home = worker_routes._pinned_codex_home(root, worker_id, w["account"])
            env["THEORYMASTER_WORKER_CODEX_HOME"] = str(home)
            env["THEORYMASTER_WORKER_PINNED_ACCOUNT"] = str(w["account"])
        directory = worker_store.worker_dir(root, worker_id)
        with open(directory / "runner.log", "a", encoding="utf-8") as output:
            worker_launch._launch_detached_service([
                sys.executable, "-m", "theory_master.worker_runtime", "run-one",
                "--research-root", str(root), "--worker", worker_id,
                "--claim-id", token[0], "--epoch", str(token[1]),
                "--resume-cycle", str(cycle)], root=root, env=env,
                output=output, kind=worker_id)
        active_record = worker_store.read_worker(root, worker_id)
        if active_record.get("status") == "PAUSED":
            worker_store.save_worker(root, worker_id, status="STARTING", pid=None, pid_start_ticks=None)
        return {"worker_id": worker_id, "action": action, "status": "STARTING",
                "pid": worker_store.read_worker(root, worker_id).get("pid"),
                "account": w.get("account"), "migration": migration,
                "cycle": str(cycle), "hypothesis_id": refreshed.get("hypothesis_id"),
                "claim_id": token[0], "epoch": token[1],
                "superseded_control": consumed_control}
