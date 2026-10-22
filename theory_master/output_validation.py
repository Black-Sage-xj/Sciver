"""Machine-contract validation and repair diagnostics for agent outputs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import RoleContract
from .cycle_criteria import criterion_rows


@dataclass(frozen=True)
class ValidationError:
    path: str
    code: str
    expected: str
    actual: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "expected": self.expected,
                "actual": self.actual, "message": self.message}


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[ValidationError, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "errors": [item.as_dict() for item in self.errors]}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def _err(path: str, code: str, expected: str, value: Any, message: str) -> ValidationError:
    return ValidationError(path, code, expected, _type_name(value), message)


def validate_scientific_model_update(
    current_model: Mapping[str, Any], update: Any, *, cycle_id: str,
    evidence_manifest: Any = None,
) -> ValidationResult:
    errors: list[ValidationError] = []
    if not isinstance(update, dict):
        return ValidationResult(False, (_err(
            "/scientific_model_update", "type_mismatch", "object", update,
            "scientific_model_update must be an object",
        ),))
    action = update.get("action")
    if action not in {"keep", "replace"}:
        errors.append(_err(
            "/scientific_model_update/action", "invalid_value", "keep|replace", action,
            "Scientific Model update action must be keep or replace",
        ))
        return ValidationResult(False, tuple(errors))
    extras = set(update) - {"action", "model"}
    if extras:
        errors.append(_err(
            "/scientific_model_update", "unexpected_field", "action plus conditional model", update,
            "Scientific Model update accepts only action and model",
        ))
    content = update.get("model")
    if action == "keep" and "model" in update:
        errors.append(_err(
            "/scientific_model_update/model", "forbidden_field", "field omitted", content,
            "keep must omit Scientific Model content entirely",
        ))
    if action == "replace" and not isinstance(content, dict):
        errors.append(_err(
            "/scientific_model_update/model", "type_mismatch", "object", content,
            "replace requires one complete Research-Lead-authored Scientific Model",
        ))
    if action == "replace" and isinstance(content, dict) and "summary" in content and not isinstance(content["summary"], str):
        errors.append(_err(
            "/scientific_model_update/model/summary", "type_mismatch", "string", content["summary"],
            "Scientific Model summary must be a Markdown paragraph string, not an array or object",
        ))
    if action == "replace" and isinstance(content, dict):
        try:
            from .scientific_model import validate_authored_content
            validate_authored_content(content)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(ValidationError(
                "/scientific_model_update/model", "candidate_model_invalid",
                "exactly one current-schema author field: summary", "object", str(exc),
            ))
    if not errors:
        try:
            from .scientific_model import preview_authored_model
            preview_authored_model(
                dict(current_model),
                content if isinstance(content, dict) else None,
                cycle_id=cycle_id,
                action=str(action),
            )
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(ValidationError(
                "/scientific_model_update", "candidate_model_invalid",
                "valid complete Scientific Model edit", "object", str(exc),
            ))
    return ValidationResult(not errors, tuple(errors))


def _declared_evidence_ids(output: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for field in ("evidence", "evidence_records"):
        values = output.get(field)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict) and str(item.get("evidence_id") or "").strip():
                ids.add(str(item["evidence_id"]).strip())
    return ids


def validate_references(output: Mapping[str, Any], state: Mapping[str, Any], context: Mapping[str, Any]) -> ValidationResult:
    """Validate machine references without making a scientific sufficiency judgment."""
    errors: list[ValidationError] = []
    lock = state.get("hypothesis_lock") if isinstance(state.get("hypothesis_lock"), dict) else {}
    criteria = (
        (lock.get("cycle_criteria") or lock.get("cycle_progress_criteria"))
        if isinstance(lock, dict) else {}
    )
    rows = criterion_rows(criteria)
    frozen_ids = {str(item.get("id")) for item in rows if isinstance(item, dict) and item.get("id")}
    manifest = (context.get("evidence_manifest") or {}).get("payload") if isinstance(context.get("evidence_manifest"), dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    active_records = {
        str(item.get("evidence_id")): item for item in manifest.get("records") or []
        if isinstance(item, dict) and item.get("evidence_id") and item.get("lifecycle_status") == "active"
    }
    declared_ids = _declared_evidence_ids(output)
    for field in ("evidence", "evidence_records"):
        values = output.get(field)
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            for criterion_id in item.get("criterion_ids") or []:
                if frozen_ids and str(criterion_id) not in frozen_ids:
                    errors.append(_err(
                        f"/{field}/{index}/criterion_ids", "unknown_reference",
                        "frozen cycle criterion id", criterion_id,
                        "scientific evidence must bind only frozen cycle criteria",
                    ))
    assessments = output.get("criterion_assessments")
    if isinstance(assessments, list):
        for index, item in enumerate(assessments):
            criterion_id = item.get("criterion_id") if isinstance(item, dict) else None
            if frozen_ids and str(criterion_id or "") not in frozen_ids:
                errors.append(_err(
                    f"/criterion_assessments/{index}/criterion_id", "unknown_reference",
                    "frozen cycle criterion id", criterion_id,
                    "criterion assessment must reference a frozen cycle criterion",
                ))

    criterion_updates = output.get("criterion_updates")
    if isinstance(criterion_updates, dict):
        for criterion_id, update in criterion_updates.items():
            if frozen_ids and str(criterion_id) not in frozen_ids:
                errors.append(_err(f"/criterion_updates/{criterion_id}", "unknown_reference",
                                   "frozen cycle criterion id", criterion_id,
                                   "criterion update must reference a frozen cycle criterion"))
                continue
            if not isinstance(update, dict):
                continue
            for index, evidence_id in enumerate(update.get("evidence_refs") or []):
                evidence_id = str(evidence_id)
                if evidence_id not in declared_ids and evidence_id not in active_records:
                    errors.append(_err(f"/criterion_updates/{criterion_id}/evidence_refs/{index}", "unknown_reference",
                                       "declared or active evidence id", evidence_id,
                                       "criterion update evidence_refs must reference evidence produced in this output or active registry evidence"))
                elif evidence_id in active_records and str(criterion_id) not in {
                    str(item) for item in active_records[evidence_id].get("criterion_ids") or []
                }:
                    errors.append(_err(f"/criterion_updates/{criterion_id}/evidence_refs/{index}", "criterion_link_missing",
                                       f"evidence linked to {criterion_id}", evidence_id,
                                       "existing evidence must already be registered against this frozen criterion"))
    objections = output.get("objections")
    if frozen_ids and isinstance(objections, list):
        for index, item in enumerate(objections):
            if isinstance(item, dict) and item.get("criterion_id") not in frozen_ids:
                errors.append(_err(f"/objections/{index}/criterion_id", "unknown_reference",
                                   "frozen cycle criterion id", item.get("criterion_id"),
                                   "criterion_id must belong to the frozen hypothesis"))

    current_attempt = int(state.get("investigation_attempt") or 0)
    attempt = (state.get("investigation_attempts") or {}).get(f"attempt-{current_attempt:04d}", {})
    known_objections = {str(item.get("objection_id")) for item in (attempt.get("objections") or [])
                        if isinstance(item, dict) and item.get("objection_id")}
    updates = output.get("objection_updates")
    if known_objections and isinstance(updates, dict):
        for objection_id in updates:
            if str(objection_id) not in known_objections:
                errors.append(_err(f"/objection_updates/{objection_id}", "unknown_reference",
                                   "known Judge objection id", objection_id,
                                   "objection update must reference an active Judge objection"))
    responses = output.get("objection_responses")
    if known_objections and isinstance(responses, list):
        for index, item in enumerate(responses):
            objection_id = item.get("objection_id") if isinstance(item, dict) else None
            if str(objection_id or "") not in known_objections:
                errors.append(_err(
                    f"/objection_responses/{index}/objection_id", "unknown_reference",
                    "known Judge objection id", objection_id,
                    "objection response must reference an active Judge objection",
                ))
    return ValidationResult(not errors, tuple(errors))


def validate_agent_output(role: str, stage: str, output: Any, state: Mapping[str, Any],
                          context: Mapping[str, Any], contract: RoleContract) -> ValidationResult:
    errors: list[ValidationError] = []
    if not isinstance(output, dict):
        return ValidationResult(False, (_err("/", "type_mismatch", "object", output,
                                           "agent output must be one JSON object"),))
    try:
        contract.validate(output, context)
    except (ValueError, TypeError, KeyError) as exc:
        errors.append(ValidationError("/", "contract_violation", contract.ref, "object", str(exc)))

    if role == "research_lead" and stage == "cycle_synthesis":
        for frozen in ("verdict", "judge_decision", "hypothesis_lock", "active_hypothesis"):
            if frozen in output:
                errors.append(ValidationError(f"/{frozen}", "forbidden_field", "field omitted", _type_name(output[frozen]),
                                              f"{frozen} is frozen outside cycle_synthesis output authority"))
        model_result = validate_scientific_model_update(
            context.get("scientific_model") if isinstance(context.get("scientific_model"), dict) else {},
            output.get("scientific_model_update"), cycle_id=str(state.get("cycle_id") or ""),
            evidence_manifest=context.get("evidence_manifest"),
        )
        errors.extend(model_result.errors)
        from .scientific_model import validate_pending_dispositions
        pending = context.get("scientific_model_pending")
        items = pending.get("items") if isinstance(pending, Mapping) else None
        injected = [str(item.get("pending_id")) for item in items or [] if isinstance(item, Mapping)]
        for message in validate_pending_dispositions(output.get("pending_dispositions"), injected):
            errors.append(ValidationError("/pending_dispositions", "pending_disposition_invalid",
                                          "one disposition per injected pending_id",
                                          _type_name(output.get("pending_dispositions")), message))

    errors.extend(validate_references(output, state, context).errors)
    # Preserve deterministic order while avoiding duplicate generic errors.
    unique: list[ValidationError] = []
    seen: set[tuple[str, str, str]] = set()
    for item in errors:
        key = (item.path, item.code, item.message)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return ValidationResult(not unique, tuple(unique))


def format_validation_errors(errors: list[dict[str, Any]] | tuple[ValidationError, ...]) -> str:
    rows = [item.as_dict() if isinstance(item, ValidationError) else item for item in errors]
    return "\n".join(
        f"{index}. {item.get('path', '/')}: {item.get('message', item.get('code', 'validation error'))}"
        for index, item in enumerate(rows, 1)
    )
