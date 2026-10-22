"""Evidence-bound correction of the old ValueError -> protocol violation mapper.

This is a no-model, no-grant state transition. It never resumes a Cycle. The
CLI owns scheduler.lock; callers already holding that lock pass it explicitly.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path

from .investigator_budget import (
    LEGACY_BRIDGE_REL, LEDGER_REL, _bridged_first_review, _entries,
    budget_status, three_plus_three_enabled,
)
from .runtime import now, write_json

LEGACY_ERROR = "3+3 Cycle contains a legacy budget ledger entry"
RECEIPT_REL = Path("budget") / "legacy_failure_reconciliation.json"


def _read(path: Path, cycle: Path, hashes: dict) -> object:
    if not path.is_relative_to(cycle) or any(
        p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(cycle)
    ) or not path.is_file():
        raise ValueError(f"Unsafe or missing reconciliation evidence: {path.name}")
    raw = path.read_bytes()
    hashes[str(path.relative_to(cycle))] = hashlib.sha256(raw).hexdigest()
    return json.loads(raw)


def _alive(pid: object) -> bool:
    if not pid:
        return False
    try:
        number = int(pid)
        if number <= 0:
            return True
        os.kill(number, 0)
        return True
    except ProcessLookupError:
        return False
    except (ValueError, TypeError, PermissionError):
        return True


def inspect_legacy_failure(cycle: Path, state: dict | None = None) -> dict:
    """Fail closed unless every independent validation proves this exact repair."""
    cycle = Path(cycle).absolute()
    if cycle.is_symlink() or cycle.parent.is_symlink():
        raise ValueError("Cycle must be a real directory")
    hashes: dict[str, str] = {}
    current = _read(cycle / "research_state.json", cycle, hashes)
    if not isinstance(current, dict) or (state is not None and state != current):
        raise ValueError("Authoritative state changed before reconciliation")
    projection = _read(cycle / "run_state.json", cycle, hashes)
    if not isinstance(projection, dict):
        raise ValueError("Invalid run-state projection")
    if not isinstance(current.get("recovery") or {}, dict):
        raise ValueError("Invalid recovery record")
    eligible_health = current.get("protocol_health") == "violation" or (
        current.get("protocol_health") == "valid"
        and (current.get("recovery") or {}).get("category") == "budget_validation"
        and (current.get("recovery") or {}).get("message") == LEGACY_ERROR)
    if (not eligible_health
            or not three_plus_three_enabled(current)
            or str(current.get("cycle_id")) != cycle.name
            or not current.get("resumable")
            or current.get("abandoned_cycle")
            or current.get("phase") == "abandon_requested"):
        raise ValueError("Not an eligible legacy budget violation")
    for snapshot in (current, projection):
        if (str(snapshot.get("status") or "").lower() not in {"failed", "paused", "interrupted"}
                or snapshot.get("execution_active") or _alive(snapshot.get("owner_pid"))):
            raise ValueError("Cycle must be stopped with no live owner")
    from .worker_runtime import list_workers
    if any(str(w.get("current_cycle")) == cycle.name and w.get("process_alive")
           for w in list_workers(cycle.parent)):
        raise ValueError("Cycle still has a live Worker")
    if (cycle / "recovery_control.json").exists():
        raise ValueError("Pending operator control must be handled before reconciliation")
    checkpoint_path = Path(str(current.get("last_safe_checkpoint") or ""))
    if checkpoint_path.parent != cycle / "checkpoints":
        raise ValueError("Checkpoint is outside this Cycle")
    checkpoint = _read(checkpoint_path, cycle, hashes)
    lock = current.get("hypothesis_lock") or {}
    if (not isinstance(checkpoint, dict) or not isinstance(lock, dict) or lock.get("status") != "locked"
            or not lock.get("id") or checkpoint.get("hypothesis_lock") != lock
            or str(checkpoint.get("cycle_id")) != cycle.name
            or projection.get("last_safe_checkpoint") != str(checkpoint_path)):
        raise ValueError("Frozen checkpoint identity does not match authoritative state")
    events_path = cycle / "events.jsonl"
    # JSONL is validated in full: an incomplete or ambiguous history cannot
    # authorize clearing a sticky guard.
    if events_path.is_symlink():
        raise ValueError("Unsafe event history")
    raw = events_path.read_bytes()
    hashes["events.jsonl"] = hashlib.sha256(raw).hexdigest()
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if any(not isinstance(row, dict) or not isinstance(row.get("payload", {}), dict)
           for row in events):
        raise ValueError("Invalid event history")
    seqs = [int(row["seq"]) for row in events]
    if seqs != sorted(set(seqs)):
        raise ValueError("Event history sequence is inconsistent")
    failures = [row for row in events if row.get("type") == "run.failed"]
    if not failures or failures[-1].get("payload", {}).get("error") != LEGACY_ERROR:
        raise ValueError("Latest recorded failure is not the legacy budget migration")
    failure = failures[-1]
    # Explicit protocol violations have their own artifacts/events. A bridge
    # cannot serve as a repair for any of them, even if budget_status passes.
    if (any((cycle / "contract_repairs").glob("*.json"))
            or any(row.get("type") in {"invocation.quarantined", "protocol.violation"}
                   or "contract_repair_failed" in json.dumps(row)
                   for row in events)):
        raise ValueError("Independent protocol violation evidence exists")
    before = []
    for path in sorted((cycle / "checkpoints").glob("*.json")):
        item = _read(path, cycle, hashes)
        if not isinstance(item, dict):
            raise ValueError("Invalid checkpoint history")
        if not isinstance(item.get("recovery") or {}, dict):
            raise ValueError("Invalid checkpoint recovery record")
        if (item.get("recovery") or {}).get("action") in {
            "contract_repair_failed", "evidence_artifact_validation_failed"
        }:
            raise ValueError("Independent protocol violation checkpoint exists")
        if int(item.get("last_seq") or 0) < int(failure["seq"]):
            before.append(item)
    if not before or max(before, key=lambda x: int(x.get("last_seq") or 0)).get("protocol_health") != "valid":
        raise ValueError("No valid protocol checkpoint before the migration failure")
    for rel in (LEGACY_BRIDGE_REL, Path("budget/internal_review_0001.json")):
        _read(cycle / rel, cycle, hashes)
    ledger = cycle / LEDGER_REL
    if ledger.is_symlink():
        raise ValueError("Unsafe budget ledger")
    hashes[str(LEDGER_REL)] = hashlib.sha256(ledger.read_bytes()).hexdigest()
    rows = _entries(cycle)
    if not rows or not _bridged_first_review(cycle, rows[0]):
        raise ValueError("Legacy approval bridge is not verified")
    budget = budget_status(cycle, cycle.parent, current)
    if budget["exhausted"] or budget["rejected"]:
        raise ValueError("Budget guard remains active")
    return {"schema": "theory-master/legacy-budget-failure-reconciliation/v1",
            "cycle_id": cycle.name, "at": now(), "failure_seq": failure["seq"],
            "failure": LEGACY_ERROR, "previous_protocol_health": current["protocol_health"],
            "protocol_health": "valid", "budget": budget, "evidence_sha256": hashes,
            "checkpoint": str(checkpoint_path), "status": "validated",
            "resume_performed": False, "token_grant": False}


def reconcile_legacy_failure(cycle: Path, *, state: dict | None = None,
                             scheduler_locked: bool = False) -> dict:
    """Apply under scheduler + ledger locks, retaining immutable evidence."""
    cycle = Path(cycle).absolute()
    lock_path = cycle.parent / ".theory-master" / "scheduler.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:
        if not scheduler_locked:
            handle = stack.enter_context(lock_path.open("a+"))
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger_lock = stack.enter_context((cycle / "budget/investigator_review.lock").open("a+"))
        fcntl.flock(ledger_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = inspect_legacy_failure(cycle, state)
        current = json.loads((cycle / "research_state.json").read_text())
        projection = json.loads((cycle / "run_state.json").read_text())
        for rel, digest in receipt["evidence_sha256"].items():
            if hashlib.sha256((cycle / rel).read_bytes()).hexdigest() != digest:
                raise ValueError("Evidence changed during reconciliation")
        # Durable intent precedes the state transition, so an interrupted write
        # still leaves the original state hashes and reason available for audit.
        receipt_path = cycle / RECEIPT_REL
        if receipt_path.is_symlink() or receipt_path.exists():
            raise ValueError("A reconciliation receipt already exists; inspect it before retry")
        write_json(receipt_path, receipt)
        for snapshot in (current, projection):
            snapshot["protocol_health"] = "valid"
            snapshot["investigator_budget"] = receipt["budget"]
            snapshot["budget_failure_reconciliation"] = str(receipt_path)
        write_json(cycle / "research_state.json", current)
        write_json(cycle / "run_state.json", projection)
        if state is not None:
            state.clear()
            state.update(current)
        from .observability.incident_registry import register_cycle_incident, record_repair_handoff
        incident, _ = register_cycle_incident(
            cycle, severity="error", kind="system_failure", code="budget_legacy_migration",
            source_event="budget.reconciliation", summary=LEGACY_ERROR,
            discriminator=str(receipt["failure_seq"]),
            fields={"original_failure_seq": receipt["failure_seq"]})
        record_repair_handoff(cycle, incident=incident, status="repair_validated",
                              target="sdk:budget_reconciliation", review_ref=str(receipt_path))
        from .supervisor_incident_session import (
            _lock, _read_session, _session_file, _append_message, _write_link,
        )
        link_path = cycle / "supervisor/incident_link.json"
        if link_path.is_file() and not link_path.is_symlink():
            link = json.loads(link_path.read_text())
            if link.get("trigger_type") == "protocol_violation":
                with _lock(cycle.parent):
                    record = _read_session(_session_file(cycle.parent, link["session_id"]))
                    if str(record.get("cycle_id")) == cycle.name:
                        _append_message(cycle.parent, record["id"], "system",
                            "Legacy budget migration and its misclassified protocol flag were verified and corrected. "
                            "Cycle execution was not resumed; existing operator pause and all resume guards remain. "
                            + str(receipt_path), {"source": "budget_reconciliation", "receipt": str(receipt_path)})
                        record["repair_status"] = "validated_pending_resume"
                        write_json(_session_file(cycle.parent, record["id"]), record)
                        _write_link(cycle, record)
        return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cycle", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    result = reconcile_legacy_failure(args.cycle) if args.apply else inspect_legacy_failure(args.cycle)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
