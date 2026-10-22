"""Shared contract primitives and current reusable validators.

The current runtime registry is stage-specific and lives in ``contract_stages``.
This module does not maintain historical or generic role-contract registries.
"""
from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping


Validator = Callable[[dict[str, Any], Mapping[str, Any]], None]


def _reject_unsupported_schema_fields(value: Any, schema: Any, path: str) -> None:
    """Reject undeclared object keys recursively, preserving explicitly free-form payloads.

    Schema entries shaped as {"optional": ...} describe an arbitrary JSON value
    (currently Investigator.result), not an object whose only legal key is optional.
    """
    if isinstance(schema, Mapping):
        if set(schema) == {"optional"}:
            return
        if not isinstance(value, Mapping):
            return
        extra = set(value) - set(schema)
        if extra:
            fields = ", ".join(sorted(str(key) for key in extra))
            raise ValueError(f"{path} contains unsupported fields: {fields}")
        for key, nested in value.items():
            _reject_unsupported_schema_fields(nested, schema[key], f"{path}.{key}")
        return
    if isinstance(schema, list) and schema and isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_unsupported_schema_fields(nested, schema[0], f"{path}[{index}]")


@dataclass(frozen=True)
class RoleContract:
    contract_id: str
    version: int
    prompt_schema: dict[str, Any]
    structural_validator: Validator
    semantic_validator: Validator
    prompt_note: str = ""
    # Optional accepted shape for persisted legacy outputs; never projected to the model.
    validation_schema: dict[str, Any] | None = None

    @property
    def ref(self) -> str:
        return f"{self.contract_id}/v{self.version}"

    @property
    def schema_hash(self) -> str:
        # Fingerprint the model-facing schema for invocation identity.
        # Human-facing prompt notes and validation-only legacy fields do not alter this fingerprint.
        payload = json.dumps(self.prompt_schema, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def descriptor(self) -> dict[str, Any]:
        return {"contract_id": self.contract_id, "contract_version": self.version,
                "schema_hash": self.schema_hash}

    def prompt_projection(self, context: Mapping[str, Any] | None = None) -> str:
        schema = self.prompt_schema
        schema = self.prompt_schema
        projection = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        semantics: list[str] = [self.prompt_note] if self.prompt_note else []
        if context is not None and self.contract_id == "theory-master/role/research-lead/candidate-selection":
            rows = context.get("qualified_candidates") if isinstance(context.get("qualified_candidates"), list) else context.get("candidates")
            rows = rows if isinstance(rows, list) else []
            allowed_ids = [
                str(item.get("id") or item.get("candidate_id") or "").strip()
                for item in rows if isinstance(item, Mapping)
            ]
            allowed_ids = [item for item in allowed_ids if item]
            if allowed_ids:
                semantics.append(
                    "selected_candidate_id is a closed choice for this invocation. Choose exactly one of: "
                    + " | ".join(allowed_ids) + ". Do not invent another candidate ID."
                )
        if context is not None and self.contract_id == "theory-master/role/research-lead/selection-repair":
            rows = context.get("repair_options") if isinstance(context.get("repair_options"), list) else context.get("candidates")
            rows = rows if isinstance(rows, list) else []
            allowed_ids = [
                str(item.get("candidate_id") or item.get("id") or "").strip()
                for item in rows if isinstance(item, Mapping)
            ]
            allowed_ids = [item for item in allowed_ids if item]
            if allowed_ids:
                rule = (
                    "selected_parent_candidate_id is a closed choice for this invocation. "
                    + ("Choose exactly one of: " if len(allowed_ids) > 1 else "If supplied, it must be: ")
                    + " | ".join(allowed_ids) + ". Do not invent another candidate ID."
                )
                semantics.append(rule)
            blocker_rows: list[str] = []
            if isinstance(context.get("repair_options"), list):
                for option in context.get("repair_options") or []:
                    if not isinstance(option, Mapping):
                        continue
                    candidate_id = str(option.get("candidate_id") or "").strip()
                    blocker_ids = [
                        str(item.get("id") or "").strip()
                        for item in (option.get("blockers") or []) if isinstance(item, Mapping)
                    ]
                    blocker_ids = [item for item in blocker_ids if item]
                    if blocker_ids:
                        blocker_rows.append(f"{candidate_id}: " + " | ".join(blocker_ids))
            elif isinstance(context.get("candidate_reviews"), list):
                for review in context.get("candidate_reviews") or []:
                    if not isinstance(review, Mapping):
                        continue
                    candidate_id = str(review.get("candidate_id") or "").strip()
                    blocker_ids = [
                        str(item.get("id") or "").strip()
                        for item in (review.get("blocking_issues") or []) if isinstance(item, Mapping)
                    ]
                    blocker_ids = [item for item in blocker_ids if item]
                    if blocker_ids:
                        blocker_rows.append(f"{candidate_id}: " + " | ".join(blocker_ids))
            if blocker_rows:
                semantics.append(
                    "blocker_closures[].blocker_id is also closed to the frozen blocker IDs for the selected parent: "
                    + "; ".join(blocker_rows) + ". Do not invent blocker IDs."
                )
        if semantics:
            return projection + "\nContract semantics: " + " ".join(item for item in semantics if item)
        return projection

    def validate(self, output: dict[str, Any], context: Mapping[str, Any]) -> None:
        if not isinstance(output, dict):
            raise ValueError(f"{self.ref} output must be an object")
        self.structural_validator(output, context)
        accepted_schema = self.validation_schema if self.validation_schema is not None else self.prompt_schema
        _reject_unsupported_schema_fields(output, accepted_schema, self.contract_id.rsplit("/", 1)[-1])
        self.semantic_validator(output, context)


def _noop(_output: dict[str, Any], _context: Mapping[str, Any]) -> None:
    return


def qualification_verdict(value: Any) -> str:
    """Map a recommendation/verdict string onto lock semantics.

    Only an explicit qualify/qualified head may lock. ``revise`` returns to
    Research Lead. ``reject`` cannot be reused. Any other text is not lockable.
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    head = text.split("(", 1)[0].strip().split()[0].strip(".,:;")
    if head in {"qualify", "qualified"}:
        return "qualified"
    if head == "revise" or "must_decompose" in text:
        return "revise"
    if head in {"reject", "rejected"}:
        return "reject"
    return "unqualified"


CHALLENGER_V2_SCHEMA = {
    "reviews": [{
        "candidate_id": "cand-1",
        "verdict": "qualified|revise|reject",
        "reason": "...",
        "blocking_issues": [{"problem": "...", "required_change": "..."}],
    }]
}


def _challenger_v2_structure(output: dict[str, Any], _context: Mapping[str, Any]) -> None:
    reviews = output.get("reviews")
    if not isinstance(reviews, list):
        raise ValueError("challenger contract requires reviews[]")
    for index, review in enumerate(reviews):
        if not isinstance(review, dict):
            raise ValueError(f"challenger reviews[{index}] must be an object")
        _text(review.get("candidate_id"), f"challenger reviews[{index}].candidate_id")
        verdict = review.get("verdict")
        if verdict not in {"qualified", "revise", "reject"}:
            raise ValueError(f"challenger reviews[{index}].verdict must be qualified|revise|reject")
        _text(review.get("reason"), f"challenger reviews[{index}].reason")
        blocking = review.get("blocking_issues")
        if verdict == "revise":
            if not isinstance(blocking, list) or not blocking:
                raise ValueError(f"challenger reviews[{index}].blocking_issues must be non-empty for revise")
            for issue_index, issue in enumerate(blocking):
                if not isinstance(issue, dict):
                    raise ValueError(f"challenger reviews[{index}].blocking_issues[{issue_index}] must be an object")
                _text(issue.get("problem"), f"challenger reviews[{index}].blocking_issues[{issue_index}].problem")
                _text(issue.get("required_change"), f"challenger reviews[{index}].blocking_issues[{issue_index}].required_change")
        elif blocking not in (None, [], ""):
            raise ValueError(f"challenger reviews[{index}].blocking_issues is only allowed for revise")
        obsolete = {"critical_issues", "fatal_issues", "known_conflicts", "required_evidence",
                    "discriminating_weakness", "recommendation", "required_revision"} & set(review)
        if obsolete:
            raise ValueError("challenger review contains obsolete fields: " + ", ".join(sorted(obsolete)))


def _challenger_v2_semantics(output: dict[str, Any], context: Mapping[str, Any]) -> None:
    candidates = context.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("challenger contract context requires candidates[]")
    expected = [str(item.get("id") or "") for item in candidates if isinstance(item, dict)]
    if any(not item for item in expected) or len(expected) != len(candidates):
        raise ValueError("challenger context candidates require stable ids")
    if len(expected) != len(set(expected)):
        raise ValueError("challenger context contains duplicate candidate ids")
    actual = [str(review.get("candidate_id") or "") for review in output["reviews"]]
    if len(actual) != len(set(actual)):
        raise ValueError("challenger returned duplicate candidate reviews")
    unknown = sorted(set(actual) - set(expected))
    missing = sorted(set(expected) - set(actual))
    allowed = ", ".join(expected)
    if unknown:
        raise ValueError(
            "candidate review references unknown candidate: "
            + ", ".join(unknown)
            + "; expected exactly these candidate_id values for this invocation: "
            + allowed
        )
    if missing:
        raise ValueError(
            "candidate review is incomplete: "
            + ", ".join(missing)
            + "; expected exactly these candidate_id values for this invocation: "
            + allowed
        )


LITERATURE_TASK_TYPES = frozenset({"landscape", "targeted_source", "novelty_check"})
LITERATURE_TRIGGER_ROLES = frozenset({"research_lead", "investigator", "judge"})

LITERATURE_REQUEST_SCHEMA = {
    "gap_id": "stable external-knowledge gap id",
    "task_type": "landscape|targeted_source|novelty_check",
    "search_target": "what to investigate in the external scholarly world",
    "why_external_needed": "why local TheoryMaster/project evidence is insufficient",
    "expected_external_evidence": "what external scholarly result/source would resolve or map the gap",
    "new_search_lead": "optional new DOI/title/author/keyword/source hint when reopening the same gap",
}
TERMINAL_BRANCH_VERDICTS = frozenset({"supported", "rejected", "milestone_advanced", "open"})
OBJECTION_STATUSES = frozenset({"resolved", "partially_resolved", "unresolved", "invalid_objection"})
CRITERION_UPDATE_STATUSES = frozenset({"supported", "failed", "addressed", "unresolved", "not_evaluated"})


def _text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _criteria(value: Any, name: str) -> set[str]:
    if not isinstance(value, dict) or set(value) != {"success", "failure"}:
        raise ValueError(f"{name} requires success[] and failure[]")
    ids: set[str] = set()
    for kind in ("success", "failure"):
        rows = value[kind]
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{name}.{kind} must be a non-empty list")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{name}.{kind} criterion must be an object")
            for field in ("id", "criterion"):
                _text(row.get(field), f"{name}.{kind}.{field}")
            if row["id"] in ids:
                raise ValueError(f"duplicate criterion id: {row['id']}")
            ids.add(row["id"])
    return ids


def _reporter_structure(output: dict[str, Any], _context: Mapping[str, Any]) -> None:
    _text(output.get("title"), "reporter title")
    _text(output.get("summary"), "reporter summary")
    summary = str(output.get("summary") or "")
    forbidden_machine_dump_markers = (
        "```json", "```yaml", "### ModelPatch JSON", "## ModelPatch JSON",
        "## Candidate Review Delivery", "## Research Lead Cycle Start Delivery",
        "## Theory Delivery", "## Numerical Delivery", "## Literature Delivery",
        "## Failure and Rework Record", "## Evolution Candidates",
    )
    marker = next((item for item in forbidden_machine_dump_markers if item.lower() in summary.lower()), None)
    if marker is not None:
        raise ValueError(f"reporter summary must be human-first prose; machine dump marker is forbidden: {marker}")
    if "candidates" in output:
        raise ValueError("reporter cannot create candidates; scientific frontier authority belongs to Research Lead")
