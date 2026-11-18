"""Actual workflow/context delivery with fake backends; no model or Cycle runs."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from theory_master.codex_backend import CodexBackend
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.skills import developer_instructions, role_skills
from tm_observer import research_chat as chat


class PersistentBackend:
    def __init__(self):
        self.calls = []

    def ensure_thread(self, role, thread, model, provider):
        return thread or "fake-chat-thread"

    def invoke(self, role, prompt, thread, model, provider):
        self.calls.append((prompt, thread))
        return thread, "建议先核对两条路径的证据，然后由你决定是否执行。"


@pytest.fixture
def workspace(tmp_path):
    task_root, research = tmp_path / "project", tmp_path / "runtime"
    task_root.mkdir()
    research.mkdir()
    plan = task_root / "research_plan.md"
    plan.write_text("# Goal\n\nCompare mechanisms.\n\n# Research Contract\n\nTest claims.\n\n# Final Gate\n\nIndependent evidence.\n\n# Required Context\n\nRead sources.\n\n# Scientific Model\n\nUnknown.\n", encoding="utf-8")
    database = task_root / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Canonical path", summary="Existing mechanism")
        store.add_hypothesis("H-0002", title="Closed path", summary="Alternative mechanism")
        store.set_frontier(branch_id="frontier:H-0002", hypothesis_id="H-0002", status="closed", reason="human routing decision")
    (task_root / "theory-master.config.json").write_text(json.dumps({
        "plan_path": str(plan), "graph_database": str(database),
        "research_root": str(research), "branch_canvas": str(task_root / "exploration.canvas"),
    }), encoding="utf-8")
    # A stale Canvas must not be mistaken for canonical scientific state.
    (task_root / "exploration.canvas").write_text(json.dumps({"nodes": [{
        "id": "H-9999", "text": "type: Hypothesis\nfrontier_status: active\nSTALE_CANVAS_ONLY"}], "edges": []}), encoding="utf-8")
    state = {"runs": [{"observer_id": "a" * 16, "task_root": str(task_root), "research_root": str(research)}]}
    return tmp_path, tmp_path / "observer", state, database


def test_selected_skill_is_loaded_on_first_and_resumed_turn(workspace):
    root, state_dir, state, database = workspace
    graph_before = database.read_bytes()
    plan = Path(state["runs"][0]["task_root"]) / "research_plan.md"
    plan_before = plan.read_bytes()
    ident = chat.create(state_dir, state, {"mode": "frontier", "task_id": "a" * 16, "skill": "research-progress"})["session_id"]
    assert chat.snapshot(state_dir, state, ident)["session"]["messages"] == []
    backend = PersistentBackend()
    chat.send(state_dir, root, state, ident, {"content": "当前几条路径分别卡在哪里？"}, backend=backend)
    chat.send(state_dir, root, state, ident, {"content": "执行你刚才的具体建议，但保留第二条路径"}, backend=backend)
    row = chat.snapshot(state_dir, state, ident)["session"]
    assert row["skill"] == "research-progress"
    for prompt, _ in backend.calls:
        assert "Loaded research chat skill: research-progress" in prompt
        assert "逐条说明每条主要路径研究了什么" in prompt
        assert "解释已经失败的尝试及失败原因" in prompt
        assert "load research-intervention/SKILL.md" in prompt
        assert "A progress/discussion recommendation is not authorization" in prompt
        assert "do not ask for duplicate confirmation" in prompt
    assert "执行你刚才的具体建议，但保留第二条路径" in backend.calls[1][0]
    assert backend.calls[1][1] == "fake-chat-thread"
    assert "Canonical SQLite science graph" in backend.calls[0][0]
    assert "Canonical path" in backend.calls[0][0]
    assert "H-0002" in backend.calls[0][0]
    assert "STALE_CANVAS_ONLY" not in backend.calls[0][0]
    assert f"Workspace search_root: {root}" in backend.calls[0][0]
    assert database.read_bytes() == graph_before
    assert plan.read_bytes() == plan_before
    assert list(Path(state["runs"][0]["research_root"]).iterdir()) == []


def test_explicit_skill_switch_is_persisted_and_loads_real_workflow(workspace):
    root, state_dir, state, _ = workspace
    ident = chat.create(state_dir, state, {"mode": "project", "task_id": "a" * 16})["session_id"]
    backend = PersistentBackend()
    chat.send(state_dir, root, state, ident, {"content": "将第一条路径暂停", "skill": "research-intervention"}, backend=backend)
    chat.send(state_dir, root, state, ident, {"content": "说明变更后的状态"}, backend=backend)
    assert chat.snapshot(state_dir, state, ident)["session"]["skill"] == "research-intervention"
    for prompt, _ in backend.calls:
        assert "record_conjecture" in prompt
        assert "set_frontier" in prompt
        assert "不能预创建一个看似已资格通过的 H" in prompt
        assert "Preferred 和 `--preferred-hypothesis` 不会定向调度" in prompt


@pytest.mark.parametrize("skill", chat.CHAT_SKILLS)
def test_every_real_skill_body_reaches_fresh_and_resumed_turns(workspace, skill):
    """Check delivery of rules, not whether a real model follows them."""
    root, state_dir, state, _ = workspace
    refs = {ref.directory: ref for ref in role_skills("research_chat")}
    body = refs[skill].skill_md.read_text(encoding="utf-8")
    ident = chat.create(state_dir, state, {
        "mode": "project", "task_id": "a" * 16, "skill": skill,
    })["session_id"]
    backend = PersistentBackend()
    chat.send(state_dir, root, state, ident, {
        "content": "先解释这个结果的证据范围，暂不提出操作。",
    }, backend=backend)
    acceptance = "仅执行第 2 项的计划更正，保留第 1 项路径，不启动研究。"
    chat.send(state_dir, root, state, ident, {"content": acceptance}, backend=backend)

    assert len(backend.calls) == 2
    for prompt, thread in backend.calls:
        assert body in prompt
        assert f"Source: {refs[skill].skill_md}" in prompt
        assert "Scientific Model" in prompt
        assert "1、2、3" in prompt
        assert "不机械" in prompt
        assert "A card click or a request to discuss is not acceptance" in prompt
        assert thread == "fake-chat-thread"
    assert acceptance in backend.calls[1][0]
    assert "Latest operator message (continue the existing thread)" in backend.calls[1][0]
    assert "仅执行第 2 项" not in backend.calls[0][0]


@pytest.mark.parametrize("value", ["../../other", "research-lead", {}, 1, ""])
def test_unlisted_skill_cannot_create_or_change_session(workspace, value):
    root, state_dir, state, _ = workspace
    with pytest.raises(chat.ResearchChatError):
        chat.create(state_dir, state, {"mode": "project", "task_id": "a" * 16, "skill": value})
    ident = chat.create(state_dir, state, {"mode": "project", "task_id": "a" * 16})["session_id"]
    backend = PersistentBackend()
    with pytest.raises(chat.ResearchChatError):
        chat.send(state_dir, root, state, ident, {"content": "read invalid skill", "skill": value}, backend=backend)
    assert not backend.calls
    assert chat.snapshot(state_dir, state, ident)["session"]["messages"] == []


def test_migration_keeps_its_workflow_without_research_card(workspace):
    _, state_dir, state, _ = workspace
    ident = chat.create(state_dir, state, {"mode": "migration"})["session_id"]
    row = chat.snapshot(state_dir, state, ident)["session"]
    assert row["skill"] is None
    assert "绝对目录路径" in row["messages"][0]["content"]
    with pytest.raises(chat.ResearchChatError):
        chat.create(state_dir, state, {"mode": "migration", "skill": "research-progress"})


def test_missing_binding_or_broken_database_does_not_invent_context_or_block_chat(workspace):
    root, state_dir, state, database = workspace
    database.write_bytes(b"not a sqlite database")
    ident = chat.create(state_dir, state, {"mode": "project", "task_id": "a" * 16})["session_id"]
    backend = PersistentBackend()
    chat.send(state_dir, root, state, ident, {"content": "解释当前信息缺口"}, backend=backend)
    assert '"status": "database_error"' in backend.calls[0][0] or "Canonical SQLite graph unavailable" in backend.calls[0][0]
    assert "STALE_CANVAS_ONLY" not in backend.calls[0][0]
    config = Path(state["runs"][0]["task_root"]) / "theory-master.config.json"
    config.unlink()
    chat.send(state_dir, root, state, ident, {"content": "现在配置也缺失了"}, backend=backend)
    assert '"binding_valid": false' in backend.calls[1][0]
    assert "Inspect the binding before task writes" in backend.calls[1][0]


def test_chat_handbook_and_skill_catalog_reach_actual_sdk_developer_prompt(tmp_path):
    refs = role_skills("research_chat")
    assert {ref.directory for ref in refs} == set(chat.CHAT_SKILLS) | {chat.PREPARATION_SKILL}
    sdk = SimpleNamespace(Sandbox=SimpleNamespace(full_access="full", workspace_write="write", read_only="read"))
    backend = CodexBackend(tmp_path)
    client = Mock()
    client.thread_start.return_value = SimpleNamespace(id="new-chat-thread")
    with patch.dict("sys.modules", {"openai_codex": sdk}), patch.object(backend, "_ensure_client", return_value=client):
        backend.ensure_thread("research_chat", None, "model", "provider")
    prompt = client.thread_start.call_args.kwargs["developer_instructions"]
    assert "Research Chat runtime AGENTS.md" in prompt
    assert "Sciver Research Chat agent handbook" in prompt
    assert "scientific verdict" in prompt
    assert "qualified by Lead/Challenger before formal H allocation" in prompt
    assert "do not require repeated acceptance" in prompt
    assert "When the conversation has reached a concrete executable proposal" in prompt
    assert "Do not manufacture actions or ask for confirmation after every explanatory reply" in prompt
    assert "Acceptance of plan edits alone does not authorize research start/resume" in prompt
    assert "one use at the next fresh `frontier_proposal`" in prompt
    assert "Human edits do not change graph facts, Judge verdicts, Hypothesis locks, or Goal completion" in prompt
    assert "Do not infer execution authorization from selecting an object, quoting text, or opening Chat" in prompt
    assert "Answer against the accepted numbered items" in prompt
    assert "If one item fails, preserve and report earlier successful items" in prompt
    for ref in refs:
        assert str(ref.skill_md) in prompt
    assert "Research Chat runtime AGENTS.md" not in developer_instructions("judge")
