"""Explicit startup migration of registered Canvas-only tasks to SQLite.

Discovery and TaskSpec loading remain read-only. Publish only a completely
imported database, preserving the original Canvas and its hashed backup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .science_graph_store import ScienceGraphStore, import_legacy_canvas
from .task import TaskBinding


def upgrade_task_graph(binding: TaskBinding) -> dict:
    from .runtime_manager import (
        _file_lock, _reject_legacy_when_claimed, branch_claim_lock_path,
        launch_lock_path, owner_lock_path, runtime_health,
    )

    canvas = binding.branch_canvas.resolve()
    database = canvas.parent / "science_graph.sqlite3"
    with _file_lock(launch_lock_path(binding.research_root)), \
            _file_lock(branch_claim_lock_path(binding.research_root)), \
            _file_lock(owner_lock_path(binding.research_root), nonblocking=True):
        if database.exists():
            return {"status": "existing", "database": str(database)}
        _reject_legacy_when_claimed(binding.research_root)
        if runtime_health(binding.research_root).get("active"):
            raise RuntimeError("Stop the task runtime before upgrading its science graph")
        original = canvas.read_bytes()
        document = json.loads(original)
        if document.get("tm_science_graph_projection"):
            raise ValueError("Generated Canvas has lost its canonical database; restore SQLite from backup")
        digest = hashlib.sha256(original).hexdigest()
        with tempfile.TemporaryDirectory(prefix=".graph-upgrade-", dir=canvas.parent) as temp:
            staged = Path(temp) / database.name
            report = import_legacy_canvas(canvas, staged, create_backup=True)
            if Path(report["backup"]).read_bytes() != original:
                raise ValueError("Legacy Canvas backup does not match the source")
            with ScienceGraphStore(staged, readonly=True) as store:
                archive = store.db.execute(
                    "SELECT original_bytes,node_count FROM legacy_canvas_archive WHERE digest=?",
                    (digest,),
                ).fetchone()
                mapped = store.db.execute(
                    "SELECT COUNT(*) FROM legacy_conversion_map WHERE source_digest=?", (digest,),
                ).fetchone()[0]
                if archive is None or archive["original_bytes"] != original or mapped != archive["node_count"]:
                    raise ValueError("Legacy graph archive or conversion map is incomplete")
                if store.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Upgraded graph failed SQLite integrity check")
                known = {row[0] for row in store.db.execute("SELECT id FROM hypotheses")}
                for field in ("branch_anchor_id", "next_step_start_id"):
                    if binding.config.get(field) and binding.config[field] not in known:
                        raise ValueError(f"Configured {field} is not a migrated hypothesis")
            if canvas.read_bytes() != original:
                raise RuntimeError("Canvas changed during upgrade; database was not published")
            # link is atomic and refuses replacement even if another writer
            # created a canonical database without participating in our locks.
            with staged.open("rb") as handle:
                os.fsync(handle.fileno())
            os.link(staged, database)
        return {**report, "status": "upgraded", "database": str(database), "source_sha256": digest}


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up and upgrade a registered legacy task graph")
    parser.add_argument("task", type=Path)
    args = parser.parse_args()
    print(json.dumps(upgrade_task_graph(TaskBinding.load(args.task)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
