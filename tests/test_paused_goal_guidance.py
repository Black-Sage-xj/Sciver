"""Paused corrections use installed SDK steering and an offline server only."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import pytest
from openai_codex.client import CodexClient
from openai_codex.errors import InvalidRequestError
from openai_codex.generated.v2_all import (
    ThreadGoalStatus, ThreadTokenUsage, ThreadTokenUsageUpdatedNotification,
    TokenUsageBreakdown, TurnStatus,
)
from openai_codex.models import Notification

from test_native_goal_resume_recovery_20261119 import OfflineServer, SCIENCE, origin_fixture
from theory_master.codex_goal_transport import NativeGoalRunner
from theory_master.native_goal import NativeGoalBudgetExceeded
from theory_master.native_goal_guidance import (
    FILENAME, PausedGoalGuidanceError, pending_guidance, queue_guidance,
)
from theory_master.native_goal_recovery import bind_origin_context, goal_identity
from theory_master.workflow_support import RecoveryStop


class SteeringServer(OfflineServer, CodexClient):
    """Keep the real SDK turn_steer serializer; replace server I/O."""
    def __init__(self, origin):
        OfflineServer.__init__(self, origin, status="paused", active=False)
        self.steer_error = None
        self.ack = None
        self.after_steer = None
        self.before_steer = None
        self.on_activate = None

    def request(self, method, params, **kwargs):
        if method != "turn/steer":
            return OfflineServer.request(self, method, params, **kwargs)
        self.calls.append((method, copy.deepcopy(params)))
        if self.before_steer:
            self.before_steer()
        if self.steer_error:
            raise self.steer_error
        assert params["threadId"] == self.goal.thread_id
        if not self.active or params["expectedTurnId"] != self.turn.id:
            raise InvalidRequestError(-32600, "expected active turn id")
        if self.after_steer:
            self.after_steer()
        return NS(turn_id=self.ack or params["expectedTurnId"])

    def thread_goal_set(self, thread_id, **kwargs):
        result = OfflineServer.thread_goal_set(self, thread_id, **kwargs)
        self.active = kwargs["status"] == ThreadGoalStatus.active
        if self.active and self.on_activate:
            self.on_activate()
        return result

    def pause_goal(self, thread_id):
        self.calls.append(("pause", thread_id))
        self.goal = self.goal.model_copy(update={"status": ThreadGoalStatus.paused})

    def turn_interrupt(self, thread_id, turn_id):
        assert thread_id == self.goal.thread_id and turn_id == self.turn.id
        self.calls.append(("interrupt", thread_id, turn_id))
        self.active = False
        self.turn = self.turn.model_copy(update={"status": TurnStatus.interrupted})

    def usage(self, last_tokens=25):
        amount = TokenUsageBreakdown(input_tokens=last_tokens, output_tokens=0,
            cached_input_tokens=last_tokens, reasoning_output_tokens=0, total_tokens=last_tokens)
        self.state.observe(Notification("thread/tokenUsage/updated",
            ThreadTokenUsageUpdatedNotification(thread_id=self.goal.thread_id, turn_id=self.turn.id,
                token_usage=ThreadTokenUsage(total=amount, last=amount))))


def fixture(tmp_path, *, observed=80, allowance=100, queue=True):
    origin, handoff = origin_fixture(tmp_path)
    server = SteeringServer(origin)
    state = {"status": "paused", "execution_active": False, "resumable": True,
             "active_role": "investigator", "hypothesis_lock": SCIENCE,
             "native_goal": {"thread_id": origin["thread_id"], "origin_identity": goal_identity(server.goal)},
             "investigator_budget": {"observed_tokens": observed, "allowance_tokens": allowance}}
    (tmp_path / "research_state.json").write_text(json.dumps(state))
    (tmp_path / "run_state.json").write_text(json.dumps({"investigator_budget":state["investigator_budget"]}))
    if queue:
        queue_guidance(tmp_path, "The original compute was live; preserve both physical attempts.", source="offline-operator")
    thread = NS(id=origin["thread_id"], _client=server)
    backend = NS(threads={thread.id:thread}, _new_thread_ids=set(), collector=None,
                 _logger=MagicMock(), ensure_thread=MagicMock(return_value=thread.id), last_native_goal={})
    return server, backend, thread, origin, handoff


def runner(server, backend, thread, origin, handoff, *, observed=80, allowance=100):
    return NativeGoalRunner(backend, "investigator", goal_objective=origin["objective"],
        context_prompt=bind_origin_context(origin), handoff_path=str(handoff), thread_id=thread.id,
        model="offline-model", provider="codex", tokens_before_goal=observed, token_allowance=allowance)


def record(tmp_path):
    return json.loads((tmp_path / FILENAME).read_text())


def test_real_sdk_expected_turn_rpc_acknowledges_same_goal_without_setup_turn(tmp_path):
    server, backend, thread, origin, handoff = fixture(tmp_path)
    original = goal_identity(server.goal)
    server.complete()
    with patch.object(NativeGoalRunner, "_setup_context") as setup:
        result = runner(server,backend,thread,origin,handoff).run()
    assert "original authored claim" in result[1]
    setup.assert_not_called()
    steer = [x for x in server.calls if x[0] == "turn/steer"]
    assert len(steer) == 1
    assert steer[0][1]["expectedTurnId"] == "original-physical-turn"
    assert "preserve both physical attempts" in steer[0][1]["input"][0]["text"]
    saved = record(tmp_path)
    assert saved["status"] == "consumed" and saved["acknowledged_turn_id"] == "original-physical-turn"
    assert saved["attempts"][0]["status"] == "acknowledged"
    assert goal_identity(server.goal)["created_at"] == original["created_at"]
    assert goal_identity(server.goal)["token_budget"] == original["token_budget"]
    assert not any(x[0] in {"cancel","pause","interrupt"} for x in server.calls)


@pytest.mark.parametrize("failure", ["rpc", "ack", "turn_race", "stale_expected_turn"])
def test_delivery_failure_stays_pending_and_confirms_real_pause(tmp_path, failure):
    server, backend, thread, origin, handoff = fixture(tmp_path)
    if failure == "rpc":server.steer_error = RuntimeError("ack lost")
    elif failure == "ack":server.ack = "foreign-turn"
    elif failure == "turn_race":
        server.after_steer = lambda:setattr(server,"turn",server.turn.model_copy(update={"id":"new-physical-turn"}))
    else:
        server.before_steer = lambda:setattr(server,"turn",server.turn.model_copy(update={"id":"new-physical-turn"}))
    with pytest.raises(PausedGoalGuidanceError):
        runner(server,backend,thread,origin,handoff).run()
    saved = record(tmp_path)
    assert saved["status"] == "pending" and saved["attempts"][0]["status"] == "unconfirmed"
    assert server.goal.status == ThreadGoalStatus.paused and not server.active
    assert backend.last_native_goal["native_goal_stop_confirmed"]
    assert len([x for x in server.calls if x[0] == "turn/steer"]) == 1


@pytest.mark.parametrize("when", ["activate", "steer"])
def test_live_budget_exhaustion_wins_and_does_not_consume_correction(tmp_path, when):
    server, backend, thread, origin, handoff = fixture(tmp_path)
    if when == "activate":server.on_activate = server.usage
    else:server.after_steer = server.usage
    with pytest.raises(NativeGoalBudgetExceeded) as error:
        runner(server,backend,thread,origin,handoff).run()
    assert error.value.observed_tokens == 105 and error.value.allowance_tokens == 100
    assert record(tmp_path)["status"] == "pending"
    assert server.goal.status == ThreadGoalStatus.paused and not server.active
    assert backend.last_native_goal["native_goal_stop_confirmed"]
    if when == "activate":assert not any(x[0] == "turn/steer" for x in server.calls)


def test_zero_remaining_budget_never_activates_original_goal(tmp_path):
    server, backend, thread, origin, handoff = fixture(tmp_path, observed=100)
    with pytest.raises(NativeGoalBudgetExceeded):
        runner(server,backend,thread,origin,handoff,observed=100)
    assert server.goal.status == ThreadGoalStatus.paused
    assert record(tmp_path)["status"] == "pending" and server.calls == []


def test_repeated_queue_peeks_and_stream_do_not_double_charge_usage(tmp_path):
    server, backend, thread, origin, handoff = fixture(tmp_path)
    server.on_activate = lambda:server.usage(5)
    server.complete()
    operation=runner(server,backend,thread,origin,handoff)
    operation.run()
    assert operation.observed_goal_tokens == 85
    assert record(tmp_path)["status"] == "consumed"


def test_replacement_after_steer_is_never_paused_as_original_owner(tmp_path):
    from theory_master.native_goal_stop import NativeGoalStopUnconfirmed
    server, backend, thread, origin, handoff = fixture(tmp_path)
    server.after_steer=lambda:setattr(server,"goal",server.goal.model_copy(update={"created_at":999}))
    with pytest.raises(NativeGoalStopUnconfirmed):
        runner(server,backend,thread,origin,handoff).run()
    assert record(tmp_path)["status"] == "pending"
    assert not any(x[0] in {"cancel","pause","interrupt"} for x in server.calls)
    assert server.goal.created_at == 999


@pytest.mark.parametrize("change", ["foreign", "complete", "active"])
def test_foreign_or_nonpaused_goal_never_receives_activation_or_correction(tmp_path, change):
    server, backend, thread, origin, handoff = fixture(tmp_path)
    if change == "foreign":server.goal = server.goal.model_copy(update={"created_at":999})
    else:server.goal = server.goal.model_copy(update={"status":ThreadGoalStatus(change)})
    with pytest.raises(PausedGoalGuidanceError):
        runner(server,backend,thread,origin,handoff).run()
    assert record(tmp_path)["status"] == "pending"
    assert not any(x[0] in {"set","cancel","pause","interrupt","turn/steer"} for x in server.calls)


def test_queue_requires_paused_inactive_owned_investigator_and_preserves_state(tmp_path):
    _, _, _, origin, handoff = fixture(tmp_path,queue=False)
    before=(tmp_path/"research_state.json").read_bytes()
    with patch("theory_master.runtime_manager.runtime_health",return_value={"runner_alive":True}):
        with pytest.raises(ValueError,match="live Runner"):queue_guidance(tmp_path,"facts",source="operator")
    first=queue_guidance(tmp_path,"facts",source="operator")
    assert (tmp_path/"research_state.json").read_bytes()==before
    with pytest.raises(ValueError,match="already pending"):queue_guidance(tmp_path,"other facts",source="operator")
    assert pending_guidance(handoff,origin,token_allowance=100,tokens_before_goal=80)["id"]==first["id"]
    with pytest.raises(PausedGoalGuidanceError,match="reset"):
        pending_guidance(handoff,origin,token_allowance=100,tokens_before_goal=0)


def test_live_budget_high_water_and_frozen_binding_cannot_be_rewritten(tmp_path):
    _, _, _, origin, handoff = fixture(tmp_path,queue=False)
    (tmp_path/"run_state.json").write_text(json.dumps({"investigator_budget":{
        "observed_tokens":90,"allowance_tokens":100}}))
    queue_guidance(tmp_path,"facts",source="operator")
    assert record(tmp_path)["budget"]["observed_tokens"] == 90
    with pytest.raises(PausedGoalGuidanceError,match="reset"):
        pending_guidance(handoff,origin,token_allowance=100,tokens_before_goal=80)
    other={**origin,"frozen_objective_sha256":"0"*64}
    with pytest.raises(PausedGoalGuidanceError,match="another frozen"):
        pending_guidance(handoff,other,token_allowance=100,tokens_before_goal=90)


def test_consumed_correction_is_archived_before_the_next_explicit_queue(tmp_path):
    server, backend, thread, origin, handoff = fixture(tmp_path)
    server.complete()
    runner(server,backend,thread,origin,handoff).run()
    previous=record(tmp_path)
    queued=queue_guidance(tmp_path,"different operator facts",source="operator")
    assert queued["id"]!=previous["id"]
    assert json.loads((tmp_path/"native_goal_guidance_history"/(previous["id"]+".json")).read_text())==previous


@pytest.mark.parametrize("field,value", [("status","active"),("execution_active",True),("resumable",False),("active_role","judge")])
def test_wrong_pause_boundary_rejects_queue(tmp_path,field,value):
    fixture(tmp_path,queue=False)
    p=tmp_path/"research_state.json";state=json.loads(p.read_text());state[field]=value;p.write_text(json.dumps(state))
    with pytest.raises(ValueError,match="inactive resumable"):queue_guidance(tmp_path,"facts",source="operator")


def test_unconfirmed_correction_has_an_explicit_workflow_pause_exit(tmp_path):
    from theory_master.invocation_transport import InvocationTransport
    state={"status":"active","investigator_budget":{"observed_tokens":80,"allowance_tokens":100},
           "native_goal":{"origin_identity":{"created_at":123}},"scientific_verdict":None}
    backend=NS(last_native_goal={"status":"paused","native_goal_stop_confirmed":True})
    owner=NS(backend=backend,_emit=MagicMock(),_persist=MagicMock())
    session=NS(owner=owner,state=state,invocation_id="original",role="investigator",identifier="thread",
               invocation_ledger=MagicMock(),artifact_tracker=MagicMock())
    with pytest.raises(RecoveryStop):
        InvocationTransport(session)._handle_paused_guidance_failure(PausedGoalGuidanceError("ack missing"))
    assert state["status"]=="paused" and state["phase"]=="paused"
    assert state["investigator_budget"]=={"observed_tokens":80,"allowance_tokens":100}
    assert state["scientific_verdict"] is None and state["native_goal"]["origin_identity"]=={"created_at":123}
    session.invocation_ledger.transition.assert_called_once()


def test_no_pending_correction_keeps_existing_attachment_path(tmp_path):
    server, backend, thread, origin, handoff = fixture(tmp_path,queue=False)
    server.complete()
    runner(server,backend,thread,origin,handoff).run()
    assert not any(x[0] == "turn/steer" for x in server.calls)


def test_durable_ack_survives_optional_collector_failure(tmp_path):
    server, backend, thread, origin, handoff = fixture(tmp_path)
    backend.collector=MagicMock()
    backend.collector.on_codex.side_effect=RuntimeError("optional collector unavailable")
    server.complete()
    runner(server,backend,thread,origin,handoff).run()
    assert record(tmp_path)["status"] == "consumed"
    assert not any(x[0] in {"pause","interrupt"} for x in server.calls)


def finalized_pause(tmp_path, *, last_role="investigator", next_executor="investigator"):
    from theory_master.invocations import InvocationLedger
    fixture(tmp_path,queue=False)
    p=tmp_path/"research_state.json";state=json.loads(p.read_text())
    identity=state["native_goal"]["origin_identity"]
    state.update(active_role=None,next_executor=next_executor,last_invocation_id="last-interrupted",
                 threads={"investigator":identity["thread_id"]})
    p.write_text(json.dumps(state))
    InvocationLedger(tmp_path/"invocations.jsonl").append({
        "invocation_id":"last-interrupted","state":"interrupted","role":last_role,
        "thread_id":identity["thread_id"],"native_goal_identity":identity})
    return state


def test_finalized_investigator_pause_uses_its_interrupted_goal_owner(tmp_path):
    finalized_pause(tmp_path)
    before=(tmp_path/"research_state.json").read_bytes()
    queued=queue_guidance(tmp_path,"facts",source="operator")
    assert queued["status"]=="pending"
    assert (tmp_path/"research_state.json").read_bytes()==before


@pytest.mark.parametrize("last_role",["research_lead","reporter","judge"])
def test_historical_investigator_goal_does_not_authorize_another_role_pause(tmp_path,last_role):
    finalized_pause(tmp_path,last_role=last_role)
    with pytest.raises(ValueError,match="inactive resumable"):
        queue_guidance(tmp_path,"facts",source="operator")


@pytest.mark.parametrize("change",["next_executor","thread","generation","missing_ledger"])
def test_finalized_pause_requires_exact_current_resume_binding(tmp_path,change):
    state=finalized_pause(tmp_path)
    if change=="next_executor":state["next_executor"]="reporter"
    elif change=="thread":state["threads"]["investigator"]="historical-thread"
    elif change=="generation":state["native_goal"]["origin_identity"]["created_at"]=999
    else:(tmp_path/"invocations.jsonl").unlink()
    (tmp_path/"research_state.json").write_text(json.dumps(state))
    with pytest.raises(ValueError,match="inactive resumable"):
        queue_guidance(tmp_path,"facts",source="operator")
