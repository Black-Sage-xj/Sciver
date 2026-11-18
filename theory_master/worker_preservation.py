"""Evidence preservation and explicit frontier release jobs."""
from __future__ import annotations
import fcntl
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _flag_orphaned_preservation_jobs(spec: Any) -> list[dict[str, str]]:
    """Report, but never replay/release, a disappeared paid preservation job."""
    from . import multi_worker, runtime_manager, worker_actions, worker_store
    from .observability.incident_registry import register_cycle_incident
    root = Path(spec.research_root).resolve()
    incidents: list[dict[str, str]] = []
    for worker in worker_store.list_workers(root):
        if worker.get("status") != "PRESERVING":
            continue
        if (worker.get("process_alive")
                or runtime_manager.process_identity_alive(worker.get("preservation_job_pid"),
                                          worker.get("preservation_job_start_ticks"))):
            continue
        wid = str(worker.get("worker_id") or "")
        cycle = str(worker.get("current_cycle") or "")
        if (not re.fullmatch(r"worker-[1-9][0-9]*", wid)
                or not cycle.isascii() or not cycle.isdecimal()
                or int(cycle) < 1):
            continue
        # A launcher may have registered only Worker status so far. Do not
        # declare it orphaned while the process is still being established.
        try:
            updated = datetime.fromisoformat(
                str(worker.get("updated_at") or "").replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (ValueError, AttributeError, OverflowError):
            continue
        if (datetime.now(timezone.utc) - updated).total_seconds() < 300:
            continue
        cycle_dir = root / cycle
        if not cycle_dir.is_dir() or cycle_dir.is_symlink():
            continue
        released_receipt = worker_store.worker_dir(root, wid) / f"preservation_orphaned_release_{cycle}.json"
        if (released_receipt.is_file() and not released_receipt.is_symlink()
                and multi_worker._read_json(released_receipt).get("status") == "verified_released"):
            continue  # durable success, regardless of a stale UI projection
        # A preservation model can finish and seal its Cycle before its
        # wrapper dies without releasing the original Worker claim. Reconcile
        # an ALREADY verified closure without re-entering Investigator.
        # Unknown/missing closure never authorizes a paid retry.
        science_path = cycle_dir / "research_state.json"
        closure_path = cycle_dir / "preservation" / "closure.json"
        if (worker.get("preservation_job_pid") and science_path.is_file()
                and not science_path.is_symlink() and closure_path.is_file()
                and not closure_path.is_symlink()):
            science = multi_worker._read_json(science_path)
            closure = multi_worker._read_json(closure_path)
            claim = worker_store._worker_live_claim(multi_worker.CycleClaims(root, worker_store._graph_path(spec)), worker)
            if (claim is not None and worker_store._checkpoint_matches_claim(science, wid, claim, cycle)
                    and science.get("status") == "over"
                    and science.get("closure_reason") == "investigator_preservation"
                    and science.get("scientific_verdict") is None
                    and closure.get("cycle_id") == cycle
                    and closure.get("scientific_verdict") is None):
                from .research_preservation import finalize_preserved_cycle, PreservationGuard
                from .budget_auto_preservation import budget_preservation_notice
                try:
                    verified = finalize_preserved_cycle(
                        cycle_dir=cycle_dir, authorization="operator_approved", dry_run=True)
                    if verified.get("scientific_verdict") is not None:
                        raise PreservationGuard("preserved scientific closure has a verdict")
                    once = worker_store.worker_dir(root, wid) / f"preservation_orphaned_release_{cycle}.json"
                    once.parent.mkdir(parents=True, exist_ok=True)
                    if not once.exists() and not once.is_symlink():
                        descriptor = os.open(once, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                            json.dump({"cycle": cycle, "worker_id": wid,
                                       "status": "requested_once", "at": multi_worker.utcnow().isoformat()}, handle)
                            handle.flush()
                            os.fsync(handle.fileno())
                        # Existing WorkerAction validates the locked claim and
                        # detects the closed authoritative Cycle; no model call.
                        result = worker_actions.worker_action(spec, wid, "preserve_release")
                        if result.get("status") == "RELEASED" and result.get("model_calls") == 0:
                            multi_worker._save_json(once, {"cycle": cycle, "worker_id": wid,
                                              "status": "verified_released",
                                              "model_calls": 0, "at": multi_worker.utcnow().isoformat()})
                            try:
                                budget_preservation_notice(
                                    root, wid, cycle, success=True,
                                    hypothesis_id=str(closure.get("hypothesis_id") or ""))
                            except (OSError, ValueError, RuntimeError) as exc:
                                multi_worker._save_json(worker_store.worker_dir(root, wid) / f"preservation_orphan_notice_warning_{cycle}.json",
                                           {"error_type": type(exc).__name__,
                                            "at": multi_worker.utcnow().isoformat()})
                            incidents.append({"worker_id": wid, "cycle": cycle,
                                              "status": "verified_released"})
                            continue
                except (OSError, ValueError, RuntimeError, PreservationGuard) as exc:
                    multi_worker._save_json(worker_store.worker_dir(root, wid) / f"preservation_orphan_release_error_{cycle}.json",
                               {"cycle": cycle, "worker_id": wid,
                                "error_type": type(exc).__name__,
                                "at": multi_worker.utcnow().isoformat()})
                    # Do not bypass the ordinary orphan incident below.
        receipt = worker_store.worker_dir(root, wid) / f"preservation_orphaned_{cycle}.json"
        if receipt.exists():
            continue
        record = {"schema": "theory-master/preservation-orphan/v1",
                  "cycle": cycle, "worker_id": wid,
                  "status": "manual_reconciliation_required",
                  "at": multi_worker.utcnow().isoformat()}
        # A dead job is NOT approval to repeat a potentially paid model call,
        # restore a checkpoint or free this reserved Cycle Claim.
        multi_worker._save_json(receipt, record)
        register_cycle_incident(
            cycle_dir, severity="error", kind="system_failure",
            source_event="sdk.worker_scheduler",
            code="preservation_worker_orphaned",
            discriminator=f"{wid}:{cycle}",
            summary=(f"{wid} · Cycle {cycle} 保全进程未运行但状态仍为 PRESERVING；"
                     "科研前沿与 Claim 已保留，请核查缓存输出与闭合回执。"),
            fields={"worker_id": wid, "cycle": cycle,
                    "claim_retained": True, "requires_reconciliation": True},
        )
        incidents.append({"worker_id": wid, "cycle": cycle,
                          "status": "manual_reconciliation_required"})
    return incidents


class PreservationPreflightRejected(ValueError):
    """Pre-mutation preservation guard refused; no Worker/Cycle change occurred."""


def request_preserve_release(spec: Any, worker_id: str) -> dict[str, Any]:
    """Queue an approved read-only Investigator archive outside the Web timeout.

    A durable Worker status and independent systemd process keep the original
    claim reserved throughout the model call. No release occurs on failure.
    """
    from . import multi_worker, runtime_manager, worker_launch, worker_routes, worker_store
    from .research_preservation import blocked_checkpoint, PreservationGuard
    root = Path(spec.research_root).resolve()
    gate = root / ".theory-master" / "scheduler.lock"
    gate.parent.mkdir(parents=True, exist_ok=True)
    with gate.open("a+", encoding="utf-8") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        worker = worker_store.read_worker(root, worker_id)
        if worker.get("close_requested") or worker.get("dismissed"):
            raise PreservationPreflightRejected("Worker is closing or closed")
        if worker.get("status") not in {"NEEDS_ATTENTION", "PAUSED", "INTERRUPTED", "PRESERVING"}:
            raise PreservationPreflightRejected("only a stopped Worker with a scientific blocker may be preserved")
        if runtime_manager.process_identity_alive(worker.get("pid"), worker.get("pid_start_ticks")):
            raise PreservationPreflightRejected("live research Worker cannot be archived")
        cycle = str(worker.get("current_cycle") or "")
        if not cycle.isdecimal() or int(cycle) < 1:
            raise PreservationPreflightRejected("Worker has no frozen Cycle")
        if worker.get("status") == "PRESERVING":
            job_pid = worker.get("preservation_job_pid")
            if (not job_pid or runtime_manager.process_identity_alive(
                    job_pid, worker.get("preservation_job_start_ticks"))):
                raise PreservationPreflightRejected("preservation may still be running; refuse a second model call")
        state_path = root / cycle / "research_state.json"
        archived_state = multi_worker._read_json(state_path)
        if str(archived_state.get("status") or "").lower() == "over":
            from .research_preservation import finalize_preserved_cycle
            verified = finalize_preserved_cycle(
                cycle_dir=root / cycle, authorization="operator_approved", dry_run=True)
            if verified.get("scientific_verdict") is not None:
                raise ValueError("archived Cycle unexpectedly has a scientific verdict")
            worker_store._ensure_legacy_claims_migrated(root)
            claims = multi_worker.CycleClaims(root, worker_store._graph_path(spec))
            claim_token = (worker["claim_id"], int(worker["epoch"]))
            reconciled = worker_store.reconcile_released_worker_cycle(root, worker)
            claims.update(worker_id, *claim_token, action="release")
            worker_store.save_worker(root, worker_id, status="RELEASED", claim_id=None, epoch=None,
                        pid=None, pid_start_ticks=None)
            return {"worker_id": worker_id, "status": "RELEASED", "cycle": cycle,
                    "reconciled": reconciled, "reused_verified_preservation": True,
                    "model_calls": 0}
        # The run_state.json returned by blocked_checkpoint is only a Web
        # projection. Worker identity/branch ownership are intentionally stored
        # in authoritative research_state.json, and may be absent from run_state.
        try:
            projected = blocked_checkpoint(root / cycle)
        except PreservationGuard as exc:
            raise PreservationPreflightRejected(
                "frozen scientific Cycle is not eligible for preservation") from exc
        if (archived_state.get("worker_id") != worker_id
                or projected.get("resumable") is not True
                or archived_state.get("resumable") is not True
                or not worker.get("claim_id")):
            raise PreservationPreflightRejected("Worker claim and authoritative frozen Cycle do not match")
        if any(projected.get(key) is not None and projected[key] != archived_state.get(key)
               for key in ("worker_id", "claimed_frontier_id")):
            raise PreservationPreflightRejected("Web projection conflicts with authoritative Worker owner")
        worker_store._ensure_legacy_claims_migrated(root)
        worker = worker_store.read_worker(root, worker_id)
        claim = worker_store._worker_live_claim(multi_worker.CycleClaims(root, worker_store._graph_path(spec)), worker)
        if claim is None or not worker_store._checkpoint_matches_claim(archived_state, worker_id, claim, cycle):
            raise PreservationPreflightRejected("Worker claim fencing changed; archive not authorized")
        original_status = str(worker["status"])
        worker_store.save_worker(root, worker_id, status="PRESERVING",
                    preservation_job_pid=None, preservation_job_start_ticks=None)
        args = [sys.executable, "-m", "theory_master.worker_runtime",
                "preserve-job", "--research-root", str(root), "--worker", worker_id]
        env = worker_launch._task_scoped_process_env(root)
        if worker.get("account"):
            env["THEORYMASTER_WORKER_CODEX_HOME"] = str(
                worker_routes._pinned_codex_home(root, worker_id, worker["account"]))
            env["THEORYMASTER_WORKER_PINNED_ACCOUNT"] = worker["account"]
        log_path = worker_store.worker_dir(root, worker_id) / "preservation.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("a", encoding="utf-8") as log:
                worker_launch._launch_detached_service(args, root=root, env=env, output=log,
                                         kind="research-preservation")
        except Exception:
            # A refused launcher has not touched any scientific files or claims.
            worker_store.save_worker(root, worker_id, status=original_status)
            raise
        return {"worker_id": worker_id, "status": "PRESERVING",
                "cycle": cycle, "claim_preserved": True,
                "requested_operation": "preserve_research_then_release"}


def execute_preserve_release_job(spec: Any, worker_id: str) -> int:
    """Preserve via existing Workflow, and release ONLY after verified closure."""
    from . import multi_worker, runtime_manager, worker_actions, worker_launch, worker_scheduler, worker_store
    root = Path(spec.research_root).resolve()
    worker = worker_store.read_worker(root, worker_id)
    cycle = str(worker.get("current_cycle") or "")
    if worker.get("status") != "PRESERVING" or not cycle.isdecimal():
        raise ValueError("Worker is not in an authorized preservation transition")
    progress_path = worker_store.worker_dir(root, worker_id) / "preservation_result.json"
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    worker_store.save_worker(root, worker_id,
                preservation_job_pid=os.getpid(),
                preservation_job_start_ticks=runtime_manager.process_start_ticks(os.getpid()))
    try:
        command = [sys.executable, "-m", "theory_master.runner",
                   "--task-root", str(root), "--operation", "preserve",
                   "--identifier", cycle, "--approve-preservation",
                   "--managed-child"]
        # Unlike a scientific resume, preserve uses only one read-only
        # Investigator Stage. The runner itself enforces evidence/Canvas guards.
        with (worker_store.worker_dir(root, worker_id) / "preservation_child.log").open("a") as log:
            finished = subprocess.run(command, cwd=root, env=worker_launch._task_scoped_process_env(root),
                                      stdin=subprocess.DEVNULL, check=False,
                                      stdout=log, stderr=subprocess.STDOUT,
                                      text=True, timeout=36000)
        if finished.returncode:
            raise RuntimeError("preserve_research Stage failed; original Cycle and claim retained")
        closed = multi_worker._read_json(root / cycle / "preservation" / "closure.json")
        science = multi_worker._read_json(root / cycle / "research_state.json")
        if (closed.get("cycle_id") != cycle
                or closed.get("scientific_verdict") is not None
                or science.get("scientific_verdict") is not None
                or str(science.get("status") or "").lower() != "over"
                or science.get("closure_reason") != "investigator_preservation"):
            raise RuntimeError("preservation closure is unverified; Worker claim retained")
        # Release reconciles the original Worker/Cycle and removes only the
        # claimed entry. Running scheduler next tick chooses the next frontier.
        result = worker_actions.worker_action(spec, worker_id, "release")
        multi_worker._save_json(progress_path, {"status": "preserved_released", "cycle": cycle,
                                   "node_id": closed.get("canvas_node_id"),
                                   "worker": result, "at": multi_worker.utcnow().isoformat()})
        # After verified closure+release, always leave a persistent reminder.
        # Failure to display a notification must not undo an already released claim.
        try:
            from .budget_auto_preservation import budget_preservation_notice
            budget_preservation_notice(root, worker_id, cycle, success=True,
                                       hypothesis_id=str(closed.get("hypothesis_id") or ""))
        except (OSError, ValueError, RuntimeError) as exc:
            multi_worker._save_json(worker_store.worker_dir(root, worker_id) / "preservation_notice_warning.json",
                       {"cycle": cycle, "error_type": type(exc).__name__, "at": multi_worker.utcnow().isoformat()})
        from .worker_mode import read_mode
        if read_mode(root).get("enabled"):
            try:
                worker_scheduler.start_scheduler(spec, max(1, int(read_mode(root)["desired_agents"])))
            except (ValueError, OSError, RuntimeError) as exc:
                multi_worker._save_json(worker_store.worker_dir(root, worker_id) / "preservation_scheduler_warning.json",
                           {"error": type(exc).__name__, "at": multi_worker.utcnow().isoformat()})
        return 0
    except Exception as exc:
        current = worker_store.read_worker(root, worker_id)
        if current.get("status") == "PRESERVING":
            worker_store.save_worker(root, worker_id, status="NEEDS_ATTENTION")
        multi_worker._save_json(progress_path, {"status": "failed_preservation_retained_claim",
                                   "cycle": cycle, "error_type": type(exc).__name__,
                                   "at": multi_worker.utcnow().isoformat()})
        try:
            from .budget_auto_preservation import budget_preservation_notice
            budget_preservation_notice(root, worker_id, cycle, success=False,
                                       detail=type(exc).__name__)
        except (OSError, ValueError, RuntimeError):
            pass  # Original Cycle remains retained; failure stays in progress_path.
        return 1
