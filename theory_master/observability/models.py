from __future__ import annotations

from typing import Any

from theory_master.role_registry import CAPABILITIES, ROLE_DISPLAY_NAMES, ROLES
from theory_master.identity import AUXILIARY_ROLES

RUN_STATUS = ("STARTING", "RUNNING", "CHECKPOINT_REACHED", "NEEDS_ATTENTION", "PAUSED", "RECOVERABLE", "OVER", "COMPLETE", "COMPLETED", "FAILED", "INTERRUPTED")
AGENT_STATES = ("idle", "waiting", "running", "rework", "done", "blocked", "failed")
SCI_STATUS_MAP = {
    "starting": "STARTING",
    "idle": "STARTING",
    "active": "RUNNING",
    "over": "OVER",
    # Historical Cycle states used `completed`; normalize them to Cycle-level OVER.
    # Goal-level COMPLETE is projected only when the task Goal gate passes; COMPLETED is legacy input.
    "completed": "OVER",
    "checkpoint_reached": "CHECKPOINT_REACHED",
    "failed": "FAILED",
    "paused": "PAUSED",
    "needs_attention": "NEEDS_ATTENTION",
    "cancelled": "INTERRUPTED",
    "interrupted": "INTERRUPTED",
}
DISPLAY_ROLE = ROLE_DISPLAY_NAMES


def investigation_progress(state: dict[str, Any]) -> dict[str, Any]:
    """Read current counters, retaining cumulative aliases for older consumers."""
    total = int(
        state.get("total_investigation_round_count",
                  state.get("total_capability_call_count", state.get("round_count"))) or 0
    )
    reworks = int(state.get("judge_rework_count", state.get("rework_count")) or 0)
    return {
        "investigation_attempt": int(state.get("investigation_attempt") or (1 if total else 0)),
        "investigation_round_count": int(state.get("investigation_round_count", state.get("round_count")) or 0),
        "judge_rework_count": reworks,
        "total_investigation_round_count": total,
        # Historical aliases retained for old dashboards/readers only.
        "total_capability_call_count": total,
        "round_count": total,
        "rework_count": reworks,
    }


def investigation_details(state: dict[str, Any]) -> dict[str, Any]:
    """Shared live/rebuilt projection; never reconstruct old attempt boundaries."""
    result = investigation_progress(state)
    for key, default in (("investigation_attempts", []), ("investigation_readiness", {}),
                         ("judge_objections", [])):
        result[key] = state.get(key, default)
    # Legacy 12/13 round counters are historic provenance only, not active budgets.
    if "max_judge_reworks_per_cycle" in state:
        result["max_judge_reworks_per_cycle"] = state["max_judge_reworks_per_cycle"]
    if "investigator_budget" in state:
        result["investigator_budget"] = state["investigator_budget"]
    return result


def current_role_threads(state: dict[str, Any]) -> dict[str, str]:
    """Select only the active attempt's Specialist threads for the live panes."""
    threads = state.get("threads") or {}
    result = {role: value for role, value in threads.items() if role in ROLES}
    attempt = int(state.get("investigation_attempt") or 0)
    if attempt:
        for role in CAPABILITIES:
            result.pop(role, None)
            value = threads.get(f"attempt-{attempt:04d}:{role}")
            if value:
                result[role] = value
    return result


def blank_agent(role: str) -> dict[str, Any]:
    return {
        "role": role,
        "state": "idle",
        "thread_id": None,
        "model": None,
        "reasoning_effort": None,
        "provider": None,
        "model_vendor": None,
        "turn_count": 0,
        "turn_id": None,
        "tokens_in": None,
        "tokens_out": None,
        "tokens_total": None,
        "raw_usage": {},
        "last_turn_usage": {},
        "cached_input_tokens": None,
        "uncached_input_tokens": None,
        "reasoning_tokens": None,
        "context_tokens": None,
        "context_window": None,
        "compaction_count": 0,
        "last_compaction": None,
        "cost_records": [],
        "cost": {"amount": None, "currency": None, "source": "unavailable", "confidence": "none"},
        "started_at": None,
        "elapsed_s": 0.0,
        "current_tool": None,
        "activity": None,
        "last_event": None,
        "retry_count": 0,
        "skills_available": [],
        "skills_used": [],
        "skill_load_events": 0,
        "skill_load_counts": {},
    }


def blank_run_state() -> dict[str, Any]:
    return {
        "run_id": "",
        "master_run_id": None,
        "goal_id": "",
        "status": "STARTING",
        "cycle_index": 0,
        "max_cycles": 1,
        "max_rounds": 12,
        "phase": "idle",
        "current_agent": None,
        "current_thread_id": None,
        "current_turn_id": None,
        "current_tool": None,
        "current_activity": None,
        "tokens_in": None,
        "tokens_out": None,
        "tokens_total": None,
        "cached_input_tokens": None,
        "uncached_input_tokens": None,
        "reasoning_tokens": None,
        "current_turn_role": None,
        "current_turn_usage": {},
        "cycle_tokens_in": None,
        "cycle_tokens_out": None,
        "cycle_tokens_total": None,
        "cost": {"amount": None, "currency": None, "source": "unavailable", "confidence": "none"},
        "cycle_cost": {"amount": None, "currency": None, "source": "unavailable", "confidence": "none"},
        "elapsed_s": 0.0,
        "cycle_elapsed_s": 0.0,
        "project_elapsed_s": 0.0,
        "project_tokens_in": None,
        "project_tokens_out": None,
        "project_tokens_total": None,
        "project_cached_input_tokens": None,
        "project_uncached_input_tokens": None,
        "project_reasoning_tokens": None,
        "project_cost": {"amount": None, "currency": None, "source": "unavailable", "confidence": "none"},
        "started_at": None,
        "total_started_at": None,
        "cycle_started_at": None,
        "last_event": None,
        "event_history": [],
        "last_action": None,
        "action_history": [],
        "agent_timeline": [],
        "project_agents": {},
        "hypothesis": "",
        "hypothesis_title": "",
        "branch_id": "",
        "round_count": 0,
        "rework_count": 0,
        "investigation_attempt": 0,
        "investigation_round_count": 0,
        "judge_rework_count": 0,
        "total_investigation_round_count": 0,
        "total_capability_call_count": 0,  # legacy projection alias
        "investigation_attempts": [],
        "investigation_readiness": {},
        "judge_objections": [],
        "last_verdict": None,
        "last_target": None,
        "last_missing": None,
        "literature_requested": False,
        "investigation_rounds": [],
        "qualification_status": "pending",
        "qualification_rationale": "",
        "hypothesis_locked": False,
        "hypothesis_lock": {},
        "agents": {role: blank_agent(role) for role in ROLES},
        "auxiliary_agents": {role: blank_agent(role) for role in AUXILIARY_ROLES},
        "artifacts": [],
        "branch_canvas": "",
        "notes_dir": "",
        "task_home": "",
        "research_root": "",
        "run_dir": "",
        "cycle_workspace": "",
        "task_root": "",  # legacy persisted-state read compatibility only
        "report_path": None,
        "master_id": None,
        "cycle_id": "",
        "resumable": False,
        "execution_active": False,
        "system_failure_actions_enabled": False,
        "cycle_audit_actions_enabled": False,
        "attention_takeover_enabled": False,
        "quota_auto_adjust_enabled": False,
        "quota_auto_adjust": {},
        "owner_pid": None,
        "attention": {},
        "last_safe_checkpoint": None,
        "recovery": {},
        "repair_handoff": {"status": "disabled", "target": None, "error": None},
        "audit_handoff": {"status": "disabled", "target": None, "error": None},
        "attention_takeover_handoff": {"status": "disabled", "target": None, "error": None},
        "attention_guidance": {},
        "human_attention": None,
    }
