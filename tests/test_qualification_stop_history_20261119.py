"""Cross-Cycle preparation feedback and exact-repeat guards use no real models."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from theory_master.backends import FakeBackend
from theory_master.invocations import stable_hash
from theory_master.qualification_stop_history import MAX_FILE_BYTES, previous_qualification_stop, recent_qualification_stops
from theory_master.runtime import write_json
from theory_master.scientific_objective import frozen_science_digest, review_binding
from theory_master.specification import TaskSpec
from theory_master.task import bootstrap
from theory_master.workflow import Workflow


SCIENCE = {"hypothesis": "A bounded transfer relation", "scope": "Frozen authorized records",
           "decisive_test": "Compare the prescribed two transfer models"}


def archive(root: Path, number=1, *, verdict="revise"):
    cycle = root / str(number)
    cycle.mkdir(parents=True)
    (cycle / ".theory-master-cycle").write_text("theory-master/v2\nfixture\n")
    candidate = {"id": "old-candidate", **SCIENCE}
    review = {"candidate_id": candidate["id"], "verdict": verdict,
              "reason": "The indispensable comparison units are unavailable.",
              "blocking_issues": [{"problem": "Missing independent units", "required_change": "Resolve unit provenance"}],
              "reviewed_science": review_binding(candidate["id"], SCIENCE)}
    entries = []
    for role, stage, output in [
        ("research_lead", "frontier_proposal", {"candidates": [candidate]}),
        ("challenger", "candidate_review", {"reviews": [review]}),
        ("research_lead", "selection_repair", {"selected_parent_candidate_id": candidate["id"],
         "outcome": "cannot_repair", "reason": "No authorized independent unit identifiers exist."}),
    ]:
        ident = f"inv-{number}-{role}-{stage}"
        closure = {"role": role, "stage": stage, "closed_output": output}
        write_json(cycle / "semantic_closure" / f"{ident}.json", closure)
        entries.append({"invocation_id": ident, "role": role, "stage": stage, "state": "committed",
                        "commit_state": "committed", "closure_hash": stable_hash(closure)})
    (cycle / "invocations.jsonl").write_text("\n".join(json.dumps(row) for row in entries) + "\n")
    write_json(cycle / "research_state.json", {
        "status": "no_qualified_candidate", "qualification_status": "no_qualified_candidate",
        "execution_active": False, "scientific_verdict": None,
        "hypothesis_lock": {"status": "unlocked"},
        "qualification_attempts": [{"candidate_ids": [candidate["id"]]}],
    })
    return cycle


def test_recovers_exact_science_and_bound_reviews_from_append_only_closures(tmp_path):
    archive(tmp_path, 1)
    newest = archive(tmp_path, 2, verdict="reject")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    [row] = recent_qualification_stops(tmp_path, current_cycle="3", limit=1)
    assert row["cycle_id"] == "2" and row["candidate_id"] == "old-candidate"
    assert row["frozen_objective"] == SCIENCE
    assert row["frozen_objective_sha256"] == frozen_science_digest(SCIENCE)
    assert row["review"]["verdict"] == "reject"
    assert row["review"]["blocking_issues"][0]["problem"] == "Missing independent units"
    assert row["cannot_repair_reason"] == "No authorized independent unit identifiers exist."
    assert len(row["source_paths"]) == 3
    assert all(Path(p).is_relative_to(newest) for p in row["source_paths"])
    assert {p: p.read_bytes() for p in before} == before
    assert recent_qualification_stops(tmp_path, current_cycle="2", limit=1)[0]["cycle_id"] == "1"
    assert recent_qualification_stops(tmp_path, current_cycle="3", limit=0) == []


@pytest.mark.parametrize("defect", ["hash", "binding", "uncommitted", "symlink", "oversized", "malformed", "locked", "scientific"])
def test_untrusted_or_scientific_records_do_not_become_preparation_feedback(tmp_path, defect):
    cycle = archive(tmp_path)
    state_path = cycle / "research_state.json"
    state = json.loads(state_path.read_text())
    closure = next((cycle / "semantic_closure").glob("*candidate_review.json"))
    if defect in {"hash", "binding"}:
        value = json.loads(closure.read_text())
        value["closed_output"]["reviews"][0]["reviewed_science"]["sha256"] = "wrong"
        write_json(closure, value)
        if defect == "binding":
            ledger = [json.loads(line) for line in (cycle / "invocations.jsonl").read_text().splitlines()]
            ledger[1]["closure_hash"] = stable_hash(value)
            (cycle / "invocations.jsonl").write_text("\n".join(json.dumps(row) for row in ledger))
    elif defect == "uncommitted":
        (cycle / "invocations.jsonl").write_text((cycle / "invocations.jsonl").read_text().replace('"committed"', '"completed"'))
    elif defect == "symlink":
        outside = tmp_path / "outside.json"
        outside.write_bytes(closure.read_bytes())
        closure.unlink()
        closure.symlink_to(outside)
    elif defect == "oversized":
        state_path.write_text(" " * (MAX_FILE_BYTES + 1))
    elif defect == "malformed":
        (cycle / "invocations.jsonl").write_text("not JSON")
    elif defect == "locked":
        state["hypothesis_lock"] = {"status": "locked"}
        write_json(state_path, state)
    elif defect == "scientific":
        state["scientific_verdict"] = "open"
        write_json(state_path, state)
    assert recent_qualification_stops(tmp_path, current_cycle="2") == []


class CaptureBackend(FakeBackend):
    def __init__(self, *, change_test=False, science=None):
        super().__init__(reworks=0)
        self.change_test = change_test
        self.science = science or SCIENCE
        self.lead_inputs, self.challenger_inputs = [], []

    def invoke(self, role, prompt, thread_id, model, provider):
        context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0] if "Context:\n" in prompt else {}
        if role == "research_lead" and "Current task: frontier_proposal" in prompt:
            self.lead_inputs.append(context)
        if role == "challenger":
            self.challenger_inputs.append(context)
        thread, raw = super().invoke(role, prompt, thread_id, model, provider)
        value = json.loads(raw)
        if role == "research_lead" and value.get("candidates"):
            for candidate in value["candidates"]:
                candidate.update(self.science)
                if self.change_test:
                    candidate["decisive_test"] += " using a distinct, available paired contrast"
        return thread, json.dumps(value)


def make_workflow(tmp_path, backend):
    plan = tmp_path / "research_plan.md"
    plan.write_text("# Goal\nFind the transfer relation\n\n# Research Contract\nAuthorized sources only\n\n"
                    "# Final Gate\nA bounded independent test\n\n# Required Context\nLocal inputs\n")
    spec = TaskSpec.from_config(bootstrap(plan).config_path)
    archive(spec.research_root)
    return Workflow(spec, backend, stop_before="investigator")


@pytest.mark.parametrize("alias", ["old-candidate", "different-candidate-id"])
def test_unchanged_science_never_gets_a_new_challenger_verdict_or_investigator(tmp_path, alias):
    backend = CaptureBackend()
    wf = make_workflow(tmp_path, backend)
    history = recent_qualification_stops(wf.spec.research_root, current_cycle="2")
    assert previous_qualification_stop({"id": alias, **SCIENCE}, history) is not None
    with patch.dict("os.environ", {"THEORY_MASTER_SUPERVISOR": "0"}):
        state = wf._execute_cycle()
    assert state["status"] == "no_qualified_candidate" and not state["hypothesis_locked"]
    assert len(backend.lead_inputs) == 3 and backend.challenger_inputs == []
    assert len(state["qualification_operational_duplicates"]) == 3
    assert state["frontier_review"]["reviews"] == []
    assert all(row["source_candidate_id"] == "old-candidate" for row in state["qualification_operational_duplicates"])
    assert backend.lead_inputs[0]["recent_qualification_stops"][0]["frozen_objective"] == SCIENCE


def test_changed_test_gets_independent_review_with_actual_serialized_feedback(tmp_path):
    backend = CaptureBackend(change_test=True)
    wf = make_workflow(tmp_path, backend)
    from test_execution_limit_history_20261119 import archive as archive_limit
    archive_limit(wf.spec.research_root, 2)
    state = wf._execute_cycle()
    assert state["status"] == "checkpoint_reached" and state["hypothesis_locked"]
    assert len(backend.challenger_inputs) == 1
    for context in [backend.lead_inputs[0], backend.challenger_inputs[0]]:
        [stop] = context["recent_qualification_stops"]
        assert stop["frozen_objective"] == SCIENCE and stop["review"]["verdict"] == "revise"
        assert context["recent_execution_limits"][0]["required_dependency"]
    assert state["hypothesis_lock"]["decisive_test"] != SCIENCE["decisive_test"]
    assert "qualification_operational_duplicates" not in state


def test_input_limited_science_can_be_requalified_with_dependency_feedback(tmp_path):
    from test_execution_limit_history_20261119 import archive as archive_limit
    science = {"hypothesis": "Response follows input", "scope": "Frozen source A",
               "decisive_test": "Fit only qualified observations"}
    backend = CaptureBackend(science=science)
    wf = make_workflow(tmp_path, backend)
    archive_limit(wf.spec.research_root, 2)
    with patch.dict("os.environ", {"THEORY_MASTER_SUPERVISOR": "0"}):
        state = wf._execute_cycle()
    assert state["status"] == "checkpoint_reached" and state["hypothesis_locked"]
    assert len(backend.challenger_inputs) == 1
    assert backend.challenger_inputs[0]["recent_execution_limits"][0]["required_dependency"]
    assert "qualification_operational_duplicates" not in state
