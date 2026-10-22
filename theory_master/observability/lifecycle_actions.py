from __future__ import annotations

"""Lifecycle side effects emitted from observability events.

Collector owns state projection and persistence; this helper owns external
notifications/repair/audit handoffs so those effects are isolated.
"""

from typing import Any


class LifecycleActions:
    def __init__(self, owner: Any):
        self.owner = owner

    def notify_terminal_email(self, kind: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner._terminal_email_sent:
            return
        try:
            from theory_master.observability.email_notify import (
                send_terminal_email, should_send_terminal_email,
                notify_cycle_terminal_once, email_terminal_events_enabled,
            )
            if not should_send_terminal_email(kind, payload) or not email_terminal_events_enabled(owner.run_dir):
                return
            snapshot = owner.snapshot()
            run_dir = owner.run_dir
            if run_dir is not None:
                sent = notify_cycle_terminal_once(run_dir, event=kind, state=snapshot, payload=payload)
            else:
                send_terminal_email(kind, snapshot, payload)
                sent = True
            owner._terminal_email_sent = True
            owner.log("INFO", f"Terminal email notification {'sent' if sent else 'already sent'} ({kind})")
        except Exception as exc:
            owner.log("ERROR", f"Terminal email notification failed: {exc}")

    def spawn_error_repair(self, payload: dict[str, Any]) -> None:
        """System failure stays recorded and stopped; no external Codex repair."""
        owner = self.owner
        with owner._lock:
            owner.state["repair_handoff"] = {
                "status": "sdk_internal_hold", "target": None,
                "error": str(payload.get("error") or "runtime failed"),
            }
        owner.log("INFO", "Runtime failure recorded; SDK safe stop, no external Codex")

    def spawn_attention_takeover(self) -> None:
        """Do not spawn: the owning SDK Workflow processes Attention inline."""
        owner = self.owner
        owner._attention_takeover_spawned = True
        owner.log("INFO", "Attention is handled by the SDK; no external repair window is created")

    def spawn_cycle_audit(self) -> None:
        owner = self.owner
        if owner._cycle_audit_spawned:
            return
        owner._cycle_audit_spawned = True
        try:
            from theory_master.observability.cycle_audit_relay import spawn_cycle_audit_agent

            target = spawn_cycle_audit_agent(owner.snapshot())
            with owner._lock:
                owner.state["audit_handoff"] = {"status": "started", "target": target, "error": None}
            owner._emit({"event": "CYCLE_AUDIT_STARTED", "target": target, "run_id": owner.state.get("run_id")})
            owner.log("INFO", f"Cycle audit Codex window spawned ({target})")
        except Exception as exc:
            with owner._lock:
                owner.state["audit_handoff"] = {"status": "failed", "target": None, "error": str(exc)}
            owner._emit({"event": "CYCLE_AUDIT_FAILED", "error": str(exc), "run_id": owner.state.get("run_id")})
            owner.log("ERROR", f"Cycle audit handoff failed: {exc}")
