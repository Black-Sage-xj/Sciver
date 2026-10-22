from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.attention_guidance import load_attention_guidance, mark_guidance_consumed, write_attention_guidance
from theory_master.attention_takeover import attention_takeover_enabled, write_attention_takeover_setting
from theory_master.backends import FakeBackend
from theory_master.observability.attention_relay import spawn_attention_takeover_agent
from theory_master.observability.collector import Collector
from theory_master.specification import TaskSpec
from theory_master.workflow import Workflow
from theory_master.supervisor import _mark_unrecoverable
from theory_master.task import bootstrap


class AttentionTakeoverTest(unittest.TestCase):
    def make(self, root: Path) -> TaskSpec:
        plan = root / "research_plan.md"
        plan.write_text(
            "# Goal\ng\n\n"
            "# Research Contract\nc\n\n"
            "# Final Gate\nf\n\n"
            "# Required Context\n- r\n",
            encoding="utf-8",
        )
        binding = bootstrap(plan)
        return TaskSpec.from_config(binding.config_path)

    def test_takeover_defaults_on_and_task_env_is_hot_setting(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=False):
            research = Path(temp) / "research"
            research.mkdir()
            os.environ.pop("THEORY_MASTER_ATTENTION_TAKEOVER", None)
            self.assertTrue(attention_takeover_enabled(research))
            write_attention_takeover_setting(research, False)
            self.assertFalse(attention_takeover_enabled(research))
            write_attention_takeover_setting(research, True)
            self.assertTrue(attention_takeover_enabled(research))

    def test_operator_guidance_is_task_local_and_consumable(self):
        with tempfile.TemporaryDirectory() as temp:
            cycle = Path(temp) / "4"
            cycle.mkdir()
            payload = write_attention_guidance(cycle, "Retry using artifact A.", source="human", attention_code="needs_input")
            self.assertEqual(load_attention_guidance(cycle)["text"], "Retry using artifact A.")
            consumed = mark_guidance_consumed(cycle, payload, role="research_lead", stage="frontier_proposal")
            self.assertEqual(consumed["status"], "consumed")
            self.assertEqual(load_attention_guidance(cycle)["status"], "consumed")

    def test_operator_guidance_is_delivered_to_exactly_next_agent_turn(self):
        class CaptureBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.prompts = []
            def invoke(self, role, prompt, thread_id, model, provider):
                self.prompts.append((role, prompt))
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec = self.make(Path(temp))
            backend = CaptureBackend()
            workflow = Workflow(spec, backend)
            workflow._ensure_cycle()
            state = workflow._new_state()
            guidance = write_attention_guidance(
                workflow.cycle_dir, "Use artifact A first.", source="human", attention_code="needs_input",
            )
            state["operator_guidance"] = guidance
            state["operator_guidance_pending"] = True
            workflow._call(state, "research_lead", {
                "prior_cycle": {}, "scientific_model": {}, "frontier_state": {},
                "canvas_graph_overview": {}, "current_node_neighborhood": {},
            })
            context = json.loads(backend.prompts[0][1].split("Context:\n", 1)[1])
            self.assertEqual(context["operator_guidance"]["text"], "Use artifact A first.")
            self.assertFalse(state["operator_guidance_pending"])
            self.assertEqual(state["attention_guidance"]["status"], "consumed")

    def test_collector_spawns_takeover_once_for_needs_attention(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = self.make(Path(temp))
            collector = Collector(spec, attention_takeover_actions=True)
            run_dir = spec.research_root / "tm-0001-attention-takeover-test"
            collector.start_run(run_dir.name, spec, run_dir=run_dir)
            sci = {
                "status": "needs_attention", "phase": "qualification", "execution_active": False,
                "resumable": True, "attention": {"code": "no_candidate", "reason": "none qualified"},
            }
            with patch("theory_master.observability.attention_relay.spawn_attention_takeover_agent") as spawn:
                collector.finish(sci)
                collector.finish(sci)
            spawn.assert_not_called()
            snap = collector.snapshot()
            self.assertTrue(snap["attention_takeover_enabled"])
            self.assertNotEqual((snap.get("attention_takeover_handoff") or {}).get("status"), "started")
            rows = [json.loads(line) for line in (run_dir / "important_events.jsonl").read_text(encoding="utf-8").splitlines()]
            attention_rows = [row for row in rows if row.get("lifecycle") == "registered" and row.get("severity") == "attention"]
            self.assertEqual(len(attention_rows), 1)
            self.assertEqual(attention_rows[0]["code"], "no_candidate")

    def test_workflow_hot_enable_on_stopped_attention_spawns_takeover(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = self.make(Path(temp))
            workflow = Workflow(spec, FakeBackend(reworks=0))
            workflow._ensure_cycle()
            cycle = workflow.cycle_dir
            state = workflow._new_state()
            state.update({
                "status": "needs_attention", "phase": "qualification",
                "execution_active": False, "resumable": True,
                "attention": {"code": "needs_input", "reason": "need direction"},
            })
            (cycle / "research_state.json").write_text(json.dumps(state), encoding="utf-8")
            (cycle / "run_state.json").write_text(json.dumps({
                "run_id": cycle.name, "cycle_id": cycle.name, "run_dir": str(cycle),
                "status": "NEEDS_ATTENTION", "attention": state["attention"],
            }), encoding="utf-8")
            with patch("theory_master.observability.attention_relay.spawn_attention_takeover_agent") as spawn:
                result = workflow.set_attention_takeover(True)
            spawn.assert_not_called()
            self.assertTrue(result["enabled"])
            self.assertEqual(result["handoff"], "sdk:pending_managed_resume")
            self.assertTrue(attention_takeover_enabled(spec.research_root))

    def test_workflow_submit_guidance_persists_for_next_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = self.make(Path(temp))
            workflow = Workflow(spec, FakeBackend(reworks=0))
            workflow._ensure_cycle()
            cycle = workflow.cycle_dir
            state = workflow._new_state()
            state.update({
                "status": "needs_attention", "phase": "qualification",
                "execution_active": False, "resumable": True,
                "attention": {"code": "needs_input", "reason": "need direction"},
            })
            (cycle / "research_state.json").write_text(json.dumps(state), encoding="utf-8")
            (cycle / "run_state.json").write_text(json.dumps({"run_id": cycle.name, "status": "NEEDS_ATTENTION"}), encoding="utf-8")
            payload = workflow.submit_attention_guidance("Use verified artifact A, then retry.")
            self.assertEqual(payload["status"], "pending")
            self.assertEqual(load_attention_guidance(cycle)["text"], "Use verified artifact A, then retry.")
            saved = json.loads((cycle / "research_state.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["attention_guidance"]["source"], "human")
            self.assertEqual(saved["attention"]["guidance_status"], "provided")

    def test_supervisor_generated_needs_attention_uses_same_takeover_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "research"
            cycle = root / "1"
            cycle.mkdir(parents=True)
            (cycle / ".theory-master-cycle").write_text("theory-master/v2\nattention-takeover-test\n", encoding="utf-8")
            (cycle / "research_state.json").write_text(json.dumps({
                "status": "active", "phase": "starting", "execution_active": False,
                "owner_pid": None, "resumable": False, "role_outputs": {},
            }), encoding="utf-8")
            (cycle / "run_state.json").write_text(json.dumps({"run_id": "1"}), encoding="utf-8")
            (root / ".theory-master").mkdir()
            (root / ".theory-master" / "env").write_text("THEORY_MASTER_ATTENTION_TAKEOVER=1\n", encoding="utf-8")
            with patch("theory_master.observability.attention_relay.spawn_attention_takeover_agent") as spawn:
                _mark_unrecoverable(root)
            spawn.assert_not_called()
            run_state = json.loads((cycle / "run_state.json").read_text(encoding="utf-8"))
            self.assertEqual(run_state["status"], "NEEDS_ATTENTION")
            self.assertTrue(run_state["attention_takeover_enabled"])
            self.assertEqual(run_state["attention_takeover_handoff"]["status"], "sdk_internal_hold")
            rows = [json.loads(line) for line in (cycle / "important_events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len([row for row in rows if row.get("severity") == "attention" and row.get("lifecycle") == "registered"]), 1)
            self.assertEqual(rows[0]["code"], "runtime_no_recoverable_checkpoint")

    def test_budget_change_lifts_hard_attention_preflight(self):
        from theory_master.observability.attention_relay import _hard_guard_still_active
        with tempfile.TemporaryDirectory() as temp:
            research = Path(temp)
            cycle = research / "5"
            cycle.mkdir()
            (cycle / "run_state.json").write_text(json.dumps({
                "cycle_cost": {"amount": 8.2}, "cycle_tokens_total": 13_000_000,
            }), encoding="utf-8")
            with patch.dict(os.environ, {"THEORY_MASTER_MAX_CYCLE_COST_USD": "5",
                                          "THEORY_MASTER_MAX_CYCLE_TOKENS": "10000000"}):
                self.assertTrue(_hard_guard_still_active(cycle, "cycle_cost_budget_exhausted"))
                self.assertTrue(_hard_guard_still_active(cycle, "cycle_token_budget_exhausted"))
                task_dir = research / ".theory-master"
                task_dir.mkdir()
                (task_dir / "env").write_text(
                    "THEORY_MASTER_MAX_CYCLE_COST_USD=10\n"
                    "THEORY_MASTER_MAX_CYCLE_TOKENS=20000000\n")
                self.assertFalse(_hard_guard_still_active(cycle, "cycle_cost_budget_exhausted"))
                self.assertFalse(_hard_guard_still_active(cycle, "cycle_token_budget_exhausted"))
                self.assertTrue(_hard_guard_still_active(cycle, "investigator_internal_turn_limit"))

    def test_unchanged_hard_guard_never_spawns_repair_codex(self):
        with tempfile.TemporaryDirectory() as temp:
            cycle = Path(temp) / "5"
            cycle.mkdir()
            state = {"run_dir": str(cycle), "run_id": "5",
                     "phase": "investigator:internal_turn_limit",
                     "status": "NEEDS_ATTENTION",
                     "attention": {"code": "investigator_internal_turn_limit",
                                   "reason": "12 committed historical turns"}}
            first = spawn_attention_takeover_agent(state)
            second = spawn_attention_takeover_agent(state)
            self.assertEqual(first, second)
            self.assertTrue(first.endswith(":sdk_internal"))
            rows = [json.loads(line) for line in (cycle / "important_events.jsonl").read_text().splitlines()]
            self.assertEqual(len([row for row in rows if row.get("lifecycle") == "registered"]), 1)

    def test_attention_compatibility_is_sdk_only_even_with_legacy_started_record(self):
        with tempfile.TemporaryDirectory() as temp:
            cycle = Path(temp) / "4"
            cycle.mkdir()
            state = {
                "run_dir": str(cycle), "run_id": "4", "goal_id": "g",
                "status": "NEEDS_ATTENTION",
                "attention": {"code": "needs_input", "reason": "need a scientific direction"},
            }
            first = spawn_attention_takeover_agent(state)
            second = spawn_attention_takeover_agent(state)
            self.assertEqual(first, second)
            self.assertTrue(first.endswith(":sdk_internal"))
            self.assertFalse((cycle / "theory_master_attention_relay.md").exists())
            rows = [json.loads(line) for line in (cycle / "important_events.jsonl").read_text().splitlines()]
            self.assertEqual([row["lifecycle"] for row in rows], ["registered"])


if __name__ == "__main__":
    unittest.main()
