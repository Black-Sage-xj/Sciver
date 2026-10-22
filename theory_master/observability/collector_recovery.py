from __future__ import annotations

"""Persisted observability recovery and historical accounting."""

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from theory_master.observability.costs import (
    aggregate_cost, aggregate_usage, assess_cost, codex_turn_usage,
)

class CollectorRecoveryMixin:
    def _historical_role_elapsed(self, run_dir: Path) -> dict[str, float]:
        """Reconstruct per-role Codex turn time from persisted runtime events.

        This is a recovery fallback for Cycles created by older versions that
        cleared agent elapsed time on resume. Completed turn pairs are summed;
        an unclosed turn is capped at the next terminal runtime event instead of
        extending across the offline/resume gap.
        """
        path = Path(run_dir) / "runtime_events.jsonl"
        if not path.is_file():
            return {}
        elapsed: dict[str, float] = {}
        active: dict[str, datetime] = {}
        terminal = {"RUN_FAILED", "RUN_INTERRUPTED", "WORKFLOW_STOPPED", "QUALIFICATION_NEEDS_ATTENTION"}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                stamp = datetime.fromisoformat(str(event.get("ts") or ""))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            kind = str(event.get("event") or "")
            role = str(event.get("role") or event.get("agent") or "")
            if kind == "LLM_TURN_STARTED" and role in self.state.get("agents", {}):
                previous = active.get(role)
                if previous is not None:
                    elapsed[role] = elapsed.get(role, 0.0) + max(0.0, (stamp - previous).total_seconds())
                active[role] = stamp
            elif kind == "LLM_TURN_COMPLETED" and role in active:
                previous = active.pop(role)
                elapsed[role] = elapsed.get(role, 0.0) + max(0.0, (stamp - previous).total_seconds())
            elif kind in terminal and active:
                for active_role, previous in list(active.items()):
                    elapsed[active_role] = elapsed.get(active_role, 0.0) + max(0.0, (stamp - previous).total_seconds())
                    active.pop(active_role, None)
        return elapsed

    def _completed_turns_by_role(self, run_dir: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], dict[str, str]]:
        """Read completed Codex request identities and start counts from runtime events."""
        path = Path(run_dir) / "runtime_events.jsonl"
        completed: dict[str, list[dict[str, Any]]] = {}
        started: dict[str, int] = {}
        lifecycle: dict[str, str] = {}
        if not path.is_file():
            return completed, started, lifecycle
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return completed, started, lifecycle
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = str(event.get("role") or event.get("agent") or "")
            if role not in self.state.get("agents", {}):
                continue
            kind = event.get("event")
            if kind == "LLM_TURN_STARTED":
                started[role] = started.get(role, 0) + 1
            elif kind == "LLM_TURN_COMPLETED" and event.get("thread_id") and event.get("turn_id"):
                completed.setdefault(role, []).append({
                    "thread_id": str(event["thread_id"]),
                    "turn_id": str(event["turn_id"]),
                })
            if kind in {"AGENT_STARTED", "AGENT_COMPLETED", "INVESTIGATION_ROUND_COMPLETED", "JUDGE_COMPLETED"}:
                lifecycle[role] = str(kind)
        return completed, started, lifecycle

    def _restore_agent_history(
        self,
        persisted_agents: dict[str, Any],
        run_dir: Path,
        *,
        persisted_auxiliary_agents: dict[str, Any] | None = None,
    ) -> None:
        """Restore completed model accounting for visible and auxiliary roles."""
        completed, started, lifecycle = self._completed_turns_by_role(run_dir)
        buckets = (
            ("agents", persisted_agents, True),
            ("auxiliary_agents", persisted_auxiliary_agents or {}, False),
        )
        for bucket_name, persisted_bucket, apply_lifecycle in buckets:
            for role, agent in self.state.get(bucket_name, {}).items():
                prior = persisted_bucket.get(role)
                if not isinstance(prior, dict):
                    prior = {}
                for key in (
                    "thread_id", "turn_id", "turn_count", "raw_usage", "last_turn_usage",
                    "retry_count", "last_event", "started_at", "compaction_count",
                    "last_compaction", "context_tokens", "context_window",
                ):
                    if key in prior:
                        agent[key] = deepcopy(prior[key])
                agent["turn_count"] = max(int(agent.get("turn_count") or 0), started.get(role, 0))
                records = deepcopy(prior.get("cost_records") or [])
                turns = completed.get(role, [])
                turn_usages = [codex_turn_usage(turn["thread_id"], turn["turn_id"]) for turn in turns]
                unmatched = list(records)
                aligned = []
                for index, usage in enumerate(turn_usages):
                    match = next((
                        offset for offset, record in enumerate(unmatched)
                        if usage and record.get("raw_usage") == usage
                    ), None)
                    if match is not None:
                        aligned.append(unmatched.pop(match))
                    elif usage:
                        aligned.append(assess_cost(
                            provider=agent.get("provider"), model_vendor=agent.get("model_vendor"),
                            model=agent.get("model"), usage=usage, native_cost=None,
                        ))
                    elif len(records) == len(turns) and index < len(unmatched):
                        aligned.append(unmatched.pop(0))
                    else:
                        aligned.append(assess_cost(
                            provider=agent.get("provider"), model_vendor=agent.get("model_vendor"),
                            model=agent.get("model"), usage=None, native_cost=None,
                        ))
                records = aligned + unmatched
                agent["cost_records"] = records
                agent["cost"] = aggregate_cost(records)
                agent["state"] = str(prior.get("state") or "idle")
                if apply_lifecycle:
                    if lifecycle.get(role) in {"AGENT_COMPLETED", "INVESTIGATION_ROUND_COMPLETED", "JUDGE_COMPLETED"}:
                        agent["state"] = "done"
                    elif lifecycle.get(role) == "AGENT_STARTED":
                        agent["state"] = "waiting"
                    elif agent["state"] in {"running", "rework"}:
                        agent["state"] = "waiting"
                        agent["turn_id"] = None
                    if agent["state"] == "idle" and turns:
                        agent["state"] = "done"
                self._set_agent_usage(agent)

        self._recompute_run_tokens()
        self._recompute_costs()

    def _restore_project_history(self, persisted: dict[str, Any]) -> None:
        """Rebuild the prior-cycle project base from persisted aggregate projections."""
        projects = persisted.get("project_agents")
        if not isinstance(projects, dict):
            return
        for role, project in projects.items():
            if not isinstance(project, dict):
                continue
            current = self.state.get("agents", {}).get(role) or self.state.get("auxiliary_agents", {}).get(role) or {}
            current_usage = aggregate_usage(current.get("cost_records") or []) or {}
            project_elapsed = float(project.get("elapsed_s") or 0.0)
            current_elapsed = float(current.get("elapsed_s") or 0.0)
            usage = {
                "input_tokens": max(0, int(project.get("tokens_in") or 0) - int(current_usage.get("input_tokens") or 0)),
                "output_tokens": max(0, int(project.get("tokens_out") or 0) - int(current_usage.get("output_tokens") or 0)),
                "cached_input_tokens": max(0, int(project.get("cached_input_tokens") or 0) - int(current_usage.get("cached_input_tokens") or 0)),
                "reasoning_tokens": max(0, int(project.get("reasoning_tokens") or 0) - int(current_usage.get("reasoning_tokens") or 0)),
            }
            has_usage = any(project.get(key) is not None for key in ("tokens_in", "tokens_out", "tokens_total"))
            base_records = []
            if has_usage:
                base_records.append({"raw_usage": usage})
            project_cost = project.get("cost") or {}
            current_cost = current.get("cost") or {}
            if project_cost.get("amount") is not None:
                base_amount = max(0.0, float(project_cost["amount"]) - float(current_cost.get("amount") or 0.0))
                if base_amount:
                    base_records.append({"amount": base_amount, "currency": project_cost.get("currency") or "USD", "source": project_cost.get("source") or "estimated"})
            self._project_agent_totals[role] = {
                "elapsed_s": max(0.0, project_elapsed - current_elapsed),
                "cost_records": base_records,
            }
