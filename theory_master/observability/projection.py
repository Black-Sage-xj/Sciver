from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from theory_master.observability.models import (
    CAPABILITIES, SCI_STATUS_MAP, blank_run_state, current_role_threads, investigation_details,
)

def _agent_key(role: str) -> str:
    return role


def latest_run_dir(research_root: Path) -> Path | None:
    from theory_master.specification import latest_cycle_dir
    return latest_cycle_dir(research_root)


def load_latest_run_state(research_root: Path) -> dict[str, Any] | None:
    run_dir = latest_run_dir(research_root)
    if run_dir is None:
        return None
    path = run_dir / "run_state.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def reconcile_runtime_state(snap: dict[str, Any], sci: dict[str, Any]) -> dict[str, Any]:
    """Overlay authoritative workflow lifecycle fields onto a dashboard snapshot."""
    goal_completion = sci.get("goal_completion") if isinstance(sci.get("goal_completion"), dict) else {}
    status = ("COMPLETE" if goal_completion.get("verdict") == "passed"
              else SCI_STATUS_MAP.get(str(sci.get("status") or "")))
    if status:
        snap["status"] = status
    snap["phase"] = str(sci.get("phase") or snap.get("phase") or "")
    snap["human_attention"] = sci.get("human_attention")
    snap["qualification_status"] = str(sci.get("qualification_status") or snap.get("qualification_status") or "pending")
    snap["qualification_rationale"] = str(sci.get("qualification_rationale") or snap.get("qualification_rationale") or "")
    attention = sci.get("attention")
    if isinstance(attention, dict) and attention:
        snap["attention"] = dict(attention)
    elif str(sci.get("status") or "") == "needs_attention" and snap["qualification_status"] == "unqualified":
        snap["attention"] = {
            "code": "no_qualified_candidate",
            "reason": snap["qualification_rationale"] or "Qualification stopped without a qualified candidate.",
            "requested_action": "Review or revise the research preparation, then resume this Cycle.",
            "resume_behavior": "Resume restarts qualification from Research Lead with a fresh frontier before Challenger review.",
        }
    elif snap.get("human_attention") and str(sci.get("status") or "") == "needs_attention":
        snap["attention"] = {
            "code": "human_attention",
            "reason": str(snap["human_attention"]),
            "requested_action": "Review the request and resume when ready.",
            "resume_behavior": "Resume continues from the persisted safe checkpoint.",
        }
    snap["resumable"] = bool(sci.get("resumable", snap.get("resumable", False)))
    snap["execution_active"] = bool(sci.get("execution_active", False))
    snap["owner_pid"] = sci.get("owner_pid")
    snap["last_safe_checkpoint"] = sci.get("last_safe_checkpoint")
    snap["recovery"] = dict(sci.get("recovery") or {})

    runtime_root = snap.get("research_root")
    # Task-level legacy runners and V2 research Workers have distinct owner
    # registries. A live, identity-verified Worker for *this exact Cycle*
    # overrides the obsolete legacy ORPHANED state in the dashboard only.
    worker_owner = None
    if runtime_root and sci.get("worker_id") and sci.get("cycle_id"):
        try:
            from theory_master.worker_runtime import list_workers
            worker_owner = next((w for w in list_workers(Path(str(runtime_root)))
                                 if w.get("worker_id") == sci["worker_id"]
                                 and str(w.get("current_cycle")) == str(sci["cycle_id"])
                                 and w.get("status") in {"RUNNING", "STARTING", "PAUSE_REQUESTED", "STOP_REQUESTED"}
                                 and w.get("process_alive")), None)
        except (OSError, ValueError):
            worker_owner = None
    if worker_owner and str(sci.get("status")).lower() == "active":
        snap["status"] = "RUNNING"
        snap["execution_active"] = True
        snap["owner_pid"] = worker_owner.get("pid")
        snap["runtime_health"] = "WORKER_RUNNING"
        snap["worker_id"] = worker_owner.get("worker_id")
    managed = None
    if runtime_root:
        try:
            from theory_master.runtime_manager import runtime_health
            managed = runtime_health(Path(str(runtime_root)))
        except Exception:
            managed = None
    if not worker_owner and isinstance(managed, dict) and managed.get("state") != "ABSENT":
        snap["runtime_health"] = managed.get("state")
        snap["heartbeat_age_s"] = managed.get("heartbeat_age_s")
        registry = managed.get("registry") if isinstance(managed.get("registry"), dict) else {}
        snap["supervisor_pid"] = registry.get("supervisor_pid")
        runtime_elapsed = registry.get("active_elapsed_s")
        if runtime_elapsed is not None:
            try:
                elapsed = max(0.0, float(runtime_elapsed))
                snap["runtime_active_elapsed_s"] = elapsed
                snap["elapsed_s"] = max(float(snap.get("elapsed_s") or 0.0), elapsed)
                snap["project_elapsed_s"] = max(float(snap.get("project_elapsed_s") or 0.0), elapsed)
            except (TypeError, ValueError):
                pass
        if managed.get("active"):
            snap["execution_active"] = True
            if managed.get("state") == "UNRESPONSIVE":
                snap["status"] = "UNRESPONSIVE"
        elif managed.get("state") == "ORPHANED" and snap.get("status") in {"STARTING", "RUNNING"}:
            snap["execution_active"] = False
            snap["status"] = "ORPHANED"
            snap["resumable"] = True
    if (not worker_owner and snap["execution_active"] and snap.get("owner_pid")
            and not (isinstance(managed, dict) and managed.get("active"))):
        try:
            os.kill(int(snap["owner_pid"]), 0)
        except (OSError, ValueError):
            snap["execution_active"] = False
            if snap.get("status") in {"STARTING", "RUNNING"}:
                snap["status"] = "ORPHANED"
                snap["resumable"] = True

    if not snap["execution_active"]:
        current = snap.get("current_agent")
        agents = snap.get("agents") or {}
        if current in agents:
            agent = agents[current]
            if agent.get("state") in {"running", "rework", "waiting"}:
                agent["state"] = "done"
            agent["current_tool"] = None
            agent["activity"] = None
        snap["current_agent"] = None
        snap["current_thread_id"] = None
        snap["current_turn_id"] = None
        snap["current_tool"] = None
        snap["current_activity"] = None
    return snap


def project_from_scientific(research_root: Path) -> dict[str, Any]:
    """Rebuild a coarse RunState from the scientific ledger when no run_state exists."""
    from theory_master.specification import latest_cycle_dir
    parent = Path(research_root)
    cycle = latest_cycle_dir(parent)
    root = cycle or parent
    state_path = root / "research_state.json"
    events_path = root / "events.jsonl"
    snap = blank_run_state()
    snap["research_root"] = str(parent)
    snap["run_dir"] = str(root) if cycle is not None else ""
    if not state_path.exists():
        return snap
    sci = json.loads(state_path.read_text(encoding="utf-8"))
    snap["goal_id"] = sci.get("goal_id") or ""
    goal_completion = sci.get("goal_completion") if isinstance(sci.get("goal_completion"), dict) else {}
    snap["status"] = ("COMPLETE" if goal_completion.get("verdict") == "passed"
                      else SCI_STATUS_MAP.get(str(sci.get("status") or ""), "STARTING"))
    snap["phase"] = str(sci.get("phase") or "")
    snap.update(investigation_details(sci))
    snap["qualification_status"] = str(sci.get("qualification_status") or "pending")
    snap["qualification_rationale"] = str(sci.get("qualification_rationale") or "")
    lock = sci.get("hypothesis_lock")
    snap["hypothesis_lock"] = dict(lock) if isinstance(lock, dict) else {}
    snap["hypothesis_locked"] = bool(sci.get("hypothesis_locked") or (isinstance(lock, dict) and lock.get("status") == "locked"))
    branches = sci.get("branches") or []
    snap["cycle_index"] = int(sci.get("invocation_cycle_index") or 1)
    if branches:
        active = branches[-1]
        snap["hypothesis"] = str(active.get("hypothesis") or "")
        snap["hypothesis_title"] = str(active.get("title") or "")
        snap["branch_id"] = str(active.get("id") or "")
        snap["report_path"] = active.get("note_path")
    for role, thread_id in current_role_threads(sci).items():
        key = _agent_key(str(role))
        if key in snap["agents"] and thread_id:
            snap["agents"][key]["thread_id"] = thread_id
            snap["agents"][key]["state"] = "done"
    if events_path.exists():
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rounds = []
        for event in events:
            payload = event.get("payload") or {}
            role = str(payload.get("role") or "")
            key = _agent_key(role)
            if event.get("type") == "role.started" and key in snap["agents"]:
                snap["agents"][key]["thread_id"] = payload.get("thread_id") or snap["agents"][key]["thread_id"]
                snap["current_agent"] = key
                snap["current_thread_id"] = snap["agents"][key]["thread_id"]
                if role in CAPABILITIES:
                    round_no = payload.get("investigation_round_count", len(rounds) + 1)
                    attempt_no = payload.get("investigation_attempt")
                    existing = next((
                        item for item in reversed(rounds)
                        if item.get("executor_role") == key
                        and item.get("round") == round_no
                        and item.get("investigation_attempt") == attempt_no
                        and (not payload.get("thread_id") or item.get("thread_id") == payload.get("thread_id"))
                    ), None) if role == "investigator" else None
                    if existing is not None:
                        existing["state"] = "running"
                        existing["internal_turns"] = max(
                            int(existing.get("internal_turns") or 1) + 1,
                            int(payload.get("internal_turn") or 0),
                        )
                    else:
                        rounds.append({
                            "round": round_no,
                            "investigation_attempt": attempt_no,
                            "executor_role": key, "state": "running",
                            "thread_id": payload.get("thread_id"),
                            "internal_turns": int(payload.get("internal_turn") or 1),
                        })
                    if role == "literature":
                        snap["literature_requested"] = True
            elif event.get("type") == "role.completed" and key in snap["agents"]:
                snap["agents"][key]["state"] = "done"
                output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
                for item in reversed(rounds):
                    if item.get("state") == "running" and (item.get("executor_role") or item.get("role")) == key:
                        item["state"] = "done"
                        break
            elif event.get("type") == "decision.recorded":
                snap["last_verdict"] = payload.get("verdict")
        snap["investigation_rounds"] = rounds
        if events:
            snap["last_event"] = str(events[-1].get("type") or "")
    if sci.get("investigation_attempt"):
        active_threads = current_role_threads(sci)
        for role in CAPABILITIES:
            snap["agents"][role]["thread_id"] = active_threads.get(role)
            snap["agents"][role]["state"] = "done" if active_threads.get(role) else "idle"
    phase = str(sci.get("phase") or "")
    if ":running" in phase:
        current = phase.split(":", 1)[0]
        key = _agent_key(current)
        if key in snap["agents"]:
            snap["agents"][key]["state"] = "running"
            snap["current_agent"] = key
            snap["current_activity"] = "Codex turn (reconstructed; no stream)"
    return reconcile_runtime_state(snap, sci)
