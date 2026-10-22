"""Services Worker accounts: task-only projection and explicit operator action."""
from __future__ import annotations

import json
import subprocess

import pytest
from bs4 import BeautifulSoup

from tm_observer.run_control import OperatorError, _validated_value
from tm_observer.services_worker_accounts import section_html, snapshot
from tm_observer.web import codex_usage_body


def test_visible_account_assignment_projection_has_no_credentials(tmp_path):
    root = tmp_path / "research"
    worker = root / ".theory-master" / "workers" / "worker-1"
    worker.mkdir(parents=True)
    (worker / "runtime.json").write_text(json.dumps({
        "schema": "theory-master/worker-runtime/v1", "worker_id": "worker-1",
        "status": "OVER", "account": "1", "current_cycle": "10",
        "access_token": "DO-NOT-EXPOSE"}))
    (root / ".theory-master" / "worker_account_preferences.json").write_text(json.dumps({
        "schema": "theory-master/worker-account-preferences/v1",
        "accounts": {"worker-1": "2"}}))
    view = snapshot({"runs": [{"observer_id": "a" * 16,
                               "research_root": str(root), "task_name": "Test"}]})
    assert view["tasks"][0]["workers"][0]["current_account"] == "1"
    assert view["tasks"][0]["workers"][0]["planned_account"] == "2"
    assert "DO-NOT-EXPOSE" not in json.dumps(view)
    assert snapshot({"runs": [{"observer_id": "../../oops", "research_root": str(root)}]}) == {
        "tasks": []}


def test_action_strict_validation_and_rendered_script_syntax():
    assert _validated_value("set_worker_account",
                            {"worker_id": "worker-2", "account": "2"})["account"] == "2"
    for request in ({"worker_id": "../etc", "account": "1"},
                    {"worker_id": "worker-2", "account": "3"},
                    {"worker_id": "worker-2", "account": "1", "path": "/tmp/x"}):
        with pytest.raises(OperatorError):
            _validated_value("set_worker_account", request)
    html = section_html()
    assert "services-workers.json" in html and "set_worker_account" in html
    assert "下一次新 Cycle" in html
    assert "Worker 账号分配" in codex_usage_body()
    assert "data-migrate" in html
    assert "切换并恢复当前 Cycle" in html
    assert "立即 Resume" in html
    good = {"worker_id": "worker-2", "operation": "resume", "account": "1"}
    assert _validated_value("worker_control", good) == good
    assert _validated_value("worker_control", {"worker_id": "worker-2", "operation": "pause"})["operation"] == "pause"
    for bad in ({"worker_id": "worker-2", "operation": "pause", "account": "1"},
                {"worker_id": "worker-2", "operation": "resume", "account": "3"},
                {"worker_id": "worker-2", "operation": "resume", "account": "../auth"},
                {"worker_id": "worker-2", "operation": "resume", "account": None}):
        with pytest.raises(OperatorError):
            _validated_value("worker_control", bad)
    scripts = BeautifulSoup(html, "html.parser").select("script")
    assert len(scripts) == 1
    check = subprocess.run(["node", "--check", "-"], input=scripts[0].get_text(),
                           text=True, capture_output=True, timeout=5)
    assert check.returncode == 0, check.stderr
