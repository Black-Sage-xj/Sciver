from __future__ import annotations

"""Scientific model, candidate/frontier state, synthesis, and continuation persistence."""

import json
from pathlib import Path
from typing import Any

from .evidence import adjudicable_manifest_records
from .runtime import now, write_json
from .specification import latest_compatible_cycle_dir
from .workflow_support import TERMINAL_SCIENTIFIC_VERDICTS

PENDING_DISPOSITION_RULE = (
    "Each item is a scientific synthesis from another Cycle that lost a Scientific Model "
    "version race and was never applied. Weigh it against the current model and this Cycle's "
    "verdict, then return pending_dispositions with exactly one entry per pending_id: "
    "integrated (its still-valid content is carried into your scientific_model_update), "
    "superseded (the current model or your update already covers it), or rejected "
    "(contradicted or beyond adjudicated evidence), each with a brief rationale."
)


class WorkflowScienceMixin:
    @staticmethod
    def _model_current_state(model: dict[str, Any], fallback: str = "") -> str:
        summary = " ".join(str(model.get("summary") or "").split())
        return summary or fallback

    @staticmethod
    def _candidate_by_id(candidates: list[Any], candidate_id: Any) -> dict[str, Any] | None:
        wanted = str(candidate_id or "")
        for candidate in candidates:
            if isinstance(candidate, dict) and str(candidate.get("id") or "") == wanted:
                return candidate
        return None

    @staticmethod
    def _validate_candidate_reviews(reviews: list[Any], candidates: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for review in reviews:
            if not isinstance(review, dict):
                raise ValueError("candidate review must be an object")
            candidate_id = str(review.get("candidate_id") or "")
            if not candidate_id or candidate_id not in candidates:
                raise ValueError(f"candidate review references unknown candidate: {candidate_id}")
            if candidate_id in seen:
                raise ValueError(f"duplicate candidate review: {candidate_id}")
            seen.add(candidate_id)
            verdict = str(review.get("verdict") or "")
            if verdict not in {"qualified", "revise", "reject"}:
                raise ValueError(f"candidate review verdict is invalid: {candidate_id}")
            reason = str(review.get("reason") or "").strip()
            if not reason:
                raise ValueError(f"candidate review reason is required: {candidate_id}")
            blocking = review.get("blocking_issues")
            normalized_blocking: list[dict[str, str]] = []
            if verdict == "revise":
                if not isinstance(blocking, list) or not blocking:
                    raise ValueError(f"revise review requires blocking_issues: {candidate_id}")
                for issue_index, issue in enumerate(blocking):
                    if not isinstance(issue, dict):
                        raise ValueError(f"blocking issue must be an object: {candidate_id}")
                    problem = str(issue.get("problem") or "").strip()
                    required_change = str(issue.get("required_change") or "").strip()
                    if not problem or not required_change:
                        raise ValueError(f"blocking issue requires problem and required_change: {candidate_id}")
                    normalized_blocking.append({
                        "id": f"{candidate_id}:block-{issue_index + 1}",
                        "problem": problem,
                        "required_change": required_change,
                    })
            elif blocking not in (None, [], ""):
                raise ValueError(f"blocking_issues is only valid for revise: {candidate_id}")
            normalized.append({"candidate_id": candidate_id, "verdict": verdict, "reason": reason,
                               **({"reviewed_science": review["reviewed_science"]} if "reviewed_science" in review else {}),
                               **({"blocking_issues": normalized_blocking} if verdict == "revise" else {})})
        missing = sorted(set(candidates) - seen)
        if missing:
            raise ValueError("candidate review is incomplete: " + ", ".join(missing))
        return normalized

    @staticmethod
    def _review_is_fatal(review: dict[str, Any] | None) -> bool:
        """Legacy name: in v2 any non-qualified candidate is not lockable."""
        return not review or review.get("verdict") != "qualified"

    def _literature_evidence_refs(self, *, active_only: bool = False) -> list[str]:
        """Registry is authoritative; legacy literature.jsonl is read-only fallback."""
        refs = self._evidence_registry().evidence_ids_by_producer("literature", active_only=active_only)
        if refs:
            return refs
        path = self.cycle_dir / "evidence" / "literature.jsonl"
        if not path.is_file():
            return []
        legacy: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(row, dict) and str(row.get("evidence_id") or "").strip():
                legacy.append(str(row["evidence_id"]))
        return list(dict.fromkeys(legacy))

    def _scientific_synthesis_commit(self, state: dict[str, Any], branch: dict[str, Any], judge: dict[str, Any]) -> None:
        """Let Research Lead author long-term science; SDK only validates/persists it."""
        from .scientific_model import (
            ScientificModelVersionConflict,
            apply_pending_dispositions,
            commit_authored_model,
            load as load_scientific_model,
            preview_authored_model,
            record_pending_update,
            validate_model,
            validate_pending_dispositions,
        )
        from .output_validation import format_validation_errors, validate_scientific_model_update

        cycle_id = str(self.cycle_dir.name)
        model = load_scientific_model(self.spec.research_root, goal_id=self.spec.goal_id)
        in_path = self.cycle_dir / "scientific_model_in.json"
        if in_path.is_file():
            input_model = json.loads(in_path.read_text(encoding="utf-8"))
            validate_model(input_model)
        else:
            input_model = model
            write_json(in_path, input_model)
        state["scientific_model_version_in"] = input_model["version"]

        evidence_context = self._evidence_context()
        role_refs = [
            str(item["evidence_id"])
            for item in adjudicable_manifest_records(evidence_context["evidence_manifest"])
            if item.get("evidence_id")
        ]
        branch["evidence_refs"] = list(dict.fromkeys(role_refs))

        synthesis_path = self.cycle_dir / "cycle_synthesis_out.json"
        update_path = self.cycle_dir / "scientific_model_update.json"
        out_path = self.cycle_dir / "scientific_model_out.json"

        # File-based idempotency is required because action=keep deliberately
        # leaves the long-term model untouched and therefore cannot use model
        # provenance as a recovery marker.
        lead: dict[str, Any] | None = None
        recovered_synthesis = False
        if synthesis_path.is_file():
            try:
                recovered = json.loads(synthesis_path.read_text(encoding="utf-8"))
                if isinstance(recovered, dict):
                    lead = recovered
                    recovered_synthesis = True
            except (OSError, ValueError):
                lead = None

        pending_context = self._scientific_model_pending_context(cycle_id, recovered=recovered_synthesis)
        pending_ids = [item["pending_id"] for item in pending_context["items"]]

        if lead is None:
            state["scientific_model_patch_status"] = "pending"
            state["scientific_model_update_status"] = "pending"
            # Qualification and long-term synthesis are distinct epistemic
            # episodes. Runtime keeps the full model/manifest for validation, while
            # stage_context exposes only minimal science plus stable file anchors.
            lead = self._call(state, "research_lead", {
                "mode": "cycle_end",
                "scientific_model": input_model,
                "scientific_model_path": str(in_path),
                "active_hypothesis": branch,
                "judge_decision": judge,
                **({"scientific_model_pending": pending_context} if pending_ids else {}),
                **evidence_context,
            }, fresh_thread=True)
            write_json(synthesis_path, lead)

        synthesis = lead.get("scientific_synthesis")
        if isinstance(synthesis, dict):
            state["scientific_synthesis"] = synthesis
        update = lead.get("scientific_model_update")
        if not isinstance(update, dict):
            raise ValueError("cycle_synthesis is missing scientific_model_update")
        action = str(update.get("action") or "")
        content = update.get("model") if isinstance(update.get("model"), dict) else None
        model_validation = validate_scientific_model_update(
            input_model, update, cycle_id=cycle_id,
            evidence_manifest=evidence_context.get("evidence_manifest"),
        )
        if not model_validation.valid:
            raise ValueError(
                "Scientific Model update failed deterministic Evidence/reference validation:\n"
                + format_validation_errors(model_validation.errors)
            )
        disposition_errors = validate_pending_dispositions(lead.get("pending_dispositions"), pending_ids)
        if disposition_errors:
            raise ValueError(
                "cycle_synthesis must dispose of every injected pending Scientific Model update:\n"
                + "\n".join(disposition_errors)
            )
        write_json(update_path, update)

        conflict_path = self.cycle_dir / "scientific_model_conflict.json"
        if conflict_path.is_file():
            state["scientific_model_conflict"] = json.loads(conflict_path.read_text(encoding="utf-8"))
        if out_path.is_file():
            updated = json.loads(out_path.read_text(encoding="utf-8"))
            validate_model(updated)
        else:
            candidate_model = preview_authored_model(
                input_model, content, cycle_id=cycle_id, action=action,
            )
            validate_model(candidate_model)
            write_json(self.cycle_dir / "scientific_model_staged.json", candidate_model)
            try:
                # Shared science barrier + Goal check inside; GoalSealedError propagates.
                updated = commit_authored_model(
                    self.spec.research_root, content, cycle_id=cycle_id, action=action,
                    expected_version=(int(input_model["version"]) if getattr(self, "worker_id", None) else None),
                    graph_path=self._graph_database_path(),
                )
            except ScientificModelVersionConflict as exc:
                # I-08: the synthesis becomes a durable pending update that a
                # later cycle_synthesis must explicitly dispose of; the newer
                # global summary is never overwritten.
                pending = record_pending_update(
                    self.spec.research_root, cycle_id=cycle_id,
                    worker_id=getattr(self, "worker_id", None),
                    base_version=exc.base_version, current_version=exc.current_version,
                    update=update,
                )
                updated = load_scientific_model(self.spec.research_root, goal_id=self.spec.goal_id)
                state["scientific_model_conflict"] = {
                    "basis_version": exc.base_version,
                    "current_version": exc.current_version,
                    "proposed_update": str(update_path),
                    "pending_id": pending["id"],
                    "pending_path": str(Path(self.spec.research_root).resolve()
                                        / ".theory-master" / "scientific_model_pending.json"),
                    "disposition": "durable_pending_for_next_cycle_synthesis",
                }
                write_json(conflict_path, state["scientific_model_conflict"])
                self._emit(state, "scientific_model.update_deferred", state["scientific_model_conflict"])
            write_json(out_path, updated)

        if pending_ids and not state.get("scientific_model_conflict"):
            # Dispositions take effect only with a successful (non-conflicted)
            # synthesis; a conflicted one leaves them pending for the next Lead.
            applied = apply_pending_dispositions(
                self.spec.research_root, list(lead.get("pending_dispositions") or []),
                resolved_by_cycle=cycle_id,
            )
            state["scientific_model_pending_dispositions"] = applied
            self._emit(state, "scientific_model.pending_resolved", {"dispositions": applied})

        status = (
            "deferred_conflict" if state.get("scientific_model_conflict") else
            "committed"
            if action == "replace"
            and str(updated.get("updated_from_cycle") or "") == cycle_id
            and updated["version"] != input_model["version"]
            else "kept"
        )
        state["scientific_model_version_out"] = updated["version"]
        state["scientific_model_patch_status"] = status
        state["scientific_model_update_status"] = status
        state["research_lead_thread_id"] = state.get("threads", {}).get("research_lead")
        state["scientific_model_current_state"] = self._model_current_state(updated, state.get("current_state", ""))
        state["current_state"] = state["scientific_model_current_state"] or state.get("current_state", "")

        self._persist(state)

        route = self.routes["research_lead"]
        self._save_research_lead_state(
            thread_id=state.get("threads", {}).get("research_lead"), route=route, cycle_id=cycle_id,
            rollover_reason=state.get("research_lead_rollover_reason"),
            rollover_history=state.get("research_lead_rollover_history"),
            context_chars=state.get("research_lead_context_chars"),
        )

    def _scientific_model_pending_context(self, cycle_id: str, *, recovered: bool) -> dict[str, Any]:
        """Pending CAS-conflicted syntheses frozen per Cycle for crash-safe replay."""
        from .scientific_model import list_pending

        path = self.cycle_dir / "scientific_model_pending_in.json"
        if path.is_file():
            items = json.loads(path.read_text(encoding="utf-8"))
        else:
            # A synthesis recovered from before this file existed saw no pending items.
            items = [] if recovered else [{
                "pending_id": item["id"],
                "source_cycle": item.get("cycle"),
                "worker": item.get("worker"),
                "base_version": item.get("base_version"),
                "current_version_at_conflict": item.get("current_version_at_conflict"),
                "update": item.get("update"),
            } for item in list_pending(self.spec.research_root) if str(item.get("cycle")) != cycle_id]
            write_json(path, items)
        return {"rule": PENDING_DISPOSITION_RULE, "items": items}

    @staticmethod
    def _normalize_candidate(candidate: dict[str, Any], branch: dict[str, Any]) -> dict[str, Any]:
        item = dict(candidate)
        item.setdefault("evolution_operator", "")
        item.setdefault("rationale", "")
        item.setdefault("evidence_refs", [])
        item.setdefault("status", "provisional")
        if not item.get("id"):
            raise ValueError("candidate requires id")
        item.pop("hypothesis_id", None)
        item.pop("parent_hypothesis_ids", None)
        if not isinstance(item["evidence_refs"], list):
            raise ValueError("candidate evidence_refs must be a list")
        if not isinstance(item["rationale"], str):
            raise ValueError("candidate rationale must be a string")
        allowed = {"refine", "repair", "specialize", "generalize", "alternative", "combine", "derive", "counter_hypothesis"}
        if item["evolution_operator"] and item["evolution_operator"] not in allowed:
            raise ValueError("candidate evolution_operator is invalid")
        if item["status"] not in {"provisional", "pending_evaluation", "selected", "open", "rejected", "closed"}:
            raise ValueError("candidate status is invalid")
        return item

    @classmethod
    def _merge_candidates(cls, candidates: list[Any], branch: dict[str, Any]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError("candidate must be an object")
            normalized = cls._normalize_candidate(candidate, branch)
            key = str(normalized["id"])
            merged[key] = {**merged.get(key, {}), **normalized}
        return list(merged.values())

    def _maintain_frontier_state(self, state: dict[str, Any], judge: dict[str, Any]) -> dict[str, Any]:
        """Ask Research Lead to update explicit SQLite H frontier labels, not plan the next Cycle."""
        from .scientific_model import load as load_scientific_model

        path = self.cycle_dir / "frontier_state_out.json"
        if path.is_file():
            try:
                recovered = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                recovered = None
            if isinstance(recovered, dict) and isinstance(recovered.get("updates"), list):
                state["frontier_maintenance"] = recovered
                return recovered
        model = load_scientific_model(self.spec.research_root, goal_id=self.spec.goal_id)
        # Same epistemic episode as cycle_synthesis. Do not open a second Lead thread.
        result = self._call(state, "research_lead", {
            "mode": "frontier_maintenance",
            "scientific_model": model,
            "judge_decision": judge,
            "scientific_synthesis": state.get("scientific_synthesis") or {},
            "active_hypothesis": state.get("hypothesis_lock") or {},
        }, fresh_thread=False, output_key="frontier_maintenance")
        write_json(path, result)
        state["frontier_maintenance"] = result
        return result
