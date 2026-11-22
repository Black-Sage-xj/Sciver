import json
from types import SimpleNamespace

import pytest

from theory_master.backend_fakes import FakeBackend, _fixture_prompt_context, _prompt_stage
from theory_master.judge_followup import candidate_problem, qualification_followup
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.workflow import Workflow
from test_task_progression_continuation_20261119 import _task


class FollowupBackend(FakeBackend):
    def __init__(self):
        super().__init__(reworks=1)
        self.successors = []
        self.reviews = []

    def invoke(self, role, prompt, thread_id, model, provider):
        thread, raw = super().invoke(role, prompt, thread_id, model, provider)
        context = _fixture_prompt_context(prompt)
        followup = context.get("judge_followup")
        if followup and _prompt_stage(prompt) == "frontier_proposal":
            self.successors.append((thread, context))
            if len(self.successors) == 1:
                candidate = json.loads(raw)["candidates"][0]
                candidate.update(followup["prior_objective"])
                return thread, json.dumps({"candidates": [candidate]})
        if followup and _prompt_stage(prompt) == "candidate_review":
            self.reviews.append(context)
        return thread, raw


def test_judge_successor_gets_complete_feedback_and_fresh_review_on_retry(tmp_path, monkeypatch):
    spec = _task(tmp_path, monkeypatch, max_cycles=2)
    backend = FollowupBackend()
    state = Workflow(spec, backend).run()
    parent = json.loads((spec.research_root/"1/research_state.json").read_text())
    assert parent["judge_followup"]["hypothesis_id"] == "H-0001"
    assert state["hypothesis_lock"]["id"] == "H-0002"
    assert state["incoming_judge_followup"] == {"cycle": "1", "hypothesis_id": "H-0001"}
    assert len(backend.successors) == 2
    assert {t for t, _ in backend.successors} == {parent["threads"]["research_lead"]}
    for _, context in backend.successors:
        assert context["agent_graph"]["entry_hypothesis_id"] == "H-0001"
        decision = context["judge_followup"]["judge_decision"]
        assert decision["objections"][0]["requested_resolution"] == "run the controlled check"
        assert decision["target_executor"] == "investigator"
        assert decision["needs_external_scholarship"] is False
    assert len(backend.reviews) == 1
    assert backend.reviews[0]["judge_followup"] == backend.successors[-1][1]["judge_followup"]
    assert all(c["frozen_objective"]["hypothesis"] != parent["hypothesis_lock"]["hypothesis"] for c in backend.reviews[0]["candidate"])
    with ScienceGraphStore(spec.graph_database, readonly=True) as graph:
        edges = list(graph.db.execute("SELECT source_hypothesis, target_hypothesis, provenance_json FROM scientific_edges"))
    assert [(e[0], e[1], json.loads(e[2])["lineage_role"]) for e in edges] == [("H-0001", "H-0002", "main")]


def test_followup_selects_own_predecessor_and_masks_blind_judge_details(tmp_path, monkeypatch):
    spec = _task(tmp_path, monkeypatch, max_cycles=1)
    Workflow(spec, FakeBackend(reworks=1)).run()
    first = json.loads((spec.research_root/"1/research_state.json").read_text())
    for num, worker, values in [
        (1, "own", first),
        (2, "other", {"status": "over"}),
        (3, "own", {"status": "no_qualified_candidate", "incoming_judge_followup": {"cycle":"1","hypothesis_id":"H-0001"}}),
    ]:
        directory = spec.research_root/str(num)
        directory.mkdir(exist_ok=True)
        (directory/".theory-master-cycle").touch()
        (directory/"research_state.json").write_text(json.dumps({**values, "worker_id":worker,
            "cycle_execution_context":{"worker_id":worker}}))
    owner = SimpleNamespace(spec=spec, worker_id="own", _graph_database_path=lambda:spec.graph_database,
                            _persist=lambda state:None)
    s = SimpleNamespace(owner=owner, state={"judge_cycle_boundary":True}, run_id="4")
    monkeypatch.setattr("theory_master.judge_followup.holdout_files", lambda path: ("hidden_fixture",))
    view = qualification_followup(s)
    assert view["hypothesis_id"] == "H-0001"
    assert "judge_decision" not in view and "run the controlled check" not in str(view)
    s.state["incoming_judge_followup"]["hypothesis_id"] = "H-unknown"
    with pytest.raises(ValueError, match="disagree"):
        qualification_followup(s)
    s.state = {"judge_cycle_boundary":True}
    owner.worker_id = "other"
    assert qualification_followup(s) is None
    owner.worker_id = "own"
    from dataclasses import replace
    path = spec.research_root/"1/research_state.json"
    recorded = json.loads(path.read_text())
    recorded["judge_followup"]["source_branch_anchor_id"] = spec.branch_anchor_id
    path.write_text(json.dumps(recorded))
    owner.spec = replace(spec, branch_anchor_id="H-different-branch")
    assert qualification_followup(s) is None
    owner.spec = replace(spec, goal_id="different-goal")
    assert qualification_followup(s) is None
    owner.spec = spec
    s.state["human_direction"] = {"status":"claimed"}
    assert qualification_followup(s) is None


def test_same_question_with_changed_test_can_qualify_but_unrelated_parent_cannot():
    objective = {"hypothesis":"mechanism A", "scope":"measured regime", "decisive_test":"first prediction"}
    feedback = {"hypothesis_id":"H-0001","prior_objective":objective}
    valid = {**objective, "decisive_test":"second prediction", "lineage_main_node_id":"H-0001"}
    assert candidate_problem(valid, feedback) is None
    assert candidate_problem({**valid, "lineage_main_node_id":"H-9999"}, feedback)
    assert candidate_problem({**valid, **objective}, feedback)


def test_exhausted_followup_retains_parent_for_later_lead_and_does_not_lock(tmp_path, monkeypatch):
    spec = _task(tmp_path, monkeypatch, max_cycles=1)
    Workflow(spec, FakeBackend(reworks=1)).run()

    class Duplicate(FakeBackend):
        def invoke(self, role, prompt, thread_id, model, provider):
            thread, raw = super().invoke(role, prompt, thread_id, model, provider)
            if role == "research_lead" and _prompt_stage(prompt) == "frontier_proposal":
                view = _fixture_prompt_context(prompt)["judge_followup"]
                response = json.loads(raw)
                response["candidates"][0].update(view["prior_objective"])
                return thread, json.dumps(response)
            return thread, raw

    def unexpected(*args, **kwargs):
        pytest.fail("Judge follow-up candidate must come from Research Lead")

    monkeypatch.setattr("theory_master.supervisor_qualification_recovery.preceding_qualification_stop", unexpected)
    monkeypatch.setattr("theory_master.supervisor_qualification_recovery.review_exhausted_frontier", unexpected)
    failed = Workflow(spec, Duplicate()).run()
    assert failed["status"] == "no_qualified_candidate" and not failed["hypothesis_locked"]
    assert len(failed["qualification_attempts"]) == 3
    successor = Workflow(spec, FakeBackend(reworks=0)).run()
    assert successor["hypothesis_lock"]["id"] == "H-0003"
    with ScienceGraphStore(spec.graph_database, readonly=True) as db:
        assert [tuple(row) for row in db.db.execute(
            "SELECT source_hypothesis, target_hypothesis FROM scientific_edges"
        )] == [("H-0001", "H-0003")]
