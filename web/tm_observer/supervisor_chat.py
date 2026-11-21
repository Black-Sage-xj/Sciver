"""Read-only projection for the shared Research Chat UI in Supervisor mode."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

def _title(record: dict[str, Any]) -> str:
    from .supervisor_notifications import OPERATIONAL_ATTENTION
    cycle = str(record.get("cycle_id") or "?")
    code = str(record.get("trigger_event") or "").partition(":")[0].strip()
    system = record.get("trigger_type") in {"runtime_failure", "protocol_violation"} or code in OPERATIONAL_ATTENTION
    return f"Cycle {cycle} · {'系统错误' if system else '科研需关注'}"


def snapshot(run: dict[str, Any], selected: str | None = None) -> dict[str, Any]:
    """Project one task's notification-created SIS history without copying its messages."""
    # Keep the Web package import-safe; load the Runtime-owned incident contract
    # only when the authorized Supervisor history route is actually requested.
    from theory_master.supervisor_incident_session import (
        IncidentSessionError, list_notification_incidents, load_incident, SESSION_ID,
    )

    raw_root = run.get("research_root") or run.get("task_root")
    root = Path(str(raw_root)).resolve() if raw_root else None
    incidents = list_notification_incidents(root) if root is not None and root.is_dir() else []
    observer_id = str(run.get("observer_id") or "")
    sessions = []
    for record in incidents:
        session_id = str(record.get("id") or "")
        if not SESSION_ID.fullmatch(session_id):
            continue
        sessions.append({
            "id": session_id,
            "title": _title(record),
            "updated_at": str(record.get("last_message_at") or record.get("created_at") or ""),
            "status": str(record.get("status") or "active"),
            "href": f"/run/{quote(observer_id, safe='')}/supervisor/?session={quote(session_id, safe='')}",
        })

    selected_id = str(selected or (sessions[0]["id"] if sessions else ""))
    selected_row = None
    if selected_id:
        if root is None or not SESSION_ID.fullmatch(selected_id):
            raise IncidentSessionError("invalid Supervisor history session", status=404)
        try:
            record, messages = load_incident(root, selected_id)
        except (IncidentSessionError, OSError, ValueError) as exc:
            raise IncidentSessionError("Supervisor history session not found", status=404) from exc
        selected_row = {
            "id": selected_id, "title": "Supervisor 接管对话", "mode": "supervisor",
            "task_id": str(record.get("task_id") or ""), "status": str(record.get("status") or "active"),
            "cycle_id": str(record.get("cycle_id") or ""),
            "trigger_event": str(record.get("trigger_event") or ""),
            "updated_at": str((messages[-1] if messages else {}).get("at") or record.get("created_at") or ""),
            "messages": messages,
        }
        from .supervisor_notifications import notification_feed, OPERATIONAL_ATTENTION
        feed = notification_feed([run])
        audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
        reference = str(audit.get("notification_id") or "")
        notice = next((item for item in feed["items"]
                       if item["cycle"] == selected_row["cycle_id"]
                       and str(item.get("session_id") or item.get("id") or item.get("code") or "") == reference), None)
        if notice is None:
            notice = next((item for item in feed["resolved_items"]
                           if item["cycle"] == selected_row["cycle_id"]), None)
        if notice is None:
            code = selected_row["trigger_event"].partition(":")[0].strip()
            category = ("system" if record.get("trigger_type") in {"runtime_failure", "protocol_violation"}
                        or code in OPERATIONAL_ATTENTION else "scientific")
            notice = {
                "category": category,
                "category_label": "系统错误" if category == "system" else "科研需关注",
                "status_label": "历史记录", "handling_status": "historical",
                "summary": selected_row["trigger_event"],
            }
        selected_row["notice"] = {key: notice.get(key) for key in (
            "category", "category_label", "status", "status_label", "handling_status",
            "handling_label", "summary", "blocking_dependency", "at")}
        selected_row["task_label"] = str(run.get("task_name") or "")
        selected_row["task_url"] = f"/run/{quote(observer_id, safe='')}/"
    return {
        "tasks": [], "sessions": sessions, "session": selected_row,
        "available_models": [], "available_reasoning_efforts": [],
        "supervisor_mode": True, "observer_id": observer_id,
    }
