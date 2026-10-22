#!/usr/bin/env python3
"""TheoryMaster-native bibliographic discovery. Only stdlib and public API schemas."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from scholar_identity import arxiv_key, consolidate, doi_key

SOURCES = ("openalex", "crossref", "semanticscholar", "pubmed", "arxiv")
USER_AGENT = "TheoryMaster-tm-scholar/1.0 (academic metadata research)"
MAX_RESPONSE = 5 * 1024 * 1024


def fetch(url: str) -> bytes:
    """Bounded public scholarly API fetch; retry 429 and transient 5xx without hiding failures."""
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT,
                                              "Accept": "application/json, application/atom+xml"}), timeout=22) as response:
                data = response.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE:
                raise ValueError("scholarly API response exceeds size limit")
            return data
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            retry_after = str((exc.headers or {}).get("Retry-After", "")).strip()
            if retry_after.isdecimal() and int(retry_after) > 3:
                raise RuntimeError(f"provider rate-limited; Retry-After={retry_after}s; deferred") from exc
            time.sleep(float(retry_after) if retry_after.isdecimal() else 0.5 * (attempt + 1))
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("scholarly request retry exhausted")


def get_json(url: str) -> dict:
    result = json.loads(fetch(url))
    if not isinstance(result, dict):
        raise ValueError("API response was not a JSON object")
    return result


def normalize(raw: dict, source: str) -> dict:
    if source == "openalex":
        title = raw.get("display_name") or raw.get("title") or ""
        authors = [a.get("author", {}).get("display_name", "") for a in raw.get("authorships") or []]
        oa = raw.get("best_oa_location") or {}
        locations = [oa, raw.get("primary_location") or {}, *(raw.get("locations") or [])]
        pdf_urls = [str(item.get("pdf_url")) for item in locations
                    if isinstance(item, dict) and item.get("is_oa") and item.get("pdf_url")]
        url = (raw.get("primary_location") or {}).get("landing_page_url") or raw.get("id") or ""
        abstract = ""
        inverted = raw.get("abstract_inverted_index") or {}
        if inverted:
            pairs = [(position, word) for word, positions in inverted.items() for position in positions]
            abstract = " ".join(word for _, word in sorted(pairs))
        ids = [str(a.get("author", {}).get("orcid") or "") for a in raw.get("authorships") or []]
        paper = make(title, authors, raw.get("publication_year"), raw.get("doi"), url, abstract,
                     raw.get("cited_by_count"), pdf_urls[0] if pdf_urls else "", source,
                     arxiv_id=(raw.get("ids") or {}).get("arxiv") or "", author_ids=ids)
        paper["open_access_urls"] = list(dict.fromkeys(pdf_urls))
        return paper
    if source == "crossref":
        title = (raw.get("title") or [""])[0]
        authors = [" ".join(filter(None, (a.get("given"), a.get("family")))) for a in raw.get("author") or []]
        parts = (raw.get("published") or raw.get("created") or {}).get("date-parts") or [[]]
        year = parts[0][0] if parts and parts[0] else None
        ids = [str(a.get("ORCID") or "") for a in raw.get("author") or []]
        return make(title, authors, year, raw.get("DOI"), raw.get("URL"), raw.get("abstract"), raw.get("is-referenced-by-count"), "", source, author_ids=ids)
    if source == "semanticscholar":
        authors = [a.get("name", "") for a in raw.get("authors") or []]
        ids = raw.get("externalIds") or {}
        pdf = raw.get("openAccessPdf") or {}
        return make(raw.get("title"), authors, raw.get("year"), ids.get("DOI"), raw.get("url"), raw.get("abstract"), raw.get("citationCount"), pdf.get("url"), source, arxiv_id=ids.get("ArXiv") or "")
    raise ValueError(f"invalid provider: {source}")


def make(title, authors, year, doi, url, abstract, citations, open_url, source, *, arxiv_id="", author_ids=()) -> dict:
    clean_doi = doi_key(doi)
    return {
        "title": str(title or "").strip(), "authors": [str(a).strip() for a in authors if a],
        "year": int(year) if year else None, "doi": clean_doi,
        "url": str(url or "").strip(), "abstract": str(abstract or "").strip(),
        "citation_count": int(citations or 0), "source": source, "sources": [source],
        "score": 0.0, "is_open_access": bool(open_url),
        "open_access_url": str(open_url or ""), "arxiv_id": arxiv_key(arxiv_id),
        "open_access_urls": [str(open_url)] if open_url else [],
        "author_ids": sorted({str(a).strip() for a in author_ids if a}),
    }


def search(source: str, query: str, limit: int) -> list[dict]:
    if source == "openalex":
        endpoint = "https://api.openalex.org/works?" + urlencode({"search": query, "per-page": limit})
        return [normalize(row, source) for row in get_json(endpoint).get("results", [])
                if row.get("type") not in {"dataset", "peer-review", "component"}]
    if source == "crossref":
        endpoint = "https://api.crossref.org/works?" + urlencode({"query.bibliographic": query, "rows": limit})
        return [normalize(row, source) for row in get_json(endpoint).get("message", {}).get("items", [])
                if row.get("type") not in {"peer-review", "component", "dataset", "journal-issue"}]
    if source == "semanticscholar":
        endpoint = "https://api.semanticscholar.org/graph/v1/paper/search?" + urlencode({
            "query": query, "limit": min(limit, 100), "fields": "title,year,authors,abstract,citationCount,externalIds,url,openAccessPdf"})
        return [normalize(row, source) for row in get_json(endpoint).get("data", [])]
    if source == "arxiv":
        endpoint = "https://export.arxiv.org/api/query?" + urlencode({
            "search_query": "all:" + query, "start": 0, "max_results": limit})
        root = ET.fromstring(fetch(endpoint))
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        result = []
        for item in root.findall("atom:entry", ns):
            url = item.findtext("atom:id", "", ns)
            aid = arxiv_key(url)
            date = item.findtext("atom:published", "", ns)
            authors = [a.findtext("atom:name", "", ns) for a in item.findall("atom:author", ns)]
            row = make(item.findtext("atom:title", "", ns), authors, date[:4] if len(date) >= 4 else None,
                       "", url, item.findtext("atom:summary", "", ns), 0,
                       "https://arxiv.org/pdf/" + aid if aid else "", source, arxiv_id=aid)
            result.append(row)
        return result
    if source == "pubmed":
        endpoint = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urlencode({
            "db": "pubmed", "term": query, "retmax": limit})
        search_root = ET.fromstring(fetch(endpoint))
        ids = [node.text for node in search_root.findall(".//IdList/Id") if node.text]
        if not ids:
            return []
        detail_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urlencode({
            "db": "pubmed", "id": ",".join(ids), "rettype": "xml"})
        root = ET.fromstring(fetch(detail_url))
        results = []
        for item in root.findall(".//PubmedArticle"):
            article = item.find(".//Article")
            if article is None:
                continue
            heading = article.find("ArticleTitle")
            title = "".join(heading.itertext()) if heading is not None else ""
            names = [" ".join(filter(None, (person.findtext("ForeName"), person.findtext("LastName"))))
                     for person in article.findall(".//AuthorList/Author")]
            year = item.findtext(".//PubDate/Year") or item.findtext(".//DateCompleted/Year")
            abstract = " ".join("".join(node.itertext()) for node in article.findall(".//AbstractText"))
            doi = ""
            for identifier in item.findall(".//ArticleId"):
                if identifier.attrib.get("IdType", "").lower() == "doi":
                    doi = identifier.text or ""
                    break
            pmid = item.findtext(".//MedlineCitation/PMID") or ""
            pmcid = next((str(x.text or "") for x in item.findall(".//ArticleId")
                          if x.attrib.get("IdType", "").lower() == "pmc"), "")
            oa_url = "https://pmc.ncbi.nlm.nih.gov/articles/" + pmcid + "/" if re.fullmatch(r"PMC\d+", pmcid) else ""
            results.append(make(title, names, year, doi,
                                "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/" if pmid else "",
                                abstract, 0, oa_url, source))
        return results
    raise ValueError(f"unsupported source '{source}'; supported: {', '.join(SOURCES)}")


def rank_and_dedupe(rows: list[dict], query: str, year_min: int | None, year_max: int | None) -> list[dict]:
    """Conservatively reconcile identifiers, title/year and author evidence."""
    return consolidate(rows, query, year_min, year_max)


def main() -> int:
    parser = argparse.ArgumentParser(description="TM Scholar — limited public scholarly metadata search")
    parser.add_argument("--query", required=True)
    parser.add_argument("--sources", default="openalex,crossref,arxiv")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--from-year", type=int)
    parser.add_argument("--to-year", type=int)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report-language", default="en")
    parser.add_argument("--report-style", default="list")
    parser.add_argument("--delay", type=float, default=0.0)  # host controls dispatch
    args = parser.parse_args()
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    selected = list(dict.fromkeys(name.strip().lower() for name in args.sources.split(",") if name.strip()))
    if not selected:
        parser.error("--sources must contain at least one scholarly provider")
    errors, fetched = [], []
    for source in selected:
        try:
            fetched.extend(search(source, args.query, max(1, min(args.limit, 100))))
        except Exception as exc:
            errors.append(f"{source}: {type(exc).__name__}: {exc}")
    papers = rank_and_dedupe(fetched, args.query, args.from_year, args.to_year)
    (out / "papers.json").write_text(json.dumps(papers, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (out / "papers.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = list(make("", [], None, "", "", "", 0, "", "").keys())
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in papers:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value for key, value in row.items()})
    (out / "papers.bib").write_text("\n".join(
        '@misc{tm' + str(i) + ', title={' + p["title"].replace("{", "").replace("}", "") +
        '}, year={' + str(p.get("year") or "") + '}, url={' + p["url"] + '}}'
        for i, p in enumerate(papers, 1)) + "\n", encoding="utf-8")
    summary = "\n".join(f"- {p['title']} ({p.get('year') or 'n.d.'}) — {p['url']}" for p in papers) or "- No results"
    (out / "report.md").write_text(f"# Bibliographic search: {args.query}\n\n{summary}\n", encoding="utf-8")
    (out / "topic_brief.md").write_text(
        "# Metadata-only overview\n\n" + summary + "\n\nDo not treat metadata hits as full-text verified claims.\n", encoding="utf-8")
    (out / "search_log.md").write_text(
        f"# Search log\n\nUTC: {datetime.now(timezone.utc).isoformat()}\nQuery: {args.query}\n"
        f"Sources: {args.sources}\nMetadata hits: {len(papers)}\nConnector errors: {len(errors)}\n", encoding="utf-8")
    error_path = out / "errors.log"
    if errors:
        error_path.write_text("\n".join(errors) + "\n", encoding="utf-8")
        print("\n".join(errors), file=sys.stderr)
    elif error_path.exists():
        error_path.unlink()
    print(f"TM Scholar: {len(papers)} unique metadata records")
    return 2 if selected and len(errors) == len(selected) else 0


if __name__ == "__main__":
    raise SystemExit(main())
