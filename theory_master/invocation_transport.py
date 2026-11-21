"""InvocationTransport: a single explicit phase owner for InvocationSession."""

from __future__ import annotations

import json
from .native_goal_guidance import PausedGoalGuidanceError
from .observability.incident_registry import register_cycle_incident
from .workflow_support import RecoveryStop
from .native_goal import (
    NativeGoalIncomplete,
    NativeGoalBudgetExceeded,
    NativeGoalDeliveryError,
    NativeGoalExecutionLimit,
    EXECUTION_LIMIT_SCHEMA,
)
from .investigator_budget import budget_status, BUDGET_ATTENTION_CODE
from .provider_errors import ProviderQuotaExhausted
from .scientific_objective import frozen_science_digest
from .workflow_invocation import _native_goal_attention
from .invocation_execution import NATIVE_INVESTIGATOR_STAGES
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .invocation_execution import InvocationSession


class InvocationTransport:

    def __init__(self, session: InvocationSession):
        self.session = session

    def _record_goal_identity(self, identity: dict):
        """Persist SDK authority at start/attach, including interrupted runs."""
        self.session.invocation_ledger.transition(
            self.session.invocation_id, "running", native_goal_identity=identity
        )
        self.session.state["native_goal"] = {
            "status": identity["status"], "thread_id": identity["thread_id"],
            "origin_identity": identity,
        }
        if getattr(self.session, "native_goal_origin", None) is None:
            backend = getattr(self.session.owner, "backend", None)
            account = getattr(backend, "goal_account_origin", None)
            rollout = getattr(backend, "goal_rollout_origin", None)
            if isinstance(account, dict) and isinstance(rollout, dict):
                self.session.state["native_goal_quota_origin"] = {
                    **account, **rollout, "thread_id": identity["thread_id"], "provider": self.session.route.provider,
                    "objective_sha256": identity["objective_sha256"], "created_at": identity["created_at"],
                }
            else:
                self.session.state.pop("native_goal_quota_origin", None)
        self.session.owner._persist(self.session.state)

    def _prepare_native_delivery(self):
        from .boot_native_goal import pending_boot_native_goal

        investigator_goal_stage = (
            self.session.role == "investigator"
            and self.session.stage in NATIVE_INVESTIGATOR_STAGES
            and not self.session.context.get("submission_delivery")
        )
        deterministic_backend = any(
            (
                cls.__name__ in {"FakeBackend", "CanonicalFakeBackend"}
                for cls in type(self.session.owner.backend).__mro__
            )
        )
        self.session.use_native_goal = investigator_goal_stage and (
            not deterministic_backend
        )
        boot_pin = pending_boot_native_goal(self.session.owner.backend)
        if boot_pin is not None and not self.session.use_native_goal:
            from .native_goal_recovery import NativeGoalRecoveryConflict
            raise NativeGoalRecoveryConflict("Boot Resume requires the original native Investigator Goal")
        if self.session.use_native_goal and (
            not callable(getattr(self.session.owner.backend, "invoke_goal", None))
        ):
            raise RuntimeError(
                "real Investigator backend does not support required native Goal execution"
            )
        if self.session.use_native_goal:
            if self.session.native_preflight_goal is None:
                raise RuntimeError("missing Investigator Native Goal preflight")
            self.session.native_objective = self.session.native_preflight_goal
            self.session.handoff_path = (
                self.session.owner.cycle_dir
                / "workspace"
                / ".tm-handoff"
                / f"{self.session.invocation_id}.json"
            ).resolve()
            self.session.handoff_path.parent.mkdir(parents=True, exist_ok=True)
            if self.session.handoff_path.exists():
                raise RuntimeError("refusing to reuse an earlier Investigator handoff")
            self.session.limit_binding = {
                "schema": EXECUTION_LIMIT_SCHEMA,
                "invocation_id": self.session.invocation_id,
                "frozen_objective_sha256": frozen_science_digest(
                    self.session.prompt_context["frozen_objective"]
                ),
                "workspace_path": str(self.session.handoff_path.parent.parent),
                "handoff_path": str(self.session.handoff_path),
            }
            continuation = self.session.state.get("supervisor_fixture_conflict_continuation") or {}
            available_again = (
                continuation.get("available_fixture_pins")
                if (continuation.get("frozen_objective_sha256") == self.session.limit_binding["frozen_objective_sha256"]
                    and continuation.get("invocation_id") == self.session.state.get("last_invocation_id")
                    and self.session.stage == "investigate" and (self.session.state.get("recovery") or {}).get("action") == "resume")
                else None
            )
            self.session.setup_context = (
                json.dumps(
                    {
                        **self.session.prompt_context,
                        "sdk_execution_limit_handoff": self.session.limit_binding,
                        **({"sdk_fixture_availability_correction": available_again} if available_again else {}),
                    },
                    ensure_ascii=False,
                    default=str,
                )
                + "\n\nSDK DELIVERY INTERFACE (not scientific evaluation criteria): "
                + f"Only after completing the frozen science, write {self.session.handoff_path} "
                + "as JSON following the current output contract:\n"
                + self.session.contract.prompt_projection(self.session.prompt_context)
                + "\nThe handoff also accepts claim as an alias for statement, or simple strings in findings. "
                + "Only a complete self-contained statement is required for each Finding; method, result and provenance "
                + "are optional authored content. Omit unused optional fields; do not invent placeholder values. "
                + "Choose the count and content yourself; preserve distinct scientific claims. "
                + "Put detailed proofs, scripts, numbers and other material in arbitrary research files, "
                + "but make each submitted claim scientifically self-contained. "
                + "Do not submit Findings while blocked, paused or unfinished. "
                + "For an authorized source audit, resolve each indispensable fixture reference from "
                + "Required Context against workspace_access.benchmark_root and the exact registered fixture manifest. "
                + "Use registered_fixture_inputs for the declared paths, sizes, hashes and current availability. "
                + "Check the exact path directly, for example with stat, and match its SHA-256 to the manifest. "
                + "A directory listing filtered by ignore rules is insufficient evidence of absence. "
                + ("The previous missing-file report contradicted SDK-verified canonical files listed in "
                   "sdk_fixture_availability_correction. Continue the same frozen decisive_test in this workspace. "
                   if available_again else "")
                + "The inventory verifies file identity only. Apply the frozen task restrictions for eligibility, "
                + "independence and permitted read timing before using source values. "
                + "NARROW EXECUTION-LIMIT DELIVERY: Use this same invocation's handoff path "
                + "for a separate JSON object only if (a) an indispensable frozen-test input or "
                + "eligible support remains unavailable after a bounded authorized source audit, or "
                + "(b) the reviewed Research Contract explicitly withholds authorization for the "
                + "remaining numerical test, no approval has been given, and the complete currently "
                + "authorized method deliverable has been preserved for operator review. Use kind "
                + "input_support_unavailable for (a), authorization_pending for (b): "
                + "{execution_limit:{kind:'input_support_unavailable'|'authorization_pending',invocation_id:string,"
                + "frozen_objective_sha256:string,reason:string,checked_alternatives:[string],"
                + "unexecuted_test:string,required_dependency:string,evidence:[{path:string,sha256:string}]}}. "
                + ("When using input_support_unavailable, add "
                   "input_support_audit:{mode:'missing_fixture_files'|'insufficient_eligible_support',fixture_references:[string]}. "
                   "Use a nonempty list of unique exact references from registered_fixture_inputs. "
                   "For missing_fixture_files, name only indispensable files whose canonical bytes are unavailable. "
                   "For insufficient_eligible_support, name the available inputs inspected and explain the distinct "
                   "eligibility or scientific-support gap in reason and checked_alternatives. "
                   if self.session.prompt_context.get("registered_fixture_inputs") else "")
                + "For authorization_pending, add authorization_clause and deliverable_clause as "
                + "exact verbatim clauses of at least 20 characters from the reviewed Research Contract "
                + "and Final Gate in Setup Context; the SDK checks both against those exact texts. "
                + "Copy invocation_id and frozen_objective_sha256 exactly from sdk_execution_limit_handoff. "
                + "For (a), describe the source-recovery audit in checked_alternatives. For (b), "
                + "describe completed authorized work and why the remaining test requires separate "
                + "approval; name that approval in required_dependency. Cite nonempty "
                + "existing regular evidence files by canonical workspace-relative path and actual SHA-256; "
                + "no symlinks, outside paths, or more than 16 MiB per file / 32 MiB total. "
                + "Do not mix this object with findings. Only these verified limits permit ending the "
                + "native operation after administrative delivery while the test remains unexecuted. "
                + "It is not scientific completion, an inconclusive result or a Judge verdict. "
                + "Method failure, scientific difficulty, cost alone, or unfinished authorized work "
                + "do not qualify. Preserve frozen "
                + "science, lawful inputs and resource guards; do not repeatedly audit unchanged sources."
                + self.session.skill_addendum
            )
        if self.session.use_native_goal:
            from .native_goal import native_goal_setup_turn
            from .native_goal_recovery import NativeGoalRecoveryConflict, bind_origin_context, find_native_goal_origin

            origin = find_native_goal_origin(self.session)
            backend = self.session.owner.backend
            retained = getattr(backend, "retained_goal_home", None)
            if boot_pin is not None:
                from .boot_native_goal import require_boot_native_origin
                require_boot_native_origin(
                    boot_pin, origin, role=self.session.role, stage=self.session.stage,
                    logical_key=self.session.logical_key, thread_id=self.session.identifier,
                    provider=self.session.route.provider, model=self.session.route.model,
                )
            elif retained is not None and not backend.retained_goal_delivered:
                original_goal = (origin or {}).get("persistent_goal") or {}
                if (origin is None or origin["thread_id"] != retained.thread_id
                        or self.session.route.provider != retained.provider
                        or origin["objective_sha256"] != retained.binding["objective_sha256"]
                        or str(original_goal.get("created_at")) != retained.binding["created_at"]):
                    raise NativeGoalRecoveryConflict("Quota Resume cannot establish the original Investigator Goal")
            if origin is not None:
                from pathlib import Path
                from .native_goal import execution_limit_setup_binding
                self.session.native_goal_origin = origin
                self.session.native_objective = origin["objective"]
                self.session.handoff_path = Path(origin["handoff_path"])
                self.session.setup_context = bind_origin_context(origin)
                self.session.limit_binding = execution_limit_setup_binding(
                    origin["setup_context"], self.session.handoff_path
                )
                self.session.invocation_ledger.transition(
                    self.session.invocation_id, "running", native_goal_origin=origin
                )
                self.session.owner._persist(self.session.state)

            self.session.state["prompt_metrics"][-1]["native_setup_turn_chars"] = len(
                native_goal_setup_turn(
                    self.session.setup_context, self.session.native_objective
                )
            )

    def invoke_with_audit(self, prompt_text: str, input_kind: str):
        if input_kind not in {"task", "continuation"}:
            self.session.state["prompt_metrics"][-1].setdefault(
                "repair_turns", []
            ).append({"input_kind": input_kind, "prompt_chars": len(prompt_text)})
        if self.session.use_native_goal and input_kind not in {"task", "continuation"}:
            self.session.owner._contract_repair_failed(
                self.session.state,
                invocation_id=self.session.invocation_id,
                role=self.session.role,
                stage=self.session.stage,
                thread_id=self.session.identifier,
                raw_outputs=[],
                errors=[
                    {
                        "path": "/",
                        "code": "native_goal_delivery_invalid",
                        "message": "Native Goal output requires operator attention; a separate LLM repair Turn is prohibited.",
                    }
                ],
                repair_prompt="",
                invocation_ledger=self.session.invocation_ledger,
                artifact_tracker=self.session.artifact_tracker,
            )
        if self.session.owner.collector is not None:
            record_input = getattr(self.session.owner.collector, "on_agent_input", None)
            if callable(record_input):
                if self.session.use_native_goal:
                    from .native_goal import native_goal_setup_turn

                    record_input(
                        role=self.session.role,
                        stage=self.session.stage,
                        invocation_id=self.session.invocation_id,
                        thread_id=self.session.identifier,
                        content=native_goal_setup_turn(
                            self.session.setup_context, self.session.native_objective
                        ),
                        input_kind=("native_goal_resume_binding" if getattr(self.session, "native_goal_origin", None)
                                    else "native_goal_setup_turn"),
                    )
                    record_input(
                        role=self.session.role,
                        stage=self.session.stage,
                        invocation_id=self.session.invocation_id,
                        thread_id=self.session.identifier,
                        content=self.session.native_objective,
                        input_kind="native_goal_objective",
                    )
                else:
                    record_input(
                        role=self.session.role,
                        stage=self.session.stage,
                        content=prompt_text,
                        input_kind=input_kind,
                    )
        if self.session.use_native_goal and input_kind in {"task", "continuation"}:
            goal_backend = getattr(self.session.owner.backend, "invoke_goal", None)
            if not callable(goal_backend):
                raise RuntimeError(
                    "native Investigator Goal requires Goal-capable backend"
                )
            live_budget = budget_status(
                self.session.owner.cycle_dir,
                self.session.owner.spec.research_root,
                self.session.state,
            )
            return goal_backend(
                self.session.role,
                goal_objective=self.session.native_objective,
                context_prompt=self.session.setup_context,
                handoff_path=str(self.session.handoff_path),
                thread_id=self.session.identifier,
                model=self.session.route.model,
                provider=self.session.route.provider,
                token_allowance=live_budget["allowance_tokens"],
                tokens_before_goal=live_budget["observed_tokens"],
                control_check=lambda: self.session.owner._check_recovery_control(self.session.state),
                goal_identity_observer=self._record_goal_identity,
            )
        extra = {}
        if self.session.role == "investigator" and self.session.context.get("submission_delivery"):
            deterministic = any(cls.__name__ in {"FakeBackend", "CanonicalFakeBackend"}
                                for cls in type(self.session.owner.backend).__mro__)
            if not deterministic:
                from .submission_rework import delivery_usage_guard
                extra = {"usage_guard": delivery_usage_guard(self.session),
                         "control_check": lambda: self.session.owner._check_recovery_control(self.session.state)}
        return self.session.owner.backend.invoke(
            self.session.role,
            prompt_text,
            self.session.identifier,
            self.session.route.model,
            self.session.route.provider,
            **extra,
        )

    def _invoke_initial(self):
        try:
            self.session.identifier, self.session.raw = self.invoke_with_audit(
                self.session.prompt,
                "continuation" if self.session.continuation else "task",
            )
            if self.session.use_native_goal:
                from .native_delivery_journal import record_native_delivery

                record_native_delivery(self.session)
                backend = self.session.owner.backend
                if getattr(backend, "retained_goal_home", None) is not None:
                    backend.retained_goal_delivered = True
            if self.session.skill_binding:
                self.session.skill_versions[self.session.identifier] = (
                    self.session.skill_binding["catalogue_version"]
                )
                self.session.skill_flags[self.session.identifier] = (
                    self.session.skill_binding["learning"]
                )
                if (
                    self.session.skill_binding.get("learning")
                    and self.session.identifier not in self.session.skill_protocols
                ):
                    self.session.skill_protocols.append(self.session.identifier)
            # Reattachment binds the original Setup without sending a new Agent input.
            if (self.session.pending_guidance is not None
                    and getattr(self.session, "native_goal_origin", None) is None):
                from .attention_guidance import mark_guidance_consumed

                consumed = mark_guidance_consumed(
                    self.session.owner.cycle_dir,
                    self.session.pending_guidance,
                    role=self.session.role,
                    stage=self.session.stage,
                )
                self.session.state["attention_guidance"] = consumed
                self.session.state["operator_guidance"] = consumed
                self.session.state["operator_guidance_pending"] = False
                self.session.owner._emit(
                    self.session.state,
                    "attention.guidance_consumed",
                    {
                        "source": consumed.get("source"),
                        "role": self.session.role,
                        "stage": self.session.stage,
                        "path": consumed.get("path"),
                    },
                )
                self.session.owner._persist(self.session.state)
        except PausedGoalGuidanceError as exc:
            return self._handle_paused_guidance_failure(exc)
        except NativeGoalBudgetExceeded as exc:
            return self._handle_live_budget(exc)
        except NativeGoalExecutionLimit as exc:
            return self._handle_execution_limit(exc)
        except NativeGoalDeliveryError as exc:
            return self._handle_invalid_delivery(exc)
        except NativeGoalIncomplete as exc:
            return self._handle_incomplete_goal(exc)

    def _handle_paused_guidance_failure(self, exc):
        """Retain pending correction without entering a generic repair retry."""
        from .workflow_support import RecoveryStop

        native = dict(getattr(self.session.owner.backend, "last_native_goal", {}) or {})
        self.session.state["native_goal"] = {
            **(self.session.state.get("native_goal") or {}), **native,
        }
        self.session.state.update(status="paused", phase="paused", resumable=True,
                                  execution_active=False, next_executor="investigator")
        self.session.invocation_ledger.transition(
            self.session.invocation_id, "interrupted", role=self.session.role,
            thread_id=self.session.identifier, reason="paused Goal correction remains unconfirmed",
        )
        if self.session.artifact_tracker is not None:
            self.session.artifact_tracker.finalize(status="interrupted")
        self.session.owner._emit(self.session.state, "native_goal.guidance_pending", {"reason": str(exc)})
        self.session.owner._persist(self.session.state)
        raise RecoveryStop("paused Goal correction requires explicit recovery") from exc

    def _handle_live_budget(self, exc):
        live_budget = budget_status(
            self.session.owner.cycle_dir,
            self.session.owner.spec.research_root,
            self.session.state,
        )
        self.session.state["investigator_budget"] = {
            k: v
            for k, v in live_budget.items()
            if k not in {"reviewed_evidence_ids", "reviewed_progress_refs"}
        }
        self.session.state.update(
            status="needs_attention",
            phase="investigator:budget_review",
            resumable=True,
            execution_active=False,
            next_executor="investigator",
            attention={
                "code": BUDGET_ATTENTION_CODE,
                "reason": f"Native Goal reached actual live Investigator usage {exc.observed_tokens}/{exc.allowance_tokens} tokens; SDK internal reviewer is evaluating the next tranche.",
                "thread_id": exc.thread_id,
            },
        )
        self.session.state["native_goal"] = dict(
            getattr(self.session.owner.backend, "last_native_goal", {}) or {}
        )
        self.session.invocation_ledger.transition(
            self.session.invocation_id,
            "interrupted",
            role=self.session.role,
            thread_id=exc.thread_id,
            reason="live Native Goal token guard exceeded",
        )
        if self.session.artifact_tracker is not None:
            self.session.artifact_tracker.finalize(status="interrupted")
        self.session.owner._emit(
            self.session.state,
            "investigation.budget_review_started",
            {
                "observed": exc.observed_tokens,
                "allowance": exc.allowance_tokens,
                "review_index": live_budget["review_index"],
                "trigger": "native_goal_live_token_usage",
                "mode": "sdk_internal",
            },
        )
        self.session.owner._persist(self.session.state)
        if self.session.owner._resolve_3plus3_budget(self.session.state, live_budget):
            return self.session.owner._call(
                self.session.state,
                self.session.role,
                self.session.context,
                resume_role=self.session.resume_role,
                fresh_thread=False,
                contract_role=self.session.contract_role,
                output_key=self.session.output_key,
            )
        from .internal_budget_review import review_budget_in_sdk

        try:
            decision = review_budget_in_sdk(
                backend=self.session.owner.backend,
                cycle_dir=self.session.owner.cycle_dir,
                state=self.session.state,
                goal=self.session.owner.spec.goal,
                research_contract=self.session.owner.spec.contract,
                final_gate=self.session.owner.spec.final_gate,
            )
        except (ProviderQuotaExhausted, RecoveryStop):
            raise
        except Exception as review_exc:
            self.session.state["attention"] = {
                "code": "investigator_budget_review_failed",
                "reason": f"SDK Budget Reviewer failed: {type(review_exc).__name__}: {review_exc}",
            }
            self.session.owner._emit(
                self.session.state,
                "investigation.budget_review_failed",
                {
                    "review_index": live_budget["review_index"],
                    "error_type": type(review_exc).__name__,
                    "detail": str(review_exc)[:600],
                },
            )
            self.session.owner._persist(self.session.state)
            raise RecoveryStop(
                "SDK budget review failed; no implicit approval"
            ) from review_exc
        self.session.owner._emit(
            self.session.state,
            "investigation.budget_review_decided",
            {
                "review_index": decision["review_index"],
                "decision": decision["decision"],
                "new_allowance": decision["new_allowance"],
                "mode": "sdk_internal",
            },
        )
        if decision["decision"] != "approve":
            self.session.owner._hold_budget_rejected(
                self.session.state, decision["reason"]
            )
        renewed = budget_status(
            self.session.owner.cycle_dir,
            self.session.owner.spec.research_root,
            self.session.state,
        )
        self.session.state["investigator_budget"] = {
            k: v
            for k, v in renewed.items()
            if k not in {"reviewed_evidence_ids", "reviewed_progress_refs"}
        }
        self.session.state.update(
            status="active",
            phase="investigator:running",
            execution_active=True,
            attention={},
        )
        self.session.owner._persist(self.session.state)
        return self.session.owner._call(
            self.session.state,
            self.session.role,
            self.session.context,
            resume_role=self.session.resume_role,
            fresh_thread=False,
            contract_role=self.session.contract_role,
            output_key=self.session.output_key,
        )

    def _handle_execution_limit(self, exc):
        from .native_goal_recovery import execution_limit_delivery_id
        delivery_id = execution_limit_delivery_id(self.session)
        if (
            not self.session.use_native_goal
            or exc.handoff_path != str(self.session.handoff_path)
            or exc.thread_id != self.session.identifier
            or exc.execution_limit.get("invocation_id") != delivery_id
            or (
                exc.execution_limit.get("frozen_objective_sha256")
                != self.session.limit_binding["frozen_objective_sha256"]
            )
        ):
            raise RuntimeError(
                "execution-limit exception is not bound to this invocation"
            ) from exc
        if "input_support_audit" in exc.execution_limit and not exc.fixture_audit_pins:
            raise ValueError("execution-limit fixture audit has no SDK boundary verification") from exc
        self.session.state["native_goal"] = dict(
            getattr(self.session.owner.backend, "last_native_goal", {}) or {}
        )
        self.session.state["execution_limit_handoff"] = {
            "execution_limit": exc.execution_limit,
            "handoff_path": exc.handoff_path,
            "invocation_id": delivery_id,
            "monitor_invocation_id": self.session.invocation_id,
            "thread_id": exc.thread_id,
            **({"fixture_audit_pins": exc.fixture_audit_pins} if exc.fixture_audit_pins else {}),
            **({"origin_binding": {
                key: value for key, value in self.session.native_goal_origin.items()
                if key not in {"objective", "setup_context"}
            }} if getattr(self.session, "native_goal_origin", None) is not None else {}),
        }
        self.session.state.update(
            execution_active=False,
            scientific_verdict=None,
            phase="investigator:execution_limited",
            last_invocation_id=self.session.invocation_id,
            active_invocation_id=None,
        )
        self.session.invocation_ledger.transition(
            self.session.invocation_id,
            "interrupted",
            role=self.session.role,
            thread_id=exc.thread_id,
            reason="validated invocation-bound execution limitation",
            execution_limit=exc.execution_limit,
            handoff_path=exc.handoff_path,
            delivery_origin_invocation_id=delivery_id,
        )
        if self.session.artifact_tracker is not None:
            self.session.artifact_tracker.finalize(status="interrupted")
        self.session.owner._emit(
            self.session.state,
            "investigation.execution_limit_delivered",
            {
                **self.session.state["execution_limit_handoff"],
                "scientific_verdict": None,
            },
        )
        self.session.owner._persist(self.session.state)
        raise

    def _handle_invalid_delivery(self, exc):
        self.session.state["native_goal"] = dict(
            getattr(self.session.owner.backend, "last_native_goal", {}) or {}
        )
        self.session.state.update(
            status="needs_attention",
            phase="investigator:handoff_invalid",
            execution_active=False,
            resumable=True,
            next_executor="investigator",
            attention={
                "code": "investigator_handoff_invalid",
                "reason": exc.detail,
                "thread_id": exc.thread_id,
            },
        )
        self.session.invocation_ledger.transition(
            self.session.invocation_id,
            "interrupted",
            role=self.session.role,
            reason=f"SDK handoff invalid: {exc.detail}",
            thread_id=exc.thread_id,
        )
        if self.session.artifact_tracker is not None:
            self.session.artifact_tracker.finalize(status="interrupted")
        self.session.owner._persist(self.session.state)
        raise RecoveryStop(
            "Investigator science complete but SDK handoff invalid: " + exc.detail
        )

    def _handle_incomplete_goal(self, exc):
        self.session.state.update(
            status="needs_attention",
            phase="investigator:goal_suspended",
            execution_active=False,
            resumable=True,
            next_executor="investigator",
            attention=_native_goal_attention(
                self.session.owner.cycle_dir,
                goal_status=exc.status,
                thread_id=exc.thread_id,
            ),
        )
        self.session.state["native_goal"] = dict(
            getattr(self.session.owner.backend, "last_native_goal", {}) or {}
        )
        capacity = self.session.state["native_goal"].get("capacity_reconnect")
        if isinstance(capacity, dict) and capacity.get("status") == "timeout":
            self.session.state["attention"]["code"] = "provider_model_capacity_timeout"
            self.session.state["attention"][
                "reason"
            ] = "Codex model service remained at capacity for 30 minutes; automatic same-thread retries reached their deadline. The scientific thread/workspace are preserved. Resume manually once the service is available."
            self.session.state["attention"]["model_error"] = str(
                capacity.get("error") or ""
            )[:400]
            self.session.state["attention"]["retry_count"] = int(
                capacity.get("retry_count") or 0
            )
            register_cycle_incident(
                self.session.owner.cycle_dir,
                severity="attention",
                kind="provider_capacity_timeout",
                source_event="sdk.capacity_retry.exhausted",
                code="provider_model_capacity_timeout",
                discriminator=self.session.invocation_id,
                summary=self.session.state["attention"]["reason"],
                fields={
                    "model": self.session.route.model,
                    "provider": self.session.route.provider,
                    "thread_id": exc.thread_id,
                    "retry_count": self.session.state["attention"]["retry_count"],
                    "max_wait_seconds": 1800,
                },
            )
        self.session.invocation_ledger.transition(
            self.session.invocation_id,
            "interrupted",
            role=self.session.role,
            reason="native Goal noncomplete: " + exc.status,
            thread_id=exc.thread_id,
        )
        if self.session.artifact_tracker is not None:
            self.session.artifact_tracker.finalize(status="interrupted")
        self.session.owner._persist(self.session.state)
        if exc.status == "usageLimited":
            self.session.state["attention"] = {}
            thread_accounts = getattr(
                self.session.owner.backend, "thread_accounts", None
            )
            account = (
                thread_accounts.get(exc.thread_id)
                if isinstance(thread_accounts, dict)
                else None
            )
            raise ProviderQuotaExhausted(
                "Codex native Goal reached usageLimited; no Findings or Judge decision was submitted. Resume the frozen scientific task from its retained thread/workspace after quota recovery.",
                provider=self.session.route.provider,
                account=account,
            ) from exc
        raise RecoveryStop("Investigator Native Goal suspended: " + exc.status)
