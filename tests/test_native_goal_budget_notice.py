"""Applied resource decisions, same-Goal steering and explicit dependency repair."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS
from unittest.mock import MagicMock,patch

import pytest
from openai_codex import Codex
from openai_codex.generated.v2_all import ThreadGoalStatus

from test_paused_goal_guidance import fixture,runner,record
from theory_master.native_goal_budget_notice import (
    approved_review,resolve_review_dependency,ensure_review_notice,
)
from theory_master.native_goal_guidance import PausedGoalGuidanceError,pending_guidance
from theory_master.native_goal_recovery import goal_identity
from theory_master.invocations import InvocationLedger


def approval(cycle,thread,allowance=100,decision="approve"):
    root=cycle/"budget";root.mkdir(exist_ok=True)
    row={"at":"2026-01-01T00:00:00+00:00","cycle_id":cycle.name,
         "review_index":1,"decision":decision,"new_allowance":allowance,
         "investigator_thread_id":thread,"prior_allowance":allowance//2}
    (root/"investigator_reviews.jsonl").write_text(json.dumps(row)+'\n')
    receipt={"schema":"theory-master/internal-budget-review/v1",
             **{k:v for k,v in row.items() if k not in {"cycle_id","investigator_thread_id"}}}
    (root/"internal_review_0001.json").write_text(json.dumps(receipt))
    return root/"internal_review_0001.json"


def test_approved_review_is_acknowledged_on_original_physical_turn_once(tmp_path):
    server,backend,thread,origin,handoff=fixture(tmp_path,queue=False)
    origin["persistent_goal"]=goal_identity(server.goal)
    path=approval(tmp_path,thread.id)
    before=path.read_bytes();server.complete()
    runner(server,backend,thread,origin,handoff).run()
    saved=record(tmp_path)
    assert saved["status"]=="consumed" and saved["source"]=="sdk_applied_budget_review"
    assert "approved review 1" in saved["text"] and "allowance is 100" in saved["text"]
    assert "NOT been reset" in saved["text"] and "not wait" in saved["text"]
    assert saved["budget"]["observed_tokens"]==80 and path.read_bytes()==before
    ensure_review_notice(handoff,origin,allowance=100,observed=85)
    assert record(tmp_path)["id"]==saved["id"]
    assert len([x for x in server.calls if x[0]=="turn/steer"])==1


@pytest.mark.parametrize("fault",["denied","receipt_disagrees","foreign_thread"])
def test_only_matching_applied_approval_can_generate_a_notice(tmp_path,fault):
    server,backend,thread,origin,handoff=fixture(tmp_path,queue=False)
    path=approval(tmp_path,thread.id,decision="reject" if fault=="denied" else "approve")
    if fault=="receipt_disagrees":
        data=json.loads(path.read_text());data["new_allowance"]=200;path.write_text(json.dumps(data))
    if fault=="foreign_thread":assert approved_review(tmp_path,"foreign",100) is None
    else:
        with pytest.raises(ValueError):approved_review(tmp_path,thread.id,100)
    assert not (tmp_path/"native_goal_resume_guidance.json").exists()


def test_changed_approval_stops_pending_notice_before_activation(tmp_path):
    server,backend,thread,origin,handoff=fixture(tmp_path,queue=False)
    origin["persistent_goal"]=goal_identity(server.goal)
    path=approval(tmp_path,thread.id)
    ensure_review_notice(handoff,origin,allowance=100,observed=80)
    path.write_text(path.read_text()+' ')
    with pytest.raises(PausedGoalGuidanceError,match="approval changed"):
        pending_guidance(handoff,origin,token_allowance=100,tokens_before_goal=80)
    assert not any(x[0]=="set" for x in server.calls)


def failed_fixture(tmp_path):
    server,backend,thread,origin,handoff=fixture(tmp_path,queue=False)
    server.goal=server.goal.model_copy(update={"status":ThreadGoalStatus.blocked})
    identity=goal_identity(server.goal)
    s=json.loads((tmp_path/"research_state.json").read_text())
    checkpoint=tmp_path/"checkpoints/frozen.json";checkpoint.parent.mkdir()
    checkpoint.write_text(json.dumps({"cycle_id":tmp_path.name,"hypothesis_lock":s["hypothesis_lock"]}))
    s.update(status="failed",phase="failed",cycle_id=tmp_path.name,active_role=None,
        next_executor="investigator",last_invocation_id="failed-investigator",
        last_safe_checkpoint=str(checkpoint),threads={"investigator":thread.id},scientific_verdict=None,
        recovery={"action":"error","kind":"NativeGoalRuntimeFailure","message":"missing budget review","at":"2026-01-01"},
        native_goal={"status":"blocked","runtime_failure_kind":"native_goal_dependency_blocked",
                     "thread_id":thread.id,"persistent_goal":identity})
    s["runtime_role_routes"]={"investigator":{"model":"offline-model","provider":"CodexHTTPS"}}
    (tmp_path/"research_state.json").write_text(json.dumps(s))
    InvocationLedger(tmp_path/"invocations.jsonl").append({"invocation_id":"failed-investigator",
        "state":"failed","role":"investigator","thread_id":thread.id,
        "commit_state":"not_committed","error_type":"NativeGoalRuntimeFailure"})
    path=approval(tmp_path,thread.id)
    request=server.request
    def metadata_rpc(method,params,**kwargs):
        if method=="thread/resume":
            server.calls.append((method,params))
            return NS(thread=NS(id=thread.id))
        return request(method,params,**kwargs)
    server.request=metadata_rpc
    # Execute the installed SDK facade and serializer, not an invented API.
    client=Codex.__new__(Codex)
    client._client=server
    owner=NS(spec=NS(research_root=tmp_path.parent,task_mode=True),_bind_cycle=MagicMock(),
             _emit=MagicMock(),_persist=MagicMock())
    return server,client,owner,s,path


def test_explicit_resolution_preserves_approved_budget_goal_science_and_failure(tmp_path):
    server,client,owner,state,path=failed_fixture(tmp_path)
    original=goal_identity(server.goal);review=path.read_bytes()
    with patch("theory_master.specification.latest_cycle_dir",return_value=tmp_path), \
         patch("theory_master.investigator_budget.budget_status",return_value={
             "exhausted":False,"rejected":False,"allowance_tokens":100,"observed_tokens":80}):
        receipt=resolve_review_dependency(owner,reason="approved review was not delivered",client=client)
    saved=owner._persist.call_args.args[0]
    assert saved["status"]=="paused" and saved["scientific_verdict"] is None
    assert saved["hypothesis_lock"]==state["hypothesis_lock"]
    assert saved["investigator_budget"]==state["investigator_budget"]
    assert receipt["token_grant"] is False and receipt["original_failure"]==state["recovery"]
    assert server.goal.status==ThreadGoalStatus.paused
    after=goal_identity(server.goal)
    for key in ["created_at","tokens_used","token_budget","objective_sha256"]:assert after[key]==original[key]
    assert path.read_bytes()==review and record(tmp_path)["status"]=="pending"
    assert record(tmp_path)["budget_review"]==receipt["approved_review"]
    assert receipt["stop_confirmation"]["confirmed"]
    assert not any(x[0]=="turn/steer" for x in server.calls) # No science until normal Resume.
    resume=next(x[1] for x in server.calls if x[0]=="thread/resume")
    assert resume["model"]=="offline-model" and resume["modelProvider"]=="openai_https"


@pytest.mark.parametrize("fault",["foreign_goal","complete_goal","active_turn","denied","wrong_role","exhausted"])
def test_resolution_refuses_unsafe_or_unapproved_reentry(tmp_path,fault):
    server,client,owner,state,path=failed_fixture(tmp_path)
    if fault=="foreign_goal":server.goal=server.goal.model_copy(update={"created_at":999})
    if fault=="complete_goal":server.goal=server.goal.model_copy(update={"status":ThreadGoalStatus.complete})
    if fault=="active_turn":server.active=True
    if fault=="denied":
        data=json.loads(path.read_text());data["decision"]="reject";path.write_text(json.dumps(data))
    if fault=="wrong_role":
        p=tmp_path/"invocations.jsonl";data=json.loads(p.read_text());data["role"]="reporter";p.write_text(json.dumps(data)+'\n')
    with patch("theory_master.specification.latest_cycle_dir",return_value=tmp_path), \
         patch("theory_master.investigator_budget.budget_status",return_value={
             "exhausted":fault=="exhausted","rejected":False,"allowance_tokens":100,"observed_tokens":80}):
        with pytest.raises((ValueError,RuntimeError)):
            resolve_review_dependency(owner,reason="operator diagnosed review delivery",client=client)
    owner._persist.assert_not_called()
    assert not any(x[0] in {"pause","interrupt","turn/steer"} for x in server.calls)


def test_goal_replacement_during_thread_load_cannot_be_paused(tmp_path):
    server,client,owner,state,path=failed_fixture(tmp_path)
    load=client.thread_resume
    def replace(thread_id,**kwargs):
        server.goal=server.goal.model_copy(update={"created_at":999})
        return load(thread_id,**kwargs)
    client.thread_resume=replace
    with patch("theory_master.specification.latest_cycle_dir",return_value=tmp_path), \
         patch("theory_master.investigator_budget.budget_status",return_value={
             "exhausted":False,"rejected":False,"allowance_tokens":100,"observed_tokens":80}):
        with pytest.raises(RuntimeError):
            resolve_review_dependency(owner,reason="operator diagnosed missing review delivery",client=client)
    owner._persist.assert_not_called()
    assert not any(x[0] in {"pause","interrupt"} for x in server.calls)
