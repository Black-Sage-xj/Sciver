"""Concurrency-safe SQLite-frontier claims for a future multi-Worker runtime.

This is the control-plane boundary, NOT a second way to start Workflow.  The
legacy Workflow owns one research-root-wide runner lock and cannot safely run
concurrently until its state persistence and Canvas writes are isolated.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .runtime_manager import process_identity_alive, runtime_health

SCHEMA = "theory-master/branch-claims/v1"
ACTIVE = "active"
LEASE_SECONDS = 24 * 3600


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"expected JSON object: {path}")
    return result


def _save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def frontier_snapshot(graph_path: Path) -> tuple[str, list[dict[str, str]]]:
    """Return an authoritative frontier snapshot from SQLite only.

    Ordered by H id only: historical Preferred is display data ("priority")
    and never influences selection or scheduling (I-12).
    """
    graph_path = Path(graph_path)
    if not graph_path.is_file():
        raise FileNotFoundError(
            f"Canonical SQLite graph is required for frontier operations: {graph_path}"
        )
    from .science_graph_store import ScienceGraphStore
    with ScienceGraphStore(graph_path, readonly=True) as store:
        snapshot = store.frontier_view()
        rows = []
        for h_id, value in snapshot.items():
            if value["frontier_status"] != ACTIVE:
                continue
            title_row = store.db.execute(
                "SELECT title FROM hypotheses WHERE id=?", (h_id,)
            ).fetchone()
            rows.append({
                "hypothesis_id": h_id, "entry_node_id": h_id,
                "priority": "preferred" if value["preferred"] else "",
                "title": str(title_row["title"] if title_row else ""),
            })
        revision = hashlib.sha256(json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")).hexdigest()
    return revision, sorted(rows, key=lambda row: row["hypothesis_id"])


class BranchClaims:
    """Legacy H-level claims (V1.0), kept for read, migration and old callers.

    Superseded by CycleClaims: Workers claim Cycles, not historical H.
    """

    def __init__(self, research_root: Path, graph_path: Path):
        self.root = Path(research_root).resolve()
        self.graph_path = Path(graph_path).resolve()
        self.path = self.root / ".theory-master" / "branch_claims.json"
        self.lock_path = self.root / ".theory-master" / "branch_claims.lock"

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                value = _read_json(self.path)
                if not value:
                    value = {"schema": SCHEMA, "epoch": 0, "claims": {}}
                if value.get("schema") != SCHEMA or not isinstance(value.get("claims"), dict):
                    raise ValueError("incompatible BranchClaims registry")
                yield value
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def status(self) -> dict[str, Any]:
        with self._locked() as registry:
            result = json.loads(json.dumps(registry))
        revision, frontiers = frontier_snapshot(self.graph_path)
        result.update({"graph_revision": revision, "frontiers": frontiers})
        return result

    def plan(self, *, available_resource_slots: int | None = None) -> dict[str, Any]:
        """No artificial 2/3-Worker cap; resources may limit actual execution.

        Unknown capacity returns runnable_workers=0 (fail closed): the caller
        must establish real Codex/compute isolation before launching anything.
        """
        if available_resource_slots is not None and available_resource_slots < 0:
            raise ValueError("available_resource_slots must be nonnegative")
        state = self.status()
        existing = state["claims"]
        unclaimed = [row for row in state["frontiers"] if row["hypothesis_id"] not in existing]
        legacy_active = bool(runtime_health(self.root).get("active"))
        # Existing single-owner Workflow uses runner.lock, master-global state and
        # the legacy single-Worker execution loop. It cannot coexist with claimed new Workers.
        # No active H is an unresolved planning state, not proof that the
        # Final Gate passed. Only a future Lead proposal may choose a
        # scientifically justified historical H origin; do not assign closed
        # H as if it were already an active same-H Worker claim.
        historical_proposal_needed = False
        if not state["frontiers"]:
            from .science_graph_store import ScienceGraphStore
            with ScienceGraphStore(self.graph_path, readonly=True) as store:
                historical_proposal_needed = bool(store.frontier_view())
        return {"historical_proposal_needed": historical_proposal_needed,
                "eligible_frontiers": len(state["frontiers"]), "claimed": len(existing),
                "unclaimed_frontiers": unclaimed, "suggested_additional_workers":
                    (0 if legacy_active else len(unclaimed)),
                "runnable_additional_workers": (0 if legacy_active else
                    min(len(unclaimed), available_resource_slots or 0)),
                "resource_capacity_known": available_resource_slots is not None,
                "blocked_by_legacy_runtime": legacy_active,
                "graph_revision": state["graph_revision"]}

    def claim(self, worker_id: str, *, hypothesis_id: str | None = None,
              expected_revision: str | None = None, owner_pid: int | None = None,
              owner_start_ticks: int | None = None, now: datetime | None = None) -> dict[str, Any]:
        if not re.fullmatch(r"worker-[1-9]\d*", worker_id):
            raise ValueError("worker ID must be worker-N (N>=1)")
        at = now or utcnow()
        with self._locked() as state:
            if runtime_health(self.root).get("active"):
                raise ValueError("legacy single-Worker runtime is active; claims must not run concurrently")
            revision, frontiers = frontier_snapshot(self.graph_path)
            if expected_revision is not None and expected_revision != revision:
                raise ValueError("SQLite graph revision changed; refresh before claiming")
            claim_by_worker = [c for c in state["claims"].values() if c.get("worker_id") == worker_id]
            if claim_by_worker:
                raise ValueError(f"{worker_id} already has a branch claim")
            candidates = {row["hypothesis_id"]: row for row in frontiers}
            if hypothesis_id is not None and hypothesis_id not in candidates:
                raise ValueError("selected hypothesis is not an eligible active frontier")
            selected = hypothesis_id or next((row["hypothesis_id"] for row in frontiers
                                              if row["hypothesis_id"] not in state["claims"]), None)
            if selected is None or selected in state["claims"]:
                raise ValueError("no unclaimed eligible hypothesis frontier")
            state["epoch"] += 1
            claim = {"claim_id": uuid.uuid4().hex, "fencing_epoch": state["epoch"],
                     "hypothesis_id": selected, "entry_node_id": candidates[selected]["entry_node_id"],
                     "worker_id": worker_id,
                     "status": "claimed", "graph_revision_at_claim": revision,
                     "acquired_at": timestamp(at), "heartbeat_at": timestamp(at),
                     "lease_expires_at": timestamp(at + timedelta(seconds=LEASE_SECONDS)),
                     "owner_pid": owner_pid, "owner_start_ticks": owner_start_ticks}
            state["claims"][selected] = claim
            _save_json(self.path, state)
            return dict(claim)

    def _owned(self, state: dict[str, Any], worker_id: str, claim_id: str, epoch: int) -> dict[str, Any]:
        owned = [c for c in state["claims"].values() if c.get("worker_id") == worker_id
                 and c.get("claim_id") == claim_id and c.get("fencing_epoch") == epoch]
        if len(owned) != 1:
            raise ValueError("stale or unowned claim: fencing token rejected")
        return owned[0]

    def update(self, worker_id: str, claim_id: str, epoch: int, *,
               action: str, now: datetime | None = None) -> dict[str, Any]:
        if action not in {"heartbeat", "pause", "resume", "release"}:
            raise ValueError("invalid claim operation")
        at = now or utcnow()
        with self._locked() as state:
            claim = self._owned(state, worker_id, claim_id, epoch)
            if action == "release":
                if process_identity_alive(claim.get("owner_pid"), claim.get("owner_start_ticks")):
                    raise ValueError("cannot release claim while owner process is alive; isolate it first")
                del state["claims"][claim["hypothesis_id"]]
                state["epoch"] += 1
            else:
                if at > datetime.fromisoformat(claim["lease_expires_at"]):
                    raise ValueError("claim lease expired; do not revive a stale Worker")
                claim["status"] = "paused" if action == "pause" else "claimed"
                claim["heartbeat_at"] = timestamp(at)
                claim["lease_expires_at"] = timestamp(at + timedelta(seconds=LEASE_SECONDS))
            _save_json(self.path, state)
            return dict(claim)

    def reissue_expired_paused_for_operator(
        self, worker_id: str, claim_id: str, epoch: int, *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Explicit paused-Cycle recovery with a NEW fencing token.

        Never called by scheduling/tick. The caller must be servicing a
        user-initiated Resume. An expired claim is NOT revived in place, and
        the old owner must be provably dead before recovery. If V2 is active,
        an old Decision entry is rewritten to the SAME parent H only.
        """
        at = now or utcnow()
        with self._locked() as state:
            old = self._owned(state, worker_id, claim_id, epoch)
            if old.get("status") != "paused":
                raise ValueError("only paused claims can be reissued for explicit Resume")
            if process_identity_alive(old.get("owner_pid"), old.get("owner_start_ticks")):
                raise ValueError("cannot reissue while old Worker owner remains alive")
            h_id = old["hypothesis_id"]
            from .science_graph_store import ScienceGraphStore
            with ScienceGraphStore(self.graph_path, readonly=True) as store:
                current = store.frontier_view().get(h_id)
            if not current or current["frontier_status"] != ACTIVE:
                raise ValueError("paused H not active in SQLite; require human frontier review")
            entry_node = h_id
            state["epoch"] += 1
            renewed = {**old,
                       "claim_id": uuid.uuid4().hex,
                       "fencing_epoch": state["epoch"],
                       "entry_node_id": entry_node,
                       "status": "claimed", "owner_pid": None,
                       "owner_start_ticks": None,
                       "heartbeat_at": timestamp(at),
                       "lease_expires_at": timestamp(at + timedelta(seconds=LEASE_SECONDS)),
                       "reissued_from_claim_id": old["claim_id"],
                       "reissued_reason": "explicit_operator_paused_resume"}
            state["claims"][h_id] = renewed
            _save_json(self.path, state)
            return dict(renewed)

    def reclaim(self, hypothesis_id: str, *, isolated: bool = False, now: datetime | None = None) -> bool:
        """Fail closed: mere expiry never proves that an old writer is stopped."""
        at = now or utcnow()
        with self._locked() as state:
            claim = state["claims"].get(hypothesis_id)
            if claim is None:
                return False
            expiry = datetime.fromisoformat(claim["lease_expires_at"])
            if at <= expiry or not isolated or process_identity_alive(
                    claim.get("owner_pid"), claim.get("owner_start_ticks")):
                return False
            del state["claims"][hypothesis_id]
            state["epoch"] += 1
            _save_json(self.path, state)
            return True

    def assert_writer(self, worker_id: str, claim_id: str, epoch: int,
                      hypothesis_id: str, *, now: datetime | None = None) -> None:
        """Fencing check for the single-writer SQLite graph commit path."""
        with self._locked() as state:
            claim = self._owned(state, worker_id, claim_id, epoch)
            if claim["hypothesis_id"] != hypothesis_id or (now or utcnow()) > datetime.fromisoformat(claim["lease_expires_at"]):
                raise ValueError("claim expired or target changed; refusing write")
            if claim["status"] == "paused":
                raise ValueError("paused Worker cannot commit graph changes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read/manage dynamic branch claims (never starts scientific LLMs)")
    parser.add_argument("--research-root", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    plan = sub.add_parser("plan")
    plan.add_argument("--resource-slots", type=int, default=None,
                      help="verified number of additional isolated Codex+compute slots")
    claim = sub.add_parser("claim")
    claim.add_argument("worker_id")
    claim.add_argument("--hypothesis")
    args = parser.parse_args(argv)
    claims = BranchClaims(args.research_root, args.database)
    if args.action == "status":
        result = claims.status()
    elif args.action == "plan":
        result = claims.plan(available_resource_slots=args.resource_slots)
    else:
        result = claims.claim(args.worker_id, hypothesis_id=args.hypothesis)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# A separate WorkerRuntime owns the child PID; never rewrite the legacy
# task-wide supervisor registry to advertise multiple independent processes.
def attach_owner(registry: "BranchClaims | CycleClaims", *, worker_id: str, claim_id: str,
                 epoch: int, owner_pid: int, owner_start_ticks: int | None) -> dict[str, Any]:
    """Bind a live process to a fresh claim (H or Cycle) before model execution."""
    if not process_identity_alive(owner_pid, owner_start_ticks):
        raise ValueError("cannot attach nonexistent Worker process")
    with registry._locked() as state:
        claim = registry._owned(state, worker_id, claim_id, epoch)
        if claim.get("owner_pid") and process_identity_alive(
                claim.get("owner_pid"), claim.get("owner_start_ticks")):
            raise ValueError("branch claim already has a live Worker")
        if utcnow() > datetime.fromisoformat(claim["lease_expires_at"]):
            raise ValueError("claim expired before Worker attach")
        claim["owner_pid"] = owner_pid
        claim["owner_start_ticks"] = owner_start_ticks
        claim["status"] = "claimed"
        _save_json(registry.path, state)
        return dict(claim)


def reserve_cycle_number(research_root: Path, goal_id: str) -> str:
    """Atomically allocate a global Cycle directory (gaps after crash are legal)."""
    from .specification import CYCLE_MARKER, SCHEMA, cycle_number

    root = Path(research_root).resolve()
    lock_path = root / ".theory-master" / "cycle_allocator.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as fp:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        highest = max((cycle_number(p.name) or 0 for p in root.iterdir() if p.is_dir()), default=0)
        ledger = root / ".theory-master" / "cycle_allocator.json"
        seen = _read_json(ledger)
        number = max(highest, int(seen.get("last_cycle") or 0)) + 1
        while True:
            target = root / str(number)
            try:
                target.mkdir()
            except FileExistsError:
                number += 1
                continue
            (target / CYCLE_MARKER).write_text(f"{SCHEMA}\n{goal_id}\n{timestamp(utcnow())}\n", encoding="utf-8")
            _save_json(ledger, {"last_cycle": number})
            return str(number)


def reserve_hypothesis_id(research_root: Path, graph_path: Path,
                          known_ids: list[str] | set[str] = (), *,
                          cycle_id: str | None = None) -> str:
    """Reserve a new H ID under a lock, using canonical SQLite identities only."""
    lock_path = Path(research_root).resolve() / ".theory-master" / "hypothesis_allocator.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as fp:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        from .science_graph_store import ScienceGraphStore
        with ScienceGraphStore(graph_path, readonly=True) as store:
            all_ids = [str(row["id"]) for row in store.db.execute("SELECT id FROM hypotheses")]
        all_ids.extend(known_ids)
        ledger = Path(research_root).resolve() / ".theory-master" / "hypothesis_allocator.json"
        old = _read_json(ledger)
        highest = max((int(s[2:]) for s in all_ids if re.fullmatch(r"H-\d{4,}", s)), default=0)
        assignments = dict(old.get("cycle_assignments") or {})
        if cycle_id is not None:
            if not str(cycle_id).isascii() or not str(cycle_id).isdecimal() or int(cycle_id) < 1:
                raise ValueError("New H requires a positive numeric Cycle ID")
            cycle_key = str(int(cycle_id))
            number = int(cycle_key)
            hypothesis = f"H-{number:04d}"
            previous = assignments.get(cycle_key)
            if previous is not None and previous != hypothesis:
                raise ValueError("Cycle/H identity allocator conflict")
            if hypothesis in all_ids and previous != hypothesis:
                raise ValueError(f"Cycle {cycle_key} H identity already exists: {hypothesis}")
            if not previous:
                assignments[cycle_key] = hypothesis
                _save_json(ledger, {**old, "last_hypothesis": max(highest, number, int(old.get("last_hypothesis") or 0)),
                                    "cycle_assignments": assignments})
            return hypothesis
        number = max(highest, int(old.get("last_hypothesis") or 0)) + 1
        _save_json(ledger, {**old, "last_hypothesis": number})
        return f"H-{number:04d}"


@contextmanager
def canvas_write_lock(research_root: Path) -> Iterator[None]:
    """Serializable Canvas science mutations shared by all worker processes."""
    path = Path(research_root).resolve() / ".theory-master" / "canvas_write.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fp:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)


def finish_owned_claim(registry: BranchClaims, *, worker_id: str, claim_id: str,
                       epoch: int, current_pid: int) -> None:
    """Final Worker instruction after terminal Cycle and durable output commit."""
    if isinstance(registry, CycleClaims):
        registry.finish(worker_id, claim_id, epoch, current_pid=current_pid)
        return
    with registry._locked() as state:
        claim = registry._owned(state, worker_id, claim_id, epoch)
        if claim.get("owner_pid") != current_pid or current_pid != os.getpid():
            raise ValueError("only original Worker process can finish its running claim")
        del state["claims"][claim["hypothesis_id"]]
        state["epoch"] += 1
        _save_json(registry.path, state)


CYCLE_CLAIMS_SCHEMA = "theory-master/cycle-claims/v1"
PROPOSAL_GATE_SCHEMA = "theory-master/proposal-gate/v1"
CLAIM_LIVE_STATUSES = frozenset({"claimed", "paused"})
CLAIM_TERMINAL_ACTIONS = {"wait_direction": "waiting_direction", "stopped_goal": "stopped_goal"}
# Archived claims with these statuses no longer own their Cycle/H identity.
CLAIM_IDENTITY_RELEASED = frozenset({"released", "superseded"})
_WORKER_ID = re.compile(r"worker-[1-9]\d*")
_HYPOTHESIS_ID = re.compile(r"H-\d{4,}")


def _assert_goal_open(research_root: Path, graph_path: Path | None) -> None:
    if graph_path is None:
        return
    from .goal_terminal import assert_goal_open
    assert_goal_open(Path(research_root), Path(graph_path))


def _cycle_key(cycle_id: Any) -> str:
    text = str(cycle_id)
    if not text.isascii() or not text.isdecimal() or int(text) < 1:
        raise ValueError("Cycle ID must be a positive decimal number")
    return str(int(text))


class CycleClaims:
    """Cycle-level run ownership with fencing tokens (V1.1, I-04/I-05/I-10).

    ``claims`` holds only live (claimed/paused) claims keyed by claim_id.
    Every terminal transition moves the claim to append-only ``history`` with
    its final status (finished/released/superseded/waiting_direction/
    stopped_goal); nothing is silently deleted and an archived token can never
    pass ``assert_writer`` again.
    """

    def __init__(self, research_root: Path, graph_path: Path | None = None):
        self.root = Path(research_root).resolve()
        self.graph_path = Path(graph_path).resolve() if graph_path is not None else None
        self.path = self.root / ".theory-master" / "cycle_claims.json"
        self.lock_path = self.root / ".theory-master" / "cycle_claims.lock"

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                value = _read_json(self.path)
                if not value:
                    value = {"schema": CYCLE_CLAIMS_SCHEMA, "epoch": 0, "claims": {}, "history": []}
                value.setdefault("history", [])
                if (value.get("schema") != CYCLE_CLAIMS_SCHEMA or not isinstance(value.get("claims"), dict)
                        or not isinstance(value["history"], list)):
                    raise ValueError("incompatible CycleClaims registry")
                yield value
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _owned(state: dict[str, Any], worker_id: str, claim_id: str, epoch: int) -> dict[str, Any]:
        claim = state["claims"].get(claim_id)
        if claim is None or claim.get("worker_id") != worker_id or claim.get("fencing_epoch") != epoch:
            raise ValueError("stale or unowned claim: fencing token rejected")
        return claim

    @staticmethod
    def _expired(claim: dict[str, Any], at: datetime) -> bool:
        return at > datetime.fromisoformat(claim["lease_expires_at"])

    @staticmethod
    def _archive(state: dict[str, Any], claim: dict[str, Any], status: str,
                 at: datetime, **extra: Any) -> dict[str, Any]:
        del state["claims"][claim["claim_id"]]
        record = {**claim, **extra, "status": status, "ended_at": timestamp(at)}
        state["history"].append(record)
        state["epoch"] += 1
        return record

    @staticmethod
    def _identity_owner(state: dict[str, Any], field: str, value: str,
                        claim_id: str) -> dict[str, Any] | None:
        for claim in [*state["claims"].values(), *state["history"]]:
            if claim.get("claim_id") == claim_id or claim.get("status") in CLAIM_IDENTITY_RELEASED:
                continue
            if claim.get(field) == value:
                return claim
        return None

    def claim_cycle(self, worker_id: str, *, owner_pid: int | None = None,
                    owner_start_ticks: int | None = None, now: datetime | None = None) -> dict[str, Any]:
        if not _WORKER_ID.fullmatch(worker_id):
            raise ValueError("worker ID must be worker-N (N>=1)")
        _assert_goal_open(self.root, self.graph_path)
        at = now or utcnow()
        with self._locked() as state:
            if runtime_health(self.root).get("active"):
                raise ValueError("legacy single-Worker runtime is active; claims must not run concurrently")
            if any(c.get("worker_id") == worker_id for c in state["claims"].values()):
                raise ValueError(f"{worker_id} already has a non-terminal cycle claim")
            state["epoch"] += 1
            claim = {"claim_id": uuid.uuid4().hex, "fencing_epoch": state["epoch"],
                     "worker_id": worker_id, "cycle_id": None, "hypothesis_id": None,
                     "status": "claimed", "acquired_at": timestamp(at), "heartbeat_at": timestamp(at),
                     "lease_expires_at": timestamp(at + timedelta(seconds=LEASE_SECONDS)),
                     "owner_pid": owner_pid, "owner_start_ticks": owner_start_ticks}
            state["claims"][claim["claim_id"]] = claim
            _save_json(self.path, state)
            return dict(claim)

    def _bind(self, worker_id: str, claim_id: str, epoch: int, field: str, value: str,
              now: datetime | None) -> dict[str, Any]:
        at = now or utcnow()
        with self._locked() as state:
            claim = self._owned(state, worker_id, claim_id, epoch)
            if claim.get(field) == value:
                return dict(claim)
            if claim.get(field) is not None:
                raise ValueError(f"claim already bound to {field}={claim[field]}; refusing rebind")
            if claim["status"] != "claimed" or self._expired(claim, at):
                raise ValueError("paused or expired claim cannot bind a new identity")
            if field == "hypothesis_id" and claim.get("cycle_id") is None:
                raise ValueError("bind the Cycle before binding its Hypothesis")
            other = self._identity_owner(state, field, value, claim_id)
            if other is not None:
                raise ValueError(f"I-04: {field}={value} already owned by "
                                 f"{other['worker_id']} claim {other['claim_id']} ({other['status']})")
            claim[field] = value
            _save_json(self.path, state)
            return dict(claim)

    def bind_cycle(self, worker_id: str, claim_id: str, epoch: int, cycle_id: str, *,
                   now: datetime | None = None) -> dict[str, Any]:
        return self._bind(worker_id, claim_id, epoch, "cycle_id", _cycle_key(cycle_id), now)

    def bind_hypothesis(self, worker_id: str, claim_id: str, epoch: int, hypothesis_id: str, *,
                        now: datetime | None = None) -> dict[str, Any]:
        if not _HYPOTHESIS_ID.fullmatch(str(hypothesis_id)):
            raise ValueError("Hypothesis ID must be H-NNNN")
        return self._bind(worker_id, claim_id, epoch, "hypothesis_id", str(hypothesis_id), now)

    def attach_owner(self, worker_id: str, claim_id: str, epoch: int, *, owner_pid: int,
                     owner_start_ticks: int | None) -> dict[str, Any]:
        return attach_owner(self, worker_id=worker_id, claim_id=claim_id, epoch=epoch,
                            owner_pid=owner_pid, owner_start_ticks=owner_start_ticks)

    def update(self, worker_id: str, claim_id: str, epoch: int, *,
               action: str, now: datetime | None = None) -> dict[str, Any]:
        if action not in {"heartbeat", "pause", "resume", "release", *CLAIM_TERMINAL_ACTIONS}:
            raise ValueError("invalid claim operation")
        at = now or utcnow()
        with self._locked() as state:
            claim = self._owned(state, worker_id, claim_id, epoch)
            if action == "release":
                if process_identity_alive(claim.get("owner_pid"), claim.get("owner_start_ticks")):
                    raise ValueError("cannot release claim while owner process is alive; isolate it first")
                result = self._archive(state, claim, "released", at)
            elif action in CLAIM_TERMINAL_ACTIONS:
                result = self._archive(state, claim, CLAIM_TERMINAL_ACTIONS[action], at)
            else:
                if self._expired(claim, at):
                    raise ValueError("claim lease expired; do not revive a stale Worker")
                if action == "pause":
                    claim["status"] = "paused"
                elif action == "resume":
                    claim["status"] = "claimed"
                claim["heartbeat_at"] = timestamp(at)
                claim["lease_expires_at"] = timestamp(at + timedelta(seconds=LEASE_SECONDS))
                result = claim
            _save_json(self.path, state)
            return dict(result)

    def assert_active(self, worker_id: str, claim_id: str, epoch: int, *,
                      now: datetime | None = None) -> dict[str, Any]:
        """Token is current, running (not paused) and within its lease."""
        with self._locked() as state:
            claim = self._owned(state, worker_id, claim_id, epoch)
            if claim["status"] != "claimed":
                raise ValueError("paused Worker cannot act on its claim")
            if self._expired(claim, now or utcnow()):
                raise ValueError("claim lease expired; refusing")
            return dict(claim)

    def assert_writer(self, worker_id: str, claim_id: str, epoch: int, *, cycle_id: str,
                      hypothesis_id: str | None = None, now: datetime | None = None) -> None:
        """Fencing check for science writes of one Cycle (and optionally its H)."""
        claim = self.assert_active(worker_id, claim_id, epoch, now=now)
        if claim.get("cycle_id") is None or claim["cycle_id"] != _cycle_key(cycle_id):
            raise ValueError("claim does not own this Cycle; refusing write")
        if hypothesis_id is not None and claim.get("hypothesis_id") != hypothesis_id:
            raise ValueError("claim does not own this Hypothesis; refusing write")

    def reissue_for_resume(self, worker_id: str, claim_id: str, epoch: int, *,
                           now: datetime | None = None) -> dict[str, Any]:
        """New claim_id + epoch for the SAME Cycle/H (I-10); old owner must be dead."""
        _assert_goal_open(self.root, self.graph_path)
        at = now or utcnow()
        with self._locked() as state:
            old = self._owned(state, worker_id, claim_id, epoch)
            if process_identity_alive(old.get("owner_pid"), old.get("owner_start_ticks")):
                raise ValueError("cannot reissue while old Worker owner remains alive")
            new_id = uuid.uuid4().hex
            self._archive(state, old, "superseded", at, superseded_by_claim_id=new_id)
            renewed = {**old, "claim_id": new_id, "fencing_epoch": state["epoch"],
                       "status": "claimed", "owner_pid": None, "owner_start_ticks": None,
                       "heartbeat_at": timestamp(at),
                       "lease_expires_at": timestamp(at + timedelta(seconds=LEASE_SECONDS)),
                       "reissued_from_claim_id": old["claim_id"], "reissued_reason": "resume"}
            state["claims"][new_id] = renewed
            _save_json(self.path, state)
            return dict(renewed)

    def finish(self, worker_id: str, claim_id: str, epoch: int, *, current_pid: int,
               now: datetime | None = None) -> None:
        at = now or utcnow()
        with self._locked() as state:
            claim = self._owned(state, worker_id, claim_id, epoch)
            if claim.get("owner_pid") != current_pid or current_pid != os.getpid():
                raise ValueError("only original Worker process can finish its running claim")
            self._archive(state, claim, "finished", at)
            _save_json(self.path, state)

    def running_hypotheses(self) -> list[dict[str, Any]]:
        """G5 claim part: live claims with a bound H (SQLite freeze_incomplete is separate)."""
        with self._locked() as state:
            rows = [{"worker_id": c["worker_id"], "cycle_id": c.get("cycle_id"),
                     "hypothesis_id": c["hypothesis_id"], "status": c["status"]}
                    for c in state["claims"].values() if c.get("hypothesis_id")]
        return sorted(rows, key=lambda row: row["hypothesis_id"])

    def list_claims(self) -> list[dict[str, Any]]:
        with self._locked() as state:
            return sorted((dict(c) for c in state["claims"].values()),
                          key=lambda c: c["fencing_epoch"])

    def history(self) -> list[dict[str, Any]]:
        with self._locked() as state:
            return [dict(c) for c in state["history"]]


class ProposalGateError(RuntimeError):
    """Gate ownership violated (not holder / duplicate waiter)."""


class ProposalGateBusy(ProposalGateError):
    """Non-blocking acquire found the gate held or other waiters ahead."""


class ProposalGate:
    """Task-wide serial proposal entry (I-01/I-03, G2).

    FIFO waiters; a holder is revoked only when its process is provably dead
    (no lease expiry for a live holder: fail closed). Waiters themselves
    recover a dead holder so a crashed N=1 runner cannot wedge the gate.
    N=1 uses worker_id="single" with claim_id/epoch None; worker-N must hold a
    running CycleClaims token.
    """

    def __init__(self, research_root: Path, graph_path: Path | None = None):
        self.root = Path(research_root).resolve()
        self.graph_path = Path(graph_path).resolve() if graph_path is not None else None
        base = self.root / ".theory-master"
        self.path = base / "proposal_gate.json"
        self.lock_path = base / "proposal_gate.lock"
        self.events_path = base / "proposal_gate_events.jsonl"

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                value = _read_json(self.path)
                if not value:
                    value = {"schema": PROPOSAL_GATE_SCHEMA, "sequence": 0, "holder": None,
                             "queue": [], "last_release": None}
                if value.get("schema") != PROPOSAL_GATE_SCHEMA or not isinstance(value.get("queue"), list):
                    raise ValueError("incompatible ProposalGate state")
                yield value
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _event(self, event: str, **fields: Any) -> None:
        line = json.dumps({"at": timestamp(utcnow()), "event": event, **fields}, ensure_ascii=False)
        with open(self.events_path, "a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _prune_dead_waiters(self, state: dict[str, Any]) -> bool:
        alive = []
        for waiter in state["queue"]:
            if process_identity_alive(waiter.get("owner_pid"), waiter.get("owner_start_ticks")):
                alive.append(waiter)
            else:
                self._event("prune_waiter", worker_id=waiter.get("worker_id"),
                            waiter_id=waiter.get("waiter_id"), reason="waiter_process_dead")
        changed = len(alive) != len(state["queue"])
        state["queue"] = alive
        return changed

    def _release_locked(self, state: dict[str, Any], *, reason: str, event: str) -> dict[str, Any]:
        holder = state["holder"]
        state["holder"] = None
        state["last_release"] = {**holder, "released_at": timestamp(utcnow()),
                                 "reason": reason, "event": event}
        self._event(event, gate_token=holder["gate_token"], worker_id=holder.get("worker_id"),
                    claim_id=holder.get("claim_id"), reason=reason)
        return holder

    def _recover_locked(self, state: dict[str, Any], reason: str) -> dict[str, Any] | None:
        holder = state.get("holder")
        if holder and not process_identity_alive(holder.get("owner_pid"), holder.get("owner_start_ticks")):
            return self._release_locked(state, reason=reason, event="recover")
        return None

    def _validate_requester(self, worker_id: str, claim_id: str | None, epoch: int | None,
                            owner_pid: int | None) -> None:
        if not owner_pid or int(owner_pid) < 1:
            raise ValueError("ProposalGate requires the requesting process id")
        if worker_id == "single":
            return
        if not _WORKER_ID.fullmatch(worker_id):
            raise ValueError("worker ID must be 'single' or worker-N (N>=1)")
        if claim_id is None or epoch is None:
            raise ValueError("worker-N must present its Cycle claim token")
        CycleClaims(self.root).assert_active(worker_id, claim_id, epoch)

    def _dequeue(self, waiter_id: str, reason: str) -> None:
        with self._locked() as state:
            before = len(state["queue"])
            state["queue"] = [w for w in state["queue"] if w.get("waiter_id") != waiter_id]
            if len(state["queue"]) != before:
                self._event("dequeue", waiter_id=waiter_id, reason=reason)
                _save_json(self.path, state)

    def acquire(self, *, worker_id: str, claim_id: str | None, epoch: int | None,
                owner_pid: int, owner_start_ticks: int | None, wait: bool = True,
                poll_seconds: float = 2.0, timeout: float | None = None) -> str:
        self._validate_requester(worker_id, claim_id, epoch, owner_pid)
        waiter = {"waiter_id": uuid.uuid4().hex, "worker_id": worker_id, "claim_id": claim_id,
                  "fencing_epoch": epoch, "owner_pid": owner_pid,
                  "owner_start_ticks": owner_start_ticks, "enqueued_at": timestamp(utcnow())}
        deadline = None if timeout is None else time.monotonic() + timeout
        enqueued = granted = False
        outcome = "abandoned"
        try:
            while True:
                _assert_goal_open(self.root, self.graph_path)
                with self._locked() as state:
                    changed = self._prune_dead_waiters(state)
                    changed |= self._recover_locked(state, "dead_holder_recovered_by_waiter") is not None
                    holder = state["holder"]
                    if holder and holder.get("worker_id") == worker_id:
                        raise ProposalGateError(f"{worker_id} already holds the proposal gate")
                    if not enqueued:
                        if any(w.get("worker_id") == worker_id for w in state["queue"]):
                            raise ProposalGateError(f"{worker_id} is already waiting for the proposal gate")
                        state["queue"].append(waiter)
                        enqueued = changed = True
                        self._event("enqueue", worker_id=worker_id, claim_id=claim_id,
                                    waiter_id=waiter["waiter_id"])
                    elif not any(w.get("waiter_id") == waiter["waiter_id"] for w in state["queue"]):
                        enqueued = False
                        raise ProposalGateError("waiter was removed from the proposal gate queue")
                    if holder is None and state["queue"][0]["waiter_id"] == waiter["waiter_id"]:
                        state["queue"].pop(0)
                        state["sequence"] = int(state.get("sequence") or 0) + 1
                        token = uuid.uuid4().hex
                        state["holder"] = {"gate_token": token, "sequence": state["sequence"],
                                           "worker_id": worker_id, "claim_id": claim_id,
                                           "fencing_epoch": epoch, "owner_pid": owner_pid,
                                           "owner_start_ticks": owner_start_ticks,
                                           "enqueued_at": waiter["enqueued_at"],
                                           "acquired_at": timestamp(utcnow())}
                        self._event("acquire", gate_token=token, worker_id=worker_id, claim_id=claim_id)
                        _save_json(self.path, state)
                        granted = True
                        return token
                    if changed:
                        _save_json(self.path, state)
                if not wait:
                    outcome = "busy_no_wait"
                    raise ProposalGateBusy("proposal gate is held or has earlier waiters")
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        outcome = "timeout"
                        raise TimeoutError("timed out waiting for the proposal gate")
                    time.sleep(min(poll_seconds, remaining))
                else:
                    time.sleep(poll_seconds)
        except BaseException as exc:
            if outcome == "abandoned":
                outcome = f"exception:{type(exc).__name__}"
            raise
        finally:
            if enqueued and not granted:
                self._dequeue(waiter["waiter_id"], outcome)

    def assert_holder(self, gate_token: str) -> None:
        with self._locked() as state:
            holder = state.get("holder")
            if not holder or holder.get("gate_token") != gate_token:
                raise ProposalGateError("proposal gate token is not the current holder")

    def release(self, gate_token: str, *, reason: str) -> bool:
        with self._locked() as state:
            holder = state.get("holder")
            if not holder or holder.get("gate_token") != gate_token:
                return False
            self._release_locked(state, reason=reason, event="release")
            _save_json(self.path, state)
            return True

    def recover_dead_holder(self) -> dict[str, Any] | None:
        with self._locked() as state:
            changed = self._prune_dead_waiters(state)
            recovered = self._recover_locked(state, "dead_holder_recovered")
            if changed or recovered is not None:
                _save_json(self.path, state)
            return dict(recovered) if recovered is not None else None

    def status(self) -> dict[str, Any]:
        with self._locked() as state:
            result = json.loads(json.dumps(state))
        holder = result.get("holder")
        result["holder_alive"] = bool(holder) and process_identity_alive(
            holder.get("owner_pid"), holder.get("owner_start_ticks"))
        return result

    @contextmanager
    def holding(self, *, release_reason: str = "hypothesis_frozen", **kw: Any) -> Iterator[str]:
        token = self.acquire(**kw)
        reason = release_reason
        try:
            yield token
        except BaseException as exc:
            reason = f"exception:{type(exc).__name__}"
            raise
        finally:
            self.release(token, reason=reason)
