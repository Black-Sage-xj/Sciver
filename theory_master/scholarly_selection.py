from __future__ import annotations

"""Query generation, candidate ranking, and source-lock helpers for scholarly transport."""

import hashlib
import html
import json
import re
from pathlib import Path
from typing import Any

_TARGET_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "use", "terms", "trace",
    "citations", "citation", "original", "definition", "authoritative", "later",
    "interpretations", "interpretation", "source", "sources", "required", "only",
}


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
    task_type: str, source_gap_id: str, search_target: str, new_lead: str, domain_context: str = ""
) -> list[str]:
    raw = f"{search_target} {new_lead}".strip()
    if task_type != "targeted_source":
        return [raw or source_gap_id]

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
    title = re.sub(r"\W+", " ", str(record.get("title") or "").lower()).strip()
    return "title:" + title


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
