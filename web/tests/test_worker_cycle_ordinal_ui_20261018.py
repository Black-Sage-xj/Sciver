"""Cycle invocation ordinal is not the task-global scientific Cycle ID."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bs4 import BeautifulSoup
from theory_master.tui.web_bridge import project
from theory_master.tui.render import render_header
from tm_observer.run_views import run_detail_body, run_index_body


def _json(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def worker_fixture(root: Path, *, cycle_index: int | None = 1, scientific_index: int | None = 1) -> dict:
    cycle = root / "130"
    cycle.mkdir(parents=True)
    (cycle / ".theory-master-cycle").write_text("v2", encoding="utf-8")
    _json(root / ".theory-master" / "worker_mode.json", {
        "schema": "theory-master/worker-mode/v1", "enabled": True,
        "desired_agents": 2, "requested_agents": 2,
    })
    _json(root / ".theory-master" / "workers" / "worker-1" / "runtime.json", {
        "schema": "theory-master/worker-runtime/v1", "worker_id": "worker-1",
        "current_cycle": "130", "status": "OVER",
    })
    run_state = {"cycle_id": "130", "cycle_index": cycle_index, "cycle_number": 130,
                 "max_cycles": 40, "status": "OVER", "phase": "complete",
                 "research_root": str(root), "tokens_total": 777, "project_tokens_total": 777}
    _json(cycle / "run_state.json", run_state)
    _json(cycle / "research_state.json", {
        "worker_id": "worker-1", "cycle_id": "130",
        "invocation_cycle_index": scientific_index, "status": "over",
    })
    return {"observer_id": "a" * 16, "run_id": 1, "task_name": "test",
            "research_root": str(root), "cycle_dir": str(cycle), "cycle": "130", "state": "OVER"}


def test_selected_worker_ordinal_is_persisted_not_global_id(tmp_path):
    run = worker_fixture(tmp_path)
    result = project(tmp_path, cycle_dir=tmp_path / "130", worker_id="worker-1")
    assert result["status"] == "OVER"
    assert result["cycle_id"] == "130"
    assert "Cycle 1 / 40" in result["parts"]["header"]
    assert "Cycle 130 / 40" not in result["parts"]["header"]
    page = BeautifulSoup(run_detail_body(run, worker_id="worker-1"), "html.parser")
    cycle_card = next(card for card in page.select(".tm-tui-kv") if card.select_one("dt")
                      and card.select_one("dt").get_text(strip=True) == "Cycle")
    assert "Cycle 1 / 40 修改上限" in cycle_card.get_text(" ", strip=True)
    assert "Cycle ID · 130" not in cycle_card.get_text(" ", strip=True)
    assert "任务上限 40" not in cycle_card.get_text(" ", strip=True)
    assert "130 / 40" not in cycle_card.get_text(" ", strip=True)
    assert "Cycle ID 130" in page.select_one(".tm-tui-identity").get_text(" ", strip=True)
    assert 'worker=worker-1' in page.select_one(".tm-hypothesis-card")["href"]
    assert "tab=research" in page.select_one(".tm-hypothesis-card")["href"]


def test_cycle_ordinal_missing_falls_back_only_to_scientific_invocation(tmp_path):
    worker_fixture(tmp_path, cycle_index=None, scientific_index=3)
    projected = project(tmp_path, cycle_dir=tmp_path / "130", worker_id="worker-1")
    assert "Cycle 3 / 40" in projected["parts"]["header"]
    _json(tmp_path / "130" / "research_state.json", {
        "worker_id": "worker-1", "cycle_id": "130", "status": "over"})
    projected = project(tmp_path, cycle_dir=tmp_path / "130", worker_id="worker-1")
    assert "Cycle — / 40" in projected["parts"]["header"]
    assert "Cycle 130 / 40" not in projected["parts"]["header"]
    assert "Cycle —" in render_header({"status": "OVER", "max_cycles": 40})


def test_master_and_worker_cycle_fields_cannot_be_confused(tmp_path):
    run = worker_fixture(tmp_path)
    page = run_detail_body(run, worker_id="worker-1")
    assert "action:'max_cycles'" in page
    assert "expected_cycle:ctx.dataset.cycle" in page
    assert "expected_status:ctx.dataset.status" in page
    assert "expected_launch_id:ctx.dataset.launch" in page
    assert 'data-cycle-max-current' in page
    scripts = BeautifulSoup(page, "html.parser").select("script")
    for script in scripts:
        code = script.get_text()
        if code.strip():
            check = subprocess.run(["node", "--check", "-"], input=code, text=True,
                                   capture_output=True, timeout=8)
            assert check.returncode == 0, check.stderr
    listing = run_index_body({"runs": [run]})
    assert "<th>Cycle ID</th>" in listing
