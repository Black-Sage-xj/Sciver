"""The source preparation chat continues with the real Task, without models."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tm_observer import research_chat as chat, research_preparation as prep
from tm_observer.collector import collect_state


PLAN = ("# Goal\n\nDistinguish the competing mechanisms.\n\n"
        "# Research Contract\n\nCompare independent observations and uncertainty.\n\n"
        "# Final Gate\n\nResolve the signed prediction with evidence.\n\n"
        "# Required Context\n\nExisting pilots remain unverified.\n")


@pytest.fixture
def created(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "data"))
    state_dir, search = tmp_path / "state", tmp_path / "workspace"
    search.mkdir()
    ident = chat.create(state_dir, {"runs": []}, {"mode": "preparation", "entry": "clear"})["session_id"]
    saved = prep.snapshot(state_dir, ident)
    saved = prep.update(state_dir, ident, {"expected_revision": saved["revision"], "draft": PLAN})
    materials = prep.workspace(state_dir, ident) / "pilot"
    materials.mkdir()
    (materials / "existing.csv").write_text("size,value\n4,0.3\n")
    receipt = chat.create_prepared_project(state_dir, search, ident,
                                          {"confirm": True, "expected_revision": saved["revision"]})
    return state_dir, search, ident, receipt, collect_state(search, include_git_info=False)


class Backend:
    def __init__(self):
        self.prompts = []
        self.threads = []

    def ensure_thread(self, role, thread, model, provider):
        self.threads.append(thread)
        return thread or "new-task-thread"

    def invoke(self, role, prompt, thread, model, provider):
        self.prompts.append(prompt)
        return thread, "请在当前项目控制台启动科研。"


def test_created_preparation_projects_task_binding_without_mutating_history(created):
    state_dir, _, ident, receipt, state = created
    path = chat._file(chat._store(state_dir), ident)
    before = path.read_bytes()
    view = chat.snapshot(state_dir, state, ident)
    row = view["session"]
    assert row["mode"] == "project" and row["task_id"] == receipt["observer_id"]
    assert row["preparation_id"] == ident and row["skill"] == "research-discussion"
    assert row["preparation"]["created_task"]["task_href"] == receipt["task_href"]
    assert next(item for item in view["sessions"] if item["id"] == ident)["mode"] == "project"
    assert path.read_bytes() == before
    assert not list(Path(receipt["research_root"]).glob("[0-9]*"))


def test_next_turn_uses_current_plan_workers_and_new_native_thread(created):
    state_dir, search, ident, receipt, state = created
    root = chat._store(state_dir)
    row = chat._read(root, ident)
    row.update(codex_thread_id="old-preparation-thread", agent_policy_version=chat._AGENT_POLICY_VERSION)
    chat._write(root, row)
    fake = Backend()
    chat.send(state_dir, search, state, ident,
              {"content": "那我们怎么开始科研呢", "skill": chat.PREPARATION_SKILL}, backend=fake)
    row = chat._read(root, ident)
    assert row["mode"] == "project" and row["task_id"] == receipt["observer_id"]
    assert fake.threads == [None]
    prompt = fake.prompts[0]
    assert "Loaded research chat skill: research-discussion" in prompt
    assert "Live Worker/scheduler identity check" in prompt
    assert "Distinguish the competing mechanisms" in prompt
    assert "Task console: " + receipt["task_href"] in prompt
    assert "There is no scientific Task" not in prompt
    assert "standalone pilot results are not Judge evidence" in prompt
    assert "Frozen preparation source navigation" in prompt
    assert str(Path(receipt["snapshot_path"]) / "materials") in prompt
    assert chat.preparation_state(state_dir, ident)["created_task"]["observer_id"] == receipt["observer_id"]
    with pytest.raises(chat.ResearchChatError, match="确认立项"):
        chat.update_preparation(state_dir, ident,
                                {"draft": PLAN, "expected_revision": prep.snapshot(state_dir, ident)["revision"]})
    # Retrying an already confirmed creation remains idempotent after transition.
    again = chat.create_prepared_project(state_dir, search, ident,
                                         {"confirm": True, "expected_revision": prep.snapshot(state_dir, ident)["revision"]})
    assert again["observer_id"] == receipt["observer_id"] and again["idempotent"]
    assert not list(Path(receipt["research_root"]).glob("[0-9]*"))


def test_stale_workspace_cannot_send_created_task_as_unbound_preparation(created):
    state_dir, search, ident, _, _ = created
    fake = Backend()
    with pytest.raises(chat.ResearchChatError, match="项目已创建，任务状态尚不可用"):
        chat.send(state_dir, search, {"runs": []}, ident, {"content": "开始科研"}, backend=fake)
    assert not fake.prompts


def test_transition_rejects_receipt_pointing_to_another_task(created):
    state_dir, search, ident, receipt, state = created
    path = prep.workspace(state_dir, ident) / "metadata.json"
    metadata = json.loads(path.read_text())
    metadata["created_task"]["research_root"] = str(search / "another-task")
    path.write_text(json.dumps(metadata))
    fake = Backend()
    with pytest.raises(chat.ResearchChatError, match="回执与当前任务绑定不一致"):
        chat.send(state_dir, search, state, ident, {"content": "启动"}, backend=fake)
    assert not fake.prompts
    assert not list(Path(receipt["research_root"]).glob("[0-9]*"))
