"""Offline Cycle-boundary account selection and account-aware tier-2 routing."""
from __future__ import annotations

import json
import logging
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from theory_master.account_model_routing import routes_for_account
from theory_master.backend_config import RoleRoute
from theory_master.codex_backend import CodexBackend
from theory_master.quota_auto_adjust import proactive_switch_target


def quota(primary: float, secondary: float, *, allowed: bool = True) -> dict:
    return {
        "ok": True, "ordinary_usage_allowed": allowed,
        "primary": {"remaining_percent": primary},
        "secondary": {"remaining_percent": secondary},
    }


def tier2() -> dict[str, RoleRoute]:
    return {
        "research_lead": RoleRoute("gpt-6.1-sol", "CodexHTTPS", reasoning_effort="high"),
        "challenger": RoleRoute("gpt-6.1-sol", "CodexHTTPS", reasoning_effort="medium"),
        "investigator": RoleRoute("gpt-6.1-sol", "CodexHTTPS", reasoning_effort="low"),
        "judge": RoleRoute("gpt-6.1-sol", "CodexHTTPS", reasoning_effort="medium"),
        "literature": RoleRoute("gpt-6-luna", "CodexHTTPS", reasoning_effort="low"),
        "reporter": RoleRoute("gpt-6-luna", "CodexHTTPS", reasoning_effort="low"),
    }


class QuotaThresholdPolicyTest(unittest.TestCase):
    def test_strict_30_threshold_and_compare_both_windows(self):
        cases = [
            (29.9, 80, 50, 50, "2"),
            (80, 29.9, 50, 50, "2"),
            (30, 80, 90, 90, None),
            (29.9, 80, 29.8, 90, None),
            (29.9, 80, 29.9, 90, None),
            (15, 60, 16, 60, "2"), # both low: switch to less constrained
            (15, 60, 60, 14, None),
            (90, 90, 99, 99, None),
        ]
        for a5,a7,b5,b7,want in cases:
            with self.subTest(current=(a5,a7),other=(b5,b7)):
                self.assertEqual(proactive_switch_target(
                    {"1":quota(a5,a7),"2":quota(b5,b7)},"1"
                ),want)

    def test_unknown_failed_or_unavailable_probes_fail_closed(self):
        self.assertIsNone(proactive_switch_target({"1":quota(10,80),"2":{"ok":False}}, "1"))
        self.assertIsNone(proactive_switch_target({"1":{"ok":False},"2":quota(90,90)}, "1"))
        self.assertIsNone(proactive_switch_target({"1":quota(10,80),
            "2":{"ok":True,"ordinary_usage_allowed":True,"primary":{"remaining_percent":60},
                 "secondary":{"remaining_percent":None}}}, "1"))
        self.assertIsNone(proactive_switch_target({"1":quota(10,80),"2":quota(70,70,allowed=False)}, "1"))
        self.assertIsNone(proactive_switch_target({"1":quota(0,0,allowed=False),"2":quota(70,70)}, "1"))
        self.assertIsNone(proactive_switch_target({}, None))
        for invalid in (True,float("nan"),float("inf"),-1,101):
            with self.subTest(invalid=invalid):
                self.assertIsNone(proactive_switch_target({"1":quota(10,80),
                    "2":quota(invalid,80)},"1"))

    def test_single_reported_window_and_symmetry(self):
        self.assertEqual(proactive_switch_target({
            "1":{"ok":True,"ordinary_usage_allowed":True,"primary":{"remaining_percent":24}},
            "2":{"ok":True,"ordinary_usage_allowed":True,"primary":{"remaining_percent":25}},
        },"1"),"2")
        self.assertEqual(proactive_switch_target({
            "1":quota(60,70), "2":quota(20,80)
        },"2"),"1")


class ModelCeilingTest(unittest.TestCase):
    def test_account2_lowers_only_unavailable_models_not_effort(self):
        selected=routes_for_account(tier2(),"2")
        self.assertEqual([selected[n].model for n in ("research_lead","challenger","investigator","judge")],
                         ["gpt-6-sol"]*4)
        self.assertEqual(selected["research_lead"].reasoning_effort,"high")
        self.assertEqual(selected["investigator"].reasoning_effort,"low")
        self.assertEqual(selected["literature"].model,"gpt-6-luna")
        self.assertEqual(selected["reporter"].model,"gpt-6-luna")
        self.assertEqual(routes_for_account(selected,"1")["research_lead"].model,"gpt-6-sol")
        # Caller must re-read preferred profile every new Cycle for upgrade.
        self.assertEqual(routes_for_account(tier2(),"1")["research_lead"].model,"gpt-6.1-sol")

    def test_other_providers_and_explicit_lower_model_are_preserved(self):
        preferred=tier2()
        preferred["investigator"]=RoleRoute("gpt-6-sol","CodexHTTPS",reasoning_effort="low")
        preferred["reporter"]=RoleRoute("gpt-6.1-sol","FixtureProvider",reasoning_effort="low")
        selected=routes_for_account(preferred,"2")
        self.assertEqual(selected["investigator"],preferred["investigator"])
        self.assertEqual(selected["reporter"],preferred["reporter"])


class CodexCycleBoundaryTest(unittest.TestCase):
    def make_backend(self,root:Path,*,enabled:bool=True)->CodexBackend:
        d=root/".theory-master";d.mkdir(exist_ok=True)
        (d/"env").write_text(f"THEORY_MASTER_QUOTA_AUTO_ADJUST={int(enabled)}\n",encoding="utf-8")
        backend=object.__new__(CodexBackend)
        backend._quota_task_root=root
        backend._logger=logging.getLogger(__name__)
        backend.client=Mock()
        backend.client_account="1"
        backend.threads={"old":object()}
        backend.thread_accounts={"old":"1"}
        backend._new_thread_ids={"new"}
        return backend

    def test_cycle_switch_changes_client_and_role_model(self):
        with tempfile.TemporaryDirectory() as temp:
            backend=self.make_backend(Path(temp))
            old=backend.client
            snapshots={"1":quota(27,70),"2":quota(60,60)}
            def read(n):return snapshots[n]
            with patch("theory_master.codex_accounts.account_rate_limits",side_effect=read) as poll,\
                 patch("theory_master.codex_accounts.selected_account",return_value="1"),\
                 patch("theory_master.codex_accounts.account_switch_lock",return_value=nullcontext()),\
                 patch("theory_master.codex_accounts.use_account") as switch:
                routes=backend.prepare_cycle_account(tier2())
            switch.assert_called_once_with("2",restart_app=False,_lock_held=True)
            self.assertEqual(poll.call_count,2)
            old.close.assert_called_once()
            self.assertIsNone(backend.client)
            self.assertEqual(backend.thread_accounts,{})
            self.assertEqual(routes["research_lead"].model,"gpt-6-sol")
            record=json.loads((Path(temp)/".theory-master"/"quota-auto-adjust.json").read_text())
            self.assertEqual(record["status"],"cycle_boundary_switched")
            self.assertEqual(record["selected_account"],"2")

    def test_no_quota_switch_mid_cycle_and_non_account_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            backend=self.make_backend(Path(temp))
            with patch("theory_master.codex_accounts.account_rate_limits") as probe,\
                 patch("theory_master.codex_accounts.selected_account",return_value="1"):
                # Thread attachment performs no quota polling / identity rotation.
                import inspect
                self.assertNotIn("maybe_adjust_quota_account",inspect.getsource(backend.ensure_thread))
                routes=backend.prepare_cycle_account(
                    {"research_lead":RoleRoute("gpt-5.6-sol","FixtureProvider")})
                self.assertEqual(routes["research_lead"].model,"gpt-5.6-sol")
                probe.assert_not_called()

    def test_disabled_auto_adjust_still_adapts_manually_selected_account(self):
        with tempfile.TemporaryDirectory() as temp:
            backend=self.make_backend(Path(temp),enabled=False)
            old=backend.client
            with patch("theory_master.codex_accounts.account_rate_limits") as poll,\
                 patch("theory_master.codex_accounts.selected_account",return_value="2"):
                routes=backend.prepare_cycle_account(tier2())
            poll.assert_not_called()
            self.assertEqual(routes["judge"].model,"gpt-6-sol")
            old.close.assert_called_once()

    def test_new_cycle_refresh_applies_account2_model_ceiling(self):
        from theory_master.workflow import Workflow
        with tempfile.TemporaryDirectory() as temp:
            backend=self.make_backend(Path(temp),enabled=False)
            wf=Workflow.__new__(Workflow)
            wf.backend=backend
            wf.routes=tier2()
            wf.route_resolver=lambda: tier2()
            wf._install_cycle_routes=Mock()
            with patch("theory_master.codex_accounts.selected_account",return_value="2"):
                wf._refresh_next_cycle_routes()
            effective=wf._install_cycle_routes.call_args.args[0]
            self.assertEqual(effective["judge"].model,"gpt-6-sol")
            self.assertEqual(effective["judge"].reasoning_effort,"medium")
            self.assertEqual(wf._cycle_codex_account,"2")

    def test_quota_resume_reduces_model_but_preserves_original_ledger(self):
        from theory_master.workflow import Workflow
        with tempfile.TemporaryDirectory() as temp:
            wf=Workflow.__new__(Workflow)
            wf.cycle_dir=Path(temp)
            wf.routes=tier2()
            wf.route_resolver=lambda: tier2()
            wf.resume_route_overrides={}
            wf._install_cycle_routes=Mock()
            state={
                "runtime_role_routes":{
                    role:{"model":route.model,"provider":route.provider,
                          "reasoning_effort":route.reasoning_effort}
                    for role,route in tier2().items()
                },
                "runtime_codex_account":"1",
                "recovery":{"code":"provider_quota_exhausted"},
            }
            original=json.dumps(state["runtime_role_routes"],sort_keys=True)
            with patch("theory_master.codex_accounts.selected_account",return_value="2"):
                wf._restore_resumed_cycle_routes(state)
            effective=wf._install_cycle_routes.call_args.args[0]
            self.assertEqual(effective["research_lead"].model,"gpt-6-sol")
            self.assertEqual(effective["research_lead"].reasoning_effort,"high")
            self.assertEqual(state["runtime_account_failover"]["from_account"],"1")
            self.assertEqual(state["runtime_account_failover"]["to_account"],"2")
            self.assertEqual(json.dumps(state["runtime_role_routes"],sort_keys=True),original)
            self.assertEqual(state["runtime_hot_role_routes"]["judge"]["model"],"gpt-6-sol")
            # A non-quota recovery is never permission to rewrite frozen routes.
            state["recovery"]={"code":"run_failed"}
            state.pop("runtime_hot_role_routes")
            wf._install_cycle_routes.reset_mock()
            with patch("theory_master.codex_accounts.selected_account",return_value="2"):
                wf._restore_resumed_cycle_routes(state)
            self.assertEqual(wf._install_cycle_routes.call_args.args[0]["judge"].model,
                             "gpt-6.1-sol")

    def test_worker_pinned_migration_uses_worker_account_not_global_selector(self):
        from theory_master.workflow import Workflow
        with tempfile.TemporaryDirectory() as temp:
            wf = Workflow.__new__(Workflow)
            wf.cycle_dir = Path(temp)
            wf.routes = tier2()
            wf.route_resolver = lambda: tier2()
            wf.resume_route_overrides = {}
            wf.worker_id = "worker-2"
            wf._install_cycle_routes = Mock()
            state = {
                "runtime_role_routes": {
                    role: {"model": route.model, "provider": route.provider,
                           "reasoning_effort": route.reasoning_effort}
                    for role, route in tier2().items()
                },
                "runtime_codex_account": "1",
                # The failure changed after migration; recovery.code is no longer
                # provider_quota_exhausted, but the durable migration still applies.
                "recovery": {"kind": "NativeGoalRuntimeFailure", "code": "run_failed"},
                "account_migration_history": [{
                    "from": "1", "to": "2",
                    "mode": "fresh_identity_threads_same_frozen_cycle",
                }],
            }
            original = json.dumps(state["runtime_role_routes"], sort_keys=True)
            with patch.dict("os.environ", {"THEORYMASTER_WORKER_PINNED_ACCOUNT": "2"}), \
                 patch("theory_master.codex_accounts.selected_account", return_value="1"):
                wf._restore_resumed_cycle_routes(state)

            effective = wf._install_cycle_routes.call_args.args[0]
            self.assertEqual(effective, routes_for_account(tier2(), "2"))
            self.assertEqual(state["runtime_account_failover"]["from_account"], "1")
            self.assertEqual(state["runtime_account_failover"]["to_account"], "2")
            self.assertEqual(state["runtime_account_failover"]["reason"], "worker_account_migration")
            self.assertEqual(json.dumps(state["runtime_role_routes"], sort_keys=True), original)
            self.assertEqual(state["runtime_hot_role_routes"]["research_lead"]["model"], "gpt-6-sol")

    def test_probe_failed_does_not_block_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            backend=self.make_backend(Path(temp))
            with patch("theory_master.codex_accounts.account_rate_limits",side_effect=TimeoutError("timeout")),\
                 patch("theory_master.codex_accounts.selected_account",return_value="1"),\
                 patch("theory_master.codex_accounts.use_account") as switch:
                routes=backend.prepare_cycle_account(tier2())
            switch.assert_not_called()
            self.assertEqual(routes["research_lead"].model,"gpt-6.1-sol")


if __name__=="__main__":
    unittest.main()
