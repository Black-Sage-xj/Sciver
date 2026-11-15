"""Fresh public Test0 setup and CLI compatibility without model requests."""
from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from theory_master.regression_cases import create_test_instance
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.scientific_model import load_from_paths
from theory_master.specification import make_run_id
from theory_master.task import bootstrap


def test_new_instances_preserve_history_and_have_empty_scientific_state(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_SEARCH_ROOT", "")
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "data"))
    instances = tmp_path / "instances"
    with patch("theory_master.runtime_manager.launch_managed_runtime", side_effect=AssertionError("offline")):
        first, _ = create_test_instance(instances_root=instances, start=False)
        (first.research_root / "old-thread.json").write_text('{"thread":"old"}')
        with ScienceGraphStore(first.branch_canvas.with_name("science_graph.sqlite3")) as graph:
            graph.add_hypothesis("H-0001", title="Previous work")
        second, outcome = create_test_instance(instances_root=instances, start=False)
    assert first.master_id != second.master_id
    assert first.task_home != second.task_home
    assert first.research_root != second.research_root
    assert outcome == {"status": "prepared"}
    assert (first.research_root / "old-thread.json").is_file()
    assert not list(second.research_root.rglob("*thread*.json*"))
    assert not list(second.research_root.glob("*/research_state.json"))
    assert make_run_id(research_root=second.research_root, task_mode=True) == "1"
    model = load_from_paths(second.research_root, second.task_home / "research_plan.md", goal_id=second.goal_id)
    assert (model["version"], model["summary"]) == (0, "")
    with ScienceGraphStore(second.branch_canvas.with_name("science_graph.sqlite3"), readonly=True) as graph:
        assert graph.db.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 0
    for name in ("expected.json", "case.json", "evaluator"):
        assert not (second.task_home / name).exists()
    assert (second.benchmark_root / "datasets/brownian_2d/trajectories.csv").is_file()
    assert bootstrap(first.task_home / "research_plan.md", allocation_root=instances).master_id == first.master_id


def test_explicit_instances_outside_configured_search_root_still_get_distinct_ids(tmp_path, monkeypatch):
    search = tmp_path / "other-workspace"
    search.mkdir()
    monkeypatch.setenv("THEORY_MASTER_SEARCH_ROOT", str(search))
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "data"))
    first, _ = create_test_instance(instances_root=tmp_path / "instances", start=False)
    second, _ = create_test_instance(instances_root=tmp_path / "instances", start=False)
    assert first.master_id != second.master_id


def test_parallel_instances_allocate_distinct_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_SEARCH_ROOT", "")
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "data"))
    with ThreadPoolExecutor(max_workers=3) as workers:
        futures = [workers.submit(create_test_instance, instances_root=tmp_path / "instances", start=False)
                   for _ in range(3)]
        bindings = [future.result()[0] for future in futures]
    assert len({binding.master_id for binding in bindings}) == 3
    assert len({binding.research_root for binding in bindings}) == 3


@pytest.mark.parametrize("command", ["test", "test0", "task0"])
@pytest.mark.parametrize("prepare", [False, True])
def test_cli_aliases_create_fresh_and_launch_only_when_requested(command, prepare):
    from theory_master.__main__ import main
    binding = SimpleNamespace(research_root=Path("/tmp/public-fresh-test-runtime"),
                              task_home=Path("/tmp/public-fresh-test-home"))
    argv = ["sciver", command] + (["--prepare-only"] if prepare else [])
    with patch("sys.argv", argv), patch("theory_master.__main__.create_test_instance",
            return_value=(binding, {"status": "prepared"})) as create, \
            patch("theory_master.__main__.task_summary", return_value="fresh task"), redirect_stdout(io.StringIO()):
        assert main() == 0
    create.assert_called_once_with("Test0", None, instances_root=None, start=not prepare)


def test_open_option_preserves_web_entry_after_offline_preparation():
    from theory_master.__main__ import main
    binding = SimpleNamespace(research_root=Path("/tmp/public-fresh-test-runtime"),
                              task_home=Path("/tmp/workspace/instance"))
    with patch("sys.argv", ["sciver", "test", "Test0", "--prepare-only", "--open", "--port", "8788"]), \
            patch("theory_master.__main__.create_test_instance", return_value=(binding, {"status": "prepared"})), \
            patch("theory_master.__main__.task_summary", return_value="fresh task"), \
            patch("theory_master.onboarding.launch_web") as launch, redirect_stdout(io.StringIO()):
        assert main() == 0
    launch.assert_called_once_with(Path("/tmp/workspace"), port=8788)


def test_default_launch_uses_new_task_run_operation(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_SEARCH_ROOT", str(tmp_path))
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "data"))
    with patch("theory_master.runtime_manager.launch_managed_runtime", return_value={"status": "started"}) as launch:
        binding, outcome = create_test_instance(instances_root=tmp_path / "instances")
    assert outcome["status"] == "started"
    assert launch.call_args.kwargs == {"operation": "run", "identifier": str(binding.master_id)}
