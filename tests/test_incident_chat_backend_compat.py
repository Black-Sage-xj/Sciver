from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theory_master.specification import TaskSpec
from theory_master.task import bootstrap
from theory_master.supervisor_incident_session import (
    _incident_supervisor_backend,
    _run_model_follow_up,
    post_human_message,
    publish_review_incident,
)


class IncidentChatBackendCompatibilityTests(unittest.TestCase):
    def _make_task(self, parent: Path):
        task_home = parent / "task"
        task_home.mkdir()
        plan = task_home / "research_plan.md"
        plan.write_text(
            "# Goal\n\nCheck a local compatibility case.\n\n"
            "# Research Contract\n\nUse only deterministic test fixtures.\n\n"
            "# Final Gate\n\nThe test finishes without a model call.\n\n"
            "# Required Context\n\nNone.\n",
            encoding="utf-8",
        )
        return bootstrap(plan)

    def test_incident_backend_accepts_old_embedded_format_without_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            binding = self._make_task(Path(temp))
            config = json.loads(binding.config_path.read_text(encoding="utf-8"))
            self.assertEqual(config.get("embedded_state_format"), 4)
            config["embedded_state_format"] = 2
            binding.config_path.write_text(json.dumps(config), encoding="utf-8")

            # The normal Runtime/task entry point must continue to reject the
            # stale scientific-plan format rather than silently migrate it.
            with self.assertRaisesRegex(ValueError, "current version 4"):
                TaskSpec.from_task_root(binding.research_root)

            sentinel = object()
            routes = {"incident_supervisor": object()}
            with patch("theory_master.backends.resolve_routes", return_value=routes) as resolve, patch(
                "theory_master.backends.CodexBackend", return_value=sentinel
            ) as backend_factory:
                result = _incident_supervisor_backend(binding.research_root)

            self.assertIs(result, sentinel)
            resolve.assert_called_once()
            self.assertEqual(
                resolve.call_args.kwargs["task_route_override_root"],
                binding.research_root.resolve(),
            )
            backend_factory.assert_called_once_with(
                binding.research_root.resolve(),
                routes=routes,
                task_config_path=binding.config_path.resolve(),
            )
            unchanged = json.loads(binding.config_path.read_text(encoding="utf-8"))
            self.assertEqual(unchanged["embedded_state_format"], 2)

    def test_manual_message_survives_reply_start_failure_and_shows_actual_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle = root / "1"
            cycle.mkdir()
            opened = publish_review_incident(
                research_root=root,
                cycle_dir=cycle,
                state={
                    "task_id": "task-test",
                    "cycle_id": "1",
                    "status": "failed",
                    "phase": "failed",
                    "protocol_health": "violation",
                    "router_ready": False,
                    "resumable": False,
                    "recovery": {"kind": "ValueError", "message": "test failure"},
                },
                trigger_type="runtime_failure",
                trigger_event="test failure",
                analysis=None,
                model_invoked=False,
                system_note="manual investigation requested",
                tool_reads=[],
            )
            with patch("theory_master.supervisor_incident_session._schedule_follow_up") as scheduled:
                accepted = post_human_message(root, opened["id"], "请解释这个异常", backend=None)

            self.assertTrue(accepted["ok"])
            self.assertTrue(accepted["pending"])
            self.assertTrue(any(row["role"] == "human" and row["content"] == "请解释这个异常"
                                for row in accepted["messages"]))
            scheduled.assert_called_once()

            reason = "research_plan format must be current version 4; backward compatibility is disabled"
            with patch(
                "theory_master.supervisor_incident_session._incident_supervisor_backend",
                side_effect=ValueError(reason),
            ):
                failed = _run_model_follow_up(root, opened["id"], backend=None)

            self.assertFalse(failed["ok"])
            self.assertEqual(failed["messages"][-1]["role"], "system")
            self.assertIn("could not start a reply to your message", failed["messages"][-1]["content"])
            self.assertIn(reason, failed["messages"][-1]["content"])
            self.assertIn("automatic monitoring is controlled separately", failed["messages"][-1]["content"])
            self.assertTrue(any(row["role"] == "human" and row["content"] == "请解释这个异常"
                                for row in failed["messages"]))


if __name__ == "__main__":
    unittest.main()
