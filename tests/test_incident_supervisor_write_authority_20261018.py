"""Offline regression for operator-facing Supervisor write authority and notifications."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from theory_master.backend_config import build_model_profile, resolve_routes
from theory_master.codex_backend import CodexBackend
from theory_master.role_registry import WRITE_ROLES
from theory_master.supervisor_incident_session import (
    _incident_supervisor_backend, open_notification_incident, post_human_message,
)


class _Backend:
    def invoke(self, role, prompt, thread_id, model, provider):
        self.role, self.prompt = role, prompt
        return "fake-thread", "已分析；未调用实际模型。"

    def drop_thread(self, thread_id):
        pass


class IncidentSupervisorAuthorityTests(unittest.TestCase):
    def test_tier_two_is_not_legacy_sol61(self):
        routes = build_model_profile("2")
        for name in ("research_lead", "challenger", "investigator", "judge"):
            self.assertEqual(routes[name].model, "gpt-6-sol")
        for name in ("literature", "reporter"):
            self.assertEqual(routes[name].model, "gpt-6-luna")
        self.assertEqual({r: x.model for r, x in resolve_routes(profile="2", env={}).items()},
                         {r: x.model for r, x in routes.items()})

    def test_incident_supervisor_is_writable_and_auto_reviewers_are_not(self):
        self.assertIn("incident_supervisor", WRITE_ROLES)
        self.assertNotIn("runtime_supervisor", WRITE_ROLES)
        self.assertNotIn("attention_reviewer", WRITE_ROLES)
        fake = SimpleNamespace(Sandbox=SimpleNamespace(workspace_write="write", read_only="read"))
        with patch.dict("sys.modules", {"openai_codex": fake}):
            self.assertEqual(CodexBackend._thread_sandbox("incident_supervisor"), "write")
            self.assertEqual(CodexBackend._thread_sandbox("runtime_supervisor"), "read")

    def test_incident_backend_targets_engine_not_science(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_backend = MagicMock()
            with patch("theory_master.backends.CodexBackend", return_value=mock_backend), patch(
                "theory_master.backends.resolve_routes", return_value={}
            ):
                backend = _incident_supervisor_backend(root)
            self.assertIs(backend, mock_backend)
            self.assertEqual(backend.cwd, Path(__file__).resolve().parents[1])
            self.assertNotEqual(backend.cwd, root)

    def test_supervisor_reports_change_and_notification_result_in_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "107"
            cycle.mkdir()
            record = open_notification_incident(
                research_root=root, cycle_dir=cycle, task_id="1",
                notification_id="SUP-0123456789abcdef", code="http_400",
                summary="Unsupported model route"
            )
            snapshots = [
                {"theory_master/model_profiles/codex.toml": "old"},
                {"theory_master/model_profiles/codex.toml": "new"},
            ]
            with patch("theory_master.supervisor_incident_session._engineering_snapshot",
                       side_effect=snapshots), patch(
                "theory_master.supervisor_incident_session._operator_repair_mail"
            ) as send:
                result = post_human_message(root, record["id"], "修复路由", backend=_Backend())
            send.assert_not_called()  # Fake backend does not send external mail during offline tests.
            reports = [m for m in result["messages"]
                       if m["metadata"].get("source") == "repair_report"]
            self.assertEqual(len(reports), 1)
            self.assertIn("theory_master/model_profiles/codex.toml", reports[0]["content"])
            self.assertIn("测试/注入后端", reports[0]["content"])
            self.assertEqual(result["messages"][-1]["role"], "supervisor")

    def test_no_change_produces_no_repair_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "107"
            cycle.mkdir()
            record = open_notification_incident(
                research_root=root, cycle_dir=cycle, task_id="1",
                notification_id="SUP-0123456789abcdef", code="http_400",
                summary="Unsupported model route"
            )
            with patch("theory_master.supervisor_incident_session._engineering_snapshot",
                       return_value={"theory_master/a.py": "same"}), patch(
                "theory_master.supervisor_incident_session._operator_repair_mail"
            ) as send:
                result = post_human_message(root, record["id"], "仅检查", backend=_Backend())
            send.assert_not_called()
            self.assertFalse(any(m["metadata"].get("source") == "repair_report"
                                 for m in result["messages"]))


if __name__ == "__main__":
    unittest.main()
