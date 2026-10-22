from __future__ import annotations

"""Dependency-light workflow constants and control signals for the current runtime."""

from typing import Any

VERDICTS = {"supported", "rejected", "milestone_advanced", "open", "needs_more_evidence"}
TERMINAL_SCIENTIFIC_VERDICTS = {"supported", "rejected", "milestone_advanced", "open"}
FINAL_GATE_VERDICTS = {"passed", "not_passed"}
REWORK_LIMIT = 3
CONTROL_LINE_DROP_IDS = frozenset({"tm-v2-current-gate", "tm-v2-next-action"})


class RecoveryStop(Exception):
    """Internal control-flow signal for a persisted pause/interruption."""


def precheckpoint_recovery_allowed(state: dict[str, Any]) -> bool:
    """Allow recovery before the first safe checkpoint only in untouched qualification."""
    if not isinstance(state, dict):
        return False
    if state.get("last_safe_checkpoint") or state.get("role_outputs"):
        return False
    lock = state.get("hypothesis_lock") if isinstance(state.get("hypothesis_lock"), dict) else {}
    if str(lock.get("status") or "unlocked").lower() == "locked" or state.get("hypothesis_locked"):
        return False
    if str(state.get("qualification_status") or "pending").lower() not in {"pending", ""}:
        return False
    phase = str(state.get("phase") or "").lower()
    return phase in {"idle", "starting", "qualification", "research_lead:running", "resuming"}


def prequalification_restart_allowed(state: dict[str, Any]) -> bool:
    """Allow explicit Attention-authorized replay of untouched qualification."""
    if not isinstance(state, dict):
        return False
    if str(state.get("phase") or "").lower() != "runtime_failure:no_recoverable_checkpoint":
        return False
    if (not state.get("prequalification_restart_authorized")
            or state.get("last_safe_checkpoint") or state.get("role_outputs")):
        return False
    lock = state.get("hypothesis_lock") if isinstance(state.get("hypothesis_lock"), dict) else {}
    return not (str(lock.get("status") or "unlocked").lower() == "locked" or state.get("hypothesis_locked"))
