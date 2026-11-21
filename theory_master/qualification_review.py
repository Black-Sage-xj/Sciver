"""QualificationReview: a single explicit phase owner for QualificationSession."""

from __future__ import annotations
import json
from .invocations import stable_hash
from .workflow_support import RecoveryStop
from .workflow_qualification import (
    NoQualifiedCandidate,
    _ProposalRetry,
    _challenger_candidate_seed,
    _REPAIR_REVIEW_PROTOCOL_VERSION,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .qualification_session import QualificationSession


class QualificationReview:

    def __init__(self, session: QualificationSession):
        self.session = session

    def stop_unqualified(self, reason: str, candidate_ids: list[str], *, allow_retry: bool = True):
        attempts = list(self.session.state.get("qualification_attempts") or [])
        attempts.append(
            {
                "round": int(
                    self.session.state.get("qualification_proposal_rounds") or 1
                ),
                "reason": reason,
                "candidate_ids": list(candidate_ids),
            }
        )
        self.session.state["qualification_attempts"] = attempts
        rounds = int(self.session.state.get("qualification_proposal_rounds") or 1)
        supervisor = self.session.state.get("supervisor_qualification") or {}
        if allow_retry and rounds < 3 and not supervisor:
            self.session.state["qualification_proposal_rounds"] = rounds + 1
            self.session.state["qualification_status"] = "pending"
            self.session.state.get("role_outputs", {}).pop(
                "research_lead_frontier", None
            )
            self.session.state.pop("qualification_working_set", None)
            history = list(self.session.state.get("qualification_resume_history") or [])
            self.session.state["qualification_restart_generation"] = (
                max(
                    int(
                        self.session.state.get("qualification_restart_generation") or 0
                    ),
                    len(history),
                )
                + 1
            )
            history.append(
                {
                    "reason": reason,
                    "candidate_ids": list(candidate_ids),
                    "reviews": json.loads(
                        json.dumps(list(self.session.reviews_by_id.values()))
                    ),
                    "angle": "different_scientific_angle",
                }
            )
            self.session.state["qualification_resume_history"] = history
            self.session.state.setdefault("recovery", {})[
                "qualification_restart"
            ] = True
            self.session.owner._persist(self.session.state)
            raise _ProposalRetry(reason)
        if not supervisor:
            from .supervisor_qualification_recovery import review_exhausted_frontier

            stopped = {
                **self.session.state,
                "frontier_review": {"generated_candidates": list(self.session.frontier_by_id.values())},
            }
            alternative = review_exhausted_frontier(self.session, stopped)
            if alternative is not None and alternative.get("candidates"):
                raise _ProposalRetry("Supervisor selected a candidate for qualification")
            supervisor = self.session.state.get("supervisor_qualification") or {}
        if supervisor.get("status") == "proposed":
            from .supervisor_qualification_recovery import mark_selection_not_qualified

            mark_selection_not_qualified(self.session, reason)
        elif supervisor.get("status") in {"waiting", "delivery_planned", "delivery_reviewed"}:
            reason = supervisor["reason"]
        self.session.state.update(
            {
                "status": "no_qualified_candidate",
                "phase": "qualification",
                "scientific_phase": "qualification",
                "qualification_status": "no_qualified_candidate",
                "qualification_rationale": reason,
                "hypothesis_lock": {"status": "unlocked"},
                "hypothesis_locked": False,
                "resumable": False,
                "execution_active": False,
                "wake_fingerprint": self.session.owner._wake_fingerprint(),
                "attention": {
                    "code": "no_qualified_candidate",
                    "reason": reason,
                    "requested_action": "This local qualification attempt is exhausted. Automatic fresh-Lead retry is bounded at the same known research direction; repeated unchanged failure waits for new evidence/direction or explicit operator retry. Stop and resource/runtime guards still apply.",
                },
            }
        )
        self.session.state["frontier_review"] = {
            "status": "unqualified",
            "generated_candidates": list(self.session.frontier_by_id.values()),
            "selected_candidate_id": None,
            "reviews": list(self.session.reviews_by_id.values()),
        }
        if supervisor.get("wake_condition"):
            self.session.state["attention"]["wake_condition"] = supervisor["wake_condition"]
        self.session.state["evolution_candidates"] = []
        self.session.state["selected_next_candidate_id"] = None
        self.session.state["next_start_node_id"] = None
        self.session.state.pop("qualification_working_set", None)
        self.session.owner._emit(
            self.session.state,
            "qualification.no_candidate_qualified",
            {
                "candidate_ids": list(candidate_ids),
                "reason": reason,
                "hypothesis_locked": False,
                "scientific_goal_passed": False,
            },
        )
        self.session.owner._persist(self.session.state, project=True)
        self.session.owner._emit(
            self.session.state,
            "qualification.no_qualified_candidate",
            {
                "rounds": rounds,
                "reason": reason,
                "attempts": self.session.state.get("qualification_attempts"),
            },
        )
        raise NoQualifiedCandidate(reason)

    def _review_pending_candidates(self):
        review_ids = [
            item
            for item in self.session.pending_ids
            if item not in self.session.reviews_by_id
        ]
        if review_ids and self.session.challenger_audited:
            if self.session.qualification_repair_count != 1 or len(review_ids) != 1:
                raise RecoveryStop(
                    "ambiguous persisted qualification repair; no scientific approval"
                )
            candidate_id = review_ids[0]
            candidate = self.session.frontier_by_id[candidate_id]
            parent_id = str(candidate.get("parent_candidate_id") or "")
            parent = self.session.frontier_by_id.get(parent_id)
            initial_review = self.session.reviews_by_id.get(parent_id)
            if (
                not parent
                or not initial_review
                or initial_review.get("verdict") != "revise"
            ):
                raise RecoveryStop(
                    "repair recovery missing original Challenger blocker ledger"
                )
            if (
                not candidate.get("decisive_test")
                or candidate["decisive_test"] == parent["decisive_test"]
            ):
                raise RecoveryStop("repaired decisive_test is missing or unchanged")
            if any(
                (
                    candidate.get(field, "") != parent.get(field, "")
                    for field in (
                        "hypothesis",
                        "scope",
                        "goal_advance",
                        "estimand",
                        "evaluation_materials",
                        "transition",
                    )
                )
            ):
                raise RecoveryStop("repair recovery changed frozen scientific identity")
            blockers = initial_review.get("blocking_issues") or []
            blocker_ids = {
                str(issue.get("id") or "")
                for issue in blockers
                if isinstance(issue, dict)
            }
            if (
                not blocker_ids
                or "" in blocker_ids
                or set(candidate.get("addressed_issue_ids") or []) != blocker_ids
            ):
                raise RecoveryStop(
                    "repair recovery lacks complete frozen blocker provenance"
                )
            repair_hash = stable_hash(
                {
                    "parent_id": parent_id,
                    "candidate": candidate,
                    "original_blockers": blockers,
                }
            )
            if self.session.repair_verification is None:
                self.session.repair_verification = {
                    "status": "repair_verification_pending",
                    "candidate_id": candidate_id,
                    "parent_candidate_id": parent_id,
                    "repair_hash": repair_hash,
                    "original_blockers": json.loads(json.dumps(blockers)),
                }
                self.session.recovery.persist_qualification_working_set()
            if (
                self.session.repair_verification.get("candidate_id") != candidate_id
                or self.session.repair_verification.get("parent_candidate_id")
                != parent_id
                or self.session.repair_verification.get("repair_hash") != repair_hash
                or (
                    self.session.repair_verification.get("original_blockers")
                    != blockers
                )
            ):
                raise RecoveryStop(
                    "repair verification checkpoint does not match frozen scientific contents"
                )
            if (
                self.session.repair_verification.get("status")
                != "repair_verification_pending"
            ):
                raise RecoveryStop(
                    "repair verification checkpoint is inconsistent with pending review"
                )
            result = self.session.owner._call(
                self.session.state,
                "challenger",
                {
                    **self.session.preparation_feedback,
                    "mode": "repair_verification",
                    "candidates": [candidate],
                    "original_candidate": parent,
                    "repaired_candidate": candidate,
                    "original_review": initial_review,
                    "repair_hash": repair_hash,
                    "repair_review_protocol_version": _REPAIR_REVIEW_PROTOCOL_VERSION,
                },
                output_key="repair_verification",
            )
            confirmed = result["verdict"] == "confirmed"
            self.session.repair_verification["review_protocol_version"] = (
                _REPAIR_REVIEW_PROTOCOL_VERSION
            )
            self.session.repair_verification["status"] = (
                "repair_verified" if confirmed else "repair_unresolved"
            )
            self.session.repair_verification["result"] = json.loads(json.dumps(result))
            self.session.reviews_by_id[candidate_id] = {
                "candidate_id": candidate_id,
                "verdict": "qualified" if confirmed else "reject",
                "reason": result["reason"],
                "qualification_basis": (
                    "repair_verified" if confirmed else "repair_unresolved"
                ),
                "parent_candidate_id": parent_id,
                "original_blocking_issues": json.loads(json.dumps(blockers)),
                "repair_review_protocol_version": _REPAIR_REVIEW_PROTOCOL_VERSION,
                "repair_verification": json.loads(json.dumps(result)),
                "reviewed_science": result["reviewed_science"],
            }
            self.session.recovery.persist_qualification_working_set()
            return True
        for candidate_id, review in self.session.reviews_by_id.items():
            if review.get("verdict") != "qualified":
                continue
            candidate = self.session.frontier_by_id.get(candidate_id) or {}
            if candidate.get("parent_candidate_id") or review.get(
                "qualification_basis"
            ) in {"frozen_blocker_repair", "repair_verified"}:
                parent_id = str(candidate.get("parent_candidate_id") or "")
                original_review = self.session.reviews_by_id.get(parent_id) or {}
                blockers = original_review.get("blocking_issues") or []
                expected_hash = stable_hash(
                    {
                        "parent_id": parent_id,
                        "candidate": candidate,
                        "original_blockers": blockers,
                    }
                )
                result = (self.session.repair_verification or {}).get("result") or {}
                if (
                    not isinstance(result, dict)
                    or result.get("verdict") != "confirmed"
                    or result.get("candidate_id") != candidate_id
                    or (review.get("repair_verification") != result)
                    or (
                        (self.session.repair_verification or {}).get("repair_hash")
                        != expected_hash
                    )
                    or (
                        (self.session.repair_verification or {}).get(
                            "original_blockers"
                        )
                        != blockers
                    )
                    or (
                        (self.session.repair_verification or {}).get("status")
                        != "repair_verified"
                    )
                    or (
                        (self.session.repair_verification or {}).get(
                            "review_protocol_version"
                        )
                        != _REPAIR_REVIEW_PROTOCOL_VERSION
                    )
                    or (
                        review.get("repair_review_protocol_version")
                        != _REPAIR_REVIEW_PROTOCOL_VERSION
                    )
                ):
                    raise RecoveryStop(
                        "repaired Candidate lacks committed Challenger confirmation"
                    )
        if review_ids:
            if self.session.challenger_audited:
                raise ValueError(
                    "qualification permits exactly one Challenger audit per Cycle"
                )
            review_candidates = [
                self.session.frontier_by_id[item] for item in review_ids
            ]
            review_output = self.session.owner._call(
                self.session.state,
                "challenger",
                {
                    **self.session.preparation_feedback,
                    "candidates": review_candidates,
                    "candidate": _challenger_candidate_seed(review_candidates),
                },
            )
            validated = self.session.owner._validate_candidate_reviews(
                (
                    review_output.get("reviews")
                    if isinstance(review_output.get("reviews"), list)
                    else []
                ),
                {item: self.session.frontier_by_id[item] for item in review_ids},
            )
            self.session.reviews_by_id.update(
                {str(item["candidate_id"]): item for item in validated}
            )
            self.session.challenger_audited = True
            self.session.state["role_outputs"]["candidate_review"] = {
                "reviews": list(self.session.reviews_by_id.values())
            }
            self.session.recovery.persist_qualification_working_set()
        return False

    def _select_qualified_candidate(self):
        qualified_ids = [
            candidate_id
            for candidate_id, review in self.session.reviews_by_id.items()
            if review.get("verdict") == "qualified"
            and candidate_id in self.session.frontier_by_id
        ]
        if qualified_ids:
            if len(qualified_ids) == 1:
                self.session.selected_id = qualified_ids[0]
                selected_candidate = self.session.frontier_by_id[
                    self.session.selected_id
                ]
                self.session.final_selection = {
                    "selected_candidate_id": self.session.selected_id,
                    "decision": f"Only Challenger-qualified candidate at the current scientific frontier; this Cycle will test: {selected_candidate['hypothesis']}",
                }
                self.session.owner._emit(
                    self.session.state,
                    "qualification.single_candidate_selected",
                    {
                        "candidate_id": self.session.selected_id,
                        "reason": "only_one_qualified_candidate",
                    },
                )
            else:
                selection = self.session.owner._call(
                    self.session.state,
                    "research_lead",
                    {
                        "phase": "finalization",
                        "candidates": [
                            self.session.frontier_by_id[item] for item in qualified_ids
                        ],
                        "candidate_reviews": [
                            self.session.reviews_by_id[item] for item in qualified_ids
                        ],
                    },
                )
                self.session.selected_id = str(
                    selection.get("selected_candidate_id") or ""
                )
                if self.session.selected_id not in qualified_ids:
                    raise ValueError(
                        "Research Lead may select only a Challenger-qualified candidate"
                    )
                self.session.final_selection = selection
            return True
        return False

    def _repair_candidate(self):
        revise_ids = [
            candidate_id
            for candidate_id in self.session.pending_ids
            if (self.session.reviews_by_id.get(candidate_id) or {}).get("verdict")
            == "revise"
        ]
        if not revise_ids:
            rejected = [
                candidate_id
                for candidate_id in self.session.pending_ids
                if (self.session.reviews_by_id.get(candidate_id) or {}).get("verdict")
                == "reject"
            ]
            reasons = [
                f"{candidate_id}: {(self.session.reviews_by_id.get(candidate_id) or {}).get('reason', 'rejected')}"
                for candidate_id in rejected or self.session.pending_ids
            ]
            reason = (
                "No candidate was scientifically qualified for investigation. "
                + "; ".join(reasons)
            )
            self.stop_unqualified(reason, rejected or self.session.pending_ids)
        if self.session.qualification_repair_count >= 1:
            raise ValueError("qualification repair must not reopen Challenger audit")
        self.session.qualification_repair_count += 1
        self.session.state["qualification_repair_count"] = (
            self.session.qualification_repair_count
        )
        self.session.state["qualification_repair_limit"] = 1
        repair_candidates = [self.session.frontier_by_id[item] for item in revise_ids]
        repair_reviews = [self.session.reviews_by_id[item] for item in revise_ids]
        repair = self.session.owner._call(
            self.session.state,
            "research_lead",
            {
                "phase": "finalization",
                "candidates": repair_candidates,
                "candidate_reviews": repair_reviews,
                "qualification_error": {
                    "code": "candidate_needs_revision",
                    "verdict": "revise",
                    "reason": "No candidate qualified; choose one revise candidate and repair its frozen blocker ledger.",
                    "blocking_issues": [],
                    "repair_attempt": self.session.qualification_repair_count,
                    "repair_limit": 1,
                },
                "frontier_state": self.session.prior_frontier,
                "research_contract": self.session.owner.spec.contract,
                "required_context": self.session.owner.spec.required_context,
            },
        )
        selected_parent_id = str(repair.get("selected_parent_candidate_id") or "")
        if len(revise_ids) == 1:
            parent_id = revise_ids[0]
            if selected_parent_id and selected_parent_id != parent_id:
                raise ValueError(
                    "selection repair selected an unknown parent candidate"
                )
        else:
            if selected_parent_id not in revise_ids:
                raise ValueError(
                    "selection repair must choose one Challenger-revise candidate"
                )
            parent_id = selected_parent_id
        parent_review = self.session.reviews_by_id[parent_id]
        repair_outcome = str(repair.get("outcome") or "")
        if repair_outcome == "cannot_repair":
            repair_reason = str(
                repair.get("reason")
                or repair.get("cannot_repair_reason")
                or "selection repair could not close the frozen Challenger blocker ledger"
            ).strip()
            self.session.owner._emit(
                self.session.state,
                "qualification.cannot_repair_unqualified",
                {
                    "candidate_id": parent_id,
                    "reason": repair_reason,
                    "blocking_issues": json.loads(
                        json.dumps(parent_review.get("blocking_issues") or [])
                    ),
                },
            )
            self.stop_unqualified(
                f"Challenger blocker not repaired for {parent_id}: {repair_reason}",
                [parent_id],
            )
        if repair_outcome != "resolved":
            raise ValueError(
                f"unknown selection repair outcome: {repair_outcome or '<empty>'}"
            )
        replacements = repair.get("replacement_candidates") or []
        parent = self.session.frontier_by_id[parent_id]
        additions = [
            self.session.proposal.normalize_candidate(item)
            for item in replacements
            if isinstance(item, dict)
        ]
        if len(additions) != 1:
            raise ValueError(
                "revise verdict requires exactly one SDK-closed repaired candidate"
            )
        for item in additions:
            if item["hypothesis"] != parent["hypothesis"]:
                raise ValueError(
                    "selection repair may not change the frozen parent hypothesis"
                )
            if item["goal_advance"] != parent["goal_advance"]:
                raise ValueError(
                    "selection repair may not change the frozen parent goal_advance"
                )
            if item.get("scope", "") != parent.get("scope", ""):
                raise ValueError(
                    "selection repair may not change the frozen parent scientific scope"
                )
            if item.get("transition") != parent.get("transition"):
                raise ValueError(
                    "selection repair may not change the frozen parent transition"
                )
            if item.get("parent_candidate_id") != parent_id:
                raise ValueError("selection repair lost its frozen parent candidate")
            if item["id"] in self.session.frontier_by_id:
                raise ValueError(
                    f"replacement candidate id already exists: {item['id']}"
                )
            self.session.frontier_by_id[item["id"]] = item
            self.session.repair_verification = {
                "status": "repair_verification_pending",
                "candidate_id": item["id"],
                "parent_candidate_id": parent_id,
                "repair_hash": stable_hash(
                    {
                        "parent_id": parent_id,
                        "candidate": item,
                        "original_blockers": parent_review.get("blocking_issues") or [],
                    }
                ),
                "original_blockers": json.loads(
                    json.dumps(parent_review.get("blocking_issues") or [])
                ),
                "review_protocol_version": _REPAIR_REVIEW_PROTOCOL_VERSION,
            }
        self.session.pending_ids = [item["id"] for item in additions]
        self.session.recovery.persist_qualification_working_set()
