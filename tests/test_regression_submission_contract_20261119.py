"""Public format declarations connect only to newly instantiated fixtures."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from theory_master import regression_cases as cases
from theory_master.runtime import write_json
from theory_master.specification import TaskSpec
from theory_master.public_submission import load_submission_contract
from theory_master.task import TaskBinding, bootstrap

REFERENCE = "benchmark/submission_contract.json"
PUBLIC_SCHEMA = "theory-master/public-submission-contract/v1"


@pytest.fixture(autouse=True)
def offline_creation(tmp_path, monkeypatch):
    monkeypatch.delenv("SCIVER_TEST_CASES_ROOT", raising=False)
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("THEORY_MASTER_SEARCH_ROOT", str(tmp_path / "instances"))
    monkeypatch.setattr(
        "theory_master.runtime_manager.launch_managed_runtime",
        lambda *a, **k: pytest.fail("offline fixture creation must not launch"),
    )
    monkeypatch.setattr(
        "theory_master.codex_backend.CodexBackend.__init__",
        lambda *a, **k: pytest.fail("offline fixture creation must not invoke models"),
    )


def test_official_contract_declares_only_public_format_and_keeps_scientific_plan():
    case = cases.resolve_case("Test0")
    assert case.metadata["scientific_fixture"]["submission_contract"] == REFERENCE
    contract = json.loads((case.root / REFERENCE).read_text())
    assert contract == {
        "schema": PUBLIC_SCHEMA,
        "json_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["D_estimate", "method", "validation"],
            "properties": {
                "D_estimate": {"type": "number", "exclusiveMinimum": 0},
                "method": {"type": "string", "minLength": 1},
                "validation": {"type": "string", "minLength": 1},
            },
            "additionalProperties": True,
        },
    }
    Draft202012Validator.check_schema(contract["json_schema"])
    plan = case.plan_path.read_text()
    assert hashlib.sha256(plan.split("# Required Context")[0].encode()).hexdigest() == (
        "08817bb3f6f55641a9b086762ef32a3ddf17c3601ced11aa40dc6dff4415ebea"
    )
    assert REFERENCE in plan.split("# Required Context")[1]
    assert cases.verify_case("Test0")["files_verified"] == 1


@pytest.mark.parametrize(
    "submission",
    [
        {},
        {"D_estimate": 1, "method": "M"},
        {"D_estimate": 0, "method": "M", "validation": "V"},
        {"D_estimate": -1, "method": "M", "validation": "V"},
        {"D_estimate": "1", "method": "M", "validation": "V"},
        {"D_estimate": 1, "method": "", "validation": "V"},
        {"D_estimate": 1, "method": "M", "validation": ""},
    ],
)
def test_schema_rejects_incomplete_or_wrongly_typed_format(submission):
    contract = json.loads((cases.resolve_case("Test0").root / REFERENCE).read_text())
    assert not Draft202012Validator(contract["json_schema"]).is_valid(submission)


def test_schema_accepts_additional_scientific_details_without_supplying_an_answer():
    contract = json.loads((cases.resolve_case("Test0").root / REFERENCE).read_text())
    # A synthetic format-only example, never a scientific acceptance assertion.
    submission = {
        "D_estimate": 1,
        "method": "Synthetic method",
        "validation": "Synthetic check",
        "limitations": ["Additional authored scientific discussion"],
    }
    assert Draft202012Validator(contract["json_schema"]).is_valid(submission)


def test_materialization_copies_contract_bytes_without_private_evaluation(tmp_path):
    case = cases.resolve_case("Test0")
    destination = cases.materialize_case("Test0", tmp_path / "fresh")
    assert (destination / REFERENCE).read_bytes() == (
        case.root / REFERENCE
    ).read_bytes()
    assert not any(
        (destination / name).exists()
        for name in ("case.json", "expected.json", "evaluator")
    )


def test_new_official_instances_both_bind_contract_without_rewriting_old_config(
    tmp_path,
):
    first, outcome = cases.create_test_instance(
        "Test0", instances_root=tmp_path / "instances", start=False
    )
    original_config = first.config_path.read_bytes()
    second, _ = cases.create_test_instance(
        "Test0", instances_root=tmp_path / "instances", start=False
    )
    assert first.master_id != second.master_id
    assert (
        outcome["status"] == "prepared"
        and first.config_path.read_bytes() == original_config
    )
    for binding in (first, second):
        assert binding.config["submission_contract"] == REFERENCE
        assert (
            json.loads(binding.config_path.read_text())["submission_contract"]
            == REFERENCE
        )
        assert (binding.benchmark_root / "submission_contract.json").read_bytes() == (
            cases.resolve_case("Test0").root / REFERENCE
        ).read_bytes()
        assert binding.config["state_seed"] is None
        assert not any(path.name.isdigit() for path in binding.research_root.iterdir())
        spec = TaskSpec.from_config(binding.config_path)
        assert spec.submission_contract_ref == REFERENCE
        assert load_submission_contract(spec)["path"] == str(
            binding.benchmark_root / "submission_contract.json"
        )


def fake_cli_new(monkeypatch, allocation):
    def run(command, **kwargs):
        assert command[1] == "new" and kwargs["cwd"] == Path(command[2]).parent
        bootstrap(Path(command[2]), allocation_root=allocation)
        return SimpleNamespace(returncode=0, stdout="Created fresh task", stderr="")

    monkeypatch.setattr(cases.subprocess, "run", run)


def test_materialize_initialize_entry_binds_same_contract_and_explicit_input_root(
    tmp_path, monkeypatch
):
    fake_cli_new(monkeypatch, tmp_path)
    root = cases.initialize_case(
        "Test0", tmp_path / "cli-instance", executable="offline-new"
    )
    binding = TaskBinding.load(root)
    assert binding.config["submission_contract"] == REFERENCE
    assert (binding.benchmark_root / "submission_contract.json").is_file()
    assert not (binding.task_home / "expected.json").exists()


def private_case(tmp_path, monkeypatch, *, reference=None, declared=False):
    root = tmp_path / "private-fixture"
    (root / "benchmark").mkdir(parents=True)
    (root / "research_plan.md").write_text(
        "# Goal\nSynthetic task.\n\n# Research Contract\nPublic input.\n\n"
        "# Final Gate\nIndependent evidence.\n\n# Required Context\n"
        "- benchmark/source_manifest.json: public provenance.\n"
    )
    write_json(root / "benchmark/source_manifest.json", {"files": []})
    write_json(
        root / "benchmark/submission_contract.json",
        {
            "schema": PUBLIC_SCHEMA,
            "json_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
            },
        },
    )
    metadata = {"scientific_fixture": {}}
    if declared:
        metadata["scientific_fixture"]["submission_contract"] = reference
    case = cases.RegressionCase("private", root, metadata)
    monkeypatch.setattr(cases, "resolve_case", lambda selector: case)
    return case


@pytest.mark.parametrize("entry", ["test-instance", "materialize-new"])
def test_undeclared_private_contract_is_not_inferred(tmp_path, monkeypatch, entry):
    private_case(tmp_path, monkeypatch)
    if entry == "test-instance":
        binding, _ = cases.create_test_instance(
            "private", tmp_path / "instance", start=False
        )
    else:
        fake_cli_new(monkeypatch, tmp_path)
        binding = TaskBinding.load(
            cases.initialize_case("private", tmp_path / "instance")
        )
    assert "submission_contract" not in binding.config


@pytest.mark.parametrize(
    "reference",
    [
        None,
        "",
        "../outside.json",
        "/absolute.json",
        "./benchmark/submission_contract.json",
        "benchmark/../outside.json",
        "benchmark",
        "research_plan.md",
        "benchmark/missing.json",
        "benchmark/source_manifest.json",
    ],
)
def test_declared_contract_path_and_wrapper_fail_closed(
    tmp_path, monkeypatch, reference
):
    private_case(tmp_path, monkeypatch, declared=True, reference=reference)
    with pytest.raises(cases.RegressionCaseError):
        cases.verify_case("private")
    assert not (tmp_path / "instance").exists()


@pytest.mark.parametrize("fault", ["invalid-json", "invalid-schema", "symlink"])
def test_explicit_contract_must_be_safe_and_syntactically_valid(
    tmp_path, monkeypatch, fault
):
    case = private_case(tmp_path, monkeypatch, declared=True, reference=REFERENCE)
    path = case.root / REFERENCE
    if fault == "invalid-json":
        path.write_text("not JSON")
    elif fault == "invalid-schema":
        write_json(
            path, {"schema": PUBLIC_SCHEMA, "json_schema": {"type": "unknown-type"}}
        )
    else:
        other = case.root / "other.json"
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
    with pytest.raises(cases.RegressionCaseError):
        cases.verify_case("private")


def test_copied_contract_cannot_silently_change_outside_original_source_manifest(
    tmp_path, monkeypatch
):
    private_case(tmp_path, monkeypatch, declared=True, reference=REFERENCE)
    original = cases.shutil.copytree

    def changed(source, destination, **kwargs):
        result = original(source, destination, **kwargs)
        path = destination / "submission_contract.json"
        content = json.loads(path.read_text())
        content["json_schema"]["required"] = ["new-field"]
        write_json(path, content)
        return result

    monkeypatch.setattr(cases.shutil, "copytree", changed)
    with pytest.raises(cases.RegressionCaseError, match="differs"):
        cases.materialize_case("private", tmp_path / "instance")


def test_packaged_fixture_allowlist_includes_public_contract():
    tree = ast.parse((cases.PACKAGE_ROOT / "setup.py").read_text())
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PUBLIC_FIXTURE_FILES"
            for target in node.targets
        )
    )
    assert "test_0/" + REFERENCE in ast.literal_eval(assignment.value)
