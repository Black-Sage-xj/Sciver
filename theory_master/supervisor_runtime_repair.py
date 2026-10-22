"""Bounded, isolated CODE repair for an already-stopped Runtime Supervisor Cycle.

This is intentionally NOT a general-purpose agent shell. An incident must point to
one allowlisted engine projection module; Codex may edit only an isolated copy of
that module, and only a verified single-file candidate may be installed. Science
files, task state, contracts, budgets and live Runner ownership are out of scope.
"""
from __future__ import annotations

import fcntl
import contextlib
import io
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

MAX_REPAIRS_PER_CYCLE = 1
MAX_REPAIR_SECONDS = 240
_REPAIR_COUNT_KEY = "supervisor_runtime_repair_count"
# Expand this allowlist only with a reviewed, independent change. In particular,
# workflow/contract/authority modules are NOT eligible for autonomous replacement.
_SAFE_MODULES = frozenset({"graph_authority.py", "canvas_projection.py", "research_focus.py"})
_TESTS = ("test_science_graph_store.py", "test_science_canvas_export.py", "test_external_canvas_nodes.py")

_FRAME = re.compile(r'File "([^"\n]+\.py)", line \d+')
_SECRET = re.compile(r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*\S+")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _result(reason: str, *, diagnosis: str = "") -> dict[str, str]:
    return {"action": "hold", "reason": reason, "diagnosis": diagnosis,
            "repair_suggestion": "Replan engineering repair using this rejected attempt: " + reason + "\n" + diagnosis,
            "repair_handoff": {"required": True, "next_action": "replan", "failure": reason, "evidence": diagnosis}}


def repair_target(log: str, state: dict[str, Any], engine: Path) -> Path | None:
    """Require a current matching traceback frame inside a narrow code allowlist."""
    from .supervisor_recovery import failure_details, operational_incident
    recovery = failure_details(state)
    message = str(recovery.get("message") or "").strip()
    if (not operational_incident(state)
            or not message or message[:85].lower() not in log.lower()
            or str(recovery.get("kind") or "") not in
            {"ValueError", "TypeError", "AttributeError", "KeyError", "NameError", "RuntimeError"}):
        return None
    # Bind frames to this error, never an older traceback in the shared log.
    error_at = log.lower().rfind(message[:85].lower())
    trace = log[max(0, error_at - 6500):error_at + len(message)]
    traceback_at = trace.rfind("Traceback (most recent call last)")
    if traceback_at < 0:
        return None
    frames: list[Path] = []
    for m in _FRAME.finditer(trace[traceback_at:]):
        try:
            target = Path(m.group(1)).resolve(strict=True)
            if target.parent == engine.resolve(strict=True) and target.name in _SAFE_MODULES:
                frames.append(target)
        except (OSError, RuntimeError):
            continue
    return frames[-1] if frames else None


def _log_for_repair(root: Path, *, log_path: Path | None = None) -> str:
    path = Path(log_path) if log_path is not None else root / ".theory-master" / "runner.log"
    if path.is_symlink():
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 32_000))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return _SECRET.sub(r"\1=[redacted]", text)[-20_000:]


def _snapshot(package: Path) -> dict[str, str]:
    """Record all copied source and test files to prevent unapproved edits."""
    paths = [path for path in package.rglob("*") if "__pycache__" not in path.parts]
    if any(path.is_symlink() for path in paths):
        raise ValueError("Symlink in isolated repair source: candidate rejected.")
    return {str(path.relative_to(package)): _hash(path.read_bytes())
            for path in paths if path.is_file()}


def _invoke_codex(sandbox: Path, model: str, prompt: str) -> tuple[bool, str]:
    binary = shutil.which("codex")
    if not binary:
        return False, "Codex executable unavailable."
    cmd = [
        binary, "exec", "--sandbox", "workspace-write", "--skip-git-repo-check",
        "--ephemeral", "--ignore-user-config", "--cd", str(sandbox),
        "--model", model, "-c", 'model_reasoning_effort="medium"', "-",
    ]
    # The sandbox contains only a copied engine package and offline tests:
    # no scientific inputs, credentials, task roots or original repository.
    with tempfile.TemporaryFile(mode="w+b") as output:
        child = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=output,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        try:
            child.communicate(input=prompt.encode(), timeout=MAX_REPAIR_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            return False, "Isolated Codex repair timed out."
        output.seek(0, 2)
        output.seek(max(0, output.tell() - 2500))
        tail = _SECRET.sub(r"\1=[redacted]", output.read().decode("utf-8", "replace"))
    return child.returncode == 0, tail[-1200:]


def _validate_candidate(sandbox: Path, target: Path) -> tuple[bool, str]:
    copied = sandbox / "tool" / "theory_master"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(copied)
    # Do not inherit developer pytest options/plugins (including -k deselection).
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    env.pop("PYTHONOPTIMIZE", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    report = sandbox / "supervisor-validation.xml"
    report.unlink(missing_ok=True)
    commands = [[sys.executable, "-m", "py_compile", str(target)],
                [sys.executable, "-m", "pytest", "-q", "-o", "addopts=",
                 "--confcutdir", str(copied / "tests"), "--junitxml", str(report),
                 *[str(copied / "tests" / pattern) for pattern in _TESTS]]]
    for command in commands:
        try:
            proc = subprocess.run(command, cwd=sandbox, env=env, capture_output=True,
                                  text=True, timeout=65, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"Offline validation unavailable: {type(exc).__name__}."
        if proc.returncode:
            return False, f"Offline validation failed ({command[-1]}): {(proc.stderr or proc.stdout)[-600:]}"
    try:
        cases = list(ET.parse(report).getroot().iter("testcase"))
        passed = [case for case in cases if not any(
            case.find(tag) is not None for tag in ("failure", "error", "skipped"))]
        suites = {Path(str(case.get("classname") or "").replace(".", "/")).name
                  for case in passed}
        expected = {Path(pattern).stem for pattern in _TESTS}
        if len(passed) != len(cases) or not cases or not expected.issubset(suites):
            return False, "Offline validation incomplete: each required suite must execute passing tests; skips are not acceptance."
    except (OSError, ET.ParseError, ValueError) as exc:
        return False, f"Offline validation report unavailable: {type(exc).__name__}."
    return True, f"Syntax and {len(passed)} pytest tests in {len(_TESTS)} required offline Canvas suites passed."


def _promote_patch(original: Path, before: bytes, after: bytes) -> bool:
    """No destructive overwrite if another task touched this module meanwhile."""
    git_dir = original.parent.parent / ".git"
    # For linked worktrees .git may be a file; use shared parent instead.
    lock_root = git_dir if git_dir.is_dir() else original.parent.parent
    with (lock_root / "tm-supervisor-repair.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if original.is_symlink() or not original.is_file() or original.read_bytes() != before:
            return False
        fd, name = tempfile.mkstemp(prefix=".tm-repair-", dir=original.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(after)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(name, original.stat().st_mode & 0o777)
            os.replace(name, original)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return True


def _register_auto_change(*, incident_id: str, cycle_dir: Path, relative: Path,
                          before: bytes, after: bytes, before_sha256: str | None = None,
                          scope: str | None = None) -> str | None:
    # Use the authoritative, lock-protected CLI registry, not a parallel DB.
    from .change_register import main
    title = f"Supervisor automatic engine repair {relative.name} {incident_id}"
    scope = scope or ("Independently validated observed patch" if before_sha256 else "Single-file isolated patch")
    args = ["add", "--title", title,
            "--source", f"Runtime incident {incident_id} at {cycle_dir}",
            "--description", f"{scope} {relative}; "
                             f"before={before_sha256 or _hash(before)} after={_hash(after)}. "
                             + ("Observed source; SDK acceptance pending registration."
                                if before_sha256 else "Source promotion pending verified offline checks.")]
    with io.StringIO() as output, contextlib.redirect_stdout(output):
        code = main(args)
        record = output.getvalue().split()
    return record[0] if code == 0 and record and record[0].startswith("TMC-") else None


def _verify_auto_change(change_id: str, *, tests: str, after: bytes) -> bool:
    from .change_register import main
    with io.StringIO() as output, contextlib.redirect_stdout(output):
        return main(["verify", change_id, "--reviewer", "Runtime Supervisor bounded repair",
                     "--evidence", f"{tests}; installed_sha256={_hash(after)}; "
                                   "real post-resume activity remains unverified."]) == 0


def attempt_runtime_repair(*, root: Path, cycle_dir: Path, state: dict[str, Any],
                           state_path: Path, session: Any, model: str,
                           incident_id: str, log_path: Path | None = None) -> dict[str, str]:
    from .runtime import now, write_json
    from .runtime_supervisor_review import resume_blocker

    # Test every non-protocol resume guard *before* running a paid repair agent;
    # never allow a repaired Python file to waive a hard budget/ownership barrier.
    preflight = resume_blocker({**state, "protocol_health": "valid"}, for_repair=True)
    if preflight is not None:
        return _result(f"Repair forbidden: {preflight}")
    if int(state.get(_REPAIR_COUNT_KEY) or 0) >= MAX_REPAIRS_PER_CYCLE:
        return _result("Bounded code repair already attempted for this Cycle; human review required.")
    engine = Path(__file__).resolve().parent
    log = _log_for_repair(root, log_path=log_path)
    target = repair_target(log, state, engine)
    if target is None:
        return _result("No attributable, allowlisted ordinary engine traceback; manual review required.")
    # Consume allowance durably before any side effects or expensive model work.
    state[_REPAIR_COUNT_KEY] = int(state.get(_REPAIR_COUNT_KEY) or 0) + 1
    write_json(state_path, state)
    session.append("system", f"Controlled repair attempt: {target.name}; incident {incident_id}.")
    try:
        before = target.read_bytes()
        repo = engine.parents[2]
        with tempfile.TemporaryDirectory(prefix="tm-supervisor-isolated-") as directory:
            sandbox = Path(directory)
            copied = sandbox / "tool" / "theory_master"
            shutil.copytree(repo / "tool" / "theory_master" / "theory_master",
                            copied / "theory_master",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
            shutil.copytree(repo / "tool" / "theory_master" / "tests",
                            copied / "tests",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
            baseline = _snapshot(copied)
            relative = target.relative_to(repo / "tool" / "theory_master")
            staging_target = copied / relative
            if not staging_target.is_file() or staging_target.is_symlink():
                return _result("Staged engine source is missing or unsafe.")
            prompt = (
                "You are a restricted Python runtime repair agent for a failed TheoryMaster "
                "Canvas engineering projection. The scientific Runner has STOPPED. "
                "Fix ONLY " + str(relative) + " inside this disposable workspace. "
                "Do not touch other files, tests, schemas, science, data, hypotheses, "
                "frozen evidence, budgets, workflow/contract modules or configuration. "
                "No network calls or model tests. Make the smallest correct code change, "
                "inspect existing code context if needed, and explain the fix. "
                "Do not attempt resume, deploy, commit, or access host paths. "
                "Failure (redacted):\n" + json.dumps({
                    "error": state.get("recovery"),
                    "phase": state.get("phase"),
                    "traceback_tail": log[-5500:],
                }, ensure_ascii=False, default=str)
            )
            ok, detail = _invoke_codex(sandbox, model, prompt)
            session.append("repair_agent", f"Codex exit={'ok' if ok else 'failed'}: {detail[:900]}")
            if not ok:
                return _result("Isolated Codex repair failed; no live source changed.",
                               diagnosis=detail[:450])
            changed = _snapshot(copied)
            altered = {key for key in baseline.keys() | changed.keys()
                       if baseline.get(key) != changed.get(key)}
            if altered != {str(relative)}:
                return _result("Repair scope violated or no effective patch; candidate rejected.",
                               diagnosis=f"changed paths: {sorted(altered)[:12]}")
            if staging_target.is_symlink() or not staging_target.is_file():
                return _result("Candidate is not a regular source file.")
            after = staging_target.read_bytes()
            if not after or len(after) > 3_000_000:
                return _result("Candidate size invalid; rejected.")
            from .supervisor_repair_receipts import source_manifest, record_canvas_validation
            validated_manifest = source_manifest(copied / "theory_master")
            passed, detail = _validate_candidate(sandbox, staging_target)
            session.append("test", detail)
            if not passed:
                return _result("Repair failed deterministic validation; no source promoted.", diagnosis=detail)
            if source_manifest(copied / "theory_master") != validated_manifest:
                return _result("Validation inputs changed while tests ran; no source promoted.")
            change_id = _register_auto_change(
                incident_id=incident_id, cycle_dir=cycle_dir, relative=relative,
                before=before, after=after)
            if not change_id:
                return _result("CLI change registration failed; source promotion denied.")
            if not _promote_patch(target, before, after):
                return _result("Live source changed concurrently; candidate not promoted.",
                               diagnosis=f"Unverified change: {change_id}")
            if not _verify_auto_change(change_id, tests=detail, after=after):
                return _result("Patch promoted but CLI verification failed; resume denied.",
                               diagnosis=f"Change awaits audit: {change_id}")
            receipt = {"at": now(), "incident_id": incident_id, "change_id": change_id,
                       "module": str(relative), "before_sha256": _hash(before),
                       "after_sha256": _hash(after), "tests": detail,
                       "status": "validated_patch_pending_runtime_verification"}
            state["supervisor_runtime_repair"] = receipt
            evidence = record_canvas_validation(
                cycle_dir, state, engine, module=target.name, change_id=change_id,
                tests=detail, manifest=validated_manifest, disposition="validated_isolated_patch")
            write_json(state_path, state)
            session.append("repair_promoted", json.dumps(receipt, ensure_ascii=False))
            session.append("repair_validated", json.dumps(evidence, ensure_ascii=False))
            blocker = resume_blocker(state, runtime_revalidated=True)
            if blocker:
                return _result("Patch validated; resume remains blocked: " + blocker, diagnosis=detail)
            return {"action": "resume", "reason": "Scoped code patch passed offline tests; safe checkpoint resume authorized.",
                    "diagnosis": detail, "repair_suggestion": ""}
    except (OSError, RuntimeError, ValueError) as exc:
        return _result(f"Repair aborted safely ({type(exc).__name__}); no resume authorized.")
