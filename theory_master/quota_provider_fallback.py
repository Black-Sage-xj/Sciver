"""Provider credential checks and legacy/manual route-migration safeguards.

Codex exhaustion never enables automatic cross-provider routing.
"""
from __future__ import annotations

from typing import Any




def codex_quota_states(snapshots: dict[str, dict[str, Any]]) -> dict[str, bool | None]:
    """Return authoritative tri-state quota availability for both Codex identities."""
    from .codex_accounts import quota_available

    return {number: quota_available(snapshots.get(number) or {}) for number in ("1", "2")}




def investigator_native_goal_in_flight(state: dict[str, Any] | None) -> bool:
    """Detect an in-flight Investigator Goal even after the Cycle phase becomes quota-paused."""
    if not isinstance(state, dict):
        return False
    goal = state.get("native_goal")
    if not isinstance(goal, dict) or not goal:
        return False
    status = str(goal.get("status") or "").lower()
    if status in {"completed", "complete", "success"}:
        return False

    recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else {}
    role_values = (
        state.get("current_agent"), state.get("active_role"), state.get("current_stage"),
        state.get("stage"), recovery.get("current_agent"), recovery.get("active_role"),
        recovery.get("role"), recovery.get("stage"), state.get("phase"),
    )
    roles = [str(value or "").strip().lower() for value in role_values if value]
    if any(role.startswith("investigator") or role in {"investigation", "investigate"} for role in roles):
        return True
    # Pausing rewrites phase to provider_quota_exhausted. If a nonterminal Native
    # Goal exists but no active role was persisted, fail closed rather than guess.
    if str(state.get("phase") or "").lower() == "provider_quota_exhausted":
        active_roles = [role for role in roles if role != "provider_quota_exhausted"]
        return not active_roles
    return False
