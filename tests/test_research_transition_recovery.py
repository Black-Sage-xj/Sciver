"""A published H dossier and resumed SQLite lock retain one source snapshot."""
from __future__ import annotations

import copy
import json

import pytest

from theory_master.cycle_outcome_documents import render_outcome_document
from theory_master.hypothesis_dossier import write_hypothesis_dossier
from theory_master.research_transition import research_transition_text
from theory_master.runtime import write_json
from theory_master.science_graph_store import ScienceGraphStore
from tests.test_science_frontier_v2 import _Workflow


def test_dossier_publication_crash_reuses_durable_source_snapshot_on_resume(tmp_path, monkeypatch):
    database = tmp_path / "science_graph.sqlite3"
    state_file = tmp_path / "research_state.json"
    with ScienceGraphStore(database, auto_refresh_canvas=False) as store:
        store.add_hypothesis("H-0001", title="Original source title")
        store.append_outcome(
            outcome_id="initial-outcome", hypothesis_id="H-0001", cycle_id="1",
            verdict="supported", summary="The relation holds within the narrow range.",
            report_ref="notes/outcomes/1.md", record_only=True,
        )

    class PersistingWorkflow(_Workflow):
        def _persist(self, state, **kwargs):
            write_json(state_file, state)

    selected = {
        "id": "candidate-3", "hypothesis": "The relation extends to the wider range.",
        "scope": "The specified wider range.", "decisive_test": "Compare the wider-range predictions.",
        "why_now": "The narrow-range result leaves this comparison unresolved.",
        "goal_advance": "Determine the relation's range of applicability.",
        "transition": {
            "prior_gap": "The narrow-range relation holds, while the wider range remains untested.",
            "current_change": "This H tests the same relation in the wider range.",
            "source_contributions": {"H-0001": "Provides the supported narrow-range relation and its applicability limit."},
        },
        "evolution_operator": "generalize", "lineage_main_node_id": "H-0001",
        "lineage_other_node_ids": [],
    }
    branch = {"id": "3", "hypothesis_ref": "H-0003", "hypothesis": selected["hypothesis"]}
    state = {"hypothesis_lock": {
        "id": "H-0003", "status": "committing", "candidate_provenance": copy.deepcopy(selected),
    }}
    workflow = PersistingWorkflow(tmp_path / "exploration.canvas")
    workflow.cycle_dir = tmp_path / "3"

    def publish_then_crash(**kwargs):
        write_hypothesis_dossier(**kwargs)
        raise RuntimeError("crash after dossier publication before SQLite commit")

    with monkeypatch.context() as interruption:
        interruption.setattr("theory_master.hypothesis_dossier.write_hypothesis_dossier", publish_then_crash)
        with pytest.raises(RuntimeError, match="after dossier publication"):
            workflow._register_locked_hypothesis(state, branch, selected)

    dossier = tmp_path / "notes/hypotheses/H-0003.md"
    published_bytes = dossier.read_bytes()
    restored = json.loads(state_file.read_text(encoding="utf-8"))
    original = restored["hypothesis_lock"]["research_transition"]
    assert research_transition_text(original).encode() in published_bytes
    assert [ref["id"] for ref in original["sources"][0]["outcome_refs"]] == ["initial-outcome"]
    with ScienceGraphStore(database, auto_refresh_canvas=False) as store:
        assert store.research_transition("H-0003") is None
        assert store.db.execute("SELECT 1 FROM hypotheses WHERE id='H-0003'").fetchone() is None
        store.db.execute("UPDATE hypotheses SET title='Later edited source title' WHERE id='H-0001'")
        store.append_outcome(
            outcome_id="later-outcome", hypothesis_id="H-0001", cycle_id="2",
            verdict="rejected", summary="A later comparison rejects a different range.",
            report_ref="notes/outcomes/2.md", record_only=True,
        )

    resumed_workflow = PersistingWorkflow(tmp_path / "exploration.canvas")
    resumed_workflow.cycle_dir = tmp_path / "3"
    resumed_workflow._register_locked_hypothesis(
        restored, copy.deepcopy(branch), copy.deepcopy(restored["hypothesis_lock"]["candidate_provenance"]),
    )
    with ScienceGraphStore(database, readonly=True) as store:
        frozen = store.research_transition("H-0003")
        assert frozen == original
        assert store.agent_hypothesis_detail("H-0003")["research_transition"] == original
    assert dossier.read_bytes() == published_bytes
    assert frozen["sources"][0]["title"] == "Original source title"
    assert frozen["sources"][0]["contribution"] == selected["transition"]["source_contributions"]["H-0001"]
    assert "later-outcome" not in json.dumps(frozen)
    report = render_outcome_document({
        "id": "cycle-3-outcome", "hypothesis_id": "H-0003", "cycle_id": "3",
        "verdict": "open", "summary": "The current comparison is unresolved.",
        "report_context": {"research_transition": frozen},
    })
    assert research_transition_text(original) in report
