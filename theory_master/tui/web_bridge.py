"""Read-only web presentation bridge sharing CLI Textual's actual renderers.

This isolated worker is invoked by the Observer through fixed argv and
server-discovered research_root. It never invokes a Workflow or task operator.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

from theory_master.observability.collector import FileSource
from theory_master.tui import render
from theory_master.tui.recent_provenance import cycle_provenance, enrich_recent_events

PRIMARY = {
    "identity": render.render_identity,
    "header": render.render_header,
    "workflow": render.render_workflow,
    "activity": render.render_activity,
    "last": render.render_last,
    "last_action": render.render_last_action,
    "investigation": render.render_investigation,
    "judge": render.render_judge,
    "tokens": render.render_tokens,
}
SECONDARY = {
    "thread": render.render_thread,
    "artifacts": render.render_artifacts,
    "research": render.render_research,
}


def _readable_action_history(actions: Any) -> list[dict[str, Any]]:
    """Hide already-persisted duplicate Judge rework markers in Web only.

    A legacy Collector reset _last_rework=0 on same-Cycle Resume. It
    re-emitted Judge rework #1 whenever the first scientific stage
    resumed, with the same timestamp as CYCLE_STARTED. Preserve the
    original request and all original event files; only the bounded
    Web timeline gets a corrected, read-only presentation.
    """
    result: list[dict[str, Any]] = []
    cycle: str | None = None
    started_at = ""
    seen: set[tuple[str, int]] = set()
    for row in actions if isinstance(actions, list) else []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("event") or "")
        stamp = str(row.get("at") or "")
        if kind == "CYCLE_STARTED":
            match = re.search(r"Cycle started\s*[·:：-]\s*(?:cycle-)?(\d+)\s*$",
                              str(row.get("text") or ""), re.I)
            if match:
                cycle = str(int(match.group(1)))
            started_at = stamp
        if kind == "JUDGE_REWORK_REQUESTED":
            retry = row.get("judge_retry_index")
            if not isinstance(retry, int):
                match = re.search(r"#(\d+)\s*$", str(row.get("text") or ""))
                retry = int(match.group(1)) if match else None
            # A real adjudication followed by rework cannot be a side
            # effect of start_run() at the EXACT Cycle-resume timestamp.
            if cycle and stamp and stamp == started_at:
                continue
            if cycle and isinstance(retry, int):
                key = cycle, retry
                if key in seen:
                    continue
                seen.add(key)
        result.append(row)
    return result


def _latest_dialogue(run_dir: Path | None) -> dict[str, str] | None:
    """Last model reply or tool call in the Cycle log, for the activity card.

    action_history omits ordinary LLM_MESSAGE and TOOL_ACTIVITY rows.
    This is a bounded tail read; it does not rewrite the log.
    """
    path = None if run_dir is None else run_dir / "runtime_events.jsonl"
    if path is None or not path.is_file() or path.is_symlink():
        return None
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 262_144))
            chunk = handle.read(262_144)
    except OSError:
        return None
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    if size > 262_144:
        lines = lines[1:]
    chosen = None
    for line in lines:
        try:
            event = json.loads(line)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = str(event.get("event") or event.get("type") or "")
        if kind not in {"LLM_MESSAGE", "TOOL_ACTIVITY"}:
            continue
        raw = event.get("content") if kind == "LLM_MESSAGE" else event.get("detail")
        detail = " ".join(str(raw or "").split())
        if kind == "TOOL_ACTIVITY":
            tool = str(event.get("tool") or "tool")
            text = f"{tool} · {detail}" if detail else tool
        else:
            text = detail or "模型回复"
        chosen = {
            "at": str(event.get("ts") or event.get("at") or "")[:64],
            "kind": kind[:96],
            "role": str(event.get("role") or event.get("agent") or "")[:48],
            "hint": text[:240],
        }
    return chosen


def project(research_root: Path, *, secondary: str = "", cycle_dir: Path | None = None,
            worker_id: str | None = None) -> dict[str, Any]:
    source = FileSource(research_root)
    if worker_id is not None:
        # Worker tabs must not display FileSource's task-global latest Cycle.
        # Read the selected Worker's frozen Cycle and its own runtime status.
        from theory_master.worker_runtime import read_worker, process_identity_alive
        if not re.fullmatch(r"worker-[1-9][0-9]*", worker_id):
            raise ValueError("invalid Worker identity")
        worker = read_worker(research_root, worker_id)
        if (worker.get("schema") != "theory-master/worker-runtime/v1"
                or worker.get("worker_id") != worker_id):
            raise ValueError("unregistered Worker")
        if cycle_dir is None or str(worker.get("current_cycle") or "") != cycle_dir.name:
            raise ValueError("Worker Cycle changed; reload the selected Worker")
        source_path = cycle_dir / "run_state.json"
        if source_path.is_symlink() or not source_path.is_file() or source_path.stat().st_size > 4_000_000:
            raise ValueError("Worker Cycle snapshot unavailable")
        state = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("invalid Worker Cycle snapshot")
        research_path = cycle_dir / "research_state.json"
        if research_path.is_file() and not research_path.is_symlink():
            scientific = json.loads(research_path.read_text(encoding="utf-8"))
            if (not isinstance(scientific, dict)
                    or scientific.get("worker_id") not in {None, worker_id}):
                raise ValueError("Worker Cycle provenance mismatch")
            # A Worker can apply a route at a safe invocation boundary before
            # the Collector refreshes run_state.agents. Prefer the SDK's
            # explicitly applied route receipt over that delayed projection.
            for field in ("runtime_role_routes", "runtime_hot_role_routes",
                          "runtime_hot_route_deferred"):
                if isinstance(scientific.get(field), dict):
                    state[field] = scientific[field]
            # The Collector does not own invocation lifetime. Its agent row
            # can lag; the SDK checkpoint supplies the invocation identity.
            state["active_invocation_id"] = scientific.get("active_invocation_id")
        # The per-Worker runtime lifecycle wins over lagging Cycle snapshots.
        state = source._overlay_live_task_config(state)
        state["status"] = str(worker.get("status") or "UNKNOWN").upper()
        state["execution_active"] = (state["status"] in
            {"RUNNING", "STARTING", "PAUSE_REQUESTED", "STOP_REQUESTED"}
            and process_identity_alive(worker.get("pid"), worker.get("pid_start_ticks")))
        state["cycle_id"] = cycle_dir.name
        # Collector's cycle_index is an invocation ordinal; the directory
        # name is a task-global Cycle ID and must never become that ordinal.
        ordinal = state.get("cycle_index")
        if type(ordinal) is not int or ordinal < 1:
            ordinal = scientific.get("invocation_cycle_index") if research_path.is_file() and not research_path.is_symlink() else None
        state["cycle_index"] = ordinal if type(ordinal) is int and ordinal >= 1 else None
    else:
        state = source.snapshot()
    # The current CLI resolver uses numeric Cycle paths. Historic files named
    # cycle-0001 are still discoverable by Observer and deserve the identical
    # text renderers rather than a fictitious STARTING/idle empty screen.
    if cycle_dir is not None and not state.get("cycle_id"):
        path = cycle_dir / "run_state.json"
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= 4_000_000:
            try:
                old_state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                old_state = None
            if isinstance(old_state, dict):
                state = dict(old_state)
                state.setdefault("cycle_id", cycle_dir.name)
    # Work on a copy. Never edit the canonical runtime_events.jsonl,
    # run_state.json, or original action_history on disk.
    state = {**state, "action_history": _readable_action_history(state.get("action_history"))}
    parts: dict[str, str] = {}
    for key, renderer in PRIMARY.items():
        try:
            # Preserve full action text in Web's scrollable timeline while
            # leaving the compact terminal renderer unchanged.
            parts[key] = render.render_last(state, full_actions=True) if key == "last" else renderer(state)
        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
            # Keep a working cockpit when a partially written snapshot lacks
            # one optional field; another valid pane remains visible.
            parts[key] = "(section temporarily unavailable)"
    if secondary in SECONDARY:
        try:
            parts["secondary"] = SECONDARY[secondary](state)
        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
            parts["secondary"] = "(section temporarily unavailable)"
    elif secondary in {"logs", "events"}:
        run_dir = cycle_dir if worker_id is not None else source.run_dir
        path = None if run_dir is None else run_dir / (
            "orchestrator.log" if secondary == "logs" else "runtime_events.jsonl"
        )
        lines: list[str] = []
        size = 0
        if path is not None and path.is_file() and not path.is_symlink():
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 262_144))
                chunk = handle.read(262_144)
            lines = chunk.decode("utf-8", errors="replace").splitlines()
            if size > 262_144:
                lines = lines[1:]
        if secondary == "logs":
            parts["secondary"] = render.render_logs(lines[-200:])
        else:
            events = []
            for line in lines:
                try:
                    event = json.loads(line)
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
                if isinstance(event, dict):
                    events.append(event)
            parts["secondary"] = ""  # Web uses structured role-colored events instead of terminal transcription.
    from theory_master.backend_config import read_task_role_overrides, read_task_role_activations, task_route_catalog
    current_agents = state.get("agents") if isinstance(state.get("agents"), dict) else {}
    historical_route_agents = state.get("project_agents") if isinstance(state.get("project_agents"), dict) else {}
    frozen_routes = state.get("runtime_role_routes") if isinstance(state.get("runtime_role_routes"), dict) else {}
    applied_routes = state.get("runtime_hot_role_routes") if isinstance(state.get("runtime_hot_role_routes"), dict) else {}
    deferred_routes = state.get("runtime_hot_route_deferred") if isinstance(state.get("runtime_hot_route_deferred"), dict) else {}
    # Only a live invocation can hold a model. A previously completed Agent,
    # idle role or paused Worker has no live model to wait for, even if its
    # immutable Cycle-start snapshot still names an older route.
    active_id = state.get("active_invocation_id")
    current_turn = state.get("current_turn_role") or state.get("current_agent")
    active_role = None
    if worker_id and state.get("execution_active"):
        if isinstance(active_id, str) and active_id:
            active_role = next((name for name in (
                "research_lead", "challenger", "investigator", "literature", "judge", "reporter")
                if re.match(r"^inv-[0-9]+-" + re.escape(name) + r"-", active_id)), None)
            if active_role is None and current_turn in (
                    "research_lead", "challenger", "investigator", "literature", "judge", "reporter"):
                active_role = current_turn
        elif "active_invocation_id" not in state and isinstance(current_turn, str) and current_turn in current_agents:
            if str(current_agents[current_turn].get("state") or "").lower() == "running":
                active_role = current_turn
    role_settings = {}
    from theory_master.worker_runtime import resolve_worker_routes, worker_route_activations
    # A Worker owns its profile and role overrides. Task-level Web overrides
    # belong to the legacy single-runner route and must never appear as a
    # phantom pending change in a Worker tab.
    worker_expected_routes = resolve_worker_routes(research_root, worker_id) if worker_id else None
    worker_activation_modes = worker_route_activations(research_root, worker_id) if worker_id else {}
    pending = {}
    activation_modes = {}
    if worker_id is None:
        try:
            pending = read_task_role_overrides(research_root)
            activation_modes = read_task_role_activations(research_root)
        except (OSError, TypeError, ValueError):
            pass
    for role in ("research_lead", "challenger", "investigator", "literature", "judge", "reporter"):
        agent = current_agents.get(role) if isinstance(current_agents.get(role), dict) else {}
        frozen = frozen_routes.get(role) if isinstance(frozen_routes.get(role), dict) else {}
        historical = (historical_route_agents.get(role)
                      if isinstance(historical_route_agents.get(role), dict) else {})
        applied = applied_routes.get(role) if isinstance(applied_routes.get(role), dict) else {}
        # A verified SDK-applied receipt wins over lagging Collector Agent data.
        # Otherwise prefer the Cycle-frozen route over historical/default guesses.
        route = next((item for item in (applied, agent, frozen, historical)
                      if item.get("model") and item.get("provider")), {})
        old = pending.get(role)
        pending_reason = ""
        if worker_expected_routes is not None:
            target = worker_expected_routes[role]
            current = (route.get("model"), route.get("provider"),
                       route.get("reasoning_effort") or frozen.get("reasoning_effort"))
            desired = (target.model, target.provider, target.reasoning_effort)
            if current != desired:
                if worker_activation_modes.get(role) == "next_cycle":
                    pending_reason = "next_cycle"
                elif role == active_role:
                    pending_reason = "active_invocation"
                elif role in deferred_routes:
                    pending_reason = "provider_deferred"
            if pending_reason:
                old = target
            else:
                # Saved Worker configuration is authoritative for an idle
                # role's NEXT invocation. Do not confuse an old scientific
                # invocation snapshot with a still-running model selection.
                route = {"model": target.model, "provider": target.provider,
                         "reasoning_effort": target.reasoning_effort}
                old = None
        role_settings[role] = {
            "model": str(route.get("model") or ""),
            "provider": str(route.get("provider") or ""),
            "reasoning_effort": str(route.get("reasoning_effort") or frozen.get("reasoning_effort") or ""),
            **({"pending_reason": pending_reason} if worker_id else {}),
            "pending": ({"model": old.model, "provider": old.provider, "reasoning_effort": old.reasoning_effort,
                         "activation": (worker_activation_modes.get(role, "next_invocation") if worker_id else
                                        activation_modes.get(role, "next_invocation"))}
                        if old is not None else None),
        }
    # A tier is an explicit configured preset, NOT something inferred from
    # similar-looking model names. Worker-local route edits stay distinguishable.
    from theory_master.backend_config import read_model_profile, build_model_profile
    from theory_master.worker_runtime import (read_worker_profile, effective_worker_profile,
                                               worker_route_overrides)
    explicit_profile = read_worker_profile(research_root, worker_id) if worker_id else None
    preset = ((effective_worker_profile(research_root, worker_id) if worker_id else None)
              or read_model_profile())
    tier_labels = {"test": "Codex 一档", "codex": "Codex 二档", "advanced": "Codex 三档"}
    overridden = worker_route_overrides(research_root, worker_id) if worker_id else {}
    expected_routes = worker_expected_routes
    preset_routes = build_model_profile(preset)
    for role, info in role_settings.items():
        label = tier_labels.get(preset, "未识别档位")
        if role in overridden and explicit_profile is None:
            label = "自定义"
        elif role in overridden and overridden[role] != preset_routes[role]:
            label += " · 兼容覆盖"
        if info.get("pending_reason") == "next_cycle":
            label += " · 下轮生效"
        elif info.get("pending_reason") in {"active_invocation", "provider_deferred"}:
            label += " · 待安全调用"
        info["preset_tier"] = label
        info["preset_profile"] = preset
    catalog = task_route_catalog()
    for option in catalog:
        option["label"] = render._MODEL_LABELS.get(option["model"], option["model"])
    # Structured accounting data is mandatory for the Web view. The
    # canonical terminal workflow has an eight-character COST column,
    # which cannot carry token counts or cost coverage provenance.
    def observed_metric(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not (0 <= value < 10**18):
            return None
        return int(value)

    def usage(record):
        data = record if isinstance(record, dict) else {}
        cost = data.get("cost") if isinstance(data.get("cost"), dict) else {}
        amount = cost.get("amount")
        priced_amount = (float(amount) if isinstance(amount, (int, float))
                         and not isinstance(amount, bool) and 0 <= amount < 1e12 else None)
        currency = cost.get("currency")
        return {
            "tokens_in": observed_metric(data.get("tokens_in")),
            "tokens_out": observed_metric(data.get("tokens_out")),
            "tokens_total": observed_metric(data.get("tokens_total")),
            "cached_input_tokens": observed_metric(data.get("cached_input_tokens")),
            "reasoning_tokens": observed_metric(data.get("reasoning_tokens")),
            "cost": {
                "amount": priced_amount if isinstance(currency, str) and currency else None,
                "currency": currency if isinstance(currency, str) and currency else None,
                "source": str(cost.get("source") or "unavailable")[:48],
                "confidence": str(cost.get("confidence") or "")[:48],
                "priced_model_calls": observed_metric(cost.get("priced_model_calls")),
                "model_calls": observed_metric(cost.get("model_calls")),
            },
        }
    current_agents = state.get("agents") if isinstance(state.get("agents"), dict) else {}
    historical_agents = state.get("project_agents") if isinstance(state.get("project_agents"), dict) else {}
    role_usage = {}
    for role in ("research_lead", "challenger", "investigator", "literature", "judge", "reporter"):
        role_usage[role] = {
            "cycle": usage(current_agents.get(role)),
            "project": usage(historical_agents.get(role) if role in historical_agents else current_agents.get(role)),
        }
    totals = {
        "cycle": usage({**{field:state.get(field) for field in ("tokens_in","tokens_out","tokens_total",
                                                            "cached_input_tokens","reasoning_tokens")},
                        "cost":state.get("cycle_cost")}),
        "project": usage({**{"tokens_in":state.get("project_tokens_in"),
                            "tokens_out":state.get("project_tokens_out"),
                            "tokens_total":state.get("project_tokens_total"),
                            "cached_input_tokens":state.get("project_cached_input_tokens"),
                            "reasoning_tokens":state.get("project_reasoning_tokens")},
                          "cost":state.get("project_cost")}),
    }
    status = str(state.get("status") or "").upper()
    attention = None
    if status == "NEEDS_ATTENTION":
        reason, requested_action, resume_behavior = render._attention_fields(state)
        takeover = state.get("attention_takeover_handoff") or {}
        attention = {"reason": reason[:12000], "requested_action": requested_action[:6000],
                     "resume_behavior": resume_behavior[:6000],
                     "takeover": str(takeover.get("status") or ("enabled" if state.get("attention_takeover_enabled") else "disabled"))}
    lock = state.get("hypothesis_lock") if isinstance(state.get("hypothesis_lock"), dict) else {}
    qualification = lock.get("qualification") if isinstance(lock.get("qualification"), dict) else {}
    review = qualification.get("review") if isinstance(qualification.get("review"), dict) else {}
    # A frozen scientific object is authoritative for both Run summary and
    # Research detail. Present it without reinterpreting its decisive_test.
    frozen_hypothesis = {
        "id": str(lock.get("id") or "")[:128],
        "hypothesis": str(lock.get("hypothesis") or "")[:12000],
        "scope": str(lock.get("scope") or "")[:12000],
        "decisive_test": str(lock.get("decisive_test") or "")[:20000],
        "locked": lock.get("status") == "locked",
        "qualification_status": str(state.get("qualification_status") or "pending")[:80],
        "selection_rationale": str(qualification.get("selection_rationale") or
                                    state.get("qualification_rationale") or "")[:8000],
        "challenger_reason": str(review.get("reason") or "")[:10000],
    }
    # Same two bounded canonical sources as render_last(full_actions=True).
    # Carry full event identity independently of its clipped HH:MM:SS label.
    recent_events = []
    for action in (state.get("action_history") or [])[-100:]:
        if isinstance(action, dict):
            recent_events.append({
                "at": str(action.get("at") or "")[:64],
                "kind": str(action.get("event") or "")[:96],
                "role": str(action.get("role") or "")[:48],
                "hint": str(action.get("text") or "")[:240],
            })
    for row in (state.get("agent_timeline") or [])[-100:]:
        if isinstance(row, dict):
            recent_events.append({
                "at": str(row.get("started_at") or "")[:64],
                "kind": "AGENT_STARTED",
                "role": str(row.get("role") or "")[:48],
                "hint": "",
            })
    recent_events = [entry for _,entry in sorted(
        enumerate(recent_events),key=lambda value:(value[1]["at"],value[0]))]
    # Resolve verified Cycle provenance from original per-Cycle event files;
    # the long-lived TUI snapshot alone does not attach Cycle IDs to old rows.
    recent_events = enrich_recent_events(recent_events, cycle_provenance(research_root, state.get("cycle_id")))
    budget = state.get("investigator_budget") if isinstance(state.get("investigator_budget"), dict) else {}
    # Quota controller is a read-only status projection. Account IDs are not counts.
    quota_state = (state.get("quota_auto_adjust")
                   if isinstance(state.get("quota_auto_adjust"), dict) else {})
    def valid_account(value):
        return str(value) if str(value) in {"1", "2"} else None
    def quota_percentage(value):
        if type(value) in (int, float) and 0 <= value <= 100:
            return round(float(value), 1)
        return None
    def quota_reset(value):
        return value if type(value) is int and 0 < value < 5_000_000_000 else None
    quota_accounts = {}
    for account_id in ("1", "2"):
        records = quota_state.get("accounts")
        record = records.get(account_id) if isinstance(records, dict) else None
        record = record if isinstance(record, dict) else {}
        windows = {}
        for window in ("primary", "secondary"):
            source_window = record.get(window)
            source_window = source_window if isinstance(source_window, dict) else {}
            windows[window] = {
                "remaining_percent": quota_percentage(source_window.get("remaining_percent")),
                "resets_at": quota_reset(source_window.get("resets_at")),
            }
        quota_accounts[account_id] = {
            **windows,
            "ordinary_usage_allowed": (record.get("ordinary_usage_allowed")
                                       if type(record.get("ordinary_usage_allowed")) is bool else None),
        }
    polling = quota_state.get("next_check_in_s")
    quota_projection = {
        "enabled": bool(state.get("quota_auto_adjust_enabled")),
        "status": str(quota_state.get("status") or "")[:40],
        "selected_account": valid_account(quota_state.get("selected_account")),
        "failed_account": valid_account(quota_state.get("failed_account")),
        "next_check_in_s": polling if type(polling) is int and 1 <= polling <= 86400 else None,
        "updated_at": str(quota_state.get("updated_at") or "")[:60],
        "accounts": quota_accounts,
    }
    dialogue = _latest_dialogue(cycle_dir if worker_id is not None else source.run_dir)
    response = {"quota_auto_adjust": quota_projection, "investigator_budget": {
                    "reviews": budget.get("reviews") if type(budget.get("reviews")) is int and 0 <= budget.get("reviews") <= 1000000 else None,
                    "allowance_tokens": budget.get("allowance_tokens"),
                },
                "recent_events": recent_events, "frozen_hypothesis": frozen_hypothesis,
                "parts": parts, "cycle_id": state.get("cycle_id"), "status": state.get("status"),
                "max_cycles": state.get("max_cycles"), "role_settings": role_settings,
                "model_catalog": catalog, "attention": attention,
                "role_usage": role_usage, "usage_totals": totals,
                "activity_detail": {
                    "current_activity": str(state.get("current_activity") or "")[:12000],
                    "last_event": str(state.get("last_event") or "")[:12000],
                    **{key: value for key, value in {
                        "latest_dialogue_at": (dialogue or {}).get("at", ""),
                        "latest_dialogue_kind": (dialogue or {}).get("kind", ""),
                        "latest_dialogue_role": (dialogue or {}).get("role", ""),
                        "latest_dialogue_hint": (dialogue or {}).get("hint", ""),
                    }.items() if value},
                }}
    if secondary == "events":
        categories = {"AGENT_STARTED", "AGENT_COMPLETED", "AGENT_INPUT", "LLM_MESSAGE",
                      "LLM_FAILED", "LLM_TURN_FAILED", "LLM_RECONNECTING", "WORKFLOW_STOPPED",
                      "TOOL_ACTIVITY", "SKILL_LOADED", "JUDGE_VERDICT", "JUDGE_COMPLETED",
                      "INVESTIGATION_ROUND_COMPLETED", "CYCLE_STARTED", "RUN_STARTED",
                      "ATTENTION_SDK_REVIEW_DECIDED", "COMPACTION_FAILED", "COMPACTION_COMPLETED"}
        visible_events = [event for event in events if str(event.get("event") or event.get("type") or "") in categories]
        response["events_capped"] = len(visible_events) > 100
        response["events"] = [{
            "role": str((event.get("role") or event.get("agent") or "system")).lower(),
            "kind": str(event.get("event") or event.get("type") or "UNKNOWN")[:70],
            "time": str(event.get("ts") or event.get("at") or "")[:45],
            "stage": str(event.get("stage") or "")[:100],
            "content": str(event.get("content") or event.get("detail") or event.get("output") or event.get("reason") or "")[:12000],
        } for event in visible_events][-100:]
        response["events_partial"] = size > 262_144
    return response


def main() -> int:
    try:
        request = json.loads(sys.stdin.read(4096))
        if not isinstance(request, dict) or not isinstance(request.get("research_root"), str):
            return 2
        research_root = Path(request["research_root"]).resolve(strict=True)
        if not research_root.is_dir():
            return 2
        secondary = str(request.get("secondary") or "")
        old_cycle = request.get("cycle_dir")
        cycle_dir = None
        if isinstance(old_cycle, str):
            candidate = Path(old_cycle).resolve(strict=True)
            if candidate.is_dir() and candidate.parent == research_root:
                cycle_dir = candidate
        worker_id = request.get("worker_id")
        if worker_id is not None and not isinstance(worker_id, str):
            return 2
        result = project(research_root, secondary=secondary, cycle_dir=cycle_dir,
                         worker_id=worker_id)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except (OSError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
