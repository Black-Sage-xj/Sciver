"""Preparation and Task creation use real local persistence, never models or Workers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import stat
from pathlib import Path

import pytest

from theory_master.science_graph_store import ScienceGraphStore
from theory_master.scientific_model import load
from tm_observer import research_preparation as prep


IDENT = "a" * 32
PLAN = ("# Goal\n\nExplain the observed scaling.\n\n"
        "# Research Contract\n\nCompare the two specified mechanisms using independent samples.\n\n"
        "# Final Gate\n\nResolve the signed prediction comparison with uncertainty.\n\n"
        "# Required Context\n\nUse the supplied measurement definitions and source notes.\n")


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "data"))
    state = tmp_path / "state"
    search = tmp_path / "workspace"
    search.mkdir()
    return state, search


def ready(project, *, ident=IDENT):
    state, search = project
    initial = prep.initialize(state, ident, "clear", "Scaling study")
    return prep.update(state, ident, {"expected_revision": initial["revision"], "draft": PLAN,
                                      "intent": "Identify which mechanism explains the observations."})


def confirm(current):
    return {"confirm": True, "expected_revision": current["revision"]}


def test_empty_preparation_needs_no_task_and_tracks_real_draft_changes(project):
    state, search = project
    initial = prep.initialize(state, IDENT, "exploratory", "An unexplained phenomenon")
    assert initial["draft"] == "" and initial["created_task"] is None
    assert initial["draft_revision"] == hashlib.sha256(b"").hexdigest()
    assert initial["creation_status"] == "preparing" and not initial["execution_started"]
    assert initial["plan_ready"] is False
    assert not (search / "projects").exists()
    workspace = prep.workspace(state, IDENT)
    assert workspace.parent.name == "research-preparations"
    (workspace / "research_plan.md").write_text("A conversation draft, still incomplete.")
    current = prep.snapshot(state, IDENT)
    assert current["draft"].startswith("A conversation draft") and current["revision"] != initial["revision"]
    assert current["plan_ready"] is False
    with pytest.raises(prep.PreparationError) as stale:
        prep.update(state, IDENT, {"expected_revision": initial["revision"], "draft": "stale"})
    assert stale.value.status == 409
    assert prep.initialize(state, IDENT, "exploratory")["draft"] == current["draft"]


@pytest.mark.parametrize("draft", ["", "Only an interest", PLAN.replace("Resolve the signed prediction comparison with uncertainty.", ""),
                                    PLAN + "\n# Scientific Model\n\nAn alleged prior conclusion.\n"])
def test_only_complete_four_section_draft_can_create(project, draft):
    state, search = project
    initial = prep.initialize(state, IDENT, "clear")
    current = prep.update(state, IDENT, {"expected_revision": initial["revision"], "draft": draft})
    assert current["plan_ready"] is False
    with pytest.raises(prep.PreparationError) as invalid:
        prep.create_project(state, search, IDENT, confirm(current))
    assert invalid.value.status == 422
    assert not (search / "projects").exists()


def test_creation_requires_explicit_confirmation_and_reviewed_revision(project):
    state, search = project
    current = ready(project)
    assert current["plan_ready"] is True
    for payload in ({"expected_revision": current["revision"]}, {"confirm": 1, "expected_revision": current["revision"]},
                    {"confirm": True, "expected_revision": "0" * 64}):
        with pytest.raises(prep.PreparationError):
            prep.create_project(state, search, IDENT, payload)
    assert not (search / "projects").exists()


@pytest.mark.parametrize("field,value", [("title", "Revised project title"), ("intent", "A different research intention")])
def test_metadata_only_edit_invalidates_stale_update_and_creation_review(project, field, value):
    state, search = project
    before = ready(project)
    updated = prep.update(state, IDENT, {"expected_revision": before["revision"], field: value})
    assert updated["draft_revision"] == before["draft_revision"]
    assert updated["revision"] != before["revision"]
    with pytest.raises(prep.PreparationError, match="已变化") as stale_save:
        prep.update(state, IDENT, {"expected_revision": before["revision"], "intent": "An obsolete edit"})
    assert stale_save.value.status == 409
    with pytest.raises(prep.PreparationError, match="已变化"):
        prep.create_project(state, search, IDENT, confirm(before))
    assert not (search / "projects").exists()


def test_creates_fresh_task_with_empty_science_and_immutable_material_snapshots(project):
    state, search = project
    current = ready(project)
    workspace = prep.workspace(state, IDENT)
    (workspace / "literature" / "search-1").mkdir(parents=True)
    source = workspace / "literature" / "search-1" / "metadata.json"
    source.write_text('{"source_access":"metadata","title":"A lead, not verified evidence"}')
    current = prep.snapshot(state, IDENT)
    conversation = [{"id": "message", "role": "user", "content": "A discussed question", "sources": []}]
    receipt = prep.create_project(state, search, IDENT, confirm(current), conversation=conversation)
    assert receipt["status"] == "created" and not receipt["execution_started"]
    assert receipt["scientific_model_empty"] and receipt["graph_empty"]
    assert receipt["observer_id"] == hashlib.sha256(receipt["research_root"].encode()).hexdigest()[:16]
    assert receipt["task_href"] == "/run/" + receipt["observer_id"] + "/"
    assert receipt["task_name"] == "Scaling study"
    assert json.loads((Path(receipt["task_root"]) / "theory-master.config.json").read_text())["task_name"] == "Scaling study"
    root = Path(receipt["research_root"])
    assert load(root)["version"] == 0 and load(root)["summary"] == ""
    with ScienceGraphStore(root / "science_graph.sqlite3", readonly=True) as store:
        assert store.db.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 0
        assert store.db.execute("SELECT COUNT(*) FROM external_records").fetchone()[0] == 0
    assert not any(path.is_dir() and path.name.isdigit() for path in root.iterdir())
    snapshot = Path(receipt["snapshot_path"])
    assert (snapshot / "research_plan.md").read_text() == PLAN
    assert json.loads((snapshot / "conversation.json").read_text()) == conversation
    saved_material = snapshot / "materials/literature/search-1/metadata.json"
    assert saved_material.read_bytes() == source.read_bytes()
    assert not stat.S_IMODE(saved_material.stat().st_mode) & 0o222
    source.write_text("Later discussion changed this source")
    assert "A lead" in saved_material.read_text()
    assert prep.snapshot(state, IDENT)["created_task"]["observer_id"] == receipt["observer_id"]
    with pytest.raises(prep.PreparationError):
        prep.update(state, IDENT, {"expected_revision": current["revision"], "draft": "do not overwrite"})


def test_duplicate_and_concurrent_creation_return_one_task(project):
    state, search = project
    current = ready(project)
    def create_or_retry(_):
        try:
            return prep.create_project(state, search, IDENT, confirm(current))
        except prep.PreparationError as exc:
            assert exc.status == 409
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create_or_retry, range(2)))
    receipts = [row or prep.create_project(state, search, IDENT, confirm(current)) for row in results]
    assert receipts[0]["task_root"] == receipts[1]["task_root"]
    assert sum(row["idempotent"] for row in receipts) == 1
    assert len(list((search / "projects").iterdir())) == 1
    assert prep.create_project(state, search, IDENT, confirm(current))["idempotent"] is True


def test_blank_preparation_title_uses_actual_goal_for_visible_task_name(project):
    state, search = project
    initial = prep.initialize(state, IDENT, "clear")
    current = prep.update(state, IDENT, {"expected_revision": initial["revision"], "draft": PLAN})
    receipt = prep.create_project(state, search, IDENT, confirm(current))
    assert receipt["task_name"] == "Explain the observed scaling."
    config = json.loads((Path(receipt["task_root"]) / "theory-master.config.json").read_text())
    assert config["task_name"] == receipt["task_name"]
    assert prep.create_project(state, search, IDENT, confirm(current))["task_name"] == receipt["task_name"]


def test_partial_bootstrap_retries_same_target_and_keeps_task_identity(project, monkeypatch):
    import theory_master.embedded_state as embedded

    state, search = project
    current = ready(project)
    original = embedded.initialize_model
    def fail(*args, **kwargs):
        raise OSError("synthetic interruption after config and graph allocation")
    monkeypatch.setattr(embedded, "initialize_model", fail)
    with pytest.raises(prep.PreparationError, match="原目标与审阅快照已保留"):
        prep.create_project(state, search, IDENT, confirm(current))
    target = search / "projects" / ("project-" + IDENT)
    allocated = json.loads((target / "theory-master.config.json").read_text())
    assert prep.snapshot(state, IDENT)["creation_status"] == "creating"
    monkeypatch.setattr(embedded, "initialize_model", original)
    receipt = prep.create_project(state, search, IDENT, confirm(current))
    assert receipt["task_id"] == allocated["task_id"]
    assert receipt["research_root"] == allocated["research_root"]
    assert json.loads((target / "theory-master.config.json").read_text())["task_name"] == "Scaling study"
    assert len(list((search / "projects").iterdir())) == 1


def test_failure_after_bootstrap_and_before_receipt_is_idempotently_recovered(project, monkeypatch):
    import theory_master.task as tasks

    state, search = project
    current = ready(project)
    original = tasks.bootstrap
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("synthetic lost completion")
    monkeypatch.setattr(tasks, "bootstrap", interrupted)
    with pytest.raises(prep.PreparationError):
        prep.create_project(state, search, IDENT, confirm(current))
    monkeypatch.setattr(tasks, "bootstrap", original)
    receipt = prep.create_project(state, search, IDENT, confirm(current))
    assert receipt["status"] == "created"
    assert len(list((search / "projects").iterdir())) == 1


def test_snapshot_failure_does_not_freeze_a_broken_creation(project, monkeypatch):
    state, search = project
    current = ready(project)
    original = prep._publish_snapshot
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("synthetic interruption before snapshot completion")
    monkeypatch.setattr(prep, "_publish_snapshot", interrupted)
    with pytest.raises(prep.PreparationError, match="快照尚未完成"):
        prep.create_project(state, search, IDENT, confirm(current))
    assert prep.snapshot(state, IDENT)["creation"] is None
    assert not (search / "projects" / ("project-" + IDENT)).exists()
    assert not list((search / "projects").iterdir())
    monkeypatch.setattr(prep, "_publish_snapshot", original)
    receipt = prep.create_project(state, search, IDENT, confirm(current))
    assert receipt["status"] == "created"


@pytest.mark.parametrize("durable", [False, True])
def test_creation_metadata_failure_can_retry_completed_snapshot(project, monkeypatch, durable):
    state, search = project
    current = ready(project)
    original = prep._write_meta
    def interrupted(root, value):
        if value.get("creation") and not value.get("created_task"):
            if durable:
                original(root, value)
            raise OSError("synthetic interruption persisting creation")
        return original(root, value)
    monkeypatch.setattr(prep, "_write_meta", interrupted)
    with pytest.raises(prep.PreparationError, match="完整筹备快照已保留"):
        prep.create_project(state, search, IDENT, confirm(current))
    assert bool(prep.snapshot(state, IDENT)["creation"]) is durable
    assert not (search / "projects" / ("project-" + IDENT)).exists()
    stages = list((search / "projects").glob(".prep-stage-*"))
    assert len(stages) == 1
    assert (stages[0] / "preparation_snapshot" / "manifest.json").is_file()
    assert not list((search / "projects").glob("*/theory-master.config.json"))
    monkeypatch.setattr(prep, "_write_meta", original)
    receipt = prep.create_project(state, search, IDENT, confirm(current))
    assert receipt["status"] == "created"
    assert len([path for path in (search / "projects").iterdir() if not path.name.startswith(".")]) == 1
    if durable:
        assert not stages[0].exists()


def test_rename_failure_retries_same_frozen_stage_and_reviewed_metadata(project, monkeypatch):
    state, search = project
    current = ready(project)
    original = prep._publish_staging
    def interrupted(*args, **kwargs):
        raise OSError("synthetic interruption publishing completed snapshot")
    monkeypatch.setattr(prep, "_publish_staging", interrupted)
    with pytest.raises(prep.PreparationError, match="原目标与审阅快照已保留"):
        prep.create_project(state, search, IDENT, confirm(current))
    recorded = prep.snapshot(state, IDENT)
    creation = recorded["creation"]
    stage = Path(creation["staging_root"])
    assert stage.is_dir() and not Path(creation["target_root"]).exists()
    assert creation["reviewed_revision"] == current["revision"]
    assert creation["snapshot_sha256"] == hashlib.sha256((stage / "preparation_snapshot/manifest.json").read_bytes()).hexdigest()
    # Recovery uses the confirmed snapshot rather than mutable preparation files.
    (prep.workspace(state, IDENT) / "research_plan.md").write_text("Later unsaved discussion")
    metadata_path = prep.workspace(state, IDENT) / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["title"] = "A later unreviewed title"
    metadata_path.write_text(json.dumps(metadata))
    monkeypatch.setattr(prep, "_publish_staging", original)
    receipt = prep.create_project(state, search, IDENT, confirm(current))
    assert receipt["task_name"] == "Scaling study"
    assert not stage.exists()
    assert (Path(receipt["snapshot_path"]) / "research_plan.md").read_text() == PLAN
    assert prep.create_project(state, search, IDENT, confirm(current))["task_id"] == receipt["task_id"]


def test_publication_refuses_even_an_empty_directory_created_during_rename(project, monkeypatch):
    state, search = project
    current = ready(project)
    original = prep._publish_staging
    def occupied(staging, target):
        target.mkdir()
        return original(staging, target)
    monkeypatch.setattr(prep, "_publish_staging", occupied)
    with pytest.raises(prep.PreparationError, match="已被占用"):
        prep.create_project(state, search, IDENT, confirm(current))
    creation = prep.snapshot(state, IDENT)["creation"]
    assert list(Path(creation["target_root"]).iterdir()) == []
    assert Path(creation["staging_root"]).is_dir()


def test_lost_creation_receipt_recovers_published_task_without_second_allocation(project, monkeypatch):
    state, search = project
    current = ready(project)
    original = prep._write_meta
    def interrupted(root, value):
        if value.get("created_task"):
            raise OSError("synthetic lost receipt write")
        return original(root, value)
    monkeypatch.setattr(prep, "_write_meta", interrupted)
    with pytest.raises(prep.PreparationError, match="回执尚未保存"):
        prep.create_project(state, search, IDENT, confirm(current))
    creation = prep.snapshot(state, IDENT)["creation"]
    target = Path(creation["target_root"])
    allocated = json.loads((target / "theory-master.config.json").read_text())
    assert not Path(creation["staging_root"]).exists()
    monkeypatch.setattr(prep, "_write_meta", original)
    receipt = prep.create_project(state, search, IDENT, confirm(current))
    assert receipt["task_id"] == allocated["task_id"]
    assert receipt["research_root"] == allocated["research_root"]
    assert len(list((search / "projects").iterdir())) == 1


def test_recovery_checks_frozen_composite_review_version(project, monkeypatch):
    state, search = project
    current = ready(project)
    monkeypatch.setattr(prep, "_publish_staging", lambda *args: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(prep.PreparationError):
        prep.create_project(state, search, IDENT, confirm(current))
    path = prep.workspace(state, IDENT) / "metadata.json"
    metadata = json.loads(path.read_text())
    metadata["creation"]["reviewed_revision"] = "b" * 64
    path.write_text(json.dumps(metadata))
    stage = Path(metadata["creation"]["staging_root"])
    # Also update origin so this test reaches composite snapshot verification.
    origin = {key: value for key, value in metadata["creation"].items() if key != "snapshot_sha256"}
    (stage / "preparation_origin.json").write_text(json.dumps(origin))
    with pytest.raises(prep.PreparationError, match="快照"):
        prep.create_project(state, search, IDENT, {"confirm": True, "expected_revision": "b" * 64})
    assert not Path(metadata["creation"]["target_root"]).exists()


def test_unrelated_existing_project_and_symlink_paths_are_preserved(project):
    state, search = project
    current = ready(project)
    projects = search / "projects"
    projects.mkdir()
    existing = projects / ("project-" + IDENT)
    existing.mkdir()
    sentinel = existing / "keep.txt"
    sentinel.write_text("Unrelated project")
    with pytest.raises(prep.PreparationError, match="已被占用"):
        prep.create_project(state, search, IDENT, confirm(current))
    assert sentinel.read_text() == "Unrelated project"
    assert prep.snapshot(state, IDENT)["creation"] is None
    sentinel.unlink()
    existing.rmdir()
    (prep.workspace(state, IDENT) / "escaped.pdf").symlink_to(search / "private.pdf")
    current = prep.snapshot(state, IDENT)
    with pytest.raises(prep.PreparationError, match="符号链接"):
        prep.create_project(state, search, IDENT, confirm(current))
    assert not existing.exists()


def test_snapshot_tampering_blocks_retry_without_overwriting_task(project, monkeypatch):
    import theory_master.task as tasks

    state, search = project
    current = ready(project)
    original = tasks.bootstrap
    monkeypatch.setattr(tasks, "bootstrap", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(prep.PreparationError):
        prep.create_project(state, search, IDENT, confirm(current))
    target = search / "projects" / ("project-" + IDENT)
    frozen = target / "preparation_snapshot" / "research_plan.md"
    frozen.chmod(0o600)
    frozen.write_text("Tampered draft")
    monkeypatch.setattr(tasks, "bootstrap", original)
    with pytest.raises(prep.PreparationError, match="快照"):
        prep.create_project(state, search, IDENT, confirm(current))
    assert not (target / "theory-master.config.json").exists()


@pytest.mark.parametrize("ident", ["../escape", "A" * 32, "a" * 31, ""])
def test_invalid_preparation_identity_never_creates_directories(project, ident):
    state, search = project
    with pytest.raises(prep.PreparationError):
        prep.initialize(state, ident, "clear")
    assert not state.exists()


def test_snapshot_reads_while_legacy_lock_rejects_mutations(project):
    import threading
    state, _search = project
    current = ready(project)
    root = prep.workspace(state, IDENT)
    started = threading.Event()
    def update_after_lock():
        started.set()
        return prep.update(state, IDENT, {'expected_revision': current['revision'], 'title': 'Next title'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        with prep._locked(root):
            # A held material transaction must not block a user opening Chat.
            read = pool.submit(prep.snapshot, state, IDENT)
            assert read.result(timeout=1)['revision'] == current['revision']
            write = pool.submit(update_after_lock)
            assert started.wait(1)
            with pytest.raises(prep.PreparationError) as busy:
                write.result(timeout=1)
            assert busy.value.status == 409
        assert update_after_lock()['title'] == 'Next title'


def test_snapshot_retries_observed_atomic_publication_changes(project, monkeypatch):
    state, _search = project
    ready(project)
    real = prep._snapshot
    calls = []
    def publish(root, metadata):
        result = real(root, metadata)
        if not calls:
            prep._atomic(root/'research_plan.md', (PLAN + '\nA newly published note.\n').encode())
        calls.append(1)
        return result
    monkeypatch.setattr(prep, '_snapshot', publish)
    result = prep.snapshot(state, IDENT)
    assert len(calls) == 2 and result['draft'].endswith('A newly published note.\n')
