from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from theory_master.backends import FakeBackend
from theory_master.specification import TaskSpec
from theory_master.scientific_model import empty_model
from theory_master.task import bootstrap
from theory_master.workflow import Workflow


def _valid_synthesis() -> dict:
    return {
        "scientific_synthesis": {"conclusions": ["result"], "open_questions": []},
        "scientific_model_update": {"action": "keep"},
    }


def _workflow(root: Path, backend: FakeBackend) -> tuple[Workflow, dict]:
    plan = root / "research_plan.md"
    plan.write_text(
        "# Goal\nG\n\n"
        "# Research Contract\nC\n\n"
        "# Final Gate\nF\n\n"
        "# Required Context\n- task-local fixture\n",
        encoding="utf-8",
    )
    binding = bootstrap(plan)
    config = json.loads(binding.config_path.read_text(encoding="utf-8"))
    config["max_cycles"] = 1
    binding.config_path.write_text(json.dumps(config), encoding="utf-8")
    workflow = Workflow(TaskSpec.from_config(binding.config_path), backend)
    workflow._ensure_cycle()
    return workflow, workflow._new_state()


def _anomalies(workflow: Workflow) -> list[dict]:
    path = workflow.cycle_dir / "important_events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class CycleAnomalyTest(unittest.TestCase):
    def test_contract_repair_retry_is_recorded_as_minor_cycle_anomaly(self):
        class Backend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = 0

            def invoke(self, role, prompt, thread_id, model, provider):
                thread = self.ensure_thread(role, thread_id, model, provider)
                if role == "research_lead":
                    self.calls += 1
                    if "Contract validation failed" in prompt:
                        return thread, json.dumps(_valid_synthesis())
                    invalid = _valid_synthesis()
                    invalid["scientific_model_update"]["action"] = "invented"
                    return thread, json.dumps(invalid)
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            backend = Backend()
            workflow, state = _workflow(Path(temp), backend)
            workflow._call(
                state, "research_lead",
                {"mode": "cycle_end", "scientific_model": empty_model(workflow.spec.goal_id)},
                fresh_thread=True,
            )
            rows = _anomalies(workflow)
            self.assertEqual([row["kind"] for row in rows], ["contract_repair_retry"])
            self.assertTrue(rows[0]["extra_llm_call"])
            self.assertEqual(rows[0]["mechanism"], "sdk_driven_llm_retry")
            self.assertEqual(rows[0]["attempt"], 1)
            self.assertEqual(rows[0]["role"], "research_lead")
            self.assertEqual(rows[0]["stage"], "cycle_synthesis")
            self.assertEqual(rows[0]["schema"], "theory-master/important-event/v1")
            self.assertEqual(rows[0]["lifecycle"], "registered")
            self.assertTrue(rows[0]["incident_id"].startswith("INC-"))
            self.assertEqual(state["minor_anomaly_count"], 1)
            self.assertEqual(state["protocol_health"], "valid")
            self.assertEqual(backend.calls, 2)

    def test_json_parse_retry_is_recorded_as_minor_cycle_anomaly(self):
        class Backend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = 0

            def invoke(self, role, prompt, thread_id, model, provider):
                thread = self.ensure_thread(role, thread_id, model, provider)
                if role == "research_lead":
                    self.calls += 1
                    if "JSON parse repair" in prompt:
                        return thread, json.dumps(_valid_synthesis())
                    return thread, "{not-json"
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            backend = Backend()
            workflow, state = _workflow(Path(temp), backend)
            workflow._call(
                state, "research_lead",
                {"mode": "cycle_end", "scientific_model": empty_model(workflow.spec.goal_id)},
                fresh_thread=True,
            )
            rows = _anomalies(workflow)
            self.assertEqual([row["kind"] for row in rows], ["json_repair_retry"])
            self.assertTrue(rows[0]["extra_llm_call"])
            self.assertEqual(rows[0]["mechanism"], "sdk_driven_llm_retry")
            self.assertEqual(state["minor_anomaly_count"], 1)
            self.assertEqual(state["protocol_health"], "valid")
            self.assertEqual(backend.calls, 2)

    def test_deterministic_json_normalization_is_recorded_without_extra_llm_call(self):
        class Backend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = 0

            def invoke(self, role, prompt, thread_id, model, provider):
                thread = self.ensure_thread(role, thread_id, model, provider)
                if role == "research_lead":
                    self.calls += 1
                    raw = json.dumps(_valid_synthesis(), separators=(",", ":"))
                    return thread, raw[:-1] + ",}"
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            backend = Backend()
            workflow, state = _workflow(Path(temp), backend)
            workflow._call(
                state, "research_lead",
                {"mode": "cycle_end", "scientific_model": empty_model(workflow.spec.goal_id)},
                fresh_thread=True,
            )
            rows = _anomalies(workflow)
            self.assertEqual([row["kind"] for row in rows], ["json_normalization"])
            self.assertFalse(rows[0]["extra_llm_call"])
            self.assertEqual(rows[0]["mechanism"], "deterministic_normalization")
            self.assertEqual(state["minor_anomaly_count"], 1)
            self.assertEqual(state["protocol_health"], "valid")
            self.assertEqual(backend.calls, 1)

    def test_terminal_status_persist_registers_both_classes_without_collector(self):
        with tempfile.TemporaryDirectory() as temp:
            workflow, state = _workflow(Path(temp), FakeBackend(reworks=0))
            self.assertIsNone(workflow.collector)
            state.update({
                "status": "needs_attention", "phase": "qualification",
                "attention": {"code": "no_qualified_candidate", "reason": "All candidates failed readiness"},
                "resumable": True, "next_executor": "research_lead",
            })
            workflow._persist(state)
            workflow._persist(state)
            rows = _anomalies(workflow)
            attention = [r for r in rows if r.get("lifecycle") == "registered"]
            self.assertEqual(len(attention), 1)
            self.assertEqual(attention[0]["code"], "no_qualified_candidate")
            self.assertEqual(attention[0]["kind"], "needs_attention")
            self.assertEqual(attention[0]["phase"], "qualification")
            state.update({
                "status": "failed", "phase": "runtime_failure",
                "recovery": {"kind": "RuntimeError", "message": "provider delivery interrupted"},
            })
            workflow._persist(state)
            workflow._persist(state)
            rows = _anomalies(workflow)
            registered = [r for r in rows if r.get("lifecycle") == "registered"]
            self.assertEqual(len(registered), 2)
            self.assertEqual(registered[1]["severity"], "error")
            self.assertEqual(registered[1]["kind"], "system_failure")
            self.assertEqual(registered[1]["code"], "run_failed")
            self.assertIn("provider delivery", registered[1]["summary"])

    def test_parallel_incident_registration_does_not_lose_unrelated_cases(self):
        from concurrent.futures import ThreadPoolExecutor
        from theory_master.observability.incident_registry import register_cycle_incident
        with tempfile.TemporaryDirectory() as temp:
            cycle = Path(temp) / "13"
            cycle.mkdir()
            def register(index):
                return register_cycle_incident(
                    cycle, severity="attention", kind="needs_attention",
                    source_event="test.concurrent", summary="concurrent attention",
                    code=f"guard-{index % 18}",
                    discriminator=f"phase-{index % 18}",
                )
            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(register, range(72)))
            rows = [json.loads(line) for line in (cycle / "important_events.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 18)
            self.assertEqual(len({x["incident_id"] for x in rows}), 18)
            self.assertEqual(sum(1 for _, created in results if created), 18)

    def test_repeated_attention_keeps_original_incident_and_records_recurrence(self):
        with tempfile.TemporaryDirectory() as temp:
            workflow, state = _workflow(Path(temp), FakeBackend(reworks=0))
            state.update({
                "status": "needs_attention", "phase": "qualification",
                "attention": {"code": "no_qualified_candidate", "reason": "candidate rejected"},
            })
            workflow._persist(state)
            state.update({"status": "active", "attention": {}})
            workflow._persist(state)
            state.update({
                "status": "needs_attention",
                "attention": {"code": "no_qualified_candidate", "reason": "second candidate rejected"},
            })
            workflow._persist(state)
            workflow._persist(state)
            rows = _anomalies(workflow)
            self.assertEqual(
                [row["lifecycle"] for row in rows],
                ["registered", "resume_attempt", "recurred"],
            )
            self.assertEqual(len({row["incident_id"] for row in rows}), 1)
            self.assertEqual(rows[-1]["state_from"], "active")
            self.assertEqual(rows[-1]["state_to"], "needs_attention")

    def test_workflow_and_collector_registration_deduplicate_one_terminal_incident(self):
        from theory_master.observability.collector import Collector
        with tempfile.TemporaryDirectory() as temp:
            workflow, state = _workflow(Path(temp), FakeBackend(reworks=0))
            collector = Collector(
                workflow.spec, system_failure_actions=False,
                attention_takeover_actions=False, cycle_audit_actions=False,
            )
            workflow.collector = collector
            collector.start_run(workflow.cycle_dir.name, workflow.spec, run_dir=workflow.cycle_dir)
            state.update({
                "status": "needs_attention", "phase": "qualification",
                "attention": {"code": "no_qualified_candidate", "reason": "no candidate qualified"},
                "resumable": True,
            })
            workflow._persist(state)
            collector.finish(state)
            rows = _anomalies(workflow)
            registered = [r for r in rows if r.get("lifecycle") == "registered"]
            self.assertEqual(len(registered), 1)
            self.assertEqual(registered[0]["source_event"], "workflow.persist")


if __name__ == "__main__":
    unittest.main()
