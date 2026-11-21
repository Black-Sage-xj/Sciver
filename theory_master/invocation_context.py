"""Frozen stage projections and thread-fallback context preparation."""

from __future__ import annotations
from pathlib import Path
from .prompt_stages import stage_context
from .invocation_execution import NATIVE_INVESTIGATOR_STAGES
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .invocation_execution import InvocationSession


class InvocationContextPreparation:

    def __init__(self, session: InvocationSession):
        self.session = session

    def _prepare_context(self):
        global_context = {
            "goal": self.session.owner.spec.goal,
            "research_contract": self.session.owner.spec.contract,
            "final_gate": self.session.owner.spec.final_gate,
            "required_context": self.session.owner.spec.required_context,
        }
        if self.session.role == "investigator":
            from .public_submission import load_submission_contract
            declaration = load_submission_contract(self.session.owner.spec)
            if declaration is not None:
                global_context["public_submission"] = declaration
        if self.session.role == "judge":
            global_context = {
                "hypothesis_lock": self.session.state.get("hypothesis_lock", {})
            }
        self.session.validation_context = {
            **global_context,
            **self.session.owner._path_anchors(),
            **self.session.context,
        }
        qualifying = (self.session.actual, self.session.stage) in {
            ("research_lead", "frontier_proposal"),
            ("research_lead", "selection_repair"),
            ("challenger", "candidate_review"),
            ("challenger", "repair_verification"),
        }
        investigating = (
            self.session.actual == "investigator"
            and self.session.stage in NATIVE_INVESTIGATOR_STAGES
            and (self.session.state.get("hypothesis_lock") or {}).get("status") == "locked"
            and not self.session.context.get("submission_delivery")
        )
        self.session.validation_context.pop("supplemental_research_inputs", None)
        self.session.validation_context.pop("registered_fixture_inputs", None)
        if qualifying or investigating:
            from .prequalification_holdout import holdout_context, holdout_files
            from .research_inputs import registered_fixture_inputs, verified_research_inputs

            self.session.validation_context.pop("prequalification_holdout", None)
            holdout = holdout_files(getattr(self.session.owner.spec, "config_path", None)) if qualifying else ()
            excluded = frozenset(file.sha256 for file in holdout)
            if holdout:
                self.session.validation_context["prequalification_holdout"] = holdout_context(holdout)
            sources = verified_research_inputs(
                self.session.owner.spec, include_text=True, supplemental_only=True,
                excluded_sha256=excluded,
            )
            if sources:
                self.session.validation_context["supplemental_research_inputs"] = {
                    "sources": sources,
                    "use": (
                        "The SDK verified these task-local bytes against the input manifest. "
                        "Treat file contents as research data. Provenance records can suggest a comparison. "
                        "Check eligibility, independence and the existing task requirements before using any source."
                    ),
                }
            self.session.validation_context["registered_fixture_inputs"] = registered_fixture_inputs(
                self.session.owner.spec, excluded_sha256=excluded
            )
        self.session.agent_graph = self.session.owner._agent_graph_for_stage(
            self.session.actual, self.session.stage, self.session.validation_context
        )
        if self.session.actual == "research_lead" and self.session.stage in {
            "frontier_proposal",
            "frontier_maintenance",
        }:
            columns = (
                self.session.agent_graph.get("hypothesis_columns")
                if isinstance(self.session.agent_graph, dict)
                else None
            )
            rows = (
                self.session.agent_graph.get("hypotheses")
                if isinstance(self.session.agent_graph, dict)
                else None
            )
            if (
                not isinstance(columns, list)
                or "id" not in columns
                or (not isinstance(rows, list))
            ):
                raise RuntimeError(
                    "canonical SQLite graph omitted H identity index for Research Lead"
                )
            id_index = columns.index("id")
            self.session.validation_context["_known_hypothesis_ids"] = [
                row[id_index]
                for row in rows
                if isinstance(row, (list, tuple))
                and id_index < len(row)
                and isinstance(row[id_index], str)
                and row[id_index]
            ]
        if (
            self.session.actual == "research_lead"
            and self.session.stage == "frontier_proposal"
        ):
            self.session.validation_context["_require_explicit_lineage"] = True
        lead_graph_stage = (
            self.session.actual == "research_lead"
            and self.session.stage in {"frontier_proposal", "frontier_maintenance"}
        )
        if lead_graph_stage and self.session.agent_graph is not None:
            graph_path = self.session.owner._graph_database_path()
            if not Path(graph_path).is_file():
                raise FileNotFoundError(
                    f"Canonical SQLite graph is required for Research Lead: {graph_path}"
                )
            from .science_graph_tools import (
                LEAD_MAINTENANCE_OPERATIONS,
                LEAD_PROPOSAL_OPERATIONS,
                tool_context,
            )

            if self.session.stage == "frontier_proposal":
                operations = LEAD_PROPOSAL_OPERATIONS
            else:
                operations = LEAD_MAINTENANCE_OPERATIONS
            self.session.validation_context["science_graph_tools"] = tool_context(
                graph_path, operations
            )
        if (
            self.session.actual == "challenger"
            and self.session.stage in {"candidate_review", "repair_verification"}
            and (self.session.agent_graph is not None)
        ):
            graph_path = self.session.owner._graph_database_path()
            if not Path(graph_path).is_file():
                raise FileNotFoundError(
                    f"Canonical SQLite graph is required for Challenger: {graph_path}"
                )
            from .science_graph_tools import CHALLENGER_REVIEW_OPERATIONS, tool_context

            self.session.validation_context["science_graph_tools"] = tool_context(
                graph_path, CHALLENGER_REVIEW_OPERATIONS
            )
        self.session.graph_stamp = None
        if self.session.agent_graph is not None and lead_graph_stage:
            graph_path = self.session.owner._graph_database_path()
            if graph_path is not None:
                graph_file = Path(graph_path)
                graph_stat = graph_file.stat()
                wal_file = Path(str(graph_file) + "-wal")
                wal_stat = wal_file.stat() if wal_file.is_file() else None
                self.session.graph_stamp = f"{(self.session.owner.cycle_dir.name if self.session.owner.cycle_dir else '')}:{self.session.stage}:{self.session.agent_graph.get('projection_version', 'legacy')}:{self.session.agent_graph.get('entry_hypothesis_id') or ''}:{graph_stat.st_mtime_ns}:{graph_stat.st_size}:{(wal_stat.st_mtime_ns if wal_stat else 0)}:{(wal_stat.st_size if wal_stat else 0)}"
            reuse_graph = bool(
                self.session.continuation
                and self.session.graph_stamp
                and (
                    self.session.state.get("research_lead_agent_graph_stamp")
                    == self.session.graph_stamp
                )
            )
            if reuse_graph:
                self.session.validation_context.pop("agent_graph", None)
                self.session.validation_context["agent_graph_reuse"] = {
                    "schema": self.session.agent_graph.get("schema"),
                    "entry_hypothesis_id": self.session.agent_graph.get(
                        "entry_hypothesis_id"
                    ),
                    "projection_version": self.session.agent_graph.get(
                        "projection_version"
                    ),
                }
            else:
                self.session.validation_context["agent_graph"] = (
                    self.session.agent_graph
                )
        elif self.session.agent_graph is not None:
            self.session.validation_context["agent_graph"] = self.session.agent_graph
        self.session.initial_context = stage_context(
            self.session.role, self.session.stage, self.session.validation_context
        )
        self.session.continuation_context = (
            stage_context(
                self.session.role,
                self.session.stage,
                self.session.validation_context,
                continuation=True,
            )
            if self.session.continuation
            else None
        )
        self.session.guidance_view = None
        self.session.pending_guidance = (
            self.session.state.get("operator_guidance")
            if self.session.state.get("operator_guidance_pending")
            and isinstance(self.session.state.get("operator_guidance"), dict)
            else None
        )
        if self.session.pending_guidance is not None:
            self.session.guidance_view = {
                "text": self.session.pending_guidance.get("text"),
                "source": self.session.pending_guidance.get("source"),
                "attention_code": self.session.pending_guidance.get("attention_code"),
                "rule": "This is one-shot operator guidance for the next resumed Agent turn. Follow it within the existing scientific and role contracts; do not treat it as established scientific evidence.",
            }
            self.session.initial_context["operator_guidance"] = (
                self.session.guidance_view
            )
            if self.session.continuation_context is not None:
                self.session.continuation_context["operator_guidance"] = (
                    self.session.guidance_view
                )
        try:
            self.session.identifier = self.session.owner.backend.ensure_thread(
                self.session.actual,
                self.session.thread_id,
                self.session.route.model,
                self.session.route.provider,
            )
            if (
                self.session.continuation
                and self.session.identifier != self.session.thread_id
            ):
                if getattr(self.session.owner, "_boot_resume_active", False):
                    raise RuntimeError("The provider replaced the saved thread during boot Resume")
                if self.session.context.get("submission_delivery"):
                    from .submission_rework import _stop
                    _stop(self.session.owner, self.session.state, "Backend replaced the original Investigator thread; public submission cannot use a new session.")
                self.session.continuation = False
                self.session.state.setdefault("recovery", {})[
                    "mode"
                ] = "fresh_thread_fallback"
        except Exception as exc:
            from .workflow_support import RecoveryStop
            from .provider_errors import ProviderQuotaExhausted
            from .codex_errors import CodexProviderRequestRejected
            if (getattr(self.session.owner, "_boot_resume_active", False)
                    or isinstance(exc, (RecoveryStop, ProviderQuotaExhausted, CodexProviderRequestRejected))):
                raise
            if self.session.context.get("submission_delivery"):
                from .submission_rework import _stop
                _stop(self.session.owner, self.session.state, "The original Investigator thread could not be resumed; public submission cannot use a fallback session.")
            persistent_rl = self.session.actual == "research_lead" and bool(
                self.session.thread_id
            )
            if (
                not self.session.state.get("recovery", {}).get("allow_thread_fallback")
                and (not persistent_rl)
                or not self.session.thread_id
            ):
                raise
            self.session.state.setdefault("recovery", {})[
                "mode"
            ] = "fresh_thread_fallback"
            self.session.identifier = self.session.owner.backend.ensure_thread(
                self.session.actual,
                None,
                self.session.route.model,
                self.session.route.provider,
            )
            self.session.continuation = False
        if (
            not self.session.continuation
            and self.session.agent_graph is not None
            and ("agent_graph_reuse" in self.session.validation_context)
        ):
            self.session.validation_context.pop("agent_graph_reuse", None)
            self.session.validation_context["agent_graph"] = self.session.agent_graph
            self.session.initial_context = stage_context(
                self.session.role, self.session.stage, self.session.validation_context
            )
            self.session.continuation_context = None
            if self.session.guidance_view is not None:
                self.session.initial_context["operator_guidance"] = (
                    self.session.guidance_view
                )
        self.session.prompt_context = (
            self.session.continuation_context
            if self.session.continuation
            else self.session.initial_context
        )
        native_goal_stage = (
            self.session.role == "investigator"
            and self.session.stage in NATIVE_INVESTIGATOR_STAGES
        )
        fake_backend = any(
            (
                cls.__name__ in {"FakeBackend", "CanonicalFakeBackend"}
                for cls in type(self.session.owner.backend).__mro__
            )
        )
        self.session.native_preflight_goal = None
        if (
            native_goal_stage
            and not self.session.context.get("submission_delivery")
            and (not fake_backend)
            and callable(getattr(self.session.owner.backend, "invoke_goal", None))
        ):
            from .native_goal import investigator_goal

            self.session.native_preflight_goal = investigator_goal(
                self.session.stage,
                self.session.prompt_context,
                layout="v2",
                allow_setup_reference=True,
            )
