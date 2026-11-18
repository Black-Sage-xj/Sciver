"""QualificationRecovery: a single explicit phase owner for QualificationSession."""

from __future__ import annotations
import json
from typing import Any
from .invocations import stable_hash
from .runtime import now
from .workflow_support import RecoveryStop
from .workflow_qualification import (
    _require_v2_new_h_candidate,
    _scientific_anchor_types,
    _REPAIR_REVIEW_PROTOCOL_VERSION,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .qualification_session import QualificationSession


class QualificationRecovery:

    def __init__(self, session: QualificationSession):
        self.session = session

    def _restore_working_set(self):
        self.session.frontier = [
            self.session.proposal.normalize_candidate(item)
            for item in self.session.frontier_output.get("candidates") or []
            if isinstance(item, dict)
        ]
        self.session.scientific_anchor_types = _scientific_anchor_types(
            self.session.owner
        )
        self.session.existing_hypotheses = bool(self.session.scientific_anchor_types)
        for item in self.session.frontier:
            _require_v2_new_h_candidate(
                item, has_existing_h=self.session.existing_hypotheses
            )
            operator = str(item.get("evolution_operator") or "").strip()
            anchor = str(item.get("lineage_anchor_node_id") or "").strip()
            sources = item.get("lineage_anchor_node_ids") or []
            if operator:
                if not 1 <= len(sources) <= 3 or len(set(sources)) != len(sources):
                    raise ValueError(
                        "derived Hypothesis requires 1..3 unique SQLite-registered H lineage sources"
                    )
                if self.session.scientific_anchor_types.get(sources[0]) != "Hypothesis":
                    raise ValueError(
                        f"primary lineage anchor must be Hypothesis/Decision: {sources[0]}"
                    )
                if any(
                    (
                        self.session.scientific_anchor_types.get(src) != "Hypothesis"
                        for src in sources
                    )
                ):
                    raise ValueError(
                        "supplemental lineage source must be Hypothesis/Decision/Finding"
                    )
            elif sources:
                raise ValueError(
                    "same-H continuation cannot have multiple lineage sources"
                )
            elif not anchor and any(
                (
                    kind in {"Hypothesis", "Decision"}
                    for kind in self.session.scientific_anchor_types.values()
                )
            ):
                raise ValueError(
                    "same-H continuation requires explicit lineage_anchor_node_id"
                )
        self.session.frontier_by_id = {
            item["id"]: item for item in self.session.frontier
        }
        if (
            len(self.session.frontier_by_id) != len(self.session.frontier)
            or not self.session.frontier_by_id
        ):
            raise ValueError(
                "Research Lead frontier candidates require unique stable ids"
            )
        self.session.reviews_by_id: dict[str, dict[str, Any]] = {}
        self.session.final_selection: dict[str, Any] | None = None
        self.session.pending_ids = list(self.session.frontier_by_id)
        self.session.qualification_repair_count = 0
        self.session.repair_verification: dict[str, Any] | None = None
        working = self.session.state.get("qualification_working_set")
        running_fingerprint = self.session.owner._running_h_fingerprint()
        if isinstance(working, dict) and working.get("running_h_fingerprint") not in (
            None,
            running_fingerprint,
        ):
            working = None
            self.session.state.pop("qualification_working_set", None)
            self.session.state.get("role_outputs", {}).pop(
                "research_lead_frontier", None
            )
        if isinstance(working, dict) and working.get("candidates"):
            restored_candidates = [
                json.loads(json.dumps(item))
                for item in working.get("candidates") or []
                if isinstance(item, dict) and item.get("id") and item.get("hypothesis")
            ]
            restored_by_id = {str(item["id"]): item for item in restored_candidates}
            if restored_by_id:
                self.session.frontier_by_id = restored_by_id
                self.session.reviews_by_id = {
                    str(item.get("candidate_id")): json.loads(json.dumps(item))
                    for item in working.get("reviews") or []
                    if isinstance(item, dict)
                    and str(item.get("candidate_id") or "")
                    in self.session.frontier_by_id
                }
                self.session.pending_ids = [
                    str(value)
                    for value in working.get("pending_ids") or []
                    if str(value) in self.session.frontier_by_id
                ]
                if not self.session.pending_ids:
                    self.session.pending_ids = [
                        item
                        for item in self.session.frontier_by_id
                        if item not in self.session.reviews_by_id
                    ]
                self.session.qualification_repair_count = int(
                    working.get("repair_count") or 0
                )
                self.session.repair_verification = (
                    working.get("repair_verification")
                    if isinstance(working.get("repair_verification"), dict)
                    else None
                )
                self.session.state["qualification_repair_count"] = (
                    self.session.qualification_repair_count
                )

    def persist_qualification_working_set(self):
        self.session.state["qualification_working_set"] = {
            "schema": "theory-master/qualification-working-set/v2",
            "repair_verification": (
                json.loads(json.dumps(self.session.repair_verification))
                if self.session.repair_verification
                else None
            ),
            "initial_review_status": (
                "initial_review_completed" if self.session.reviews_by_id else "pending"
            ),
            "lead_repair_status": (
                "lead_repair_completed"
                if self.session.qualification_repair_count
                else "pending"
            ),
            "candidates": json.loads(
                json.dumps(list(self.session.frontier_by_id.values()))
            ),
            "reviews": json.loads(
                json.dumps(list(self.session.reviews_by_id.values()))
            ),
            "pending_ids": list(self.session.pending_ids),
            "repair_count": self.session.qualification_repair_count,
            "running_h_fingerprint": self.session.owner._running_h_fingerprint(),
            "updated_at": now(),
        }
        self.session.owner._persist(self.session.state)

    def restore_legacy_estimand(self, parent_id: str, repaired_id: str):
        raw_frontier = (self.session.state.get("role_outputs") or {}).get(
            "research_lead_frontier"
        )
        raw_candidates = (
            raw_frontier.get("candidates") if isinstance(raw_frontier, dict) else []
        )
        raw_parent = next(
            (
                item
                for item in raw_candidates
                if isinstance(item, dict) and str(item.get("id") or "") == parent_id
            ),
            None,
        )
        frozen_estimand = (
            raw_parent.get("estimand") if isinstance(raw_parent, dict) else None
        )
        if frozen_estimand in (None, ""):
            return
        for candidate_id in (parent_id, repaired_id):
            candidate = self.session.frontier_by_id.get(candidate_id)
            if isinstance(candidate, dict) and candidate.get("estimand") in (None, ""):
                candidate["estimand"] = json.loads(json.dumps(frozen_estimand))

    def _migrate_repair_verification(self):
        if self.session.repair_verification:
            stored_version = int(
                self.session.repair_verification.get("review_protocol_version") or 0
            )
            stored_status = str(self.session.repair_verification.get("status") or "")
            repaired_id = str(
                self.session.repair_verification.get("candidate_id") or ""
            )
            if stored_version != _REPAIR_REVIEW_PROTOCOL_VERSION:
                if stored_status == "repair_verification_pending":
                    repaired = self.session.frontier_by_id.get(repaired_id) or {}
                    parent_id = str(
                        repaired.get("parent_candidate_id")
                        or self.session.repair_verification.get("parent_candidate_id")
                        or ""
                    )
                    original_review = self.session.reviews_by_id.get(parent_id) or {}
                    blockers = original_review.get("blocking_issues") or []
                    expected_hash = stable_hash(
                        {
                            "parent_id": parent_id,
                            "candidate": repaired,
                            "original_blockers": blockers,
                        }
                    )
                    if (
                        not repaired
                        or original_review.get("verdict") != "revise"
                        or (not blockers)
                        or (
                            self.session.repair_verification.get("repair_hash")
                            != expected_hash
                        )
                        or (
                            self.session.repair_verification.get("original_blockers")
                            != blockers
                        )
                    ):
                        raise RecoveryStop(
                            "legacy pending repair review lacks a complete frozen blocker ledger"
                        )
                    self.restore_legacy_estimand(parent_id, repaired_id)
                    self.session.repair_verification["repair_hash"] = stable_hash(
                        {
                            "parent_id": parent_id,
                            "candidate": repaired,
                            "original_blockers": blockers,
                        }
                    )
                    stale_review = self.session.reviews_by_id.get(repaired_id) or {}
                    if stale_review.get("qualification_basis") in {
                        "repair_verified",
                        "repair_unresolved",
                    }:
                        self.session.reviews_by_id.pop(repaired_id, None)
                    self.session.repair_verification["review_protocol_version"] = (
                        _REPAIR_REVIEW_PROTOCOL_VERSION
                    )
                    self.session.repair_verification.pop("result", None)
                    self.session.pending_ids = [repaired_id]
                    self.persist_qualification_working_set()
                elif stored_status == "repair_verified":
                    result = (
                        self.session.repair_verification.get("result")
                        if isinstance(
                            self.session.repair_verification.get("result"), dict
                        )
                        else {}
                    )
                    stale_review = self.session.reviews_by_id.get(repaired_id) or {}
                    if (
                        result.get("verdict") != "confirmed"
                        or result.get("candidate_id") != repaired_id
                        or stale_review.get("qualification_basis") != "repair_verified"
                        or (stale_review.get("repair_verification") != result)
                    ):
                        raise RecoveryStop(
                            "legacy confirmed repair review cannot be safely re-opened"
                        )
                    repaired = self.session.frontier_by_id.get(repaired_id) or {}
                    parent_id = str(
                        repaired.get("parent_candidate_id")
                        or self.session.repair_verification.get("parent_candidate_id")
                        or ""
                    )
                    original_review = self.session.reviews_by_id.get(parent_id) or {}
                    blockers = original_review.get("blocking_issues") or []
                    expected_hash = stable_hash(
                        {
                            "parent_id": parent_id,
                            "candidate": repaired,
                            "original_blockers": blockers,
                        }
                    )
                    if (
                        not repaired
                        or original_review.get("verdict") != "revise"
                        or (not blockers)
                        or (
                            self.session.repair_verification.get("repair_hash")
                            != expected_hash
                        )
                        or (
                            self.session.repair_verification.get("original_blockers")
                            != blockers
                        )
                    ):
                        raise RecoveryStop(
                            "legacy confirmed repair review lacks a complete frozen blocker ledger"
                        )
                    self.restore_legacy_estimand(parent_id, repaired_id)
                    self.session.repair_verification["repair_hash"] = stable_hash(
                        {
                            "parent_id": parent_id,
                            "candidate": repaired,
                            "original_blockers": blockers,
                        }
                    )
                    self.session.reviews_by_id.pop(repaired_id, None)
                    self.session.repair_verification = {
                        key: value
                        for key, value in self.session.repair_verification.items()
                        if key != "result"
                    }
                    self.session.repair_verification["status"] = (
                        "repair_verification_pending"
                    )
                    self.session.repair_verification["review_protocol_version"] = (
                        _REPAIR_REVIEW_PROTOCOL_VERSION
                    )
                    self.session.pending_ids = [repaired_id]
                    self.persist_qualification_working_set()
                elif stored_status != "repair_unresolved":
                    raise RecoveryStop(
                        "unknown legacy repair verification state; no scientific approval"
                    )

    def _exclude_repeated_stops(self):
        from .qualification_stop_history import previous_qualification_stop

        duplicates = []
        for candidate_id in list(self.session.pending_ids):
            if candidate_id in self.session.reviews_by_id:
                continue
            previous = previous_qualification_stop(
                self.session.frontier_by_id[candidate_id],
                self.session.preparation_feedback["recent_qualification_stops"],
            )
            if previous is None:
                continue
            duplicate = {
                "candidate_id": candidate_id,
                "source_cycle": previous["cycle_id"],
                "source_candidate_id": previous["candidate_id"],
                "frozen_objective_sha256": previous["frozen_objective_sha256"],
                "source_paths": previous["source_paths"],
            }
            self.session.state.setdefault(
                "qualification_operational_duplicates", []
            ).append(duplicate)
            self.session.owner._emit(
                self.session.state, "qualification.operational_duplicate", duplicate
            )
            self.session.pending_ids.remove(candidate_id)
            duplicates.append(candidate_id)
        if duplicates and (not self.session.pending_ids):
            self.session.review.stop_unqualified(
                "Unchanged candidate science repeats an exhausted qualification; address the recorded prerequisites or propose a distinct in-contract test before another scientific review.",
                duplicates,
            )
