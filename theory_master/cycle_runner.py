"""Cycle lifecycle with explicit startup, qualification, execution and exit phases."""

from __future__ import annotations
from typing import TYPE_CHECKING
from dataclasses import dataclass, field
from typing import Any, Protocol
from .runtime import now
from .goal_terminal import GoalSealedError
from .provider_errors import ProviderQuotaExhausted
from .workflow_qualification import NoQualifiedCandidate
from .workflow_support import RecoveryStop


class CycleOwner(Protocol):

    def _execute_cycle(
        self,
        resume_state: dict[str, Any] | None = None,
        *,
        same_cycle_continuation: bool = False,
    ) -> dict[str, Any]: ...


if TYPE_CHECKING:
    from .cycle_start import CycleStartup
    from .cycle_exit import CycleExit


@dataclass
class CycleRunner:
    exit: CycleExit = field(init=False)
    startup: CycleStartup = field(init=False)
    owner: Any
    state: dict[str, Any] | None = None
    run_id: str | None = None
    resuming: bool | None = None
    prior_cycle: dict[str, Any] | None = None
    interrupted_attention: Any = None
    before: dict[str, Any] | None = None
    events_start: int | None = None
    branch: dict[str, Any] | None = None
    finalized: dict[str, Any] | None = None
    active_title: Any = None
    active_hypothesis: Any = None
    resume_state: dict[str, Any] | None = None
    same_cycle_continuation: bool | None = None

    def run(self, resume_state=None, *, same_cycle_continuation=False):
        if same_cycle_continuation:
            return self.owner._execute_cycle(resume_state, same_cycle_continuation=True)
        return self.owner._execute_cycle(resume_state)

    def execute(self, resume_state=None, *, same_cycle_continuation=False):
        self.resume_state = resume_state
        self.same_cycle_continuation = same_cycle_continuation
        self.startup._initialize()
        try:
            self._qualify()
            self._checkpoint_and_execute()
        except NoQualifiedCandidate:
            return self.exit.handle_noqualifiedcandidate()
        except GoalSealedError:
            return self.exit.handle_goalsealederror()
        except RecoveryStop:
            return self.exit.handle_recoverystop()
        except ProviderQuotaExhausted as exc:
            return self.exit.handle_providerquotaexhausted(exc)
        except (KeyboardInterrupt, SystemExit) as exc:
            return self.exit.handle_keyboardinterrupt_systemexit(exc)
        except Exception as exc:
            return self.exit.handle_exception(exc)
        return self.exit._finalize()

    def _qualify(self):
        if self.resuming and self.owner._lock_prequalified_checkpoint(self.state):
            self.branch, self.finalized, self.active_title, self.active_hypothesis = (
                self.owner._restore_locked_cycle(self.state, self.run_id)
            )
            self.owner._emit(
                self.state,
                "recovery.stage_resumed",
                {
                    "from": "pre_hypothesis_lock",
                    "scientific_phase": self.state.get("scientific_phase"),
                },
            )
            self.owner._persist(self.state)
        elif (
            self.resuming
            and self.state.get("hypothesis_lock", {}).get("status") == "committing"
        ):
            self.branch, self.finalized, self.active_title, self.active_hypothesis = (
                self.owner._restore_locked_cycle(self.state, self.run_id)
            )
            self.owner._emit(
                self.state,
                "recovery.hypothesis_commit_completed",
                {"hypothesis_id": (self.state.get("hypothesis_lock") or {}).get("id")},
            )
            self.owner._persist(self.state)
        elif (
            self.resuming
            and self.state.get("hypothesis_lock", {}).get("status") == "locked"
        ):
            self.branch, self.finalized, self.active_title, self.active_hypothesis = (
                self.owner._restore_locked_cycle(self.state, self.run_id)
            )
            self.owner._emit(
                self.state,
                "recovery.stage_resumed",
                {
                    "from": "hypothesis_lock",
                    "scientific_phase": self.state.get("scientific_phase"),
                    "investigation_attempt": self.state.get("investigation_attempt", 0),
                    "investigation_round_count": self.state.get(
                        "investigation_round_count", 0
                    ),
                },
            )
            self.owner._persist(self.state)
        else:
            try:
                if (
                    self.resuming
                    and self.state.get("qualification_status")
                    == "no_qualified_candidate"
                ):
                    self.state.update(
                        {
                            "status": "no_qualified_candidate",
                            "phase": "qualification",
                            "resumable": False,
                            "execution_active": False,
                            "attention": dict(self.interrupted_attention),
                        }
                    )
                    raise NoQualifiedCandidate(
                        self.state.get("qualification_rationale") or ""
                    )
                (
                    self.branch,
                    self.finalized,
                    self.active_title,
                    self.active_hypothesis,
                ) = self.owner._qualify_and_lock(
                    self.state, self.run_id, self.prior_cycle
                )
            except (RecoveryStop, NoQualifiedCandidate):
                code = str((self.state.get("attention") or {}).get("code") or "")
                if (
                    code != "no_qualified_candidate"
                    or not self.owner.supervisor_enabled()
                ):
                    raise
                from .supervisor_attention_review import review_supervisor_attention

                outcome = review_supervisor_attention(
                    backend=self.owner.backend,
                    cycle_dir=self.owner.cycle_dir,
                    state=self.state,
                    goal=self.owner.spec.goal,
                    research_contract=self.owner.spec.contract,
                    final_gate=self.owner.spec.final_gate,
                )
                self.state["supervisor_attention_review"] = outcome
                self.state["internal_attention_review"] = outcome
                self.owner._emit(
                    self.state,
                    "attention.sdk_review_decided",
                    {"code": code, "action": outcome["action"], "mode": "sdk_internal"},
                )
                if outcome["action"] != "retry_qualification":
                    self.owner._persist(self.state)
                    raise
                self.state["internal_attention_retry_count"] = (
                    int(self.state.get("internal_attention_retry_count") or 0) + 1
                )
                if self.state.get("qualification_status") == "no_qualified_candidate":
                    self.state["qualification_status"] = "unqualified"
                self.owner._restart_unqualified_qualification(self.state)
                from .attention_guidance import write_attention_guidance

                current_guidance = (
                    self.state.get("operator_guidance")
                    if self.state.get("operator_guidance_pending")
                    else None
                )
                if current_guidance is None:
                    advisory = write_attention_guidance(
                        self.owner.cycle_dir,
                        outcome["guidance"],
                        source="sdk_internal",
                        attention_code=code,
                    )
                    self.state["operator_guidance"] = advisory
                    self.state["operator_guidance_pending"] = True
                self.state.update(
                    {"status": "active", "resumable": True, "execution_active": True}
                )
                self.owner._emit(
                    self.state,
                    "attention.sdk_retry_qualification",
                    {
                        "retry_count": self.state["internal_attention_retry_count"],
                        "guidance_source": (
                            self.state.get("operator_guidance") or {}
                        ).get("source"),
                    },
                )
                self.state.pop("supervisor_attention_review", None)
                self.state.pop("internal_attention_review", None)
                self.owner._persist(self.state)
                try:
                    (
                        self.branch,
                        self.finalized,
                        self.active_title,
                        self.active_hypothesis,
                    ) = self.owner._qualify_and_lock(
                        self.state, self.run_id, self.prior_cycle
                    )
                except NoQualifiedCandidate:
                    outcome = review_supervisor_attention(
                        backend=self.owner.backend,
                        cycle_dir=self.owner.cycle_dir,
                        state=self.state,
                        goal=self.owner.spec.goal,
                        research_contract=self.owner.spec.contract,
                        final_gate=self.owner.spec.final_gate,
                    )
                    self.state["supervisor_attention_review"] = outcome
                    self.state["internal_attention_review"] = outcome
                    self.owner._emit(
                        self.state,
                        "attention.sdk_review_decided",
                        {
                            "code": code,
                            "action": outcome["action"],
                            "mode": "sdk_internal",
                            "authority": "supervisor",
                            "session_id": outcome.get("session_id"),
                        },
                    )
                    self.owner._persist(self.state)
                    raise

    def _checkpoint_and_execute(self):
        if self.owner.stop_before == "investigator":
            self.state.update(
                {
                    "status": "checkpoint_reached",
                    "phase": "post_hypothesis_lock",
                    "checkpoint": {
                        "name": "post_hypothesis_lock",
                        "hypothesis_id": self.state["hypothesis_lock"]["id"],
                        "reached_at": now(),
                    },
                    "resumable": True,
                    "execution_active": False,
                }
            )
            self.owner._emit(
                self.state,
                "workflow.checkpoint_reached",
                {
                    "checkpoint": "post_hypothesis_lock",
                    "hypothesis_id": self.state["hypothesis_lock"]["id"],
                },
            )
            self.owner._persist(self.state, project=True)
            raise RecoveryStop("post_hypothesis_lock checkpoint reached")
        if not self.resuming:
            self.owner._write_replay_checkpoint(self.state, "investigator_start")
        judge, verdict = self.owner._investigate_and_judge(
            self.state, self.branch, self.finalized
        )
        if self.state.get("operational_only_closure"):
            self.owner._finalize_non_scientific_cycle(
                self.state, self.branch, self.active_title, self.active_hypothesis
            )
        else:
            self.owner._scientific_synthesis_commit(self.state, self.branch, judge)
            self.state["scientific_phase"] = "committed"
            self.owner._maintain_frontier_state(self.state, judge)
            self.owner._write_cycle_report(
                self.state,
                self.branch,
                judge,
                self.active_title,
                self.active_hypothesis,
                verdict,
            )
            if verdict == "supported":
                self.owner._persist(self.state, project=True)
                self.owner._run_goal_completion(self.state, judge)
            attention = (
                self.state.get("attention")
                if isinstance(self.state.get("attention"), dict)
                else {}
            )
            if attention.get("code") == "no_viable_scientific_frontier":
                self.state.update(
                    {
                        "status": "needs_attention",
                        "phase": "needs_attention",
                        "resumable": True,
                        "execution_active": False,
                    }
                )
                if self.owner.supervisor_enabled():
                    from .supervisor_attention_review import review_supervisor_attention

                    outcome = review_supervisor_attention(
                        backend=self.owner.backend,
                        cycle_dir=self.owner.cycle_dir,
                        state=self.state,
                        goal=self.owner.spec.goal,
                        research_contract=self.owner.spec.contract,
                        final_gate=self.owner.spec.final_gate,
                    )
                    self.state["supervisor_attention_review"] = outcome
                    self.state["internal_attention_review"] = outcome
                    self.owner._emit(
                        self.state,
                        "attention.sdk_review_decided",
                        {
                            "code": attention.get("code"),
                            "action": outcome["action"],
                            "mode": "sdk_internal",
                        },
                    )
                self.owner._emit(
                    self.state,
                    "workflow.stopped",
                    {
                        "reason": "needs_attention",
                        "resumable": True,
                        "attention": dict(attention),
                    },
                )

    def __post_init__(self):
        from .cycle_start import CycleStartup
        from .cycle_exit import CycleExit

        self.startup = CycleStartup(self)
        self.exit = CycleExit(self)
