from __future__ import annotations

"""Query generation, candidate ranking, and source-lock helpers for scholarly transport."""

import hashlib
import html
import json
import re
import importlib.util
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

_TARGET_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "use", "terms", "trace",
    "citations", "citation", "original", "definition", "authoritative", "later",
    "interpretations", "interpretation", "source", "sources", "required", "only",
}

_SEARCH_STOPWORDS = _TARGET_STOPWORDS | {
    "how", "does", "what", "which", "affect", "effects", "including", "identify",
    "foundational", "recent", "nearest", "work", "contrasting", "findings",
    "tractable", "open", "questions", "question", "interested", "interest",
    "search", "literature", "papers", "please", "review", "overview", "about",
    "are", "can", "could", "would", "should", "this", "that", "these", "those",
}


def _compact_query(value: str, *, max_terms: int = 14) -> str:
    """Remove task prose while retaining terms, names and non-English queries."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    terms = [token for token in _ascii_focus(text).split()
             if token.casefold() not in _SEARCH_STOPWORDS]
    if terms:
        return " ".join(terms[:max_terms])
    return text[:300]


def _planned_queries(values: Any, *, limit: int = 6) -> list[str]:
    """The planner supplies short complementary queries, never executable commands."""
    if not isinstance(values, list):
        return []
    queries, seen = [], set()
    for item in values:
        value = item.get("query") if isinstance(item, dict) else item
        if not isinstance(value, str):
            continue
        value = re.sub(r"\s+", " ", value).strip()[:400]
        key = " ".join(re.findall(r"[^\W_]+", value.casefold()))
        if key and key not in seen:
            queries.append(value)
            seen.add(key)
        if len(queries) >= limit:
            break
    return queries


def _metadata_issue(record: Any) -> str:
    """Reject corrupt provider records before they enter a model's evidence corpus.

    Provider artifacts stay in each query directory; rejected content is never
    silently shortened into an apparently valid article title.
    """
    if not isinstance(record, dict):
        return "record_not_object"
    title = record.get("title")
    if not isinstance(title, str) or not title.strip():
        return "missing_title"
    if len(title) > 1200 or len(title.split()) > 160:
        return "title_exceeds_article_bound"
    if not re.search(r"\w", title, re.UNICODE):
        return "missing_title"
    abstract = record.get("abstract") or ""
    if not isinstance(abstract, str):
        return "invalid_abstract"
    if len(abstract) > 30000:
        return "abstract_exceeds_metadata_bound"
    page_markers = ("enable javascript", "verify you are human", "checking your browser",
                    "access denied", "just a moment", "accept all cookies", "cookie settings")
    if any(marker in title.casefold() for marker in page_markers):
        return "webpage_title_instead_of_article"
    if re.search(r"<(?:html|body|script|!doctype)\b", title, re.I):
        return "html_document_in_title"
    year = record.get("year")
    if year not in (None, ""):
        try:
            int(year)
        except (TypeError, ValueError, OverflowError):
            return "invalid_year"
    return ""


def _clean_metadata_record(record: Any) -> tuple[dict[str, Any] | None, str]:
    issue = _metadata_issue(record)
    if issue:
        return None, issue
    row = dict(record)
    for field in ("title", "abstract", "venue"):
        value = html.unescape(re.sub(r"<[^>]+>", " ", str(row.get(field) or "")))
        row[field] = re.sub(r"\s+", " ", value).strip()
    row["year"] = int(row["year"]) if row.get("year") not in (None, "") else None
    for field in ("authors", "sources", "author_ids", "keywords", "open_access_urls"):
        values = row.get(field) or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            values = []
        row[field] = list(dict.fromkeys(str(v).strip()[:1000] for v in values[:500]
                                       if isinstance(v, (str, int)) and str(v).strip()))
    if not row["sources"] and row.get("source"):
        row["sources"] = [str(row["source"])[:100]]
    for field in ("citation_count",):
        try:
            row[field] = min(10**9, max(0, int(row.get(field) or 0)))
        except (TypeError, ValueError, OverflowError):
            row[field] = 0
    try:
        score = float(row.get("score") or 0)
        row["score"] = score if math.isfinite(score) else 0.0
    except (TypeError, ValueError, OverflowError):
        row["score"] = 0.0
    for field in ("oa_locations", "fulltext_candidates"):
        if isinstance(row.get(field), list):
            row[field] = [item for item in row[field][:40] if isinstance(item, dict)]
    return row, ""


def _term_set(value: str) -> set[str]:
    tokens = re.findall(r"[^\W_]+", str(value or "").casefold())
    return {token[:-1] if len(token) > 4 and token.endswith("s") and not token.endswith("ss")
            else token for token in tokens if len(token) >= 3 and token not in _SEARCH_STOPWORDS}


def _landscape_relevance(record: dict[str, Any], queries: list[str], domain_context: str = "",
                         relevance_terms: list[str] | None = None) -> float:
    """Reward scientific phrase coverage; provider popularity breaks only close ties."""
    title = _term_set(record.get("title") or "")
    abstract = _term_set(record.get("abstract") or "")
    query_terms = [_term_set(query) for query in queries]
    coverage = [(.75 * len(terms & title) + .25 * len(terms & abstract)) / len(terms)
                for terms in query_terms if terms]
    relevance = max(coverage, default=0.0)
    # Shared query terms are a model-independent subject anchor. This prevents a
    # result matching only an ambiguous subtopic from winning the broad survey.
    shared = set.intersection(*query_terms) if query_terms and all(query_terms) else set()
    anchors = _term_set(str(domain_context or "").split(",", 1)[0]) or shared
    if relevance_terms:
        anchors |= _term_set(" ".join(str(value) for value in relevance_terms[:12]))
    alignment = len(anchors & (title | abstract)) / len(anchors) if anchors else 0.0
    citations = math.log1p(min(int(record.get("citation_count") or 0), 10000)) / math.log1p(10000)
    return round(relevance + .65 * alignment + .03 * citations, 5)


def _ascii_focus(value: str) -> str:
    text = str(value or "").replace("–", " ").replace("—", " ").replace("_", " ")
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9.+/-]*", text)
    kept: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        key = token.lower().strip("./-")
        if len(key) < 3 or key in _TARGET_STOPWORDS or key in seen:
            continue
        seen.add(key)
        kept.append(token.strip("./-"))
    return " ".join(kept)


def _precise_search_anchors(value: str) -> list[str]:
    """Extract source-specific filenames and compact identifiers from scientific context."""
    text = str(value or "")
    filenames = re.findall(
        r"\b[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:csv|tsv|txt|json|py|md|xlsx|xls)\b",
        text,
        flags=re.IGNORECASE,
    )
    identifiers = re.findall(r"\b[A-Z][A-Z0-9_-]{2,}\b", text)
    values: list[str] = []
    seen: set[str] = set()
    internal_artifact = re.compile(
        # Keep the legacy evidence_packet name excluded so historical Cycles are never
        # rediscovered as scholarly/local-source material.
        r"^(?:local_source_review_ER\d+|evidence_packet|evidence_manifest|coverage_matrix|judge_packet|handoff|transport)\.json$",
        flags=re.IGNORECASE,
    )
    for raw in [*filenames, *identifiers]:
        if internal_artifact.fullmatch(raw):
            continue
        key = raw.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(raw)
    return values


def _query_variants(
    task_type: str, source_gap_id: str, search_target: str, new_lead: str, domain_context: str = "",
    search_queries: list[Any] | None = None,
) -> list[str]:
    raw = f"{search_target} {new_lead}".strip()
    planned = _planned_queries(search_queries)
    if planned:
        return planned
    if task_type != "targeted_source":
        primary = _compact_query(domain_context.split(",", 1)[0]) if domain_context else ""
        broad = _compact_query(search_target or new_lead)
        clauses = re.split(r"[?;\n]|\b(?:including|compare|versus)\b", raw, flags=re.I)
        candidates = [broad]
        # Compact clauses complement the overview while retaining its subject.
        anchor = primary or " ".join(broad.split()[:3])
        for clause in clauses[1:]:
            compact = _compact_query(clause, max_terms=8)
            if _term_set(compact):
                candidates.append(_compact_query(f"{anchor} {compact}"))
        return _planned_queries(candidates, limit=4) or [source_gap_id]

    def focus(value: str, limit: int) -> str:
        return " ".join(_ascii_focus(value).split()[:limit])

    target_focus = focus(search_target, 18)
    lead_focus = focus(new_lead, 12)
    domain_focus = focus(domain_context, 20)
    precise_anchors = _precise_search_anchors(domain_context)
    anchor_focus = " ".join(precise_anchors[:8])
    combined_focus = focus(f"{search_target} {new_lead} {source_gap_id}", 22)

    def expand_domain(value: str) -> str:
        if re.search(r"\b(?:phantom|chain|chains|elastically)\b", value.lower()):
            return f"{value} polymer"
        return value

    anchored_target = " ".join(value for value in (domain_focus, target_focus) if value)
    anchored_lead = " ".join(value for value in (domain_focus, lead_focus) if value)
    if domain_focus:
        # Once the workflow has supplied a scientific domain anchor, do not add
        # generic unanchored fallbacks. They inflate retrieval cost and can
        # reintroduce polysemous drift (e.g. molecular `cluster` -> regional or
        # astronomical Cluster) even though final ranking may later demote it.
        domain_core = " ".join(domain_focus.split()[:8])
        specific = " ".join(value for value in (anchor_focus, domain_core, target_focus) if value)
        candidates = [
            expand_domain(specific),
            expand_domain(anchored_target),
        ]
    else:
        candidates = [
            expand_domain(target_focus),
            expand_domain(lead_focus),
            expand_domain(combined_focus),
            raw,
        ]
    return list(dict.fromkeys(value for value in candidates if value)) or [source_gap_id]


def _paper_key(record: dict[str, Any]) -> str:
    doi = str(record.get("doi") or "").strip().lower()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", doi, flags=re.I)
    if doi:
        return "doi:" + doi
    arxiv = _identity_helpers().arxiv_key(record.get("arxiv_id"))
    if arxiv:
        return "arxiv:" + arxiv
    title = re.sub(r"\W+", " ", str(record.get("title") or "").lower()).strip()
    return "title:" + title


@lru_cache(maxsize=1)
def _identity_helpers():
    """Use the same identity implementation as the bundled retrieval CLI."""
    from .skills import skills_root
    path = skills_root() / "tm-scholar" / "scripts" / "scholar_identity.py"
    spec = importlib.util.spec_from_file_location("sciver_scholarly_identity", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("scholarly identity helpers unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reconcile_locked_source(papers: list[dict], source_lock: dict) -> list[dict]:
    """Attach a requested source to an unambiguous provider identity and its OA URLs."""
    if not source_lock:
        return papers
    identity = _identity_helpers()
    requested_doi = identity.doi_key(source_lock.get("doi"))
    requested_title = identity.title_key(source_lock.get("title"))
    candidates = []
    for paper in papers:
        if paper.get("source") == "source-lock":
            continue
        actual_doi = identity.doi_key(paper.get("doi"))
        if requested_doi:
            matches = actual_doi == requested_doi
        else:
            matches = bool(requested_title and identity.title_key(paper.get("title")) == requested_title)
            if source_lock.get("year") and paper.get("year"):
                matches = matches and abs(int(source_lock["year"]) - int(paper["year"])) <= 1
            if source_lock.get("authors") and paper.get("authors"):
                matches = matches and identity.authors_compatible(source_lock["authors"], paper["authors"])
        if matches:
            candidates.append(paper)
    stable_ids = {identity.doi_key(paper.get("doi")) or identity.arxiv_key(paper.get("arxiv_id"))
                  or (identity.title_key(paper.get("title")), paper.get("year"), tuple(paper.get("authors") or []))
                  for paper in candidates}
    seeds = [paper for paper in papers if paper.get("source") == "source-lock"]
    if len(stable_ids) != 1:
        for seed in seeds:
            seed["identity_resolution"] = "ambiguous" if candidates else "unresolved"
        return papers
    provider = dict(candidates[0])
    for paper in candidates[1:]:
        provider = identity.merge_paper(provider, paper)
    provider["source_locked"] = True
    provider["source_lock_status"] = str(source_lock.get("status") or "locked")
    provider["identity_resolution"] = "exact_doi" if requested_doi else "unique_exact_title_candidate"
    provider["requested_source"] = {key: source_lock.get(key) for key in ("doi", "title", "year", "authors") if source_lock.get(key)}
    if "is_open_access" in source_lock and not source_lock["is_open_access"]:
        provider["is_open_access"] = False
    return [provider, *(paper for paper in papers if paper not in candidates and paper not in seeds)]


def _proper_name_tokens(value: str) -> list[str]:
    text = str(value or "").replace("–", " ").replace("—", " ")
    tokens = re.findall(r"\b[A-Z][A-Za-z]{3,}\b", text)
    return list(dict.fromkeys(
        token.lower() for token in tokens
        if token.lower() not in _TARGET_STOPWORDS
        and token.lower() not in {"case", "use", "the"}
    ))


def _domain_alignment(record: dict[str, Any], domain_context: str) -> float:
    """Score the primary scientific domain phrase, not just generic query words.

    The caller supplies comma-separated scientific context; the FIRST phrase is
    the primary subject, with no built-in list of disciplines or synonyms.
    """
    primary = str(domain_context or "").split(",", 1)[0]
    anchors = [token.casefold() for token in _ascii_focus(primary).split()]
    if len(anchors) < 2:
        return 0.0
    def singular(token: str) -> str:
        return token[:-1] if len(token) > 4 and token.endswith("s") and not token.endswith("ss") else token

    anchors = [singular(token) for token in anchors]
    title = {singular(token) for token in
             re.findall(r"[a-z0-9]+", str(record.get("title") or "").casefold())}
    abstract = {singular(token) for token in
                re.findall(r"[a-z0-9]+", str(record.get("abstract") or "").casefold())}
    in_title = sum(token in title for token in anchors)
    in_abstract = sum(token in abstract for token in anchors)
    # A majority of a domain phrase appearing together in the title is much
    # stronger evidence than isolated terms such as "network" or "weighted".
    majority = max(2, (len(anchors) + 1) // 2)
    return round((1.8 * in_title / len(anchors) if in_title >= majority else 0.0)
                 + 0.2 * in_abstract / len(anchors), 4)


def _targeted_relevance(record: dict[str, Any], query: str, *, name_source: str = "") -> float:
    tokens = [token.lower() for token in _ascii_focus(query).split()]
    haystack = " ".join(
        [
            str(record.get("title") or ""),
            " ".join(str(v) for v in (record.get("authors") or [])),
            str(record.get("abstract") or ""),
        ]
    ).lower()
    if not tokens:
        return 0.0
    hits = sum(1 for token in tokens if token in haystack)
    title_authors = (
        str(record.get("title") or "") + " " + " ".join(str(v) for v in (record.get("authors") or []))
    ).lower()
    strong = sum(1 for token in tokens if token in title_authors)
    proper = _proper_name_tokens(name_source)
    proper_hits = sum(1 for token in proper if token in title_authors)
    domain_tokens = [
        token for token in tokens
        if token not in proper and token not in {"case", "criterion", "membership", "periodic"}
    ]
    domain_hits = sum(1 for token in domain_tokens if token in haystack)
    proper_bonus = (
        1.5 * proper_hits / max(1, len(proper))
        if proper_hits and domain_hits >= 2 else 0.0
    )
    return hits / len(tokens) + 0.35 * strong / len(tokens) + proper_bonus


def _write_evidence_index(retrieval_dir: Path, papers: list[dict[str, Any]],
                          query_runs: list[dict[str, Any]], *, top_k: int = 6,
                          reading_count: int = 0, citation_runs: list[dict] | None = None) -> Path:
    """Small first-read index. The full papers.json remains available on demand."""
    top = papers[:top_k]
    path = retrieval_dir / "evidence_index.md"
    errors = sum(int(run.get("connector_error_count") or 0) for run in query_runs)
    lines = [
        "# Literature evidence index (metadata-first)",
        "", f"Total metadata candidates: {len(papers)}; visible: {len(top)}; connector errors: {errors}",
        f"Materialized reading items: {reading_count}. A reading file is not automatically a verified scientific claim.",
        "Use this index first; for a locked DOI read paper_focus.json (bounded abstract) next.",
        "For other papers select individual records from papers.json; NEVER cat the entire file.",
        "Citation edges and zero returned citations are indexed, not a complete citation history.",
        "", "## Ranked candidates",
    ]
    compact = []
    for n, p in enumerate(top, 1):
        title = str(p.get("title") or "").replace("\n", " ")[:180]
        ident = str(p.get("doi") or p.get("arxiv_id") or p.get("url") or "")[:120]
        lines.append(f"{n}. {title} ({p.get('year') or 'n.d.'}); ID: {ident}")
        lines.append(f"   - sources: {','.join(p.get('sources') or [str(p.get('source') or '')])}; "
                     f"score: {p.get('score',0)}; OA URL available: {bool(p.get('open_access_url'))}")
        compact.append({key: p.get(key) for key in
                        ("title", "doi", "arxiv_id", "year", "authors", "sources", "score",
                         "targeted_relevance", "open_access_url", "source_locked")})
    lines += ["", "## Targeted source details", "If the target DOI is locked, use "
              "paper_focus.json (maximum 1800 abstract characters). Otherwise select one DOI "
              "from papers.json and print only the relevant abstract excerpt."]
    if errors:
        lines.append("Review retrieval/errors.log for unavailable or rate-limited providers.")
    if citation_runs:
        lines.append(f"Citation trace runs: {len(citation_runs)}; read their short reports before full graph JSON.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (retrieval_dir / "papers_top.json").write_text(
        json.dumps(compact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    locked = next((p for p in papers if p.get("source_locked")), None)
    focus_path = retrieval_dir / "paper_focus.json"
    if locked:
        focus = {key: locked.get(key) for key in
                 ("title", "doi", "year", "authors", "sources", "url",
                  "is_open_access", "open_access_url", "source_lock_status")}
        abstract = html.unescape(re.sub(r"<[^>]+>", " ", str(locked.get("abstract") or "")))
        abstract = re.sub(r"\s+", " ", abstract).strip()
        focus["abstract"] = abstract[:1800]
        focus["evidence_level"] = "abstract" if abstract else "metadata_only"
        focus_path.write_text(
            json.dumps(focus, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif focus_path.exists():
        focus_path.unlink()
    return path


def _write_targeted_index(retrieval_dir: Path, papers: list[dict[str, Any]], query_runs: list[dict[str, Any]]) -> None:
    lines = ["# Targeted-source SDK retrieval", ""]
    for run in query_runs:
        lines.append(f"- query: {run['query']}")
        lines.append(f"  - papers: {run['paper_count']}; connector_errors: {run['connector_error_count']}")
    lines.extend(["", "## Ranked merged candidates", ""])
    for index, paper in enumerate(papers, 1):
        lines.append(
            f"{index}. {paper.get('title','')} ({paper.get('year','')}) — "
            f"{paper.get('doi') or paper.get('url') or 'no identifier'}"
        )
    text = "\n".join(lines) + "\n"
    (retrieval_dir / "report.md").write_text(text, encoding="utf-8")
    (retrieval_dir / "topic_brief.md").write_text(text, encoding="utf-8")
    (retrieval_dir / "search_log.md").write_text(
        json.dumps({"query_runs": query_runs}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return (cleaned[:72] or hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]).lower()


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default

def _source_lock_record(source_lock: dict[str, Any]) -> dict[str, Any]:
    doi = str(source_lock.get("doi") or "").strip()
    title = str(source_lock.get("title") or "").strip()
    return {
        "title": title,
        "authors": list(source_lock.get("authors") or []),
        "year": source_lock.get("year"),
        "venue": str(source_lock.get("venue") or ""),
        "doi": doi,
        "arxiv_id": str(source_lock.get("arxiv_id") or ""),
        "url": str(source_lock.get("url") or (f"https://doi.org/{doi}" if doi else "")),
        "abstract": str(source_lock.get("abstract") or ""),
        "source": "source-lock",
        "citation_count": int(source_lock.get("citation_count") or 0),
        "is_open_access": bool(source_lock.get("is_open_access")),
        "open_access_url": str(source_lock.get("open_access_url") or ""),
        "publication_type": str(source_lock.get("publication_type") or "journal-article"),
        "keywords": list(source_lock.get("keywords") or []),
        "source_locked": True,
        "source_lock_status": str(source_lock.get("status") or "locked"),
    }
