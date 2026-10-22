from __future__ import annotations

"""Canonical SQLite graph authority guard used by the Canvas projection renderer."""

from pathlib import Path


class SQLiteGraphAuthorityMixin:
    def _graph_database_path(self) -> Path:
        """Require the task's canonical SQLite graph; never fall back to Canvas."""
        canvas = getattr(self, "branch_canvas", None) or getattr(
            getattr(self, "spec", None), "branch_canvas", None
        )
        if canvas is None:
            raise RuntimeError("Task configuration must define the Canvas projection path")
        graph_path = Path(canvas).resolve().parent / "science_graph.sqlite3"
        if not graph_path.is_file():
            raise FileNotFoundError(
                f"Canonical SQLite graph is required at {graph_path}; "
                "A canonical SQLite graph is required; Canvas-only runtime mode is not supported"
            )
        return graph_path
