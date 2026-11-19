"""InvocationPreparation: a single explicit phase owner for InvocationSession."""

from __future__ import annotations
import json
from pathlib import Path
from .identity import identity_files
from .invocation_artifacts import InvocationArtifactTracker
from .prompt_stages import build_continuation_prompt, build_initial_prompt
from .role_registry import CAPABILITIES
from .runtime import now
from .skills import WRITE_ROLES, role_skills, thread_cycle_prefix, write_rule
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .invocation_execution import InvocationSession


class InvocationPreparation:

    def __init__(self, session: InvocationSession):
        self.session = session

    def _prepare_thread(self):
        self.session.threads = self.session.state.setdefault("threads", {})
        self.session.thread_providers = self.session.state.get(
            "runtime_thread_providers"
        )
        if not isinstance(self.session.thread_providers, dict):
            self.session.thread_providers = {}
            self.session.state["runtime_thread_providers"] = (
                self.session.thread_providers
            )
        self.session.thread_key = self.session.owner._thread_key(
            self.session.state, self.session.actual
        )
        required = (self.session.context.get("submission_delivery") or {}).get("thread_id")
        if required and (self.session.fresh_thread or self.session.threads.get(self.session.thread_key) != required):
            from .submission_rework import _stop
            _stop(self.session.owner, self.session.state, "Public submission requires its original Investigator thread; replacement is forbidden.")
        if self.session.fresh_thread:
            old = self.session.threads.pop(self.session.thread_key, None)
            self.session.thread_providers.pop(self.session.thread_key, None)
            drop = getattr(self.session.owner.backend, "drop_thread", None)
            if callable(drop):
                drop(old)
            self.session.thread_id = None
        else:
            self.session.thread_id = self.session.threads.get(self.session.thread_key)
        self.session.owner._apply_pending_task_route(
            self.session.state, self.session.actual, self.session.thread_id
        )
        self.session.route = self.session.owner.routes[self.session.actual]
        provider_failover = self.session.state.get("runtime_provider_failover")
        frozen_routes = self.session.state.get("runtime_role_routes")
        frozen_route = (
            frozen_routes.get(self.session.actual)
            if isinstance(frozen_routes, dict)
            else None
        )
        known_thread_provider = self.session.thread_providers.get(
            self.session.thread_key
        )
        if known_thread_provider is None and isinstance(frozen_route, dict):
            known_thread_provider = frozen_route.get("provider")
        if (
            self.session.thread_id
            and isinstance(known_thread_provider, str)
            and (known_thread_provider != self.session.route.provider)
        ):
            if required:
                from .submission_rework import _stop
                _stop(self.session.owner, self.session.state, "The original Investigator provider changed; retain the thread and public submission gap without starting a new session.")
            self.session.threads.pop(self.session.thread_key, None)
            self.session.thread_providers.pop(self.session.thread_key, None)
            drop = getattr(self.session.owner.backend, "drop_thread", None)
            if callable(drop):
                drop(self.session.thread_id)
            self.session.thread_id = None
            if isinstance(provider_failover, dict):
                fresh_roles = provider_failover.setdefault("fresh_thread_roles", [])
                if (
                    isinstance(fresh_roles, list)
                    and self.session.actual not in fresh_roles
                ):
                    fresh_roles.append(self.session.actual)
        lead_new_cycle = False
        if self.session.actual == "research_lead" and (not self.session.fresh_thread):
            persisted = self.session.owner._load_research_lead_state()
            current_cycle_id = str(
                self.session.owner.cycle_dir.name
                if self.session.owner.cycle_dir
                else ""
            )
            same_cycle_thread = (
                bool(self.session.thread_id)
                and str(persisted.get("last_cycle_id") or "") == current_cycle_id
            )
            rollover_reason = (
                None
                if same_cycle_thread
                else self.session.owner._research_lead_rollover_reason(
                    self.session.state, persisted, self.session.route
                )
            )
            if rollover_reason:
                old = self.session.threads.pop(self.session.actual, None)
                drop = getattr(self.session.owner.backend, "drop_thread", None)
                if callable(drop):
                    drop(old or persisted.get("thread_id"))
                self.session.thread_id = None
                self.session.state["research_lead_rollover_reason"] = rollover_reason
                self.session.state["research_lead_context_chars"] = 0
                history = list(persisted.get("rollover_history") or [])
                history.append(
                    {
                        "cycle_id": (
                            self.session.owner.cycle_dir.name
                            if self.session.owner.cycle_dir
                            else ""
                        ),
                        "reason": rollover_reason,
                        "at": now(),
                    }
                )
                self.session.state["research_lead_rollover_history"] = history[-20:]
        self.session.continuation = bool(self.session.thread_id) and (
            not lead_new_cycle
        )

    def _prepare_skills(self):
        from .skill_evolution.runtime import (
            safely as skill_memory,
            prompt as skill_memory_prompt,
        )

        capable_threads = self.session.state.setdefault(
            "skill_memory_capable_threads", []
        )
        if (
            self.session.actual in {"investigator", "literature"}
            and self.session.thread_id is None
            and (self.session.identifier not in capable_threads)
        ):
            capable_threads.append(self.session.identifier)
        self.session.skill_binding = skill_memory(
            "prepare",
            root=self.session.owner.spec.research_root,
            config_path=self.session.owner.spec.config_path,
            cycle_dir=self.session.owner.cycle_dir,
            workspace=self.session.owner.cycle_workspace,
            role=self.session.actual,
            logical_key=self.session.logical_key,
            query=self.session.owner.spec.goal[:4000]
            + " "
            + self.session.owner.spec.contract[:2000]
            + " "
            + self.session.stage,
            allow_catalogue=self.session.identifier in capable_threads,
        )
        skill_note_path = self.session.owner._skill_memory_note_path(
            self.session.actual, self.session.invocation_id
        )
        self.session.skill_versions = self.session.state.setdefault(
            "skill_memory_catalogues", {}
        )
        self.session.skill_protocols = self.session.state.setdefault(
            "skill_memory_protocol_threads", []
        )
        self.session.skill_flags = self.session.state.setdefault(
            "skill_memory_learning_flags", {}
        )
        self.session.skill_addendum = skill_memory_prompt(
            self.session.skill_binding,
            note_path=skill_note_path,
            previous_catalogue=self.session.skill_versions.get(self.session.identifier),
            protocol_seen=self.session.identifier in self.session.skill_protocols,
            previous_learning=self.session.skill_flags.get(self.session.identifier),
        )
        if self.session.skill_binding:
            self.session.state.setdefault("skill_memory_bindings", {})[
                self.session.invocation_id
            ] = self.session.skill_binding["binding_path"]
        if self.session.owner.collector is not None:
            from .skills import SkillRef

            self.session.owner.collector.learned_skill_refs = tuple(
                (
                    SkillRef(
                        "learned-" + row["name"],
                        "learned-" + row["name"],
                        row["description"],
                        Path(row["path"]),
                        row["name"],
                        "task",
                    )
                    for row in self.session.skill_binding.get("skills", [])
                )
            )
            self.session.owner.collector.skill_evolution_invocation_id = (
                self.session.invocation_id
            )

    def _begin(self):
        self.session.predecessor_id = (
            str(self.session.state.get("active_invocation_id") or "")
            if self.session.state.get("recovery", {}).get("action") == "resume"
            else ""
        )
        predecessor = (
            self.session.invocation_ledger.latest(self.session.predecessor_id)
            if self.session.predecessor_id
            else None
        )
        if predecessor and predecessor.get("state") not in {
            "completed",
            "failed",
            "cancelled",
            "quarantined",
            "interrupted",
            "superseded",
        }:
            self.session.invocation_ledger.transition(
                self.session.predecessor_id,
                "interrupted",
                successor_invocation_id=self.session.invocation_id,
                reason="process recovery before a completed invocation",
            )
        self.session.invocation_ledger.prepare(
            invocation_id=self.session.invocation_id,
            kind="agent",
            input_payload=self.session.prompt_context,
            contract_ref=self.session.contract.descriptor(),
            predecessor_invocation_id=self.session.predecessor_id or None,
            logical_key=self.session.logical_key,
            role=self.session.role,
            stage=self.session.stage,
        )
        self.session.state["active_invocation_id"] = self.session.invocation_id
        self.session.artifact_tracker = (
            InvocationArtifactTracker.start(
                cycle_dir=self.session.owner.cycle_dir,
                workspace=self.session.owner.cycle_workspace,
                invocation_id=self.session.invocation_id,
                role=self.session.role,
                stage=self.session.stage,
            )
            if self.session.actual in WRITE_ROLES
            else None
        )
        self.session.lifecycle.tracker = self.session.artifact_tracker
        if self.session.native_preflight_goal is not None:
            self.session.prompt = (
                "[Investigator Native Goal; actual Setup and Goal logged separately]"
            )
        else:
            new_thread_prompt = build_initial_prompt(
                prefix=thread_cycle_prefix(
                    self.session.owner.cycle_dir.name, self.session.role
                ),
                role=self.session.role,
                stage=self.session.stage,
                contract_ref=self.session.contract.ref,
                contract_schema=self.session.contract.prompt_projection(
                    self.session.initial_context
                ),
                context=self.session.initial_context,
                write_rule=write_rule(self.session.actual),
            )
            self.session.prompt = (
                build_continuation_prompt(
                    role=self.session.role,
                    stage=self.session.stage,
                    contract_ref=self.session.contract.ref,
                    contract_schema=self.session.contract.prompt_projection(
                        self.session.prompt_context
                    ),
                    context=self.session.prompt_context,
                )
                if self.session.continuation
                else new_thread_prompt
            )
            self.session.prompt += self.session.skill_addendum
        if (
            self.session.native_preflight_goal is None
            and self.session.state.get("recovery", {}).get("action") == "resume"
        ):
            if (
                self.session.state.get("recovery", {}).get("qualification_restart")
                and self.session.actual == "research_lead"
                and (self.session.stage == "frontier_proposal")
            ):
                self.session.prompt += "\n\nThe previous qualification attempt ended without a qualified candidate. Use qualification_feedback in this turn's Context to address the recorded Challenger reasons. Re-enter qualification from Research Lead: reconsider the current preparation and produce a fresh frontier before Challenger review. Do not treat the previous frontier or its reviews as current qualification output."
            else:
                self.session.prompt += "\n\nThe previous TheoryMaster execution was interrupted. Resume the same assigned scientific task; do not restart completed workflow stages or change the Main Hypothesis. Inspect the existing workspace and continue from the last consistent state."
        self.session.state.setdefault("prompt_metrics", []).append(
            {
                "invocation_id": self.session.invocation_id,
                "role": self.session.role,
                "stage": self.session.stage,
                "prompt_chars": (
                    len(self.session.prompt)
                    if self.session.native_preflight_goal is None
                    else 0
                ),
                "context_chars": len(
                    json.dumps(
                        self.session.prompt_context, ensure_ascii=False, default=str
                    )
                ),
                "contract_chars": len(
                    self.session.contract.prompt_projection(self.session.prompt_context)
                ),
                "continuation": self.session.continuation,
                **(
                    {
                        "native_goal_chars": len(self.session.native_preflight_goal),
                        "native_goal_mode": (
                            "setup_context_sha256_reference"
                            if "FROZEN_OBJECTIVE_CONTEXT_SHA256="
                            in self.session.native_preflight_goal
                            else "full_immutable_science"
                        ),
                        "native_goal_safety_budget_chars": 3800,
                        "native_goal_provider_limit_chars": 4000,
                    }
                    if self.session.native_preflight_goal is not None
                    else {}
                ),
            }
        )
        self.session.state["threads"][self.session.thread_key] = self.session.identifier
        self.session.thread_providers[self.session.thread_key] = (
            self.session.route.provider
        )
        if self.session.actual in CAPABILITIES and self.session.state.get(
            "investigation_attempt"
        ):
            self.session.state["threads"][
                f"attempt-{int(self.session.state['investigation_attempt']):04d}:{self.session.actual}"
            ] = self.session.identifier
            self.session.thread_providers[
                f"attempt-{int(self.session.state['investigation_attempt']):04d}:{self.session.actual}"
            ] = self.session.route.provider
        if self.session.actual in CAPABILITIES and self.session.state.get(
            "investigation_attempt"
        ):
            self.session.owner._save_attempt(self.session.state)
        # While an invocation is active this is its current role. Completion
        # retains the same value as the last execution position for recovery.
        # Set it before role.started/persist so the no-Collector projection
        # cannot label this invocation with the previous completed role.
        self.session.state.update(
            {
                "status": "active",
                "phase": f"{self.session.role}:running",
                "active_role": self.session.role,
            }
        )
        self.session.owner._emit(
            self.session.state,
            "role.started",
            {
                "role": self.session.role,
                "invocation_id": self.session.invocation_id,
                "stage": self.session.stage,
                "prompt_chars": len(self.session.prompt),
                "contract_id": self.session.contract.contract_id,
                "contract_version": self.session.contract.version,
                "contract_schema_hash": self.session.contract.schema_hash,
                "thread_id": self.session.identifier,
                "resume_thread_id": self.session.thread_id,
                "investigation_attempt": self.session.state.get(
                    "investigation_attempt", 0
                ),
                "investigation_round_count": self.session.state.get(
                    "investigation_round_count", 0
                )
                + (
                    1
                    if self.session.actual in CAPABILITIES
                    and self.session.state.get("investigation_attempt")
                    else 0
                ),
                "total_investigation_round_count": self.session.state.get(
                    "total_investigation_round_count", 0
                )
                + (
                    1
                    if self.session.actual in CAPABILITIES
                    and self.session.state.get("investigation_attempt")
                    else 0
                ),
                "thread_namespace": self.session.thread_key,
                "identities": [path.name for path in identity_files(self.session.role)],
                "skills": [
                    ref.directory
                    for ref in role_skills(
                        self.session.actual,
                        task_config_path=self.session.owner.spec.config_path,
                    )
                ],
                "skill_scopes": {
                    ref.directory: ref.scope
                    for ref in role_skills(
                        self.session.actual,
                        task_config_path=self.session.owner.spec.config_path,
                    )
                },
                **(
                    {
                        "learned_skill_binding": self.session.skill_binding[
                            "binding_path"
                        ],
                        "learned_skill_versions": {
                            s["id"]: s["version"]
                            for s in self.session.skill_binding["skills"]
                        },
                    }
                    if self.session.skill_binding
                    else {}
                ),
                **(
                    {
                        "literature_task_type": (
                            self.session.validation_context.get("literature_task") or {}
                        ).get("task_type"),
                        "literature_triggered_by": (
                            self.session.validation_context.get("literature_task") or {}
                        ).get("triggered_by"),
                        "literature_gap_id": (
                            self.session.validation_context.get("literature_task") or {}
                        ).get("gap_id"),
                        "literature_source_gap_id": (
                            self.session.validation_context.get("literature_task") or {}
                        ).get("source_gap_id"),
                        "literature_objection_id": (
                            self.session.validation_context.get("literature_task") or {}
                        ).get("objection_id"),
                    }
                    if self.session.role == "literature"
                    and isinstance(
                        self.session.validation_context.get("literature_task"), dict
                    )
                    else {}
                ),
            },
        )
        self.session.invocation_ledger.transition(
            self.session.invocation_id,
            "running",
            role=self.session.role,
            thread_id=self.session.identifier,
            execution_mode=(
                "thread_continuation"
                if self.session.thread_id
                and self.session.identifier == self.session.thread_id
                else "new_thread"
            ),
            predecessor_invocation_id=self.session.predecessor_id or None,
        )
        self.session.owner._persist(self.session.state)
