"""SDK-owned invocation artifact manifests.

Scientific Agents report scientific findings, not filesystem bookkeeping.  This
module records task-local file changes at an Agent invocation boundary so
reproducibility/audit metadata remains available without putting paths into the
Investigator or Judge contracts.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime import now, write_json

MANIFEST_SCHEMA = "theory-master/invocation-artifacts/v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Return a cheap pre/post fingerprint for regular task-local files.

    Full content hashes are intentionally computed only for files detected as
    created/modified; large immutable benchmark inputs should not be re-hashed on
    every Agent turn merely to discover that they did not change.
    """
    root = root.resolve()
    rows: dict[str, tuple[int, int]] = {}
    if not root.is_dir():
        return rows
    for path in root.rglob("*"):
        try:
            if not path.is_file() or path.is_symlink():
                continue
            stat = path.stat()
            rows[path.relative_to(root).as_posix()] = (int(stat.st_size), int(stat.st_mtime_ns))
        except (OSError, ValueError):
            continue
    return rows


@dataclass
class InvocationArtifactTracker:
    cycle_dir: Path
    workspace: Path
    invocation_id: str
    role: str
    stage: str
    before: dict[str, tuple[int, int]]
    _finalized: bool = False
    _manifest_path: Path | None = None

    @classmethod
    def start(cls, *, cycle_dir: Path, workspace: Path, invocation_id: str,
              role: str, stage: str) -> "InvocationArtifactTracker":
        return cls(
            cycle_dir=cycle_dir.resolve(), workspace=workspace.resolve(),
            invocation_id=invocation_id, role=role, stage=stage,
            before=_snapshot(workspace),
        )

    def finalize(self, *, status: str) -> Path:
        """Persist one idempotent manifest for this invocation."""
        if self._finalized and self._manifest_path is not None:
            return self._manifest_path
        after = _snapshot(self.workspace)
        created = sorted(set(after) - set(self.before))
        deleted = sorted(set(self.before) - set(after))
        modified = sorted(
            path for path in set(after) & set(self.before)
            if after[path] != self.before[path]
        )

        def describe(relative: str) -> dict[str, Any]:
            path = (self.workspace / relative).resolve()
            stat = path.stat()
            return {
                "path": str(path),
                "relative_path": relative,
                "size": int(stat.st_size),
                "sha256": _sha256(path),
            }

        rows_created: list[dict[str, Any]] = []
        rows_modified: list[dict[str, Any]] = []
        for relative, target in ((item, rows_created) for item in created):
            try:
                target.append(describe(relative))
            except OSError:
                continue
        for relative, target in ((item, rows_modified) for item in modified):
            try:
                target.append(describe(relative))
            except OSError:
                continue
        payload = {
            "schema": MANIFEST_SCHEMA,
            "invocation_id": self.invocation_id,
            "role": self.role,
            "stage": self.stage,
            "workspace": str(self.workspace),
            "status": status,
            "created": rows_created,
            "modified": rows_modified,
            "deleted": deleted,
            "captured_at": now(),
        }
        path = self.cycle_dir / "invocation_artifacts" / f"{self.invocation_id}.json"
        write_json(path, payload)
        self._finalized = True
        self._manifest_path = path
        return path
