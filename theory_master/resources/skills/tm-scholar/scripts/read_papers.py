#!/usr/bin/env python3
"""Open-access text materialization; missing access never counts as verified."""
from __future__ import annotations

import argparse
import csv
import html
from html.parser import HTMLParser
import json
import ipaddress
import socket
import time
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pdf_to_markdown import convert_pdf_to_markdown


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "h1", "h2", "h3", "li"}:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.chunks.append(data)


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
                       timeout=30)


def _download(url: str, max_bytes: int) -> tuple[bytes, str]:
    for attempt in range(3):
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
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (URLError, TimeoutError, socket.timeout):
            if attempt == 2:
                raise
        time.sleep(0.15 * (attempt + 1))
    raise RuntimeError("unreachable download retry state")


def read_source(url: str, dest: Path, max_bytes: int = 12 * 1024 * 1024, *, max_pages: int = 20) -> tuple[str, str]:
    if not safe_source(url):
        raise ValueError("nonpublic or non-HTTPS source URL is not permitted")
    raw, mime = _download(url, max_bytes)
    if raw.startswith(b"%PDF-"):
        dest.mkdir(parents=True, exist_ok=True)
        pdf = dest / "source.pdf"
        pdf.write_bytes(raw)
        md = convert_pdf_to_markdown(pdf, dest / "converted", max_pages=max_pages)
        text = md.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            raise ValueError("PDF conversion produced no text")
        return text, "pdf/marker"
    if "application/pdf" in mime:
        raise ValueError("declared PDF has no PDF header")
    if not ("text/" in mime or "xml" in mime):
        raise ValueError("source MIME type is not readable article text")
    parser = _Text()
    parser.feed(raw.decode("utf-8", errors="replace"))
    text = html.unescape(" ".join(parser.chunks))
    return re.sub(r"[ \t]+", " ", text).strip(), "html/text"


def candidate_urls(paper: dict) -> list[str]:
    """Only explicit open-access full-text URLs; DOI landing pages are not substitutes."""
    if not paper.get("is_open_access"):
        return []
    candidates = [paper.get("open_access_url"), paper.get("pdf_url")]
    candidates.extend(paper.get("open_access_urls") or [])
    if paper.get("arxiv_id"):
        from scholar_identity import arxiv_key
        aid = arxiv_key(paper["arxiv_id"])
        if aid:
            candidates.append("https://arxiv.org/pdf/" + aid)
    return list(dict.fromkeys(str(url) for url in candidates if url and safe_source(str(url))))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--papers-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--query", default="")
    parser.add_argument("--reading-limit", type=int, default=5)
    parser.add_argument("--reading-selection", choices=("top", "manual"), default="top")
    parser.add_argument("--paper-ids", default="")
    parser.add_argument("--pdf-policy", default="open-access-only")
    parser.add_argument("--max-pages", type=int, default=20)
    args = parser.parse_args()
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
            ident = str(row.get("doi") or row.get("title") or "")
            if args.paper_ids.casefold() not in ident.casefold():
                continue
        candidates.append(row)
    candidates = candidates[:max(0, min(args.reading_limit, 20))]
    manifest = []
    summaries = ["# Materialized reading index", "", "Only a nonempty saved primary text is full-text verified.", ""]
    for idx, paper in enumerate(candidates, 1):
        title = str(paper.get("title") or f"Paper {idx}")
        urls = candidate_urls(paper)
        item = {"paper": title, "url": urls[0] if urls else "", "status": "unverified", "method": "", "reason": ""}
        if not urls:
            item["reason"] = "no declared safe open-access full-text URL"
        else:
            failures = []
            for url in urls:
                try:
                    raw, method = read_source(url, dest / f"paper-{idx:02d}", max_pages=args.max_pages)
                    if not raw.strip():
                        raise ValueError("download contained no extractable text")
                    (dest / f"paper-{idx:02d}.md").write_text("# " + title + "\n\n" + raw[:450_000] + "\n", encoding="utf-8")
                    item.update({"status": "read", "url": url, "method": method, "reason": ""})
                    break
                except Exception as exc:
                    failures.append(f"{type(exc).__name__}: {exc}")
            if item["status"] != "read":
                item["reason"] = "; ".join(failures)[:700]
        manifest.append(item)
        summaries.append(f"- {title}: {item['status']} — {item['reason'] or item['method']}")
    with (dest / "pdf_manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["paper", "url", "status", "method", "reason"])
        writer.writeheader()
        writer.writerows(manifest)
    (dest / "reading_index.md").write_text("\n".join(summaries) + "\n", encoding="utf-8")
    print(f"TM Scholar reading: {sum(p['status'] == 'read' for p in manifest)}/{len(manifest)} texts materialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
