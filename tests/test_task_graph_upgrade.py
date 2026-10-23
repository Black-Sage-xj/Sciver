"""Legacy launch upgrades use real SQLite, with no model or runtime requests."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from theory_master.science_graph_store import ScienceGraphStore
from theory_master.task_graph_upgrade import upgrade_task_graph


@pytest.fixture
def legacy(tmp_path):
    canvas = tmp_path / "exploration.canvas"
    canvas.write_text(json.dumps({"nodes": [
        {"id": "H-0001", "type": "text", "text": "id: H-0001\ntype: Hypothesis\n\n# Original claim"},
        {"id": "1-decision", "type": "text", "text": "id: 1-decision\ntype: Decision\ncycle_id: 1\nverdict: supported\n\n# Local result"},
    ], "edges": [{"id": "e", "fromNode": "H-0001", "toNode": "1-decision"}],
        "custom": "preserve this"}))
    return SimpleNamespace(branch_canvas=canvas, research_root=tmp_path / "research", config={})


def test_upgrade_preserves_bytes_and_cycle_verdict_and_replays(legacy):
    raw = legacy.branch_canvas.read_bytes()
    result = upgrade_task_graph(legacy)
    database = Path(result["database"])
    assert result["status"] == "upgraded"
    assert legacy.branch_canvas.read_bytes() == raw
    assert Path(result["backup"]).read_bytes() == raw
    with ScienceGraphStore(database, readonly=True) as store:
        archive = store.db.execute("SELECT original_bytes FROM legacy_canvas_archive").fetchone()[0]
        assert archive == raw
        assert store.cycle_history("H-0001")[0]["verdict"] == "supported"
        assert store.db.execute("SELECT COUNT(*) FROM legacy_conversion_map").fetchone()[0] == 2
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    assert upgrade_task_graph(legacy)["status"] == "existing"
    assert hashlib.sha256(database.read_bytes()).hexdigest() == digest


def test_partial_import_is_never_published(legacy):
    def broken(canvas, database, **kwargs):
        database.write_bytes(b"partial database")
        raise ValueError("ambiguous lineage")
    raw = legacy.branch_canvas.read_bytes()
    with patch("theory_master.task_graph_upgrade.import_legacy_canvas", side_effect=broken):
        with pytest.raises(ValueError, match="ambiguous"):
            upgrade_task_graph(legacy)
    assert not (legacy.branch_canvas.parent / "science_graph.sqlite3").exists()
    assert legacy.branch_canvas.read_bytes() == raw
    assert not list(legacy.branch_canvas.parent.glob(".graph-upgrade-*"))
    assert upgrade_task_graph(legacy)["status"] == "upgraded"


def test_active_runtime_is_refused(legacy):
    with patch("theory_master.runtime_manager.runtime_health", return_value={"active": True}):
        with pytest.raises(RuntimeError, match="Stop the task"):
            upgrade_task_graph(legacy)
    assert not (legacy.branch_canvas.parent / "science_graph.sqlite3").exists()


def test_live_runner_lock_is_refused_even_with_stale_registry(legacy):
    from theory_master.runtime_manager import _file_lock, owner_lock_path
    with _file_lock(owner_lock_path(legacy.research_root)):
        with pytest.raises(BlockingIOError):
            upgrade_task_graph(legacy)
    assert not (legacy.branch_canvas.parent / "science_graph.sqlite3").exists()


def test_worker_claims_block_upgrade(legacy):
    guard = legacy.research_root / ".theory-master"
    guard.mkdir(parents=True)
    (guard / "branch_claims.json").write_text(json.dumps({
        "schema": "theory-master/branch-claims/v1", "claims": {"H-0001": {"worker_id": "worker-1"}},
    }))
    with pytest.raises(RuntimeError, match="branch claims"):
        upgrade_task_graph(legacy)
    assert not (legacy.branch_canvas.parent / "science_graph.sqlite3").exists()


def test_corrupted_existing_backup_is_refused(legacy):
    digest = hashlib.sha256(legacy.branch_canvas.read_bytes()).hexdigest()[:12]
    legacy.branch_canvas.with_name(legacy.branch_canvas.name + ".before-sqlite-" + digest + ".bak").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="backup does not match"):
        upgrade_task_graph(legacy)
    assert not (legacy.branch_canvas.parent / "science_graph.sqlite3").exists()


def test_missing_database_for_projection_requires_restore(legacy):
    legacy.branch_canvas.write_text(json.dumps({"nodes": [], "edges": [], "tm_science_graph_projection": {"canonical": "sqlite"}}))
    with pytest.raises(ValueError, match="restore SQLite"):
        upgrade_task_graph(legacy)
    assert not (legacy.branch_canvas.parent / "science_graph.sqlite3").exists()


def test_invalid_anchor_does_not_publish(legacy):
    legacy.config["branch_anchor_id"] = "H-missing"
    with pytest.raises(ValueError, match="branch_anchor_id"):
        upgrade_task_graph(legacy)
    assert not (legacy.branch_canvas.parent / "science_graph.sqlite3").exists()


def test_changed_canvas_does_not_publish(legacy):
    from theory_master.science_graph_store import import_legacy_canvas
    def changing(canvas, database, **kwargs):
        result = import_legacy_canvas(canvas, database, **kwargs)
        canvas.write_text('{"nodes": [], "edges": []}')
        return result
    with patch("theory_master.task_graph_upgrade.import_legacy_canvas", side_effect=changing):
        with pytest.raises(RuntimeError, match="changed during upgrade"):
            upgrade_task_graph(legacy)
    assert not (legacy.branch_canvas.parent / "science_graph.sqlite3").exists()


def test_web_resume_upgrades_then_loads_spec_before_launch(legacy):
    from theory_master.operator_control import dispatch
    config = legacy.branch_canvas.parent / "config.json"
    config.write_text("{}")
    legacy.config_path = config
    legacy.master_id = 128
    legacy.research_root.mkdir()
    spec = SimpleNamespace(master_id=128, research_root=legacy.research_root)
    def load(_):
        database = legacy.branch_canvas.parent / "science_graph.sqlite3"
        if not database.exists():
            raise FileNotFoundError(f"canonical science_graph.sqlite3 is required: {database}")
        return spec
    with patch("theory_master.operator_control.TaskBinding.load", return_value=legacy), \
            patch("theory_master.operator_control.TaskSpec.from_config", side_effect=load), \
            patch("theory_master.runtime_manager.launch_managed_runtime", return_value={"status": "started", "launch_id": "fixture"}) as launch:
        result = dispatch({"task_root": str(legacy.research_root), "research_root": str(legacy.research_root), "run_id": 128, "action": "resume", "value": {}})
    assert result["launch_status"] == "started"
    launch.assert_called_once()
    assert launch.call_args.kwargs["operation"] == "resume"


@pytest.mark.parametrize("action,run_id", [("pause", 128), ("resume", 129)])
def test_nonlaunch_or_wrong_target_does_not_upgrade(legacy, action, run_id):
    from theory_master.operator_control import dispatch
    legacy.config_path = legacy.branch_canvas.parent / "config.json"
    legacy.master_id = 128
    legacy.research_root.mkdir()
    with patch("theory_master.operator_control.TaskBinding.load", return_value=legacy), \
            patch("theory_master.operator_control.TaskSpec.from_config", side_effect=FileNotFoundError("canonical science_graph.sqlite3 is required: missing")), \
            patch("theory_master.task_graph_upgrade.upgrade_task_graph") as upgrade:
        with pytest.raises((ValueError, FileNotFoundError)):
            dispatch({"task_root": str(legacy.research_root), "research_root": str(legacy.research_root), "run_id": run_id, "action": action})
        upgrade.assert_not_called()
