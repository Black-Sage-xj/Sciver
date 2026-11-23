"""Read-only review reports archived separately in each workspace's state directory."""
from __future__ import annotations

import json
import re
from pathlib import Path


_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,95}\Z")


def read_report(root: Path, slug: str) -> dict:
    if not _SLUG.fullmatch(slug):
        raise ValueError("invalid report id")
    archive = root / "reviews"
    folder = archive / slug
    source = folder / "report.json"
    if archive.is_symlink() or folder.is_symlink() or source.is_symlink():
        raise ValueError("invalid report directory")
    report = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or not isinstance(report.get("title"), str):
        raise ValueError("invalid review report")
    return report


def attachment(root: Path, slug: str, filename: str) -> bytes:
    report = read_report(root, slug)
    allowed = {item["file"] for item in report.get("details", [])}
    if filename not in allowed or Path(filename).name != filename:
        raise ValueError("unknown attachment")
    path = root / "reviews" / slug / filename
    if path.is_symlink():
        raise ValueError("invalid attachment")
    return path.read_bytes()


def page_body(root: Path, slug: str = "") -> str:
    from .review_reports_ui import archive_body, report_body

    if not slug:
        entries = []
        for path in sorted((root / "reviews").glob("*/report.json"), reverse=True):
            try:
                entries.append((path.parent.name, read_report(root, path.parent.name)))
            except (OSError, ValueError, KeyError):
                continue
        return archive_body(entries)
    report = read_report(root, slug)
    return report_body(slug, report, lambda name: attachment(root, slug, name))
