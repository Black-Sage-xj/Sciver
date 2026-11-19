"""Provenance delivery errors use existing repair before Evidence side effects."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.backends import FakeBackend
from theory_master.contract_closure import close_agent_output
from theory_master.contracts import role_contract
from theory_master.evidence import EvidenceRegistry
from theory_master.evidence_provenance import ProvenanceValidationError
from theory_master.invocation_output import InvocationOutputValidation
from theory_master.specification import TaskSpec
from theory_master.task import bootstrap
from theory_master.workflow import Workflow
from theory_master.workflow_invocation import CONTRACT_REPAIR_LIMIT
from theory_master.workflow_support import RecoveryStop

OUTSIDE_ERROR = (
    "protected provenance declaration_ref must be outside the current Cycle workspace"
)
BAD_REF = "investigation/independent_check.json"
FIELD_PATH = "/findings/0/provenance/declaration_ref"


def _finding(reference=BAD_REF):
    return {
        "findings": [
            {
                "statement": "The retained bounded comparison has a qualified result.",
                "method": "A retained computation with separately recorded checks.",
                "result": {"value": 1, "limitations": ["reused public observations"]},
                "provenance": {
                    "source": "benchmark_provided",
                    "split": "validation",
                    "declaration_ref": reference,
                },
            }
        ],
    }


def _registry(tmp_path):
    cycle = tmp_path / "research" / "4"
    workspace = cycle / "workspace"
    (workspace / "investigation").mkdir(parents=True)
    (workspace / BAD_REF).write_text('{"source_sha256":{},"check_completed":true}')
    benchmark = tmp_path / "benchmark"
    benchmark.mkdir()
    declaration = benchmark / "source_manifest.json"
    declaration.write_text('{"public_inputs":["observations.csv"]}')
    registry = EvidenceRegistry(
        cycle, workspace, research_root=cycle.parent, benchmark_root=benchmark
    )
    return registry, declaration


def _validator(registry, output):
    session = SimpleNamespace(
        owner=SimpleNamespace(_evidence_registry=lambda: registry),
        role="investigator",
        stage="investigate",
        state={},
        validation_context={},
        contract=role_contract("investigator", phase="investigate"),
    )
    return InvocationOutputValidation(session).validate_output(output)


def test_workspace_recomputation_ref_is_an_exact_delivery_error_without_writes(
    tmp_path,
):
    registry, _ = _registry(tmp_path)
    authored = _finding()
    original = copy.deepcopy(authored)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    validation = _validator(registry, authored)
    assert not validation.valid
    assert [(e.path, e.code, e.message) for e in validation.errors] == [
        (FIELD_PATH, "provenance_invalid", OUTSIDE_ERROR)
    ]
    assert authored == original
    assert not registry.evidence_dir.exists()
    assert before == {
        str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize(
    "provenance,field,message",
    [
        ({"source": "agent_generated", "split": "holdout"}, "split", "agent-generated"),
        ({"source": "external_source", "split": "gate"}, "source", "requires frozen"),
        (
            {"source": "benchmark_provided", "split": "validation"},
            "declaration_ref",
            "requires declaration_ref",
        ),
        (
            {"source": "benchmark_provided", "declaration_ref": "missing.json"},
            "declaration_ref",
            "exactly one",
        ),
        (
            {"source": "benchmark_provided", "declaration_ref": "bad\0reference"},
            "declaration_ref",
            "cannot be resolved",
        ),
    ],
)
def test_preflight_reports_the_existing_rule_at_the_affected_field(
    tmp_path, provenance, field, message
):
    registry, _ = _registry(tmp_path)
    authored = _finding()
    authored["findings"][0]["provenance"] = provenance
    issues = registry.preflight_finding_provenance(authored)
    assert len(issues) == 1
    assert issues[0]["path"] == "/findings/0/provenance/" + field
    assert message in issues[0]["message"]
    assert not registry.evidence_dir.exists()


@pytest.mark.parametrize("change", ["delete", "workspace-symlink"])
def test_registration_rechecks_sources_after_a_successful_read_only_preflight(
    tmp_path, change
):
    registry, declaration = _registry(tmp_path)
    authored = _finding("benchmark/source_manifest.json")
    assert _validator(registry, authored).valid
    assert not registry.evidence_dir.exists()
    declaration.unlink()
    if change == "workspace-symlink":
        declaration.symlink_to(registry.workspace / BAD_REF)
    with pytest.raises(ProvenanceValidationError, match="exactly one"):
        registry.register_role_output(
            "investigator",
            close_agent_output("investigator", "investigate", authored, {}, {}),
            epoch="post_lock_test",
            producer_invocation={
                "invocation_id": "inv-test",
                "role": "investigator",
                "stage": "investigate",
            },
        )
    assert not registry.records_path.exists()


class RepairBackend(FakeBackend):
    def __init__(self):
        super().__init__(reworks=0)
        self.responses = []
        self.investigator_calls = []

    def invoke(self, role, prompt, thread_id, model, provider):
        if role != "investigator" or not self.responses:
            return super().invoke(role, prompt, thread_id, model, provider)
        identifier = self.ensure_thread(role, thread_id, model, provider)
        self.investigator_calls.append((identifier, prompt))
        response = self.responses[
            min(len(self.investigator_calls) - 1, len(self.responses) - 1)
        ]
        return identifier, json.dumps(response)


class NativeDeliveryBackend:
    """Use real invocation policy with a deterministic official-Goal return."""

    def __init__(self):
        self.delegate = FakeBackend(reworks=0)
        self.goal_calls = []
        self.investigator_turns = []
        self.last_native_goal = {}
        self.output = _finding()

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def invoke(self, role, prompt, thread_id, model, provider, **kwargs):
        if role == "investigator":
            self.investigator_turns.append(prompt)
            raise AssertionError(
                "Native delivery must not start an additional repair Turn"
            )
        return self.delegate.invoke(role, prompt, thread_id, model, provider)

    def invoke_goal(self, role, *, handoff_path, thread_id, model, provider, **kwargs):
        identifier = self.delegate.ensure_thread(role, thread_id, model, provider)
        output = self.output
        Path(handoff_path).write_text(json.dumps(output))
        self.last_native_goal = {
            "status": "complete",
            "thread_id": identifier,
            "physical_turns": 1,
        }
        self.goal_calls.append((identifier, kwargs))
        return identifier, json.dumps(output)


def _prepared(tmp_path, monkeypatch, backend):
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("THEORY_MASTER_SUPERVISOR", "0")
    home = tmp_path / "notebook"
    home.mkdir()
    plan = home / "research_plan.md"
    plan.write_text(
        "# Goal\n\nOffline provenance contract.\n\n"
        "# Research Contract\n\nRetain the public input identity.\n\n"
        "# Final Gate\n\nIndependent assessment.\n\n# Required Context\n\nLocal fixture.\n"
    )
    binding = bootstrap(plan)
    benchmark = tmp_path / "public-benchmark"
    benchmark.mkdir()
    config = json.loads(binding.config_path.read_text())
    config["benchmark_root"] = str(benchmark)
    binding.config_path.write_text(json.dumps(config))
    spec = TaskSpec.from_config(binding.config_path)
    workflow = Workflow(spec, backend, stop_before="investigator")
    state = workflow.run()
    assert state["status"] == "checkpoint_reached"
    workflow.stop_before = None
    workspace = workflow.cycle_workspace
    (workspace / "investigation").mkdir()
    (workspace / BAD_REF).write_text('{"check_completed":true}')
    spec.benchmark_root.mkdir(exist_ok=True)
    declaration = spec.benchmark_root / "source_manifest.json"
    declaration.write_text('{"public_inputs":["observations.csv"]}')
    return workflow, state, declaration


@pytest.mark.parametrize(
    "bad_ref", [BAD_REF, "bad\0reference"], ids=["workspace-output", "invalid-path"]
)
def test_existing_bounded_repair_keeps_original_thread_science_body_and_h(
    tmp_path, monkeypatch, bad_ref
):
    backend = RepairBackend()
    workflow, state, declaration = _prepared(tmp_path, monkeypatch, backend)
    initial, corrected = _finding(bad_ref), _finding("benchmark/source_manifest.json")
    backend.responses = [initial, corrected]
    authored_originals = copy.deepcopy(backend.responses)
    before_lock = copy.deepcopy(state["hypothesis_lock"])
    source_bytes = declaration.read_bytes()
    output = workflow._call(state, "investigator", {"hypothesis_lock": before_lock})
    assert state["contract_repair_count"] == 1
    assert len(backend.investigator_calls) == 2
    assert len({identifier for identifier, _ in backend.investigator_calls}) == 1
    assert FIELD_PATH in backend.investigator_calls[1][1]
    assert (
        "do not remove or downgrade source identity" in backend.investigator_calls[1][1]
    )
    assert state["hypothesis_lock"] == before_lock
    assert declaration.read_bytes() == source_bytes
    assert output["evidence"][0]["summary"] == initial["findings"][0]["statement"]
    for field in ("method", "result"):
        assert output["evidence"][0][field] == initial["findings"][0][field]
    assert backend.responses == authored_originals
    row = json.loads(
        workflow._evidence_registry().records_path.read_text().splitlines()[0]
    )
    assert row["provenance"]["declaration_ref"] == str(declaration)
    assert row["provenance"]["protected_identity_verified"] is False


@pytest.mark.parametrize(
    "mutation",
    ["science-value", "delete-provenance", "downgrade-source", "change-split"],
)
def test_provenance_only_repair_cannot_drop_identity_or_change_science(
    tmp_path, monkeypatch, mutation
):
    backend = RepairBackend()
    workflow, state, _ = _prepared(tmp_path, monkeypatch, backend)
    before_lock = copy.deepcopy(state["hypothesis_lock"])
    invalid = _finding()
    changed = _finding("benchmark/source_manifest.json")
    finding = changed["findings"][0]
    if mutation == "science-value":
        finding["result"][
            "value"
        ] = True  # Python 1 == True must not defeat content identity.
    elif mutation == "delete-provenance":
        finding.pop("provenance")
    elif mutation == "downgrade-source":
        finding["provenance"]["source"] = "agent_generated"
    else:
        finding["provenance"]["split"] = "none"
    backend.responses = [invalid, changed]
    with pytest.raises(RecoveryStop):
        workflow._call(state, "investigator", {"hypothesis_lock": before_lock})
    assert state["contract_repair_count"] == CONTRACT_REPAIR_LIMIT
    assert len(backend.investigator_calls) == 1 + CONTRACT_REPAIR_LIMIT
    assert len({identifier for identifier, _ in backend.investigator_calls}) == 1
    assert state["hypothesis_lock"] == before_lock
    assert not workflow._evidence_registry().records_path.exists()
    receipt = next((workflow.cycle_dir / "contract_repairs").glob("*.json"))
    errors = json.loads(receipt.read_text())["validation_errors"]
    assert any(error["code"] == "provenance_repair_content_loss" for error in errors)


def test_repeated_bad_reference_uses_the_existing_bounded_repair_limit(
    tmp_path, monkeypatch
):
    backend = RepairBackend()
    workflow, state, _ = _prepared(tmp_path, monkeypatch, backend)
    backend.responses = [_finding()]
    with pytest.raises(RecoveryStop):
        workflow._call(
            state, "investigator", {"hypothesis_lock": state["hypothesis_lock"]}
        )
    assert state["contract_repair_count"] == CONTRACT_REPAIR_LIMIT
    assert len(backend.investigator_calls) == 1 + CONTRACT_REPAIR_LIMIT
    assert state["attention"]["code"] == "contract_repair_failed"
    assert not workflow._evidence_registry().records_path.exists()


@pytest.mark.parametrize(
    "bad_ref,error",
    [
        (BAD_REF, OUTSIDE_ERROR),
        ("bad\0reference", "provenance declaration_ref cannot be resolved"),
    ],
)
def test_native_goal_stops_with_precise_contract_error_and_zero_extra_turns(
    tmp_path, monkeypatch, bad_ref, error
):
    backend = NativeDeliveryBackend()
    backend.output = _finding(bad_ref)
    workflow, state, _ = _prepared(tmp_path, monkeypatch, backend)
    before_lock = copy.deepcopy(state["hypothesis_lock"])
    with pytest.raises(RecoveryStop):
        workflow._call(state, "investigator", {"hypothesis_lock": before_lock})
    assert len(backend.goal_calls) == 1
    assert backend.investigator_turns == []
    assert not state.get("contract_repair_count")
    assert state["attention"]["code"] == "contract_repair_failed"
    assert state["hypothesis_lock"] == before_lock
    assert not workflow._evidence_registry().records_path.exists()
    receipt = json.loads(
        next((workflow.cycle_dir / "contract_repairs").glob("*.json")).read_text()
    )
    assert [(e["path"], e["message"]) for e in receipt["validation_errors"]] == [
        (FIELD_PATH, error)
    ]
    assert len(receipt["raw_outputs"]) == 1
    journal = json.loads(
        next((workflow.cycle_dir / "native_deliveries").glob("*.json")).read_text()
    )
    assert journal["native_goal"]["status"] == "complete"
    assert (
        journal["raw_sha256"]
        == hashlib.sha256(receipt["raw_outputs"][0].encode()).hexdigest()
    )


def test_current_output_contract_explains_the_existing_source_declaration_guard():
    prompt = role_contract("investigator", phase="investigate").prompt_projection({})
    assert "original input/source declaration outside this Cycle workspace" in prompt
    assert "not a result, script or recomputation output created in this turn" in prompt
    assert "path alone never proves pre-Cycle freeze" in prompt
