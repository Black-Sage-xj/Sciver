"""Native Goal usage limits must enter the managed quota switching path."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from theory_master.backends import FakeBackend
from theory_master.backend_config import parse_role_spec
from theory_master.codex_backend import CodexBackend
from theory_master.native_goal import NativeGoalIncomplete, NativeGoalDeliveryError
from theory_master.specification import TaskSpec
from theory_master.task import bootstrap
from theory_master.workflow import Workflow
from theory_master.supervisor import _quota_pause_context


class GoalQuotaBackend:
    """Non-FakeBackend class to exercise the real native Goal route."""
    def __init__(self, status):
        self.fake = FakeBackend(reworks=0)
        self.status = status
        self.thread_accounts = {}
        self.last_native_goal = {}
        self.started = []

    def ensure_thread(self, role, thread_id, model, provider):
        ident = self.fake.ensure_thread(role, thread_id, model, provider)
        if role == "investigator":
            self.thread_accounts[ident] = "2"
        return ident

    def invoke(self, role, prompt, thread_id, model, provider):
        return self.fake.invoke(role, prompt, thread_id, model, provider)

    def drop_thread(self, thread_id):
        return self.fake.drop_thread(thread_id)

    def invoke_goal(self, role, *, goal_objective, context_prompt, handoff_path,
                    thread_id, model, provider, token_allowance=None,
                    tokens_before_goal=0):
        self.started.append(thread_id)
        self.last_native_goal = {"status": self.status, "thread_id": thread_id}
        raise NativeGoalIncomplete(self.status, thread_id)


class NativeGoalQuotaTests(unittest.TestCase):
    def make_spec(self, root):
        plan = root / "research_plan.md"
        plan.write_text("# Goal\nQuota recovery\n# Research Contract\nSynthetic tests\n"
                        "# Final Gate\nIndependent Judge must assess\n# Required Context\n- fixture\n")
        return TaskSpec.from_config(bootstrap(plan).config_path)

    def test_usage_limited_enters_standard_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = self.make_spec(Path(tmp))
            backend = GoalQuotaBackend("usageLimited")
            state = Workflow(spec, backend).run()
            self.assertEqual(len(backend.started), 1)
            self.assertEqual(state["status"], "paused")
            self.assertEqual(state["phase"], "provider_quota_exhausted")
            self.assertEqual(state["recovery"]["provider"], "CodexHTTPS")
            self.assertEqual(state["recovery"]["codex_account"], "2")
            self.assertEqual(state["native_goal"]["status"], "usageLimited")
            self.assertFalse(state.get("attention"))
            self.assertTrue(state["resumable"])
            self.assertEqual(_quota_pause_context(spec.research_root)["failed_account"], "2")
            self.assertEqual(state["threads"]["investigator"], backend.started[0])
            self.assertFalse((state.get("role_outputs") or {}).get("judge"))
            self.assertEqual((state.get("hypothesis_lock") or {}).get("status"), "locked")
            self.assertTrue(Path(state["last_safe_checkpoint"]).exists())

    def test_real_native_goal_audit_records_wire_inputs_not_legacy_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = self.make_spec(Path(tmp))
            from theory_master.observability.collector import Collector
            collector = Collector(spec)
            workflow = Workflow(spec, GoalQuotaBackend("usageLimited"), collector=collector)
            workflow.run()
            events = [
                json.loads(line) for line in (Path(collector.snapshot()["run_dir"]) /
                                              "runtime_events.jsonl").read_text().splitlines()
                if line.strip()
            ]
            inputs = [
                event for event in events
                if event.get("event") == "AGENT_INPUT" and event.get("role") == "investigator"
            ]
            kinds = [event.get("input_kind") for event in inputs]
            self.assertEqual(kinds, ["native_goal_setup_turn", "native_goal_objective"])
            self.assertNotIn("Continue as TheoryMaster investigator", "\n".join(str(event.get("content")) for event in inputs))
            self.assertTrue(any("FROZEN SCIENTIFIC OBJECTIVE" in str(event.get("content"))
                                for event in inputs))

    def test_goal_science_never_enters_extra_ordinary_llm_repair_turn(self):
        class InvalidContractHandoff(GoalQuotaBackend):
            def invoke_goal(self, role, *, goal_objective, context_prompt, handoff_path,
                            thread_id, model, provider, token_allowance=None,
                            tokens_before_goal=0):
                self.last_native_goal = {"status": "complete", "thread_id": thread_id}
                return thread_id, '{"findings": [{"statement": ""}]}'

            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "investigator":
                    raise AssertionError("Ordinary Investigator LLM repair Turn prohibited")
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as tmp:
            spec = self.make_spec(Path(tmp))
            state = Workflow(spec, InvalidContractHandoff("complete")).run()
            self.assertEqual(state["status"], "needs_attention")
            self.assertEqual(state["attention"]["code"], "contract_repair_failed")
            self.assertFalse((state.get("role_outputs") or {}).get("judge"))


    def test_science_complete_but_missing_handoff_is_actionable_attention(self):
        class BrokenDeliveryBackend(GoalQuotaBackend):
            def invoke_goal(self, role, *, goal_objective, context_prompt, handoff_path,
                            thread_id, model, provider, token_allowance=None,
                            tokens_before_goal=0):
                self.started.append(thread_id)
                self.last_native_goal={"status":"complete", "thread_id":thread_id}
                raise NativeGoalDeliveryError(thread_id or "retained", "missing explicit file")

        with tempfile.TemporaryDirectory() as tmp:
            spec=self.make_spec(Path(tmp))
            backend=BrokenDeliveryBackend("complete")
            state=Workflow(spec,backend).run()
            self.assertEqual(state["status"],"needs_attention")
            self.assertEqual(state["phase"],"investigator:handoff_invalid")
            self.assertEqual(state["attention"]["code"],"investigator_handoff_invalid")
            self.assertTrue(state["resumable"])
            self.assertFalse((state.get("role_outputs") or {}).get("judge"))
            self.assertEqual(state["native_goal"]["status"],"complete")

    def test_capacity_30min_timeout_becomes_actionable_attention(self):
        class TimedOutCapacity(GoalQuotaBackend):
            def invoke_goal(self, role, *, goal_objective, context_prompt, handoff_path,
                            thread_id, model, provider, token_allowance=None,
                            tokens_before_goal=0):
                self.started.append(thread_id)
                self.last_native_goal = {
                    "status":"blocked", "thread_id":thread_id,
                    "capacity_reconnect":{
                        "status":"timeout", "retry_count":34, "limit_seconds":1800,
                        "error":"Selected model is at capacity. Please try a different model.",
                    },
                }
                raise NativeGoalIncomplete("blocked", thread_id)

        with tempfile.TemporaryDirectory() as tmp:
            spec = self.make_spec(Path(tmp))
            backend = TimedOutCapacity("blocked")
            state = Workflow(spec, backend).run()
            self.assertEqual(state["status"],"needs_attention")
            self.assertTrue(state["resumable"])
            self.assertEqual(state["attention"]["code"],"provider_model_capacity_timeout")
            self.assertIn("30 minutes",state["attention"]["reason"])
            self.assertEqual(state["attention"]["retry_count"],34)
            self.assertEqual(state["native_goal"]["capacity_reconnect"]["limit_seconds"],1800)
            self.assertFalse((state.get("role_outputs") or {}).get("judge"))

    def test_other_native_goal_stops_do_not_switch_accounts(self):
        for status in ("blocked", "paused", "budgetLimited"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                spec = self.make_spec(Path(tmp))
                state = Workflow(spec, GoalQuotaBackend(status)).run()
                self.assertEqual(state["status"], "needs_attention")
                self.assertEqual(state["attention"]["goal_status"], status)
                self.assertIsNone(_quota_pause_context(spec.research_root))


class QuotaSupervisorAndModelResumeTests(unittest.TestCase):
    def test_native_quota_pause_picks_other_known_available_account(self):
        from theory_master import supervisor
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshots = {
                "1": {"ok": True, "ordinary_usage_allowed": True},
                "2": {"ok": True, "ordinary_usage_allowed": False},
            }
            with patch.object(supervisor, "_quota_snapshots", return_value=snapshots), \
                 patch.object(supervisor, "_activate_quota_account", return_value="1") as activate:
                chosen = supervisor._resolve_quota_pause(
                    root, {"provider": "CodexHTTPS", "failed_account": "2"}
                )
            self.assertEqual(chosen, "1")
            self.assertEqual(activate.call_args.args[1], "1")
            self.assertIn("2", activate.call_args.args[3])

    def test_two_exhausted_accounts_enter_waiting_quota(self):
        from theory_master import supervisor
        from theory_master.quota_auto_adjust import read_controller_state
        snapshots = {
            "1": {"ok": True, "ordinary_usage_allowed": False},
            "2": {"ok": True, "ordinary_usage_allowed": False},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(supervisor, "_quota_snapshots", return_value=snapshots):
                chosen = supervisor._resolve_quota_pause(
                    root, {"provider": "CodexHTTPS", "failed_account": "2"}
                )
            self.assertIsNone(chosen)
            self.assertEqual(read_controller_state(root)["status"], "waiting_refresh")

    def test_lower_cost_model_applies_on_same_codex_thread_resume(self):
        import sys
        route = parse_role_spec("investigator=gpt-6-luna@CodexHTTPS")[1]
        with tempfile.TemporaryDirectory() as tmp:
            with patch("theory_master.codex_backend._codex_home", return_value=Path(tmp)):
                backend = CodexBackend(Path(tmp), routes={"investigator": route})
            backend.client_account = "1"
            resumed = SimpleNamespace(id="old-thread")
            client = Mock()
            client.thread_resume.return_value = resumed
            sdk = SimpleNamespace(
                Sandbox=SimpleNamespace(workspace_write="write", read_only="read"),
            )
            with patch.dict(sys.modules, {"openai_codex": sdk}), \
                 patch.object(backend, "_ensure_client", return_value=client):
                thread_id = backend.ensure_thread(
                    "investigator", "old-thread", route.model, route.provider,
                )
            self.assertEqual(thread_id, "old-thread")
            self.assertEqual(client.thread_resume.call_args.kwargs["model"], "gpt-6-luna")
            self.assertEqual(client.thread_resume.call_args.kwargs["model_provider"], "openai_https")
            self.assertEqual(
                client.thread_resume.call_args.kwargs["config"]["model_reasoning_effort"],
                "medium",
            )

    def test_resume_existing_fixture_thread_uses_fixture_provider_not_default_openai(self):
        import sys
        route = parse_role_spec('investigator=gpt-6-sol@FixtureProvider')[1]
        with tempfile.TemporaryDirectory() as tmp:
            with patch('theory_master.codex_backend._codex_home', return_value=Path(tmp)):
                backend = CodexBackend(Path(tmp), routes={'investigator': route})
            resumed = SimpleNamespace(id='existing-science-thread')
            client = Mock()
            client.thread_resume.return_value = resumed
            sdk = SimpleNamespace(Sandbox=SimpleNamespace(workspace_write='write', read_only='read'))
            with patch.dict(sys.modules, {'openai_codex': sdk}), \
                 patch.object(backend, '_ensure_client', return_value=client):
                identity = backend.ensure_thread('investigator', resumed.id, route.model, route.provider)
            self.assertEqual(identity, resumed.id)
            self.assertEqual(client.thread_resume.call_args.kwargs['model_provider'], 'FixtureProvider')
            self.assertEqual(client.thread_resume.call_args.kwargs['model'], 'gpt-6-sol')
            client.thread_start.assert_not_called()
