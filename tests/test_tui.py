from __future__ import annotations

import asyncio

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.observability.models import blank_run_state
from theory_master.tui.demo import DemoSource, demo_state
from theory_master.tui.render import (
    render_activity,
    render_artifacts,
    render_canvas,
    render_challenges,
    render_events,
    render_error,
    render_header,
    render_identity,
    quota_waiting_active,
    render_quota_wait_popup,
    render_hypothesis,
    render_investigation,
    render_judge,
    render_last,
    render_last_action,
    render_logs,
    render_thread,
    render_tokens,
    render_workflow,
)


def _workflow_columns_share_starts(workflow: str) -> bool:
    """Every workflow row, including the header, must occupy the same columns."""
    lines = [line for line in workflow.splitlines() if line.strip()]
    return len(lines) >= 2 and len({len(line) for line in lines}) == 1


class RenderTest(unittest.TestCase):
    def test_identity_shows_runtime_safety_switches(self):
        state = blank_run_state()
        state.update({
            "master_id": 0,
            "cycle_id": "cycle-0068",
            "supervisor_enabled": True,
            "cycle_audit_actions_enabled": False,
        })
        text = render_identity(state)
        self.assertIn("Supervisor: 开启", text)
        self.assertNotIn("错误排查", text)
        self.assertNotIn("Attention接管", text)
        self.assertIn("自动审计: 关闭", text)
        self.assertIn("额度自调: 关闭", text)

    def test_quota_wait_popup_shows_selected_and_both_account_snapshots(self):
        state = blank_run_state()
        state.update({"quota_auto_adjust_enabled": True, "status": "PAUSED",
            "quota_auto_adjust": {
                "status": "waiting_refresh", "selected_account": "2", "failed_account": "1",
                "next_check_in_s": 60,
                "exhausted_accounts": {"1": {"source": "explicit_provider_error"}},
                "accounts": {
                    "1": {"ok": True, "ordinary_usage_allowed": False,
                          "primary": {"used_percent": 100, "remaining_percent": 0, "window_duration_mins": 300, "resets_at": 1780000000},
                          "secondary": {"used_percent": 95, "remaining_percent": 5, "window_duration_mins": 10080, "resets_at": 1780500000}},
                    "2": {"ok": True, "ordinary_usage_allowed": True,
                          "primary": {"used_percent": 40, "remaining_percent": 60, "window_duration_mins": 300, "resets_at": 1780200000},
                          "secondary": {"used_percent": 80, "remaining_percent": 20, "window_duration_mins": 10080, "resets_at": 1780600000}},
                },
            },
        })
        self.assertTrue(quota_waiting_active(state))
        display = render_quota_wait_popup(state)
        self.assertIn("account-2  ← 当前选中", display)
        self.assertIn("account-1", display)
        self.assertIn("短周期（窗口 300 分钟）: 剩余 60%", display)
        self.assertIn("长周期（窗口 10080 分钟）: 剩余 5%", display)
        self.assertIn("每 60 秒", display)
        state["quota_auto_adjust"]["status"] = "resuming"
        self.assertFalse(quota_waiting_active(state))
        state["quota_auto_adjust"]["status"] = "waiting_refresh"
        state["quota_auto_adjust_enabled"] = False
        self.assertFalse(quota_waiting_active(state))

    def test_quota_wait_popup_handles_missing_probe_without_synthetic_quota(self):
        state = {"quota_auto_adjust_enabled": True,
                 "quota_auto_adjust": {"status": "waiting_refresh", "selected_account": "1",
                                       "accounts": {"1": {"ok": False, "error": "timeout"}, "2": {}}}}
        display = render_quota_wait_popup(state)
        self.assertIn("查询状态: 不可用", display)
        self.assertIn("诊断: timeout", display)
        self.assertNotIn("剩余 100%", display)

    def test_live_collector_reads_latest_supervisor_quota_state(self):
        from theory_master.observability.collector import Collector
        from theory_master.quota_auto_adjust import (
            write_quota_auto_adjust_setting, write_controller_state,
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "research"
            root.mkdir()
            spec = SimpleNamespace(research_root=root, goal_id="goal", max_cycles=1)
            collector = Collector(spec)
            write_quota_auto_adjust_setting(root, True)
            write_controller_state(root, status="waiting_refresh",
                                   selected_account="2",
                                   accounts={"2": {"ok": True, "primary": {"remaining_percent": 23}}})
            snap = collector.snapshot()
            self.assertTrue(snap["quota_auto_adjust_enabled"])
            self.assertEqual(snap["quota_auto_adjust"]["selected_account"], "2")
            self.assertTrue(quota_waiting_active(snap))
            write_controller_state(root, status="idle", selected_account="1")
            snap = collector.snapshot()
            self.assertFalse(quota_waiting_active(snap))
            self.assertEqual(snap["quota_auto_adjust"]["selected_account"], "1")

    def test_events_render_full_conversation_without_debug_noise(self):
        events = [
            {"ts": "2026-09-24T15:35:00+08:00", "event": "RUN_STARTED", "run_id": "cycle-0007"},
            {"ts": "2026-09-24T15:35:01+08:00", "event": "AGENT_STARTED", "agent": "challenger", "role": "challenger",
             "stage": "candidate_review", "thread_id": "thr-1"},
            {"ts": "2026-09-24T15:35:02+08:00", "event": "AGENT_INPUT", "agent": "challenger", "role": "challenger",
             "stage": "candidate_review", "input_kind": "task", "content": "Review H1 and H2", "thread_id": "thr-1"},
            {"ts": "2026-09-24T15:35:03+08:00", "event": "LLM_TURN_STARTED", "agent": "challenger", "role": "challenger",
             "thread_id": "thr-1", "turn_id": "turn-1"},
            {"ts": "2026-09-24T15:35:08+08:00", "event": "LLM_RECONNECTING", "agent": "challenger", "role": "challenger",
             "thread_id": "thr-1", "turn_id": "turn-1", "error": "Reconnecting... waiting for network"},
            {"ts": "2026-09-24T15:35:10+08:00", "event": "LLM_MESSAGE", "agent": "challenger", "role": "challenger",
             "content": "H1 is qualified."},
            {"ts": "2026-09-24T15:35:10.2+08:00", "event": "COMPACTION_STARTED", "agent": "challenger", "role": "challenger",
             "episode_id": "thr-1", "context_before": 219700, "context_window": 258400, "compact_threshold_tokens": 220000, "compact_scope": "total"},
            {"ts": "2026-09-24T15:35:10.8+08:00", "event": "COMPACTION_COMPLETED", "agent": "challenger", "role": "challenger",
             "episode_id": "thr-1", "context_before": 219700, "context_after": None, "context_window": 258400, "duration_s": 0.6},
            {"ts": "2026-09-24T15:35:11+08:00", "event": "TOKEN_USAGE", "agent": "challenger", "tokens_total": 12345},
            {"ts": "2026-09-24T15:35:12+08:00", "event": "LLM_TURN_COMPLETED", "agent": "challenger", "role": "challenger",
             "thread_id": "thr-1", "turn_id": "turn-1"},
            {"ts": "2026-09-24T15:35:13+08:00", "event": "AGENT_COMPLETED", "agent": "challenger", "role": "challenger",
             "stage": "candidate_review", "output": {"duplicate": "must not render"}},
        ]
        rendered = render_events(events)
        self.assertIn("cycle-0007 · chronological · oldest → newest · 2026-09-24", rendered)
        self.assertIn("Challenger · candidate_review", rendered)
        self.assertIn("15:35:02  THEORYMASTER → CHALLENGER · task", rendered)
        self.assertIn("Review H1 and H2", rendered)
        self.assertIn("15:35:08  ⚠ RECONNECTING · Reconnecting... waiting for network", rendered)
        self.assertIn("15:35:10  CHALLENGER", rendered)
        self.assertIn("H1 is qualified.", rendered)
        self.assertIn("COMPACT START · 219.7k / 258.4k · threshold 220k · total", rendered)
        self.assertIn("COMPACT DONE · 00:00 · same episode · context after: next-turn measurement", rendered)
        self.assertIn("TURN END · 00:09", rendered)
        self.assertIn("STAGE COMPLETE", rendered)
        self.assertNotIn("thr-1", rendered)
        self.assertNotIn("turn-1", rendered)
        self.assertNotIn("TOKEN_USAGE", rendered)
        self.assertNotIn("12345", rendered)
        self.assertNotIn("must not render", rendered)

    def test_header_and_workflow_from_snapshot(self):
        state = blank_run_state()
        state.update({"run_id": "tm-0007-g1", "master_run_id": 7, "goal_id": "g1", "status": "RUNNING", "cycle_index": 2, "max_cycles": 5, "phase": "investigator:running", "tokens_total": 87412, "elapsed_s": 100, "total_started_at": "2026-09-21T10:00:00+08:00", "cycle_started_at": "2026-09-21T10:05:00+08:00", "cost": {"amount": 5.6, "currency": "CNY", "source": "calculated"}})
        state["agents"]["investigator"]["state"] = "running"
        state["agents"]["investigator"]["thread_id"] = "thr_c22"
        state["agents"]["investigator"]["tokens_total"] = 39311
        state["agents"]["investigator"]["model"] = "gpt-5.6-sol"
        state["agents"]["investigator"]["reasoning_effort"] = "low"
        header = render_header(state)
        self.assertIn("Started  09-21T10:00:00", header)
        self.assertIn("Cycle start 09-21T10:05:00", header)
        self.assertIn("RUNNING", header)
        self.assertIn("¥5.60 calculated", header)
        workflow = render_workflow(state)
        self.assertIn("Investigator", workflow)
        self.assertNotIn("thr_c22", workflow)
        self.assertNotIn("THREAD", workflow)
        self.assertIn("MODEL", workflow)
        self.assertIn("GPT-5.6 Sol · low", workflow)
        self.assertIn("STATE", workflow)
        self.assertLessEqual(max(map(len, workflow.splitlines())), 70)
        self.assertTrue(_workflow_columns_share_starts(workflow))
        activity = render_activity(state)
        self.assertIn("Agent", activity)
        self.assertNotIn("Thread", activity)
        self.assertNotIn("Turn", activity)
        self.assertIn("Last action", activity)
        self.assertEqual(render_last(state), "(none)")

    def test_workflow_shows_mixed_role_models_and_usage_does_not_claim_one_model(self):
        state = blank_run_state()
        state["agents"]["research_lead"].update({"model": "gpt-5.6-sol", "state": "done"})
        state["agents"]["challenger"].update({"model": "gpt-5.6-luna", "state": "done"})
        state["agents"]["investigator"].update({"model": "deepseek-flash", "state": "running"})
        workflow = render_workflow(state)
        self.assertIn("Research Lead", workflow)
        self.assertIn("GPT-5.6 Sol", workflow)
        self.assertIn("GPT-5.6 Luna", workflow)
        self.assertIn("DeepSeek Flash", workflow)
        self.assertTrue(_workflow_columns_share_starts(workflow))
        usage = render_tokens(state)
        self.assertNotIn("Models       mixed", usage)
        self.assertNotIn("see workflow", usage)
        self.assertNotIn("Model        gpt-5.6-sol", usage)

    def test_token_panel_separates_current_turn_and_falls_back_to_tokens_when_cost_unavailable(self):
        state = blank_run_state()
        state.update({
            "current_turn_role": "challenger",
            "current_turn_usage": {
                "input_tokens": 54464, "cached_input_tokens": 48000,
                "output_tokens": 342, "reasoning_tokens": 198,
            },
            "tokens_in": 692301, "cached_input_tokens": 643456,
            "uncached_input_tokens": 48845, "tokens_out": 11731,
            "reasoning_tokens": 6427, "tokens_total": 704032,
            "project_tokens_in": 692301, "project_cached_input_tokens": 643456,
            "project_tokens_out": 11731, "project_reasoning_tokens": 6427,
            "project_tokens_total": 704032,
        })
        panel = render_tokens(state)
        self.assertIn("CURRENT TURN", panel)
        self.assertIn("Cached     48,000  88.1%", panel)
        self.assertIn("Uncached   6,464", panel)
        self.assertIn("Reasoning    198", panel)
        self.assertIn("CYCLE USAGE", panel)
        self.assertIn("Input        692,301", panel)
        self.assertIn("Reasoning    6,427", panel)
        self.assertIn("PROJECT TOTAL", panel)
        self.assertIn("Cost         — · 704,032 tok", panel)
        header = render_header(state)
        self.assertIn("Cost — · Tokens 704,032", header)

    def test_estimated_cost_is_marked_as_estimated_in_token_panel(self):
        state = blank_run_state()
        state["cost"] = {"amount": 0.17241626, "currency": "USD", "source": "estimated"}
        state["project_cost"] = state["cost"]
        panel = render_tokens(state)
        self.assertIn("Cost         ~$0.172", panel)

    def test_header_shows_recovery_checkpoint(self):
        state = blank_run_state()
        state.update({"status": "INTERRUPTED", "phase": "interrupted", "resumable": True,
                      "recovery_phase": "interrupted", "last_safe_checkpoint": "/tmp/tm-0007/checkpoints/000123-investigator.json"})
        header = render_header(state)
        self.assertIn("Recovery interrupted", header)
        self.assertIn("Checkpoint /tmp/tm-0007/checkpoints/000123-investigator.json", header)

    def test_header_hides_internal_recovery_checkpoint_during_normal_run(self):
        state = blank_run_state()
        state.update({
            "status": "RUNNING", "phase": "investigator:running", "execution_active": True,
            "resumable": True, "last_safe_checkpoint": "/tmp/tm-0007/checkpoints/000123-investigator.json",
        })
        header = render_header(state)
        self.assertNotIn("Recovery ", header)
        self.assertNotIn("Checkpoint ", header)

    def test_header_hides_stale_checkpoint_after_over_cycle(self):
        state = blank_run_state()
        state.update({
            "status": "OVER", "phase": "complete", "execution_active": False,
            "resumable": True, "last_safe_checkpoint": "/tmp/tm-0007/checkpoints/000123-investigator.json",
        })
        header = render_header(state)
        self.assertNotIn("Recovery ", header)
        self.assertNotIn("Checkpoint ", header)

    def test_dashboard_hypothesis_keeps_history_for_scroll(self):
        state = blank_run_state()
        state["hypothesis_title"] = "Short title"
        state["hypothesis"] = "body " * 80
        state["hypothesis_history"] = [
            {"ts": "2026-09-20T23:40:05+08:00", "title": "Draft 0", "body": "old " * 20},
        ]
        dash = render_hypothesis(state)
        self.assertIn("Short title", dash)
        self.assertIn("body body body", dash)
        self.assertIn("Previous", dash)
        self.assertIn("Draft 0", dash)
        self.assertIn("09-20T23:40:05", dash)
        self.assertIn("[h] full current hypothesis", dash)
        full = render_hypothesis(state, full=True)
        self.assertGreater(len(full), 300)
        self.assertIn("body body body", full)
        self.assertNotIn("Previous", full)
        self.assertNotIn("[h] full current hypothesis", full)

    def test_dashboard_challenges_show_gap_driven_current_state(self):
        state = blank_run_state()
        state["next_executor"] = "investigator"
        text = render_challenges(state)
        self.assertIn("Hypothesis Qualification", text)
        self.assertIn("Hypothesis Lock  pending", text)
        self.assertIn("Next executor  investigator", text)
        self.assertNotIn("Investigation Requirements", text)
        self.assertNotIn("Critical Issues", text)
        self.assertNotIn("Mini Battle", text)
        self.assertNotIn("Director", text)
        self.assertLess(max(len(line) for line in text.splitlines()), 90)
        self.assertEqual(render_last(state), "(none)")


    def test_last_pane_timestamps_agent_stages(self):
        state = blank_run_state()
        state["agent_timeline"] = [
            {"started_at": "2026-09-20T23:40:01+08:00", "role": "research_lead", "elapsed_s": 69, "active": False, "handoff_to": "challenger"},
            {"started_at": "2026-09-20T23:41:10+08:00", "role": "challenger", "elapsed_s": 12, "active": True},
        ]
        text = render_last(state)
        self.assertIn("23:40:01  Research Lead    done", text)
        self.assertIn("-> Challenger", text)
        self.assertIn("23:41:10  Challenger       running", text)
        self.assertNotIn("WORKFLOW", text)
        self.assertNotIn("AGENTS / HANDOFFS", text)
        self.assertNotIn("RUN_STARTED", text)

    def test_last_pane_merges_workflow_and_agents_chronologically_with_pinned_live_action(self):
        state = blank_run_state()
        state["action_history"] = [
            {"at": "2026-09-27T16:45:01+08:00", "event": "CYCLE_STARTED", "text": "Cycle started · cycle-0057"},
            {"at": "2026-09-27T16:47:22+08:00", "event": "JUDGE_REWORK_REQUESTED", "text": "Judge returned → Theorist for rework #1"},
        ]
        state["agent_timeline"] = [
            {"started_at": "2026-09-27T16:45:02+08:00", "role": "research_lead", "elapsed_s": 31, "active": False, "handoff_to": "challenger"},
        ]
        state["last_action"] = {
            "at": "2026-09-27T16:48:03+08:00", "event": "TOOL_ACTIVITY",
            "text": "Theorist · command · python check.py",
        }
        history = render_last(state)
        cycle_pos = history.index("16:45:01  Cycle started · cycle-0057")
        lead_pos = history.index("16:45:02  Research Lead")
        rework_pos = history.index("16:47:22  Judge returned → Theorist for rework #1")
        self.assertLess(cycle_pos, lead_pos)
        self.assertLess(lead_pos, rework_pos)
        self.assertNotIn("WORKFLOW", history)
        self.assertNotIn("AGENTS / HANDOFFS", history)
        self.assertEqual(
            render_last_action(state),
            "Last action  16:48:03  Theorist · command · python check.py",
        )

    def test_skill_load_is_compact_in_last_and_detailed_in_events(self):
        state = {"action_history": [{
            "at": "2026-09-27T16:48:03+08:00", "event": "SKILL_LOADED",
            "text": "Investigator · skill loaded · symbolic · load #2", "role": "investigator",
        }], "agent_timeline": []}
        self.assertIn("skill loaded · symbolic · load #2", render_last(state))
        rendered = render_events([{
            "ts": "2026-09-27T16:48:03+08:00", "event": "SKILL_LOADED", "role": "investigator",
            "skill": "symbolic", "skill_path": "/tmp/symbolic/SKILL.md", "load_index": 2,
            "first_load_in_turn": True, "detection": "observed_file_read",
        }])
        self.assertIn("SKILL LOADED · symbolic · load #2 · first in turn · observed file read", rendered)
        self.assertIn("path: /tmp/symbolic/SKILL.md", rendered)

        activity_state = blank_run_state()
        activity_state["current_agent"] = "investigator"
        activity_state["agents"]["investigator"].update({
            "skills_available": ["symbolic", "plotting"],
            "skills_used": ["symbolic"],
            "skill_load_events": 2,
        })
        self.assertNotIn("Skills", render_activity(activity_state))
        self.assertNotIn("loads:", render_activity(activity_state))
        self.assertIn("Elapsed", render_activity(activity_state))

    def test_error_pane_reports_terminal_workflow_error(self):
        state = blank_run_state()
        state.update({"status": "FAILED", "current_activity": "Codex process closed stdout. stderr_tail="})
        self.assertEqual(
            render_error(state),
            "FAILED: Codex process closed stdout. stderr_tail=",
        )
        state["status"] = "RUNNING"
        self.assertEqual(render_error(state), "(no current error)")


class AttachTuiTest(unittest.TestCase):
    def test_tui_master_id_uses_global_search_from_any_working_directory(self):
        from theory_master.__main__ import main

        spec = SimpleNamespace(research_root=Path("/tmp/global-tui-task"))
        search_root = Path("/tmp/theory-master-search")
        with patch("sys.argv", ["theory_master", "tui", "0", "--root", str(search_root)]), \
             patch("theory_master.cli_discovery.spec_from_task", side_effect=ValueError("not local")), \
             patch("theory_master.cli_discovery.spec_from_resume_identifier", return_value=spec) as resolve, \
             patch("theory_master.runtime_manager.reconnect_tui") as reconnect:
            self.assertEqual(main(), 0)
        resolve.assert_called_once_with("0", search_root)
        reconnect.assert_called_once_with(spec, focus=True)

    def test_tui_without_id_keeps_current_task_discovery(self):
        from theory_master.__main__ import main

        spec = SimpleNamespace(research_root=Path("/tmp/local-tui-task"))
        with patch("sys.argv", ["theory_master", "tui"]), \
             patch("theory_master.cli_discovery.spec_from_task", return_value=spec) as resolve, \
             patch("theory_master.runtime_manager.reconnect_tui") as reconnect:
            self.assertEqual(main(), 0)
        resolve.assert_called_once_with()
        reconnect.assert_called_once_with(spec, focus=True)

    def test_standalone_tui_attaches_real_workflow_control_plane(self):
        from theory_master.__main__ import attach_tui
        from theory_master.core import Workflow

        spec = SimpleNamespace(
            research_root=Path("/tmp/theory-master-attach-test"),
            branch_canvas=None,
            next_step_start_id=None,
            task_mode=True,
            master_id=0,
        )
        source = SimpleNamespace(snapshot=lambda: {"run_id": "cycle-0001"})
        with patch("theory_master.observability.collector.FileSource", return_value=source), \
             patch("theory_master.tui.app.launch_tui") as launch:
            self.assertEqual(attach_tui(spec), 0)
        args, kwargs = launch.call_args
        self.assertIs(args[0], source)
        self.assertTrue(kwargs["running_workflow"])
        self.assertIsInstance(kwargs["workflow"], Workflow)
        self.assertTrue(callable(kwargs["workflow"].request_control))

    def test_failed_live_run_requests_auto_close_after_repair_handoff(self):
        from theory_master.__main__ import run_with_tui

        class Collector:
            def snapshot(self):
                return {"repair_handoff": {"status": "started", "target": "agent:修复.0"}}

        with tempfile.TemporaryDirectory() as temp:
            workflow = SimpleNamespace(
                spec=SimpleNamespace(
                    research_root=Path(temp),
                    task_mode=False,
                    control_canvas=Path(temp) / "control.canvas",
                ),
                run=lambda: None,
            )

            def fail():
                raise RuntimeError("boom")

            with patch("theory_master.tui.app.launch_tui") as launch, \
                 patch("theory_master.cli_runtime._schedule_close_current_tmux_window") as close:
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    run_with_tui(workflow, Collector(), operation=fail)
            self.assertTrue(launch.call_args.kwargs["close_on_repair_handoff"])
            close.assert_called_once()


class TextualAppTest(unittest.TestCase):
    def test_app_constructs_when_textual_available(self):
        try:
            import textual  # noqa: F401
        except ImportError:
            self.skipTest("textual not installed")
        from theory_master.tui.app import TheoryMasterApp

        class Source:
            def snapshot(self):
                return blank_run_state()
            def runtime_events(self, limit=400):
                return []
            def logs(self, limit=200):
                return []

        app = TheoryMasterApp(Source())
        self.assertTrue(hasattr(app, "run"))
        self.assertIn("#header { border: solid cyan;", app.CSS)
        self.assertIn("border-right: solid cyan", app.CSS)
        self.assertNotIn("#workflow { height: 10; }", app.CSS)
        demo = TheoryMasterApp(DemoSource(), running_workflow=False)
        self.assertTrue(hasattr(demo, "run"))
        self.assertFalse(demo.running_workflow)
        self.assertIn("#last-scroll", demo.CSS)
        self.assertNotIn("#hypothesis-col", demo.CSS)
        self.assertNotIn("#challenges-scroll", demo.CSS)
        labels = {binding.key: binding.description for binding in app.BINDINGS}
        self.assertNotIn("5", labels)
        self.assertEqual(labels["r"], "Research")
        self.assertEqual(labels["c"], "Max cycles")
        self.assertEqual(labels["a"], "Supervisor")
        self.assertNotIn("h", labels)
        self.assertEqual(labels["p"], "Pause")
        self.assertNotIn("i", labels)
        self.assertEqual(labels["k"], "Kill Cycle")
        self.assertEqual(labels["ctrl+c"], "Exit")

    def test_legacy_interrupt_on_exit_option_now_requests_safe_pause(self):
        try:
            import textual  # noqa: F401
        except ImportError:
            self.skipTest("textual not installed")
        import time
        from theory_master.tui.app import TheoryMasterApp

        class Source:
            def snapshot(self): return blank_run_state()
            def runtime_events(self, limit=400): return []
            def logs(self, limit=200): return []

        app = TheoryMasterApp(Source(), running_workflow=True,
                              workflow=object(), interrupt_on_exit=True)
        app._quit_armed_until = time.monotonic() + 30
        with patch.object(app, "_request_control", return_value="ok") as control, \
             patch.object(app, "exit") as exit_mock:
            app._confirm_exit("q")
            control.assert_called_once_with("pause")
            exit_mock.assert_called_once()


    def test_deprecation_notice_shows_on_every_new_tui_launch(self):
        try:
            import textual  # noqa: F401
        except ImportError:
            self.skipTest("textual not installed")
        from theory_master.tui.app import TheoryMasterApp

        class Source:
            def snapshot(self): return blank_run_state()
            def runtime_events(self, limit=400): return []
            def logs(self, limit=200): return []

        async def exercise():
            for key in ("enter", "escape"):
                app = TheoryMasterApp(Source(), running_workflow=False)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    self.assertEqual(app.screen.__class__.__name__, "NoticeScreen")
                    self.assertIn("停止维护", app.screen.heading)
                    self.assertIn("建议使用 Web 端", app.screen.detail)
                    self.assertIn("http://127.0.0.1:8787/", app.screen.detail)
                    await pilot.press(key)
                    await pilot.pause()
                    self.assertEqual(app.screen.__class__.__name__, "Screen")
                    app.refresh_state()
                    await pilot.pause()
                    self.assertEqual(app.screen.__class__.__name__, "Screen")
                    await pilot.press("f12")  # tmux reattach triggers this key
                    await pilot.pause()
                    self.assertEqual(app.screen.__class__.__name__, "NoticeScreen")
                    self.assertIn("停止维护", app.screen.heading)
                    await pilot.press("enter")
                    await pilot.pause()
                    self.assertEqual(app.screen.__class__.__name__, "Screen")

        asyncio.run(exercise())

    def test_pane_treats_malformed_markup_as_literal_text(self):
        try:
            import textual  # noqa: F401
        except ImportError:
            self.skipTest("textual not installed")
        import asyncio
        from theory_master.tui.app import TheoryMasterApp

        class Source:
            def snapshot(self):
                state = blank_run_state()
                state["event_history"] = ["[foo='/home/broken]"]
                return state
            def runtime_events(self, limit=400):
                return []
            def logs(self, limit=200):
                return []

        async def exercise():
            app = TheoryMasterApp(Source())
            async with app.run_test() as pilot:
                app.query_one("#last").show(Source().snapshot())
                await pilot.pause()

        asyncio.run(exercise())

    def test_needs_attention_screen_accepts_operator_guidance(self):
        try:
            import textual  # noqa: F401
        except ImportError:
            self.skipTest("textual not installed")
        import asyncio
        from textual.widgets import Input
        from theory_master.tui.app import TheoryMasterApp

        class Source:
            def snapshot(self):
                state = blank_run_state()
                state.update({
                    "status": "NEEDS_ATTENTION", "master_id": 7,
                    "attention": {"code": "needs_input", "reason": "Need operator direction."},
                })
                return state
            def runtime_events(self, limit=400): return []
            def logs(self, limit=200): return []

        class WorkflowControl:
            def __init__(self): self.guidance = None
            def submit_attention_guidance(self, text, source="human"):
                self.guidance = (text, source)
                return {"source": source, "path": "/tmp/operator_guidance.md"}
            def request_control(self, *args, **kwargs): return {}
            def current_max_cycles(self): return 1
            def set_max_cycles(self, value): return value
            def set_attention_takeover(self, enabled): return {"enabled": enabled, "handoff": None}

        async def exercise():
            workflow = WorkflowControl()
            app = TheoryMasterApp(Source(), running_workflow=True, workflow=workflow)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("escape")  # dismiss startup deprecation notice
                await pilot.pause()
                field = app.screen.query_one("#attention-guidance-input", Input)
                field.value = "Use the verified local artifact and retry qualification."
                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(workflow.guidance, ("Use the verified local artifact and retry qualification.", "human"))

        asyncio.run(exercise())

    def test_events_screen_opens_at_latest_event(self):
        try:
            import textual  # noqa: F401
        except ImportError:
            self.skipTest("textual not installed")
        import asyncio
        from textual.containers import VerticalScroll
        from theory_master.tui.app import TheoryMasterApp

        class Source:
            def __init__(self):
                self._events = [
                    {
                        "ts": f"2026-09-27T19:{index // 60:02d}:{index % 60:02d}+08:00",
                        "event": "LLM_MESSAGE",
                        "agent": "investigator",
                        "role": "investigator",
                        "content": f"event {index} " + ("x" * 100),
                    }
                    for index in range(90)
                ]

            def snapshot(self):
                return blank_run_state()

            def runtime_events(self, limit=400):
                return list(self._events)

            def logs(self, limit=200):
                return []

        async def exercise():
            app = TheoryMasterApp(Source())
            async with app.run_test(size=(80, 20)) as pilot:
                await pilot.press("enter")  # dismiss startup deprecation notice
                await pilot.press("3")
                await pilot.pause()
                await pilot.pause()
                scroll = app.screen.query_one("#body-scroll", VerticalScroll)
                self.assertTrue(scroll.is_vertical_scroll_end)

        asyncio.run(exercise())


class DemoSourceTest(unittest.TestCase):
    def test_demo_snapshot_fills_every_pane(self):
        source = DemoSource()
        state = source.snapshot()
        self.assertEqual(state["goal_id"], "tui-demo")
        header = render_header(state)
        self.assertIn("Started", header)
        self.assertIn("Cycle start", header)
        self.assertIn("Elapsed", header)
        self.assertNotIn("Goal ", header)
        self.assertIn("Process RUNNING", header)
        self.assertIn("State RUNNING", header)
        # Goal Gate is scientific content, intentionally kept out of the
        # operational header after the scientific-only refactor.
        self.assertNotIn("Gate", header)
        workflow = render_workflow(state)
        self.assertIn("Investigator", workflow)
        self.assertIn("STATE", workflow)
        self.assertIn("GPT-5.6 Sol", workflow)
        self.assertIn("GPT-5.6 Terra", workflow)
        self.assertTrue(_workflow_columns_share_starts(workflow))
        self.assertIn("[h] full current hypothesis", render_hypothesis(state))
        self.assertGreater(len(render_hypothesis(state, full=True)), 200)
        self.assertIn("Previous", render_hypothesis(state))
        self.assertEqual(render_last(state), "(none)")
        self.assertIn("R2 Investigator", render_investigation(state))
        self.assertIn("Hypothesis Lock  locked", render_challenges(state))
        self.assertNotIn("Investigation Requirements", render_challenges(state))
        self.assertIn("Investigator", render_activity(state))
        self.assertNotIn("History", render_activity(state))
        self.assertIn("waiting", render_judge(state))
        self.assertNotIn("Models       mixed", render_tokens(state))
        self.assertNotIn("see workflow", render_tokens(state))
        self.assertIn("thr_demo_investigator", render_thread(state))
        self.assertIn("branch.canvas", render_canvas(state))
        self.assertIn("sketch.md", render_artifacts(state))
        self.assertIn("chronological · oldest → newest", render_events(source.runtime_events()))
        self.assertEqual(len(source.runtime_events(limit=0)), len(source._events))
        self.assertIn("09-20T23:40:01", render_logs(source.logs()))
        self.assertGreater(state["elapsed_s"], 0)

    def test_demo_state_helper_is_offline(self):
        state = demo_state()
        self.assertEqual(state["status"], "RUNNING")
        self.assertEqual(state["agents"]["investigator"]["state"], "running")


class QuotaWaitingModalTest(unittest.TestCase):
    def test_wait_modal_once_and_auto_dismiss_on_recovery(self):
        from theory_master.tui.app import TheoryMasterApp

        class Source:
            def __init__(self):
                self.state = blank_run_state()
                self.state["status"] = "PAUSED"
                self.state["quota_auto_adjust_enabled"] = True
                self.state["quota_auto_adjust"] = {"status": "idle"}

            def snapshot(self):
                return self.state

            def runtime_events(self, limit=0):
                return []

            def logs(self):
                return []

        async def scenario():
            source = Source()
            app = TheoryMasterApp(source, running_workflow=False)
            async with app.run_test(size=(110, 42)) as pilot:
                await pilot.pause()
                self.assertEqual(app.screen.__class__.__name__, "NoticeScreen")
                await pilot.press("enter")  # dismiss startup deprecation notice
                await pilot.pause()
                self.assertEqual(app.screen.__class__.__name__, "Screen")
                source.state["quota_auto_adjust"] = {
                    "status": "waiting_refresh", "selected_account": "1", "failed_account": "2",
                    "accounts": {"1": {"ok": True, "primary": {"remaining_percent": 42}},
                                 "2": {"ok": False, "error": "timeout"}},
                }
                app.refresh_state()
                await pilot.pause()
                self.assertEqual(app.screen.__class__.__name__, "NoticeScreen")
                self.assertIn("account-1", app.screen.detail)
                self.assertIn("42%", app.screen.detail)
                source.state["quota_auto_adjust"]["accounts"]["1"]["primary"]["remaining_percent"] = 35
                app.refresh_state()
                await pilot.pause()
                self.assertIn("35%", app.screen.detail)
                await pilot.press("escape")
                await pilot.pause()
                app.refresh_state()
                await pilot.pause()
                self.assertEqual(app.screen.__class__.__name__, "Screen")
                source.state["quota_auto_adjust"]["status"] = "resuming"
                app.refresh_state()
                source.state["quota_auto_adjust"]["status"] = "waiting_refresh"
                app.refresh_state()
                await pilot.pause()
                self.assertEqual(app.screen.__class__.__name__, "NoticeScreen")
                source.state["quota_auto_adjust"]["status"] = "idle"
                app.refresh_state()
                await pilot.pause()
                self.assertEqual(app.screen.__class__.__name__, "Screen")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
