"""CycleStartup: explicit Cycle lifecycle phase."""

from __future__ import annotations
import json
import os
import uuid
from pathlib import Path
from .runtime import now, write_json
from .specification import cycle_number, make_run_id
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cycle_runner import CycleRunner


class CycleStartup:

    def __init__(self, cycle: CycleRunner):
        self.cycle = cycle

    def _initialize(self):
        if self.cycle.resume_state is None:
            self.cycle.owner._refresh_next_cycle_routes()
        self.cycle.owner._claim_invocation_cycle_slot(
            same_cycle_continuation=self.cycle.same_cycle_continuation
        )
        self.cycle.resuming = self.cycle.resume_state is not None
        if self.cycle.resuming:
            self.cycle.run_id = str(
                self.cycle.resume_state.get("cycle_id")
                or Path(str(self.cycle.resume_state.get("cycle_dir"))).name
            )
            self.cycle.owner._bind_cycle(
                self.cycle.owner.spec.research_root / self.cycle.run_id, create=False
            )
            self.cycle.state = self.cycle.resume_state
            self.cycle.state["sdk_runtime_capabilities"] = {
                "abandon_cycle_v1": True,
                "hot_role_route_v1": True,
            }
            recovery = dict(self.cycle.state.get("recovery") or {})
            recovery.update(
                {"action": "resume", "allow_thread_fallback": True, "resumed_at": now()}
            )
            self.cycle.interrupted_attention = self.cycle.state.get("attention") or {}
            if (
                self.cycle.interrupted_attention.get("code")
                == "lead_literature_unavailable"
            ):
                self.cycle.state["interrupted_lead_literature"] = json.loads(
                    json.dumps(self.cycle.interrupted_attention)
                )
            self.cycle.state.update(
                {
                    "status": "active",
                    "resumable": True,
                    "recovery": recovery,
                    "cycle_dir": str(self.cycle.owner.cycle_dir),
                    "cycle_id": self.cycle.run_id,
                    "master_id": self.cycle.owner._master_id_for_cycle(
                        self.cycle.run_id
                    ),
                    "cycle_number": cycle_number(self.cycle.run_id),
                    "owner_pid": os.getpid(),
                    "execution_active": True,
                    "human_attention": None,
                    "attention": {},
                }
            )
            self.cycle.prior_cycle = {}
            self.cycle.state.pop("supervisor_attention_review", None)
            self.cycle.state.pop("internal_attention_review", None)
            self.cycle.owner._restart_unqualified_qualification(self.cycle.state)
        else:
            if getattr(self.cycle.owner, "worker_id", None):
                from .multi_worker import reserve_cycle_number

                self.cycle.run_id = reserve_cycle_number(
                    self.cycle.owner.spec.research_root, self.cycle.owner.spec.goal_id
                )
                if getattr(self.cycle.owner, "worker_claims", None) and getattr(
                    self.cycle.owner, "worker_claim_id", None
                ):
                    self.cycle.owner.worker_claims.bind_cycle(
                        self.cycle.owner.worker_id,
                        self.cycle.owner.worker_claim_id,
                        self.cycle.owner.worker_claim_epoch,
                        self.cycle.run_id,
                    )
            else:
                replay_id = str(
                    getattr(self.cycle.owner.spec, "replay_cycle_id", None) or ""
                ).strip()
                self.cycle.run_id = replay_id or make_run_id(
                    self.cycle.owner.spec.goal_id,
                    self.cycle.owner.spec.research_root,
                    task_mode=self.cycle.owner.spec.task_mode,
                )
            self.cycle.prior_cycle = {}
            self.cycle.owner.branch_parent_id = None
            self.cycle.owner._bind_cycle(
                self.cycle.owner.spec.research_root / self.cycle.run_id, create=True
            )
            self.cycle.state = self.cycle.owner._new_state()
            self.cycle.state["investigator_budget_policy"] = "three_plus_three_v1"
            if getattr(self.cycle.owner, "worker_id", None):
                self.cycle.state["worker_id"] = self.cycle.owner.worker_id
                if getattr(self.cycle.owner, "worker_claim_id", None):
                    self.cycle.state["worker_cycle_claim"] = {
                        "claim_id": self.cycle.owner.worker_claim_id,
                        "epoch": self.cycle.owner.worker_claim_epoch,
                    }
            if getattr(self.cycle.owner, "worker_id", None):
                from .worker_runtime import list_workers, scheduler_status

                peers = sorted(
                    {
                        w["worker_id"]
                        for w in list_workers(self.cycle.owner.spec.research_root)
                        if w.get("process_alive")
                        and str(w.get("effective_status"))
                        in {"RUNNING", "STARTING", "PAUSE_REQUESTED", "STOP_REQUESTED"}
                    }
                    | {self.cycle.owner.worker_id}
                )
                scheduler = scheduler_status(self.cycle.owner.spec.research_root)
                worker_record = next(
                    (
                        w
                        for w in list_workers(self.cycle.owner.spec.research_root)
                        if w["worker_id"] == self.cycle.owner.worker_id
                    ),
                    {},
                )
                self.cycle.state["cycle_execution_context"] = {
                    "mode": "multi_worker",
                    "worker_id": self.cycle.owner.worker_id,
                    "concurrent_workers_at_start": len(peers),
                    "worker_ids_observed_at_start": peers,
                    "resource_slots_configured": worker_record.get(
                        "resource_slots_configured"
                    )
                    or scheduler.get("resource_slots"),
                    "observed_at": now(),
                }
            else:
                self.cycle.state["cycle_execution_context"] = {
                    "mode": "single_worker",
                    "worker_id": None,
                    "concurrent_workers_at_start": 1,
                    "worker_ids_observed_at_start": [],
                    "observed_at": now(),
                }
            self.cycle.state["sdk_runtime_capabilities"] = {
                "abandon_cycle_v1": True,
                "hot_role_route_v1": True,
            }
            if getattr(self.cycle.owner, "_cycle_codex_account", None) in {"1", "2"}:
                self.cycle.state["runtime_codex_account"] = (
                    self.cycle.owner._cycle_codex_account
                )
            if callable(self.cycle.owner.route_resolver):
                self.cycle.state["runtime_role_routes"] = {
                    role: {
                        "model": route.model,
                        "provider": route.provider,
                        "model_vendor": route.model_vendor,
                        "reasoning_effort": route.reasoning_effort,
                    }
                    for role, route in self.cycle.owner.routes.items()
                }
            if isinstance(self.cycle.owner._cycle_provider_fallback, dict):
                self.cycle.state["runtime_provider_failover"] = dict(
                    self.cycle.owner._cycle_provider_fallback
                )
            self.cycle.state["attempt_id"] = uuid.uuid4().hex
            from .scientific_model import load as load_scientific_model

            model_at_start = load_scientific_model(
                self.cycle.owner.spec.research_root,
                goal_id=self.cycle.owner.spec.goal_id,
            )
            model_state = self.cycle.owner._model_current_state(model_at_start, "")
            self.cycle.state["scientific_model_version_in"] = int(
                model_at_start.get("version") or 0
            )
            self.cycle.state["scientific_model_current_state"] = model_state
            if model_state:
                self.cycle.state["current_state"] = model_state
            write_json(self.cycle.owner._state_path(), self.cycle.state)
            if getattr(self.cycle.owner, "worker_id", None):
                from .science_cycle_memory_index import index_task_cycle_memories

                db = self.cycle.owner._graph_database_path()
                index_task_cycle_memories(db, self.cycle.owner.spec.research_root)
        if self.cycle.owner.collector is not None:
            start = getattr(self.cycle.owner.collector, "start_run", None)
            if callable(start):
                if self.cycle.same_cycle_continuation:
                    start(
                        self.cycle.run_id,
                        self.cycle.owner.spec,
                        run_dir=self.cycle.owner.cycle_dir,
                        notes_dir=self.cycle.owner.notes_dir,
                        branch_canvas=self.cycle.owner.branch_canvas,
                        cycle_workspace=self.cycle.owner.cycle_workspace,
                        same_cycle_continuation=True,
                    )
                else:
                    start(
                        self.cycle.run_id,
                        self.cycle.owner.spec,
                        run_dir=self.cycle.owner.cycle_dir,
                        notes_dir=self.cycle.owner.notes_dir,
                        branch_canvas=self.cycle.owner.branch_canvas,
                        cycle_workspace=self.cycle.owner.cycle_workspace,
                    )
        if not self.cycle.resuming:
            self.cycle.state.update(
                {
                    "status": "active",
                    "phase": "starting",
                    "investigation_attempt": 0,
                    "investigation_round_count": 0,
                    "judge_rework_count": 0,
                    "scientific_rework_count": 0,
                    "json_parse_repair_count": 0,
                    "json_normalization_count": 0,
                    "contract_repair_count": 0,
                    "minor_anomaly_count": 0,
                    "prompt_metrics": [],
                    "total_investigation_round_count": 0,
                    "investigation_attempts": {},
                    "active_branch_id": "",
                    "threads": {},
                    "cycle_dir": str(self.cycle.owner.cycle_dir),
                    "cycle_id": self.cycle.run_id,
                    "master_id": self.cycle.owner._master_id_for_cycle(
                        self.cycle.run_id
                    ),
                    "cycle_number": cycle_number(self.cycle.run_id),
                    "owner_pid": os.getpid(),
                    "execution_active": True,
                    "invocation_cycle_index": self.cycle.owner.invocation_cycle_index,
                }
            )
        else:
            self.cycle.state["invocation_cycle_index"] = (
                self.cycle.owner.invocation_cycle_index
            )
        self.cycle.state.setdefault("role_outputs", {})
        self.cycle.owner._persist(self.cycle.state)
        if not self.cycle.resuming:
            self.cycle.owner._write_replay_checkpoint(self.cycle.state, "cycle_start")
        self.cycle.before = json.loads(json.dumps(self.cycle.state))
        self.cycle.events_start = self.cycle.state["last_seq"] + 1
