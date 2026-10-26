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
    "hypothesis", "scope", "why_now", "goal_advance", "decisive_test",
    "lineage_anchor_node_id", "lineage_anchor_node_ids", "evolution_operator", "reconsideration_trigger",
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
            if key != "evolution_operator" or str(candidate.get("evolution_operator") or "").strip()
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
        if (checkpoint.get("selected_candidate_id") != selected.get("id")
                or assembled != frozen_science_view(selected)):
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
        candidates = ((frontier or {}).get("candidates") if isinstance(frontier, dict) else None)
        if not isinstance(candidates, list) or not candidates:
            candidates = working.get("candidates") if isinstance(working, dict) else []
        derived = [item for item in candidates or [] if isinstance(item, dict) and item.get("evolution_operator")]
        stale_pending_lineage = False
        if qualification_status == "pending" and derived:
            anchors = _scientific_anchor_types(self)
            stale_pending_lineage = any(
                (not sources or any(anchors.get(anchor) != "Hypothesis" for anchor in sources))
                for item in derived
                for sources in [list(item.get("lineage_anchor_node_ids") or [
                    str(item.get("lineage_anchor_node_id") or "").strip()
                ])]
            )
        worker_origin_mismatch = (
            bool(getattr(self, "worker_id", None)) and
            str((state.get("recovery") or {}).get("message") or "") ==
            "selected hypothesis does not originate from the claimed SQLite frontier"
        )
        if qualification_status != "unqualified" and not stale_pending_lineage and not worker_origin_mismatch:
            return False

        role_outputs = state.setdefault("role_outputs", {})
        frontier = role_outputs.get("research_lead_frontier")
        candidate_ids = [
            str(item.get("id")) for item in ((frontier or {}).get("candidates") or [])
            if isinstance(item, dict) and item.get("id")
        ] if isinstance(frontier, dict) else []
        history = list(state.get("qualification_resume_history") or [])
        reviews = (state.get("frontier_review") or {}).get("reviews") or []
        history.append({
            "at": now(),
            **({"stale_frontier": json.loads(json.dumps(frontier)),
                "stale_working_set": json.loads(json.dumps(working))}
               if stale_pending_lineage else {}),
            **({"reviews": json.loads(json.dumps(reviews))} if reviews else {
                "reason": str(state.get("qualification_rationale") or "unqualified qualification"),
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
                if stale_pending_lineage else "previous qualification ended unqualified"
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
        try:
            output = self._call(state, "research_lead", {
                "mode": "hypothesis_display",
                "frozen_hypothesis": {
                    "hypothesis": lock["hypothesis"],
                    "scope": lock["scope"],
                    "decisive_test": lock["decisive_test"],
                },
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

    def _qualify_and_lock(
        self, state: dict[str, Any], run_id: str, prior_cycle: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        # Stop legacy uncommitted recovery before migration or any model call.
        checkpoint = state.get("checkpoint") or {}
        if checkpoint.get("name") == "pre_hypothesis_lock":
            _require_reviewed_candidate(checkpoint.get("selected_candidate") or {},
                                        checkpoint.get("selected_review") or {})
        working = state.get("qualification_working_set") or {}
        stored_candidates = {str(c.get("id") or ""): c for c in working.get("candidates", [])}
        for review in working.get("reviews", []):
            _require_reviewed_candidate(stored_candidates.get(str(review.get("candidate_id") or ""), {}), review)
        if not getattr(self, "_in_proposal_gate", False):
            return self._qualify_under_proposal_gate(state, run_id, prior_cycle)
        from .scientific_model import load as load_scientific_model
        model = load_scientific_model(self.spec.research_root, goal_id=self.spec.goal_id)

        # Previous-Cycle Frontier State must never become Lead science.
        prior_frontier: dict[str, Any] = {}
        # The canonical SQLite graph is the only cross-Cycle H registry.

        def title_for(hypothesis: str) -> str:
            return " ".join(str(hypothesis).split())

        def normalize_candidate(raw: dict[str, Any]) -> dict[str, Any]:
            hypothesis = str(raw.get("hypothesis") or "").strip()
            item = {
                "id": str(raw.get("id") or "").strip(),
                "title": title_for(hypothesis),
                "hypothesis": hypothesis,
                "why_now": str(raw.get("why_now") or "").strip(),
                "goal_advance": str(raw.get("goal_advance") or "").strip(),
                "decisive_test": str(raw.get("decisive_test") or "").strip(),
                "scope": str(raw.get("scope") or ""),
                "canvas_title": str(raw.get("canvas_title") or "").strip(),
                "canvas_summary": str(raw.get("canvas_summary") or "").strip(),
                "lineage_anchor_node_id": str(raw.get("lineage_anchor_node_id") or "").strip(),
                "lineage_anchor_node_ids": list(raw.get("lineage_anchor_node_ids") or []),
                "reconsideration_trigger": str(raw.get("reconsideration_trigger") or "").strip(),
                "rationale": str(raw.get("rationale") or raw.get("why_now") or "").strip(),
                "evidence_refs": list(raw.get("evidence_refs") or []),
                "status": str(raw.get("status") or "provisional"),
            }
            if "estimand" in raw:
                # Preserve any already-frozen estimand through SDK normalization;
                # the repair stage must compare the same object, not two dropped fields.
                item["estimand"] = raw.get("estimand")
            parent_candidate_id = str(raw.get("parent_candidate_id") or "").strip()
            if parent_candidate_id:
                item["parent_candidate_id"] = parent_candidate_id
            # Repair provenance is audit metadata, not a substitute for the
            # binding repaired decisive_test passed to Investigator and Judge.
            if parent_candidate_id:
                item["addressed_issue_ids"] = list(raw.get("addressed_issue_ids") or [])
                item["repair_details"] = dict(raw.get("repair_details") or {})
                item["blocker_closures"] = list(raw.get("blocker_closures") or [])
            evolution_operator = str(raw.get("evolution_operator") or "").strip()
            if evolution_operator and evolution_operator != "repair":
                item["evolution_operator"] = evolution_operator
                if not item["lineage_anchor_node_ids"] and item["lineage_anchor_node_id"]:
                    # Already-recorded single-source candidates remain resumable.
                    item["lineage_anchor_node_ids"] = [item["lineage_anchor_node_id"]]
                if item["lineage_anchor_node_ids"]:
                    item["lineage_anchor_node_id"] = item["lineage_anchor_node_ids"][0]
            if not item["id"] or not item["hypothesis"] or not item["why_now"] or not item["decisive_test"]:
                raise ValueError("Research Lead candidate lacks id/hypothesis/why_now/decisive_test")
            require_mainline_goal_priority(
                item,
                goal=self.spec.goal,
                research_contract=self.spec.contract,
                final_gate=self.spec.final_gate,
            )
            return item

        # Upgrade a legacy pre-H checkpoint whose repaired Candidate was confirmed
        # under the older blocker-only policy. Never let that result silently satisfy
        # the current fixed feasibility/discrimination review; recover the frozen
        # parent + blocker ledger and run the existing one-shot Stage once.
        legacy_checkpoint = state.get("checkpoint") if isinstance(state.get("checkpoint"), dict) else {}
        if legacy_checkpoint.get("name") == "pre_hypothesis_lock":
            legacy_selected = legacy_checkpoint.get("selected_candidate") if isinstance(legacy_checkpoint.get("selected_candidate"), dict) else {}
            legacy_review = legacy_checkpoint.get("selected_review") if isinstance(legacy_checkpoint.get("selected_review"), dict) else {}
            legacy_confirmation = legacy_review.get("repair_verification") if isinstance(legacy_review.get("repair_verification"), dict) else {}
            legacy_id = str(legacy_checkpoint.get("selected_candidate_id") or legacy_selected.get("id") or "")
            legacy_is_confirmed_repair = (
                legacy_review.get("qualification_basis") == "repair_verified"
                and legacy_review.get("verdict") == "qualified"
                and legacy_confirmation.get("verdict") == "confirmed"
                and legacy_confirmation.get("candidate_id") == legacy_id
                and legacy_review.get("repair_review_protocol_version") != _REPAIR_REVIEW_PROTOCOL_VERSION
            )
            if legacy_is_confirmed_repair:
                parent_id = str(legacy_selected.get("parent_candidate_id") or legacy_review.get("parent_candidate_id") or "")
                raw_frontier = (state.get("role_outputs") or {}).get("research_lead_frontier")
                raw_candidates = raw_frontier.get("candidates") if isinstance(raw_frontier, dict) else []
                raw_parent = next((item for item in raw_candidates if isinstance(item, dict)
                                   and str(item.get("id") or "") == parent_id), None)
                blockers = legacy_review.get("original_blocking_issues") or []
                if not parent_id or not legacy_id or not isinstance(raw_parent, dict) or not isinstance(blockers, list) or not blockers:
                    raise RecoveryStop("legacy pre-H Lock checkpoint lacks the original Candidate/blocker ledger required for current scientific review")
                parent = normalize_candidate(raw_parent)
                repaired = normalize_candidate(legacy_selected)
                # Older SDK normalization dropped estimand even though the repair
                # closure copied it from the frozen parent. Restore only that absent
                # legacy field from the parent; never overwrite an explicit mismatch.
                if repaired.get("estimand") in (None, "") and parent.get("estimand") not in (None, ""):
                    repaired["estimand"] = json.loads(json.dumps(parent["estimand"]))
                blocker_ids = [str(row.get("id") or "") for row in blockers if isinstance(row, dict)]
                if (parent.get("id") != parent_id or repaired.get("id") != legacy_id
                        or len(blocker_ids) != len(blockers) or not blocker_ids or "" in blocker_ids
                        or len(set(blocker_ids)) != len(blocker_ids)
                        or set(repaired.get("addressed_issue_ids") or []) != set(blocker_ids)
                        or any(repaired.get(field, "") != parent.get(field, "")
                               for field in ("hypothesis", "scope", "goal_advance", "estimand"))
                        or " ".join(str(repaired.get("decisive_test") or "").split())
                           == " ".join(str(parent.get("decisive_test") or "").split())):
                    raise RecoveryStop("legacy pre-H Lock checkpoint cannot safely preserve repaired Candidate identity for current scientific review")
                prior_review_output = (state.get("role_outputs") or {}).get("candidate_review")
                prior_reviews = prior_review_output.get("reviews") if isinstance(prior_review_output, dict) else []
                original_review = next((row for row in prior_reviews if isinstance(row, dict)
                                        and str(row.get("candidate_id") or "") == parent_id
                                        and row.get("verdict") == "revise"
                                        and row.get("blocking_issues") == blockers), None)
                if original_review is None:
                    # The checkpoint's immutable original_blocking_issues is the
                    # authoritative ledger; reconstruct only its envelope, never a verdict.
                    original_review = {
                        "candidate_id": parent_id,
                        "verdict": "revise",
                        "reason": "Recovered the frozen original blocker ledger from a legacy pre-H Lock checkpoint.",
                        "blocking_issues": json.loads(json.dumps(blockers)),
                    }
                repair_hash = stable_hash({"parent_id": parent_id, "candidate": repaired,
                                           "original_blockers": blockers})
                state["qualification_working_set"] = {
                    "schema": "theory-master/qualification-working-set/v2",
                    "repair_verification": {
                        "status": "repair_verification_pending",
                        "candidate_id": legacy_id,
                        "parent_candidate_id": parent_id,
                        "repair_hash": repair_hash,
                        "original_blockers": json.loads(json.dumps(blockers)),
                        "review_protocol_version": _REPAIR_REVIEW_PROTOCOL_VERSION,
                    },
                    "initial_review_status": "initial_review_completed",
                    "lead_repair_status": "lead_repair_completed",
                    "candidates": [parent, repaired],
                    "reviews": [original_review],
                    "pending_ids": [legacy_id],
                    "repair_count": 1,
                    "running_h_fingerprint": self._running_h_fingerprint(),
                    "updated_at": now(),
                }
                state["checkpoint"] = {
                    "name": "repair_verification_recovery_pending",
                    "migrated_from": "pre_hypothesis_lock",
                    "selected_candidate_id": legacy_id,
                    "review_protocol_version": _REPAIR_REVIEW_PROTOCOL_VERSION,
                    "reached_at": now(),
                }
                state["hypothesis_lock"] = {"status": "unlocked"}
                state["hypothesis_locked"] = False
                state["qualification_status"] = "pending"
                state["qualification_repair_count"] = 1
                state.update({"status": "active", "phase": "qualification",
                              "scientific_phase": "qualification", "resumable": True,
                              "execution_active": True})
                self._emit(state, "qualification.repair_verification_policy_migrated", {
                    "candidate_id": legacy_id, "parent_candidate_id": parent_id,
                    "from_version": legacy_review.get("repair_review_protocol_version"),
                    "to_version": _REPAIR_REVIEW_PROTOCOL_VERSION,
                })
                self._persist(state)
        frontier_output = state["role_outputs"].get("research_lead_frontier")
        working_checkpoint = state.get("qualification_working_set")
        if (state.get("qualification_status") == "pending"
                and (not isinstance(frontier_output, dict) or not frontier_output.get("candidates"))
                and isinstance(working_checkpoint, dict)
                and isinstance(working_checkpoint.get("candidates"), list)
                and working_checkpoint["candidates"]):
            # A checkpoint can outlive the transient frontier role output.
            # Recover its persisted candidates rather than spending another
            # Lead call and replacing already reviewed scientific objects.
            frontier_output = {"candidates": json.loads(json.dumps(working_checkpoint["candidates"]))}
            state["role_outputs"]["research_lead_frontier"] = frontier_output
        lead_context = None
        if not isinstance(frontier_output, dict) or not frontier_output.get("candidates"):
            lead_context = {
                    "mode": "cycle_start", "goal": self.spec.goal, "research_contract": self.spec.contract,
                    "final_gate": self.spec.final_gate, "required_context": self.spec.required_context,
                    "scientific_model": model,
                    "frontier": prior_cycle.get("candidates") or state.get("candidates") or [],
                    "frontier_state": prior_cycle.get("frontier_state") or state.get("frontier_state") or {},
                    "qualification_feedback": (
                        (state.get("qualification_resume_history") or [])[-1]
                        if state.get("recovery", {}).get("qualification_restart")
                        and state.get("qualification_resume_history") else None
                    ),
                    "prior_cycle": prior_cycle, "state": state,
                }
            prior_literature = (state.get("lead_literature_results") or {}).get("frontier_proposal")
            if isinstance(prior_literature, dict):
                lead_context["lead_literature_result"] = prior_literature
            lead_context["running_hypotheses"] = self._running_hypotheses_for_lead()
            guidance = state.get("operator_guidance") if state.get("operator_guidance_pending") else None
            if isinstance(guidance, dict) and guidance.get("text"):
                lead_context["operator_guidance"] = str(guidance.get("text"))
            frontier_output = self._call(state, "research_lead", lead_context)
            if frontier_output.get("needs_literature") is True and not state.get("qualification_literature_used"):
                state["qualification_literature_used"] = True
                literature = self._run_lead_literature_detour(
                    state, frontier_output.get("literature_request") or {},
                    lead_stage="frontier_proposal",
                )
                lead_context["lead_literature_result"] = literature
                frontier_output = self._call(
                    state, "research_lead", lead_context,
                    output_key="research_lead_frontier_after_literature",
                )
                if frontier_output.get("needs_literature") is True:
                    # One bounded Lead correction, never a second Literature call.
                    literature_result = dict(lead_context.get("lead_literature_result") or {})
                    lead_context["lead_literature_result"] = {
                        **literature_result,
                        "status": "detour_limit_reached",
                        "reason": (
                            "The one authorized Literature detour has been used. "
                            "Use the available external sources and task evidence to produce "
                            "1–3 defensible candidates. Do not request another Literature call "
                            "or invent missing scientific prerequisites."
                        ),
                    }
                    self._emit(state, "qualification.lead_literature_correction", {
                        "lead_stage": "frontier_proposal", "literature_calls": 1,
                    })
                    frontier_output = self._call(
                        state, "research_lead", lead_context,
                        output_key="research_lead_frontier_after_literature_correction",
                    )
                    if frontier_output.get("needs_literature") is True and not state.get("qualification_literature_used"):
                        state["qualification_literature_used"] = True
                        self._run_lead_literature_detour(
                            state, frontier_output.get("literature_request") or {},
                            lead_stage="frontier_proposal",
                        )
            state["role_outputs"]["research_lead_frontier"] = frontier_output

        frontier = [normalize_candidate(item) for item in (frontier_output.get("candidates") or []) if isinstance(item, dict)]
        scientific_anchor_types = _scientific_anchor_types(self)
        existing_hypotheses = bool(scientific_anchor_types)
        for item in frontier:
            _require_v2_new_h_candidate(item, has_existing_h=existing_hypotheses)
            operator = str(item.get("evolution_operator") or "").strip()
            anchor = str(item.get("lineage_anchor_node_id") or "").strip()
            sources = item.get("lineage_anchor_node_ids") or []
            if operator:
                if not 1 <= len(sources) <= 3 or len(set(sources)) != len(sources):
                    raise ValueError("derived Hypothesis requires 1..3 unique SQLite-registered H lineage sources")
                if scientific_anchor_types.get(sources[0]) != "Hypothesis":
                    raise ValueError(f"primary lineage anchor must be Hypothesis/Decision: {sources[0]}")
                if any(scientific_anchor_types.get(src) != "Hypothesis"
                       for src in sources):
                    raise ValueError("supplemental lineage source must be Hypothesis/Decision/Finding")
            elif sources:
                raise ValueError("same-H continuation cannot have multiple lineage sources")
            elif not anchor and any(kind in {"Hypothesis", "Decision"}
                                     for kind in scientific_anchor_types.values()):
                raise ValueError("same-H continuation requires explicit lineage_anchor_node_id")
            # Eligibility and unique same-H identity are checked at selection.
        frontier_by_id = {item["id"]: item for item in frontier}
        if len(frontier_by_id) != len(frontier) or not frontier_by_id:
            raise ValueError("Research Lead frontier candidates require unique stable ids")
        reviews_by_id: dict[str, dict[str, Any]] = {}
        final_selection: dict[str, Any] | None = None
        pending_ids = list(frontier_by_id)
        qualification_repair_count = 0
        repair_verification: dict[str, Any] | None = None

        working = state.get("qualification_working_set")
        running_fingerprint = self._running_h_fingerprint()
        if isinstance(working, dict) and working.get("running_h_fingerprint") not in (None, running_fingerprint):
            # G4: another Worker froze an H while this attempt was down.
            working = None
            state.pop("qualification_working_set", None)
            state.get("role_outputs", {}).pop("research_lead_frontier", None)
        if isinstance(working, dict) and working.get("candidates"):
            restored_candidates = [
                json.loads(json.dumps(item)) for item in (working.get("candidates") or [])
                if isinstance(item, dict) and item.get("id") and item.get("hypothesis")
            ]
            restored_by_id = {str(item["id"]): item for item in restored_candidates}
            if restored_by_id:
                frontier_by_id = restored_by_id
                reviews_by_id = {
                    str(item.get("candidate_id")): json.loads(json.dumps(item))
                    for item in (working.get("reviews") or [])
                    if isinstance(item, dict) and str(item.get("candidate_id") or "") in frontier_by_id
                }
                pending_ids = [
                    str(value) for value in (working.get("pending_ids") or [])
                    if str(value) in frontier_by_id
                ]
                if not pending_ids:
                    pending_ids = [item for item in frontier_by_id if item not in reviews_by_id]
                qualification_repair_count = int(working.get("repair_count") or 0)
                repair_verification = working.get("repair_verification") if isinstance(working.get("repair_verification"), dict) else None
                state["qualification_repair_count"] = qualification_repair_count

        def persist_qualification_working_set() -> None:
            state["qualification_working_set"] = {
                "schema": "theory-master/qualification-working-set/v2",
                "repair_verification": json.loads(json.dumps(repair_verification)) if repair_verification else None,
                "initial_review_status": "initial_review_completed" if reviews_by_id else "pending",
                "lead_repair_status": "lead_repair_completed" if qualification_repair_count else "pending",
                "candidates": json.loads(json.dumps(list(frontier_by_id.values()))),
                "reviews": json.loads(json.dumps(list(reviews_by_id.values()))),
                "pending_ids": list(pending_ids),
                "repair_count": qualification_repair_count,
                "running_h_fingerprint": self._running_h_fingerprint(),
                "updated_at": now(),
            }
            self._persist(state)

        def restore_legacy_estimand(parent_id: str, repaired_id: str) -> None:
            raw_frontier = (state.get("role_outputs") or {}).get("research_lead_frontier")
            raw_candidates = raw_frontier.get("candidates") if isinstance(raw_frontier, dict) else []
            raw_parent = next((item for item in raw_candidates if isinstance(item, dict)
                               and str(item.get("id") or "") == parent_id), None)
            frozen_estimand = raw_parent.get("estimand") if isinstance(raw_parent, dict) else None
            if frozen_estimand in (None, ""):
                return
            for candidate_id in (parent_id, repaired_id):
                candidate = frontier_by_id.get(candidate_id)
                if isinstance(candidate, dict) and candidate.get("estimand") in (None, ""):
                    candidate["estimand"] = json.loads(json.dumps(frozen_estimand))

        # Persisted working sets from the previous blocker-only Stage may contain
        # a confirmed result that is no longer sufficient for the current policy.
        # Re-open that same repaired Candidate once; keep the original review/repair
        # and let Invocation Ledger reuse only a closure from the current contract.
        if repair_verification:
            stored_version = int(repair_verification.get("review_protocol_version") or 0)
            stored_status = str(repair_verification.get("status") or "")
            repaired_id = str(repair_verification.get("candidate_id") or "")
            if stored_version != _REPAIR_REVIEW_PROTOCOL_VERSION:
                if stored_status == "repair_verification_pending":
                    repaired = frontier_by_id.get(repaired_id) or {}
                    parent_id = str(repaired.get("parent_candidate_id") or repair_verification.get("parent_candidate_id") or "")
                    original_review = reviews_by_id.get(parent_id) or {}
                    blockers = original_review.get("blocking_issues") or []
                    expected_hash = stable_hash({"parent_id": parent_id, "candidate": repaired,
                                                 "original_blockers": blockers})
                    if (not repaired or original_review.get("verdict") != "revise" or not blockers
                            or repair_verification.get("repair_hash") != expected_hash
                            or repair_verification.get("original_blockers") != blockers):
                        raise RecoveryStop("legacy pending repair review lacks a complete frozen blocker ledger")
                    restore_legacy_estimand(parent_id, repaired_id)
                    repair_verification["repair_hash"] = stable_hash({"parent_id": parent_id, "candidate": repaired,
                                                                       "original_blockers": blockers})
                    stale_review = reviews_by_id.get(repaired_id) or {}
                    if stale_review.get("qualification_basis") in {"repair_verified", "repair_unresolved"}:
                        reviews_by_id.pop(repaired_id, None)
                    repair_verification["review_protocol_version"] = _REPAIR_REVIEW_PROTOCOL_VERSION
                    repair_verification.pop("result", None)
                    pending_ids = [repaired_id]
                    persist_qualification_working_set()
                elif stored_status == "repair_verified":
                    result = repair_verification.get("result") if isinstance(repair_verification.get("result"), dict) else {}
                    stale_review = reviews_by_id.get(repaired_id) or {}
                    if (result.get("verdict") != "confirmed" or result.get("candidate_id") != repaired_id
                            or stale_review.get("qualification_basis") != "repair_verified"
                            or stale_review.get("repair_verification") != result):
                        raise RecoveryStop("legacy confirmed repair review cannot be safely re-opened")
                    repaired = frontier_by_id.get(repaired_id) or {}
                    parent_id = str(repaired.get("parent_candidate_id") or repair_verification.get("parent_candidate_id") or "")
                    original_review = reviews_by_id.get(parent_id) or {}
                    blockers = original_review.get("blocking_issues") or []
                    expected_hash = stable_hash({"parent_id": parent_id, "candidate": repaired,
                                                 "original_blockers": blockers})
                    if (not repaired or original_review.get("verdict") != "revise" or not blockers
                            or repair_verification.get("repair_hash") != expected_hash
                            or repair_verification.get("original_blockers") != blockers):
                        raise RecoveryStop("legacy confirmed repair review lacks a complete frozen blocker ledger")
                    restore_legacy_estimand(parent_id, repaired_id)
                    repair_verification["repair_hash"] = stable_hash({"parent_id": parent_id, "candidate": repaired,
                                                                       "original_blockers": blockers})
                    reviews_by_id.pop(repaired_id, None)
                    repair_verification = {key: value for key, value in repair_verification.items() if key != "result"}
                    repair_verification["status"] = "repair_verification_pending"
                    repair_verification["review_protocol_version"] = _REPAIR_REVIEW_PROTOCOL_VERSION
                    pending_ids = [repaired_id]
                    persist_qualification_working_set()
                elif stored_status != "repair_unresolved":
                    raise RecoveryStop("unknown legacy repair verification state; no scientific approval")

        def stop_unqualified(reason: str, candidate_ids: list[str]) -> None:
            attempts = list(state.get("qualification_attempts") or [])
            attempts.append({
                "round": int(state.get("qualification_proposal_rounds") or 1),
                "reason": reason,
                "candidate_ids": list(candidate_ids),
            })
            state["qualification_attempts"] = attempts
            rounds = int(state.get("qualification_proposal_rounds") or 1)
            if rounds < 3:
                state["qualification_proposal_rounds"] = rounds + 1
                state["qualification_status"] = "pending"
                state.get("role_outputs", {}).pop("research_lead_frontier", None)
                state.pop("qualification_working_set", None)
                history = list(state.get("qualification_resume_history") or [])
                history.append({
                    "reason": reason,
                    "candidate_ids": list(candidate_ids),
                    "reviews": json.loads(json.dumps(list(reviews_by_id.values()))),
                    "angle": "different_scientific_angle",
                })
                state["qualification_resume_history"] = history
                state.setdefault("recovery", {})["qualification_restart"] = True
                self._persist(state)
                raise _ProposalRetry(reason)
            state.update({
                "status": "no_qualified_candidate",
                "phase": "qualification",
                "scientific_phase": "qualification",
                "qualification_status": "no_qualified_candidate",
                "qualification_rationale": reason,
                "hypothesis_lock": {"status": "unlocked"},
                "hypothesis_locked": False,
                "resumable": False,
                "execution_active": False,
                "wake_fingerprint": self._wake_fingerprint(),
                "attention": {
                    "code": "no_qualified_candidate",
                    "reason": reason,
                    "requested_action": "Waiting for a research-state change or an explicit retry_direction.",
                },
            })
            state["frontier_review"] = {
                "status": "unqualified",
                "generated_candidates": list(frontier_by_id.values()),
                "selected_candidate_id": None,
                "reviews": list(reviews_by_id.values()),
            }
            state["evolution_candidates"] = []
            state["selected_next_candidate_id"] = None
            state["next_start_node_id"] = None
            state.pop("qualification_working_set", None)
            self._emit(state, "qualification.no_candidate_qualified", {
                "candidate_ids": list(candidate_ids),
                "reason": reason,
                "hypothesis_locked": False,
                "scientific_goal_passed": False,
            })
            self._persist(state, project=True)
            self._emit(state, "qualification.no_qualified_candidate", {
                "rounds": 3, "reason": reason, "attempts": state.get("qualification_attempts"),
            })
            raise NoQualifiedCandidate(reason)

        challenger_audited = bool(reviews_by_id)
        while True:
            review_ids = [item for item in pending_ids if item not in reviews_by_id]
            if review_ids and challenger_audited:
                # A repaired Candidate is not qualified until a genuine, committed
                # Challenger confirmation. Reconstruct only a PENDING verification
                # for legacy v1 working sets; never reconstruct a verdict.
                if qualification_repair_count != 1 or len(review_ids) != 1:
                    raise RecoveryStop("ambiguous persisted qualification repair; no scientific approval")
                candidate_id = review_ids[0]
                candidate = frontier_by_id[candidate_id]
                parent_id = str(candidate.get("parent_candidate_id") or "")
                parent = frontier_by_id.get(parent_id)
                initial_review = reviews_by_id.get(parent_id)
                if not parent or not initial_review or initial_review.get("verdict") != "revise":
                    raise RecoveryStop("repair recovery missing original Challenger blocker ledger")
                if not candidate.get("decisive_test") or candidate["decisive_test"] == parent["decisive_test"]:
                    raise RecoveryStop("repaired decisive_test is missing or unchanged")
                if any(candidate.get(field, "") != parent.get(field, "") for field in
                       ("hypothesis", "scope", "goal_advance", "estimand")):
                    raise RecoveryStop("repair recovery changed frozen scientific identity")
                blockers = initial_review.get("blocking_issues") or []
                blocker_ids = {str(issue.get("id") or "") for issue in blockers if isinstance(issue, dict)}
                if (not blocker_ids or "" in blocker_ids
                        or set(candidate.get("addressed_issue_ids") or []) != blocker_ids):
                    raise RecoveryStop("repair recovery lacks complete frozen blocker provenance")
                repair_hash = stable_hash({
                    "parent_id": parent_id, "candidate": candidate,
                    "original_blockers": blockers,
                })
                if repair_verification is None:
                    repair_verification = {
                        "status": "repair_verification_pending",
                        "candidate_id": candidate_id,
                        "parent_candidate_id": parent_id,
                        "repair_hash": repair_hash,
                        "original_blockers": json.loads(json.dumps(blockers)),
                    }
                    persist_qualification_working_set()
                if (repair_verification.get("candidate_id") != candidate_id
                        or repair_verification.get("parent_candidate_id") != parent_id
                        or repair_verification.get("repair_hash") != repair_hash
                        or repair_verification.get("original_blockers") != blockers):
                    raise RecoveryStop("repair verification checkpoint does not match frozen scientific contents")
                if repair_verification.get("status") != "repair_verification_pending":
                    raise RecoveryStop("repair verification checkpoint is inconsistent with pending review")
                result = self._call(state, "challenger", {
                    "mode": "repair_verification",
                    "candidates": [candidate],  # invocation identity only; omitted from prompt
                    "original_candidate": parent,
                    "repaired_candidate": candidate,
                    "original_review": initial_review,
                    "repair_hash": repair_hash,
                    "repair_review_protocol_version": _REPAIR_REVIEW_PROTOCOL_VERSION,
                }, output_key="repair_verification")
                # Contract and semantic closure enforce exact Candidate identity
                # and confirmed|unresolved; model/service failures do not reach here.
                confirmed = result["verdict"] == "confirmed"
                repair_verification["review_protocol_version"] = _REPAIR_REVIEW_PROTOCOL_VERSION
                repair_verification["status"] = "repair_verified" if confirmed else "repair_unresolved"
                repair_verification["result"] = json.loads(json.dumps(result))
                reviews_by_id[candidate_id] = {
                    "candidate_id": candidate_id,
                    "verdict": "qualified" if confirmed else "reject",
                    "reason": result["reason"],
                    "qualification_basis": "repair_verified" if confirmed else "repair_unresolved",
                    "parent_candidate_id": parent_id,
                    "original_blocking_issues": json.loads(json.dumps(blockers)),
                    "repair_review_protocol_version": _REPAIR_REVIEW_PROTOCOL_VERSION,
                    "repair_verification": json.loads(json.dumps(result)),
                    "reviewed_science": result["reviewed_science"],
                }
                persist_qualification_working_set()
                continue
            # A legacy synthetic qualified review is not independent scientific
            # confirmation. The already-locked historical Cycles are untouched.
            for candidate_id, review in reviews_by_id.items():
                if review.get("verdict") != "qualified":
                    continue
                candidate = frontier_by_id.get(candidate_id) or {}
                if candidate.get("parent_candidate_id") or review.get("qualification_basis") in {"frozen_blocker_repair", "repair_verified"}:
                    parent_id = str(candidate.get("parent_candidate_id") or "")
                    original_review = reviews_by_id.get(parent_id) or {}
                    blockers = original_review.get("blocking_issues") or []
                    expected_hash = stable_hash({
                        "parent_id": parent_id, "candidate": candidate,
                        "original_blockers": blockers,
                    })
                    result = (repair_verification or {}).get("result") or {}
                    if (not isinstance(result, dict) or result.get("verdict") != "confirmed"
                            or result.get("candidate_id") != candidate_id
                            or review.get("repair_verification") != result
                            or (repair_verification or {}).get("repair_hash") != expected_hash
                            or (repair_verification or {}).get("original_blockers") != blockers
                            or (repair_verification or {}).get("status") != "repair_verified"
                            or (repair_verification or {}).get("review_protocol_version") != _REPAIR_REVIEW_PROTOCOL_VERSION
                            or review.get("repair_review_protocol_version") != _REPAIR_REVIEW_PROTOCOL_VERSION):
                        raise RecoveryStop("repaired Candidate lacks committed Challenger confirmation")
            if review_ids:
                if challenger_audited:
                    raise ValueError("qualification permits exactly one Challenger audit per Cycle")
                # One continuous Challenger conversation performs one exhaustive
                # audit.  Repairs are checked against its frozen blocker ledger;
                # they do not reopen adversarial review or allow moving goalposts.
                review_candidates = [frontier_by_id[item] for item in review_ids]
                review_output = self._call(state, "challenger", {
                    # Keep the full candidate list in runtime memory for deterministic
                    # validation/closure. stage_context exposes only the scientific
                    # candidate seed plus Canvas/workspace navigation access.
                    "candidates": review_candidates,
                    "candidate": _challenger_candidate_seed(review_candidates),
                })
                validated = self._validate_candidate_reviews(
                    review_output.get("reviews") if isinstance(review_output.get("reviews"), list) else [],
                    {item: frontier_by_id[item] for item in review_ids},
                )
                reviews_by_id.update({str(item["candidate_id"]): item for item in validated})
                challenger_audited = True
                state["role_outputs"]["candidate_review"] = {"reviews": list(reviews_by_id.values())}
                persist_qualification_working_set()

            qualified_ids = [candidate_id for candidate_id, review in reviews_by_id.items()
                             if review.get("verdict") == "qualified" and candidate_id in frontier_by_id]
            if qualified_ids:
                if len(qualified_ids) == 1:
                    selected_id = qualified_ids[0]
                    selected_candidate = frontier_by_id[selected_id]
                    final_selection = {
                        "selected_candidate_id": selected_id,
                        "decision": (
                            "Only Challenger-qualified candidate at the current scientific frontier; "
                            f"this Cycle will test: {selected_candidate['hypothesis']}"
                        ),
                    }
                    self._emit(state, "qualification.single_candidate_selected", {
                        "candidate_id": selected_id,
                        "reason": "only_one_qualified_candidate",
                    })
                else:
                    selection = self._call(state, "research_lead", {
                        "phase": "finalization",
                        "candidates": [frontier_by_id[item] for item in qualified_ids],
                        "candidate_reviews": [reviews_by_id[item] for item in qualified_ids],
                    })
                    selected_id = str(selection.get("selected_candidate_id") or "")
                    if selected_id not in qualified_ids:
                        raise ValueError("Research Lead may select only a Challenger-qualified candidate")
                    final_selection = selection
                break

            revise_ids = [candidate_id for candidate_id in pending_ids
                          if (reviews_by_id.get(candidate_id) or {}).get("verdict") == "revise"]
            if not revise_ids:
                rejected = [candidate_id for candidate_id in pending_ids
                            if (reviews_by_id.get(candidate_id) or {}).get("verdict") == "reject"]
                reasons = [
                    f"{candidate_id}: {(reviews_by_id.get(candidate_id) or {}).get('reason', 'rejected')}"
                    for candidate_id in (rejected or pending_ids)
                ]
                reason = "No candidate was scientifically qualified for investigation. " + "; ".join(reasons)
                stop_unqualified(reason, rejected or pending_ids)
            if qualification_repair_count >= 1:
                raise ValueError("qualification repair must not reopen Challenger audit")
            qualification_repair_count += 1
            state["qualification_repair_count"] = qualification_repair_count
            state["qualification_repair_limit"] = 1
            repair_candidates = [frontier_by_id[item] for item in revise_ids]
            repair_reviews = [reviews_by_id[item] for item in revise_ids]
            repair = self._call(state, "research_lead", {
                "phase": "finalization",
                "candidates": repair_candidates,
                "candidate_reviews": repair_reviews,
                "qualification_error": {
                    "code": "candidate_needs_revision", "verdict": "revise",
                    "reason": "No candidate qualified; choose one revise candidate and repair its frozen blocker ledger.",
                    "blocking_issues": [],
                    "repair_attempt": qualification_repair_count,
                    "repair_limit": 1,
                },
                "frontier_state": prior_frontier,
                "required_context": self.spec.required_context,
            })
            selected_parent_id = str(repair.get("selected_parent_candidate_id") or "")
            if len(revise_ids) == 1:
                parent_id = revise_ids[0]
                if selected_parent_id and selected_parent_id != parent_id:
                    raise ValueError("selection repair selected an unknown parent candidate")
            else:
                if selected_parent_id not in revise_ids:
                    raise ValueError("selection repair must choose one Challenger-revise candidate")
                parent_id = selected_parent_id
            parent_review = reviews_by_id[parent_id]
            repair_outcome = str(repair.get("outcome") or "")
            if repair_outcome == "cannot_repair":
                repair_reason = str(
                    repair.get("reason")
                    or repair.get("cannot_repair_reason")
                    or "selection repair could not close the frozen Challenger blocker ledger"
                ).strip()
                # A true Investigation-owned prerequisite should have been
                # qualified by Challenger, NOT encoded as revise. An unresolved
                # frozen revise blocker cannot be promoted to qualified.
                self._emit(state, "qualification.cannot_repair_unqualified", {
                    "candidate_id": parent_id, "reason": repair_reason,
                    "blocking_issues": json.loads(json.dumps(parent_review.get("blocking_issues") or [])),
                })
                stop_unqualified(
                    f"Challenger blocker not repaired for {parent_id}: {repair_reason}",
                    [parent_id],
                )
            if repair_outcome != "resolved":
                raise ValueError(f"unknown selection repair outcome: {repair_outcome or '<empty>'}")
            replacements = repair.get("replacement_candidates") or []
            parent = frontier_by_id[parent_id]
            additions = [normalize_candidate(item) for item in replacements if isinstance(item, dict)]
            if len(additions) != 1:
                raise ValueError("revise verdict requires exactly one SDK-closed repaired candidate")
            for item in additions:
                if item["hypothesis"] != parent["hypothesis"]:
                    raise ValueError("selection repair may not change the frozen parent hypothesis")
                if item["goal_advance"] != parent["goal_advance"]:
                    raise ValueError("selection repair may not change the frozen parent goal_advance")
                if item.get("scope", "") != parent.get("scope", ""):
                    raise ValueError("selection repair may not change the frozen parent scientific scope")
                if item.get("parent_candidate_id") != parent_id:
                    raise ValueError("selection repair lost its frozen parent candidate")
                if item["id"] in frontier_by_id:
                    raise ValueError(f"replacement candidate id already exists: {item['id']}")
                frontier_by_id[item["id"]] = item
                repair_verification = {
                    "status": "repair_verification_pending",
                    "candidate_id": item["id"],
                    "parent_candidate_id": parent_id,
                    "repair_hash": stable_hash({
                        "parent_id": parent_id, "candidate": item,
                        "original_blockers": parent_review.get("blocking_issues") or [],
                    }),
                    "original_blockers": json.loads(json.dumps(parent_review.get("blocking_issues") or [])),
                    "review_protocol_version": _REPAIR_REVIEW_PROTOCOL_VERSION,
                }
            pending_ids = [item["id"] for item in additions]
            persist_qualification_working_set()

        if not final_selection:
            raise ValueError("qualification did not produce a qualified selection")
        selected_id = str(final_selection["selected_candidate_id"])
        selected = frontier_by_id[selected_id]
        selected_review = reviews_by_id[selected_id]
        _require_reviewed_candidate(selected, selected_review)
        decision = str(final_selection.get("decision") or "").strip()
        decisive_test = str(selected["decisive_test"])
        hypothesis = str(selected["hypothesis"])
        active_title = str(selected["title"])
        active_hypothesis = hypothesis
        if scientific_anchor_types and not str(selected.get("evolution_operator") or "").strip():
            raise ValueError("Each new Cycle in the SQLite graph must declare an evolution_operator")
        if getattr(self, "worker_id", None) and getattr(self, "worker_claims", None):
            self.worker_claims.assert_writer(
                self.worker_id, self.worker_claim_id, self.worker_claim_epoch,
                cycle_id=str(run_id),
            )
        evolution_operator = str(selected.get("evolution_operator") or "").strip()
        lineage_anchor_node_id = str(selected.get("lineage_anchor_node_id") or "").strip()
        hypothesis_ref = None  # Every new Cycle H is allocated and registered in SQLite.
        from .multi_worker import reserve_hypothesis_id
        hypothesis_ref = reserve_hypothesis_id(
            self.spec.research_root, self._graph_database_path(),
            cycle_id=str(run_id),
        )

        cycle_criteria = make_cycle_criteria(decisive_test)
        # Qualification freezes only the scientific hypothesis. Investigation
        # planning begins after lock inside the unified Investigator.
        initial_executor = "investigator"
        initial_executor_reason = "qualified_hypothesis_enters_unified_investigator"
        state["frontier_review"] = {"status": "finalized", "generated_candidates": list(frontier_by_id),
                                    "selected_candidate_id": selected_id, "selection_rationale": decision,
                                    "reviews": list(reviews_by_id.values())}
        state["qualification_status"] = "qualified"
        state["qualification_rationale"] = decision
        state["cycle_start_candidates"] = list(frontier_by_id.values())
        state["cycle_start_rationale"] = decision
        state["role_outputs"]["research_lead_start"] = dict(final_selection)

        finalized = {**final_selection, "id": hypothesis_ref, "title": active_title, "hypothesis": active_hypothesis,
                     "next_executor": initial_executor, "decisive_test": decisive_test,
                     "cycle_criteria": cycle_criteria,
                     "initial_investigation_intent": decisive_test}
        # Current task-mode Cycle IDs are plain natural numbers (`1`, `2`, ...).
        # Historical states may use `cycle-0001` or the duplicated
        # `cycle-cycle-0001`; keep those readable without migrating them.
        canonical_branch_id = str(run_id)
        compatible_branch_ids = {canonical_branch_id}
        if canonical_branch_id.isdigit():
            compatible_branch_ids.add("cycle-" + canonical_branch_id)
        elif canonical_branch_id.startswith("cycle-"):
            compatible_branch_ids.add("cycle-" + canonical_branch_id)
        branch = next(
            (item for item in state.get("branches", [])
             if str(item.get("id") or "") in compatible_branch_ids),
            None,
        )
        if branch is None:
            branch = {"id": canonical_branch_id, "title": active_title, "hypothesis": active_hypothesis,
                      "hypothesis_ref": hypothesis_ref,
                      "scope": str(selected.get("scope") or ""),
                      "status": "testing",
                      "source_node_id": lineage_anchor_node_id or None,
                      "source_node_ids": list(selected.get("lineage_anchor_node_ids") or []),
                      "timestamp": canvas_ts()}
            state["branches"].append(branch)
        state["candidates"] = self._merge_candidates([*state.get("candidates", []), *state["cycle_start_candidates"]], branch)
        state["active_branch_id"] = branch["id"]
        branch["hypothesis_ref"] = hypothesis_ref
        if lineage_anchor_node_id:
            branch["source_node_id"] = lineage_anchor_node_id
        if evolution_operator:
            branch["source_node_ids"] = list(selected.get("lineage_anchor_node_ids") or [lineage_anchor_node_id])
        branch.pop("hypothesis_id", None)
        branch.pop("hypothesis_relation", None)
        branch["scope"] = str(selected.get("scope") or "")
        branch["decisive_test"] = decisive_test
        branch["reconsideration_trigger"] = str(
            selected.get("reconsideration_trigger") or ""
        )
        branch["cycle_criteria"] = json.loads(json.dumps(cycle_criteria))
        for legacy_field in ("success_criteria", "failure_criteria", "cycle_progress_criteria", "hypothesis_terminal_criteria"):
            branch.pop(legacy_field, None)
        if self.stop_before == "hypothesis_lock":
            checkpoint = {
                "name": "pre_hypothesis_lock",
                "selected_candidate_id": selected_id,
                "selected_candidate": json.loads(json.dumps(selected)),
                "selected_review": json.loads(json.dumps(selected_review)),
                "selection_rationale": decision,
                "finalized": json.loads(json.dumps(finalized)),
                "initial_executor": initial_executor,
                "initial_executor_reason": initial_executor_reason,
                "qualification_repair_count": qualification_repair_count,
                "reached_at": now(),
            }
            state.update({
                "status": "checkpoint_reached",
                "phase": "pre_hypothesis_lock",
                "checkpoint": checkpoint,
                "resumable": False,
                "execution_active": False,
                "hypothesis_locked": False,
                "scientific_phase": "qualification",
            })
            state["hypothesis_lock"] = {"status": "unlocked"}
            state.pop("qualification_working_set", None)
            self._emit(state, "workflow.checkpoint_reached", {
                "checkpoint": "pre_hypothesis_lock",
                "selected_candidate_id": selected_id,
                "qualification_repair_count": qualification_repair_count,
            })
            self._persist(state)
            raise RecoveryStop("pre_hypothesis_lock checkpoint reached")
        from .goal_terminal import science_commit_barrier
        committing = {
            "status": "committing", "id": hypothesis_ref, "candidate_id": selected_id,
            "title": active_title, "hypothesis": active_hypothesis, "scope": selected.get("scope", ""),
            "decisive_test": decisive_test,
            "cycle_criteria": json.loads(json.dumps(cycle_criteria)),
            "initial_investigation_intent": decisive_test,
            "candidate_provenance": json.loads(json.dumps(selected)),
            "qualification": {"selection_rationale": decision, "review": json.loads(json.dumps(selected_review)),
                              "literature_evidence_refs": self._literature_evidence_refs()},
        }
        state["hypothesis_lock"] = committing
        state["hypothesis_locked"] = False
        self._persist(state)
        with science_commit_barrier(self.spec.research_root, "shared"):
            self._author_hypothesis_display(state, selected)
            state["hypothesis_lock"]["candidate_provenance"] = json.loads(json.dumps(selected))
            self._ensure_locked_hypothesis(state, branch, selected)
            if getattr(self, "worker_id", None) and getattr(self, "worker_claims", None):
                self.worker_claims.bind_hypothesis(
                    self.worker_id, self.worker_claim_id, self.worker_claim_epoch, hypothesis_ref,
                )
            state["hypothesis_lock"] = {**state["hypothesis_lock"], "status": "locked", "locked_at": now()}
            state["hypothesis_locked"] = True
            state.pop("qualification_working_set", None)
            state["scientific_phase"] = "locked"
            state["current_state"] = direction_text(active_title, active_hypothesis)
            self._checkpoint(state, "hypothesis-lock")
        if state.get("hypothesis_lock", {}).get("status") != "locked":
            raise RuntimeError("Investigation requires a locked hypothesis")
        state["initial_executor"] = initial_executor
        state["initial_executor_reason"] = initial_executor_reason
        self._emit(state, "investigation.initial_executor_selected", {
            "executor": initial_executor, "reason": initial_executor_reason,
        })
        return branch, finalized, active_title, active_hypothesis
