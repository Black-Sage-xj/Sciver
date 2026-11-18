"""Operator-chat features: deterministic storage, concurrency and SDK fakes only."""
from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from tm_observer import research_chat as chat
from tm_observer.research_chat_attachments import attachment_content, store_upload
from theory_master.codex_backend import CodexBackend, CodexTurnCancelled
from theory_master.observability.codex_events import map_chat_notification, map_notification


def session(tmp_path):
    task = tmp_path / "project"
    task.mkdir()
    state_dir = tmp_path / "state"
    state = {"runs": [{"observer_id": "a" * 16, "task_root": str(task), "research_root": str(task)}]}
    ident = chat.create(state_dir, state, {"mode": "project", "task_id": "a" * 16})["session_id"]
    return state_dir, state, ident


def wait_for(predicate, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("fake chat worker did not settle")


class FakeHandle:
    def __init__(self):
        self.steered = []

    def steer(self, value):
        self.steered.append(value)


class EventBackend:
    instances = []
    hold = False
    ask = False
    secret = False
    asynchronous = False

    def __init__(self, *args, **kwargs):
        self.handle = FakeHandle()
        self.started = threading.Event()
        self.release = threading.Event()
        self.images = []
        self.answer = None
        self.prompt = ""
        self.__class__.instances.append(self)

    def ensure_thread(self, role, thread, model, provider):
        return thread or "fake-native-thread"

    def invoke(self, role, prompt, thread, model, provider, *, on_event=None, on_delta=None,
               on_handle=None, input_images=None, request_user_input=None, cancel_event=None):
        self.prompt = prompt
        self.images = input_images or []
        if on_handle:
            on_handle(self.handle)
        self.started.set()
        if on_event:
            on_event({"type": "reasoning_summary", "kind": "reasoning", "item_id": "reason", "text": "检查", "delta": True})
            on_event({"type": "reasoning_summary", "kind": "reasoning", "item_id": "reason", "text": "证据", "delta": True})
            on_event({"type": "tool", "item_id": "command", "label": "读取资料", "status": "running"})
            on_event({"type": "tool", "item_id": "command", "label": "读取资料", "status": "complete"})
            on_event({"type": "usage", "raw_usage": {"input_tokens": 110, "output_tokens": 12},
                      "last_request_usage": {"input_tokens": 10, "output_tokens": 2},
                      "context_tokens": 110, "context_window": 200000})
            on_event({"type": "usage", "raw_usage": {"input_tokens": 125, "output_tokens": 15},
                      "last_request_usage": {"input_tokens": 15, "output_tokens": 3}})
            if self.asynchronous:
                on_event(map_chat_notification(SimpleNamespace(method="item/completed", payload={"item": {
                    "type": "agentMessage", "id": "async-message", "text": "选择方法", "questions": [
                        {"title": "选择方法", "options": ["A", "B"]}]}})))
        if self.ask:
            self.answer = request_user_input({"itemId": "question-1", "questions": [
                {"id": "method", "header": "方法", "question": "选择方法", "isSecret": self.secret,
                 "options": [{"label": "A"}, {"label": "B"}]}]})
        while self.hold and not self.release.wait(0.01):
            if cancel_event is not None and cancel_event.is_set():
                raise CodexTurnCancelled("stopped")
        if cancel_event is not None and cancel_event.is_set():
            raise CodexTurnCancelled("stopped")
        if on_delta:
            on_delta("已核验")
        if on_handle:
            on_handle(None)
        return thread, "已核验，下面给出回复。"

    def close(self):
        pass


@pytest.fixture(autouse=True)
def reset_fakes():
    EventBackend.instances = []
    EventBackend.hold = EventBackend.ask = EventBackend.secret = EventBackend.asynchronous = False
    yield
    for backend in EventBackend.instances:
        backend.release.set()
    with chat._JOBS_LOCK:
        jobs = list(chat._JOBS.values())
    for job in jobs:
        job.get("cancel", threading.Event()).set()
        worker = job.get("thread")
        if worker and hasattr(worker, "join"):
            worker.join(timeout=2)


def test_history_keeps_each_run_metrics_events_and_long_input(tmp_path):
    state_dir, state, ident = session(tmp_path)
    first = EventBackend()
    chat.send(state_dir, tmp_path, state, ident, {"content": "材料" * 60000}, backend=first)
    row = chat.snapshot(state_dir, state, ident)["session"]
    run = row["messages"][-1]["run"]
    assert run["duration_ms"] >= 0
    assert run["started_at"] <= run["completed_at"]
    assert run["model"] == "gpt-6-sol" and run["reasoning_effort"] == "medium"
    assert run["usage"] == {"input_tokens": 25, "output_tokens": 5, "total_tokens": 30}
    assert run["context"]["context_window"] == 200000
    assert next(e for e in run["events"] if e["kind"] == "reasoning")["text"] == "检查证据"
    assert [e["status"] for e in run["events"] if e["type"] == "tool"] == ["complete"]
    assert "材料" * 60000 in first.prompt
    old_id = run["id"]
    chat.send(state_dir, tmp_path, state, ident, {"content": "下一条"}, backend=EventBackend())
    row = chat.snapshot(state_dir, state, ident)["session"]
    assert len(row["runs"]) == 2
    assert row["messages"][1]["run"]["id"] == old_id
    stored = chat._read(chat._store(state_dir), ident)
    stored["messages"].append({"id": "large", "role": "assistant", "content": "x" * 1_100_000})
    chat._write(chat._store(state_dir), stored)
    assert chat._read(chat._store(state_dir), ident)["messages"][-1]["id"] == "large"
    with pytest.raises(chat.ResearchChatError):
        chat.begin_send(state_dir, tmp_path, state, ident, {"content": "x" * 120001})


def test_queue_is_nonblocking_stop_pauses_and_resume_is_explicit(tmp_path, monkeypatch):
    state_dir, state, ident = session(tmp_path)
    monkeypatch.setattr("theory_master.codex_backend.CodexBackend", EventBackend)
    EventBackend.hold = True
    chat.begin_send(state_dir, tmp_path, state, ident, {"content": "第一条"})
    wait_for(lambda: EventBackend.instances and EventBackend.instances[0].started.is_set())
    before = time.monotonic()
    queued = chat.queue_message(state_dir, tmp_path, state, ident, {"content": "待发"})
    assert time.monotonic() - before < 0.5
    before = time.monotonic()
    with pytest.raises(chat.ResearchChatError) as failure:
        chat.rename_session(state_dir, ident, "运行中重命名")
    assert failure.value.status == 409 and time.monotonic() - before < 0.5
    with pytest.raises(chat.ResearchChatError):
        chat.begin_send(state_dir, tmp_path, state, ident, {"content": "拒绝并发"})
    chat.update_queued_message(state_dir, ident, queued["queue_id"], {"content": "修改后的待发"})
    chat.stop(state_dir, ident)
    wait_for(lambda: chat.snapshot(state_dir, state, ident)["session"]["stream_status"] == "stopped")
    assert len(EventBackend.instances) == 1
    row = chat.snapshot(state_dir, state, ident)["session"]
    assert row["queue_paused"] and row["queue"][0]["content"] == "修改后的待发"
    EventBackend.hold = False
    chat.resume_queue(state_dir, tmp_path, state, ident)
    wait_for(lambda: len(EventBackend.instances) == 2 and chat.snapshot(state_dir, state, ident)["session"]["stream_status"] == "complete")
    assert not chat.snapshot(state_dir, state, ident)["session"]["queue"]


def test_successful_turn_drains_queue_in_order_and_withdraws_removed_item(tmp_path, monkeypatch):
    state_dir, state, ident = session(tmp_path)
    monkeypatch.setattr("theory_master.codex_backend.CodexBackend", EventBackend)
    EventBackend.hold = True
    chat.begin_send(state_dir, tmp_path, state, ident, {"content": "一"})
    wait_for(lambda: EventBackend.instances and EventBackend.instances[0].started.is_set())
    chat.queue_message(state_dir, tmp_path, state, ident, {"content": "二"})
    removed = chat.queue_message(state_dir, tmp_path, state, ident, {"content": "撤回"})
    chat.queue_message(state_dir, tmp_path, state, ident, {"content": "三"})
    chat.remove_queued_message(state_dir, ident, removed["queue_id"])
    EventBackend.hold = False
    EventBackend.instances[0].release.set()
    wait_for(lambda: len(EventBackend.instances) == 3 and chat.snapshot(state_dir, state, ident)["session"]["stream_status"] == "complete")
    row = chat.snapshot(state_dir, state, ident)["session"]
    assert [message["content"] for message in row["messages"] if message["role"] == "user"] == ["一", "二", "三"]
    assert not row["queue"]


def test_concurrent_queue_resumes_keep_one_reservation_and_fifo(tmp_path, monkeypatch):
    state_dir, state, ident = session(tmp_path)
    root = chat._store(state_dir)
    chat._runtime_update(root, ident, lambda data: data.update({"queue_paused": True}))
    first = chat.queue_message(state_dir, tmp_path, state, ident, {"content": "第一条"})
    chat.queue_message(state_dir, tmp_path, state, ident, {"content": "第二条"})
    entered, release = threading.Event(), threading.Event()
    calls, errors = [], []

    def fake_begin(state_dir, search_root, state, ident, payload):
        calls.append(payload["content"])
        row = chat._read(root, ident)
        chat._message(row, "user", payload["content"])
        row["messages"][-1]["queue_id"] = payload["queue_id"]
        row["active_run"] = chat._new_run(row, payload["content"], row["messages"][-1]["id"])
        chat._write(root, row)
        if payload["content"] == "第一条":
            # Admission has persisted a starting turn, but has not published
            # its worker yet. A second resume must neither recover nor steal it.
            entered.set()
            assert release.wait(3)
            assert chat._read(root, ident)["active_run"]["status"] == "starting"
        row["active_run"]["status"] = "complete"
        chat._write(root, row)
        return {"ok": True}

    monkeypatch.setattr(chat, "begin_send", fake_begin)

    def resume():
        try:
            chat.resume_queue(state_dir, tmp_path, state, ident)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=resume)
    worker.start()
    try:
        assert entered.wait(3)
        reservation = chat._runtime(root, ident)
        assert reservation["dispatch_item"]["id"] == first["queue_id"]
        result = chat.resume_queue(state_dir, tmp_path, state, ident)
        still_reserved = chat._runtime(root, ident)
        assert not result["started"]
        assert calls == ["第一条"]
        assert still_reserved["dispatch_token"] == reservation["dispatch_token"]
        assert still_reserved["dispatch_owner"] == reservation["dispatch_owner"]
        assert len(still_reserved["queue"]) == 1
        assert chat._read(root, ident)["active_run"]["status"] == "starting"
    finally:
        release.set()
        worker.join(timeout=4)
    assert not worker.is_alive() and not errors
    assert calls == ["第一条", "第二条"]
    runtime = chat._runtime(root, ident)
    assert not runtime["queue"] and not runtime["dispatching"]
    assert not any(key in runtime for key in ("dispatch_owner", "dispatch_item", "dispatch_token"))


@pytest.mark.parametrize("accepted_in", [None, "current", "branch", "legacy"])
def test_queue_resume_recovers_stale_owner_without_losing_or_replaying_item(tmp_path, monkeypatch, accepted_in):
    state_dir, state, ident = session(tmp_path)
    root = chat._store(state_dir)
    chat._runtime_update(root, ident, lambda data: data.update({"queue_paused": True}))
    first = chat.queue_message(state_dir, tmp_path, state, ident, {"content": "第一条"})
    chat.queue_message(state_dir, tmp_path, state, ident, {"content": "第二条"})
    if accepted_in in {"current", "branch"}:
        row = chat._read(root, ident)
        chat._message(row, "user", "第一条")
        row["messages"][-1]["queue_id"] = first["queue_id"]
        if accepted_in == "branch":
            chat._archive_branch(row, "已接受的旧版本")
            row["messages"] = []
        chat._write(root, row)

    def stale_reservation(data):
        data["dispatching"] = True
        if accepted_in != "legacy":
            data["dispatch_item"] = data["queue"].pop(0)
            # The PID exists, but belongs to another process generation: this
            # exercises restart recovery even when the OS has reused a PID.
            data["dispatch_owner"] = {"instance": "previous-server", "pid": chat.os.getpid(),
                                      "started": "previous-process-start"}
            data["dispatch_token"] = "old-reservation"

    chat._runtime_update(root, ident, stale_reservation)
    calls = []
    monkeypatch.setattr(chat, "begin_send", lambda *args: calls.append(args[-1]["content"]) or {"ok": True})
    chat.resume_queue(state_dir, tmp_path, state, ident)
    assert calls == (["第二条"] if accepted_in in {"current", "branch"} else ["第一条", "第二条"])
    runtime = chat._runtime(root, ident)
    assert not runtime["queue"] and not runtime["dispatching"]
    assert not any(key in runtime for key in ("dispatch_owner", "dispatch_item", "dispatch_token"))


def test_steer_acceptance_is_preserved_in_history(tmp_path, monkeypatch):
    state_dir, state, ident = session(tmp_path)
    monkeypatch.setattr("theory_master.codex_backend.CodexBackend", EventBackend)
    EventBackend.hold = True
    chat.begin_send(state_dir, tmp_path, state, ident, {"content": "初始问题"})
    wait_for(lambda: EventBackend.instances and EventBackend.instances[0].started.is_set())
    result = chat.steer(state_dir, tmp_path, state, ident, {"content": "补充约束"})
    assert result["steered"]
    assert EventBackend.instances[0].handle.steered == ["补充约束"]
    EventBackend.instances[0].release.set()
    wait_for(lambda: chat.snapshot(state_dir, state, ident)["session"]["stream_status"] == "complete")
    row = chat.snapshot(state_dir, state, ident)["session"]
    assert any(m.get("steered") and m["content"] == "补充约束" for m in row["messages"])


def test_options_roundtrip_validates_answers_and_unblocks_same_turn(tmp_path, monkeypatch):
    state_dir, state, ident = session(tmp_path)
    monkeypatch.setattr("theory_master.codex_backend.CodexBackend", EventBackend)
    EventBackend.ask = True
    chat.begin_send(state_dir, tmp_path, state, ident, {"content": "需要选择"})
    wait_for(lambda: chat.snapshot(state_dir, state, ident)["session"]["pending_questions"])
    with pytest.raises(chat.ResearchChatError):
        chat.answer_question(state_dir, ident, {"request_id": "question-1", "answers": {"wrong": ["A"]}})
    chat.answer_question(state_dir, ident, {"request_id": "question-1", "answers": {"method": ["B"]}})
    wait_for(lambda: chat.snapshot(state_dir, state, ident)["session"]["stream_status"] == "complete")
    assert EventBackend.instances[0].answer == {"answers": {"method": {"answers": ["B"]}}}
    assert not chat.snapshot(state_dir, state, ident)["session"]["pending_questions"]


def test_secret_options_are_delivered_once_without_persistent_plaintext(tmp_path, monkeypatch):
    state_dir, state, ident = session(tmp_path)
    monkeypatch.setattr("theory_master.codex_backend.CodexBackend", EventBackend)
    EventBackend.ask = EventBackend.secret = True
    chat.begin_send(state_dir, tmp_path, state, ident, {"content": "等待保密输入"})
    wait_for(lambda: chat.snapshot(state_dir, state, ident)["session"]["pending_questions"])
    secret = "do-not-persist-this-answer-82749"
    payload = {"request_id": "question-1", "answers": {"method": [secret]}}
    chat.answer_question(state_dir, ident, payload)
    wait_for(lambda: chat.snapshot(state_dir, state, ident)["session"]["stream_status"] == "complete")
    assert EventBackend.instances[0].answer["answers"]["method"]["answers"] == [secret]
    assert chat.answer_question(state_dir, ident, payload)["answered"]
    with pytest.raises(chat.ResearchChatError):
        chat.answer_question(state_dir, ident, {"request_id": "question-1", "answers": {"method": ["different"]}})
    assert secret not in chat._file(chat._store(state_dir), ident).read_text()
    assert secret not in chat._file(chat._store(state_dir), ident).with_suffix(".runtime").read_text()


def test_native_async_message_questions_continue_with_selected_user_answer(tmp_path, monkeypatch):
    state_dir, state, ident = session(tmp_path)
    monkeypatch.setattr("theory_master.codex_backend.CodexBackend", EventBackend)
    EventBackend.asynchronous = True
    chat.begin_send(state_dir, tmp_path, state, ident, {"content": "给出选项"})
    wait_for(lambda: chat.snapshot(state_dir, state, ident)["session"]["stream_status"] == "complete")
    pending = chat.snapshot(state_dir, state, ident)["session"]["pending_questions"]
    assert pending[0]["questions"][0]["options"][1]["label"] == "B"
    EventBackend.asynchronous = False
    chat.answer_question(state_dir, ident, {"request_id": pending[0]["request_id"], "answers": {"q-0": ["B"]}},
                         search_root=tmp_path, state=state)
    wait_for(lambda: len(EventBackend.instances) == 2 and chat.snapshot(state_dir, state, ident)["session"]["stream_status"] == "complete")
    assert "选择方法：B" in EventBackend.instances[-1].prompt


def test_edit_preserves_versions_and_fork_owns_copied_attachments(tmp_path, monkeypatch):
    state_dir, state, ident = session(tmp_path)
    attachment = store_upload(state_dir, ident, "notes.txt", "text/plain", b"Original evidence")
    chat.send(state_dir, tmp_path, state, ident, {"content": "旧问题", "attachment_ids": [attachment["id"]]}, backend=EventBackend())
    before = chat._read(chat._store(state_dir), ident)
    user = before["messages"][0]
    old_branch = before["current_branch_id"]
    monkeypatch.setattr("theory_master.codex_backend.CodexBackend", EventBackend)
    chat.begin_send(state_dir, tmp_path, state, ident, {"content": "新问题", "edit_message_id": user["id"]})
    wait_for(lambda: chat.snapshot(state_dir, state, ident)["session"]["stream_status"] == "complete")
    assert chat.snapshot(state_dir, state, ident)["session"]["messages"][0]["content"] == "新问题"
    chat.switch_branch(state_dir, ident, old_branch)
    assert chat.snapshot(state_dir, state, ident)["session"]["messages"][0]["content"] == "旧问题"
    fork = chat.fork_branch(state_dir, ident, {"message_id": user["id"]})["session_id"]
    assert fork != ident
    copied = chat.snapshot(state_dir, state, fork)["session"]
    assert copied["codex_thread_id"] is None
    new_attachment = copied["messages"][0]["attachments"][0]
    assert new_attachment["id"] != attachment["id"]
    assert f"/{fork}/" in new_attachment["url"]
    assert attachment_content(state_dir, fork, new_attachment["id"])[0] == b"Original evidence"


def test_image_only_send_reaches_backend_and_retry_keeps_attachment(tmp_path, monkeypatch):
    state_dir, state, ident = session(tmp_path)
    image = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(image, format="PNG")
    attachment = store_upload(state_dir, ident, "plot.png", "image/png", image.getvalue())
    first = EventBackend()
    chat.send(state_dir, tmp_path, state, ident, {"content": "", "attachment_ids": [attachment["id"]]}, backend=first)
    assert Path(first.images[0]).read_bytes() == image.getvalue()
    row = chat._read(chat._store(state_dir), ident)
    row["active_run"]["status"] = "error"
    chat._write(chat._store(state_dir), row)
    monkeypatch.setattr("theory_master.codex_backend.CodexBackend", EventBackend)
    chat.begin_retry(state_dir, tmp_path, state, ident)
    wait_for(lambda: len(EventBackend.instances) == 2 and chat.snapshot(state_dir, state, ident)["session"]["stream_status"] == "complete")
    assert EventBackend.instances[-1].images == first.images


def test_fork_text_followup_replays_prior_images_into_new_native_thread(tmp_path):
    state_dir, state, ident = session(tmp_path)
    image = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(image, format="PNG")
    attachment = store_upload(state_dir, ident, "diagram.png", "image/png", image.getvalue())
    chat.send(state_dir, tmp_path, state, ident, {"content": "分析图片", "attachment_ids": [attachment["id"]]}, backend=EventBackend())
    row = chat.snapshot(state_dir, state, ident)["session"]
    fork = chat.fork_branch(state_dir, ident, {"message_id": row["messages"][-1]["id"]})["session_id"]
    backend = EventBackend()
    chat.send(state_dir, tmp_path, state, fork, {"content": "继续分析上图"}, backend=backend)
    assert len(backend.images) == 1
    assert f"/{fork}/" in backend.images[0]
    assert Path(backend.images[0]).read_bytes() == image.getvalue()
    assert chat.snapshot(state_dir, state, fork)["session"]["active_run"]["context"]["image_replay_count"] == 1


def test_chat_event_projection_does_not_change_scientific_audit_or_expose_raw_reasoning(tmp_path):
    event = SimpleNamespace(method="item/reasoning/summaryTextDelta", payload={"itemId": "r", "delta": "公开摘要"})
    assert map_chat_notification(event)["text"] == "公开摘要"
    assert map_notification(event) is None
    raw = SimpleNamespace(method="item/reasoning/textDelta", payload={"delta": "private"})
    assert map_chat_notification(raw) is None
    command = SimpleNamespace(method="item/started", payload={"item": {"type": "commandExecution", "id": "c", "command": "python check.py"}})
    assert map_chat_notification(command)["status"] == "running"
    assert map_notification(command) is None
    usage = map_chat_notification({"method": "thread/tokenUsage/updated", "payload": {"tokenUsage": {
        "total": {"inputTokens": 14, "outputTokens": 2}, "last": {"inputTokens": 4, "outputTokens": 2},
        "modelContextWindow": 200000}}})
    assert usage["context_window"] == 200000 and usage["last_request_usage"]["input_tokens"] == 4
    failed = map_chat_notification({"method": "item/completed", "payload": {"item": {
        "id": "failed-command", "type": "commandExecution", "command": "false", "exitCode": 1}}})
    assert failed["status"] == "error"


def test_sdk_adapter_forwards_public_events_images_and_handle_without_model(tmp_path):
    received, handles, payloads = [], [], []
    class Handle:
        id = "fake-turn"
        def stream(self):
            yield SimpleNamespace(method="item/reasoning/summaryTextDelta", payload={"itemId": "r", "delta": "summary"})
            yield SimpleNamespace(method="turn/completed", payload={"turn": {"id": self.id, "status": "completed", "final_response": "answer"}})
    class Thread:
        def turn(self, payload):
            payloads.append(payload)
            return Handle()
    backend = CodexBackend(tmp_path)
    backend.threads["fake-thread"] = Thread()
    backend.ensure_thread = lambda *args: "fake-thread"
    assert backend.invoke("research_chat", "prompt", "fake-thread", "gpt-6-sol", "CodexHTTPS",
        on_event=received.append, on_handle=handles.append, input_images=["/tmp/fake.png"])[1] == "answer"
    assert any(event["type"] == "reasoning_summary" for event in received)
    assert handles[0].id == "fake-turn" and handles[-1] is None
    assert any(getattr(item, "path", None) == "/tmp/fake.png" for item in payloads[0])


def test_sdk_question_handler_restores_original_tool_approval_handler(tmp_path):
    delegated = []
    def approval(method, params):
        delegated.append(method)
        return {"decision": "accept"}
    inner = SimpleNamespace(_approval_handler=approval)
    replies = []
    class Handle:
        def stream(self):
            yield SimpleNamespace(method="turn/completed", payload={"turn": {"id": "t", "final_response": "done"}})
    class Thread:
        def turn(self, payload):
            replies.append(inner._approval_handler("item/tool/requestUserInput", {"questions": []}))
            inner._approval_handler("item/fileChange/requestApproval", {})
            return Handle()
    backend = CodexBackend(tmp_path)
    backend.client = SimpleNamespace(_client=inner)
    backend.threads["fake"] = Thread()
    backend.ensure_thread = lambda *args: "fake"
    backend.invoke("research_chat", "prompt", "fake", "gpt-6-sol", "CodexHTTPS",
                   request_user_input=lambda params: {"answers": {"q": {"answers": ["yes"]}}})
    assert replies == [{"answers": {"q": {"answers": ["yes"]}}}]
    assert delegated == ["item/fileChange/requestApproval"]
    assert inner._approval_handler is approval
