"""Published deliverables are exact frozen files, never a workspace read grant."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.delivery_publish import MANIFEST_SCHEMA, publish_delivery, read_publications
from theory_master.delivery_repair import _canonical
from theory_master.goal_materials import goal_material_sources, material_roots, resolve_material
from theory_master.goal_snapshot import GoalSnapshotSeed, materialize_goal_snapshot


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _published(tmp_path):
    research = tmp_path / "private-runtime" / "research"
    notes = tmp_path / "notes"
    home = tmp_path / "task-home"
    for directory in (research, notes, home):
        directory.mkdir(parents=True)
    plan = notes / "research_plan.md"
    plan.write_text("# Final Gate\nRead the delivered coverage note.\n")
    source = research / "coverage.md"
    source.write_text("Documentation of the supplied coverage limit.\n")
    spec = SimpleNamespace(
        research_root=research, document=plan, task_home=home,
        branch_canvas=notes / "exploration.canvas", task_mode=True,
        required_context="", graph_database=research / "science_graph.sqlite3",
    )
    manifest = {
        "schema": MANIFEST_SCHEMA, "research_root": str(research),
        "plan_sha256": _hash(plan),
        "destinations": {name: str(home / name) for name in ("analysis", "result")},
        "files": [{
            "source": {"kind": "supplemental_document", "path": str(source),
                       "sha256": _hash(source),
                       "references": [{"path": str(plan), "sha256": _hash(plan)}]},
            "target": "result/coverage.md",
        }],
    }
    receipt = publish_delivery(spec, manifest, dry_run=False)
    return spec, source, home / "result" / "coverage.md", receipt


def test_verified_publication_allows_only_the_exact_file(tmp_path):
    spec, _, target, _ = _published(tmp_path)
    sibling = target.with_name("unregistered.md")
    sibling.write_text("Must remain outside the Goal input boundary.")
    assert spec.task_home not in material_roots(spec)
    assert resolve_material(spec, str(target))["sha256"] == _hash(target)
    for path in (sibling, target.parent, spec.task_home):
        assert resolve_material(spec, str(path))["status"] == "outside_registered_roots"
    alias = spec.task_home / "alias.md"
    alias.symlink_to(target)
    assert resolve_material(spec, str(alias))["status"] == "symlink"


@pytest.mark.parametrize("changed", ["target", "source", "reference", "receipt"])
def test_any_publication_identity_drift_revokes_outside_file_access(tmp_path, changed):
    spec, source, target, receipt = _published(tmp_path)
    path = {"target": target, "source": source, "reference": spec.document,
            "receipt": Path(receipt["receipt_path"])}[changed]
    path.write_bytes(path.read_bytes() + b"\nchanged\n")
    assert read_publications(spec)[0]["status"] == "drifted"
    assert resolve_material(spec, str(target))["status"] == "outside_registered_roots"


def test_goal_snapshot_freezes_published_file_but_excludes_its_sibling(tmp_path):
    spec, _, target, receipt = _published(tmp_path)
    sibling = target.with_name("unregistered.md")
    sibling.write_text("Not registered")
    payload = {
        "material_index": {}, "delivery_publications": read_publications(spec),
        "other": {"path": str(sibling)},
    }
    payload["goal_material_sources"] = goal_material_sources(spec, [payload])
    snapshot = materialize_goal_snapshot(
        spec, spec.research_root / "1",
        GoalSnapshotSeed(spec.document.read_bytes(), _canonical({}), _canonical({}),
                         _canonical(payload), _canonical({}), _canonical({})),
    )
    frozen = json.loads((snapshot.root / "delivery_revision.json").read_text())
    publication = frozen["delivery_publications"][0]
    delivered = Path(publication["files"][0]["path"])
    assert delivered.is_relative_to(snapshot.root)
    assert delivered.read_bytes() == target.read_bytes()
    assert not Path(frozen["other"]["path"]).exists()
    assert publication["authority"] == "delivery_only"
    assert publication["scientific_evidence"] is False
    snapshot.validate()
    target.write_text("Changed after freezing")
    assert delivered.read_text() == "Documentation of the supplied coverage limit.\n"
    with pytest.raises(ValueError, match="source changed"):
        snapshot.validate()
