"""QualificationLockCommit: a single explicit phase owner for QualificationSession."""

from __future__ import annotations
import json
from .cycle_criteria import make_cycle_criteria
from .display import direction_text
from .runtime import canvas_ts, now
from .workflow_support import RecoveryStop
from .workflow_qualification import _require_reviewed_candidate
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .qualification_session import QualificationSession


class QualificationLockCommit:

    def __init__(self, session: QualificationSession):
        self.session = session

    def _prepare_selected_hypothesis(self):
        if not self.session.final_selection:
            raise ValueError("qualification did not produce a qualified selection")
        self.session.selected_id = str(
            self.session.final_selection["selected_candidate_id"]
        )
        self.session.selected = self.session.frontier_by_id[self.session.selected_id]
        self.session.selected_review = self.session.reviews_by_id[
            self.session.selected_id
        ]
        _require_reviewed_candidate(self.session.selected, self.session.selected_review)
        from .judge_followup import candidate_problem
        problem = candidate_problem(self.session.selected, self.session.preparation_feedback.get("judge_followup"))
        if problem:
            self.session.review.stop_unqualified(problem, [self.session.selected_id])
        self.session.decision = str(
            self.session.final_selection.get("decision") or ""
        ).strip()
        self.session.decisive_test = str(self.session.selected["decisive_test"])
        self.session.hypothesis = str(self.session.selected["hypothesis"])
        self.session.active_title = str(self.session.selected["title"])
        self.session.active_hypothesis = self.session.hypothesis
        if self.session.scientific_anchor_types and (
            not str(self.session.selected.get("evolution_operator") or "").strip()
        ):
            raise ValueError(
                "Each new Cycle in the SQLite graph must declare an evolution_operator"
            )
        if getattr(self.session.owner, "worker_id", None) and getattr(
            self.session.owner, "worker_claims", None
        ):
            self.session.owner.worker_claims.assert_writer(
                self.session.owner.worker_id,
                self.session.owner.worker_claim_id,
                self.session.owner.worker_claim_epoch,
                cycle_id=str(self.session.run_id),
            )
        self.session.evolution_operator = str(
            self.session.selected.get("evolution_operator") or ""
        ).strip()
        self.session.lineage_anchor_node_id = str(
            self.session.selected.get("lineage_anchor_node_id") or ""
        ).strip()
        self.session.hypothesis_ref = None
        from .multi_worker import reserve_hypothesis_id

        self.session.hypothesis_ref = reserve_hypothesis_id(
            self.session.owner.spec.research_root,
            self.session.owner._graph_database_path(),
            cycle_id=str(self.session.run_id),
        )
        self.session.cycle_criteria = make_cycle_criteria(self.session.decisive_test)
        self.session.initial_executor = "investigator"
        self.session.initial_executor_reason = (
            "qualified_hypothesis_enters_unified_investigator"
        )
        self.session.state["frontier_review"] = {
            "status": "finalized",
            "generated_candidates": list(self.session.frontier_by_id),
            "selected_candidate_id": self.session.selected_id,
            "selection_rationale": self.session.decision,
            "reviews": list(self.session.reviews_by_id.values()),
        }
        self.session.state["qualification_status"] = "qualified"
        self.session.state["qualification_rationale"] = self.session.decision
        self.session.state["cycle_start_candidates"] = list(
            self.session.frontier_by_id.values()
        )
        self.session.state["cycle_start_rationale"] = self.session.decision
        self.session.state["role_outputs"]["research_lead_start"] = dict(
            self.session.final_selection
        )
        self.session.finalized = {
            **self.session.final_selection,
            "id": self.session.hypothesis_ref,
            "title": self.session.active_title,
            "hypothesis": self.session.active_hypothesis,
            "next_executor": self.session.initial_executor,
            "decisive_test": self.session.decisive_test,
            "cycle_criteria": self.session.cycle_criteria,
            "initial_investigation_intent": self.session.decisive_test,
            **({"evaluation_materials": json.loads(json.dumps(self.session.selected["evaluation_materials"]))}
               if "evaluation_materials" in self.session.selected else {}),
        }

    def _commit_hypothesis_lock(self):
        self.session.canonical_branch_id = str(self.session.run_id)
        self.session.compatible_branch_ids = {self.session.canonical_branch_id}
        if self.session.canonical_branch_id.isdigit():
            self.session.compatible_branch_ids.add(
                "cycle-" + self.session.canonical_branch_id
            )
        elif self.session.canonical_branch_id.startswith("cycle-"):
            self.session.compatible_branch_ids.add(
                "cycle-" + self.session.canonical_branch_id
            )
        self.session.branch = next(
            (
                item
                for item in self.session.state.get("branches", [])
                if str(item.get("id") or "") in self.session.compatible_branch_ids
            ),
            None,
        )
        if self.session.branch is None:
            self.session.branch = {
                "id": self.session.canonical_branch_id,
                "title": self.session.active_title,
                "hypothesis": self.session.active_hypothesis,
                "hypothesis_ref": self.session.hypothesis_ref,
                "scope": str(self.session.selected.get("scope") or ""),
                "status": "testing",
                "source_node_id": self.session.lineage_anchor_node_id or None,
                "source_node_ids": list(
                    self.session.selected.get("lineage_anchor_node_ids") or []
                ),
                "timestamp": canvas_ts(),
            }
            self.session.state["branches"].append(self.session.branch)
        self.session.state["candidates"] = self.session.owner._merge_candidates(
            [
                *self.session.state.get("candidates", []),
                *self.session.state["cycle_start_candidates"],
            ],
            self.session.branch,
        )
        self.session.state["active_branch_id"] = self.session.branch["id"]
        self.session.branch["hypothesis_ref"] = self.session.hypothesis_ref
        if self.session.lineage_anchor_node_id:
            self.session.branch["source_node_id"] = self.session.lineage_anchor_node_id
        if self.session.evolution_operator:
            self.session.branch["source_node_ids"] = list(
                self.session.selected.get("lineage_anchor_node_ids")
                or [self.session.lineage_anchor_node_id]
            )
        self.session.branch.pop("hypothesis_id", None)
        self.session.branch.pop("hypothesis_relation", None)
        self.session.branch["scope"] = str(self.session.selected.get("scope") or "")
        self.session.branch["decisive_test"] = self.session.decisive_test
        self.session.branch["reconsideration_trigger"] = str(
            self.session.selected.get("reconsideration_trigger") or ""
        )
        self.session.branch["cycle_criteria"] = json.loads(
            json.dumps(self.session.cycle_criteria)
        )
        for legacy_field in (
            "success_criteria",
            "failure_criteria",
            "cycle_progress_criteria",
            "hypothesis_terminal_criteria",
        ):
            self.session.branch.pop(legacy_field, None)
        if self.session.owner.stop_before == "hypothesis_lock":
            checkpoint = {
                "name": "pre_hypothesis_lock",
                "selected_candidate_id": self.session.selected_id,
                "selected_candidate": json.loads(json.dumps(self.session.selected)),
                "selected_review": json.loads(json.dumps(self.session.selected_review)),
                "selection_rationale": self.session.decision,
                "finalized": json.loads(json.dumps(self.session.finalized)),
                "initial_executor": self.session.initial_executor,
                "initial_executor_reason": self.session.initial_executor_reason,
                "qualification_repair_count": self.session.qualification_repair_count,
                "reached_at": now(),
            }
            self.session.state.update(
                {
                    "status": "checkpoint_reached",
                    "phase": "pre_hypothesis_lock",
                    "checkpoint": checkpoint,
                    "resumable": False,
                    "execution_active": False,
                    "hypothesis_locked": False,
                    "scientific_phase": "qualification",
                }
            )
            self.session.state["hypothesis_lock"] = {"status": "unlocked"}
            self.session.state.pop("qualification_working_set", None)
            self.session.owner._emit(
                self.session.state,
                "workflow.checkpoint_reached",
                {
                    "checkpoint": "pre_hypothesis_lock",
                    "selected_candidate_id": self.session.selected_id,
                    "qualification_repair_count": self.session.qualification_repair_count,
                },
            )
            self.session.owner._persist(self.session.state)
            raise RecoveryStop("pre_hypothesis_lock checkpoint reached")
        from .goal_terminal import science_commit_barrier

        committing = {
            "status": "committing",
            "id": self.session.hypothesis_ref,
            "candidate_id": self.session.selected_id,
            "title": self.session.active_title,
            "hypothesis": self.session.active_hypothesis,
            "scope": self.session.selected.get("scope", ""),
            "decisive_test": self.session.decisive_test,
            "cycle_criteria": json.loads(json.dumps(self.session.cycle_criteria)),
            "initial_investigation_intent": self.session.decisive_test,
            "candidate_provenance": json.loads(json.dumps(self.session.selected)),
            **({"evaluation_materials": json.loads(json.dumps(self.session.selected["evaluation_materials"]))}
               if "evaluation_materials" in self.session.selected else {}),
            "qualification": {
                "selection_rationale": self.session.decision,
                "review": json.loads(json.dumps(self.session.selected_review)),
                "literature_evidence_refs": self.session.owner._literature_evidence_refs(),
            },
        }
        self.session.state["hypothesis_lock"] = committing
        self.session.state["hypothesis_locked"] = False
        self.session.owner._persist(self.session.state)
        with science_commit_barrier(self.session.owner.spec.research_root, "shared"):
            self.session.owner._author_hypothesis_display(
                self.session.state, self.session.selected
            )
            self.session.state["hypothesis_lock"]["candidate_provenance"] = json.loads(
                json.dumps(self.session.selected)
            )
            self.session.owner._ensure_locked_hypothesis(
                self.session.state, self.session.branch, self.session.selected
            )
            if getattr(self.session.owner, "worker_id", None) and getattr(
                self.session.owner, "worker_claims", None
            ):
                self.session.owner.worker_claims.bind_hypothesis(
                    self.session.owner.worker_id,
                    self.session.owner.worker_claim_id,
                    self.session.owner.worker_claim_epoch,
                    self.session.hypothesis_ref,
                )
            self.session.state["hypothesis_lock"] = {
                **self.session.state["hypothesis_lock"],
                "status": "locked",
                "locked_at": now(),
            }
            self.session.state["hypothesis_locked"] = True
            self.session.state.pop("qualification_working_set", None)
            self.session.state["scientific_phase"] = "locked"
            self.session.state["current_state"] = direction_text(
                self.session.active_title, self.session.active_hypothesis
            )
            self.session.owner._checkpoint(self.session.state, "hypothesis-lock")
        if self.session.state.get("hypothesis_lock", {}).get("status") != "locked":
            raise RuntimeError("Investigation requires a locked hypothesis")
        self.session.state["initial_executor"] = self.session.initial_executor
        self.session.state["initial_executor_reason"] = (
            self.session.initial_executor_reason
        )
        self.session.owner._emit(
            self.session.state,
            "investigation.initial_executor_selected",
            {
                "executor": self.session.initial_executor,
                "reason": self.session.initial_executor_reason,
            },
        )
        return (
            self.session.branch,
            self.session.finalized,
            self.session.active_title,
            self.session.active_hypothesis,
        )
