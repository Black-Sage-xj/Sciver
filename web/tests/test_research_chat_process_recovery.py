"""Cross-process Chat recovery uses the durable session lock, without models."""
from __future__ import annotations

import fcntl
import select
import subprocess
import sys
from contextlib import contextmanager

import pytest

from tm_observer import research_chat as chat


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "_JOBS", {})
    state_dir = tmp_path / "state"
    ident = chat.create(state_dir, {"runs": []}, {
        "mode": "preparation", "entry": "exploratory",
    })["session_id"]
    root = chat._store(state_dir)
    row = chat._read(root, ident)
    chat._message(row, "user", "请保存计划供我审阅。")
    chat._write(root, row)
    return state_dir, root, ident


@contextmanager
def held_by_another_process(root, ident):
    script = """
import fcntl, select, sys
with open(sys.argv[1], 'a+') as handle:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    print('locked', flush=True)
    if select.select([sys.stdin], [], [], 10)[0]:
        sys.stdin.read(1)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(chat._file(root, ident).with_suffix(".lock"))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 5)[0], "lock holder did not start"
        assert process.stdout.readline() == "locked\n"
        yield process
    finally:
        try:
            process.communicate("\n", timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        assert process.returncode == 0


@pytest.mark.parametrize("status", ["starting", "streaming", "stopping"])
def test_foreign_active_turn_stays_live_until_its_process_releases_lock(session, status):
    state_dir, root, ident = session
    row = chat._read(root, ident)
    row["active_run"] = {"id": "foreign-run", "status": status,
                         "user_message_id": row["messages"][-1]["id"],
                         "stream_text": "已核对来源，正在整理计划。"}
    chat._write(root, row)
    chat._runtime_update(root, ident, lambda data: data.update({
        "run": {**row["active_run"], "stage": "tool", "event_seq": 7},
    }))
    record_path = chat._file(root, ident)
    runtime_path = record_path.with_suffix(".runtime")
    before = record_path.read_bytes(), runtime_path.read_bytes()

    with held_by_another_process(root, ident) as owner:
        view = chat.snapshot(state_dir, {"runs": []}, ident)["session"]
        assert owner.poll() is None
        assert view["streaming"] is True
        assert view["stream_status"] == status
        assert view["stream_text"] == "已核对来源，正在整理计划。"
        assert view["active_run"]["status"] == status
        assert view["active_run"]["stage"] == "tool"
        assert view["active_run"]["event_seq"] == 7
        assert "error_type" not in view["active_run"]
        assert ident not in chat._JOBS
        assert (record_path.read_bytes(), runtime_path.read_bytes()) == before

    recovered = chat._recover_selected_session(root, ident)
    assert recovered["active_run"]["status"] == "interrupted"
    assert recovered["active_run"]["error_type"] == "ObserverProcessRestarted"
    assert recovered["messages"][-1]["role"] == "assistant"
    assert "未完成" in recovered["messages"][-1]["content"]


def test_foreign_legacy_turn_is_only_recovered_after_lock_release(session):
    _, root, ident = session
    row = chat._read(root, ident)
    row["mode"] = "frontier"
    row.pop("active_run", None)
    chat._write(root, row)
    before = chat._file(root, ident).read_bytes()

    with held_by_another_process(root, ident) as owner:
        projected = chat._recover_selected_session(root, ident)
        assert owner.poll() is None
        assert projected.get("active_run") is None
        assert projected["messages"][-1]["role"] == "user"
        assert chat._file(root, ident).read_bytes() == before

    recovered = chat._recover_selected_session(root, ident)
    assert recovered["active_run"]["status"] == "interrupted"
    assert recovered["active_run"]["user_message_id"] == row["messages"][-1]["id"]


def test_recovery_rechecks_completion_after_acquiring_session_lock(session, monkeypatch):
    _, root, ident = session
    row = chat._read(root, ident)
    row["active_run"] = {"id": "just-finished", "status": "streaming"}
    chat._write(root, row)
    acquire = chat._try_session_lock

    def finish_before_recovery(handle):
        acquired = acquire(handle)
        if acquired:
            completed = chat._read(root, ident)
            completed["active_run"]["status"] = "complete"
            chat._message(completed, "assistant", "计划已保存。")
            chat._write(root, completed)
        return acquired

    monkeypatch.setattr(chat, "_try_session_lock", finish_before_recovery)
    recovered = chat._recover_selected_session(root, ident)
    assert recovered["active_run"]["status"] == "complete"
    assert "error_type" not in recovered["active_run"]
    assert recovered["messages"][-1]["content"] == "计划已保存。"


def test_recovery_releases_session_lock_when_finalization_fails(session, monkeypatch):
    _, root, ident = session
    row = chat._read(root, ident)
    row["active_run"] = {"id": "orphan", "status": "streaming"}
    chat._write(root, row)

    def fail(*args):
        raise RuntimeError("cannot persist recovery")

    monkeypatch.setattr(chat, "_finish_run", fail)
    with pytest.raises(RuntimeError, match="cannot persist recovery"):
        chat._recover_selected_session(root, ident)
    with chat._file(root, ident).with_suffix(".lock").open("a+") as handle:
        assert chat._try_session_lock(handle)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
