from __future__ import annotations
from typing import TYPE_CHECKING

"Qualification session with explicit proposal, review, repair and commit phases."
from typing import Any
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from .qualification_proposal import QualificationProposal
    from .qualification_recovery import QualificationRecovery
    from .qualification_review import QualificationReview
    from .qualification_lock import QualificationLockCommit


@dataclass
class QualificationSession:
    lock: QualificationLockCommit = field(init=False)
    review: QualificationReview = field(init=False)
    recovery: QualificationRecovery = field(init=False)
    proposal: QualificationProposal = field(init=False)
    owner: Any
    state: dict[str, Any]
    run_id: str
    prior_cycle: dict[str, Any]
    model: Any = None
    preparation_feedback: dict[str, Any] | None = None
    prior_frontier: Any = None
    frontier_output: Any = None
    lead_context: Any = None
    frontier: list[dict[str, Any]] | None = None
    scientific_anchor_types: Any = None
    existing_hypotheses: Any = None
    frontier_by_id: dict[str, dict[str, Any]] | None = None
    reviews_by_id: dict[str, dict[str, Any]] | None = None
    final_selection: dict[str, Any] | None = None
    pending_ids: list[str] | None = None
    qualification_repair_count: int | None = None
    repair_verification: dict[str, Any] | None = None
    challenger_audited: bool | None = None
    selected_id: Any = None
    selected: dict[str, Any] | None = None
    selected_review: dict[str, Any] | None = None
    decision: Any = None
    decisive_test: Any = None
    hypothesis: Any = None
    active_title: Any = None
    active_hypothesis: Any = None
    evolution_operator: Any = None
    lineage_anchor_node_id: Any = None
    hypothesis_ref: Any = None
    cycle_criteria: Any = None
    initial_executor: Any = None
    initial_executor_reason: Any = None
    finalized: Any = None
    canonical_branch_id: Any = None
    compatible_branch_ids: Any = None
    branch: Any = None

    def __post_init__(self):
        if self.preparation_feedback is None:
            self.preparation_feedback = {}
        from .qualification_proposal import QualificationProposal
        from .qualification_recovery import QualificationRecovery
        from .qualification_review import QualificationReview
        from .qualification_lock import QualificationLockCommit

        self.proposal = QualificationProposal(self)
        self.recovery = QualificationRecovery(self)
        self.review = QualificationReview(self)
        self.lock = QualificationLockCommit(self)

    def run(self):
        self.proposal._load_model_and_feedback()
        self.proposal._recover_legacy_checkpoint()
        self.proposal._propose_frontier()
        if "blocking_reason" in self.frontier_output:
            self.frontier_by_id, self.reviews_by_id = {}, {}
            self.review.stop_unqualified(self.frontier_output["blocking_reason"], [], allow_retry=False)
        self.recovery._restore_working_set()
        self.recovery._exclude_invalid_judge_successors()
        self.recovery._migrate_repair_verification()
        self.recovery._exclude_repeated_stops()
        self.challenger_audited = bool(self.reviews_by_id)
        while True:
            if self.review._review_pending_candidates():
                continue
            if self.review._select_qualified_candidate():
                break
            self.review._repair_candidate()
        self.lock._prepare_selected_hypothesis()
        return self.lock._commit_hypothesis_lock()
