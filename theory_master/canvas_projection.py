from __future__ import annotations

"""Deterministic scientific Canvas projection and layout."""

import json

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

class CycleOutcomeConflictError(ValueError):
    """A retried Cycle Outcome disagrees with the one already committed for the Cycle."""


class CanvasProjectionMixin:
    def _claim_bound_hypothesis(self) -> str | None:
        claims = getattr(self, "worker_claims", None)
        claim_id = getattr(self, "worker_claim_id", None)
        if claims is None or not hasattr(claims, "list_claims"):
            return None
        for claim in claims.list_claims():
            if claim.get("claim_id") == claim_id:
                return claim.get("hypothesis_id") or None
        return None

    def _assert_science_writer(self, cycle_id: str, hypothesis_id: str | None,
                               *, require_hypothesis: bool) -> None:
        if not getattr(self, "worker_id", None):
            return
        if not require_hypothesis:
            # An H registration precedes bind_hypothesis; once bound, the
            # claimed H must be the one being written.
            hypothesis_id = hypothesis_id if self._claim_bound_hypothesis() else None
        self.worker_claims.assert_writer(
            self.worker_id, self.worker_claim_id, self.worker_claim_epoch,
            cycle_id=cycle_id, hypothesis_id=hypothesis_id or None,
        )

    @contextmanager
    def _science_write_scope(self, *, cycle_id: str, hypothesis_id: str | None,
                             require_hypothesis: bool = True) -> Iterator[None]:
        """Lock order: shared science barrier -> canvas_write_lock -> claim fence.

        The caller opens the SQLite transaction inside this scope. Both locks
        are re-entrant for the holding thread (the barrier natively, the Canvas
        lock through ``_canvas_write_locked``), so freeze paths that already
        hold them can call in. GoalSealedError is never caught here.
        """
        from .goal_terminal import assert_goal_open, science_commit_barrier
        from .multi_worker import canvas_write_lock
        root = self.spec.research_root
        with science_commit_barrier(root, "shared"):
            assert_goal_open(root, self._graph_database_path())
            if getattr(self, "_canvas_write_locked", False):
                self._assert_science_writer(cycle_id, hypothesis_id,
                                            require_hypothesis=require_hypothesis)
                yield
                return
            with canvas_write_lock(root):
                self._canvas_write_locked = True
                try:
                    self._assert_science_writer(cycle_id, hypothesis_id,
                                                require_hypothesis=require_hypothesis)
                    yield
                finally:
                    self._canvas_write_locked = False


    @staticmethod
    def _stable_hypothesis_node_id(hypothesis_ref: str) -> str:
        value = str(hypothesis_ref or "").strip()
        if not re.fullmatch(r"H-\d{4,}", value):
            raise ValueError("Hypothesis node id must match H-xxxx")
        return value

    @staticmethod
    def _locked_hypothesis_ref(
        state: dict[str, Any], branch: dict[str, Any], selected: dict[str, Any]
    ) -> str:
        """Use the locked H id; legacy aliases are read only for old checkpoints."""
        lock = state.get("hypothesis_lock") if isinstance(state.get("hypothesis_lock"), dict) else {}
        return str(lock.get("id") or branch.get("hypothesis_ref") or "").strip()

    def _ensure_locked_hypothesis_projection(
        self, state: dict[str, Any], branch: dict[str, Any],
        selected: dict[str, Any],
    ) -> str:
        """Compatibility bridge for an already-loaded pre-V2 qualification caller.

        The canonical implementation and all new call sites use
        ``_ensure_locked_hypothesis``. Keeping this narrow alias prevents a
        mixed-revision Worker from failing before it can report its source
        provenance; it does not re-enable legacy Canvas storage.
        """
        return self._ensure_locked_hypothesis(state, branch, selected)

    def _ensure_locked_hypothesis(
        self, state: dict[str, Any], branch: dict[str, Any],
        selected: dict[str, Any],
    ) -> str:
        """V2 Hypothesis Lock: register H and H→H evolution in SQLite only."""
        self._graph_database_path()  # a missing canonical graph fails before any lock
        h_id = self._locked_hypothesis_ref(state, branch, selected)
        if not h_id:
            raise ValueError("V2 Hypothesis Lock must provide stable H ID")
        with self._science_write_scope(cycle_id=str(self.cycle_dir.name), hypothesis_id=h_id,
                                       require_hypothesis=False):
            return self._register_locked_hypothesis(state, branch, selected)

    def _register_locked_hypothesis(
        self, state: dict[str, Any], branch: dict[str, Any],
        selected: dict[str, Any],
    ) -> str:
        from .science_graph_store import ScienceGraphStore
        from .vault_canvas import refresh_registered_canvases
        target = self.branch_canvas or self.spec.branch_canvas
        graph_path = self._graph_database_path()
        h_id = self._locked_hypothesis_ref(state, branch, selected)
        if not h_id:
            raise ValueError("V2 Hypothesis Lock must provide stable H ID")
        cycle_id = str(branch.get("id") or self.cycle_dir.name)
        source_ids = list(dict.fromkeys(
            str(x).strip() for x in (
                selected.get("lineage_anchor_node_ids")
                or branch.get("source_node_ids")
                or ([selected.get("lineage_anchor_node_id") or branch.get("source_node_id")]
                    if (selected.get("lineage_anchor_node_id") or branch.get("source_node_id")) else [])
            ) if str(x).strip()
        ))
        from .hypothesis_lineage import explicit_lineage
        explicit = explicit_lineage(selected)
        if explicit is not None:
            source_ids = [explicit[0], *explicit[1]]
        operator = str(selected.get("evolution_operator") or branch.get("evolution_operator") or "").strip()
        from .hypothesis_display import normalize_hypothesis_display
        display_title, display_summary = normalize_hypothesis_display(
            h_id, title=selected.get("canvas_title"), summary=selected.get("canvas_summary"),
            fallback_title=branch.get("title") or branch.get("hypothesis") or h_id,
        )
        with ScienceGraphStore(graph_path) as store:
            with store.transaction():
                exists = store.db.execute(
                    "SELECT 1 FROM hypotheses WHERE id=?", (h_id,)
                ).fetchone()
                if not exists:
                    if store.db.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] and (
                        not operator or not source_ids
                    ):
                        raise ValueError(
                            "V2 new H needs explicit evolution_operator and H lineage"
                        )
                    for parent_id in source_ids:
                        store.assert_eligible_hypothesis(parent_id, same_h=False)
                    store.add_hypothesis(
                        h_id, title=display_title, summary=display_summary,
                    )
                    store.register_cycle_hypothesis(cycle_id=cycle_id, hypothesis_id=h_id)
                    for ordinal, source_id in enumerate(dict.fromkeys(source_ids)):
                        if source_id == h_id:
                            raise ValueError("New H cannot derive from itself")
                        store.add_evolution(
                            edge_id=f"v2-evolution-{source_id}-{h_id}-{ordinal}",
                            source=source_id, target=h_id, operator=operator,
                            lineage_role=("main" if ordinal == 0 else "other") if explicit else None,
                        )
                else:
                    # An existing H is valid only when this very Cycle already
                    # locked it. Legacy H without an owner is not a new lock.
                    owner = store.db.execute(
                        "SELECT cycle_id FROM hypothesis_cycle_owners WHERE hypothesis_id=?",
                        (h_id,),
                    ).fetchone()
                    if not owner or owner["cycle_id"] != cycle_id:
                        raise ValueError("V2 new Cycle cannot reuse an existing H")
                    if explicit is not None:
                        stored_sources = [(row["source_hypothesis"],
                                           json.loads(row["provenance_json"]).get("lineage_role"))
                                          for row in store.db.execute(
                            "SELECT source_hypothesis,provenance_json FROM scientific_edges "
                            "WHERE target_hypothesis=?", (h_id,))]
                        expected_sources = [(explicit[0], "main"),
                                            *((source, "other") for source in explicit[1])]
                        if sorted(stored_sources) != sorted(expected_sources):
                            raise ValueError("Locked Hypothesis lineage sources cannot change on replay")
                    outcome_row = store.db.execute(
                        "SELECT verdict,cycle_id,summary,decisive_test,judge_rationale "
                        "FROM outcomes WHERE hypothesis_id=?", (h_id,),
                    ).fetchone()
                    adjudicated = bool(outcome_row and (
                        str(outcome_row["cycle_id"] or "").strip()
                        or str(outcome_row["summary"] or "").strip()
                        or str(outcome_row["decisive_test"] or "").strip()
                        or str(outcome_row["judge_rationale"] or "").strip()
                    ))
                    display_title, display_summary = normalize_hypothesis_display(
                        h_id, title=display_title, summary=display_summary,
                        fallback_title=branch.get("title") or branch.get("hypothesis") or h_id,
                        scientific_status=outcome_row["verdict"] if outcome_row else "open",
                        adjudicated=adjudicated,
                    )
                    store.db.execute(
                        "UPDATE hypotheses SET title=?,summary=? WHERE id=? "
                        "AND (title IS NOT ? OR summary IS NOT ?)",
                        (display_title, display_summary, h_id, display_title, display_summary),
                    )
                from .evaluation_history import freeze_materials
                freeze_materials(store, h_id, selected)
                lock = state.setdefault("hypothesis_lock", {})
                reasoning = store.record_research_transition(h_id, selected, frozen=lock.get("research_transition"))
                if reasoning is not None and "research_transition" not in lock:
                    # The dossier is append-only and may outlive a rolled-back
                    # SQL transaction. Persist its source snapshot before publication.
                    lock["research_transition"] = reasoning
                    if persist := getattr(self, "_persist", None):
                        persist(state)
                # The frozen scientific definition is a full-length Markdown
                # dossier, never a Canvas-card summary. Both new and reused H
                # append one immutable Cycle snapshot idempotently.
                from .hypothesis_dossier import write_hypothesis_dossier
                notes_root = (getattr(self.spec, "notes_mirror_dir", None)
                              or target.resolve().parent / "notes")
                dossier_ref = write_hypothesis_dossier(
                    canvas_path=target, notes_root=notes_root,
                    hypothesis_id=h_id, cycle_id=cycle_id,
                    hypothesis=str(branch.get("hypothesis") or selected.get("hypothesis") or ""),
                    scope=str(branch.get("scope") or selected.get("scope") or ""),
                    why_now=str(selected.get("why_now") or selected.get("rationale") or ""),
                    goal_advance=str(selected.get("goal_advance") or ""),
                    decisive_test=str(selected.get("decisive_test") or branch.get("decisive_test") or ""),
                    display_title=display_title,
                    display_summary=display_summary,
                    candidate_id=str(selected.get("id") or ""),
                    evolution_operator=operator,
                    lineage_anchor_node_id=source_ids[0] if source_ids else "",
                    lineage_anchor_node_ids=source_ids,
                    lineage_main_node_id=explicit[0] if explicit else "",
                    lineage_other_node_ids=explicit[1] if explicit else [],
                    research_transition=reasoning,
                )
                store.db.execute("UPDATE hypotheses SET dossier_ref=? WHERE id=? AND dossier_ref IS NOT ?",
                                 (dossier_ref, h_id, dossier_ref))
        branch["hypothesis_ref"] = h_id
        branch["scientific_parent_node_id"] = source_ids[0] if source_ids else None
        branch["scientific_parent_node_ids"] = list(source_ids)
        branch.pop("continuation_anchor_decision_id", None)
        state.setdefault("canvas_projection", {})["active_hypothesis_node_id"] = h_id
        if target is not None:
            refresh_registered_canvases(graph_path)
        return h_id

    def _render_canvas_from_sqlite(self, state: dict[str, Any]) -> None:
        """Commit the pending Cycle Outcome/frontier labels to SQLite, then export Canvas."""
        from .science_graph_store import ScienceGraphStore
        from .vault_canvas import refresh_registered_canvases
        graph_path = self._graph_database_path()
        target = self.branch_canvas or self.spec.branch_canvas
        outcome_record = state.get("cycle_outcome_record")
        outcome_record = outcome_record if isinstance(outcome_record, dict) else None
        maintenance = state.get("frontier_maintenance")
        updates = maintenance.get("updates") if isinstance(maintenance, dict) else None
        if outcome_record is None and not isinstance(updates, list):
            # Pure projection: no science write, so no barrier/claim fence.
            from contextlib import nullcontext
            from .multi_worker import canvas_write_lock
            with (nullcontext() if getattr(self, "_canvas_write_locked", False)
                  else canvas_write_lock(self.spec.research_root)):
                if target is not None:
                    refresh_registered_canvases(graph_path)
            state.setdefault("canvas_projection", {})["next_start_node_id"] = str(
                state.get("next_start_node_id") or ""
            )
            return
        lock_id = str((state.get("hypothesis_lock") or {}).get("id") or "").strip()
        with self._science_write_scope(cycle_id=str(self.cycle_dir.name),
                                       hypothesis_id=lock_id or None,
                                       require_hypothesis=outcome_record is not None):
            self._commit_cycle_science(state, graph_path, target, outcome_record, updates)

    def _commit_cycle_science(self, state: dict[str, Any], graph_path: Path, target: Path | None,
                              outcome_record: dict[str, Any] | None, updates: Any) -> None:
        from .science_graph_store import ScienceGraphStore
        from .vault_canvas import refresh_registered_canvases
        cycle_outcome = None
        with ScienceGraphStore(graph_path) as store:
            with store.transaction():
                if outcome_record is not None:
                    h_id = str(outcome_record.get("hypothesis_id") or "").strip()
                    cycle_id = str(outcome_record.get("cycle_id") or "").strip()
                    outcome_id = str(outcome_record.get("id") or "").strip()
                    lock_id = str((state.get("hypothesis_lock") or {}).get("id") or "").strip()
                    if not h_id or h_id != lock_id:
                        raise ValueError("SQLite Cycle Outcome must match the frozen H identity")
                    if cycle_id != str(self.cycle_dir.name):
                        raise ValueError("SQLite Cycle Outcome must match the current Cycle identity")
                    if not outcome_id or not outcome_id.startswith("cycle-outcome-"):
                        raise ValueError("SQLite Cycle Outcome requires a canonical outcome ID")
                    if store.db.execute(
                        "SELECT 1 FROM hypotheses WHERE id=?", (h_id,)
                    ).fetchone() is None:
                        raise ValueError(f"SQLite Cycle Outcome requires a known H: {h_id}")
                    owner = store.db.execute(
                        "SELECT hypothesis_id FROM hypothesis_cycle_owners WHERE cycle_id=?",
                        (cycle_id,),
                    ).fetchone()
                    if not owner or owner["hypothesis_id"] != h_id:
                        raise ValueError("SQLite Cycle Outcome requires the current Cycle's registered H owner")
                    verdict = str(outcome_record.get("verdict") or "open")
                    existing = store.cycle_evaluation(cycle_id)
                    if existing is not None:
                        # Retry after a crash: the Cycle is keyed by (cycle_id, H).
                        if existing.get("hypothesis_id") != h_id or existing.get("verdict") != verdict:
                            raise CycleOutcomeConflictError(
                                f"Cycle {cycle_id} already committed Outcome {existing.get('id')} "
                                f"({existing.get('hypothesis_id')}={existing.get('verdict')}); "
                                f"refusing conflicting {h_id}={verdict}"
                            )
                        cycle_outcome = existing
                if outcome_record is not None and cycle_outcome is None:
                    store.append_outcome(
                        outcome_id=outcome_id,
                        hypothesis_id=h_id,
                        cycle_id=cycle_id,
                        verdict=verdict,
                        title=str(outcome_record.get("title") or ""),
                        summary=str(outcome_record.get("summary") or ""),
                        decisive_test=str(outcome_record.get("decisive_test") or ""),
                        judge_rationale=str(outcome_record.get("judge_rationale") or ""),
                        findings=list(outcome_record.get("findings") or []),
                        open_questions=list(outcome_record.get("open_questions") or []),
                        boundary=list(outcome_record.get("boundary") or []),
                        evidence_refs=list(outcome_record.get("evidence_refs") or []),
                        report_ref=str(outcome_record.get("report_ref") or "") or None,
                        graph_summary=outcome_record.get("graph_summary"),
                        report_context=outcome_record.get("report_context"),
                        record_only=bool(outcome_record.get("record_only", True)),
                    )
                    cycle_outcome = next(
                        (row for row in store.cycle_history(h_id) if row.get("id") == outcome_id),
                        None,
                    )
                if isinstance(updates, list):
                    mapped = []
                    for index, item in enumerate(updates):
                        from .frontier_opportunity import OPPORTUNITY_FIELDS
                        if not isinstance(item, dict) or set(item) - {"hypothesis_id", "status", "reason"} - OPPORTUNITY_FIELDS:
                            raise ValueError("Frontier maintenance may change only SQLite H status/reason and opportunity details")
                        h_id = str(item.get("hypothesis_id") or "").strip()
                        status = str(item.get("status") or "").strip()
                        if not h_id or status not in {"active", "parked", "closed"}:
                            raise ValueError(f"Invalid SQLite frontier update at index {index}")
                        mapped.append({
                            "hypothesis_id": h_id,
                            "status": status,
                            "reason": str(item.get("reason") or ""),
                            **{key: item[key] for key in OPPORTUNITY_FIELDS if key in item},
                        })
                    store.set_frontiers_batch(mapped)

        if cycle_outcome is not None and target is not None:
            from .cycle_outcome_documents import write_outcome_document
            notes_root = (getattr(self.spec, "notes_mirror_dir", None)
                          or target.resolve().parent / "notes")
            write_outcome_document(notes_root, cycle_outcome, lock=state.get("hypothesis_lock") or {})
            h_id = str(cycle_outcome.get("hypothesis_id") or "")
            evidence = list(cycle_outcome.get("evidence_refs") or [])
            if h_id and evidence:
                from .hypothesis_dossier import append_hypothesis_evidence
                notes_root = (getattr(self.spec, "notes_mirror_dir", None)
                              or target.resolve().parent / "notes")
                dossier = notes_root / "hypotheses" / (h_id + ".md")
                if dossier.is_file():
                    append_hypothesis_evidence(
                        canvas_path=target,
                        wikilink_stem=dossier.resolve().relative_to(target.resolve().parent).with_suffix("").as_posix(),
                        cycle_id=str(cycle_outcome.get("cycle_id") or ""),
                        evidence_refs=evidence,
                    )
        if target is not None:
            refresh_registered_canvases(graph_path)
        if outcome_record is not None:
            state.pop("cycle_outcome_record", None)
        state.setdefault("canvas_projection", {})["next_start_node_id"] = str(
            state.get("next_start_node_id") or ""
        )

    def _render_canvas(self, state: dict[str, Any]) -> None:
        """Render the SQLite graph as a Canvas projection; no alternate renderer exists.

        Science writes take the barrier/Canvas lock/claim fence themselves.
        """
        return self._render_canvas_from_sqlite(state)
