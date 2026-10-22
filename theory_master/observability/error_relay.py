"""SDK-internal failed-run incident management compatibility shim.

Never launch a Codex window from an observability callback or supervisor.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from .incident_registry import register_cycle_incident, record_repair_handoff

# Historical locations remain discoverable for migration/tests only.
# This module never executes either launcher.
REPO_ROOT = Path(__file__).resolve().parents[4]
RELAY_STARTER = REPO_ROOT / "tool/python_script/start_origin_tmux_relay.py"
RESUME_STARTER = REPO_ROOT / "tool/theory_master/scripts/start_recovery_resume.py"


def spawn_repair_agent(state: dict[str, Any], payload: dict[str, Any]) -> str:
    """Legacy API: register a safe hold, never spawn an external agent."""
    cycle_dir = Path(str(state.get("run_dir") or state.get("cycle_dir") or ".")).resolve()
    if not cycle_dir.is_dir():
        raise FileNotFoundError(f"cycle directory not found: {cycle_dir}")
    error = str(payload.get("error") or state.get("last_event") or "unknown runtime failure")
    incident, _ = register_cycle_incident(
        cycle_dir, severity="error", kind="system_failure",
        source_event=str(payload.get("event") or "run.failed"),
        summary=error, code=str(payload.get("code") or "run_failed"),
        discriminator=f"{state.get('phase') or ''}:run_failed",
        fields={"phase": state.get("phase"), "role": payload.get("role"),
                "error_type": payload.get("error_type")},
    )
    record_repair_handoff(
        cycle_dir, incident=incident, status="blocked",
        target="sdk:internal_hold",
        error="Runner has stopped; verified code/state repair is required before a managed resume.",
        policy_version=4,
    )
    return f"incident:{incident['incident_id']}:sdk_internal_hold"
