"""Structural and provenance checks for generated reports and independent reviews.

These checks do not adjudicate science or replace the model review.  In
particular, membership in ``source_index`` proves a reference exists, not that
it supports a sentence; the independent reviewer must inspect that content.
"""

from __future__ import annotations

from copy import deepcopy
import re
from collections.abc import Callable, Mapping
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from jsonschema import Draft202012Validator

from .prompts import (
    LEGACY_REPORT_SCHEMA, LEGACY_REVIEW_CRITERIA, LEGACY_REVIEW_SCHEMA,
    REPORT_SCHEMA, REVIEW_CRITERIA, REVIEW_SCHEMA, reviewer_prompt,
)
from .reading import _MATH, _outside
from .markdown import CODE

_CITATION = re.compile(r"(?<!!)\[\[([^\]\n]+)\]\]")


def canonicalize_report_references(report: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Expand exact short material IDs while preserving the archived source keys."""
    aliases = {}
    for ref, record in snapshot["source_index"].items():
        short = record.get("material_id")
        if short:
            aliases.setdefault(short, set()).add(ref)
    resolved = {short: next(iter(refs)) for short, refs in aliases.items() if len(refs) == 1}
    result = deepcopy(report)
    for field in ("claims", "research_path", "research_story", "limitations", "figures"):
        rows = result.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            refs = row.get("source_refs")
            if isinstance(refs, list) and all(isinstance(ref, str) for ref in refs):
                row["source_refs"] = list(dict.fromkeys(resolved.get(ref, ref) for ref in refs))
            if isinstance(row.get("source_ref"), str):
                row["source_ref"] = resolved.get(row["source_ref"], row["source_ref"])

    def replace_citation(match: re.Match) -> str:
        ref = re.split(r"[|#]", match[1], maxsplit=1)[0]
        if ref not in resolved:
            return match[0]
        return "[[" + resolved[ref] + match[1][len(ref):] + "]]"

    if isinstance(result.get("markdown"), str):
        result["markdown"] = _outside(result["markdown"], CODE,
            lambda text: _outside(text, _MATH, lambda prose: _CITATION.sub(replace_citation, prose)))
    return result


def _schema_errors(value: Any, schema: dict[str, Any], name: str) -> list[str]:
    errors = sorted(Draft202012Validator(schema).iter_errors(value),
                    key=lambda item: "/".join(map(str, item.absolute_path)))
    return [f"{name}{''.join(f'[{part}]' if isinstance(part, int) else '.' + part for part in error.absolute_path)}: {error.message}"
            for error in errors]


def _report_schema(report: Any) -> dict[str, Any]:
    # Stored v1 reports remain readable and reviewable. New writers receive v2.
    return LEGACY_REPORT_SCHEMA if isinstance(report, Mapping) and report.get("schema_version") == "1" else REPORT_SCHEMA


def _source_refs(snapshot: Any, errors: list[str]) -> set[str]:
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("source_index"), Mapping):
        errors.append("snapshot.source_index: expected the canonical source-reference mapping")
        return set()
    return set(snapshot["source_index"])


def _ids(snapshot: Mapping[str, Any], key: str) -> set[str]:
    return {str(item["id"]) for item in snapshot.get(key, [])
            if isinstance(item, Mapping) and item.get("id") is not None}


def _check_refs(refs: list[str], allowed: set[str], location: str,
                errors: list[str]) -> None:
    for ref in refs:
        if ref not in allowed:
            errors.append(f"{location}: unknown reference {ref!r}")


def _unique_ids(items: list[dict[str, Any]], field: str, location: str,
                errors: list[str]) -> set[str]:
    values = [item[field] for item in items]
    seen: set[str] = set()
    for value in values:
        if value in seen:
            errors.append(f"{location}: duplicate {field} {value!r}")
        seen.add(value)
    return seen


def _markdown_images(markdown: str) -> list[str]:
    """Read conventional Markdown, Obsidian embeds and HTML image targets."""
    markdown = CODE.sub("", markdown)
    targets = [match.group(1) or match.group(2) for match in re.finditer(
        r"!\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))", markdown)]
    targets.extend(match.group(1).split("|", 1)[0] for match in re.finditer(
        r"!\[\[([^\]\n]+)\]\]", markdown))
    class Images(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "img":
                targets.extend(value for name, value in attrs if name == "src" and value)
    Images().feed(markdown)
    return [unquote(value) for value in targets]


def validate_report(report: Any, snapshot: Any) -> list[str]:
    """Return actionable validation errors; an empty list permits model review."""
    errors = _schema_errors(report, _report_schema(report), "report")
    if errors:
        return errors
    sources = _source_refs(snapshot, errors)
    if errors:
        return errors
    source_index = snapshot["source_index"]
    prose = _MATH.sub("", CODE.sub("", report["markdown"]))
    markdown_refs = {match.split("|", 1)[0].split("#", 1)[0].strip() for match in _CITATION.findall(prose)}
    _check_refs(sorted(markdown_refs), sources, "report.markdown", errors)
    hypotheses, outcomes = _ids(snapshot, "hypotheses"), _ids(snapshot, "outcomes")
    claim_ids = _unique_ids(report["claims"], "id", "report.claims", errors)
    _unique_ids(report["requirements"], "requirement_id", "report.requirements", errors)
    _unique_ids(report["figures"], "id", "report.figures", errors)
    selected = set(report["selected_hypothesis_ids"])
    _check_refs(report["selected_hypothesis_ids"], hypotheses,
                "report.selected_hypothesis_ids", errors)

    for index, claim in enumerate(report["claims"]):
        location = f"report.claims[{index}]"
        _check_refs(claim["source_refs"], sources, location + ".source_refs", errors)
        _check_refs(claim["hypothesis_ids"], hypotheses, location + ".hypothesis_ids", errors)
        _check_refs(claim["outcome_ids"], outcomes, location + ".outcome_ids", errors)
        visible_support = [ref for ref in claim["source_refs"] if isinstance(source_index.get(ref), Mapping)
                           and source_index[ref].get("visibility", "full") != "index_only"]
        if not visible_support:
            errors.append(location + ".source_refs: index_only material cannot be the sole support for a substantive claim")

    for index, requirement in enumerate(report["requirements"]):
        location = f"report.requirements[{index}]"
        _check_refs(requirement["claim_ids"], claim_ids, location + ".claim_ids", errors)
        if requirement["status"] != "unresolved" and not requirement["claim_ids"]:
            errors.append(location + ".claim_ids: answered requirements need a sourced claim")

    path_ids = set()
    for index, step in enumerate(report["research_path"]):
        location = f"report.research_path[{index}]"
        path_ids.add(step["hypothesis_id"])
        _check_refs([step["hypothesis_id"]], hypotheses, location + ".hypothesis_id", errors)
        _check_refs(step["outcome_ids"], outcomes, location + ".outcome_ids", errors)
        _check_refs(step["source_refs"], sources, location + ".source_refs", errors)
        if step["hypothesis_id"] not in selected:
            errors.append(location + ".hypothesis_id: research path must use selected hypotheses")
    for missing in sorted(selected - path_ids):
        errors.append(f"report.research_path: selected hypothesis {missing!r} has no path entry")

    outcome_hypotheses = {str(row["id"]): row.get("hypothesis_id") for row in snapshot.get("outcomes", [])
                          if isinstance(row, Mapping) and row.get("id") is not None}
    for index, stage in enumerate(report.get("research_story", [])):
        location = f"report.research_story[{index}]"
        _check_refs(stage["hypothesis_ids"], hypotheses, location + ".hypothesis_ids", errors)
        _check_refs(stage["outcome_ids"], outcomes, location + ".outcome_ids", errors)
        _check_refs(stage["source_refs"], sources, location + ".source_refs", errors)
        for hypothesis_id in stage["hypothesis_ids"]:
            if hypothesis_id not in selected:
                errors.append(location + f".hypothesis_ids: stage hypothesis {hypothesis_id!r} must be selected")
        for outcome_id in stage["outcome_ids"]:
            hypothesis_id = outcome_hypotheses.get(outcome_id)
            if hypothesis_id and hypothesis_id not in stage["hypothesis_ids"]:
                errors.append(location + f".outcome_ids: {outcome_id!r} belongs to hypothesis {hypothesis_id!r} outside this stage")
        visible_support = [ref for ref in stage["source_refs"] if isinstance(source_index.get(ref), Mapping)
                           and source_index[ref].get("visibility", "full") != "index_only"]
        if not visible_support:
            errors.append(location + ".source_refs: index_only material cannot be the sole support for a research stage")

    for index, limitation in enumerate(report["limitations"]):
        _check_refs(limitation["source_refs"], sources,
                    f"report.limitations[{index}].source_refs", errors)

    originals = {str(item["id"]): item for item in snapshot.get("figures", [])
                 if isinstance(item, Mapping) and item.get("id")}
    image_targets = _markdown_images(report["markdown"])
    approved_targets = set()
    for index, figure in enumerate(report["figures"]):
        location = f"report.figures[{index}]"
        _check_refs([figure["source_ref"]], sources, location + ".source_ref", errors)
        _check_refs(figure["claim_ids"], claim_ids, location + ".claim_ids", errors)
        original = originals.get(figure["id"])
        if original is None:
            errors.append(location + ".id: figure is absent from the snapshot")
            continue
        canonical_path = original.get("path") or original.get("frozen_path") or original.get("source_path")
        if not canonical_path or figure["path"] != str(canonical_path):
            errors.append(location + ".path: must equal the supplied original/frozen figure path")
        else:
            approved_targets.add(figure["path"])
        if figure["source_ref"] != original.get("source_ref"):
            errors.append(location + ".source_ref: does not identify this figure's source")
        if figure["path"] not in image_targets:
            errors.append(location + ".path: selected figure is not embedded in report.markdown")
        status = original.get("status")
        if status and status not in {"available", "frozen", "ok"}:
            errors.append(location + f": source figure is unavailable ({status})")
    for target in image_targets:
        if target not in approved_targets:
            errors.append(f"report.markdown: image {target!r} is not an approved snapshot figure")
    if re.search(r"<\s*(?:svg|canvas)\b", CODE.sub("", report["markdown"]), flags=re.IGNORECASE):
        errors.append("report.markdown: inline generated graphics are not approved snapshot figures")
    return errors


def _check_location(location: str, report: dict[str, Any], derived_visuals: list | None = None) -> str | None:
    fields = ("title|abstract|markdown|claims|selected_hypothesis_ids|figures|requirements|"
              "research_path|research_story|limitations|derived_visuals")
    location = re.split(rf"[；、]\s*(?=(?:{fields})\b)", location, maxsplit=1)[0]
    match = re.fullmatch(
        rf"({fields})(?:\[([^\]{{}}]+)\])?(?:[.#:： (（].*)?", location)
    if not match:
        return "must identify a report field, array item or Markdown section"
    field, raw_index = match.groups()
    if field != "derived_visuals" and field not in report:
        return "must identify a field present in this report"
    if raw_index is not None:
        value = derived_visuals if field == "derived_visuals" else report.get(field)
        if not isinstance(value, list):
            return "array index is outside this report"
        id_field = {"claims": "id", "requirements": "requirement_id", "figures": "id",
                    "research_path": "hypothesis_id"}.get(field)
        for index in (part.strip() for part in raw_index.split(",")):
            if index.isdecimal():
                if int(index) >= len(value):
                    return "array index is outside this report"
            elif id_field is None or not any(isinstance(row, Mapping) and row.get(id_field) == index for row in value):
                return "item identifier is absent from this report field"
    return None


def validate_review(review: Any, report: Any, snapshot: Any, *, derived_visuals: list | None = None,
                    allow_legacy: bool = True) -> list[str]:
    """Check the applicable review contract, issue references and verdict consistency."""
    legacy = allow_legacy and isinstance(review, Mapping) and review.get("schema_version") == "1"
    errors = _schema_errors(review, LEGACY_REVIEW_SCHEMA if legacy else REVIEW_SCHEMA, "review")
    if errors:
        return errors
    report_errors = _schema_errors(report, _report_schema(report), "report")
    if report_errors:
        return report_errors
    sources = _source_refs(snapshot, errors)
    seen: set[str] = set()
    failed: set[str] = set()
    for index, dimension in enumerate(review["dimensions"]):
        location = f"review.dimensions[{index}]"
        criterion = dimension["criterion"]
        if criterion in seen:
            errors.append(location + ".criterion: duplicate review dimension")
        seen.add(criterion)
        _check_refs(dimension["source_refs"], sources, location + ".source_refs", errors)
        if dimension["status"] == "fail":
            failed.add(criterion)
        if dimension["status"] == "not_applicable" and (
            criterion != "figures_and_captions" or report["figures"]
        ):
            errors.append(location + ".status: not_applicable is only valid when no figures exist")
    criteria = LEGACY_REVIEW_CRITERIA if legacy else REVIEW_CRITERIA
    for missing in sorted(set(criteria) - seen):
        errors.append(f"review.dimensions: missing criterion {missing!r}")

    issue_criteria: set[str] = set()
    severe = False
    for index, issue in enumerate(review["issues"]):
        location = f"review.issues[{index}]"
        issue_criteria.add(issue["criterion"])
        _check_refs(issue["source_refs"], sources, location + ".source_refs", errors)
        location_error = _check_location(issue["report_location"], report, derived_visuals)
        if location_error:
            errors.append(location + ".report_location: " + location_error)
        if issue["severity"] in {"blocker", "major"}:
            severe = True
            if issue["criterion"] not in failed:
                errors.append(location + ".criterion: blocker/major issues require a failed dimension")
    for criterion in sorted(failed - issue_criteria):
        errors.append(f"review.issues: failed dimension {criterion!r} needs a specific issue")
    if review["verdict"] == "pass" and (severe or failed):
        errors.append("review.verdict: pass conflicts with failed dimensions or blocker/major issues")
    if review["verdict"] == "revise" and not review["issues"]:
        errors.append("review.verdict: revise requires at least one specific issue")
    return errors


def run_review_agent(report: dict[str, Any], bundle: dict[str, Any], *,
                     frozen: dict[str, Any], publication_root: Path, report_errors: list[str],
                     backend: Callable[[str, str, dict, dict], dict]) -> tuple[dict, list[str]]:
    """Review a finished draft in a new reviewer conversation with only frozen sources."""
    from .render import lineage_svg

    graph = lineage_svg(report, frozen)
    derived_visuals = [{"kind": "research_story", "svg": graph,
        "caption": "按关键问题、检验与转向整理的研究主线；箭头表示下一关键阶段，各阶段附对应来源。"}] if graph else []
    review = backend("reviewer", reviewer_prompt(),
        {"snapshot": bundle, "report": report, "publication_root": str(publication_root),
         "derived_visuals": derived_visuals, "mechanical_validation_errors": report_errors}, REVIEW_SCHEMA)
    return review, validate_review(review, report, bundle, derived_visuals=derived_visuals, allow_legacy=False)
