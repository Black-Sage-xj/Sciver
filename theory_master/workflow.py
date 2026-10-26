"""TheoryMaster workflow implementation.

Public compatibility imports remain available from :mod:`theory_master.core`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import time
import tomllib
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .identity import identity_block, identity_files
from .contracts import role_contract
from .output_validation import validate_agent_output
from .output_normalization import OutputNormalizationError, normalize_agent_output
from .prompt_stages import (
    build_contract_repair_prompt,
    build_continuation_prompt,
    build_initial_prompt,
    build_json_repair_prompt,
    stage_context,
    stage_for,
)
from .cycle_runner import CycleRunner
from .provider_errors import ProviderQuotaExhausted


from .backends import (
    Backend, CanonicalFakeBackend, CodexBackend, DEFAULT_ROUTE, DEFAULT_ROUTES,
    FakeBackend, KNOWN_ROUTES, MODEL_PROFILES, MODEL_PROFILE_DIR, MODEL_PROVIDER_MAP, RoleRoute, approval_decision,
    is_history_rewriting_command, is_process_control_command,
    make_approval_decision, parse_model_spec, parse_role_spec, read_model_profile,
    read_model_provider, resolve_routes, write_model_profile, write_model_provider,
)
from .display import direction_text, is_runtime_current_state, sticky_direction
from .invocations import InvocationLedger
from .graph_authority import SQLiteGraphAuthorityMixin
from .canvas_projection import CanvasProjectionMixin
from .goal_terminal import GoalSealedError
from .reporting import ReportingMixin
from .workflow_invocation import InvocationMixin
from .workflow_qualification import NoQualifiedCandidate, QualificationMixin
from .workflow_investigation import InvestigationMixin
from .workflow_state import WorkflowStateMixin
from .workflow_science import WorkflowScienceMixin
from .workflow_goal import GoalCompletionMixin
from .specification import (
    CYCLE_DIR_RE, CYCLE_MARKER, LEGACY_CYCLE_DIR_RE, LEGACY_TIMESTAMP_CYCLE_DIR_RE,
    MASTER_RUN_DIR_RE, SCHEMA, TaskSpec, cycle_number,
    find_cycle, is_cycle_dir, latest_cycle_dir, list_cycle_dirs, make_run_id,
    master_run_id, migration_preview,
)
from .runtime import (
    CLIPPED_MARK,
    CONTEXT_MAX_DEPTH,
    CONTEXT_MAX_MAPPING,
    CONTEXT_MAX_SEQUENCE,
    CONTEXT_VALUE_MAX_CHARS,
    PROTECTED_CONTEXT_KEYS,
    atomic,
    bound_json_value,
    bound_prompt_context,
    canvas_ts,
    compact_ts,
    now,
    write_json,
)
from .read_trace import unread_selection_sources
from .skills import WRITE_ROLES, developer_instructions, role_skills, thread_cycle_prefix, write_rule
from .role_registry import CAPABILITIES
from .execution_policy import PROCESS_CONTROL_GUARD, REPO_GUARD

from .workflow_support import (
    CONTROL_LINE_DROP_IDS, FINAL_GATE_VERDICTS, REWORK_LIMIT,
    TERMINAL_SCIENTIFIC_VERDICTS, VERDICTS, RecoveryStop,
    precheckpoint_recovery_allowed, prequalification_restart_allowed,
)

class Workflow(WorkflowStateMixin, WorkflowScienceMixin, GoalCompletionMixin, InvocationMixin, QualificationMixin, InvestigationMixin, SQLiteGraphAuthorityMixin, CanvasProjectionMixin, ReportingMixin):
    def __init__(self, spec: TaskSpec, backend: Backend, routes: dict[str, RoleRoute] | None = None,
                 collector: Any | None = None, scholarly_retriever: Any | None = None,
                 semantic_backend: Any | None = None,
                 stop_before: str | None = None,
                 route_resolver: Any | None = None,
                 resume_route_overrides: dict[str, RoleRoute] | None = None,
                 explicit_cli_route_roles: set[str] | None = None):
        self.spec, self.backend = spec, backend
        if isinstance(backend, CodexBackend) and getattr(backend, "task_config_path", None) is None:
            backend.task_config_path = spec.config_path
        from .scholarly_transport import fake_scholarly_retrieval, retrieve_scholarly
        self.scholarly_retriever = scholarly_retriever or (
            fake_scholarly_retrieval if isinstance(backend, (FakeBackend, CanonicalFakeBackend)) else retrieve_scholarly
        )
        self.routes = dict(routes or DEFAULT_ROUTES)
        # A task Runner can reload operator-selected routes only between Cycles.
        # Direct/fixture Workflows remain frozen unless explicitly opted in.
        self.route_resolver = route_resolver
        self._cycle_provider_fallback: dict[str, Any] | None = None
        # Explicit CLI --route on resume is an auditable operational repair.
        # Never apply the ordinary saved profile to a frozen historical Cycle.
        self.resume_route_overrides = dict(resume_route_overrides or {})
        # CLI --route is deliberately higher priority than Web task settings,
        # including when a later invocation starts within this Cycle.
        self.explicit_cli_route_roles = frozenset(explicit_cli_route_roles or ())
        if not set(self.resume_route_overrides).issubset(self.routes):
            raise ValueError("unknown resumed Cycle role model override")
        if semantic_backend is None:
            from .abc_pipeline import AgentSemanticBackend, DeterministicSemanticBackend, FallbackSemanticBackend, JevBackend
            if isinstance(backend, (FakeBackend, CanonicalFakeBackend)):
                semantic_backend = DeterministicSemanticBackend()
            else:
                semantic_route = self.routes.get("research_lead", DEFAULT_ROUTE)
                agent_semantic = AgentSemanticBackend(
                    backend, model=semantic_route.model, provider=semantic_route.provider
                )
                if os.environ.get("THEORYMASTER_SEMANTIC_BACKEND", "jev").lower() == "jev" and os.environ.get("TYPESAFE_API_KEY"):
                    semantic_backend = FallbackSemanticBackend(JevBackend(), agent_semantic)
                else:
                    semantic_backend = agent_semantic
        self.semantic_backend = semantic_backend
        self.cycle_dir: Path | None = None
        self.notes_dir: Path | None = None
        self.cycle_workspace: Path | None = None
        self.branch_canvas: Path | None = spec.branch_canvas
        # The latest terminal Cycle state persists the cross-invocation frontier.
        # `branch_parent_id` also chains Cycles created in this Workflow instance.
        self.branch_parent_id: str | None = None
        self.invocation_cycle_index = 0
        self.events_path = spec.research_root / "events.jsonl"
        self.collector = collector
        self.stop_before = str(stop_before or "").strip().lower().replace("-", "_") or None
        if self.stop_before not in {None, "hypothesis_lock", "judge"}:
            raise ValueError(f"unsupported workflow stop checkpoint: {stop_before}")
        if collector is not None:
            backend.collector = collector  # type: ignore[attr-defined]
            set_routes = getattr(collector, "set_routes", None)
            if callable(set_routes):
                set_routes(self.routes)
















































    @staticmethod
    def _can_continue_cycle_budget(state: dict[str, Any], *, require_next_frontier: bool = False) -> bool:
        """Return whether the same invocation may create another Cycle."""
        goal_completion = state.get("goal_completion") if isinstance(state.get("goal_completion"), dict) else {}
        if str(goal_completion.get("verdict") or "") == "passed":
            return False
        # Read-only compatibility: historical Cycle artifacts may still spell
        # the already-closed Cycle as `completed`; current writes use only `over`.
        if str(state.get("status") or "").lower() not in {"over", "completed"}:
            return False
        # Operational-only closure commits no Scientific Model or Frontier
        # change. Starting another Cycle in the same invocation would therefore
        # repeat work from the same scientific state until max_cycles is spent.
        if state.get("operational_only_closure"):
            return False
        if require_next_frontier and not state.get("next_start_node_id"):
            return False
        return True

    def run(self) -> dict[str, Any]:
        """Run up to `max_cycles` incremental Cycles for this invocation.

        Existing cycle directories provide historical IDs only; they never consume
        the current invocation's Cycle budget. `make_run_id` allocates the next ID.
        """
        completed_goal = self._latest_goal_completion_state()
        if completed_goal is not None:
            return completed_goal
        from .research_focus import check_preferred_research_focus
        # Preferred is advisory: report completed focus lines but never veto
        # the Goal-driven next Cycle, including when no active tip remains.
        check_preferred_research_focus(self)
        state: dict[str, Any] | None = None
        while self.invocation_cycle_index < self.current_max_cycles():
            state = self._run_cycle()
            check_preferred_research_focus(self, state)
            if not self._can_continue_cycle_budget(state):
                break
        if state is not None:
            if self.collector is not None:
                finish_invocation = getattr(self.collector, "finish_invocation", None)
                if callable(finish_invocation):
                    finish_invocation(state)
            return state
        latest = latest_cycle_dir(self.spec.research_root, task_mode=self.spec.task_mode)
        if latest is None:
            return {}
        path = latest / "research_state.json"
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _run_cycle(
        self, resume_state: dict[str, Any] | None = None, *,
        same_cycle_continuation: bool = False,
    ) -> dict[str, Any]:
        """Run one Cycle; internal Supervisor continuation reuses its slot."""
        if same_cycle_continuation:
            return CycleRunner(self).run(
                resume_state, same_cycle_continuation=True
            )
        return CycleRunner(self).run(resume_state)

    def _claim_invocation_cycle_slot(self, *, same_cycle_continuation: bool = False) -> int:
        """Count one slot per logical Cycle, not per internal Supervisor turn."""
        if same_cycle_continuation:
            if self.invocation_cycle_index < 1:
                raise RuntimeError("same-Cycle continuation has no invocation budget slot")
        else:
            self.invocation_cycle_index += 1
        return self.invocation_cycle_index


    def _install_cycle_routes(self, refreshed: dict[str, RoleRoute], *, reset_provider_client: bool = False) -> None:
        """Atomically switch the Workflow/backend/semantic/collector route view."""
        if not isinstance(refreshed, dict) or set(refreshed) != set(self.routes):
            raise ValueError("incomplete Cycle model routes")
        if refreshed == self.routes:
            return
        provider_changed = any(self.routes[role].provider != refreshed[role].provider
                               for role in refreshed)
        if isinstance(self.backend, CodexBackend):
            if provider_changed and reset_provider_client:
                self.backend.reset_client_for_provider_change()
            self.backend.routes = dict(refreshed)
        self.routes = dict(refreshed)
        from .abc_pipeline import AgentSemanticBackend, FallbackSemanticBackend
        semantic = self.semantic_backend
        if isinstance(semantic, FallbackSemanticBackend):
            semantic = semantic.fallback
        if isinstance(semantic, AgentSemanticBackend):
            lead = refreshed["research_lead"]
            semantic.model, semantic.provider = lead.model, lead.provider
        if self.collector is not None:
            set_routes = getattr(self.collector, "set_routes", None)
            if callable(set_routes):
                set_routes(self.routes)

    @staticmethod
    def _route_snapshot(route: RoleRoute) -> dict[str, Any]:
        return {"model": route.model, "provider": route.provider,
                "model_vendor": route.model_vendor,
                "reasoning_effort": route.reasoning_effort}

    def _apply_pending_task_route(self, state: dict[str, Any], role: str,
                                  thread_id: str | None = None) -> None:
        """Accept a Web route change ONLY between Agent invocations.

        A native Goal and an ordinary provider Turn each remain immutable until
        completion. Original Cycle routing remains preserved for reproducibility;
        hot changes have a separate authoritative event and resume snapshot.
        """
        if not callable(self.route_resolver) or self.cycle_dir is None:
            return
        if role in getattr(self, "explicit_cli_route_roles", ()) or role in self.resume_route_overrides:
            return
        worker_id = getattr(self, "worker_id", None)
        if worker_id:
            # A paused Worker may resume the same Cycle with new preferences.
            # An unfinished Invocation (including a Native Goal) must retain
            # its originally bound route until it commits or terminates.
            if state.get("active_invocation_id"):
                return
            from .worker_runtime import worker_route_activations
            if worker_route_activations(self.spec.research_root, worker_id).get(role) == "next_cycle":
                return
            selected = self.route_resolver().get(role)
            source = "worker_role_preference"
        else:
            from .backend_config import read_task_role_overrides, read_task_role_activations
            selected = read_task_role_overrides(self.spec.research_root).get(role)
            if read_task_role_activations(self.spec.research_root).get(role) == "next_cycle":
                return
            source = "web_task_role_override"
        previous = self.routes[role]
        if selected is None or selected == previous:
            return
        if thread_id and selected.provider != previous.provider:
            # Reusing a Codex thread on a different Provider can corrupt its
            # auth/transport context. Do not silently create a fresh thread.
            note = {"role": role, "reason": "provider_change_requires_new_thread",
                    "requested": self._route_snapshot(selected),
                    "current": self._route_snapshot(previous)}
            if state.setdefault("runtime_hot_route_deferred", {}).get(role) != note:
                state["runtime_hot_route_deferred"][role] = note
                self._emit(state, "operator.route.deferred", note)
                self._persist(state)
            return
        updated = dict(self.routes)
        updated[role] = selected
        old_routes = dict(self.routes)
        self._install_cycle_routes(updated)
        try:
            # A Codex thread is cached after its first invocation. Reattach it
            # explicitly with the desired model + reasoning config at this safe
            # boundary, otherwise a model edit would silently leave it stale.
            if thread_id and isinstance(self.backend, CodexBackend):
                self.backend.reconfigure_cached_thread(role, thread_id, selected)
        except Exception:
            self._install_cycle_routes(old_routes)
            self._emit(state, "operator.route.failed", {"role": role,
                "requested": self._route_snapshot(selected),
                "current": self._route_snapshot(previous),
                "reason": "provider_thread_reconfiguration_failed"})
            self._persist(state)
            raise  # fail closed, never silently invoke the old model
        effective = self._route_snapshot(selected)
        state.setdefault("runtime_hot_role_routes", {})[role] = effective
        state.setdefault("runtime_hot_route_deferred", {}).pop(role, None)
        record = {"role": role, "at": now(), "source": source,
                  "old": self._route_snapshot(previous), "new": effective,
                  "effective_at": "next_agent_invocation"}
        history = state.setdefault("runtime_hot_route_history", [])
        history.append(record)
        del history[:-64]
        self._emit(state, "operator.route.applied", record)
        self._persist(state)

    def _refresh_next_cycle_routes(self) -> None:
        """Re-read task routes and apply Codex-first quota fallback at a NEW Cycle."""
        refreshed = self.route_resolver() if callable(self.route_resolver) else dict(self.routes)
        self._cycle_provider_fallback = None
        if isinstance(self.backend, CodexBackend):
            refreshed = self.backend.prepare_cycle_account(refreshed)
            self._cycle_codex_account = getattr(self.backend, "_cycle_selected_account", None)
            fallback = getattr(self.backend, "_cycle_provider_fallback", None)
            self._cycle_provider_fallback = dict(fallback) if isinstance(fallback, dict) else None
        self._install_cycle_routes(refreshed, reset_provider_client=True)

    def _restore_resumed_cycle_routes(self, state: dict[str, Any]) -> None:
        """Honor the model routing frozen when an existing Cycle began.

        Older Cycles predate runtime_role_routes and use their canonical
        read-only monitoring snapshot. Never apply newly saved models to a
        resumed old Cycle; those belong to the NEXT new Cycle.
        """
        if not callable(self.route_resolver) or self.cycle_dir is None:
            return
        source = state.get("runtime_role_routes")
        if not isinstance(source, dict):
            path = self.cycle_dir / "run_state.json"
            if path.is_file() and not path.is_symlink() and path.stat().st_size < 4_000_000:
                try:
                    monitored = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, UnicodeError):
                    monitored = {}
                source = monitored.get("agents") if isinstance(monitored, dict) else None
        if not isinstance(source, dict):
            return  # Legacy Cycle without recoverable role information.
        frozen: dict[str, RoleRoute] = {}
        for role in self.routes:
            values = source.get(role)
            if not isinstance(values, dict):
                return
            model, provider = values.get("model"), values.get("provider")
            effort = values.get("reasoning_effort")
            if not isinstance(model, str) or not model or len(model) > 100:
                return
            if not isinstance(provider, str) or not provider or len(provider) > 100:
                return
            if effort is not None and (not isinstance(effort, str) or effort not in {"low","medium","high","xhigh"}):
                return
            vendor = values.get("model_vendor")
            if vendor is not None and (not isinstance(vendor, str) or len(vendor) > 100):
                return
            frozen[role] = RoleRoute(model, provider, model_vendor=vendor,
                                    reasoning_effort=effort)
        # A legitimately hot-applied route survives same-Cycle recovery,
        # while runtime_role_routes remains the immutable Cycle-start record.
        applied = state.get("runtime_hot_role_routes")
        if isinstance(applied, dict):
            for role, data in applied.items():
                if role not in frozen or not isinstance(data, dict):
                    continue
                model, provider, effort = (data.get("model"), data.get("provider"),
                                          data.get("reasoning_effort"))
                if not isinstance(model, str) or not model or len(model) > 100:
                    continue
                if not isinstance(provider, str) or not provider or len(provider) > 100:
                    continue
                if effort not in {None, "low", "medium", "high", "xhigh"}:
                    continue
                frozen[role] = RoleRoute(model, provider,
                    model_vendor=data.get("model_vendor") if isinstance(data.get("model_vendor"), str) else None,
                    reasoning_effort=effort)
        overrides = getattr(self, "resume_route_overrides", {})
        if overrides:
            from dataclasses import replace
            changes = {}
            for role, explicit in overrides.items():
                original = frozen[role]
                # A plain ROLE=MODEL CLI override changes ONLY that model/
                # provider; keep the original role-specific reasoning effort.
                effective = (replace(explicit, reasoning_effort=original.reasoning_effort)
                             if explicit.reasoning_effort is None else explicit)
                frozen[role] = effective
                changes[role] = {
                    "source": "explicit_resume_cli",
                    "previous_model": original.model,
                    "previous_provider": original.provider,
                    "previous_reasoning_effort": original.reasoning_effort,
                    "effective_model": effective.model,
                    "effective_provider": effective.provider,
                    "effective_reasoning_effort": effective.reasoning_effort,
                }
            state.setdefault("runtime_resume_route_overrides", {}).update(changes)
        # Explicit Codex selection takes effect only at a safe paused boundary.
        provider_fallback = os.environ.get("THEORYMASTER_QUOTA_PROVIDER_FALLBACK", "").strip()
        recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else {}
        from .service_provider_switch import selected_service_preference
        manual_codex_resume = (provider_fallback == "Codex"
                               and selected_service_preference() == "codex"
                               and str(state.get("status") or "").lower() == "paused"
                               and recovery.get("reason") == "multi_worker_operator")
        if manual_codex_resume:
            from .backend_config import ACCOUNT_PROVIDERS
            requested = self.route_resolver()
            if (set(requested) != set(frozen) or any(
                    route.provider not in ACCOUNT_PROVIDERS for route in requested.values())):
                raise RuntimeError("operator Codex resume rejected: Worker Codex routes unavailable")
            hot = state.setdefault("runtime_hot_role_routes", {})
            for role, route in requested.items():
                if route != frozen[role]:
                    hot[role] = self._route_snapshot(route)
            frozen = dict(requested)
        # At a safe resume boundary, reconcile a changed account only when
        # quota recovery authorized it or this is a Worker-specific pinned
        # identity. Worker migrations must not consult the host-global selector:
        # two Workers may intentionally run different authenticated accounts.
        recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else {}
        worker_id = getattr(self, "worker_id", None)
        pinned = (os.environ.get("THEORYMASTER_WORKER_PINNED_ACCOUNT") or "").strip()
        pinned_worker_account = pinned if worker_id and pinned in {"1", "2"} else None
        quota_resume = recovery.get("code") == "provider_quota_exhausted"
        if quota_resume or pinned_worker_account:
            from .codex_accounts import selected_account
            from .account_model_routing import routes_for_account
            from .backend_config import ACCOUNT_PROVIDERS
            original_account = state.get("runtime_codex_account")
            selected = pinned_worker_account or selected_account()
            if (any(route.provider in ACCOUNT_PROVIDERS for route in frozen.values())
                    and original_account in {"1", "2"} and selected in {"1", "2"}
                    and selected != original_account):
                adjusted = routes_for_account(frozen, selected)
                if adjusted != frozen:
                    hot = state.setdefault("runtime_hot_role_routes", {})
                    for role in adjusted:
                        if adjusted[role] != frozen[role]:
                            hot[role] = self._route_snapshot(adjusted[role])
                migrations = state.get("account_migration_history")
                last_migration = migrations[-1] if isinstance(migrations, list) and migrations else {}
                worker_migration = (
                    bool(pinned_worker_account)
                    and isinstance(last_migration, dict)
                    and str(last_migration.get("from") or "") == str(original_account)
                    and str(last_migration.get("to") or "") == selected
                )
                state["runtime_account_failover"] = {
                    "from_account": original_account, "to_account": selected,
                    "reason": ("worker_account_migration" if worker_migration
                               else "worker_pinned_account_resume" if pinned_worker_account
                               else "provider_quota_exhausted"),
                    "route_downgrades": [role for role in adjusted if adjusted[role] != frozen[role]],
                }
                frozen = adjusted
        self._install_cycle_routes(frozen, reset_provider_client=True)

    def _execute_cycle(
        self, resume_state: dict[str, Any] | None = None, *,
        same_cycle_continuation: bool = False,
    ) -> dict[str, Any]:
        # No persisted state/thread for the new Cycle yet: this is the only
        # safe point to incorporate task-local model and effort changes.
        if resume_state is None:
            self._refresh_next_cycle_routes()
        self._claim_invocation_cycle_slot(same_cycle_continuation=same_cycle_continuation)
        resuming = resume_state is not None
        if resuming:
            run_id = str(resume_state.get("cycle_id") or Path(str(resume_state.get("cycle_dir"))).name)
            self._bind_cycle(self.spec.research_root / run_id, create=False)
            state = resume_state
            # Stamped only by the actual long-lived Runner executing this
            # SDK, never by a short-lived Web control client.
            state["sdk_runtime_capabilities"] = {"abandon_cycle_v1": True, "hot_role_route_v1": True}
            recovery = dict(state.get("recovery") or {})
            recovery.update({"action": "resume", "allow_thread_fallback": True, "resumed_at": now()})
            state.update({"status": "active", "resumable": True, "recovery": recovery, "cycle_dir": str(self.cycle_dir), "cycle_id": run_id,
                          "master_id": self._master_id_for_cycle(run_id), "cycle_number": cycle_number(run_id), "owner_pid": os.getpid(), "execution_active": True,
                          "human_attention": None, "attention": {}})
            prior_cycle = {}
            # Each new stop requires a fresh Supervisor incident review.
            # The prior receipt remains append-only in supervisor/session.jsonl;
            # keeping its cached dict here silently suppressed later review.
            state.pop("supervisor_attention_review", None)
            state.pop("internal_attention_review", None)
            self._restart_unqualified_qualification(state)
        else:
            if getattr(self, "worker_id", None):
                from .multi_worker import reserve_cycle_number
                run_id = reserve_cycle_number(self.spec.research_root, self.spec.goal_id)
                if getattr(self, "worker_claims", None) and getattr(self, "worker_claim_id", None):
                    self.worker_claims.bind_cycle(
                        self.worker_id, self.worker_claim_id, self.worker_claim_epoch, run_id,
                    )
            else:
                replay_id = str(getattr(self.spec, "replay_cycle_id", None) or "").strip()
                run_id = replay_id or make_run_id(
                    self.spec.goal_id, self.spec.research_root, task_mode=self.spec.task_mode,
                )
            prior_cycle = {}  # Every new Cycle starts from the Research Plan + canonical SQLite graph.
            self.branch_parent_id = None
            self._bind_cycle(self.spec.research_root / run_id, create=True)
            state = self._new_state()
            # Opt in only fresh Cycles; never reinterpret a paused historic
            # budget ledger when a newer SDK resumes it.
            state["investigator_budget_policy"] = "three_plus_three_v1"
            if getattr(self, "worker_id", None):
                state["worker_id"] = self.worker_id
                if getattr(self, "worker_claim_id", None):
                    state["worker_cycle_claim"] = {
                        "claim_id": self.worker_claim_id,
                        "epoch": self.worker_claim_epoch,
                    }
            # Cycle provenance freezes the mode/observed Worker set at creation.
            # This is a start-time observation, not a claim that those agents
            # remained concurrent throughout the scientific investigation.
            if getattr(self, "worker_id", None):
                from .worker_runtime import list_workers, scheduler_status
                peers = sorted({w["worker_id"] for w in list_workers(self.spec.research_root)
                                if w.get("process_alive") and str(w.get("effective_status")) in
                                {"RUNNING", "STARTING", "PAUSE_REQUESTED", "STOP_REQUESTED"}} |
                               {self.worker_id})
                scheduler = scheduler_status(self.spec.research_root)
                worker_record = next((w for w in list_workers(self.spec.research_root)
                                      if w["worker_id"] == self.worker_id), {})
                state["cycle_execution_context"] = {
                    "mode": "multi_worker", "worker_id": self.worker_id,
                    "concurrent_workers_at_start": len(peers),
                    "worker_ids_observed_at_start": peers,
                    "resource_slots_configured": (worker_record.get("resource_slots_configured")
                                                  or scheduler.get("resource_slots")),
                    "observed_at": now(),
                }
            else:
                state["cycle_execution_context"] = {
                    "mode": "single_worker", "worker_id": None,
                    "concurrent_workers_at_start": 1,
                    "worker_ids_observed_at_start": [], "observed_at": now(),
                }
            state["sdk_runtime_capabilities"] = {"abandon_cycle_v1": True, "hot_role_route_v1": True}
            if getattr(self, "_cycle_codex_account", None) in {"1", "2"}:
                state["runtime_codex_account"] = self._cycle_codex_account
            if callable(self.route_resolver):
                # Persist the Cycle's routing provenance for same-Cycle Resume.
                state["runtime_role_routes"] = {
                    role: {"model": route.model, "provider": route.provider,
                           "model_vendor": route.model_vendor,
                           "reasoning_effort": route.reasoning_effort}
                    for role, route in self.routes.items()
                }
            if isinstance(self._cycle_provider_fallback, dict):
                state["runtime_provider_failover"] = dict(self._cycle_provider_fallback)
            state["attempt_id"] = uuid.uuid4().hex
            # Next-Cycle scientific input is the Research Plan and the canonical
            # SQLite graph. The generated Canvas is only a visualization projection.
            from .scientific_model import load as load_scientific_model
            model_at_start = load_scientific_model(self.spec.research_root, goal_id=self.spec.goal_id)
            model_state = self._model_current_state(model_at_start, "")
            state["scientific_model_version_in"] = int(model_at_start.get("version") or 0)
            state["scientific_model_current_state"] = model_state
            if model_state:
                state["current_state"] = model_state
            write_json(self._state_path(), state)
            # Index the Cycle at creation as well as at exit. A running or
            # failed Cycle has the same H→memory provenance as an OVER Cycle.
            if getattr(self, "worker_id", None):
                from .science_cycle_memory_index import index_task_cycle_memories
                db = self._graph_database_path()
                index_task_cycle_memories(db, self.spec.research_root)
        if self.collector is not None:
            start = getattr(self.collector, "start_run", None)
            if callable(start):
                if same_cycle_continuation:
                    start(
                        run_id, self.spec, run_dir=self.cycle_dir, notes_dir=self.notes_dir,
                        branch_canvas=self.branch_canvas, cycle_workspace=self.cycle_workspace,
                        same_cycle_continuation=True,
                    )
                else:
                    start(
                        run_id, self.spec, run_dir=self.cycle_dir, notes_dir=self.notes_dir,
                        branch_canvas=self.branch_canvas, cycle_workspace=self.cycle_workspace,
                    )
        # A previous failed/cancelled run is historical evidence, not current prompt context.
        if not resuming:
            state.update({"status": "active", "phase": "starting", "investigation_attempt": 0,
                          "investigation_round_count": 0, "judge_rework_count": 0, "scientific_rework_count": 0,
                          "json_parse_repair_count": 0, "json_normalization_count": 0, "contract_repair_count": 0,
                          "minor_anomaly_count": 0, "prompt_metrics": [],
                          "total_investigation_round_count": 0, "investigation_attempts": {},
                          "active_branch_id": "", "threads": {},
                          "cycle_dir": str(self.cycle_dir), "cycle_id": run_id, "master_id": self._master_id_for_cycle(run_id),
                          "cycle_number": cycle_number(run_id),
                          "owner_pid": os.getpid(), "execution_active": True,
                          "invocation_cycle_index": self.invocation_cycle_index})
        else:
            state["invocation_cycle_index"] = self.invocation_cycle_index
        state.setdefault("role_outputs", {})
        self._persist(state)
        if not resuming:
            self._write_replay_checkpoint(state, "cycle_start")
        before = json.loads(json.dumps(state))
        events_start = state["last_seq"] + 1
        try:
            if resuming and self._lock_prequalified_checkpoint(state):
                branch, finalized, active_title, active_hypothesis = self._restore_locked_cycle(state, run_id)
                self._emit(state, "recovery.stage_resumed", {
                    "from": "pre_hypothesis_lock", "scientific_phase": state.get("scientific_phase"),
                })
                self._persist(state)
            elif resuming and state.get("hypothesis_lock", {}).get("status") == "committing":
                branch, finalized, active_title, active_hypothesis = self._restore_locked_cycle(state, run_id)
                self._emit(state, "recovery.hypothesis_commit_completed", {
                    "hypothesis_id": (state.get("hypothesis_lock") or {}).get("id"),
                })
                self._persist(state)
            elif resuming and state.get("hypothesis_lock", {}).get("status") == "locked":
                branch, finalized, active_title, active_hypothesis = self._restore_locked_cycle(state, run_id)
                self._emit(state, "recovery.stage_resumed", {
                    "from": "hypothesis_lock", "scientific_phase": state.get("scientific_phase"),
                    "investigation_attempt": state.get("investigation_attempt", 0),
                    "investigation_round_count": state.get("investigation_round_count", 0),
                })
                self._persist(state)
            else:
                try:
                    branch, finalized, active_title, active_hypothesis = self._qualify_and_lock(
                        state, run_id, prior_cycle
                    )
                except RecoveryStop:
                    # Qualification Attention is handled by one SDK-internal,
                    # read-only reviewer while this Workflow owns the Cycle.
                    # No external tmux/Codex, no unbounded retry, and no
                    # reversal of the original Challenger rejection.
                    code = str((state.get('attention') or {}).get('code') or '')
                    if code != 'no_qualified_candidate' or not self.supervisor_enabled():
                        raise
                    from .supervisor_attention_review import review_supervisor_attention
                    outcome = review_supervisor_attention(
                        backend=self.backend, cycle_dir=self.cycle_dir, state=state,
                        goal=self.spec.goal, research_contract=self.spec.contract,
                        final_gate=self.spec.final_gate,
                    )
                    state['supervisor_attention_review'] = outcome
                    state['internal_attention_review'] = outcome  # legacy projection
                    self._emit(state, 'attention.sdk_review_decided', {
                        'code': code, 'action': outcome['action'], 'mode': 'sdk_internal',
                    })
                    if outcome['action'] != 'retry_qualification':
                        self._persist(state)
                        raise
                    state['internal_attention_retry_count'] = (
                        int(state.get('internal_attention_retry_count') or 0) + 1
                    )
                    self._restart_unqualified_qualification(state)
                    from .attention_guidance import write_attention_guidance
                    # Human guidance takes priority if one already exists.
                    current_guidance = state.get('operator_guidance') if state.get('operator_guidance_pending') else None
                    if current_guidance is None:
                        advisory = write_attention_guidance(
                            self.cycle_dir, outcome['guidance'],
                            source='sdk_internal', attention_code=code,
                        )
                        state['operator_guidance'] = advisory
                        state['operator_guidance_pending'] = True
                    state.update({'status': 'active', 'execution_active': True})
                    self._emit(state, 'attention.sdk_retry_qualification', {
                        'retry_count': state['internal_attention_retry_count'],
                        'guidance_source': (state.get('operator_guidance') or {}).get('source'),
                    })
                    state.pop('supervisor_attention_review', None)
                    state.pop('internal_attention_review', None)
                    self._persist(state)
                    # The fresh proposal is always reviewed by Challenger under
                    # the unchanged qualification contract. A second rejection
                    # stops normally and cannot run another internal review.
                    branch, finalized, active_title, active_hypothesis = self._qualify_and_lock(
                        state, run_id, prior_cycle
                    )
            if not resuming:
                self._write_replay_checkpoint(state, "investigator_start")
            judge, verdict = self._investigate_and_judge(state, branch, finalized)
            if state.get("operational_only_closure"):
                # Infrastructure/no-Evidence attempts are execution history, not
                # scientific Decisions. Keep Model, Frontier and Canvas unchanged.
                self._finalize_non_scientific_cycle(
                    state, branch, active_title, active_hypothesis
                )
            else:
                # Scientific Model is changed only after a terminal Judge verdict.
                self._scientific_synthesis_commit(state, branch, judge)
                state["scientific_phase"] = "committed"
                self._maintain_frontier_state(state, judge)
                self._write_cycle_report(
                    state, branch, judge, active_title, active_hypothesis, verdict
                )
                if verdict == "supported":
                    # Goal completion is a task-level, event-triggered Gate rather
                    # than a tax on every Cycle. Render Reporter/Canvas output first
                    # so the fresh Goal Judge sees the fully closed scientific path.
                    self._persist(state, project=True)
                    self._run_goal_completion(state, judge)
                attention = state.get("attention") if isinstance(state.get("attention"), dict) else {}
                if attention.get("code") == "no_viable_scientific_frontier":
                    # The scientific transaction and human projections are now
                    # complete, but the Goal has no authorized continuation.
                    # Enter NEEDS_ATTENTION only after Reporter/Canvas so the
                    # terminal Cycle remains fully auditable.
                    state.update({
                        "status": "needs_attention",
                        "phase": "needs_attention",
                        "resumable": True,
                        "execution_active": False,
                    })
                    if self.supervisor_enabled():
                        from .supervisor_attention_review import review_supervisor_attention
                        outcome = review_supervisor_attention(
                            backend=self.backend, cycle_dir=self.cycle_dir, state=state,
                            goal=self.spec.goal, research_contract=self.spec.contract,
                            final_gate=self.spec.final_gate,
                        )
                        state['supervisor_attention_review'] = outcome
                        state['internal_attention_review'] = outcome  # legacy projection
                        self._emit(state, 'attention.sdk_review_decided', {
                            'code': attention.get('code'), 'action': outcome['action'],
                            'mode': 'sdk_internal',
                        })
                    self._emit(state, "workflow.stopped", {
                        "reason": "needs_attention",
                        "resumable": True,
                        "attention": dict(attention),
                    })
        except NoQualifiedCandidate:
            if self.collector is not None:
                self.collector.finish(state)
            return state
        except GoalSealedError:
            if str(state.get("status") or "") != "stopped_goal_completed":
                self._mark_stopped_for_goal(state)
            if self.collector is not None:
                self.collector.finish(state)
            return state
        except RecoveryStop:
            state["execution_active"] = False
            if (state.get('status') == 'needs_attention' and self.supervisor_enabled()
                    and not any(isinstance(state.get(k), dict) for k in (
                        'supervisor_attention_review', 'internal_attention_review'))):
                # All other non-budget Attention classes are also handled
                # *inside* the owned Workflow, not from Collector or tmux.
                from .supervisor_attention_review import review_supervisor_attention
                result = review_supervisor_attention(
                    backend=self.backend, cycle_dir=self.cycle_dir, state=state,
                    goal=self.spec.goal, research_contract=self.spec.contract,
                    final_gate=self.spec.final_gate,
                )
                state['supervisor_attention_review'] = result
                state['internal_attention_review'] = result  # legacy projection
                self._emit(state, 'attention.sdk_review_decided', {
                    'code': (state.get('attention') or {}).get('code'),
                    'action': result['action'], 'mode': 'sdk_internal',
                    'authority': 'supervisor', 'session_id': result.get('session_id'),
                })
                if (state.get("attention") or {}).get("code") == "investigator_native_goal_blocked":
                    # The Supervisor (not Investigator or legacy Attention
                    # Takeover) owns this scientific stop/continue decision.
                    self._emit(state, "supervisor.scientific_review_decided", {
                        "code": "investigator_native_goal_blocked",
                        "action": result["action"],
                        "session_id": result.get("session_id"),
                        "authority": "supervisor",
                    })
                    if result["action"] == "continue":
                        state["supervisor_scientific_continue_count"] = (
                            int(state.get("supervisor_scientific_continue_count") or 0) + 1
                        )
                        state["operator_guidance"] = {
                            "text": result["guidance"], "source": "supervisor_attention",
                            "attention_code": "investigator_native_goal_blocked",
                        }
                        state["operator_guidance_pending"] = True
                        self._emit(state, "investigator.supervisor_continue_authorized", {
                            "session_id": result.get("session_id"),
                            "count": state["supervisor_scientific_continue_count"],
                        })
                        self._persist(state)
                        return self._run_cycle(state, same_cycle_continuation=True)
                    if result["action"] == "preserve":
                        from .research_preservation import run_preservation_stage, finalize_preserved_cycle
                        approval = self.cycle_dir / "preservation" / "supervisor_authorization.json"
                        write_json(approval, {
                            "source": "supervisor_attention", "action": "preserve",
                            "hypothesis_id": str(state["hypothesis_lock"]["id"]),
                            "session_id": str(result.get("session_id") or ""),
                            "reason": result["reason"],
                        })
                        try:
                            route = self.routes["investigator"]
                            preservation = run_preservation_stage(
                                cycle_dir=self.cycle_dir, canvas_path=self.branch_canvas,
                                backend=self.backend, model=route.model,
                                provider=route.provider, authorization="supervisor_approved",
                            )
                            closure = finalize_preserved_cycle(
                                cycle_dir=self.cycle_dir, authorization="supervisor_approved",
                            )
                        except Exception as exc:
                            state["attention"] = {
                                "code": "investigator_preservation_failed",
                                "reason": f"Supervisor preservation failed: {type(exc).__name__}: {str(exc)[:400]}",
                            }
                            self._emit(state, "investigator.preservation_failed", {
                                "error_type": type(exc).__name__,
                            })
                        else:
                            state["preservation"] = preservation
                            state["preservation_closure"] = closure
                            state["closure_reason"] = "investigator_preservation"
                            state.update(status="over", phase="investigator:preserved",
                                         resumable=False, execution_active=False,
                                         attention={}, next_executor=None)
                            self._emit(state, "investigator.preservation_completed", {
                                "hypothesis_id": preservation["hypothesis_id"],
                                "node_id": preservation["node_id"],
                                "science_adjudicated": False,
                            })
            if state.get("status") == "needs_attention":
                self._emit(state, "workflow.stopped", {
                    "reason": "needs_attention",
                    "resumable": bool(state.get("resumable")),
                    "attention": dict(state.get("attention") or {}),
                })
            elif state.get("status") == "checkpoint_reached":
                self._emit(state, "workflow.stopped", {
                    "reason": "checkpoint_reached",
                    "checkpoint": dict(state.get("checkpoint") or {}),
                    "resumable": False,
                })
            self._persist(state)
            if self.collector is not None:
                self.collector.finish(state)
            return state
        except ProviderQuotaExhausted as exc:
            state["last_seq"] = max(int(state.get("last_seq", 0)), int(before.get("last_seq", 0)))
            state["current_state"] = sticky_direction(state) or sticky_direction(before)
            message = str(exc) or "provider quota exhausted"
            checkpoint = state.get("last_safe_checkpoint")
            role = state.get("active_role") or state.get("current_agent")
            route = self.routes.get(str(role or ""))
            provider = getattr(exc, "provider", None) or getattr(route, "provider", None)
            codex_account = getattr(exc, "account", None)
            from .backend_config import ACCOUNT_PROVIDERS
            if provider in ACCOUNT_PROVIDERS and codex_account not in {"1", "2"}:
                try:
                    from .codex_accounts import selected_account
                    codex_account = selected_account()
                except Exception:
                    codex_account = None
            state.update({
                "status": "paused",
                "phase": "provider_quota_exhausted",
                "resumable": True,
                "execution_active": False,
                "resource_disposition": "provider_quota_exhausted",
                "recovery": {
                    "action": "resume",
                    "kind": "ProviderQuotaExhausted",
                    "code": "provider_quota_exhausted",
                    "message": message,
                    "provider": provider,
                    "codex_account": codex_account,
                    "checkpoint": checkpoint,
                    "resume_when": "provider quota is available",
                    "at": now(),
                },
            })
            self._emit(state, "run.paused", {
                "reason": message,
                "error": message,
                "error_kind": "ProviderQuotaExhausted",
                "pause_kind": "provider_quota_exhausted",
                "role": role,
                "provider": provider,
                "codex_account": codex_account,
                "stage": state.get("phase"),
                "resumable": True,
                "checkpoint": checkpoint,
            })
            try:
                self._persist(state, project=True)
            except Exception:
                self._persist(state, project=False)
            if self.collector is not None:
                self.collector.finish(state)
            return state
        except (KeyboardInterrupt, SystemExit) as exc:
            state["status"] = "interrupted"
            state["phase"] = "interrupted"
            state["resumable"] = True
            state["execution_active"] = False
            state["recovery"] = {"action": "interrupt", "reason": str(exc) or "process interrupted", "at": now()}
            self._emit(state, "run.interrupted", {"reason": str(exc) or "process interrupted"})
            self._persist(state, project=True)
            if self.collector is not None:
                self.collector.finish(state)
            raise
        except Exception as exc:
            state["last_seq"] = max(int(state.get("last_seq", 0)), int(before.get("last_seq", 0)))
            state["current_state"] = sticky_direction(state) or sticky_direction(before)
            state.update({"status": "failed", "phase": "failed", "resumable": True,
                          "execution_active": False,
                          "recovery": {"action": "error", "kind": type(exc).__name__, "message": str(exc), "at": now()}})
            from .failure_classification import record_failure
            self._emit(state, "run.failed", record_failure(state, exc))
            # Failure state is authoritative even when Canvas projection itself is
            # the failing component. Never let a secondary projection exception
            # prevent research_state.json from recording the resumable failure.
            try:
                self._persist(state, project=True)
            except Exception:
                self._persist(state, project=False)
            if self.collector is not None:
                self.collector.finish(state)
            raise
        state["execution_active"] = False
        if self._goal_completion_passed(state):
            state["next_start_node_id"] = None
        try:
            self._persist(state, project=True)
            if self._goal_completion_passed(state):
                # Close every remaining active/parked SQLite frontier after the
                # final projection. Task COMPLETE has no current research frontier.
                self._close_open_frontier_tips()
                state["next_start_node_id"] = None
                frontier = state.get("frontier_state") if isinstance(state.get("frontier_state"), dict) else None
                if isinstance(frontier, dict):
                    frontier["next_start_node_id"] = None
                # Commit a fingerprint only after the final authoritative SQLite update.
                state.setdefault("goal_completion", {})["authority_digest"] = self._goal_authority_digest()
                self._persist(state, project=False)
        except Exception as exc:
            # Final projection happens after the scientific/reporting work has
            # completed. If it fails, do not leave a split-brain state where TUI
            # says OVER while the authoritative research_state is still active.
            state.update({"status": "failed", "phase": "failed", "resumable": True,
                          "execution_active": False,
                          "recovery": {"action": "error", "kind": type(exc).__name__,
                                       "message": str(exc), "at": now()}})
            from .failure_classification import record_failure
            self._emit(state, "run.failed", {**record_failure(state, exc),
                                            "stage": "final_canvas_projection"})
            self._persist(state, project=False)
            if self.collector is not None:
                self.collector.finish(state)
            raise
        run_dir = self.cycle_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        all_events = self.events_path.read_text(encoding="utf-8").splitlines(); atomic(run_dir / "event_slice.jsonl", "\n".join(all_events[events_start - 1:]) + "\n")
        cost: dict[str, Any] = {"run_id": run_id, "workflow_events": state["last_seq"] - events_start + 1,
                                "model_calls": 0, "currency": None, "total_cost": None,
                                "cost": {"amount": None, "currency": None, "source": "unavailable", "confidence": "none", "priced_model_calls": 0, "model_calls": 0},
                                "by_agent": {}, "by_provider": {}, "by_model": {}, "by_source": {},
                                "usage": {"input_tokens": None, "output_tokens": None, "cached_input_tokens": 0}}
        if self.collector is not None:
            snap = self.collector.snapshot()
            agents = snap.get("agents") or {}
            auxiliary_agents = snap.get("auxiliary_agents") or {}
            cost_roles = {**agents, **auxiliary_agents}
            all_records = [record for agent in cost_roles.values() for record in agent.get("cost_records", [])]
            by_provider: dict[str, float] = {}
            by_model: dict[str, float] = {}
            by_source: dict[str, float] = {}
            for record in all_records:
                if record.get("amount") is None:
                    continue
                amount = float(record["amount"])
                by_provider[str(record.get("provider") or "unknown")] = by_provider.get(str(record.get("provider") or "unknown"), 0.0) + amount
                by_model[str(record.get("model") or "unknown")] = by_model.get(str(record.get("model") or "unknown"), 0.0) + amount
                by_source[str(record.get("source") or "unavailable")] = by_source.get(str(record.get("source") or "unavailable"), 0.0) + amount
            cost.update({
                "model_calls": len(all_records),
                "currency": (snap.get("cost") or {}).get("currency"),
                "total_cost": (snap.get("cost") or {}).get("amount"),
                "cost": snap.get("cost"),
                "input_tokens": snap.get("tokens_in"),
                "output_tokens": snap.get("tokens_out"),
                "total_tokens": snap.get("tokens_total"),
                "by_agent": {role: agent.get("cost") for role, agent in cost_roles.items()},
                "by_provider": by_provider,
                "by_model": by_model,
                "by_source": by_source,
                "usage": {
                    "input_tokens": snap.get("tokens_in"), "output_tokens": snap.get("tokens_out"),
                    "cached_input_tokens": snap.get("cached_input_tokens"),
                },
                "usage_by_role": {
                    role: {
                        "input_tokens": agent.get("tokens_in"),
                        "output_tokens": agent.get("tokens_out"),
                        "total_tokens": agent.get("tokens_total"),
                    }
                    for role, agent in cost_roles.items()
                    if agent.get("tokens_total")
                },
            })
        write_json(run_dir / "cost_summary.json", cost)
        atomic(
            run_dir / "run_report.md",
            "# TheoryMaster run index\n\n"
            f"- run_id: `{run_id}`\n"
            f"- cycle_status: `{state.get('status', 'over')}`\n"
            f"- branch_verdict: `{branch.get('status', '')}`\n"
            f"- state: `{self._state_path()}`\n",
        )
        if self.collector is not None:
            self.collector.finish(state)
        return state

    def _restore_locked_cycle(
        self, state: dict[str, Any], run_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        """Reconstruct the locked Cycle boundary without rerunning qualification.

        A locked Hypothesis is a durable recovery boundary. Challenger review,
        Literature qualification and Lead candidate selection are historical facts
        after this point and must never be re-executed by resume.
        """
        lock = state.get("hypothesis_lock") or {}
        from .scientific_objective import locked_science_view
        try:
            locked_science_view(lock)
        except (KeyError, ValueError) as exc:
            raise RecoveryStop(str(exc)) from exc
        completing = lock.get("status") == "committing"
        if lock.get("status") not in {"locked", "committing"}:
            raise ValueError("locked-cycle recovery requires hypothesis_lock.status=locked")
        finalized = state.get("role_outputs", {}).get("research_lead_start")
        if not isinstance(finalized, dict) or not finalized.get("selected_candidate_id") or not finalized.get("decision"):
            raise ValueError("locked-cycle recovery is missing finalized Research Lead selection")
        finalized = {**finalized, "next_executor": state.get("next_executor") or finalized.get("next_executor") or "investigator"}
        active_branch_id = str(state.get("active_branch_id") or "")
        branches = state.get("branches") or []
        branch = next((item for item in branches if isinstance(item, dict) and str(item.get("id") or "") == active_branch_id), None)
        if branch is None:
            compatible_ids = {run_id}
            if run_id.isdigit():
                compatible_ids.add("cycle-" + run_id)
            elif run_id.startswith("cycle-"):
                compatible_ids.add("cycle-" + run_id)
            branch = next((item for item in branches if isinstance(item, dict) and str(item.get("id") or "") in compatible_ids), None)
        if branch is None:
            raise ValueError("locked-cycle recovery is missing the active branch")
        active_title = str(lock.get("title") or branch.get("title") or finalized.get("title") or "")
        active_hypothesis = str(lock.get("hypothesis") or branch.get("hypothesis") or finalized.get("hypothesis") or "")
        if not active_title or not active_hypothesis:
            raise ValueError("locked-cycle recovery is missing active hypothesis identity")
        # The lock is authoritative for the frozen scientific boundary; restore
        # these projections in case a crash occurred between state writes.
        branch["decisive_test"] = (lock.get("decisive_test") or lock.get("cycle_milestone")
                                   or branch.get("decisive_test") or branch.get("cycle_milestone"))
        branch["hypothesis_ref"] = str(lock.get("id") or branch.get("hypothesis_ref") or "")
        if not branch["hypothesis_ref"]:
            raise ValueError("locked Cycle is missing its canonical SQLite H identity")
        branch.pop("hypothesis_id", None)
        branch.pop("hypothesis_relation", None)
        branch["scope"] = str(lock.get("scope") if lock.get("scope") is not None else branch.get("scope") or "")
        branch["cycle_criteria"] = json.loads(json.dumps(lock.get("cycle_criteria") or branch.get("cycle_criteria") or {}))
        for legacy_field in ("success_criteria", "failure_criteria", "cycle_progress_criteria", "hypothesis_terminal_criteria"):
            branch.pop(legacy_field, None)
        state["hypothesis_locked"] = True
        selected = dict(lock.get("candidate_provenance") or {})
        # The pre_hypothesis_lock checkpoint intentionally precedes display
        # compression. On resume, run that stage before writing H to SQLite;
        # an already-registered H is served from its compliant display summary.
        self._author_hypothesis_display(state, selected)
        lock["candidate_provenance"] = json.loads(json.dumps(selected))
        state["hypothesis_lock"] = lock
        self._ensure_locked_hypothesis(state, branch, selected)
        if completing and getattr(self, "worker_id", None) and getattr(self, "worker_claims", None):
            self.worker_claims.bind_hypothesis(
                self.worker_id, self.worker_claim_id, self.worker_claim_epoch,
                str(lock.get("id") or ""),
            )
        if completing:
            state["hypothesis_lock"] = {**lock, "status": "locked", "locked_at": now()}
            state["hypothesis_locked"] = True
        self._checkpoint(state, "hypothesis-lock")
        return branch, dict(finalized), active_title, active_hypothesis





    def cancel(self, reason: str) -> dict[str, Any]:
        """Record an operator-enforced stop without fabricating a scientific decision."""
        state = self.initialize()
        state.update({"status": "cancelled", "phase": "cancelled"})
        state["execution_active"] = False
        if is_runtime_current_state(state.get("current_state")):
            state["current_state"] = sticky_direction(state)
        self._emit(state, "run.cancelled", {"reason": reason})
        self._persist(state, project=True)
        return state

    def request_control(self, action: str, *, reason: str = "operator request", role: str | None = None,
                        thread_id: str | None = None) -> dict[str, Any]:
        """Publish a control request for the active process at the next safe boundary."""
        cycle = self.cycle_dir or latest_cycle_dir(self.spec.research_root, task_mode=self.spec.task_mode)
        if cycle is None:
            raise ValueError("no Cycle to control")
        self._bind_cycle(cycle, create=False)
        state = self.initialize()
        status = str(state.get("status", "")).lower()
        source_action = action
        # Kill is a non-resumable scientific withdrawal, not an interrupt
        # checkpoint. Keep the old abandon_cycle command as a compatibility
        # alias for already registered clients and saved control records.
        command = "abandon_cycle" if action == "kill" else action
        if command not in {"pause", "interrupt", "abandon_cycle"}:
            raise ValueError("unsupported recovery control action")
        if status == "paused":
            if command != "abandon_cycle" or bool(state.get("execution_active")):
                raise ValueError("only an inactive PAUSED Cycle can be killed")
            from .runtime_manager import runtime_health
            if runtime_health(self.spec.research_root).get("runner_alive"):
                raise ValueError("PAUSED Cycle still owns a live Runner; wait for it to stop")
            if (self.cycle_dir / "recovery_control.json").exists():
                raise ValueError("a control request is already pending")
            # No Runner remains to consume recovery_control.json. Complete
            # the same canonical abandonment transition synchronously.
            payload = {"action": "abandon_cycle", "source_action": source_action,
                       "reason": reason, "requested_at": now()}
            write_json(self.cycle_dir / "recovery_control.json", payload)
            state["recovery"] = payload
            state["resumable"] = False
            state["status"] = "interrupted"
            state["phase"] = "abandon_requested"
            self._persist(state, project=False)
            try:
                self._check_recovery_control(state)
            except RecoveryStop:
                return state
            raise RuntimeError("paused Cycle abandonment did not complete")
        if status in {"over", "completed", "cancelled", "failed", "interrupted", "needs_attention"}:
            raise ValueError(f"terminal/stopped Cycle cannot receive recovery control: {status}")
        if (command == "abandon_cycle" and
                (state.get("sdk_runtime_capabilities") or {}).get("abandon_cycle_v1") is not True):
            raise ValueError("active Runner has not loaded the abandon-cycle safe checkpoint protocol")
        payload = {"action": command, "source_action": source_action,
                   "reason": reason, "requested_at": now()}
        if role:
            payload["role"] = role
        if thread_id:
            payload["thread_id"] = thread_id
        write_json(self.cycle_dir / "recovery_control.json", payload)
        state["recovery"] = payload
        state["resumable"] = command != "abandon_cycle"
        state["status"] = "paused" if command == "pause" else "interrupted"
        state["phase"] = ("abandon_requested" if command == "abandon_cycle"
                          else "control_requested")
        self._emit(state, f"run.{source_action}_requested", payload)
        self._persist(state, project=False)
        return state

    def preserve_interrupted_cycle(self, identifier: str | int, *,
                                   authorization: str, dry_run: bool = False) -> dict[str, Any]:
        """Run the authorized preservation Stage; do NOT resume frozen science.

        This is deliberately separate from resume(): a Codex blocked status is
        only a stop-review request. No Judge/Reporter call is made here.
        """
        from .research_preservation import blocked_checkpoint, run_preservation_stage, finalize_preserved_cycle
        selector = str(identifier).strip()
        if self.spec.task_mode and selector in {str(self.spec.master_id), "0"}:
            cycle = latest_cycle_dir(self.spec.research_root, task_mode=self.spec.task_mode)
        else:
            cycle = find_cycle(self.spec.research_root, identifier, task_mode=self.spec.task_mode)
        if cycle is None:
            raise ValueError("no Cycle available for preservation")
        if not (cycle / CYCLE_MARKER).is_file():
            raise ValueError("target is not a TheoryMaster Cycle")
        state = blocked_checkpoint(cycle)
        self._bind_cycle(cycle, create=False)
        route = self.routes["investigator"]
        result = run_preservation_stage(
            cycle_dir=cycle, canvas_path=Path(state["branch_canvas"]),
            backend=self.backend, model=route.model, provider=route.provider,
            authorization=authorization, dry_run=dry_run,
        )
        # A successful preservation is a genuine Cycle-over transition;
        # original stopped-state files are saved before the state change.
        # Never deliver this incomplete decisive_test to Judge/Reporter.
        if not dry_run:
            result = dict(result, closure=finalize_preserved_cycle(
                cycle_dir=cycle, authorization=authorization,
            ))
        return result


    def close_preserved_cycle(self, identifier: str | int, *,
                              authorization: str) -> dict[str, Any]:
        """Finalize an existing preservation without starting a model thread."""
        from .research_preservation import finalize_preserved_cycle
        selector = str(identifier).strip()
        if self.spec.task_mode and selector in {str(self.spec.master_id), "0"}:
            cycle = latest_cycle_dir(self.spec.research_root, task_mode=self.spec.task_mode)
        else:
            cycle = find_cycle(self.spec.research_root, identifier, task_mode=self.spec.task_mode)
        if cycle is None or not (cycle / CYCLE_MARKER).is_file():
            raise ValueError("no valid Cycle available for preservation closure")
        return finalize_preserved_cycle(cycle_dir=cycle, authorization=authorization)


    def resume(self, identifier: str | int) -> dict[str, Any]:
        """Continue an existing Cycle, then finish its remaining cycle budget.

        The resumed Cycle consumes the first slot of this invocation's
        ``max_cycles`` budget. Subsequent slots create new Cycle IDs just as
        ``run()`` does; the resumed Cycle itself is never duplicated.
        """
        task_selector = str(identifier).strip()
        if self.spec.task_mode and task_selector in {str(self.spec.master_id), "0"}:
            cycle = latest_cycle_dir(self.spec.research_root, task_mode=self.spec.task_mode)
            if cycle is None:
                return self.run()
            latest_state_path = cycle / "research_state.json"
            latest_state = json.loads(latest_state_path.read_text(encoding="utf-8")) if latest_state_path.is_file() else {}
            latest_status = str(latest_state.get("status") or "").lower()
            completed = self._latest_goal_completion_state()
            if completed is not None:
                return completed
            if latest_status == "cancelled" and latest_state.get("abandoned_cycle"):
                raise ValueError("abandoned Cycle requires an explicit new Run after human intervention")
            if latest_status in {"over", "completed", "cancelled"}:
                # A terminal Cycle is never reopened. `completed` is accepted here
                # only as the historical spelling of Cycle-over.
                return self.run()
        else:
            cycle = find_cycle(self.spec.research_root, identifier, task_mode=self.spec.task_mode)
        if not (cycle / CYCLE_MARKER).is_file():
            raise ValueError(f"not a TheoryMaster Cycle: {cycle}")
        self._bind_cycle(cycle, create=False)
        path = self._state_path()
        if not path.is_file():
            raise ValueError("Cycle has no research_state.json")
        state = json.loads(path.read_text(encoding="utf-8"))
        self._restore_resumed_cycle_routes(state)
        status = str(state.get("status") or "").lower()
        if status in {"over", "completed", "cancelled"}:
            raise ValueError(f"Cycle is not resumable: {status}")
        owner_pid = state.get("owner_pid")
        if state.get("execution_active") and owner_pid:
            try:
                os.kill(int(owner_pid), 0)
            except (OSError, ValueError):
                pass
            else:
                raise ValueError(f"Cycle already has an active execution (pid {owner_pid})")
        if str(state.get("phase") or "") == "abandon_requested":
            raise ValueError("Cycle abandonment is pending; Resume is forbidden")
        precheckpoint_restart = precheckpoint_recovery_allowed(state)
        prequalification_restart = prequalification_restart_allowed(state)
        if not state.get("resumable") and not state.get("last_safe_checkpoint") and not state.get("role_outputs") and not precheckpoint_restart and not prequalification_restart:
            raise ValueError("Cycle has no recoverable checkpoint")
        control = self.cycle_dir / "recovery_control.json"
        if control.is_file():
            try:
                pending_action = json.loads(control.read_text(encoding="utf-8")).get("action")
            except (OSError, ValueError, AttributeError):
                pending_action = None
            if pending_action == "abandon_cycle":
                raise ValueError("Cycle abandonment is pending; Resume is forbidden")
            control.unlink()
        recovery = dict(state.get("recovery") or {})
        recovery.update({"action": "resume", "resumed_at": now(), "allow_thread_fallback": True})
        from .attention_guidance import load_attention_guidance
        guidance = load_attention_guidance(cycle)
        if guidance is not None and str(guidance.get("status") or "pending") != "consumed":
            state["operator_guidance"] = guidance
            state["operator_guidance_pending"] = True
            recovery["attention_guidance"] = {
                "source": guidance.get("source"), "path": guidance.get("path"),
                "attention_code": guidance.get("attention_code"),
            }
        if precheckpoint_restart:
            recovery.update({
                "precheckpoint_restart": True,
                "precheckpoint_restart_reason": "runner ended before the first safe checkpoint; no scientific role output was committed",
            })
        if prequalification_restart:
            recovery.update({
                "prequalification_restart": True,
                "prequalification_restart_reason": "Attention explicitly authorized replay after pure runtime/code repair; no scientific output was committed",
            })
        attention = state.get("attention") if isinstance(state.get("attention"), dict) else {}
        if attention.get("code") == "investigator_actionable_no_progress_repeated_twice":
            attempt = int(state.get("investigation_attempt") or 0)
            record = (state.get("investigation_attempts") or {}).get(f"attempt-{attempt:04d}")
            if isinstance(record, dict):
                record["actionable_no_progress_streak"] = 0
                record.pop("actionable_no_progress_correction_pending", None)
                self._save_attempt(state)
            state["next_executor"] = "investigator"
            state.pop("attention", None)
        state.update({"status": "active", "phase": "resuming", "resumable": True,
                      "recovery": recovery,
                      "cycle_id": cycle.name, "master_id": self._master_id_for_cycle(cycle.name),
                      "cycle_number": cycle_number(cycle.name),
                      "owner_pid": os.getpid(), "execution_active": True})
        self._persist(state)
        state = self._run_cycle(state)
        if not self._can_continue_cycle_budget(state):
            # Resume can terminate on the old Cycle (e.g. NEEDS_ATTENTION)
            # before entering the multi-Cycle loop. Collector must still close
            # its final projection, including the terminal alert decision.
            if self.collector is not None:
                finish_invocation = getattr(self.collector, 'finish_invocation', None)
                if callable(finish_invocation):
                    finish_invocation(state)
            return state
        while self.invocation_cycle_index < self.current_max_cycles():
            # A completed Decision plus continuation context is sufficient to
            # start the next Cycle.  It must not require a pre-created future
            # Hypothesis node.
            if not self._can_continue_cycle_budget(state):
                break
            state = self._run_cycle()
        if self.collector is not None:
            finish_invocation = getattr(self.collector, "finish_invocation", None)
            if callable(finish_invocation):
                finish_invocation(state)
        return state
