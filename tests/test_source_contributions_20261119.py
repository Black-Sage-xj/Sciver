"""Offline source conservation, contribution snapshots and recovery regressions."""
from __future__ import annotations

import copy
import json
import itertools

import pytest

from theory_master.backend_fakes import FakeBackend
from theory_master.contracts import role_contract
from theory_master.hypothesis_lineage import (
    assert_repair_lineage_preserved, lineage_repair_snapshot, normalize_singleton_references,
)
from theory_master.research_transition import research_transition_text
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.workflow import Workflow
from tests.test_research_transition_store import CANDIDATE
from tests.test_science_frontier_v2 import _Workflow


def proposal():
    candidate = copy.deepcopy(CANDIDATE)
    candidate.update(lineage_main_node_id="H-0007", lineage_other_node_ids=["H-0008"],
                     evolution_operator="refine")
    candidate["transition"]["source_contributions"] = {
        "H-0007": "Provides the original common-sample prediction and its baseline.",
        "H-0008": "Rules out the competing age-function explanation.",
    }
    return {"candidates": [candidate]}


KNOWN = {"H-0007", "H-0008", "H-0009"}


def test_new_contract_requires_exact_contributions_but_saved_candidates_remain_readable():
    contract = role_contract("research_lead", phase="frontier_proposal")
    assert contract.version == 14
    context = {"_known_hypothesis_ids": list(KNOWN), "_require_explicit_lineage": True}
    contract.validate(proposal(), context)
    old = proposal()
    old["candidates"][0]["transition"].pop("source_contributions")
    contract.validate(old, {"_known_hypothesis_ids": list(KNOWN)})
    with pytest.raises(ValueError, match="requires transition.source_contributions"):
        contract.validate(old, context)
    root = {"candidates": [copy.deepcopy(CANDIDATE)]}
    root["candidates"][0]["transition"]["source_contributions"] = {}
    contract.validate(root, {"_known_hypothesis_ids": [], "_require_explicit_lineage": True})


@pytest.mark.parametrize("notes", [
    {}, {"H-0007": "Baseline only"},
    {"H-0007": "Baseline", "H-0008": "Comparison", "H-0009": "Undeclared source"},
    {"H-0007": "Baseline", "H-0008": " "},
    {"H-0007": "Baseline", "H-0008": "x" * 401},
])
def test_contributions_cannot_omit_invent_or_truncate_sources(notes):
    value = proposal()
    value["candidates"][0]["transition"]["source_contributions"] = notes
    with pytest.raises(ValueError, match="source_contributions"):
        role_contract("research_lead", phase="frontier_proposal").validate(
            value, {"_known_hypothesis_ids": list(KNOWN), "_require_explicit_lineage": True})


def test_single_known_canonical_string_is_wrapped_without_mutating_the_original():
    original = proposal()
    original["candidates"][0]["lineage_other_node_ids"] = "H-0008"
    normalized, repairs = normalize_singleton_references(original, KNOWN)
    assert normalized["candidates"][0]["lineage_other_node_ids"] == ["H-0008"]
    assert original["candidates"][0]["lineage_other_node_ids"] == "H-0008"
    assert normalized["candidates"][0]["transition"] == original["candidates"][0]["transition"]
    assert repairs[0]["kind"] == "wrap_singleton_lineage_reference"


@pytest.mark.parametrize("source", [" H-0008", "H-0008 ", "H-0008,H-0009", "H-9999", "H-0007", ""])
def test_ambiguous_unknown_or_main_reference_is_never_guessed(source):
    value = proposal()
    value["candidates"][0]["lineage_other_node_ids"] = source
    assert normalize_singleton_references(value, KNOWN) == (value, [])


@pytest.mark.parametrize("change", ["delete", "swap", "add", "notes", "candidate"])
def test_contract_repair_cannot_change_valid_source_decisions(change):
    value = proposal()
    frozen = lineage_repair_snapshot(value, KNOWN)
    repaired = copy.deepcopy(value)
    candidate = repaired["candidates"][0]
    if change == "delete":
        candidate["lineage_other_node_ids"] = []
        candidate["transition"]["source_contributions"].pop("H-0008")
    elif change == "swap":
        candidate.update(lineage_main_node_id="H-0008", lineage_other_node_ids=["H-0007"])
    elif change == "add":
        candidate["lineage_other_node_ids"].append("H-0009")
    elif change == "notes":
        candidate["transition"]["source_contributions"]["H-0008"] = "A hindsight explanation"
    else:
        repaired["candidates"] = []
    with pytest.raises(ValueError, match="repair"):
        assert_repair_lineage_preserved(frozen, repaired)
    candidate = copy.deepcopy(value)
    candidate["candidates"][0]["why_now"] = "Correct an unrelated missing rationale"
    assert_repair_lineage_preserved(frozen, candidate)


def test_invalid_contribution_can_be_repaired_without_changing_its_source():
    original = proposal()
    original["candidates"][0]["transition"]["source_contributions"]["H-0008"] = "x" * 401
    corrected = proposal()
    assert_repair_lineage_preserved(lineage_repair_snapshot(original, KNOWN), corrected)


def test_source_guard_does_not_freeze_an_invalid_missing_subject_field():
    original = proposal()
    original["candidates"][0].pop("hypothesis")
    assert_repair_lineage_preserved(lineage_repair_snapshot(original, KNOWN), proposal())


def over_capacity_proposal():
    value = proposal()
    value["candidates"][0]["lineage_other_node_ids"] = ["H-0008", "H-0009", "H-0010"]
    value["candidates"][0]["transition"]["source_contributions"].update({
        "H-0009": "Provides an independent comparator.",
        "H-0010": "Defines the boundary of the comparison.",
    })
    return value


def test_over_capacity_pool_leaves_every_legal_subset_to_the_agent():
    original = over_capacity_proposal()
    before = copy.deepcopy(original)
    known = KNOWN | {"H-0010"}
    frozen = lineage_repair_snapshot(original, known)
    assert original == before
    assert frozen[0]["roles"] == {"H-0007": "main"}
    assert frozen[0]["repairable_other_sources"] == ["H-0008", "H-0009", "H-0010"]
    for n in range(3):
        for subset in itertools.combinations(frozen[0]["repairable_other_sources"], n):
            repaired = copy.deepcopy(original)
            candidate = repaired["candidates"][0]
            candidate["lineage_other_node_ids"] = list(subset)
            candidate["transition"]["source_contributions"] = {
                key: val for key, val in candidate["transition"]["source_contributions"].items()
                if key in {"H-0007", *subset}}
            role_contract("research_lead", phase="frontier_proposal").validate(
                repaired, {"_known_hypothesis_ids": list(known), "_require_explicit_lineage": True})
            assert_repair_lineage_preserved(frozen, repaired)


@pytest.mark.parametrize("change", ["main", "subject", "retained_note", "extra_source", "stale_note"])
def test_capacity_repair_cannot_rewrite_valid_science_or_invent_a_source(change):
    original = over_capacity_proposal()
    frozen = lineage_repair_snapshot(original, KNOWN | {"H-0010", "H-0011"})
    repaired = copy.deepcopy(original)
    candidate = repaired["candidates"][0]
    candidate["lineage_other_node_ids"] = ["H-0008", "H-0010"]
    candidate["transition"]["source_contributions"].pop("H-0009")
    if change == "main":
        candidate.update(lineage_main_node_id="H-0009")
    elif change == "subject":
        candidate["hypothesis"] = "A different scientific subject"
    elif change == "retained_note":
        candidate["transition"]["source_contributions"]["H-0010"] = "A new explanation"
    elif change == "extra_source":
        candidate["lineage_other_node_ids"] = ["H-0008", "H-0011"]
    else:
        candidate["transition"]["source_contributions"]["H-0009"] = "Removed source still cited"
    with pytest.raises(ValueError, match="repair"):
        assert_repair_lineage_preserved(frozen, repaired)


def test_real_invocation_over_capacity_repair_commits_only_agent_selected_sources(tmp_path):
    from tests.test_core import TheoryMasterTest

    class CapacityBackend(FakeBackend):
        repairs = []

        def invoke(self, role, prompt, thread_id, model, provider):
            if role == "research_lead" and ("Current task: frontier_proposal " in prompt
                    or "Contract validation failed for stage frontier_proposal" in prompt):
                value = over_capacity_proposal()
                if "Contract validation failed" in prompt:
                    self.repairs.append(prompt)
                    candidate = value["candidates"][0]
                    # The Agent chooses the last pair, not an SDK first-two truncation.
                    candidate["lineage_other_node_ids"] = ["H-0009", "H-0010"]
                    candidate["transition"]["source_contributions"].pop("H-0008")
                return self.ensure_thread(role, thread_id, model, provider), json.dumps(value)
            return super().invoke(role, prompt, thread_id, model, provider)

    spec, _ = TheoryMasterTest().make(tmp_path, reworks=0)
    with ScienceGraphStore(spec.graph_database) as store:
        for source in ("H-0007", "H-0008", "H-0009", "H-0010"):
            store.add_hypothesis(source)
    backend = CapacityBackend(reworks=0)
    state = Workflow(spec, backend).run()
    assert state["status"] in {"over", "completed"}
    assert state["contract_repair_count"] == 1 and len(backend.repairs) == 1
    assert "you must choose a legal subset" in backend.repairs[0]
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        detail = store.agent_hypothesis_detail("H-0001")
        assert detail["lineage_main_node_id"] == "H-0007"
        assert detail["lineage_other_node_ids"] == ["H-0009", "H-0010"]
        assert {row["hypothesis_id"] for row in detail["research_transition"]["sources"]} == {
            "H-0007", "H-0009", "H-0010"}


def test_frozen_notes_share_sources_with_dossier_graph_report_and_replay(tmp_path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        for source in ("H-0007", "H-0008"):
            store.add_hypothesis(source, title=source)
    selected = proposal()["candidates"][0]
    selected["id"] = "candidate"
    branch = {"id": "1", "hypothesis_ref": "H-0001", "hypothesis": selected["hypothesis"]}
    state = {"hypothesis_lock": {"id": "H-0001"}}
    workflow = _Workflow(tmp_path / "exploration.canvas")
    workflow.cycle_dir = tmp_path / "1"
    workflow._register_locked_hypothesis(state, branch, selected)
    frozen = state["hypothesis_lock"]["research_transition"]
    assert "source_contributions" not in frozen  # One persisted source representation.
    for source in frozen["sources"]:
        assert source["contribution"] == selected["transition"]["source_contributions"][source["hypothesis_id"]]
    dossier = tmp_path / "notes/hypotheses/H-0001.md"
    original = dossier.read_bytes()
    assert research_transition_text(frozen).encode() in original
    workflow._register_locked_hypothesis(state, branch, selected)
    assert dossier.read_bytes() == original
    with ScienceGraphStore(database, readonly=True) as store:
        assert store.agent_hypothesis_detail("H-0001")["research_transition"] == frozen
    changed = copy.deepcopy(selected)
    changed["transition"]["source_contributions"]["H-0008"] = "Changed contribution"
    with pytest.raises(ValueError, match="cannot change on replay"):
        workflow._register_locked_hypothesis(state, branch, changed)
    assert dossier.read_bytes() == original


def test_compact_and_detail_keep_main_other_and_unknown_roles(tmp_path):
    with ScienceGraphStore(tmp_path / "graph.sqlite3") as store:
        for source in ("H-0007", "H-0008", "H-0009"):
            store.add_hypothesis(source)
        store.add_evolution(edge_id="m", source="H-0007", target="H-0009", operator="refine", lineage_role="main")
        store.add_evolution(edge_id="o", source="H-0008", target="H-0009", operator="refine", lineage_role="other")
        store.add_evolution(edge_id="legacy", source="H-0007", target="H-0008", operator="refine")
        view = store.agent_view("H-0009")
        assert view["projection_version"] == "compact-frontier-v4"
        for graph in [view, store.agent_hypothesis_detail("H-0009"),
                      store.agent_candidate_review_view([proposal()["candidates"][0]]),
                      store.agent_goal_completion_view()]:
            assert graph["lineage_columns"][-1] == "role"
            roles = {(row[0], row[1]): row[-1] for row in graph["lineage"]}
            assert roles["H-0007", "H-0009"] == "main"
            assert roles["H-0008", "H-0009"] == "other"
        assert store.agent_hypothesis_detail("H-0008")["lineage"][0][-1] == "unknown"


@pytest.mark.parametrize("mode", ["singleton", "preserve", "drop"])
def test_runtime_normalization_and_contract_repair_conserve_sources(tmp_path, mode):
    from tests.test_core import TheoryMasterTest
    class SourceBackend(FakeBackend):
        def invoke(self, role, prompt, thread_id, model, provider):
            if role == "research_lead" and ("Current task: frontier_proposal " in prompt
                                            or "Contract validation failed for stage frontier_proposal" in prompt):
                value = proposal()
                repairing = "Contract validation failed" in prompt
                if mode == "singleton":
                    value["candidates"][0]["lineage_other_node_ids"] = "H-0008"
                elif not repairing:
                    value["candidates"][0]["goal_advance"] = ""
                elif mode == "drop":
                    value["candidates"][0]["lineage_other_node_ids"] = []
                    value["candidates"][0]["transition"]["source_contributions"].pop("H-0008")
                return self.ensure_thread(role, thread_id, model, provider), json.dumps(value)
            return super().invoke(role, prompt, thread_id, model, provider)

    spec, _ = TheoryMasterTest().make(tmp_path, reworks=0)
    with ScienceGraphStore(spec.graph_database) as store:
        for source in ("H-0007", "H-0008"):
            store.add_hypothesis(source, title=source)
    workflow = Workflow(spec, SourceBackend(reworks=0))
    state = workflow.run()
    if mode == "drop":
        assert state["attention"]["code"] == "contract_repair_failed"
        assert state["contract_repair_count"] == 3
        artifact = next(workflow.cycle_dir.glob("contract_repairs/*.json"))
        assert "lineage_content_loss" in artifact.read_text()
        with ScienceGraphStore(spec.graph_database, readonly=True) as store:
            assert store.db.execute("SELECT 1 FROM hypotheses WHERE id='H-0001'").fetchone() is None
    else:
        assert state["status"] in {"over", "completed"}
        with ScienceGraphStore(spec.graph_database, readonly=True) as store:
            detail = store.agent_hypothesis_detail("H-0001")
            assert detail["lineage_other_node_ids"] == ["H-0008"]
            assert len(detail["research_transition"]["sources"]) == 2
        if mode == "singleton":
            receipt = next(workflow.cycle_dir.glob("output_normalizations/*.json"))
            assert "wrap_singleton_lineage_reference" in receipt.read_text()
            assert not state.get("contract_repair_count")
        else:
            assert state["contract_repair_count"] == 1
