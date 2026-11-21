import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tm_observer.http_page_routes import get_page
from tm_observer.review_trace import project
from tm_observer.review_trace_ui import as_html, as_text


def write_log(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.fixture
def run(tmp_path):
    cycle = tmp_path / "1"
    cycle.mkdir()
    rows = []

    def emit(event, role="investigator", **fields):
        rows.append(dict(event=event, role=role, ts=f"2026-01-01T09:00:{len(rows):02d}Z", **fields))

    emit("AGENT_INPUT", stage="thread_initialization", input_kind="developer_instructions", content="init")
    emit("AGENT_STARTED", invocation_id="inv-1", stage="investigate", prompt_chars=2)
    emit("AGENT_INPUT", stage="investigate", input_kind="native_goal_setup_turn", content="setup")
    emit("AGENT_INPUT", stage="investigate", input_kind="native_goal_objective", content="objective")
    emit("LLM_MESSAGE", message_role="user", content="private user echo")
    emit("LLM_MESSAGE", content="earlier observation")
    emit("TOOL_ACTIVITY", detail="command log")
    emit("TOKEN_USAGE", tokens_in=99999)
    emit("LLM_MESSAGE", content="latest observation")
    result = {"findings": [{"statement": "<main> test", "result": "retained"}]}
    emit("LLM_MESSAGE", content=json.dumps(result, indent=2))
    emit("AGENT_TURN_COMPLETED", invocation_id="inv-1", output=result)
    emit("AGENT_STARTED", "judge", invocation_id="inv-2", stage="terminal_assessment")
    emit("LLM_MESSAGE", "judge", content='{"verdict":"open"} trailing note')
    emit("JUDGE_COMPLETED", "judge", invocation_id="inv-2", output={"verdict": "open"})
    emit("AGENT_INPUT", "judge", stage="thread_initialization", input_kind="developer_instructions", content="later init")
    emit("AGENT_STARTED", "judge", invocation_id="inv-3", stage="goal_completion")
    write_log(cycle / "runtime_events.jsonl", rows)
    write_log(cycle / "invocations.jsonl", [])
    write_log(cycle / "events.jsonl", [
        {"type": "role.completed", "at": rows[10]["ts"], "payload": {"invocation_id": "inv-1", "output": result}},
        {"type": "investigation.routing", "at": rows[10]["ts"], "payload": {
            "selected_executor": "judge", "decision": "send_to_judge"}},
    ])
    (cycle / "cost_summary.json").write_text(json.dumps({
        "usage": {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 40},
        "cost": {"amount": 0.5, "currency": "USD", "source": "estimated", "confidence": "partial",
                 "model_calls": 2, "priced_model_calls": 1}}))
    return {"research_root": str(tmp_path), "cycle": "1", "observer_id": "a" * 16, "task_name": "示例任务"}


def test_compact_text_is_grounded_and_source_free_until_requested(run):
    trace = project(run)
    text = as_text(trace)
    assert trace["stages"][1]["state"] == "completed"
    assert text.count("statement · <main> test") == 1
    assert text.count("交接 ") == 1 and "Investigator → Judge · send_to_judge" in text
    assert "模型附文｜trailing note" in text
    assert "阶段输入 14 字符" in text
    assert "输入 100 / 输出 20" in text and "仅 1/2 已计价" in text
    for omitted in ("earlier observation", "command log", "99999", "private user echo", "objective", "later init"):
        assert omitted not in text and omitted not in as_html(trace)
    assert "&lt;main&gt; test" in as_html(trace)
    detail = as_text(project(run, stage_id="inv-1", view="output"))
    assert "earlier observation" in detail and detail.count("retained") == 1
    prompts = as_text(project(run, stage_id="inv-1", view="prompt"))
    assert "developer_instructions · 4 字符｜init" in prompts
    assert "native_goal_objective · 9 字符｜objective" in prompts
    assert "later init" not in as_text(project(run, stage_id="inv-2", view="prompt"))
    assert "later init" in as_text(project(run, stage_id="inv-3", view="prompt"))


def test_empty_and_paging_are_cycle_scoped(run):
    root = Path(run["research_root"])
    (root / "2").mkdir()
    (root / "3").symlink_to(root / "1", target_is_directory=True)
    for value in ("../1", "3", "99"):
        with pytest.raises(ValueError):
            project(run, cycle=value)
    empty = as_text(project(run, cycle="2"))
    assert "未计价" in empty and "尚无阶段记录" in empty
    with pytest.raises(ValueError):
        project(run, cycle="2", stage_id="inv-1", view="prompt")
    rows = [{"event": "AGENT_STARTED", "role": "judge", "invocation_id": f"stage-{i}",
             "stage": f"pass-{i}"} for i in range(10)]
    write_log(root / "2" / "runtime_events.jsonl", rows)
    first = as_text(project(run, cycle="2"))
    assert "stage-7" in first and "stage-8" not in first and "page=2&format=txt" in first
    assert "stage-9" in as_text(project(run, cycle="2", page=2))
    with pytest.raises(ValueError):
        project(run, cycle="2", page=3)


@pytest.mark.parametrize(("query", "expected_type", "code"), [
    ("cycle=1&format=txt", "text/plain", 200),
    ("cycle=1&stage=inv-1&view=prompt", "text/html", 200),
    ("cycle=100", "text/plain", 404),
    ("cycle=1&page=bad", "text/plain", 404),
])
def test_route_uses_scoped_task_and_requested_representation(run, query, expected_type, code):
    class Handler:
        def _send(self, data, content_type, status=200, **kwargs):
            self.response = (data, content_type, status)
    handler = Handler()
    route = "/run/" + run["observer_id"] + "/"
    handler.path = route + "?tab=trace&" + query
    services = SimpleNamespace(_page=lambda title, body, **kwargs: body, _console_cookie=lambda ident: "")
    assert get_page(handler, route, {"runs": [run]}, None, services)
    body, content_type, status = handler.response
    assert content_type.startswith(expected_type) and status == code
    if expected_type == "text/html":
        assert b"data-trace-text" in body
    elif code == 200:
        assert b"<script" not in body and "Investigator".encode() in body
    assert get_page(handler, route, {"runs": []}, None, services)
    assert handler.response[-1] == 404
