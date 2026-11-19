"""Offline authority, source conservation and single-use Lead reentry tests."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from theory_master import supervisor_lineage_recovery as recovery
from theory_master import supervisor_repair_receipts as receipts
from theory_master.backend_fakes import FakeBackend
from theory_master.contracts import role_contract
from theory_master.invocations import stable_hash
from theory_master.runtime import write_json
from theory_master.runtime_supervisor_review import resume_blocker, review_runtime_failure
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.workflow import Workflow
from theory_master.workflow_support import prequalification_restart_allowed
from tests.test_source_contributions_20261119 import over_capacity_proposal


@pytest.fixture
def stopped_lead(tmp_path, monkeypatch):
    from tests.test_core import TheoryMasterTest
    spec, _ = TheoryMasterTest().make(tmp_path, reworks=0)
    with ScienceGraphStore(spec.graph_database) as store:
        for source in ("H-0007", "H-0008", "H-0009", "H-0010"):
            store.add_hypothesis(source)
    workflow = Workflow(spec, FakeBackend(reworks=0))
    cycle = spec.research_root / "1"
    workflow._bind_cycle(cycle, create=True)
    state = workflow._new_state()
    invocation, thread = "inv-1-research_lead-original", "original-lead-thread"
    artifact = cycle / "contract_repairs" / (invocation + ".json")
    original = over_capacity_proposal()
    original["needs_literature"] = False
    notes = original["candidates"][0]["transition"]["source_contributions"]
    roles = {"H-0007": "main", "H-0008": "other", "H-0009": "other", "H-0010": "other"}
    protected = [{"index": 0, "hypothesis": original["candidates"][0]["hypothesis"],
                  "roles": roles, "exact": False, "contributions": notes}]
    prompt = "Historical bounded contract repair.\n" + recovery._OLD_PROMPT + json.dumps(protected)
    context = {"agent_graph": {"hypothesis_columns": ["id"],
                              "hypotheses": [[source] for source in roles]}}
    smaller = copy.deepcopy(original)
    smaller["candidates"][0]["lineage_other_node_ids"] = ["H-0009", "H-0010"]
    raw = [json.dumps(value) for value in (original, smaller, original, smaller)]
    contract = role_contract("research_lead", phase="frontier_proposal")
    errors = [
        {"path": "/", "code": "contract_violation", "expected": contract.ref, "actual": "object",
         "message": "transition.source_contributions keys must exactly match main and other source H IDs"},
        {"path": "/candidates", "code": "lineage_content_loss", "expected": "preserved source decisions",
         "actual": "changed lineage", "message":
         "candidates[0] repair must preserve valid lineage IDs and main/other roles: " + str(roles)},
    ]
    fault = {"action": "contract_repair_failed", "invocation_id": invocation,
             "role": "research_lead", "stage": "frontier_proposal", "thread_id": thread,
             "artifact": str(artifact), "repair_prompt": prompt, "at": "original-occurrence"}
    state.update(status="needs_attention", phase=recovery._PHASE, protocol_health="violation",
                 execution_active=False, resumable=True, recovery=fault,
                 attention={"code": "contract_repair_failed"}, threads={"research_lead": thread},
                 qualification_status="pending", hypothesis_lock={"status": "unlocked"},
                 role_outputs={}, contract_repair_count=3)
    write_json(artifact, {**fault, "raw_outputs": raw, "validation_errors": errors})
    ledger = [{"invocation_id": invocation, "state": "prepared", "event": "invocation.prepared",
               "role": "research_lead", "stage": "frontier_proposal", "commit_state": "not_committed",
               "contract_ref": contract.descriptor(), "logical_key": stable_hash("slot"),
               "input_hash": stable_hash(context)},
              {"invocation_id": invocation, "state": "failed", "thread_id": thread,
               "event": "invocation.failed", "error_type": "ContractRepairFailed"}]
    (cycle / "invocations.jsonl").write_text("".join(json.dumps(row) + "\n" for row in ledger))
    rows = [{"event": "AGENT_INPUT", "role": "research_lead", "stage": "frontier_proposal",
             "input_kind": "task", "content": "Context:\n" + json.dumps(context)}]
    for index, value in enumerate(raw):
        rows.append({"event": "LLM_MESSAGE", "role": "research_lead", "message_role": "assistant",
                     "thread_id": thread, "content": value})
        if index != 3:
            rows.append({"event": "AGENT_INPUT", "role": "research_lead", "stage": "frontier_proposal",
                         "input_kind": "contract_repair", "content": prompt})
    (cycle / "runtime_events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    write_json(cycle / "research_state.json", state)
    # Historical Git byte lookup is independently covered below; use a small
    # fixture here so checkout depth does not weaken the recovery regression.
    monkeypatch.setattr(recovery, "_historical_source_proof", lambda *_: {
        "git_head": "1" * 40, "module_sha256": {"workflow.py": "2" * 64},
        "hypothesis_lineage_sha256": recovery._OLD_LINEAGE_SHA256})
    return spec, cycle, state


def validate(fixture):
    _, cycle, state = fixture
    return recovery.validate_existing_lineage_structure(cycle, state,
        engine=Path(recovery.__file__).resolve().parent)


def test_receipt_preserves_original_fault_science_and_all_raw_deliveries(stopped_lead):
    _, cycle, state = stopped_lead
    original = copy.deepcopy(state)
    raw = Path(state["recovery"]["artifact"]).read_bytes()
    assert not prequalification_restart_allowed(state)
    record = validate(stopped_lead)
    assert record["validator"] == recovery.VALIDATOR
    assert record["checkpoint"] is None and record["runtime_verification"] == "pending"
    assert json.loads(record["tests"])["legal_subsets_checked"] == 7
    assert receipts.inspect_repair_receipt(cycle, state) == record
    assert prequalification_restart_allowed(state)
    assert resume_blocker(state, runtime_revalidated=True).startswith("Protocol/contract")
    assert resume_blocker(state, runtime_revalidated=True, contract_projection_repair=record) is None
    assert {k: v for k, v in state.items() if not k.startswith("supervisor_")} == {
        k: v for k, v in original.items() if not k.startswith("supervisor_")}
    assert Path(state["recovery"]["artifact"]).read_bytes() == raw


def test_stopped_prepare_installs_only_supervisor_receipt_bookkeeping(stopped_lead):
    spec, cycle, state = stopped_lead
    original = copy.deepcopy(state)
    receipt = recovery.prepare_lineage_reentry(spec.research_root)
    persisted = json.loads((cycle / "research_state.json").read_text())
    assert receipts.inspect_repair_receipt(cycle, persisted) == receipt
    assert {k: v for k, v in persisted.items() if not k.startswith("supervisor_")} == original
    assert persisted["protocol_health"] == "violation"
    assert not persisted.get("supervisor_lineage_reentries")


@pytest.mark.parametrize("mutation", ["locked", "outputs", "active", "other_stage", "other_error", "changed_science", "missing_input", "symlink"])
def test_unrelated_or_unpreserved_fault_cannot_receive_receipt(stopped_lead, mutation):
    _, cycle, state = stopped_lead
    path = Path(state["recovery"]["artifact"])
    if mutation == "locked":
        state["hypothesis_lock"] = {"status": "locked", "id": "H-0001"}
    elif mutation == "outputs":
        state["role_outputs"] = {"research_lead_frontier": {"candidates": []}}
    elif mutation == "active":
        state["execution_active"] = True
    elif mutation == "other_stage":
        state["recovery"]["stage"] = "frontier_maintenance"
    elif mutation == "other_error":
        value = json.loads(path.read_text()); value["validation_errors"][0]["message"] = "Missing decisive_test"
        write_json(path, value)
    elif mutation == "changed_science":
        value = json.loads(path.read_text()); answer = json.loads(value["raw_outputs"][1])
        answer["candidates"][0]["decisive_test"] = "Different test"
        value["raw_outputs"][1] = json.dumps(answer); write_json(path, value)
    elif mutation == "missing_input":
        (cycle / "runtime_events.jsonl").unlink()
    else:
        moved = cycle / "outside.json"; path.rename(moved); path.symlink_to(moved)
    write_json(cycle / "research_state.json", state)
    assert validate(stopped_lead) is None


@pytest.mark.parametrize("mutation", ["fault", "science", "artifact", "events", "config", "graph", "source"])
def test_receipt_binds_full_occurrence_science_inputs_and_source(stopped_lead, mutation, monkeypatch):
    spec, cycle, state = stopped_lead
    assert validate(stopped_lead)
    if mutation == "fault":
        state["recovery"]["at"] = "different-occurrence"
    elif mutation == "science":
        state["scientific_model_current_state"] = "Changed model"
    elif mutation == "source":
        manifest = receipts.source_manifest(Path(receipts.__file__).resolve().parent)
        monkeypatch.setattr(receipts, "source_manifest", lambda _: {**manifest, "changed.py": "bad"})
    else:
        path = {"artifact": Path(state["recovery"]["artifact"]),
                "events": cycle / "runtime_events.jsonl", "config": spec.config_path,
                "graph": spec.graph_database}[mutation]
        path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        receipts.inspect_repair_receipt(cycle, state)


def test_consumption_is_single_use_and_retains_original_failure_audit(stopped_lead):
    _, cycle, state = stopped_lead
    assert validate(stopped_lead)
    fault = copy.deepcopy(state["recovery"])
    # Supervisor credit consumption and Workflow reentry are different steps.
    state["supervisor_consumed_repairs"] = [receipts.repair_credit_key(state)]
    assert recovery.consume_lineage_reentry(cycle, state)
    assert state["protocol_health"] == "valid" and state["recovery"] == fault
    assert state["supervisor_lineage_reentry_audit"][0]["original_failure"] == fault
    assert state["hypothesis_lock"] == {"status": "unlocked"} and not state["role_outputs"]
    replay = copy.deepcopy(state)
    replay.update(protocol_health="violation", attention={"code": "contract_repair_failed"})
    assert not recovery.has_verified_lineage_reentry(replay, cycle=cycle)
    with pytest.raises(ValueError, match="verified lineage"):
        recovery.consume_lineage_reentry(cycle, replay)


@pytest.mark.parametrize("guard", ["budget", "operator", "abandon"])
def test_reentry_never_waives_independent_runtime_guards(stopped_lead, guard):
    _, cycle, state = stopped_lead
    if guard == "budget":
        state["investigator_budget"] = {"exhausted": True}
    elif guard == "abandon":
        state["abandoned_cycle"] = True
    write_json(cycle / "research_state.json", state)
    assert validate(stopped_lead)
    if guard == "operator":
        write_json(cycle / "recovery_control.json", {"action": "pause"})
    before = copy.deepcopy(state)
    with pytest.raises(ValueError):
        recovery.consume_lineage_reentry(cycle, state)
    assert state == before


def test_current_failure_requires_receipt_even_for_direct_workflow_resume(stopped_lead):
    spec, cycle, _ = stopped_lead
    with pytest.raises(ValueError, match="verified lineage"):
        Workflow(spec, FakeBackend()).resume(cycle.name)


@pytest.mark.parametrize("existing_only,previous_resumes", [(False, 0), (True, 3)])
def test_runtime_review_uses_receipt_without_any_model_or_repair_call(stopped_lead, existing_only, previous_resumes):
    _, cycle, state = stopped_lead
    state["supervisor_runtime_resume_count"] = previous_resumes
    if existing_only:
        assert validate(stopped_lead)
        write_json(cycle / "research_state.json", state)
    original = copy.deepcopy(state)
    backend = MagicMock()
    with patch("theory_master.supervisor_runtime_repair.attempt_runtime_repair") as engineering:
        result = review_runtime_failure(backend=backend, research_root=cycle.parent, cycle_dir=cycle,
            state=state, state_path=cycle / "research_state.json", incident_id="INC-lineage-offline",
            existing_receipt_only=existing_only, scheduler_locked=existing_only)
    assert result["action"] == "resume"
    assert state["supervisor_runtime_resume_count"] == previous_resumes + 1
    assert {k: v for k, v in state.items() if not k.startswith("supervisor_")} == {
        k: v for k, v in original.items() if not k.startswith("supervisor_")}
    assert state["protocol_health"] == "violation"
    backend.invoke_runtime_review.assert_not_called()
    backend.invoke.assert_not_called()
    engineering.assert_not_called()
    assert recovery.consume_lineage_reentry(cycle, state)
    assert state["supervisor_runtime_resume_count"] == previous_resumes + 1


def test_actual_workflow_resume_asks_agent_and_commits_its_legal_subset(stopped_lead):
    spec, cycle, state = stopped_lead
    assert validate(stopped_lead)
    write_json(cycle / "research_state.json", state)

    class ReenteringLead(FakeBackend):
        proposals = 0

        def invoke(self, role, prompt, thread_id, model, provider):
            if role == "research_lead" and "Current task: frontier_proposal " in prompt:
                self.proposals += 1
                value = over_capacity_proposal(); candidate = value["candidates"][0]
                candidate["lineage_other_node_ids"] = ["H-0009", "H-0010"]
                candidate["transition"]["source_contributions"].pop("H-0008")
                return self.ensure_thread(role, thread_id, model, provider), json.dumps(value)
            return super().invoke(role, prompt, thread_id, model, provider)

    backend = ReenteringLead(reworks=0)
    final = Workflow(spec, backend).resume(cycle.name)
    assert final["status"] in {"over", "completed"} and final["protocol_health"] == "valid"
    assert backend.proposals == 1
    assert len(final["supervisor_lineage_reentries"]) == 1
    assert final["scientific_verdict"] == "supported"
    assert json.loads(Path(state["recovery"]["artifact"]).read_text())["validation_errors"]
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        detail = store.agent_hypothesis_detail("H-0001")
        assert detail["lineage_other_node_ids"] == ["H-0009", "H-0010"]


def test_historical_source_proof_checks_each_recorded_hash_and_recognized_bug(tmp_path, monkeypatch):
    # Exercise Git/source validation separately from receipt fixture plumbing.
    engine = tmp_path / "repo/theory_master"; engine.mkdir(parents=True)
    source = {"workflow.py": b"original workflow", "hypothesis_lineage.py": b"old lineage guard"}
    monkeypatch.setattr(recovery, "_OLD_LINEAGE_SHA256", hashlib.sha256(source["hypothesis_lineage.py"]).hexdigest())
    monkeypatch.setattr(recovery, "_git_bytes", lambda _engine, _head, name: source[name])
    state = {"runtime_source_provenance": {"source_coherent": True, "source_root": str(engine.parent),
        "sdk_git_root": str(engine.parent), "sdk_git_sha": "1" * 40,
        "module_paths": {"workflow.py": str(engine / "workflow.py")},
        "module_sha256": {"workflow.py": hashlib.sha256(source["workflow.py"]).hexdigest()}}}
    # Restore the actual implementation hidden by the fixture monkeypatch.
    assert recovery._historical_source_proof(engine, state)["git_head"] == "1" * 40
    source["workflow.py"] = b"different loaded source"
    with pytest.raises(ValueError, match="source hash"):
        recovery._historical_source_proof(engine, state)
