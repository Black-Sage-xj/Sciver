#!/usr/bin/env python3
"""Reconnect a recovered task through its pointer and managed runtime."""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPO_ROOT / "tool/theory_master"


def _runtime_python() -> str:
    """Return an interpreter capable of running the account-backed SDK.

    Recovery relays can be launched by a lightweight system Python which is
    intentionally sufficient for task-pointer handling but lacks either its
    SSL extension or the optional ``openai-codex`` dependency.  Do not make a
    recovered run inherit that broken launcher interpreter.
    """
    requested = os.environ.get("THEORYMASTER_RUNTIME_PYTHON", "").strip()
    candidates = [requested] if requested else []
    candidates.extend([sys.executable, str(Path.home() / "miniconda3/bin/python")])
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        if path.resolve() == Path(sys.executable).resolve():
            if importlib.util.find_spec("_ssl") and importlib.util.find_spec("openai_codex"):
                return str(path)
            continue
        # The managed Conda runtime is the repository-supported SDK runtime.
        # An explicit override is allowed for maintenance environments.
        return str(path)
    raise RuntimeError(
        "No TheoryMaster SDK runtime is available; set THEORYMASTER_RUNTIME_PYTHON "
        "to a Python with _ssl and openai_codex installed."
    )


def _runtime_env(tmux_session: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    parts = [str(PACKAGE_ROOT), str(REPO_ROOT), str(REPO_ROOT / "gs_study/_tool")]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = ":".join(parts)
    if tmux_session:
        env["THEORY_MASTER_TMUX_SESSION"] = tmux_session
    return env


def start_resume(*, identifier: str, task_root: Path, tmux_session: str,
                 authorize_prequalification_restart: bool = False) -> str:
    task_root = task_root.expanduser().resolve()
    sys.path.insert(0, str(PACKAGE_ROOT))
    from theory_master.task import TaskBinding
    binding = TaskBinding.load(task_root)
    if authorize_prequalification_restart:
        import json
        from theory_master.workflow_support import prequalification_restart_allowed
        from theory_master.runtime import write_json
        from theory_master.specification import latest_cycle_dir
        research_root = Path(binding.config["research_root"]).expanduser().resolve()
        cycle = latest_cycle_dir(research_root, task_mode=True)
        if cycle is None:
            raise ValueError("there is no existing Cycle to authorize for qualification restart")
        state_path = cycle / "research_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        candidate = {**state, "prequalification_restart_authorized": True}
        if not prequalification_restart_allowed(candidate):
            raise ValueError("prequalification restart requires an untouched, unlocked runtime failure")
        write_json(state_path, candidate)
    proc = subprocess.run(
        [_runtime_python(), "-m", "theory_master", "resume", str(binding.master_id)],
        cwd=binding.task_root, env=_runtime_env(tmux_session),
        text=True, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "failed to start managed TheoryMaster recovery")
    from theory_master.runtime_manager import load_registry
    registry = load_registry(binding.task_root)
    tui = registry.get("tui") if isinstance(registry.get("tui"), dict) else {}
    target = str(tui.get("window_id") or f"{tmux_session}:TheoryMaster-{binding.master_id}")
    print(target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identifier", default="0")
    parser.add_argument("--task-root", required=True, type=Path)
    parser.add_argument("--tmux-session", default=os.environ.get("THEORY_MASTER_TMUX_SESSION", "agent"))
    parser.add_argument(
        "--authorize-prequalification-restart", action="store_true",
        help="explicitly authorize qualification replay after pure runtime/code repair",
    )
    args = parser.parse_args(argv)
    start_resume(identifier=args.identifier, task_root=args.task_root, tmux_session=args.tmux_session,
                 authorize_prequalification_restart=args.authorize_prequalification_restart)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
