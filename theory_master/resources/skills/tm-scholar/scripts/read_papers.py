#!/usr/bin/env python3
"""Open-access text materialization; missing access never counts as verified."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import ipaddress
import os
import socket
import time
from pathlib import Path
import re
from urllib.parse import quote, urlencode, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pdf_to_markdown import convert_pdf_to_markdown
from scholar_identity import doi_key, title_key, arxiv_key
from scholar_fulltext import ArticleHTML, alternative_urls, article_body, oa_locations, validate_identity


class ArticleUnavailable(ValueError):
    def __init__(self, reason: str, alternatives: list[str]):
        super().__init__(reason)
        self.alternatives = alternatives


def safe_source(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            return False
        if host in {"localhost", "localhost.localdomain", "metadata.google.internal"}:
            return False
        if host.endswith((".local", ".internal", ".localhost")):
            return False
        try:
            ip = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            return bool(re.fullmatch(r"[a-z0-9.-]+", host)) and "." in host
        return ip.is_global
    except ValueError:
        return False


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not safe_source(newurl):
            raise ValueError("redirect to nonpublic URL was rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_public(url: str):
    if not safe_source(url):
        raise ValueError("nonpublic or non-HTTPS source URL is not permitted")
    opener = build_opener(_SafeRedirect())
    return opener.open(Request(url, headers={"User-Agent": "TheoryMaster-tm-scholar/1.0"}),
                       timeout=18)


def _download(url: str, max_bytes: int) -> tuple[bytes, str]:
    for attempt in range(2):
        try:
            with _open_public(url) as response:
                if not safe_source(response.geturl()):
                    raise ValueError("redirect to nonpublic URL was rejected")
                mime = response.headers.get("Content-Type", "").lower()
                raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("download exceeds size limit")
            return raw, mime
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 1:
                raise
        except (URLError, TimeoutError, socket.timeout):
            if attempt == 1:
                raise
        time.sleep(0.15 * (attempt + 1))
    raise RuntimeError("unreachable download retry state")


def read_source(url: str, dest: Path, max_bytes: int = 12 * 1024 * 1024, *, max_pages: int | None = None,
                expected_paper: dict | None = None) -> tuple[str, str]:
    if not safe_source(url):
        raise ValueError("nonpublic or non-HTTPS source URL is not permitted")
    raw, mime = _download(url, max_bytes)
    dest.mkdir(parents=True, exist_ok=True)
    paper = expected_paper or {}
    if raw.startswith(b"%PDF-"):
        pdf = dest / "source.pdf"
        pdf.write_bytes(raw)
        # Conversion is an explicit user capability. Its configured page range
        # and provider apply consistently to all Literature callers.
        from theory_master.document_conversion import ConversionError, load_settings
        settings = load_settings()
        fingerprint = hashlib.sha256(json.dumps({"source": hashlib.sha256(raw).hexdigest(),
            "settings_revision": settings.get("revision"), "provider": settings.get("provider"),
            "config_path": settings.get("config_path"), "max_pages": max_pages}, sort_keys=True).encode()).hexdigest()
        attempt_path = dest / "conversion_attempt.json"
        if attempt_path.is_file():
            try:
                previous = json.loads(attempt_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = {}
            if previous.get("fingerprint") == fingerprint and previous.get("status") == "failed":
                raise ConversionError("Identical PDF conversion already failed with the current settings; trying other source formats")
        try:
            md = convert_pdf_to_markdown(pdf, dest / "converted", max_pages=max_pages)
        except ConversionError as exc:
            attempt_path.write_text(json.dumps({"fingerprint": fingerprint, "status": "failed",
                "error_type": type(exc).__name__}, indent=2) + "\n", encoding="utf-8")
            raise
        attempt_path.write_text(json.dumps({"fingerprint": fingerprint, "status": "materialized"},
                                          indent=2) + "\n", encoding="utf-8")
        text = md.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            raise ValueError("PDF conversion produced no text")
        identity = validate_identity(paper, text)
        _acquisition_receipt(dest, url, raw, mime, "pdf/converted", identity)
        return text, "pdf/converted"
    if "application/pdf" in mime:
        raise ValueError("declared PDF has no PDF header")
    if not ("text/" in mime or "xml" in mime):
        raise ValueError("source MIME type is not readable article text")
    source_text = raw.decode("utf-8", errors="replace")
    (dest / "source.html").write_bytes(raw)
    parser = ArticleHTML()
    parser.feed(source_text)
    # Resolve a landing page's declared public article/PDF only after checking
    # the page identity. A DOI redirect or an abstract never counts as a body.
    validate_identity(paper, parser.text(), parser.metadata)
    try:
        text, identity = article_body(parser, paper)
    except ValueError as exc:
        raise ArticleUnavailable(str(exc), [candidate for candidate in alternative_urls(parser, url)
                                            if safe_source(candidate)]) from exc
    _acquisition_receipt(dest, url, raw, mime, "html/text", identity)
    return text, "html/text"


def _acquisition_receipt(dest: Path, url: str, raw: bytes, mime: str, method: str, identity: str) -> None:
    (dest / "acquisition.json").write_text(json.dumps({
        "source_url": url, "content_type": mime, "source_sha256": hashlib.sha256(raw).hexdigest(),
        "method": method, "identity_check": identity, "scientific_status": "materialized_unverified",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def candidate_urls(paper: dict) -> list[str]:
    """Only declared OA locations and public arXiv bodies; never a search-page guess."""
    candidates = []
    if paper.get("is_open_access") or paper.get("oa_locations"):
        candidates = [paper.get("open_access_url"), paper.get("pdf_url")]
        candidates.extend(paper.get("open_access_urls") or [])
        candidates.extend(location.get("url") for location in paper.get("oa_locations") or []
                          if isinstance(location, dict))
    explicit_arxiv = arxiv_key(paper.get("arxiv_id"))
    aid = explicit_arxiv or next((key for url in candidates if (key := arxiv_key(url))), "")
    if aid:
        # arXiv's actual HTML body works without enabling any PDF converter.
        if explicit_arxiv:
            candidates.insert(0, "https://arxiv.org/html/" + aid)
        else:
            candidates.append("https://arxiv.org/html/" + aid)
        candidates.append("https://arxiv.org/pdf/" + aid)
    return list(dict.fromkeys(str(url) for url in candidates if url and safe_source(str(url))))


def resolve_oa_locations(paper: dict, dest: Path, *, deadline: float, exclude: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Two exact-ID public lookups, cached per artifact; no title-search identity guesses."""
    doi = doi_key(paper.get("doi"))
    if not doi:
        return [], []
    excluded = sorted(exclude or [])
    dest.mkdir(parents=True, exist_ok=True)
    cache = dest / "oa_resolution.json"
    if cache.is_file():
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if saved.get("doi") == doi and saved.get("version") == 2 and saved.get("excluded_urls") == excluded:
                return list(saved.get("urls") or []), list(saved.get("errors") or [])
        except (ValueError, OSError):
            pass
    urls, errors = [], []
    endpoints = [("openalex", "https://api.openalex.org/works/https://doi.org/" + quote(doi, safe="/")),
                 ("semanticscholar", "https://api.semanticscholar.org/graph/v1/paper/DOI:" + quote(doi, safe="/") + "?" +
                  urlencode({"fields": "title,externalIds,openAccessPdf"}))]
    for provider, endpoint in endpoints:
        if time.monotonic() >= deadline:
            errors.append("OA resolution deadline reached")
            break
        try:
            raw, _ = _download(endpoint, 3 * 1024 * 1024)
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("OA resolver metadata is not an object")
            returned_doi = result.get("doi") if provider == "openalex" else (result.get("externalIds") or {}).get("DOI")
            if doi_key(returned_doi) != doi:
                raise ValueError("OA resolver returned a different DOI")
            title = result.get("display_name") or result.get("title")
            if title:
                validate_identity(paper, "", {"citation_title": [str(title)]})
            locations = oa_locations(result, provider)
            candidate = {"is_open_access": bool(locations), "oa_locations": locations,
                         "arxiv_id": (result.get("externalIds") or {}).get("ArXiv")}
            urls.extend(url for url in candidate_urls(candidate) if url not in excluded)
            if urls:
                break
        except Exception as exc:
            errors.append(f"{provider}: {type(exc).__name__}: {exc}")
    urls = list(dict.fromkeys(urls))[:12]
    cache.write_text(json.dumps({"doi": doi, "version": 2, "excluded_urls": excluded, "urls": urls, "errors": errors},
                                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return urls, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--papers-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--query", default="")
    parser.add_argument("--reading-limit", type=int, default=5)
    parser.add_argument("--reading-selection", choices=("top", "manual"), default="top")
    parser.add_argument("--paper-ids", default="")
    parser.add_argument("--pdf-policy", default="open-access-only")
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--resolve-oa", action="store_true", help="Resolve missing OA locations through exact DOI metadata")
    parser.add_argument("--timeout-s", type=int, default=150)
    args = parser.parse_args()
    deadline = time.monotonic() + max(10, min(args.timeout_s, 600))
    dest = args.output_dir
    dest.mkdir(parents=True, exist_ok=True)
    papers = json.loads(args.papers_json.read_text(encoding="utf-8"))
    if not isinstance(papers, list):
        raise ValueError("expected list of bibliographic papers")
    candidates = []
    for row in papers:
        if not isinstance(row, dict):
            continue
        if args.reading_selection == "manual" and args.paper_ids:
            requested_doi = doi_key(args.paper_ids)
            requested_arxiv = arxiv_key(args.paper_ids)
            matches = (doi_key(row.get("doi")) == requested_doi if requested_doi else
                       arxiv_key(row.get("arxiv_id")) == requested_arxiv if requested_arxiv else
                       title_key(row.get("title")) == title_key(args.paper_ids))
            if not matches:
                continue
        candidates.append(row)
    if args.reading_selection == "manual" and len(candidates) > 1:
        locked = [row for row in candidates if row.get("source_locked")]
        candidates = locked if len(locked) == 1 else []
    candidates = candidates[:max(0, min(args.reading_limit, 20))]
    manifest = []
    summaries = ["# Materialized reading index", "", "Saved text is materialized source content; Literature must read it and verify each scientific claim.", ""]
    for idx, paper in enumerate(candidates, 1):
        title = str(paper.get("title") or f"Paper {idx}")
        urls = candidate_urls(paper)
        item = {"paper": title, "doi": doi_key(paper.get("doi")), "arxiv_id": arxiv_key(paper.get("arxiv_id")),
                "text_path": str(dest / f"paper-{idx:02d}.md"), "url": urls[0] if urls else "",
                "status": "unverified", "method": "", "reason": "",
                "converted_markdown_path": "", "conversion_manifest": "", "max_pages": "",
                "acquisition_manifest": "", "oa_resolution_manifest": ""}
        failures = []
        tried: set[str] = set()
        resolved = not args.resolve_oa
        while item["status"] != "read":
            if time.monotonic() >= deadline:
                failures.append("reading acquisition deadline reached")
                break
            if not urls:
                if resolved:
                    break
                resolved = True
                urls, resolver_errors = resolve_oa_locations(paper, dest / f"paper-{idx:02d}", deadline=deadline, exclude=tried)
                urls = [url for url in urls if url not in tried]
                failures.extend(resolver_errors)
                cache = dest / f"paper-{idx:02d}" / "oa_resolution.json"
                if cache.is_file():
                    item["oa_resolution_manifest"] = str(cache)
                if not urls:
                    break
            url = urls.pop(0)
            if url in tried:
                continue
            tried.add(url)
            if len(tried) > 10:
                failures.append("open-access fallback limit reached")
                break
            try:
                raw, method = read_source(url, dest / f"paper-{idx:02d}", max_pages=args.max_pages, expected_paper=paper)
                if not raw.strip():
                    raise ValueError("download contained no extractable text")
                converted = dest / f"paper-{idx:02d}" / "converted" / "paper.md"
                provenance = converted.parent / "conversion.json"
                if method.startswith("pdf/") and converted.is_file() and provenance.is_file():
                    info = json.loads(provenance.read_text(encoding="utf-8"))
                    item.update({"converted_markdown_path": str(converted),
                                 "conversion_manifest": str(provenance), "max_pages": info.get("max_pages", "")})
                    prefix = Path(os.path.relpath(converted.parent, dest)).as_posix() + "/"
                    raw = re.sub(r"(!?\[[^\]]*\]\()([^\s)]+)(\))",
                                 lambda match: match[1] + (prefix if not urlparse(match[2]).scheme
                                     and not match[2].startswith(("/", "#")) else "") + match[2] + match[3], raw)
                (dest / f"paper-{idx:02d}.md").write_text("# " + title + "\n\n" + raw[:450_000] + "\n", encoding="utf-8")
                item.update({"status": "read", "url": url, "method": method, "reason": ""})
                acquisition = dest / f"paper-{idx:02d}" / "acquisition.json"
                if acquisition.is_file():
                    item["acquisition_manifest"] = str(acquisition)
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
                if isinstance(exc, ArticleUnavailable):
                    urls.extend(candidate for candidate in exc.alternatives if candidate not in tried and candidate not in urls)
                from theory_master.document_conversion import ConversionDisabled
                if isinstance(exc, ConversionDisabled):
                    item["status"] = "conversion_disabled"
        if item["status"] != "read":
            item["reason"] = "; ".join(failures)[:700] or "no declared safe open-access full-text URL"
        manifest.append(item)
        summaries.append(f"- {title}: {item['status']} — {item['reason'] or item['method']}")
    with (dest / "pdf_manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["paper", "doi", "arxiv_id", "text_path", "url", "status", "method", "reason",
                                                    "converted_markdown_path", "conversion_manifest", "max_pages",
                                                    "acquisition_manifest", "oa_resolution_manifest"])
        writer.writeheader()
        writer.writerows(manifest)
    (dest / "reading_index.md").write_text("\n".join(summaries) + "\n", encoding="utf-8")
    print(f"TM Scholar reading: {sum(p['status'] == 'read' for p in manifest)}/{len(manifest)} texts materialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
