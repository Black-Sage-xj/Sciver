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
    assert "reviewed plan, formal Task and authorized SDK Cycle" in instructions
    assert "verified Task" in instructions and "research-intervention" in instructions
    assert "card clicks do not authorize edits" in instructions
    assert "/research-chat/?channel=system" in instructions
    assert "research_preparation.create_project" not in instructions
    assert "record_conjecture" not in instructions


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
        assert "research_preparation.create_project" in prompt
        assert "reviewed_revision" in prompt
        assert "原题、公开附件和评分说明" in prompt
    assert bad_proposal in backend.calls[0][1]
    assert "Latest operator message (continue the existing thread)" in backend.calls[1][1]
    assert "再补两档更大的尺寸" in backend.calls[1][1]
    snapshot = prep.snapshot(state_dir, ident)
    assert snapshot["draft"] == "" and snapshot["created_task"] is None
    assert not (tmp_path / "projects").exists()


def test_project_discussion_and_intervention_share_existing_task_boundary():
    """Changing the selected skill cannot turn Chat into an Investigator."""
    instructions = developer_instructions("research_chat")
    assert "authorized SDK Cycle" in instructions
    discussion = chat._skill_context({"mode": "project", "skill": "research-discussion"})
    intervention = chat._skill_context({"mode": "project", "skill": "research-intervention"})
    assert "已有数据" in discussion
    assert "record_conjecture" in intervention and "record_conjecture" not in discussion
