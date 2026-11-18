"""Offline lock, immutable source snapshots and shared report reading context."""
import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.canvas_projection import CanvasProjectionMixin
from theory_master.cycle_outcome_documents import render_outcome_document
from theory_master.reporting import ReportingMixin
from theory_master.research_transition import research_transition_text
from theory_master.science_canvas_export import canvas_projection
from theory_master.science_graph_store import ScienceGraphStore
from tests.test_science_frontier_v2 import _Workflow


CANDIDATE = {
    "hypothesis": "The extended model explains the unresolved range", "scope": "Prespecified range",
    "why_now": "The prior comparison isolates this missing term.",
    "goal_advance": "Resolve the remaining prediction gap.", "decisive_test": "Compare the frozen predictions",
    "transition": {"prior_gap": "The narrow-range result holds; the wide-range prediction remains untested.",
                   "current_change": "Extend the prediction to the wide range with the missing term."},
}


def test_lock_replay_freezes_actual_sources_and_rejects_rewritten_reason(tmp_path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        for ident in ("H-0001", "H-0002"):
            store.add_hypothesis(ident, title="Source " + ident)
        store.append_outcome(outcome_id="out-1", hypothesis_id="H-0001", cycle_id="1",
                             verdict="supported", summary="Narrow claim holds", record_only=True)
    workflow = _Workflow(tmp_path / "exploration.canvas")
    workflow.cycle_dir = tmp_path / "3"
    branch = {"id": "3", "hypothesis_ref": "H-0003", "hypothesis": CANDIDATE["hypothesis"]}
    selected = {**copy.deepcopy(CANDIDATE), "id": "candidate", "evolution_operator": "combine",
                "lineage_main_node_id": "H-0001", "lineage_other_node_ids": ["H-0002"]}
    state = {"hypothesis_lock": {"id": "H-0003"}}
    workflow._register_locked_hypothesis(state, branch, selected)
    dossier = tmp_path / "notes/hypotheses/H-0003.md"
    frozen_dossier = dossier.read_bytes()
    with ScienceGraphStore(database) as store:
        frozen = store.research_transition("H-0003")
        assert [(s["hypothesis_id"], s["role"]) for s in frozen["sources"]] == [
            ("H-0001", "main"), ("H-0002", "other")]
        assert frozen["sources"][0]["scientific_status"] == "none"  # Cycle support is not whole-H support.
        assert frozen["sources"][0]["outcome_refs"][0]["verdict"] == "supported"
        assert frozen["sources"][1]["outcome_refs"] == []
        store.append_outcome(outcome_id="out-2", hypothesis_id="H-0002", cycle_id="4",
                             verdict="rejected", summary="A later result", record_only=True)
    workflow._register_locked_hypothesis(state, branch, selected)
    with ScienceGraphStore(database, readonly=True) as store:
        assert store.research_transition("H-0003") == frozen
        assert store.agent_hypothesis_detail("H-0003")["research_transition"] == frozen
        node = next(n for n in canvas_projection(store)["nodes"] if n["id"] == "H-0003")
        assert frozen["current_change"] in node["text"]
        assert node["research_transition"] == frozen
        assert research_transition_text(frozen).encode() in frozen_dossier
    changed = copy.deepcopy(selected)
    changed["transition"]["prior_gap"] = "A hindsight explanation"
    with pytest.raises(ValueError, match="cannot change on replay"):
        workflow._register_locked_hypothesis(state, branch, changed)
    assert dossier.read_bytes() == frozen_dossier


def test_root_and_legacy_records_have_distinct_reading_context(tmp_path):
    with ScienceGraphStore(tmp_path / "graph.sqlite3") as store:
        store.add_hypothesis("H-0001")
        root = store.record_research_transition("H-0001", CANDIDATE)
        assert root["sources"] == []
        text = research_transition_text(root)
        assert "从什么问题出发" in text and "前序留下的问题" not in text
        store.add_hypothesis("H-0002")
        assert store.record_research_transition("H-0002", {"why_now": "Historical reason"}) is None
        assert store.research_transition("H-0002") is None
        assert store.db.execute("SELECT COUNT(*) FROM hypothesis_reading_context").fetchone()[0] == 1


def test_source_references_preserve_historical_verdict_instead_of_relabeling_it(tmp_path):
    with ScienceGraphStore(tmp_path / "graph.sqlite3") as store:
        store.add_hypothesis("H-0001")
        store.add_hypothesis("H-0002")
        store.append_outcome(outcome_id="legacy-outcome", hypothesis_id="H-0001", cycle_id="1",
                             verdict="open", record_only=True,
                             source_decisions=[{"id": "legacy-outcome", "raw_verdict": "milestone_advanced"}])
        store.add_evolution(edge_id="parent", source="H-0001", target="H-0002", lineage_role="main")
        frozen = store.record_research_transition("H-0002", CANDIDATE)
        assert frozen["sources"][0]["outcome_refs"][0]["original_verdicts"] == ["milestone_advanced"]
        text = research_transition_text(frozen)
        assert "历史裁决：milestone_advanced" in text
        assert "尚无法判定" not in text


def test_source_whole_h_status_matches_canonical_graph_not_legacy_h_column(tmp_path):
    with ScienceGraphStore(tmp_path / "graph.sqlite3") as store:
        store.add_hypothesis("H-0001")
        store.add_hypothesis("H-0002")
        store.append_outcome(outcome_id="whole-assessment", hypothesis_id="H-0001", cycle_id="1",
                             verdict="supported", summary="Explicit whole-H assessment", record_only=False)
        assert store.db.execute("SELECT scientific_status FROM hypotheses WHERE id='H-0001'").fetchone()[0] == "none"
        parent = next(n for n in store.graph_view()["nodes"] if n["id"] == "H-0001")
        assert parent["scientific_status"] == "supported"
        store.add_evolution(edge_id="source", source="H-0001", target="H-0002", lineage_role="main")
        frozen = store.record_research_transition("H-0002", CANDIDATE)
        assert frozen["sources"][0]["scientific_status"] == parent["scientific_status"]


def test_old_v2_database_can_be_read_without_schema_or_content_changes(tmp_path):
    database = tmp_path / "graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Historical H")
        store.db.execute("DROP TABLE hypothesis_reading_context")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    with ScienceGraphStore(database, readonly=True) as store:
        assert store.research_transition("H-0001") is None
        assert "research_transition" not in store.graph_view()["nodes"][0]
        assert "Historical H" in canvas_projection(store)["nodes"][0]["text"]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_report_uses_frozen_node_reasoning_before_results_without_model_call(tmp_path):
    database = tmp_path / "science_graph.sqlite3"

    class Reporter(ReportingMixin, CanvasProjectionMixin):
        def _graph_database_path(self):
            return database

        def _path_anchors(self):
            return {"repo_root": str(tmp_path)}

        def _evidence_context(self):
            return {"evidence_manifest": {"records": []}}

        def _call(self, *args, **kwargs):
            raise AssertionError("No extra model calls for transition rendering")

        def _emit(self, *args, **kwargs):
            pass

    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Frozen question")
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
        frozen = store.record_research_transition("H-0001", CANDIDATE)
    reporter = Reporter()
    reporter.cycle_dir = tmp_path / "research/1"
    (reporter.cycle_dir / "checkpoints").mkdir(parents=True)
    reporter.notes_dir = tmp_path / "notes"
    reporter.branch_canvas = None
    reporter.spec = SimpleNamespace(notes_mirror_dir=None)
    report = {"title": "Research result", "summary": "The full scientific report.",
              "graph_summary": {"conclusion": "Result", "basis": "Evidence", "scope": "Frozen range", "open_question": ""}}
    state = {"role_outputs": {"reporter": report}, "last_seq": 1,
             "hypothesis_lock": {"id": "H-0001", "hypothesis": CANDIDATE["hypothesis"]}}
    branch = {"title": "Frozen question", "hypothesis_ref": "H-0001", "decisive_test": "Frozen check"}
    reporter._write_cycle_report(state, branch, {"verdict": "supported", "rationale": "Test passed"},
                                 "Frozen question", CANDIDATE["hypothesis"], "supported")
    record = state["cycle_outcome_record"]
    assert record["report_context"]["research_transition"] == frozen
    document = Path(branch["note_path"]).read_text()
    assert research_transition_text(frozen) in document
    assert document.index("为什么研究到这里") < document.index("本轮概览") < document.index(report["summary"])
    reporter._commit_cycle_science(state, database, tmp_path / "exploration.canvas", record, None)
    with ScienceGraphStore(database, readonly=True) as store:
        stored = store.cycle_evaluation("1")
        assert stored["report_context"]["research_transition"] == frozen
        assert research_transition_text(frozen) in render_outcome_document(stored)
