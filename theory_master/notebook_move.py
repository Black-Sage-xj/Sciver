"""Relocate notebook files while keeping task identity and computation data fixed."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from .notebook import atomic_json, configured_vault_root, graph_database_path, notebook_config, notebook_files


@contextmanager
def _destination_reservation(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    marker = destination / ".sciver-notebook-move"
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("目标目录正在被另一个任务使用") from exc
    try:
        os.close(fd)
        if any(p != marker for p in destination.iterdir()):
            raise ValueError("请选择空文件夹，系统不会覆盖已有文件")
        yield
    finally:
        marker.unlink(missing_ok=True)


def notebook_is_busy(research_root: Path) -> bool:
    from .runtime_manager import runtime_health
    from .worker_runtime import list_workers
    from .runtime_manager import process_identity_alive
    scheduler_path = research_root / ".theory-master" / "worker_scheduler.json"
    scheduler = json.loads(scheduler_path.read_text(encoding="utf-8")) if scheduler_path.is_file() else {}
    return bool(runtime_health(research_root).get("active")
                or process_identity_alive(scheduler.get("pid"), scheduler.get("start_ticks"))
                or any(w.get("process_alive") for w in list_workers(research_root)))


def move_notebook(root: Path, destination: Path) -> dict[str, Any]:
    root = Path(root).resolve(strict=True)
    raw_destination = Path(destination).expanduser()
    if not raw_destination.is_absolute() or raw_destination.is_symlink():
        raise ValueError("请选择绝对路径的目标文件夹")
    destination = raw_destination.resolve()
    if destination == root:
        return {"vault_root": str(root), "moved": False}
    registered = notebook_config(root)
    if not registered:
        raise ValueError("此任务尚未登记可迁移的笔记库")
    config_path, config = registered
    if configured_vault_root(config) != root:
        raise ValueError("请选择此任务当前登记的笔记库")
    research = Path(config["research_root"]).resolve()
    database = graph_database_path(root)
    from .task import PACKAGE_ROOT, write_binding
    forbidden = (root, research, PACKAGE_ROOT)
    if any(destination == p or destination.is_relative_to(p) or p.is_relative_to(destination)
           for p in forbidden):
        raise ValueError("目标文件夹必须与原笔记库、运行数据和程序目录分开")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("请选择空文件夹，系统不会覆盖已有文件")
    research_meta = research / ".theory-master"
    research_meta.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        for filename in ("launch.lock", "scheduler.lock", "canvas_write.lock"):
            lock = stack.enter_context((research_meta / filename).open("a+"))
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = notebook_config(root)
        if not current or current[0] != config_path or current[1] != config:
            raise ValueError("笔记库配置已变化，请刷新后重试")
        if notebook_is_busy(research):
            raise ValueError("请先暂停任务及 Worker 调度器，再移动笔记库")
        from .vault_canvas import canvas_bindings, replace_canvas_bindings, refresh_registered_canvases
        old_bindings = canvas_bindings(database)
        stack.enter_context(_destination_reservation(destination))
        files = list(notebook_files(root))
        obsidian = root / ".obsidian"
        if obsidian.exists():
            files.append(obsidian)
        for source in files:
            if source.is_symlink() or (source.is_dir() and any(p.is_symlink() for p in source.rglob("*"))):
                raise ValueError("请先处理笔记库中的符号链接，再移动")
        for name in ("markdown-publications.json", "external-publications.json"):
            publications = root / ".theory-master" / name
            if publications.is_file():
                files.append(publications)
        moved: list[tuple[Path, Path]] = []
        try:
            for source in files:
                target = destination / source.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                moved.append((source, target))
            updated = {**config, "vault_root": str(destination),
                       "graph_database": str(database),
                       "plan_path": str(destination / Path(config["plan_path"]).relative_to(root)),
                       "branch_canvas": str(destination / Path(config["branch_canvas"]).name),
                       "notes_dir": str(destination / Path(config["notes_dir"]).relative_to(root))}
            write_binding(destination, config_path)
            atomic_json(config_path, updated)
            bindings = []
            for binding in old_bindings:
                path = Path(binding["canvas_path"])
                if Path(binding["vault_root"]).resolve() != root:
                    raise ValueError("此任务登记了另一个笔记库，请先统一位置")
                bindings.append({**binding, "vault_root": str(destination),
                                 "canvas_path": str(destination / path.name)})
            replace_canvas_bindings(database, bindings)
        except BaseException:
            atomic_json(config_path, config)
            replace_canvas_bindings(database, old_bindings)
            for source, target in reversed(moved):
                source.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    shutil.move(str(target), str(source))
            marker = destination / ".theory-master" / "task.json"
            if marker.exists():
                marker.unlink()
            for directory in sorted((p for p in destination.rglob("*") if p.is_dir()),
                                    key=lambda p: len(p.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
        refresh_registered_canvases(database)
    return {"vault_root": str(destination), "graph_database": str(database), "moved": True}
