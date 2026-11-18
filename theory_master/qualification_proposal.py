"""QualificationProposal: a single explicit phase owner for QualificationSession."""

from __future__ import annotations
import json
from typing import Any
from .contract_stages import require_mainline_goal_priority
from .invocations import stable_hash
from .runtime import now
from .workflow_support import RecoveryStop
from .research_transition import validate_transition
from .workflow_qualification import _REPAIR_REVIEW_PROTOCOL_VERSION
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .qualification_session import QualificationSession


class QualificationProposal:

    def __init__(self, session: QualificationSession):
        self.session = session

    def _load_model_and_feedback(self):
        from .scientific_model import load as load_scientific_model

        self.session.model = load_scientific_model(
            self.session.owner.spec.research_root,
            goal_id=self.session.owner.spec.goal_id,
        )
        from .execution_limit_history import recent_execution_limits
        from .qualification_stop_history import recent_qualification_stops

        self.session.preparation_feedback = {
            "recent_execution_limits": recent_execution_limits(
                self.session.owner.spec.research_root,
                current_cycle=str(self.session.run_id),
            ),
            "recent_qualification_stops": recent_qualification_stops(
                self.session.owner.spec.research_root,
                current_cycle=str(self.session.run_id),
            ),
        }
        self.session.prior_frontier: dict[str, Any] = {}

    def title_for(self, hypothesis: str):
        return " ".join(str(hypothesis).split())

    def normalize_candidate(self, raw: dict[str, Any]):
        hypothesis = str(raw.get("hypothesis") or "").strip()
        item = {
            "id": str(raw.get("id") or "").strip(),
            "title": self.title_for(hypothesis),
            "hypothesis": hypothesis,
            "why_now": str(raw.get("why_now") or "").strip(),
            "goal_advance": str(raw.get("goal_advance") or "").strip(),
            "decisive_test": str(raw.get("decisive_test") or "").strip(),
            "scope": str(raw.get("scope") or ""),
            "canvas_title": str(raw.get("canvas_title") or "").strip(),
            "canvas_summary": str(raw.get("canvas_summary") or "").strip(),
            "lineage_anchor_node_id": str(
                raw.get("lineage_anchor_node_id") or ""
            ).strip(),
            "lineage_anchor_node_ids": list(raw.get("lineage_anchor_node_ids") or []),
            "reconsideration_trigger": str(
                raw.get("reconsideration_trigger") or ""
            ).strip(),
            "rationale": str(raw.get("rationale") or raw.get("why_now") or "").strip(),
            "evidence_refs": list(raw.get("evidence_refs") or []),
            "status": str(raw.get("status") or "provisional"),
        }
        from .hypothesis_lineage import explicit_lineage

        explicit = explicit_lineage(raw)
        if "transition" in raw:
            validate_transition(raw["transition"])
            item["transition"] = json.loads(json.dumps(raw["transition"]))
        if explicit is not None:
            item["lineage_main_node_id"], item["lineage_other_node_ids"] = explicit
            item["lineage_anchor_node_ids"] = [explicit[0], *explicit[1]]
            item["lineage_anchor_node_id"] = explicit[0]
        if "estimand" in raw:
            item["estimand"] = raw.get("estimand")
        parent_candidate_id = str(raw.get("parent_candidate_id") or "").strip()
        if parent_candidate_id:
            item["parent_candidate_id"] = parent_candidate_id
        if parent_candidate_id:
            item["addressed_issue_ids"] = list(raw.get("addressed_issue_ids") or [])
            item["repair_details"] = dict(raw.get("repair_details") or {})
            item["blocker_closures"] = list(raw.get("blocker_closures") or [])
        self.session.evolution_operator = str(
            raw.get("evolution_operator") or ""
        ).strip()
        if (
            self.session.evolution_operator
            and self.session.evolution_operator != "repair"
        ):
            item["evolution_operator"] = self.session.evolution_operator
            if not item["lineage_anchor_node_ids"] and item["lineage_anchor_node_id"]:
                item["lineage_anchor_node_ids"] = [item["lineage_anchor_node_id"]]
            if item["lineage_anchor_node_ids"]:
                item["lineage_anchor_node_id"] = item["lineage_anchor_node_ids"][0]
        if (
            not item["id"]
            or not item["hypothesis"]
            or (not item["why_now"])
            or (not item["decisive_test"])
        ):
            raise ValueError(
                "Research Lead candidate lacks id/hypothesis/why_now/decisive_test"
            )
        require_mainline_goal_priority(
            item,
            goal=self.session.owner.spec.goal,
            research_contract=self.session.owner.spec.contract,
            final_gate=self.session.owner.spec.final_gate,
        )
        return item

    def _recover_legacy_checkpoint(self):
        legacy_checkpoint = (
            self.session.state.get("checkpoint")
            if isinstance(self.session.state.get("checkpoint"), dict)
            else {}
        )
        if legacy_checkpoint.get("name") == "pre_hypothesis_lock":
            legacy_selected = (
                legacy_checkpoint.get("selected_candidate")
                if isinstance(legacy_checkpoint.get("selected_candidate"), dict)
                else {}
            )
            legacy_review = (
                legacy_checkpoint.get("selected_review")
                if isinstance(legacy_checkpoint.get("selected_review"), dict)
                else {}
            )
            legacy_confirmation = (
                legacy_review.get("repair_verification")
                if isinstance(legacy_review.get("repair_verification"), dict)
                else {}
            )
            legacy_id = str(
                legacy_checkpoint.get("selected_candidate_id")
                or legacy_selected.get("id")
                or ""
            )
            legacy_is_confirmed_repair = (
                legacy_review.get("qualification_basis") == "repair_verified"
                and legacy_review.get("verdict") == "qualified"
                and (legacy_confirmation.get("verdict") == "confirmed")
                and (legacy_confirmation.get("candidate_id") == legacy_id)
                and (
                    legacy_review.get("repair_review_protocol_version")
                    != _REPAIR_REVIEW_PROTOCOL_VERSION
                )
            )
            if legacy_is_confirmed_repair:
                parent_id = str(
                    legacy_selected.get("parent_candidate_id")
                    or legacy_review.get("parent_candidate_id")
                    or ""
                )
                raw_frontier = (self.session.state.get("role_outputs") or {}).get(
                    "research_lead_frontier"
                )
                raw_candidates = (
                    raw_frontier.get("candidates")
                    if isinstance(raw_frontier, dict)
                    else []
                )
                raw_parent = next(
                    (
                        item
                        for item in raw_candidates
                        if isinstance(item, dict)
                        and str(item.get("id") or "") == parent_id
                    ),
                    None,
                )
                blockers = legacy_review.get("original_blocking_issues") or []
                if (
                    not parent_id
                    or not legacy_id
                    or (not isinstance(raw_parent, dict))
                    or (not isinstance(blockers, list))
                    or (not blockers)
                ):
                    raise RecoveryStop(
                        "legacy pre-H Lock checkpoint lacks the original Candidate/blocker ledger required for current scientific review"
                    )
                parent = self.normalize_candidate(raw_parent)
                repaired = self.normalize_candidate(legacy_selected)
                if repaired.get("estimand") in (None, "") and parent.get(
                    "estimand"
                ) not in (None, ""):
                    repaired["estimand"] = json.loads(json.dumps(parent["estimand"]))
                blocker_ids = [
                    str(row.get("id") or "")
                    for row in blockers
                    if isinstance(row, dict)
                ]
                if (
                    parent.get("id") != parent_id
                    or repaired.get("id") != legacy_id
                    or len(blocker_ids) != len(blockers)
                    or (not blocker_ids)
                    or ("" in blocker_ids)
                    or (len(set(blocker_ids)) != len(blocker_ids))
                    or (
                        set(repaired.get("addressed_issue_ids") or [])
                        != set(blocker_ids)
                    )
                    or any(
                        (
                            repaired.get(field, "") != parent.get(field, "")
                            for field in (
                                "hypothesis",
                                "scope",
                                "goal_advance",
                                "estimand",
                                "transition",
                            )
                        )
                    )
                    or (
                        " ".join(str(repaired.get("decisive_test") or "").split())
                        == " ".join(str(parent.get("decisive_test") or "").split())
                    )
                ):
                    raise RecoveryStop(
                        "legacy pre-H Lock checkpoint cannot safely preserve repaired Candidate identity for current scientific review"
                    )
                prior_review_output = (
                    self.session.state.get("role_outputs") or {}
                ).get("candidate_review")
                prior_reviews = (
                    prior_review_output.get("reviews")
                    if isinstance(prior_review_output, dict)
                    else []
                )
                original_review = next(
                    (
                        row
                        for row in prior_reviews
                        if isinstance(row, dict)
                        and str(row.get("candidate_id") or "") == parent_id
                        and (row.get("verdict") == "revise")
                        and (row.get("blocking_issues") == blockers)
                    ),
                    None,
                )
                if original_review is None:
                    original_review = {
                        "candidate_id": parent_id,
                        "verdict": "revise",
                        "reason": "Recovered the frozen original blocker ledger from a legacy pre-H Lock checkpoint.",
                        "blocking_issues": json.loads(json.dumps(blockers)),
                    }
                repair_hash = stable_hash(
                    {
                        "parent_id": parent_id,
                        "candidate": repaired,
                        "original_blockers": blockers,
                    }
                )
                self.session.state["qualification_working_set"] = {
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
                    "running_h_fingerprint": self.session.owner._running_h_fingerprint(),
                    "updated_at": now(),
                }
                self.session.state["checkpoint"] = {
                    "name": "repair_verification_recovery_pending",
                    "migrated_from": "pre_hypothesis_lock",
                    "selected_candidate_id": legacy_id,
                    "review_protocol_version": _REPAIR_REVIEW_PROTOCOL_VERSION,
                    "reached_at": now(),
                }
                self.session.state["hypothesis_lock"] = {"status": "unlocked"}
                self.session.state["hypothesis_locked"] = False
                self.session.state["qualification_status"] = "pending"
                self.session.state["qualification_repair_count"] = 1
                self.session.state.update(
                    {
                        "status": "active",
                        "phase": "qualification",
                        "scientific_phase": "qualification",
                        "resumable": True,
                        "execution_active": True,
                    }
                )
                self.session.owner._emit(
                    self.session.state,
                    "qualification.repair_verification_policy_migrated",
                    {
                        "candidate_id": legacy_id,
                        "parent_candidate_id": parent_id,
                        "from_version": legacy_review.get(
                            "repair_review_protocol_version"
                        ),
                        "to_version": _REPAIR_REVIEW_PROTOCOL_VERSION,
                    },
                )
                self.session.owner._persist(self.session.state)

    def _propose_frontier(self):
        self.session.frontier_output = self.session.state["role_outputs"].get(
            "research_lead_frontier"
        )
        working_checkpoint = self.session.state.get("qualification_working_set")
        if (
            self.session.state.get("qualification_status") == "pending"
            and (
                not isinstance(self.session.frontier_output, dict)
                or not self.session.frontier_output.get("candidates")
            )
            and isinstance(working_checkpoint, dict)
            and isinstance(working_checkpoint.get("candidates"), list)
            and working_checkpoint["candidates"]
        ):
            self.session.frontier_output = {
                "candidates": json.loads(json.dumps(working_checkpoint["candidates"]))
            }
            self.session.state["role_outputs"][
                "research_lead_frontier"
            ] = self.session.frontier_output
        self.session.lead_context = None
        if not isinstance(
            self.session.frontier_output, dict
        ) or not self.session.frontier_output.get("candidates"):
            self.session.lead_context = {
                "mode": "cycle_start",
                "goal": self.session.owner.spec.goal,
                "research_contract": self.session.owner.spec.contract,
                "final_gate": self.session.owner.spec.final_gate,
                "required_context": self.session.owner.spec.required_context,
                "scientific_model": self.session.model,
                "frontier": self.session.prior_cycle.get("candidates")
                or self.session.state.get("candidates")
                or [],
                "frontier_state": self.session.prior_cycle.get("frontier_state")
                or self.session.state.get("frontier_state")
                or {},
                "qualification_feedback": (
                    (self.session.state.get("qualification_resume_history") or [])[-1]
                    if self.session.state.get("recovery", {}).get(
                        "qualification_restart"
                    )
                    and self.session.state.get("qualification_resume_history")
                    else None
                ),
                "prior_cycle": self.session.prior_cycle,
                "state": self.session.state,
                **self.session.preparation_feedback,
            }
            prior_literature = (
                self.session.state.get("lead_literature_results") or {}
            ).get("frontier_proposal")
            if isinstance(prior_literature, dict):
                self.session.lead_context["lead_literature_result"] = prior_literature
            self.session.lead_context["running_hypotheses"] = (
                self.session.owner._running_hypotheses_for_lead()
            )
            guidance = (
                self.session.state.get("operator_guidance")
                if self.session.state.get("operator_guidance_pending")
                else None
            )
            from .operator_direction import claim_conjecture, qualification_guidance

            try:
                if "human_direction_checked" not in self.session.state:
                    if self.session.frontier_output is None:
                        from .specification import TaskSpec

                        live_spec = TaskSpec.load(self.session.owner.spec.document)
                        claimed = claim_conjecture(
                            self.session.owner._graph_database_path(),
                            cycle_id=str(self.session.run_id),
                            required_context=live_spec.required_context,
                            research_root=self.session.owner.spec.research_root,
                        )
                        self.session.state["human_direction"] = claimed
                    self.session.state["human_direction_checked"] = True
                    self.session.owner._persist(self.session.state)
                claimed = self.session.state.get("human_direction")
                record = (
                    {
                        **claimed["record"],
                        "source_references": claimed.get("source_references", []),
                    }
                    if isinstance(claimed, dict)
                    else None
                )
                direction = qualification_guidance(
                    "",
                    (
                        str(guidance.get("text") or "")
                        if isinstance(guidance, dict)
                        else ""
                    ),
                    record=record,
                )
            except ValueError as exc:
                raise RecoveryStop(str(exc)) from exc
            if direction:
                self.session.lead_context["operator_guidance"] = direction
            resumed_literature = (
                self.session.owner._resume_reclassified_lead_literature(
                    self.session.state
                )
            )
            if resumed_literature is not None:
                self.session.lead_context["lead_literature_result"] = resumed_literature
                self.session.frontier_output = self.session.owner._call(
                    self.session.state,
                    "research_lead",
                    self.session.lead_context,
                    output_key="research_lead_frontier_after_literature",
                )
            else:
                self.session.frontier_output = self.session.owner._call(
                    self.session.state, "research_lead", self.session.lead_context
                )
            literature_used = self.session.state.get(
                "qualification_literature_used"
            ) or (self.session.state.get("lead_literature_detours") or {}).get(
                "frontier_proposal", 0
            )
            if self.session.frontier_output.get("needs_literature") is True and (
                not literature_used
            ):
                self.session.state["qualification_literature_used"] = True
                literature = self.session.owner._run_lead_literature_detour(
                    self.session.state,
                    self.session.frontier_output.get("literature_request") or {},
                    lead_stage="frontier_proposal",
                )
                self.session.lead_context["lead_literature_result"] = literature
                self.session.frontier_output = self.session.owner._call(
                    self.session.state,
                    "research_lead",
                    self.session.lead_context,
                    output_key="research_lead_frontier_after_literature",
                )
            if self.session.frontier_output.get("needs_literature") is True:
                literature_result = dict(
                    self.session.lead_context.get("lead_literature_result") or {}
                )
                self.session.lead_context["lead_literature_result"] = {
                    **literature_result,
                    "status": "detour_limit_reached",
                    "reason": "The one authorized Literature detour has been used. Use the available external sources and task evidence to produce 1–3 defensible candidates. Do not request another Literature call or invent missing scientific prerequisites.",
                }
                self.session.owner._emit(
                    self.session.state,
                    "qualification.lead_literature_correction",
                    {"lead_stage": "frontier_proposal", "literature_calls": 1},
                )
                self.session.frontier_output = self.session.owner._call(
                    self.session.state,
                    "research_lead",
                    self.session.lead_context,
                    output_key="research_lead_frontier_after_literature_correction",
                )
                if self.session.frontier_output.get("needs_literature") is True:
                    self.session.owner._pause_exhausted_lead_literature(
                        self.session.state, self.session.frontier_output
                    )
            self.session.state["role_outputs"][
                "research_lead_frontier"
            ] = self.session.frontier_output
        claimed = self.session.state.get("human_direction")
        if isinstance(claimed, dict) and claimed.get("status") == "claimed":
            from .operator_direction import consume_conjecture

            self.session.owner._persist(self.session.state)
            consume_conjecture(
                self.session.owner._graph_database_path(),
                external_id=claimed["record"]["external_id"],
                cycle_id=str(self.session.run_id),
                research_root=self.session.owner.spec.research_root,
            )
            claimed["status"] = "consumed"
            self.session.owner._persist(self.session.state)
