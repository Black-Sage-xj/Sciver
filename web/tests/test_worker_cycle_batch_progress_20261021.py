"""Regression: a selected Worker shows durable batch progress, not Cycle ID."""
from __future__ import annotations

import json
from pathlib import Path

from bs4 import BeautifulSoup
from tm_observer.run_views import run_detail_body
from tm_observer.worker_console import worker_batch_cycle_progress


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def fixture(root: Path) -> dict:
    write_json(root / ".theory-master" / "worker_mode.json", {
        "schema": "theory-master/worker-mode/v1", "enabled": True,
        "desired_agents": 2, "requested_agents": 2,
    })
    write_json(root / ".theory-master" / "worker_scheduler.json", {
        "status": "RUNNING", "quota_mode": "per_worker",
        "worker_ids": ["worker-1", "worker-2"],
        "cycles_started": 21,
        "cycles_started_by_worker": {"worker-1": 15, "worker-2": 6},
        "max_cycles_per_worker": 50,
    })
    for number, cycle in [(1, "183"), (2, "184")]:
        wid = f"worker-{number}"
        write_json(root / ".theory-master" / "workers" / wid / "runtime.json", {
            "schema": "theory-master/worker-runtime/v1", "worker_id": wid,
            "current_cycle": cycle, "status": "PAUSED",
        })
        c = root / cycle
        c.mkdir()
        (c / ".theory-master-cycle").write_text("v2", encoding="utf-8")
        write_json(c / "run_state.json", {
            "cycle_id": cycle, "cycle_index": 1, "max_cycles": 50,
            "status": "PAUSED", "phase": "paused", "research_root": str(root),
        })
        write_json(c / "research_state.json", {
            "worker_id": wid, "cycle_id": cycle, "status": "paused",
            "invocation_cycle_index": 1,
        })
    return {"observer_id": "a" * 16, "run_id": 1, "task_name": "test",
            "research_root": str(root), "cycle_dir": str(root / "184"),
            "cycle": "184", "state": "PAUSED"}


def cycle_text(page: str) -> str:
    soup = BeautifulSoup(page, "html.parser")
    return next(card.get_text(" ", strip=True) for card in soup.select(".tm-tui-kv")
                if card.select_one("dt") and card.dt.get_text(strip=True) == "Cycle")


def test_worker_cards_read_own_batch_count_not_global_cycle_id(tmp_path):
    run = fixture(tmp_path)
    assert worker_batch_cycle_progress(run, "worker-1") == (15, 50)
    assert worker_batch_cycle_progress(run, "worker-2") == (6, 50)
    first = run_detail_body(run, worker_id="worker-1")
    second = run_detail_body(run, worker_id="worker-2")
    assert "15 / 50 修改上限" in cycle_text(first)
    assert "6 / 50 修改上限" in cycle_text(second)
    assert "183 / 50" not in cycle_text(first)
    assert "184 / 50" not in cycle_text(second)
    assert "Cycle ID 183" in BeautifulSoup(first, "html.parser").select_one(".tm-tui-identity").get_text(" ", strip=True)
    assert "Cycle ID 184" in BeautifulSoup(second, "html.parser").select_one(".tm-tui-identity").get_text(" ", strip=True)


def test_worker_batch_progress_changes_with_scheduler_snapshot(tmp_path):
    run = fixture(tmp_path)
    scheduler = tmp_path / ".theory-master" / "worker_scheduler.json"
    record = json.loads(scheduler.read_text())
    record["cycles_started_by_worker"]["worker-2"] = 7
    write_json(scheduler, record)
    assert worker_batch_cycle_progress(run, "worker-2") == (7, 50)
    assert "7 / 50 修改上限" in cycle_text(run_detail_body(run, worker_id="worker-2"))


def test_missing_or_invalid_scheduler_does_not_guess_global_id(tmp_path):
    run = fixture(tmp_path)
    scheduler = tmp_path / ".theory-master" / "worker_scheduler.json"
    record = json.loads(scheduler.read_text())
    for bad in (None, {**record, "quota_mode": "shared"},
                {**record, "cycles_started_by_worker": {"worker-1": 15}},
                {**record, "cycles_started_by_worker": {"worker-1": 15, "worker-2": True}},
                {**record, "max_cycles_per_worker": 0}):
        if bad is None:
            scheduler.unlink()
        else:
            write_json(scheduler, bad)
        assert worker_batch_cycle_progress(run, "worker-2") is None
    scheduler.write_text("{broken", encoding="utf-8")
    assert worker_batch_cycle_progress(run, "worker-2") is None
