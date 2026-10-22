from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime import atomic

LEDGER_SCHEMA = "theory-master/invocation-ledger/v1"
TERMINAL_STATES = frozenset({"completed", "committed", "failed", "cancelled", "quarantined", "interrupted", "superseded"})


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Reconciliation:
    action: str
    reason: str


class InvocationLedger:
    """Append-only ledger for agent and external invocations.

    The ledger records observations and state transitions.  It never claims
    exactly-once execution; callers use input/artifact hashes to reuse,
    reattach, quarantine, or rerun work after recovery.
    """

    def __init__(self, path: Path):
        self.path = path

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        record = {"schema": LEDGER_SCHEMA, "recorded_at": now(), **event}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        prior = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
        atomic(self.path, prior + json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record

    def records(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
        return records

    def latest(self, invocation_id: str) -> dict[str, Any] | None:
        for record in reversed(self.records()):
            if record.get("invocation_id") == invocation_id:
                return record
        return None

    def snapshot(self, invocation_id: str) -> dict[str, Any] | None:
        """Fold every append-only event for one invocation into its logical state."""
        folded: dict[str, Any] = {}
        found = False
        for record in self.records():
            if record.get("invocation_id") != invocation_id:
                continue
            folded.update(record)
            found = True
        return folded if found else None

    def snapshots(self) -> list[dict[str, Any]]:
        """Return invocation snapshots in first-seen order, preserving prepared metadata."""
        order: list[str] = []
        folded: dict[str, dict[str, Any]] = {}
        for record in self.records():
            invocation_id = str(record.get("invocation_id") or "")
            if not invocation_id:
                continue
            if invocation_id not in folded:
                folded[invocation_id] = {}
                order.append(invocation_id)
            folded[invocation_id].update(record)
        return [folded[invocation_id] for invocation_id in order]

    def find_reusable(
        self, *, logical_key: str, contract_ref: dict[str, Any], role: str, stage: str,
    ) -> dict[str, Any] | None:
        """Find the newest artifact-complete invocation for one deterministic workflow slot."""
        contract_hash = stable_hash(contract_ref)
        for snapshot in reversed(self.snapshots()):
            state = str(snapshot.get("state") or "")
            if state not in {"completed", "committed"}:
                continue
            if state == "committed" and snapshot.get("commit_state") != "committed":
                continue
            if not str(snapshot.get("closure_hash") or ""):
                continue
            if str(snapshot.get("logical_key") or "") != logical_key:
                continue
            if str(snapshot.get("role") or "") != role or str(snapshot.get("stage") or "") != stage:
                continue
            if stable_hash(snapshot.get("contract_ref") or {}) != contract_hash:
                continue
            return snapshot
        return None

    def find_committed(
        self, *, logical_key: str, contract_ref: dict[str, Any], role: str, stage: str,
    ) -> dict[str, Any] | None:
        """Find the newest committed invocation for one deterministic workflow slot."""
        contract_hash = stable_hash(contract_ref)
        for snapshot in reversed(self.snapshots()):
            if snapshot.get("state") != "committed" or snapshot.get("commit_state") != "committed":
                continue
            if str(snapshot.get("logical_key") or "") != logical_key:
                continue
            if str(snapshot.get("role") or "") != role or str(snapshot.get("stage") or "") != stage:
                continue
            if stable_hash(snapshot.get("contract_ref") or {}) != contract_hash:
                continue
            return snapshot
        return None

    def prepare(
        self,
        *,
        invocation_id: str,
        kind: str,
        input_payload: Any,
        contract_ref: dict[str, Any] | None = None,
        attempt: int = 1,
        expected_artifacts: list[dict[str, Any]] | None = None,
        external_job_id: str | None = None,
        predecessor_invocation_id: str | None = None,
        logical_key: str | None = None,
        role: str | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        return self.append({
            "event": "invocation.prepared",
            "invocation_id": invocation_id,
            "kind": kind,
            "attempt": attempt,
            "input_hash": stable_hash(input_payload),
            "contract_ref": contract_ref or {},
            "expected_artifacts": expected_artifacts or [],
            "external_job_id": external_job_id,
            "predecessor_invocation_id": predecessor_invocation_id,
            "logical_key": logical_key,
            "role": role,
            "stage": stage,
            "state": "prepared",
            "commit_state": "not_committed",
        })

    def transition(self, invocation_id: str, state: str, **details: Any) -> dict[str, Any]:
        if not invocation_id:
            raise ValueError("invocation_id is required")
        return self.append({"event": f"invocation.{state}", "invocation_id": invocation_id,
                            "state": state, **details})


def reconcile(
    latest: dict[str, Any] | None,
    *,
    input_hash: str,
    artifacts_valid: bool,
    external_alive: bool = False,
) -> Reconciliation:
    if latest is None:
        return Reconciliation("rerun", "no ledger record")
    if latest.get("input_hash") and latest.get("input_hash") != input_hash:
        return Reconciliation("quarantine", "input hash mismatch")
    state = str(latest.get("state") or "")
    if external_alive and state in {"prepared", "running"}:
        return Reconciliation("reattach", "external execution is still alive")
    if artifacts_valid and state in {"running", "completed", "committed"}:
        return Reconciliation("reuse", "validated artifacts already exist")
    if state in TERMINAL_STATES and not artifacts_valid:
        return Reconciliation("rerun", "terminal record has no valid artifacts")
    return Reconciliation("rerun", "execution cannot be reconciled safely")
