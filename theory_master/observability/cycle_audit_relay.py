"""Spawn a fresh Codex window to audit one finished TheoryMaster Cycle."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from theory_master.audit_adapter import resolve_audit_adapter


def _window_name(cycle_id: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "-", cycle_id).strip("-") or "cycle"
    return f"TM审计-{suffix}"


def spawn_cycle_audit_agent(state: dict[str, Any]) -> str:
    """Start one fresh Codex audit thread for a terminal scientific Cycle."""
    cycle_dir = Path(str(state.get("run_dir") or state.get("cycle_dir") or ".")).resolve()
    if not cycle_dir.is_dir():
        raise FileNotFoundError(f"cycle directory not found: {cycle_dir}")
    adapter = resolve_audit_adapter()

    cycle_id = str(state.get("run_id") or state.get("cycle_id") or cycle_dir.name)
    report_dir = cycle_dir / "audit"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / "cycle_audit.md"
    session = os.environ.get("THEORY_MASTER_TMUX_SESSION", "agent")
    prompt = (
        f"读取 {adapter.skill}，按该 Skill 审计 TheoryMaster Cycle {cycle_id} 的具体对话："
        f"{cycle_dir}。将审计结果写入 {report}。"
    )
    from theory_master.operator_preferences import read as read_web_operator_prefs
    try:
        selected_model = read_web_operator_prefs(cycle_dir.parent)["models"].get("cycle_audit")
    except (OSError, ValueError):
        selected_model = None
    cmd = [
        sys.executable, str(adapter.relay),
        "--prompt", prompt,
        "--origin", "codex",
        "--tmux-target", f"{session}:0",
        "--window-name", _window_name(cycle_id),
        "--workdir", str(adapter.workdir),
        "--verify-start",
        "--no-focus",
    ]
    if selected_model:
        cmd.extend(["--model", selected_model])
    failures: list[str] = []
    for attempt in range(2):
        proc = subprocess.run(cmd, cwd=adapter.workdir, text=True, capture_output=True, check=False)
        if proc.returncode == 0:
            return proc.stdout.strip()
        failures.append(proc.stderr.strip() or proc.stdout.strip() or f"relay attempt {attempt + 1} failed")
        if attempt == 0:
            time.sleep(0.5)
    raise RuntimeError("; retry failed: ".join(failures))
