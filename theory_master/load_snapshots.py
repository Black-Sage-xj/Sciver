"""Append-only task snapshots created before TheoryMaster loads mutable console state."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SNAPSHOT_SCHEMA = "theory-master/load-snapshot/v1"


def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "load")).strip("-.")
    return token or "load"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_load_snapshot(spec: Any, *, reason: str) -> Path:
    """Snapshot human console + Scientific Model before a load may mutate them.

    Snapshots are append-only directories under ``.theory-master/load-snapshots``.
    This function deliberately reads/copies the current files *without* calling
    Scientific Model migration/repair code first, so the pre-load state is exact.
    """
    research_root = Path(spec.research_root).expanduser().resolve()
    snapshot_root = research_root / ".theory-master" / "load-snapshots"
    snapshot_root.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    base = f"{stamp}--{_safe_token(reason)}"
    target = snapshot_root / base
    suffix = 1
    while target.exists():
        target = snapshot_root / f"{base}--{suffix:02d}"
        suffix += 1
    temp = snapshot_root / f".{target.name}.tmp-{os.getpid()}"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)

    sources: list[tuple[str, Path]] = []
    if getattr(spec, "config_path", None):
        sources.append(("theory-master.config.json", Path(spec.config_path)))
    if getattr(spec, "document", None):
        sources.append(("research_plan.md", Path(spec.document)))
    if getattr(spec, "research_root", None):
        sources.append(("scientific_model_state.json", Path(spec.research_root) / "scientific_model_state.json"))
    if getattr(spec, "branch_canvas", None):
        sources.append(("branch.canvas", Path(spec.branch_canvas)))

    copied: dict[str, dict[str, Any]] = {}
    scientific_model_version: int | None = None
    for name, source in sources:
        if not source.is_file():
            copied[name] = {"source": str(source), "present": False}
            continue
        destination = temp / name
        shutil.copy2(source, destination)
        copied[name] = {
            "source": str(source),
            "present": True,
            "sha256": _sha256(destination),
            "bytes": destination.stat().st_size,
        }
        if name == "scientific_model_state.json":
            try:
                payload = json.loads(destination.read_text(encoding="utf-8"))
                scientific_model_version = int(payload.get("version")) if payload.get("version") is not None else None
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                scientific_model_version = None

    manifest = {
        "schema": SNAPSHOT_SCHEMA,
        "snapshot_id": target.name,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "reason": str(reason),
        "research_root": str(research_root),
        "scientific_model_version": scientific_model_version,
        "files": copied,
    }
    (temp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.rename(target)
    return target
