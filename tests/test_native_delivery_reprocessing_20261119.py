"""Offline original-delivery closure and subsequent canonical reuse, with no provider."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from theory_master.backends import FakeBackend
from theory_master.invocations import InvocationLedger, stable_hash
from theory_master.native_delivery_reprocessing import reprocess_failed_native_delivery
from theory_master.native_goal import NativeGoalResult, read_goal_findings
from theory_master.runtime import write_json
from theory_master.scientific_objective import frozen_science_digest, locked_science_view
from theory_master.specification import TaskSpec
from theory_master.task import bootstrap
from theory_master.workflow import Workflow


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def delivered(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("THEORY_MASTER_SUPERVISOR", "0")
    notes = tmp_path / "notebook"
    notes.mkdir()
    plan = notes / "research_plan.md"
    plan.write_text(
        "# Goal\n\nOffline fixture.\n\n# Research Contract\n\nLocal comparison.\n\n"
        "# Final Gate\n\nIndependent support.\n\n# Required Context\n\n"
        "- benchmark/source_manifest.json: public provenance.\n"
    )
    spec = TaskSpec.from_config(bootstrap(plan).config_path)
    benchmark = tmp_path / "explicit-benchmark"
    benchmark.mkdir()
    declaration = benchmark / "source_manifest.json"
    declaration.write_text('{"schema":"original-public-fixture"}\n')
    spec = replace(spec, benchmark_dir=benchmark)
    backend = FakeBackend(reworks=0)
    owner = Workflow(spec, backend, stop_before="investigator")
    assert owner.run()["status"] == "checkpoint_reached"
    owner.stop_before = None
    cycle = owner.cycle_dir
    raw = {"findings": [{
        "statement": "The fixture comparison has the stated measured value.",
        "method": "Read the saved deterministic local comparison.",
        "result": {"value": 1},
        "provenance": {"source": "benchmark_provided", "split": "validation",
                       "declaration_ref": "benchmark/source_manifest.json"},
    }]}
    ordinary_invoke = backend.invoke

    def invoke(role, prompt, thread_id, model, provider):
        if role != "investigator":
            return ordinary_invoke(role, prompt, thread_id, model, provider)
        identifier = backend.ensure_thread(role, thread_id, model, provider)
        state = json.loads((cycle / "research_state.json").read_text())
        workspace = cycle / "workspace"
        (workspace / "result.txt").write_text("value=1\n")
        handoff = workspace / ".tm-handoff" / (state["active_invocation_id"] + ".json")
        write_json(handoff, raw)
        return identifier, json.dumps(raw)

    monkeypatch.setattr(backend, "invoke", invoke)
    register = owner._register_evidence

    def failed_registration(*args, **kwargs):
        raise ValueError("provenance declaration_ref must resolve to exactly one local artifact")

    monkeypatch.setattr(owner, "_register_evidence", failed_registration)
    with pytest.raises(ValueError, match="exactly one local artifact"):
        owner.resume("1")
    monkeypatch.setattr(owner, "_register_evidence", register)
    state = json.loads((cycle / "research_state.json").read_text())
    invocation = state["last_invocation_id"]
    snapshot = InvocationLedger(cycle / "invocations.jsonl").snapshot(invocation)
    manifest = cycle / "invocation_artifacts" / (invocation + ".json")
    handoff = cycle / "workspace/.tm-handoff" / (invocation + ".json")
    binding = {
        "authority": "verified_original_success_return_path",
        "resume_preparation": "saved_delivery_semantic_closure",
        "invocation_id": invocation, "thread_id": snapshot["thread_id"],
        "role": "investigator", "stage": "investigate", "logical_key": snapshot["logical_key"],
        "contract_ref": snapshot["contract_ref"], "handoff_path": str(handoff),
        "handoff_sha256": sha(handoff),
        "raw_findings_sha256": stable_hash(json.loads(read_goal_findings(
            NativeGoalResult("complete", snapshot["thread_id"], 1, 0), handoff))),
        "frozen_objective_sha256": frozen_science_digest(locked_science_view(state["hypothesis_lock"])),
        "artifact_manifest_path": str(manifest), "artifact_manifest_sha256": sha(manifest),
        "config_path": str(spec.config_path), "benchmark_root": str(benchmark),
        "research_root": str(spec.research_root),
    }
    # Authority detection has its own validator regressions. These tests use a
    # genuine signed SDK receipt to exercise its current-failure/input checks
    # and the complete original invocation closure/reuse path independently.
    from theory_master import supervisor_repair_receipts as receipts
    monkeypatch.setattr(receipts, "source_manifest", lambda engine: {"fixture-source": "unchanged"})

    def authorize(**updates):
        payload = {**binding, **updates}
        state = json.loads((cycle / "research_state.json").read_text())
        paths = [handoff, manifest, declaration, cycle / "invocations.jsonl", cycle / "events.jsonl"]
        inventory = json.loads(manifest.read_text())
        paths += [Path(row["path"]) for row in inventory["created"] + inventory["modified"]]
        receipts._record_validation(
            cycle, state, Path(receipts.__file__).parent,
            validator="benchmark-provenance-alias/v1", module="evidence.py", change_id="offline-fixture",
            tests=json.dumps(payload), manifest=receipts.source_manifest(Path(receipts.__file__).parent),
            disposition="already_fixed", extra_inputs={str(path): sha(path) for path in paths},
        )
        write_json(cycle / "research_state.json", state)

    authorize()
    calls = []

    def no_provider(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Recovery attempted a provider call")

    monkeypatch.setattr(backend, "invoke", no_provider)
    monkeypatch.setattr(backend, "ensure_thread", no_provider)
    monkeypatch.setattr(backend, "invoke_goal", no_provider, raising=False)
    monkeypatch.setattr(owner, "scholarly_retriever", no_provider)
    monkeypatch.setattr("theory_master.abc_pipeline.annotate_literature", no_provider)
    return owner, cycle, binding, authorize, calls


def test_original_delivery_closes_once_and_normal_slot_reuses_without_model(delivered):
    owner, cycle, binding, _, calls = delivered
    manifest = Path(binding["artifact_manifest_path"])
    original_manifest = manifest.read_bytes()
    original_rows = (cycle / "invocations.jsonl").read_bytes()
    handoff = Path(binding["handoff_path"])
    original_handoff = handoff.read_bytes()
    result = reprocess_failed_native_delivery(owner)
    assert result["extra_model_requests"] == 0 and calls == []
    assert (cycle / "invocations.jsonl").read_bytes().startswith(original_rows)
    state = json.loads((cycle / "research_state.json").read_text())
    assert state["status"] == "paused" and state["execution_active"] is False
    assert state["phase"] == "engineering_delivery_reprocessed" and state["resumable"] is True
    assert state["recovery"]["action"] == "engineering_delivery_reprocessed"
    assert state["scientific_verdict"] is None
    assert state["investigation_round_count"] == 0
    assert state["supervisor_delivery_reprocessing"]["status"] == "committed"
    archive = Path(state["supervisor_delivery_reprocessing"]["original_failed_manifest"])
    assert archive.read_bytes() == original_manifest
    assert handoff.read_bytes() == original_handoff
    completed = json.loads(manifest.read_text())
    original = json.loads(original_manifest)
    assert completed["status"] == "completed"
    for key in ("created", "modified", "deleted", "captured_at", "workspace"):
        assert completed[key] == original[key]
    snapshot = InvocationLedger(cycle / "invocations.jsonl").snapshot(binding["invocation_id"])
    assert snapshot["state"] == "committed" and snapshot["commit_state"] == "committed"
    assert snapshot["closure_hash"] == result["closure_hash"]
    context = {"hypothesis_lock": state["hypothesis_lock"], "investigation_attempt": 1, "round": 1}
    output = owner._call(state, "investigator", context)
    assert output == result["output"]
    assert state["last_invocation_id"] == binding["invocation_id"]
    assert state["invocation_reuse_count"] == 1 and calls == []
    records = owner._evidence_context()["evidence_manifest"]["payload"]["records"]
    assert len(records) == 1
    assert records[0]["provenance"]["freeze_status"] == "unverified"
    assert records[0]["provenance"]["protected_identity_verified"] is False
    with pytest.raises(ValueError):
        reprocess_failed_native_delivery(owner)


@pytest.mark.parametrize("target", ["raw", "scientific_file", "declaration", "manifest", "failure"])
def test_changed_receipt_input_never_closes_or_calls_provider(delivered, target):
    owner, cycle, binding, _, calls = delivered
    paths = {
        "raw": Path(binding["handoff_path"]), "scientific_file": cycle / "workspace/result.txt",
        "declaration": owner.spec.benchmark_root / "source_manifest.json",
        "manifest": Path(binding["artifact_manifest_path"]), "failure": cycle / "research_state.json",
    }
    path = paths[target]
    path.write_text(path.read_text() + " ")
    if target == "failure":
        state = json.loads(path.read_text())
        state["recovery"]["at"] = "another failure"
        write_json(path, state)
    before = (cycle / "invocations.jsonl").read_bytes()
    with pytest.raises(ValueError):
        reprocess_failed_native_delivery(owner)
    assert (cycle / "invocations.jsonl").read_bytes() == before
    assert not (cycle / "semantic_closure" / (binding["invocation_id"] + ".json")).exists()
    assert calls == []


@pytest.mark.parametrize("updates", [
    {"authority": "a success sentence"}, {"stage": "judge_rework"}, {"logical_key": "another-slot"},
    {"thread_id": "another-thread"}, {"contract_ref": {}}, {"frozen_objective_sha256": "0" * 64},
    {"raw_findings_sha256": "0" * 64}, {"invocation_id": "../another"},
    {"config_path": "/another/config.json"}, {"benchmark_root": "/another/benchmark"},
    {"research_root": "/another/research"},
])
def test_even_current_receipt_cannot_change_original_delivery_identity(delivered, updates):
    owner, cycle, binding, authorize, calls = delivered
    authorize(**updates)
    before = (cycle / "invocations.jsonl").read_bytes()
    with pytest.raises(ValueError):
        reprocess_failed_native_delivery(owner)
    assert (cycle / "invocations.jsonl").read_bytes() == before and calls == []


def test_contract_rejection_has_no_llm_repair_or_successful_closure(delivered, monkeypatch):
    owner, cycle, binding, _, calls = delivered
    from theory_master import native_delivery_reprocessing as recovery
    from theory_master.output_validation import ValidationError, ValidationResult
    monkeypatch.setattr(recovery, "validate_agent_output", lambda *a: ValidationResult(False, (
        ValidationError("/findings", "fixture", "original contract", "invalid", "rejected"),)))
    before = Path(binding["artifact_manifest_path"]).read_bytes()
    with pytest.raises(ValueError, match="fails current contract"):
        reprocess_failed_native_delivery(owner)
    assert Path(binding["artifact_manifest_path"]).read_bytes() == before
    assert calls == []
    assert not (cycle / "semantic_closure" / (binding["invocation_id"] + ".json")).exists()


def test_registration_failure_restores_failed_inventory_and_never_commits(delivered, monkeypatch):
    owner, cycle, binding, _, calls = delivered
    original = Path(binding["artifact_manifest_path"]).read_bytes()
    monkeypatch.setattr(owner, "_register_evidence", lambda *a, **kw: (_ for _ in ()).throw(ValueError("still invalid")))
    with pytest.raises(ValueError, match="still invalid"):
        reprocess_failed_native_delivery(owner)
    assert Path(binding["artifact_manifest_path"]).read_bytes() == original
    assert InvocationLedger(cycle / "invocations.jsonl").snapshot(binding["invocation_id"])["state"] == "failed"
    state = json.loads((cycle / "research_state.json").read_text())
    assert state["supervisor_delivery_reprocessing"]["status"] == "failed"
    assert state["status"] == "failed" and state["scientific_verdict"] is None and calls == []


def test_active_runtime_refuses_before_delivery_mutation(delivered, monkeypatch):
    owner, cycle, binding, _, calls = delivered
    import theory_master.runtime_manager as runtime
    monkeypatch.setattr(runtime, "runtime_health", lambda root: {"active": True})
    before = (cycle / "invocations.jsonl").read_bytes()
    with pytest.raises(ValueError, match="inactive runtime"):
        reprocess_failed_native_delivery(owner)
    assert (cycle / "invocations.jsonl").read_bytes() == before and calls == []


@pytest.mark.parametrize("guard", [
    {"resumable": False}, {"abandoned_cycle": True},
    {"investigator_budget": {"exhausted": True}},
    {"investigator_budget": {"rejected": True}},
    {"protocol_health": "violation"},
])
def test_current_receipt_never_waives_existing_stop_or_budget_guards(delivered, guard):
    owner, cycle, binding, authorize, calls = delivered
    path = cycle / "research_state.json"
    state = json.loads(path.read_text())
    state.update(guard)
    write_json(path, state)
    authorize()
    before = Path(binding["artifact_manifest_path"]).read_bytes()
    with pytest.raises(ValueError, match="recovery guard"):
        reprocess_failed_native_delivery(owner)
    assert Path(binding["artifact_manifest_path"]).read_bytes() == before and calls == []


def test_pending_operator_command_is_not_consumed(delivered):
    owner, cycle, binding, _, calls = delivered
    command = cycle / "recovery_control.json"
    write_json(command, {"action": "pause", "reason": "operator stop"})
    with pytest.raises(ValueError, match="Pending operator control"):
        reprocess_failed_native_delivery(owner)
    assert command.exists() and calls == []


@pytest.mark.parametrize("phase", ["semantic_closure", "checkpoint", "commit"])
def test_mid_commit_failure_never_marks_operational_recovery_paused(delivered, monkeypatch, phase):
    owner, cycle, binding, _, calls = delivered
    import theory_master.contract_closure as closure
    if phase == "semantic_closure":
        monkeypatch.setattr(closure, "close_agent_output", lambda *a: (_ for _ in ()).throw(ValueError("closure sentinel")))
    elif phase == "checkpoint":
        monkeypatch.setattr(owner, "_checkpoint", lambda *a: (_ for _ in ()).throw(ValueError("checkpoint sentinel")))
    else:
        transition = InvocationLedger.transition

        def reject_commit(ledger, invocation_id, status, **details):
            if status == "committed":
                raise ValueError("commit sentinel")
            return transition(ledger, invocation_id, status, **details)

        monkeypatch.setattr(InvocationLedger, "transition", reject_commit)
    original_manifest = Path(binding["artifact_manifest_path"]).read_bytes()
    with pytest.raises(ValueError, match="sentinel"):
        reprocess_failed_native_delivery(owner)
    state = json.loads((cycle / "research_state.json").read_text())
    assert state["status"] == "failed" and state["scientific_verdict"] is None
    assert state["active_invocation_id"] is None and state["active_role"] is None
    assert state["execution_active"] is False
    assert state["supervisor_delivery_reprocessing"]["status"] == "failed" and calls == []
    assert Path(state["supervisor_delivery_reprocessing"]["original_failed_manifest"]).read_bytes() == original_manifest
    snapshot = InvocationLedger(cycle / "invocations.jsonl").snapshot(binding["invocation_id"])
    if phase == "semantic_closure":
        assert snapshot["state"] == "failed"
        assert Path(binding["artifact_manifest_path"]).read_bytes() == original_manifest
    else:
        # Once canonical closure is durable, normal invocation reuse safely
        # completes its checkpoint instead of repeating the provider request.
        assert snapshot["state"] == "completed" and snapshot["commit_state"] == "not_committed"
        assert snapshot["closure_hash"]
