"""User catalogs extend official fixtures without importing any research history."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from theory_master.regression_cases import (
    RegressionCaseError,
    create_test_instance,
    list_cases,
    materialize_case,
    resolve_case,
    verify_case,
)
from theory_master.scientific_model import state_path


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def catalog(tmp_path, *, ident="test_7", aliases=None, manifest="source_manifest.json"):
    root = tmp_path / "catalog"
    case = root / "private-case"
    data = case / "benchmark" / "inputs" / "observations.csv"
    data.parent.mkdir(parents=True)
    data.write_text("x,y\n1,2\n")
    (case / "research_plan.md").write_text(
        "# Goal\nSynthetic fixture.\n\n# Research Contract\nPublic input only.\n\n"
        "# Final Gate\nReproducible result.\n\n# Required Context\n"
        "- benchmark/inputs/observations.csv: observations.\n"
    )
    meta = {
        "schema": "theory-master/regression-case/v1",
        "id": ident,
        "aliases": aliases if aliases is not None else ["Test7", "synthetic-private"],
        "scientific_fixture": {"source_manifest": "benchmark/" + manifest},
    }
    write_json(case / "case.json", meta)
    write_json(
        case / "benchmark" / manifest,
        {
            "files": [
                {
                    "path": "benchmark/inputs/observations.csv",
                    "size": data.stat().st_size,
                    "sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
                }
            ]
        },
    )
    write_json(
        root / "index.json",
        {
            "schema": "theory-master/regression-case-index/v1",
            "cases": [{"id": ident, "path": "private-case"}],
        },
    )
    (case / "expected.json").write_text("synthetic grader-only metadata")
    (case / "evaluator").mkdir()
    (case / "evaluator" / "grade.py").write_text(
        "raise AssertionError('not a research input')"
    )
    return root, case


@pytest.fixture(autouse=True)
def no_user_catalog(monkeypatch):
    monkeypatch.delenv("SCIVER_TEST_CASES_ROOT", raising=False)


def test_user_catalog_merges_and_preserves_official_test0(tmp_path, monkeypatch):
    official = resolve_case("Test0").root
    root, case = catalog(tmp_path)
    monkeypatch.setenv("SCIVER_TEST_CASES_ROOT", str(root))
    assert [row.case_id for row in list_cases()] == ["test_0", "test_7"]
    assert resolve_case("TEST_7").root == case
    assert resolve_case("synthetic-private").root == case
    assert resolve_case("Test0").root == official
    assert verify_case("Test7")["files_verified"] == 1
    copied = materialize_case("Test7", tmp_path / "new-instance")
    assert (copied / "benchmark/inputs/observations.csv").read_bytes() == (
        case / "benchmark/inputs/observations.csv"
    ).read_bytes()
    assert not any(
        (copied / name).exists() for name in ("case.json", "expected.json", "evaluator")
    )
    assert (case / "research_plan.md").read_bytes() == (
        copied / "research_plan.md"
    ).read_bytes()


def test_custom_fixture_manifest_preserves_original_provenance(tmp_path, monkeypatch):
    root, case = catalog(tmp_path, manifest="fixture_manifest.json")
    provenance = b'{"schema":"original-public-provenance","origin":"synthetic"}\n'
    (case / "benchmark/source_manifest.json").write_bytes(provenance)
    monkeypatch.setenv("SCIVER_TEST_CASES_ROOT", str(root))
    assert verify_case("Test7")["files_verified"] == 1
    copied = materialize_case("Test7", tmp_path / "instance")
    assert (copied / "benchmark/source_manifest.json").read_bytes() == provenance
    assert (copied / "benchmark/fixture_manifest.json").read_bytes() == (
        case / "benchmark/fixture_manifest.json"
    ).read_bytes()


def test_show_user_case_without_private_baseline(tmp_path, monkeypatch, capsys):
    from theory_master.regression_cases import main

    root, case = catalog(tmp_path)
    (case / "expected.json").unlink()
    monkeypatch.setenv("SCIVER_TEST_CASES_ROOT", str(root))
    assert main(["show", "Test7"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["id"] == "test_7" and shown["baseline"] is None


def test_user_instances_have_independent_empty_science_without_launch(
    tmp_path, monkeypatch
):
    root, case = catalog(tmp_path)
    monkeypatch.setenv("SCIVER_TEST_CASES_ROOT", str(root))
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("THEORY_MASTER_SEARCH_ROOT", str(tmp_path / "instances"))
    monkeypatch.setattr(
        "theory_master.runtime_manager.launch_managed_runtime",
        lambda *a, **k: pytest.fail("offline create must not launch"),
    )
    monkeypatch.setattr(
        "theory_master.codex_backend.CodexBackend.__init__",
        lambda *a, **k: pytest.fail("offline create must not load a model"),
    )
    before = (case / "research_plan.md").read_bytes()
    first, one = create_test_instance(
        "Test7", instances_root=tmp_path / "instances", start=False
    )
    second, two = create_test_instance(
        "Test7", instances_root=tmp_path / "instances", start=False
    )
    assert one["status"] == two["status"] == "prepared"
    assert first.master_id != second.master_id
    assert (
        first.task_home != second.task_home
        and first.research_root != second.research_root
    )
    assert (case / "research_plan.md").read_bytes() == before
    for binding in (first, second):
        assert binding.config.get("state_seed") is None
        assert not any(path.name.isdigit() for path in binding.research_root.iterdir())
        with sqlite3.connect(binding.config["graph_database"]) as database:
            for table in ("hypotheses", "outcomes", "goal_gate_assessments"):
                assert (
                    database.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
                )
        model = json.loads(state_path(binding.research_root).read_text())
        assert model["version"] == 0 and model["updated_from_cycle"] is None
        assert (binding.benchmark_root / "inputs/observations.csv").is_file()
        assert not (binding.task_home / "expected.json").exists()


@pytest.mark.parametrize(
    "ident,aliases",
    [
        ("test_0", []),
        ("other", ["Brownian-2D"]),
        ("Test0", []),
    ],
)
def test_user_catalog_cannot_replace_official_case_or_alias(
    tmp_path, monkeypatch, ident, aliases
):
    root, _ = catalog(tmp_path, ident=ident, aliases=aliases)
    monkeypatch.setenv("SCIVER_TEST_CASES_ROOT", str(root))
    with pytest.raises(RegressionCaseError, match="duplicate"):
        resolve_case("Test0")


def test_duplicate_user_alias_is_rejected(tmp_path, monkeypatch):
    root, case = catalog(tmp_path)
    other = root / "other"
    other.mkdir()
    write_json(
        other / "case.json",
        {
            "schema": "theory-master/regression-case/v1",
            "id": "other",
            "aliases": ["synthetic_private"],
        },
    )
    index = json.loads((root / "index.json").read_text())
    index["cases"].append({"id": "other", "path": "other"})
    write_json(root / "index.json", index)
    monkeypatch.setenv("SCIVER_TEST_CASES_ROOT", str(root))
    with pytest.raises(RegressionCaseError, match="duplicate"):
        list_cases()


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside-case"])
def test_case_path_cannot_escape_catalog(tmp_path, monkeypatch, path):
    root, _ = catalog(tmp_path)
    index = json.loads((root / "index.json").read_text())
    index["cases"][0]["path"] = path
    write_json(root / "index.json", index)
    monkeypatch.setenv("SCIVER_TEST_CASES_ROOT", str(root))
    with pytest.raises(RegressionCaseError, match="unsafe fixture path"):
        list_cases()


@pytest.mark.parametrize(
    "path", ["../outside.csv", "/tmp/outside.csv", "benchmark/../../outside.csv"]
)
def test_manifest_path_cannot_read_outside_fixture(tmp_path, monkeypatch, path):
    root, case = catalog(tmp_path)
    manifest = json.loads((case / "benchmark/source_manifest.json").read_text())
    manifest["files"][0]["path"] = path
    write_json(case / "benchmark/source_manifest.json", manifest)
    monkeypatch.setenv("SCIVER_TEST_CASES_ROOT", str(root))
    with pytest.raises(RegressionCaseError, match="unsafe fixture path"):
        verify_case("Test7")


@pytest.mark.parametrize(
    "target", ["catalog", "case", "input", "extra", "manifest", "plan"]
)
def test_symlink_inputs_and_catalog_paths_are_rejected(tmp_path, monkeypatch, target):
    root, case = catalog(tmp_path)
    if target == "catalog":
        link = tmp_path / "catalog-link"
        link.symlink_to(root, target_is_directory=True)
        root = link
    elif target == "case":
        link = root / "case-link"
        link.symlink_to(case, target_is_directory=True)
        index = json.loads((root / "index.json").read_text())
        index["cases"][0]["path"] = "case-link"
        write_json(root / "index.json", index)
    else:
        name = {
            "input": "benchmark/inputs/observations.csv",
            "manifest": "benchmark/source_manifest.json",
            "plan": "research_plan.md",
            "extra": "benchmark/extra.txt",
        }[target]
        path = case / name
        actual = tmp_path / "linked-content"
        actual.write_bytes(path.read_bytes() if path.exists() else b"not an input")
        path.unlink(missing_ok=True)
        path.symlink_to(actual)
    monkeypatch.setenv("SCIVER_TEST_CASES_ROOT", str(root))
    with pytest.raises(RegressionCaseError, match="symlink"):
        materialize_case("Test7", tmp_path / "new-instance")


def test_manifest_selection_and_destination_are_confined(tmp_path, monkeypatch):
    root, case = catalog(tmp_path)
    monkeypatch.setenv("SCIVER_TEST_CASES_ROOT", str(root))
    meta = json.loads((case / "case.json").read_text())
    meta["scientific_fixture"]["source_manifest"] = "../private.json"
    write_json(case / "case.json", meta)
    with pytest.raises(RegressionCaseError, match="unsafe fixture path"):
        verify_case("Test7")
    meta["scientific_fixture"]["source_manifest"] = "benchmark/source_manifest.json"
    write_json(case / "case.json", meta)
    with pytest.raises(RegressionCaseError, match="inside the fixture"):
        materialize_case("Test7", case / "nested-output")


def test_relative_catalog_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv("SCIVER_TEST_CASES_ROOT", "relative/private-catalog")
    with pytest.raises(RegressionCaseError, match="absolute path"):
        list_cases()
