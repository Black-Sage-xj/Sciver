"""Declared evaluation identities and known reuse, never a blind-access attestation."""
from __future__ import annotations

import json
from typing import Any, Mapping


SCIENTIFIC_CHECK_SCHEMA = {
    key: {"assessment": "clear|blocker|not_applicable", "basis": "concrete source facts/comparison or reason inapplicable"}
    for key in ("source_fidelity", "definition_consistency", "discriminating_test", "evidence_identity")
}


def validate_scientific_checks(checks: Any, *, qualified: bool) -> None:
    if not isinstance(checks, dict) or set(checks) != set(SCIENTIFIC_CHECK_SCHEMA):
        raise ValueError("scientific_checks requires source_fidelity/definition_consistency/discriminating_test/evidence_identity")
    for row in checks.values():
        if (not isinstance(row, dict) or set(row) != {"assessment", "basis"}
                or row["assessment"] not in {"clear", "blocker", "not_applicable"}
                or not isinstance(row["basis"], str) or not row["basis"].strip()):
            raise ValueError("scientific check requires assessment and concrete basis")
        if qualified and row["assessment"] == "blocker":
            raise ValueError("qualified review cannot retain a scientific check blocker")


EVALUATION_MATERIAL_SCHEMA = {
    "unit_id": "stable dataset/sample-partition identity; preserve across file/method renames",
    "use": "development|calibration|evaluation",
    "independence": "not_required|ordinary|protected_final",
    "prior_feedback": "none|present|unknown; includes metrics and model/design selection, not only labels",
    "history_basis": "source references and timing supporting the declared history and partition boundary",
}


def validate_materials(materials: Any) -> None:
    if not isinstance(materials, list):
        raise ValueError("evaluation_materials must be an array")
    seen: set[str] = set()
    for row in materials:
        if not isinstance(row, dict) or set(row) != set(EVALUATION_MATERIAL_SCHEMA):
            raise ValueError("evaluation material requires unit_id/use/independence/prior_feedback/history_basis")
        for field in ("unit_id", "history_basis"):
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(f"evaluation material {field} must be nonempty")
        if row["unit_id"] != row["unit_id"].strip() or row["unit_id"] in seen:
            raise ValueError("evaluation material unit_id must be canonical and unique")
        seen.add(row["unit_id"])
        if row["use"] not in {"development", "calibration", "evaluation"}:
            raise ValueError("invalid evaluation material use")
        if row["independence"] not in {"not_required", "ordinary", "protected_final"}:
            raise ValueError("invalid evaluation material independence")
        if row["prior_feedback"] not in {"none", "present", "unknown"}:
            raise ValueError("invalid evaluation material prior_feedback")
        if row["independence"] == "protected_final" and row["use"] != "evaluation":
            raise ValueError("protected_final is only an evaluation role")


def require_eligible_identity(candidate: Mapping[str, Any], history: list[dict[str, Any]]) -> None:
    materials = candidate.get("evaluation_materials", [])
    validate_materials(materials)
    for row in materials:
        if row["independence"] != "protected_final":
            continue
        known = [item for item in history if item["unit_id"] == row["unit_id"]
                 and item.get("terminal_outcome")]
        if row["prior_feedback"] != "none" or known:
            raise ValueError(
                f"protected_final identity unavailable for {row['unit_id']}: prior feedback or unknown history; "
                "relocking or an unlabeled method cannot restore it. Revise the claim or use eligible new units"
            )


def freeze_materials(store: Any, hypothesis_id: str, candidate: Mapping[str, Any]) -> None:
    with store.transaction():
        _freeze_materials(store, hypothesis_id, candidate)


def _freeze_materials(store: Any, hypothesis_id: str, candidate: Mapping[str, Any]) -> None:
    rows = candidate.get("evaluation_materials", [])
    validate_materials(rows)
    old = store.db.execute("SELECT source_json FROM hypothesis_evaluation_materials WHERE hypothesis_id=?",
                           (hypothesis_id,)).fetchone()
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    if old is not None:
        if json.loads(old[0]) != rows:
            raise ValueError("Frozen evaluation materials cannot change on replay")
        return
    require_eligible_identity(candidate, evaluation_history(store))
    store.db.execute("INSERT INTO hypothesis_evaluation_materials VALUES (?,?)", (hypothesis_id, encoded))


def evaluation_history(store: Any) -> list[dict[str, Any]]:
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hypothesis_evaluation_materials'").fetchone():
        return []
    result = []
    for row in store.db.execute(
        "SELECT m.*,o.cycle_id,EXISTS(SELECT 1 FROM cycle_evaluations e "
        "WHERE e.hypothesis_id=m.hypothesis_id AND e.verdict IN ('supported','rejected','open')) AS terminal_outcome "
        "FROM hypothesis_evaluation_materials m LEFT JOIN hypothesis_cycle_owners o ON o.hypothesis_id=m.hypothesis_id "
        "ORDER BY m.hypothesis_id"
    ):
        result.extend({**item, "hypothesis_id": row["hypothesis_id"], "cycle_id": row["cycle_id"],
                       "terminal_outcome": bool(row["terminal_outcome"])} for item in json.loads(row["source_json"]))
    return result
