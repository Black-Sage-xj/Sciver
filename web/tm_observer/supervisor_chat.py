"""Read-only projection for the shared Research Chat UI in Supervisor mode."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

def _title(record: dict[str, Any]) -> str:
    cycle = str(record.get("cycle_id") or "Cycle ?")
    trigger = str(record.get("trigger_event") or "Supervisor 通知")
    return f"{cycle} · {trigger[:72]}"


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
    return {
        "tasks": [], "sessions": sessions, "session": selected_row,
        "available_models": [], "available_reasoning_efforts": [],
        "supervisor_mode": True, "observer_id": observer_id,
    }
