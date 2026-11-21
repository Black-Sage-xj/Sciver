"""Verify an operator-selected archival delivery against an existing Goal assessment."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

from .public_submission import read_bounded_regular_file, strict_json
from .science_graph_store import ScienceGraphStore
from .specification import CYCLE_MARKER


SELECTION = Path(".theory-master/archival-goal-delivery.json")
SELECTION_LIMIT = 256 * 1024
SCHEMA = "theory-master/archival-goal-delivery/v1"
_SELECTION_KEYS = {
    "schema", "master_id", "stopped_cycle", "anchor_cycle", "goal_assessment",
    "prior_delivery_revision", "material", "outcome_ids", "sources",
}
_SHA256 = re.compile(r"[a-f0-9]{64}")
_CYCLE = re.compile(r"[1-9][0-9]*")
_FINAL_VERDICTS = {"supported", "open", "rejected"}
_MATERIAL_LIMIT = 1024 * 1024
_SOURCE_LIMIT = 64 * 1024 * 1024
_SOURCE_TOTAL_LIMIT = 128 * 1024 * 1024
_REVISION_LIMIT = 16_000_000
_MAX_REFERENCES = 64


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _text(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 4096 and value.strip() == value and "\x00" not in value


def _cycle_id(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 20 and _CYCLE.fullmatch(value) is not None


def _material_asset(home: Path, material: Any) -> dict:
    if (not isinstance(material, dict) or set(material) != {"path", "size", "sha256"}
            or not _text(material["path"]) or not _digest(material["sha256"])
            or type(material["size"]) is not int or not 0 < material["size"] <= _MATERIAL_LIMIT):
        raise ValueError("Invalid archival Goal material identity")
    relative = Path(material["path"])
    if (relative.is_absolute() or relative.as_posix() != material["path"] or ".." in relative.parts
            or "\\" in material["path"] or relative.suffix != ".md"):
        raise ValueError("Archival Goal material must be a task-relative Markdown file")
    path = home / relative
    data = read_bounded_regular_file(path, material["size"])
    text = data.decode("utf-8")
    if len(data) != material["size"] or _sha(data) != material["sha256"] or not text.strip() or "\x00" in text:
        raise ValueError("Archival Goal material changed or is not UTF-8 Markdown")
    return {**material, "reference": str(path), "path": str(path), "status": "available"}


def _frozen_source_assets(sources: Any, catalog: Any, roots: tuple[Path, ...]) -> list[dict]:
    if (not isinstance(sources, list) or not 0 < len(sources) <= _MAX_REFERENCES
            or not isinstance(catalog, list)):
        raise ValueError("Archival Goal delivery requires bounded prior material references")
    by_reference: dict[str, list[dict]] = {}
    for row in catalog:
        if isinstance(row, dict) and _text(row.get("reference")):
            by_reference.setdefault(row["reference"], []).append(row)
    seen: set[str] = set()
    seen_paths: set[Path] = set()
    assets = []
    remaining = _SOURCE_TOTAL_LIMIT
    for pin in sources:
        if (not isinstance(pin, dict) or set(pin) != {"reference", "sha256"}
                or not _text(pin["reference"]) or not _digest(pin["sha256"]) or pin["reference"] in seen):
            raise ValueError("Invalid or repeated archival Goal source identity")
        seen.add(pin["reference"])
        candidates = by_reference.get(pin["reference"], [])
        if len(candidates) != 1:
            raise ValueError("Archival Goal source is ambiguous or absent from the selected prior revision")
        prior = candidates[0]
        if (prior.get("status") != "available" or prior.get("sha256") != pin["sha256"]
                or not _text(prior.get("path"))):
            raise ValueError("Archival Goal source does not match its prior revision")
        path = Path(prior["path"])
        if (not path.is_absolute() or path.as_posix() != prior["path"] or ".." in path.parts
                or not any(path.is_relative_to(root) for root in roots) or path in seen_paths):
            raise ValueError("Archival Goal source is outside this task or duplicates another source")
        seen_paths.add(path)
        data = read_bounded_regular_file(path, min(_SOURCE_LIMIT, remaining))
        if _sha(data) != pin["sha256"]:
            raise ValueError("Archival Goal source changed after the prior assessment")
        remaining -= len(data)
        assets.append({**pin, "path": str(path), "size": len(data), "status": "available"})
    return assets


def _prior_assessment(graph: Path, anchor_cycle: str, selection: dict) -> dict:
    outcome_ids = selection["outcome_ids"]
    if (not isinstance(outcome_ids, list) or not 0 < len(outcome_ids) <= _MAX_REFERENCES
            or any(not _text(value) for value in outcome_ids) or len(set(outcome_ids)) != len(outcome_ids)):
        raise ValueError("Invalid archival Goal Outcome identities")
    try:
        with ScienceGraphStore(graph, readonly=True) as store:
            store.db.execute("BEGIN")
            assessment = store.goal_gate_assessment(
                anchor_cycle, assessment_id=selection["goal_assessment"],
                delivery_revision=selection["prior_delivery_revision"],
            )
            if (not assessment or assessment.get("legacy") or assessment["verdict"] != "not_passed"
                    or assessment["outcome_id"] not in outcome_ids):
                raise ValueError("Archival Goal delivery must name a prior not-passed assessment and its Outcome")
            for outcome_id in outcome_ids:
                outcome = store.db.execute(
                    "SELECT cycle_id,verdict FROM cycle_evaluations WHERE id=?", (outcome_id,)
                ).fetchone()
                if (not outcome or not _cycle_id(outcome["cycle_id"])
                        or outcome_id != f"cycle-outcome-{outcome['cycle_id']}"
                        or outcome["verdict"] not in _FINAL_VERDICTS):
                    raise ValueError("Archival Goal delivery names an uncommitted or foreign Outcome")
                if outcome_id == assessment["outcome_id"] and (
                        outcome["cycle_id"] != anchor_cycle or outcome["verdict"] != "supported"):
                    raise ValueError("Archival Goal anchor Outcome changed")
            return assessment
    except sqlite3.Error as exc:
        raise ValueError("Cannot verify archival Goal Outcomes in the task graph") from exc


def validate_archival_goal_delivery(spec: Any, graph: Path, anchor: Path, selection: Any, *,
                                    selection_id: str) -> dict:
    """Verify a staged selection without publishing it or altering research records."""
    from .delivery_repair import _canonical
    from .operator_public_submission import _check_frozen_assessment

    root = Path(spec.research_root)
    home = Path(getattr(spec, "task_home", None) or Path(spec.document).parent)
    if (not isinstance(selection, dict) or set(selection) != _SELECTION_KEYS or selection["schema"] != SCHEMA
            or not _digest(selection_id) or type(selection["master_id"]) is not int
            or selection["master_id"] < 0 or selection["master_id"] != getattr(spec, "master_id", None)
            or not _cycle_id(selection["anchor_cycle"]) or not _cycle_id(selection["stopped_cycle"])
            or int(selection["stopped_cycle"]) <= int(selection["anchor_cycle"])
            or not _text(selection["goal_assessment"]) or not _digest(selection["prior_delivery_revision"])):
        raise ValueError("Invalid archival Goal selection for this task")
    if anchor != root / selection["anchor_cycle"] or not root.is_absolute() or not home.is_absolute():
        raise ValueError("Archival Goal anchor is outside the canonical task")
    for cycle in (anchor, root / selection["stopped_cycle"]):
        read_bounded_regular_file(cycle / CYCLE_MARKER, 4096)
    configured_graph = Path(getattr(spec, "graph_database", None) or root / "science_graph.sqlite3")
    if (graph != configured_graph or not graph.is_absolute() or ".." in graph.parts or not graph.is_file()
            or any(part.is_symlink() for part in (graph, *graph.parents))):
        raise ValueError("Archival Goal verification requires this task's canonical graph")
    assessment = _prior_assessment(graph, anchor.name, selection)
    _check_frozen_assessment(root, anchor, assessment)
    revision = selection["prior_delivery_revision"]
    frozen = strict_json(read_bounded_regular_file(root / "delivery_revisions" / f"{revision}.json", _REVISION_LIMIT))
    if (not isinstance(frozen, dict) or frozen.get("schema") != "theory-master/delivery-revision/v1"
            or frozen.get("cycle_id") != anchor.name or frozen.get("science_digest") != assessment["science_digest"]
            or _sha(_canonical(frozen)) != revision):
        raise ValueError("Prior archival Goal delivery revision changed")
    material = _material_asset(home, selection["material"])
    benchmark = getattr(spec, "benchmark_root", None)
    roots = (root, home)
    if benchmark is not None and Path(benchmark).is_relative_to(root.parent):
        roots += (Path(benchmark),)
    source_assets = _frozen_source_assets(selection["sources"], frozen.get("goal_material_sources"), roots)
    return {**selection, "selection_id": selection_id, "material": material, "source_assets": source_assets}


def read_archival_goal_delivery(spec: Any, graph: Path, anchor: Path) -> dict | None:
    """Read the operator's selection and reverify every selected byte identity."""
    try:
        raw = read_bounded_regular_file(Path(spec.research_root) / SELECTION, SELECTION_LIMIT)
        return validate_archival_goal_delivery(spec, graph, anchor, strict_json(raw), selection_id=_sha(raw))
    except (KeyError, OSError, TypeError, ValueError):
        return None
