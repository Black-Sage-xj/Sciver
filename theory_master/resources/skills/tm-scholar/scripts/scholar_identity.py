"""Canonical paper identity and conservative metadata merging for TM Scholar.

Provider-independent rules: stable identifiers win; fuzzy titles alone never prove identity.
No third-party retrieval code is bundled.
"""
from __future__ import annotations

import math
import re
import unicodedata
from urllib.parse import unquote


def doi_key(value: object) -> str:
    raw = unquote(str(value or "")).strip()
    raw = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", raw, flags=re.I)
    raw = raw.strip().strip(".,;")
    return raw.casefold() if re.fullmatch(r"10\.\d{1,9}/[^\s]+", raw, re.I) else ""


def arxiv_key(value: object) -> str:
    raw = unquote(str(value or "")).strip()
    raw = re.sub(r"^(?:https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/|arxiv:)", "", raw, flags=re.I)
    raw = re.sub(r"\.pdf$", "", raw, flags=re.I)
    raw = re.sub(r"v\d+$", "", raw, flags=re.I)
    return raw.casefold() if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})", raw, re.I) else ""


def title_key(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def _author_tokens(value: object) -> list[str]:
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", normalized)


def authors_compatible(left: list, right: list) -> bool:
    if not left or not right:
        return True  # unknown is not contradictory evidence
    for a in left:
        aa = _author_tokens(a)
        for b in right:
            bb = _author_tokens(b)
            if not aa or not bb:
                continue
            if aa[-1] == bb[-1] and (
                aa[0] == bb[0] or aa[0][0] == bb[0][0] and (len(aa[0]) == 1 or len(bb[0]) == 1)
            ):
                return True
            # Some metadata providers reverse the given/family-name order.
            if len(aa) > 1 and len(bb) > 1 and set(aa) == set(bb):
                return True
    return False


def same_paper(a: dict, b: dict) -> bool:
    ad, bd = doi_key(a.get("doi")), doi_key(b.get("doi"))
    ax, bx = arxiv_key(a.get("arxiv_id")), arxiv_key(b.get("arxiv_id"))
    if ad and bd and ad != bd:
        return False
    if ax and bx and ax != bx:
        return False
    # ORCID disagreement is decisive for weak title/year matching, not for shared DOI.
    if not (ad and ad == bd or ax and ax == bx):
        left_ids = {str(v).casefold() for v in a.get("author_ids") or []}
        right_ids = {str(v).casefold() for v in b.get("author_ids") or []}
        if left_ids and right_ids and left_ids.isdisjoint(right_ids):
            return False
    if ad and ad == bd or ax and ax == bx:
        return True
    ta, tb = title_key(a.get("title")), title_key(b.get("title"))
    if not ta or ta != tb:
        return False
    ay, by = a.get("year"), b.get("year")
    if ay and by and abs(int(ay) - int(by)) > 1:
        return False
    left_authors, right_authors = a.get("authors") or [], b.get("authors") or []
    if not (ay and by or left_authors and right_authors):
        return False  # title alone is insufficient for an identity decision
    return authors_compatible(left_authors, right_authors)


def merge_paper(old: dict, new: dict) -> dict:
    result = dict(old)
    result["sources"] = sorted(set((old.get("sources") or [old.get("source")]) +
                                   (new.get("sources") or [new.get("source")])) - {None, ""})
    for key in ("doi", "arxiv_id", "url", "open_access_url", "abstract", "title", "authors"):
        value = new.get(key)
        previous = result.get(key)
        if value and (not previous or key in {"abstract", "title", "authors"} and len(str(value)) > len(str(previous))):
            result[key] = value
    result["open_access_urls"] = list(dict.fromkeys(
        str(url) for item in (old, new) for url in (
            [item.get("open_access_url")] + list(item.get("open_access_urls") or []))
        if url))
    if not result.get("open_access_url") and result["open_access_urls"]:
        result["open_access_url"] = result["open_access_urls"][0]
    result["citation_count"] = max(int(old.get("citation_count") or 0),
                                   int(new.get("citation_count") or 0))
    result["is_open_access"] = bool(result.get("is_open_access") or new.get("is_open_access"))
    result["author_ids"] = sorted(set((old.get("author_ids") or []) + (new.get("author_ids") or [])))
    return result


def score_paper(row: dict, query: str) -> float:
    terms = set(re.findall(r"[^\W_]+", title_key(query)))
    if not terms:
        return 0.0
    title_terms = set(title_key(row.get("title")).split())
    abstract_terms = set(title_key(row.get("abstract")).split())
    relevance = 0.75 * len(terms & title_terms) / len(terms)
    relevance += 0.25 * len(terms & abstract_terms) / len(terms)
    citations = min(max(int(row.get("citation_count") or 0), 0), 10000)
    provenance = min(len(row.get("sources") or []), 4)
    return round(relevance + 0.045 * math.log1p(citations) / math.log1p(10000) +
                 0.015 * max(0, provenance - 1), 5)


def consolidate(rows: list[dict], query: str, year_min: int | None = None,
                year_max: int | None = None) -> list[dict]:
    groups: list[dict] = []
    for raw in rows:
        if not isinstance(raw, dict) or not title_key(raw.get("title")):
            continue
        row = dict(raw)
        row["doi"] = doi_key(row.get("doi"))
        row["arxiv_id"] = arxiv_key(row.get("arxiv_id"))
        row["sources"] = sorted(set(row.get("sources") or [row.get("source")]) - {None, ""})
        year = row.get("year")
        if year and (year_min is not None and int(year) < year_min or
                     year_max is not None and int(year) > year_max):
            continue
        # Never bridge two distinct DOI identities via a third record without a DOI.
        matches = [i for i, item in enumerate(groups) if same_paper(item, row)]
        if matches:
            winner = groups[matches[0]]
            if any(not same_paper(winner, groups[i]) for i in matches[1:]):
                matches = matches[:1]
            for i in reversed(matches[1:]):
                winner = merge_paper(winner, groups.pop(i))
            groups[matches[0]] = merge_paper(winner, row)
        else:
            groups.append(row)
    for item in groups:
        item["score"] = score_paper(item, query)
    return sorted(groups, key=lambda item: (-item["score"], -(int(item.get("year") or 0)),
                                            title_key(item.get("title"))))