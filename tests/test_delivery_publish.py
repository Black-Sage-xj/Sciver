"""Publication identity, no-overwrite guarantees and interrupted-copy recovery."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from theory_master import delivery_publish as publisher
from theory_master.delivery_publish import publication_spec, publish_delivery, read_publications
from theory_master.delivery_repair import current_delivery_payload, freeze_delivery_revision, science_authority_digest
from theory_master.evidence import EvidenceRegistry
from theory_master.goal_terminal import GoalSealedError
from theory_master.invocation_artifacts import InvocationArtifactTracker
from theory_master.science_graph_store import ScienceGraphStore


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root):
    return {str(path.relative_to(root)): (path.lstat().st_mode, path.read_bytes() if path.is_file() else None)
            for path in root.rglob("*")}


@pytest.fixture
def task(tmp_path):
    root = tmp_path / "private-task" / "research"
    home = tmp_path / "task-home"
    notes = tmp_path / "notes"
    for path in (root, home, notes):
        path.mkdir(parents=True)
    plan = notes / "research_plan.md"
    plan.write_text("# Goal\nFixture deliverable.\n# Final Gate\nExplicit analysis/ and result/.\n")
    spec = SimpleNamespace(research_root=root, task_home=home, document=plan,
                           branch_canvas=notes / "canvas.canvas", benchmark_root=None, task_mode=True,
                           goal_id="fixture", graph_database=root / "science_graph.sqlite3")
    cycle = root / "6"
    workspace = cycle / "workspace"
    workspace.mkdir(parents=True)
    producer = {"invocation_id": "inv-6-investigator", "role": "investigator", "stage": "investigate"}
    tracker = InvocationArtifactTracker.start(cycle_dir=cycle, workspace=workspace, **producer)
    script = workspace / "analysis.py"
    result = workspace / "result.json"
    script.write_text("print('fixture')\n")
    result.write_text('{"fixture_result": true}\n')
    manifest_path = tracker.finalize(status="completed")
    registry = EvidenceRegistry(cycle, workspace, research_root=root)
    registry.register_role_output("investigator", {"evidence": [{"summary": "fixture finding"}]},
                                  epoch="post_lock_test", producer_invocation=producer,
                                  invocation_artifact_manifest=manifest_path)
    manifest = {
        "schema": publisher.MANIFEST_SCHEMA, "research_root": str(root), "plan_sha256": sha(plan),
        "destinations": {"analysis": str(home / "analysis"), "result": str(home / "result")},
        "files": [{"source": {"kind": "registered_material", "cycle_id": "6",
                              "invocation_id": producer["invocation_id"], "path": source.name,
                              "sha256": sha(source)}, "target": target}
                  for source, target in [(script, "analysis/c6/analysis.py"), (result, "result/c6/result.json")]],
    }
    return SimpleNamespace(spec=spec, root=root, home=home, notes=notes, cycle=cycle, registry=registry,
                           workspace=workspace, manifest=manifest, manifest_path=manifest_path,
                           script=script, result=result, tmp=tmp_path)


def test_preview_is_pure_and_apply_is_idempotent_without_changing_authority(task):
    with ScienceGraphStore(task.spec.graph_database):
        pass
    digest = science_authority_digest(task.spec, task.spec.graph_database)
    before = snapshot(task.tmp)
    preview = publish_delivery(task.spec, task.manifest)
    assert preview["status"] == "preview"
    assert all(row["status"] == "pending" for row in preview["files"])
    assert snapshot(task.tmp) == before
    assert read_publications(task.spec) == []
    source_before = snapshot(task.workspace)
    result = publish_delivery(task.spec, task.manifest, dry_run=False)
    receipt = Path(result["receipt_path"])
    receipt_bytes = receipt.read_bytes()
    after = snapshot(task.tmp)
    replay = publish_delivery(task.spec, task.manifest, dry_run=False)
    assert replay == result
    assert snapshot(task.tmp) == after
    assert receipt.read_bytes() == receipt_bytes
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert snapshot(task.workspace) == source_before
    assert science_authority_digest(task.spec, task.spec.graph_database) == digest
    assert result["authority"] == "delivery_only" and result["scientific_evidence"] is False
    rows = read_publications(task.spec)
    assert len(rows) == 1 and rows[0]["status"] == "available"
    assert {row["path"] for row in rows[0]["files"]} == {
        str(task.home / "analysis/c6/analysis.py"), str(task.home / "result/c6/result.json")}
    assert all(sha(Path(row["path"])) == row["sha256"] for row in rows[0]["files"])


def test_known_conflicts_preflight_entire_batch_without_writes(task):
    conflict = task.home / "result/c6/result.json"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("user work")
    before = snapshot(task.tmp)
    with pytest.raises(ValueError, match="conflict"):
        publish_delivery(task.spec, task.manifest, dry_run=False)
    assert snapshot(task.tmp) == before
    assert not (task.home / "analysis").exists()


@pytest.mark.parametrize("change", ["task", "plan", "source_hash", "invocation", "cycle", "relative", "uncommitted", "manifest"])
def test_foreign_uncommitted_or_drifted_identity_is_rejected(task, change):
    manifest = copy.deepcopy(task.manifest)
    if change == "task":
        manifest["research_root"] = str(task.tmp / "other")
    elif change == "plan":
        manifest["plan_sha256"] = "a" * 64
    elif change == "source_hash":
        manifest["files"][0]["source"]["sha256"] = "a" * 64
    elif change == "invocation":
        manifest["files"][0]["source"]["invocation_id"] = "different-invocation"
    elif change == "cycle":
        manifest["files"][0]["source"]["cycle_id"] = "5"
    elif change == "relative":
        manifest["files"][0]["source"]["path"] = "../analysis.py"
    elif change == "uncommitted":
        task.registry.registration_batches_path.unlink()
    else:
        task.manifest_path.write_text("{}")
    before = snapshot(task.tmp)
    with pytest.raises((ValueError, OSError)):
        publish_delivery(task.spec, manifest, dry_run=False)
    assert snapshot(task.tmp) == before


@pytest.mark.parametrize("target", ["../escape", "/tmp/escape", "result", "notes/file.md", "analysis/../escape"])
def test_invalid_targets_rejected(task, target):
    manifest = copy.deepcopy(task.manifest)
    manifest["files"][0]["target"] = target
    with pytest.raises(ValueError):
        publish_delivery(task.spec, manifest)
    assert not (task.home / "analysis").exists()


def test_destinations_must_be_explicit_non_synced_taskhome_roots(task):
    for name, path in [("analysis", task.notes / "analysis"), ("result", task.root / "result"),
                       ("analysis", task.home / "inputs")]:
        manifest = copy.deepcopy(task.manifest)
        manifest["destinations"][name] = str(path)
        with pytest.raises(ValueError, match="destination"):
            publish_delivery(task.spec, manifest)
    task.spec.task_home = task.notes
    manifest = copy.deepcopy(task.manifest)
    manifest["destinations"] = {name: str(task.notes / name) for name in ("analysis", "result")}
    with pytest.raises(ValueError, match="destination"):
        publish_delivery(task.spec, manifest)


def test_duplicate_and_file_directory_overlap_rejected(task):
    for target in ["analysis/c6/analysis.py", "analysis/c6/analysis.py/nested"]:
        manifest = copy.deepcopy(task.manifest)
        manifest["files"][1]["target"] = target
        with pytest.raises(ValueError, match="overlapping"):
            publish_delivery(task.spec, manifest)


def test_taskhome_in_sibling_note_directory_still_cannot_publish_into_sync_vault(task):
    home = task.tmp / "笔记区" / "other-task-home"
    home.mkdir(parents=True)
    task.spec.task_home = home
    manifest = copy.deepcopy(task.manifest)
    manifest["destinations"] = {name: str(home / name) for name in ("analysis", "result")}
    with pytest.raises(ValueError, match="destination"):
        publish_delivery(task.spec, manifest)


@pytest.mark.parametrize("location", ["source", "target_parent", "target", "receipt", "evidence", "lock"])
def test_symlink_paths_cannot_read_or_publish_foreign_files(task, location):
    outside = task.tmp / "outside"
    outside.mkdir()
    foreign = outside / "foreign.txt"
    foreign.write_text("user work")
    if location == "source":
        task.script.unlink()
        task.script.symlink_to(foreign)
    elif location == "target_parent":
        (task.home / "analysis").symlink_to(outside, target_is_directory=True)
    elif location == "target":
        target = task.home / "analysis/c6/analysis.py"
        target.parent.mkdir(parents=True)
        target.symlink_to(foreign)
    elif location == "receipt":
        (task.root / "delivery_publications").symlink_to(outside, target_is_directory=True)
    elif location == "evidence":
        task.registry.records_path.rename(outside / "registry.jsonl")
        task.registry.records_path.symlink_to(outside / "registry.jsonl")
    else:
        (task.root / ".theory-master").symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        publish_delivery(task.spec, task.manifest, dry_run=False)
    assert foreign.read_text() == "user work"
    assert not (outside / "c6").exists()
    assert not (outside / "science_commit.lock").exists()


def test_source_drift_between_preflight_and_copy_is_not_published(task, monkeypatch):
    original = publisher._install_file
    def mutate(source, target, digest):
        source.write_text("changed after preflight")
        original(source, target, digest)
    monkeypatch.setattr(publisher, "_install_file", mutate)
    with pytest.raises(ValueError, match="changed during copy"):
        publish_delivery(task.spec, task.manifest, dry_run=False)
    assert not (task.home / "analysis/c6/analysis.py").exists()
    assert not (task.root / "delivery_publications").exists()


def test_target_race_never_overwrites_user_content(task, monkeypatch):
    original = publisher.os.link
    def race(source, target, **kwargs):
        if target == "analysis.py":
            fd = publisher.os.open(target, publisher.os.O_CREAT | publisher.os.O_WRONLY | publisher.os.O_EXCL,
                                   0o600, dir_fd=kwargs["dst_dir_fd"])
            publisher.os.write(fd, b"concurrent user work")
            publisher.os.close(fd)
        return original(source, target, **kwargs)
    monkeypatch.setattr(publisher.os, "link", race)
    with pytest.raises(ValueError, match="conflict"):
        publish_delivery(task.spec, task.manifest, dry_run=False)
    assert (task.home / "analysis/c6/analysis.py").read_text() == "concurrent user work"
    assert not (task.root / "delivery_publications").exists()


@pytest.mark.parametrize("change", ["delete_target", "replace_parent"])
def test_target_disappearing_after_copy_cannot_commit_a_success_receipt(task, monkeypatch, change):
    original = publisher._install_file
    first = task.home / "analysis/c6/analysis.py"
    def race(source, target, digest):
        original(source, target, digest)
        if target.name == "result.json":
            if change == "delete_target":
                first.unlink()
            else:
                first.parent.rename(first.parent.with_name("moved"))
                first.parent.mkdir()
    monkeypatch.setattr(publisher, "_install_file", race)
    with pytest.raises((ValueError, OSError)):
        publish_delivery(task.spec, task.manifest, dry_run=False)
    assert not (task.root / "delivery_publications").exists()


def test_interrupted_partial_copy_resumes_same_manifest_without_receipt_rewrite(task, monkeypatch):
    original = publisher._install_file
    def interrupt(source, target, digest):
        if target.name == "result.json":
            raise OSError("simulated interruption")
        original(source, target, digest)
    monkeypatch.setattr(publisher, "_install_file", interrupt)
    with pytest.raises(OSError, match="simulated interruption"):
        publish_delivery(task.spec, task.manifest, dry_run=False)
    first = task.home / "analysis/c6/analysis.py"
    first_inode = first.stat().st_ino
    assert not (task.root / "delivery_publications").exists()
    monkeypatch.setattr(publisher, "_install_file", original)
    result = publish_delivery(task.spec, task.manifest, dry_run=False)
    assert first.stat().st_ino == first_inode
    assert Path(result["receipt_path"]).is_file()
    assert read_publications(task.spec)[0]["status"] == "available"


def supplemental(task):
    document = task.home / "field-coverage.md"
    document.write_text("Fixture field coverage; no independent scientific verdict.\n")
    source = task.home / "inputs" / "description.txt"
    source.parent.mkdir()
    source.write_text("Fixture input description.\n")
    manifest = copy.deepcopy(task.manifest)
    manifest["files"] = [{"target": "result/field-coverage.md", "source": {
        "kind": "supplemental_document", "path": str(document), "sha256": sha(document),
        "references": [{"path": str(source), "sha256": sha(source)}]}}]
    return manifest, document, source


def test_supplemental_documents_require_pinned_task_provenance_without_becoming_evidence(task):
    manifest, _, _ = supplemental(task)
    registry = task.registry.records_path.read_bytes()
    result = publish_delivery(task.spec, manifest, dry_run=False)
    row = read_publications(task.spec)[0]
    assert result["authority"] == "delivery_only" and result["scientific_evidence"] is False
    assert row["files"][0]["kind"] == "supplemental_document"
    assert row["files"][0]["references"] == manifest["files"][0]["source"]["references"]
    assert task.registry.records_path.read_bytes() == registry


@pytest.mark.parametrize("change", ["no_references", "foreign", "drift", "self", "directory"])
def test_supplemental_document_provenance_rejections(task, change):
    manifest, document, source = supplemental(task)
    item = manifest["files"][0]["source"]
    if change == "no_references":
        item["references"] = []
    elif change == "foreign":
        foreign = task.tmp / "other-task.md"
        foreign.write_text("foreign")
        item["references"] = [{"path": str(foreign), "sha256": sha(foreign)}]
    elif change == "drift":
        source.write_text("changed")
    elif change == "self":
        item["references"] = [{"path": str(document), "sha256": sha(document)}]
    else:
        item["path"] = str(task.home)
    with pytest.raises((ValueError, OSError)):
        publish_delivery(task.spec, manifest, dry_run=False)
    assert not (task.home / "result").exists()


@pytest.mark.parametrize("position", ["source", "reference"])
def test_benchmark_under_taskhome_requires_exact_required_context_registration(task, position):
    manifest, _, _ = supplemental(task)
    benchmark = task.home / "benchmark"
    benchmark.mkdir()
    task.spec.benchmark_root = benchmark
    public = benchmark / "description.md"
    hidden = benchmark / "answer.md"
    public.write_text("registered description")
    hidden.write_text("unregistered answer")
    task.spec.required_context = "- benchmark/description.md\n"
    item = manifest["files"][0]["source"]
    if position == "source":
        item.update(path=str(hidden), sha256=sha(hidden))
    else:
        item["references"] = [{"path": str(hidden), "sha256": sha(hidden)}]
    with pytest.raises(ValueError, match="Required Context"):
        publish_delivery(task.spec, manifest)
    if position == "source":
        item.update(path=str(public), sha256=sha(public))
    else:
        item["references"] = [{"path": str(public), "sha256": sha(public)}]
    assert publish_delivery(task.spec, manifest)["status"] == "preview"


@pytest.mark.parametrize("change", ["target", "source", "reference", "receipt", "plan"])
def test_reader_never_authorizes_drifted_receipts_or_files(task, change):
    manifest, document, source = supplemental(task)
    result = publish_delivery(task.spec, manifest, dry_run=False)
    path = {"target": Path(result["files"][0]["path"]), "source": document,
            "reference": source, "receipt": Path(result["receipt_path"]), "plan": task.spec.document}[change]
    path.write_text("changed")
    before = snapshot(task.tmp)
    rows = read_publications(task.spec)
    assert len(rows) == 1 and rows[0]["status"] == "drifted" and rows[0]["files"] == []
    assert rows[0]["errors"]
    assert snapshot(task.tmp) == before


def test_sealed_goal_rejects_apply_before_writes(task):
    before = snapshot(task.tmp)
    with patch("theory_master.goal_terminal.goal_sealed", return_value={"source_cycle": "6"}):
        with pytest.raises(GoalSealedError, match="sealed"):
            publish_delivery(task.spec, task.manifest, dry_run=False)
    assert snapshot(task.tmp) == before


def test_publication_is_separate_from_paused_cycle_and_old_scientific_guard(task):
    with ScienceGraphStore(task.spec.graph_database):
        pass
    task.cycle.joinpath("research_state.json").write_text(json.dumps({
        "status": "over", "hypothesis_lock": {"id": "H6", "scope": "original"}}))
    current = task.root / "7"
    current.mkdir()
    current.joinpath("research_state.json").write_text(json.dumps({"status": "paused"}))
    task.cycle.joinpath("delivery_revision.json").write_text(json.dumps({"science_digest": "0" * 64}))
    before = {path: path.read_bytes() for path in [task.cycle / "research_state.json",
              current / "research_state.json", task.cycle / "delivery_revision.json", task.registry.records_path]}
    digest = science_authority_digest(task.spec, task.spec.graph_database)
    publish_delivery(task.spec, task.manifest, dry_run=False)
    assert science_authority_digest(task.spec, task.spec.graph_database) == digest
    assert all(path.read_bytes() == content for path, content in before.items())
    with patch("theory_master.scientific_model.load_from_paths", return_value={}):
        payload = current_delivery_payload(task.spec, task.spec.graph_database, task.cycle)
        assert payload["delivery_publications"][0]["status"] == "available"
        assert payload["science_digest"] == digest
        with pytest.raises(ValueError, match="scientific authority"):
            freeze_delivery_revision(task.spec, task.spec.graph_database, task.cycle, require_unchanged_science=True)
    assert not (task.root / "delivery_revisions").exists()


@pytest.mark.parametrize("apply", [False, True])
def test_cli_never_constructs_backend_workflow_or_routes(task, apply, capsys):
    from theory_master.__main__ import main
    manifest_path = task.tmp / "publication.json"
    manifest_path.write_text(json.dumps(task.manifest))
    argv = ["sciver", "publish-delivery", "fixture", "--manifest", str(manifest_path)]
    if apply:
        argv.append("--apply")
    before = snapshot(task.tmp)
    with patch("sys.argv", argv), patch("theory_master.delivery_publish.publication_spec", return_value=task.spec), \
         patch("theory_master.__main__.spec_from_task", side_effect=AssertionError("mutating task discovery")), \
         patch("theory_master.__main__.CodexBackend", side_effect=AssertionError("backend")), \
         patch("theory_master.__main__.FakeBackend", side_effect=AssertionError("fake backend")), \
         patch("theory_master.__main__.Workflow", side_effect=AssertionError("workflow")), \
         patch("theory_master.__main__.resolve_routes", side_effect=AssertionError("routes")):
        assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is not apply
    if not apply:
        assert snapshot(task.tmp) == before


def test_manifest_task_loader_is_readonly_and_never_repairs_a_missing_pointer(tmp_path):
    from theory_master.task import bootstrap
    plan = tmp_path / "notes" / "research_plan.md"
    plan.parent.mkdir()
    plan.write_text("# Goal\nFixture.\n# Research Contract\nLocal files.\n# Final Gate\nDelivery.\n# Required Context\n- fixture\n")
    binding = bootstrap(plan)
    manifest = {"research_root": str(binding.research_root)}
    before = snapshot(tmp_path)
    spec = publication_spec(manifest, binding.master_id)
    assert spec.research_root == binding.research_root
    assert snapshot(tmp_path) == before
    with pytest.raises(ValueError, match="Task ID/root"):
        publication_spec(manifest, str(binding.master_id) + "9")
    pointer = binding.research_root / ".theory-master/task.json"
    pointer.unlink()
    before = snapshot(tmp_path)
    with pytest.raises(FileNotFoundError):
        publication_spec(manifest, binding.master_id)
    assert snapshot(tmp_path) == before
