from __future__ import annotations

"""Isolated execution of verified TheoryMaster Cycle boundary snapshots."""

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from .specification import TaskSpec, cycle_number, make_run_id


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _fingerprint_tree(root: Path) -> str:
    if any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError(f"replay snapshot tree contains a symlink: {root}")
    rows = [(p.relative_to(root).as_posix(), _sha(p.read_bytes()))
            for p in sorted(root.rglob("*")) if p.is_file()]
    return _sha(json.dumps(rows, separators=(",", ":")).encode())


def _sdk_code_fingerprint() -> str:
    package_root = Path(__file__).resolve().parent
    rows = [(p.relative_to(package_root).as_posix(), _sha(p.read_bytes()))
            for p in sorted(package_root.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc" and "build" not in p.parts]
    return _sha(json.dumps(rows, separators=(",", ":")).encode())


def _rebase_state_paths(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, dict):
        return {k: _rebase_state_paths(v, replacements) for k, v in value.items()}
    if isinstance(value, list):
        return [_rebase_state_paths(v, replacements) for v in value]
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
    return value


def _next_private_cycle_id(graph_path: Path, research_root: Path, *, task_mode: bool, goal_id: str) -> str:
    """Allocate a private replay Cycle after the SQLite history, not just its empty directory tree."""
    if not task_mode:
        return make_run_id(goal_id, research_root, task_mode=False)
    existing: list[int] = []
    if graph_path.is_file():
        with sqlite3.connect(f"file:{graph_path.resolve()}?mode=ro", uri=True) as db:
            for table in ("hypothesis_cycle_owners", "cycle_evaluations", "cycle_preservations"):
                try:
                    rows = db.execute(f"SELECT cycle_id FROM {table}").fetchall()
                except sqlite3.OperationalError:
                    continue
                for row in rows:
                    number = cycle_number(str(row[0] or ""))
                    if number is not None:
                        existing.append(number)
    if research_root.is_dir():
        existing.extend(
            number for path in research_root.iterdir()
            if (number := cycle_number(path.name)) is not None
        )
    return str(max(existing, default=0) + 1)


def _rebind_private_replay_owner(
    graph_path: Path, *, source_cycle_id: str, replay_cycle_id: str, hypothesis_id: str,
) -> None:
    """Rebind a frozen H to its replay Cycle inside the private cloned graph only."""
    from .science_graph_store import ScienceGraphStore
    with ScienceGraphStore(graph_path) as store:
        with store.transaction():
            owner = store.db.execute(
                "SELECT cycle_id FROM hypothesis_cycle_owners WHERE hypothesis_id=?",
                (hypothesis_id,),
            ).fetchone()
            if not owner or str(owner["cycle_id"]) != str(source_cycle_id):
                raise ValueError("investigator checkpoint graph does not own the frozen H in its source Cycle")
            if store.db.execute(
                "SELECT 1 FROM hypothesis_cycle_owners WHERE cycle_id=?", (str(replay_cycle_id),)
            ).fetchone():
                raise ValueError("private replay Cycle ID is already owned in the cloned graph")
            if store.db.execute(
                "SELECT 1 FROM cycle_evaluations WHERE cycle_id=?", (str(source_cycle_id),)
            ).fetchone() or store.db.execute(
                "SELECT 1 FROM cycle_preservations WHERE cycle_id=?", (str(source_cycle_id),)
            ).fetchone():
                raise ValueError("investigator checkpoint graph already contains a Cycle result; refusing to rebind its H owner")
            store.db.execute(
                "UPDATE hypothesis_cycle_owners SET cycle_id=? WHERE cycle_id=? AND hypothesis_id=?",
                (str(replay_cycle_id), str(source_cycle_id), hypothesis_id),
            )
            store._event("cycle.hypothesis_replay_rebound", str(replay_cycle_id), {
                "hypothesis_id": hypothesis_id, "source_cycle_id": str(source_cycle_id),
            })


def _assert_no_active_writer(source_spec: TaskSpec) -> None:
    from .specification import list_cycle_dirs
    for cycle in list_cycle_dirs(source_spec.research_root, task_mode=source_spec.task_mode):
        path = cycle / "research_state.json"
        if not path.is_file():
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot establish source Cycle writer state: {path}") from exc
        if not state.get("execution_active"):
            continue
        owner = state.get("owner_pid")
        if owner is None:
            raise ValueError(f"source Cycle has execution_active without an owner PID: {cycle}")
        if _alive(owner):
            raise ValueError(f"source task has an active writer in {cycle} (pid {owner})")


def _validate_checkpoint(source_cycle: Path, requested: str, source_spec: TaskSpec) -> tuple[dict, Path]:
    checkpoint_name = {"start": "cycle_start", "investigator": "investigator_start", "judge": "judge_start"}[requested]
    path = source_cycle / "replay_checkpoints" / f"{checkpoint_name}.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"no complete {requested} replay checkpoint; historical snapshots are never reconstructed")
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema") != "theory-master/replay-checkpoint/v2":
        raise ValueError("unsupported replay checkpoint schema")
    expected_boundary = {"start": "before_research_lead", "investigator": "after_frozen_h_lock_before_investigator",
                         "judge": "after_complete_findings_before_judge"}[requested]
    if record.get("name") != checkpoint_name or record.get("boundary") != expected_boundary:
        raise ValueError("checkpoint name/boundary does not match requested replay stage")
    if not record.get("sdk_code_sha256") or record.get("sdk_code_sha256") != _sdk_code_fingerprint():
        raise ValueError("TheoryMaster SDK code fingerprint differs from the checkpoint")
    routes = record.get("routes")
    route_digest = _sha(json.dumps(routes, sort_keys=True, separators=(",", ":")).encode()) if isinstance(routes, dict) else ""
    if not routes or route_digest != record.get("routes_sha256"):
        raise ValueError("checkpoint model routes are missing or their digest is invalid")
    state = record.get("state")
    actual_state_digest = _sha(json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) if isinstance(state, dict) else ""
    if not isinstance(state, dict) or actual_state_digest != record.get("state_sha256"):
        raise ValueError(f"checkpoint state digest mismatch (expected {record.get('state_sha256')}, found {actual_state_digest})")
    inputs = path.parent / str(record.get("inputs_dir") or "")
    if inputs.is_symlink() or not inputs.is_dir() or inputs.resolve().parent != path.parent.resolve():
        raise ValueError("checkpoint frozen input directory is missing or unsafe")
    expected = record.get("source_sha256") or {}
    external = record.get("external_references") or {}
    if not isinstance(external, dict) or set(external) - {"benchmark"}:
        raise ValueError("unsupported external scientific input reference")
    for label, digest in expected.items():
        if label in external:
            reference = Path(str(external[label]))
            if not reference.is_absolute() or not reference.is_dir() or _fingerprint_tree(reference) != digest:
                raise ValueError(f"external scientific input has changed: {label}")
            continue
        if label in {"task_skills", "notes_mirror"}:
            directory = inputs / label
            if not directory.is_dir() or _fingerprint_tree(directory) != digest:
                raise ValueError(f"checkpoint input digest mismatch: {label}")
            continue
        if label == "science_graph":
            matches = [inputs / "science_graph.sqlite3"]
        else:
            matches = list(inputs.glob(label + ".*"))
        if len(matches) != 1 or matches[0].is_symlink() or not matches[0].is_file() or _sha(matches[0].read_bytes()) != digest:
            raise ValueError(f"checkpoint input digest mismatch: {label}")
    required = {"research_plan": ".md", "task_config": ".json", "branch_canvas": ".canvas",
                "science_graph": ".sqlite3", "scientific_model_state": ".json"}
    for label, suffix in required.items():
        if label not in expected or (inputs / f"{label}{suffix}").is_symlink() or not (inputs / f"{label}{suffix}").is_file():
            raise ValueError(f"checkpoint lacks frozen {label}; refusing replay")
    if requested in {"investigator", "judge"}:
        workspace = inputs / "workspace"
        if not workspace.is_dir() or not record.get("workspace_sha256") or _fingerprint_tree(workspace) != record["workspace_sha256"]:
            raise ValueError("checkpoint lacks a valid frozen qualification workspace")
        lock = state.get("hypothesis_lock") or {}
        if lock.get("status") != "locked":
            raise ValueError("investigator/judge replay requires a frozen locked hypothesis")
    if requested == "judge":
        raise ValueError("judge replay is unavailable: this checkpoint does not yet certify a complete, reconciled Findings/Evidence transaction")
    from .specification import CYCLE_MARKER
    if not (source_cycle / CYCLE_MARKER).is_file():
        raise ValueError("source path is not a marked TheoryMaster Cycle")
    _assert_no_active_writer(source_spec)
    return record, inputs


def replay_cycle(source_spec: TaskSpec, source_cycle: Path, requested: str, *, dry_run: bool = False,
                 backend_factory=None) -> dict[str, Any]:
    record, inputs = _validate_checkpoint(source_cycle, requested, source_spec)
    if dry_run:
        return {"status": "validated", "source_cycle": str(source_cycle), "checkpoint": record["name"], "stage": requested}
    lock_path = source_spec.research_root / ".cycle-replay.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_path.open("a+")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another replay preparation/execution owns this task") from exc
        locked_record, locked_inputs = _validate_checkpoint(source_cycle, requested, source_spec)
        if locked_record != record or locked_inputs != inputs:
            raise ValueError("checkpoint changed during replay preflight")
        record, inputs = locked_record, locked_inputs
        _assert_no_active_writer(source_spec)
        attempts_root = source_spec.research_root / ".cycle-replays" / str(record["logical_cycle"])
        attempts_root.mkdir(parents=True, exist_ok=True)
        attempt_id = uuid.uuid4().hex
        replay_task_id = uuid.uuid4().int & ((1 << 63) - 1) or 1
        attempt_root = attempts_root / attempt_id
        staging = Path(tempfile.mkdtemp(prefix=".prepare-", dir=attempts_root))
        try:
            task_home = staging / "task"
            runtime_root = staging / "research"
            task_home.mkdir(parents=True)
            runtime_root.mkdir(parents=True)
            shutil.copytree(inputs, staging / "frozen_inputs")
            plan = task_home / "research_plan.md"
            canvas = task_home / "branch.canvas"
            config_path = task_home / ".theory-master" / "theory-master.config.json"
            config_path.parent.mkdir(parents=True)
            shutil.copy2(inputs / "research_plan.md", plan)
            shutil.copy2(inputs / "branch_canvas.canvas", canvas)
            # The private replay task must receive the canonical graph snapshot;
            # Canvas is only a projection and cannot reconstruct scientific state.
            shutil.copy2(inputs / "science_graph.sqlite3", task_home / "science_graph.sqlite3")
            config = json.loads((inputs / "task_config.json").read_text(encoding="utf-8"))
            if "task_skills" in record["source_sha256"]:
                shutil.copytree(inputs / "task_skills", config_path.parent / "skills")
            if "notes_mirror" in record["source_sha256"]:
                shutil.copytree(inputs / "notes_mirror", task_home / "notes", dirs_exist_ok=True)
            benchmark_src = inputs / "benchmark" if (
                "benchmark" in record["source_sha256"] and
                "benchmark" not in (record.get("external_references") or {})
            ) else None
            external_benchmark = (
                Path(record["external_references"]["benchmark"])
                if "benchmark" in (record.get("external_references") or {}) else None
            )
            benchmark = task_home / "benchmark"
            if benchmark_src is not None:
                shutil.copytree(benchmark_src, benchmark)
            benchmark_access = external_benchmark or (benchmark if benchmark_src is not None else None)
            config.update({"plan_path": str(plan), "task_home": str(task_home), "research_root": str(runtime_root),
                           "branch_canvas": str(canvas), "notes_dir": str(task_home / "notes"),
                           "legacy_research_root": None, "task_id": replay_task_id,
                           "max_cycles": 1})
            if benchmark_access is not None:
                config["benchmark_root"] = str(benchmark_access)
            (task_home / "notes").mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            shutil.copy2(inputs / "scientific_model_state.json", runtime_root / "scientific_model_state.json")
            private_spec = replace(source_spec, document=plan, research_root=runtime_root, branch_canvas=canvas,
                                   notes_mirror_dir=task_home / "notes", config_path=config_path, task_home=task_home,
                                   benchmark_dir=benchmark_access,
                                   legacy_research_root=None, max_cycles=1)
            manifest = {"schema": "theory-master/cycle-replay-attempt/v1", "attempt_id": attempt_id,
                        "source_task_id": source_spec.master_id, "source_cycle": str(source_cycle),
                        "replay_task_id": replay_task_id,
                        "source_attempt_id": record.get("attempt_id"), "checkpoint": record["name"],
                        "stage": requested, "frozen_input_sha256": record["source_sha256"],
                        "status": "prepared", "execution_root": str(attempt_root)}
            (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if requested == "investigator":
                replay_cycle_id = _next_private_cycle_id(
                    task_home / "science_graph.sqlite3", runtime_root,
                    task_mode=private_spec.task_mode, goal_id=private_spec.goal_id,
                )
                lock = record["state"].get("hypothesis_lock") or {}
                frozen_h_id = str(lock.get("id") or "").strip()
                if not frozen_h_id:
                    raise ValueError("investigator checkpoint has no frozen H identity")
                _rebind_private_replay_owner(
                    task_home / "science_graph.sqlite3", source_cycle_id=str(source_cycle.name),
                    replay_cycle_id=replay_cycle_id, hypothesis_id=frozen_h_id,
                )
                cycle_workspace = runtime_root / replay_cycle_id / "workspace"
                cycle_workspace.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(inputs / "workspace", cycle_workspace)
                checkpoint_state = json.loads(json.dumps(record["state"]))
                source_branch_id = str(checkpoint_state.get("active_branch_id") or source_cycle.name)
                branches = checkpoint_state.get("branches")
                if not isinstance(branches, list):
                    raise ValueError("investigator checkpoint is missing its frozen branch registry")
                matching_branches = [
                    item for item in branches
                    if isinstance(item, dict) and str(item.get("id") or "") in {
                        source_branch_id, str(source_cycle.name), "cycle-" + str(source_cycle.name),
                    }
                ]
                if len(matching_branches) != 1:
                    raise ValueError("investigator checkpoint must identify exactly one frozen branch to replay")
                matching_branches[0]["id"] = replay_cycle_id
                checkpoint_state.update({"cycle_id": replay_cycle_id, "run_id": replay_cycle_id,
                                         "active_branch_id": replay_cycle_id,
                                         "cycle_dir": str(runtime_root / replay_cycle_id),
                                         "last_seq": 0, "events": [], "threads": {}, "investigation_attempts": {},
                                         "attempt_id": uuid.uuid4().hex, "status": "interrupted",
                                         "execution_active": False, "resumable": True,
                                         "role_outputs": {k: v for k, v in (checkpoint_state.get("role_outputs") or {}).items()
                                                          if k in {"research_lead_start", "challenger", "literature"}}})
                replacements = [
                    (str(source_cycle.resolve() / "workspace"), str(attempt_root / "research" / replay_cycle_id / "workspace")),
                    (str(source_cycle.resolve()), str(attempt_root / "research" / replay_cycle_id)),
                    (str(source_spec.document.resolve()), str(attempt_root / "task/research_plan.md")),
                    (str(source_spec.branch_canvas.resolve()), str(attempt_root / "task/branch.canvas")),
                    (str(source_spec.research_root.resolve()), str(attempt_root / "research")),
                    (str(source_spec.config_path.resolve()), str(attempt_root / "task/.theory-master/theory-master.config.json")),
                ]
                checkpoint_state = _rebase_state_paths(checkpoint_state, replacements)
            staging.rename(attempt_root)
            from .task import write_binding
            final_task = attempt_root / "task"
            final_root = attempt_root / "research"
            final_config = final_task / ".theory-master/theory-master.config.json"
            final_config_data = json.loads(final_config.read_text(encoding="utf-8"))
            final_config_data.update({"plan_path": str(final_task / "research_plan.md"), "task_home": str(final_task),
                                      "research_root": str(final_root), "branch_canvas": str(final_task / "branch.canvas"),
                                      "notes_dir": str(final_task / "notes"), "legacy_research_root": None,
                                      "task_id": replay_task_id, "max_cycles": 1})
            if benchmark_access is not None:
                final_config_data["benchmark_root"] = str(
                    benchmark_access if external_benchmark is not None else final_task / "benchmark"
                )
            final_config.write_text(json.dumps(final_config_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            write_binding(final_task, final_config)
            write_binding(final_root, final_config)
            replay_cycle_id = None
            if requested == "start":
                replay_cycle_id = _next_private_cycle_id(
                    final_task / "science_graph.sqlite3", final_root,
                    task_mode=private_spec.task_mode, goal_id=private_spec.goal_id,
                )
            private_spec = replace(private_spec, document=attempt_root / "task/research_plan.md",
                                   research_root=attempt_root / "research", branch_canvas=attempt_root / "task/branch.canvas",
                                   notes_mirror_dir=attempt_root / "task/notes", config_path=attempt_root / "task/.theory-master/theory-master.config.json",
                                   task_home=attempt_root / "task",
                                   benchmark_dir=(external_benchmark if external_benchmark is not None else
                                                  attempt_root / "task/benchmark" if benchmark_src is not None else None),
                                   master_id=replay_task_id, replay_cycle_id=replay_cycle_id)
            from .backends import CodexBackend, RoleRoute
            from .workflow import Workflow
            routes = {role: RoleRoute(**route) for role, route in record["routes"].items()}
            backend = backend_factory(private_spec, routes) if backend_factory else CodexBackend(private_spec.research_root, routes=routes)
            workflow = Workflow(private_spec, backend, routes)
            _assert_no_active_writer(source_spec)
            if requested == "start":
                result = workflow.run()
            else:
                result = workflow._run_cycle(checkpoint_state)
            if external_benchmark is not None and _fingerprint_tree(external_benchmark) != record["source_sha256"]["benchmark"]:
                raise ValueError("external scientific benchmark changed during replay; result is invalid")
            manifest.update({"status": result.get("status", "unknown"), "replay_cycle_id": result.get("cycle_id"),
                             "replay_cycle_attempt_id": result.get("attempt_id"), "finished": True})
            (attempt_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return {"status": manifest["status"], "attempt_id": attempt_id, "execution_root": str(attempt_root),
                    "source_cycle": str(source_cycle), "replay_cycle_id": manifest.get("replay_cycle_id")}
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging)
            if attempt_root.exists():
                manifest_path = attempt_root / "manifest.json"
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
                    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
            raise
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()
