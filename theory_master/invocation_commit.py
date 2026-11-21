"""InvocationCommit: a single explicit phase owner for InvocationSession."""

from __future__ import annotations
from typing import Any
from .invocations import stable_hash
from .prompt_stages import compact_hypothesis_view
from .role_registry import CAPABILITIES
from .runtime import now, write_json
from .workflow_invocation import _bind_review_output
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .invocation_execution import InvocationSession


class InvocationCommit:

    def __init__(self, session: InvocationSession):
        self.session = session

    def _close_output(self):
        self.session.artifact_manifest = (
            self.session.artifact_tracker.finalize(status="completed")
            if self.session.artifact_tracker is not None
            else None
        )
        if self.session.artifact_manifest is not None:
            self.session.state["last_invocation_artifact_manifest"] = str(
                self.session.artifact_manifest
            )
        self.session.state["last_invocation_id"] = self.session.invocation_id
        self.session.state["threads"][self.session.thread_key] = self.session.identifier
        self.session.agent_output = self.session.output
        self.session.semantic_audit = None
        from .abc_pipeline import annotate_literature

        if (
            self.session.role == "literature"
            and str(self.session.validation_context.get("routing_source") or "")
            != "research_lead"
        ):
            semantic_context = dict(self.session.validation_context)
            lock = (
                semantic_context.get("hypothesis_lock")
                or semantic_context.get("branch")
                or {}
            )
            if isinstance(lock, dict) and "cycle_criteria" not in semantic_context:
                semantic_context["cycle_criteria"] = (
                    lock.get("cycle_criteria")
                    or lock.get("cycle_progress_criteria")
                    or {}
                )
            semantic_context["hypothesis_lock"] = compact_hypothesis_view(lock)
            semantic_context.pop("branch", None)
            self.session.output, self.session.semantic_audit = annotate_literature(
                self.session.agent_output,
                semantic_context,
                self.session.owner.semantic_backend,
            )
        from .contract_closure import bind_registered_evidence, close_agent_output

        self.session.output = close_agent_output(
            self.session.role,
            self.session.stage,
            self.session.output,
            self.session.validation_context,
            self.session.state,
        )
        if self.session.role == "challenger" and self.session.stage in {
            "candidate_review",
            "repair_verification",
        }:
            self.session.output = _bind_review_output(
                self.session.output, self.session.initial_context, self.session.stage
            )
        self.session.state.setdefault("agent_outputs", {})[
            self.session.output_key or self.session.role
        ] = self.session.agent_output
        if (
            self.session.actual == "research_lead"
            and self.session.stage in {"frontier_proposal", "frontier_maintenance"}
            and self.session.graph_stamp
            and isinstance(self.session.initial_context.get("agent_graph"), dict)
        ):
            self.session.state["research_lead_agent_graph_stamp"] = (
                self.session.graph_stamp
            )
        if self.session.actual == "research_lead":
            self.session.state["research_lead_thread_id"] = self.session.identifier
            consume_reads = getattr(
                self.session.owner.backend, "consume_turn_reads", None
            )
            if callable(consume_reads):
                turn_reads = consume_reads()
                if turn_reads is not None:
                    self.session.state.setdefault("research_lead_reads", []).extend(
                        turn_reads
                    )
            if self.session.owner.cycle_dir is not None:
                self.session.owner._save_research_lead_state(
                    thread_id=self.session.identifier,
                    route=self.session.route,
                    cycle_id=self.session.owner.cycle_dir.name,
                    rollover_reason=self.session.state.get(
                        "research_lead_rollover_reason"
                    ),
                    rollover_history=self.session.state.get(
                        "research_lead_rollover_history"
                    ),
                    context_chars=self.session.state.get("research_lead_context_chars"),
                )
        lead_frontier_grounding = (
            self.session.role == "literature"
            and str(self.session.validation_context.get("routing_source") or "")
            == "research_lead"
        )
        self.session.evidence_registration_audit: list[dict[str, Any]] = []
        if lead_frontier_grounding:
            self.session.evidence_registration_audit = [
                {
                    "status": "frontier_grounding_only",
                    "reason": "Research Lead Literature detour is planning context, not current-Cycle adjudicative Evidence",
                }
            ]
        elif (self.session.role == "investigator" and
              (self.session.context.get("submission_delivery") or {}).get("authority")
              == "operator_delivery_structure_only"):
            # A delivery supplement records execution provenance, not a new
            # scientific Finding or an amendment to committed Evidence.
            self.session.evidence_registration_audit = [{"status": "delivery_only",
                "reason": "Explicit operator delivery correction preserves committed scientific Evidence"}]
        elif self.session.role in CAPABILITIES or self.session.role == "challenger":
            refs = self.session.owner._register_evidence(
                self.session.state,
                self.session.role,
                self.session.output,
                stage=self.session.stage,
            )
            self.session.evidence_registration_audit = list(
                self.session.owner._last_evidence_registration_audit
            )
            bind_registered_evidence(self.session.output, refs)
        self.session.closure_artifact = (
            self.session.owner.cycle_dir
            / "semantic_closure"
            / f"{self.session.invocation_id}.json"
        )
        self.session.closure_payload = {
            "role": self.session.role,
            "stage": self.session.stage,
            "agent_output": self.session.agent_output,
            "semantic_backend": (
                getattr(
                    self.session.owner.semantic_backend,
                    "name",
                    type(self.session.owner.semantic_backend).__name__,
                )
                if self.session.semantic_audit is not None
                else None
            ),
            "semantic_pipeline": self.session.semantic_audit,
            "evidence_registration_audit": self.session.evidence_registration_audit,
            "invocation_artifact_manifest": (
                str(self.session.artifact_manifest)
                if self.session.artifact_manifest
                else None
            ),
            "closed_output": self.session.output,
            "at": now(),
        }
        write_json(self.session.closure_artifact, self.session.closure_payload)
        self.session.closure_hash = stable_hash(self.session.closure_payload)
        all_artifacts_rejected = [
            item
            for item in self.session.evidence_registration_audit
            if item.get("status") == "all_artifacts_rejected"
        ]
        if all_artifacts_rejected:
            evidence_ids = ", ".join(
                (str(item.get("evidence_id")) for item in all_artifacts_rejected)
            )
            message = f"Evidence artifact protocol violation: every submitted artifact pointer was rejected for evidence {evidence_ids}; registration audit is saved in {self.session.closure_artifact}"
            self.session.state.update(
                {
                    "phase": f"{self.session.role}:{self.session.stage}:artifact_validation_failed",
                    "protocol_health": "violation",
                    "resumable": True,
                    "recovery": {
                        "action": "evidence_artifact_validation_failed",
                        "invocation_id": self.session.invocation_id,
                        "role": self.session.role,
                        "stage": self.session.stage,
                        "artifact": str(self.session.closure_artifact),
                        "at": now(),
                    },
                }
            )
            self.session.invocation_ledger.transition(
                self.session.invocation_id,
                "quarantined",
                role=self.session.role,
                stage=self.session.stage,
                commit_state="not_committed",
                closure_artifact=str(self.session.closure_artifact),
                closure_hash=self.session.closure_hash,
                logical_key=self.session.logical_key,
                error_type="EvidenceArtifactProtocolViolation",
                error=message,
            )
            self.session.owner._emit(
                self.session.state,
                "invocation.quarantined",
                {
                    "invocation_id": self.session.invocation_id,
                    "role": self.session.role,
                    "stage": self.session.stage,
                    "reason": "all_artifacts_rejected",
                    "semantic_closure": str(self.session.closure_artifact),
                },
            )
            self.session.owner._persist(self.session.state)
            raise ValueError(message)

    def _commit_output(self):
        self.session.state["active_invocation_id"] = None
        self.session.state.setdefault("role_outputs", {})[
            self.session.output_key or self.session.role
        ] = self.session.output
        self.session.state.update({"phase": f"{self.session.role}:completed"})
        self.session.invocation_ledger.transition(
            self.session.invocation_id,
            "completed",
            role=self.session.role,
            stage=self.session.stage,
            commit_state="not_committed",
            closure_artifact=str(self.session.closure_artifact),
            closure_hash=self.session.closure_hash,
            logical_key=self.session.logical_key,
            invocation_artifact_manifest=(
                str(self.session.artifact_manifest)
                if self.session.artifact_manifest
                else None
            ),
        )
        self.session.state["active_role"] = self.session.role
        self.session.owner._emit(
            self.session.state,
            "role.completed",
            {
                "role": self.session.role,
                "stage": self.session.stage,
                "output": self.session.agent_output,
                "semantic_closure": str(self.session.closure_artifact),
                "invocation_id": self.session.invocation_id,
                "thread_id": self.session.identifier,
                "investigation_attempt": self.session.state.get(
                    "investigation_attempt", 0
                ),
                "investigation_round_count": self.session.validation_context.get(
                    "round"
                ),
            },
        )
        self.session.owner._persist(self.session.state)
        self.session.owner._checkpoint(
            self.session.state, self.session.role.replace("/", "-").replace(" ", "-")
        )
        self.session.invocation_ledger.transition(
            self.session.invocation_id,
            "committed",
            role=self.session.role,
            stage=self.session.stage,
            commit_state="committed",
            closure_artifact=str(self.session.closure_artifact),
            closure_hash=self.session.closure_hash,
            logical_key=self.session.logical_key,
            invocation_artifact_manifest=(
                str(self.session.artifact_manifest)
                if self.session.artifact_manifest
                else None
            ),
        )
        self.session.owner._finish_skill_memory(
            self.session.state,
            self.session.actual,
            self.session.invocation_id,
            binding=self.session.skill_binding,
        )
        return self.session.output
