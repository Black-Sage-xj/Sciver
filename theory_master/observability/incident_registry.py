"""Unified important-event registry: anomalies and significant scientific control events."""
from __future__ import annotations

import hashlib
import json
import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from theory_master.runtime import atomic, now
from .incident_routing import classify_incident

LOG_NAME = "important_events.jsonl"
LEGACY_LOG_NAME = "cycle_anomalies.jsonl"
SCHEMA = "theory-master/important-event/v1"
REPAIR_MODEL = "gpt-6-luna"
REPAIR_REASONING_EFFORT = "medium"


def _summary(value: Any) -> str:
    return " ".join(str(value or "").split())[:1200]


def _rows(cycle_dir: Path) -> list[dict[str, Any]]:
    # Read old anomaly ledgers alongside new unified important events.
    # Never rewrite historical scientific/operational records.
    paths = [Path(cycle_dir) / LEGACY_LOG_NAME, Path(cycle_dir) / LOG_NAME]
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


@contextmanager
def _registry_lock(cycle_dir: Path):
    """Serialize the read/modify/write transaction across SDK, Collector, Supervisor.

    Replacing the log atomically is not enough: concurrent registering
    processes can otherwise lose entire events between reads and writes.
    """
    lock_path = Path(cycle_dir) / (LOG_NAME + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _append_unlocked(cycle_dir: Path, record: dict[str, Any]) -> None:
    path = Path(cycle_dir) / LOG_NAME
    prior = path.read_text(encoding="utf-8") if path.is_file() else ""
    atomic(path, prior + json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _append(cycle_dir: Path, record: dict[str, Any]) -> None:
    with _registry_lock(cycle_dir):
        _append_unlocked(cycle_dir, record)


def incident_key(
    *,
    cycle_id: str,
    severity: str,
    kind: str,
    code: str | None = None,
    discriminator: str | None = None,
) -> str:
    canonical = json.dumps(
        {
            "cycle_id": str(cycle_id),
            "severity": str(severity),
            "kind": str(kind),
            "code": str(code or ""),
            "discriminator": str(discriminator or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"{severity}:{kind}:{digest}"


def register_cycle_incident(
    cycle_dir: Path,
    *,
    severity: str,
    kind: str,
    source_event: str,
    summary: str,
    code: str | None = None,
    discriminator: str | None = None,
    fields: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    cycle_dir = Path(cycle_dir)
    cycle_id = cycle_dir.name
    key = incident_key(
        cycle_id=cycle_id,
        severity=severity,
        kind=kind,
        code=code,
        discriminator=discriminator,
    )
    incident_id = ("EVT-" if severity == "important" else "INC-") + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    with _registry_lock(cycle_dir):
        for row in _rows(cycle_dir):
            if row.get("incident_key") == key and row.get("lifecycle") == "registered":
                return row, False
        record: dict[str, Any] = {
            "schema": SCHEMA,
            "at": now(),
            "incident_id": incident_id,
            "incident_key": key,
            "lifecycle": "registered",
            "severity": severity,
            "kind": kind,
            "source_event": source_event,
            "cycle_id": cycle_id,
            "code": code,
            "summary": _summary(summary),
            "routing": classify_incident(
                severity=severity, kind=kind, code=code, summary=summary, fields=fields,
            ),
        }
        if fields:
            record.update(fields)
        _append_unlocked(cycle_dir, record)
    return record, True



def record_incident_lifecycle(
    cycle_dir: Path, *, incident: dict[str, Any], lifecycle: str,
    source_event: str, state_from: str, state_to: str,
) -> dict[str, Any]:
    """Append an auditable occurrence or resume attempt, without erasing history.

    resume_attempt does NOT claim the issue was resolved.
    A subsequent recurrence is a separate event linked to the same incident.
    """
    if lifecycle not in {"resume_attempt", "recurred"}:
        raise ValueError("unsupported incident lifecycle")
    record = {
        "schema": SCHEMA,
        "at": now(),
        "incident_id": incident["incident_id"],
        "incident_key": incident["incident_key"],
        "lifecycle": lifecycle,
        "severity": incident.get("severity"),
        "kind": incident.get("kind"),
        "source_event": source_event,
        "cycle_id": incident.get("cycle_id"),
        "code": incident.get("code"),
        "summary": incident.get("summary"),
        "state_from": state_from,
        "state_to": state_to,
    }
    _append(cycle_dir, record)
    return record


def deliver_incident_notification(
    cycle_dir: Path, *, incident: dict[str, Any], send: Any,
) -> bool:
    """Best-effort single SMTP notification per durable incident identity.

    Serialize sending and receipt with the existing registry lock. SMTP cannot
    provide atomic exactly-once semantics if the process dies after acceptance
    but before recording the receipt, so recovery may in that rare case retry.
    Failed sends are recorded and eligible for a subsequent retry.
    """
    cycle_dir = Path(cycle_dir)
    with _registry_lock(cycle_dir):
        history = [row for row in _rows(cycle_dir)
                   if row.get("incident_key") == incident.get("incident_key")]
        # A repeated FAILED state projection or even a Resume attempt does not
        # prove that a stopped task became healthy. The same incident must not
        # send a second mail just because workflow.persist logged "recurred".
        # Another genuinely distinct incident (or another Cycle) has a new key.
        occurrence_index = 0
        if any(row.get("lifecycle") == "notification_sent" for row in history):
            return False
        common = {
            "schema": SCHEMA, "at": now(),
            "incident_id": incident["incident_id"],
            "incident_key": incident["incident_key"],
            "severity": incident.get("severity"), "kind": incident.get("kind"),
            "code": incident.get("code"), "cycle_id": incident.get("cycle_id"),
            "source_event": "sdk.terminal_notification",
            "occurrence_index": occurrence_index,
        }
        try:
            send()
        except Exception as exc:
            _append_unlocked(cycle_dir, {
                **common, "lifecycle": "notification_failed",
                "notification": {"channel": "email", "error": _summary(exc)},
            })
            raise
        _append_unlocked(cycle_dir, {
            **common, "lifecycle": "notification_sent",
            "notification": {"channel": "email", "status": "sent"},
        })
        return True

def repair_handoff(cycle_dir: Path, incident_key_value: str) -> dict[str, Any] | None:
    for row in reversed(_rows(Path(cycle_dir))):
        if row.get("incident_key") != incident_key_value:
            continue
        repair = row.get("repair")
        if isinstance(repair, dict) and str(repair.get("status") or "") in {"started", "blocked"}:
            return repair
    return None


def record_repair_handoff(
    cycle_dir: Path,
    *,
    incident: dict[str, Any],
    status: str,
    target: str | None = None,
    error: str | None = None,
    policy_version: int | None = None,
    review_ref: str | None = None,
) -> dict[str, Any]:
    record = {
        "schema": SCHEMA,
        "at": now(),
        "incident_id": incident["incident_id"],
        "incident_key": incident["incident_key"],
        "lifecycle": "repair_handoff",
        "severity": incident.get("severity"),
        "kind": incident.get("kind"),
        "source_event": incident.get("source_event"),
        "cycle_id": incident.get("cycle_id"),
        "code": incident.get("code"),
        "summary": incident.get("summary"),
        "repair": {
            "status": status,
            "target": target,
            "error": _summary(error) if error else None,
            "model": REPAIR_MODEL,
            "reasoning_effort": REPAIR_REASONING_EFFORT,
            **({"policy_version": int(policy_version)} if policy_version is not None else {}),
            **({"review_ref": review_ref} if review_ref is not None else {}),
        },
    }
    _append(Path(cycle_dir), record)
    return record


def record_incident_verification(cycle_dir: Path, *, incident_id: str,
                                 status: str, detail: str) -> dict[str, Any]:
    """Append evidence of resumed execution, never infer root-cause resolution."""
    if status not in {"runtime_activity_verified", "verification_failed"}:
        raise ValueError("unsupported incident verification")
    cycle_dir = Path(cycle_dir)
    with _registry_lock(cycle_dir):
        incident = next((row for row in _rows(cycle_dir)
                         if row.get('incident_id') == incident_id
                         and row.get('lifecycle') == 'registered'), None)
        if incident is None:
            raise ValueError("unknown incident verification target")
        if any(row.get('incident_id') == incident_id
               and row.get('lifecycle') == 'runtime_activity_verified'
               for row in _rows(cycle_dir)):
            return {"incident_id": incident_id, "lifecycle": "runtime_activity_verified"}
        record = {
            "schema": SCHEMA, "at": now(),
            "incident_id": incident_id, "incident_key": incident['incident_key'],
            "cycle_id": incident['cycle_id'], "kind": incident['kind'],
            "severity": incident['severity'], "code": incident.get('code'),
            "lifecycle": status, "source_event": "worker_supervisor.runtime_verification",
            "verification": {"detail": _summary(detail),
                             "scope": "model_or_tool_activity_only",
                             "root_cause_resolved": False},
        }
        _append_unlocked(cycle_dir, record)
        return record
