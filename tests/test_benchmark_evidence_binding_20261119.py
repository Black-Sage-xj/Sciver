"""Task-v2 benchmark aliases bind to configured inputs without certifying freeze."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.evidence import EvidenceRegistry
from theory_master.workflow import Workflow


def registry_fixture(tmp_path):
    home = tmp_path / "notebook"
    research = tmp_path / "runtime" / "research"
    benchmark = tmp_path / "runtime" / "benchmark"
    cycle = research / "1"
    workspace = cycle / "workspace"
    for directory in (home, workspace, benchmark):
        directory.mkdir(parents=True)
    declaration = benchmark / "source_manifest.json"
    declaration.write_text('{"schema":"original-fixture-inputs"}\n')
    registry = EvidenceRegistry(cycle, workspace, task_local_root=home, research_root=research,
                                benchmark_root=benchmark, repo_root=tmp_path / "repo")
    return registry, declaration


def register(registry, ref="benchmark/source_manifest.json"):
    registry.register_role_output("investigator", {"evidence": [{
        "summary": "An ordinary labeled-validation comparison under the declared assumptions.",
        "provenance": {"source": "benchmark_provided", "split": "validation", "declaration_ref": ref},
    }]}, epoch="post_lock_test", producer_invocation={
        "invocation_id": "inv-v2-benchmark", "role": "investigator", "stage": "investigate"})
    return json.loads(registry.records_path.read_text().splitlines()[0])


def test_relocated_benchmark_declaration_registers_without_strengthening_identity(tmp_path):
    registry, declaration = registry_fixture(tmp_path)
    # The original relative input spelling no longer sits under the notebook,
    # research root or workspace after Task-v2 splits benchmark and research.
    row = register(registry)
    provenance = row["provenance"]
    assert provenance["declaration_ref"] == str(declaration.resolve())
    assert provenance["declaration_sha256"] == hashlib.sha256(declaration.read_bytes()).hexdigest()
    assert provenance["split"] == "validation"
    assert provenance["freeze_status"] == "unverified"
    assert provenance["protected_identity_verified"] is False
    assert "frozen_before_current_cycle" not in provenance


def test_workflow_passes_the_configured_benchmark_root(tmp_path):
    registry, declaration = registry_fixture(tmp_path)
    owner = Workflow.__new__(Workflow)
    owner.spec = SimpleNamespace(
        task_home=tmp_path / "notebook", document=tmp_path / "notebook" / "research_plan.md",
        branch_canvas=tmp_path / "notebook" / "exploration.canvas", research_root=registry.cycle_dir.parent,
        benchmark_root=declaration.parent, legacy_research_root=None)
    owner.cycle_dir, owner.cycle_workspace, owner.notes_dir = registry.cycle_dir, registry.workspace, None
    bound = owner._evidence_registry()
    assert bound._provenance_declaration_matches("benchmark/source_manifest.json") == [str(declaration)]


def test_explicit_alias_is_not_rebound_to_workspace_or_repo_shadow(tmp_path):
    registry, declaration = registry_fixture(tmp_path)
    for shadow in (registry.workspace / "benchmark" / declaration.name,
                   tmp_path / "repo" / "benchmark" / declaration.name):
        shadow.parent.mkdir(parents=True)
        shadow.write_text("unrelated post-hoc shadow")
    assert register(registry)["provenance"]["declaration_ref"] == str(declaration)
    canonical, rejected, _ = registry._canonical_artifact_pointers(["benchmark/source_manifest.json"])
    assert canonical == [str(declaration)] and rejected == []


def test_provenance_alias_requires_an_explicit_benchmark_binding(tmp_path):
    registry, declaration = registry_fixture(tmp_path)
    unbound = EvidenceRegistry(registry.cycle_dir, registry.workspace, repo_root=declaration.parent.parent)
    assert (declaration.parent.parent / "benchmark" / declaration.name).is_file()
    with pytest.raises(ValueError, match="exactly one local artifact"):
        register(unbound)


@pytest.mark.parametrize("ref", ["benchmark/../outside.json", "benchmark/missing.json", "benchmark"])
def test_invalid_alias_cannot_fall_back_to_a_parent_repo_or_workspace_basename(tmp_path, ref):
    registry, _ = registry_fixture(tmp_path)
    outside = tmp_path / "runtime" / "outside.json"
    outside.write_text("outside the bound benchmark root")
    (registry.workspace / "missing.json").write_text("unique basename recovery is forbidden for this alias")
    with pytest.raises(ValueError, match="exactly one local artifact"):
        register(registry, ref)
    canonical, rejected, _ = registry._canonical_artifact_pointers([ref])
    assert canonical == [] and len(rejected) == 1


@pytest.mark.parametrize("directory", [False, True])
def test_benchmark_alias_rejects_symlink_files_and_directories(tmp_path, directory):
    registry, declaration = registry_fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / declaration.name).write_text("outside declaration")
    if directory:
        (declaration.parent / "linked").symlink_to(outside, target_is_directory=True)
        ref = "benchmark/linked/source_manifest.json"
    else:
        declaration.unlink()
        declaration.symlink_to(outside / declaration.name)
        ref = "benchmark/source_manifest.json"
    with pytest.raises(ValueError, match="exactly one local artifact"):
        register(registry, ref)


def test_bound_declaration_change_still_invalidates_evidence_integrity(tmp_path):
    registry, declaration = registry_fixture(tmp_path)
    register(registry)
    declaration.write_text("changed after registration")
    registry.rebuild()
    row = json.loads(registry.manifest_path.read_text())["records"][0]
    assert row["adjudicable"] is False
    assert str(declaration) in row["integrity_current"]["changed_paths"]


def test_unbound_ordinary_alias_cannot_use_repo_or_workspace_basename_shadow(tmp_path):
    registry, declaration = registry_fixture(tmp_path)
    (registry.workspace / declaration.name).write_text("workspace shadow")
    unbound = EvidenceRegistry(registry.cycle_dir, registry.workspace, repo_root=declaration.parent.parent)
    canonical, rejected, mapping = unbound._canonical_artifact_pointers(["benchmark/source_manifest.json"])
    assert canonical == [] and len(rejected) == 1 and mapping == {}


@pytest.mark.parametrize("ancestor", [False, True])
def test_benchmark_root_or_ancestor_symlink_is_not_erased_by_canonicalization(tmp_path, ancestor):
    registry, declaration = registry_fixture(tmp_path)
    alias = tmp_path / "linked"
    alias.symlink_to(declaration.parent.parent if ancestor else declaration.parent, target_is_directory=True)
    configured = alias / "benchmark" if ancestor else alias
    linked = EvidenceRegistry(registry.cycle_dir, registry.workspace, benchmark_root=configured)
    with pytest.raises(ValueError, match="exactly one local artifact"):
        register(linked)
    canonical, rejected, _ = linked._canonical_artifact_pointers(["benchmark/source_manifest.json"])
    assert canonical == [] and len(rejected) == 1
