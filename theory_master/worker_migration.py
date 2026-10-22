"""Adopt a STOPPED, checkpointed single-Runner Cycle as the first Worker.

No old single-runner compatibility: after this transaction, the same frozen
Cycle belongs only to WorkerRuntime. Human-approved service stop is a preflight
requirement and the preserved migration snapshot remains immutable.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from .multi_worker import (
    LEASE_SECONDS, SCHEMA as BRANCH_CLAIMS_SCHEMA, BranchClaims, CycleClaims, _read_json, _save_json,
    timestamp, utcnow,
)
from .runtime_manager import process_identity_alive, runtime_health
from .worker_runtime import list_workers, read_worker, save_worker


def _legacy_cycle(root: Path, worker_id: str, claim: dict[str, Any]) -> str | None:
    """Cycle of a legacy H claim, only when the Worker record agrees on claim and H."""
    try:
        record = read_worker(root, worker_id)
    except ValueError:
        return None
    cycle = str(record.get("current_cycle") or "")
    if not cycle.isdecimal() or int(cycle) < 1:
        return None
    if record.get("claim_id") != claim.get("claim_id") or record.get("target_h_id") != claim.get("hypothesis_id"):
        return None
    return str(int(cycle))


def migrate_branch_claims(research_root: Path) -> dict[str, Any]:
    """Convert legacy H claims (branch_claims.json) into paused Cycle claims.

    Fail closed: refuses while any legacy owner process is alive; every
    migrated claim is ``paused`` with no owner and a NEW fencing epoch, so the
    old token is dead and resume goes through reissue/attach. Idempotent by
    legacy claim_id; branch_claims.json is never modified or deleted and a
    content-addressed backup is kept under .theory-master/legacy/.
    """
    root = Path(research_root).resolve()
    source = root / ".theory-master" / "branch_claims.json"
    if not source.is_file():
        return {"source": str(source), "backup": None, "migrated": [], "skipped": []}
    raw = source.read_bytes()
    legacy = json.loads(raw.decode("utf-8"))
    if legacy.get("schema") != BRANCH_CLAIMS_SCHEMA or not isinstance(legacy.get("claims"), dict):
        raise ValueError("incompatible legacy BranchClaims registry")
    alive = [c.get("worker_id") for c in legacy["claims"].values()
             if process_identity_alive(c.get("owner_pid"), c.get("owner_start_ticks"))]
    if alive:
        raise RuntimeError(f"legacy claim owners still running: {sorted(alive)}; stop them before migration")
    digest = hashlib.sha256(raw).hexdigest()
    backup = root / ".theory-master" / "legacy" / f"branch_claims.{digest[:16]}.json"
    if not backup.is_file():
        backup.parent.mkdir(parents=True, exist_ok=True)
        temp = backup.with_name(f".{backup.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(source, temp)
        temp.replace(backup)
    registry = CycleClaims(root)
    migrated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    at = utcnow()
    with registry._locked() as state:
        known = {(c.get("migrated_from") or {}).get("claim_id")
                 for c in [*state["claims"].values(), *state["history"]]} - {None}
        for old in sorted(legacy["claims"].values(), key=lambda c: c.get("fencing_epoch") or 0):
            old_id = old.get("claim_id")
            worker_id = str(old.get("worker_id") or "")
            h_id = old.get("hypothesis_id")
            if old_id in known:
                continue
            cycle_id = _legacy_cycle(root, worker_id, old)
            reason = None
            if not h_id or not worker_id.startswith("worker-"):
                reason = "legacy claim lacks worker or hypothesis identity"
            elif any(c.get("worker_id") == worker_id for c in state["claims"].values()):
                reason = "worker already has a non-terminal cycle claim"
            elif registry._identity_owner(state, "hypothesis_id", h_id, ""):
                reason = "hypothesis already owned by a cycle claim"
            elif cycle_id and registry._identity_owner(state, "cycle_id", cycle_id, ""):
                reason = "cycle already owned by a cycle claim"
            if reason:
                skipped.append({"legacy_claim_id": old_id, "worker_id": worker_id,
                                "hypothesis_id": h_id, "reason": reason})
                continue
            state["epoch"] += 1
            claim = {"claim_id": uuid.uuid4().hex, "fencing_epoch": state["epoch"],
                     "worker_id": worker_id, "cycle_id": cycle_id, "hypothesis_id": h_id,
                     "status": "paused", "acquired_at": timestamp(at), "heartbeat_at": timestamp(at),
                     "lease_expires_at": timestamp(at + timedelta(seconds=LEASE_SECONDS)),
                     "owner_pid": None, "owner_start_ticks": None,
                     "migrated_from": {"source": "branch_claims.json", "claim_id": old_id,
                                       "fencing_epoch": old.get("fencing_epoch"),
                                       "status": old.get("status"), "backup": str(backup)}}
            if cycle_id is None:
                claim["needs_attention"] = "cycle_not_discoverable_from_worker_registry"
            state["claims"][claim["claim_id"]] = claim
            migrated.append(dict(claim))
        if migrated:
            _save_json(registry.path, state)
    return {"source": str(source), "backup": str(backup), "migrated": migrated, "skipped": skipped}


def adopt_stopped_cycle(spec: Any, cycle_id: str, *, snapshot: Path,
                        worker_id: str = "worker-1") -> dict[str, Any]:
    root = Path(spec.research_root).resolve()
    if not cycle_id.isdecimal() or int(cycle_id) < 1:
        raise ValueError("invalid interrupted Cycle number")
    if runtime_health(root).get("active"):
        raise RuntimeError("cannot migrate while legacy single Runner is active")
    if list_workers(root):
        raise RuntimeError("Worker records already exist; migration requires a clean Worker registry")
    state_path = root / cycle_id / "research_state.json"
    state = _read_json(state_path)
    if state.get("cycle_id") != cycle_id or not state.get("resumable"):
        raise ValueError("Cycle lacks verified resumable scientific state")
    if state.get("worker_id"):
        raise ValueError("Cycle is already owned by another Worker")
    if state.get("status") not in {"active", "interrupted", "paused", "failed", "needs_attention"}:
        raise ValueError("migration requires interrupted, unfinished Cycle")
    locked_h = str((state.get("hypothesis_lock") or {}).get("id") or "")
    if not locked_h.startswith("H-") or not state.get("last_safe_checkpoint"):
        raise ValueError("frozen Hypothesis / scientific checkpoint missing")
    checkpoint = Path(state["last_safe_checkpoint"]).resolve()
    if checkpoint.parent != (root / cycle_id / "checkpoints").resolve() or not checkpoint.is_file():
        raise ValueError("checkpoint provenance is not inside this Cycle")
    backup = Path(snapshot).resolve()
    allowed = (root / ".theory-master" / "migration-backups").resolve()
    if backup.parent != allowed or not (backup / "manifest.json").is_file():
        raise ValueError("verified migration backup manifest is required")
    manifest = _read_json(backup / "manifest.json")
    if manifest.get("cycle") != cycle_id or not all((backup / key).is_file() for key in (
            "cycle" + cycle_id + "/research_state.json", "science_graph.sqlite3", "research_plan.md")):
        raise ValueError("migration backup incomplete or for a different Cycle")
    import hashlib
    for row in manifest.get("files", []):
        file = backup / row["backup"]
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("backup integrity check failed")
    prior_account = state.get("runtime_codex_account")
    if prior_account is not None and str(prior_account) not in {"1", "2"}:
        raise ValueError("unsupported frozen Codex identity; cannot resume")
    graph_path = Path(spec.branch_canvas).resolve().with_name("science_graph.sqlite3")
    if not graph_path.is_file():
        raise FileNotFoundError(f"canonical SQLite graph required for Worker adoption: {graph_path}")
    claims = BranchClaims(root, graph_path)
    frontier = next((entry for entry in claims.status()["frontiers"]
                     if entry["hypothesis_id"] == locked_h), None)
    if frontier is None:
        raise ValueError("frozen H is not an eligible current SQLite frontier; require human science review")
    claim = claims.claim(worker_id, hypothesis_id=locked_h)
    state["worker_id"] = worker_id
    state["claimed_hypothesis_id"] = locked_h
    state["claimed_frontier_id"] = claim["entry_node_id"]
    state["status"] = "interrupted"
    state["runtime_state"] = "paused"
    state["execution_active"] = False
    state["owner_pid"] = None
    state["recovery"] = {**(state.get("recovery") or {}),
                         "action": "resume", "allow_thread_fallback": True,
                         "migration_backup": str(backup)}
    _save_json(state_path, state)
    record = save_worker(root, worker_id, status="PAUSED", current_cycle=cycle_id,
                         target_h_id=locked_h, entry_node_id=claim["entry_node_id"],
                         claim_id=claim["claim_id"], epoch=claim["fencing_epoch"],
                         pid=None, pid_start_ticks=None,
                         account=str(prior_account) if prior_account is not None else None,
                         migration_backup=str(backup))
    return {"worker_id": worker_id, "cycle": cycle_id, "target_h_id": locked_h,
            "entry_node_id": claim["entry_node_id"], "status": record["status"],
            "account": record["account"], "snapshot": str(backup)}
