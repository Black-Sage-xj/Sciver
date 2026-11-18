"""CycleExit: explicit Cycle lifecycle phase."""

from __future__ import annotations
from typing import Any
from .display import sticky_direction
from .runtime import now, atomic, write_json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cycle_runner import CycleRunner


class CycleExit:

    def __init__(self, cycle: CycleRunner):
        self.cycle = cycle

    def handle_noqualifiedcandidate(self):
        if self.cycle.owner.collector is not None:
            self.cycle.owner.collector.finish(self.cycle.state)
        return self.cycle.state

    def handle_goalsealederror(self):
        if str(self.cycle.state.get("status") or "") != "stopped_goal_completed":
            self.cycle.owner._mark_stopped_for_goal(self.cycle.state)
        if self.cycle.owner.collector is not None:
            self.cycle.owner.collector.finish(self.cycle.state)
        return self.cycle.state

    def handle_recoverystop(self):
        self.cycle.state["execution_active"] = False
        if (
            self.cycle.state.get("status") == "needs_attention"
            and self.cycle.owner.supervisor_enabled()
            and (
                not any(
                    (
                        isinstance(self.cycle.state.get(k), dict)
                        for k in (
                            "supervisor_attention_review",
                            "internal_attention_review",
                        )
                    )
                )
            )
        ):
            from .supervisor_attention_review import review_supervisor_attention

            result = review_supervisor_attention(
                backend=self.cycle.owner.backend,
                cycle_dir=self.cycle.owner.cycle_dir,
                state=self.cycle.state,
                goal=self.cycle.owner.spec.goal,
                research_contract=self.cycle.owner.spec.contract,
                final_gate=self.cycle.owner.spec.final_gate,
            )
            self.cycle.state["supervisor_attention_review"] = result
            self.cycle.state["internal_attention_review"] = result
            self.cycle.owner._emit(
                self.cycle.state,
                "attention.sdk_review_decided",
                {
                    "code": (self.cycle.state.get("attention") or {}).get("code"),
                    "action": result["action"],
                    "mode": "sdk_internal",
                    "authority": "supervisor",
                    "session_id": result.get("session_id"),
                },
            )
            if (self.cycle.state.get("attention") or {}).get(
                "code"
            ) == "investigator_native_goal_blocked":
                self.cycle.owner._emit(
                    self.cycle.state,
                    "supervisor.scientific_review_decided",
                    {
                        "code": "investigator_native_goal_blocked",
                        "action": result["action"],
                        "session_id": result.get("session_id"),
                        "authority": "supervisor",
                    },
                )
                if result["action"] == "continue":
                    self.cycle.state["supervisor_scientific_continue_count"] = (
                        int(
                            self.cycle.state.get("supervisor_scientific_continue_count")
                            or 0
                        )
                        + 1
                    )
                    self.cycle.state["operator_guidance"] = {
                        "text": result["guidance"],
                        "source": "supervisor_attention",
                        "attention_code": "investigator_native_goal_blocked",
                    }
                    self.cycle.state["operator_guidance_pending"] = True
                    self.cycle.owner._emit(
                        self.cycle.state,
                        "investigator.supervisor_continue_authorized",
                        {
                            "session_id": result.get("session_id"),
                            "count": self.cycle.state[
                                "supervisor_scientific_continue_count"
                            ],
                        },
                    )
                    self.cycle.owner._persist(self.cycle.state)
                    return self.cycle.owner._run_cycle(
                        self.cycle.state, same_cycle_continuation=True
                    )
                if result["action"] == "preserve":
                    from .research_preservation import (
                        run_preservation_stage,
                        finalize_preserved_cycle,
                    )

                    approval = (
                        self.cycle.owner.cycle_dir
                        / "preservation"
                        / "supervisor_authorization.json"
                    )
                    write_json(
                        approval,
                        {
                            "source": "supervisor_attention",
                            "action": "preserve",
                            "hypothesis_id": str(
                                self.cycle.state["hypothesis_lock"]["id"]
                            ),
                            "session_id": str(result.get("session_id") or ""),
                            "reason": result["reason"],
                        },
                    )
                    try:
                        route = self.cycle.owner.routes["investigator"]
                        preservation = run_preservation_stage(
                            cycle_dir=self.cycle.owner.cycle_dir,
                            canvas_path=self.cycle.owner.branch_canvas,
                            backend=self.cycle.owner.backend,
                            model=route.model,
                            provider=route.provider,
                            authorization="supervisor_approved",
                        )
                        closure = finalize_preserved_cycle(
                            cycle_dir=self.cycle.owner.cycle_dir,
                            authorization="supervisor_approved",
                        )
                    except Exception as exc:
                        self.cycle.state["attention"] = {
                            "code": "investigator_preservation_failed",
                            "reason": f"Supervisor preservation failed: {type(exc).__name__}: {str(exc)[:400]}",
                        }
                        self.cycle.owner._emit(
                            self.cycle.state,
                            "investigator.preservation_failed",
                            {"error_type": type(exc).__name__},
                        )
                    else:
                        self.cycle.state["preservation"] = preservation
                        self.cycle.state["preservation_closure"] = closure
                        self.cycle.state["closure_reason"] = "investigator_preservation"
                        self.cycle.state.update(
                            status="over",
                            phase="investigator:preserved",
                            resumable=False,
                            execution_active=False,
                            attention={},
                            next_executor=None,
                        )
                        self.cycle.owner._emit(
                            self.cycle.state,
                            "investigator.preservation_completed",
                            {
                                "hypothesis_id": preservation["hypothesis_id"],
                                "node_id": preservation["node_id"],
                                "science_adjudicated": False,
                            },
                        )
        if self.cycle.state.get("status") == "needs_attention":
            self.cycle.owner._emit(
                self.cycle.state,
                "workflow.stopped",
                {
                    "reason": "needs_attention",
                    "resumable": bool(self.cycle.state.get("resumable")),
                    "attention": dict(self.cycle.state.get("attention") or {}),
                },
            )
        elif self.cycle.state.get("status") == "checkpoint_reached":
            self.cycle.owner._emit(
                self.cycle.state,
                "workflow.stopped",
                {
                    "reason": "checkpoint_reached",
                    "checkpoint": dict(self.cycle.state.get("checkpoint") or {}),
                    "resumable": bool(self.cycle.state.get("resumable")),
                },
            )
        self.cycle.owner._persist(self.cycle.state)
        if self.cycle.owner.collector is not None:
            self.cycle.owner.collector.finish(self.cycle.state)
        return self.cycle.state

    def handle_providerquotaexhausted(self, exc):
        self.cycle.state["last_seq"] = max(
            int(self.cycle.state.get("last_seq", 0)),
            int(self.cycle.before.get("last_seq", 0)),
        )
        self.cycle.state["current_state"] = sticky_direction(
            self.cycle.state
        ) or sticky_direction(self.cycle.before)
        message = str(exc) or "provider quota exhausted"
        checkpoint = self.cycle.state.get("last_safe_checkpoint")
        failure = self.cycle.state.get("last_invocation_failure") or {}
        role = (
            self.cycle.state.get("active_role")
            or self.cycle.state.get("current_agent")
            or failure.get("role")
        )
        route = self.cycle.owner.routes.get(str(role or ""))
        provider = getattr(exc, "provider", None) or getattr(route, "provider", None)
        codex_account = getattr(exc, "account", None)
        from .backend_config import ACCOUNT_PROVIDERS

        if provider in ACCOUNT_PROVIDERS and codex_account not in {"1", "2"}:
            try:
                from .codex_accounts import selected_account

                codex_account = selected_account()
            except Exception:
                codex_account = None
        self.cycle.state.update(
            {
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
            }
        )
        self.cycle.owner._emit(
            self.cycle.state,
            "run.paused",
            {
                "reason": message,
                "error": message,
                "error_kind": "ProviderQuotaExhausted",
                "pause_kind": "provider_quota_exhausted",
                "role": role,
                "provider": provider,
                "codex_account": codex_account,
                "stage": self.cycle.state.get("phase"),
                "resumable": True,
                "checkpoint": checkpoint,
            },
        )
        try:
            self.cycle.owner._persist(self.cycle.state, project=True)
        except Exception:
            self.cycle.owner._persist(self.cycle.state, project=False)
        if self.cycle.owner.collector is not None:
            self.cycle.owner.collector.finish(self.cycle.state)
        return self.cycle.state

    def handle_keyboardinterrupt_systemexit(self, exc):
        self.cycle.state["status"] = "interrupted"
        self.cycle.state["phase"] = "interrupted"
        self.cycle.state["resumable"] = True
        self.cycle.state["execution_active"] = False
        self.cycle.state["recovery"] = {
            "action": "interrupt",
            "reason": str(exc) or "process interrupted",
            "at": now(),
        }
        self.cycle.owner._emit(
            self.cycle.state,
            "run.interrupted",
            {"reason": str(exc) or "process interrupted"},
        )
        self.cycle.owner._persist(self.cycle.state, project=True)
        if self.cycle.owner.collector is not None:
            self.cycle.owner.collector.finish(self.cycle.state)
        raise

    def handle_exception(self, exc):
        self.cycle.state["last_seq"] = max(
            int(self.cycle.state.get("last_seq", 0)),
            int(self.cycle.before.get("last_seq", 0)),
        )
        self.cycle.state["current_state"] = sticky_direction(
            self.cycle.state
        ) or sticky_direction(self.cycle.before)
        self.cycle.state.update(
            {
                "status": "failed",
                "phase": "failed",
                "resumable": True,
                "execution_active": False,
                "recovery": {
                    "action": "error",
                    "kind": type(exc).__name__,
                    "message": str(exc),
                    "at": now(),
                },
            }
        )
        from .failure_classification import record_failure

        self.cycle.owner._emit(
            self.cycle.state, "run.failed", record_failure(self.cycle.state, exc)
        )
        try:
            self.cycle.owner._persist(self.cycle.state, project=True)
        except Exception:
            self.cycle.owner._persist(self.cycle.state, project=False)
        if self.cycle.owner.collector is not None:
            self.cycle.owner.collector.finish(self.cycle.state)
        raise

    def _finalize(self):
        self.cycle.state["execution_active"] = False
        if self.cycle.owner._goal_completion_passed(self.cycle.state):
            self.cycle.state["next_start_node_id"] = None
        try:
            self.cycle.owner._persist(self.cycle.state, project=True)
            if self.cycle.owner._goal_completion_passed(self.cycle.state):
                self.cycle.owner._close_open_frontier_tips()
                self.cycle.state["next_start_node_id"] = None
                frontier = (
                    self.cycle.state.get("frontier_state")
                    if isinstance(self.cycle.state.get("frontier_state"), dict)
                    else None
                )
                if isinstance(frontier, dict):
                    frontier["next_start_node_id"] = None
                self.cycle.state.setdefault("goal_completion", {})[
                    "authority_digest"
                ] = self.cycle.owner._goal_authority_digest()
                self.cycle.owner._persist(self.cycle.state, project=False)
        except Exception as exc:
            self.cycle.state.update(
                {
                    "status": "failed",
                    "phase": "failed",
                    "resumable": True,
                    "execution_active": False,
                    "recovery": {
                        "action": "error",
                        "kind": type(exc).__name__,
                        "message": str(exc),
                        "at": now(),
                    },
                }
            )
            from .failure_classification import record_failure

            self.cycle.owner._emit(
                self.cycle.state,
                "run.failed",
                {
                    **record_failure(self.cycle.state, exc),
                    "stage": "final_canvas_projection",
                },
            )
            self.cycle.owner._persist(self.cycle.state, project=False)
            if self.cycle.owner.collector is not None:
                self.cycle.owner.collector.finish(self.cycle.state)
            raise
        run_dir = self.cycle.owner.cycle_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        all_events = self.cycle.owner.events_path.read_text(
            encoding="utf-8"
        ).splitlines()
        atomic(
            run_dir / "event_slice.jsonl",
            "\n".join(all_events[self.cycle.events_start - 1 :]) + "\n",
        )
        cost: dict[str, Any] = {
            "run_id": self.cycle.run_id,
            "workflow_events": self.cycle.state["last_seq"]
            - self.cycle.events_start
            + 1,
            "model_calls": 0,
            "currency": None,
            "total_cost": None,
            "cost": {
                "amount": None,
                "currency": None,
                "source": "unavailable",
                "confidence": "none",
                "priced_model_calls": 0,
                "model_calls": 0,
            },
            "by_agent": {},
            "by_provider": {},
            "by_model": {},
            "by_source": {},
            "usage": {
                "input_tokens": None,
                "output_tokens": None,
                "cached_input_tokens": 0,
            },
        }
        if self.cycle.owner.collector is not None:
            snap = self.cycle.owner.collector.snapshot()
            agents = snap.get("agents") or {}
            auxiliary_agents = snap.get("auxiliary_agents") or {}
            cost_roles = {**agents, **auxiliary_agents}
            all_records = [
                record
                for agent in cost_roles.values()
                for record in agent.get("cost_records", [])
            ]
            by_provider: dict[str, float] = {}
            by_model: dict[str, float] = {}
            by_source: dict[str, float] = {}
            for record in all_records:
                if record.get("amount") is None:
                    continue
                amount = float(record["amount"])
                by_provider[str(record.get("provider") or "unknown")] = (
                    by_provider.get(str(record.get("provider") or "unknown"), 0.0)
                    + amount
                )
                by_model[str(record.get("model") or "unknown")] = (
                    by_model.get(str(record.get("model") or "unknown"), 0.0) + amount
                )
                by_source[str(record.get("source") or "unavailable")] = (
                    by_source.get(str(record.get("source") or "unavailable"), 0.0)
                    + amount
                )
            cost.update(
                {
                    "model_calls": len(all_records),
                    "currency": (snap.get("cost") or {}).get("currency"),
                    "total_cost": (snap.get("cost") or {}).get("amount"),
                    "cost": snap.get("cost"),
                    "input_tokens": snap.get("tokens_in"),
                    "output_tokens": snap.get("tokens_out"),
                    "total_tokens": snap.get("tokens_total"),
                    "by_agent": {
                        role: agent.get("cost") for role, agent in cost_roles.items()
                    },
                    "by_provider": by_provider,
                    "by_model": by_model,
                    "by_source": by_source,
                    "usage": {
                        "input_tokens": snap.get("tokens_in"),
                        "output_tokens": snap.get("tokens_out"),
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
                }
            )
        write_json(run_dir / "cost_summary.json", cost)
        atomic(
            run_dir / "run_report.md",
            f"# TheoryMaster run index\n\n- run_id: `{self.cycle.run_id}`\n- cycle_status: `{self.cycle.state.get('status', 'over')}`\n- branch_verdict: `{self.cycle.branch.get('status', '')}`\n- state: `{self.cycle.owner._state_path()}`\n",
        )
        if self.cycle.owner.collector is not None:
            self.cycle.owner.collector.finish(self.cycle.state)
        return self.cycle.state
