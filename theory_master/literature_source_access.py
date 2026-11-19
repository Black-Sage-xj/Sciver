"""Deterministic source and quotation binding for Cycle Literature Evidence."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

from .scholarly_selection import _identity_helpers

PROTOCOL = "theory-master/literature-source-access/v1"
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_INDEX_ROWS = 1000
MAX_QUOTE_CHARS = 64 * 1024


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _doi(value: Any) -> str:
    return _identity_helpers().doi_key(value)


def _arxiv(value: Any) -> str:
    return _identity_helpers().arxiv_key(value)


def _read(value: Any, root: Path, hashes: dict[str, str], *, relative_to: Path | None = None) -> tuple[Path, bytes]:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("SDK source path is missing")
    path = Path(raw)
    if ".." in path.parts:
        raise ValueError("SDK source path contains a parent traversal")
    if not path.is_absolute():
        path = (relative_to or root) / path
    if (not path.is_relative_to(root)
            or any(part.is_symlink() for part in (path, *path.parents))
            or not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES):
        raise ValueError("SDK source must be a bounded regular file inside its retrieval workspace")
    with path.open("rb") as stream:
        blob = stream.read(MAX_SOURCE_BYTES + 1)
    if len(blob) > MAX_SOURCE_BYTES:
        raise ValueError("SDK source exceeds its byte bound")
    hashes[str(path)] = hashlib.sha256(blob).hexdigest()
    return path, blob


def resolve_paper_reference(source_id: Any, papers: list[dict]) -> dict:
    """Resolve an explicit index or exactly one compatible bibliographic record."""
    wanted = _text(source_id)
    if wanted.startswith("paper:"):
        try:
            index = int(wanted.split(":", 1)[1])
        except ValueError:
            return {}
        return papers[index] if 0 <= index < len(papers) else {}
    doi, arxiv = _doi(wanted), _arxiv(wanted)
    wanted_title = _identity_helpers().title_key(wanted)
    matches = []
    for paper in papers:
        if doi:
            match = doi in {_doi(paper.get(key)) for key in ("doi", "url", "id")}
        elif arxiv:
            match = arxiv in {_arxiv(paper.get(key)) for key in ("arxiv_id", "url", "id")}
        else:
            match = bool(wanted and wanted.casefold() in {
                _text(paper.get(key)).casefold() for key in ("id", "doi", "url", "arxiv_id")}
                or wanted_title and wanted_title == _identity_helpers().title_key(paper.get("title")))
        if match:
            matches.append(paper)
    return matches[0] if len(matches) == 1 else {}


def _load_papers(transport: dict, workspace: Path | None, hashes: dict) -> tuple[list[dict], Path]:
    value = transport.get("papers_json")
    if workspace is not None:
        root = Path(workspace).absolute() / "literature" / "sdk_retrieval"
    else:
        # Legacy callers may enrich metadata from an explicit local index. This
        # loader grants no verified access; the Registry still requires SDK roots.
        value = Path(str(value or "")).absolute()
        root = value.parent
    path, blob = _read(value, root, hashes)
    papers = json.loads(blob)
    if not isinstance(papers, list) or len(papers) > MAX_INDEX_ROWS or any(not isinstance(row, dict) for row in papers):
        raise ValueError("SDK papers index is not a bounded record list")
    return papers, path


def load_transport_papers(transport: dict, *, workspace: Path | None = None) -> list[dict]:
    """Bound metadata enrichment before closure; access authority is separate."""
    return _load_papers(transport, workspace, {})[0]


def _reading_matches(paper: dict, row: dict, papers: list[dict]) -> bool:
    # A conflicting DOI/arXiv is never repaired by an equal title or empty field.
    identity = _identity_helpers()
    declared = {"doi": _doi(row.get("doi")) or _doi(row.get("key")) or _doi(row.get("url")),
                "arxiv_id": _arxiv(row.get("arxiv_id")) or _arxiv(row.get("key")) or _arxiv(row.get("url")),
                "title": row.get("title") or row.get("paper")}
    if declared["doi"] or declared["arxiv_id"]:
        return identity.same_paper(paper, declared)
    title = identity.title_key(declared["title"])
    if not title or title != identity.title_key(paper.get("title")) or sum(
            identity.title_key(item.get("title")) == title for item in papers) != 1:
        return False
    year, authors = row.get("year"), row.get("authors") or row.get("author")
    if year:
        declared["year"] = int(year)
    if authors:
        declared["authors"] = json.loads(authors) if isinstance(authors, str) and authors.startswith("[") else (
            [authors] if isinstance(authors, str) else authors)
        if not isinstance(declared["authors"], list):
            return False
    if year or authors:
        return identity.same_paper(paper, declared)
    # Legacy title-only manifests are usable only when the unique indexed
    # source itself has a strong identity. A shared empty field never binds it.
    return bool(_doi(paper.get("doi")) or _arxiv(paper.get("arxiv_id")))


def assess_source_access(declared: str, source_id: str, transport: dict, item: dict, *, workspace: Path) -> dict:
    """Attest source identity, accessible bytes and a located quote, never science."""
    result = {"protocol": PROTOCOL, "declared": declared, "verified": "unavailable",
              "verification_status": "unverified", "source_id": source_id,
              "quote_located": False, "locator_semantically_checked": False}
    if declared == "unavailable":
        result["verification_status"] = "verified"
        return result
    hashes: dict[str, str] = {}
    try:
        if declared not in {"metadata", "abstract", "full_text"}:
            raise ValueError("Unknown Literature access declaration")
        quote = _text(item.get("exact_quote"))
        if not quote or len(quote) > MAX_QUOTE_CHARS:
            raise ValueError("Literature exact_quote is absent or exceeds its bound")
        root = Path(workspace).absolute() / "literature" / "sdk_retrieval"
        papers, papers_path = _load_papers(transport, workspace, hashes)
        paper = resolve_paper_reference(source_id, papers)
        if not paper:
            raise ValueError("Literature source identity is absent or ambiguous in SDK retrieval")
        result["retrieval_ref"] = {"papers_json": str(papers_path), "source_id": source_id}
        if declared == "metadata":
            text = json.dumps({key: val for key, val in paper.items() if key not in {"abstract", "abstract_variants"}}, ensure_ascii=False)
        elif declared == "abstract":
            variants = paper.get("abstract_variants") or []
            if not isinstance(variants, list) or len(variants) > MAX_INDEX_ROWS:
                raise ValueError("SDK abstract variants exceed their structural bound")
            texts = [str(paper.get("abstract") or "")]
            texts.extend(row["text"] for row in variants if isinstance(row, dict) and isinstance(row.get("text"), str))
            text = next((text for text in texts if quote in _text(text)), "")
        else:
            reading = transport.get("reading") or {}
            if not isinstance(reading, dict):
                raise ValueError("SDK reading manifest is missing")
            manifest_path, blob = _read(reading.get("pdf_manifest"), root, hashes)
            rows = []
            for row in csv.DictReader(io.StringIO(blob.decode("utf-8-sig"))):
                rows.append(row)
                if len(rows) > MAX_INDEX_ROWS:
                    raise ValueError("SDK reading manifest exceeds its row bound")
            matches = [(index, row) for index, row in enumerate(rows, 1)
                       if row.get("status") == "read" and _reading_matches(paper, row, papers)]
            if len(matches) != 1:
                raise ValueError("No unique reading record establishes this exact source identity")
            index, row = matches[0]
            target = row.get("markdown_path") or row.get("text_path") or manifest_path.parent / f"paper-{index:02d}.md"
            path, blob = _read(target, root, hashes, relative_to=manifest_path.parent)
            text = blob.decode("utf-8")
            result["reading_ref"] = {"pdf_manifest": str(manifest_path), "key": str(row.get("key") or source_id),
                                     "markdown_path": str(path)}
        if quote not in _text(text):
            raise ValueError("Literature exact_quote was not located in the declared SDK source")
        result.update(verified=declared, verification_status="verified", quote_located=True,
                      artifact_hashes=hashes)
    except (OSError, ValueError, TypeError, UnicodeError, csv.Error, RecursionError, RuntimeError) as exc:
        result["reason"] = str(exc)[:1000]
    return result
