"""Offline Test0 setup and local Web entry points."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

from .regression_cases import materialize_case, verify_case
from .task import TaskBinding, bootstrap, ensure_runtime_outside_package, summary


def default_workspace() -> Path:
    return Path.home() / "Sciver-workspace"


def prepare_test0(workspace: Path) -> TaskBinding:
    root = ensure_runtime_outside_package(workspace / "test0")
    verify_case("Test0")
    marker = root / ".sciver-test0.json"
    if marker.is_file() and json.loads(marker.read_text()) == {"schema": "sciver/quickstart/v1", "case": "test_0"}:
        binding = TaskBinding.load(root)
    else:
        materialize_case("Test0", root)
        binding = bootstrap(root / "research_plan.md")
        config = json.loads(binding.config_path.read_text(encoding="utf-8"))
        config["max_cycles"] = 1
        binding.config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        marker.write_text(json.dumps({"schema": "sciver/quickstart/v1", "case": "test_0"}) + "\n")
    with sqlite3.connect((root / "science_graph.sqlite3").as_uri() + "?mode=ro", uri=True) as database:
        if database.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Test0 scientific graph failed SQLite integrity check")
    print("Test0 offline checks passed: dataset checksum, task configuration, SQLite graph.", flush=True)
    print("No model requests were sent. Configure a provider before starting research in the UI.", flush=True)
    print(summary(binding).replace("TheoryMaster task discovered", "Sciver task ready"), flush=True)
    return binding


def launch_web(workspace: Path, *, port: int = 8787, open_browser: bool = True) -> None:
    try:
        from tm_observer.web import serve
    except ImportError as exc:
        raise ValueError("Web UI is not installed. From the checkout run: python -m pip install -e ./web") from exc
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"Workspace does not exist: {workspace}. Run sciver quickstart first.")
    # Explicit operator settings win. The active environment is usable on a fresh
    # installation without assuming a private Miniconda path.
    os.environ.setdefault("THEORYMASTER_COMPUTE_PYTHON", sys.executable)
    serve(workspace, host="127.0.0.1", port=port, open_browser=open_browser)
