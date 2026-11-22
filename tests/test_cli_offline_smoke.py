"""Exercise the public offline CLI entry point without provider credentials."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class OfflineSmokeTests(unittest.TestCase):
    def test_public_smoke_completes_with_fake_backend_and_no_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            isolated = Path(directory)
            env = os.environ.copy()
            env.update({
                "HOME": str(isolated / "home"),
                "TMPDIR": str(isolated),
                "THEORY_MASTER_SEARCH_ROOT": str(isolated / "workspace"),
                "THEORY_MASTER_DATA_ROOT": str(isolated / "data"),
            })
            for key in ("CODEX_HOME", "THEORYMASTER_CODEX_HOME", "OPENAI_API_KEY"):
                env.pop(key, None)
            result = subprocess.run(
                [sys.executable, "-m", "theory_master", "smoke"],
                env=env, capture_output=True, text=True, timeout=90, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            artifact_root = Path(result.stdout.strip().splitlines()[-1])
            self.assertTrue(artifact_root.is_relative_to(isolated))
            plan = (artifact_root / "research_plan.md").read_text()
            self.assertIn("# Final Gate\n", plan)
            config = json.loads((artifact_root / "theory-master.config.json").read_text())
            states = sorted(
                (json.loads(path.read_text()) for path in Path(config["research_root"]).glob("*/research_state.json")),
                key=lambda state: state["cycle_number"],
            )
            self.assertEqual(states[0]["closure_reason"], "judge_followup_required")
            self.assertIsNone(states[0]["scientific_verdict"])
            self.assertEqual(states[1]["incoming_judge_followup"]["hypothesis_id"], states[0]["hypothesis_lock"]["id"])
            self.assertEqual(states[-1]["branches"][-1]["status"], "supported")
            self.assertFalse((isolated / "home" / ".codex" / "auth.json").exists())
