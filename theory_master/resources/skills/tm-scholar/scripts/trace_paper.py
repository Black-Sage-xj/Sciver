#!/usr/bin/env python3
"""Conservative OpenAlex citation tracing: provider edges are metadata, not verified claims."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

from scholar_identity import authors_compatible, doi_key, title_key


def api(url: str) -> dict:
    with urlopen(Request(url, headers={"User-Agent": "TheoryMaster-tm-scholar/1.0"}), timeout=25) as response:
        content = response.read(4 * 1024 * 1024 + 1)
    if len(content) > 4 * 1024 * 1024:
        raise ValueError("OpenAlex response exceeds size limit")
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("OpenAlex returned non-object data")
    return result


def work_id(value: object) -> str:
    raw = str(value or "").strip().rsplit("/", 1)[-1].upper()
    return raw if re.fullmatch(r"W\d+", raw) else ""


def compact(item: dict) -> dict:
    authorships = item.get("authorships") or []
    return {
        "title": str(item.get("display_name") or ""),
        "year": item.get("publication_year"),
        "doi": doi_key(item.get("doi")),
        "url": str(item.get("id") or ""),
        "work_id": work_id(item.get("id")),
        "authors": [a.get("author", {}).get("display_name", "") for a in authorships],
        "author_ids": sorted({str(a.get("author", {}).get("orcid")) for a in authorships
                              if a.get("author", {}).get("orcid")}),
        "citation_count": int(item.get("cited_by_count") or 0),
        "source": "openalex_metadata",
        "verification_level": "metadata_only",
    }


def seed_from_local(query: str, papers_path: Path | None) -> dict:
    if not papers_path or not papers_path.is_file():
        return {}
    rows = json.loads(papers_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("papers.json must be a list")
    normalized_query = doi_key(query)
    matches = [row for row in rows if isinstance(row, dict) and (
        normalized_query and doi_key(row.get("doi")) == normalized_query or
        title_key(row.get("title")) == title_key(query) or
        str(row.get("url") or "").casefold() == query.casefold()
    )]
    if len(matches) > 1:
        # An ambiguous local title must not silently select a DOI or author.
        identities = {doi_key(row.get("doi")) or (title_key(row.get("title")), row.get("year"),
                                                  tuple(row.get("authors") or [])) for row in matches}
        if len(identities) > 1:
            raise ValueError("ambiguous local seed identity")
    return matches[0] if matches else {}


def resolve_seed(query: str, local: dict) -> dict:
    expected_doi = doi_key(local.get("doi") or query)
    expected_title = title_key(local.get("title") or ("" if expected_doi else query))
    requested_work_id = work_id(local.get("url") or query)
    if expected_doi:
        url = "https://api.openalex.org/works/https://doi.org/" + quote(expected_doi, safe="/")
        source = api(url)
    elif requested_work_id:
        source = api("https://api.openalex.org/works/" + requested_work_id)
    else:
        results = api("https://api.openalex.org/works?" + urlencode({"search": query, "per-page": 5})).get("results") or []
        candidates = [item for item in results if title_key(item.get("display_name")) == expected_title]
        if local.get("year"):
            candidates = [item for item in candidates if item.get("publication_year") and
                          abs(int(item["publication_year"]) - int(local["year"])) <= 1]
        if local.get("authors"):
            candidates = [item for item in candidates if authors_compatible(
                local["authors"], compact(item)["authors"])]
        if len(candidates) != 1:
            raise ValueError("seed title is missing or ambiguous; provide a DOI or work ID")
        source = candidates[0]
    actual_doi = doi_key(source.get("doi"))
    if expected_doi and actual_doi != expected_doi:
        raise ValueError("OpenAlex DOI does not match the requested seed")
    if requested_work_id and work_id(source.get("id")) != requested_work_id:
        raise ValueError("OpenAlex work ID does not match the requested seed")
    if expected_title and title_key(source.get("display_name")) != expected_title:
        raise ValueError("OpenAlex title does not match the requested seed")
    if local.get("year") and source.get("publication_year") and (
        abs(int(local["year"]) - int(source["publication_year"])) > 1
    ):
        raise ValueError("OpenAlex year conflicts with the local seed")
    if local.get("authors") and not authors_compatible(local["authors"], compact(source)["authors"]):
        raise ValueError("OpenAlex authors conflict with the local seed")
    local_ids = {str(v).casefold() for v in local.get("author_ids") or []}
    provider_ids = {str(v).casefold() for v in compact(source)["author_ids"]}
    if local_ids and provider_ids and local_ids.isdisjoint(provider_ids):
        raise ValueError("OpenAlex author ORCIDs conflict with the local seed")
    if not work_id(source.get("id")):
        raise ValueError("seed has no valid OpenAlex work ID")
    return source


def trace(source: dict, limit: int) -> dict:
    seed_id = work_id(source.get("id"))
    result = {
        "seed_metadata": compact(source), "references": [], "citations": [],
        "errors": [], "status": "metadata_only",
        "coverage": {
            "provider": "OpenAlex", "metadata_only": True,
            "indexed_cited_by_count": int(source.get("cited_by_count") or 0),
            "reference_total": len(source.get("referenced_works") or []),
            "citation_query_total": None, "references_returned": 0,
            "citations_returned": 0, "reference_truncated": False,
            "citation_truncated": False, "citation_lookup_complete": False,
            "warning": "Provider coverage does not prove global citation completeness.",
        },
    }
    seen = {seed_id}
    for raw in source.get("referenced_works") or []:
        rid = work_id(raw)
        if not rid or rid in seen:
            continue
        seen.add(rid)
        if len(result["references"]) >= limit:
            break
        try:
            ref = compact(api("https://api.openalex.org/works/" + rid))
            if ref["work_id"] != rid:
                raise ValueError("reference work ID mismatch")
            ref["edge_status"] = "provider_indexed"
        except Exception as exc:
            ref = {"url": "https://openalex.org/" + rid, "work_id": rid,
                   "source": "openalex_reference_unresolved", "edge_status": "unresolved",
                   "error": f"{type(exc).__name__}: {exc}"}
            result["errors"].append(f"reference {rid}: {type(exc).__name__}: {exc}")
        result["references"].append(ref)
    result["coverage"]["references_returned"] = len(result["references"])
    result["coverage"]["reference_truncated"] = (
        result["coverage"]["reference_total"] > len(result["references"]))
    seen_citing = {seed_id}
    try:
        total = None
        # Paginate over provider duplicates/invalid edges without assuming page 1 is complete.
        for page in range(1, 4):
            response = api("https://api.openalex.org/works?" + urlencode({
                "filter": "cites:" + seed_id, "per-page": limit, "page": page}))
            count = (response.get("meta") or {}).get("count")
            if count is not None:
                total = max(0, int(count))
            candidates = response.get("results") or []
            for item in candidates:
                candidate = compact(item)
                cid = candidate["work_id"]
                if not cid or cid in seen_citing:
                    continue
                if "referenced_works" in item and seed_id not in {
                    work_id(w) for w in item.get("referenced_works") or []
                }:
                    result["errors"].append(f"citation {cid}: provider edge conflicts with seed")
                    continue
                seen_citing.add(cid)
                candidate["edge_status"] = "provider_indexed"
                result["citations"].append(candidate)
                if len(result["citations"]) >= limit:
                    break
            if len(result["citations"]) >= limit:
                break
            if not candidates or len(candidates) < limit or (
                total is not None and page * limit >= total
            ):
                result["coverage"]["citation_lookup_complete"] = True
                break
        result["coverage"]["citation_query_total"] = total
        result["coverage"]["citations_returned"] = len(result["citations"])
        result["coverage"]["citation_truncated"] = (
            total > len(result["citations"]) if total is not None else
            len(result["citations"]) >= limit)
        if total is not None and total > 0 and not result["citations"]:
            result["errors"].append("provider reported indexed citing works but no qualifying edge returned")
    except Exception as exc:
        result["errors"].append(f"citations: {type(exc).__name__}: {exc}")
    if result["errors"]:
        result["status"] = "partial_metadata"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper", required=True)
    parser.add_argument("--papers-json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--openalex", action="store_true")
    parser.add_argument("--delay", type=float, default=0.0)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    query = args.paper.strip()
    payload = {
        "seed": query, "status": "unverified", "references": [], "citations": [],
        "limitations": ["Bibliographic citation edges are not verified scientific claims."],
    }
    try:
        local = seed_from_local(query, args.papers_json)
        source = resolve_seed(query, local)
        payload.update(trace(source, max(1, min(args.limit, 50))))
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
    (args.output_dir / "paper_trace.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report = [
        "# Paper citation trace", "", f"Seed: {query}", f"Status: {payload['status']}", "",
        "Edges are provider-indexed bibliographic metadata, not verified intellectual dependence.",
        "", f"References: {len(payload['references'])}", f"Citations: {len(payload['citations'])}",
        "Citation coverage is indexed-provider-only; 0 returned does not establish 0 actual citations.",
        "Coverage: " + json.dumps(payload.get("coverage") or {}, ensure_ascii=False),
    ]
    for issue in payload.get("errors") or []:
        report.append("- Partial failure: " + issue)
    if payload.get("error"):
        report.append("Error: " + payload["error"])
    (args.output_dir / "paper_trace_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return 0 if payload["status"] in {"metadata_only", "partial_metadata"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
