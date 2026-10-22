from __future__ import annotations

"""Investigation routing, Literature queueing, Judge rework, and adjudication."""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .cycle_criteria import criterion_rows
from .evidence import scientific_evidence_producer
from .investigator_budget import budget_status, BUDGET_ATTENTION_CODE, format_k_tokens
from .internal_budget_review import review_budget_in_sdk
from .provider_errors import ProviderQuotaExhausted

from .role_registry import CAPABILITIES
from .runtime import now, write_json
from .specification import cycle_number, is_cycle_dir, list_cycle_dirs
from .workflow_support import (
    REWORK_LIMIT,
    TERMINAL_SCIENTIFIC_VERDICTS,
    VERDICTS,
    RecoveryStop,
)

class InvestigationMixin:
    def _persist_investigator_budget_snapshot(self, state: dict[str, Any]) -> None:
        """Project the authoritative review ledger before stopping or continuing.

        A budget decision is written to the append-only ledger first. Persist its
        reconstructed status even on rejection, so restart and Observer snapshots
        cannot misreport a recorded rejection as an unreviewed first tranche.
        """
        state["investigator_budget"] = {
            key: value for key, value in budget_status(
                self.cycle_dir, self.spec.research_root, state,
            ).items() if key not in {"reviewed_evidence_ids", "reviewed_progress_refs"}
        }
        self._persist(state)

    def _hold_budget_rejected(self, state: dict[str, Any], reason: str) -> None:
        """Close the spending path, not the science, after a durable denial.

        Preserve any prior nonterminal Judge objection and the current native
        checkpoint. Neither historical Evidence nor an unfinished native Goal
        can be presented as a new Investigator submission.
        """
        self._persist_investigator_budget_snapshot(state)
        status = budget_status(self.cycle_dir, self.spec.research_root, state)
        if not status["rejected"]:
            raise ValueError("budget rejection handoff requires a committed reject ledger")
        attempts = state.get("investigation_attempts") or {}
        current_key = f"attempt-{int(state.get('investigation_attempt') or 0):04d}"
        current = attempts.get(current_key) or {}
        prior = next((
            item for key, item in reversed(list(attempts.items()))
            if key != current_key and isinstance(item, dict)
            and isinstance(item.get("judge_decision"), dict)
            and item["judge_decision"].get("assessment_state") in {
                "rework_requested", "insufficient_evidence",
            }
        ), None)
        old_objection = (prior or {}).get("judge_decision") or {}
        # Successful formal submissions, if any, remain durable. However,
        # no *new* finding can be manufactured from a raw workspace report.
        submitted = [
            item for item in (current.get("outputs") or [])
            if isinstance(item, dict)
            and item.get("executor_role") == "investigator"
            and item.get("round_completed") is True
            and (item.get("output") or {}).get("findings")
        ]
        ready_for_judge = bool(submitted and self._adjudicable_judge_records(
            self._evidence_context()))
        handoff = {
            "schema": "theory-master/denied-budget-science-handoff/v1",
            "cycle_id": str(self.cycle_dir.name),
            "hypothesis_id": (state.get("hypothesis_lock") or {}).get("id"),
            "decision": "reject",
            "budget_review_index": status["review_index"],
            "prior_judge_objection": old_objection.get("missing_evidence"),
            "prior_judge_assessment": old_objection.get("assessment_state"),
            "formal_new_investigator_submissions": len(submitted),
            "ready_for_judge": ready_for_judge,
            "scientific_verdict": None,
            "reason": reason,
            "checkpoint": state.get("last_safe_checkpoint"),
        }
        path = self.cycle_dir / "budget" / "denied_budget_science_handoff.json"
        write_json(path, handoff)
        state.update({
            "status": "needs_attention", "resumable": True,
            "execution_active": False,
            "phase": ("judge:ready_after_budget_denial" if ready_for_judge
                      else "judge:rework_blocked" if prior
                      else "investigator:budget_denied"),
            "next_executor": "judge" if ready_for_judge else "investigator",
            "scientific_verdict": None,
            "resource_disposition": "budget_denied",
            "closure_reason": ("new_formal_submission_awaits_judge" if ready_for_judge
                               else "judge_rework_unfinished_budget_denied" if prior
                               else "investigator_no_formal_submission_budget_denied"),
            "attention": {
                "code": "investigator_budget_review_rejected",
                "reason": "Budget Reviewer rejected further spending: " + reason,
                "resume_behavior": (
                    "The reviewer has denied a resource tranche; preserve the "
                    "frozen Hypothesis, original Judge objection and checkpoint. "
                    "Do not invent a scientific open verdict or route historical "
                    "findings as a new submission. An explicit ledger correction "
                    "or new formal submission is needed before re-adjudication."
                ),
            },
        })
        if prior and not ready_for_judge:
            state["assessment_state"] = "rework_requested"
        if isinstance(current, dict):
            current["readiness"] = {
                **(current.get("readiness") or {}),
                "decision": "send_to_judge" if ready_for_judge else "rework_blocked",
                "reason": "budget_review_rejected",
                "ready": ready_for_judge,
            }
        if status.get("policy_version") == "three_plus_three_v1" and not ready_for_judge:
            state["budget_auto_preserve"] = {
                "policy": "three_plus_three_v1", "code": "investigator_budget_review_rejected",
                "source": "sdk_authorized_budget_terminal", "cycle": self.cycle_dir.name,
            }
        self._emit(state, "investigation.budget_review_rejected", {
            "review_index": status["review_index"],
            "allowance": status["allowance_tokens"],
            "prior_judge_rework": bool(prior),
            "formal_new_submissions": len(submitted),
            "ready_for_judge": ready_for_judge,
            "handoff": str(path),
        })
        self._save_attempt(state)
        self._persist(state, project=True)
        raise RecoveryStop("Investigator budget review rejected; scientific verdict not inferred")

    def _hold_budget_terminal(self, state: dict[str, Any], *, code: str, reason: str) -> None:
        """Terminate Investigator spending without inventing a Judge decision."""
        self._persist_investigator_budget_snapshot(state)
        budget = budget_status(self.cycle_dir, self.spec.research_root, state)
        if budget["policy_version"] != "three_plus_three_v1":
            raise ValueError("Only new 3+3 Cycles have automatic budget closure")
        state.update({
            "status": "needs_attention", "execution_active": False,
            "resumable": True, "phase": "investigator:budget_denied",
            "scientific_verdict": None, "resource_disposition": "budget_denied",
            "attention": {"code": code, "reason": reason},
        })
        if self._adjudicable_judge_records(self._evidence_context()):
            # A genuinely completed formal submission belongs to Judge, not
            # the preservation archiver. Do not mint an automatic close claim.
            state["next_executor"] = "judge"
            state["phase"] = "judge:ready_after_budget_denial"
            state.pop("budget_auto_preserve", None)
        else:
            state["budget_auto_preserve"] = {
                "policy": "three_plus_three_v1", "code": code,
                "source": "sdk_authorized_budget_terminal", "cycle": self.cycle_dir.name,
            }
        self._emit(state, "investigation.budget_terminal",
                   {"code": code, "granted_tranches": budget["granted_tranches"]})
        self._persist(state, project=True)
        raise RecoveryStop("Investigator 3+3 resource phase ended without scientific verdict")


    def _resolve_3plus3_budget(self, state: dict[str, Any], budget: dict[str, Any]) -> bool:
        """Apply the same safe 3+3 boundary for ordinary and native Goals."""
        if budget["exhausted"] and budget.get("policy_version") == "three_plus_three_v1":
            from .investigator_budget import (
                REVIEWER_TRANCHES, BUDGET_CAP_CODE,
                SUPERVISOR_DECISION_CODE, SUPERVISOR_DECLINED_CODE,
                grant_supervisor_tranche,
            )
            if budget["hard_cap_reached"]:
                self._hold_budget_terminal(
                    state, code=BUDGET_CAP_CODE,
                    reason="Six Investigator tranches exhausted; no seventh grant is authorized.")
            if budget["supervisor_declined"]:
                self._hold_budget_terminal(
                    state, code=SUPERVISOR_DECLINED_CODE,
                    reason="Supervisor declined the optional three-tranche extension.")
            if budget["supervisor_authorized"]:
                # Already authorized; only SDK may issue tranches 5/6.
                state.update({"status": "needs_attention", "resumable": True,
                              "execution_active": False,
                              "phase": "investigator:budget_review",
                              "attention": {"code": BUDGET_ATTENTION_CODE,
                                            "reason": "Applying previously authorized Supervisor installment."}})
                self._persist(state)
                try:
                    grant = grant_supervisor_tranche(self.cycle_dir)
                except Exception as exc:
                    self._persist_investigator_budget_snapshot(state)
                    state["attention"] = {"code": "investigator_budget_review_failed",
                                           "reason": f"Supervisor tranche ledger failed: {type(exc).__name__}: {exc}"}
                    self._persist(state)
                    raise RecoveryStop("Supervisor tranche failed closed") from exc
                self._emit(state, "investigation.supervisor_installment_granted",
                           {"grant_number": grant["grant_number"], "new_allowance": grant["new_allowance"]})
                self._persist_investigator_budget_snapshot(state)
                state.update({"status": "active", "execution_active": True,
                              "phase": "investigator:running", "attention": {}})
                self._persist(state)
                return True
            if budget["granted_tranches"] == REVIEWER_TRANCHES:
                # Reviewer has no authority over #4. Freeze the
                # exact boundary before a fresh Supervisor call.
                state.update({"status": "needs_attention", "resumable": True,
                              "execution_active": False,
                              "phase": "investigator:supervisor_budget_review",
                              "attention": {"code": SUPERVISOR_DECISION_CODE,
                                            "reason": "Three Reviewer tranches spent; Supervisor decides once."}})
                self._persist(state)
                from .supervisor_budget_review import review_supervisor_extension
                try:
                    result = review_supervisor_extension(
                        backend=self.backend, cycle_dir=self.cycle_dir, state=state,
                        goal=self.spec.goal, research_contract=self.spec.contract,
                        final_gate=self.spec.final_gate)
                except (ProviderQuotaExhausted, RecoveryStop):
                    raise
                except Exception as exc:
                    self._persist_investigator_budget_snapshot(state)
                    state["attention"] = {
                        "code": "investigator_budget_supervisor_review_failed",
                        "reason": f"Supervisor budget review failed safely: {type(exc).__name__}: {exc}"}
                    self._persist(state)
                    raise RecoveryStop("Supervisor resource review failed closed") from exc
                self._persist_investigator_budget_snapshot(state)
                if result["decision"] == "supervisor_decline":
                    self._hold_budget_terminal(
                        state, code=SUPERVISOR_DECLINED_CODE,
                        reason=str(result["reason"]))
                state.update({"status": "active", "execution_active": True,
                              "phase": "investigator:running", "attention": {}})
                self._emit(state, "investigation.supervisor_extension_authorized",
                           {"new_allowance": result["status"]["allowance_tokens"]})
                self._persist(state)
                return True
        return False


    def _investigation_budgets(self) -> dict[str, int]:
        """Only Judge's scientific rework permission; no round-count resource cap."""
        reworks = getattr(self.spec, "max_judge_reworks_per_cycle", None)
        return {"max_judge_reworks_per_cycle": (
            REWORK_LIMIT if reworks is None else int(reworks)
        )}

    def _authorize_judge_rework(self, state: dict[str, Any], record: dict[str, Any],
                                judge: dict[str, Any], target: Any) -> tuple[bool, str]:
        """Authorize only one small scientific objection; execution details are SDK-owned."""
        if target not in CAPABILITIES:
            return False, "target_executor_not_executable"
        objections = judge.get("objections")
        if not isinstance(objections, list) or len(objections) != 1 or not isinstance(objections[0], dict):
            return False, "missing_single_structured_objection"
        objection = objections[0]
        criterion = str(objection.get("criterion_id") or "").strip()
        if not criterion:
            return False, "objection_missing_criterion_id"
        if not str(objection.get("requested_resolution") or "").strip():
            return False, "objection_missing_requested_resolution"
        executor = objection.get("executor_role")
        if executor is not None and executor != target:
            return False, "target_executor_does_not_match_objection"
        lock = state.get("hypothesis_lock") or {}
        criteria = criterion_rows(lock.get("cycle_criteria") or lock.get("cycle_progress_criteria") or [])
        frozen = {str(item.get("id") or item.get("criterion_id")) for item in criteria if isinstance(item, dict)}
        if not frozen:
            frozen = set((record.get("criterion_coverage") or {}).keys())
        if criterion not in frozen:
            return False, "criterion_not_in_frozen_decisive_test"
        plan = judge.get("rework_plan")
        if not isinstance(plan, dict) or plan.get("target_executor") != target:
            return False, "sdk_rework_plan_missing_or_mismatched"
        forbidden_plan_fields = {
            "required_evidence_ids", "required_artifact_paths", "required_input_refs", "blocked_by",
            "requires_hypothesis_change", "requires_model_change", "requires_new_definition",
        } & set(plan)
        if forbidden_plan_fields:
            return False, "sdk_rework_plan_contains_legacy_prerequisites"
        if not str(plan.get("expected_output") or "").strip():
            return False, "sdk_rework_plan_missing_expected_output"
        return True, "single_scientific_objection"


    def _pause_unfinished_judge_rework(
        self, state: dict[str, Any], record: dict[str, Any],
        judge: dict[str, Any], *, code: str, detail: str,
        target: Any = None, reason: str | None = None,
    ) -> None:
        """Operationally blocked Judge request is NOT a scientific open verdict.

        Preserve the locked Cycle, the original nonterminal request, and
        the Investigator thread.  The caller must resolve the block before
        trying the same cached request again.
        """
        state.update({
            "status": "needs_attention",
            "phase": "judge:rework_blocked",
            "resumable": True,
            "execution_active": False,
            "scientific_verdict": None,
            "assessment_state": "rework_requested",
            "scientific_phase": "investigating",
            "next_disposition": "rework_blocked",
            "next_executor": "judge",
            "closure_reason": code,
            "resource_disposition": (
                "budget_exhausted" if code == "judge_rework_budget_exhausted"
                else "external_source_gap_unresolved" if code == "judge_literature_rework_blocked"
                else "rework_rejected"
            ),
            "rework_authorization": {
                "authorized": code == "judge_literature_rework_blocked",
                "reason": reason or code,
                "target_executor": target,
                "judge_request": judge,
                "attempt": state.get("investigation_attempt"),
            },
            "attention": {
                "code": code,
                "reason": detail,
                "resume_behavior": (
                    "Preserve the current Cycle/H/decisive_test and the original "
                    "Judge objection. Resolve the specified budget, contract, or "
                    "source blocker before resuming; do not start a new Hypothesis "
                    "or treat this execution state as a scientific open verdict."
                ),
            },
        })
        record["readiness"] = {
            **(record.get("readiness") or {}),
            "decision": "rework_blocked",
            "reason": code,
            "ready": False,
        }
        self._emit(state, "judge.rework_blocked", {
            "code": code,
            "detail": detail,
            "attempt": state.get("investigation_attempt"),
            "judge_rework_count": state.get("judge_rework_count"),
            "target_executor": target,
            "original_judge_verdict": judge.get("verdict"),
        })
        self._save_attempt(state)
        self._persist(state, project=True)
        raise RecoveryStop(code)

    def _start_investigation_attempt(self, state: dict[str, Any], executor: str,
                                     judge: dict[str, Any] | None = None, *, migrate: bool = False) -> None:
        if executor not in CAPABILITIES and executor != "judge":
            raise ValueError("invalid Investigation starting executor")
        if not migrate:
            state["investigation_attempt"] += 1
            state["investigation_round_count"] = 0
        attempt = state["investigation_attempt"]
        key = f"attempt-{attempt:04d}"
        objections = []
        if judge:
            raw = judge.get("objections") or [{"reason": judge.get("missing_evidence") or judge.get("rationale", ""),
                                               "requested_resolution": judge.get("missing_evidence", "")}]
            for index, item in enumerate(raw, 1):
                item = item if isinstance(item, dict) else {"reason": str(item)}
                objections.append({**item,
                    "objection_id": item.get("objection_id") or f"J-{state['judge_rework_count']:04d}-{index:02d}",
                    "criterion_id": item.get("criterion_id") or "M-01",
                    "reason": item.get("reason", ""), "requested_resolution": item.get("requested_resolution", ""),
                    "evidence_that_would_resolve_it": item.get("evidence_that_would_resolve_it",
                                                                   item.get("evidence_that_would_resolve", [])),
                    "evidence_that_would_not_resolve_it": item.get("evidence_that_would_not_resolve_it",
                                                                       item.get("evidence_that_would_not_resolve", [])),
                    "linked_evidence_ids": [], "response_evidence_recorded": False})
        previous = list(state.get("investigation_attempts", {}).values())
        handoff = {"schema": "theory-master/investigation-handoff/v1", "investigation_attempt": attempt,
                   "hypothesis_lock": state["hypothesis_lock"], "judge_feedback": judge or {},
                   "judge_objections": objections,
                   "previous_attempts": [{"attempt": item["attempt"], "outputs": item.get("outputs", []),
                                          "readiness": item.get("readiness", {})} for item in previous],
                   "do_not_repeat": [{"executor_role": item.get("executor_role") or item.get("role"),
                                      "invocation_id": item.get("invocation_id"),
                                      "evidence_refs": item.get("evidence_refs", [])}
                                     for prior in previous for item in prior.get("outputs", [])],
                   **self._evidence_context()}
        directory = self.cycle_dir / "investigation_attempts" / key
        write_json(directory / "handoff.json", handoff)
        state["investigation_attempts"][key] = {"attempt": attempt, "threads": {}, "outputs": [],
            "objections": objections, "criterion_coverage": {},
            "handoff_path": str(directory / "handoff.json"), "judge_feedback": judge or {},
            "readiness": {"decision": "investigate", "reason": "attempt_started"}}
        if migrate:
            self._migrate_legacy_specialists(state, key)
        state["next_executor"] = executor
        state["previous_executor"] = "judge" if judge else None
        state["scientific_phase"] = "investigating"
        self._save_attempt(state)
        self._emit(state, "investigation.attempt_started", {"investigation_attempt": attempt,
                   "judge_rework_count": state["judge_rework_count"], "handoff_path": str(directory / "handoff.json")})
        self._checkpoint(state, key + "-started")

    def _migrate_legacy_specialists(self, state: dict[str, Any], attempt_key: str) -> None:
        """Keep pre-refactor specialist history audit-only and start a fresh Investigator thread."""
        record = (state.get("investigation_attempts") or {}).get(attempt_key)
        if not isinstance(record, dict) or isinstance(record.get("migration_snapshot"), dict):
            return
        threads = state.setdefault("threads", {})
        legacy_threads = {
            role: threads.get(role)
            for role in ("theorist", "numerical")
            if threads.get(role)
        }
        role_outputs = state.get("role_outputs") if isinstance(state.get("role_outputs"), dict) else {}
        legacy_outputs = {role: role_outputs[role] for role in ("theorist", "numerical") if role in role_outputs}
        if not legacy_threads and not legacy_outputs:
            return
        snapshot = {
            "schema": "theory-master/legacy-specialist-migration/v1",
            "from_roles": ["theorist", "numerical"],
            "legacy_threads": legacy_threads,
            "legacy_role_outputs": legacy_outputs,
            "continuation_role": "investigator",
            "continuation_thread": "fresh",
            "captured_at": now(),
        }
        record["migration_snapshot"] = snapshot
        state.setdefault("historical_threads", {}).update(legacy_threads)
        has_legacy_specialist_state = bool(legacy_threads or legacy_outputs)
        if has_legacy_specialist_state:
            state["protocol_health"] = "migration_required"
            migrations = state.setdefault("protocol_migrations", [])
            if not any(isinstance(item, dict) and item.get("schema") == snapshot["schema"]
                       for item in migrations):
                migrations.append(snapshot)
            # Preserve old IDs for audit while ensuring Investigator never
            # continues a Theorist/Numerical conversation.
            for role in CAPABILITIES:
                if threads.get(role):
                    threads[f"{attempt_key}:{role}"] = threads[role]
            threads.pop("investigator", None)
            self._emit(state, "investigation.legacy_specialists_migrated", {
                "investigation_attempt": state.get("investigation_attempt"),
                "attempt_key": attempt_key,
                "legacy_roles": ["theorist", "numerical"],
                "continuation_role": "investigator",
                "continuation_thread": "fresh",
            })
            self._persist(state)

    def _literature_domain_context(self, state: dict[str, Any]) -> str:
        """Compact scientific anchors that keep external search in-domain and source-specific."""
        lock = state.get("hypothesis_lock") if isinstance(state.get("hypothesis_lock"), dict) else {}
        spec = getattr(self, "spec", None)
        parts = [
            str(lock.get("hypothesis") or ""),
            str(lock.get("decisive_test") or lock.get("cycle_milestone") or ""),
        ]
        attempt = int(state.get("investigation_attempt") or 0)
        record = (state.get("investigation_attempts") or {}).get(f"attempt-{attempt:04d}") or {}
        for item in (record.get("outputs") or [])[-6:]:
            if not isinstance(item, dict):
                continue
            closed = item.get("output") if isinstance(item.get("output"), dict) else {}
            for evidence in closed.get("evidence") or []:
                if not isinstance(evidence, dict):
                    continue
                parts.append(str(evidence.get("summary") or ""))
                for pointer in evidence.get("artifact_pointers") or evidence.get("artifacts") or []:
                    path = Path(str(pointer))
                    parts.append(path.name)
                    # Small inspectable JSON artifacts often contain the exact
                    # upstream filenames/DOIs that A-layer prose intentionally
                    # summarizes away. Extract only source-like identifiers,
                    # not arbitrary artifact contents, for scholarly query planning.
                    if path.is_file() and path.suffix.lower() == ".json" and path.stat().st_size <= 131072:
                        try:
                            raw = path.read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            raw = ""
                        hints = re.findall(
                            r"\b[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:csv|tsv|txt|json|py|md|xlsx|xls)\b|"
                            r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b|"
                            r"\b[A-Z][A-Z0-9_-]{2,}\b",
                            raw,
                        )
                        if hints:
                            parts.append(" ".join(dict.fromkeys(hints[:40])))
            gap = closed.get("remaining_gap") if isinstance(closed.get("remaining_gap"), dict) else {}
            parts.append(str(gap.get("description") or ""))
        parts.extend([
            str(getattr(spec, "required_context", "") or ""),
            str(getattr(spec, "goal", "") or ""),
        ])
        return " ".join(" ".join(parts).split())[:5000]

    @staticmethod
    def _normalize_literature_task(task: dict[str, Any], *, triggered_by: str) -> dict[str, Any]:
        return {
            "gap_id": str(task.get("gap_id") or "external-gap").strip(),
            "source_gap_id": str(task.get("source_gap_id") or task.get("gap_id") or "external-gap").strip(),
            "objection_id": str(task.get("objection_id") or "").strip(),
            "task_type": str(task.get("task_type") or "targeted_source").strip(),
            "search_target": str(task.get("search_target") or "").strip(),
            "why_external_needed": str(task.get("why_external_needed") or "").strip(),
            "expected_external_evidence": str(task.get("expected_external_evidence") or "").strip(),
            "new_search_lead": str(task.get("new_search_lead") or "").strip(),
            "domain_context": str(task.get("domain_context") or "").strip(),
            "source_lock": json.loads(json.dumps(task.get("source_lock") or {})),
            "triggered_by": triggered_by,
        }

    def _queue_literature_task(self, state: dict[str, Any], task: dict[str, Any]) -> tuple[bool, str]:
        normalized = self._normalize_literature_task(task, triggered_by=str(task.get("triggered_by") or "investigator"))
        if not normalized.get("domain_context"):
            normalized["domain_context"] = self._literature_domain_context(state)
        history = state.setdefault("literature_task_history", [])
        previous = [item for item in history if isinstance(item, dict)
                    and (item.get("source_gap_id") or item.get("gap_id")) == normalized["source_gap_id"]
                    and item.get("status") == "completed"]
        # Gap identity survives Cycle boundaries. Historical states may lack
        # source_gap_id, in which case their gap_id is the stable identity.
        research_root = getattr(getattr(self, "spec", None), "research_root", None)
        for cycle in list_cycle_dirs(research_root) if research_root else []:
            current_cycle = getattr(self, "cycle_dir", None)
            if current_cycle is None or (cycle_number(cycle.name) or -1) >= (cycle_number(current_cycle.name) or -1):
                continue
            prior_path = cycle / "research_state.json"
            if not prior_path.is_file():
                continue
            try:
                prior_history = json.loads(prior_path.read_text(encoding="utf-8")).get("literature_task_history") or []
            except (OSError, ValueError, AttributeError):
                continue
            previous.extend(item for item in prior_history if isinstance(item, dict)
                            and (item.get("source_gap_id") or item.get("gap_id")) == normalized["source_gap_id"]
                            and item.get("status") == "completed")
        lead = normalized["new_search_lead"].casefold()
        if previous and (not lead or any(str(item.get("new_search_lead") or "").strip().casefold() == lead for item in previous)):
            state["pending_literature_task"] = None
            state["last_literature_dedup"] = {
                "gap_id": normalized["gap_id"], "at": now(),
                "reason": "same external gap already searched; no new external search lead",
            }
            return False, "duplicate_external_gap_without_new_search_lead"
        normalized["status"] = "queued"
        normalized["queued_at"] = now()
        history.append(dict(normalized))
        state["pending_literature_task"] = normalized
        return True, "external_literature_task_queued"

    @staticmethod
    def _judge_source_gap_id(_state: dict[str, Any], objection: dict[str, Any]) -> str:
        """Return a stable scientific external-gap id for a current Judge objection."""
        explicit = str(objection.get("source_gap_id") or "").strip()
        if explicit:
            return explicit
        basis = "\n".join((
            str(objection.get("criterion_id") or "").strip(),
            str(objection.get("requested_resolution") or "").strip(),
            str(objection.get("reason") or "").strip(),
        ))
        return "source-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _literature_task_is_external_artifact(task: dict[str, Any]) -> bool:
        """Guard Literature's scholarly boundary for misclassified external records."""
        if not isinstance(task, dict):
            return False
        if isinstance(task.get("source_lock"), dict) and task.get("source_lock"):
            return False
        target_text = " ".join(str(task.get(key) or "") for key in (
            "search_target", "expected_external_evidence", "new_search_lead"
        )).casefold()
        context_text = str(task.get("domain_context") or "").casefold()
        # Do not trust legacy `why_external_needed`: older Semantic Closure
        # mechanically wrote "requires external literature" even for benchmark
        # files/repositories. Resource identity comes from the actual target,
        # explicit search lead, source lock, and source-specific context.
        scholarly = re.search(r"\b(?:paper|literature|doi|journal|arxiv|scholarly|preprint)\b|文献|论文", target_text)
        artifact = re.search(
            r"\b(?:benchmark|repository|repo|upstream|dataset|webpage|website|web page|source record|prediction template)\b"
            r"|\b\S+\.(?:csv|tsv|txt|json|py|xlsx|xls)\b|外部文件|仓库|网页",
            f"{target_text} {context_text}",
        )
        return bool(artifact and not scholarly)

    def _validate_pending_literature_task(self, state: dict[str, Any]) -> tuple[bool, str]:
        """Validate the current pending scholarly task before Literature invocation."""
        task = state.get("pending_literature_task")
        if not isinstance(task, dict):
            return False, "missing_pending_literature_task"
        if self._literature_task_is_external_artifact(task):
            source_gap_id = str(task.get("source_gap_id") or task.get("gap_id") or "external-artifact")
            reclassified = {
                "source_gap_id": source_gap_id,
                "task": json.loads(json.dumps(task)),
                "reason": "non-scholarly external benchmark/repository artifact cannot be satisfied by Literature scholarly transport",
                "at": now(),
            }
            state["external_artifact_reclassification"] = reclassified
            for item in reversed(state.get("literature_task_history") or []):
                if (isinstance(item, dict) and item.get("status") == "queued"
                        and (item.get("source_gap_id") or item.get("gap_id")) == source_gap_id):
                    item["status"] = "reclassified_external_artifact"
                    item["completed_at"] = now()
                    item["reclassification_reason"] = reclassified["reason"]
                    break
            state["pending_literature_task"] = None
            self._emit(state, "literature.task_reclassified_external_artifact", reclassified)
            return False, "external_artifact_source_unavailable"
        return True, "pending_literature_task_ready"

    @staticmethod
    def _reusable_scholarly_transport(
        state: dict[str, Any], task: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Reuse the host retrieval that fed an unconsumed committed Literature invocation."""
        transport = state.get("last_sdk_scholarly_retrieval")
        if not isinstance(transport, dict) or not transport.get("transport_ok"):
            return None
        expected_gap = str(task.get("source_gap_id") or task.get("gap_id") or "external-gap")
        if str(transport.get("source_gap_id") or "") != expected_gap:
            return None
        expected_type = str(task.get("task_type") or "targeted_source")
        if str(transport.get("task_type") or "targeted_source") != expected_type:
            return None
        if str(transport.get("status") or "") == "test_stub":
            return json.loads(json.dumps(transport))
        papers_path = Path(str(transport.get("papers_json") or ""))
        if not papers_path.is_file():
            return None
        reading = transport.get("reading") if isinstance(transport.get("reading"), dict) else {}
        manifest_text = str(reading.get("pdf_manifest") or "").strip()
        if manifest_text and not Path(manifest_text).is_file():
            return None
        return json.loads(json.dumps(transport))

    def _complete_literature_task(self, state: dict[str, Any], output: dict[str, Any]) -> None:
        pending = state.get("pending_literature_task") if isinstance(state.get("pending_literature_task"), dict) else None
        if pending is None:
            return
        history = state.setdefault("literature_task_history", [])
        for item in reversed(history):
            if isinstance(item, dict) and item.get("gap_id") == pending.get("gap_id") and item.get("status") == "queued":
                item["status"] = "completed"
                item["completed_at"] = now()
                item["evidence_refs"] = [str(record.get("evidence_id")) for record in (output.get("evidence_records") or [])
                                         if isinstance(record, dict) and record.get("evidence_id")]
                break
        state["pending_literature_task"] = None

    def _run_lead_literature_detour(
        self, state: dict[str, Any], request: dict[str, Any], *, lead_stage: str
    ) -> dict[str, Any]:
        """Run at most one Literature detour inside one Research Lead frontier stage."""
        if lead_stage != "frontier_proposal":
            raise ValueError(f"Research Lead Literature detour is not allowed from {lead_stage!r}")
        usage = state.setdefault("lead_literature_detours", {})
        used = int(usage.get(lead_stage) or 0)
        if used >= 1:
            reason = (
                f"Research Lead requested a second Literature detour during {lead_stage}; "
                "the stage permits exactly one bounded external-scholarly detour."
            )
            state.update({
                "status": "needs_attention",
                "human_attention": reason,
                "resumable": True,
                "execution_active": False,
                "attention": {
                    "code": "lead_literature_detour_exhausted",
                    "reason": reason,
                    "lead_stage": lead_stage,
                    "detour_limit": 1,
                    "requested_action": "Use the returned Literature result to finish the same Lead stage, or provide new human scientific guidance.",
                    "resume_behavior": "Resume continues from the same Research Lead frontier stage without another automatic Literature detour.",
                },
            })
            self._emit(state, "lead.literature_detour_exhausted", dict(state["attention"]))
            self._persist(state, project=True)
            raise RecoveryStop(reason)

        task_type = str(request.get("task_type") or "").strip()
        if task_type not in {"landscape", "targeted_source", "novelty_check"}:
            raise ValueError("Research Lead Literature detour supports landscape|targeted_source|novelty_check")
        if task_type == "targeted_source" and not str(request.get("new_search_lead") or "").strip():
            raise ValueError("Research Lead targeted_source Literature detour requires a concrete new_search_lead")

        basis = "\n".join((
            lead_stage,
            task_type,
            str(request.get("search_target") or "").strip(),
            str(request.get("why_external_needed") or "").strip(),
            str(request.get("expected_external_evidence") or "").strip(),
        ))
        gap_id = "lead-source-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
        task = {
            **json.loads(json.dumps(request)),
            "gap_id": gap_id,
            "source_gap_id": gap_id,
            "triggered_by": "research_lead",
        }

        # Consume the single detour authorization before any transport/model side
        # effect so retries or malformed downstream outputs can never spend it twice.
        usage[lead_stage] = used + 1
        state["lead_literature_detours"] = usage
        queued, queue_reason = self._queue_literature_task(state, task)
        self._emit(state, "lead.literature_detour_requested", {
            "lead_stage": lead_stage,
            "task_type": task_type,
            "source_gap_id": gap_id,
            "queued": queued,
            "reason": queue_reason,
        })
        self._persist(state)

        if not queued:
            result = {
                "status": "deduplicated",
                "reason": queue_reason,
                "source_gap_id": gap_id,
                "evidence_refs": self._literature_evidence_refs(),
            }
            state.setdefault("lead_literature_results", {})[lead_stage] = result
            return result

        ready, ready_reason = self._validate_pending_literature_task(state)
        if not ready:
            reason = (
                "Research Lead requested scholarly Literature, but the request could not be "
                f"served by the scholarly transport: {ready_reason}."
            )
            state.update({
                "status": "needs_attention",
                "human_attention": reason,
                "resumable": True,
                "execution_active": False,
                "attention": {
                    "code": "lead_literature_unavailable",
                    "reason": reason,
                    "lead_stage": lead_stage,
                    "source_gap_id": gap_id,
                    "requested_action": "Provide the missing scholarly source/context or revise the frontier decision.",
                    "resume_behavior": "Resume returns to the same Research Lead frontier stage; the automatic Literature detour is already spent.",
                },
            })
            self._persist(state, project=True)
            raise RecoveryStop(reason)

        literature_task = state.get("pending_literature_task") or task
        transport = self.scholarly_retriever(literature_task, self.cycle_workspace)
        state["last_sdk_scholarly_retrieval"] = transport
        self._emit(state, "scholarly.transport_prepared", {
            "status": transport.get("status"),
            "transport_ok": bool(transport.get("transport_ok")),
            "source_gap_id": transport.get("source_gap_id"),
            "paper_count": transport.get("paper_count"),
            "output_dir": transport.get("output_dir"),
            "triggered_by": "research_lead",
            "lead_stage": lead_stage,
        })
        if not transport.get("transport_ok"):
            pending = state.get("pending_literature_task")
            if isinstance(pending, dict):
                for item in reversed(state.get("literature_task_history") or []):
                    if (isinstance(item, dict) and item.get("status") == "queued"
                            and item.get("source_gap_id") == pending.get("source_gap_id")):
                        item["status"] = "transport_blocked"
                        item["completed_at"] = now()
                        break
            state["pending_literature_task"] = None
            reason = str(
                transport.get("reason")
                or transport.get("connector_errors")
                or "SDK scholarly transport was unavailable."
            )
            state.update({
                "status": "needs_attention",
                "human_attention": reason,
                "resumable": True,
                "execution_active": False,
                "attention": {
                    "code": "lead_literature_unavailable",
                    "reason": reason,
                    "lead_stage": lead_stage,
                    "source_gap_id": gap_id,
                    "requested_action": "Restore scholarly retrieval or provide the required source/context.",
                    "resume_behavior": "Resume returns to the same Research Lead frontier stage; the automatic Literature detour is already spent.",
                },
            })
            self._persist(state, project=True)
            raise RecoveryStop(reason)

        output = self._call(state, "literature", {
            "literature_task": literature_task,
            "sdk_scholarly_retrieval": transport,
            "lead_stage": lead_stage,
            "routing_source": "research_lead",
        }, fresh_thread=True, output_key=f"lead_{lead_stage}_literature")
        self._complete_literature_task(state, output)
        result = json.loads(json.dumps(output))
        state.setdefault("lead_literature_results", {})[lead_stage] = result
        self._emit(state, "lead.literature_detour_completed", {
            "lead_stage": lead_stage,
            "task_type": task_type,
            "source_gap_id": gap_id,
            "source_record_count": sum(
                1 for item in (output.get("evidence_records") or []) if isinstance(item, dict)
            ),
        })
        self._persist(state)
        return result

    @staticmethod
    def _previous_scientific_delta(record: dict[str, Any]) -> dict[str, Any]:
        """Project only the science a later executor could not reconstruct safely."""
        outputs = record.get("outputs") if isinstance(record.get("outputs"), list) else []
        if not outputs:
            return {}
        last = outputs[-1] if isinstance(outputs[-1], dict) else {}
        closed = last.get("output") if isinstance(last.get("output"), dict) else {}
        evidence_rows = [
            item for field in ("evidence", "evidence_records")
            for item in (closed.get(field) or []) if isinstance(item, dict)
        ]
        findings = [{key: value for key, value in {
            "statement": str(item.get("statement") or item.get("summary") or item.get("scientific_insight") or ""),
            "scope": str(item.get("scope") or ""),
            "limitations": str(item.get("limitations") or ""),
            # Literature verification status and source locator are scientific
            # qualifications: losing them can make abstract-only claims appear
            # to the next Investigator as independently verified facts.
            "source_ref": item.get("source_ref"),
            "source_access": item.get("source_access"),
            "locator": item.get("locator"),
            "provenance": item.get("provenance"),
            "freshness": item.get("freshness"),
        }.items() if value not in (None, "", [], {})} for item in evidence_rows]
        return {"findings": findings} if findings else {}

    @staticmethod
    def _adjudicable_judge_records(evidence_context: dict[str, Any]) -> list[dict[str, Any]]:
        """Return all technically valid scientific Evidence for the Judge.

        Criterion relevance and support/contradiction are Judge decisions, not
        pre-Judge semantic filters. Qualification rows are not findings.
        """
        manifest_wrapper = evidence_context.get("evidence_manifest")
        if isinstance(manifest_wrapper, dict) and manifest_wrapper:
            payload = manifest_wrapper.get("payload") or {}
        else:
            # Historical persisted runs may predate the current Registry-manifest
            # handoff and expose only the old frozen Judge packet.  Read it only
            # as a recovery source for scientific rows; current Judge prompts never
            # receive that packet.
            payload = (evidence_context.get("judge_packet") or {}).get("payload") or {}
        records = payload.get("records") if isinstance(payload, dict) else []
        accepted = []
        for record in records or []:
            if not isinstance(record, dict):
                continue
            if record.get("lifecycle_status", "active") != "active" or not record.get("adjudicable", True):
                continue
            # Evidence uses SDK producer identity, never a model-authored type;
            # historical rows without a role are handled by read-only fallback.
            if scientific_evidence_producer(record) is None:
                continue
            accepted.append(record)
        return accepted

    def _materialize_investigation_result(
        self, state: dict[str, Any], record: dict[str, Any], evidence_context: dict[str, Any]
    ) -> dict[str, Any]:
        """Build the only scientific payload current Judge is allowed to evaluate."""
        findings: list[dict[str, Any]] = []
        for item in self._adjudicable_judge_records(evidence_context):
            provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
            provenance_view = {
                key: provenance.get(key) for key in ("source", "split")
                if provenance.get(key) not in (None, "", [], {})
            }
            if provenance.get("source") in {"benchmark_provided", "pre_specified_synthetic"}:
                # A declared holdout/gate is not a verified one. Do not pass
                # the claim as an authoritative split to the scientific Judge.
                if "source" in provenance_view:
                    provenance_view["declared_source"] = provenance_view.pop("source")
                if provenance.get("split") in {"holdout", "gate"}:
                    provenance_view["declared_split"] = provenance_view.pop("split")
                provenance_view["freeze_status"] = (
                    "legacy_unverified" if provenance.get("freeze_status") in (None, "legacy_unverified")
                    else "unverified"
                )
            source_access = item.get("source_access") if isinstance(item.get("source_access"), dict) else {}
            source_access_view = {
                key: source_access.get(key) for key in ("verified", "verification_status")
                if source_access.get(key) not in (None, "", [], {})
            }
            finding = {
                "statement": str(item.get("summary") or item.get("body") or ""),
                "scope": str(item.get("scope") or ""),
                "limitations": str(item.get("limitations") or ""),
                    "origin_cycle": item.get("origin_cycle"),
                "origin_cycles": item.get("origin_cycles"),
                "analysis_cycle": item.get("analysis_cycle"),
                "freshness": item.get("freshness"),
                "provenance": provenance_view,
                "source_access": source_access_view,
            }
            scientific = {key: value for key, value in finding.items() if value not in (None, "", [], {})}
            for key in ("method", "result"):
                if key in item:
                    scientific[key] = item[key]
            findings.append(scientific)
        outputs = record.get("outputs") if isinstance(record.get("outputs"), list) else []
        latest = outputs[-1].get("output") if outputs and isinstance(outputs[-1], dict) else {}
        latest = latest if isinstance(latest, dict) else {}
        result = {
            "schema": "theory-master/investigation-result/v1",
            "investigation_attempt": int(state.get("investigation_attempt") or 0),
            "rounds": int(state.get("investigation_round_count") or 0),
            "findings": findings,
        }
        directory = self.cycle_dir / "investigation_attempts" / f"attempt-{int(state.get('investigation_attempt') or 0):04d}"
        path = directory / "investigation_result.json"
        write_json(path, result)
        record["investigation_result_path"] = str(path)
        return result

    @staticmethod
    def _non_scientific_closure_reason(readiness: dict[str, Any], adjudicable_evidence: list[dict[str, Any]]) -> str:
        if not adjudicable_evidence:
            return "No technically valid Investigator/Literature scientific Evidence is available for adjudication."
        reason = str(readiness.get("reason") or "readiness_incomplete")
        unanswered = readiness.get("unanswered_objections") or []
        blockers = []
        if unanswered:
            blockers.append("Judge objections have no linked response finding")
        detail = "; ".join(blockers) if blockers else reason
        return f"Criterion-bound scientific Evidence exists, but Judge readiness is blocked: {detail}."

    def _mark_non_scientific_closure(
        self,
        state: dict[str, Any],
        branch: dict[str, Any],
        record: dict[str, Any],
        *,
        kind: str,
        reason: str,
        transport: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Close an execution attempt without manufacturing a scientific verdict."""
        branch["status"] = "not_adjudicated"
        state["scientific_verdict"] = None
        state["assessment_state"] = "not_adjudicated"
        state["scientific_phase"] = "investigating"
        state["closure_reason"] = kind
        state["resource_disposition"] = (
            "operational_blocked" if kind == "literature_transport_blocked"
            else "external_source_gap_unresolved" if kind == "external_artifact_source_unavailable"
            else "readiness_incomplete" if kind.startswith("readiness_incomplete")
            else "no_scientific_evidence"
        )
        state["operational_only_closure"] = {
            "kind": kind,
            "reason": reason,
            "at": now(),
            "source_gap_id": str(((transport or {}).get("source_gap_id") or "")),
            **({"transport": transport} if isinstance(transport, dict) else {}),
        }
        record["readiness"] = {
            **(record.get("readiness") or {}),
            "decision": "close_without_judge",
            "reason": kind,
            "ready": False,
            "selected_executor": None,
        }
        self._emit(state, "investigation.non_scientific_closure", {
            "investigation_attempt": state.get("investigation_attempt"),
            "kind": kind,
            "reason": reason,
            "source_gap_id": state["operational_only_closure"].get("source_gap_id"),
        })
        state.setdefault("threads", {})
        if getattr(self, "cycle_dir", None) is not None:
            self._save_attempt(state)
        return {
            "assessment_state": "not_called",
            "verdict": None,
            "rationale": reason,
            "closure_reason": kind,
            "resource_disposition": state.get("resource_disposition"),
        }, "open"

    def _finalize_non_scientific_cycle(
        self, state: dict[str, Any], branch: dict[str, Any], active_title: str, active_hypothesis: str
    ) -> None:
        """Persist a no-Evidence attempt while keeping Scientific Model/Frontier unchanged."""
        from .scientific_model import load as load_scientific_model
        model = load_scientific_model(self.spec.research_root, goal_id=self.spec.goal_id)
        write_json(self.cycle_dir / "scientific_model_in.json", model)
        write_json(self.cycle_dir / "scientific_model_out.json", model)
        state["scientific_model_version_in"] = int(model.get("version") or 0)
        state["scientific_model_version_out"] = int(model.get("version") or 0)
        state["scientific_model_patch_status"] = "kept"
        state["scientific_model_update_status"] = "kept"
        state["evolution_candidates"] = []
        state["selected_next_candidate_id"] = None
        # No scientific next-start is inferred for the next Cycle from an operational closure.
        state["next_start_node_id"] = None
        state["current_state"] = active_hypothesis
        state.update({
            "status": "over", "phase": "complete",
            "resumable": False, "execution_active": False,
        })
        write_json(self.cycle_dir / "operational_closure.json", state.get("operational_only_closure") or {})
        self._emit(state, "operational.closure_recorded", {
            "kind": (state.get("operational_only_closure") or {}).get("kind"),
            "next_start_node_id": state.get("next_start_node_id"),
            "scientific_model_version": state.get("scientific_model_version_out"),
        })

    def _investigate_and_judge(
        self, state: dict[str, Any], branch: dict[str, Any], finalized: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        self._normalize_state_axes(state)
        if state.get("assessment_state") == "terminal" and state.get("scientific_verdict") in TERMINAL_SCIENTIFIC_VERDICTS:
            return state["role_outputs"]["judge"], state["scientific_verdict"]
        budgets = self._investigation_budgets()
        capability = state.get("next_executor") or finalized.get("next_executor") or "investigator"
        if (state.get("investigation_attempt")
                and f"attempt-{state['investigation_attempt']:04d}" in (state.get("investigation_attempts") or {})
                and state.get("next_executor") not in {*CAPABILITIES, "judge"}
                and capability in {*CAPABILITIES, "judge"}):
            state["next_executor"] = capability
            state.setdefault("recovery", {})["restored_next_executor"] = capability
        if not state["investigation_attempt"]:
            self._start_investigation_attempt(state, capability)
        elif f"attempt-{state['investigation_attempt']:04d}" not in state["investigation_attempts"]:
            self._start_investigation_attempt(state, capability, migrate=True)
        else:
            self._migrate_legacy_specialists(state, f"attempt-{state['investigation_attempt']:04d}")
        while True:
            key = f"attempt-{state['investigation_attempt']:04d}"
            record = state["investigation_attempts"][key]
            capability = state["next_executor"]
            recovery_literature_dedup = False
            attention_code = str((state.get("attention") or {}).get("code") or "")
            if (attention_code in {
                    "investigator_internal_turn_limit",
                    "investigator_actionable_no_progress_repeated_twice",
                } and capability == "investigator" and record.get("outputs")):
                adjudicable = self._adjudicable_judge_records(self._evidence_context())
                capability = "judge" if adjudicable else "non_scientific_close"
                state["next_executor"] = capability
                record["readiness"] = {
                    "decision": "send_to_judge" if adjudicable else "close_without_judge",
                    "reason": "legacy_investigator_result_reused" if adjudicable else "legacy_no_adjudicable_evidence",
                    "ready": bool(adjudicable),
                }
                self._emit(state, "investigation.legacy_turn_result_reused", {
                    "investigation_attempt": state["investigation_attempt"],
                    "executor": capability,
                    "result_count": len(record["outputs"]),
                })
            while capability in CAPABILITIES:
                if capability == "investigator":
                    budget = budget_status(self.cycle_dir, self.spec.research_root, state)
                    state["investigator_budget"] = {
                        key: value for key, value in budget.items()
                        if key != "reviewed_evidence_ids"
                    }
                    if self._resolve_3plus3_budget(state, budget):
                        continue
                    if budget["exhausted"]:
                        # This is an SDK-local model review, not a NEEDS_ATTENTION
                        # handoff to an external tmux Agent. Persist the boundary
                        # before approval so the ledger's atomic writer can verify
                        # the frozen checkpoint. The scientific thread is untouched.
                        state.update({
                            "status": "needs_attention", "resumable": True,
                            "execution_active": False, "phase": "investigator:budget_review",
                            "attention": {
                                "code": BUDGET_ATTENTION_CODE,
                                "reason": (
                                    f"Investigator used {format_k_tokens(budget['observed_tokens'])} / "
                                    f"{format_k_tokens(budget['allowance_tokens'])} tokens; "
                                    "SDK internal Budget Reviewer is evaluating the next tranche."
                                ),
                            },
                        })
                        self._emit(state, "investigation.budget_review_started", {
                            "observed": budget["observed_tokens"],
                            "allowance": budget["allowance_tokens"],
                            "review_index": budget["review_index"],
                            "mode": "sdk_internal",
                        })
                        self._persist(state)
                        if budget["rejected"]:
                            self._hold_budget_rejected(
                                state, "Previously recorded resource review denied further spending.")
                        try:
                            decision = review_budget_in_sdk(
                                backend=self.backend, cycle_dir=self.cycle_dir, state=state,
                                goal=self.spec.goal, research_contract=self.spec.contract,
                                final_gate=self.spec.final_gate,
                            )
                        except (ProviderQuotaExhausted, RecoveryStop):
                            raise
                        except Exception as exc:
                            # No implicit approval on model failure, malformed
                            # JSON, invalid ID, or ledger conflict. No tmux retry.
                            # The model/receipt write may have committed a Ledger
                            # decision before a later ancillary write failed.
                            # Never leave a now-authoritative grant/denial out of
                            # the state projection used by recovery and Observer.
                            self._persist_investigator_budget_snapshot(state)
                            state["attention"] = {
                                "code": "investigator_budget_review_failed",
                                "reason": f"SDK internal Budget Reviewer failed: {type(exc).__name__}: {exc}",
                            }
                            self._emit(state, "investigation.budget_review_failed", {
                                "review_index": budget["review_index"],
                                "error_type": type(exc).__name__, "detail": str(exc)[:700],
                            })
                            self._persist(state)
                            raise RecoveryStop("SDK internal budget approval failed") from exc
                        self._emit(state, "investigation.budget_review_decided", {
                            "review_index": decision["review_index"],
                            "decision": decision["decision"],
                            "new_allowance": decision["new_allowance"],
                            "mode": "sdk_internal",
                        })
                        self._persist_investigator_budget_snapshot(state)
                        if decision["decision"] != "approve":
                            self._hold_budget_rejected(state, decision["reason"])
                        state.update({"status": "active", "phase": "investigator:running",
                                      "execution_active": True, "attention": {}})
                        self._persist(state)
                        # If the model has granted a tranche, the same Workflow
                        # immediately continues to the next Investigator call.
                        # On resume a committed grant is read from the ledger.
                context = {"branch": branch, "hypothesis_lock": state["hypothesis_lock"],
                    "investigation_attempt": state["investigation_attempt"],
                    "round": state["investigation_round_count"] + 1,
                    "rework_left": budgets["max_judge_reworks_per_cycle"] - state["judge_rework_count"],
                    "attempt_handoff": {"path": record["handoff_path"]}, "judge_feedback": record["judge_feedback"],
                    "judge_objections": record["objections"], "router_feedback": record["readiness"],
                    "previous_executor": state.get("previous_executor"),
                    "previous_scientific_delta": self._previous_scientific_delta(record),
                    "budgets": budgets, **self._evidence_context()}
                if capability == "literature":
                    task = state.get("pending_literature_task") if isinstance(state.get("pending_literature_task"), dict) else None
                    if task is None:
                        state["closure_reason"] = "missing_pending_literature_task"
                        state["resource_disposition"] = "external_source_gap_unresolved"
                        recovery_literature_dedup = True
                        break
                    queued, reason = self._validate_pending_literature_task(state)
                    if not queued:
                        state["closure_reason"] = reason
                        state["resource_disposition"] = "external_source_gap_unresolved"
                        recovery_literature_dedup = True
                        break
                    context["literature_task"] = state.get("pending_literature_task") or task
                    transport = self._reusable_scholarly_transport(state, context["literature_task"])
                    if transport is None:
                        transport = self.scholarly_retriever(context["literature_task"], self.cycle_workspace)
                        transport_event = "scholarly.transport_prepared"
                    else:
                        transport_event = "scholarly.transport_reused"
                    state["last_sdk_scholarly_retrieval"] = transport
                    self._emit(state, transport_event, {
                        "status": transport.get("status"),
                        "transport_ok": bool(transport.get("transport_ok")),
                        "source_gap_id": transport.get("source_gap_id"),
                        "paper_count": transport.get("paper_count"),
                        "output_dir": transport.get("output_dir"),
                    })
                    if not transport.get("transport_ok"):
                        pending = state.get("pending_literature_task")
                        if isinstance(pending, dict):
                            for item in reversed(state.get("literature_task_history") or []):
                                if (isinstance(item, dict) and item.get("status") == "queued"
                                        and item.get("source_gap_id") == pending.get("source_gap_id")):
                                    item["status"] = "transport_blocked"
                                    item["completed_at"] = now()
                                    break
                        state["pending_literature_task"] = None
                        return self._mark_non_scientific_closure(
                            state, branch, record,
                            kind="literature_transport_blocked",
                            reason=str(transport.get("reason") or transport.get("connector_errors") or "SDK scholarly transport was unavailable."),
                            transport=transport,
                        )
                    context["sdk_scholarly_retrieval"] = transport
                # Literature is a fresh verification episode. External network
                # transport has already been completed by the SDK host runner.
                logical_round = state["investigation_round_count"] + 1
                output = self._call(state, capability, context, fresh_thread=(capability == "literature"))
                if capability == "literature":
                    self._complete_literature_task(state, output)
                invocation_id = str(state.get("last_invocation_id") or "")
                consumed = next((
                    item for item in reversed(record.get("outputs") or [])
                    if isinstance(item, dict) and invocation_id
                    and str(item.get("invocation_id") or "") == invocation_id
                ), None)
                if consumed is None:
                    consumed = {
                        "executor_role": capability,
                        "round": logical_round,
                        "round_completed": False,
                        "invocation_id": invocation_id,
                        "output": output,
                        "evidence_refs": list(state.get("evidence_refs") or []),
                    }
                    record["outputs"].append(consumed)
                state["investigation_round_count"] += 1
                state["total_investigation_round_count"] += 1
                consumed["round_completed"] = True
                self._emit(state, "investigation.round_completed", {
                    "role": capability,
                    "investigation_attempt": state["investigation_attempt"],
                    "round": state["investigation_round_count"],
                    "thread_id": state.get("threads", {}).get(capability),
                })
                if (capability == "literature"
                        and not (output.get("evidence_records") or [])
                        and output.get("requested_next_executor") is None):
                    return self._mark_non_scientific_closure(
                        state, branch, record,
                        kind="literature_no_scientific_evidence",
                        reason="Literature completed without any explicit, criterion-bindable scientific Evidence.",
                        transport=(state.get("last_sdk_scholarly_retrieval")
                                   if isinstance(state.get("last_sdk_scholarly_retrieval"), dict) else None),
                    )
                # No Investigator-selected routing: one complete submission
                # proceeds to the independent Judge when criterion-bound
                # evidence exists.  A Literature objection handoff always
                # returns to Investigator before Judge.
                if capability == "literature":
                    capability = "investigator"
                    reason = "judge_literature_handoff"
                else:
                    adjudicable = self._adjudicable_judge_records(self._evidence_context())
                    capability = "judge" if adjudicable else "non_scientific_close"
                    reason = "investigator_submission" if adjudicable else "no_scientific_evidence"
                record["readiness"] = {
                    "decision": "send_to_judge" if capability == "judge"
                                else "close_without_judge" if capability == "non_scientific_close"
                                else "continue",
                    "reason": reason,
                    "ready": capability == "judge",
                }
                self._emit(state, "investigation.routing", {
                    "investigation_attempt": state["investigation_attempt"],
                    **record["readiness"], "selected_executor": capability,
                })
                state["previous_executor"] = consumed["executor_role"]
                state["next_executor"] = capability
                self._checkpoint(state, f"{key}-r{state['investigation_round_count']}")
            if capability != "judge":
                if capability == "non_scientific_close":
                    readiness = record.get("readiness") if isinstance(record.get("readiness"), dict) else {}
                    adjudicable = self._adjudicable_judge_records(self._evidence_context())
                    return self._mark_non_scientific_closure(
                        state, branch, record,
                        kind=str(readiness.get("reason") or "no_scientific_evidence"),
                        reason=self._non_scientific_closure_reason(readiness, adjudicable),
                        transport=(state.get("last_sdk_scholarly_retrieval")
                                   if isinstance(state.get("last_sdk_scholarly_retrieval"), dict) else None),
                    )
                if recovery_literature_dedup:
                    kind = str(state.get("closure_reason") or "duplicate_external_gap_without_new_search_lead")
                    reason = (
                        "The remaining dependency is a non-scholarly external benchmark/repository artifact; no compatible external-artifact transport is configured, so no new scientific Evidence was produced."
                        if kind == "external_artifact_source_unavailable"
                        else "The same external source gap has no new search lead; no new scientific Evidence was produced."
                    )
                    return self._mark_non_scientific_closure(state, branch, record, kind=kind, reason=reason)
                raise ValueError("invalid Investigation transition")
            if self.stop_before == "judge" and not record.get("judge_decision"):
                state.update({
                    "status": "checkpoint_reached",
                    "phase": "post_investigation",
                    "resumable": True,
                    "execution_active": False,
                    "checkpoint": {
                        "name": "pre_judge",
                        "after": "investigation",
                        "before": "judge",
                    },
                })
                self._checkpoint(state, "pre-judge")
                raise RecoveryStop("pre_judge checkpoint reached")
            judge = record.get("judge_decision")
            if not judge:
                evidence_context = self._evidence_context()
                adjudicable_records = self._adjudicable_judge_records(evidence_context)
                if not adjudicable_records:
                    # Judge evaluates a completed Investigation Result, not an
                    # empty workflow handoff. Require at least one accepted,
                    # technically valid scientific finding before spending the call.
                    state.update({
                        "status": "needs_attention",
                        "resumable": True,
                        "execution_active": False,
                        "closure_reason": "judge_investigation_result_not_ready",
                        "resource_disposition": "evidence_not_ready",
                        "attention": {
                            "code": "judge_investigation_result_not_ready",
                            "reason": "Judge routing was requested but no accepted scientific finding is technically valid for the Investigation Result.",
                            "resume_behavior": "Complete/register a criterion-bound scientific finding, then resume adjudication.",
                        },
                    })
                    record["readiness"] = {
                        **(record.get("readiness") or {}),
                        "decision": "not_ready",
                        "reason": "no_adjudicable_scientific_evidence",
                    }
                    self._emit(state, "judge.not_ready", {
                        "investigation_attempt": state["investigation_attempt"],
                        "reason": "no_adjudicable_scientific_evidence",
                    })
                    self._save_attempt(state)
                    self._persist(state, project=True)
                    raise RecoveryStop("judge investigation result not ready")
                investigation_result = self._materialize_investigation_result(state, record, evidence_context)
                prior_objection = record["objections"][-1] if record.get("objections") else None
                from .hypothesis_history import build_hypothesis_adjudication_history
                lock = state.get("hypothesis_lock") if isinstance(state.get("hypothesis_lock"), dict) else {}
                prior_adjudicated_context = build_hypothesis_adjudication_history(
                    self.spec.research_root,
                    str(lock.get("id") or lock.get("hypothesis_id") or ""),
                    current_cycle_id=str(self.cycle_dir.name),
                    task_mode=self.spec.task_mode,
                    legacy_research_root=self.spec.legacy_research_root,
                )
                self._write_replay_checkpoint(state, "judge_start")
                judge = self._call(state, "judge", {
                    "branch": branch,
                    "investigation_result": investigation_result,
                    "prior_objection": prior_objection,
                    "prior_adjudicated_context": prior_adjudicated_context,
                })
                record["judge_decision"] = judge
                self._save_attempt(state)
            insufficient = (
                judge.get("verdict") == "needs_more_evidence"
                or judge.get("assessment_state") in {"insufficient_evidence", "rework_requested"}
            )
            verdict = "needs_more_evidence" if insufficient else judge.get("verdict")
            if verdict not in VERDICTS:
                raise ValueError("judge schema")
            plan = judge.get("rework_plan") if isinstance(judge.get("rework_plan"), dict) else {}
            target = plan.get("target_executor") or plan.get("target_role")
            rework_exhausted = state["judge_rework_count"] >= budgets["max_judge_reworks_per_cycle"]
            if insufficient and rework_exhausted:
                self._pause_unfinished_judge_rework(
                    state, record, judge, code="judge_rework_budget_exhausted",
                    detail=(
                        "Judge requested a still-unfinished frozen decisive test, "
                        f"but the {budgets['max_judge_reworks_per_cycle']} authorized "
                        "reworks for this Cycle are exhausted. No scientific "
                        "terminal verdict has been established."
                    ),
                    target=target,
                )
            if not insufficient:
                state["closure_reason"] = "judge_terminal_verdict"
                state["resource_disposition"] = (
                    "budget_exhausted" if record["readiness"].get("budget_exhausted")
                    else "available"
                )
                break
            authorized, authorization_reason = self._authorize_judge_rework(state, record, judge, target)
            if not authorized:
                self._pause_unfinished_judge_rework(
                    state, record, judge, code="judge_rework_not_authorized",
                    detail=(
                        "SDK did not authorize the Judge's nonterminal rework "
                        f"request: {authorization_reason}. This is a protocol "
                        "blocker, not scientific inconclusiveness."
                    ),
                    target=target, reason=authorization_reason,
                )
            if target == "literature":
                objection = next((item for item in (judge.get("objections") or []) if isinstance(item, dict)), {})
                plan = judge.get("rework_plan") if isinstance(judge.get("rework_plan"), dict) else {}
                source_gap_id = self._judge_source_gap_id(state, objection)
                judge_task = {
                    "gap_id": source_gap_id,
                    "source_gap_id": source_gap_id,
                    "objection_id": str(objection.get("objection_id") or ""),
                    "task_type": "targeted_source",
                    "search_target": str(objection.get("requested_resolution") or judge.get("missing_evidence") or judge.get("rationale") or "targeted external scholarly verification"),
                    "why_external_needed": str(objection.get("reason") or judge.get("rationale") or "Judge identified an unresolved external scholarly dependency."),
                    "expected_external_evidence": str(plan.get("expected_output") or "traceable external source evidence that addresses the Judge objection"),
                    "new_search_lead": str(objection.get("new_search_lead") or plan.get("new_search_lead") or ""),
                    "triggered_by": "judge",
                }
                queued, queue_reason = self._queue_literature_task(state, judge_task)
                if not queued:
                    state["closure_reason"] = queue_reason
                    state["resource_disposition"] = "external_source_gap_unresolved"
                    state["rework_authorization"] = {
                        "authorized": True,
                        "reason": authorization_reason,
                        "target_executor": target,
                        "judge_request": judge,
                        "attempt": state["investigation_attempt"],
                        "execution_status": queue_reason,
                    }
                    self._pause_unfinished_judge_rework(
                        state, record, judge, code="judge_literature_rework_blocked",
                        detail=(
                            "Judge's required scholarly rework could not be "
                            f"queued: {queue_reason}. The decisive test remains "
                            "unresolved; retain its current Cycle and evidence."
                        ),
                        target=target, reason=queue_reason,
                    )
            state["scientific_verdict"] = None
            state["assessment_state"] = "rework_requested"
            state["next_disposition"] = "rework"
            state["judge_rework_count"] += 1
            state["scientific_rework_count"] += 1
            state["rework_authorization"] = {
                "authorized": True, "reason": authorization_reason,
                "target_executor": target, "judge_request": judge,
                "attempt": state["investigation_attempt"],
            }
            self._start_investigation_attempt(state, target, judge)
        state.update({"scientific_verdict": verdict, "assessment_state": "terminal",
                      "next_disposition": "close", "scientific_phase": "adjudicated"})
        branch["status"] = verdict
        judge = {**judge, "verdict": verdict, "assessment_state": "terminal",
                 "closure_reason": state.get("closure_reason"), "resource_disposition": state.get("resource_disposition"),
                 "rework_authorization": state.get("rework_authorization")}
        state["role_outputs"]["judge"] = judge
        self._checkpoint(state, "branch-adjudicated")
        return judge, verdict
