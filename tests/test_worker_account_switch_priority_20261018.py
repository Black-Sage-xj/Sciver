"""Safe shared-quota Worker assignment and cross-account frozen Cycle migration."""
from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from theory_master import worker_runtime as runtime
from theory_master.worker_mode import configure_mode, read_mode


def _write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def _quota(remaining):
    return {"ok":True, "ordinary_usage_allowed":remaining > 0,
            "primary":{"remaining_percent":remaining},
            "secondary":{"remaining_percent":80}}


def test_sharing_default_off_and_explicit_switch_preserves_current_worker_count(tmp_path):
    root=tmp_path/"research"
    assert read_mode(root)["share_accounts"] is False
    first=configure_mode(root, enabled=True, desired_agents=2, share_accounts=True)
    assert first["share_accounts"] is True
    assert configure_mode(root, enabled=True, desired_agents=3)["share_accounts"] is True
    assert configure_mode(root, enabled=True, desired_agents=2, share_accounts=False)["share_accounts"] is False


def test_share_capacity_and_quota_selection_do_not_pretend_two_auth_are_two_slots(tmp_path):
    root=tmp_path/"research"
    configure_mode(root, enabled=True, desired_agents=3, share_accounts=True)
    spec=SimpleNamespace(research_root=root)
    with mock.patch("theory_master.backends.resolve_routes", return_value={
            "research_lead": SimpleNamespace(provider="CodexHTTPS")}), \
         mock.patch("theory_master.codex_accounts.account_status", return_value={
            "accounts":{"1":{"auth_present":True},"2":{"auth_present":True}}}):
        capacity,accounts=runtime._route_capacity(spec,3)
    assert (capacity,accounts)==(3,["1","2"])
    with mock.patch("theory_master.codex_accounts.account_rate_limits",
                    side_effect=lambda account:_quota(60 if account=="1" else 95)):
        assert runtime._shared_account_candidate(accounts, [], [])=="2"
        assert runtime._shared_account_candidate(
            accounts,[{"account":"2","worker_id":"worker-1"}],[])=="1"
        assert runtime._shared_account_candidate(
            accounts,[{"account":"2","worker_id":"worker-1"},
                      {"account":"2","worker_id":"worker-2"}],[])=="1"
        assert runtime._shared_account_candidate(
            accounts,[{"account":"2"},{"account":"2"},
                      {"account":"1"},{"account":"1"}],[]) is None


def _migration_fixture(tmp_path):
    root=tmp_path/"research"
    accounts=tmp_path/"accounts"
    for a in ("1","2"):
        (accounts/a).mkdir(parents=True)
        (accounts/a/"auth.json").write_text(f"fake-account-{a}")
    home=root/".theory-master/workers/worker-2/codex-home"
    home.mkdir(parents=True)
    (home/"auth.json").symlink_to(accounts/"2"/"auth.json")
    (home/"config.toml").write_text("dummy = true")
    worker={"worker_id":"worker-2","status":"PAUSED","account":"2",
            "target_h_id":"H-0089","entry_node_id":"H-0089",
            "claim_id":"claim-valid","epoch":11,"current_cycle":"134"}
    _write(root/".theory-master/workers/worker-2/runtime.json",
           {"schema":runtime.SCHEMA, **worker})
    frozen={"status":"locked", "id":"H-0134","hypothesis":"frozen scientific proposition",
            "decisive_test":"test is unchanged"}
    science={"worker_id":"worker-2","claimed_frontier_id":"H-0089",
             "status":"paused","phase":"provider_quota_exhausted","resumable":True,
             "hypothesis_lock":frozen,
             "threads":{"research_lead":"thread-from-account2","challenger":"challenge-2"},
             "research_lead_thread_id":"thread-from-account2",
             "role_outputs":{"research_lead_frontier":{"candidates":["committed"]}}}
    _write(root/"134/research_state.json",science)
    _write(root/".theory-master/cycle_claims.json",_cycle_claims("worker-2","claim-valid",11))
    return root,accounts,worker,science


def _cycle_claims(worker_id, claim_id, epoch):
    return {"schema":"theory-master/cycle-claims/v1","epoch":epoch,"history":[],
            "claims":{claim_id:{"claim_id":claim_id,"fencing_epoch":epoch,"worker_id":worker_id,
                                "cycle_id":"134","hypothesis_id":None,"status":"paused"}}}


def test_migration_keeps_scientific_identity_and_retires_old_account_threads(tmp_path):
    root,accounts,worker,science=_migration_fixture(tmp_path)
    with mock.patch("theory_master.codex_accounts.ensure_layout",return_value={
            "1":accounts/"1","2":accounts/"2"}), \
         mock.patch("theory_master.codex_accounts.account_switch_lock",
                    side_effect=lambda:nullcontext()):
        migration=runtime._migrate_paused_worker_account(root,worker,"1")
    assert migration["preserved_science"]
    state=json.loads((root/"134/research_state.json").read_text())
    assert state["hypothesis_lock"]==science["hypothesis_lock"]
    assert state["role_outputs"]==science["role_outputs"]
    assert state["threads"]=={}
    assert state["research_lead_thread_id"] is None
    assert state["account_migration_history"][-1]["from"]=="2"
    assert state["account_migration_history"][-1]["to"]=="1"
    archive=Path(state["account_migration_history"][-1]["before_checkpoint"])
    assert json.loads(archive.read_text())["threads"]==science["threads"]
    assert (root/".theory-master/workers/worker-2/codex-home/auth.json").resolve()==accounts/"1"/"auth.json"
    assert runtime.read_worker(root,"worker-2")["account"]=="1"
    assert state["claimed_frontier_id"]=="H-0089"
    assert not (root/"135").exists()


def test_migration_rejects_stale_frontier_and_live_process_without_mutation(tmp_path):
    root,accounts,worker,science=_migration_fixture(tmp_path)
    _write(root/".theory-master/cycle_claims.json",_cycle_claims("worker-1","other",13))
    with mock.patch("theory_master.codex_accounts.ensure_layout",return_value={
            "1":accounts/"1","2":accounts/"2"}):
        with pytest.raises(ValueError,match="no longer owns"):
            runtime._migrate_paused_worker_account(root,worker,"1")
    assert json.loads((root/"134/research_state.json").read_text())==science
    with mock.patch.object(runtime,"process_identity_alive",return_value=True):
        with pytest.raises(ValueError,match="active scientific Turn"):
            runtime._migrate_paused_worker_account(root,worker,"1")
    assert runtime.read_worker(root,"worker-2")["account"]=="2"


def test_quota_watcher_prefers_migrating_paused_cycle_when_other_login_has_quota(tmp_path):
    root,accounts,worker,science=_migration_fixture(tmp_path)
    (root/".theory-master/env").write_text("THEORY_MASTER_QUOTA_AUTO_ADJUST=1\n")
    spec=SimpleNamespace(research_root=root)
    with mock.patch("theory_master.codex_accounts.account_rate_limits",
                    side_effect=lambda acct:_quota(0 if acct=="2" else 90)), \
         mock.patch.object(runtime,"worker_action",
                           return_value={"status":"STARTING","cycle":"134"}) as resume, \
         mock.patch("theory_master.worker_runtime.time.sleep",
                    side_effect=StopIteration("one pass completed")):
        with pytest.raises(StopIteration):
            runtime.watch_worker_quota(spec,"worker-2")
    resume.assert_called_once_with(spec,"worker-2","resume",account="1",provider_fallback=None)
    receipt=json.loads((root/".theory-master/workers/worker-2/last_quota_recovery.json").read_text())
    assert receipt["cycle"]=="134"
    assert not (root/"135").exists()



def test_budget_technical_failure_quota_resume_same_login_once(tmp_path):
    root = tmp_path / "research"
    (root / ".theory-master/workers/worker-2").mkdir(parents=True)
    (root / ".theory-master/env").write_text("THEORY_MASTER_QUOTA_AUTO_ADJUST=1\n")
    science = {
        "status": "needs_attention", "phase": "investigator:budget_review",
        "worker_id": "worker-2", "resumable": True,
        "last_safe_checkpoint": "checkpoints/123.json",
        "investigator_budget": {"review_index": 1},
        "attention": {"code": "investigator_budget_review_failed"},
    }
    _write(root / "184/research_state.json", science)
    _write(root / ".theory-master/workers/worker-2/runtime.json", {
        "schema": runtime.SCHEMA, "worker_id": "worker-2",
        "status": "NEEDS_ATTENTION", "account": "2", "current_cycle": "184",
    })
    spec = SimpleNamespace(research_root=root)
    with mock.patch("theory_master.codex_accounts.account_rate_limits",
                    side_effect=lambda acct: _quota(80 if acct == "1" else 55)), \
         mock.patch.object(runtime, "worker_action",
                           return_value={"status": "STARTING", "cycle": "184"}) as resume:
        assert runtime.watch_worker_quota(spec, "worker-2") == 0
        resume.assert_called_once_with(spec, "worker-2", "resume")
        assert runtime.watch_worker_quota(spec, "worker-2") == 0
        resume.assert_called_once()
    receipt = json.loads((root / ".theory-master/workers/worker-2/budget_review_quota_resume.json").read_text())
    assert receipt["account"] == "2" and receipt["status"] == "dispatched"
    assert not (root / "185").exists()


def test_budget_technical_watcher_survives_transient_quota_probe_failure(tmp_path):
    root = tmp_path / "research"
    (root / ".theory-master/workers/worker-2").mkdir(parents=True)
    (root / ".theory-master/env").write_text("THEORY_MASTER_QUOTA_AUTO_ADJUST=1\n")
    science = {
        "status": "needs_attention", "phase": "investigator:budget_review",
        "worker_id": "worker-2", "resumable": True,
        "last_safe_checkpoint": "checkpoints/184.json",
        "investigator_budget": {"review_index": 1, "rejected": False},
        "attention": {"code": "investigator_budget_review_failed"},
    }
    _write(root / "184/research_state.json", science)
    _write(root / ".theory-master/workers/worker-2/runtime.json", {
        "schema": runtime.SCHEMA, "worker_id": "worker-2",
        "status": "NEEDS_ATTENTION", "account": "2", "current_cycle": "184",
    })
    spec = SimpleNamespace(research_root=root)
    with mock.patch("theory_master.codex_accounts.account_rate_limits",
                    side_effect=[TimeoutError("temporary quota service error"), _quota(30)]) as probe, \
         mock.patch("theory_master.worker_runtime.time.sleep") as sleep, \
         mock.patch.object(runtime, "worker_action",
                           return_value={"status": "STARTING", "cycle": "184"}) as resume:
        assert runtime.watch_worker_quota(spec, "worker-2") == 0
    assert probe.call_count == 2
    sleep.assert_called_once_with(60)
    resume.assert_called_once_with(spec, "worker-2", "resume")
    diagnostic = json.loads((root / ".theory-master/workers/worker-2/quota_probe_error.json").read_text())
    assert diagnostic["error_type"] == "TimeoutError"


def test_technical_quota_watch_requires_exact_unrejected_recoverable_checkpoint():
    eligible = {
        "status": "needs_attention", "phase": "investigator:budget_review",
        "worker_id": "worker-2", "resumable": True,
        "last_safe_checkpoint": "checkpoints/184.json",
        "investigator_budget": {"review_index": 1, "rejected": False},
        "attention": {"code": "investigator_budget_review_failed"},
    }
    assert runtime._technical_review_quota_watch_eligible(eligible, "worker-2")
    for change in ({"worker_id": "worker-1"}, {"resumable": False},
                   {"last_safe_checkpoint": ""}, {"investigator_budget": {"rejected": True}},
                   {"attention": {"code": "investigator_budget_review_rejected"}},
                   {"phase": "investigator:running"}):
        assert not runtime._technical_review_quota_watch_eligible(
            {**eligible, **change}, "worker-2"
        )


def test_quota_watcher_never_overrides_budget_rejection(tmp_path):
    root = tmp_path / "research"
    (root / ".theory-master/workers/worker-2").mkdir(parents=True)
    (root / ".theory-master/env").write_text("THEORY_MASTER_QUOTA_AUTO_ADJUST=1\n")
    _write(root / "184/research_state.json", {
        "status": "needs_attention", "phase": "investigator:budget_review",
        "resumable": True, "last_safe_checkpoint": "ckpt.json",
        "attention": {"code": "investigator_budget_review_rejected"},
    })
    _write(root / ".theory-master/workers/worker-2/runtime.json", {
        "schema": runtime.SCHEMA, "worker_id": "worker-2", "account": "2",
        "status": "NEEDS_ATTENTION", "current_cycle": "184",
    })
    with mock.patch.object(runtime, "worker_action") as resume:
        assert runtime.watch_worker_quota(SimpleNamespace(research_root=root), "worker-2") == 0
        resume.assert_not_called()


def test_quota_watcher_never_migrates_unrelated_pause(tmp_path):
    root,accounts,worker,science=_migration_fixture(tmp_path)
    (root/".theory-master/env").write_text("THEORY_MASTER_QUOTA_AUTO_ADJUST=1\n")
    science["phase"]="needs_attention"
    _write(root/"134/research_state.json",science)
    spec=SimpleNamespace(research_root=root)
    with mock.patch.object(runtime,"worker_action") as resume:
        assert runtime.watch_worker_quota(spec,"worker-2")==0
    resume.assert_not_called()



def test_unfinished_native_goal_is_not_migrated_across_accounts(tmp_path):
    root,accounts,worker,state=_migration_fixture(tmp_path)
    state["phase"]="investigator:running"
    state["native_goal"]={"status":"usageLimited","thread_id":"native-private-acct2"}
    _write(root/"134/research_state.json",state)
    with mock.patch("theory_master.codex_accounts.ensure_layout",return_value={
            "1":accounts/"1","2":accounts/"2"}):
        with pytest.raises(ValueError,match="Native Goal requires same-account"):
            runtime._migrate_paused_worker_account(root,worker,"1")
    assert runtime.read_worker(root,"worker-2")["account"]=="2"
    assert json.loads((root/"134/research_state.json").read_text())==state
