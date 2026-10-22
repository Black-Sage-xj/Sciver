#!/usr/bin/env python3
"""Offline-only v1 Task Home data relocation; no science/Evidence rewriting.

Safe default is DRY RUN. --apply requires a quiescent runtime and a single
filesystem so rename + compatibility links preserve old absolute references.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from theory_master.runtime_manager import runtime_health
from theory_master.task import (
    LEGACY_PATH_MODEL, PATH_MODEL, SYSTEM_CONFIG_NAME, non_sync_data_area,
)


def tree_digest(root: Path) -> tuple[int, str]:
    sha = hashlib.sha256()
    count = 0
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
        if file.is_symlink():
            raise ValueError(f"nested symlink requires manual review: {file}")
        rel = file.relative_to(root).as_posix()
        sha.update(rel.encode("utf-8") + b"\0")
        sha.update(str(file.stat().st_size).encode("ascii") + b"\0")
        with file.open("rb") as handle:
            while part := handle.read(1024 * 1024):
                sha.update(part)
        count += 1
    return count, sha.hexdigest()


def validate_network_pairs(root: Path) -> None:
    for file in root.rglob("*"):
        if file.is_symlink() or not file.is_file() or file.suffix not in {".data", ".pickle"}:
            continue
        other = file.with_suffix(".pickle" if file.suffix == ".data" else ".data")
        if not other.is_file():
            raise ValueError(f"paired network asset absent; manual review required: {file} -> {other}")


def atomic_config_write(config_file: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=config_file.parent, prefix=".tm-path-migration-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, config_file)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def migrate(task_home: Path, *, apply: bool = False, allow_preexisting_unpaired_benchmark: bool = False) -> str:
    task_home = task_home.expanduser().resolve()
    config_file = task_home / SYSTEM_CONFIG_NAME
    original_text = config_file.read_text(encoding="utf-8")
    config = json.loads(original_text)
    if Path(config["plan_path"]).resolve().parent != task_home:
        raise ValueError("config plan/task home mismatch")
    if config.get("path_model", LEGACY_PATH_MODEL) == PATH_MODEL:
        return "ALREADY_V2 no changes"
    if config.get("path_model", LEGACY_PATH_MODEL) != LEGACY_PATH_MODEL:
        raise ValueError("unrecognized Task Path Model")
    old_research = task_home / ".theory-master" / "research"
    old_benchmark = task_home / "benchmark"
    if old_research.is_symlink() or old_benchmark.is_symlink():
        raise ValueError("preexisting alias/symlink: manual review before v1 migration")
    if not old_research.is_dir():
        raise ValueError(f"canonical runtime missing: {old_research}")
    health = runtime_health(old_research)
    if health.get("active") or str(health.get("state", "")).upper() in {"RUNNING", "PAUSED", "WAITING_QUOTA"}:
        return f"BLOCKED_ACTIVE_RUNTIME state={health.get('state')} root={old_research}; no files touched"
    # Fail closed for a live owner even if the lease record is stale or unreadable.
    for cycle in old_research.iterdir():
        state = cycle / "research_state.json"
        if not state.is_file():
            continue
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
            pid = int(data.get("owner_pid") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raise ValueError(f"unreadable Cycle state: {state}")
        if data.get("execution_active") and pid > 0:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                return f"BLOCKED_ACTIVE_CYCLE owner_pid={pid}; no files touched"
    data_area = non_sync_data_area(Path(config["plan_path"]), int(config["task_id"]), str(config["goal_id"]))
    destinations = [(old_research, data_area / "research")]
    if old_benchmark.is_dir():
        destinations.append((old_benchmark, data_area / "benchmark"))
    old_archive = task_home / ".theory-master" / "archive"
    if old_archive.is_dir():
        destinations.append((old_archive, data_area / "archive"))
    if any(dst.exists() or dst.is_symlink() for _, dst in destinations):
        raise ValueError("target data area not empty; refusing overwrite/partial re-run")
    for src, dst in destinations:
        ancestor = dst.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        if src.stat().st_dev != ancestor.stat().st_dev:
            raise ValueError(f"cross-filesystem move requires separate verified staging: {src}")
        if src == old_benchmark and allow_preexisting_unpaired_benchmark:
            # Explicitly approved legacy input-set exception: move the ENTIRE
            # original folder atomically without inventing any missing counterpart.
            # This does not split a pair or rename a single network asset.
            print("WARNING: moving preexisting unpaired benchmark assets without modification")
        else:
            validate_network_pairs(src)
    if not apply:
        return "DRY_RUN safe_to_migrate: " + "; ".join(f"{src} -> {dst}" for src, dst in destinations)
    data_area.parent.mkdir(parents=True, exist_ok=True)
    data_area.mkdir(parents=False, exist_ok=False)
    moved: list[tuple[Path, Path]] = []
    previous_config = original_text
    try:
        for src, dst in destinations:
            before = tree_digest(src)
            os.rename(src, dst)
            moved.append((src, dst))  # rollback must cover failures in symlink/digest
            # Absolute paths in historic scientific artifacts remain usable.
            src.symlink_to(os.path.relpath(dst, src.parent), target_is_directory=True)
            after = tree_digest(dst)
            if before != after:
                raise ValueError(f"content integrity mismatch: {src}")
        updated = dict(config)
        updated.update({
            "path_model": PATH_MODEL,
            "task_home": str(task_home),
            "research_root": str(data_area / "research"),
            "benchmark_root": str(data_area / "benchmark"),
        })
        atomic_config_write(config_file, updated)
        from theory_master.task import TaskBinding
        binding = TaskBinding.load(task_home)
        if binding.research_root != data_area / "research":
            raise ValueError("post-migration Task Binding root mismatch")
        if not (data_area / "research" / ".theory-master" / "task.json").is_file():
            raise ValueError("runtime discovery pointer missing")
        from theory_master.runtime_manager import update_registry
        updated_registry = update_registry(data_area / "research")
        if updated_registry.get("research_root") != str(data_area / "research"):
            raise ValueError("runtime registry root rebinding failed")
        return f"MIGRATED_V2 files_intact paths={len(moved)} data_area={data_area}"
    except BaseException:
        atomic_config_write(config_file, json.loads(previous_config))
        for src, dst in reversed(moved):
            if src.is_symlink():
                src.unlink()
            if not src.exists() and dst.exists():
                os.rename(dst, src)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-home", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="only when all runtime owners are stopped")
    parser.add_argument("--allow-preexisting-unpaired-benchmark", action="store_true",
                        help="explicit operator approval to relocate the ENTIRE benchmark tree, preserving already unpaired source assets unchanged")
    args = parser.parse_args()
    print(migrate(args.task_home, apply=args.apply,
                  allow_preexisting_unpaired_benchmark=args.allow_preexisting_unpaired_benchmark))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
