from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.observability.models import blank_run_state
from theory_master.observability.projection import reconcile_runtime_state
from theory_master.runtime_manager import (
    ensure_canonical_tui,
    foreground_runtime,
    load_registry,
    process_start_ticks,
    runtime_health,
    update_registry,
)


class RuntimeManagerTest(unittest.TestCase):
    def test_zombie_process_is_not_a_live_owner(self):
        from theory_master.runtime_manager import process_identity_alive
        # A zombie still answers kill(pid, 0); stat state must win.
        stat = "123 (worker process) Z 1 1 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 12345 0 0"
        with patch("theory_master.runtime_manager.os.kill"), \
             patch("theory_master.runtime_manager.Path.read_text", return_value=stat):
            self.assertFalse(process_identity_alive(123))
            self.assertFalse(process_identity_alive(123, 12345))

    def test_live_owner_still_checks_pid_start_ticks(self):
        from theory_master.runtime_manager import process_identity_alive
        stat = "123 (live process) S " + " ".join(str(i) for i in range(1, 22))
        with patch("theory_master.runtime_manager.os.kill"), \
             patch("theory_master.runtime_manager.Path.read_text", return_value=stat):
            self.assertTrue(process_identity_alive(123))
            self.assertTrue(process_identity_alive(123, 19))
            self.assertFalse(process_identity_alive(123, 20))

    def test_runtime_python_honors_explicit_sdk_interpreter(self):
        from theory_master import runtime_manager
        with patch.dict(os.environ, {"THEORYMASTER_RUNTIME_PYTHON": "/chosen/python"}, clear=False), \
             patch("theory_master.runtime_manager.Path.is_file", return_value=True), \
             patch("theory_master.runtime_manager.os.access", return_value=True), \
             patch("theory_master.runtime_manager.Path.resolve", side_effect=[Path("/chosen/python"), Path("/current/python")]):
            self.assertEqual(runtime_manager.runtime_python(), "/chosen/python")

    def test_checkpoint_reached_projects_as_intentional_terminal_status(self):
        snap = blank_run_state()
        reconcile_runtime_state(snap, {
            "status": "checkpoint_reached",
            "phase": "pre_hypothesis_lock",
            "execution_active": False,
            "resumable": False,
        })
        self.assertEqual(snap["status"], "CHECKPOINT_REACHED")
        self.assertFalse(snap["execution_active"])
        self.assertFalse(snap["resumable"])

    def test_health_reports_live_supervisor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            update_registry(
                root,
                state="RUNNING",
                supervisor_pid=os.getpid(),
                supervisor_start_ticks=process_start_ticks(os.getpid()),
                runner_pid=os.getpid(),
                runner_start_ticks=process_start_ticks(os.getpid()),
                heartbeat_at="2099-01-01T00:00:00+00:00",
            )
            health = runtime_health(root)
            self.assertTrue(health["active"])
            self.assertEqual(health["state"], "RUNNING")

    def test_foreground_runtime_resume_is_live_and_accumulates_active_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("theory_master.runtime_manager.HEARTBEAT_INTERVAL_S", 0.01):
                with foreground_runtime(root, master_id=14, operation="run"):
                    first = runtime_health(root)
                    self.assertTrue(first["active"])
                    self.assertEqual(first["state"], "RUNNING")
                    self.assertEqual(first["registry"]["runner_pid"], os.getpid())
                    import time
                    time.sleep(0.03)
                    first_elapsed = float(load_registry(root)["active_elapsed_s"])
                    self.assertGreater(first_elapsed, 0.0)
                self.assertEqual(load_registry(root)["state"], "STOPPED")
                with foreground_runtime(root, master_id=14, operation="resume"):
                    resumed = runtime_health(root)
                    self.assertTrue(resumed["active"])
                    self.assertEqual(resumed["state"], "RUNNING")
                    self.assertEqual(resumed["registry"]["master_id"], 14)
                    self.assertEqual(resumed["registry"]["resume_count"], 1)
                    self.assertGreaterEqual(float(resumed["registry"]["active_elapsed_s"]), first_elapsed)
                    self.assertTrue(resumed["registry"].get("runtime_segments"))

    def test_foreground_runtime_projection_keeps_tui_live_and_elapsed_advancing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            update_registry(
                root,
                state="RUNNING",
                launcher="foreground",
                runner_pid=os.getpid(),
                runner_start_ticks=process_start_ticks(os.getpid()),
                heartbeat_at="2099-01-01T00:00:00+00:00",
                active_elapsed_s=42.5,
            )
            snap = blank_run_state()
            snap.update({
                "research_root": str(root),
                "status": "RUNNING",
                "elapsed_s": 5.0,
                "project_elapsed_s": 5.0,
            })
            sci = {
                "status": "active",
                "phase": "numerical:running",
                "execution_active": False,
                "owner_pid": None,
                "resumable": True,
            }
            reconcile_runtime_state(snap, sci)
            self.assertTrue(snap["execution_active"])
            self.assertEqual(snap["runtime_health"], "RUNNING")
            self.assertEqual(snap["runtime_active_elapsed_s"], 42.5)
            self.assertEqual(snap["elapsed_s"], 42.5)
            self.assertEqual(snap["project_elapsed_s"], 42.5)

    def test_health_reports_orphaned_dead_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            update_registry(
                root,
                state="RUNNING",
                supervisor_pid=99999999,
                runner_pid=99999998,
                heartbeat_at="2026-01-01T00:00:00+00:00",
            )
            health = runtime_health(root)
            self.assertFalse(health["active"])
            self.assertEqual(health["state"], "ORPHANED")

    def test_projection_never_leaves_dead_agent_running(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            update_registry(root, state="RUNNING", supervisor_pid=99999999, runner_pid=99999998)
            snap = blank_run_state()
            snap.update({
                "research_root": str(root),
                "status": "RUNNING",
                "execution_active": True,
                "current_agent": "investigator",
            })
            snap["agents"]["investigator"]["state"] = "running"
            sci = {
                "status": "active",
                "phase": "investigator:running",
                "execution_active": True,
                "owner_pid": 99999998,
                "resumable": True,
            }
            reconcile_runtime_state(snap, sci)
            self.assertEqual(snap["status"], "ORPHANED")
            self.assertFalse(snap["execution_active"])
            self.assertIsNone(snap["current_agent"])
            self.assertEqual(snap["agents"]["investigator"]["state"], "done")

    def test_canonical_tui_reuses_registered_window(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            canvas = root / "control.canvas"
            canvas.write_text("{}", encoding="utf-8")
            spec = SimpleNamespace(research_root=root, master_id=0, control_canvas=canvas, task_mode=True)
            update_registry(root, state="STOPPED", tui={"window_id": "@7", "session": "agent", "window_name": "TheoryMaster-0"})
            windows = [{"window_id": "@7", "session": "agent", "window_name": "TheoryMaster-0", "pane_pid": "123"}]
            with patch("theory_master.runtime_manager._tmux_windows", return_value=windows):
                target = ensure_canonical_tui(spec)
            self.assertEqual(target["status"], "existing")
            self.assertEqual(target["window_id"], "@7")

    def test_reconnect_existing_tui_reopens_deprecation_notice(self):
        from theory_master.runtime_manager import reconnect_tui
        spec = SimpleNamespace(research_root=Path("/unused"), master_id=7)
        existing = {"status": "existing", "window_id": "@7", "session": "agent"}
        created = {"status": "created", "window_id": "@8", "session": "agent"}
        with patch("theory_master.runtime_manager.ensure_canonical_tui", side_effect=[existing, created]), \
             patch("theory_master.runtime_manager.focus_tui_window") as focus, \
             patch("theory_master.runtime_manager.sys.stdin.isatty", return_value=True), \
             patch("theory_master.runtime_manager.sys.stdout.isatty", return_value=True), \
             patch("theory_master.runtime_manager.subprocess.run") as run:
            reconnect_tui(spec)
            run.assert_called_once_with(
                ["tmux", "send-keys", "-t", "@7", "F12"],
                text=True, capture_output=True, check=False,
            )
            reconnect_tui(spec)
            self.assertEqual(run.call_count, 1)  # new window already shows it on mount
            self.assertEqual(focus.call_count, 2)

    def test_canonical_tui_launches_from_task_binding_not_canvas(self):
        from theory_master.runtime_manager import ensure_canonical_tui
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = SimpleNamespace(research_root=root, master_id=0, task_mode=True)
            row = {"window_id": "@8", "session": "agent", "window_name": "TheoryMaster-0", "pane_pid": "321"}
            proc = SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch("theory_master.runtime_manager.shutil.which", return_value="/usr/bin/tmux"), \
                 patch("theory_master.runtime_manager._ensure_tmux_session"), \
                 patch("theory_master.runtime_manager._tmux_windows", side_effect=[[], [row], [row]]), \
                 patch("theory_master.runtime_manager.subprocess.run", return_value=proc) as run, \
                 patch("theory_master.runtime_manager.time.sleep"):
                target = ensure_canonical_tui(spec)
            self.assertEqual(target["window_id"], "@8")
            command = run.call_args.args[0][-1]
            self.assertIn("-m theory_master tui 0 --direct", command)
            self.assertNotIn("--canvas", command)
            self.assertIn(str(root), command)

    def test_dead_tui_window_is_not_persisted_as_canonical(self):
        from theory_master.runtime_manager import ensure_canonical_tui
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = SimpleNamespace(research_root=root, master_id=0, task_mode=True)
            row = {"window_id": "@8", "session": "agent", "window_name": "TheoryMaster-0", "pane_pid": "321"}
            proc = SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch("theory_master.runtime_manager.shutil.which", return_value="/usr/bin/tmux"), \
                 patch("theory_master.runtime_manager._ensure_tmux_session"), \
                 patch("theory_master.runtime_manager._tmux_windows", side_effect=[[], [row], []]), \
                 patch("theory_master.runtime_manager.subprocess.run", return_value=proc), \
                 patch("theory_master.runtime_manager.time.sleep"):
                with self.assertRaisesRegex(RuntimeError, "exited during startup"):
                    ensure_canonical_tui(spec)
            self.assertIsNone(load_registry(root).get("tui"))

    def test_systemd_stop_owns_entire_supervisor_runner_control_group(self):
        from theory_master.runtime_manager import launch_managed_runtime
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = SimpleNamespace(research_root=root, master_id=0, task_mode=True)
            proc = SimpleNamespace(returncode=0, stdout="started", stderr="")
            with patch("theory_master.runtime_manager.shutil.which", return_value="/usr/bin/systemd-run"), \
                 patch("theory_master.runtime_manager.subprocess.run", return_value=proc) as run:
                result = launch_managed_runtime(spec, operation="run", identifier="0")
            self.assertEqual(result["status"], "started")
            command = run.call_args.args[0]
            self.assertIn("--property=KillMode=control-group", command)

    def test_new_launch_resets_supervisor_attempt_before_supervisor_starts(self):
        from theory_master.runtime_manager import launch_managed_runtime
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = SimpleNamespace(research_root=root, master_id=0, task_mode=True)
            update_registry(root, state="STOPPED", supervisor_attempt=9, recovery_count=4)
            proc = SimpleNamespace(returncode=0, stdout="started", stderr="")
            with patch("theory_master.runtime_manager.shutil.which", return_value="/usr/bin/systemd-run"), \
                 patch("theory_master.runtime_manager.subprocess.run", return_value=proc):
                result = launch_managed_runtime(spec, operation="run", identifier="0")
            self.assertEqual(result["status"], "started")
            registry = load_registry(root)
            self.assertEqual(registry["supervisor_attempt"], 0)
            self.assertEqual(registry["recovery_count"], 0)
            self.assertEqual(registry["requested_operation"], "run")

    def test_launch_refuses_duplicate_legacy_runner(self):
        from theory_master.runtime_manager import launch_managed_runtime
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle = root / "cycle-0001"
            cycle.mkdir()
            (cycle / ".theory-master-cycle").write_text("theory-master/v2\n", encoding="utf-8")
            (cycle / "research_state.json").write_text(json.dumps({
                "status": "active",
                "execution_active": True,
                "owner_pid": os.getpid(),
                "resumable": True,
            }), encoding="utf-8")
            spec = SimpleNamespace(research_root=root, task_mode=True, master_id=0)
            result = launch_managed_runtime(spec, operation="resume", identifier="0")
            self.assertEqual(result["status"], "already_running")
            self.assertTrue(result.get("legacy"))
            registry = load_registry(root)
            self.assertEqual(registry["runner_pid"], os.getpid())
            self.assertEqual(registry["state"], "LEGACY_RUNNING")

    def test_quota_auto_adjust_task_setting_wins_over_inherited_environment(self):
        from theory_master.quota_auto_adjust import (
            quota_auto_adjust_enabled,
            write_quota_auto_adjust_setting,
        )
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ, {"THEORY_MASTER_QUOTA_AUTO_ADJUST": "0"}, clear=False):
            root = Path(temp)
            self.assertFalse(quota_auto_adjust_enabled(root))
            write_quota_auto_adjust_setting(root, True)
            self.assertTrue(quota_auto_adjust_enabled(root))
            write_quota_auto_adjust_setting(root, False)
            self.assertFalse(quota_auto_adjust_enabled(root))


class SupervisorRecoveryTest(unittest.TestCase):
    def test_quota_pause_exit_zero_relaunches_as_resume_without_crash_budget(self):
        from theory_master import supervisor

        class Child:
            def __init__(self, pid, on_wait):
                self.pid = pid
                self.on_wait = on_wait
            def wait(self, timeout=None):
                self.on_wait()
                return 0

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".theory-master").mkdir()
            update_registry(root, state="LAUNCHING", launch_id="launch-quota")
            cycle = root / "cycle-0001"
            cycle.mkdir()
            (cycle / ".theory-master-cycle").write_text("theory-master/v2\n", encoding="utf-8")
            state_path = cycle / "research_state.json"
            state_path.write_text(json.dumps({
                "status": "active",
                "phase": "investigator:running",
                "execution_active": True,
                "resumable": True,
            }), encoding="utf-8")

            def quota_pause():
                state_path.write_text(json.dumps({
                    "status": "paused",
                    "phase": "provider_quota_exhausted",
                    "execution_active": False,
                    "resumable": True,
                    "recovery": {
                        "code": "provider_quota_exhausted",
                        "provider": "CodexHTTPS",
                        "codex_account": "1",
                    },
                }), encoding="utf-8")

            def finish_cycle():
                state_path.write_text(json.dumps({
                    "status": "over",
                    "phase": "complete",
                    "execution_active": False,
                    "resumable": False,
                }), encoding="utf-8")

            children = [Child(11111, quota_pause), Child(22222, finish_cycle)]
            calls = []

            def fake_popen(cmd, **kwargs):
                calls.append(list(cmd))
                return children.pop(0)

            argv = [
                "--task-root", str(root),
                "--operation", "run",
                "--identifier", "0",
                "--launch-id", "launch-quota",
            ]
            with patch("theory_master.supervisor.subprocess.Popen", side_effect=fake_popen), \
                 patch("theory_master.supervisor.process_start_ticks", return_value=1), \
                 patch("theory_master.supervisor.process_identity_alive", return_value=False), \
                 patch.object(supervisor, "_quota_auto_resume", return_value=True) as auto_resume:
                self.assertEqual(supervisor.main(argv), 0)

            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][calls[0].index("--operation") + 1], "run")
            self.assertEqual(calls[1][calls[1].index("--operation") + 1], "resume")
            auto_resume.assert_called_once_with(root)
            registry = load_registry(root)
            self.assertEqual(registry["state"], "STOPPED")
            self.assertEqual(int(registry.get("recovery_count") or 0), 0)

    def test_child_failure_restarts_as_resume(self):
        from theory_master import supervisor

        class Child:
            def __init__(self, pid, rc):
                self.pid = pid
                self.rc = rc
            def wait(self, timeout=None):
                return self.rc

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".theory-master").mkdir()
            update_registry(root, state="LAUNCHING", launch_id="launch-test")
            cycle = root / "cycle-0001"
            cycle.mkdir()
            (cycle / ".theory-master-cycle").write_text("theory-master/v2\n", encoding="utf-8")
            (cycle / "research_state.json").write_text(json.dumps({
                "status": "active",
                "phase": "numerical:running",
                "execution_active": True,
                "owner_pid": 99999999,
                "resumable": True,
                "last_safe_checkpoint": str(cycle / "checkpoints/000001-safe.json"),
                "role_outputs": {"research_lead_frontier": {"candidates": [{"id": "h1"}]}},
            }), encoding="utf-8")
            children = [Child(11111, 1), Child(22222, 0)]
            calls = []

            def fake_popen(cmd, **kwargs):
                calls.append(list(cmd))
                return children.pop(0)

            argv = [
                "--task-root", str(root),
                "--operation", "run",
                "--identifier", "0",
                "--launch-id", "launch-test",
            ]
            with patch("theory_master.supervisor.subprocess.Popen", side_effect=fake_popen), \
                 patch("theory_master.supervisor.process_start_ticks", return_value=1), \
                 patch("theory_master.supervisor.process_identity_alive", return_value=False), \
                 patch("theory_master.supervisor.time.sleep", return_value=None):
                self.assertEqual(supervisor.main(argv), 0)
            self.assertEqual(len(calls), 2)
            first_op = calls[0][calls[0].index("--operation") + 1]
            second_op = calls[1][calls[1].index("--operation") + 1]
            self.assertEqual(first_op, "run")
            self.assertEqual(second_op, "resume")
            self.assertTrue(all("--managed-child" in call for call in calls))
            self.assertEqual(load_registry(root)["state"], "STOPPED")

    def test_precheckpoint_qualification_is_recoverable_after_runner_loss(self):
        from theory_master import supervisor
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle = root / "cycle-0001"
            cycle.mkdir()
            (cycle / ".theory-master-cycle").write_text("theory-master/v2\n", encoding="utf-8")
            (cycle / "research_state.json").write_text(json.dumps({
                "status": "active",
                "phase": "research_lead:running",
                "execution_active": True,
                "owner_pid": 99999999,
                "resumable": False,
                "last_safe_checkpoint": None,
                "role_outputs": {},
                "hypothesis_lock": {"status": "unlocked"},
                "hypothesis_locked": False,
                "qualification_status": "pending",
            }), encoding="utf-8")
            self.assertEqual(supervisor._exit_disposition(root), "recoverable")

    def test_quota_controller_switches_to_available_other_account(self):
        from contextlib import nullcontext
        from theory_master import supervisor

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".theory-master").mkdir()
            update_registry(root, state="RUNNING")
            snapshots = {
                "1": {"account": "1", "ok": True, "ordinary_usage_allowed": False, "primary": {"resets_at": 123}},
                "2": {"account": "2", "ok": True, "ordinary_usage_allowed": True},
            }
            with patch.object(supervisor, "_quota_snapshots", return_value=snapshots), \
                 patch("theory_master.codex_accounts.selected_account", return_value="1"), \
                 patch("theory_master.codex_accounts.account_switch_lock", return_value=nullcontext()), \
                 patch("theory_master.codex_accounts.use_account") as switch:
                chosen = supervisor._resolve_quota_pause(
                    root,
                    {"provider": "CodexHTTPS", "failed_account": "1", "message": "usage limit"},
                )

            self.assertEqual(chosen, "2")
            switch.assert_called_once_with("2", restart_app=False, _lock_held=True)
            controller = json.loads((root / ".theory-master" / "quota-auto-adjust.json").read_text(encoding="utf-8"))
            self.assertEqual(controller["status"], "resuming")
            self.assertIn("1", controller["exhausted_accounts"])
            self.assertEqual(load_registry(root)["state"], "QUOTA_RECOVERING")

    def test_quota_wait_resumes_when_either_account_refreshes(self):
        from contextlib import nullcontext
        from theory_master import supervisor

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / ".theory-master"
            runtime.mkdir()
            (runtime / "env").write_text("THEORY_MASTER_QUOTA_AUTO_ADJUST=1\n", encoding="utf-8")
            update_registry(root, state="WAITING_QUOTA")
            # A real Supervisor can only wait for quota while an actual
            # paused, resumable Cycle with a quota-exhausted reason exists.
            cycle = root / "1"
            cycle.mkdir()
            (cycle / ".theory-master-cycle").write_text("theory-master/v2\n")
            (cycle / "research_state.json").write_text(json.dumps({
                "status": "paused", "resumable": True,
                "phase": "provider_quota_exhausted",
                "recovery": {"code": "provider_quota_exhausted", "provider": "CodexHTTPS",
                             "codex_account": "1"}
            }))
            both_empty = {
                "1": {"account": "1", "ok": True, "ordinary_usage_allowed": False},
                "2": {"account": "2", "ok": True, "ordinary_usage_allowed": False},
            }
            second_refreshed = {
                "1": {"account": "1", "ok": True, "ordinary_usage_allowed": False},
                "2": {"account": "2", "ok": True, "ordinary_usage_allowed": True},
            }
            # This is a quota recovery test, not a mail delivery test.
            # Never invoke the configured production SMTP sender from a fixture.
            with patch.object(supervisor, "_quota_snapshots", side_effect=[both_empty, second_refreshed]), \
                 patch("theory_master.codex_accounts.selected_account", return_value="1"), \
                 patch("theory_master.codex_accounts.account_switch_lock", return_value=nullcontext()), \
                 patch("theory_master.codex_accounts.use_account") as switch, \
                 patch.object(supervisor, "_notify_verified_quota_wait"), \
                 patch("theory_master.supervisor.time.sleep", return_value=None):
                chosen = supervisor._wait_for_quota(root)

            self.assertEqual(chosen, "2")
            switch.assert_called_once_with("2", restart_app=False, _lock_held=True)
            controller = json.loads((runtime / "quota-auto-adjust.json").read_text(encoding="utf-8"))
            self.assertEqual(controller["status"], "resuming")
            self.assertNotIn("2", controller.get("exhausted_accounts") or {})
            self.assertEqual(load_registry(root)["state"], "QUOTA_RECOVERING")

    def test_repair_handoff_prevents_parallel_supervisor_resume(self):
        from theory_master import supervisor

        class Child:
            pid = 11111
            def wait(self, timeout=None):
                return 1

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".theory-master").mkdir()
            cycle = root / "cycle-0001"
            cycle.mkdir()
            (cycle / ".theory-master-cycle").write_text("theory-master/v2\n", encoding="utf-8")
            (cycle / "run_state.json").write_text(json.dumps({
                "repair_handoff": {"status": "started", "target": "agent:repair"},
            }), encoding="utf-8")
            update_registry(root, state="LAUNCHING", launch_id="launch-repair")
            argv = ["--task-root", str(root), "--operation", "run", "--identifier", "0", "--launch-id", "launch-repair"]
            with patch("theory_master.supervisor.subprocess.Popen", return_value=Child()) as popen, \
                 patch("theory_master.supervisor.process_start_ticks", return_value=1), \
                 patch("theory_master.supervisor.process_identity_alive", return_value=False):
                self.assertEqual(supervisor.main(argv), 0)
            self.assertEqual(popen.call_count, 1)
            # A historical repair_handoff=started without a scientific
            # research_state is not live or recoverable ownership proof.
            self.assertEqual(load_registry(root)["state"], "FAILED_RECOVERY")


if __name__ == "__main__":
    unittest.main()
