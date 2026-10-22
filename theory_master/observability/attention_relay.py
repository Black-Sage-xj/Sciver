"""SDK-internal Attention compatibility and deterministic hard-guard checks."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from theory_master.investigator_budget import BUDGET_ATTENTION_CODE
from theory_master.observability.incident_registry import register_cycle_incident

REPO_ROOT = Path(__file__).resolve().parents[4]
RELAY_STARTER = REPO_ROOT / "tool/python_script/start_origin_tmux_relay.py"
RESUME_STARTER = REPO_ROOT / "tool/theory_master/scripts/start_recovery_resume.py"
ATTENTION_REPAIR_POLICY_VERSION = 3
# A human/automatic resume cannot change a persistent hard budget without an
# actual policy/input change. Do not spend Repair Codex turns on an unchanged
# deterministic gate. Legacy internal-turn limits are cleared by the new SDK
# investigation route on a deliberately initiated recovery.
HARD_ATTENTION_BLOCKERS = frozenset({
    "investigator_internal_turn_limit",
    "investigation_round_budget_exhausted",
    "cycle_cost_budget_exhausted",
    "cycle_token_budget_exhausted",
})


def _hard_guard_still_active(cycle_dir: Path, code: str) -> bool:
    """Deterministic preflight for hard guards; never guess the incident is fixed."""
    if code not in HARD_ATTENTION_BLOCKERS:
        return False
    if code not in {"cycle_cost_budget_exhausted", "cycle_token_budget_exhausted"}:
        return True
    key = ("THEORY_MASTER_MAX_CYCLE_COST_USD"
           if code == "cycle_cost_budget_exhausted" else "THEORY_MASTER_MAX_CYCLE_TOKENS")
    default = 5.0 if code == "cycle_cost_budget_exhausted" else 10_000_000.0
    task_env = cycle_dir.parent / ".theory-master" / "env"
    raw = os.environ.get(key, str(default))
    if task_env.is_file():
        for line in task_env.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == key:
                raw = value.strip().strip("'\"")
    try:
        threshold = float(raw)
    except (TypeError, ValueError):
        threshold = default
    if threshold <= 0:
        return False
    try:
        snapshot = json.loads((cycle_dir / "run_state.json").read_text(encoding="utf-8"))
        amount = snapshot.get("cycle_cost") or snapshot.get("cost") or {}
        spent = (float(amount.get("amount") or 0) if isinstance(amount, dict) else 0.0) if code == "cycle_cost_budget_exhausted" else float(snapshot.get("cycle_tokens_total") or snapshot.get("tokens_total") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return True
    return spent >= threshold


def spawn_attention_takeover_agent(state: dict[str, Any]) -> str:
    """Legacy API: register the incident, NEVER launch an external worker.

    A currently owned Workflow performs SDK-internal diagnosis at its safe
    control boundary. A stopped/supervisor-only snapshot has no live backend
    or owner: it must stay stopped until normal managed resume, not spawn tmux.
    """
    cycle_dir = Path(str(state.get("run_dir") or state.get("cycle_dir") or ".")).resolve()
    if not cycle_dir.is_dir():
        raise FileNotFoundError(f"cycle directory not found: {cycle_dir}")
    attention = state.get("attention") if isinstance(state.get("attention"), dict) else {}
    code = str(attention.get("code") or state.get("closure_reason") or "needs_attention")
    incident, _ = register_cycle_incident(
        cycle_dir, severity="attention", kind="needs_attention",
        source_event="compat.internal_attention", code=code,
        summary=str(attention.get("reason") or state.get("human_attention") or "NEEDS_ATTENTION"),
        discriminator=f"{state.get('phase') or ''}:{code}",
        fields={"phase": state.get("phase"), "resumable": state.get("resumable")},
    )
    # Ignore obsolete handoff='started' records from the old external tmux
    # implementation. No stale window may block the SDK or trigger a new one.
    return f"incident:{incident['incident_id']}:sdk_internal"
