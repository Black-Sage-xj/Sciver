"""Pre-task chat assembly, authorization and immediate navigation without models."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tm_observer import research_chat as chat
from tm_observer import research_preparation as prep
from tm_observer.collector import collect_state
from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler


PLAN = "# Goal\n\nCompare the two mechanisms.\n\n# Research Contract\n\nUse independent observations and quantify uncertainty.\n\n# Final Gate\n\nReport a discriminating result and its limits.\n\n# Required Context\n\nThe current observation has not been verified.\n"


class Backend:
    def __init__(self):
        self.calls = []

    def ensure_thread(self, role, thread, model, provider):
        return thread or "preparation-thread"

    def invoke(self, role, prompt, thread, model, provider):
        self.calls.append((role, prompt, thread))
        return thread, "可以先明确问题并核对相关论文，再形成计划。"


@pytest.mark.parametrize("entry", ["clear", "exploratory"])
def test_empty_workspace_starts_preparation_and_loads_actual_skill_on_every_turn(tmp_path, entry):
    state_dir, state = tmp_path / "observer", {"runs": []}
    before = chat.snapshot(state_dir, state, "__new__")
    assert before["session"] is None and before["sessions"] == []
    ident = chat.create(state_dir, state, {"mode": "preparation", "entry": entry})["session_id"]
    row = chat.snapshot(state_dir, state, ident)["session"]
    assert row["task_id"] is None and row["skill"] == chat.PREPARATION_SKILL
    assert row["preparation"]["draft"] == "" and row["preparation"]["created_task"] is None
    assert not (tmp_path / "projects").exists()
    fake = Backend()
    chat.send(state_dir, tmp_path, state, ident, {"content": "我只有一个观察，想一起找研究问题。"}, backend=fake)
    original = prep.snapshot(state_dir, ident)
    prep.update(state_dir, ident, {"expected_revision": original["revision"], "intent": "A preliminary observation", "draft": PLAN})
    chat.send(state_dir, tmp_path, state, ident, {"content": "讨论这个计划的边界，不创建项目。"}, backend=fake)
    for role, prompt, thread in fake.calls:
        assert role == "research_chat" and thread == "preparation-thread"
        assert "Loaded research chat skill: research-preparation" in prompt
        assert "没有正式 Task" in prompt
        assert "Do not demand a complete research contract" in prompt
        assert "No separate Web controller" not in prompt
        assert "do not invent papers" in prompt
        assert str(prep.workspace(state_dir, ident)) in prompt
        assert "research_preparation.update" in prompt
    assert "A preliminary observation" in fake.calls[-1][1]
    assert "Compare the two mechanisms" in fake.calls[-1][1]
    assert not (tmp_path / "projects").exists()


def test_preparation_rejects_existing_task_binding_and_wrong_entry(tmp_path):
    state = {"runs": [{"observer_id": "a" * 16}]}
    for payload in ({"mode": "preparation", "entry": "clear", "task_id": "a" * 16},
                    {"mode": "preparation", "entry": "unknown"},
                    {"mode": "preparation", "entry": "clear", "skill": "research-intervention"}):
        with pytest.raises(chat.ResearchChatError):
            chat.create(tmp_path / "observer", state, payload)


def test_active_or_cross_process_chat_refuses_editor_and_creation(tmp_path):
    state_dir = tmp_path / "observer"
    ident = chat.create(state_dir, {"runs": []}, {"mode": "preparation", "entry": "clear"})["session_id"]
    current = prep.snapshot(state_dir, ident)
    payload = {"expected_revision": current["revision"], "draft": PLAN}
    try:
        with chat._JOBS_LOCK:
            chat._JOBS[ident] = {"status": "streaming"}
        with pytest.raises(chat.ResearchChatError, match="仍在生成"):
            chat.update_preparation(state_dir, ident, payload)
        with pytest.raises(chat.ResearchChatError, match="仍在生成"):
            chat.create_prepared_project(state_dir, tmp_path, ident, {"confirm": True, "expected_revision": current["revision"]})
    finally:
        with chat._JOBS_LOCK:
            chat._JOBS.pop(ident, None)
    import fcntl
    with (state_dir / "research-chat" / (ident + ".lock")).open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        with pytest.raises(chat.ResearchChatError, match="正在更新"):
            chat.update_preparation(state_dir, ident, payload)
        with pytest.raises(chat.ResearchChatError, match="正在更新"):
            chat.create_prepared_project(state_dir, tmp_path, ident, {"confirm": True, "expected_revision": current["revision"]})
    assert prep.snapshot(state_dir, ident)["draft"] == ""


def test_preparation_retry_and_edit_keep_actual_user_question(tmp_path, monkeypatch):
    state_dir, state = tmp_path / "observer", {"runs": []}
    ident = chat.create(state_dir, state, {"mode": "preparation", "entry": "exploratory"})["session_id"]
    fake = Backend()
    chat.send(state_dir, tmp_path, state, ident, {"content": "原问题"}, backend=fake)
    store = chat._store(state_dir)
    row = chat._read(store, ident)
    user = next(message for message in row["messages"] if message["role"] == "user")
    row["active_run"].update(status="error", content="原问题")
    chat._write(store, row)
    accepted = []
    monkeypatch.setattr(chat, "begin_send", lambda *args: accepted.append(args[-1]) or {"ok": True})
    assert chat.begin_retry(state_dir, tmp_path, state, ident)["ok"]
    assert accepted[-1]["content"] == "原问题"
    monkeypatch.undo()
    class HeldThread:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
    try:
        with patch.object(chat.threading, "Thread", HeldThread):
            assert chat.begin_send(state_dir, tmp_path, state, ident,
                                   {"content": "修改后的问题", "edit_message_id": user["id"]})["streaming"]
        latest = chat._read(store, ident)
        assert latest["messages"][-1]["content"] == "修改后的问题"
        assert latest["codex_thread_id"] is None
        assert latest["skill"] == chat.PREPARATION_SKILL
    finally:
        with chat._JOBS_LOCK:
            chat._JOBS.pop(ident, None)


def test_creation_service_freezes_actual_persisted_conversation(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "data"))
    state_dir = tmp_path / "observer"
    ident = chat.create(state_dir, {"runs": []}, {"mode": "preparation", "entry": "clear"})["session_id"]
    original = prep.snapshot(state_dir, ident)
    current = prep.update(state_dir, ident, {"expected_revision": original["revision"], "draft": PLAN})
    actual = chat._read(chat._store(state_dir), ident)["messages"]
    receipt = prep.create_project(state_dir, tmp_path, ident, {"confirm": True, "expected_revision": current["revision"]},
                                  conversation=[{"role": "assistant", "content": "fabricated"}])
    frozen = json.loads((Path(receipt["snapshot_path"]) / "conversation.json").read_text())
    assert frozen == actual


def test_http_creation_is_visible_in_stale_cache_and_obeys_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "data"))
    cache = StateCache(tmp_path)
    cache._state = collect_state(tmp_path, include_git_info=False)
    cache._at = time.monotonic()
    # Hold the original empty snapshot to reproduce immediate navigation before scanning.
    monkeypatch.setattr(cache, "_start_state_refresh_locked", lambda: None)
    monkeypatch.setattr(cache, "_start_git_refresh_locked", lambda: None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache, field_notes_state_dir=tmp_path / "observer"))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = "http://127.0.0.1:" + str(server.server_port)
    def post(path, payload, valid=True):
        request = Request(origin + path, data=json.dumps(payload, ensure_ascii=False).encode(),
                          headers={"Origin": origin if valid else "https://foreign.invalid", "X-TM-Operator-Intent": "1", "Content-Type": "application/json"})
        with urlopen(request) as response:
            return json.load(response)
    try:
        with urlopen(origin + "/research-chat/?new=1") as response:
            assert "创建新项目" in response.read().decode()
        with pytest.raises(HTTPError) as rejected:
            post("/api/research-chat/session", {"mode": "preparation", "entry": "clear"}, valid=False)
        assert rejected.value.code == 403
        ident = post("/api/research-chat/session", {"mode": "preparation", "entry": "clear"})["session_id"]
        base = "/api/research-chat/" + ident
        with urlopen(origin + base + "/preparation") as response:
            current = json.load(response)
        large_draft = PLAN + "\n讨论中的资料：" + "观测条件" * 16000
        large = post(base + "/preparation", {"expected_revision": current["revision"], "draft": large_draft})
        assert large["draft"] == large_draft
        saved = post(base + "/preparation", {"expected_revision": large["revision"], "draft": PLAN, "title": "新研究项目"})
        receipt = post(base + "/create-project", {"confirm": True, "expected_revision": saved["revision"]})
        with urlopen(origin + receipt["task_href"]) as response:
            assert response.status == 200
        assert any(run["observer_id"] == receipt["observer_id"] for run in cache.get()["runs"])
        again = post(base + "/create-project", {"confirm": True, "expected_revision": saved["revision"]})
        assert again["observer_id"] == receipt["observer_id"] and again["idempotent"]
        session = chat.snapshot(tmp_path / "observer", cache.get(), ident)["session"]
        assert sum(bool(message.get("preparation_receipt")) for message in session["messages"]) == 1
        assert not list(Path(receipt["research_root"]).glob("[0-9]*"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_workspace_refresh_does_not_drop_a_just_created_task(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "data"))
    state_dir = tmp_path / "observer"
    ident = chat.create(state_dir, {"runs": []}, {"mode": "preparation", "entry": "clear"})["session_id"]
    current = prep.snapshot(state_dir, ident)
    current = prep.update(state_dir, ident, {"expected_revision": current["revision"], "draft": PLAN})
    receipt = prep.create_project(state_dir, tmp_path, ident, {"confirm": True, "expected_revision": current["revision"]})
    cache = StateCache(tmp_path)
    old_snapshot = {"runs": [], "system": {"task_count": 0}}
    cache._state = old_snapshot
    cache.include_created_task(Path(receipt["task_root"]))
    monkeypatch.setattr("tm_observer.web.collect_state", lambda *args, **kwargs: {"runs": [], "system": {"task_count": 0}})
    monkeypatch.setattr(cache, "_start_git_refresh_locked", lambda: None)
    cache._refresh_state()
    assert cache.get()["runs"][0]["observer_id"] == receipt["observer_id"]
    assert old_snapshot["runs"] == []


def test_chat_html_state_progress_and_preparation_stay_readable_under_literature_material_lock(tmp_path):
    state_dir, search = tmp_path/'observer', tmp_path/'workspace'
    search.mkdir()
    ident = chat.create(state_dir, {'runs': []}, {'mode': 'preparation', 'entry': 'clear'})['session_id']
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(StateCache(search), field_notes_state_dir=state_dir))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        with prep._locked(prep.workspace(state_dir, ident)):
            for path in (f'/research-chat/?session={ident}', f'/api/research-chat/{ident}/state',
                         f'/api/research-chat/{ident}/progress', f'/api/research-chat/{ident}/preparation'):
                with urlopen(base + path, timeout=1) as response:
                    assert response.status == 200
                    assert response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
