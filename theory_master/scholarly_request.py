"""Frozen retrieval requests and bounded local artifact receipts for safe reuse."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .scholarly_selection import _query_variants, _slug

REQUEST_SCHEMA = "theory-master/scholarly-request/v1"
ARTIFACT_SCHEMA = "theory-master/scholarly-artifacts/v1"
MAX_ARTIFACTS = 1024
MAX_ENTRIES = 4096
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024


def scholarly_request_identity(task: dict[str, Any]) -> dict[str, Any]:
    """Freeze every input used to choose, rank, or acquire scholarly sources."""

    def text(key, default=""):
        return str(task.get(key) or default).strip()

    gap = text("source_gap_id", text("gap_id", "external-gap"))
    kind = text("task_type", "targeted_source")
    lock = task.get("source_lock") if isinstance(task.get("source_lock"), dict) else {}
    lock = json.loads(json.dumps(lock, allow_nan=False))
    if kind == "targeted_source" and (
        lock.get("doi") or lock.get("arxiv_id") or lock.get("title")
    ):
        queries = [
            str(
                lock.get("doi") or lock.get("arxiv_id") or lock.get("title") or ""
            ).strip()
        ]
    else:
        queries = _query_variants(
            kind,
            gap,
            text("search_target"),
            text("new_search_lead"),
            text("domain_context"),
            task.get("search_queries"),
        )
    terms = task.get("relevance_terms")
    terms = (
        json.loads(json.dumps(terms, allow_nan=False))
        if isinstance(terms, list)
        else []
    )
    try:
        limit = max(1, min(100, int(task.get("max_candidates") or 60)))
    except (TypeError, ValueError, OverflowError):
        limit = 60
    request = {
        "source_gap_id": gap,
        "task_type": kind,
        "search_target": text("search_target"),
        "new_search_lead": text("new_search_lead"),
        "domain_context": text("domain_context"),
        "source_lock": lock,
        "search_queries": queries,
        "relevance_terms": terms,
        "max_candidates": limit,
    }
    encoded = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return {
        "schema": REQUEST_SCHEMA,
        "request": request,
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def retrieval_directory(workspace: Path, identity: dict[str, Any]) -> Path:
    """Each acquisition is append-only, including a retry after artifact drift."""
    request = identity["request"]
    parent = (
        Path(workspace).absolute()
        / "literature"
        / "sdk_retrieval"
        / _slug(request["source_gap_id"])
        / identity["sha256"]
    )
    if any(path.is_symlink() for path in (parent, *parent.parents)):
        raise ValueError("Scholarly retrieval directory cannot contain symlinks")
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="acquisition-", dir=parent))


def _root(transport: dict[str, Any], workspace: Path) -> Path:
    raw = str(transport.get("output_dir") or "")
    path = Path(raw)
    allowed = Path(workspace).absolute() / "literature" / "sdk_retrieval"
    if (
        not raw
        or not path.is_absolute()
        or ".." in path.parts
        or not path.is_relative_to(allowed)
        or any(part.is_symlink() for part in (path, *path.parents))
    ):
        raise ValueError(
            "Scholarly artifacts must remain inside their SDK retrieval workspace"
        )
    return path


def _inventory(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        raise ValueError("Scholarly artifact directory is missing")
    rows = []
    total = 0
    entries = 0
    paths = []
    directories = [root]
    while directories:
        with os.scandir(directories.pop()) as children:
            for entry in children:
                entries += 1
                if entries > MAX_ENTRIES or entry.is_symlink():
                    raise ValueError(
                        "Scholarly artifact inventory is unbounded or contains a symlink"
                    )
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    directories.append(path)
                else:
                    paths.append(path)
    for path in sorted(paths):
        if not path.is_file():
            raise ValueError("Scholarly artifact is not a regular file")
        before = path.stat()
        if before.st_size > MAX_FILE_BYTES:
            raise ValueError("Scholarly artifact exceeds its reuse byte bound")
        total += before.st_size
        if total > MAX_TOTAL_BYTES or len(rows) >= MAX_ARTIFACTS:
            raise ValueError("Scholarly artifact inventory exceeds its reuse bound")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                if size > MAX_FILE_BYTES or size > before.st_size:
                    raise ValueError("Scholarly artifact changed during hashing")
                digest.update(chunk)
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or size != before.st_size:
            raise ValueError("Scholarly artifact changed during hashing")
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": size,
                "sha256": digest.hexdigest(),
            }
        )
    return rows


def _navigation(transport: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: transport.get(key)
        for key in (
            "papers_json",
            "evidence_index_md",
            "papers_top_json",
            "paper_focus_json",
            "report_md",
            "topic_brief_md",
            "search_log_md",
            "errors_log",
            "metadata_quality_json",
        )
        if transport.get(key)
    }
    reading = (
        transport.get("reading") if isinstance(transport.get("reading"), dict) else {}
    )
    result["reading"] = {
        key: reading[key]
        for key in ("pdf_manifest", "reading_index")
        if reading.get(key)
    }
    result["traces"] = [
        {key: row[key] for key in ("report", "json") if row.get(key)}
        for row in transport.get("traces") or []
        if isinstance(row, dict)
    ]
    return result


def _navigation_paths(navigation: dict[str, Any]) -> list[Any]:
    return [
        *(
            value
            for key, value in navigation.items()
            if key not in {"reading", "traces"}
        ),
        *navigation["reading"].values(),
        *(value for row in navigation["traces"] for value in row.values()),
    ]


def pin_scholarly_transport(
    task: dict[str, Any], transport: dict[str, Any], workspace: Path
) -> dict[str, Any]:
    """Bind the SDK's returned corpus, without treating legacy files as attested."""
    result = json.loads(json.dumps(transport))
    result["request_identity"] = scholarly_request_identity(task)
    if result.get("status") == "test_stub":
        result["artifact_snapshot"] = {
            "schema": ARTIFACT_SCHEMA,
            "test_stub": True,
            "files": [],
        }
        return result
    try:
        root = _root(result, workspace)
        files = _inventory(root)
        papers = Path(str(result.get("papers_json") or ""))
        if (
            not papers.is_absolute()
            or not papers.is_relative_to(root)
            or not papers.is_file()
        ):
            raise ValueError("Scholarly papers index is outside its artifact inventory")
        navigation = _navigation(result)
        for value in _navigation_paths(navigation):
            path = Path(str(value))
            if (
                not path.is_absolute()
                or ".." in path.parts
                or not path.is_relative_to(root)
            ):
                raise ValueError(
                    "Scholarly navigation is outside its artifact inventory"
                )
        result["artifact_snapshot"] = {
            "schema": ARTIFACT_SCHEMA,
            "root": str(root),
            "files": files,
            "navigation": navigation,
        }
    except (OSError, ValueError) as exc:
        result.pop("artifact_snapshot", None)
        result["reuse_unavailable_reason"] = str(exc)
    return result


def reusable_scholarly_transport(
    transport: dict[str, Any], task: dict[str, Any], workspace: Path
) -> bool:
    """Require the exact frozen request and all original local artifact bytes."""
    if transport.get("request_identity") != scholarly_request_identity(task):
        return False
    snapshot = transport.get("artifact_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("schema") != ARTIFACT_SCHEMA:
        return False
    if transport.get("status") == "test_stub":
        return snapshot == {"schema": ARTIFACT_SCHEMA, "test_stub": True, "files": []}
    try:
        root = _root(transport, workspace)
        if snapshot.get("root") != str(root) or snapshot.get("files") != _inventory(
            root
        ):
            return False
        navigation = _navigation(transport)
        if snapshot.get("navigation") != navigation:
            return False
        files = {row["path"] for row in snapshot["files"]}
        for value in _navigation_paths(navigation):
            if not value:
                continue
            path = Path(str(value))
            if (
                not path.is_absolute()
                or ".." in path.parts
                or not path.is_relative_to(root)
                or (path.relative_to(root).as_posix() not in files and path.exists())
            ):
                return False
        return bool(transport.get("papers_json"))
    except (OSError, ValueError, KeyError, TypeError):
        return False
