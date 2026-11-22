"""Compact Cycle citations for reading a frozen, fully sourced report.

This is a presentation projection. It neither changes the authored report nor
turns file names or hypothesis numbers into scientific Cycle identities.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from pathlib import PurePath
import re
from typing import Any, Callable
from urllib.parse import unquote

from .markdown import CODE


_GROUP = re.compile(r"(?:〔[ \t]*)?(?<!!)\[\[[^\]\n]+\]\](?:[ \t]*(?:[、,，][ \t]*)?\[\[[^\]\n]+\]\])*(?:[ \t]*〕)?")
_REFERENCE = re.compile(r"\[\[([^\]\n]+)\]\]")
_MATH = re.compile(r"\$\$[\s\S]*?\$\$|(?<!\\)\$(?!\$)[^\n$]+(?<!\\)\$|\\\[[\s\S]*?\\\]|\\\([^\n]*?\\\)")
_CATALOG = re.compile(
    r"(?m)^复现入口按研究问题组织如下[^\n]*\n(?:[ \t]*\n)*"
    r"(?P<items>(?:^[-*+][ \t]+[^\n]*(?:\n|$)(?:^[ \t]*\n)*)+)")
_PARAGRAPH_GAP = re.compile(r"(\n[ \t]*\n+)")
_SEPARATE_LINE = re.compile(r"^[ \t]{0,3}(?:[-*+][ \t]+|[0-9]+[.)][ \t]+|\||#{1,6}[ \t]+)")
_HEADING = re.compile(r"(?m)^ {0,3}##[ \t]+([^\n]+)(?:\n|$)")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _rows(snapshot: Mapping[str, Any], name: str) -> list[Mapping[str, Any]]:
    values = snapshot.get(name, [])
    return [row for row in values if isinstance(row, Mapping)] if isinstance(values, list) else []


def _cycle(value: Any) -> str:
    value = str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) else ""


def _order(value: str) -> tuple:
    return (0, int(value), value) if value.isascii() and value.isdecimal() else (1, value)


def _outside(text: str, pattern: re.Pattern, transform: Callable[[str], str]) -> str:
    parts, offset = [], 0
    for match in pattern.finditer(text):
        parts.extend((transform(text[offset:match.start()]), match[0]))
        offset = match.end()
    parts.append(transform(text[offset:]))
    return "".join(parts)


def _document_aliases(row: Mapping[str, Any]) -> list[str]:
    return list(dict.fromkeys(value for name in ("id", "source_ref", "reference", "source_path")
                              if (value := _text(row.get(name)))))


def _fold_materials(markdown: str) -> tuple[str, int]:
    protected = [(match.start(), match.end()) for match in CODE.finditer(markdown)]
    headings = [match for match in _HEADING.finditer(markdown)
                if not any(start < match.end() and end > match.start() for start, end in protected)]
    parts, offset, folded = [], 0, 0
    for index, heading in enumerate(headings):
        if "复现" not in heading[1]:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        body = markdown[heading.end():end]
        if body.lstrip().startswith('<details class="report-materials">'):
            continue
        parts.extend((markdown[offset:heading.end()],
                      '\n<details class="report-materials">\n<summary>展开执行与复现说明</summary>\n\n',
                      body.strip("\n"), '\n\n</details>\n\n'))
        offset, folded = end, folded + 1
    parts.append(markdown[offset:])
    return "".join(parts), folded


def cycle_reading(report: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Group reading citations by recorded Cycle while retaining full provenance.

    Cycle membership comes from actual round/evidence fields and registered
    aliases. A file may additionally inherit an existing Cycle when its frozen
    source path is inside that exact Cycle directory under the bound task root.
    Ambiguous aliases and unassigned sources never acquire a guessed Cycle.
    """
    source_index = snapshot.get("source_index", {})
    source_index = source_index if isinstance(source_index, Mapping) else {}
    documents = _rows(snapshot, "documents") + _rows(snapshot, "figures")
    records = [(row, kind) for kind in ("outcomes", "preservations") for row in _rows(snapshot, kind)]
    cycles: dict[str, dict[str, Any]] = {}
    owners: dict[str, set[str]] = defaultdict(set)
    doc_alias_owners: dict[str, set[str]] = defaultdict(set)
    for index, document in enumerate(documents):
        identity = _text(document.get("source_ref")) or _text(document.get("id")) or f"document-{index}"
        for alias in _document_aliases(document):
            doc_alias_owners[alias].add(identity)
    ambiguous = {key for key, values in doc_alias_owners.items() if len(values) > 1}
    ambiguous.update(value for value in snapshot.get("ambiguous_source_refs", []) if isinstance(value, str))

    def own(reference: Any, cycle: str) -> None:
        key = _text(reference)
        if key and key not in ambiguous and cycle in cycles:
            owners[key].add(cycle)

    for row, kind in records:
        cycle = _cycle(row.get("cycle_id"))
        if not cycle:
            continue
        item = cycles.setdefault(cycle, {"kind": "cycle", "id": cycle, "cycle_id": cycle,
            "title": "Cycle " + cycle, "source_ref": None, "outcome_ids": []})
        ident = _text(row.get("id")) or _text(row.get("source_ref"))
        if kind == "outcomes" and ident and ident not in item["outcome_ids"]:
            item["outcome_ids"].append(ident)
        for reference in (row.get("id"), row.get("source_ref"), row.get("report_ref"), row.get("hypothesis_id")):
            own(reference, cycle)

    for row in _rows(snapshot, "evidence_records"):
        cycle = _cycle(row.get("cycle_id")) or _cycle(row.get("analysis_cycle"))
        if cycle not in cycles:
            continue
        for reference in (row.get("id"), row.get("source_ref"), row.get("evidence_id"), row.get("manifest_source_ref")):
            own(reference, cycle)
        materials = row.get("execution_materials")
        if isinstance(materials, Mapping):
            own(materials.get("manifest_path"), cycle)

    seal = snapshot.get("goal_terminal")
    if isinstance(seal, Mapping):
        own(seal.get("source_ref"), _cycle(seal.get("source_cycle")))
    for row in _rows(snapshot, "goal_gate_history"):
        own(row.get("source_ref"), _cycle(row.get("cycle_id")))

    bindings = snapshot.get("sources", {})
    root_value = _text(bindings.get("research_root")) if isinstance(bindings, Mapping) else ""
    root = PurePath(root_value) if root_value and PurePath(root_value).is_absolute() else None
    for document in documents:
        source = _text(document.get("source_path"))
        path = PurePath(source) if source and PurePath(source).is_absolute() else None
        if path is None or ".." in path.parts:
            continue
        memberships = set()
        if root and ".." not in root.parts and path.is_relative_to(root):
            parts = path.relative_to(root).parts
            if parts and parts[0] in cycles:
                memberships.add(parts[0])
        for alias in _document_aliases(document):
            for cycle in memberships:
                own(alias, cycle)

    # A recorded alias and its canonical identity share membership in both
    # directions, including extensionless references to frozen round reports.
    parents: dict[str, str] = {}
    def find(key: str) -> str:
        parents.setdefault(key, key)
        start = key
        while parents[key] != key:
            key = parents[key]
        while parents[start] != start:
            previous, parents[start] = parents[start], key
            start = previous
        return key

    def join(values: list[str]) -> None:
        values = [value for value in values if value and value not in ambiguous]
        if values:
            first = find(values[0])
            for value in values[1:]:
                parents[find(value)] = first

    for document in documents:
        join(_document_aliases(document))
    for alias, row in source_index.items():
        if isinstance(alias, str) and isinstance(row, Mapping):
            join([alias, _text(row.get("id"))])
    groups: dict[str, set[str]] = defaultdict(set)
    for ref, memberships in owners.items():
        groups[find(ref)].update(memberships)
    references = {ref: sorted(groups[find(ref)], key=_order) for ref in list(parents)
                  if ref not in ambiguous and groups[find(ref)]}

    by_alias: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for document in documents:
        if not _text(document.get("frozen_path")) or document.get("kind", "document") != "document":
            continue
        if document.get("status") not in {None, "available", "frozen", "ok"}:
            continue
        for alias in _document_aliases(document):
            if alias not in ambiguous:
                by_alias[alias].append(document)
    round_sources: dict[str, set[str]] = defaultdict(set)
    for row, _ in records:
        cycle, reference = _cycle(row.get("cycle_id")), _text(row.get("report_ref"))
        if cycle not in cycles or not reference or reference in ambiguous:
            continue
        index_row = source_index.get(reference, {})
        canonical = _text(index_row.get("id")) if isinstance(index_row, Mapping) else ""
        for document in by_alias.get(reference, []) + by_alias.get(canonical, []):
            key = _text(document.get("source_ref")) or _text(document.get("id"))
            if key and key not in ambiguous:
                round_sources[cycle].add(key)
    for cycle, item in cycles.items():
        candidates = round_sources[cycle]
        item["source_ref"] = next(iter(candidates)) if len(candidates) == 1 else None
        references["cycle:" + cycle] = [cycle]
    cycles = {"cycle:" + key: cycles[key] for key in sorted(cycles, key=_order)}

    markdown = report.get("markdown", "")
    markdown = markdown if isinstance(markdown, str) else ""
    code_spans = [(match.start(), match.end()) for pattern in (CODE, _MATH) for match in pattern.finditer(markdown)]
    removed_catalogs = 0
    def catalog(match: re.Match) -> str:
        nonlocal removed_catalogs
        bullets = [line for line in match["items"].splitlines() if line.strip()]
        protected = any(start < match.end() and end > match.start() for start, end in code_spans)
        if protected or not bullets or any("[[file:" not in line for line in bullets):
            return match[0]
        removed_catalogs += 1
        return ""
    markdown = _CATALOG.sub(catalog, markdown)
    omitted = set()
    def citation_group(match: re.Match, seen: set[str]) -> str:
        selected = set()
        for token in _REFERENCE.findall(match[0]):
            ref = token.split("|", 1)[0].split("#", 1)[0].strip()
            mapped = references.get(ref) or references.get(unquote(ref))
            if mapped:
                selected.update(mapped)
            else:
                omitted.add(ref)
        ordered = sorted(selected - seen, key=_order)
        seen.update(selected)
        if not ordered:
            return ""
        return "〔" + "、".join("[[cycle:" + cycle + "|" + ("Cycle " if index == 0 else "") + cycle + "]]"
                               for index, cycle in enumerate(ordered)) + "〕"

    def compact(code_free: str) -> str:
        seen: set[str] = set()
        previous_separate = False
        def plain(fragment: str) -> str:
            nonlocal previous_separate
            result = []
            for paragraph in _PARAGRAPH_GAP.split(fragment):
                if _PARAGRAPH_GAP.fullmatch(paragraph):
                    seen.clear()
                    previous_separate = False
                    result.append(paragraph)
                    continue
                for line in paragraph.splitlines(keepends=True):
                    separate = bool(_SEPARATE_LINE.match(line))
                    if separate or (previous_separate and line.strip() and not line.startswith((" ", "\t"))):
                        seen.clear()
                    if line.strip():
                        previous_separate = separate or (previous_separate and line.startswith((" ", "\t")))
                    result.append(_GROUP.sub(lambda match: citation_group(match, seen), line))
            return "".join(result)
        return _outside(code_free, _MATH, plain)

    markdown = _outside(markdown, CODE, compact)
    markdown, folded = _fold_materials(markdown)
    return {"markdown": markdown, "cycles": cycles, "ref_cycles": references,
            "omitted_refs": sorted(omitted), "removed_catalog_blocks": removed_catalogs,
            "folded_material_sections": folded}
