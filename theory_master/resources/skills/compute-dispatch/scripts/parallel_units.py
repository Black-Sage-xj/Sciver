#!/usr/bin/env python3
"""Bounded, restartable independent scientific subprocess dispatcher.

This is execution tooling, not scientific adjudication or a sandbox.
An Investigator supplies the *already authorized* immutable-unit manifest.
No shell command interpretation or scientific parameter selection occurs here.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

SCHEMA = "theory-master/independent-compute-units/v1"
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
THREAD_ENV = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
SHELLS = {"sh", "bash", "zsh", "fish", "dash", "csh", "tcsh", "cmd", "cmd.exe", "powershell"}
MiB = 1024 * 1024


class DispatchError(ValueError):
    """Fail closed before performing any scientific computation."""


def _sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * MiB), b""):
            result.update(block)
    return result.hexdigest()


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _relative_file(root: Path, raw: str, *, exists: bool = False) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise DispatchError("empty file/directory path")
    path = (root / raw).absolute() if not Path(raw).is_absolute() else Path(raw).absolute()
    if not _within(path, root):
        raise DispatchError(f"path escapes the authorized workspace: {raw}")
    if path.resolve(strict=False) != path:
        raise DispatchError(f"symlink or non-canonical path rejected: {raw}")
    if exists and (not path.is_file() or path.is_symlink()):
        raise DispatchError(f"required file missing: {raw}")
    return path


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _unit_inputs(unit: dict[str, Any], workspace: Path) -> dict[str, str]:
    expected = unit.get("inputs")
    if not isinstance(expected, list) or not expected:
        raise DispatchError(f"{unit['id']}: specify all scientific inputs and script in inputs[]")
    sources: dict[str, str] = {}
    for item in expected:
        if not isinstance(item, dict):
            raise DispatchError(f"{unit['id']}: invalid input descriptor")
        raw = item.get("path")
        digest = item.get("sha256")
        if not isinstance(raw, str) or not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise DispatchError(f"{unit['id']}: inputs need path and lowercase sha256")
        # Read-only scientific inputs may intentionally live outside cycle_workspace;
        # only *writable* job cwd, output paths and state are workspace-bound.
        file_path = Path(raw).expanduser()
        if not file_path.is_absolute():
            file_path = workspace / file_path
        file_path = file_path.resolve(strict=True)
        if not file_path.is_file():
            raise DispatchError(f"{unit['id']}: missing input: {raw}")
        key = str(file_path)
        if key in sources and sources[key] != digest:
            raise DispatchError(f"{unit['id']}: contradictory input digests")
        sources[key] = digest
    return sources


def _load_manifest(manifest: Path, workspace: Path) -> tuple[list[dict[str, Any]], dict[str, str], str]:
    if not manifest.is_file() or manifest.is_symlink():
        raise DispatchError("manifest must be an existing regular file")
    try:
        raw = manifest.read_bytes()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchError(f"invalid manifest: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise DispatchError(f"manifest schema must equal {SCHEMA}")
    items = data.get("units")
    if not isinstance(items, list) or not items:
        raise DispatchError("manifest must contain nonempty independent units[]")
    validated: list[dict[str, Any]] = []
    ids: set[str] = set()
    working_dirs: set[Path] = set()
    owned_outputs: set[Path] = set()
    all_sources: dict[str, str] = {}
    for unit in items:
        if not isinstance(unit, dict):
            raise DispatchError("unit must be a JSON object")
        ident = unit.get("id")
        if not isinstance(ident, str) or not IDENT.fullmatch(ident) or ident in ids:
            raise DispatchError(f"invalid or duplicate unit ID: {ident!r}")
        ids.add(ident)
        command = unit.get("argv")
        if (not isinstance(command, list) or len(command) < 2
                or not all(isinstance(word, str) and word for word in command)):
            raise DispatchError(f"{ident}: argv must be a literal argument array (executable + arguments)")
        if Path(command[0]).name.lower() in SHELLS:
            raise DispatchError(f"{ident}: shell launchers are prohibited; use argv without a shell")
        cwd = _relative_file(workspace, unit.get("cwd", ""))
        if cwd == workspace or cwd in working_dirs:
            raise DispatchError(f"{ident}: independent units require distinct private cwd paths")
        working_dirs.add(cwd)
        outputs = unit.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise DispatchError(f"{ident}: declare at least one expected relative output file")
        paths = []
        for raw_output in outputs:
            if not isinstance(raw_output, str) or Path(raw_output).is_absolute():
                raise DispatchError(f"{ident}: output paths must be relative to unit cwd")
            target = _relative_file(cwd, raw_output)
            if target == cwd or not _within(target, cwd) or target in owned_outputs:
                raise DispatchError(f"{ident}: overlapping output paths: {raw_output}")
            owned_outputs.add(target)
            paths.append(target)
        environment = unit.get("env", {})
        if (not isinstance(environment, dict)
                or any(not isinstance(k, str) or re.fullmatch(r"[A-Z_][A-Z_0-9]*", k) is None
                       or not isinstance(v, str) for k, v in environment.items())):
            raise DispatchError(f"{ident}: env must map uppercase environment names to strings")
        if any(k in THREAD_ENV for k in environment):
            raise DispatchError(f"{ident}: BLAS/OMP limits are controlled by dispatcher CLI")
        sources = _unit_inputs(unit, workspace)
        for filename, digest in sources.items():
            if filename in all_sources and all_sources[filename] != digest:
                raise DispatchError(f"conflicting frozen input digest: {filename}")
            all_sources[filename] = digest
        validated.append({"id": ident, "cwd": cwd, "outputs": paths,
                          "argv": command, "env": environment, "inputs": sources})
    for path in working_dirs:
        if any(other != path and _within(path, other) for other in working_dirs):
            raise DispatchError("nested working directories not independent")
    if any(_within(manifest, cwd) for cwd in working_dirs):
        raise DispatchError("manifest is inside a writable unit workspace")
    if any(_within(Path(source), cwd) for source in all_sources for cwd in working_dirs):
        raise DispatchError("frozen scientific input lies inside a writable unit workspace")
    # Require the directly executed Python script to be independently fingerprinted
    # rather than trusting only the manifest arguments.
    for unit in validated:
        if Path(unit["argv"][0]).name.lower().startswith("python"):
            for argument in unit["argv"][1:2]:
                if argument.endswith(".py"):
                    script = Path(argument)
                    if not script.is_absolute():
                        script = unit["cwd"] / script
                    if str(script.resolve(strict=False)) not in unit["inputs"]:
                        raise DispatchError(f"{unit['id']}: Python worker script absent from inputs[]")
    # A conservative guarantee: no unit may write into another unit's workspace.
    for unit in validated:
        for path in unit["outputs"]:
            if any(path == other["cwd"] or _within(path, other["cwd"])
                   for other in validated if other["id"] != unit["id"]):
                raise DispatchError("unit output collides with another unit workspace")
    return validated, all_sources, hashlib.sha256(raw).hexdigest()


def _verify_sources(sources: dict[str, str]) -> None:
    for raw, digest in sources.items():
        file_path = Path(raw)
        if not file_path.is_file() or _sha256(file_path) != digest:
            raise DispatchError(f"frozen input missing or changed: {raw}")


def _cpu_slots() -> int:
    try:
        affinity = set(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = set(range(os.cpu_count() or 1))
    slots = len(affinity)
    # Linux physical CPU/core pairs are preferable for independent CPU-bound work.
    try:
        physical: dict[int, tuple[str, str]] = {}
        for entry in Path("/proc/cpuinfo").read_text().split("\n\n"):
            values = dict(re.findall(r"^([^:\n]+)\s*:\s*(.+)$", entry, re.M))
            if "processor" not in values:
                continue
            index = int(values["processor"])
            if index in affinity:
                physical[index] = (values.get("physical id", "0"), values.get("core id", str(index)))
        if len(physical) == len(affinity):
            slots = len(set(physical.values()))
    except (OSError, ValueError):
        pass
    # Effective quotas can further restrict the physical/affinity calculation.
    try:
        group = next((line.split("::", 1)[1] for line in Path("/proc/self/cgroup").read_text().splitlines()
                      if "::" in line), "")
        quota = (Path("/sys/fs/cgroup") / group.lstrip("/") / "cpu.max").read_text().split()
        if len(quota) >= 2 and quota[0] != "max":
            slots = min(slots, max(1, int(quota[0]) // int(quota[1])))
    except (OSError, ValueError, ZeroDivisionError):
        pass
    return max(1, slots)


def _memory_available() -> int:
    try:
        lines = Path("/proc/meminfo").read_text().splitlines()
        free_kib = next(int(line.split()[1]) for line in lines if line.startswith("MemAvailable:"))
        usable = free_kib * 1024
    except (OSError, ValueError, StopIteration):
        usable = 0
    try:
        group = next((line.split("::", 1)[1] for line in Path("/proc/self/cgroup").read_text().splitlines()
                      if "::" in line), "")
        base = Path("/sys/fs/cgroup") / group.lstrip("/")
        cap = (base / "memory.max").read_text().strip()
        if cap != "max":
            remaining = max(0, int(cap) - int((base / "memory.current").read_text().strip()))
            usable = min(usable, remaining) if usable else remaining
    except (OSError, ValueError, StopIteration):
        pass
    return usable


def _resource_plan(workspace: Path, count: int, args: argparse.Namespace) -> dict[str, Any]:
    physical = _cpu_slots()
    cpu = max(1, physical // args.threads_per_worker)
    mem_bytes = _memory_available()
    memory = max(0, (mem_bytes - args.reserve_memory_mib * MiB) // (args.memory_mib_per_worker * MiB))
    disk_bytes = shutil.disk_usage(workspace).free
    disk = max(0, (disk_bytes - args.reserve_disk_mib * MiB) // (args.disk_mib_per_worker * MiB))
    if memory < 1 or disk < 1:
        raise DispatchError("not enough free memory or disk after reserved headroom")
    requested = args.max_workers if args.workers == "auto" else int(args.workers)
    selected = min(count, requested, args.max_workers, cpu, memory, disk)
    if selected < 1:
        raise DispatchError("resource plan permits no worker")
    return {"workers": selected, "requested_workers": requested, "physical_cpu_slots": physical,
            "affinity_cgroup_cpu_budget": cpu, "memory_worker_cap": memory,
            "disk_worker_cap": disk, "threads_per_worker": args.threads_per_worker,
            "memory_available_bytes": mem_bytes, "disk_free_bytes": disk_bytes,
            "warning": "capacity estimates are planning limits; OS resource/IO use still requires observation"}


def _unit_record(state: Path, unit: dict[str, Any], manifest_hash: str) -> dict[str, Any] | None:
    path = state / "units" / (unit["id"] + ".json")
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchError(f"corrupt run state for {unit['id']}: {exc}") from exc
    if record.get("manifest_sha256") != manifest_hash:
        raise DispatchError(f"manifest changed: refused to reinterpret {unit['id']}'s outputs")
    if record.get("status") == "complete":
        saved = record.get("outputs_sha256", {})
        if not saved or set(saved) != set(str(out) for out in unit["outputs"]):
            raise DispatchError(f"missing output proof for {unit['id']}")
        for name, expected in saved.items():
            target = Path(name)
            if not target.is_file() or target.resolve(strict=False) != target or _sha256(target) != expected:
                raise DispatchError(f"completed output changed: {name}")
    return record


def _execute_one(unit: dict[str, Any], *, state: Path, manifest_hash: str,
                 threads: int, attempt: int, min_disk_bytes: int) -> dict[str, Any]:
    ident = unit["id"]
    unit["cwd"].mkdir(parents=True, exist_ok=True)
    log_root = state / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    stdout = log_root / f"{ident}.attempt-{attempt}.stdout.log"
    stderr = log_root / f"{ident}.attempt-{attempt}.stderr.log"
    env = os.environ.copy()
    env.update(unit["env"])
    for key in THREAD_ENV:
        env[key] = str(threads)
    started = time.time()
    code = None
    error = None
    try:
        if shutil.disk_usage(state).free < min_disk_bytes:
            error = "insufficient free disk for next unit after reserved headroom"
        else:
            with stdout.open("wb") as out, stderr.open("wb") as err:
                # No shell or detached process; normal TM permissions remain in force.
                completed = subprocess.run(unit["argv"], cwd=unit["cwd"], env=env, stdout=out, stderr=err,
                                           check=False)
            code = completed.returncode
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"
    outputs: dict[str, str] = {}
    if code == 0:
        for output in unit["outputs"]:
            if not output.is_file() or output.resolve(strict=False) != output:
                error = f"declared result missing or uses symlink: {output}"
                break
            outputs[str(output)] = _sha256(output)
    result = {
        "schema": "theory-master/independent-unit-result/v1",
        "id": ident, "status": "complete" if code == 0 and not error else "failed",
        "attempt": attempt, "manifest_sha256": manifest_hash,
        "argv": unit["argv"], "cwd": str(unit["cwd"]),
        "inputs_sha256": unit["inputs"],
        "outputs_sha256": outputs if code == 0 and not error else {},
        "returncode": code, "error": error, "duration_seconds": round(time.time() - started, 3),
        "stdout_log": str(stdout), "stderr_log": str(stderr),
        "ended_unix": time.time(),
    }
    _atomic_json(state / "units" / f"{ident}.json", result)
    return result


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >=1")
    return number


def _nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be >=0")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="authorized Investigator cycle write-root")
    parser.add_argument("--manifest", required=True, help="JSON independent-unit manifest")
    parser.add_argument("--state-dir", default=None, help="within workspace; defaults to <manifest stem>.dispatch")
    parser.add_argument("--workers", default="auto", help="auto or explicit worker count")
    parser.add_argument("--max-workers", type=_positive, default=16)
    parser.add_argument("--threads-per-worker", type=_positive, default=1)
    parser.add_argument("--memory-mib-per-worker", type=_positive, default=1024)
    parser.add_argument("--disk-mib-per-worker", type=_positive, default=128)
    parser.add_argument("--reserve-memory-mib", type=_nonnegative, default=1024)
    parser.add_argument("--reserve-disk-mib", type=_nonnegative, default=2048)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retry-failed", action="store_true",
                        help="explicitly rerun failed unit directories with identical frozen inputs")
    args = parser.parse_args(argv)
    try:
        if args.workers != "auto" and (not args.workers.isdecimal() or int(args.workers) < 1):
            raise DispatchError("--workers must be auto or a positive integer")
        workspace = Path(args.workspace).expanduser().resolve(strict=True)
        if not workspace.is_dir():
            raise DispatchError("--workspace must be a directory")
        manifest = _relative_file(workspace, args.manifest, exists=True)
        if args.state_dir is not None:
            state = _relative_file(workspace, args.state_dir)
        else:
            state = manifest.parent / (manifest.stem + ".dispatch")
        if not _within(state, workspace) or state == workspace or state == manifest.parent:
            raise DispatchError("state-dir must be a dedicated directory within workspace")
        units, sources, manifest_hash = _load_manifest(manifest, workspace)
        if any(_within(state, u["cwd"]) or _within(u["cwd"], state) for u in units):
            raise DispatchError("state directory overlaps a job workspace")
        _verify_sources(sources)
        plan = _resource_plan(workspace, len(units), args)
        if args.dry_run:
            print(json.dumps({
                "status": "validated_dry_run", "manifest_sha256": manifest_hash,
                "units": len(units), "resource_plan": plan,
                "unit_ids": [u["id"] for u in units],
            }, ensure_ascii=False, indent=2))
            return 0
        if state.exists() and not (state / "manifest_binding.json").is_file():
            unexpected = [child for child in state.iterdir() if child.name != ".lock"]
            if unexpected:
                raise DispatchError("state-dir exists but is not a dispatcher state; refusing writes")
        state.mkdir(parents=True, exist_ok=True)
        with (state / ".lock").open("a+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DispatchError("dispatcher already running in this state directory") from exc
            old_meta = state / "manifest_binding.json"
            if old_meta.is_file():
                try:
                    old = json.loads(old_meta.read_text())
                except (OSError, json.JSONDecodeError) as exc:
                    raise DispatchError("corrupt prior manifest binding") from exc
                if old.get("manifest_sha256") != manifest_hash:
                    raise DispatchError("state-dir is bound to a different scientific manifest")
            else:
                _atomic_json(old_meta, {"schema": SCHEMA, "manifest_sha256": manifest_hash,
                                        "manifest": str(manifest), "workspace": str(workspace)})
            completed, pending = [], []
            for unit in units:
                record = _unit_record(state, unit, manifest_hash)
                if record and record.get("status") == "complete":
                    completed.append(unit["id"])
                elif record and record.get("status") == "failed" and not args.retry_failed:
                    raise DispatchError(f"{unit['id']}: previous failure; use --retry-failed")
                else:
                    if not record and any(path.exists() for path in unit["outputs"]):
                        raise DispatchError(f"{unit['id']}: existing outputs without certified state; refusing overwrite")
                    pending.append((unit, int(record.get("attempt", 0)) + 1 if record else 1))
            results = {}
            if pending:
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(plan["workers"], len(pending))) as pool:
                    futures = {
                        pool.submit(_execute_one, unit, state=state, manifest_hash=manifest_hash,
                                    threads=args.threads_per_worker, attempt=attempt,
                                    min_disk_bytes=(args.reserve_disk_mib + args.disk_mib_per_worker) * MiB
                                    ): unit["id"]
                        for unit, attempt in pending
                    }
                    for future in concurrent.futures.as_completed(futures):
                        results[futures[future]] = future.result()
            try:
                _verify_sources(sources)
                if _sha256(manifest) != manifest_hash:
                    raise DispatchError("manifest changed during execution; results are invalid")
            except DispatchError as exc:
                report = {"status": "invalid_input_changed", "error": str(exc),
                          "manifest_sha256": manifest_hash, "resource_plan": plan,
                          "completed_before": completed,
                          "attempts": results}
                _atomic_json(state / "summary.json", report)
                raise
            failed = sorted(name for name, result in results.items() if result["status"] != "complete")
            report = {
                "schema": "theory-master/independent-compute-summary/v1",
                "status": "failed" if failed else "complete",
                "manifest_sha256": manifest_hash,
                "total": len(units),
                "skipped_verified": len(completed),
                "executed_now": len(pending),
                "successful_now": sum(result["status"] == "complete" for result in results.values()),
                "failed": failed,
                "resource_plan": plan,
                "units": results,
                "scientific_verdict": None,
                "note": "process completion is engineering evidence only; science requires separate validation",
            }
            _atomic_json(state / "summary.json", report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if failed else 0
    except (DispatchError, OSError) as exc:
        print(f"DISPATCH ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
