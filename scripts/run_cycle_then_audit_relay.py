#!/usr/bin/env python3
"""Run one TheoryMaster invocation, then audit only a clearly closed Cycle.

Runtime failure and post-Cycle audit are independent mechanisms. A failed,
interrupted, paused or needs-attention Cycle remains a recovery target and must
not spawn the audit relay. Only ``status=over`` after the normal report/
projection boundary may start the fresh Codex audit window. Historical
``status=completed`` Cycle state is accepted only as a legacy alias.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPO_ROOT / "tool/theory_master"
RELAY_STARTER = REPO_ROOT / "tool/python_script/start_origin_tmux_relay.py"
AUDIT_SKILL = REPO_ROOT / "tool/.codex/skills/tm-audit-review/SKILL.md"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from theory_master.backends import CodexBackend, resolve_routes  # noqa: E402
from theory_master.core import latest_cycle_dir  # noqa: E402
from theory_master.observability.collector import Collector  # noqa: E402
from theory_master.specification import TaskSpec  # noqa: E402
from theory_master.workflow import Workflow  # noqa: E402


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_relay_command(
    *, cycle_dir: Path, report_file: Path, tmux_session: str, window_name: str, model: str | None,
) -> list[str]:
    prompt = (
        f"读取 {AUDIT_SKILL}，按该 Skill 审计 TheoryMaster Cycle 的具体对话："
        f"{cycle_dir.resolve()}。将审计结果写入 {report_file.resolve()}。"
    )
    command = [
        sys.executable,
        str(RELAY_STARTER),
        "--prompt", prompt,
        "--origin", "codex",
        "--tmux-session", tmux_session,
        "--window-name", window_name,
        "--workdir", str(REPO_ROOT),
        "--verify-start",
        "--no-focus",
    ]
    if model:
        command.extend(["--model", model])
    return command


def validate_inputs(task_root: Path, tmux_session: str) -> TaskSpec:
    task_root = task_root.resolve()
    if not AUDIT_SKILL.is_file():
        raise FileNotFoundError(f"trajectory-audit skill not found: {AUDIT_SKILL}")
    if not RELAY_STARTER.is_file():
        raise FileNotFoundError(f"tmux relay starter not found: {RELAY_STARTER}")
    probe = subprocess.run(
        ["tmux", "has-session", "-t", tmux_session], text=True,
        capture_output=True, check=False,
    )
    if probe.returncode != 0:
        raise RuntimeError(probe.stderr.strip() or f"tmux session does not exist: {tmux_session}")
    return TaskSpec.from_task_root(task_root)


def run_cycle(spec: TaskSpec) -> dict[str, Any]:
    env_file = spec.research_root / ".theory-master/env"
    routes = resolve_routes(task_env_file=env_file if env_file.is_file() else None)
    collector = Collector(spec, system_failure_actions=True, email_notifications=True)
    backend = CodexBackend(spec.research_root, routes=routes)
    backend.collector = collector
    workflow = Workflow(spec, backend, routes, collector=collector)
    return workflow.run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--tmux-session", default=os.environ.get("THEORY_MASTER_TMUX_SESSION", "agent"))
    parser.add_argument("--window-name", default="TheoryMaster审计")
    parser.add_argument("--audit-model", default=None)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)

    spec = validate_inputs(args.task_root, args.tmux_session)
    if args.check_only:
        print(json.dumps({
            "task_root": str(spec.research_root),
            "audit_skill": str(AUDIT_SKILL),
        }, ensure_ascii=False, indent=2))
        return 0

    state_path = spec.research_root / ".theory-master/cycle_audit_relay_state.json"
    before = latest_cycle_dir(spec.research_root, task_mode=spec.task_mode)
    record: dict[str, Any] = {
        "schema": "theory-master/cycle-audit-relay/v1",
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cycle_before": before.name if before else None,
        "task_root": str(spec.research_root),
        "audit_skill": str(AUDIT_SKILL),
        "tmux_session": args.tmux_session,
        "window_name": args.window_name,
        "cycle_status": "running",
        "relay_status": "pending",
    }
    _write_json(state_path, record)

    cycle_error: BaseException | None = None
    final_state: dict[str, Any] | None = None
    try:
        final_state = run_cycle(spec)
        record["cycle_status"] = str(final_state.get("status") or "")
        record["cycle_phase"] = final_state.get("phase")
    except BaseException as exc:
        cycle_error = exc
        record["cycle_status"] = "failed"
        record["cycle_error"] = f"{type(exc).__name__}: {exc}"
        record["cycle_traceback"] = traceback.format_exc()

    after = latest_cycle_dir(spec.research_root, task_mode=spec.task_mode)
    record["cycle_after"] = after.name if after else None
    record["cycle_dir"] = str(after.resolve()) if after else None
    record["cycle_finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    audit_eligible = bool(final_state) and after is not None and str(final_state.get("status") or "").lower() == "over"
    record["audit_eligible"] = audit_eligible
    if audit_eligible:
        report = after / "audit" / "cycle_audit.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        relay_command = build_relay_command(
            cycle_dir=after,
            report_file=report,
            tmux_session=args.tmux_session,
            window_name=args.window_name,
            model=args.audit_model,
        )
        relay = subprocess.run(
            relay_command, cwd=REPO_ROOT, text=True,
            capture_output=True, check=False,
        )
        record["relay_returncode"] = relay.returncode
        record["relay_stdout"] = relay.stdout.strip()
        record["relay_stderr"] = relay.stderr.strip()
        record["relay_status"] = "started" if relay.returncode == 0 else "failed"
        record["relay_finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        if relay.returncode != 0:
            print(relay.stderr or relay.stdout or "audit relay failed", file=sys.stderr)
    else:
        record["relay_status"] = "deferred_nonterminal_cycle"
        record["audit_deferred_reason"] = "Cycle is not over; recovery owns runtime failure handling"
    _write_json(state_path, record)

    print(json.dumps(record, ensure_ascii=False, indent=2))
    if cycle_error is not None:
        return 1
    if not audit_eligible:
        return 1
    return 0 if record["relay_status"] == "started" else 2


if __name__ == "__main__":
    raise SystemExit(main())
