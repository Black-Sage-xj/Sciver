#!/usr/bin/env python3
"""Task-local, resumable five-Cycle engineering audit/repair supervisor.

The supervisor owns cadence and registration; Workflow owns scientific decisions.
No real work starts on import. Run with --task-root to start the loop.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPO_ROOT / "tool/theory_master"
sys.path.insert(0, str(PACKAGE_ROOT))
from theory_master.backends import CodexBackend, resolve_routes  # noqa: E402
from theory_master.observability.collector import Collector  # noqa: E402
from theory_master.specification import TaskSpec, list_cycle_dirs  # noqa: E402
from theory_master.workflow import Workflow  # noqa: E402

AUDIT_SKILL = REPO_ROOT / "tool/.codex/skills/tm-audit-review/SKILL.md"
COUNT = 5
SCHEMA = "theory-master/audit-batch/v1"


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def has_report(path: Path) -> bool:
    return path.is_file() and bool(path.read_text(encoding="utf-8").strip())


def cycle_status(path: Path) -> str:
    try:
        return str(read_json(path / "research_state.json").get("status") or "").lower()
    except (OSError, ValueError):
        return "unknown"


def select_new_cycles(task_root: Path, baseline: list[str], *, task_mode: bool = True) -> list[Path]:
    """Return IDs absent at batch start, oldest first (list_cycle_dirs is newest first)."""
    before = set(baseline)
    return [p for p in reversed(list_cycle_dirs(task_root, task_mode=task_mode)) if p.name not in before]


def cycle_is_over(path: Path) -> bool:
    # `completed` is accepted only for historical Cycle artifacts.
    return cycle_status(path) in {"over", "completed"}


def completed_batch(cycles: list[Path]) -> bool:
    return len(cycles) == COUNT and all(cycle_is_over(p) for p in cycles)


def control_disposition(batch_root: Path) -> str:
    if (batch_root / "STOP").exists():
        return "stop"
    if (batch_root / "PAUSE").exists():
        return "pause"
    return "continue"


def audit_prompt(spec: TaskSpec, cycles: list[Path], report: Path) -> str:
    cycle_lines = "\n".join(f"- {p.name}: {p}" for p in cycles)
    return (
        f"读取 {AUDIT_SKILL}，按该 Skill 审计下面这些 TheoryMaster Cycle 的具体对话：\n"
        f"{cycle_lines}\n将审计结果写入 {report}。"
    )


def repair_prompt(spec: TaskSpec, audit: Path, report: Path, *, recovery: bool = False) -> str:
    source = (f"interrupted Cycle {audit.parent}; its research state {audit}; and interruption logs under {spec.research_root}"
              if recovery else f"audit report {audit}")
    recovery_contract = ("On its own line, write exactly 'engineering-recoverable: yes' or "
                         "'engineering-recoverable: no'.\n" if recovery else "")
    return f"""TheoryMaster fresh engineering repair agent. Read {source}, current code/docs and relevant tests.
Authority: fix ONLY actionable SDK, process, contract, TUI, Canvas projection, and observability defects. Preserve unrelated dirty changes. Do not commit or push. Do not edit scientific verdicts, Evidence, Scientific Model conclusions, Cycle scientific artifacts, research_plan scientific content, or fabricate missing scientific evidence. A scientific insufficiency is 'scientific follow-up; no direct repair' and must remain for normal future Cycles/human attention. Do not resume TheoryMaster or send email.
Run focused tests and the full TheoryMaster unit test suite if code changes. Write {report} with one row per issue: issue ID -> disposition -> files changed -> tests/evidence -> unresolved items. {recovery_contract}Explicitly state whether the interruption is engineering-recoverable without scientific mutation. Keep changes limited to supported fixes.
"""


def invoke_codex(executable: Path, *, prompt: str, mode: str, batch_dir: Path, name: str,
                 report: Path | None = None) -> tuple[int, str]:
    """A separate exec call starts a fresh Codex session; all streams stay local."""
    cmd = [str(executable), "exec", "--sandbox", mode, "-C", str(REPO_ROOT), "--skip-git-repo-check"]
    if report is not None and mode == "read-only":
        cmd += ["--output-last-message", str(report)]
    cmd += [prompt]
    with (batch_dir / f"{name}.stdout.log").open("w", encoding="utf-8") as out, \
         (batch_dir / f"{name}.stderr.log").open("w", encoding="utf-8") as err:
        result = subprocess.run(cmd, cwd=REPO_ROOT, stdout=out, stderr=err, check=False)
    body = report.read_text(encoding="utf-8") if report and report.is_file() else ""
    return result.returncode, body


def run_workflow(spec: TaskSpec, *, resume_id: str | None, remaining: int) -> dict[str, Any]:
    """Keep runtime observability while disabling legacy automatic actions."""
    spec = replace(spec, max_cycles=remaining)
    env_file = spec.research_root / ".theory-master/env"
    routes = resolve_routes(task_env_file=env_file if env_file.is_file() else None)
    collector = Collector(spec, cycle_audit_actions=False, system_failure_actions=False,
                          email_notifications=False)
    backend = CodexBackend(spec.research_root, routes=routes)
    workflow = Workflow(spec, backend, routes, collector=collector)
    return workflow.resume(resume_id) if resume_id else workflow.run()


def register_batch(root: Path, manifest: dict[str, Any]) -> bool:
    """Idempotent registration; manifest is durable before the single history row."""
    batch_id = manifest["batch_id"]
    history = root / "history.jsonl"
    if history.exists():
        for line in history.read_text(encoding="utf-8").splitlines():
            if json.loads(line).get("batch_id") == batch_id:
                return False
    write_json(root / batch_id / "manifest.json", manifest)
    with history.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"batch_id": batch_id, "cycle_ids": manifest["cycle_ids"],
                                 "registered_at": manifest["registered_at"],
                                 "audit_status": manifest["audit_status"],
                                 "repair_status": manifest["repair_status"],
                                 "next_loop_disposition": manifest["next_loop_disposition"]}, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return True


class Supervisor:
    def __init__(self, task_root: Path, codex: Path):
        self.spec = TaskSpec.from_task_root(task_root.resolve())
        if self.spec.max_cycles != COUNT:
            raise ValueError(f"research_plan.md max_cycles must be exactly {COUNT}")
        self.root = self.spec.research_root / ".theory-master/audit_batches"
        self.codex = codex.expanduser().resolve()
        self.stop_signal = False
        self.state_path = self.root / "state.json"

    def check(self) -> dict[str, str]:
        """Read-only preflight for configuration and supervisor paths."""
        if not self.codex.is_file() or not os.access(self.codex, os.X_OK):
            raise ValueError(f"Codex executable is missing or not executable: {self.codex}")
        if not AUDIT_SKILL.is_file():
            raise FileNotFoundError(f"trajectory-audit skill missing: {AUDIT_SKILL}")
        if self.root.exists() and not self.root.is_dir():
            raise ValueError(f"batch root is not a directory: {self.root}")
        for path in (self.root / "supervisor.lock", self.root / "PAUSE", self.root / "STOP", self.state_path):
            if path.exists() and not path.is_file():
                raise ValueError(f"control/state path is not a file: {path}")
        if self.state_path.exists():
            read_json(self.state_path)
        return {"task_root": str(self.spec.research_root), "codex": str(self.codex),
                "state": str(self.state_path), "lock": str(self.root / "supervisor.lock"),
                "pause": str(self.root / "PAUSE"), "stop": str(self.root / "STOP"),
                "reports": str(self.root / "<batch-id>"), "history": str(self.root / "history.jsonl")}

    def _signal(self, _signum: int, _frame: Any) -> None:
        self.stop_signal = True

    def _save(self, state: dict[str, Any]) -> None:
        write_json(self.state_path, state)

    def _pending(self, state: dict[str, Any]) -> dict[str, Any]:
        pending = state.get("pending")
        if pending:
            return pending
        dirs = list_cycle_dirs(self.spec.research_root, task_mode=self.spec.task_mode)
        # Never skip a pre-existing unfinished Cycle: it must be resumed or escalated.
        if dirs and not cycle_is_over(dirs[0]):
            raise RuntimeError(f"existing unfinished Cycle needs recovery: {dirs[0]}")
        pending = {"batch_id": "batch-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}",
                   "started_at": stamp(), "baseline": [p.name for p in dirs], "interruptions": [], "recovery_attempts": 0}
        state["pending"] = pending
        self._save(state)
        return pending

    def _recover(self, pending: dict[str, Any], cycle: Path, batch_dir: Path) -> bool:
        pending["interruptions"].append({"cycle_id": cycle.name, "status": cycle_status(cycle), "at": stamp()})
        if pending["recovery_attempts"] >= 1:
            return False
        pending["recovery_attempts"] += 1
        report = batch_dir / "recovery_repair_result.md"
        rc, _ = invoke_codex(self.codex, prompt=repair_prompt(self.spec, cycle / "research_state.json", report, recovery=True),
                             mode="workspace-write", batch_dir=batch_dir, name="recovery_repair")
        pending["recovery_report"] = str(report)
        return (rc == 0 and report.is_file() and
                re.search(r"^engineering-recoverable: yes$", report.read_text(encoding="utf-8"), re.I | re.M) is not None)

    def _fill(self, state: dict[str, Any], pending: dict[str, Any], batch_dir: Path) -> bool:
        baseline = pending["baseline"]
        while True:
            cycles = select_new_cycles(self.spec.research_root, baseline, task_mode=self.spec.task_mode)
            if len(cycles) > COUNT:
                raise RuntimeError("more than five new Cycle IDs; refusing ambiguous batch")
            if completed_batch(cycles):
                pending["cycle_ids"] = [p.name for p in cycles]
                self._save(state)
                return True
            if any(cycle_status(p) not in {"over", "completed", "active", "running", "failed", "interrupted", "paused", "needs_attention"} for p in cycles):
                raise RuntimeError("unknown Cycle status; inspect before continuing")
            unfinished = [p for p in cycles if not cycle_is_over(p)]
            if len(unfinished) > 1 or (unfinished and unfinished[-1] != cycles[-1]):
                raise RuntimeError("nonterminal Cycle precedes a newer Cycle; refusing to discard it")
            if self.stop_signal or control_disposition(self.root) != "continue":
                self._save(state)
                return False
            if unfinished:
                current = unfinished[0]
                if cycle_status(current) == "needs_attention" or cycle_status(current) == "failed":
                    if not self._recover(pending, current, batch_dir):
                        state["disposition"] = "human_or_scientific_attention"
                        self._save(state)
                        return False
                resume_id = current.name
            else:
                resume_id = None
            remaining = COUNT - sum(cycle_is_over(p) for p in cycles)
            pending["last_invocation"] = {"started_at": stamp(), "resume_id": resume_id, "remaining": remaining}
            self._save(state)
            try:
                result = run_workflow(self.spec, resume_id=resume_id, remaining=remaining)
                pending["last_invocation"]["result_status"] = result.get("status")
            except Exception as exc:
                pending["last_invocation"]["error"] = f"{type(exc).__name__}: {exc}"
                (batch_dir / "workflow_exception.log").write_text(traceback.format_exc(), encoding="utf-8")
            pending["last_invocation"]["finished_at"] = stamp()
            self._save(state)
            after = select_new_cycles(self.spec.research_root, baseline, task_mode=self.spec.task_mode)
            if not after or after == cycles and all(cycle_is_over(p) for p in after):
                state["disposition"] = "human_attention_no_progress"
                self._save(state)
                return False

    def _audit_repair(self, state: dict[str, Any], pending: dict[str, Any], batch_dir: Path) -> bool:
        cycles = [self.spec.research_root / name for name in pending["cycle_ids"]]
        audit = batch_dir / "batch_audit.md"
        repair = batch_dir / "repair_result.md"
        # A process may have written the report and crashed before saving state.
        if "audit_status" not in pending and has_report(audit):
            pending["audit_status"] = "completed"
            self._save(state)
        if pending.get("audit_status") != "completed":
            rc, body = invoke_codex(self.codex, prompt=audit_prompt(self.spec, cycles, audit), mode="read-only",
                                    batch_dir=batch_dir, name="audit", report=audit)
            pending["audit_status"] = "completed" if rc == 0 and body.strip() else "failed"
            self._save(state)
        if pending.get("audit_status") != "completed":
            state["disposition"] = "audit_failed"
            self._save(state)
            return False
        if "repair_status" not in pending and has_report(repair):
            pending["repair_status"] = "completed"
            self._save(state)
        if pending.get("repair_status") != "completed":
            rc, _ = invoke_codex(self.codex, prompt=repair_prompt(self.spec, audit, repair), mode="workspace-write",
                                 batch_dir=batch_dir, name="repair")
            pending["repair_status"] = "completed" if rc == 0 and has_report(repair) else "failed"
            self._save(state)
        if pending.get("repair_status") != "completed":
            state["disposition"] = "repair_failed"
            self._save(state)
            return False
        disposition = "stop" if self.stop_signal else control_disposition(self.root)
        manifest = {"schema": SCHEMA, "batch_id": pending["batch_id"], "cycle_ids": pending["cycle_ids"],
                    "started_at": pending["started_at"], "finished_at": stamp(),
                    "audit_report_path": str(audit), "audit_status": pending["audit_status"],
                    "repair_report_path": str(repair), "repair_status": pending["repair_status"],
                    "test_summary": "see repair_result.md", "interruptions": pending["interruptions"],
                    "next_loop_disposition": disposition, "registered_at": stamp()}
        register_batch(self.root, manifest)
        state["latest_registered_batch"] = pending["batch_id"]
        state["pending"] = None
        state["disposition"] = disposition
        self._save(state)
        return disposition == "continue"

    def run(self, *, once: bool = False) -> None:
        self.check()
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / "supervisor.lock"
        with lock.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"another supervisor holds {lock}") from exc
            handle.seek(0); handle.truncate()
            handle.write(json.dumps({"pid": os.getpid(), "started_at": stamp(), "task_root": str(self.spec.research_root)}) + "\n")
            handle.flush()
            signal.signal(signal.SIGINT, self._signal)
            signal.signal(signal.SIGTERM, self._signal)
            state = read_json(self.state_path) or {"schema": SCHEMA, "pending": None}
            while True:
                if not state.get("pending") and (self.stop_signal or control_disposition(self.root) != "continue"):
                    state["disposition"] = "stop" if self.stop_signal else control_disposition(self.root)
                    self._save(state)
                    break
                pending = self._pending(state)
                batch_dir = self.root / pending["batch_id"]
                batch_dir.mkdir(exist_ok=True)
                if not pending.get("cycle_ids") and not self._fill(state, pending, batch_dir):
                    break
                if not self._audit_repair(state, pending, batch_dir) or once:
                    break


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--codex", type=Path, default=Path.home() / "bin" / "codex")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    supervisor = Supervisor(args.task_root, args.codex)
    if args.check_only:
        print(json.dumps(supervisor.check(), ensure_ascii=False, indent=2))
    else:
        supervisor.run(once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
