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
from .output_validation import validate_agent_output
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
from .native_goal import NativeGoalIncomplete, NativeGoalBudgetExceeded, NativeGoalDeliveryError
from .investigator_budget import budget_status, BUDGET_ATTENTION_CODE
from .provider_errors import ProviderQuotaExhausted
from .scientific_objective import frozen_science_view, review_binding, require_review_binding


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
            "qualification_restart_generation": len(state.get("qualification_resume_history") or []),
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

    def _call(self, state: dict[str, Any], role: str, context: dict[str, Any], resume_role: str | None = None, *,
              fresh_thread: bool = False, contract_role: str | None = None,
              output_key: str | None = None) -> dict[str, Any]:
        self._check_recovery_control(state)
        actual = resume_role or role
        stage = stage_for(role, state, context, contract_role=contract_role)
        contract = role_contract(role, phase=stage)
        invocation_ledger = InvocationLedger(self.cycle_dir / "invocations.jsonl")
        logical_key = self._invocation_logical_key(state, role, actual, stage, context, output_key)
        reused = self._reuse_committed_invocation(
            state, invocation_ledger=invocation_ledger, logical_key=logical_key,
            contract_ref=contract.descriptor(), role=role, actual=actual, stage=stage,
            output_key=output_key, context=context,
        )
        if reused is not None:
            return reused
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
        if getattr(self, "_proposal_gate_token", None) and role in {"research_lead", "challenger"}:
            self._proposal_gate().assert_holder(self._proposal_gate_token)
        threads = state.setdefault("threads", {})
        thread_providers = state.get("runtime_thread_providers")
        if not isinstance(thread_providers, dict):
            thread_providers = {}
            state["runtime_thread_providers"] = thread_providers
        thread_key = self._thread_key(state, actual)
        if fresh_thread:
            old = threads.pop(thread_key, None)
            thread_providers.pop(thread_key, None)
            drop = getattr(self.backend, "drop_thread", None)
            if callable(drop):
                drop(old)
            thread_id = None
        else:
            thread_id = threads.get(thread_key)
        # This exact point is a serialized safe boundary: no Native Goal/Turn
        # from the previous invocation is running, and reused closures have
        # already returned without a new provider call.
        self._apply_pending_task_route(state, actual, thread_id)
        route = self.routes[actual]
        provider_failover = state.get("runtime_provider_failover")
        frozen_routes = state.get("runtime_role_routes")
        frozen_route = frozen_routes.get(actual) if isinstance(frozen_routes, dict) else None
        known_thread_provider = thread_providers.get(thread_key)
        if known_thread_provider is None and isinstance(frozen_route, dict):
            known_thread_provider = frozen_route.get("provider")
        if thread_id and isinstance(known_thread_provider, str) and known_thread_provider != route.provider:
            # Provider-owned thread IDs cannot be resumed under a different provider.
            threads.pop(thread_key, None)
            thread_providers.pop(thread_key, None)
            drop = getattr(self.backend, "drop_thread", None)
            if callable(drop):
                drop(thread_id)
            thread_id = None
            if isinstance(provider_failover, dict):
                fresh_roles = provider_failover.setdefault("fresh_thread_roles", [])
                if isinstance(fresh_roles, list) and actual not in fresh_roles:
                    fresh_roles.append(actual)
        lead_new_cycle = False
        # Decide whether a persistent Lead thread must roll over before building
        # the prompt.  Previously this check happened after the prompt was built,
        # so a turn could be constructed as a continuation and then sent to a new
        # thread without its required initialization.
        if actual == "research_lead" and not fresh_thread:
            persisted = self._load_research_lead_state()
            current_cycle_id = str(self.cycle_dir.name if self.cycle_dir else "")
            # Once a Lead thread has been used in this Cycle it is pinned until
            # the Cycle closes. Context-budget/provider/branch rollover is a
            # between-Cycle policy; rolling mid-Cycle creates a second dialog and
            # destroys the continuity that candidate selection relies on.
            same_cycle_thread = bool(thread_id) and str(persisted.get("last_cycle_id") or "") == current_cycle_id
            rollover_reason = None if same_cycle_thread else self._research_lead_rollover_reason(state, persisted, route)
            if rollover_reason:
                old = threads.pop(actual, None)
                drop = getattr(self.backend, "drop_thread", None)
                if callable(drop):
                    drop(old or persisted.get("thread_id"))
                thread_id = None
                state["research_lead_rollover_reason"] = rollover_reason
                # The new thread starts with an empty context budget.  Do not carry
                # the old thread's estimate into the next turn.
                state["research_lead_context_chars"] = 0
                history = list(persisted.get("rollover_history") or [])
                history.append({"cycle_id": self.cycle_dir.name if self.cycle_dir else "", "reason": rollover_reason, "at": now()})
                state["research_lead_rollover_history"] = history[-20:]
            # A persisted Research Lead record is rollover/audit metadata only.
            # Never restore its thread into a new Cycle: the live conversation
            # namespace is the current Cycle's state["threads"] exclusively.
            # This preserves one conversation per role within a Cycle while
            # preventing context leakage between Cycles.
        continuation = bool(thread_id) and not lead_new_cycle
        global_context = {"goal": self.spec.goal, "research_contract": self.spec.contract,
                          "final_gate": self.spec.final_gate,
                          "required_context": self.spec.required_context}
        if role == "judge":
            # Judge policy lives in the Stage Card; Context carries only the
            # frozen scientific object and current adjudication evidence.
            global_context = {"hypothesis_lock": state.get("hypothesis_lock", {})}
        # The complete validation context remains in runtime memory. Lead frontier
        # stages receive a compact SDK view from canonical SQLite; Canvas is only a projection.
        validation_context = {
            **global_context,
            **self._path_anchors(),
            **context,
        }
        agent_graph = self._agent_graph_for_stage(actual, stage, validation_context)
        if actual == "research_lead" and stage in {"frontier_proposal", "frontier_maintenance"}:
            columns = agent_graph.get("hypothesis_columns") if isinstance(agent_graph, dict) else None
            rows = agent_graph.get("hypotheses") if isinstance(agent_graph, dict) else None
            if not isinstance(columns, list) or "id" not in columns or not isinstance(rows, list):
                raise RuntimeError("canonical SQLite graph omitted H identity index for Research Lead")
            id_index = columns.index("id")
            validation_context["_known_hypothesis_ids"] = [
                row[id_index] for row in rows
                if isinstance(row, (list, tuple)) and id_index < len(row)
                and isinstance(row[id_index], str) and row[id_index]
            ]
        lead_graph_stage = actual == "research_lead" and stage in {
            "frontier_proposal", "frontier_maintenance",
        }
        if lead_graph_stage and agent_graph is not None:
            graph_path = self._graph_database_path()
            if not Path(graph_path).is_file():
                raise FileNotFoundError(
                    f"Canonical SQLite graph is required for Research Lead: {graph_path}"
                )
            from .science_graph_tools import (
                LEAD_MAINTENANCE_OPERATIONS, LEAD_PROPOSAL_OPERATIONS, tool_context,
            )
            if stage == "frontier_proposal":
                operations = LEAD_PROPOSAL_OPERATIONS
            else:
                operations = LEAD_MAINTENANCE_OPERATIONS
            validation_context["science_graph_tools"] = tool_context(graph_path, operations)
        if actual == "challenger" and stage in {"candidate_review", "repair_verification"} and agent_graph is not None:
            graph_path = self._graph_database_path()
            if not Path(graph_path).is_file():
                raise FileNotFoundError(
                    f"Canonical SQLite graph is required for Challenger: {graph_path}"
                )
            from .science_graph_tools import CHALLENGER_REVIEW_OPERATIONS, tool_context
            validation_context["science_graph_tools"] = tool_context(
                graph_path, CHALLENGER_REVIEW_OPERATIONS,
            )
        graph_stamp = None
        if agent_graph is not None and lead_graph_stage:
            graph_path = self._graph_database_path()
            if graph_path is not None:
                graph_file = Path(graph_path)
                graph_stat = graph_file.stat()
                wal_file = Path(str(graph_file) + "-wal")
                wal_stat = wal_file.stat() if wal_file.is_file() else None
                graph_stamp = (
                    f"{self.cycle_dir.name if self.cycle_dir else ''}:{stage}:"
                    f"{agent_graph.get('projection_version', 'legacy')}:"
                    f"{agent_graph.get('entry_hypothesis_id') or ''}:"
                    f"{graph_stat.st_mtime_ns}:{graph_stat.st_size}:"
                    f"{wal_stat.st_mtime_ns if wal_stat else 0}:{wal_stat.st_size if wal_stat else 0}"
                )
            reuse_graph = bool(
                continuation and graph_stamp
                and state.get("research_lead_agent_graph_stamp") == graph_stamp
            )
            if reuse_graph:
                # Do not also inject the newly rebuilt full view: continuation should
                # reuse the retained graph from this same Cycle/Stage context.
                validation_context.pop("agent_graph", None)
                validation_context["agent_graph_reuse"] = {
                    "schema": agent_graph.get("schema"),
                    "entry_hypothesis_id": agent_graph.get("entry_hypothesis_id"),
                    "projection_version": agent_graph.get("projection_version"),
                }
            else:
                validation_context["agent_graph"] = agent_graph
        elif agent_graph is not None:
            # Other roles receive their purpose-built SQLite view on each invocation;
            # Lead-only graph reuse stamps must never replace these richer views.
            validation_context["agent_graph"] = agent_graph
        # stage_context is the authoritative stage-specific projection. Do not
        # apply generic character/list clipping after it: that can turn a complete
        # hypothesis, decisive test, criterion, blocker, or finding into a partial
        # scientific statement and change the downstream judgment.
        initial_context = stage_context(role, stage, validation_context)
        continuation_context = stage_context(role, stage, validation_context, continuation=True) if continuation else None
        guidance_view = None
        pending_guidance = state.get("operator_guidance") if state.get("operator_guidance_pending") and isinstance(state.get("operator_guidance"), dict) else None
        if pending_guidance is not None:
            guidance_view = {
                "text": pending_guidance.get("text"),
                "source": pending_guidance.get("source"),
                "attention_code": pending_guidance.get("attention_code"),
                "rule": "This is one-shot operator guidance for the next resumed Agent turn. Follow it within the existing scientific and role contracts; do not treat it as established scientific evidence.",
            }
            initial_context["operator_guidance"] = guidance_view
            if continuation_context is not None:
                continuation_context["operator_guidance"] = guidance_view
        try:
            identifier = self.backend.ensure_thread(actual, thread_id, route.model, route.provider)
            if continuation and identifier != thread_id:
                continuation = False
                state.setdefault("recovery", {})["mode"] = "fresh_thread_fallback"
        except Exception:
            persistent_rl = actual == "research_lead" and bool(thread_id)
            if (not state.get("recovery", {}).get("allow_thread_fallback") and not persistent_rl) or not thread_id:
                raise
            state.setdefault("recovery", {})["mode"] = "fresh_thread_fallback"
            identifier = self.backend.ensure_thread(actual, None, route.model, route.provider)
            continuation = False
        # If thread creation fell back after we tentatively reused an earlier graph,
        # this is a fresh conversation and must receive the full current SQLite view.
        if not continuation and agent_graph is not None and "agent_graph_reuse" in validation_context:
            validation_context.pop("agent_graph_reuse", None)
            validation_context["agent_graph"] = agent_graph
            initial_context = stage_context(role, stage, validation_context)
            continuation_context = None
            if guidance_view is not None:
                initial_context["operator_guidance"] = guidance_view
        prompt_context = continuation_context if continuation else initial_context
        # Preflight Native Goal BEFORE starting the ledger, marking the role
        # running or paying for the setup Turn. Large scientific locks are
        # referenced by hash from the FULL setup Context, never truncated.
        native_goal_stage = role == "investigator" and stage in {
            "investigate", "judge_rework", "integrate_literature"
        }
        fake_backend = any(
            cls.__name__ in {"FakeBackend", "CanonicalFakeBackend"}
            for cls in type(self.backend).__mro__
        )
        native_preflight_goal = None
        if native_goal_stage and not fake_backend and callable(getattr(self.backend, "invoke_goal", None)):
            from .native_goal import investigator_goal
            native_preflight_goal = investigator_goal(
                stage, prompt_context, layout="v2", allow_setup_reference=True,
            )
        invocation_id = f"inv-{self.cycle_dir.name}-{role}-{uuid.uuid4().hex[:12]}"
        predecessor_id = str(state.get("active_invocation_id") or "") if state.get("recovery", {}).get("action") == "resume" else ""
        predecessor = invocation_ledger.latest(predecessor_id) if predecessor_id else None
        if predecessor and predecessor.get("state") not in {"completed", "failed", "cancelled", "quarantined", "interrupted", "superseded"}:
            invocation_ledger.transition(predecessor_id, "interrupted", successor_invocation_id=invocation_id,
                                         reason="process recovery before a completed invocation")
        invocation_ledger.prepare(
            invocation_id=invocation_id, kind="agent", input_payload=prompt_context,
            contract_ref=contract.descriptor(), predecessor_invocation_id=predecessor_id or None,
            logical_key=logical_key, role=role, stage=stage,
        )
        state["active_invocation_id"] = invocation_id
        artifact_tracker = (
            InvocationArtifactTracker.start(
                cycle_dir=self.cycle_dir, workspace=self.cycle_workspace, invocation_id=invocation_id,
                role=role, stage=stage,
            )
            if actual in WRITE_ROLES else None
        )
        if native_preflight_goal is not None:
            # For real Investigator Native Goal, there is NO ordinary Stage
            # prompt. Do not construct/cost/log that obsolete instruction tree.
            prompt = "[Investigator Native Goal; actual Setup and Goal logged separately]"
        else:
            new_thread_prompt = build_initial_prompt(
                prefix=thread_cycle_prefix(self.cycle_dir.name, role), role=role, stage=stage,
                contract_ref=contract.ref, contract_schema=contract.prompt_projection(initial_context),
                context=initial_context, write_rule=write_rule(actual),
            )
            prompt = build_continuation_prompt(
                role=role, stage=stage, contract_ref=contract.ref,
                contract_schema=contract.prompt_projection(prompt_context), context=prompt_context,
            ) if continuation else new_thread_prompt
        if native_preflight_goal is None and state.get("recovery", {}).get("action") == "resume":
            if (state.get("recovery", {}).get("qualification_restart")
                    and actual == "research_lead" and stage == "frontier_proposal"):
                prompt += ("\n\nThe previous qualification attempt ended without a qualified candidate. "
                           "Use qualification_feedback in this turn's Context to address the recorded "
                           "Challenger reasons. Re-enter qualification from Research Lead: reconsider the current "
                           "preparation and produce a fresh frontier before Challenger review. Do not treat the previous frontier "
                           "or its reviews as current qualification output.")
            else:
                prompt += ("\n\nThe previous TheoryMaster execution was interrupted. Resume the same assigned scientific task; "
                           "do not restart completed workflow stages or change the Main Hypothesis. Inspect the "
                           "existing workspace and continue from the last consistent state.")
        state.setdefault("prompt_metrics", []).append({
            "invocation_id": invocation_id, "role": role, "stage": stage,
            "prompt_chars": len(prompt) if native_preflight_goal is None else 0,
            "context_chars": len(json.dumps(prompt_context, ensure_ascii=False, default=str)),
            "contract_chars": len(contract.prompt_projection(prompt_context)),
            "continuation": continuation,
            **({
                "native_goal_chars": len(native_preflight_goal),
                "native_goal_mode": (
                    "setup_context_sha256_reference"
                    if "FROZEN_OBJECTIVE_CONTEXT_SHA256=" in native_preflight_goal
                    else "full_immutable_science"
                ),
                "native_goal_safety_budget_chars": 3800,
                "native_goal_provider_limit_chars": 4000,
            } if native_preflight_goal is not None else {}),
        })
        state["threads"][thread_key] = identifier
        thread_providers[thread_key] = route.provider
        if actual in CAPABILITIES and state.get("investigation_attempt"):
            # Stable key drives reuse; attempt-scoped alias preserves the exact
            # thread used by each audit attempt for old readers and handoff files.
            state["threads"][f"attempt-{int(state['investigation_attempt']):04d}:{actual}"] = identifier
            thread_providers[f"attempt-{int(state['investigation_attempt']):04d}:{actual}"] = route.provider
        if actual in CAPABILITIES and state.get("investigation_attempt"):
            self._save_attempt(state)
        state.update({"status": "active", "phase": f"{role}:running"})
        self._emit(state, "role.started", {
            "role": role,
            "invocation_id": invocation_id,
            "stage": stage,
            "prompt_chars": len(prompt),
            "contract_id": contract.contract_id,
            "contract_version": contract.version,
            "contract_schema_hash": contract.schema_hash,
            "thread_id": identifier,
            "resume_thread_id": thread_id,
            "investigation_attempt": state.get("investigation_attempt", 0),
            "investigation_round_count": state.get("investigation_round_count", 0) + (1 if actual in CAPABILITIES and state.get("investigation_attempt") else 0),
            "total_investigation_round_count": state.get("total_investigation_round_count", 0) + (1 if actual in CAPABILITIES and state.get("investigation_attempt") else 0),
            "thread_namespace": thread_key,
            "identities": [path.name for path in identity_files(role)],
            "skills": [ref.directory for ref in role_skills(actual, task_config_path=self.spec.config_path)],
            "skill_scopes": {ref.directory: ref.scope for ref in role_skills(actual, task_config_path=self.spec.config_path)},
            **({
                "literature_task_type": (validation_context.get("literature_task") or {}).get("task_type"),
                "literature_triggered_by": (validation_context.get("literature_task") or {}).get("triggered_by"),
                "literature_gap_id": (validation_context.get("literature_task") or {}).get("gap_id"),
                "literature_source_gap_id": (validation_context.get("literature_task") or {}).get("source_gap_id"),
                "literature_objection_id": (validation_context.get("literature_task") or {}).get("objection_id"),
            } if role == "literature" and isinstance(validation_context.get("literature_task"), dict) else {}),
        })
        invocation_ledger.transition(invocation_id, "running", role=role, thread_id=identifier,
                                     execution_mode="thread_continuation" if thread_id and identifier == thread_id else "new_thread",
                                     predecessor_invocation_id=predecessor_id or None)
        self._persist(state)

        # Native Goal is the normal Codex Investigator execution contract. Test
        # backends intentionally retain their deterministic one-turn behavior.
        investigator_goal_stage = role == "investigator" and stage in {
            "investigate", "judge_rework", "integrate_literature"
        }
        deterministic_backend = any(
            cls.__name__ in {"FakeBackend", "CanonicalFakeBackend"}
            for cls in type(self.backend).__mro__
        )
        use_native_goal = investigator_goal_stage and not deterministic_backend
        if use_native_goal and not callable(getattr(self.backend, "invoke_goal", None)):
            raise RuntimeError("real Investigator backend does not support required native Goal execution")
        if use_native_goal:
            if native_preflight_goal is None:
                raise RuntimeError("missing Investigator Native Goal preflight")
            native_objective = native_preflight_goal
            # The tiny file handoff is an administrative SDK interface, not a
            # new scientific objective or an additional post-Goal model call.
            handoff_path = (self.cycle_dir / "workspace" / ".tm-handoff"
                            / f"{invocation_id}.json").resolve()
            handoff_path.parent.mkdir(parents=True, exist_ok=True)
            if handoff_path.exists():
                raise RuntimeError("refusing to reuse an earlier Investigator handoff")
            setup_context = (
                json.dumps(prompt_context, ensure_ascii=False, default=str)
                + "\n\nSDK DELIVERY INTERFACE (not scientific evaluation criteria): "
                + f"Only after completing the frozen science, write {handoff_path} "
                + "as JSON following the current output contract:\n"
                + contract.prompt_projection(prompt_context)
                + "\nThe handoff also accepts claim as an alias for statement, or simple strings in findings. "
                + "Only a complete self-contained statement is required for each Finding; method, result and provenance "
                + "are optional authored content. Omit unused optional fields; do not invent placeholder values. "
                + "Choose the count and content yourself; preserve distinct scientific claims. "
                + "Put detailed proofs, scripts, numbers and other material in arbitrary research files, "
                + "but make each submitted claim scientifically self-contained. "
                + "Do not submit a handoff while blocked, paused or unfinished."
            )
        if use_native_goal:
            from .native_goal import native_goal_setup_turn
            state["prompt_metrics"][-1]["native_setup_turn_chars"] = len(
                native_goal_setup_turn(setup_context, native_objective)
            )

        def invoke_with_audit(prompt_text: str, input_kind: str) -> tuple[str, str]:
            if input_kind not in {"task", "continuation"}:
                state["prompt_metrics"][-1].setdefault("repair_turns", []).append({
                    "input_kind": input_kind, "prompt_chars": len(prompt_text),
                })
            if use_native_goal and input_kind not in {"task", "continuation"}:
                # Never start an additional ordinary LLM repair Turn after a
                # Native Goal has already produced its scientific handoff.
                self._contract_repair_failed(
                    state, invocation_id=invocation_id, role=role, stage=stage,
                    thread_id=identifier, raw_outputs=[],
                    errors=[{"path": "/", "code": "native_goal_delivery_invalid",
                             "message": "Native Goal output requires operator attention; "
                                        "a separate LLM repair Turn is prohibited."}],
                    repair_prompt="", invocation_ledger=invocation_ledger,
                    artifact_tracker=artifact_tracker,
                )
            if self.collector is not None:
                record_input = getattr(self.collector, "on_agent_input", None)
                if callable(record_input):
                    if use_native_goal:
                        from .native_goal import native_goal_setup_turn
                        record_input(
                            role=role, stage=stage,
                            content=native_goal_setup_turn(setup_context, native_objective),
                            input_kind="native_goal_setup_turn",
                        )
                        record_input(
                            role=role, stage=stage, content=native_objective,
                            input_kind="native_goal_objective",
                        )
                    else:
                        record_input(
                            role=role, stage=stage, content=prompt_text,
                            input_kind=input_kind,
                        )
            if use_native_goal and input_kind in {"task", "continuation"}:
                goal_backend = getattr(self.backend, "invoke_goal", None)
                if not callable(goal_backend):
                    raise RuntimeError("native Investigator Goal requires Goal-capable backend")
                live_budget = budget_status(self.cycle_dir, self.spec.research_root, state)
                return goal_backend(
                    role, goal_objective=native_objective,
                    context_prompt=setup_context, handoff_path=str(handoff_path),
                    thread_id=identifier, model=route.model, provider=route.provider,
                    token_allowance=live_budget["allowance_tokens"],
                    tokens_before_goal=live_budget["observed_tokens"],
                )
            return self.backend.invoke(role, prompt_text, identifier, route.model, route.provider)

        def normalize_with_audit(raw_text: str, input_kind: str) -> dict[str, Any]:
            try:
                result = normalize_agent_output(raw_text, contract.prompt_schema)
            except OutputNormalizationError as exc:
                self._emit(state, "json.normalization_failed", {
                    "role": role, "stage": stage, "input_kind": input_kind,
                    "classification": exc.classification, "error": str(exc),
                })
                raise
            if role == "investigator" and stage == "investigate":
                normalized, compatibility_repairs = _normalize_legacy_investigator_artifact_fields(result.value)
                if compatibility_repairs:
                    result = NormalizationResult(
                        value=normalized,
                        canonical_json=json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=False),
                        classification=(
                            "syntax_and_compatibility_normalization" if result.repairs
                            else "compatibility_normalization"
                        ),
                        repairs=tuple(result.repairs) + tuple(compatibility_repairs),
                        original_error=result.original_error,
                    )
                    self._emit(state, "agent.compatibility_normalized", {
                        "role": role, "stage": stage,
                        "repairs": compatibility_repairs,
                    })
            if result.repairs:
                state["json_normalization_count"] = int(state.get("json_normalization_count") or 0) + 1
                sequence = int(state["json_normalization_count"])
                artifact = self.cycle_dir / "output_normalizations" / f"{invocation_id}-{sequence:02d}-{input_kind}.json"
                payload = {
                    "invocation_id": invocation_id, "role": role, "stage": stage,
                    "input_kind": input_kind, "classification": result.classification,
                    "original_error": result.original_error,
                    "repairs": list(result.repairs),
                    "raw_output": raw_text,
                    "canonical_output": result.canonical_json,
                    "at": now(),
                }
                write_json(artifact, payload)
                self._emit(state, "json.normalized", {
                    "role": role, "stage": stage, "input_kind": input_kind,
                    "classification": result.classification,
                    "repair_count": len(result.repairs),
                    "repair_kinds": [str(item.get("kind") or "") for item in result.repairs],
                    "artifact": str(artifact),
                })
                self._record_cycle_anomaly(
                    state, kind="json_normalization", role=role, stage=stage,
                    invocation_id=invocation_id, thread_id=identifier,
                    mechanism="deterministic_normalization",
                    summary=result.original_error or ", ".join(str(item.get("kind") or "") for item in result.repairs),
                    input_kind=input_kind, artifact=str(artifact),
                )
            return result.value

        try:
            identifier, raw = invoke_with_audit(prompt, "continuation" if continuation else "task")
            if pending_guidance is not None:
                from .attention_guidance import mark_guidance_consumed
                consumed = mark_guidance_consumed(self.cycle_dir, pending_guidance, role=role, stage=stage)
                state["attention_guidance"] = consumed
                state["operator_guidance"] = consumed
                state["operator_guidance_pending"] = False
                self._emit(state, "attention.guidance_consumed", {
                    "source": consumed.get("source"), "role": role, "stage": stage,
                    "path": consumed.get("path"),
                })
                self._persist(state)
        except NativeGoalBudgetExceeded as exc:
            # A TOKEN_USAGE event has crossed the ledger limit while the Goal
            # is still ACTIVE. Cancelling this logical Goal is a resource
            # boundary, not scientific completion; never format Findings.
            live_budget = budget_status(self.cycle_dir, self.spec.research_root, state)
            state["investigator_budget"] = {
                k: v for k, v in live_budget.items()
                if k not in {"reviewed_evidence_ids", "reviewed_progress_refs"}
            }
            state.update(status="needs_attention", phase="investigator:budget_review",
                         resumable=True, execution_active=False, next_executor="investigator",
                         attention={
                             "code": BUDGET_ATTENTION_CODE,
                             "reason": (
                                 f"Native Goal reached actual live Investigator usage "
                                 f"{exc.observed_tokens}/{exc.allowance_tokens} tokens; "
                                 "SDK internal reviewer is evaluating the next tranche."
                             ),
                             "thread_id": exc.thread_id,
                         })
            state["native_goal"] = dict(getattr(self.backend, "last_native_goal", {}) or {})
            invocation_ledger.transition(invocation_id, "interrupted", role=role,
                                         thread_id=exc.thread_id,
                                         reason="live Native Goal token guard exceeded")
            if artifact_tracker is not None:
                artifact_tracker.finalize(status="interrupted")
            self._emit(state, "investigation.budget_review_started", {
                "observed": exc.observed_tokens,
                "allowance": exc.allowance_tokens,
                "review_index": live_budget["review_index"],
                "trigger": "native_goal_live_token_usage", "mode": "sdk_internal",
            })
            self._persist(state)
            # A Native Goal may hit the token boundary *inside* its call.
            # Reuse exactly the same 3+3 gate as normal Investigator routing,
            # never launch a fourth Reviewer approval from this exception path.
            if self._resolve_3plus3_budget(state, live_budget):
                return self._call(
                    state, role, context, resume_role=resume_role,
                    fresh_thread=False, contract_role=contract_role,
                    output_key=output_key,
                )
            from .internal_budget_review import review_budget_in_sdk
            try:
                decision = review_budget_in_sdk(
                    backend=self.backend, cycle_dir=self.cycle_dir, state=state,
                    goal=self.spec.goal, research_contract=self.spec.contract,
                    final_gate=self.spec.final_gate,
                )
            except (ProviderQuotaExhausted, RecoveryStop):
                raise
            except Exception as review_exc:
                state["attention"] = {
                    "code": "investigator_budget_review_failed",
                    "reason": f"SDK Budget Reviewer failed: {type(review_exc).__name__}: {review_exc}",
                }
                self._emit(state, "investigation.budget_review_failed", {
                    "review_index": live_budget["review_index"],
                    "error_type": type(review_exc).__name__,
                    "detail": str(review_exc)[:600],
                })
                self._persist(state)
                raise RecoveryStop("SDK budget review failed; no implicit approval") from review_exc
            self._emit(state, "investigation.budget_review_decided", {
                "review_index": decision["review_index"],
                "decision": decision["decision"],
                "new_allowance": decision["new_allowance"],
                "mode": "sdk_internal",
            })
            if decision["decision"] != "approve":
                self._hold_budget_rejected(state, decision["reason"])
            renewed = budget_status(self.cycle_dir, self.spec.research_root, state)
            state["investigator_budget"] = {
                k: v for k, v in renewed.items()
                if k not in {"reviewed_evidence_ids", "reviewed_progress_refs"}
            }
            state.update(status="active", phase="investigator:running",
                         execution_active=True, attention={})
            self._persist(state)
            # Start a NEW native Goal operation in the SAME scientific thread
            # using the already frozen objective and workspace. This is not
            # a continuation of the cancelled logical Goal, nor a new Cycle.
            return self._call(
                state, role, context, resume_role=resume_role,
                fresh_thread=False, contract_role=contract_role,
                output_key=output_key,
            )
        except NativeGoalDeliveryError as exc:
            state["native_goal"] = dict(getattr(self.backend, "last_native_goal", {}) or {})
            state.update(status="needs_attention", phase="investigator:handoff_invalid",
                         execution_active=False, resumable=True, next_executor="investigator",
                         attention={"code": "investigator_handoff_invalid",
                                    "reason": exc.detail, "thread_id": exc.thread_id})
            invocation_ledger.transition(invocation_id, "interrupted", role=role,
                                         reason=f"SDK handoff invalid: {exc.detail}",
                                         thread_id=exc.thread_id)
            if artifact_tracker is not None:
                artifact_tracker.finalize(status="interrupted")
            self._persist(state)
            raise RecoveryStop("Investigator science complete but SDK handoff invalid: " + exc.detail)
        except NativeGoalIncomplete as exc:
            # Native blocked is not a permitted Investigator scientific exit.
            # Preserve the raw status and public report for operational review,
            # but do not route new events into the legacy scientific-stop /
            # Supervisor-preservation authorization path.
            state.update(status="needs_attention", phase="investigator:goal_suspended",
                         execution_active=False, resumable=True, next_executor="investigator",
                         attention=_native_goal_attention(
                             self.cycle_dir, goal_status=exc.status, thread_id=exc.thread_id,
                         ))
            state["native_goal"] = dict(getattr(self.backend, "last_native_goal", {}) or {})
            capacity = state["native_goal"].get("capacity_reconnect")
            if isinstance(capacity, dict) and capacity.get("status") == "timeout":
                state["attention"]["code"] = "provider_model_capacity_timeout"
                state["attention"]["reason"] = (
                    "Codex model service remained at capacity for 30 minutes; "
                    "automatic same-thread retries reached their deadline. "
                    "The scientific thread/workspace are preserved. "
                    "Resume manually once the service is available."
                )
                state["attention"]["model_error"] = str(capacity.get("error") or "")[:400]
                state["attention"]["retry_count"] = int(capacity.get("retry_count") or 0)
                register_cycle_incident(
                    self.cycle_dir, severity="attention", kind="provider_capacity_timeout",
                    source_event="sdk.capacity_retry.exhausted",
                    code="provider_model_capacity_timeout",
                    discriminator=invocation_id,
                    summary=state["attention"]["reason"],
                    fields={
                        "model": route.model, "provider": route.provider,
                        "thread_id": exc.thread_id,
                        "retry_count": state["attention"]["retry_count"],
                        "max_wait_seconds": 1800,
                    },
                )
            invocation_ledger.transition(invocation_id, "interrupted", role=role,
                                         reason="native Goal noncomplete: " + exc.status,
                                         thread_id=exc.thread_id)
            if artifact_tracker is not None:
                artifact_tracker.finalize(status="interrupted")
            self._persist(state)
            if exc.status == "usageLimited":
                state["attention"] = {}
                # A native Goal may reach the Codex account's usage limit while
                # TM's scientific token budget still has ample headroom.
                # Route ONLY this terminal native status through the existing
                # provider-quota PAUSED/Supervisor switching protocol. Other
                # native statuses are not evidence of account exhaustion.
                thread_accounts = getattr(self.backend, "thread_accounts", None)
                account = (thread_accounts.get(exc.thread_id)
                           if isinstance(thread_accounts, dict) else None)
                raise ProviderQuotaExhausted(
                    "Codex native Goal reached usageLimited; no Findings or Judge "
                    "decision was submitted. Resume the frozen scientific task "
                    "from its retained thread/workspace after quota recovery.",
                    provider=route.provider, account=account,
                ) from exc
            raise RecoveryStop("Investigator Native Goal suspended: " + exc.status)
        except Exception as exc:
            from .supervisor_incident import NativeGoalRuntimeFailure
            if isinstance(exc, NativeGoalRuntimeFailure):
                # Preserve provider evidence on the FAILED scientific checkpoint.
                # This is operational telemetry, never a Judge verdict.
                state["native_goal"] = dict(getattr(self.backend, "last_native_goal", {}) or {})
            artifact_manifest = artifact_tracker.finalize(status="failed") if artifact_tracker is not None else None
            invocation_ledger.transition(
                invocation_id, "failed", role=role,
                error_type=type(exc).__name__, error=str(exc),
                invocation_artifact_manifest=str(artifact_manifest) if artifact_manifest else None,
            )
            raise
        raw_outputs = [raw]
        repair_prompt = ""
        json_repair_used = False
        try:
            output = normalize_with_audit(raw, "agent_output")
        except OutputNormalizationError as exc:
            state["json_parse_repair_count"] = int(state.get("json_parse_repair_count") or 0) + 1
            json_repair_used = True
            repair_prompt = build_json_repair_prompt(stage, raw, str(exc))
            self._record_cycle_anomaly(
                state, kind="json_repair_retry", role=role, stage=stage,
                invocation_id=invocation_id, thread_id=identifier,
                mechanism="sdk_driven_llm_retry", summary=str(exc), attempt=1,
                input_kind="agent_output",
            )
            identifier, repaired_raw = invoke_with_audit(repair_prompt, "json_repair")
            raw_outputs.append(repaired_raw)
            try:
                output = normalize_with_audit(repaired_raw, "json_repair")
            except OutputNormalizationError as repair_exc:
                self._contract_repair_failed(
                    state, invocation_id=invocation_id, role=role, stage=stage, thread_id=identifier,
                    raw_outputs=raw_outputs,
                    errors=[{"path": "/", "code": "json_content_loss" if repair_exc.classification == "content_loss" else "json_syntax_error",
                             "expected": "recoverable JSON object", "actual": repair_exc.classification,
                             "message": str(repair_exc or exc)}],
                    repair_prompt=repair_prompt, invocation_ledger=invocation_ledger,
                    artifact_tracker=artifact_tracker,
                )
        validation = validate_agent_output(role, stage, output, state, validation_context, contract)
        if not validation.valid:
            errors = [item.as_dict() for item in validation.errors]
            self._emit(state, "json.contract_violation", {
                "role": role, "stage": stage, "classification": "contract_violation",
                "error_count": len(validation.errors),
                "paths": [item.path for item in validation.errors],
            })
            repaired_output = None
            for repair_attempt in range(1, CONTRACT_REPAIR_LIMIT + 1):
                state["contract_repair_count"] = int(state.get("contract_repair_count") or 0) + 1
                repair_prompt = build_contract_repair_prompt(
                    stage, errors, contract.prompt_projection(validation_context), validation_context,
                    repair_attempt=repair_attempt, repair_limit=CONTRACT_REPAIR_LIMIT,
                )
                self._record_cycle_anomaly(
                    state, kind="contract_repair_retry", role=role, stage=stage,
                    invocation_id=invocation_id, thread_id=identifier,
                    mechanism="sdk_driven_llm_retry",
                    summary="; ".join(str(item.get("message") or item.get("code") or "contract violation") for item in errors),
                    attempt=repair_attempt, input_kind="contract_repair",
                    error_paths=[str(item.get("path") or "") for item in errors],
                )
                identifier, repaired_raw = invoke_with_audit(repair_prompt, "contract_repair")
                raw_outputs.append(repaired_raw)
                try:
                    repaired_output = normalize_with_audit(repaired_raw, "contract_repair")
                except OutputNormalizationError as exc:
                    if json_repair_used:
                        self._contract_repair_failed(
                            state, invocation_id=invocation_id, role=role, stage=stage, thread_id=identifier,
                            raw_outputs=raw_outputs,
                            errors=[{"path": "/", "code": "json_content_loss" if exc.classification == "content_loss" else "json_syntax_error",
                                     "expected": "recoverable JSON object", "actual": exc.classification,
                                     "message": str(exc)}],
                            repair_prompt=repair_prompt, invocation_ledger=invocation_ledger,
                            artifact_tracker=artifact_tracker,
                        )
                    json_repair_used = True
                    state["json_parse_repair_count"] = int(state.get("json_parse_repair_count") or 0) + 1
                    json_repair_prompt = build_json_repair_prompt(stage, repaired_raw, str(exc))
                    self._record_cycle_anomaly(
                        state, kind="json_repair_retry", role=role, stage=stage,
                        invocation_id=invocation_id, thread_id=identifier,
                        mechanism="sdk_driven_llm_retry", summary=str(exc), attempt=1,
                        input_kind="contract_repair_output",
                    )
                    identifier, json_repaired_raw = invoke_with_audit(json_repair_prompt, "json_repair")
                    raw_outputs.append(json_repaired_raw)
                    try:
                        repaired_output = normalize_with_audit(json_repaired_raw, "json_repair_after_contract")
                    except OutputNormalizationError as json_exc:
                        self._contract_repair_failed(
                            state, invocation_id=invocation_id, role=role, stage=stage, thread_id=identifier,
                            raw_outputs=raw_outputs,
                            errors=[{"path": "/", "code": "json_content_loss" if json_exc.classification == "content_loss" else "json_syntax_error",
                                     "expected": "recoverable JSON object", "actual": json_exc.classification,
                                     "message": str(json_exc)}],
                            repair_prompt=json_repair_prompt, invocation_ledger=invocation_ledger,
                            artifact_tracker=artifact_tracker,
                        )
                repaired_validation = validate_agent_output(role, stage, repaired_output, state, validation_context, contract)
                if repaired_validation.valid:
                    output = repaired_output
                    break
                errors = [item.as_dict() for item in repaired_validation.errors]
                self._emit(state, "json.contract_violation", {
                    "role": role, "stage": stage, "classification": "contract_violation",
                    "input_kind": "contract_repair", "repair_attempt": repair_attempt,
                    "repair_limit": CONTRACT_REPAIR_LIMIT,
                    "error_count": len(repaired_validation.errors),
                    "paths": [item.path for item in repaired_validation.errors],
                })
            else:
                self._contract_repair_failed(
                    state, invocation_id=invocation_id, role=role, stage=stage, thread_id=identifier,
                    raw_outputs=raw_outputs, errors=errors,
                    repair_prompt=repair_prompt, invocation_ledger=invocation_ledger,
                    artifact_tracker=artifact_tracker,
                )
        artifact_manifest = artifact_tracker.finalize(status="completed") if artifact_tracker is not None else None
        if artifact_manifest is not None:
            state["last_invocation_artifact_manifest"] = str(artifact_manifest)
        state["last_invocation_id"] = invocation_id
        state["active_invocation_id"] = None
        state["threads"][thread_key] = identifier
        agent_output = output
        semantic_audit = None
        from .abc_pipeline import annotate_literature
        if role == "literature" and str(validation_context.get("routing_source") or "") != "research_lead":
            # Literature A reports only source-grounded claims.  H/criterion/objection
            # binding is B/SDK authority and never needs to be echoed into the Agent prompt.
            semantic_context = dict(validation_context)
            lock = semantic_context.get("hypothesis_lock") or semantic_context.get("branch") or {}
            if isinstance(lock, dict) and "cycle_criteria" not in semantic_context:
                semantic_context["cycle_criteria"] = (
                    lock.get("cycle_criteria") or lock.get("cycle_progress_criteria") or {}
                )
            semantic_context["hypothesis_lock"] = compact_hypothesis_view(lock)
            semantic_context.pop("branch", None)
            output, semantic_audit = annotate_literature(agent_output, semantic_context, self.semantic_backend)
        from .contract_closure import bind_registered_evidence, close_agent_output
        output = close_agent_output(role, stage, output, validation_context, state)
        if role == "challenger" and stage in {"candidate_review", "repair_verification"}:
            # Bind the actual delivered projection, not a model-provided claim
            # or a candidate reconstructed after invocation/cache reuse.
            output = _bind_review_output(output, initial_context, stage)
        state.setdefault("agent_outputs", {})[output_key or role] = agent_output
        if (actual == "research_lead" and stage in {"frontier_proposal", "frontier_maintenance"}
                and graph_stamp and isinstance(initial_context.get("agent_graph"), dict)):
            state["research_lead_agent_graph_stamp"] = graph_stamp
        if actual == "research_lead":
            state["research_lead_thread_id"] = identifier
            consume_reads = getattr(self.backend, "consume_turn_reads", None)
            if callable(consume_reads):
                turn_reads = consume_reads()
                if turn_reads is not None:
                    state.setdefault("research_lead_reads", []).extend(turn_reads)
            if self.cycle_dir is not None:
                self._save_research_lead_state(
                    thread_id=identifier, route=route, cycle_id=self.cycle_dir.name,
                    rollover_reason=state.get("research_lead_rollover_reason"),
                    rollover_history=state.get("research_lead_rollover_history"),
                    context_chars=state.get("research_lead_context_chars"),
                )
        lead_frontier_grounding = (
            role == "literature"
            and str(validation_context.get("routing_source") or "") == "research_lead"
        )
        evidence_registration_audit: list[dict[str, Any]] = []
        if lead_frontier_grounding:
            # Lead-triggered Literature informs frontier formulation/planning.
            # It must not mutate the current Cycle's adjudicative Evidence after
            # Judge/Model/Goal-Gate closure (or before a Hypothesis even exists).
            evidence_registration_audit = [{
                "status": "frontier_grounding_only",
                "reason": "Research Lead Literature detour is planning context, not current-Cycle adjudicative Evidence",
            }]
        elif role in CAPABILITIES or role == "challenger":
            refs = self._register_evidence(state, role, output, stage=stage)
            evidence_registration_audit = list(self._last_evidence_registration_audit)
            bind_registered_evidence(output, refs)
        closure_artifact = self.cycle_dir / "semantic_closure" / f"{invocation_id}.json"
        closure_payload = {
            "role": role, "stage": stage, "agent_output": agent_output,
            "semantic_backend": (getattr(self.semantic_backend, "name", type(self.semantic_backend).__name__)
                                 if semantic_audit is not None else None),
            "semantic_pipeline": semantic_audit,
            "evidence_registration_audit": evidence_registration_audit,
            "invocation_artifact_manifest": str(artifact_manifest) if artifact_manifest else None,
            "closed_output": output, "at": now(),
        }
        write_json(closure_artifact, closure_payload)
        closure_hash = stable_hash(closure_payload)
        all_artifacts_rejected = [
            item for item in evidence_registration_audit
            if item.get("status") == "all_artifacts_rejected"
        ]
        if all_artifacts_rejected:
            evidence_ids = ", ".join(str(item.get("evidence_id")) for item in all_artifacts_rejected)
            message = (
                "Evidence artifact protocol violation: every submitted artifact pointer was rejected "
                f"for evidence {evidence_ids}; registration audit is saved in {closure_artifact}"
            )
            state.update({
                "phase": f"{role}:{stage}:artifact_validation_failed",
                "protocol_health": "violation",
                "resumable": True,
                "recovery": {
                    "action": "evidence_artifact_validation_failed",
                    "invocation_id": invocation_id,
                    "role": role,
                    "stage": stage,
                    "artifact": str(closure_artifact),
                    "at": now(),
                },
            })
            invocation_ledger.transition(
                invocation_id, "quarantined", role=role, stage=stage,
                commit_state="not_committed", closure_artifact=str(closure_artifact),
                closure_hash=closure_hash, logical_key=logical_key,
                error_type="EvidenceArtifactProtocolViolation", error=message,
            )
            self._emit(state, "invocation.quarantined", {
                "invocation_id": invocation_id, "role": role, "stage": stage,
                "reason": "all_artifacts_rejected", "semantic_closure": str(closure_artifact),
            })
            self._persist(state)
            raise ValueError(message)
        state.setdefault("role_outputs", {})[output_key or role] = output
        state.update({"phase": f"{role}:completed"})
        # Once semantic closure and all invocation-owned side effects exist, a
        # recovery may safely reuse this output even if the following workflow
        # state checkpoint or final committed marker is interrupted.
        invocation_ledger.transition(
            invocation_id, "completed", role=role, stage=stage,
            commit_state="not_committed", closure_artifact=str(closure_artifact),
            closure_hash=closure_hash, logical_key=logical_key,
            invocation_artifact_manifest=str(artifact_manifest) if artifact_manifest else None,
        )
        state["active_role"] = role
        self._emit(state, "role.completed", {
            "role": role, "stage": stage, "output": agent_output,
            "semantic_closure": str(closure_artifact),
            "invocation_id": invocation_id,
            "thread_id": identifier,
            "investigation_attempt": state.get("investigation_attempt", 0),
            "investigation_round_count": validation_context.get("round"),
        })
        self._persist(state)
        self._checkpoint(state, role.replace("/", "-").replace(" ", "-"))
        invocation_ledger.transition(
            invocation_id, "committed", role=role, stage=stage,
            commit_state="committed", closure_artifact=str(closure_artifact),
            closure_hash=closure_hash, logical_key=logical_key,
            invocation_artifact_manifest=str(artifact_manifest) if artifact_manifest else None,
        )
        return output
