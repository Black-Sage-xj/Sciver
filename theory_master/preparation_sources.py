"""Read-only navigation to a Task's frozen preparation source materials."""
from __future__ import annotations

import json
import re
import stat
from pathlib import Path, PurePosixPath


_MANIFEST_LIMIT = 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SNAPSHOT_FILES = {"conversation.json", "preparation.json", "research_plan.md"}


def _without_symlinks(path: Path) -> bool:
    return not any(item.is_symlink() for item in (path, *path.parents))


def preparation_source_anchors(task_home: Path) -> dict[str, str]:
    """Return stable local roots, without accepting sources as scientific evidence.

    Preparation-relative references retain their spelling in the reviewed Plan.
    Its files live beneath ``preparation_materials_root`` after formal creation.
    Only the supplied Task home is considered; no parent or sibling discovery is
    performed. The manifest is a navigation index, not an integrity attestation.
    """
    home = Path(task_home).expanduser().absolute()
    snapshot = home / "preparation_snapshot"
    manifest_path = snapshot / "manifest.json"
    try:
        if not _without_symlinks(manifest_path) or not snapshot.is_dir():
            return {}
        metadata = manifest_path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MANIFEST_LIMIT:
            return {}
        with manifest_path.open("rb") as stream:
            raw = stream.read(_MANIFEST_LIMIT + 1)
        if len(raw) > _MANIFEST_LIMIT:
            return {}
        manifest = json.loads(raw)
        if not isinstance(manifest, dict) or manifest.get("schema") != "sciver/preparation-snapshot/v1":
            return {}
        rows = manifest.get("files")
        if not isinstance(rows, list) or not rows:
            return {}
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                return {}
            name = row.get("path")
            if not isinstance(name, str) or not name or "\\" in name:
                return {}
            relative = PurePosixPath(name)
            if (not relative.parts or relative.is_absolute() or relative.as_posix() != name
                    or any(part.startswith(".") for part in relative.parts)
                    or (name not in _SNAPSHOT_FILES and relative.parts[0] != "materials")
                    or name in seen):
                return {}
            if (not isinstance(row.get("sha256"), str) or not _SHA256.fullmatch(row["sha256"])
                    or type(row.get("bytes")) is not int or row["bytes"] < 0):
                return {}
            source = snapshot.joinpath(*relative.parts)
            if not _without_symlinks(source) or not stat.S_ISREG(source.stat().st_mode):
                return {}
            seen.add(name)
        anchors = {
            "preparation_snapshot_root": str(snapshot),
            "preparation_manifest_path": str(manifest_path),
        }
        materials = snapshot / "materials"
        if materials.is_dir() and _without_symlinks(materials):
            anchors["preparation_materials_root"] = str(materials)
        return anchors
    except (OSError, ValueError, TypeError):
        return {}
