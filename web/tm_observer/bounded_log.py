"""Read a bounded suffix without loading a task's historical transcript."""
from __future__ import annotations

import os
import stat
from pathlib import Path

MAX_TAIL_BYTES = 262_144


def tail_lines(path: Path, *, limit: int = 300, max_bytes: int = MAX_TAIL_BYTES) -> list[str]:
    if limit <= 0 or max_bytes <= 0:
        return []
    budget = min(MAX_TAIL_BYTES, int(max_bytes))
    try:
        if path.is_symlink() or not path.is_file():
            return []
        with path.open("rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return []
            stream.seek(0, 2)
            size = stream.tell()
            start = max(0, size - budget)
            # Inspect the preceding byte so a complete first line is preserved.
            if start:
                stream.seek(start - 1)
                boundary = stream.read(1) == b"\n"
            else:
                boundary = True
            stream.seek(start)
            raw = stream.read(budget)
        if not boundary:
            first_break = raw.find(b"\n")
            raw = raw[first_break + 1:] if first_break >= 0 else b""
        return raw.decode("utf-8", errors="replace").splitlines()[-min(10_000, int(limit)):]
    except (OSError, ValueError):
        return []
