"""Offline regression checks for the integrated Web Observatory checkout."""
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tm_observer.stage_flow import _core_source_dir
from tm_observer.archify_views import (
    CURRENT_AGENT_STAGES,
    HISTORICAL_COMPAT_STAGES,
    _stage_pairs_from_source,
    _validate_workflow_stage_catalog,
)
from tm_observer.change_requests import default_request_dir
from tm_observer import run_control, run_tui_bridge


class IntegratedRepoLayoutTests(TestCase):
    def test_web_reads_core_from_standalone_repo_and_example_project_parent(self):
        core_root = Path(__file__).resolve().parents[2]
        self.assertEqual(_core_source_dir(core_root), core_root / "theory_master")
        self.assertTrue(_stage_pairs_from_source(core_root))
        original_parent = core_root.parents[1]
        if (original_parent / "tool" / "theory_master").is_dir():
            self.assertEqual(_core_source_dir(original_parent), core_root / "theory_master")
            self.assertTrue(_stage_pairs_from_source(original_parent))
        self.assertEqual(default_request_dir(), core_root / "change_requests")

    def test_archify_workflow_catalog_matches_runtime_and_fails_closed_on_drift(self):
        core_root = Path(__file__).resolve().parents[2]
        source = _stage_pairs_from_source(core_root)
        self.assertIsNotNone(source)
        expected = {
            (role, stage)
            for role, stages in CURRENT_AGENT_STAGES.items()
            for stage in stages
        } | HISTORICAL_COMPAT_STAGES
        self.assertEqual(source, expected)
        self.assertIn(("challenger", "repair_verification"), source)
        self.assertNotIn(("reporter", "canvas_maintenance"), expected)
        _validate_workflow_stage_catalog(core_root)

        # Preserve the fail-closed check: both an unmapped current stage and
        # a stale Observer-only stage must remain visible as distinct errors.
        with patch.dict(
            CURRENT_AGENT_STAGES,
            {
                "challenger": ("candidate_review",),
                "reporter": ("report", "canvas_maintenance"),
            },
        ):
            with self.assertRaises(RuntimeError) as raised:
                _validate_workflow_stage_catalog(core_root)
        message = str(raised.exception)
        self.assertIn("unmapped_source_stages=[('challenger', 'repair_verification')]", message)
        self.assertIn("stale_observer_stages=[('reporter', 'canvas_maintenance')]", message)

    def test_web_worker_paths_resolve_integrated_core(self):
        core_root = Path(__file__).resolve().parents[2]
        for module in (run_control, run_tui_bridge):
            self.assertEqual(Path(module.__file__).resolve().parents[2], core_root)

    def test_private_host_is_not_shipped_as_server_default(self):
        from tm_observer import al_public
        self.assertNotIn("39.105", al_public.__file__)
        self.assertTrue(isinstance(al_public.AL_PUBLIC_HOSTNAME, str))
