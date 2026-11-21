"""Read-only actionable notifications for *currently* blocked tasks.

Every operational lifecycle remains in the immutable incident ledger or its
Supervisor history. The notification bell is not an alternative event archive;
retries, recoverable errors and old Supervisor sessions cannot create badges.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .run_monitor import data as monitor_data, redact
from .important_events import _clock, _read_file
from .supervisor_view import (
    SESSION_ID, _cycle_dir, _has_data, _legacy_sessions,
    _session_rows, _read_status, supervisor_enabled_for,
)

STOPPED = {"NEEDS_ATTENTION", "FAILED", "FAILED_RECOVERY"}
PAGE_SIZE = 40
OPERATIONAL_ATTENTION = frozenset({
    "runtime_failure", "supervisor_unrecoverable", "provider_model_capacity_timeout",
    "investigator_native_goal_unexpected_blocked", "runtime_retry_exhausted",
    "runtime_no_recoverable_checkpoint", "lead_literature_unavailable",
    "contract_repair_failed",
})


def _recovery(cycle_dir: Any) -> dict[str, Any]:
    if cycle_dir is None:
        return {}
    path = cycle_dir / "supervisor" / "recovery.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 128_000:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    if not isinstance(value, dict) or value.get("schema") != "theory-master/supervisor-recovery/v1":
        return {}
    return value if str(value.get("cycle") or "") == cycle_dir.name else {}


def _category(stop_event: dict[str, Any] | None, state: dict[str, Any]) -> str:
    routing = (stop_event or {}).get("routing") or {}
    if isinstance(routing, dict):
        nature = routing.get("nature")
        if nature == "scientific_or_operational_attention":
            return "scientific"
        if nature in {"engineering_failure", "operational_failure", "provider_configuration",
                      "provider_quota", "budget_guard"}:
            return "system"
    code = str((stop_event or {}).get("code") or (state.get("attention") or {}).get("code") or "")
    return ("scientific" if str(state.get("status") or "").upper() == "NEEDS_ATTENTION"
            and code not in OPERATIONAL_ATTENTION else "system")


def _status(job: dict[str, Any], category: str, enabled: bool) -> tuple[str, str]:
    if not enabled:
        return "disabled", "Supervisor 未开启"
    if category == "scientific":
        return "attention", "待科研判断"
    return {
        "pending": ("diagnosing", "等待诊断"),
        "working": ("diagnosing", "Supervisor 排查中"),
        "waiting": ("waiting", "等待恢复条件"),
        "resume_authorized": ("resuming", "受控恢复中"),
    }.get(str(job.get("status") or ""), ("stopped", "等待处理"))


def _verified_recoveries(run: dict[str, Any], current: str, *, stopped: bool) -> list[dict[str, Any]]:
    """Keep verified SPV repairs visible after later Cycles without raising a badge."""
    if not run.get("research_root"):
        return []
    try:
        root = Path(str(run.get("research_root") or "")).resolve(strict=True)
        names = [path.name for path in root.iterdir()
                 if path.is_dir() and not path.is_symlink() and path.name.isdecimal()]
    except (OSError, RuntimeError):
        return []
    results: list[dict[str, Any]] = []
    for name in names:
        if stopped and name == current:
            continue
        folder = _cycle_dir(run, name)
        job = _recovery(folder)
        progress = job.get("last_verified_progress") or {}
        if job.get("status") != "activity_verified" or not isinstance(progress, dict) or progress.get("scope") != "model_or_tool_activity_only":
            continue
        stamp = str(progress.get("at") or "")
        try:
            verified_at = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if verified_at.tzinfo is None:
            continue
        validated = any(
            isinstance(row, dict) and isinstance(row.get("validation"), dict)
            and row["validation"].get("phase") == "repair_validated"
            for row in job.get("history") or []
        )
        # An ordinary recovery is only a short-lived status hint. A validated
        # repair remains in history until the user hides the notification.
        if not validated and datetime.now(timezone.utc) - verified_at > timedelta(hours=48):
            continue
        ident = str(run.get("observer_id") or "")
        results.append({
            "task": str(run.get("task_name") or ""), "cycle": name,
            "category": "system", "category_label": "系统错误",
            "status": "repaired" if validated else "recovered",
            "status_label": "已修复" if validated else "已恢复",
            "summary": "Supervisor 已验证任务重新产生模型或工具活动。",
            "at": stamp, "url": f"/run/{ident}/",
            "notice_key": "verified:" + hashlib.sha256(f"{ident}:{name}:{stamp}".encode()).hexdigest()[:24],
        })
    return results


def _stop_event(run: dict[str, Any], cycle: str) -> dict[str, Any] | None:
    """Return the latest real stop transition, including legacy event ledgers."""
    folder = _cycle_dir(run, cycle)
    if folder is None:
        return None
    rows: list[dict[str, Any]] = []
    for name in ("cycle_anomalies.jsonl", "important_events.jsonl"):
        rows.extend(_read_file(folder / name, cycle=cycle))
    candidates = [row for row in rows
                  if row.get("kind") in {"needs_attention", "system_failure"}
                  and row.get("lifecycle") in {"registered", "recurred"}]
    return max(candidates, key=lambda row: _clock(row.get("at"))) if candidates else None


def _stop_occurrence(event: dict[str, Any] | None) -> str:
    """Identify a stop occurrence from its authoritative event, never a heartbeat."""
    if event is not None:
        return ":".join(str(event.get(key) or "") for key in
                        ("incident_id", "lifecycle", "at"))
    return "legacy-stop"


def _cycle_entries(run: dict[str, Any], name: str) -> list[dict[str, Any]]:
    folder = _cycle_dir(run, name)
    if folder is None or not _has_data(folder):
        return []
    rows, _warnings = _session_rows(folder)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        sid = str(row.get("session_id") or "")
        if not SESSION_ID.fullmatch(sid):
            continue
        item = grouped.setdefault(sid, {
            "session_id": sid, "profile": str(row.get("profile") or ""),
            "code": str(row.get("code") or ""), "incident_id": str(row.get("incident_id") or ""),
            "at": str(row.get("at") or ""), "status": "active", "summary": "",
            "legacy": False,
        })
        if row.get("kind") == "opened":
            item["summary"] = redact(str(row.get("content") or ""))[:350]
        if row.get("kind") == "decision":
            item["summary"] = redact(str(row.get("content") or item["summary"]))[:350]
            if row.get("status") in {"hold", "returned"}:
                item["status"] = row["status"]
        if row.get("kind") == "verification":
            item["summary"] = redact(str(row.get("content") or item["summary"]))[:350]
            if row.get("status") == "unverified":
                item["status"] = "unverified"
    # An SDK Recovery can mark the current session returned without rewriting
    # its immutable decision history.  Display the effective outcome.
    status = _read_status(folder) or {}
    current_sid = status.get("session_id")
    if current_sid in grouped and status.get("status") == "returned":
        grouped[current_sid]["status"] = "returned"

    entries = list(grouped.values())
    for old in _legacy_sessions(folder):
        if old.get("incident_id") and any(
            x.get("incident_id") == old["incident_id"] for x in entries
        ):
            continue
        decision = next(
            (m for m in reversed(old.get("messages") or []) if m.get("kind") == "decision"),
            {},
        )
        entries.append({
            "session_id": str(old["session_id"]),
            "profile": str(old.get("profile") or "attention"),
            "code": str(old.get("code") or ""),
            "incident_id": str(old.get("incident_id") or ""),
            "at": str(old.get("opened_at") or ""), "status": old.get("status") or "hold",
            "summary": redact(str(decision.get("content") or old.get("code") or ""))[:350],
            "legacy": True,
        })
    ident = str(run.get("observer_id") or "")
    task = str(run.get("task_name") or "")
    for item in entries:
        item.update({
            "task": task, "cycle": name,
            "url": f"/run/{ident}/supervisor/?cycle={name}#{item['session_id']}",
        })
    return entries


def notification_feed(runs: list[dict[str, Any]], *, offset: int = 0,
                      limit: int = PAGE_SIZE) -> dict[str, Any]:
    """Page *all* retained sessions; count unresolved holds independent of page."""
    if offset < 0 or not (1 <= limit <= 100):
        raise ValueError("invalid notification page")
    entries: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    pending = 0
    for run in runs:
        current = str(run.get("cycle") or "")
        state = monitor_data(run)
        stopped = str(state.get("status") or "").upper() in STOPPED
        resolved.extend(_verified_recoveries(run, current, stopped=stopped))
        if not stopped:
            continue
        stop_event = _stop_event(run, current)
        category = _category(stop_event, state)
        recovery = _recovery(_cycle_dir(run, current))
        # Only the current Cycle can own an actionable stop. Reading every
        # historical Supervisor folder on each 20-second bell poll is wasteful.
        task_entries = _cycle_entries(run, current)
        # Exactly one notification per *currently stopped task*. Historical
        # resolved sessions and operational retries are only audit evidence.
        current_entries = [e for e in task_entries if e["cycle"] == current]
        latest = max(current_entries, key=lambda e: (e["at"], e["session_id"])) if current_entries else None
        attention = state.get("attention") or {}
        attention = attention if isinstance(attention, dict) else {}
        reason = redact(str(attention.get("reason") or ""))[:350]
        code = str(attention.get("code") or (stop_event or {}).get("code") or "task_stopped")[:100]
        if latest is not None and latest["status"] in {"hold", "unverified"}:
            item = dict(latest)
            if reason:
                item["summary"] = reason
        else:
            # Never imply that a historical Supervisor 'returned' decision
            # resolved a later/current stop, or fabricate a Supervisor record.
            item = {
                "id": "current-stop:" + hashlib.sha256(
                    f"{run.get('observer_id')}:{current}".encode()).hexdigest()[:18],
                "session_id": "", "profile": "runtime", "code": code,
                "incident_id": "", "at": str((stop_event or {}).get("at") or ""),
                "status": "current_stop", "summary": reason or
                redact(str((stop_event or {}).get("summary") or ""))[:350] or
                f"任务当前状态为 {str(state.get('status') or '停止')}，需要检查运行状态。",
                "legacy": False, "task": str(run.get("task_name") or ""),
                "cycle": current, "url": f"/run/{str(run.get('observer_id') or '')}/",
            }
        item["needs_attention"] = True
        item["category"] = category
        item["category_label"] = "科研需关注" if category == "scientific" else "系统错误"
        item["cause_code"] = str((stop_event or {}).get("code") or code)[:100]
        item["supervisor_enabled"] = supervisor_enabled_for(run, state)
        item["handling_status"], item["handling_label"] = _status(recovery, category, item["supervisor_enabled"])
        item["blocking_dependency"] = redact(str(recovery.get("blocking_dependency") or ""))[:350]
        occurrence = _stop_occurrence(stop_event)
        if occurrence == "legacy-stop" and item.get("session_id"):
            occurrence = "session:" + str(item["session_id"])
        item["notice_key"] = hashlib.sha256(
            f"{run.get('observer_id')}:{current}:{occurrence}".encode()).hexdigest()[:24]
        entries.append(item)
        pending += 1
    entries.sort(key=lambda e: (e["at"], e["session_id"]), reverse=True)
    resolved.sort(key=lambda e: e["at"], reverse=True)
    # Deprecated response field retained as empty for old client versions.
    # No recoverable incident produces a red dot, even before first read.
    total = len(entries)
    return {
        "needs_attention": pending,
        "active_keys": [entry["notice_key"] for entry in entries],
        "internal_alert_ids": [],
        "total": total,
        "items": entries[offset:offset + limit],
        "next_offset": offset + limit if offset + limit < total else None,
        "category_counts": {
            "system": sum(item["category"] == "system" for item in entries),
            "scientific": sum(item["category"] == "scientific" for item in entries),
        },
        "resolved_items": resolved[:10],
    }
