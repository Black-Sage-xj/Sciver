from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from theory_master.core import FakeBackend, TaskSpec, Workflow
from theory_master.specification import list_cycle_dirs
from theory_master.backends import RoleRoute
from theory_master.observability.collector import Collector, FileSource
from theory_master.tui.render import render_activity, render_attention, render_header, render_investigation, render_last, render_logs, render_workflow


class ObservabilityTest(unittest.TestCase):
    def test_collector_persists_actual_runtime_switches(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            collector = Collector(
                spec,
                system_failure_actions=True,
                cycle_audit_actions=False,
            )
            run_dir = spec.research_root / "tm-0000-test-goal"
            collector.start_run("tm-0000-test-goal", spec, run_dir=run_dir)
            snap = collector.snapshot()
            self.assertTrue(snap["system_failure_actions_enabled"])
            self.assertFalse(snap["cycle_audit_actions_enabled"])
            persisted = json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))
            self.assertTrue(persisted["system_failure_actions_enabled"])
            self.assertFalse(persisted["cycle_audit_actions_enabled"])


    def make(self, root: Path, reworks: int = 1):
        plan = root / "research_plan.md"
        control = root / "control.canvas"
        research = root / "research"
        control.write_text(json.dumps({"nodes": [{"id": "human", "type": "text", "text": f"<!-- theory-master-task-document: {plan} -->\nkeep me", "x": 9, "y": 8, "width": 1, "height": 1}], "edges": []}), encoding="utf-8")
        plan.write_text(
            '# Goal\n\ng\n\n# Research Contract\n\nc\n\n# Final Gate\n\nReproducible result.\n\n# Required Context\n\n- r\n',
            encoding="utf-8",
        )
        return TaskSpec.load(plan), FakeBackend(reworks)

    def test_run_state_answers_cockpit_questions(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp))
            # Exercise the complete five-Cycle smoke run so the final verdict
            # is observable; explicitly pin the current task budget rather than
            # inheriting a historical three-Cycle expectation.
            config_path = Path(spec.config_path)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["max_cycles"] = 5
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            spec = TaskSpec.from_config(config_path)
            collector = Collector(spec)
            state = Workflow(spec, backend, collector=collector).run()
            snap = collector.snapshot()
            self.assertEqual(state["status"], "over")
            self.assertEqual(snap["status"], "OVER")
            self.assertFalse(snap["execution_active"])
            self.assertEqual(snap["master_run_id"], spec.master_id)
            self.assertEqual(snap["goal_id"], spec.goal_id)
            self.assertEqual(snap["hypothesis"], "smoke-h")
            self.assertEqual(snap["cycle_index"], 5)
            self.assertEqual(snap["max_cycles"], 5)
            self.assertEqual(snap["cycle_index"], snap["max_cycles"])
            self.assertTrue(snap["agents"]["research_lead"]["thread_id"])
            self.assertEqual(snap["agents"]["research_lead"]["state"], "done")
            self.assertEqual(snap["agents"]["literature"]["state"], "idle")
            self.assertFalse(snap["literature_requested"])
            self.assertGreaterEqual(len(snap["investigation_rounds"]), 1)
            self.assertTrue(all(
                (row.get("executor_role") or row.get("role")) == "investigator"
                for row in snap["investigation_rounds"]
            ))
            self.assertEqual(snap["rework_count"], 0)
            # The cockpit's last_verdict is the Final Gate assessment;
            # the deterministic fixture intentionally leaves that gate open.
            self.assertEqual(snap["last_verdict"], "not_passed")
            self.assertNotIn("critical_issues", snap)
            self.assertNotIn("investigation_requirements", state)
            self.assertEqual(state["initial_executor"], "investigator")
            self.assertGreaterEqual(snap["tokens_total"] or 0, 1)
            run_dir = Path(snap["run_dir"])
            self.assertTrue((run_dir / "run_state.json").is_file())
            self.assertTrue((run_dir / "runtime_events.jsonl").is_file())
            self.assertTrue((run_dir / "orchestrator.log").is_file())
            self.assertTrue((run_dir / "runtime_summary.json").is_file())
            event_files = [path / "runtime_events.jsonl" for path in list_cycle_dirs(spec.research_root, task_mode=True) if (path / "runtime_events.jsonl").is_file()]
            events = [json.loads(line) for path in event_files for line in path.read_text().splitlines() if line.strip()]
            kinds = {event["event"] for event in events}
            self.assertIn("LLM_TURN_STARTED", kinds)
            self.assertIn("LLM_TURN_COMPLETED", kinds)
            self.assertIn("AGENT_INPUT", kinds)
            agent_inputs = [event for event in events if event.get("event") == "AGENT_INPUT"]
            self.assertTrue(agent_inputs)
            self.assertTrue(all(str(event.get("content") or "").strip() for event in agent_inputs))
            self.assertTrue(any(event.get("stage") == "frontier_proposal" for event in agent_inputs))
            self.assertIn("JUDGE_REWORK_REQUESTED", kinds)
            header = render_header(snap)
            self.assertIn("Started", header)
            self.assertIn("Cycle start", header)
            self.assertIn("OVER", header)
            self.assertIn("Process STOPPED", header)
            workflow_text = render_workflow(snap)
            self.assertIn("Investigator", workflow_text)
            self.assertIn("MODEL", workflow_text)
            self.assertIn("STATE", workflow_text)
            self.assertIn("luna", workflow_text.lower())
            self.assertNotIn("THREAD", workflow_text)
            self.assertNotIn(snap["agents"]["research_lead"]["thread_id"], render_workflow(snap))
            self.assertIn("Round", render_investigation(snap))
            activity = render_activity(snap)
            self.assertIn("Agent", activity)
            self.assertNotIn("Thread", activity)
            self.assertNotIn("Turn", activity)
            log_text = (run_dir / "orchestrator.log").read_text()
            self.assertIn("Research Lead started", log_text)
            self.assertNotRegex(log_text, r"\d{4}-\d{2}-\d{2}T")
            self.assertRegex(log_text.splitlines()[0], r"^\d{2}-\d{2}T\d{2}:\d{2}:\d{2}  ")
            self.assertNotIn(snap["agents"]["research_lead"]["thread_id"], log_text)
            self.assertEqual(render_logs(["2026-09-20T22:07:35+08:00 [INFO] Research Lead started"]), "09-20T22:07:35  Research Lead started")
            attached = FileSource(spec.research_root).snapshot()
            self.assertEqual(attached["run_id"], snap["run_id"])
            sci = [json.loads(line) for line in (Path(snap["run_dir"]) / "events.jsonl").read_text().splitlines()]
            self.assertEqual([event["seq"] for event in sci], list(range(1, len(sci) + 1)))
            self.assertTrue(all("type" in event for event in sci))

    def test_agent_input_persists_exact_prompt_for_conversation_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            collector = Collector(spec)
            run_dir = spec.research_root / "tm-0000-test-goal"
            collector.start_run("tm-0000-test-goal", spec, run_dir=run_dir)
            prompt = "Stage card\n\nScientific Model:\nH1 → H2"
            collector.on_agent_input(role="research_lead", stage="frontier_proposal", content=prompt, input_kind="task")
            events = collector.runtime_events(limit=0)
            recorded = next(event for event in events if event.get("event") == "AGENT_INPUT")
            self.assertEqual(recorded["role"], "research_lead")
            self.assertEqual(recorded["stage"], "frontier_proposal")
            self.assertEqual(recorded["input_kind"], "task")
            self.assertEqual(recorded["content"], prompt)
            self.assertNotIn("thread_id", recorded)
            self.assertNotIn("turn_id", recorded)

    def test_last_action_tracks_cycle_start_live_tool_and_rework_history(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            collector = Collector(spec)
            run_dir = spec.research_root / "tm-0000-test-goal"
            collector.start_run("tm-0000-test-goal", spec, run_dir=run_dir)
            snap = collector.snapshot()
            self.assertEqual(snap["action_history"][-1]["event"], "CYCLE_STARTED")
            self.assertIn("tm-0000-test-goal", snap["action_history"][-1]["text"])

            collector.on_codex(
                {"event": "TOOL_ACTIVITY", "tool": "command", "detail": "python check.py"},
                role="investigator", thread_id="thr-investigation",
            )
            live = collector.snapshot()
            self.assertEqual(live["last_action"]["event"], "TOOL_ACTIVITY")
            self.assertIn("Investigator · command · python check.py", live["last_action"]["text"])
            self.assertEqual(len(live["action_history"]), 1)

            collector._emit({
                "event": "JUDGE_REWORK_REQUESTED", "target_agent": "investigator",
                "judge_retry_index": 1, "investigation_attempt": 2,
            })
            collector._flush_state()
            reworked = collector.snapshot()
            self.assertEqual(reworked["action_history"][-1]["event"], "JUDGE_REWORK_REQUESTED")
            self.assertEqual(reworked["last_action"]["text"], "Judge returned → Investigator for rework #1")
            persisted = json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["last_action"]["text"], "Judge returned → Investigator for rework #1")

            next_dir = spec.research_root / "tm-0001-test-goal"
            collector.start_run("tm-0001-test-goal", spec, run_dir=next_dir)
            second = collector.snapshot()
            cycle_starts = [item for item in second["action_history"] if item.get("event") == "CYCLE_STARTED"]
            self.assertEqual(len(cycle_starts), 2)
            self.assertIn("tm-0001-test-goal", cycle_starts[-1]["text"])

    def test_skill_reads_emit_runtime_telemetry_and_aggregate_per_agent(self):
        from theory_master.skills import role_skills

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            collector = Collector(spec)
            run_dir = spec.research_root / "tm-0000-test-goal"
            collector.start_run("tm-0000-test-goal", spec, run_dir=run_dir)
            skill_path = role_skills("investigator")[2].skill_md
            for turn_id in ("turn-1", "turn-2"):
                collector.on_codex(
                    {"event": "TOOL_ACTIVITY", "tool": "command", "detail": f"sed -n 1,80p {skill_path}", "turn_id": turn_id},
                    role="investigator", thread_id="thr-investigator",
                )
            snap = collector.snapshot()
            agent = snap["agents"]["investigator"]
            self.assertEqual(agent["skills_used"], ["symbolic"])
            self.assertEqual(agent["skill_load_events"], 2)
            self.assertEqual(agent["skill_load_counts"], {"symbolic": 2})
            self.assertEqual(snap["action_history"][-1]["event"], "SKILL_LOADED")
            events = [event for event in collector.runtime_events(limit=0) if event.get("event") == "SKILL_LOADED"]
            self.assertEqual([event["load_index"] for event in events], [1, 2])
            self.assertTrue(all(event["detection"] == "observed_file_read" for event in events))
            self.assertFalse((run_dir / "events.jsonl").exists())

            collector.finish()
            summary = json.loads((run_dir / "runtime_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["agents"]["investigator"]["skills_used"], ["symbolic"])
            self.assertEqual(summary["agents"]["investigator"]["skill_load_events"], 2)

    def test_codex_token_usage_produces_official_price_estimate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec, _ = self.make(root, reworks=0)
            cache = root / "pricing.json"
            cache.write_text(json.dumps({
                "schema": "theory-master/pricing/v1",
                "source_url": "https://developers.openai.com/api/docs/pricing",
                "currency": "USD",
                "long_context_threshold_tokens": 272000,
                "fetched_at": "2026-09-26T00:00:00+00:00",
                "models": {"gpt-6-luna": {"short_context": {
                    "input": 0.1, "cached_input": 0.01, "cache_write": 0.125, "output": 0.5,
                }}},
            }), encoding="utf-8")
            with patch.dict(os.environ, {"THEORYMASTER_PRICING_CACHE": str(cache)}):
                collector = Collector(spec)
                collector.set_routes({"research_lead": RoleRoute("gpt-6-luna", "CodexHTTPS", "openai", "medium")})
                collector.start_run("tm-0000-test-goal", spec, run_dir=spec.research_root / "tm-0000-test-goal")
                collector.on_codex({"event": "LLM_TURN_STARTED", "turn_id": "t1"}, role="research_lead", thread_id="thr")
                collector.on_codex({
                    "event": "TOKEN_USAGE", "turn_id": "t1", "tokens_in": 100000, "tokens_out": 10000, "tokens_total": 110000,
                    "raw_usage": {"input_tokens": 100000, "cached_input_tokens": 80000, "output_tokens": 10000},
                }, role="research_lead", thread_id="thr")
                collector.on_codex({
                    "event": "LLM_TURN_COMPLETED", "turn_id": "t1",
                    "raw_usage": {"input_tokens": 100000, "cached_input_tokens": 80000, "output_tokens": 10000},
                }, role="research_lead", thread_id="thr")
                cost = collector.snapshot()["agents"]["research_lead"]["cost"]
            self.assertAlmostEqual(cost["amount"], 0.0078)
            self.assertEqual(cost["currency"], "USD")
            self.assertEqual(cost["source"], "estimated")

    def test_turn_with_tool_subrequests_uses_each_request_pricing_band(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec, _ = self.make(root, reworks=0)
            cache = root / "pricing.json"
            cache.write_text(json.dumps({
                "schema": "theory-master/pricing/v1", "currency": "USD",
                "long_context_threshold_tokens": 100,
                "models": {"gpt-6-luna": {
                    "short_context": {"input": 1, "cached_input": 1, "cache_write": 1, "output": 1},
                    "long_context": {"input": 10, "cached_input": 10, "cache_write": 10, "output": 10},
                }},
            }), encoding="utf-8")
            with patch.dict(os.environ, {"THEORYMASTER_PRICING_CACHE": str(cache)}):
                collector = Collector(spec)
                collector.set_routes({"research_lead": RoleRoute("gpt-6-luna", "CodexHTTPS", "openai", "medium")})
                collector.start_run("tm-0000-test-goal", spec, run_dir=spec.research_root / "tm-0000-test-goal")
                collector.on_codex({"event": "LLM_TURN_STARTED", "turn_id": "t1"}, role="research_lead", thread_id="thr")
                for total in (80, 160):
                    collector.on_codex({
                        "event": "TOKEN_USAGE", "turn_id": "t1",
                        "raw_usage": {"input_tokens": total, "cached_input_tokens": 0, "output_tokens": 0},
                    }, role="research_lead", thread_id="thr")
                collector.on_codex({"event": "LLM_TURN_COMPLETED", "turn_id": "t1"}, role="research_lead", thread_id="thr")
                agent = collector.snapshot()["agents"]["research_lead"]
            self.assertEqual(agent["tokens_in"], 160)
            self.assertEqual(agent["cost_records"][0]["priced_request_count"], 2)
            self.assertAlmostEqual(agent["cost"]["amount"], 0.00016)

    def test_auxiliary_semantic_annotator_is_counted_without_becoming_visible_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            collector = Collector(spec)
            collector.set_routes({"semantic_annotator": RoleRoute("gpt-6-luna", "CodexHTTPS", "openai", "medium")})
            run_dir = spec.research_root / "tm-0000-test-goal"
            collector.start_run("tm-0000-test-goal", spec, run_dir=run_dir)
            self.assertNotIn("semantic_annotator", collector.snapshot()["agents"])

            collector.on_codex({"event": "LLM_TURN_STARTED", "turn_id": "semantic-1"},
                               role="semantic_annotator", thread_id="semantic-thread")
            collector.on_codex({
                "event": "TOKEN_USAGE", "turn_id": "semantic-1",
                "raw_usage": {"input_tokens": 120, "cached_input_tokens": 20, "output_tokens": 30},
            }, role="semantic_annotator", thread_id="semantic-thread")
            collector.on_codex({
                "event": "LLM_TURN_COMPLETED", "turn_id": "semantic-1",
                "raw_usage": {"input_tokens": 120, "cached_input_tokens": 20, "output_tokens": 30},
            }, role="semantic_annotator", thread_id="semantic-thread")

            snap = collector.snapshot()
            auxiliary = snap["auxiliary_agents"]["semantic_annotator"]
            self.assertEqual(auxiliary["tokens_total"], 150)
            self.assertEqual(snap["tokens_total"], 150)
            self.assertEqual(snap["cost"]["model_calls"], 1)
            self.assertNotEqual(snap.get("current_agent"), "semantic_annotator")
            collector.finish()
            summary = json.loads((run_dir / "runtime_summary.json").read_text(encoding="utf-8"))
            self.assertNotIn("semantic_annotator", summary["agents"])

    def test_cost_summary_separates_workflow_events_from_model_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), reworks=0)
            collector = Collector(spec)
            Workflow(spec, backend, collector=collector).run()
            cycle_dirs = list_cycle_dirs(spec.research_root, task_mode=True)
            self.assertTrue(cycle_dirs)
            cost = json.loads((cycle_dirs[-1] / "cost_summary.json").read_text(encoding="utf-8"))
            self.assertIn("workflow_events", cost)
            self.assertIn("model_calls", cost)
            self.assertNotIn("calls", cost)
            self.assertEqual(cost["model_calls"], cost["cost"]["model_calls"])
            self.assertNotIn("total_calls", cost["cost"])
            self.assertNotIn("known_calls", cost["cost"])

    def test_cycle_and_project_tokens_use_thread_cumulative_deltas_including_tool_subrequests(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            collector = Collector(spec)
            collector.start_run("tm-0000-test-goal", spec, run_dir=spec.research_root / "tm-0000-test-goal")
            collector.on_codex({"event": "LLM_TURN_STARTED", "turn_id": "t1"}, role="challenger", thread_id="thr")
            collector.on_codex({
                "event": "TOKEN_USAGE", "turn_id": "t1", "tokens_in": 500000, "tokens_out": 30000,
                "tokens_total": 530000,
                "raw_usage": {"input_tokens": 500000, "cached_input_tokens": 480000, "output_tokens": 30000, "reasoning_tokens": 232},
                "last_request_usage": {"input_tokens": 45395, "cached_input_tokens": 40320, "output_tokens": 741, "reasoning_tokens": 232},
            }, role="challenger", thread_id="thr")
            self.assertIsNone(collector.snapshot()["tokens_total"])
            collector.on_codex({
                "event": "LLM_TURN_COMPLETED", "turn_id": "t1",
                "raw_usage": {"input_tokens": 45395, "cached_input_tokens": 40320, "output_tokens": 741, "reasoning_tokens": 232},
            }, role="challenger", thread_id="thr")
            collector.on_codex({"event": "LLM_TURN_STARTED", "turn_id": "t2"}, role="challenger", thread_id="thr")
            collector.on_codex({
                "event": "TOKEN_USAGE", "turn_id": "t2", "tokens_in": 554464, "tokens_out": 30342,
                "tokens_total": 584806,
                "raw_usage": {"input_tokens": 554464, "cached_input_tokens": 528000, "output_tokens": 30342, "reasoning_tokens": 430},
                "last_request_usage": {"input_tokens": 54464, "cached_input_tokens": 48000, "output_tokens": 342, "reasoning_tokens": 198},
            }, role="challenger", thread_id="thr")
            collector.on_codex({
                "event": "LLM_TURN_COMPLETED", "turn_id": "t2",
                "raw_usage": {"input_tokens": 54464, "cached_input_tokens": 48000, "output_tokens": 342, "reasoning_tokens": 198},
            }, role="challenger", thread_id="thr")
            snap = collector.snapshot()
            self.assertEqual(snap["tokens_in"], 554464)
            self.assertEqual(snap["cached_input_tokens"], 528000)
            self.assertEqual(snap["uncached_input_tokens"], 26464)
            self.assertEqual(snap["tokens_out"], 30342)
            self.assertEqual(snap["reasoning_tokens"], 430)
            self.assertEqual(snap["tokens_total"], 584806)
            self.assertEqual(snap["current_turn_usage"]["input_tokens"], 54464)
            collector._accumulate_cycle()
            collector.state["agents"] = {role: {} for role in collector.state["agents"]}
            collector._refresh_project_totals()
            self.assertEqual(collector.state["project_tokens_in"], 554464)
            self.assertEqual(collector.state["project_cached_input_tokens"], 528000)
            self.assertEqual(collector.state["project_tokens_total"], 584806)

    def test_completed_turn_recovers_usage_from_native_codex_session(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            collector = Collector(spec)
            collector.start_run("tm-0000-test-goal", spec, run_dir=spec.research_root / "tm-0000-test-goal")
            usage = {
                "input_tokens": 42, "cached_input_tokens": 30, "output_tokens": 7,
                "cache_write_tokens": 0, "reasoning_tokens": 3,
            }
            with patch("theory_master.observability.collector.codex_turn_usage", return_value=usage) as recover:
                collector.on_codex(
                    {"event": "LLM_TURN_COMPLETED", "turn_id": "turn-native"},
                    role="challenger", thread_id="thread-native",
                )
            recover.assert_called_once_with("thread-native", "turn-native")
            agent = collector.snapshot()["agents"]["challenger"]
            self.assertEqual(agent["cost_records"][0]["raw_usage"], usage)
            self.assertEqual(agent["tokens_in"], 42)
            self.assertEqual(agent["tokens_total"], 49)

    def test_native_compaction_keeps_episode_and_tracks_before_after(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            collector = Collector(spec)
            collector.start_run("tm-0000-test-goal", spec, run_dir=spec.research_root / "tm-0000-test-goal")
            collector.on_codex({
                "event": "TOKEN_USAGE", "turn_id": "t1", "context_tokens": 219700, "context_window": 258400,
                "raw_usage": {"input_tokens": 219700, "cached_input_tokens": 180000, "output_tokens": 1000},
            }, role="investigator", thread_id="thr-episode")
            collector.on_codex({
                "event": "COMPACTION_STARTED", "turn_id": "tc1", "episode_id": "thr-episode",
                "compact_threshold_tokens": 220000, "compact_scope": "total",
            }, role="investigator", thread_id="thr-episode")
            collector.on_codex({
                "event": "COMPACTION_COMPLETED", "turn_id": "tc1", "episode_id": "thr-episode",
                "context_after": None, "compaction_usage": None, "compaction_cost": None,
            }, role="investigator", thread_id="thr-episode")
            collector.on_codex({
                "event": "TOKEN_USAGE", "turn_id": "t2", "context_tokens": 42000, "context_window": 258400,
                "raw_usage": {"input_tokens": 42000, "cached_input_tokens": 32000, "output_tokens": 500},
            }, role="investigator", thread_id="thr-episode")
            agent = collector.snapshot()["agents"]["investigator"]
            self.assertEqual(agent["thread_id"], "thr-episode")
            self.assertEqual(agent["compaction_count"], 1)
            self.assertEqual(agent["context_tokens"], 42000)
            self.assertEqual(agent["context_window"], 258400)
            self.assertEqual(agent["last_compaction"]["episode_id"], "thr-episode")
            self.assertEqual(agent["last_compaction"]["context_before"], 219700)
            self.assertEqual(agent["last_compaction"]["context_after"], 42000)
            self.assertFalse(agent["last_compaction"]["awaiting_context_after"])
            kinds = [event["event"] for event in collector.runtime_events(limit=0)]
            self.assertIn("COMPACTION_STARTED", kinds)
            self.assertIn("COMPACTION_COMPLETED", kinds)

    def test_needs_attention_is_not_projected_as_running(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            collector = Collector(spec)
            collector.start_run("tm-0000-test-goal", spec, run_dir=spec.research_root / "tm-0000-test-goal")
            collector.on_persist({
                "status": "active",
                "phase": "qualification",
                "resumable": False,
                "execution_active": True,
                "owner_pid": os.getpid(),
                "threads": {},
                "branches": [],
            })
            collector.on_codex(
                {"event": "LLM_TURN_STARTED", "turn_id": "turn-1"},
                role="challenger", thread_id="thread-1",
            )
            self.assertEqual(collector.snapshot()["status"], "RUNNING")
            collector.on_persist({
                "status": "needs_attention",
                "phase": "qualification",
                "qualification_status": "unqualified",
                "qualification_rationale": "no qualified candidate",
                "resumable": True,
                "execution_active": False,
                "owner_pid": os.getpid(),
                "human_attention": "Qualification stopped without a qualified candidate.",
                "attention": {
                    "code": "no_qualified_candidate",
                    "reason": "qualification revision limit reached without a qualified candidate",
                    "requested_action": "Review the research preparation and resume.",
                    "resume_behavior": "Resume restarts qualification from Research Lead.",
                },
                "threads": {"challenger": "thread-1"},
                "branches": [],
            })
            snap = collector.snapshot()
            self.assertEqual(snap["status"], "NEEDS_ATTENTION")
            self.assertFalse(snap["execution_active"])
            self.assertIsNone(snap["current_agent"])
            self.assertIsNone(snap["current_activity"])
            header = render_header(snap)
            self.assertIn("Process STOPPED", header)
            self.assertIn("State NEEDS ATTENTION", header)
            self.assertIn("without a qualified candidate", header)
            attention = render_attention(snap)
            self.assertIn("Requested action", attention)
            self.assertIn("Resume restarts qualification from Research Lead", attention)

    def test_file_source_reconciles_stale_running_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            run_dir = spec.research_root / "tm-0001-test-goal"
            run_dir.mkdir(parents=True)
            (run_dir / ".theory-master-cycle").write_text("theory-master/v2\ntest-goal\n", encoding="utf-8")
            (run_dir / "run_state.json").write_text(json.dumps({
                "run_id": run_dir.name,
                "status": "RUNNING",
                "execution_active": True,
                "current_agent": "challenger",
                "current_activity": "stale activity",
                "agents": {"challenger": {"state": "running", "current_tool": None, "activity": "stale activity"}},
            }), encoding="utf-8")
            (run_dir / "research_state.json").write_text(json.dumps({
                "status": "needs_attention",
                "phase": "qualification",
                "resumable": True,
                "execution_active": False,
                "owner_pid": os.getpid(),
                "human_attention": "Qualification needs attention.",
                "attention": {"code": "no_qualified_candidate", "reason": "No candidate passed qualification."},
            }), encoding="utf-8")
            snap = FileSource(spec.research_root).snapshot()
            self.assertEqual(snap["status"], "NEEDS_ATTENTION")
            self.assertFalse(snap["execution_active"])
            self.assertIsNone(snap["current_agent"])
            self.assertIsNone(snap["current_activity"])

    def test_file_source_overlays_live_max_cycles_from_task_config(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            run_dir = spec.research_root / "tm-0001-test-goal"
            run_dir.mkdir(parents=True)
            (run_dir / ".theory-master-cycle").write_text("theory-master/v2\ntest-goal\n", encoding="utf-8")
            config = json.loads(Path(spec.config_path).read_text(encoding="utf-8"))
            config["max_cycles"] = 1
            Path(spec.config_path).write_text(json.dumps(config), encoding="utf-8")
            (run_dir / "run_state.json").write_text(json.dumps({
                "run_id": run_dir.name,
                "status": "NEEDS_ATTENTION",
                "execution_active": False,
                "max_cycles": 5,
                "config_path": str(spec.config_path),
            }), encoding="utf-8")
            snap = FileSource(spec.research_root).snapshot()
            self.assertEqual(snap["max_cycles"], 1)

    def test_collector_none_still_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), reworks=0)
            state = Workflow(spec, backend).run()
            self.assertEqual(state["status"], "over")

    def test_cycle_audit_runs_once_per_over_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), reworks=0)
            collector = Collector(spec, cycle_audit_actions=True)
            with patch("theory_master.observability.cycle_audit_relay.spawn_cycle_audit_agent", return_value="agent:TM审计.0") as spawn:
                Workflow(spec, backend, collector=collector).run()
            self.assertEqual(spawn.call_count, spec.max_cycles)
            self.assertEqual(collector.snapshot()["audit_handoff"]["status"], "started")

    def test_needs_attention_audit_is_one_shot_and_failed_state_is_not_audited(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            collector = Collector(spec, cycle_audit_actions=True)
            collector.start_run("tm-0000-test-goal", spec, run_dir=spec.research_root / "tm-0000-test-goal")
            needs_attention = {
                "status": "needs_attention", "phase": "qualification",
                "resumable": True, "execution_active": False, "threads": {}, "branches": [],
            }
            with patch("theory_master.observability.cycle_audit_relay.spawn_cycle_audit_agent", return_value="agent:TM审计.0") as spawn:
                collector.finish(needs_attention)
                collector.finish_invocation(needs_attention)
                spawn.assert_called_once()

            collector.start_run("tm-0001-test-goal", spec, run_dir=spec.research_root / "tm-0001-test-goal")
            failed = {
                "status": "failed", "phase": "failed", "resumable": True,
                "execution_active": False, "threads": {}, "branches": [],
            }
            with patch("theory_master.observability.cycle_audit_relay.spawn_cycle_audit_agent", return_value="agent:TM审计.0") as spawn:
                collector.finish(failed)
                spawn.assert_not_called()

    def test_resume_restores_agent_elapsed_and_continues_accumulating(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            run_dir = spec.research_root / "tm-0007-test-goal"
            run_dir.mkdir(parents=True)
            persisted = {
                "run_id": run_dir.name,
                "cycle_id": run_dir.name,
                "cycle_index": 1,
                "total_started_at": "2026-09-24T20:00:00+08:00",
                "cycle_started_at": "2026-09-24T20:00:00+08:00",
                "cycle_elapsed_s": 42.0,
                "project_elapsed_s": 57.0,
                "agents": {"research_lead": {"elapsed_s": 12.5}},
                "agent_timeline": [
                    {"role": "research_lead", "elapsed_s": 4.0, "active": False},
                    {"role": "research_lead", "elapsed_s": 0.0, "active": True},
                ],
            }
            (run_dir / "run_state.json").write_text(json.dumps(persisted), encoding="utf-8")
            (run_dir / "runtime_events.jsonl").write_text("\n".join([
                json.dumps({"ts": "2026-09-24T20:00:00+08:00", "event": "LLM_TURN_STARTED", "role": "challenger"}),
                json.dumps({"ts": "2026-09-24T20:00:06+08:00", "event": "LLM_TURN_COMPLETED", "role": "challenger"}),
                json.dumps({"ts": "2026-09-24T20:00:10+08:00", "event": "LLM_TURN_STARTED", "role": "challenger"}),
                json.dumps({"ts": "2026-09-24T20:00:14+08:00", "event": "RUN_INTERRUPTED"}),
            ]) + "\n", encoding="utf-8")
            collector = Collector(spec)
            collector.start_run(run_dir.name, spec, run_dir=run_dir)
            snap = collector.snapshot()
            self.assertEqual(snap["agents"]["research_lead"]["elapsed_s"], 12.5)
            self.assertAlmostEqual(snap["agents"]["challenger"]["elapsed_s"], 10.0, places=3)
            self.assertFalse(any(item.get("active") for item in snap["agent_timeline"]))
            self.assertAlmostEqual(snap["agent_timeline"][-1]["elapsed_s"], 8.5, places=3)
            self.assertGreaterEqual(snap["cycle_elapsed_s"], 42.0)
            self.assertGreaterEqual(snap["project_elapsed_s"], 57.0)

            collector.state["agents"]["research_lead"]["state"] = "running"
            collector._role_elapsed_base["research_lead"] = 12.5
            collector._role_started_mono["research_lead"] = 100.0
            with patch("theory_master.observability.collector_projection.time.monotonic", return_value=105.0):
                collector._refresh_elapsed()
            self.assertAlmostEqual(collector.state["agents"]["research_lead"]["elapsed_s"], 17.5, places=3)
            self.assertAlmostEqual(collector.state["project_agents"]["research_lead"]["elapsed_s"], 17.5, places=3)

    def test_resume_refreshes_invocation_cycle_index_in_runtime_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            resumed_dir = spec.research_root / "tm-0007-test-goal"
            resumed_dir.mkdir(parents=True)
            (resumed_dir / "run_state.json").write_text(json.dumps({
                "run_id": resumed_dir.name,
                "cycle_index": 2,
                "cycle_elapsed_s": 12.0,
                "project_elapsed_s": 20.0,
                "agents": {},
            }), encoding="utf-8")

            collector = Collector(spec)
            collector.start_run(resumed_dir.name, spec, run_dir=resumed_dir)
            snap = collector.snapshot()
            self.assertEqual(snap["cycle_index"], 1)
            persisted = json.loads((resumed_dir / "run_state.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["cycle_index"], 1)
            started = [event for event in collector.runtime_events(limit=0) if event.get("event") == "CYCLE_STARTED"]
            self.assertEqual(started[-1]["cycle_index"], 1)

            collector.finish()
            summary = json.loads((resumed_dir / "runtime_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["cycle_index"], 1)

            next_dir = spec.research_root / "tm-0008-test-goal"
            collector.start_run(next_dir.name, spec, run_dir=next_dir)
            self.assertEqual(collector.snapshot()["cycle_index"], 2)
            next_persisted = json.loads((next_dir / "run_state.json").read_text(encoding="utf-8"))
            self.assertEqual(next_persisted["cycle_index"], 2)
            next_started = [event for event in collector.runtime_events(limit=0) if event.get("event") == "CYCLE_STARTED"]
            self.assertEqual(next_started[-1]["cycle_index"], 2)

    def test_resume_restores_completed_request_cost_and_agent_state(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            run_dir = spec.research_root / "tm-0007-test-goal"
            run_dir.mkdir(parents=True)
            persisted = {
                "run_id": run_dir.name,
                "cycle_index": 1,
                "cycle_elapsed_s": 50.0,
                "project_elapsed_s": 100.0,
                "agents": {
                    "research_lead": {"state": "waiting", "elapsed_s": 20.0, "turn_count": 1, "cost_records": []},
                    "challenger": {
                        "state": "idle", "elapsed_s": 10.0, "turn_count": 1,
                        "cost_records": [{
                            "amount": 0.01, "currency": "USD", "source": "estimated",
                            "raw_usage": {"input_tokens": 40, "cached_input_tokens": 30, "output_tokens": 4, "reasoning_tokens": 2},
                        }],
                    },
                },
                # This projection includes the current Cycle, whose request
                # history was lost by the old resume path.
                "project_agents": {
                    "challenger": {
                        "elapsed_s": 30.0, "tokens_in": 160, "tokens_out": 16,
                        "cached_input_tokens": 40, "reasoning_tokens": 3,
                        "cost": {"amount": 0.04, "currency": "USD", "source": "estimated"},
                    },
                },
            }
            (run_dir / "run_state.json").write_text(json.dumps(persisted), encoding="utf-8")
            events = [
                {"event": "LLM_TURN_STARTED", "role": "research_lead", "thread_id": "rl-thread", "turn_id": "rl-turn"},
                {"event": "LLM_TURN_COMPLETED", "role": "research_lead", "thread_id": "rl-thread", "turn_id": "rl-turn"},
                {"event": "LLM_TURN_STARTED", "role": "challenger", "thread_id": "ch-thread", "turn_id": "ch-turn"},
                {"event": "LLM_TURN_COMPLETED", "role": "challenger", "thread_id": "ch-thread", "turn_id": "ch-turn"},
                {"event": "LLM_TURN_STARTED", "role": "challenger", "thread_id": "ch-thread", "turn_id": "ch-turn-2"},
                {"event": "LLM_TURN_COMPLETED", "role": "challenger", "thread_id": "ch-thread", "turn_id": "ch-turn-2"},
                {"event": "AGENT_COMPLETED", "role": "challenger"},
            ]
            (run_dir / "runtime_events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8",
            )

            def make_cost(*, usage, **_kwargs):
                return {"amount": 0.01, "currency": "USD", "source": "estimated", "raw_usage": usage or {}}

            usage_by_turn = {
                ("rl-thread", "rl-turn"): {"input_tokens": 20, "cached_input_tokens": 10, "output_tokens": 2, "reasoning_tokens": 1},
                ("ch-thread", "ch-turn"): {"input_tokens": 20, "cached_input_tokens": 10, "output_tokens": 2, "reasoning_tokens": 1},
                ("ch-thread", "ch-turn-2"): {"input_tokens": 40, "cached_input_tokens": 30, "output_tokens": 4, "reasoning_tokens": 2},
            }
            collector = Collector(spec)
            with patch("theory_master.observability.collector_recovery.codex_turn_usage", side_effect=lambda thread, turn: usage_by_turn.get((thread, turn))), \
                    patch("theory_master.observability.collector_recovery.assess_cost", side_effect=make_cost):
                collector.start_run(run_dir.name, spec, run_dir=run_dir)
            snap = collector.snapshot()
            self.assertEqual(snap["agents"]["research_lead"]["state"], "waiting")
            self.assertEqual(snap["agents"]["research_lead"]["cost"]["amount"], 0.01)
            self.assertEqual(snap["agents"]["challenger"]["state"], "done")
            self.assertEqual(snap["agents"]["challenger"]["turn_count"], 2)
            self.assertEqual(snap["agents"]["challenger"]["tokens_in"], 60)
            self.assertEqual(snap["agents"]["challenger"]["cost"]["amount"], 0.02)
            self.assertEqual(snap["project_agents"]["challenger"]["tokens_in"], 160)
            self.assertEqual(snap["project_agents"]["challenger"]["cost"]["amount"], 0.04)

    def test_completed_elapsed_is_frozen(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = self.make(Path(temp), reworks=0)
            collector = Collector(spec)
            Workflow(spec, backend, collector=collector).run()
            elapsed = collector.snapshot()["elapsed_s"]
            time.sleep(0.02)
            later = collector.snapshot()
            self.assertEqual(later["elapsed_s"], elapsed)
            persisted = json.loads((Path(later["run_dir"]) / "run_state.json").read_text())
            self.assertEqual(persisted["elapsed_s"], elapsed)

    def test_run_failed_logs_error_on_activity_and_orchestrator(self):
        class Boom(FakeBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "investigator":
                    raise RuntimeError("approval_policy untrusted")
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            collector = Collector(spec)
            with patch.object(collector, "_spawn_error_repair") as spawn, \
                 patch.object(collector, "_notify_terminal_email") as notify:
                with self.assertRaisesRegex(RuntimeError, "untrusted"):
                    Workflow(spec, Boom(reworks=0), collector=collector).run()
            spawn.assert_not_called()
            notify.assert_not_called()
            snap = collector.snapshot()
            self.assertFalse((Path(snap["run_dir"]) / "theory_master_error_relay.md").exists())
            self.assertEqual(snap["status"], "FAILED")
            self.assertEqual(snap["cycle_index"], 1)
            logs = "\n".join(collector.logs())
            self.assertIn("[ERROR]", logs)
            self.assertIn("approval_policy untrusted", logs)
            activity = render_activity(snap)
            self.assertIn("Agent", activity)
            self.assertNotIn("Thread", activity)
            self.assertNotIn("Turn", activity)
            self.assertIn("approval_policy untrusted", activity)

    def test_real_run_opt_in_records_sdk_internal_hold_without_repair_worker(self):
        class Boom(FakeBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "investigator":
                    raise RuntimeError("provider exploded")
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp), reworks=0)
            collector = Collector(spec, system_failure_actions=True)
            with patch.object(collector, "_notify_terminal_email") as notify, \
                 patch("theory_master.observability.error_relay.spawn_repair_agent", return_value="agent:修复.0") as spawn:
                with self.assertRaisesRegex(RuntimeError, "provider exploded"):
                    Workflow(spec, Boom(reworks=0), collector=collector).run()
            spawn.assert_not_called()
            notify.assert_not_called()
            handoff = collector.snapshot()["repair_handoff"]
            self.assertEqual(handoff["status"], "sdk_internal_hold")
            self.assertIsNone(handoff["target"])


if __name__ == "__main__":
    unittest.main()
