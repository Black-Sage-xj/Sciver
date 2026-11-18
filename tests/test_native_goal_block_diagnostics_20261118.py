"""Offline regressions for public blocked-Goal reports and incident provenance."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from openai_codex.generated.v2_all import (
    ItemCompletedNotification, ThreadGoalStatus, ThreadItem, Turn,
    TurnCompletedNotification, TurnStatus,
)
from openai_codex.models import Notification, UnknownNotification

from theory_master.native_goal import run_native_goal
from theory_master.supervisor_incident import engineering_evidence


def _item(text, *, thread="thread-1", turn="logical-1"):
    return Notification("item/completed", ItemCompletedNotification(
        completed_at_ms=1, thread_id=thread, turn_id=turn,
        item=ThreadItem.model_validate({"type": "agentMessage", "id": "report", "text": text}),
    ))


def _complete(items=(), *, thread="thread-1", turn="logical-1"):
    return Notification("turn/completed", TurnCompletedNotification(
        thread_id=thread, turn=Turn(id=turn, items=list(items), status=TurnStatus.completed),
    ))


def _run(events):
    state = SimpleNamespace(status=ThreadGoalStatus.blocked)
    class Stream:
        def __init__(self, **kwargs):
            pass
        def __iter__(self):
            return iter(events)
        def close(self):
            pass
    client = SimpleNamespace(
        start_goal_operation=lambda thread, objective: (state, "logical-1"),
        next_goal_notification=lambda state: None,
        unregister_goal_operation=lambda state: None,
        cancel_goal_operation=lambda state: pytest.fail("unexpected Goal cancellation"),
    )
    with patch("openai_codex._goal._GoalNotificationStream", Stream):
        return run_native_goal(SimpleNamespace(id="thread-1", _client=client), "frozen test")


def test_native_goal_retains_full_completed_public_report_with_official_sdk_types():
    report = "The frozen test has one/zero eligible transitions. " + "Evidence. " * 700
    result = _run([_item("Earlier public progress"), _item(report), _complete()])
    assert result.status == "blocked"
    assert result.final_response == report
    assert len(result.final_response) > 1800  # clipping belongs to the incident projection


def test_native_goal_uses_terminal_turn_items_without_item_completed_notification():
    final = ThreadItem.model_validate({"type": "agentMessage", "id": "final", "text": "Need source input"})
    result = _run([_complete([final])])
    assert result.final_response == "Need source input"


def test_native_goal_unknown_transport_envelope_excludes_non_assistant_and_reasoning():
    def unknown(item):
        return Notification("item/completed", UnknownNotification({
            "threadId": "thread-1", "turnId": "logical-1", "item": item,
        }))
    events = [unknown({"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": "Public "}, {"type": "output_text", "text": "report"},
    ]})]
    for role in ("user", "developer", "system"):
        events.append(unknown({"type": "message", "role": role,
                               "content": [{"type": "input_text", "text": "Input secret"}]}))
    events += [unknown({"type": "reasoning", "text": "Private reasoning"}),
               unknown({"type": "commandExecution", "text": "Tool output"}), _complete()]
    assert _run(events).final_response == "Public report"


def test_native_goal_report_is_bound_to_thread_and_logical_operation():
    result = _run([_item("Current report"), _item("Other thread", thread="other"),
                   _item("Prior Goal", turn="prior-goal"),
                   _complete([ThreadItem.model_validate({
                       "type": "agentMessage", "id": "other", "text": "Foreign terminal report",
                   })], turn="other-goal"), _complete()])
    assert result.final_response == "Current report"


def _write(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.fixture
def incident(tmp_path):
    # Minimal, verbatim-shaped Cycle 14 thermal records: no Finding was submitted.
    start = "2026-11-18T22:51:34+08:00"
    finish = "2026-11-18T22:56:32+08:00"
    invocation = "inv-14-investigator-b25609335197"
    thread = "01a1ef24-45c9-77f1-9ca6-5a4ca73b00ab"
    state = {
        "active_invocation_id": invocation, "status": "failed", "phase": "failed",
        "native_goal": {"status": "blocked", "thread_id": thread, "reported_block_reason": ""},
        "recovery": {"action": "error", "kind": "NativeGoalRuntimeFailure"},
    }
    events = [
        {"type": "role.started", "at": start, "payload": {
            "role": "investigator", "invocation_id": invocation, "thread_id": thread}},
        {"type": "run.failed", "at": finish, "payload": {"error_kind": "NativeGoalRuntimeFailure"}},
    ]
    report = {
        "ts": finish, "event": "LLM_MESSAGE", "role": "investigator", "message_role": "assistant",
        "thread_id": thread, "turn_id": "01a1ef24-8817-7b73-8293-b4ccbf400e2a",
        "content": "Eligibility remains one nighttime transition in the first file and none in the second. "
                   "I’ve marked the Goal blocked after the same input limitation persisted across three goal turns. "
                   "No Finding or handoff was submitted.",
    }
    _write(tmp_path / "events.jsonl", events)
    _write(tmp_path / "runtime_events.jsonl", [report])
    return tmp_path, state, events, report


def test_thermal_historical_block_recovers_public_report_and_provenance_without_state_change(incident):
    cycle, state, _, report = incident
    before = json.dumps(state, sort_keys=True)
    bundle = engineering_evidence(cycle, state)
    assert bundle["reported_native_block"] == report["content"]
    source = bundle["reported_native_block_source"]
    assert source["source"] == "runtime_events.jsonl/LLM_MESSAGE"
    assert source["kind"] == "public_assistant_report"
    assert source["invocation_id"] == state["active_invocation_id"]
    assert source["thread_id"] == report["thread_id"]
    assert source["turn_id"] == report["turn_id"]
    assert source["at"] == report["ts"]
    assert json.dumps(state, sort_keys=True) == before
    assert not {"action", "verdict", "resume_authorized"} & bundle.keys()


def test_historical_fallback_excludes_other_threads_roles_invocations_and_post_failure(incident):
    cycle, state, events, report = incident
    stale = dict(report, ts="2026-11-18T22:51:33+08:00", content="Prior invocation")
    excluded = [dict(report, thread_id="foreign", content="Other thread"),
                dict(report, role="judge", content="Other role"),
                dict(report, invocation_id="other", content="Other invocation"),
                dict(report, message_role="user", content="User text"),
                dict(report, event="REASONING", content="Private text"),
                dict(report, ts="2026-11-18T22:56:33+08:00", content="After failure")]
    _write(cycle / "runtime_events.jsonl", [stale, report, *excluded])
    assert engineering_evidence(cycle, state)["reported_native_block"] == report["content"]
    # A later invocation on the same thread does not change this failed invocation's binding.
    events.append({"type": "role.started", "at": "2026-11-18T22:57:00+08:00", "payload": {
        "role": "investigator", "invocation_id": "later", "thread_id": report["thread_id"]}})
    _write(cycle / "events.jsonl", events)
    _write(cycle / "runtime_events.jsonl", [report, dict(report, ts="2026-11-18T22:57:01+08:00",
                                                       content="Later invocation"),
        dict(report, ts="2026-11-18T22:57:02+08:00", event="LLM_FAILED", error="Later failure")])
    bundle = engineering_evidence(cycle, state)
    assert bundle["incident"]["invocation_id"] == state["active_invocation_id"]
    assert bundle["reported_native_block"] == report["content"]
    assert bundle["incident"]["last_terminal_error"] is None


@pytest.mark.parametrize("missing", ["events", "invocation", "thread", "assistant", "blocked"])
def test_historical_fallback_requires_complete_binding(incident, missing):
    cycle, state, events, report = incident
    if missing == "events":
        events = []
    elif missing == "invocation":
        state["active_invocation_id"] = "unrecorded-invocation"
    elif missing == "thread":
        state["native_goal"]["thread_id"] = "mismatched-thread"
    elif missing == "assistant":
        report.pop("message_role")
    else:
        state["native_goal"]["status"] = "complete"
    _write(cycle / "events.jsonl", events)
    _write(cycle / "runtime_events.jsonl", [report])
    bundle = engineering_evidence(cycle, state)
    assert bundle["reported_native_block"] == ""
    assert bundle["reported_native_block_source"] == {}


@pytest.mark.parametrize("persisted", [False, True])
def test_block_reports_are_redacted_and_bounded_from_both_sources(incident, persisted):
    cycle, state, _, report = incident
    text = "password=hidden api_key='key-hidden' Bearer bearer-hidden " + "detail " * 1000
    if persisted:
        state["native_goal"]["reported_block_reason"] = text
    else:
        _write(cycle / "runtime_events.jsonl", [dict(report, content=text)])
    bundle = engineering_evidence(cycle, state)
    result = bundle["reported_native_block"]
    assert "hidden" not in result
    assert "[redacted]" in result
    assert len(result) <= 1800
    if persisted:
        assert bundle["reported_native_block_source"] == {"source": "native_goal.reported_block_reason"}
