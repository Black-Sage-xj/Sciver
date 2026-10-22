from __future__ import annotations

"""Pure in-process projection of scientific/Codex events into RunState."""

import time
from typing import Any

from theory_master.runtime import now
from theory_master.observability.costs import aggregate_cost, aggregate_usage, assess_cost
from theory_master.observability.models import (
    CAPABILITIES, DISPLAY_ROLE, SCI_STATUS_MAP, current_role_threads, investigation_details,
)


def _agent_key(role: str) -> str:
    return role


_USAGE_KEYS = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens", "reasoning_tokens")


def _usage_numeric(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {key: int(value.get(key) or 0) for key in _USAGE_KEYS}


def _usage_delta(current: Any, baseline: Any) -> dict[str, int]:
    current_values = _usage_numeric(current)
    baseline_values = _usage_numeric(baseline)
    result: dict[str, int] = {}
    for key in _USAGE_KEYS:
        now_value = current_values.get(key, 0)
        before = baseline_values.get(key, 0)
        # A provider/session reset must not create negative usage.
        result[key] = now_value - before if now_value >= before else now_value
    result["provider_specific_usage"] = {}
    return result


def _usage_subtract(current: Any, latest_request: Any) -> dict[str, int]:
    current_values = _usage_numeric(current)
    request_values = _usage_numeric(latest_request)
    result = {key: max(0, current_values.get(key, 0) - request_values.get(key, 0)) for key in _USAGE_KEYS}
    result["provider_specific_usage"] = {}
    return result


def _usage_add(baseline: Any, increment: Any) -> dict[str, int]:
    base_values = _usage_numeric(baseline)
    inc_values = _usage_numeric(increment)
    result = {key: base_values.get(key, 0) + inc_values.get(key, 0) for key in _USAGE_KEYS}
    result["provider_specific_usage"] = {}
    return result

class CollectorProjectionMixin:
    def _science_event_name(self, kind: str, payload: dict[str, Any]) -> str:
        if kind == "role.started":
            return "AGENT_STARTED"
        if kind == "role.completed":
            role = payload.get("role")
            if role in CAPABILITIES:
                return "AGENT_TURN_COMPLETED"
            if role == "judge":
                return "JUDGE_COMPLETED"
            return "AGENT_COMPLETED"
        if kind == "decision.recorded":
            return "JUDGE_VERDICT"
        if kind == "run.failed":
            return "RUN_FAILED"
        if kind == "run.cancelled":
            return "RUN_INTERRUPTED"
        return kind.replace(".", "_").upper()

    def _science_payload(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        role = data.get("role")
        if role:
            data["agent"] = _agent_key(str(role))
        return data

    def _apply_scientific(self, kind: str, payload: dict[str, Any], sci: dict[str, Any]) -> None:
        self._sync_from_research(sci)
        role = str(payload.get("role") or "")
        key = _agent_key(role) if role else None
        if kind == "role.started" and key in self.state["agents"]:
            self._start_agent_timeline(
                key,
                investigation_attempt=payload.get("investigation_attempt"),
                investigation_round=payload.get("investigation_round_count"),
                thread_id=payload.get("thread_id"),
            )
            agent = self.state["agents"][key]
            available = payload.get("skills") or []
            agent["skills_available"] = list(dict.fromkeys(str(item) for item in available))
            if self.state["judge_rework_count"] > 0 and key in CAPABILITIES:
                agent["state"] = "rework"
                agent["retry_count"] = self.state["judge_rework_count"]
            else:
                agent["state"] = "running"
            agent["thread_id"] = payload.get("thread_id") or agent["thread_id"]
            agent["started_at"] = now()
            agent["current_tool"] = None
            if role == "literature" and payload.get("literature_task_type"):
                task_type = str(payload.get("literature_task_type"))
                triggered_by = str(payload.get("literature_triggered_by") or "unknown")
                agent["activity"] = f"{task_type} · via {triggered_by}"
                agent["last_event"] = f"literature {task_type} started via {triggered_by}"
            else:
                agent["activity"] = "Codex turn"
                agent["last_event"] = f"{role} started"
            self.state["current_agent"] = key
            self.state["current_thread_id"] = agent["thread_id"]
            self.state["current_activity"] = f"{DISPLAY_ROLE.get(key, key)} running"
            if role in CAPABILITIES:
                rounds = self.state["investigation_rounds"]
                round_no = payload.get("investigation_round_count", self.state["investigation_round_count"] + 1)
                attempt_no = payload.get("investigation_attempt", self.state["investigation_attempt"])
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
                    round_row = {
                        "round": round_no,
                        "investigation_attempt": attempt_no,
                        "executor_role": key, "state": "running", "thread_id": payload.get("thread_id"),
                        "internal_turns": int(payload.get("internal_turn") or 1),
                    }
                    if role == "literature":
                        round_row.update({
                            "literature_task_type": payload.get("literature_task_type"),
                            "literature_triggered_by": payload.get("literature_triggered_by"),
                            "literature_gap_id": payload.get("literature_gap_id"),
                        })
                    rounds.append(round_row)
                if role == "literature":
                    self.state["literature_requested"] = True
            if role == "judge":
                self.log("INFO", "Judge started")
            elif role == "literature" and payload.get("literature_task_type"):
                self.log("INFO", f"Literature {payload.get('literature_task_type')} started via {payload.get('literature_triggered_by')}")
            else:
                self.log("INFO", f"{DISPLAY_ROLE.get(key, key)} started")
            rework = self.state["judge_rework_count"]
            if rework > self._last_rework:
                self._last_rework = rework
                # JUDGE_REWORK is also written via _emit from caller path below
        elif kind == "role.completed" and key in self.state["agents"]:
            agent = self.state["agents"][key]
            agent["state"] = "done"
            agent["current_tool"] = None
            agent["activity"] = None
            agent["last_event"] = f"{role} completed"
            now_mono = time.monotonic()
            self._finish_agent_timeline(key, now_mono=now_mono)
            self._freeze_role_elapsed(key, now_mono=now_mono)
            output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
            for item in reversed(self.state["investigation_rounds"]):
                if item.get("state") == "running" and (item.get("executor_role") or item.get("role")) == key:
                    item["state"] = "done"
                    break
            # Current Investigator submits to Judge via SDK; historic output
            # executor hints have no authority in the live TUI projection.
            if role == "research_lead":
                if self.state["agents"]["challenger"]["state"] == "idle":
                    self.state["agents"]["challenger"]["state"] = "waiting"
            lead_next = output.get("next_executor") or output.get("next")
            if role == "research_lead" and isinstance(output, dict) and lead_next in CAPABILITIES:
                nxt = lead_next
                if self.state["agents"][nxt]["state"] == "idle":
                    self.state["agents"][nxt]["state"] = "waiting"
            if role == "judge":
                self.state["last_verdict"] = output.get("verdict")
                plan = output.get("rework_plan") if isinstance(output.get("rework_plan"), dict) else {}
                self.state["last_target"] = plan.get("target_executor") or plan.get("target_role")
                self.state["last_missing"] = output.get("missing_evidence")
        elif kind == "decision.recorded":
            self.state["last_verdict"] = payload.get("verdict")
            self.state["status"] = "OVER"
            self.state["phase"] = "complete"
            self.state["current_activity"] = None
            self.state["current_agent"] = None
            self.state["current_tool"] = None
        elif kind == "run.failed":
            error = str(payload.get("error") or "failed")
            self.state["status"] = "FAILED"
            self.state["phase"] = "failed"
            self.state["last_event"] = error
            self.state["current_activity"] = error
            self.state["current_tool"] = None
            current = self.state.get("current_agent")
            if current in self.state["agents"]:
                self.state["agents"][current]["state"] = "failed"
                self.state["agents"][current]["current_tool"] = None
                self.state["agents"][current]["activity"] = error
            self.log("ERROR", error)
        elif kind == "run.cancelled":
            self.state["status"] = "INTERRUPTED"
            self.state["phase"] = "cancelled"
        rework = self.state["judge_rework_count"]
        if rework > self._last_rework:
            self._last_rework = rework

    def _apply_codex(self, mapped: dict[str, Any], *, role: str, thread_id: str) -> None:
        event = mapped.get("event")
        agent = self.state["agents"].get(role) or self.state["agents"].get(_agent_key(role))
        is_auxiliary = False
        if agent is None:
            agent = self.state.get("auxiliary_agents", {}).get(role) or self.state.get("auxiliary_agents", {}).get(_agent_key(role))
            is_auxiliary = agent is not None
        if agent is None:
            return
        agent["thread_id"] = thread_id or agent["thread_id"]
        if mapped.get("turn_id"):
            agent["turn_id"] = mapped["turn_id"]
            if not is_auxiliary:
                self.state["current_turn_id"] = mapped["turn_id"]
        if not is_auxiliary:
            self.state["current_agent"] = role
            self.state["current_thread_id"] = agent["thread_id"]
        if self.state.get("execution_active"):
            self.state["status"] = "RUNNING"
        label = f"{event}"
        if mapped.get("tool"):
            label = f"{event} {mapped['tool']}"
        if mapped.get("path"):
            label = f"{event} {mapped['path']}"
        if event not in {"LLM_MESSAGE", "TOKEN_USAGE"}:
            agent["last_event"] = label
            if not is_auxiliary:
                self.state["last_event"] = label
        if event == "LLM_TURN_STARTED":
            agent["turn_count"] = int(agent.get("turn_count") or 0) + 1
            agent["state"] = "rework" if agent.get("state") == "rework" else "running"
            agent["activity"] = "Agent reasoning / generating response"
            agent["current_tool"] = None
            agent["raw_usage"] = {}
            agent["turn_request_cost_records"] = []
            agent["last_turn_cumulative_usage"] = None
            if not is_auxiliary:
                self.state["current_turn_usage"] = {}
                self.state["current_turn_role"] = role
                self.state["current_tool"] = None
                self.state["current_activity"] = agent["activity"]
        elif event == "SKILL_LOADED":
            skill = str(mapped.get("skill") or "unknown")
            counts = agent.setdefault("skill_load_counts", {})
            counts[skill] = int(counts.get(skill) or 0) + 1
            used = agent.setdefault("skills_used", [])
            if skill not in used:
                used.append(skill)
            agent["skill_load_events"] = int(agent.get("skill_load_events") or 0) + 1
            agent["activity"] = f"Loaded skill {skill}"
            agent["current_tool"] = "skill"
            agent["last_event"] = f"SKILL_LOADED {skill}"
            self.state["current_tool"] = "skill"
            self.state["current_activity"] = agent["activity"]
        elif event == "LLM_RECONNECTING":
            # Codex may emit this while the same turn is still alive.  Do not
            # mark the agent/run failed; the backend will either receive the
            # eventual LLM_TURN_COMPLETED or raise a terminal exception.
            agent["state"] = "running"
            agent["retry_count"] = int(agent.get("retry_count") or 0) + 1
            agent["activity"] = "Codex reconnecting; waiting for retry"
            agent["last_event"] = mapped.get("error") or "Codex reconnecting"
            self.state["current_activity"] = agent["activity"]
            self.state["last_event"] = agent["last_event"]
        elif event == "LLM_FAILED":
            agent["state"] = "failed"
            self.state["status"] = "FAILED"
        elif event == "LLM_MESSAGE":
            agent["last_message"] = mapped.get("content")
        elif event == "COMPACTION_STARTED":
            before = mapped.get("context_before")
            if before is None:
                before = agent.get("context_tokens")
            mapped["context_before"] = before
            mapped.setdefault("context_window", agent.get("context_window"))
            mapped.setdefault("episode_id", thread_id)
            if not mapped.get("inferred_from_completed"):
                self._compaction_started_mono[role] = time.monotonic()
            agent["activity"] = "Native Codex context compaction"
            agent["last_compaction"] = {
                "status": "started", "episode_id": mapped.get("episode_id"), "context_before": before,
                "context_after": None, "context_window": mapped.get("context_window"),
                "threshold_tokens": mapped.get("compact_threshold_tokens"), "scope": mapped.get("compact_scope"),
                "started_at": now(), "duration_s": None, "usage": mapped.get("compaction_usage"),
                "cost": mapped.get("compaction_cost"), "inferred_from_completed": bool(mapped.get("inferred_from_completed")),
            }
        elif event == "COMPACTION_COMPLETED":
            previous = agent.get("last_compaction") if isinstance(agent.get("last_compaction"), dict) else {}
            started = self._compaction_started_mono.pop(role, None)
            duration = max(0.0, time.monotonic() - started) if started is not None else None
            mapped.setdefault("episode_id", thread_id)
            mapped.setdefault("context_before", previous.get("context_before") or agent.get("context_tokens"))
            mapped.setdefault("context_window", previous.get("context_window") or agent.get("context_window"))
            mapped["duration_s"] = duration
            agent["compaction_count"] = int(agent.get("compaction_count") or 0) + 1
            agent["context_tokens"] = None
            agent["last_compaction"] = {
                **previous, "status": "completed", "episode_id": mapped.get("episode_id"),
                "context_before": mapped.get("context_before"), "context_after": mapped.get("context_after"),
                "context_window": mapped.get("context_window"), "duration_s": duration,
                "usage": mapped.get("compaction_usage"), "cost": mapped.get("compaction_cost"),
                "completed_at": now(), "awaiting_context_after": mapped.get("context_after") is None,
            }
            agent["activity"] = "Codex context compacted; continuing same thread"
        elif event == "COMPACTION_FAILED":
            previous = agent.get("last_compaction") if isinstance(agent.get("last_compaction"), dict) else {}
            started = self._compaction_started_mono.pop(role, None)
            duration = max(0.0, time.monotonic() - started) if started is not None else None
            mapped["duration_s"] = duration
            mapped.setdefault("episode_id", thread_id)
            agent["last_compaction"] = {
                **previous, "status": "failed", "episode_id": mapped.get("episode_id"),
                "duration_s": duration, "error": mapped.get("error"), "failed_at": now(),
            }
            agent["activity"] = "Native Codex compaction failed"
        if mapped.get("raw_usage"):
            agent["raw_usage"] = dict(mapped["raw_usage"])
            if event == "TOKEN_USAGE":
                cumulative = dict(mapped["raw_usage"])
                baselines = self.state.setdefault("thread_usage_baselines", {})
                baseline = baselines.get(thread_id)
                if not isinstance(baseline, dict):
                    # tokenUsage.total is thread-lifetime cumulative usage.  On
                    # real Codex notifications total-last reconstructs the usage
                    # that existed before this turn (including after a process
                    # resume). Synthetic/legacy events may omit `last`; in that
                    # case treat the first cumulative observation as a fresh
                    # thread rather than discarding it.
                    last_request = mapped.get("last_request_usage")
                    if int(agent.get("turn_count") or 0) <= 1:
                        baseline = {}
                    else:
                        baseline = _usage_subtract(cumulative, last_request) if isinstance(last_request, dict) else {}
                    baselines[thread_id] = baseline
                previous = agent.get("last_turn_cumulative_usage")
                request_usage = _usage_delta(cumulative, previous if isinstance(previous, dict) else baseline)
                if any(request_usage.get(key, 0) for key in _USAGE_KEYS):
                    agent.setdefault("turn_request_cost_records", []).append(assess_cost(
                        provider=agent.get("provider"), model_vendor=agent.get("model_vendor"),
                        model=agent.get("model"), usage=request_usage, native_cost=None,
                    ))
                agent["last_turn_cumulative_usage"] = cumulative
                agent["latest_cumulative_usage"] = cumulative
                if not is_auxiliary:
                    self.state["current_turn_usage"] = _usage_delta(cumulative, baseline)
                    self.state["current_turn_role"] = role
        if mapped.get("context_window") is not None:
            agent["context_window"] = int(mapped["context_window"])
        if mapped.get("context_tokens") is not None:
            observed = int(mapped["context_tokens"])
            agent["context_tokens"] = observed
            last_compaction = agent.get("last_compaction")
            if isinstance(last_compaction, dict) and last_compaction.get("status") == "completed" and last_compaction.get("awaiting_context_after"):
                last_compaction["context_after"] = observed
                last_compaction["context_after_observed_at"] = now()
                last_compaction["awaiting_context_after"] = False
        if event == "LLM_TURN_COMPLETED":
            baselines = self.state.setdefault("thread_usage_baselines", {})
            baseline = baselines.get(thread_id) if isinstance(baselines.get(thread_id), dict) else {}
            cumulative = agent.get("latest_cumulative_usage") if isinstance(agent.get("latest_cumulative_usage"), dict) else {}
            if cumulative:
                # Account every model request that happened inside this Codex
                # turn, including requests separated by tool calls.
                completed_usage = _usage_delta(cumulative, baseline)
                baselines[thread_id] = dict(cumulative)
            else:
                completed_usage = mapped.get("raw_usage") or {}
                if not completed_usage and (mapped.get("tokens_in") is not None or mapped.get("tokens_out") is not None):
                    # Some completed-turn backends expose exact request counters
                    # without the provider-specific raw_usage wrapper.
                    completed_usage = {
                        key: int(mapped[source]) for key, source in (
                            ("input_tokens", "tokens_in"), ("output_tokens", "tokens_out"),
                        ) if mapped.get(source) is not None
                    }
                if completed_usage:
                    baselines[thread_id] = _usage_add(baseline, completed_usage)
            agent["latest_cumulative_usage"] = {}
            record = assess_cost(
                provider=agent.get("provider"), model_vendor=agent.get("model_vendor"), model=agent.get("model"),
                usage=completed_usage, native_cost=mapped.get("native_cost"),
            )
            request_records = agent.get("turn_request_cost_records") or []
            request_usage = aggregate_usage(request_records)
            request_cost = aggregate_cost(request_records)
            if (record.get("source") != "provider_reported" and request_usage
                    and all(request_usage[key] == int(completed_usage.get(key) or 0) for key in _USAGE_KEYS)
                    and request_cost.get("amount") is not None):
                # A Codex turn can contain many model requests separated by tool
                # calls. Price each request by its own context length, while
                # retaining one durable cost record per completed turn.
                bands = {item.get("pricing_band") for item in request_records if item.get("pricing_band")}
                rules = {item.get("pricing_rule_id") for item in request_records if item.get("pricing_rule_id")}
                record.update({
                    "amount": request_cost["amount"], "currency": request_cost["currency"],
                    "source": request_cost["source"], "confidence": request_cost["confidence"],
                    "pricing_band": next(iter(bands)) if len(bands) == 1 else "mixed",
                    "pricing_rule_id": next(iter(rules)) if len(rules) == 1 else "per-request-mixed",
                    "priced_request_count": len(request_records),
                })
            agent["turn_request_cost_records"] = []
            agent["last_turn_cumulative_usage"] = None
            agent.setdefault("cost_records", []).append(record)
            agent["cost"] = aggregate_cost(agent["cost_records"])
            agent["last_turn_usage"] = dict(completed_usage)
            if completed_usage and not is_auxiliary:
                self.state["current_turn_usage"] = dict(completed_usage)
                self.state["current_turn_role"] = role
            self._set_agent_usage(agent)
            self._recompute_run_tokens()
            self._recompute_costs()

    @staticmethod
    def _set_agent_usage(agent: dict[str, Any]) -> None:
        usage = aggregate_usage(agent.get("cost_records") or [])
        agent["tokens_in"] = usage["input_tokens"] if usage else None
        agent["tokens_out"] = usage["output_tokens"] if usage else None
        agent["tokens_total"] = usage["total_tokens"] if usage else None
        agent["cached_input_tokens"] = usage["cached_input_tokens"] if usage else None
        agent["uncached_input_tokens"] = usage["uncached_input_tokens"] if usage else None
        agent["reasoning_tokens"] = usage["reasoning_tokens"] if usage else None

    def _recompute_run_tokens(self) -> None:
        run_records = []
        for bucket in ("agents", "auxiliary_agents"):
            for item in self.state.get(bucket, {}).values():
                self._set_agent_usage(item)
                run_records.extend(item.get("cost_records") or [])
        usage = aggregate_usage(run_records)
        self.state["tokens_total"] = usage["total_tokens"] if usage else None
        self.state["tokens_in"] = usage["input_tokens"] if usage else None
        self.state["tokens_out"] = usage["output_tokens"] if usage else None
        self.state["cached_input_tokens"] = usage["cached_input_tokens"] if usage else None
        self.state["uncached_input_tokens"] = usage["uncached_input_tokens"] if usage else None
        self.state["reasoning_tokens"] = usage["reasoning_tokens"] if usage else None
        self.state["cycle_tokens_total"] = self.state.get("tokens_total")
        self.state["cycle_tokens_in"] = self.state.get("tokens_in")
        self.state["cycle_tokens_out"] = self.state.get("tokens_out")

    def _recompute_costs(self) -> None:
        records = [
            record
            for bucket in ("agents", "auxiliary_agents")
            for agent in self.state.get(bucket, {}).values()
            for record in agent.get("cost_records", [])
        ]
        self.state["cost"] = aggregate_cost(records)
        self.state["cycle_cost"] = self.state["cost"]

    def _accumulate_cycle(self) -> None:
        """Fold the finished cycle into project-level agent totals."""
        for bucket in ("agents", "auxiliary_agents"):
            for role, agent in self.state.get(bucket, {}).items():
                total = self._project_agent_totals.setdefault(role, {"elapsed_s": 0.0, "cost_records": []})
                total["elapsed_s"] += float(agent.get("elapsed_s") or 0.0)
                total["cost_records"].extend(agent.get("cost_records") or [])

    def _freeze_role_elapsed(self, role: str, *, now_mono: float | None = None) -> float:
        """Freeze one active role timer into its cumulative per-Cycle elapsed time."""
        agent = self.state.get("agents", {}).get(role)
        if not isinstance(agent, dict):
            return 0.0
        started = self._role_started_mono.pop(role, None)
        base = float(self._role_elapsed_base.get(role, agent.get("elapsed_s") or 0.0))
        if started is not None:
            now_mono = now_mono if now_mono is not None else time.monotonic()
            base += max(0.0, now_mono - started)
        agent["elapsed_s"] = base
        self._role_elapsed_base[role] = base
        return base

    def _start_agent_timeline(
        self,
        role: str,
        *,
        investigation_attempt: int | None = None,
        investigation_round: int | None = None,
        thread_id: str | None = None,
    ) -> None:
        now_mono = time.monotonic()
        previous = self.state.get("current_agent")
        if previous:
            self._finish_agent_timeline(previous, handoff_to=role if previous != role else None, now_mono=now_mono)
            self._freeze_role_elapsed(previous, now_mono=now_mono)
        agent = self.state.get("agents", {}).get(role) or {}
        self._role_elapsed_base[role] = float(agent.get("elapsed_s") or 0.0)
        self._role_started_mono[role] = now_mono
        timeline = self.state.setdefault("agent_timeline", [])
        if role == "investigator" and investigation_round is not None:
            existing = next((
                item for item in reversed(timeline)
                if item.get("role") == role
                and item.get("investigation_attempt") == investigation_attempt
                and item.get("investigation_round") == investigation_round
                and (not thread_id or item.get("thread_id") == thread_id)
            ), None)
            if existing is not None:
                existing["active"] = True
                existing["internal_turns"] = int(existing.get("internal_turns") or 1) + 1
                return
        timeline.append({
            "role": role, "started_at": now(), "elapsed_s": 0.0, "active": True,
            "investigation_attempt": investigation_attempt,
            "investigation_round": investigation_round,
            "thread_id": thread_id,
            "internal_turns": 1,
        })

    def _finish_agent_timeline(self, role: str, *, handoff_to: str | None = None, now_mono: float | None = None) -> None:
        now_mono = now_mono or time.monotonic()
        started = self._role_started_mono.get(role)
        for item in reversed(self.state.setdefault("agent_timeline", [])):
            if item.get("role") == role and item.get("active"):
                elapsed = max(0.0, now_mono - started) if started is not None else 0.0
                item["elapsed_s"] = float(item.get("elapsed_s") or 0.0) + elapsed
                item["active"] = False
                if handoff_to:
                    item["handoff_to"] = handoff_to
                break

    def _sync_from_research(self, sci: dict[str, Any]) -> None:
        goal_completion = sci.get("goal_completion") if isinstance(sci.get("goal_completion"), dict) else {}
        status = (
            "COMPLETE" if str(goal_completion.get("verdict") or "") == "passed"
            else SCI_STATUS_MAP.get(str(sci.get("status") or ""), self.state["status"])
        )
        if self.state["status"] not in {"FAILED", "INTERRUPTED"} or status in {"FAILED", "INTERRUPTED", "OVER", "COMPLETE", "COMPLETED"}:
            if status:
                self.state["status"] = status
        self.state["phase"] = str(sci.get("phase") or self.state["phase"])
        self.state["human_attention"] = sci.get("human_attention")
        attention = sci.get("attention")
        if isinstance(attention, dict) and attention:
            self.state["attention"] = dict(attention)
        elif str(sci.get("status") or "") == "needs_attention" and str(sci.get("qualification_status") or "") == "unqualified":
            self.state["attention"] = {
                "code": "no_qualified_candidate",
                "reason": str(sci.get("qualification_rationale") or "Qualification stopped without a qualified candidate."),
                "requested_action": "Review or revise the research preparation, then resume this Cycle.",
                "resume_behavior": "Resume restarts qualification from Research Lead with a fresh frontier before Challenger review.",
            }
        elif self.state.get("human_attention") and str(sci.get("status") or "") == "needs_attention":
            self.state["attention"] = {
                "code": "human_attention",
                "reason": str(self.state["human_attention"]),
                "requested_action": "Review the request and resume when ready.",
                "resume_behavior": "Resume continues from the persisted safe checkpoint.",
            }
        self.state["resumable"] = bool(sci.get("resumable", False))
        self.state["execution_active"] = bool(sci.get("execution_active", False))
        self.state["owner_pid"] = sci.get("owner_pid")
        self.state["last_safe_checkpoint"] = sci.get("last_safe_checkpoint")
        self.state["recovery"] = dict(sci.get("recovery") or {})
        guidance = sci.get("attention_guidance")
        if isinstance(guidance, dict):
            self.state["attention_guidance"] = dict(guidance)
        previous_attempt = self.state["investigation_attempt"]
        self.state.update(investigation_details(sci))
        if self.state["investigation_attempt"] != previous_attempt:
            for role in CAPABILITIES:
                self.state["agents"][role].update({"thread_id": None, "state": "idle",
                                                   "current_tool": None, "activity": None})
        self.state["qualification_status"] = str(sci.get("qualification_status") or self.state.get("qualification_status") or "pending")
        self.state["qualification_rationale"] = str(sci.get("qualification_rationale") or self.state.get("qualification_rationale") or "")
        lock = sci.get("hypothesis_lock")
        if isinstance(lock, dict):
            self.state["hypothesis_lock"] = dict(lock)
        self.state["hypothesis_locked"] = bool(sci.get("hypothesis_locked") or (isinstance(lock, dict) and lock.get("status") == "locked"))
        branches = sci.get("branches") or []
        if branches:
            active_id = sci.get("active_branch_id") or branches[-1].get("id")
            active = next((item for item in branches if item.get("id") == active_id), branches[-1])
            self.state["hypothesis"] = str(active.get("hypothesis") or "")
            self.state["hypothesis_title"] = str(active.get("title") or "")
            self.state["branch_id"] = str(active.get("id") or "")
            note = active.get("note_path")
            if note:
                self.state["report_path"] = str(note)
        threads = current_role_threads(sci)
        for role, thread_id in threads.items():
            key = _agent_key(str(role))
            if key in self.state["agents"] and thread_id:
                self.state["agents"][key]["thread_id"] = thread_id
        artifacts = []
        if self.state.get("report_path"):
            artifacts.append(self.state["report_path"])
        if self.state.get("branch_canvas"):
            artifacts.append(self.state["branch_canvas"])
        if self.state.get("run_dir"):
            artifacts.append(self.state["run_dir"])
        self.state["artifacts"] = artifacts
        if not self.state["execution_active"] and self.state["status"] != "RUNNING":
            current = self.state.get("current_agent")
            if current in self.state["agents"]:
                stop_mono = time.monotonic()
                self._finish_agent_timeline(current, now_mono=stop_mono)
                self._freeze_role_elapsed(current, now_mono=stop_mono)
                agent = self.state["agents"][current]
                if agent.get("state") in {"running", "rework", "waiting"}:
                    agent["state"] = "done"
                agent["current_tool"] = None
                agent["activity"] = None
            self.state["current_agent"] = None
            self.state["current_thread_id"] = None
            self.state["current_turn_id"] = None
            self.state["current_tool"] = None
            self.state["current_activity"] = None
        self._refresh_elapsed()
        if self.state["status"] == "STARTING" and self.state["phase"] not in {"idle", "starting"}:
            self.state["status"] = "RUNNING"

    def _refresh_elapsed(self) -> None:
        end_mono = self._finished_mono if self._finished_mono is not None else time.monotonic()
        total_started = self._total_started_mono or self._started_mono
        project_elapsed = self._project_elapsed_base + max(0.0, end_mono - total_started)
        cycle_elapsed = self._cycle_elapsed_base + max(0.0, end_mono - self._started_mono)
        self.state["elapsed_s"] = project_elapsed
        self.state["project_elapsed_s"] = project_elapsed
        self.state["cycle_elapsed_s"] = cycle_elapsed
        now_mono = time.monotonic()
        for role, started in self._role_started_mono.items():
            agent = self.state["agents"].get(role)
            if agent and agent.get("state") in {"running", "rework"}:
                base = float(self._role_elapsed_base.get(role, 0.0))
                agent["elapsed_s"] = base + max(0.0, now_mono - started)
        self._refresh_project_totals()

    def _refresh_project_totals(self) -> None:
        project_agents = {}
        current_agents = {
            **(self.state.get("agents") or {}),
            **(self.state.get("auxiliary_agents") or {}),
        }
        for role, base in self._project_agent_totals.items():
            current = current_agents.get(role) or {}
            records = list(base.get("cost_records") or []) + list(current.get("cost_records") or [])
            usage = aggregate_usage(records)
            project_agents[role] = {
                "elapsed_s": float(base.get("elapsed_s") or 0.0) + float(current.get("elapsed_s") or 0.0),
                "tokens_in": usage["input_tokens"] if usage else None,
                "tokens_out": usage["output_tokens"] if usage else None,
                "tokens_total": usage["total_tokens"] if usage else None,
                "cached_input_tokens": usage["cached_input_tokens"] if usage else None,
                "uncached_input_tokens": usage["uncached_input_tokens"] if usage else None,
                "reasoning_tokens": usage["reasoning_tokens"] if usage else None,
                "cost": aggregate_cost(records),
            }
        for role, current in current_agents.items():
            project_agents.setdefault(role, {
                "elapsed_s": float(current.get("elapsed_s") or 0.0),
                "tokens_in": current.get("tokens_in"),
                "tokens_out": current.get("tokens_out"),
                "tokens_total": current.get("tokens_total"),
                "cached_input_tokens": current.get("cached_input_tokens"),
                "uncached_input_tokens": current.get("uncached_input_tokens"),
                "reasoning_tokens": current.get("reasoning_tokens"),
                "cost": aggregate_cost(current.get("cost_records") or []),
            })
        self.state["project_agents"] = project_agents
        records = []
        for base in self._project_agent_totals.values():
            records.extend(base.get("cost_records") or [])
        records.extend(
            record
            for agent in current_agents.values()
            for record in agent.get("cost_records", [])
        )
        project_usage = aggregate_usage(records)
        self.state["project_tokens_in"] = project_usage["input_tokens"] if project_usage else None
        self.state["project_tokens_out"] = project_usage["output_tokens"] if project_usage else None
        self.state["project_tokens_total"] = project_usage["total_tokens"] if project_usage else None
        self.state["project_cached_input_tokens"] = project_usage["cached_input_tokens"] if project_usage else None
        self.state["project_uncached_input_tokens"] = project_usage["uncached_input_tokens"] if project_usage else None
        self.state["project_reasoning_tokens"] = project_usage["reasoning_tokens"] if project_usage else None
        self.state["project_cost"] = aggregate_cost(records)

    @staticmethod
    def _action_text(event: dict[str, Any]) -> str | None:
        kind = str(event.get("event") or "")
        role_key = _agent_key(str(event.get("agent") or event.get("role") or ""))
        role = DISPLAY_ROLE.get(role_key, role_key or "")
        if kind == "CYCLE_STARTED":
            run_id = str(event.get("run_id") or "").strip()
            return f"Cycle started · {run_id}" if run_id else "Cycle started"
        if kind == "JUDGE_REWORK_REQUESTED":
            target_key = _agent_key(str(event.get("target_agent") or ""))
            target = DISPLAY_ROLE.get(target_key, target_key or "upstream stage")
            retry = event.get("judge_retry_index")
            return f"Judge returned → {target} for rework" + (f" #{retry}" if retry else "")
        if kind == "JUDGE_VERDICT":
            verdict = event.get("verdict") or event.get("scientific_verdict") or "recorded"
            return f"Judge verdict: {verdict}"
        if kind == "RUN_FAILED":
            return f"Workflow failed: {event.get('error') or 'unknown error'}"
        if kind == "RUN_INTERRUPTED":
            return "Workflow interrupted"
        stage = str(event.get("stage") or "").strip()
        stage_names = {
            "frontier_proposal": "Frontier Proposal",
            "cycle_synthesis": "Cycle Synthesis",
            "frontier_maintenance": "Frontier Maintenance",
            "terminal_assessment": "Terminal Assessment",
            "goal_completion": "Goal Completion",
            "hypothesis_display": "Hypothesis Display",
            "selection_repair": "Selection Repair",
        }
        stage_label = stage_names.get(stage, stage.replace("_", " ").title()) if stage else ""
        role_stage = role + (" · " + stage_label if stage_label else "")
        if kind == "AGENT_STARTED" and role:
            return f"{role_stage} · started"
        if kind == "LLM_TURN_STARTED" and role:
            return f"{role} reasoning"
        if kind in {"AGENT_COMPLETED", "INVESTIGATION_ROUND_COMPLETED", "JUDGE_COMPLETED"} and role:
            output = event.get("output") if isinstance(event.get("output"), dict) else {}
            verdict = str(output.get("verdict") or "").strip().lower()
            if role_key == "judge":
                if stage == "goal_completion":
                    # Goal passed/not_passed is NOT a scientific H verdict.
                    return f"{role_stage} · Completed · {verdict or 'unrecorded'}"
                if verdict in {"needs_rework", "rework", "needs_revision", "revise"} or \
                        str(output.get("assessment_state") or "") == "rework_requested":
                    return f"{role_stage} · Rework requested" + (f" · {verdict}" if verdict else "")
                return f"{role_stage} · Completed · {verdict or 'unadjudicated'}"
            if role_key == "research_lead":
                return f"{role_stage} · done"
            if verdict in {"revise", "rejected", "reject", "needs_revision", "not_qualified", "needs_rework"}:
                return f"{role_stage} returned stage: {verdict}"
            return f"{role_stage} completed"
        if kind == "TOOL_ACTIVITY" and role:
            tool = str(event.get("tool") or "tool")
            detail = " ".join(str(event.get("detail") or "").split())
            return f"{role} · {tool}" + (f" · {detail}" if detail else "")
        if kind == "SKILL_LOADED" and role:
            skill = str(event.get("skill") or "unknown")
            load_index = event.get("load_index")
            suffix = f" · load #{load_index}" if int(load_index or 0) > 1 else ""
            return f"{role} · skill loaded · {skill}{suffix}"
        if kind == "COMPACTION_STARTED" and role:
            return f"{role} · context compaction started"
        if kind == "COMPACTION_COMPLETED" and role:
            return f"{role} · context compaction completed"
        if kind == "COMPACTION_FAILED" and role:
            return f"{role} · context compaction failed"
        if kind == "LLM_RECONNECTING" and role:
            return f"{role} · reconnecting"
        if kind == "LLM_FAILED" and role:
            return f"{role} · failed"
        return None

    @staticmethod
    def _keep_action_history(event: dict[str, Any], text: str) -> bool:
        kind = str(event.get("event") or "")
        if kind in {"CYCLE_STARTED", "JUDGE_REWORK_REQUESTED", "JUDGE_VERDICT", "RUN_FAILED", "RUN_INTERRUPTED", "SKILL_LOADED"}:
            return True
        if kind in {"AGENT_COMPLETED", "INVESTIGATION_ROUND_COMPLETED", "JUDGE_COMPLETED"}:
            return (str(event.get("agent") or event.get("role") or "") in {"judge", "research_lead"}
                    or "returned stage:" in text)
        return False
