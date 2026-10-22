"""The State metric must not resurrect a dead Worker's stale RUNNING snapshot."""
import os
from pathlib import Path

from bs4 import BeautifulSoup

from tm_observer.collector import collect_run
from tm_observer.run_views import _tui_summary, run_detail_body
from tm_observer.worker_console import registered_workers

from test_worker_live_status_regression_20261017 import _fixture, _write


TUI_RUNNING = (
    "Process RUNNING   State RUNNING\n"
    "Cycle 1 / 40    Phase investigator:running  Elapsed 13:55\n"
    "Started 10-17T22:00:00  Cycle start 10-17T22:00:00  Cost — · Tokens 200,828"
)


def _state_metric(body: str) -> str:
    doc = BeautifulSoup(body, "html.parser")
    for tile in doc.select(".tm-tui-header-stats .tm-tui-kv"):
        title = tile.select_one("dt")
        if title and title.get_text(strip=True) == "State":
            return next(tile.select_one("dd .tm-tui-process").stripped_strings)
    raise AssertionError("State metric missing")


def test_verified_worker_state_overrides_stale_textual_cycle_header():
    assert _state_metric(_tui_summary(TUI_RUNNING)) == "RUNNING"
    body = _tui_summary(TUI_RUNNING, effective_status="ORPHANED",
                        allow_process_actions=False)
    assert _state_metric(body) == "ORPHANED"
    doc = BeautifulSoup(body, "html.parser")
    assert not doc.select_one(".tm-tui-process-green")
    assert doc.select_one(".tm-tui-process-red")
    assert doc.select_one(".tm-tui-process").get_text(strip=True) == "ORPHANED"


def test_dead_worker_detail_state_metric_matches_badge_and_tab(tmp_path: Path):
    location = _fixture(tmp_path, worker_pid=99999999)
    research = location.research_root
    _write(research / ".theory-master/worker_mode.json", {
        "schema": "theory-master/worker-mode/v1", "enabled": True, "desired_agents": 1
    })
    (research / "130/.theory-master-cycle").write_text("v2")
    run = collect_run(location)
    assert run["state"] == "ORPHANED"
    assert registered_workers(run)[0]["raw_status"] == "RUNNING"
    body = run_detail_body(run, worker_id="worker-1")
    doc = BeautifulSoup(body, "html.parser")
    assert doc.select_one(".tm-tui-title-actions .tm-tui-state-badge") is None
    assert doc.select_one('[data-worker-state-open]')
    assert doc.select_one(".tm-worker-tab.is-selected small").get_text(strip=True) == "ORPHANED"
    assert _state_metric(body) == "ORPHANED"
    assert doc.select_one(".tm-tui-screen")["data-tui-status"] == "ORPHANED"


def test_verified_live_worker_remains_running_on_all_worker_cards(tmp_path: Path):
    location = _fixture(tmp_path, worker_pid=os.getpid())
    research = location.research_root
    _write(research / ".theory-master/worker_mode.json", {
        "schema": "theory-master/worker-mode/v1", "enabled": True, "desired_agents": 1
    })
    (research / "130/.theory-master-cycle").write_text("v2")
    run = collect_run(location)
    assert run["state"] == "LIVE"
    doc = BeautifulSoup(run_detail_body(run, worker_id="worker-1"), "html.parser")
    assert doc.select_one(".tm-tui-title-actions .tm-tui-state-badge") is None
    assert doc.select_one('[data-worker-state-open]')
    assert doc.select_one('[data-worker-action="pause"]')
    assert doc.select_one('[data-worker-action="kill"]')
    assert doc.select_one(".tm-worker-tab.is-selected small").get_text(strip=True) == "RUNNING"
    assert _state_metric(str(doc)) == "RUNNING"
