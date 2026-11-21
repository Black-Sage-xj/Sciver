from __future__ import annotations

"""Research Lead/Challenger qualification and Hypothesis Lock flow."""

import json
import re
from typing import Any

from .contract_stages import require_mainline_goal_priority
from .invocations import stable_hash
from .cycle_criteria import make_cycle_criteria

from .display import direction_text
from .runtime import canvas_ts, now
from .workflow_support import RecoveryStop
from .scientific_objective import frozen_science_view, require_review_binding
from .research_transition import validate_transition


def _require_reviewed_candidate(candidate: dict[str, Any], review: dict[str, Any]) -> None:
    if review.get("candidate_id") != candidate.get("id"):
        raise RecoveryStop("scientific review candidate identity changed")
    try:
        require_review_binding(str(candidate.get("id") or ""), frozen_science_view(candidate),
                               review.get("reviewed_science"))
    except (KeyError, ValueError) as exc:
        raise RecoveryStop(str(exc)) from exc


class NoQualifiedCandidate(Exception):
    """Lead produced no qualified H within the bounded proposal rounds."""


class _ProposalRetry(Exception):
    """Try another scientific angle inside the same Proposal Gate hold."""


_CHALLENGER_CANDIDATE_FIELDS = (
    "hypothesis", "scope", "why_now", "goal_advance", "decisive_test", "transition",
    "lineage_anchor_node_id", "lineage_anchor_node_ids", "lineage_main_node_id",
    "lineage_other_node_ids", "evolution_operator", "reconsideration_trigger", "evaluation_materials",
)


def _require_v2_new_h_candidate(candidate: dict[str, Any], *, has_existing_h: bool) -> None:
    """Reject same-H proposals before Challenger or selection spends work on them."""
    if has_existing_h and not str(candidate.get("evolution_operator") or "").strip():
        raise ValueError("Each new V2 Cycle needs a Lead-authored new H with an evolution_operator")


def _challenger_candidate_seed(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepare the only candidate science that Challenger receives in its prompt."""
    return [
        {
            key: candidate.get(key, "")
            for key in ("id", *_CHALLENGER_CANDIDATE_FIELDS)
            if (key != "evolution_operator" or str(candidate.get("evolution_operator") or "").strip())
            and (key != "transition" or key in candidate)
            and (key != "evaluation_materials" or key in candidate)
        }
        for candidate in candidates
    ]




def _scientific_anchor_types(workflow: Any) -> dict[str, str]:
    """Return every registered H identity from the canonical SQLite graph."""
    graph_path = workflow._graph_database_path()
    from .science_graph_store import ScienceGraphStore
    with ScienceGraphStore(graph_path, readonly=True) as store:
        return {
            str(row["id"]): "Hypothesis"
            for row in store.db.execute("SELECT id FROM hypotheses ORDER BY id")
        }


_REPAIR_REVIEW_PROTOCOL_VERSION = 2


class QualificationMixin:
    def _pause_exhausted_lead_literature(
        self, state: dict[str, Any], frontier_output: dict[str, Any],
    ) -> None:
        reason = (
            "Research Lead still needs external scholarship after the qualification "
            "Literature detour and one bounded correction. No candidate is ready "
            "for qualification; the Hypothesis remains unlocked."
        )
        state["role_outputs"]["research_lead_frontier"] = frontier_output
        state.update(
            status="needs_attention", phase="qualification", scientific_phase="qualification",
            qualification_status="pending", resumable=True, execution_active=False,
            human_attention=reason,
            attention={
                "code": "lead_literature_detour_exhausted",
                "reason": reason,
                "lead_stage": "frontier_proposal",
                "detour_limit": 1,
                "literature_request": json.loads(json.dumps(frontier_output.get("literature_request") or {})),
                "requested_action": "Provide the missing sources or scientific guidance before resuming qualification.",
                "resume_behavior": "Resume continues qualification using existing Literature; no additional automatic detour is authorized.",
            },
        )
        self._emit(state, "lead.literature_detour_exhausted", dict(state["attention"]))
        self._persist(state, project=True)
        raise RecoveryStop(reason)

    def _lock_prequalified_checkpoint(self, state: dict[str, Any]) -> bool:
        checkpoint = state.get("checkpoint") if isinstance(state.get("checkpoint"), dict) else {}
        if checkpoint.get("name") != "pre_hypothesis_lock":
            return False
        selected = checkpoint.get("selected_candidate") or {}
        selected_review = checkpoint.get("selected_review") or {}
        _require_reviewed_candidate(selected, selected_review)
        if selected_review.get("verdict") != "qualified":
            raise RecoveryStop("pre-H Lock requires a qualified scientific review")
        if selected.get("parent_candidate_id") or selected_review.get("qualification_basis") in {"frozen_blocker_repair", "repair_verified"}:
            confirmation = selected_review.get("repair_verification") or {}
            if (selected_review.get("qualification_basis") != "repair_verified"
                    or selected_review.get("verdict") != "qualified"
                    or confirmation.get("verdict") != "confirmed"
                    or confirmation.get("candidate_id") != checkpoint.get("selected_candidate_id")):
                raise RecoveryStop("pre-H Lock checkpoint lacks independent Challenger repair confirmation")
            if selected_review.get("repair_review_protocol_version") != _REPAIR_REVIEW_PROTOCOL_VERSION:
                # A legacy confirmation used the narrower blocker-only review. Defer
                # locking so _qualify_and_lock can migrate the checkpoint into one
                # current-policy review without replaying Lead repair or candidate_review.
                return False
        finalized = checkpoint.get("finalized") or {}
        decisive_test = finalized.get("decisive_test") or finalized.get("cycle_milestone", "")
        assembled = {"hypothesis": finalized.get("hypothesis"), "scope": selected.get("scope", ""),
                     "decisive_test": decisive_test}
        if "evaluation_materials" in finalized:
            assembled["evaluation_materials"] = finalized["evaluation_materials"]
        if (checkpoint.get("selected_candidate_id") != selected.get("id")
                or frozen_science_view(assembled) != frozen_science_view(selected)):
            raise RecoveryStop("pre-H Lock science differs from the reviewed candidate")
        criteria = make_cycle_criteria(decisive_test)
        state["hypothesis_lock"] = {
            "status": "locked", "locked_at": now(),
            "id": finalized.get("id") or finalized.get("hypothesis_id"),
            "candidate_id": checkpoint.get("selected_candidate_id"),
            "title": finalized.get("title"), "hypothesis": finalized.get("hypothesis"),
            "scope": selected.get("scope", ""),
            "decisive_test": decisive_test,
            "cycle_criteria": json.loads(json.dumps(criteria)),
            "initial_investigation_intent": finalized.get("initial_investigation_intent", ""),
            "candidate_provenance": json.loads(json.dumps(selected)),
            **({"evaluation_materials": json.loads(json.dumps(selected["evaluation_materials"]))}
               if "evaluation_materials" in selected else {}),
            "qualification": {
                "selection_rationale": checkpoint.get("selection_rationale", ""),
                "review": json.loads(json.dumps(checkpoint.get("selected_review") or {})),
                "literature_evidence_refs": self._literature_evidence_refs(),
            },
        }
        recovered_executor = checkpoint.get("initial_executor") or finalized.get("next_executor") or "investigator"
        recovered_reason = checkpoint.get("initial_executor_reason") or "prequalified_checkpoint"
        state.update({
            "hypothesis_locked": True, "status": "active", "phase": "hypothesis_lock",
            "resumable": True, "execution_active": True, "scientific_phase": "locked",
            "initial_executor": recovered_executor,
            "initial_executor_reason": recovered_reason,
            "next_executor": recovered_executor,
            "current_state": direction_text(finalized.get("title", ""), finalized.get("hypothesis", "")),
        })
        state["checkpoint"] = {**checkpoint, "consumed_at": now()}
        self._checkpoint(state, "hypothesis-lock")
        return True

    def _restart_unqualified_qualification(self, state: dict[str, Any]) -> bool:
        """Restart an unlocked, exhausted qualification from Research Lead.

        Qualification outputs are attempt-local.  Reusing a persisted frontier
        after an ``unqualified`` stop makes resume enter directly at Challenger,
        even though the human-facing contract says the research preparation must
        be revised before continuing.  Keep the role threads for conversational
        continuity, but invalidate the stale qualification products so the next
        scientific turn is a fresh Research Lead frontier proposal.
        """
        lock = state.get("hypothesis_lock") or {}
        if str(lock.get("status") or "unlocked") == "locked":
            return False
        qualification_status = str(state.get("qualification_status") or "").lower()
        # A normal pending checkpoint is a valid continuation. A derived
        # candidate whose saved SQLite lineage anchor disappeared must be rebuilt.
        working = state.get("qualification_working_set")
        frontier = state.get("role_outputs", {}).get("research_lead_frontier")
        guidance = state.get("operator_guidance") or {}
        literature_guidance_restart = (
            qualification_status == "pending"
            and isinstance(frontier, dict) and frontier.get("needs_literature") is True
            and (state.get("qualification_literature_used")
                 or (state.get("lead_literature_detours") or {}).get("frontier_proposal", 0))
            and state.get("operator_guidance_pending")
            and isinstance(guidance, dict) and bool(str(guidance.get("text") or "").strip())
        )
        candidates = ((frontier or {}).get("candidates") if isinstance(frontier, dict) else None)
        if not isinstance(candidates, list) or not candidates:
            candidates = working.get("candidates") if isinstance(working, dict) else []
        derived = [item for item in candidates or [] if isinstance(item, dict) and item.get("evolution_operator")]
        from .hypothesis_lineage import lineage_sources
        stale_pending_lineage = False
        if qualification_status == "pending" and derived:
            anchors = _scientific_anchor_types(self)
            stale_pending_lineage = any(
                (not sources or any(anchors.get(anchor) != "Hypothesis" for anchor in sources))
                for item in derived
                for sources in [lineage_sources(item)]
            )
        worker_origin_mismatch = (
            bool(getattr(self, "worker_id", None)) and
            str((state.get("recovery") or {}).get("message") or "") ==
            "selected hypothesis does not originate from the claimed SQLite frontier"
        )
        if (qualification_status != "unqualified" and not stale_pending_lineage
                and not worker_origin_mismatch and not literature_guidance_restart):
            return False

        role_outputs = state.setdefault("role_outputs", {})
        frontier = role_outputs.get("research_lead_frontier")
        candidate_ids = [
            str(item.get("id")) for item in ((frontier or {}).get("candidates") or [])
            if isinstance(item, dict) and item.get("id")
        ] if isinstance(frontier, dict) else []
        history = list(state.get("qualification_resume_history") or [])
        state["qualification_restart_generation"] = max(
            int(state.get("qualification_restart_generation") or 0), len(history),
        ) + 1
        reviews = (state.get("frontier_review") or {}).get("reviews") or []
        history.append({
            "at": now(),
            **({"literature_request": json.loads(json.dumps(frontier.get("literature_request") or {}))}
               if literature_guidance_restart else
               {"stale_frontier": json.loads(json.dumps(frontier)),
                "stale_working_set": json.loads(json.dumps(working))}
               if stale_pending_lineage else {}),
            **({"reviews": json.loads(json.dumps(reviews))} if reviews else {
                "reason": ("new operator guidance for an exhausted Literature request"
                           if literature_guidance_restart else
                           str(state.get("qualification_rationale") or "unqualified qualification")),
                "candidate_ids": candidate_ids,
            }),
        })
        state["qualification_resume_history"] = history[-20:]

        # Rejected/revise blockers remain authoritative during recovery.
        # A persisted cannot_repair is NOT a qualification: restart the Lead
        # frontier after attention rather than forging an approved review.
        for key in ("research_lead", "challenger", "research_lead_frontier", "candidate_review", "research_lead_start"):
            role_outputs.pop(key, None)
        state.pop("qualification_working_set", None)

        state["frontier_review"] = {
            "status": "pending", "generated_candidates": [],
            "selected_candidate_id": None, "reviews": [],
        }
        state.pop("qualification_error", None)
        state.update({
            "qualification_status": "pending",
            "qualification_rationale": "",
            "status": "active",
            "phase": "qualification",
            "scientific_phase": "qualification",
            "human_attention": "",
            "attention": {},
            "active_role": None,
            "current_turn_id": None,
            "next_executor": None,
        })
        recovery = dict(state.get("recovery") or {})
        recovery.update({
            "qualification_restart": True,
            "qualification_restart_reason": (
                "stale persisted lineage anchor requires Research Lead reconciliation"
                if stale_pending_lineage else
                "new operator guidance for an exhausted Literature request"
                if literature_guidance_restart else "previous qualification ended unqualified"
            ),
        })
        state["recovery"] = recovery
        return True

    def _author_hypothesis_display(self, state: dict[str, Any], selected: dict[str, Any]) -> None:
        """Create a faithful display compression after the scientific H is frozen."""
        from .hypothesis_display import (
            compose_hypothesis_summary, fallback_hypothesis_display,
            is_compliant_hypothesis_summary, normalize_hypothesis_display,
        )

        lock = state.get("hypothesis_lock") or {}
        h_id = str(lock.get("id") or "")
        from .scientific_objective import locked_science_view
        try:
            locked_science_view(lock)
        except (KeyError, ValueError) as exc:
            raise RecoveryStop(str(exc)) from exc
        lock_status = lock.get("status")
        if lock_status not in {"locked", "committing"} or not h_id:
            raise ValueError("hypothesis_display requires a frozen Hypothesis")
        if lock_status == "committing":
            # Normal qualification and crash recovery run faithful display
            # compression after the candidate has been frozen into the commit
            # record but before its SQLite H transaction is finalized.
            # Require the complete frozen boundary; do not admit a provisional
            # candidate or let display generation invent missing science.
            missing_frozen_fields = [
                field for field in ("hypothesis", "scope", "decisive_test")
                if not isinstance(lock.get(field), str) or not lock[field].strip()
            ]
            if missing_frozen_fields:
                raise ValueError(
                    "hypothesis_display committing record is missing frozen fields: "
                    + ", ".join(missing_frozen_fields)
                )
        fallback_title = selected.get("title") or selected.get("hypothesis") or h_id
        previous = state.get("hypothesis_display") or {}
        if previous.get("hypothesis_id") == h_id and previous.get("status") in {
            "generated", "fallback", "existing",
        }:
            title, summary = normalize_hypothesis_display(
                h_id, title=previous.get("canvas_title"), summary=previous.get("canvas_summary"),
                fallback_title=fallback_title,
            )
            selected["canvas_title"], selected["canvas_summary"] = title, summary
            previous["canvas_title"], previous["canvas_summary"] = title, summary
            state["hypothesis_display"] = previous
            return

        graph_path = self._graph_database_path()
        from .science_graph_store import ScienceGraphStore
        with ScienceGraphStore(graph_path, readonly=True) as store:
            existing = store.db.execute(
                "SELECT h.title,h.summary,o.verdict,o.cycle_id,o.judge_rationale,"
                "o.decisive_test,o.summary AS outcome_summary "
                "FROM hypotheses h LEFT JOIN outcomes o ON o.hypothesis_id=h.id WHERE h.id=?",
                (h_id,),
            ).fetchone()
        if existing is not None:
            adjudicated = bool(
                str(existing["cycle_id"] or "").strip()
                or str(existing["judge_rationale"] or "").strip()
                or str(existing["decisive_test"] or "").strip()
                or str(existing["outcome_summary"] or "").strip()
            )
            old_summary = str(existing["summary"] or "")
            title, summary = normalize_hypothesis_display(
                h_id, title=existing["title"], summary=old_summary,
                fallback_title=fallback_title, scientific_status=existing["verdict"],
                adjudicated=adjudicated,
            )
            selected["canvas_title"], selected["canvas_summary"] = title, summary
            state["hypothesis_display"] = {
                "hypothesis_id": h_id,
                "status": "existing" if (
                    str(existing["title"] or "").strip() == title
                    and is_compliant_hypothesis_summary(old_summary)
                ) else "fallback",
                "canvas_title": title,
                "canvas_summary": summary,
            }
            return

        status_before, phase_before, attention_before = (
            state.get("status"), state.get("phase"), state.get("attention"),
        )
        claim = conditions = ""
        provenance = lock.get("candidate_provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        frozen_reasoning = {
            key: json.loads(json.dumps(provenance[key]))
            for key in ("transition", "why_now", "goal_advance", "lineage_main_node_id",
                        "lineage_other_node_ids", "lineage_anchor_node_id", "lineage_anchor_node_ids",
                        "evolution_operator")
            if key in provenance and provenance[key] not in (None, "", [], {})
        }
        try:
            output = self._call(state, "research_lead", {
                "mode": "hypothesis_display",
                "frozen_hypothesis": {
                    "hypothesis": lock["hypothesis"],
                    "scope": lock["scope"],
                    "decisive_test": lock["decisive_test"],
                },
                "frozen_reasoning": frozen_reasoning,
            }, output_key="research_lead_hypothesis_display")
            title = str(output["canvas_title"]).strip()
            claim = str(output["canvas_claim"]).strip()
            conditions = str(output["canvas_conditions"]).strip()
            summary = compose_hypothesis_summary(claim, conditions)
            display_status = "generated"
        except RecoveryStop:
            raise
        except Exception as exc:
            # Display failure is not a scientific failure. Fall back to the
            # immutable frozen proposition instead of a placeholder summary.
            state["status"], state["phase"], state["attention"] = status_before, phase_before, attention_before
            title, summary = fallback_hypothesis_display(
                h_id, preferred_title=fallback_title, long_text=lock.get("hypothesis"),
            )
            claim = conditions = ""
            display_status = "fallback"
            self._emit(state, "hypothesis.display_fallback", {
                "hypothesis_id": h_id, "reason": type(exc).__name__,
            })
        selected["canvas_title"], selected["canvas_summary"] = title, summary
        state["hypothesis_display"] = {
            "hypothesis_id": h_id, "status": display_status,
            "canvas_title": title, "canvas_summary": summary,
            "canvas_claim": claim, "canvas_conditions": conditions,
        }
        provenance = lock.get("candidate_provenance")
        if isinstance(provenance, dict):
            provenance["canvas_title"], provenance["canvas_summary"] = title, summary

    def _proposal_gate(self):
        gate = getattr(self, "worker_proposal_gate", None)
        if gate is not None:
            return gate
        from .multi_worker import ProposalGate
        return ProposalGate(self.spec.research_root, self._graph_database_path())

    def _qualify_under_proposal_gate(
        self, state: dict[str, Any], run_id: str, prior_cycle: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        """Hold the serial Proposal Gate from Lead context through H freeze (I-01, I-03)."""
        import os
        from .runtime_manager import process_start_ticks
        worker = str(getattr(self, "worker_id", None) or "single")
        claim_id = getattr(self, "worker_claim_id", None) if worker != "single" else None
        epoch = getattr(self, "worker_claim_epoch", None) if worker != "single" else None
        pid = os.getpid()
        self._in_proposal_gate = True
        try:
            with self._proposal_gate().holding(
                worker_id=worker, claim_id=claim_id, epoch=epoch,
                owner_pid=pid, owner_start_ticks=process_start_ticks(pid),
            ) as token:
                self._proposal_gate_token = token
                while True:
                    try:
                        return self._qualify_and_lock(state, run_id, prior_cycle)
                    except _ProposalRetry:
                        continue
        finally:
            self._in_proposal_gate = False
            self._proposal_gate_token = None

    def _running_h_fingerprint(self) -> list[str]:
        return sorted(str(row.get("hypothesis_id") or "") for row in self._running_hypotheses_for_lead()
                      if row.get("hypothesis_id"))

    def _wake_fingerprint(self) -> dict[str, Any]:
        from .worker_runtime import research_direction_fingerprint
        return research_direction_fingerprint(self.spec)

    def _running_hypotheses_for_lead(self) -> list[dict[str, Any]]:
        """G5: frozen H still in a non-terminal Cycle, plus an incomplete freeze."""
        import json
        from .specification import list_cycle_dirs
        own = self.cycle_dir.name if self.cycle_dir is not None else ""
        rows: list[dict[str, Any]] = []
        claims = getattr(self, "worker_claims", None)
        if claims is not None and hasattr(claims, "running_hypotheses"):
            rows.extend(item for item in claims.running_hypotheses()
                        if str(item.get("cycle_id") or "") != own and item.get("hypothesis_id"))
        root = self.spec.research_root
        seen = {str(item.get("hypothesis_id")) for item in rows}
        for cycle in list_cycle_dirs(root, task_mode=getattr(self.spec, "task_mode", True)):
            if cycle.name == own:
                continue
            try:
                saved = json.loads((cycle / "research_state.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            lock = saved.get("hypothesis_lock") if isinstance(saved.get("hypothesis_lock"), dict) else {}
            if lock.get("status") != "committing":
                continue
            h_id = str(lock.get("id") or "")
            if not h_id or h_id in seen:
                continue
            rows.append({
                "worker_id": saved.get("worker_id"), "cycle_id": cycle.name,
                "hypothesis_id": h_id, "status": "freeze_incomplete",
                "title": lock.get("title"), "hypothesis": lock.get("hypothesis"),
                "decisive_test": lock.get("decisive_test"),
            })
        enriched = []
        for row in rows:
            if row.get("hypothesis") and row.get("decisive_test"):
                enriched.append(row)
                continue
            cycle_id = str(row.get("cycle_id") or "")
            path = root / cycle_id / "research_state.json"
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                lock = saved.get("hypothesis_lock") if isinstance(saved.get("hypothesis_lock"), dict) else {}
            except (OSError, ValueError):
                lock = {}
            enriched.append({
                **row,
                "title": row.get("title") or lock.get("title"),
                "hypothesis": row.get("hypothesis") or lock.get("hypothesis"),
                "decisive_test": row.get("decisive_test") or lock.get("decisive_test"),
            })
        return enriched

    def _mark_stopped_for_goal(self, state: dict[str, Any]) -> None:
        state.update({
            "status": "stopped_goal_completed",
            "phase": "goal_completed_stop",
            "resumable": False,
            "execution_active": False,
        })
        self._emit(state, "workflow.stopped_goal_completed", {
            "cycle": self.cycle_dir.name if self.cycle_dir is not None else None,
        })
        self._persist(state)

    def _qualify_and_lock(self, state: dict[str, Any], run_id: str, prior_cycle: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        checkpoint = state.get('checkpoint') or {}
        if checkpoint.get('name') == 'pre_hypothesis_lock':
            _require_reviewed_candidate(checkpoint.get('selected_candidate') or {}, checkpoint.get('selected_review') or {})
        working = state.get('qualification_working_set') or {}
        stored_candidates = {str(c.get('id') or ''): c for c in working.get('candidates', [])}
        for review in working.get('reviews', []):
            _require_reviewed_candidate(stored_candidates.get(str(review.get('candidate_id') or ''), {}), review)
        if not getattr(self, '_in_proposal_gate', False):
            return self._qualify_under_proposal_gate(state, run_id, prior_cycle)
        from .qualification_session import QualificationSession
        return QualificationSession(self, state, run_id, prior_cycle).run()
