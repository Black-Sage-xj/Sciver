"""Host orchestration waits without a parent model or implicit scholarly writes."""
from pathlib import Path
import pytest
from tm_observer.research_chat_literature_flow import explicit_literature_request, prepare_literature


@pytest.mark.parametrize("text, expected", [("我对于高分子的拓扑和力学感兴趣,搜搜文献看看.", True),
    ("搜索一下相关论文", True), ("find recent papers on polymer fracture", True),
    ("不要搜文献，先解释概念", False), ("请解释这篇文献中的公式", False),
    ("目前研究进展如何", False)])
def test_explicit_scope(text, expected):
    assert explicit_literature_request(text) is expected


def test_host_delegation_uses_stable_job_and_awaits_report_before_chat(tmp_path, monkeypatch):
    from tm_observer import research_chat_literature as literature
    calls, events = [], []
    def run(state, search, sid, payload, **kwargs):
        calls.append(payload)
        kwargs["on_event"]({"event": "literature.planning_started"})
        kwargs["on_event"]({"event": "literature.job_finished"})
        return {"status": "completed", "source_count": 4, "source_access_counts": {"full_text": 2},
                "report_href": "/report", "agent_turns": 3}
    monkeypatch.setattr(literature, "run_study", run)
    monkeypatch.setattr(literature, "read_report", lambda *args: "# Real material from fake transport")
    row = {"id": "a" * 32, "mode": "preparation", "active_run": {"id": "run-1"},
           "messages": [{"role": "user", "content": "搜搜文献看看"}]}
    receipt = prepare_literature(tmp_path, tmp_path, row, on_event=events.append)
    again = prepare_literature(tmp_path, tmp_path, row)
    assert all("scope" not in payload for payload in calls)
    assert calls[0]["job_id"] == calls[1]["job_id"] == receipt["job_id"]
    assert receipt["source_access_counts"]["full_text"] == 2
    assert receipt["report_markdown"].startswith("# Real material")
    assert events[0]["status"] == "running" and events[-1]["status"] == "complete"
    assert prepare_literature(tmp_path, None, row) is None


def test_completed_receipt_reaches_chat_without_retrieving_again(tmp_path, monkeypatch):
    from tm_observer import research_chat as chat
    from tm_observer import research_chat_literature as literature

    state = {"runs": []}
    ident = chat.create(tmp_path, state, {"mode": "preparation", "entry": "clear"})["session_id"]
    jobs, prompts = [], []

    def run(_state, _root, _session, payload, **kwargs):
        jobs.append(payload["job_id"])
        return {"status": "completed", "report_href": "/test-report", "source_count": 1}

    monkeypatch.setattr(literature, "run_study", run)
    monkeypatch.setattr(literature, "read_report", lambda *args: "A finding from the requested study.")

    class Backend:
        def invoke(self, role, prompt, thread, model, provider):
            prompts.append(prompt)
            return "offline-thread", "已阅读实际报告。"

    chat.send(tmp_path, tmp_path, state, ident, {"content": "请搜索相关论文"}, backend=Backend())
    assert len(jobs) == len(prompts) == 1
    assert jobs[0] in prompts[0] and "/test-report" in prompts[0]
    assert "A finding from the requested study." in prompts[0]
    receipt_instruction = prompts[0].split("Literature job receipt for this request.", 1)[1].split("\n", 1)[0]
    assert "research-literature" in receipt_instruction and "report_href" in receipt_instruction


def test_contextual_followups_and_selected_project_stay_with_chat(tmp_path, monkeypatch):
    from tm_observer import research_chat_literature as literature
    monkeypatch.setattr(literature, "run_study", lambda *args, **kwargs: pytest.fail("context must reach Chat first"))
    row = {"id": "a" * 32, "mode": "preparation", "active_run": {"id": "run-2"},
        "messages": [{"role": "user", "content": "我关心环链弹性"},
                     {"role": "user", "content": "针对上面搜搜文献看看"}]}
    assert prepare_literature(tmp_path, tmp_path, row) is None
    row.update(mode="project", messages=[{"role": "user", "content": "搜搜文献看看"}])
    assert prepare_literature(tmp_path, tmp_path, row) is None


def test_retry_carries_originating_literature_job(tmp_path, monkeypatch):
    from tm_observer import research_chat as chat
    from tm_observer import research_preparation
    state = {"runs": []}
    ident = chat.create(tmp_path, state, {"mode": "preparation", "entry": "exploratory"})["session_id"]
    row = chat._read(chat._store(tmp_path), ident)
    row["messages"] = [{"id": "original-user", "role": "user", "content": "搜搜高分子力学文献"}]
    row["active_run"] = {"id": "original-run", "status": "error", "content": "搜搜高分子力学文献",
                         "user_message_id": "original-user", "literature_job_id": "b" * 32}
    chat._write(chat._store(tmp_path), row)
    called = []
    monkeypatch.setattr(chat, "begin_send", lambda *args: called.append(args[-1]) or {"ok": True})
    chat.begin_retry(tmp_path, tmp_path, state, ident)
    assert called[0]["_retry_literature_job_id"] == "b" * 32
