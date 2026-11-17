"""Single-runner Task tabs and State cards share lifecycle/process authority."""
import json
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from tm_observer.collector import collect_run
from tm_observer.run_views import run_detail_body


HEADER = (
    "Process RUNNING   State RUNNING\n"
    "Cycle 2 / 5    Phase qualification  Elapsed 12:49\n"
    "Started 11-16T18:01:04  Cycle start 11-16T18:08:47  Cost $1.05"
)


def fixture(root, scientific_status):
    cycle = root / "2"
    cycle.mkdir(parents=True)
    (cycle / ".theory-master-cycle").write_text("v2")
    (cycle / "run_state.json").write_text(json.dumps({
        "status": "RUNNING", "execution_active": True, "owner_pid": 99999999,
    }))
    (cycle / "research_state.json").write_text(json.dumps({
        "status": scientific_status, "execution_active": False, "resumable": False,
        "attention": {"code": "no_qualified_candidate", "reason": "No candidate passed."},
    }))
    return collect_run(root)


def test_stopped_qualification_overrides_historical_running_without_writes(tmp_path):
    run = fixture(tmp_path, "no_qualified_candidate")
    assert run["raw_status"] == "RUNNING"
    assert run["state"] == "NEEDS_ATTENTION"
    assert run["active_agent"] is None
    assert run["provenance"]["state"]["field"] == "status"
    assert run["provenance"]["state"]["source"].endswith("research_state.json")
    assert not any(w["code"] == "ORPHANED" for w in run["warnings"])
    assert json.loads((tmp_path / "2/run_state.json").read_text())["status"] == "RUNNING"


@pytest.mark.parametrize("state", ["ORPHANED", "NEEDS_ATTENTION", "FAILED", "COMPLETE"])
def test_task_state_card_and_agent_tab_override_stale_running(tmp_path, state):
    run = fixture(tmp_path, "active")
    run["state"] = state
    with patch("tm_observer.run_tui_bridge.read_tui", return_value={
        "status": "RUNNING", "cycle_id": "2", "parts": {"header": HEADER},
    }):
        doc = BeautifulSoup(run_detail_body(run), "html.parser")
    assert doc.select_one(".tm-worker-tab.is-selected small").get_text(strip=True) == state
    assert doc.select_one(".tm-tui-screen")["data-tui-status"] == state
    metric = next(tile for tile in doc.select(".tm-tui-kv")
                  if tile.select_one("dt") and tile.select_one("dt").get_text(strip=True) == "State")
    assert metric.select_one("dd .tm-tui-process").get_text(strip=True) == state
    assert not metric.select_one(".tm-tui-process-green")


def test_genuine_live_task_retains_running_projection(tmp_path):
    run = fixture(tmp_path, "active")
    run.update(state="LIVE", pid_alive=True)
    with patch("tm_observer.run_tui_bridge.read_tui", return_value={
        "status": "RUNNING", "cycle_id": "2", "parts": {"header": HEADER},
    }):
        doc = BeautifulSoup(run_detail_body(run), "html.parser")
    assert doc.select_one(".tm-tui-screen")["data-tui-status"] == "RUNNING"
    assert doc.select_one(".tm-tui-process-green").get_text(strip=True).startswith("RUNNING")
