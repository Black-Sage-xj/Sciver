"""Offline contract and lock-to-browser coverage of explicit branch provenance."""
from pathlib import Path

import pytest

from theory_master.contracts import role_contract
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.science_canvas_export import canvas_projection
from tm_observer.canvas_md import _sqlite_graph_projection
from tests.test_science_frontier_v2 import _Workflow


BASE = dict(hypothesis="Independent scientific claim", scope="Frozen scope", why_now="Feasible test",
            goal_advance="Advance the goal", decisive_test="A discriminating check",
            transition={"prior_gap": "The declared sources leave this relation unresolved.",
                        "current_change": "Combine the sources to test the bounded relation."},
            evolution_operator="combine")
KNOWN = {"_known_hypothesis_ids": ["H-0001", "H-0002", "H-0003"]}


def validate(**fields):
    role_contract("research_lead", phase="frontier_proposal").validate(
        {"candidates": [{**BASE, **fields}]}, KNOWN)


def test_main_and_other_contract():
    validate(lineage_main_node_id="H-0002", lineage_other_node_ids=["H-0001", "H-0003"])
    validate(lineage_main_node_id="H-0002")


@pytest.mark.parametrize("fields", [
    {"lineage_other_node_ids": ["H-0001"]},
    {"lineage_main_node_id": ["H-0001", "H-0002"]},
    {"lineage_main_node_id": "H-0001", "lineage_other_node_ids": ["H-0001"]},
    {"lineage_main_node_id": "H-0001", "lineage_other_node_ids": ["H-0002", "H-0002"]},
    {"lineage_main_node_id": "H-0001", "lineage_other_node_ids": ["H-0002", "H-0003", "H-0004"]},
    {"lineage_main_node_id": "H-9999"},
    {"lineage_main_node_id": "H-0001", "lineage_other_node_ids": ["H-9999"]},
    {"lineage_main_node_id": "H-0001", "lineage_anchor_node_ids": ["H-0002"]},
    {"lineage_main_node_id": " H-0001"},
])
def test_invalid_or_ambiguous_sources_rejected(fields):
    with pytest.raises(ValueError):
        validate(**fields)


def test_lock_persists_roles_and_draws_only_main_in_both_views(tmp_path: Path):
    canvas = tmp_path / "exploration.canvas"
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        for h in ["H-0001", "H-0002"]:
            store.add_hypothesis(h, title=h)
        # Legacy edges remain untouched and visible; never guess their main.
        store.add_evolution(edge_id="legacy", source="H-0001", target="H-0002", operator="derive")
    workflow = _Workflow(canvas)
    workflow.cycle_dir = tmp_path / "3"
    branch = {"id": "3", "hypothesis_ref": "H-0003", "hypothesis": BASE["hypothesis"]}
    selected = {**BASE, "id": "cand-new", "lineage_main_node_id": "H-0002",
                "lineage_other_node_ids": ["H-0001"]}
    state = {"hypothesis_lock": {"id": "H-0003"}}
    workflow._register_locked_hypothesis(state, branch, selected)
    # A replay must not duplicate provenance or the dossier snapshot.
    dossier = tmp_path / "notes/hypotheses/H-0003.md"
    original = dossier.read_text()
    workflow._register_locked_hypothesis(state, branch, selected)
    assert dossier.read_text() == original
    with pytest.raises(ValueError, match="lineage sources cannot change"):
        workflow._register_locked_hypothesis(state, branch, {
            **selected, "lineage_main_node_id": "H-0001", "lineage_other_node_ids": ["H-0002"]})
    assert dossier.read_text() == original
    assert "Main 来源（分支连线）：H-0002" in original
    assert "其它来源（仅追溯）：H-0001" in original
    with ScienceGraphStore(database, readonly=True) as store:
        detail = store.agent_hypothesis_detail("H-0003")
        assert detail["lineage_main_node_id"] == "H-0002"
        assert detail["lineage_other_node_ids"] == ["H-0001"]
        assert len(detail["lineage"]) == 2
        exported = canvas_projection(store)
    observed = _sqlite_graph_projection(tmp_path)
    for view in [exported, observed]:
        edges = [(e["fromNode"], e["toNode"]) for e in view["edges"]
                 if e.get("edge_type") != "research_source"]
        assert edges == [("H-0001", "H-0002"), ("H-0002", "H-0003")]
        text = next(n["text"] for n in view["nodes"] if n["id"] == "H-0003")
        assert "Main（分支连线）：H-0002" in text
        assert "其它（仅追溯）：H-0001" in text
    assert [(e["fromNode"], e["toNode"]) for e in observed["edges"]
            if e.get("edge_type") == "research_source"] == [("H-0001", "H-0003")]


def test_sqlite_refuses_a_second_main(tmp_path):
    with ScienceGraphStore(tmp_path / "graph.sqlite3") as store:
        for h in ["H-0001", "H-0002", "H-0003"]:
            store.add_hypothesis(h)
        store.add_evolution(edge_id="main", source="H-0001", target="H-0003", lineage_role="main")
        with pytest.raises(ValueError, match="already has a main"):
            store.add_evolution(edge_id="another", source="H-0002", target="H-0003", lineage_role="main")


def test_fake_workflow_preserves_main_and_other_through_qualification(tmp_path):
    import json
    from theory_master.backend_fakes import FakeBackend
    from theory_master.workflow import Workflow
    from tests.test_core import TheoryMasterTest

    class MultiSourceBackend(FakeBackend):
        def invoke(self, role, prompt, thread_id, model, provider):
            tid, raw = super().invoke(role, prompt, thread_id, model, provider)
            if role == "research_lead" and "Current task: frontier_proposal " in prompt:
                output = json.loads(raw)
                for candidate in output.get("candidates", []):
                    candidate["lineage_main_node_id"] = "H-0008"
                    candidate["lineage_other_node_ids"] = ["H-0007"]
                    candidate["transition"]["source_contributions"] = {
                        "H-0008": "Provides the main claim refined by this test.",
                        "H-0007": "Provides the independent comparison combined with the main claim.",
                    }
                return tid, json.dumps(output)
            return tid, raw

    spec, _ = TheoryMasterTest().make(tmp_path, reworks=0)
    database = spec.graph_database
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0007", title="First source")
        store.add_hypothesis("H-0008", title="Main source")
    state = Workflow(spec, MultiSourceBackend(reworks=0)).run()
    assert state["status"] in {"over", "completed"}
    with ScienceGraphStore(database, readonly=True) as store:
        detail = store.agent_hypothesis_detail("H-0001")
        assert detail["lineage_main_node_id"] == "H-0008"
        assert detail["lineage_other_node_ids"] == ["H-0007"]


def test_fresh_proposal_cannot_use_legacy_sources_but_saved_candidates_still_parse():
    contract = role_contract("research_lead", phase="frontier_proposal")
    saved = {"candidates": [{**BASE, "lineage_anchor_node_ids": ["H-0001", "H-0002"]}]}
    contract.validate(saved, KNOWN)
    with pytest.raises(ValueError, match="new frontier proposal requires"):
        contract.validate(saved, {**KNOWN, "_require_explicit_lineage": True})
