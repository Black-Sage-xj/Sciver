"""Python submission checks are limited to exact registered source structure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.public_submission import (
    PYTHON_SCHEMA,
    check_current_submission,
    check_frozen_submission,
    load_submission_contract,
)


DECLARATION = {
    "schema": PYTHON_SCHEMA,
    "filename": "solution.py",
    "callable": {"name": "solve", "parameters": ["x", "y"]},
    "allowed_imports": ["numpy", "math"],
}
SCRIPT = b"import numpy.linalg as algebra\nraise RuntimeError('must never run')\ndef solve(x, /, y):\n    from math import sqrt\n    return x + y\n"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def make_contract(root, value=DECLARATION):
    path = root / "submission.json"
    path.write_text(json.dumps(value))
    return load_submission_contract(SimpleNamespace(benchmark_root=root, submission_contract_ref="benchmark/submission.json"))


def add_candidate(payload, path, raw=SCRIPT, *, role=None, producer="investigator", lifecycle="active", pinned=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    content = digest(raw)
    index = payload["material_index"]["materials"]
    index.append({
        "relation": "same_invocation" if pinned else None,
        "manifest_sha256": "a" * 64 if pinned else None,
        "producer_invocation": {"role": producer, "invocation_id": "I-test"},
        "integrity": {"status": "verified"},
        "evidence_states": {"E-1": {"lifecycle_status": lifecycle}},
        "assets": [{"path": str(path), "status": "available", "role": role,
                    "sha256": content if pinned else None}],
    })
    payload["goal_material_sources"].append({"path": str(path), "status": "available", "sha256": content})


@pytest.fixture
def payload(tmp_path):
    return {"submission_contract": make_contract(tmp_path),
            "material_index": {"materials": []}, "goal_material_sources": []}


def test_live_and_frozen_python_structure_never_execute_registered_source(tmp_path, payload):
    path = tmp_path / "workspace" / "solution.py"
    add_candidate(payload, path)
    expected = {"status": "valid", "verification": "python_ast_structure",
                "contract_sha256": payload["submission_contract"]["sha256"],
                "submission": {"path": str(path), "sha256": digest(SCRIPT)}}
    assert check_current_submission(payload) == expected
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"files": [{"path": "workspace/solution.py", "sha256": digest(SCRIPT)}]}))
    snapshot = SimpleNamespace(root=tmp_path, manifest_path=manifest)
    assert check_frozen_submission(payload, snapshot) == expected
    payload["submission_contract"]["allowed_imports"].append("pathlib")
    with pytest.raises(ValueError, match="frozen authority"):
        check_frozen_submission(payload, snapshot)


@pytest.mark.parametrize("source,detail", [
    (b"async def solve(x, y): pass", "synchronous"),
    (b"class Other:\n    def solve(x, y): pass", "top-level"),
    (b"def solve(x, y): pass\ndef solve(x, y): pass", "one top-level"),
    (b"def solve(y, x): pass", "positional signature"),
    (b"def solve(x, y=1): pass", "positional signature"),
    (b"def solve(x, *, y): pass", "positional signature"),
    (b"def solve(x, y, *args): pass", "positional signature"),
    (b"import numpy_extra\ndef solve(x,y): pass", "numpy_extra"),
    (b"def solve(x,y):\n    from .numpy import array", ".numpy"),
    (b"def solve(x,y):\n    import math, pathlib", "pathlib"),
    (b"def solve(x,y) return x", "Invalid Python syntax"),
])
def test_python_contract_reports_literal_shape_mismatch(tmp_path, payload, source, detail):
    add_candidate(payload, tmp_path / "solution.py", source)
    result = check_current_submission(payload)
    assert result["status"] == "invalid" and result["verification"] == "python_ast_structure"
    assert detail in result["artifact_errors"][0]["errors"][0]["message"]


@pytest.mark.parametrize("filename,flags", [
    ("different.py", {}),
    ("solution.py", {"pinned": False}),
    ("solution.py", {"producer": "literature"}),
    ("solution.py", {"lifecycle": "retracted"}),
    ("solution.py", {"role": "input"}),
])
def test_python_candidate_rejects_wrong_name_and_unregistered_inactive_or_input_material(tmp_path, payload, filename, flags):
    add_candidate(payload, tmp_path / filename, **flags)
    result = check_current_submission(payload)
    assert result["code"] == "registered_submission_schema_absent"
    assert result["matching_artifacts"] == []


def test_invalid_old_script_can_be_supplemented_but_two_valid_paths_are_ambiguous(tmp_path, payload):
    add_candidate(payload, tmp_path / "old/solution.py", b"def wrong(): pass")
    new = tmp_path / "supplement/solution.py"
    add_candidate(payload, new)
    assert check_current_submission(payload)["submission"]["path"] == str(new)
    add_candidate(payload, tmp_path / "second/solution.py")
    assert check_current_submission(payload)["code"] == "registered_submission_ambiguous"
    new.write_bytes(SCRIPT + b"# changed\n")
    assert check_current_submission(payload)["submission"]["path"] == str(tmp_path / "second/solution.py")


@pytest.mark.parametrize("change", [
    {"filename": "subpackage/solution.py"},
    {"filename": "answer.json"},
    {"callable": {"name": "class", "parameters": []}},
    {"callable": {"name": "solve", "parameters": ["x", "x"]}},
    {"allowed_imports": [".numpy"]},
    {"allowed_imports": ["numpy", "numpy"]},
    {"execute": "python solution.py"},
])
def test_python_contract_validates_declaration_before_any_source(tmp_path, change):
    with pytest.raises(ValueError):
        make_contract(tmp_path, {**DECLARATION, **change})


def test_workflow_passes_structurally_valid_python_to_existing_goal_review(tmp_path):
    from tests.test_public_submission_gate_20261119 import AuthorBackend, make_spec
    from theory_master.core import TaskSpec, Workflow

    spec = make_spec(tmp_path / "registered")
    make_contract(spec.benchmark_root)
    spec = TaskSpec.from_config(spec.config_path)

    class PythonAuthor(AuthorBackend):
        def invoke(self, role, prompt, thread_id, model, provider):
            if role == "investigator":
                (Path(self.cwd) / "solution.py").write_bytes(SCRIPT)
            return super().invoke(role, prompt, thread_id, model, provider)

    backend = PythonAuthor(initial_valid=True)
    state = Workflow(spec, backend).run()
    assert backend.goal_calls == 1
    assert state["goal_submission_validation"]["verification"] == "python_ast_structure"
    assert state["goal_submission_validation"]["submission"]["sha256"] == digest(SCRIPT)
    assert state["goal_completion"]["verdict"] == "passed"
