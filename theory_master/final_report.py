"""Post-completion report jobs; scientific records remain sealed and read-only.

An actual completion commit dispatches a detached consumer; read paths only
enqueue. The shared graph_research writer and independent reviewer pipeline
never run inside the scientific Cycle process.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
from typing import Any, Iterator
from uuid import uuid4

from .notebook import atomic_json, graph_database_path
from .science_graph_store import ScienceGraphStore

JOB_SCHEMA = "sciver/final-report-job/v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report_registry(spec: Any) -> Path:
    root = Path(spec.research_root).expanduser().resolve()
    path = root
    for part in (".theory-master", "final-reports"):
        path = path / part
        if path.is_symlink():
            raise ValueError("final report registry must not contain symbolic links")
        path.mkdir(exist_ok=True)
    return path


def _database(spec: Any, database: Path | None) -> Path:
    explicit = database or getattr(spec, "graph_database", None)
    return Path(explicit) if explicit is not None else graph_database_path(Path(spec.research_root))


def _seal(database: Path) -> dict[str, Any] | None:
    if not database.is_file() or database.is_symlink():
        return None
    with ScienceGraphStore(database, readonly=True) as store:
        terminal = store.goal_terminal()
    return terminal if terminal and terminal.get("verdict") == "passed" else None


def _identity(terminal: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(terminal, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


@contextmanager
def _lock(path: Path, *, blocking: bool = True) -> Iterator[None]:
    if path.is_symlink():
        raise ValueError("final report lock must not be a symbolic link")
    with path.open("a+", encoding="utf-8") as stream:
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(stream.fileno(), operation)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_job(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError("unsafe final report job")
    job = json.loads(path.read_text(encoding="utf-8"))
    if job.get("schema") != JOB_SCHEMA or job.get("job_id") != path.stem:
        raise ValueError("invalid final report job identity")
    return job


def ensure_final_report_job(spec: Any, *, database: Path | None = None) -> dict[str, Any] | None:
    """Idempotently enqueue an SQLite-sealed task without importing a model client.

    The same operation repairs the seal-to-enqueue crash window on later reads.
    An unsealed task, including one with only a completed Cycle, gets no job.
    """
    graph = _database(spec, database)
    terminal = _seal(graph)
    if terminal is None:
        return None
    registry = report_registry(spec)
    ident = _identity(terminal)
    jobs = registry / "jobs"
    if jobs.is_symlink():
        raise ValueError("final report jobs must not be a symbolic link")
    jobs.mkdir(exist_ok=True)
    path = jobs / f"{ident}.json"
    with _lock(registry / "queue.lock"):
        if path.exists():
            return _read_job(path)
        job = {"schema": JOB_SCHEMA, "job_id": ident, "state": "queued",
               "created_at": _now(), "updated_at": _now(), "attempt": 0,
               "research_root": str(Path(spec.research_root).resolve()),
               "plan_path": str(Path(spec.document).resolve()),
               "graph_database": str(graph.resolve()), "goal_terminal": terminal}
        atomic_json(path, job)
    return job


def _allowed_roots(spec: Any) -> list[Path]:
    """Only host-bound task and notebook roots; no inferred benchmark parent."""
    roots = {Path(spec.research_root).resolve()}
    for value in (getattr(spec, "task_home", None),
                  getattr(spec, "branch_canvas", None),
                  getattr(spec, "notes_mirror_dir", None)):
        if value is not None:
            path = Path(value).resolve()
            roots.add(path.parent if path.suffix == ".canvas" else path)
    return sorted(roots, key=str)


def final_report_backend_options(spec: Any) -> dict[str, Any]:
    """Reuse task routes and existing provider/auth configuration in fresh chats.

    Provider environments can contain credentials. They are process inputs only,
    never report material, job metadata, command-line arguments or saved logs.
    """
    from .account_model_routing import routes_for_account
    from .backend_config import ACCOUNT_PROVIDERS, resolve_routes
    from .codex_accounts import selected_account
    from .codex_backend import CodexBackend, _codex_home
    root = Path(spec.research_root)
    task_env = root / ".theory-master" / "env"
    routes = resolve_routes(task_env_file=task_env if task_env.is_file() else None,
        task_route_override_root=root if getattr(spec, "task_mode", False) else None)
    account = os.environ.get("THEORYMASTER_WORKER_PINNED_ACCOUNT") or selected_account()
    routes = routes_for_account(routes, account)
    home = str(_codex_home())
    options: dict[str, dict[str, Any]] = {key: {} for key in (
        "model_by_role", "reasoning_effort_by_role", "config_overrides_by_role", "env_by_role")}
    for role, scientific_role in (("writer", "reporter"), ("reviewer", "judge")):
        route = routes[scientific_role]
        overrides, environment = CodexBackend._runtime_config({scientific_role: route})
        config = tomllib.loads("\n".join(overrides)) if overrides else {}
        native_provider = ("openai_https" if route.provider == "CodexHTTPS"
                           else None if route.provider in ACCOUNT_PROVIDERS else route.provider)
        if native_provider is not None:
            config["model_provider"] = native_provider
        options["model_by_role"][role] = route.model
        options["reasoning_effort_by_role"][role] = (route.reasoning_effort or
            ("medium" if route.provider in ACCOUNT_PROVIDERS else None))
        options["config_overrides_by_role"][role] = config
        options["env_by_role"][role] = {**environment, "CODEX_HOME": home}
    return options


def launch_final_report_job(spec: Any, *, database: Path | None = None,
                            retry_failed: bool = False) -> dict[str, Any] | None:
    """Dispatch a detached report consumer for a sealed completed task.

    Ordinary completion reads only enqueue. A failed attempt needs an explicit
    retry, and this dispatcher never resumes or starts a scientific Cycle.
    """
    job = ensure_final_report_job(spec, database=database)
    if job is None or job["state"] not in ({"queued", "failed"} if retry_failed else {"queued"}):
        return job
    root = Path(spec.research_root).resolve()
    if not (root / ".theory-master" / "task.json").is_file():
        return {**job, "launch_status": "task_binding_unavailable"}
    registry = report_registry(spec)
    receipt = registry / f"launch-{job['job_id']}.json"
    from .runtime_manager import process_identity_alive, process_start_ticks
    with _lock(registry / "dispatch.lock"):
        job = _read_job(registry / "jobs" / f"{job['job_id']}.json")
        if job["state"] not in ({"queued", "failed"} if retry_failed else {"queued"}):
            return job
        if receipt.is_symlink():
            raise ValueError("unsafe report launcher receipt")
        if receipt.is_file():
            previous = json.loads(receipt.read_text(encoding="utf-8"))
            if process_identity_alive(previous.get("pid"), previous.get("pid_start_ticks")):
                return {**job, "launch_status": "running", "launcher": previous}
        if job["state"] == "failed":
            job.update(state="queued", updated_at=_now())
            atomic_json(registry / "jobs" / f"{job['job_id']}.json", job)
        command = [sys.executable, "-m", "theory_master.final_report", "--task", str(root), "--generate"]
        source_root = str(Path(__file__).resolve().parent.parent)
        environment = dict(os.environ)
        previous_path = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = source_root + (os.pathsep + previous_path if previous_path else "")
        log_path = registry / f"consumer-{job['job_id']}.log"
        if log_path.is_symlink():
            raise ValueError("unsafe report consumer log")
        with log_path.open("ab") as log:
            process = subprocess.Popen(command, cwd=registry, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
                env=environment)
        launcher = {"pid": process.pid, "pid_start_ticks": process_start_ticks(process.pid),
                    "started_at": _now(), "log_path": str(log_path), "source_root": source_root}
        atomic_json(receipt, launcher)
    return {**job, "launch_status": "started", "launcher": launcher}


def generate_final_report(spec: Any, backend: Any = None, *, database: Path | None = None,
                          output_dir: Path | None = None, regenerate: bool = False,
                          backend_factory: Any = None) -> dict[str, Any]:
    """Run exactly one shared writer/reviewer attempt, independently of Cycles.

    Concurrent consumers cannot run a second writer for this seal. Failures are
    recorded in this registry and never change scientific completion or verdicts.
    """
    if (backend is None) == (backend_factory is None):
        raise ValueError("provide either a report backend or a backend factory")
    job = ensure_final_report_job(spec, database=database)
    if job is None:
        raise ValueError("final reports require an SQLite-sealed completed task")
    registry = report_registry(spec)
    path = registry / "jobs" / f"{job['job_id']}.json"
    try:
        with _lock(registry / f"{job['job_id']}.lock", blocking=False):
            job = _read_job(path)
            if job["state"] in {"ready", "needs_revision"} and not regenerate:
                return job
            job = {key: job[key] for key in (
                "schema", "job_id", "created_at", "attempt", "research_root", "plan_path",
                "graph_database", "goal_terminal")}
            job.update(state="running", attempt=int(job.get("attempt", 0)) + 1,
                       started_at=_now(), updated_at=_now())
            atomic_json(path, job)
            destination = (Path(output_dir).expanduser().resolve() if output_dir is not None
                           else registry / "versions" / job["job_id"] / f"attempt-{job['attempt']:04d}")
            try:
                effective_backend = backend if backend_factory is None else backend_factory()
                from graph_research.final_report import generate_report
                from graph_research.final_report_snapshot import build_snapshot
                graph = _database(spec, database)
                if _identity(_seal(graph) or {}) != job["job_id"]:
                    raise ValueError("task completion seal changed before report generation")
                snapshot = build_snapshot(graph, research_root=Path(spec.research_root),
                    plan_path=Path(spec.document), allowed_roots=_allowed_roots(spec),
                    task_metadata={"task_id": str(getattr(spec, "master_id", "")),
                                   "goal_id": str(getattr(spec, "goal_id", ""))},
                    require_seal=True)
                result = generate_report(snapshot, destination, effective_backend)
                if result.get("state") not in {"ready", "needs_revision", "failed"}:
                    raise ValueError("shared report pipeline returned an invalid state")
                job.update(result)
            except Exception as error:
                job.update(state="failed", error=f"{type(error).__name__}: {error}")
            job.update(updated_at=_now(), finished_at=_now(), output_dir=str(destination))
            atomic_json(path, job)
            return job
    except BlockingIOError:
        return {**_read_job(path), "state": "running", "busy": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Queue or generate a sealed task's final research report")
    parser.add_argument("--task", type=Path, help="Registered task, notebook or research root")
    parser.add_argument("--generate", action="store_true", help="Run one writer and one independent reviewer")
    parser.add_argument("--regenerate", action="store_true", help="Create another explicit report attempt")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--model", help="Explicit model override; default uses task Reporter/Judge routes")
    parser.add_argument("--timeout-seconds", type=int, default=600,
                        help="Maximum seconds per writer/reviewer invocation")
    args = parser.parse_args(argv)
    if not args.generate and (args.regenerate or args.model or args.output_dir):
        parser.error("--regenerate, --model and --output-dir require --generate")
    from .cli_discovery import spec_from_task
    spec = spec_from_task(args.task)
    if args.generate:
        if args.timeout_seconds < 1:
            parser.error("--timeout-seconds must be positive")
        def create_backend():
            from graph_research.final_report_codex import CodexFinalReportBackend
            return CodexFinalReportBackend(work_dir=report_registry(spec) / "agent-runs" / uuid4().hex,
                model=args.model, timeout_seconds=args.timeout_seconds, **final_report_backend_options(spec))
        result = generate_final_report(spec, backend_factory=create_backend,
            output_dir=args.output_dir, regenerate=args.regenerate)
    else:
        result = ensure_final_report_job(spec)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result is not None and result.get("state") in {"queued", "ready"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
