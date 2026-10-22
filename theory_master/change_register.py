"""Lightweight modification record distinct from the real-Cycle audit queue.

Static/deterministically verifiable changes are logged here without creating
a future Cycle review obligation. A record is not proof of implementation.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .audit_queue import QueueError, _atomic_write, _nonempty

SCHEMA = "theory-master/change-register/v1"
DEFAULT_STORE = Path(__file__).resolve().parents[1] / "changes" / "change_register.json"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read(path: Path) -> dict:
    if not path.exists():
        return {"schema": SCHEMA, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QueueError(f"cannot read change register: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("items"), list):
        raise QueueError("invalid change register schema")
    identifiers = [x.get("id") for x in data["items"] if isinstance(x, dict)]
    if len(identifiers) != len(data["items"]) or len(identifiers) != len(set(identifiers)):
        raise QueueError("invalid or duplicate change records")
    return data


@contextmanager
def _edit(path: Path):
    import hashlib
    digest = hashlib.sha256(str(path.expanduser().resolve()).encode()).hexdigest()[:20]
    lock = Path(tempfile.gettempdir()) / f"tm-change-register-{digest}.lock"
    with lock.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            data = _read(path)
            yield data
            _atomic_write(path, data)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _add(args, data: dict) -> dict:
    title = _nonempty(args.title, "title")
    source = _nonempty(args.source, "source")
    source_tma = (args.source_tma or "").strip()
    if source_tma:
        for existing in data["items"]:
            if existing.get("source_tma") == source_tma:
                return existing  # Idempotent migration, do not make duplicates.
    else:
        for existing in data["items"]:
            if (not existing.get("source_tma") and existing["title"].casefold() == title.casefold()
                    and existing["source"] == source):
                return existing  # Stable record for the same logical change.
    now = _now()
    record = {
        "id": f"TMC-{datetime.now().astimezone():%Y%m%d}-{uuid.uuid4().hex[:12]}",
        "title": title,
        "source": source,
        "description": (args.description or "").strip(),
        "source_tma": source_tma,
        "legacy_audit_status": (args.legacy_status or "").strip(),
        "state": "registered",  # NOT automatically implemented or verified
        "verification": "",
        "created_at": now,
        "updated_at": now,
    }
    data["items"].append(record)
    return record


def build_parser():
    parser = argparse.ArgumentParser(description="Change history; not a research-cycle audit queue")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="record a static/deterministically verifiable change")
    add.add_argument("--title", required=True)
    add.add_argument("--source", required=True)
    add.add_argument("--description", default="")
    add.add_argument("--source-tma", default="", help="original audit id, for scope migration")
    add.add_argument("--legacy-status", default="")
    ls = sub.add_parser("list")
    ls.add_argument("--json", action="store_true")
    show = sub.add_parser("show")
    show.add_argument("id")
    show.add_argument("--json", action="store_true")
    verify = sub.add_parser("verify", help="record completed static test/review evidence")
    verify.add_argument("id")
    verify.add_argument("--evidence", required=True)
    verify.add_argument("--reviewer", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd in {"list", "show"}:
            items = _read(args.store)["items"]
            if args.cmd == "show":
                items = [x for x in items if x["id"] == args.id]
                if not items:
                    raise QueueError(f"unknown change: {args.id}")
            if args.json:
                print(json.dumps(items, ensure_ascii=False, indent=2))
            else:
                for x in items:
                    print(x["id"], x["state"], x["title"])
            return 0
        with _edit(args.store) as data:
            if args.cmd == "add":
                record = _add(args, data)
            else:
                record = next((x for x in data["items"] if x["id"] == args.id), None)
                if record is None:
                    raise QueueError(f"unknown change: {args.id}")
                record["verification"] = _nonempty(args.evidence, "evidence")
                record["reviewer"] = _nonempty(args.reviewer, "reviewer")
                record["state"] = "verified"
                record["updated_at"] = _now()
        print(record["id"], record["state"], record["title"])
        return 0
    except QueueError as exc:
        print(f"tm-change-register: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
