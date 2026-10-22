"""Bounded, provenance-backed incident evidence shared by both Supervisor profiles.

Operational telemetry is distinct from an Investigator public scientific claim.
Only scoped event summaries are sent to models, with secrets and large payloads
redacted. The reviewer never receives write authority through this module.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MAX_TAIL_BYTES = 768_000
MAX_EVENTS = 25
SECRET = re.compile(
    r"(?i)(bearer\s+)[a-z0-9._~+/=-]+|"
    r"((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[\"']?[^\s\"']+"
)


def _safe(value: Any, size: int = 650) -> str:
    raw = " ".join(str(value or "").split())
    return SECRET.sub(lambda m: (m.group(1) or m.group(2) or "") + "[redacted]", raw)[:size]


def _tail(path: Path) -> list[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file():
            return []
        with path.open("rb") as handle:
            handle.seek(0, 2)
            length = handle.tell()
            handle.seek(max(0, length - MAX_TAIL_BYTES))
            chunk = handle.read(MAX_TAIL_BYTES)
        lines = chunk.splitlines()
        if length > MAX_TAIL_BYTES:
            lines = lines[1:]
        found = []
        for line in lines:
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(value, dict):
                found.append(value)
        return found
    except OSError:
        return []


def incident_context(cycle_dir: Path, state: dict[str, Any], *, role: str = "") -> dict[str, Any]:
    """Return compact diagnostics with source and scope, without source content dumps."""
    cycle_dir = Path(cycle_dir)
    attention = state.get("attention") if isinstance(state.get("attention"), dict) else {}
    native = state.get("native_goal") if isinstance(state.get("native_goal"), dict) else {}
    recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else {}
    thread_id = str(attention.get("thread_id") or native.get("thread_id")
                    or (state.get("threads") or {}).get(role) or "")
    focus_role = role or ("investigator" if thread_id and (native or attention.get("thread_id"))
                          else str(state.get("next_executor") or state.get("active_role")
                                   or state.get("current_agent") or "investigator"))
    # Scoping by thread AND latest role invocation ensures an old transport
    # failure does not contaminate a later scientific blocked in the same
    # retained Investigator thread.
    start_at = ""
    invocation_id = ""
    for item in _tail(cycle_dir / "events.jsonl"):
        if item.get("type") != "role.started":
            continue
        payload = item.get("payload") or {}
        if payload.get("role") == focus_role and item.get("at"):
            start_at = str(item["at"])
            invocation_id = str(payload.get("invocation_id") or "")
    records = _tail(cycle_dir / "runtime_events.jsonl")
    if thread_id:
        records = [e for e in records if str(e.get("thread_id") or "") == thread_id]
    elif focus_role:
        records = [e for e in records if str(e.get("role") or e.get("agent") or "") == focus_role]
    if start_at:
        records = [e for e in records if str(e.get("ts") or e.get("at") or "") >= start_at]
    events = []
    errors = []
    reconnects = 0
    for event in records:
        typ = str(event.get("event") or "")
        if typ == "LLM_RECONNECTING":
            reconnects += 1
        if typ in {"LLM_FAILED", "LLM_TURN_FAILED", "CAPACITY_RETRY_EXHAUSTED"}:
            errors.append({
                "at": _safe(event.get("ts") or event.get("at"), 48),
                "event": typ, "error": _safe(event.get("error") or event.get("detail"), 1100),
            })
        if typ not in {"LLM_FAILED", "LLM_TURN_FAILED", "LLM_TURN_COMPLETED",
                       "LLM_MESSAGE", "LLM_RECONNECTING", "CAPACITY_RETRY_EXHAUSTED",
                       "CAPACITY_RETRY_WAIT", "TOOL_ACTIVITY", "LLM_TURN_STARTED"}:
            continue
        item = {"at": _safe(event.get("ts") or event.get("at"), 48), "event": typ}
        if typ in {"LLM_FAILED", "LLM_TURN_FAILED", "CAPACITY_RETRY_EXHAUSTED"}:
            item["error"] = _safe(event.get("error") or event.get("detail"), 750)
        elif typ == "LLM_RECONNECTING":
            item["detail"] = _safe(event.get("error") or event.get("detail"), 90)
        elif typ == "LLM_MESSAGE":
            item["detail"] = "public agent message emitted"  # content is not an operational error
        events.append(item)
    progress = state.get("investigator_budget") if isinstance(state.get("investigator_budget"), dict) else {}
    return {
        "schema": "theory-master/supervisor-incident/v1",
        "evidence_source": (
            "cycle/events.jsonl role.started + runtime_events.jsonl "
            "(read-only, invocation/thread-filtered tail)"
        ),
        "invocation_id": _safe(invocation_id, 120),
        "invocation_started_at": _safe(start_at, 48),
        "cycle_id": _safe(state.get("cycle_id") or cycle_dir.name, 80),
        "phase": _safe(state.get("phase"), 100),
        "status": _safe(state.get("status"), 70),
        "attention_code": _safe(attention.get("code"), 120),
        "attention_reason": _safe(attention.get("reason"), 1800),
        "native_goal_status": _safe(native.get("status") or attention.get("goal_status"), 90),
        "thread_id": _safe(thread_id, 160),
        "role": _safe(focus_role, 50),
        "last_terminal_error": errors[-1] if errors else None,
        "recent_errors": errors[-4:],
        "reconnection_events_in_retained_tail": reconnects,
        "recent_events": events[-MAX_EVENTS:],
        "resumable": bool(state.get("resumable")),
        "safe_checkpoint_present": bool(state.get("last_safe_checkpoint") or state.get("role_outputs")),
        "next_executor": _safe(state.get("next_executor"), 60),
        "investigation_attempt": state.get("investigation_attempt"),
        "runtime_resumes_used": int(state.get("supervisor_runtime_resume_count") or 0),
        "budget": {
            "remaining_tokens": progress.get("remaining_tokens"),
            "exhausted": bool(progress.get("exhausted")),
        },
        "recovery_action": _safe(recovery.get("action"), 80),
    }


class NativeGoalRuntimeFailure(RuntimeError):
    """A Native Goal runtime failure, optionally backed by a provider event."""

    def __init__(self, *, thread_id: str, error: str, provider_event: str = ""):
        self.thread_id = thread_id
        self.error = _safe(error, 1200)
        self.provider_event = provider_event
        source = f" provider event {provider_event}" if provider_event else ""
        super().__init__(f"Investigator native Goal runtime failure{source} "
                         f"(thread={thread_id}): {self.error}")
