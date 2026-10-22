"""Read-only warnings for task/global/effective model route disagreement."""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from theory_master.backend_config import build_model_profile
from theory_master.route_preflight import model_route_preflight_lines


class ModelRoutePreflightTests(unittest.TestCase):
    def _lines(self, effective, task_env=None, process_env=None):
        with patch("theory_master.route_preflight.read_model_profile", return_value="codex"), \
             patch("theory_master.route_preflight.read_model_provider", return_value="codex"), \
             patch.dict("os.environ", process_env or {}, clear=True):
            return "\n".join(model_route_preflight_lines(
                effective, task_env_file=task_env,
            ))

    def test_no_override_has_effective_report_without_warning(self):
        routes = build_model_profile("codex", "codex")
        result = self._lines(routes)
        self.assertIn("MODEL PREFLIGHT global_tier=codex", result)
        self.assertIn("MODEL EFFECTIVE ROUTES", result)
        self.assertNotIn("OVERRIDE WARNING", result)

    def test_task_profile_override_is_visible_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "env"
            env_file.write_text("THEORYMASTER_PROFILE=1\nAPI_TOKEN=DO_NOT_PRINT_THIS\n")
            result = self._lines(build_model_profile("test", "codex"), env_file)
            self.assertIn("task_env_tier=test", result)
            self.assertIn("MODEL ROUTE OVERRIDE WARNING", result)
            self.assertIn("research_lead:global=", result)
            self.assertNotIn("DO_NOT_PRINT_THIS", result)

    def test_numeric_alias_of_global_profile_is_not_a_false_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "env"
            env_file.write_text("THEORYMASTER_PROFILE=2\n")
            result = self._lines(build_model_profile("codex", "codex"), env_file)
            self.assertNotIn("OVERRIDE WARNING", result)

    def test_task_profile_masked_by_higher_priority_still_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "env"
            env_file.write_text("THEORYMASTER_PROFILE=1\n")
            result = self._lines(build_model_profile("codex", "codex"), env_file)
            self.assertIn("masked by another", result)

    def test_process_profile_and_individual_role_override_are_visible(self):
        routes = build_model_profile("codex", "codex")
        tweaked = dict(routes)
        tweaked["judge"] = replace(build_model_profile("test", "codex")["judge"])
        result = self._lines(tweaked, process_env={"THEORYMASTER_PROFILE": "1"})
        self.assertIn("process_env_tier=test", result)
        self.assertIn("judge:global=", result)
        self.assertNotIn("research_lead:global=", result)

    def test_diagnostic_never_mutates_resolved_routes(self):
        routes = build_model_profile("codex", "codex")
        before = dict(routes)
        self._lines(routes)
        self.assertEqual(routes, before)


if __name__ == "__main__":
    unittest.main()
