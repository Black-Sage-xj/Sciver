from __future__ import annotations

"""Agent invocation, prompt/contract validation, repair, and thread continuity."""

import json
import os
import uuid
from pathlib import Path
from typing import Any

from .contracts import role_contract
from .identity import identity_files
from .observability.incident_registry import LOG_NAME as INCIDENT_LOG_NAME, register_cycle_incident
from .invocation_artifacts import InvocationArtifactTracker
from .invocations import InvocationLedger, stable_hash
from .output_normalization import NormalizationResult, OutputNormalizationError, normalize_agent_output
from .output_validation import ValidationError, ValidationResult, validate_agent_output
from .prompt_stages import (
    build_continuation_prompt,
    build_contract_repair_prompt,
    build_initial_prompt,
    build_json_repair_prompt,
    compact_hypothesis_view,
    stage_context,
    stage_for,
)
from .role_registry import CAPABILITIES
from .runtime import atomic, now, write_json
from .skills import WRITE_ROLES, role_skills, thread_cycle_prefix, write_rule
from .workflow_support import RecoveryStop
from .native_goal import (
    NativeGoalIncomplete, NativeGoalBudgetExceeded, NativeGoalDeliveryError,
    NativeGoalExecutionLimit, EXECUTION_LIMIT_SCHEMA,
)
from .investigator_budget import budget_status, BUDGET_ATTENTION_CODE
from .provider_errors import ProviderQuotaExhausted
from .scientific_objective import frozen_science_view, frozen_science_digest, review_binding, require_review_binding


def _review_subjects(context: dict[str, Any], stage: str) -> dict[str, dict[str, Any]]:
    rows = ([context.get("repaired_candidate", {})] if stage == "repair_verification"
            else context.get("candidate", context.get("candidates", [])))
    return {str(row.get("id") or ""): row.get("frozen_objective", frozen_science_view(row))
            for row in rows if isinstance(row, dict)}


def _bind_review_output(output: dict[str, Any], context: dict[str, Any], stage: str,
                        *, verify: bool = False) -> dict[str, Any]:
    subjects = _review_subjects(context, stage)
    result = json.loads(json.dumps(output))
    reviews = [result] if stage == "repair_verification" else result.get("reviews", [])
    for review in reviews:
        candidate_id = str(review.get("candidate_id") or "")
        if candidate_id not in subjects:
            raise RecoveryStop("scientific review subject is missing from its invocation")
        if verify:
            try:
                require_review_binding(candidate_id, subjects[candidate_id], review.get("reviewed_science"))
            except (KeyError, ValueError) as exc:
                raise RecoveryStop(str(exc)) from exc
        else:
            review["reviewed_science"] = review_binding(candidate_id, subjects[candidate_id])
    return result

_LEGACY_INVESTIGATOR_ARTIFACT_FIELDS = frozenset({
    "artifacts", "artifact_pointers", "artifact_roles", "artifact_path", "path",
})


def _normalize_legacy_investigator_artifact_fields(
    output: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Drop only obsolete Investigator finding path fields at runtime ingress.

    The strict RoleContract remains unchanged for direct callers. This narrow
    compatibility shim never opens, resolves, or registers a supplied path.
    """
    findings = output.get("findings")
    if not isinstance(findings, list):
        return output, []
    normalized = dict(output)
    normalized_findings: list[Any] = []
    repairs: list[dict[str, Any]] = []
    for index, item in enumerate(findings):
        if not isinstance(item, dict):
            normalized_findings.append(item)
            continue
        removed = sorted(_LEGACY_INVESTIGATOR_ARTIFACT_FIELDS.intersection(item))
        if not removed:
            normalized_findings.append(item)
            continue
        cleaned = dict(item)
        for field in removed:
            cleaned.pop(field, None)
        normalized_findings.append(cleaned)
        repairs.append({
            "kind": "drop_legacy_investigator_artifact_fields",
            "finding_index": index,
            "fields": removed,
        })
    if repairs:
        normalized["findings"] = normalized_findings
    return normalized, repairs


CONTRACT_REPAIR_LIMIT = 3


def _native_goal_last_public_report(cycle_dir: Path, thread_id: str, *, tail_bytes: int = 128_000) -> str:
    """Read the last public Investigator message, not hidden reasoning or tools.

    This is only operational diagnostic context. It is *never* a Finding,
    scientific verdict, or permission to change the frozen decisive_test.
    The bounded tail avoids scanning a multi-GB run transcript after a stop.
    """
    import re

    if not thread_id:
        return ""
    events = Path(cycle_dir) / "runtime_events.jsonl"
    try:
        if events.is_symlink():
            return ""
        with events.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - tail_bytes))
            lines = stream.read().splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except (UnicodeError, ValueError):
            continue
        if (not isinstance(event, dict) or event.get("event") != "LLM_MESSAGE"
                or event.get("role") != "investigator"
                or event.get("thread_id") != thread_id):
            continue
        message = event.get("content")
        if isinstance(message, str) and message.strip():
            concise = " ".join(message.split())[:1600]
            return re.sub(
                r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*\S+",
                r"\1=[redacted]", concise,
            )
    return ""


def _native_goal_attention(cycle_dir: Path, *, goal_status: str, thread_id: str) -> dict[str, str]:
    """Project a Native Goal terminal status into actionable diagnostic data."""
    last_report = (_native_goal_last_public_report(cycle_dir, thread_id)
                   if goal_status == "blocked" else "")
    if goal_status == "blocked":
        reason = (
            "Investigator Native Goal returned an unexpected blocked status before "
            "completing its frozen decisive_test; no Finding is ready for Judge. "
            "Investigator has no authority to request scientific self-blocking; "
            "this status is an interruption requiring operational review, not "
            "a scientific stop/preservation request. "
            + ("Last public Investigator report (not independently adjudicated): " + last_report
               if last_report else
               "No public blocker explanation was recorded; review the "
               "preserved native thread and workspace before resuming.")
        )
    else:
        reason = f"Investigator Native Goal returned {goal_status} before scientific handoff."
    return {
        "code": ("investigator_native_goal_unexpected_blocked" if goal_status == "blocked"
                 else "investigator_native_goal_" + goal_status),
        "goal_status": goal_status, "thread_id": thread_id, "reason": reason,
        "reason_source": "investigator_public_report" if last_report else "sdk_status_only",
    }


class InvocationMixin:
    @staticmethod
    def _thread_key(state: dict[str, Any], role: str) -> str:
        # Role is the stable storage namespace, not a promise that one role owns
        # one conversation for the whole Cycle.  Explicit fresh_thread=True calls
        # create epistemic episode boundaries while reuse remains the default for
        # stages whose reasoning continuity is scientifically useful.
        return role

    def _save_attempt(self, state: dict[str, Any]) -> None:
        attempt = int(state.get("investigation_attempt") or 0)
        if not attempt:
            return
        key = f"attempt-{attempt:04d}"
        record = state["investigation_attempts"][key]
        record["threads"] = {}
        for role in CAPABILITIES:
            stable = role
            legacy = f"{key}:{role}"
            if stable in state["threads"]:
                record["threads"][role] = state["threads"][stable]
            elif legacy in state["threads"]:
                record["threads"][role] = state["threads"][legacy]
        directory = self.cycle_dir / "investigation_attempts" / key
        write_json(directory / "threads.json", record["threads"])
        write_json(directory / "judge_objections.json", record.get("objections", []))
        write_json(directory / "readiness.json", record.get("readiness", {}))
        write_json(directory / "state.json", record)

    def _record_cycle_anomaly(
        self, state: dict[str, Any], *, kind: str, role: str, stage: str,
        invocation_id: str, thread_id: str | None, mechanism: str,
        summary: str, attempt: int | None = None, input_kind: str | None = None,
        error_paths: list[str] | None = None, artifact: str | None = None,
    ) -> None:
        """Persist a minor per-Cycle format/contract anomaly without changing routing."""
        path = self.cycle_dir / INCIDENT_LOG_NAME
        record, created = register_cycle_incident(
            self.cycle_dir,
            severity="minor",
            kind=kind,
            source_event="cycle.minor_anomaly",
            summary=summary,
            discriminator=f"{invocation_id}:{attempt}:{mechanism}",
            fields={
                "mechanism": mechanism,
                "extra_llm_call": mechanism == "sdk_driven_llm_retry",
                "invocation_id": invocation_id,
                "role": role,
                "stage": stage,
                "thread_id": thread_id or None,
                "attempt": attempt,
                "input_kind": input_kind,
                "error_paths": list(error_paths or []),
                "artifact": artifact,
            },
        )
        if created:
            state["minor_anomaly_count"] = int(state.get("minor_anomaly_count") or 0) + 1
        self._emit(state, "cycle.minor_anomaly", {
            "incident_id": record["incident_id"], "incident_key": record["incident_key"],
            "kind": kind, "severity": "minor", "role": role, "stage": stage,
            "invocation_id": invocation_id, "thread_id": thread_id or None,
            "mechanism": mechanism, "extra_llm_call": record["extra_llm_call"],
            "attempt": attempt, "input_kind": input_kind, "summary": record["summary"],
            "artifact": artifact, "anomaly_log": str(path),
        })

    def _contract_repair_failed(self, state: dict[str, Any], *, invocation_id: str, role: str,
                                stage: str, thread_id: str, raw_outputs: list[str],
                                errors: list[dict[str, Any]], repair_prompt: str,
                                invocation_ledger: InvocationLedger,
                                artifact_tracker: InvocationArtifactTracker | None = None) -> None:
        artifact_manifest = artifact_tracker.finalize(status="failed") if artifact_tracker is not None else None
        artifact = self.cycle_dir / "contract_repairs" / f"{invocation_id}.json"
        payload = {
            "invocation_id": invocation_id, "role": role, "stage": stage, "thread_id": thread_id,
            "raw_outputs": raw_outputs, "validation_errors": errors, "repair_prompt": repair_prompt,
            "invocation_artifact_manifest": str(artifact_manifest) if artifact_manifest else None,
            "at": now(),
        }
        write_json(artifact, payload)
        if role == "research_lead" and stage == "hypothesis_display":
            # This optional view has no scientific authority. Keep its failed
            # invocation evidence, then let the caller render the safe fallback.
            invocation_ledger.transition(invocation_id, "failed", role=role, thread_id=thread_id,
                                         error_type="ContractRepairFailed", error="optional display contract repair failed")
            self._emit(state, "hypothesis.display_contract_failed", {
                "role": role, "stage": stage, "artifact": str(artifact), "errors": errors,
            })
            raise ValueError("optional hypothesis display contract repair failed")
        if role == "judge" and stage == "goal_completion" and getattr(self, "_delivery_reassessment_control", None):
            invocation_ledger.transition(invocation_id, "failed", role=role, thread_id=thread_id,
                                         error_type="ContractRepairFailed", error="delivery reassessment contract repair failed")
            self._emit(state, "contract.repair_failed", {"role": role, "stage": stage, "thread_id": thread_id,
                                                         "artifact": str(artifact), "errors": errors})
            state["active_invocation_id"] = None
            raise RecoveryStop("delivery reassessment contract repair failed")
        state.update({
            "status": "needs_attention",
            "phase": f"{role}:{stage}:contract_repair_failed", "closure_reason": "contract_repair_failed",
            "protocol_health": "violation", "resumable": True,
            "human_attention": f"Contract repair failed for {role}:{stage}; inspect the recovery artifact and repair the stage output before resuming.",
            "attention": {
                "code": "contract_repair_failed",
                "reason": f"The {role}:{stage} output still violated its contract after the bounded repair attempt.",
                "requested_action": "Inspect the recorded contract-repair artifact, correct the preparation or contract mismatch, then resume this Cycle.",
                "resume_behavior": "Resume continues from the persisted recovery point after the contract issue is addressed.",
            },
            "active_invocation_id": None,
            "recovery": {"action": "contract_repair_failed", "invocation_id": invocation_id,
                         "role": role, "stage": stage, "thread_id": thread_id,
                         "artifact": str(artifact), "repair_prompt": repair_prompt, "at": now()},
        })
        invocation_ledger.transition(invocation_id, "failed", role=role, thread_id=thread_id,
                                     error_type="ContractRepairFailed", error="contract repair failed")
        self._emit(state, "contract.repair_failed", {"role": role, "stage": stage, "thread_id": thread_id,
                                                      "artifact": str(artifact), "errors": errors})
        self._persist(state, project=True)
        raise RecoveryStop("contract repair failed")

    @staticmethod
    def _invocation_logical_key(
        state: dict[str, Any], role: str, actual: str, stage: str,
        context: dict[str, Any], output_key: str | None,
    ) -> str:
        """Identify one workflow consumption slot independently of mutable projections."""
        literature_task = context.get("literature_task") if isinstance(context.get("literature_task"), dict) else {}
        branch = context.get("hypothesis_lock") or context.get("branch")
        branch = branch if isinstance(branch, dict) else {}
        candidates = context.get("candidates") if isinstance(context.get("candidates"), list) else []
        candidate_ids = [
            str(item.get("id") or item.get("candidate_id") or "")
            for item in candidates if isinstance(item, dict)
        ]
        basis = {
            "cycle_id": str(state.get("cycle_id") or ""),
            "role": role,
            "actual_role": actual,
            "stage": stage,
            "output_key": output_key or role,
            "mode": context.get("mode"),
            "phase": context.get("phase"),
            "investigation_attempt": context.get("investigation_attempt", state.get("investigation_attempt")),
            "round": context.get("round"),
            "judge_rework_count": state.get("judge_rework_count"),
            "qualification_repair_count": state.get("qualification_repair_count"),
            "qualification_restart_generation": state.get(
                "qualification_restart_generation", len(state.get("qualification_resume_history") or []),
            ),
            "branch_id": branch.get("candidate_id") or branch.get("id"),
            "candidate_ids": candidate_ids,
            "repair_hash": context.get("repair_hash") if stage == "repair_verification" else None,
            "repair_review_protocol_version": context.get("repair_review_protocol_version") if stage == "repair_verification" else None,
            "literature_gap_id": literature_task.get("source_gap_id") or literature_task.get("gap_id"),
        }
        return stable_hash(basis)

    def _reuse_committed_invocation(
        self, state: dict[str, Any], *, invocation_ledger: InvocationLedger,
        logical_key: str, contract_ref: dict[str, Any], role: str, actual: str,
        stage: str, output_key: str | None, context: dict[str, Any] | None = None,
        expected_goal_closure: tuple[str, str] | None = None,
    ) -> dict[str, Any] | None:
        snapshot = invocation_ledger.find_reusable(
            logical_key=logical_key, contract_ref=contract_ref, role=role, stage=stage,
        )
        if snapshot is None:
            return None
        scientific_review = role == "challenger" and stage in {"candidate_review", "repair_verification"}
        invocation_id = str(snapshot.get("invocation_id") or "")
        expected_path = self.cycle_dir / "semantic_closure" / f"{invocation_id}.json"
        expected_hash = str(snapshot.get("closure_hash") or "")
        if expected_goal_closure is not None and (invocation_id, expected_hash) != expected_goal_closure:
            raise RecoveryStop("The retained Goal Judge closure changed before reuse")
        if not invocation_id or not expected_hash or not expected_path.is_file():
            if scientific_review:
                raise RecoveryStop("scientific review closure unavailable; explicit recovery required")
            return None
        try:
            closure = json.loads(expected_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if scientific_review:
                raise RecoveryStop("scientific review closure unreadable; explicit recovery required")
            return None
        if not isinstance(closure, dict) or stable_hash(closure) != expected_hash:
            if scientific_review:
                raise RecoveryStop("scientific review closure integrity changed; explicit recovery required")
            return None
        if closure.get("role") != role or closure.get("stage") != stage:
            if scientific_review:
                raise RecoveryStop("scientific review closure identity changed; explicit recovery required")
            return None
        rejected = [
            item for item in (closure.get("evidence_registration_audit") or [])
            if isinstance(item, dict) and item.get("status") == "all_artifacts_rejected"
        ]
        if rejected:
            evidence_ids = ", ".join(str(item.get("evidence_id")) for item in rejected)
            message = (
                "Evidence artifact protocol violation: every submitted artifact pointer was rejected "
                f"for evidence {evidence_ids}; registration audit is saved in {expected_path}"
            )
            # Historical builds could mark this closure committed before performing
            # artifact validation. Quarantine it on first recovery instead of
            # repeatedly selecting the same invalid reusable invocation.
            invocation_ledger.transition(
                invocation_id, "quarantined", role=role, stage=stage,
                commit_state="not_committed", closure_artifact=str(expected_path),
                closure_hash=expected_hash, logical_key=logical_key,
                reconciliation="rejected_closure_quarantined",
                error_type="EvidenceArtifactProtocolViolation", error=message,
            )
            return None
        output = closure.get("closed_output")
        if not isinstance(output, dict):
            if scientific_review:
                raise RecoveryStop("scientific review result unavailable; explicit recovery required")
            return None
        if role == "challenger" and stage in {"candidate_review", "repair_verification"}:
            # Do not rebind old results to a new body or silently spend a fresh
            # model call when legacy closures lack this protocol's binding.
            output = _bind_review_output(output, context or {}, stage, verify=True)
        agent_output = closure.get("agent_output")
        if isinstance(agent_output, dict):
            state.setdefault("agent_outputs", {})[output_key or role] = agent_output
        state.setdefault("role_outputs", {})[output_key or role] = output
        state["last_invocation_id"] = invocation_id
        state["active_invocation_id"] = None
        state["active_role"] = role
        state["phase"] = f"{role}:completed"
        thread_id = snapshot.get("thread_id")
        if thread_id:
            state.setdefault("threads", {})[self._thread_key(state, actual)] = thread_id
        state["invocation_reuse_count"] = int(state.get("invocation_reuse_count") or 0) + 1
        if snapshot.get("state") == "completed":
            invocation_ledger.transition(
                invocation_id, "committed", role=role, stage=stage,
                commit_state="committed", closure_artifact=str(expected_path),
                closure_hash=expected_hash, logical_key=logical_key,
                reconciliation="validated_closure_reuse",
            )
        self._emit(state, "invocation.reused", {
            "invocation_id": invocation_id, "role": role, "stage": stage,
            "logical_key": logical_key, "semantic_closure": str(expected_path),
        })
        self._persist(state)
        return json.loads(json.dumps(output))

    def _agent_graph_for_stage(
        self, role: str, stage: str, context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Build role-specific Agent context from canonical SQLite, never its Canvas projection."""
        supported = {
            ("research_lead", "frontier_proposal"),
            ("research_lead", "frontier_maintenance"),
            ("challenger", "candidate_review"),
            ("challenger", "repair_verification"),
            ("judge", "goal_completion"),
        }
        if (role, stage) not in supported:
            return None
        if (role, stage) == ("judge", "goal_completion"):
            from .goal_snapshot import GoalAuthoritySnapshot
            snapshot = context.get("goal_authority_snapshot")
            if isinstance(snapshot, GoalAuthoritySnapshot):
                snapshot.validate(compare_sources=False)
                return snapshot.agent_view()
        resolver = getattr(self, "_graph_database_path", None)
        if not callable(resolver):
            raise RuntimeError("SQLite graph authority resolver is unavailable")
        graph_path = resolver()
        if graph_path is None:
            raise RuntimeError("SQLite graph database path is not configured for this Stage")
        if not Path(graph_path).is_file():
            raise FileNotFoundError(f"Canonical SQLite graph is required: {graph_path}")

        from .science_graph_store import ScienceGraphStore
        with ScienceGraphStore(graph_path, readonly=True) as store:
            if role == "research_lead":
                entry = str(
                    context.get("entry_hypothesis_id")
                    or getattr(getattr(self, "spec", None), "branch_anchor_id", "")
                    or ""
                ).strip()
                return store.agent_view(entry_hypothesis_id=entry or None)
            if role == "challenger":
                candidates = context.get("candidates")
                if not isinstance(candidates, list):
                    candidates = context.get("candidate")
                return store.agent_candidate_review_view(
                    candidates if isinstance(candidates, list) else []
                )
            if role == "judge":
                return store.agent_goal_completion_view()

    def _call(self, state: dict[str, Any], role: str, context: dict[str, Any], resume_role: str | None=None, *, fresh_thread: bool=False, contract_role: str | None=None, output_key: str | None=None,
              delivery_recovery_pin: dict | None = None) -> dict[str, Any]:
        self._check_recovery_control(state)
        actual = resume_role or role
        stage = stage_for(role, state, context, contract_role=contract_role)
        contract = role_contract(role, phase=stage)
        invocation_ledger = InvocationLedger(self.cycle_dir / 'invocations.jsonl')
        logical_key = self._invocation_logical_key(state, role, actual, stage, context, output_key)
        expected_goal_closure = None
        if delivery_recovery_pin is not None:
            from .goal_reassessment_recovery import goal_reassessment_invocation_pin, goal_reassessment_output_key
            from .goal_snapshot import GoalAuthoritySnapshot

            pin = delivery_recovery_pin
            snapshot = context.get("goal_authority_snapshot")
            if (role != "judge" or actual != role or stage != "goal_completion" or not fresh_thread
                    or not isinstance(snapshot, GoalAuthoritySnapshot)
                    or self.cycle_dir.name != pin.get("anchor_cycle")
                    or str(snapshot.manifest_path) != pin.get("snapshot_manifest")
                    or snapshot.manifest_sha256 != pin.get("snapshot_manifest_sha256")
                    or output_key != goal_reassessment_output_key(pin.get("delivery_revision"), snapshot.manifest_sha256)):
                raise RecoveryStop("Recovery did not reach the retained Goal Judge slot")
            slot = goal_reassessment_invocation_pin(self.cycle_dir, state, logical_key, contract)
            if slot is None or any(pin.get(key) != value for key, value in slot.items()):
                raise RecoveryStop("The retained Goal Judge invocation changed before recovery")
            if slot["mode"] == "reuse_closure":
                expected_goal_closure = (slot["invocation_id"], slot["closure_hash"])
        reused = None
        if delivery_recovery_pin is None or expected_goal_closure is not None:
            reused = self._reuse_committed_invocation(
                state, invocation_ledger=invocation_ledger, logical_key=logical_key,
                contract_ref=contract.descriptor(), role=role, actual=actual, stage=stage,
                output_key=output_key, context=context, expected_goal_closure=expected_goal_closure,
            )
        if expected_goal_closure is not None and reused is None:
            raise RecoveryStop("No verified original Goal Judge result remains available for reuse")
        if reused is not None:
            self._finish_skill_memory(state, actual, str(state.get('last_invocation_id') or ''))
            return reused
        from .boot_native_goal import pending_boot_native_goal

        if pin := pending_boot_native_goal(self.backend):
            if (role != "investigator" or actual != role or fresh_thread or context.get("submission_delivery")
                    or state.get("active_invocation_id") != pin["invocation_id"]
                    or stage != pin["stage"] or logical_key != pin["logical_key"]
                    or contract.descriptor() != pin["contract_ref"]):
                from .native_goal_recovery import NativeGoalRecoveryConflict
                raise NativeGoalRecoveryConflict("Boot Resume did not reach the original Investigator slot")
        from .goal_terminal import GoalSealedError, assert_goal_open
        try:
            graph = self._graph_database_path()
        except (RuntimeError, FileNotFoundError, OSError):
            graph = None
        if graph is not None:
            try:
                assert_goal_open(self.spec.research_root, graph, spec=self.spec)
            except GoalSealedError:
                self._mark_stopped_for_goal(state)
                raise
        if getattr(self, '_proposal_gate_token', None) and role in {'research_lead', 'challenger'}:
            self._proposal_gate().assert_holder(self._proposal_gate_token)
        from .invocation_execution import InvocationSession
        return InvocationSession(self, state, role, context, resume_role, fresh_thread, contract_role, output_key, actual, stage, contract, invocation_ledger, logical_key).run()

    def _skill_memory_note_path(self, role: str, invocation_id: str) -> Path:
        workspace = self.cycle_workspace / "literature" if role == "literature" else self.cycle_workspace
        return workspace / f".skill-note-{invocation_id}.json"

    def _finish_skill_memory(self, state: dict[str, Any], role: str, invocation_id: str,
                             *, binding: dict[str, Any] | None = None) -> None:
        from .skill_evolution.runtime import safely
        workspace = self.cycle_workspace / "literature" if role == "literature" else self.cycle_workspace
        safely("finish", root=self.spec.research_root, cycle_dir=self.cycle_dir,
               workspace=workspace, role=role, invocation_id=invocation_id,
               note_path=self._skill_memory_note_path(role, invocation_id), binding=binding,
               config_path=self.spec.config_path)
