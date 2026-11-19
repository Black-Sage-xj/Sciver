"""Run the existing Literature role outside a scientific Cycle.

The SDK schedules discovery, bounded source acquisition and successive Literature
stages.  Its receipts describe execution and source access, never a scientific
verdict.  A caller supplies a dedicated job directory, so preparation and Chat
can share the same role, contracts and transport without creating a Task.
"""
from __future__ import annotations

from contextlib import contextmanager
import csv
from dataclasses import replace
import fcntl
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterator
import uuid

from .backend_config import build_model_profile, read_model_profile, read_model_provider
from .contract_closure import close_agent_output
from .contracts import role_contract
from .literature_delivery_integrity import (
    LiteratureDeliveryIntegrityError, capture_completed_delivery, capture_invocation_delivery,
    capture_transport_delivery, validate_saved_invocation, verify_completed_delivery,
    verify_invocation_delivery, verify_transport_delivery,
)
from .output_normalization import normalize_agent_output
from .prompt_stages import build_continuation_prompt, build_initial_prompt, stage_context
from .runtime import atomic, now, write_json
from .scholarly_selection import _identity_helpers, _metadata_issue
from .skills import developer_instructions, role_skills, write_rule
from .stage_registry import stage_for


SCHEMA = "sciver/literature-job/v1"
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_IMPORT_BYTES = 96 * 1024 * 1024
MAX_AGENT_ATTEMPTS = 2
MAX_RETRIEVAL_ATTEMPTS = 2


class LiteratureServiceError(ValueError):
    pass


class LiteratureTransportError(LiteratureServiceError):
    pass


class _Paused(Exception):
    pass


def _retryable_invocation_error(error: Exception) -> bool:
    """Use the planner's bounded transport retry classification, never content."""
    from .codex_errors import CodexProviderRequestRejected, CodexTurnCancelled
    from .provider_errors import ProviderQuotaExhausted
    from .workflow_support import RecoveryStop
    if isinstance(error, (RecoveryStop, ProviderQuotaExhausted, CodexProviderRequestRejected, CodexTurnCancelled)):
        return False
    return isinstance(error, (ConnectionError, TimeoutError)) or bool(re.search(
        r"transient|temporar|reconnect|connection|timed?\s*out|\b50[234]\b", str(error), re.I
    ))


def _json(path: Path, default: Any = None) -> Any:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_ARTIFACT_BYTES:
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _child(root: Path, path: str | Path, *, file: bool = False) -> Path:
    value = Path(path)
    value = value if value.is_absolute() else root / value
    if value != value.resolve() or not value.is_relative_to(root) or value.is_symlink():
        raise LiteratureServiceError("Literature artifact escapes its dedicated workspace")
    if file and (not value.is_file() or value.stat().st_size > MAX_ARTIFACT_BYTES):
        raise LiteratureServiceError("Literature artifact is absent or exceeds the size limit")
    return value


@contextmanager
def _locked(workspace: Path) -> Iterator[Path]:
    root = Path(workspace).expanduser()
    if not root.is_absolute() or root != root.resolve():
        raise LiteratureServiceError("Literature needs an absolute, unlinked job workspace")
    root.mkdir(parents=True, exist_ok=True)
    if any((root / name).exists() for name in (
        "theory-master.config.json", "science_graph.sqlite3", "research_plan.md", "cycle_state.json",
    )):
        raise LiteratureServiceError("Use a separate Literature job, not a scientific Task/Cycle directory")
    path = _child(root, ".literature-job.lock")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LiteratureServiceError("This Literature job is already running") from exc
        yield root


def _request(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LiteratureServiceError("Literature request must be an object")
    query = value.get("query", value.get("search_target", ""))
    scope = value.get("scope", value.get("domain_context", ""))
    if not isinstance(query, str) or not query.strip() or len(query) > 6000 or "\0" in query:
        raise LiteratureServiceError("Literature query is empty or too long")
    if not isinstance(scope, str) or len(scope) > 12000 or "\0" in scope:
        raise LiteratureServiceError("Literature scope is invalid")
    result: dict[str, Any] = {"query": query.strip(), "scope": scope.strip()}
    for key, default, upper in (("max_rounds", 2, 4), ("max_sources", 6, 12)):
        item = value.get(key, default)
        if isinstance(item, bool) or not isinstance(item, int) or item < 1 or item > upper:
            raise LiteratureServiceError(f"{key} must be an integer from 1 to {upper}")
        result[key] = item
    manifests = value.get("retrieval_manifests", [])
    if not isinstance(manifests, list) or len(manifests) > 8 or any(not isinstance(item, str) for item in manifests):
        raise LiteratureServiceError("retrieval_manifests must contain at most eight SDK manifest paths")
    result["retrieval_manifests"] = manifests
    for key in ("provider", "model_profile", "model_provider"):
        if value.get(key):
            if not isinstance(value[key], str) or len(value[key]) > 100:
                raise LiteratureServiceError(f"Invalid {key}")
            result[key] = value[key]
    return result


def _stable(paper: dict[str, Any]) -> str:
    identity = _identity_helpers()
    doi = identity.doi_key(paper.get("doi"))
    if doi:
        return "doi:" + doi
    arxiv = identity.arxiv_key(paper.get("arxiv_id"))
    if arxiv:
        return "arxiv:" + arxiv
    authors = sorted(str(value).casefold() for value in paper.get("authors") or [])
    extra = hashlib.sha256(json.dumps([paper.get("year"), authors], sort_keys=True).encode()).hexdigest()[:12]
    return "title:" + identity.title_key(paper.get("title")) + ":" + extra


def _relocate(value: Any, old: Path, new: Path) -> Any:
    if isinstance(value, str):
        return str(new) + value[len(str(old)):] if value == str(old) or value.startswith(str(old) + "/") else value
    if isinstance(value, list):
        return [_relocate(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _relocate(item, old, new) for key, item in value.items()}
    return value


def _transport_paths(root: Path, value: dict[str, Any]) -> dict[str, Any]:
    """Validate only declared local navigation fields, never remote source URLs."""
    if not isinstance(value, dict):
        raise LiteratureServiceError("Scholarly transport did not return an object")
    result = json.loads(json.dumps(value))
    for key in ("output_dir", "retrieval_dir"):
        if result.get(key):
            _child(root, result[key])
    for key in ("papers_json", "evidence_index_md", "papers_top_json", "paper_focus_json",
                "report_md", "topic_brief_md", "search_log_md", "errors_log"):
        if result.get(key):
            _child(root, result[key])
    reading = result.get("reading") if isinstance(result.get("reading"), dict) else {}
    for key in ("pdf_manifest", "reading_index"):
        if reading.get(key):
            _child(root, reading[key])
    for row in result.get("traces") or []:
        if isinstance(row, dict):
            for key in ("report", "json"):
                if row.get(key):
                    _child(root, row[key])
    return result


def _conversion_capability() -> dict[str, Any]:
    from .document_conversion import load_settings
    try:
        settings = load_settings()
    except (OSError, ValueError) as exc:
        return {"enabled": False, "available": False, "provider": "unavailable",
                "availability_reason": type(exc).__name__}
    return {key: settings.get(key) for key in ("enabled", "provider", "available", "max_pages", "extract_images")}


def _import_manifest(root: Path, path_text: str, number: int) -> dict[str, Any]:
    path = Path(path_text)
    if not path.is_absolute() or path != path.resolve() or path.is_symlink():
        raise LiteratureServiceError("Reused literature manifest must be an actual SDK file")
    manifest = _json(path)
    if not isinstance(manifest, dict) or manifest.get("schema") != "sciver/preparation-literature/v1":
        raise LiteratureServiceError("Only SDK preparation retrieval manifests may be reused")
    if manifest.get("status") == "blocked" or (manifest.get("transport") or {}).get("status") == "blocked" or (manifest.get("transport") or {}).get("transport_ok") is False:
        raise LiteratureTransportError("Reused Literature retrieval is blocked; connector failure cannot be used as source material")
    old = path.parent
    transport = _transport_paths(old, manifest.get("transport"))
    rows = manifest.get("artifacts")
    if not isinstance(rows, list) or len(rows) > 300:
        raise LiteratureServiceError("Reused SDK manifest has no bounded artifact inventory")
    destination = _child(root, f"imports/{number:02d}")
    destination.mkdir(parents=True, exist_ok=True)
    total = 0
    copied: set[Path] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise LiteratureServiceError("Invalid reused artifact inventory")
        # Preparation manifests use paths relative to the preparation root.
        relative = Path(str(row.get("path") or ""))
        candidates = [old / relative, *[ancestor / relative for ancestor in old.parents[:3]]]
        source = next((candidate for candidate in candidates if candidate.is_file() and candidate.is_relative_to(old)), None)
        if source is None:
            raise LiteratureServiceError("A reused SDK artifact is missing")
        source = _child(old, source, file=True)
        data = source.read_bytes()
        total += len(data)
        if total > MAX_IMPORT_BYTES or hashlib.sha256(data).hexdigest() != row.get("sha256"):
            raise LiteratureServiceError("Reused SDK artifact hash or size does not match")
        target = _child(destination, source.relative_to(old))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        copied.add(source)
    papers = Path(str(transport.get("papers_json") or ""))
    if papers not in copied:
        raise LiteratureServiceError("Reused SDK manifest does not attest its papers index")
    imported = _relocate(transport, old, destination)
    # Rewrite local paths in JSON/Markdown navigation, not binary originals.
    for target in destination.rglob("*"):
        if target.suffix in {".json", ".md", ".csv"} and target.is_file():
            text = target.read_text(encoding="utf-8", errors="replace")
            target.write_text(text.replace(str(old), str(destination)), encoding="utf-8")
    return imported


def _catalog(root: Path, transports: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Build one bounded navigation corpus; text availability is not claim verification."""
    identity = _identity_helpers()
    papers: list[dict[str, Any]] = []
    lookup: dict[str, dict[str, Any]] = {}
    bindings: list[tuple] = []
    rejected_metadata: list[dict] = []
    for transport in transports:
        _transport_paths(root, transport)
        rows = _json(Path(str(transport.get("papers_json") or "")), [])
        if not isinstance(rows, list):
            continue
        accepted = []
        for row in rows[:1000]:
            issue = _metadata_issue(row)
            if issue:
                rejected_metadata.append({"title": str(row.get("title") or "")[:200] if isinstance(row, dict) else "",
                    "reason": issue, "source": str(transport.get("papers_json") or "")})
            else:
                accepted.append(row)
        rows = accepted
        reading = transport.get("reading") if isinstance(transport.get("reading"), dict) else {}
        manifest = Path(str(reading.get("pdf_manifest") or ""))
        reads: list[dict[str, Any]] = []
        if manifest.is_file():
            _child(root, manifest, file=True)
            with manifest.open(encoding="utf-8-sig", newline="") as stream:
                reads = list(csv.DictReader(stream))
        for paper in rows:
            key = _stable(paper)
            found = lookup.get(key)
            if found is None:
                # Shared scholar identity rejects conflicting DOI/arXiv IDs.
                # A weak row that matches several identities cannot bridge them.
                matches = [row for row in papers if identity.same_paper(row, paper)]
                found = matches[0] if len(matches) == 1 else None
            if found is None:
                found = dict(paper)
                papers.append(found)
                if len(papers) > 2000:
                    raise LiteratureServiceError("Literature navigation corpus exceeds its bounded size")
            else:
                merged = identity.merge_paper(found, paper)
                for field, item in paper.items():
                    if field not in merged and item not in (None, "", [], {}):
                        merged[field] = item
                found.clear()
                found.update(merged)
            lookup[key] = found
            lookup[_stable(found)] = found
            bindings.append((dict(paper), found, rows, reads, manifest))
    sources: dict[str, dict[str, Any]] = {}
    for paper in papers:
        key = _stable(paper)
        sources[key] = {"source_id": key, "title": str(paper.get("title") or ""),
            "doi": str(paper.get("doi") or ""), "url": str(paper.get("url") or ""),
            "year": paper.get("year"), "authors": paper.get("authors") or [],
            "arxiv_id": paper.get("arxiv_id") or "",
            "reading_level": "abstract" if str(paper.get("abstract") or "").strip() else "metadata",
            "text_path": "", "claim_verification": "not_reviewed", "origin": "sdk_retrieval"}
    for raw_paper, paper, rows, reads, manifest in bindings:
        source = sources[_stable(paper)]
        for index, record in enumerate(reads, 1):
            if record.get("status") != "read":
                continue
            declared = {"doi": identity.doi_key(record.get("doi")) or identity.doi_key(record.get("key")) or identity.doi_key(record.get("url")),
                        "arxiv_id": identity.arxiv_key(record.get("arxiv_id")) or identity.arxiv_key(record.get("key")),
                        "title": record.get("title") or record.get("paper")}
            strong = bool(declared["doi"] or declared["arxiv_id"])
            if strong:
                if not identity.same_paper(paper, declared):
                    continue
            else:
                title = identity.title_key(declared["title"])
                candidates = [row for row in rows if title and identity.title_key(row.get("title")) == title]
                if len(candidates) != 1 or identity.title_key(raw_paper.get("title")) != title:
                    continue
                if not (identity.doi_key(raw_paper.get("doi")) or identity.arxiv_key(raw_paper.get("arxiv_id"))):
                    # Legacy title-only reading manifests cannot disambiguate
                    # later provider identities sharing that title.
                    global_matches = [row for row in papers if identity.title_key(row.get("title")) == title]
                    if len(global_matches) != 1:
                        continue
            target = record.get("text_path") or record.get("markdown_path") or str(manifest.parent / f"paper-{index:02d}.md")
            text_path = _child(root, target)
            if text_path.is_file() and text_path.stat().st_size <= MAX_ARTIFACT_BYTES and text_path.read_text(encoding="utf-8").strip():
                for field in ("conversion_manifest", "converted_markdown_path"):
                    if record.get(field):
                        _child(root, record[field])
                source.update({"reading_level": "full_text", "text_path": str(text_path),
                               "materialization": {"pdf_manifest": str(manifest), "record": record,
                                   "notice": "Available text may contain only a configured page range; materialization is not scientific verification."}})
    home = _child(root, "corpus")
    retrieval = _child(home, "retrieval")
    retrieval.mkdir(parents=True, exist_ok=True)
    write_json(retrieval / "papers.json", papers)
    write_json(retrieval / "papers_top.json", papers[:6])
    lock = transports[-1].get("source_lock") if transports else None
    focused = [paper for paper in papers if identity.same_paper(lock, paper)] if isinstance(lock, dict) and lock else []
    write_json(retrieval / "paper_focus.json", focused or papers[:6])
    source_rows = list(sources.values())
    write_json(home / "access_levels.json", source_rows)
    write_json(home / "rejected_metadata.json", rejected_metadata)
    capability = _conversion_capability()
    write_json(home / "document_conversion.json", capability)
    reading = _child(home, "reading")
    reading.mkdir(exist_ok=True)
    navigation = ["# Available reading material", "", "Read the relevant body file; availability is not scientific verification.", ""]
    for source in source_rows:
        if source.get("text_path"):
            navigation.append(f"- {source['title']} | {source.get('doi') or source.get('arxiv_id')} | {source['text_path']}")
    if len(navigation) == 4:
        navigation.append("No readable body has been materialized yet. Use metadata/abstract access levels in the retrieval index.")
    atomic(reading / "reading_index.md", "\n".join(navigation) + "\n")
    lines = ["# Literature corpus navigation", "", "SDK access levels describe available material, not verified scientific claims.",
             "The full_text access level means readable body text is available; it does not certify the complete paper was acquired or read.",
             "PDF component: " + json.dumps(capability, ensure_ascii=False) + ". Conversion may be disabled or unavailable; do not install or invoke a converter yourself.",
             "Do not load the complete papers.json at once. Use this index, then bounded relevant records/passages.", ""]
    for index, paper in enumerate(papers):
        source = sources.get(_stable(paper), {})
        lines.append(f"- paper:{index}: {paper.get('title', '')} | DOI: {paper.get('doi', '')} | access: {source.get('reading_level', 'metadata')}")
        if source.get("text_path"):
            lines.append(f"  Readable source: {source['text_path']}")
            record = (source.get("materialization") or {}).get("record") or {}
            pages = str(record.get("max_pages") or "")
            if pages and pages.isdigit() and int(pages) > 0:
                lines.append(f"  Reading scope: only the first {pages} pages were requested; actual page coverage is not independently verified.")
            if record.get("conversion_manifest"):
                lines.append(f"  Conversion receipt: {record['conversion_manifest']}")
            if record.get("converted_markdown_path"):
                lines.append(f"  Original converted Markdown (including extracted image references): {record['converted_markdown_path']}")
    lines.extend(["", "## Prior Literature stage outputs", ""])
    for path in sorted((root / "invocations").glob("*/output.json")):
        lines.append(f"- {path}")
    atomic(retrieval / "evidence_index.md", "\n".join(lines) + "\n")
    return papers, {"output_dir": str(home), "papers_json": str(retrieval / "papers.json"),
                    "evidence_index_md": str(retrieval / "evidence_index.md")}, source_rows


def _paper_ref(ref: Any, papers: list[dict[str, Any]]) -> dict[str, Any] | None:
    text = str(ref or "").strip()
    if re.fullmatch(r"paper:\d+", text):
        index = int(text.split(":")[1])
        return papers[index] if index < len(papers) else None
    identity = _identity_helpers()
    normalized = text.casefold()
    doi = identity.doi_key(text)
    if doi:
        return next((paper for paper in papers if identity.doi_key(paper.get("doi")) == doi), None)
    matches = [paper for paper in papers if any(
        str(paper.get(field) or "").strip().casefold() == normalized
        for field in ("title", "url", "id", "arxiv_id")
    )] if normalized else []
    return matches[0] if len(matches) == 1 else None


def _select(output: dict[str, Any], papers: list[dict[str, Any]], seen: set[str]) -> list[dict[str, Any]]:
    landscape = output.get("landscape") or {}
    rows = list(landscape.get("representative_papers") or [])
    for route in landscape.get("major_routes") or []:
        if isinstance(route, dict):
            rows.extend(route.get("representative_papers") or [])
    candidates: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        # Closed deliveries carry stable identities; catalog filtering can
        # change old paper:N positions when a job is resumed.
        stable_ref = item.get("doi_or_url") or item.get("paper_title")
        paper = _paper_ref(stable_ref or item.get("source_ref"), papers)
        if paper is not None and _stable(paper) not in seen and paper not in candidates:
            candidates.append(paper)
    orders = [str(item).casefold() for item in landscape.get("recommended_reading_order") or []]
    def rank(paper: dict[str, Any]) -> int:
        exacts = [str(paper.get(field) or "").casefold() for field in ("doi", "title", "id")]
        index = papers.index(paper)
        return next((number for number, text in enumerate(orders)
                     if f"paper:{index}" in text or any(value and value in text for value in exacts)), len(orders))
    return sorted(candidates, key=rank)


def _reconcile_source_bookkeeping(state: dict[str, Any], papers: list[dict[str, Any]]) -> None:
    """Keep acquisition/review indexes stable when a preprint gains its DOI."""
    aliases = dict(state.get("source_aliases") or {})
    identity = _identity_helpers()
    for choice in state.get("selected_sources") or []:
        original = choice.get("paper") or {}
        matches = [paper for paper in papers if identity.same_paper(original, paper)]
        if len(matches) == 1 and choice.get("source_id"):
            old, current = choice["source_id"], _stable(matches[0])
            if old != current:
                aliases[old] = current
    def canonical(key):
        seen = set()
        while key in aliases and key not in seen:
            seen.add(key)
            key = aliases[key]
        return key
    if aliases:
        state["source_aliases"] = {key: canonical(value) for key, value in aliases.items()}
        state["source_attempts"] = list(dict.fromkeys(canonical(key) for key in state.get("source_attempts") or []))
        state["source_reviews"] = {canonical(key): value for key, value in (state.get("source_reviews") or {}).items()}


def _access(output: dict[str, Any], raw: dict[str, Any], papers: list[dict[str, Any]], sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Reject unsupported access claims while retaining the actual model delivery."""
    result = json.loads(json.dumps(output))
    by_key = {source["source_id"]: source for source in sources}
    normalize = lambda value: re.sub(r"\s+", " ", str(value or "")).strip()
    for record, declaration in zip(result.get("evidence_records") or [], raw.get("evidence_records") or []):
        stable_ref = record.get("doi_or_url") or record.get("paper_title")
        paper = _paper_ref(stable_ref or declaration.get("source_ref"), papers)
        if paper is None:
            # Closure can resolve exact titles for ordinary single-source
            # tasks; this multi-source job also rejects ambiguous titles.
            record.update({"paper_title": str(declaration.get("source_ref") or ""),
                           "authors": [], "year": None, "doi_or_url": "", "local_original_path": ""})
        source = by_key.get(_stable(paper), {}) if paper else {}
        declared = declaration.get("source_access", "unavailable")
        available = source.get("reading_level", "unavailable")
        text = ""
        quote = normalize(declaration.get("exact_quote"))
        abstract_origin = None
        if declared == "metadata" and paper:
            text = json.dumps({key: value for key, value in paper.items() if key not in {"abstract", "abstract_variants"}}, ensure_ascii=False)
        elif declared == "abstract" and paper:
            text = str(paper.get("abstract") or "")
            variants = [*(paper.get("abstract_variants") or [])[:12],
                        {"text": text, "provider": "merged_metadata" if len(paper.get("sources") or []) > 1
                            else paper.get("source") or "", "url": paper.get("url") or ""}]
            match = next((variant for variant in variants if isinstance(variant, dict)
                and isinstance(variant.get("text"), str) and len(variant["text"]) <= 30000
                and quote and quote in normalize(variant["text"])), None)
            if match:
                text = match["text"]
                abstract_origin = {"provider": str(match.get("provider") or "")[:200], "url": str(match.get("url") or "")[:2000]}
        elif declared == "full_text" and source.get("text_path"):
            text = Path(source["text_path"]).read_text(encoding="utf-8")
        rank = {"unavailable": 0, "metadata": 1, "abstract": 2, "full_text": 3}
        grounded = declared != "unavailable" and rank.get(declared, 0) <= rank.get(available, 0) and bool(quote) and quote in normalize(text)
        record["declared_source_access"] = declared
        record["source_access"] = declared if grounded else "unavailable"
        record["access_assessment"] = {"available": available, "quote_located": grounded,
                                       "locator_semantically_checked": False,
                                       "source_id": source.get("source_id", "")}
        record["limitations"] = str(declaration.get("limitations") or "")
        if grounded and abstract_origin is not None:
            record["access_assessment"]["abstract_provenance"] = abstract_origin
        if source.get("text_path"):
            record["access_assessment"]["text_path"] = source["text_path"]
        if declared != "unavailable" and not grounded:
            record["limitations"] = (str(record.get("limitations") or "") +
                " SDK could not bind the declared quote/access to materialized source text; retain as unverified.").strip()
    landscape = result.get("landscape") or {}
    maps = [*(landscape.get("representative_papers") or [])]
    for route in landscape.get("major_routes") or []:
        if isinstance(route, dict):
            maps.extend(route.get("representative_papers") or [])
    for row in maps:
        paper = _paper_ref(row.get("doi_or_url") or row.get("paper_title") or row.get("source_ref"), papers)
        if paper is None:
            row.update({"paper_title": str(row.get("source_ref") or ""),
                        "authors": [], "year": None, "doi_or_url": "", "local_original_path": ""})
        source = by_key.get(_stable(paper), {}) if paper else {}
        available = source.get("reading_level", "unavailable")
        row["declared_source_access"] = row.get("source_access")
        ranks = {"unavailable": 0, "metadata": 1, "abstract": 2, "full_text": 3}
        if ranks.get(row.get("source_access"), 0) > ranks.get(available, 0):
            row["source_access"] = available
        row["materialized_source_access"] = available
    return result


def _expand_report_references(text: str, sources: list[dict[str, Any]]) -> str:
    """Render persisted corpus references as ordinary paper citations."""
    from urllib.parse import quote, urlsplit
    def replace(match):
        index = int(match[1])
        if index >= len(sources):
            return match[0]
        source = sources[index]
        title = str(source.get("title") or match[0])[:400].replace("[", "\\[").replace("]", "\\]")
        doi = _identity_helpers().doi_key(source.get("doi"))
        href = "https://doi.org/" + quote(doi, safe="/") if doi else str(source.get("url") or "")
        try:
            parsed = urlsplit(href)
            safe = parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password
        except ValueError:
            safe = False
        return f"[{title}]({href})" if safe else title
    return re.sub(r"\bpaper:(\d+)\b", replace, text)


def _report(state: dict[str, Any]) -> str:
    from urllib.parse import quote, urlsplit
    output = state.get("output") or {}
    landscape = output.get("landscape") or {}
    sources = state.get("sources") or []
    def citation(row):
        title = str(row.get("paper_title") or row.get("title") or row.get("source_ref") or "未命名文献")[:400]
        source = next((source for source in sources if str(source.get("title") or "").casefold() == title.casefold()), {})
        ident = str(row.get("doi_or_url") or row.get("doi") or source.get("doi") or row.get("url") or source.get("url") or "")
        href = "https://doi.org/" + quote(ident, safe="/") if re.fullmatch(r"10\.\d{4,9}/[^\s]+", ident) else ident
        parsed = urlsplit(href)
        safe = parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password
        label = title.replace("[", "\\[").replace("]", "\\]")
        result = f"[{label}]({href})" if safe else label
        year = row.get("year") or source.get("year")
        authors = row.get("authors") or source.get("authors") or []
        details = ", ".join([str(authors[0]) + (" et al." if len(authors) > 1 else "")] if authors else [])
        if year: details += (", " if details else "") + str(year)
        return result + (" — " + details if details else "")
    lines = ["# 文献调研 / Literature research", "", str(landscape.get("field_scope") or state["request"]["query"]), "",
             str(landscape.get("relation_to_current_research") or ""), "", "## 主要方向 / Technical routes", ""]
    for route in landscape.get("major_routes") or []:
        lines.extend([f"### {route.get('name', '')}", "", str(route.get("focus") or ""), ""])
        for paper in route.get("representative_papers") or []:
            lines.append(f"- {citation(paper)}：{paper.get('relevance', '')}（access: {paper.get('source_access', 'unavailable')}）")
    representatives = landscape.get("representative_papers") or []
    if representatives:
        lines.extend(["", "## 代表文献", ""])
        lines.extend(f"- {citation(paper)}：{paper.get('relevance', '')}（access: {paper.get('source_access', 'unavailable')}）" for paper in representatives)
    lines.extend(["", "## 文献依据与分歧 / Source-grounded observations and disagreements", ""])
    for record in output.get("evidence_records") or []:
        checked = (record.get("access_assessment") or {}).get("quote_located") is True
        lines.extend([f"### {citation(record)}", "", str(record.get("scientific_insight") or ""), "",
                      str(record.get("interpretation") or ""), "",
                      f"Access: {record.get('source_access', 'unavailable')}; locator: {json.dumps(record.get('locator') or {}, ensure_ascii=False)}.", "",
                      "引句定位：" + ("已在对应材料中定位" if checked else "尚未核验") + "。",
                      str(record.get("limitations") or ""), ""])
    order = landscape.get("recommended_reading_order") or []
    if order:
        lines.extend(["## 推荐阅读顺序", ""])
        for index, item in enumerate(order, 1):
            match = next((row for row in sources if str(item).casefold() in {
                str(row.get("title") or "").casefold(), str(row.get("doi") or "").casefold()}), None)
            lines.append(f"{index}. {citation(match) if match else str(item)[:1000]}")
    lines.extend(["", "## 开放问题与资料缺口 / Open questions and coverage limits", "",
                  f"作业状态：{state['status']}（{state.get('stop_reason', '')}）。", ""])
    lines.extend(f"- {question}" for question in landscape.get("open_questions") or [])
    lines.extend(["", str(output.get("remaining_scientific_question") or ""), "", "## 已核对材料 / Available source material", ""])
    reviewed = state.get("source_reviews") or {}
    rows = [row for row in sources if row.get("source_id") in reviewed]
    if not rows: rows = sources[:12]
    lines.extend(f"- {citation(row)} | {row['reading_level']}" for row in rows[:24])
    lines.extend(["", f"本轮保留 {len(sources)} 条检索候选；已选读材料见上表，完整题录与检索记录保留在来源索引。",
                  "来源可用、引句定位与科学推论分别判断；具体主张的适用边界以上述材料为准。", ""])
    return _expand_report_references("\n".join(lines), sources)


def run_literature(workspace: Path, request: dict[str, Any], *, backend: Any = None,
                   retriever: Callable[..., dict[str, Any]] | None = None,
                   model: str | None = None, reasoning_effort: str | None = None,
                   cancel_event: Any = None, on_event: Callable[[dict[str, Any]], Any] | None = None,
                   planner: Callable[..., dict] | None = None) -> dict[str, Any]:
    """Run/resume one bounded Literature job using the production role and contracts.

    ``retriever(task, workspace)`` follows ``retrieve_scholarly``. ``backend``
    follows the normal SDK Backend interface; tests inject both without network
    or model calls. Re-entering a job reuses persisted completed transports and
    stage deliveries. Resource limits may increase on an explicit resumed call.
    """
    request = _request(request)
    owns_backend = backend is None
    with _locked(workspace) as root:
        manifest_path = _child(root, "manifest.json")
        state = _json(manifest_path)
        if manifest_path.exists() and state is None:
            raise LiteratureServiceError("The existing Literature manifest is unreadable; it cannot be replaced with a fresh job")
        if state is not None and (not isinstance(state, dict) or state.get("schema") != SCHEMA):
            raise LiteratureServiceError("This workspace does not contain a Literature job")
        if state is None:
            state = {"schema": SCHEMA, "workflow_version": 2 if backend is None or planner else 1, "job_id": root.name if re.fullmatch(r"[a-f0-9]{32}", root.name) else uuid.uuid4().hex, "request": request,
                "created_at": now(), "status": "running", "round": 0, "thread_id": None,
                "transports": [], "invocations": [], "source_attempts": [], "sources": [],
                "literature_agent_invoked": False, "model_invoked": False,
                "scientific_evidence_created": False, "output": {}, "error": ""}
        else:
            previous = state.get("request") or {}
            if any(previous.get(key) != request.get(key) for key in request if key not in {"max_rounds", "max_sources"}):
                raise LiteratureServiceError("A Literature job cannot silently change its assigned question or model profile")
            for key in ("max_rounds", "max_sources"):
                request[key] = max(request[key], int(previous.get(key, request[key])))
            state["request"] = request
        agent_root = _child(root, "literature")
        agent_root.mkdir(exist_ok=True)
        routes = build_model_profile(request.get("model_profile") or read_model_profile(),
                                     request.get("model_provider") or read_model_provider())
        route = routes["literature"]
        route = replace(route, model=model or route.model, provider=request.get("provider") or route.provider,
                        reasoning_effort=reasoning_effort or route.reasoning_effort)
        selected_route = {"model": route.model, "provider": route.provider, "reasoning_effort": route.reasoning_effort}
        if state.get("route") and state["route"] != selected_route:
            raise LiteratureServiceError("Resume the Literature job with its original model/provider route")
        if state.get("status") == "completed" or state.get("delivery_integrity") is not None:
            try:
                verification = verify_completed_delivery(root, state)
            except LiteratureDeliveryIntegrityError as exc:
                state.update(status="failed", stop_reason="delivery_integrity_failed", error=str(exc))
                write_json(manifest_path, state)
                return _receipt(root, state)
            if state.get("status") == "completed":
                return {**_receipt(root, state), "delivery_verification": verification["status"]}
        state["route"] = selected_route
        state["document_conversion"] = _conversion_capability()
        state.update({"status": "running", "error": "", "stop_reason": ""})

        def save() -> None:
            state["updated_at"] = now()
            write_json(manifest_path, state)

        def emit(kind: str, **payload: Any) -> None:
            event = {"event": kind, "job_id": state["job_id"], "at": now(), **payload}
            with _child(root, "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            if on_event:
                on_event(event)

        def check_cancel() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise _Paused("cancelled")

        def retrieve(key: str, task: dict[str, Any]) -> dict[str, Any]:
            existing = next((item for item in state["transports"] if item["key"] == key), None)
            if existing:
                if existing["transport"].get("status") == "blocked" or existing["transport"].get("transport_ok") is False:
                    raise LiteratureTransportError("Saved Literature retrieval is blocked; its connector failure cannot be reused as source material")
                verify_transport_delivery(root, existing)
                return existing["transport"]
            check_cancel()
            fingerprint = hashlib.sha256(json.dumps({
                "stage": task["task_type"],
                "query": re.sub(r"\s+", " ", str(task.get("search_target") or "")).strip().casefold(),
                "scope": str(task.get("domain_context") or "").casefold(),
                "source": _stable(task["source_lock"]) if task.get("source_lock") else "",
                "search_queries": task.get("search_queries") or [],
                "relevance_terms": task.get("relevance_terms") or [],
                "max_candidates": task.get("max_candidates"),
            }, sort_keys=True).encode()).hexdigest()
            reused = next((item for item in state["transports"] if item.get("query_fingerprint") == fingerprint), None)
            if reused:
                verify_transport_delivery(root, reused)
                state["transports"].append({"key": key, "task": task, "transport": reused["transport"],
                                            "reused_from": reused["key"], "query_fingerprint": fingerprint,
                                            "delivery_integrity": reused.get("delivery_integrity")})
                save()
                emit("literature.retrieval_reused", key=key, reused_from=reused["key"])
                return reused["transport"]
            attempts = state.setdefault("retrieval_attempt_history", [])
            prior_attempts = [item for item in attempts if item.get("key") == key]
            if len(prior_attempts) >= MAX_RETRIEVAL_ATTEMPTS:
                raise LiteratureTransportError("Literature retrieval retry bound reached; original connector receipts were retained")
            attempt = {"key": key, "attempt": len(prior_attempts) + 1, "started_at": now(), "status": "running"}
            attempts.append(attempt)
            save()
            emit("literature.retrieval_started", key=key, task_type=task["task_type"])
            try:
                transport = _transport_paths(root, retriever(task, root))
                attempt.update(transport=transport, completed_at=now())
                if transport.get("status") == "blocked" or transport.get("transport_ok") is False:
                    raise LiteratureTransportError(str(transport.get("reason") or transport.get("error") or "Literature source connectors failed")[:1200])
                seal = capture_transport_delivery(root, transport)
                attempt["status"] = "completed"
            except Exception as exc:
                attempt.update(status="failed", completed_at=now(), error=f"{type(exc).__name__}: {exc}"[:1200])
                save()
                raise
            state["transports"].append({"key": key, "task": task, "transport": transport,
                                       "query_fingerprint": fingerprint, "delivery_integrity": seal})
            save()
            emit("literature.retrieval_completed", key=key, status=transport.get("status"),
                 metadata_hit_count=transport.get("metadata_hit_count", 0),
                 fulltext_materialized_count=transport.get("fulltext_read_count", 0))
            return transport

        def invoke(key: str, task: dict[str, Any], papers: list[dict[str, Any]], corpus: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
            nonlocal backend
            previous = next((item for item in state["invocations"] if item["key"] == key), None)
            if previous and previous.get("status") == "completed":
                verify_invocation_delivery(root, previous)
                # Contract and access checks are repeated against current
                # retained material; replay never rewrites the original receipt.
                return validate_saved_invocation(root, previous, papers, sources, exact=False)
            check_cancel()
            context = {"literature_task": task, "sdk_scholarly_retrieval": corpus}
            stage = stage_for("literature", {}, context)
            contract = role_contract("literature", phase=stage)
            projection = stage_context("literature", stage, context, continuation=bool(state["thread_id"]))
            home = _child(root, f"invocations/{key}")
            home.mkdir(parents=True, exist_ok=True)
            developer = developer_instructions("literature")
            if not (home / "developer.md").exists():
                atomic(home / "developer.md", developer)
            if not (home / "context.json").exists():
                write_json(home / "context.json", context)
            continuation = bool(state["thread_id"])
            if continuation:
                prompt = build_continuation_prompt(role="literature", stage=stage,
                    contract_ref=contract.ref, contract_schema=contract.prompt_projection(projection), context=projection)
            else:
                rule = write_rule("literature").replace("this Cycle's Literature workspace cycle_workspace/literature/", str(agent_root))
                prompt = build_initial_prompt(prefix=f"Literature job {state['job_id']} (independent preparation/Chat work; no scientific Cycle).",
                    role="literature", stage=stage, contract_ref=contract.ref,
                    contract_schema=contract.prompt_projection(projection), context=projection, write_rule=rule)
            if (home / "prompt.md").is_file():
                prompt = _child(root, home / "prompt.md", file=True).read_text(encoding="utf-8")
            else:
                atomic(home / "prompt.md", prompt)
            invocation = previous or {"key": key, "role": "literature", "stage": stage,
                "contract": contract.descriptor(), "skills": [ref.directory for ref in role_skills("literature")],
                "prompt_path": str(home / "prompt.md"), "developer_path": str(home / "developer.md"),
                "context_path": str(home / "context.json"),
                "output_path": str(home / "output.json"), "raw_output_path": str(home / "response.txt"),
                "route": selected_route, "attempts": 0}
            if previous is None:
                state["invocations"].append(invocation)
            raw_path = _child(root, invocation["raw_output_path"])
            # A persisted response is the original delivery, even if its syntax
            # or contract failed. Re-entry must never pay to replace that raw.
            if not raw_path.exists():
                attempts = invocation.get("attempts", 0)
                if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
                    raise LiteratureServiceError("Saved Literature invocation attempts are invalid")
                phase = invocation.get("phase")
                can_start = attempts == 0 and (not previous or phase in {"thread_start", "before_invocation"})
                can_retry = (invocation.get("status") == "failed" and invocation.get("retryable") is True
                             and phase in {"thread_start", "before_invocation"})
                if attempts >= MAX_AGENT_ATTEMPTS:
                    invocation.update({"status": "failed", "phase": "attempt_limit", "retryable": False,
                                       "error": "Literature invocation transport retry bound reached"})
                    save()
                    raise LiteratureServiceError(invocation["error"])
                if not can_start and not can_retry:
                    invocation.update({"status": "blocked", "retryable": False,
                        "error": "Prior Literature invocation has no durable response; recover its original thread before another model request"})
                    save()
                    raise LiteratureServiceError(invocation["error"])
                attempt = None
                try:
                    invocation.update({"status": "running", "phase": "thread_start", "retryable": False,
                                       "attempts": attempts + 1})
                    attempt = {"attempt": attempts + 1, "thread_id": invocation.get("thread_id") or state.get("thread_id"),
                               "started_at": now(), "status": "running"}
                    invocation.setdefault("attempt_history", []).append(attempt)
                    save()
                    if backend is None:
                        from .codex_backend import CodexBackend
                        # Keep the role cwd below this job; do not nest it twice.
                        backend = CodexBackend(root, routes={"literature": route})
                    if Path(getattr(backend, "cwd", root)).resolve() != root:
                        raise LiteratureServiceError("Literature backend must be bound to this dedicated job workspace")
                    original_thread = invocation.get("thread_id") or state.get("thread_id")
                    thread = backend.ensure_thread("literature", original_thread, route.model, route.provider)
                    if original_thread and thread != original_thread:
                        raise LiteratureServiceError("Literature retries must retain the original thread")
                    state["thread_id"] = thread
                    invocation.update({"status": "running", "phase": "invoking", "started_at": now(),
                                       "thread_id": thread})
                    attempt["thread_id"] = thread
                    state["model_invoked"] = True
                    state["literature_agent_invoked"] = True
                    save()
                    emit("literature.agent_started", stage=stage, invocation_key=key, thread_id=thread,
                         model=route.model, provider=route.provider)
                    parameters = inspect.signature(backend.invoke).parameters
                    supports_kwargs = any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values())
                    kwargs = {"cancel_event": cancel_event} if "cancel_event" in parameters or supports_kwargs else {}
                    actual_thread, raw = backend.invoke("literature", prompt, thread, route.model, route.provider, **kwargs)
                    invocation["returned_thread_id"] = actual_thread
                    invocation["phase"] = "response_received"
                    save()
                    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_ARTIFACT_BYTES:
                        raise LiteratureServiceError("Literature response must be bounded textual JSON")
                    atomic(raw_path, raw)
                    attempt.update({"status": "response_received", "completed_at": now()})
                    save()
                    if actual_thread != thread:
                        raise LiteratureServiceError("Literature response returned from a different thread; raw receipt retained")
                except Exception as exc:
                    retryable = (invocation.get("phase") in {"thread_start", "before_invocation"}
                                 and _retryable_invocation_error(exc)
                                 and int(invocation["attempts"]) < MAX_AGENT_ATTEMPTS)
                    invocation.update({"status": "failed", "retryable": retryable,
                                       "error": f"{type(exc).__name__}: {exc}"[:1200]})
                    if attempt is not None:
                        attempt.update({"status": "failed", "error": invocation["error"], "completed_at": now()})
                    save()
                    raise
            try:
                invocation.update({"phase": "validation", "retryable": False})
                if invocation.get("returned_thread_id", invocation.get("thread_id")) != invocation.get("thread_id"):
                    raise LiteratureServiceError("Literature response returned from a different thread; raw receipt retained")
                raw = _child(root, raw_path, file=True).read_text(encoding="utf-8")
                value = normalize_agent_output(raw).value
                contract.validate(value, context)
                closed = close_agent_output("literature", stage, value, context, {})
                closed = _access(closed, value, papers, sources)
            except Exception as exc:
                invocation.update({"status": "failed", "retryable": False,
                                   "error": f"{type(exc).__name__}: {exc}"[:1200]})
                save()
                raise
            write_json(home / "agent_output.json", value)
            write_json(home / "output.json", closed)
            invocation.update({"status": "completed", "phase": "completed", "completed_at": now(), "error": "", "retryable": False,
                               "access_validation_version": 2})
            invocation["delivery_integrity"] = capture_invocation_delivery(root, invocation)
            save()
            emit("literature.agent_completed", stage=stage, invocation_key=key, thread_id=state["thread_id"],
                 remaining_scientific_question=closed.get("remaining_scientific_question", ""))
            return closed

        save()
        emit("literature.job_started", resumed=bool(state["invocations"]))
        try:
            for entry in state["transports"]:
                verify_transport_delivery(root, entry)
            for invocation in state["invocations"]:
                if invocation.get("status") == "completed":
                    verify_invocation_delivery(root, invocation)
            check_cancel()
            if retriever is None:
                from .scholarly_transport import retrieve_scholarly
                retriever = lambda task, workspace: retrieve_scholarly(task, workspace, cancel_event=cancel_event)
            for index, manifest in enumerate(request["retrieval_manifests"], 1):
                key = f"import-{index:02d}"
                if not any(item["key"] == key for item in state["transports"]):
                    transport = _import_manifest(root, manifest, index)
                    state["transports"].append({"key": key, "transport": transport, "source_manifest": manifest,
                                                "delivery_integrity": capture_transport_delivery(root, transport)})
                    save()
            if state.get("workflow_version") == 2 and not state.get("search_plan"):
                emit("literature.planning_started")
                from .literature_planning import plan_search
                state["search_plan"] = (planner or plan_search)(root, request, route, cancel_event=cancel_event)
                save()
                emit("literature.planning_completed", query_count=len(state["search_plan"]["queries"]))
            for round_index in range(1, request["max_rounds"] + 1):
                check_cancel()
                previous_output = state.get("output") or {}
                remaining = str(previous_output.get("remaining_scientific_question") or "").strip()
                if round_index > 1 and not remaining and state.get("round", 0) >= round_index - 1:
                    break
                prefix = f"round-{round_index:02d}"
                query = request["query"] if round_index == 1 else remaining
                task = {"source_gap_id": prefix + "-discovery", "task_type": "landscape", "search_target": query,
                        "domain_context": request["scope"], "triggered_by": "research_chat"}
                if state.get("search_plan"):
                    plan = state["search_plan"]
                    task.update(search_queries=plan["queries"], relevance_terms=plan["relevance_terms"], max_candidates=60)
                    if round_index > 1:
                        # Refresh a gap plan once; never send prose as a provider query.
                        from .literature_planning import plan_search
                        gap_root = _child(root, f"planning-round-{round_index:02d}")
                        gap_root.mkdir(exist_ok=True)
                        gap_plan = (planner or plan_search)(gap_root, {"query": remaining, "scope": request["scope"],
                            "previous_queries": plan["queries"]}, route, cancel_event=cancel_event)
                        task.update(search_queries=gap_plan["queries"], relevance_terms=gap_plan["relevance_terms"])
                elif round_index > 1:
                    task["new_search_lead"] = remaining
                if not (round_index == 1 and request["retrieval_manifests"]):
                    retrieve(prefix + "-discovery", task)
                transports = [item["transport"] for item in state["transports"]]
                papers, corpus, sources = _catalog(root, transports)
                _reconcile_source_bookkeeping(state, papers)
                state["sources"] = sources
                discovery = invoke(prefix + "-landscape", {**task,
                    "why_external_needed": "Map the field, compare routes and identify the few decision-relevant original sources. The SDK uses representative_papers and recommended_reading_order to acquire them; do not select a Hypothesis.",
                    "expected_external_evidence": "A field map distinguishing foundational work, approaches, documented disagreements and unresolved questions; nominate important source reading, not every metadata hit."}, papers, corpus, sources)
                state["output"] = discovery
                selected = _select(discovery, papers, set(state["source_attempts"]))
                available_slots = request["max_sources"] - len(state["source_attempts"])
                batch_size = min(4 if state.get("workflow_version") == 2 else 3, max(0, available_slots))
                # Reconstruct this round's original selection on recovery. A
                # completed acquisition still needs its targeted Agent delivery.
                pending = [item for item in state.get("selected_sources", []) if item["round"] == round_index]
                if not pending:
                    pending = [{"round": round_index, "paper": paper, "source_id": _stable(paper)}
                               for paper in selected[:batch_size]]
                    state.setdefault("selected_sources", []).extend(pending)
                    save()
                for number, choice in enumerate(pending, 1):
                    check_cancel()
                    paper = choice["paper"]
                    source_key = choice["source_id"]
                    targeted_key = prefix + f"-source-{number:02d}"
                    targeted = {"source_gap_id": targeted_key, "task_type": "targeted_source",
                        "search_target": query, "domain_context": request["scope"],
                        "source_lock": {**paper, "status": "locked"}, "triggered_by": "research_chat",
                        "why_external_needed": next((item.get("relevance", "") for item in (discovery.get("landscape") or {}).get("representative_papers", [])
                                                     if _paper_ref(item.get("source_ref"), papers) == paper), "Verify the selected representative source and its applicability."),
                        "expected_external_evidence": "Verify decision-relevant original passages, locators and assumptions. Report unavailable/abstract-only material honestly and state any remaining source question."}
                    retrieve(targeted_key, targeted)
                    if source_key not in state["source_attempts"]:
                        state["source_attempts"].append(source_key)
                        save()
                    papers, corpus, sources = _catalog(root, [item["transport"] for item in state["transports"]])
                    _reconcile_source_bookkeeping(state, papers)
                    reading = invoke(targeted_key, targeted, papers, corpus, sources)
                    canonical_key = (state.get("source_aliases") or {}).get(source_key, source_key)
                    state.setdefault("source_reviews", {})[canonical_key] = reading
                    state["sources"] = sources
                    save()
                papers, corpus, sources = _catalog(root, [item["transport"] for item in state["transports"]])
                synthesis = invoke(prefix + "-synthesis", {"source_gap_id": prefix + "-synthesis", "task_type": "landscape",
                    "search_target": request["query"], "domain_context": request["scope"],
                    "why_external_needed": "Write a useful scientific orientation in the user's language. Explain mechanisms and boundaries, contrast actual conditions and findings, identify foundational and recent nearby work with exact traceable sources, recommend a short reading order, and formulate tractable next questions. Broad interest does not require exhaustive coverage before a useful first delivery. Mark inaccessible sources locally without letting one access failure replace the whole answer. Synthesize this job's retained Literature source reviews, newly materialized originals and field map. The corpus index links prior stage outputs. Compare assumptions, methods, agreement, documented conflict and nearest prior work. Do not repeat an unverified source claim as established science.",
                    "expected_external_evidence": "Deliver the existing landscape contract with a traceable cross-paper account and honest access levels. Express any material unsolved literature need in remaining_scientific_question as a specific complementary search question; Keep open field research questions in landscape.open_questions; remaining_scientific_question is only a concrete missing source check needed for this assigned review, not every unresolved question in the field. Leave it empty once a useful bounded review is internally complete, never merely because the SDK budget is exhausted."},
                    papers, corpus, sources)
                state.update({"output": synthesis, "round": round_index, "sources": sources})
                save()
                remaining = str(synthesis.get("remaining_scientific_question") or "").strip()
                if not remaining:
                    unsupported = [record for record in synthesis.get("evidence_records") or []
                                   if record.get("declared_source_access") != "unavailable"
                                   and not (record.get("access_assessment") or {}).get("quote_located")]
                    state.update({"status": "paused" if unsupported else "completed",
                                  "stop_reason": "source_access_unverified" if unsupported else "review_delivered"})
                    break
                if len(state["source_attempts"]) >= request["max_sources"]:
                    state.update({"status": "paused", "stop_reason": "source_budget_exhausted"})
                    break
                if round_index == request["max_rounds"]:
                    state.update({"status": "paused", "stop_reason": "round_budget_exhausted"})
            if state["status"] == "running":
                state.update({"status": "paused", "stop_reason": "remaining_source_question"})
        except _Paused as exc:
            state.update({"status": "paused", "stop_reason": str(exc)})
        except Exception as exc:
            cancelled = cancel_event is not None and cancel_event.is_set()
            reason = "delivery_integrity_failed" if isinstance(exc, LiteratureDeliveryIntegrityError) else "transport_blocked" if isinstance(exc, LiteratureTransportError) else "execution_failed"
            state.update({"status": "paused" if cancelled else "failed", "stop_reason": "cancelled" if cancelled else reason,
                          "error": f"{type(exc).__name__}: {exc}"[:1600]})
        finally:
            if owns_backend and callable(getattr(backend, "close", None)):
                try:
                    backend.close()
                except Exception as exc:
                    state["cleanup_error"] = f"{type(exc).__name__}: {exc}"[:1200]
                    save()
        save()
        atomic(_child(root, "report.md"), _report(state))
        if state["status"] == "completed":
            try:
                state["delivery_integrity"] = capture_completed_delivery(root, state)
            except LiteratureDeliveryIntegrityError as exc:
                state.update(status="failed", stop_reason="delivery_integrity_failed", error=str(exc))
            save()
        emit("literature.job_finished", status=state["status"], stop_reason=state["stop_reason"],
             source_count=len(state["sources"]), agent_turns=len(state["invocations"]))
        receipt = _receipt(root, state)
        if state["status"] == "completed":
            receipt["delivery_verification"] = "sealed"
        return receipt


def _receipt(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    output = state.get("output") or {}
    sources = state.get("sources") or []
    return {"job_id": state["job_id"], "status": state["status"], "stop_reason": state.get("stop_reason", ""),
        "manifest": str(root / "manifest.json"), "report": str(root / "report.md"), "output": output,
        "thread_id": state.get("thread_id"), "model_invoked": state["model_invoked"],
        "literature_agent_invoked": state["literature_agent_invoked"], "scientific_evidence_created": False,
        "sources": sources, "source_count": len(sources), "source_acquisition_attempts": len(state["source_attempts"]),
        "source_access_counts": {level: sum(row["reading_level"] == level for row in sources)
                                 for level in ("metadata", "abstract", "full_text")},
        "document_conversion": state.get("document_conversion", {}),
        "agent_turns": sum(item.get("status") == "completed" for item in state["invocations"]),
        "remaining_scientific_question": output.get("remaining_scientific_question", ""), "error": state.get("error", "")}
