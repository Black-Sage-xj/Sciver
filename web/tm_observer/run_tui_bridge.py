"""Read-only boundary to the canonical CLI tm tui renderers.

The Web package intentionally does not import the scientific SDK runtime.
Task root is taken exclusively from Observer-discovered task metadata.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .run_monitor import _safe_cycle, redact


SECONDARY = frozenset({"logs", "thread", "events", "artifacts", "research"})


def read_tui(run: dict[str, Any], *, secondary: str = "") -> dict[str, Any]:
    cycle = _safe_cycle(run)
    if cycle is None:
        return {"parts": {}, "unavailable": "该任务尚无可读取的 Cycle 数据"}
    if secondary not in SECONDARY:
        secondary = ""
    root = Path(str(run["research_root"]))
    source = Path(__file__).resolve().parents[2]  # integrated TheoryMaster checkout
    env = dict(os.environ)
    env["PYTHONPATH"] = str(source) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        done = subprocess.run(
            [sys.executable, "-m", "theory_master.tui.web_bridge"],
            input=json.dumps({"research_root": str(root), "cycle_dir": str(cycle), "secondary": secondary,
                              "worker_id": run.get("worker_id")}),
            capture_output=True, text=True, timeout=9, check=False, shell=False, env=env,
        )
        if done.returncode != 0 or len(done.stdout) > (850_000 if secondary == "events" else 240_000):
            raise ValueError("TUI worker unavailable")
        projected = json.loads(done.stdout)
        if not isinstance(projected, dict) or not isinstance(projected.get("parts"), dict):
            raise ValueError("TUI projection missing")
        projected["parts"] = {key: redact(str(value)[:30_000]) for key, value in projected["parts"].items()}
        if isinstance(projected.get("events"), list):
            for event in projected["events"]:
                if isinstance(event, dict):
                    event["content"] = redact(str(event.get("content") or ""))
        if isinstance(projected.get("activity_detail"), dict):
            projected["activity_detail"] = {
                key: redact(str(value)[:12000])
                for key, value in projected["activity_detail"].items()
                if key in {"current_activity", "last_event",
                           "latest_dialogue_at", "latest_dialogue_kind",
                           "latest_dialogue_role", "latest_dialogue_hint"}
            }
        if isinstance(projected.get("attention"), dict):
            projected["attention"] = {
                key: redact(str(value)) for key, value in projected["attention"].items()
            }
        return projected
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return {"parts": {}, "unavailable": "TUI 状态同步暂时不可用；请稍后刷新"}
