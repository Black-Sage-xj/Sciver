"""Worker runtime persistence, ownership records and lifecycle reconciliation."""
from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
from typing import Any


def worker_dir(root: Path, worker_id: str) -> Path:
    if not worker_id.startswith("worker-") or not worker_id[7:].isdigit() or int(worker_id[7:]) < 1:
        raise ValueError("invalid Worker ID")
    return Path(root).resolve() / ".theory-master" / "workers" / worker_id


def read_worker(root: Path, worker_id: str) -> dict[str, Any]:
    from . import multi_worker
    return multi_worker._read_json(worker_dir(root, worker_id) / "runtime.json")


def save_worker(root: Path, worker_id: str, **changes: Any) -> dict[str, Any]:
    from . import multi_worker, runtime_manager, worker_state
    directory = worker_dir(root, worker_id)
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "runtime.lock", "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        payload = multi_worker._read_json(directory / "runtime.json") or {"schema": worker_state.SCHEMA, "worker_id": worker_id}
        if payload.get("schema") != worker_state.SCHEMA:
            raise ValueError("incompatible worker runtime schema")
        payload.update(changes)
        if "runtime_source_provenance" not in payload or changes.get("status") == "RUNNING":
            provenance = runtime_manager.runtime_source_provenance()
            payload["runtime_source_provenance"] = provenance
            history = payload.get("runtime_source_history")
            history = history if isinstance(history, list) else []
            last = history[-1] if history and isinstance(history[-1], dict) else {}
            if (not history or last.get("sdk_git_sha") != provenance.get("sdk_git_sha")
                    or last.get("module_sha256") != provenance.get("module_sha256")):
                history.append({"observed_at": multi_worker.utcnow().isoformat(), **provenance})
            payload["runtime_source_history"] = history[-8:]
        payload["updated_at"] = multi_worker.utcnow().isoformat()
        multi_worker._save_json(directory / "runtime.json", payload)
        return payload


def list_workers(root: Path) -> list[dict[str, Any]]:
    from . import multi_worker, runtime_manager, worker_state
    directory = Path(root).resolve() / ".theory-master" / "workers"
    result = []
    for path in directory.glob("worker-*/runtime.json") if directory.exists() else []:
        data = multi_worker._read_json(path)
        if data.get("schema") != worker_state.SCHEMA:
            continue
        active = runtime_manager.process_identity_alive(data.get("pid"), data.get("pid_start_ticks"))
        result.append({**data, "process_alive": active,
                       "effective_status": data.get("status") if active or data.get("status") not in worker_state.RUNNING else "ORPHANED"})
    return sorted(result, key=lambda d: int(d.get("worker_id", "worker-0").split("-")[-1]))


def reconcile_released_worker_cycle(root: Path, worker: dict[str, Any]) -> dict[str, Any] | None:
    """Align a released Worker's persisted Cycle lifecycle without changing science.

    Releasing a frontier is an explicit choice not to resume that exact Cycle.
    A dead Worker must therefore never leave its authoritative research state
    claiming active execution. Preserve the frozen hypothesis, evidence and
    checkpoint; record the prior runtime identity; never fabricate a Judge/Goal
    verdict or turn a nonterminal Cycle into OVER.
    """
    from . import multi_worker, runtime_manager
    root = Path(root).resolve()
    cycle_id = str(worker.get("current_cycle") or "")
    if not cycle_id:
        return None
    if not cycle_id.isascii() or not cycle_id.isdecimal() or int(cycle_id) < 1:
        raise ValueError("Worker has an invalid assigned Cycle; refusing to release")
    cycle_dir = root / cycle_id
    if cycle_dir.parent != root or cycle_dir.is_symlink() or not cycle_dir.is_dir():
        raise ValueError("Worker Cycle path is unsafe or missing; refusing to release")

    state_path = cycle_dir / "research_state.json"
    if state_path.is_symlink() or not state_path.is_file():
        raise ValueError("Worker Cycle lacks authoritative research_state.json; refusing to release")
    state = multi_worker._read_json(state_path)
    worker_id = str(worker.get("worker_id") or "")
    if not worker_id or state.get("worker_id") != worker_id:
        raise ValueError("Worker/Cycle identity mismatch; refusing to release")

    run_path = cycle_dir / "run_state.json"
    if run_path.is_symlink():
        raise ValueError("Worker Cycle run_state.json is a symlink; refusing to release")
    run = multi_worker._read_json(run_path) if run_path.is_file() else {}
    original_state = dict(state)
    original_run = dict(run)
    science_status = str(state.get("status") or "").lower()
    run_status = str(run.get("status") or "").lower()
    active_labels = {"active", "running", "starting", "unresponsive"}
    if science_status in active_labels or run_status in active_labels:
        owner_pid = state.get("owner_pid") or run.get("owner_pid")
        worker_pid = worker.get("pid")
        try:
            same_owner = owner_pid is not None and worker_pid is not None and int(owner_pid) == int(worker_pid)
        except (TypeError, ValueError):
            same_owner = False
        owner_ticks = (
            worker.get("pid_start_ticks") if same_owner
            else state.get("owner_pid_start_ticks") or state.get("pid_start_ticks")
                 or run.get("owner_pid_start_ticks") or run.get("pid_start_ticks")
        )
        if runtime_manager.process_identity_alive(owner_pid, owner_ticks):
            raise ValueError("Cycle owner process is still live; refusing to reconcile a released Worker")
        if runtime_manager.process_identity_alive(worker_pid, worker.get("pid_start_ticks")):
            raise ValueError("Worker process is still live; refusing to reconcile its Cycle")

    when = multi_worker.utcnow().isoformat()
    prior_recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else {}
    prior_status = science_status or "unknown"
    prior_owner_pid = state.get("owner_pid") or run.get("owner_pid")
    prior_turn = state.get("current_turn_id") or run.get("current_turn_id")
    stopped_statuses = {"paused", "failed", "needs_attention", "interrupted"}
    terminal_statuses = {"over", "complete", "completed", "cancelled", "stopped"}
    if science_status in {"active", "running"}:
        state["status"] = "interrupted"
        state["phase"] = "interrupted"
        state["runtime_state"] = "interrupted"
    elif science_status in stopped_statuses:
        # RELEASED is deliberately not resumable; the frozen artifacts remain
        # inspectable and a later Cycle may choose a frontier independently.
        pass
    elif science_status in terminal_statuses:
        # Preserve the terminal scientific result and verdict exactly.
        pass
    else:
        raise ValueError(f"Unknown Cycle lifecycle status {science_status!r}; refusing to release")

    recovery = {
        "action": "worker_release",
        "reason": "Worker frontier was explicitly released after verifying its process was absent",
        "at": when,
        "worker_id": worker_id,
        "cycle": cycle_id,
        "previous_status": prior_status,
        "previous_owner_pid": prior_owner_pid,
        "previous_current_turn_id": prior_turn,
        "previous_execution_active": state.get("execution_active"),
        "previous_resumable": state.get("resumable"),
        "prior_recovery": prior_recovery,
    }
    state["execution_active"] = False
    state["resumable"] = False
    state["owner_pid"] = None
    state["recovery"] = recovery
    backup_state_path = cycle_dir / "research_state.before-worker-release-reconcile.json"
    if backup_state_path.is_symlink():
        raise ValueError("Worker Cycle reconciliation backup is a symlink; refusing to release")
    if not backup_state_path.exists():
        multi_worker._save_json(backup_state_path, original_state)
    if run_path.is_file():
        backup_run_path = cycle_dir / "run_state.before-worker-release-reconcile.json"
        if backup_run_path.is_symlink():
            raise ValueError("Worker Cycle run-state backup is a symlink; refusing to release")
        if not backup_run_path.exists():
            multi_worker._save_json(backup_run_path, original_run)
    multi_worker._save_json(state_path, state)

    status_map = {
        "active": "INTERRUPTED", "running": "INTERRUPTED",
        "paused": "PAUSED", "failed": "FAILED", "needs_attention": "NEEDS_ATTENTION",
        "interrupted": "INTERRUPTED", "over": "OVER", "complete": "COMPLETE",
        "completed": "COMPLETED", "cancelled": "CANCELLED", "stopped": "STOPPED",
    }
    projected_status = status_map[science_status]
    if run:
        run["status"] = projected_status
        run["phase"] = state.get("phase") or projected_status.lower()
        run["execution_active"] = False
        run["resumable"] = False
        run["owner_pid"] = None
        run["recovery"] = recovery
        for key in ("current_agent", "current_turn_id", "current_thread_id", "current_tool", "current_activity"):
            if key in run:
                run[key] = None
        multi_worker._save_json(run_path, run)

    event = {
        "event": "worker_cycle_reconciled",
        "at": when,
        "worker_id": worker_id,
        "cycle": cycle_id,
        "previous_status": prior_status,
        "status": state["status"],
        "execution_active": False,
        "resumable": False,
        "scientific_verdict_changed": False,
        "evidence_or_checkpoint_deleted": False,
    }
    event_path = cycle_dir / "worker_lifecycle_events.jsonl"
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return {"worker_id": worker_id, "cycle": cycle_id, "status": state["status"],
            "run_state_status": projected_status, "resumable": False, "reconciled": True}


def available_worker_name(root: Path, *, exclude: frozenset[str] = frozenset(),
                          eligible_ids: frozenset[str] | None = None) -> str:
    from . import worker_state
    workers = list_workers(root)
    claims = set(exclude) | {w["worker_id"] for w in workers if w.get("process_alive")
                               or w.get("close_requested")
                               or w.get("effective_status") in (worker_state.RESUMABLE | worker_state.RUNNING)}
    for index in range(1, len(workers) + 2):
        name = f"worker-{index}"
        if name not in claims and (eligible_ids is None or name in eligible_ids):
            return name
    raise RuntimeError("Worker name allocation failed")


def _graph_path(spec: Any) -> Path:
    if getattr(spec, "graph_database", None) is not None:
        return Path(spec.graph_database).resolve()
    if getattr(spec, "branch_canvas", None) is None:
        raise ValueError("Task configuration must define a Canvas projection path")
    from .notebook import graph_database_path
    return graph_database_path(Path(spec.branch_canvas).resolve().parent)


def _live_cycle_claims(root: Path) -> list[dict[str, Any]]:
    """Non-terminal Cycle Claims (claimed/paused), read without taking the lock."""
    from . import multi_worker
    registry = multi_worker._read_json(Path(root) / ".theory-master" / "cycle_claims.json")
    return [c for c in (registry.get("claims") or {}).values() if isinstance(c, dict)]


def _worker_live_claim(claims: Any, worker: dict[str, Any]) -> dict[str, Any] | None:
    """The live Cycle Claim exactly matching the Worker's recorded fencing token."""
    claim_id, epoch = worker.get("claim_id"), worker.get("epoch")
    if not claim_id or epoch is None:
        return None
    for claim in claims.list_claims():
        if (claim.get("claim_id") == claim_id and claim.get("worker_id") == worker.get("worker_id")
                and int(claim.get("fencing_epoch") or 0) == int(epoch)):
            return claim
    return None


def _claim_owns_cycle(claim: dict[str, Any] | None, cycle: Any) -> bool:
    text = str(cycle or "")
    return bool(claim) and text.isascii() and text.isdecimal() and int(text) >= 1 \
        and claim.get("cycle_id") == str(int(text))


def _checkpoint_matches_claim(state: dict[str, Any], worker_id: str,
                              claim: dict[str, Any] | None, cycle: Any) -> bool:
    """Cycle identity = Worker + Cycle Claim (+ bound H), not a claimed historical H."""
    if state.get("worker_id") != worker_id or not _claim_owns_cycle(claim, cycle):
        return False
    locked = str((state.get("hypothesis_lock") or {}).get("id") or "")
    bound = claim.get("hypothesis_id")
    return not (bound and locked and locked != bound)


def _recover_failed_cycle_pointer(
    root: Path, worker: dict[str, Any], claim: dict[str, Any] | None,
) -> str | None:
    """Recover only a FAILED preflight's existing, safely fenced Cycle ID."""
    from . import multi_worker
    if (worker.get("status") != "FAILED" or not isinstance(claim, dict)
            or claim.get("worker_id") != worker.get("worker_id")):
        return None
    cycle = str(claim.get("cycle_id") or "")
    if not _claim_owns_cycle(claim, cycle):
        return None
    state_path = Path(root) / cycle / "research_state.json"
    if state_path.is_symlink() or not state_path.is_file():
        return None
    state = multi_worker._read_json(state_path)
    if (state.get("resumable") is not True
            or str(state.get("status") or "").lower() not in {"paused", "failed", "interrupted", "needs_attention"}
            or not _checkpoint_matches_claim(state, str(worker.get("worker_id") or ""), claim, cycle)):
        return None
    return cycle


def _ensure_legacy_claims_migrated(root: Path) -> dict[str, Any] | None:
    """First use after V1.1: convert unmigrated legacy H claims into Cycle Claims.

    Fail closed when migration skips any entry. Worker records holding the
    legacy token are re-pointed to the new paused Cycle Claim token.
    """
    from . import multi_worker
    root = Path(root).resolve()
    legacy = multi_worker._read_json(root / ".theory-master" / "branch_claims.json")
    entries = [c for c in (legacy.get("claims") or {}).values() if isinstance(c, dict)]
    if not entries:
        return None
    registry = multi_worker.CycleClaims(root)
    migrated_claims = [*registry.list_claims(), *registry.history()]
    known = {(c.get("migrated_from") or {}).get("claim_id") for c in migrated_claims} - {None}
    if all(c.get("claim_id") in known for c in entries):
        return None
    from .worker_migration import migrate_branch_claims
    result = migrate_branch_claims(root)
    if result.get("skipped"):
        raise RuntimeError("legacy branch claim migration skipped entries; manual review required: "
                           + json.dumps(result["skipped"], ensure_ascii=False)[:700])
    for claim in registry.list_claims():
        source = claim.get("migrated_from") or {}
        if not source.get("claim_id"):
            continue
        try:
            record = read_worker(root, str(claim.get("worker_id")))
        except ValueError:
            continue
        if record.get("claim_id") == source["claim_id"]:
            save_worker(root, claim["worker_id"], claim_id=claim["claim_id"],
                        epoch=claim["fencing_epoch"], legacy_claim_id=source["claim_id"],
                        cycle_claims_migrated_at=multi_worker.utcnow().isoformat())
    return result


def _living_workers(root: Path) -> list[dict[str, Any]]:
    """Return Workers that reserve a slot, not necessarily live processes.

    Any non-terminal Cycle Claim reserves Agent/account capacity until a
    validated explicit Resume/Release. This includes FAILED and NEEDS_ATTENTION
    Workers whose frozen Cycle may still be resumed; it prevents a later resume
    from exceeding the configured parallel limit. The scheduler separately
    reports dead failed claims as requiring manual recovery, not as live work.
    """
    from . import worker_state
    claim_owners = {(c.get("worker_id"), c.get("claim_id"), c.get("fencing_epoch"))
                    for c in _live_cycle_claims(root)}
    return [w for w in list_workers(root) if
            (w.get("process_alive") and w.get("status") in worker_state.RUNNING) or
            (w.get("worker_id"), w.get("claim_id"), w.get("epoch")) in claim_owners]


def _master_complete(root: Path, *, spec: Any | None = None) -> bool:
    """Goal terminal = SQLite seal (G1); verified legacy receipt is a read-only fallback."""
    from . import multi_worker
    from .goal_terminal import goal_sealed
    from .specification import TaskSpec, list_cycle_dirs

    root = Path(root).resolve()
    if spec is None:
        spec = TaskSpec.from_task_root(root)
    graph = _graph_path(spec) if getattr(spec, "branch_canvas", None) is not None else None
    if graph is not None and goal_sealed(root, graph) is not None:
        return True
    # No candidate receipt => skip resolving the legacy Goal authority, which
    # preserves lightweight Worker planning for unfinished/fixture task roots.
    if graph is None or not any(((multi_worker._read_json(c / "research_state.json").get("goal_completion") or {})
                                 .get("verdict") == "passed")
                                for c in list_cycle_dirs(root, task_mode=True)):
        return False
    return goal_sealed(root, graph, spec=spec) is not None
