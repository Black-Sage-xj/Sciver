"""Explicit notebook/data bindings shared by the SDK and file readers."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

NOTEBOOK_SUFFIXES = {".md", ".canvas", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf",
                     ".mp3", ".mp4", ".wav", ".ogg", ".m4a", ".webm", ".txt", ".bib", ".excalidraw"}


def notebook_files(root: Path):
    """Enumerate portable documents at any user-chosen depth, without symlinks."""
    root = Path(root).resolve()
    for directory, dirs, names in os.walk(root, followlinks=False):
        folder = Path(directory)
        dirs[:] = [name for name in dirs if not name.startswith(".")
                   and not (folder / name).is_symlink()]
        for name in sorted(names):
            path = folder / name
            relative = path.relative_to(root)
            outcome_archive = (path.suffix.lower() == ".json" and relative.parts[0] == "notes"
                               and "outcomes" in relative.parts[1:-1])
            if (not name.startswith(".") and (path.suffix.lower() in NOTEBOOK_SUFFIXES or outcome_archive)
                    and not path.is_symlink()):
                yield path


def configured_vault_root(config: dict[str, Any]) -> Path:
    return Path(config.get("vault_root") or config.get("task_home")
                or Path(config["plan_path"]).parent).expanduser().resolve()


def notebook_config(root: Path) -> tuple[Path, dict[str, Any]] | None:
    """Resolve a registered notebook without trusting a file supplied over HTTP."""
    root = Path(root).resolve()
    marker = root / ".theory-master" / "task.json"
    if not marker.exists():
        return None
    if marker.is_symlink() or marker.resolve() != marker or marker.stat().st_size > 512 * 1024:
        raise ValueError("unsafe notebook binding")
    binding = json.loads(marker.read_text(encoding="utf-8"))
    if binding.get("schema") != "theory-master/task/v2":
        raise ValueError("invalid notebook binding")
    config_path = Path(binding["config_path"])
    if (not config_path.is_absolute() or config_path.is_symlink() or config_path.resolve() != config_path
            or config_path.stat().st_size > 512 * 1024):
        raise ValueError("unsafe notebook configuration")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema") != "theory-master/system/v1":
        raise ValueError("invalid notebook configuration")
    if root not in {configured_vault_root(config),
                    Path(config.get("task_home") or config_path.parent).resolve(),
                    Path(config["research_root"]).resolve()}:
        raise ValueError("notebook configuration does not bind this directory")
    return config_path.resolve(), config


def graph_database_path(root: Path) -> Path:
    registered = notebook_config(root)
    if registered:
        _, config = registered
        explicit = config.get("graph_database")
        if explicit:
            path = Path(explicit).expanduser()
            if not path.is_absolute():
                raise ValueError("graph_database must be absolute")
            if path.is_symlink():
                raise ValueError("graph_database must not be a symbolic link")
            return path.resolve()
        return Path(config["branch_canvas"]).resolve().parent / "science_graph.sqlite3"
    return Path(root).resolve() / "science_graph.sqlite3"


def atomic_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("unsafe metadata destination")
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".notebook-", delete=False) as stream:
            name = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
