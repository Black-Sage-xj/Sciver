"""Commit a Cycle directory's Git-eligible text artifacts when the Cycle ends.

Which files are eligible is decided entirely by the repository ``.gitignore``
(whitelist of ``.py``/``.md``; data and ``replay_checkpoints`` excluded).  The
commit is path-limited to the Cycle directory, so paths other Agents have
staged stay staged and uncommitted.  Failures are reported, never raised: a Git
problem must not change the scientific outcome of a Cycle.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

ENV_SWITCH = "THEORY_MASTER_CYCLE_GIT_COMMIT"
_TIMEOUT_S = 120
_SOURCE_TREE = "tool/theory_master/"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=_TIMEOUT_S,
    )


def commit_cycle_dir(cycle_dir: Path | None, *, status: str = "", label: str = "") -> dict[str, Any]:
    if os.environ.get(ENV_SWITCH, "1").strip().lower() in {"0", "false", "no", "off"}:
        return {"result": "disabled"}
    if cycle_dir is None or not Path(cycle_dir).is_dir():
        return {"result": "skipped", "reason": "no cycle directory"}
    cycle_dir = Path(cycle_dir).resolve()
    try:
        top = _git(cycle_dir, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            return {"result": "skipped", "reason": "not a git repository"}
        repo = Path(top.stdout.strip())
        rel = cycle_dir.relative_to(repo).as_posix()
        if rel.startswith(_SOURCE_TREE):
            return {"result": "skipped", "reason": "cycle directory inside TheoryMaster source tree"}
        added = _git(repo, "add", "--", rel)
        if added.returncode != 0:
            return {"result": "error", "step": "add", "stderr": added.stderr.strip()[-500:]}
        pending = _git(repo, "diff", "--cached", "--name-only", "--", rel)
        files = [line for line in pending.stdout.splitlines() if line]
        if not files:
            return {"result": "nothing_to_commit", "path": rel}
        message = f"chore(tm-cycle): {label or rel} [{status or 'finish'}] auto-commit {len(files)} file(s)"
        done = _git(repo, "commit", "--no-verify", "-q", "-m", message, "--", rel)
        if done.returncode != 0:
            return {"result": "error", "step": "commit", "stderr": done.stderr.strip()[-500:]}
        head = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
        return {"result": "committed", "commit": head, "files": len(files), "path": rel}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"result": "error", "step": "exception", "stderr": str(exc)[-500:]}
