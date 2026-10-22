from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from theory_master.cycle_git_commit import commit_cycle_dir
from theory_master.runtime import compact_ts, now, write_json
from theory_master.specification import cycle_number, master_run_id
from theory_master.observability.costs import aggregate_cost, aggregate_usage, assess_cost, codex_turn_usage
from theory_master.observability.models import (
    CAPABILITIES,
    DISPLAY_ROLE,
    SCI_STATUS_MAP,
    blank_run_state,
    current_role_threads,
    investigation_details,
)
from theory_master.observability.lifecycle_actions import LifecycleActions
from theory_master.observability.incident_registry import register_cycle_incident
from theory_master.observability.collector_recovery import CollectorRecoveryMixin
from theory_master.observability.collector_projection import CollectorProjectionMixin
from theory_master.read_trace import paths_from_event
from theory_master.skills import loaded_skill_for_path

def _agent_key(role: str) -> str:
    return role


class Collector(CollectorRecoveryMixin, CollectorProjectionMixin):
    """In-process RunState + runtime_events.jsonl. Thread-safe snapshots for TUI."""

    def __init__(
        self,
        spec: Any | None = None,
        *,
        system_failure_actions: bool = False,
        email_notifications: bool = False,
        cycle_audit_actions: bool = False,
        attention_takeover_actions: bool = False,
    ):
        self.spec = spec
        self.system_failure_actions = bool(system_failure_actions)
        self.email_notifications = bool(email_notifications)
        self.cycle_audit_actions = bool(cycle_audit_actions)
        self.attention_takeover_actions = bool(attention_takeover_actions)
        self._lock = threading.Lock()
        self.state = blank_run_state()
        self.state["system_failure_actions_enabled"] = self.system_failure_actions
        self.state["cycle_audit_actions_enabled"] = self.cycle_audit_actions
        self.state["attention_takeover_enabled"] = self.attention_takeover_actions
        self.state["supervisor_enabled"] = self.attention_takeover_actions
        self.run_dir: Path | None = None
        self._events_path: Path | None = None
        self._log_path: Path | None = None
        self._started_mono = time.monotonic()
        self._total_started_mono: float | None = None
        self._finished_mono: float | None = None
        self._role_started_mono: dict[str, float] = {}
        self._role_elapsed_base: dict[str, float] = {}
        self._compaction_started_mono: dict[str, float] = {}
        self._cycle_elapsed_base = 0.0
        self._project_elapsed_base = 0.0
        self._last_rework = 0
        self._terminal_email_sent = False
        self._cycle_audit_spawned = False
        self._attention_takeover_spawned = False
        self._project_agent_totals: dict[str, dict[str, Any]] = {}
        self._cycle_count = 0
        self._lifecycle_actions = LifecycleActions(self)
        if spec is not None:
            self._bind_spec(spec)

    def _configured_max_cycles(self, fallback: int | None = None) -> int:
        default = max(1, int(fallback or self.state.get("max_cycles") or getattr(self.spec, "max_cycles", 1) or 1))
        raw_path = self.state.get("config_path") or getattr(self.spec, "config_path", None)
        if not raw_path:
            return default
        try:
            payload = json.loads(Path(str(raw_path)).read_text(encoding="utf-8"))
            return max(1, int(payload.get("max_cycles", default)))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return default

    def _bind_spec(self, spec: Any) -> None:
        self.spec = spec
        self.state["goal_id"] = getattr(spec, "goal_id", "")
        self.state["config_path"] = str(getattr(spec, "config_path", "") or "")
        self.state["max_cycles"] = self._configured_max_cycles(int(getattr(spec, "max_cycles", 1) or 1))
        self.state["max_rounds"] = int(getattr(spec, "max_rounds", 12) or 12)
        for key in ("max_rounds_per_investigation_attempt", "max_judge_reworks_per_cycle",
                    "max_total_investigation_rounds_per_cycle"):
            value = getattr(spec, key, None)
            if value is not None:
                self.state[key] = value
        self.state["plan_path"] = str(getattr(spec, "document", "") or "")
        self.state["branch_canvas"] = str(getattr(spec, "branch_canvas", "") or "")
        self.state["notes_dir"] = str(getattr(spec, "notes_dir", "") or "")
        self.state["research_root"] = str(getattr(spec, "research_root", "") or "")
        self.state["task_home"] = str(getattr(spec, "task_home", "") or "")
        from theory_master.quota_auto_adjust import quota_auto_adjust_enabled, read_controller_state
        self.state["quota_auto_adjust_enabled"] = quota_auto_adjust_enabled(Path(self.state["research_root"]))
        self.state["quota_auto_adjust"] = read_controller_state(Path(self.state["research_root"]))
        routes = getattr(self, "_routes", None)
        if routes:
            for role, route in routes.items():
                key = _agent_key(role)
                agent = self.state["agents"].get(key) or self.state.get("auxiliary_agents", {}).get(key)
                if agent is not None:
                    agent["model"] = getattr(route, "model", None)
                    agent["reasoning_effort"] = getattr(route, "reasoning_effort", None)
                    agent["provider"] = getattr(route, "provider", None)
                    agent["model_vendor"] = getattr(route, "model_vendor", None) or getattr(route, "provider", None)

    def set_routes(self, routes: dict[str, Any]) -> None:
        self._routes = routes
        with self._lock:
            for role, route in routes.items():
                key = _agent_key(role)
                agent = self.state["agents"].get(key) or self.state.get("auxiliary_agents", {}).get(key)
                if agent is not None:
                    agent["model"] = getattr(route, "model", None)
                    agent["reasoning_effort"] = getattr(route, "reasoning_effort", None)
                    agent["provider"] = getattr(route, "provider", None)
                    agent["model_vendor"] = getattr(route, "model_vendor", None) or getattr(route, "provider", None)





    def start_run(self, run_id: str, spec: Any | None = None, run_dir: Path | None = None, notes_dir: Path | None = None, branch_canvas: Path | None = None, cycle_workspace: Path | None = None, same_cycle_continuation: bool = False) -> None:
        if spec is not None:
            self._bind_spec(spec)
        previous_total_started_at = self.state.get("total_started_at")
        with self._lock:
            self._refresh_elapsed()
            previous_project_elapsed = float(self.state.get("project_elapsed_s") or 0.0)
            self._accumulate_cycle()
            if same_cycle_continuation:
                active_run_id = str(self.state.get("run_id") or self.state.get("cycle_id") or "")
                if active_run_id != run_id:
                    raise ValueError("same-Cycle continuation must retain the active Collector run")
                current_cycle_index = int(self.state.get("cycle_index") or 0)
                if current_cycle_index < 1:
                    raise ValueError("same-Cycle continuation has no Collector cycle slot")
                self._cycle_count = max(self._cycle_count, current_cycle_index)
            else:
                self._cycle_count += 1
                current_cycle_index = self._cycle_count
            previous_timeline = deepcopy(self.state.get("agent_timeline") or [])
            previous_action_history = deepcopy(self.state.get("action_history") or [])
        research = Path(self.state["research_root"] or getattr(spec, "research_root", "."))
        if run_dir is None:
            run_dir = research / run_id
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        persisted: dict[str, Any] = {}
        persisted_path = run_dir / "run_state.json"
        if persisted_path.is_file():
            try:
                loaded = json.loads(persisted_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = None
            if isinstance(loaded, dict) and str(loaded.get("run_id") or loaded.get("cycle_id") or "") == run_id:
                persisted = loaded
        if persisted:
            previous_total_started_at = persisted.get("total_started_at") or previous_total_started_at
            previous_timeline = deepcopy(persisted.get("agent_timeline") or previous_timeline)
            previous_action_history = deepcopy(persisted.get("action_history") or previous_action_history)
        persisted_agents = persisted.get("agents") if isinstance(persisted.get("agents"), dict) else {}
        persisted_auxiliary_agents = persisted.get("auxiliary_agents") if isinstance(persisted.get("auxiliary_agents"), dict) else {}
        persisted_cycle_elapsed = float(persisted.get("cycle_elapsed_s") or persisted.get("elapsed_s") or 0.0)
        persisted_project_elapsed = float(persisted.get("project_elapsed_s") or persisted.get("elapsed_s") or 0.0)
        historical_role_elapsed = self._historical_role_elapsed(run_dir) if persisted else {}
        with self._lock:
            self.state = blank_run_state()
            self.state["system_failure_actions_enabled"] = self.system_failure_actions
            self.state["cycle_audit_actions_enabled"] = self.cycle_audit_actions
            self.state["attention_takeover_enabled"] = self.attention_takeover_actions
            self.state["supervisor_enabled"] = self.attention_takeover_actions
            self._bind_spec(spec or self.spec)
            self.state["run_id"] = run_id
            bound_spec = spec or self.spec
            if getattr(bound_spec, "task_mode", False):
                task_master_id = int(getattr(bound_spec, "master_id", 0) or 0)
            else:
                task_master_id = master_run_id(run_id)
            self.state["master_run_id"] = task_master_id
            self.state["master_id"] = task_master_id
            self.state["cycle_id"] = run_id
            self.state["cycle_number"] = cycle_number(run_id)
            self.state["status"] = "STARTING"
            self.state["phase"] = "starting"
            cycle_started_at = now()
            self.state["total_started_at"] = previous_total_started_at or persisted.get("total_started_at") or cycle_started_at
            self.state["started_at"] = cycle_started_at
            self.state["cycle_started_at"] = persisted.get("cycle_started_at") or cycle_started_at
            # cycle_index is invocation-scoped observability, not durable Cycle identity.
            # A resumed Cycle is slot 1 of the new invocation; never restore its old ordinal.
            self.state["cycle_index"] = current_cycle_index
            for item in previous_timeline:
                if isinstance(item, dict) and item.get("active"):
                    role = str(item.get("role") or "")
                    prior = persisted_agents.get(role) if isinstance(persisted_agents, dict) else None
                    if isinstance(prior, dict):
                        prior_total = float(prior.get("elapsed_s") or 0.0)
                        finished_total = sum(
                            float(other.get("elapsed_s") or 0.0)
                            for other in previous_timeline
                            if isinstance(other, dict) and other is not item and other.get("role") == role and not other.get("active")
                        )
                        item["elapsed_s"] = max(float(item.get("elapsed_s") or 0.0), max(0.0, prior_total - finished_total))
                    item["active"] = False
            self.state["agent_timeline"] = previous_timeline
            self.state["action_history"] = previous_action_history[-100:]
            for role, prior in persisted_agents.items():
                if role in self.state["agents"] and isinstance(prior, dict):
                    persisted_elapsed = float(prior.get("elapsed_s") or 0.0)
                    self.state["agents"][role]["elapsed_s"] = max(persisted_elapsed, float(historical_role_elapsed.get(role, 0.0)))
            for role, elapsed in historical_role_elapsed.items():
                if role in self.state["agents"] and role not in persisted_agents:
                    self.state["agents"][role]["elapsed_s"] = float(elapsed)
            for role, prior in persisted_auxiliary_agents.items():
                if role in self.state.get("auxiliary_agents", {}) and isinstance(prior, dict):
                    self.state["auxiliary_agents"][role]["elapsed_s"] = float(prior.get("elapsed_s") or 0.0)
            self.state["run_dir"] = str(run_dir)
            if notes_dir is not None:
                self.state["notes_dir"] = str(notes_dir)
            if branch_canvas is not None:
                self.state["branch_canvas"] = str(branch_canvas)
            if cycle_workspace is not None:
                self.state["cycle_workspace"] = str(cycle_workspace)
            self.state["agents"]["research_lead"]["state"] = "waiting"
            now_mono = time.monotonic()
            self._started_mono = now_mono
            self._total_started_mono = now_mono
            self._finished_mono = None
            self._role_started_mono = {}
            self._role_elapsed_base = {
                role: float(agent.get("elapsed_s") or 0.0)
                for role, agent in self.state["agents"].items()
            }
            self._cycle_elapsed_base = persisted_cycle_elapsed if persisted else 0.0
            self._project_elapsed_base = persisted_project_elapsed if persisted else previous_project_elapsed
            # Resume of the SAME Cycle must preserve the prior Judge rework
            # counter. Otherwise the first role.started after start_run sees
            # the already recorded #1 as 0→1 and emits a fake NEW Judge
            # request at every Resume timestamp.
            self._last_rework = max(0, int(
                persisted.get("judge_rework_count") or persisted.get("rework_count") or 0
            )) if persisted else 0
            self._terminal_email_sent = False
            self._cycle_audit_spawned = False
            self.run_dir = run_dir
            self._events_path = run_dir / "runtime_events.jsonl"
            self._log_path = run_dir / "orchestrator.log"
            if persisted:
                self._restore_agent_history(
                    persisted_agents,
                    run_dir,
                    persisted_auxiliary_agents=persisted_auxiliary_agents,
                )
                self._restore_project_history(persisted)
        if same_cycle_continuation:
            self._emit({"event": "RUN_CONTINUED", "run_id": run_id, "goal_id": self.state["goal_id"]})
            self._emit({"event": "CYCLE_CONTINUED", "cycle_index": current_cycle_index, "run_id": run_id})
            self.log("INFO", f"Cycle {current_cycle_index} continued ({run_id})")
        else:
            self._emit({"event": "RUN_STARTED", "run_id": run_id, "goal_id": self.state["goal_id"]})
            self._emit({"event": "CYCLE_STARTED", "cycle_index": current_cycle_index, "run_id": run_id})
            self.log("INFO", f"Cycle {current_cycle_index} started ({run_id})")
        self._flush_state()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_elapsed()
            self.state["max_cycles"] = self._configured_max_cycles()
            result = deepcopy(self.state)
        # Supervisor may update quota waiting/selected-account snapshots while
        # the live Collector is no longer receiving runtime events. Read the
        # controller on each TUI snapshot; this is a read-only projection.
        if self.spec is not None and getattr(self.spec, "research_root", None):
            from theory_master.quota_auto_adjust import quota_auto_adjust_enabled, read_controller_state
            root = Path(self.spec.research_root)
            result["quota_auto_adjust_enabled"] = quota_auto_adjust_enabled(root)
            result["quota_auto_adjust"] = read_controller_state(root)
        return result

    def runtime_events(self, limit: int | None = 400) -> list[dict[str, Any]]:
        path = self._events_path
        if path is None or not path.exists():
            return []
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        selected = lines if limit is None or limit <= 0 else lines[-limit:]
        return [json.loads(line) for line in selected]

    def logs(self, limit: int = 200) -> list[str]:
        path = self._log_path
        if path is None or not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        return lines[-limit:]

    def log(self, level: str, message: str) -> None:
        path = self._log_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = compact_ts(now())
        if level == "INFO":
            line = f"{stamp}  {message}\n"
        else:
            line = f"{stamp}  [{level}] {message}\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def on_scientific(self, kind: str, payload: dict[str, Any], sci: dict[str, Any]) -> None:
        prev_rework = self._last_rework
        with self._lock:
            self._apply_scientific(kind, payload, sci)
            rework = int(self.state.get("rework_count") or 0)
        self._emit({"event": self._science_event_name(kind, payload), **self._science_payload(kind, payload)})
        if kind == "run.failed" and self.run_dir is not None:
            error = str(payload.get("error") or self.state.get("last_event") or "run failed")
            register_cycle_incident(
                self.run_dir,
                severity="error",
                kind="system_failure",
                source_event="run.failed",
                summary=error,
                code=str(payload.get("code") or "run_failed"),
                discriminator=f"{self.state.get('phase') or ''}:run_failed",
                fields={"phase": self.state.get("phase"), "role": payload.get("role"), "error_type": payload.get("error_type")},
            )
        # Transient FAILED events are visible in the incident ledger / Web bell.
        # Only the owning Supervisor knows after runtime recovery review whether
        # the process is truly unable to continue and requires an email.
        if kind == "run.paused" and self.email_notifications:
            self._notify_terminal_email(kind, payload)
        if kind == "run.failed" and self.system_failure_actions:
            self._spawn_error_repair(payload)
        role = str(payload.get("role") or "")
        if rework > prev_rework:
            self._emit({
                "event": "JUDGE_REWORK_REQUESTED",
                "target_agent": _agent_key(role) if role in CAPABILITIES else self.state.get("last_target"),
                "target_thread_id": payload.get("thread_id"),
                "judge_retry_index": rework,
                "investigation_attempt": self.state["investigation_attempt"],
            })
        self._flush_state()

    def _notify_terminal_email(self, kind: str, payload: dict[str, Any]) -> None:
        """Compatibility delegate to isolated lifecycle side effects."""
        self._lifecycle_actions.notify_terminal_email(kind, payload)

    def _spawn_error_repair(self, payload: dict[str, Any]) -> None:
        """Compatibility delegate to isolated lifecycle side effects."""
        self._lifecycle_actions.spawn_error_repair(payload)

    def _spawn_cycle_audit(self) -> None:
        """Compatibility delegate to isolated lifecycle side effects."""
        self._lifecycle_actions.spawn_cycle_audit()

    def _spawn_attention_takeover(self) -> None:
        """Delegate NEEDS_ATTENTION takeover to isolated lifecycle side effects."""
        self._lifecycle_actions.spawn_attention_takeover()

    def agent_total_tokens(self, role: str) -> int:
        """Read accumulated live usage under the Collector lock.

        tokens_total already includes cached input; never add cache twice.
        Avoid an expensive full TUI snapshot with quota-side reads.
        """
        with self._lock:
            agent = (self.state.get("agents") or {}).get(_agent_key(role)) or {}
            return int(agent.get("tokens_total") or 0)

    def record_native_goal_usage(self, role: str, *, observed_tokens: int, allowance_tokens: int) -> None:
        """Persist live high-water WITHOUT waiting for the entire Goal's last Turn.

        This is operational token accounting, NOT a completed scientific Finding.
        Budget guard reads exactly this same persisted high-water after recovery.
        """
        if observed_tokens < 0:
            raise ValueError("native goal token usage cannot be negative")
        with self._lock:
            agent = (self.state.get("agents") or {}).get(_agent_key(role))
            if agent is None:
                raise ValueError(f"no configured agent: {role}")
            amount = max(
                int(agent.get("tokens_total_live") or 0),
                int(agent.get("tokens_total") or 0),
                observed_tokens,
            )
            agent["tokens_total_live"] = amount
            committed = int(agent.get("tokens_total") or 0)
            agent["tokens_total"] = max(committed, amount)
            self.state["tokens_total_live"] = max(
                int(self.state.get("tokens_total_live") or 0), amount
            )
            self.state["cycle_tokens_total_live"] = self.state["tokens_total_live"]
            self.state["tokens_total"] = max(int(self.state.get("tokens_total") or 0), amount)
            self.state["cycle_tokens_total"] = self.state["tokens_total"]
            budget = self.state.setdefault("investigator_budget", {})
            if _agent_key(role) == "investigator":
                budget["observed_tokens"] = max(int(budget.get("observed_tokens") or 0), amount)
                budget["allowance_tokens"] = allowance_tokens
                budget["remaining_tokens"] = max(0, allowance_tokens - amount)
                budget["exhausted"] = amount >= allowance_tokens
        self._flush_state()

    def on_persist(self, sci: dict[str, Any]) -> None:
        with self._lock:
            self._sync_from_research(sci)
        self._flush_state()

    def on_agent_input(self, *, role: str, stage: str, content: str, input_kind: str = "task") -> None:
        """Record actual Agent input for audit; input_kind identifies SDK payload layer.

        Historical Investigator task/continuation events predating Native Goal
        may be logical Stage projections, not literal Codex Goal input.
        """
        self._emit({
            "event": "AGENT_INPUT",
            "agent": _agent_key(role),
            "role": _agent_key(role),
            "stage": stage,
            "input_kind": input_kind,
            "content": content,
        })

    def on_codex(self, mapped: dict[str, Any], *, role: str, thread_id: str) -> None:
        if not mapped:
            return
        role_key = _agent_key(role)
        if mapped.get("event") == "COMPACTION_COMPLETED":
            with self._lock:
                agent = self.state.get("agents", {}).get(role_key) or {}
                previous = agent.get("last_compaction") if isinstance(agent.get("last_compaction"), dict) else {}
                saw_start = previous.get("status") == "started" or role_key in self._compaction_started_mono
                context_before = agent.get("context_tokens")
                context_window = agent.get("context_window")
            if not saw_start:
                # Some app-server versions expose only the confirmed compacted
                # notification. Preserve the requested lifecycle without inventing
                # provider data: START is explicitly marked as inferred and has no duration.
                self.on_codex({
                    "event": "COMPACTION_STARTED",
                    "episode_id": mapped.get("episode_id") or thread_id,
                    "context_before": context_before,
                    "context_window": context_window,
                    "compact_threshold_tokens": mapped.get("compact_threshold_tokens"),
                    "compact_scope": mapped.get("compact_scope"),
                    "inferred_from_completed": True,
                }, role=role, thread_id=thread_id)
        if mapped.get("event") == "LLM_TURN_COMPLETED" and not mapped.get("raw_usage"):
            recovered = codex_turn_usage(thread_id, str(mapped.get("turn_id") or ""))
            if recovered:
                mapped = {**mapped, "raw_usage": recovered}
        with self._lock:
            self._apply_codex(mapped, role=_agent_key(role), thread_id=thread_id)
        # Keep SDK/provider and billing internals in memory for state updates,
        # but persist only the model conversation contract.
        event_fields = {
            "event", "agent", "role", "thread_id", "turn_id", "message_role",
            "content", "error", "tokens_in", "tokens_out", "tokens_total", "tool", "detail",
            "episode_id", "context_before", "context_after", "context_tokens", "context_window",
            "compact_threshold_tokens", "compact_scope", "duration_s", "compaction_usage", "compaction_cost",
            "inferred_from_completed", "provider", "thread_action", "native_compaction_enabled",
            "skill", "skill_path", "skill_scope", "load_index", "first_load_in_turn", "detection",
        }
        logged = {key: value for key, value in mapped.items() if key in event_fields}
        logged.setdefault("agent", role)
        logged.setdefault("role", role)
        logged.setdefault("thread_id", thread_id)
        self._emit(logged)
        if mapped.get("event") == "TOOL_ACTIVITY":
            seen: set[str] = set()
            for path in paths_from_event(mapped):
                ref = loaded_skill_for_path(role, path, task_config_path=getattr(self.spec, "config_path", None))
                if ref is None or ref.directory in seen:
                    continue
                seen.add(ref.directory)
                with self._lock:
                    agent = self.state.get("agents", {}).get(_agent_key(role)) or {}
                    prior = int((agent.get("skill_load_counts") or {}).get(ref.directory) or 0)
                    skill_event = {
                        "event": "SKILL_LOADED",
                        "agent": _agent_key(role),
                        "role": _agent_key(role),
                        "thread_id": thread_id,
                        "turn_id": mapped.get("turn_id"),
                        "skill": ref.directory,
                        "skill_path": str(ref.skill_md),
                        "skill_scope": ref.scope,
                        "load_index": prior + 1,
                        "first_load_in_turn": prior == 0 or agent.get("skill_last_turn", {}).get(ref.directory) != mapped.get("turn_id"),
                        "detection": "observed_file_read",
                    }
                    agent.setdefault("skill_last_turn", {})[ref.directory] = mapped.get("turn_id")
                    self._apply_codex(skill_event, role=_agent_key(role), thread_id=thread_id)
                self._emit(skill_event)
        self._flush_state()

    def finish(self, sci: dict[str, Any] | None = None) -> None:
        failed = False
        failure_detail = "failed"
        with self._lock:
            if sci is not None:
                self._sync_from_research(sci)
            if self._finished_mono is None:
                self._finished_mono = time.monotonic()
            failed = self.state.get("status") == "FAILED"
            failure_detail = str(self.state.get("last_event") or "failed")
        if failed and self.run_dir is not None:
            register_cycle_incident(
                self.run_dir,
                severity="error",
                kind="system_failure",
                source_event="collector.finish",
                summary=failure_detail,
                code="run_failed",
                discriminator=f"{self.state.get('phase') or ''}:run_failed",
                fields={"phase": self.state.get("phase")},
            )
        if failed and self.system_failure_actions:
            self._spawn_error_repair({"event": "run.failed", "error": failure_detail, "code": "run_failed"})
        status = str(self.state.get("status") or "").upper()
        if status == "NEEDS_ATTENTION" and self.run_dir is not None:
            attention = self.state.get("attention") if isinstance(self.state.get("attention"), dict) else {}
            attention_code = str(attention.get("code") or self.state.get("closure_reason") or "needs_attention")
            reason = str(attention.get("reason") or self.state.get("human_attention") or "NEEDS_ATTENTION")
            register_cycle_incident(
                self.run_dir,
                severity="attention",
                kind="needs_attention",
                source_event="collector.finish",
                summary=reason,
                code=attention_code,
                discriminator=f"{self.state.get('phase') or ''}:{attention_code}",
                fields={
                    "phase": self.state.get("phase"),
                    "resumable": self.state.get("resumable"),
                    "next_executor": self.state.get("next_executor"),
                },
            )
        if status == "NEEDS_ATTENTION" and self.email_notifications:
            attention = self.state.get("attention") if isinstance(self.state.get("attention"), dict) else {}
            self._notify_terminal_email("run.needs_attention", {
                "attention_code": str(attention.get("code") or self.state.get("closure_reason") or "needs_attention"),
                "reason": str(attention.get("reason") or self.state.get("human_attention") or "SDK cannot continue"),
                "stage": self.state.get("phase"),
            })

        # SDK-internal review ran before collector.finish. Terminal Attention
        # notification never launches an external Codex repair Agent.
        if self.cycle_audit_actions and status in {"OVER", "COMPLETE", "COMPLETED", "NEEDS_ATTENTION"}:
            self._spawn_cycle_audit()
        self.state["cycle_git_commit"] = commit_cycle_dir(self.run_dir, status=status)
        self._flush_state()
        self._write_summary()

    def finish_invocation(self, sci: dict[str, Any]) -> None:
        """Finalize observability without emailing normal or operator-driven stops."""
        self.finish(sci)

















    def _emit(self, payload: dict[str, Any]) -> None:
        event = {"ts": now(), **payload}
        if "event" not in event:
            event["event"] = "UNKNOWN"
        path = self._events_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        with self._lock:
            kind = str(event.get("event") or "")
            action_text = self._action_text(event)
            if action_text:
                self.state["last_action"] = {
                    "at": event.get("ts"),
                    "event": kind,
                    "text": action_text,
                    "role": event.get("agent") or event.get("role"),
                }
                if self._keep_action_history(event, action_text):
                    history = self.state.setdefault("action_history", [])
                    entry = dict(self.state["last_action"])
                    if not history or history[-1] != entry:
                        history.append(entry)
                        if len(history) > 100:
                            del history[:-100]
            extra = event.get("tool") or event.get("path") or event.get("error") or event.get("agent") or ""
            extra = " ".join(str(extra).split())
            if len(extra) > 56:
                extra = extra[:55] + "…"
            if kind not in {"LLM_MESSAGE", "TOKEN_USAGE", "AGENT_INPUT"}:
                if kind == "RUN_FAILED" and event.get("error"):
                    self.state["last_event"] = str(event["error"])
                else:
                    self.state["last_event"] = f"{kind} {extra}".strip()
                line = f"{compact_ts(event.get('ts'))}  {kind} {extra}".rstrip()
                hist = self.state.setdefault("event_history", [])
                if not hist or hist[-1] != line:
                    hist.append(line)
                    if len(hist) > 100:
                        del hist[:-100]

    def _flush_state(self) -> None:
        if self.run_dir is None:
            return
        with self._lock:
            self._refresh_elapsed()
            self.state["max_cycles"] = self._configured_max_cycles()
            payload = deepcopy(self.state)
        write_json(self.run_dir / "run_state.json", payload)

    def _write_summary(self) -> None:
        if self.run_dir is None:
            return
        snap = self.snapshot()
        agents = {}
        for role, agent in snap["agents"].items():
            agents[role] = {
                "calls": 1 if agent.get("thread_id") else 0,
                "turns": agent.get("turn_count") or 0,
                "tokens": agent.get("tokens_total"),
                "time_s": agent.get("elapsed_s") or 0,
                "retries": agent.get("retry_count") or 0,
                "skills_available": list(agent.get("skills_available") or []),
                "skills_used": list(agent.get("skills_used") or []),
                "skill_load_events": int(agent.get("skill_load_events") or 0),
                "skill_load_counts": dict(agent.get("skill_load_counts") or {}),
            }
        summary = {
            "run_id": snap["run_id"],
            "elapsed_s": snap["elapsed_s"],
            "tokens_in": snap["tokens_in"],
            "tokens_out": snap["tokens_out"],
            "tokens_total": snap["tokens_total"],
            "cycle_index": snap["cycle_index"],
            "investigation_rounds": len(snap.get("investigation_rounds") or []),
            "judge_reworks": snap.get("rework_count") or 0,
            **investigation_details(snap),
            "literature_calls": 1 if snap.get("literature_requested") else 0,
            "agents": agents,
            "estimated_cost": None,
            "cost": snap.get("cost"),
        }
        write_json(self.run_dir / "runtime_summary.json", summary)


class FileSource:
    """Read-only attach source: latest run_state.json under research_root."""

    def __init__(self, research_root: Path):
        self.research_root = Path(research_root)

    @property
    def run_dir(self) -> Path | None:
        from theory_master.observability.projection import latest_run_dir
        return latest_run_dir(self.research_root)

    def _overlay_live_task_config(self, data: dict[str, Any]) -> dict[str, Any]:
        raw_path = data.get("config_path")
        if raw_path:
            try:
                payload = json.loads(Path(str(raw_path)).read_text(encoding="utf-8"))
                configured = int(payload.get("max_cycles", data.get("max_cycles") or 1))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                pass
            else:
                data["max_cycles"] = max(1, configured)
        from theory_master.attention_takeover import attention_takeover_enabled
        from theory_master.quota_auto_adjust import quota_auto_adjust_enabled, read_controller_state
        data["attention_takeover_enabled"] = attention_takeover_enabled(self.research_root)
        data["supervisor_enabled"] = data["attention_takeover_enabled"]
        data["quota_auto_adjust_enabled"] = quota_auto_adjust_enabled(self.research_root)
        data["quota_auto_adjust"] = read_controller_state(self.research_root)
        from theory_master.supervisor_recovery import projection
        try:
            folder = self.research_root / str(data.get('cycle_id') or '')
            data['supervisor_recovery'] = projection(folder) if folder.parent == self.research_root else {}
        except (OSError, ValueError, TypeError):
            data['supervisor_recovery'] = {'status': 'unreadable', 'executing': False}
        return data

    def snapshot(self) -> dict[str, Any]:
        from theory_master.observability.projection import load_latest_run_state, project_from_scientific, reconcile_runtime_state
        data = load_latest_run_state(self.research_root)
        if data is not None:
            run_dir = self.run_dir
            state_path = None if run_dir is None else run_dir / "research_state.json"
            if state_path is not None and state_path.is_file():
                try:
                    sci = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    sci = None
                if isinstance(sci, dict):
                    reconcile_runtime_state(data, sci)
            return self._overlay_live_task_config(data)
        return self._overlay_live_task_config(project_from_scientific(self.research_root))

    def runtime_events(self, limit: int | None = 400) -> list[dict[str, Any]]:
        run_dir = self.run_dir
        path = None if run_dir is None else run_dir / "runtime_events.jsonl"
        if path is None or not path.exists():
            return []
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        selected = lines if limit is None or limit <= 0 else lines[-limit:]
        return [json.loads(line) for line in selected]

    def logs(self, limit: int = 200) -> list[str]:
        run_dir = self.run_dir
        path = None if run_dir is None else run_dir / "orchestrator.log"
        if path is None or not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()[-limit:]
