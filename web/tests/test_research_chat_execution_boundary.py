"""Deliver execution boundaries through real prompt assembly; never call models."""
from __future__ import annotations

from theory_master.skills import developer_instructions
from tm_observer import research_chat as chat
from tm_observer import research_preparation as prep


class RecordingBackend:
    def __init__(self):
        self.calls = []

    def ensure_thread(self, role, thread, model, provider):
        return thread or "execution-boundary-thread"

    def invoke(self, role, prompt, thread, model, provider):
        self.calls.append((role, prompt, thread))
        return thread, "先把检验和资源边界写成可审阅计划。"


def test_native_chat_identity_requires_plan_task_and_cycle_for_scientific_pilots():
    """Native threads receive this layer once, before selected per-turn skills."""
    instructions = developer_instructions("research_chat")
    assert "Numerical pilots, reproductions, parameter scans" in instructions
    assert "Research Chat does not perform Investigator work" in instructions
    assert "If an earlier assistant offered such a run, correct that proposal" in instructions
    assert "The intervention workflow operates on a verified existing Task" in instructions
    assert "Reading literature, explaining methods, viewing existing images/data" in instructions
    assert "/research-chat/?channel=system" in instructions
    assert "Do not repair application source or deploy services" in instructions
    assert "Carry out the operator’s requests using available tools, including editing files" not in instructions


def test_preparation_acceptance_and_followup_receive_boundary_on_fresh_and_resumed_turns(tmp_path):
    """An old bad suggestion stays visible as history, while current rules persist."""
    state_dir, state = tmp_path / "observer", {"runs": []}
    ident = chat.create(state_dir, state, {
        "mode": "preparation", "entry": "exploratory",
    })["session_id"]
    store = chat._store(state_dir)
    row = chat._read(store, ident)
    bad_proposal = "先用 Python 做一个小尺寸复现，不用先写计划或立项。"
    chat._message(row, "assistant", bad_proposal)
    chat._write(store, row)
    backend = RecordingBackend()

    chat.send(state_dir, tmp_path, state, ident, {"content": "好的"}, backend=backend)
    chat.send(state_dir, tmp_path, state, ident, {"content": "再补两档更大的尺寸"}, backend=backend)

    assert len(backend.calls) == 2
    for role, prompt, thread in backend.calls:
        assert role == "research_chat" and thread == "execution-boundary-thread"
        assert "Loaded research chat skill: research-preparation" in prompt
        assert "小尺寸复现、数值试算、原型实验、参数扫描" in prompt
        assert "不能在筹备目录用 shell/Python 直接试跑" in prompt
        assert "用户的“好的”不能使被跳过的前置步骤消失" in prompt
        assert "查看已有图片/数据" in prompt
        assert "不得在筹备科研对话中修改安装源码" in prompt
        assert "The selected card is an initial workflow" not in prompt
    assert bad_proposal in backend.calls[0][1]
    assert "Latest operator message (continue the existing thread)" in backend.calls[1][1]
    assert "再补两档更大的尺寸" in backend.calls[1][1]
    snapshot = prep.snapshot(state_dir, ident)
    assert snapshot["draft"] == "" and snapshot["created_task"] is None
    assert not (tmp_path / "projects").exists()


def test_project_discussion_and_intervention_share_existing_task_boundary():
    """Changing the selected skill cannot turn Chat into an Investigator."""
    for skill in ("research-discussion", "research-intervention"):
        prompt = chat._skill_context({"mode": "project", "skill": skill})
        assert "research-preparation/SKILL.md" in prompt
        assert "SDK Cycle" in prompt
        assert "直接运行" in prompt
        assert ("科研脚本" if skill == "research-discussion" else "实验脚本") in prompt
        assert "已有数据" in prompt
