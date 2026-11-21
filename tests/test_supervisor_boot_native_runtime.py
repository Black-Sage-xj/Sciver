"""Native boot Resume admission and attach, using only deterministic SDK fakes."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from openai_codex.generated.v2_all import ThreadGoalStatus

from tests.test_boot_native_goal_20261121 import active_goal, write_rows
from tests.test_native_goal_resume_recovery_20261119 import OfflineServer, SCIENCE, session_fixture
from tests.test_supervisor_boot_active import NEW_LAUNCH, previous_boot_active
from theory_master import runner
from theory_master.backends import FakeBackend
from theory_master.codex_goal_transport import NativeGoalRunner
from theory_master.invocation_transport import InvocationTransport
from theory_master.invocations import InvocationLedger, stable_hash
from theory_master.native_goal import NativeGoalIncomplete
from theory_master.native_goal_recovery import NativeGoalRecoveryConflict, verify_existing_goal
from theory_master.retained_goal_quota import goal_rollout_identity
from theory_master.runtime import write_json
from theory_master.supervisor_boot_recovery import eligible_boot_resume
from theory_master.workflow import Workflow


def _native_pin(origin: dict, account: dict) -> dict:
    return {
        "invocation_id": origin["invocation_id"], "origin_invocation_id": origin["invocation_id"],
        "stage": origin["stage"], "logical_key": origin["logical_key"], "thread_id": origin["thread_id"],
        "contract_ref": origin["contract_ref"], "origin_sha256": stable_hash(origin), "account_binding": account,
    }


def test_managed_previous_boot_uses_the_actual_local_native_goal_proof(
        previous_boot_active, active_goal, monkeypatch):
    spec, cycle, state, registry = previous_boot_active
    case = active_goal
    state.update(case.state, current_turn_id="previous-native-turn", native_goal={
        "status": "active", "thread_id": case.row["thread_id"],
    })
    state_path = cycle / "research_state.json"
    checkpoint = Path(state["last_safe_checkpoint"])
    checkpoint_state = json.loads(checkpoint.read_bytes())
    write_json(checkpoint, {**checkpoint_state, "hypothesis_lock": state["hypothesis_lock"]})
    write_json(state_path, state)

    ledger = InvocationLedger(cycle / "invocations.jsonl")
    row = case.row
    ledger.prepare(invocation_id=row["invocation_id"], kind=row["kind"], input_payload={},
                   role=row["role"], stage=row["stage"], logical_key=row["logical_key"],
                   contract_ref=row["contract_ref"])
    ledger.transition(row["invocation_id"], "running", thread_id=row["thread_id"],
                      native_goal_identity=row["native_goal_identity"])
    write_rows(cycle / "events.jsonl", [case.started])
    inputs = [{**item, "content": item["content"].replace(str(case.cycle), str(cycle))}
              for item in case.evidence]
    audit_path = cycle / "runtime_events.jsonl"
    write_rows(audit_path, inputs)
    origins = []

    def bind_account(bound_cycle, bound_state, origin):
        assert bound_cycle == cycle and bound_state == state
        origins.append(origin)
        return case.account

    monkeypatch.setattr("theory_master.retained_goal_quota.boot_goal_account_binding", bind_account)
    plan = eligible_boot_resume(spec.research_root, registry=registry)
    assert plan is not None and plan["cycle"] == cycle.name
    assert plan["origin"]["native_goal"] == _native_pin(origins[0], case.account)
    assert Path(origins[0]["handoff_path"]).parent.parent == cycle / "workspace"
    write_rows(audit_path, inputs[:1])
    assert eligible_boot_resume(spec.research_root, registry=registry) is None
    assert len(origins) == 1
    assert json.loads(state_path.read_bytes()) == state


@pytest.mark.parametrize("case", ["exact", "wrong_cycle", "changed_credential", "unrelated_stage"])
def test_managed_runner_passes_verified_native_ticket_to_workflow_before_invoking(
        previous_boot_active, monkeypatch, capsys, case):
    spec, cycle, original, _ = previous_boot_active
    root = spec.research_root
    plan = eligible_boot_resume(root)
    origin = {"invocation_id": "inv-2-investigator-original", "thread_id": "original-thread",
              "stage": "investigate", "logical_key": "original-slot", "contract_ref": {},
              "state_sha256": plan["origin"]["state_sha256"]}
    account = {"thread_id": origin["thread_id"], "account": "2", "provider": "codex", "model": "fixture"}
    pin = _native_pin(origin, account)
    ticket = {**plan, "launch_id": NEW_LAUNCH, "origin": {**plan["origin"], "native_goal": pin}}
    if case == "wrong_cycle":
        ticket["cycle"] = "7"
    home = SimpleNamespace(binding=account if case != "changed_credential" else {**account, "account": "1"})
    events: list[str] = []

    def verify(request_root, launch_id, *, managed_child):
        assert (request_root, launch_id, managed_child) == (root, NEW_LAUNCH, True)
        events.append("verify_ticket")
        return ticket

    def prepare_home(request_root, binding):
        assert request_root == root and binding == account
        events.append("pin_account")
        return home

    class OfflineBackend(FakeBackend):
        def __init__(self, request_root, *, routes, retained_goal_home, boot_native_goal):
            super().__init__(reworks=0)
            assert request_root == root and retained_goal_home is home and boot_native_goal == pin
            self.retained_goal_home = retained_goal_home
            self.boot_native_goal = boot_native_goal
            events.append("backend_bound")

        def _verify_provider_runtime(self):
            assert events == ["verify_ticket", "pin_account", "backend_bound"]
            events.append("verify_sdk")

        def preflight_runtime(self):
            pytest.fail("boot-native Resume must not create disposable provider threads")

        def ensure_thread(self, *args, **kwargs):
            pytest.fail("no provider thread may be opened in this admission test")

        def invoke(self, *args, **kwargs):
            pytest.fail("admission must not make a provider call")

    def run_cycle(owner, state, *, same_cycle_continuation):
        assert owner.cycle_dir == cycle and same_cycle_continuation is True
        assert owner._boot_resume_active and owner.invocation_cycle_index == 2
        assert owner.backend.boot_native_goal == pin
        assert state["hypothesis_lock"] == original["hypothesis_lock"]
        assert state["recovery"]["allow_thread_fallback"] is False
        events.append("entered_original_cycle")
        if case == "unrelated_stage":
            try:
                owner._call(state, "judge", {})
            except NativeGoalRecoveryConflict:
                events.append("unrelated_stage_rejected")
                raise
            pytest.fail("an unrelated stage was not rejected before opening its provider thread")
        return {**state, "status": "needs_attention"}

    monkeypatch.setattr("theory_master.supervisor_boot_recovery.verify_boot_resume", verify)
    monkeypatch.setattr("theory_master.retained_goal_quota.prepare_boot_goal_home", prepare_home)
    monkeypatch.setattr(runner, "CodexBackend", OfflineBackend)
    monkeypatch.setattr(runner, "model_route_preflight_lines", lambda *_, **__: [])
    monkeypatch.setattr(Workflow, "_run_cycle", run_cycle)
    monkeypatch.setattr(Workflow, "run", lambda _: pytest.fail("boot Resume cannot enter an ordinary Run"))
    argv = ["--task-root", str(root), "--operation", "resume", "--identifier", cycle.name,
            "--managed-child", "--boot-resume-launch", NEW_LAUNCH]

    if case == "wrong_cycle":
        with pytest.raises(SystemExit) as raised:
            runner.main(argv)
        assert raised.value.code == 2
        assert events == ["verify_ticket"]
    else:
        assert runner.main(argv) == (0 if case == "exact" else 1)
        assert events[:4] == ["verify_ticket", "pin_account", "backend_bound", "verify_sdk"]
        assert events.count("entered_original_cycle") == (0 if case == "changed_credential" else 1)
        if case == "changed_credential":
            assert "no matching original Native Goal credential" in capsys.readouterr().err
        elif case == "unrelated_stage":
            assert events[-1] == "unrelated_stage_rejected"


@pytest.mark.parametrize("case", ["active", "active_idle", "complete", "paused", "usage_limited", "replaced_goal",
                                  "different_rollout", "different_stage"])
def test_boot_native_transport_only_attaches_to_the_audited_goal(tmp_path, case):
    session, record, origin = session_fixture(tmp_path)
    server = OfflineServer(origin)
    origin["persistent_goal"] = verify_existing_goal(server.goal, origin)
    record["native_goal_identity"] = origin["persistent_goal"]
    rollout = tmp_path / "original-rollout.jsonl"
    rollout.write_text("Transcript prefix that existed before the boot\n")
    server.rollout_path = str(rollout)
    account = {"thread_id": origin["thread_id"], "account": "2", "provider": "codex", "model": "fixture-model",
               "created_at": "123", "objective_sha256": origin["objective_sha256"],
               **goal_rollout_identity(str(rollout))}
    pin = _native_pin(origin, account)
    thread = SimpleNamespace(id=origin["thread_id"], _client=server)
    retained = SimpleNamespace(thread_id=thread.id, account="2", provider="codex", binding=account,
                               verify_auth=Mock())
    backend = SimpleNamespace(threads={thread.id: thread}, _new_thread_ids=set(), collector=None,
                              _logger=Mock(), ensure_thread=Mock(return_value=thread.id), last_native_goal={},
                              boot_native_goal=pin, retained_goal_home=retained, retained_goal_delivered=False,
                              client_account="2")
    backend.invoke_goal = Mock(side_effect=lambda role, **kwargs: NativeGoalRunner(backend, role, **kwargs).run())
    backend.invoke = Mock(side_effect=AssertionError("cold boot must not use an ordinary Turn"))
    session.owner.backend = backend
    session.owner.spec = SimpleNamespace(research_root=tmp_path)
    session.owner.collector = None
    session.owner._check_recovery_control = Mock()
    session.owner._persist = Mock()
    session.invocation_ledger.transition = Mock()
    session.role = "investigator"
    session.route = SimpleNamespace(provider=account["provider"], model=account["model"])
    session.native_preflight_goal = "A regenerated objective cannot replace the recorded original."
    session.prompt_context = {"frozen_objective": SCIENCE}
    session.skill_addendum = ""
    session.state["prompt_metrics"] = [{}]
    session.state["active_invocation_id"] = pin["invocation_id"]
    session.contract.prompt_projection = lambda _: "fixture contract"
    transport = InvocationTransport(session)
    handoff = Path(origin["handoff_path"])
    original_handoff = handoff.read_bytes()

    if case == "different_stage":
        session.stage = "judge_rework"
        with pytest.raises(NativeGoalRecoveryConflict):
            transport._prepare_native_delivery()
        assert not server.calls
        backend.invoke_goal.assert_not_called()
        return
    if case == "replaced_goal":
        server.goal = server.goal.model_copy(update={"created_at": 999})
    elif case == "different_rollout":
        server.rollout_path = str(tmp_path / "foreign-rollout.jsonl")
    elif case in {"complete", "paused", "usage_limited"}:
        server.active = False
        server.goal = server.goal.model_copy(update={"status": ThreadGoalStatus(
            "usageLimited" if case == "usage_limited" else case)})
    else:
        server.active = case != "active_idle"
        server.complete()

    transport._prepare_native_delivery()
    assert session.native_goal_origin == origin
    assert session.handoff_path == handoff
    with patch("theory_master.invocation_transport.budget_status", return_value={
        "allowance_tokens": 1_000_000, "observed_tokens": 10_000,
    }), patch("theory_master.codex_goal_transport.NativeGoalRunner._setup_context") as setup:
        if case == "replaced_goal":
            with pytest.raises(NativeGoalRecoveryConflict, match="identity or accumulated"):
                transport.invoke_with_audit("unused ordinary prompt", "continuation")
            assert not any(name == "register" for name, _ in server.calls)
        elif case == "different_rollout":
            with pytest.raises(NativeGoalRecoveryConflict, match="original native Goal transcript"):
                transport.invoke_with_audit("unused ordinary prompt", "continuation")
        elif case in {"paused", "usage_limited"}:
            with pytest.raises((NativeGoalIncomplete, NativeGoalRecoveryConflict)):
                transport.invoke_with_audit("unused ordinary prompt", "continuation")
        else:
            identifier, output = transport.invoke_with_audit("unused ordinary prompt", "continuation")
            assert identifier == thread.id and "The original authored claim." in output
        setup.assert_not_called()
    assert handoff.read_bytes() == original_handoff
    activations = [kwargs for name, kwargs in server.calls if name == "set"]
    assert activations == ([{"status": ThreadGoalStatus.active}] if case == "active_idle" else [])
    assert not any(name in {"cancel", "pause", "interrupt"} for name, _ in server.calls)
    backend.ensure_thread.assert_called_once_with("investigator", thread.id, account["model"], account["provider"])
    backend.invoke.assert_not_called()
    backend.invoke_goal.assert_called_once()
