"""Exact scientific text shared by qualification, Lock and downstream roles.

Review bindings attest only text identity, never scientific validity, completeness
or compliance by a research script. No scientific text is inferred from rationale.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


SCIENCE_FIELDS = ("hypothesis", "scope", "decisive_test")
REVIEW_PROTOCOL_VERSION = 1


def frozen_science_view(value: Any, *, legacy: bool = False) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    science = {key: value.get(key) for key in SCIENCE_FIELDS}
    if legacy and not science["decisive_test"]:
        science["decisive_test"] = value.get("cycle_milestone")
    return {key: val for key, val in science.items() if val not in (None, "", [], {})}


def serialize_frozen_science(objective: Mapping[str, Any]) -> str:
    # Preserve exact field values, including Unicode and scientific whitespace.
    return json.dumps({key: objective[key] for key in SCIENCE_FIELDS},
                      ensure_ascii=False, separators=(",", ":"))


def frozen_science_digest(objective: Mapping[str, Any]) -> str:
    return hashlib.sha256(serialize_frozen_science(objective).encode("utf-8")).hexdigest()


def review_binding(candidate_id: str, objective: Mapping[str, Any]) -> dict[str, Any]:
    return {"protocol_version": REVIEW_PROTOCOL_VERSION, "candidate_id": candidate_id,
            "sha256": frozen_science_digest(objective)}


def require_review_binding(candidate_id: str, objective: Mapping[str, Any], binding: Any) -> None:
    if not isinstance(binding, Mapping) or binding != review_binding(candidate_id, objective):
        raise ValueError("scientific review binding missing or changed; explicit qualification recovery required")


def locked_science_view(value: Any) -> dict[str, Any]:
    science = frozen_science_view(value, legacy=True)
    if isinstance(value, Mapping):
        review = (value.get("qualification") or {}).get("review") or {}
        # Historical locked objectives without this record remain readable.
        # A present but malformed record must never fall back to legacy mode.
        if "reviewed_science" in review:
            require_review_binding(str(value.get("candidate_id") or ""), science,
                                   review["reviewed_science"])
    return science
