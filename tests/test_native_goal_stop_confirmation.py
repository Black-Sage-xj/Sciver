"""Real SDK cancellation plus a local RPC model of queued automatic Turns."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace as NS

import pytest
from openai_codex._goal import _GoalOperationState
from openai_codex.client import CodexClient
from openai_codex.errors import InvalidRequestError
from openai_codex.generated.v2_all import ThreadGoalStatus

from theory_master.native_goal_recovery import goal_identity
from theory_master.native_goal_stop import NativeGoalStopUnconfirmed, cancel_owned_goal


class Server(CodexClient):
    """Use installed cancel code; replace only its server I/O."""
    def __init__(self):
        self.goal = NS(thread_id="owned", objective="frozen science", created_at=7,
                       updated_at=8, status=ThreadGoalStatus.active, token_budget=None,
                       tokens_used=19, time_used_seconds=3)
        self.active = None
        self.calls = []
        self.reads = 0
        self.pause_broken = False
        self.spawn_after_cancel = False
        self.spawn_on_read = None
        self.foreign_on_read = None
        self.bad_thread = False
        self.interrupt_broken = False
        self.race_interrupt_once = False

    def get_goal(self, _thread_id):
        return copy.deepcopy(self.goal)

    def pause_goal(self, thread_id):
        self.calls.append(("pause", thread_id))
        if self.pause_broken:
            raise RuntimeError("pause unavailable")
        self.goal.status = ThreadGoalStatus.paused

    def cancel_goal_operation(self, state):
        self.calls.append(("official_cancel", state.thread_id))
        super().cancel_goal_operation(state)
        # The continuation was queued before pause but absent from the cursor.
        if self.spawn_after_cancel:
            self.active = "automatic-successor"

    def turn_interrupt(self, thread_id, turn_id):
        self.calls.append(("interrupt", thread_id, turn_id))
        if self.interrupt_broken:
            raise RuntimeError("interrupt unavailable")
        if self.race_interrupt_once:
            self.race_interrupt_once = False
            self.active = "newer-observed-turn"
            raise InvalidRequestError(-32600, "expected active turn id")
        assert thread_id == "owned" and turn_id == self.active
        self.active = None

    def thread_read(self, thread_id, *, include_turns=True):
        assert thread_id == "owned" and include_turns
        self.reads += 1
        if self.spawn_on_read == self.reads:
            self.active = "delayed-successor"
        if self.foreign_on_read == self.reads:
            self.goal.created_at += 1
        turns = [NS(id=self.active, status=NS(value="inProgress"))] if self.active else []
        return NS(thread=NS(id="foreign" if self.bad_thread else "owned",
                           status=NS(root=NS(type="active" if self.active else "idle")), turns=turns))

    def thread_goal_clear(self, _):
        pytest.fail("administrative stop must preserve Goal history")

    def start_goal_operation(self, *_):
        pytest.fail("stop must not create another Goal")


def stop(server, **kwargs):
    expected = goal_identity(server.goal)
    state = _GoalOperationState("owned", logical_turn_id="finished-boundary",
                                status=ThreadGoalStatus.active)
    return cancel_owned_goal(server, state, expected, poll_interval=0, **kwargs)


def test_official_cancel_alone_leaves_queued_turn_but_confirmed_stop_prevents_late_work():
    old = Server()
    old.spawn_after_cancel = True
    old.cancel_goal_operation(_GoalOperationState("owned", status=ThreadGoalStatus.active))
    assert old.active == "automatic-successor"  # Historical cancellation gap.

    fixed = Server()
    fixed.spawn_after_cancel = True
    before = (fixed.goal.created_at, fixed.goal.tokens_used, fixed.goal.token_budget)
    receipt = stop(fixed)
    assert receipt["confirmed"] and receipt["goal_status"] == "paused"
    assert receipt["interrupted_turn_ids"] == ["automatic-successor"]
    assert fixed.active is None and fixed.goal.status == ThreadGoalStatus.paused
    assert (fixed.goal.created_at, fixed.goal.tokens_used, fixed.goal.token_budget) == before


def test_two_quiet_reads_detect_a_delayed_successor():
    server = Server()
    server.spawn_on_read = 2
    receipt = stop(server)
    assert server.active is None
    assert receipt["interrupted_turn_ids"] == ["delayed-successor"]
    assert receipt["reads"] >= 4


def test_existing_idle_paused_operation_needs_no_turn_guess():
    server = Server()
    server.goal.status = ThreadGoalStatus.paused
    receipt = stop(server)
    assert receipt["confirmed"] and receipt["reads"] == 2
    assert not any(c[0] == "interrupt" for c in server.calls)


def test_swallowed_pause_error_cannot_be_claimed_as_success():
    server = Server()
    server.pause_broken = True
    with pytest.raises(NativeGoalStopUnconfirmed, match="pause unavailable"):
        stop(server)
    assert server.goal.status == ThreadGoalStatus.active


def test_interruption_failure_stops_sdk_progression():
    server = Server()
    server.spawn_after_cancel = True
    server.interrupt_broken = True
    with pytest.raises(NativeGoalStopUnconfirmed, match="interrupt unavailable"):
        stop(server)
    assert server.active == "automatic-successor"


@pytest.mark.parametrize("change", ["created_at", "objective", "token_budget", "tokens_used"])
def test_foreign_or_reset_goal_is_never_cancelled(change):
    server = Server()
    expected = goal_identity(server.goal)
    setattr(server.goal, change, {"created_at": 99, "objective": "foreign",
                                "token_budget": 100, "tokens_used": 0}[change])
    state = _GoalOperationState("owned", status=ThreadGoalStatus.active)
    with pytest.raises(NativeGoalStopUnconfirmed):
        cancel_owned_goal(server, state, expected, poll_interval=0)
    assert server.calls == []


def test_replacement_after_cancel_cannot_be_interrupted():
    server = Server()
    server.spawn_after_cancel = True
    server.foreign_on_read = 1
    with pytest.raises(NativeGoalStopUnconfirmed):
        stop(server)
    assert not any(c[0] == "interrupt" for c in server.calls)


def test_foreign_thread_snapshot_cannot_supply_an_interrupt_target():
    server = Server()
    server.bad_thread = True
    with pytest.raises(NativeGoalStopUnconfirmed, match="foreign thread"):
        stop(server)
    assert not any(c[0] == "interrupt" for c in server.calls)


def test_wrong_operation_thread_has_no_side_effects():
    server = Server()
    with pytest.raises(NativeGoalStopUnconfirmed, match="owning thread"):
        cancel_owned_goal(server, _GoalOperationState("foreign"), goal_identity(server.goal))
    assert server.calls == []


def test_stale_interrupt_reloads_the_actual_turn_without_guessing_an_id():
    server = Server()
    server.spawn_after_cancel = True
    server.race_interrupt_once = True
    receipt = stop(server)
    assert receipt["interrupted_turn_ids"] == ["newer-observed-turn"]
    assert server.active is None


def test_absent_goal_is_safe_only_when_actual_thread_is_idle():
    server = Server()
    expected = goal_identity(server.goal)
    server.goal = None
    state = _GoalOperationState("owned")
    receipt = cancel_owned_goal(server, state, expected, poll_interval=0)
    assert receipt["confirmed"] and receipt["goal_status"] is None and server.calls == []
    server.active = "foreign-explicit-turn"
    with pytest.raises(NativeGoalStopUnconfirmed, match="lost its owning Goal"):
        cancel_owned_goal(server, state, expected, poll_interval=0)
    assert server.calls == []


def test_production_boundary_stops_queued_successor_before_returning_input_limit(tmp_path):
    from test_native_goal_execution_limit_20261119 import (
        _delivery, _started, _completed, _goal_update, _official_stream, backend_fixture,
    )
    from theory_master.native_goal import NativeGoalExecutionLimit
    handoff, _, binding = _delivery(tmp_path / "workspace")
    backend, thread, _ = backend_fixture()
    _, calls, consumed = _official_stream(thread, [_started(), _goal_update(ThreadGoalStatus.active), _completed()])
    active = []
    official = thread._client.cancel_goal_operation
    def cancel(state):
        official(state)
        active.append("queued-successor")
    def read(tid, **_):
        return NS(thread=NS(id=tid, status=NS(root=NS(type="active" if active else "idle")),
                            turns=[NS(id=active[0], status=NS(value="inProgress"))] if active else []))
    def interrupt(tid, turn):
        assert tid == thread.id and turn == active[0]
        calls.append("successor_interrupted")
        active.clear()
    thread._client.cancel_goal_operation = cancel
    thread._client.thread_read = read
    thread._client.turn_interrupt = interrupt
    with pytest.raises(NativeGoalExecutionLimit):
        backend.invoke_goal("investigator", goal_objective="frozen goal",
                            context_prompt=json.dumps({"sdk_execution_limit_handoff": binding}),
                            handoff_path=str(handoff), thread_id=thread.id, model="Fake", provider="Fake")
    assert not active and len(consumed) == 3
    assert calls == ["official_cancel", "successor_interrupted", "official_unregister"]
    assert backend.last_native_goal["native_goal_stop_confirmed"]


def test_unconfirmed_production_stop_cannot_become_an_execution_limit(tmp_path):
    from test_native_goal_execution_limit_20261119 import (
        _delivery, _started, _completed, _goal_update, _official_stream, backend_fixture,
    )
    handoff, _, binding = _delivery(tmp_path / "workspace")
    backend, thread, _ = backend_fixture()
    _, calls, _ = _official_stream(thread, [_started(), _goal_update(ThreadGoalStatus.active), _completed()])
    thread._client.cancel_goal_operation = lambda _: calls.append("best_effort_noop")
    def pause(_):
        raise RuntimeError("pause rejected")
    thread._client.pause_goal = pause
    with pytest.raises(NativeGoalStopUnconfirmed, match="pause rejected"):
        backend.invoke_goal("investigator", goal_objective="frozen goal",
                            context_prompt=json.dumps({"sdk_execution_limit_handoff": binding}),
                            handoff_path=str(handoff), thread_id=thread.id, model="Fake", provider="Fake")
    assert backend.last_native_goal["status"] == "stop_unconfirmed"
    assert backend.last_native_goal["native_goal_stop_confirmed"] is False
    assert calls == ["best_effort_noop", "official_unregister"]


def test_already_complete_goal_is_preserved_instead_of_paused():
    server = Server()
    server.goal.status = ThreadGoalStatus.complete
    receipt = stop(server)
    assert receipt["confirmed"] and receipt["goal_status"] == "complete"
    assert server.goal.status == ThreadGoalStatus.complete and server.calls == []


def test_terminal_goal_with_own_inflight_turn_stops_turn_without_changing_goal():
    server = Server()
    server.goal.status = ThreadGoalStatus.complete
    server.active = "finishing-original-turn"
    receipt = stop(server)
    assert receipt["confirmed"] and server.active is None
    assert server.goal.status == ThreadGoalStatus.complete
    assert receipt["interrupted_turn_ids"] == ["finishing-original-turn"]
    assert server.calls == [("interrupt", "owned", "finishing-original-turn")]
