"""Spawn a fresh Codex window to audit one finished TheoryMaster Cycle."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
RELAY_STARTER = REPO_ROOT / "tool/python_script/start_origin_tmux_relay.py"
AUDIT_SKILL = REPO_ROOT / "tool/.codex/skills/tm-audit-review/SKILL.md"


def _window_name(cycle_id: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "-", cycle_id).strip("-") or "cycle"
    return f"TM审计-{suffix}"


def spawn_cycle_audit_agent(state: dict[str, Any]) -> str:
    """Start one fresh Codex audit thread for a terminal scientific Cycle."""
    cycle_dir = Path(str(state.get("run_dir") or state.get("cycle_dir") or ".")).resolve()
    if not cycle_dir.is_dir():
        raise FileNotFoundError(f"cycle directory not found: {cycle_dir}")
    if not AUDIT_SKILL.is_file():
        raise FileNotFoundError(f"TheoryMaster trajectory-audit skill not found: {AUDIT_SKILL}")

    cycle_id = str(state.get("run_id") or state.get("cycle_id") or cycle_dir.name)
    report_dir = cycle_dir / "audit"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / "cycle_audit.md"
    session = os.environ.get("THEORY_MASTER_TMUX_SESSION", "agent")
    prompt = (
        f"读取 {AUDIT_SKILL}，按该 Skill 审计 TheoryMaster Cycle {cycle_id} 的具体对话："
        f"{cycle_dir}。将审计结果写入 {report}。"
    )
    from theory_master.operator_preferences import read as read_web_operator_prefs
    try:
        selected_model = read_web_operator_prefs(cycle_dir.parent)["models"].get("cycle_audit")
    except (OSError, ValueError):
        selected_model = None
    cmd = [
        sys.executable, str(RELAY_STARTER),
        "--prompt", prompt,
        "--origin", "codex",
        "--tmux-target", f"{session}:0",
        "--window-name", _window_name(cycle_id),
        "--workdir", str(REPO_ROOT),
        "--verify-start",
        "--no-focus",
    ]
    if selected_model:
        cmd.extend(["--model", selected_model])
    failures: list[str] = []
    for attempt in range(2):
        proc = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
        if proc.returncode == 0:
            return proc.stdout.strip()
        failures.append(proc.stderr.strip() or proc.stdout.strip() or f"relay attempt {attempt + 1} failed")
        if attempt == 0:
            time.sleep(0.5)
    raise RuntimeError("; retry failed: ".join(failures))
