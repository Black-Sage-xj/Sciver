from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/start_recovery_resume.py"


def load_script():
    spec = importlib.util.spec_from_file_location("theorymaster_start_recovery_resume", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RecoveryResumeTest(unittest.TestCase):
    def test_start_resume_uses_runtime_pointer_and_managed_resume(self):
        module = load_script()
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            plan = workspace / "research_plan.md"
            plan.write_text(
                "# Goal\n\ng\n\n# Research Contract\n\nc\n\n"
                "# Final Gate\n\nThe resumed task is recorded.\n\n"
                "# Required Context\n\n- r\n\n# Scientific Model\n\n尚未形成稳定的科学总结。\n",
                encoding="utf-8",
            )
            from theory_master.task import bootstrap
            binding = bootstrap(plan)
            result = SimpleNamespace(returncode=0, stdout="TheoryMaster runtime: started\n", stderr="")
            with patch.object(module.subprocess, "run", return_value=result) as run, \
                 patch("theory_master.runtime_manager.load_registry", return_value={"tui": {"window_id": "@77"}}):
                target = module.start_resume(identifier="cycle-0025", task_root=workspace, tmux_session="agent")
            self.assertEqual(target, "@77")
            self.assertEqual(run.call_args.args[0][-4:], ["-m", "theory_master", "resume", str(binding.master_id)])
            self.assertEqual(run.call_args.kwargs["cwd"], binding.task_root)


if __name__ == "__main__":
    unittest.main()
