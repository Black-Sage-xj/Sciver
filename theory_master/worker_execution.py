"""Execution of one owned Cycle and its terminal handoff."""
from __future__ import annotations
import json
import os
import threading
from pathlib import Path
from typing import Any


def _update_cycle_memory_index(spec: Any, root: Path, cycle_dir: Path | None, *, quiet: bool) -> None:
    """Derived H -> Cycle -> raw evidence index; never replaces the Cycle outcome."""
    from . import goal_terminal, multi_worker, worker_store
    if cycle_dir is None:
        return
    from .science_cycle_memory_index import index_task_cycle_memories
    db = worker_store._graph_path(spec)
    if not db.is_file():
        return
    try:
        index_task_cycle_memories(db, root)
    except (OSError, ValueError, goal_terminal.GoalSealedError) as index_error:
        if quiet:
            return
        with (cycle_dir / "worker_runtime_warnings.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"kind": "cycle_memory_index_update_failed",
                                  "detail": str(index_error), "at": multi_worker.utcnow().isoformat()}) + "\n")


def _wake_fingerprint_for(spec: Any, result: dict[str, Any]) -> dict[str, Any] | None:
    """Prefer the Workflow's fingerprint when it uses the shared G6 fields."""
    from . import worker_direction, worker_state
    offered = result.get("wake_fingerprint")
    if worker_direction._direction_values(offered) is not None:
        return {"schema": worker_state.DIRECTION_FINGERPRINT_SCHEMA,
                **{key: offered[key] for key in worker_state.DIRECTION_FINGERPRINT_FIELDS}}
    try:
        return worker_direction.research_direction_fingerprint(spec)
    except (OSError, ValueError):
        return None  # Unknown signal: only an explicit retry_direction wakes this Worker.


def run_once(spec: Any, worker_id: str, claim_id: str, epoch: int, *,
             resume_cycle: str | None = None) -> int:
    """Only the matching freshly-owned OS process may run the claimed Cycle."""
    from . import goal_terminal, multi_worker, runtime_manager, worker_direction, worker_quota, worker_routes, worker_state, worker_store
    from .backends import CodexBackend
    from .workflow import Workflow

    root = Path(spec.research_root).resolve()
    graph_path = worker_store._graph_path(spec)
    claims = multi_worker.CycleClaims(root, graph_path)
    claim = claims.attach_owner(worker_id, claim_id, epoch, owner_pid=os.getpid(),
                                owner_start_ticks=runtime_manager.process_start_ticks(os.getpid()))
    stop = threading.Event()
    workflow = None
    import hashlib
    original_plan_digest = hashlib.sha256(Path(spec.document).read_bytes()).hexdigest()

    def heartbeat() -> None:
        pulses = 0
        last_live_workers: tuple[str, ...] | None = None
        last_notice: str | None = None
        while not stop.wait(3):
            try:
                current = claims.update(worker_id, claim_id, epoch, action="heartbeat")
                updates = {"heartbeat_at": multi_worker.utcnow().isoformat()}
                if current.get("hypothesis_id"):
                    updates["target_h_id"] = current["hypothesis_id"]
                if workflow and workflow.cycle_dir is not None:
                    updates["current_cycle"] = workflow.cycle_dir.name
                    peers = tuple(sorted(w["worker_id"] for w in worker_store.list_workers(root)
                                         if w.get("process_alive") and
                                         w.get("effective_status") in worker_state.RUNNING))
                    # The start snapshot is immutable; this append-only event
                    # stream records later observed changes in live concurrency.
                    if peers != last_live_workers:
                        event = {"observed_at": multi_worker.utcnow().isoformat(),
                                 "worker_id": worker_id,
                                 "live_worker_ids": list(peers),
                                 "concurrent_count": len(peers),
                                 "observation": "periodic_sample_not_exact_continuous_overlap"}
                        with (workflow.cycle_dir / "worker_concurrency_events.jsonl").open(
                                "a", encoding="utf-8") as timeline:
                            timeline.write(json.dumps(event, ensure_ascii=False) + "\n")
                        last_live_workers = peers
                pulses += 1
                if pulses % 4 == 0:
                    try:
                        notice = worker_direction.detect_critical_knowledge_change(
                            spec, plan_digest=original_plan_digest,
                            hypothesis_id=current.get("hypothesis_id"),
                            cycle_id=current.get("cycle_id"))
                    except (ValueError, OSError) as check_error:
                        notice = None
                        updates["critical_knowledge_check_error"] = str(check_error)[:300]
                    if notice and notice["reason"] != last_notice:
                        updates["critical_knowledge_notice"] = {**notice, "detected_at": multi_worker.utcnow().isoformat()}
                        last_notice = notice["reason"]
                worker_store.save_worker(root, worker_id, **updates)
            except (ValueError, OSError):
                # A lost claim must fail CLOSED before any subsequent Canvas write.
                stop.set()
                return

    def stop_for_goal(detail: str | None) -> int:
        try:
            claims.update(worker_id, claim_id, epoch, action="stopped_goal")
        except ValueError:
            pass  # Already archived/fenced; the sealed Goal stays authoritative.
        worker_store.save_worker(root, worker_id, status=worker_state.STOPPED_GOAL_COMPLETED, error_kind=None,
                    claim_id=None, epoch=None, last_claim_id=claim_id, last_claim_epoch=epoch,
                    goal_stop={"at": multi_worker.utcnow().isoformat(), "detail": (detail or "")[:500]},
                    current_cycle=(workflow.cycle_dir.name if workflow and workflow.cycle_dir else None))
        return 0

    thread = threading.Thread(target=heartbeat, name=f"tm-{worker_id}-heartbeat", daemon=True)
    thread.start()
    try:
        routes = worker_routes.resolve_worker_routes(root, worker_id)
        backend = CodexBackend(root, routes=routes)
        from .observability.collector import Collector
        collector = Collector(spec, system_failure_actions=False, email_notifications=True,
                              cycle_audit_actions=False, attention_takeover_actions=False)
        workflow = Workflow(spec, backend, routes, collector=collector,
                            route_resolver=lambda: worker_routes.resolve_worker_routes(root, worker_id))
        workflow.worker_id = worker_id
        workflow.worker_claim_id = claim_id
        workflow.worker_claim_epoch = epoch
        workflow.worker_claims = claims
        workflow.worker_proposal_gate = multi_worker.ProposalGate(root, graph_path)
        backend.preflight_runtime()
        worker_store.save_worker(root, worker_id, status="RUNNING", error_kind=None, pid=os.getpid(),
                    pid_start_ticks=runtime_manager.process_start_ticks(os.getpid()))
        if resume_cycle:
            target = root / resume_cycle
            if not target.is_dir() or target.parent != root or not resume_cycle.isdigit():
                raise ValueError("invalid Worker resume Cycle")
            state = multi_worker._read_json(target / "research_state.json")
            if not worker_store._checkpoint_matches_claim(state, worker_id, claim, resume_cycle):
                raise ValueError("resume checkpoint belongs to a different Worker or Cycle claim")
            if str(state.get("status") or "").lower() not in {"paused", "failed", "needs_attention", "interrupted"}:
                raise ValueError("Worker Cycle is not resumable")
            # The Worker must bind the old Cycle before reading frozen role
            # routes. Otherwise Workflow._restore_resumed_cycle_routes exits
            # early because its cycle_dir is still None.
            workflow._bind_cycle(target, create=False)
            workflow._restore_resumed_cycle_routes(state)
            # Worker resume bypasses Workflow.resume_cycle(); restore human
            # guidance explicitly or the approval is silently omitted.
            state = worker_direction._restore_cycle_operator_guidance(state, target)
            result = workflow._run_cycle(resume_state=state)
        else:
            result = workflow._run_cycle()
        status = str(result.get("status") or "FAILED").upper()
        # Each committed Cycle automatically updates the H -> Cycle -> raw
        # evidence index (no manual CLI pass and no science state mutation).
        _update_cycle_memory_index(spec, root, workflow.cycle_dir, quiet=False)
        cycle_name = workflow.cycle_dir.name if workflow.cycle_dir else None
        if status == worker_state.STOPPED_GOAL_COMPLETED:
            return stop_for_goal(str(result.get("reason") or result.get("detail") or ""))
        from .task_progression import local_replan_reason
        replan_reason = local_replan_reason(result)
        if replan_reason is not None:
            # This process owns exactly one Cycle allocation. Archive that
            # attempt before the daemon uses its ordinary remaining quota to
            # claim another Cycle; never resume this Cycle or retain its H.
            multi_worker.finish_owned_claim(claims, worker_id=worker_id, claim_id=claim_id,
                               epoch=epoch, current_pid=os.getpid())
            worker_store.save_worker(root, worker_id, status="OVER", error_kind=None,
                        claim_id=None, epoch=None, last_claim_id=claim_id, last_claim_epoch=epoch,
                        target_h_id=None, entry_node_id=None, current_cycle=cycle_name,
                        wake_fingerprint=None, waiting_since=None,
                        last_local_replan={"reason": replan_reason, "cycle_id": cycle_name,
                                           "at": multi_worker.utcnow().isoformat()})
            return 0
        if status in {"OVER", "COMPLETED"} and result.get("operational_only_closure"):
            # A terminal-looking execution record does not waive a pause,
            # resource guard or unknown operational failure for the next claim.
            claims.update(worker_id, claim_id, epoch, action="pause")
            worker_store.save_worker(root, worker_id, status="NEEDS_ATTENTION", current_cycle=cycle_name,
                        local_replan_blocked={"cycle_id": cycle_name,
                                              "at": multi_worker.utcnow().isoformat()})
            return 2
        if status == "NO_QUALIFIED_CANDIDATE":
            fingerprint = _wake_fingerprint_for(spec, result)
            claims.update(worker_id, claim_id, epoch, action="wait_direction")
            worker_store.save_worker(root, worker_id, status=worker_state.WAITING_RESEARCH_DIRECTION, error_kind=None,
                        claim_id=None, epoch=None, last_claim_id=claim_id, last_claim_epoch=epoch,
                        wake_fingerprint=fingerprint, waiting_since=multi_worker.utcnow().isoformat(),
                        workflow_wake_fingerprint=result.get("wake_fingerprint"),
                        current_cycle=cycle_name)
            return 0
        worker_store.save_worker(root, worker_id, status=status,
                    error_kind=None if status in {"OVER", "COMPLETED"} else worker_store.read_worker(root, worker_id).get("error_kind"),
                    current_cycle=cycle_name)
        if status in {"PAUSED", "NEEDS_ATTENTION", "INTERRUPTED"}:
            claims.update(worker_id, claim_id, epoch, action="pause")
        if (status in {"PAUSED", "NEEDS_ATTENTION"}
                and workflow is not None and workflow.cycle_dir is not None):
            try:
                checkpoint = multi_worker._read_json(workflow.cycle_dir / "research_state.json")
                if ((status == "PAUSED" and checkpoint.get("phase") == "provider_quota_exhausted")
                        or (status == "NEEDS_ATTENTION"
                            and worker_quota._technical_review_quota_watch_eligible(checkpoint, worker_id))):
                    worker_quota.start_worker_quota_watch(spec, worker_id)
            except (OSError, ValueError, RuntimeError):
                pass  # Watcher setup cannot replace the durable science pause.
        if status in {"OVER", "CANCELLED"}:
            # Full terminal Cycle (including human withdrawal) is durable
            # before releasing this Worker-specific Cycle claim.
            multi_worker.finish_owned_claim(claims, worker_id=worker_id, claim_id=claim_id,
                               epoch=epoch, current_pid=os.getpid())
            return 0
        return 2
    except goal_terminal.GoalSealedError as exc:
        # A late write rejected by the sealed Goal is completion, never failure.
        _update_cycle_memory_index(spec, root, workflow.cycle_dir if workflow else None, quiet=True)
        return stop_for_goal(str(exc))
    except BaseException as exc:
        # Do not misclassify scientific blocked or generic errors as quota.
        # Only a failed provider delivery plus fresh exhausted-account proof
        # can become a resource wait with an existing quota watcher.
        from .supervisor_incident import NativeGoalRuntimeFailure
        reason = str(getattr(exc, "error", "")).lower()
        delivery_fault = ("stream disconnected" in reason
                          or "response.completed" in reason
                          or "completed without assistant final response" in reason)
        from .backend_config import ACCOUNT_PROVIDERS
        routes_now = getattr(workflow, "routes", {}) if workflow is not None else {}
        codex_only = (bool(routes_now) and all(
            getattr(route, "provider", None) in ACCOUNT_PROVIDERS
            for route in routes_now.values()))
        if isinstance(exc, NativeGoalRuntimeFailure) and delivery_fault and codex_only:
            pinned_account = str(worker_store.read_worker(root, worker_id).get("account") or "")
            resets = (worker_quota._verified_codex_quota_wait()
                      or worker_quota._verified_pinned_codex_quota_wait(pinned_account))
            cycle = workflow.cycle_dir.name if workflow and workflow.cycle_dir else resume_cycle
            if (resets and cycle and worker_quota._persist_verified_quota_wait(
                    root, worker_id, cycle, claim, resets, "provider-delivery-failed-during-exhausted-quota")):
                claims.update(worker_id, claim_id, epoch, action="pause")
                worker_store.save_worker(root, worker_id, status="PAUSED", error_kind=None, current_cycle=cycle)
                try:
                    worker_quota.start_worker_quota_watch(spec, worker_id)
                except (OSError, RuntimeError, ValueError):
                    # Science remains PAUSED; watcher launch needs operator repair.
                    pass
                return 0
        worker_store.save_worker(root, worker_id, status="FAILED", error_kind=type(exc).__name__,
                    current_cycle=(workflow.cycle_dir.name if workflow and workflow.cycle_dir else resume_cycle))
        # A FAILED checkpoint is still research memory and must be traceable
        # to its H. Failure to index cannot replace the original exception.
        _update_cycle_memory_index(spec, root, workflow.cycle_dir if workflow else None, quiet=True)
        try:
            claims.update(worker_id, claim_id, epoch, action="pause")
        except ValueError:
            pass  # expired/externally fenced Worker must not revive old claim
        raise
    finally:
        stop.set()
        thread.join(timeout=5)
