"""InvocationOutputValidation: a single explicit phase owner for InvocationSession."""

from __future__ import annotations
import json
from .output_normalization import (
    NormalizationResult,
    OutputNormalizationError,
    normalize_agent_output,
)
from .output_validation import ValidationError, ValidationResult, validate_agent_output
from .prompt_stages import build_contract_repair_prompt, build_json_repair_prompt
from .runtime import now, write_json
from .workflow_invocation import (
    _normalize_legacy_investigator_artifact_fields,
    CONTRACT_REPAIR_LIMIT,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .invocation_execution import InvocationSession


class InvocationOutputValidation:

    def __init__(self, session: InvocationSession):
        self.session = session

    def normalize_with_audit(self, raw_text: str, input_kind: str):
        try:
            result = normalize_agent_output(
                raw_text, self.session.contract.prompt_schema
            )
        except OutputNormalizationError as exc:
            self.session.owner._emit(
                self.session.state,
                "json.normalization_failed",
                {
                    "role": self.session.role,
                    "stage": self.session.stage,
                    "input_kind": input_kind,
                    "classification": exc.classification,
                    "error": str(exc),
                },
            )
            raise
        if (
            self.session.role == "research_lead"
            and self.session.stage == "frontier_proposal"
        ):
            from .hypothesis_lineage import normalize_singleton_references

            normalized, source_repairs = normalize_singleton_references(
                result.value,
                set(self.session.validation_context.get("_known_hypothesis_ids") or []),
            )
            if source_repairs:
                result = NormalizationResult(
                    value=normalized,
                    canonical_json=json.dumps(normalized, ensure_ascii=False),
                    classification="lineage_type_normalization",
                    repairs=tuple(result.repairs) + tuple(source_repairs),
                    original_error=result.original_error,
                )
        if self.session.role == "investigator" and self.session.stage == "investigate":
            normalized, compatibility_repairs = (
                _normalize_legacy_investigator_artifact_fields(result.value)
            )
            if compatibility_repairs:
                result = NormalizationResult(
                    value=normalized,
                    canonical_json=json.dumps(
                        normalized,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=False,
                    ),
                    classification=(
                        "syntax_and_compatibility_normalization"
                        if result.repairs
                        else "compatibility_normalization"
                    ),
                    repairs=tuple(result.repairs) + tuple(compatibility_repairs),
                    original_error=result.original_error,
                )
                self.session.owner._emit(
                    self.session.state,
                    "agent.compatibility_normalized",
                    {
                        "role": self.session.role,
                        "stage": self.session.stage,
                        "repairs": compatibility_repairs,
                    },
                )
        if result.repairs:
            self.session.state["json_normalization_count"] = (
                int(self.session.state.get("json_normalization_count") or 0) + 1
            )
            sequence = int(self.session.state["json_normalization_count"])
            artifact = (
                self.session.owner.cycle_dir
                / "output_normalizations"
                / f"{self.session.invocation_id}-{sequence:02d}-{input_kind}.json"
            )
            payload = {
                "invocation_id": self.session.invocation_id,
                "role": self.session.role,
                "stage": self.session.stage,
                "input_kind": input_kind,
                "classification": result.classification,
                "original_error": result.original_error,
                "repairs": list(result.repairs),
                "raw_output": raw_text,
                "canonical_output": result.canonical_json,
                "at": now(),
            }
            write_json(artifact, payload)
            self.session.owner._emit(
                self.session.state,
                "json.normalized",
                {
                    "role": self.session.role,
                    "stage": self.session.stage,
                    "input_kind": input_kind,
                    "classification": result.classification,
                    "repair_count": len(result.repairs),
                    "repair_kinds": [
                        str(item.get("kind") or "") for item in result.repairs
                    ],
                    "artifact": str(artifact),
                },
            )
            self.session.owner._record_cycle_anomaly(
                self.session.state,
                kind="json_normalization",
                role=self.session.role,
                stage=self.session.stage,
                invocation_id=self.session.invocation_id,
                thread_id=self.session.identifier,
                mechanism="deterministic_normalization",
                summary=result.original_error
                or ", ".join((str(item.get("kind") or "") for item in result.repairs)),
                input_kind=input_kind,
                artifact=str(artifact),
            )
        return result.value

    def _validate_and_repair(self):
        raw_outputs = [self.session.raw]
        repair_prompt = ""
        json_repair_used = False
        try:
            self.session.output = self.normalize_with_audit(
                self.session.raw, "agent_output"
            )
        except OutputNormalizationError as exc:
            self.session.state["json_parse_repair_count"] = (
                int(self.session.state.get("json_parse_repair_count") or 0) + 1
            )
            json_repair_used = True
            repair_prompt = build_json_repair_prompt(
                self.session.stage, self.session.raw, str(exc)
            )
            self.session.owner._record_cycle_anomaly(
                self.session.state,
                kind="json_repair_retry",
                role=self.session.role,
                stage=self.session.stage,
                invocation_id=self.session.invocation_id,
                thread_id=self.session.identifier,
                mechanism="sdk_driven_llm_retry",
                summary=str(exc),
                attempt=1,
                input_kind="agent_output",
            )
            self.session.identifier, repaired_raw = (
                self.session.transport.invoke_with_audit(repair_prompt, "json_repair")
            )
            raw_outputs.append(repaired_raw)
            try:
                self.session.output = self.normalize_with_audit(
                    repaired_raw, "json_repair"
                )
            except OutputNormalizationError as repair_exc:
                self.session.owner._contract_repair_failed(
                    self.session.state,
                    invocation_id=self.session.invocation_id,
                    role=self.session.role,
                    stage=self.session.stage,
                    thread_id=self.session.identifier,
                    raw_outputs=raw_outputs,
                    errors=[
                        {
                            "path": "/",
                            "code": (
                                "json_content_loss"
                                if repair_exc.classification == "content_loss"
                                else "json_syntax_error"
                            ),
                            "expected": "recoverable JSON object",
                            "actual": repair_exc.classification,
                            "message": str(repair_exc or exc),
                        }
                    ],
                    repair_prompt=repair_prompt,
                    invocation_ledger=self.session.invocation_ledger,
                    artifact_tracker=self.session.artifact_tracker,
                )
        from .hypothesis_lineage import (
            lineage_repair_snapshot,
            assert_repair_lineage_preserved,
        )

        protected_lineage = (
            lineage_repair_snapshot(
                self.session.output,
                set(self.session.validation_context.get("_known_hypothesis_ids") or []),
            )
            if self.session.role == "research_lead"
            and self.session.stage == "frontier_proposal"
            else []
        )
        validation = validate_agent_output(
            self.session.role,
            self.session.stage,
            self.session.output,
            self.session.state,
            self.session.validation_context,
            self.session.contract,
        )
        if not validation.valid:
            self._repair_contract(
                validation,
                protected_lineage,
                raw_outputs,
                repair_prompt,
                json_repair_used,
            )

    def _repair_contract(
        self,
        validation,
        protected_lineage,
        raw_outputs,
        repair_prompt,
        json_repair_used,
    ):
        from .hypothesis_lineage import assert_repair_lineage_preserved

        errors = [item.as_dict() for item in validation.errors]
        self.session.owner._emit(
            self.session.state,
            "json.contract_violation",
            {
                "role": self.session.role,
                "stage": self.session.stage,
                "classification": "contract_violation",
                "error_count": len(validation.errors),
                "paths": [item.path for item in validation.errors],
            },
        )
        repaired_output = None
        for repair_attempt in range(1, CONTRACT_REPAIR_LIMIT + 1):
            self.session.state["contract_repair_count"] = (
                int(self.session.state.get("contract_repair_count") or 0) + 1
            )
            repair_prompt = build_contract_repair_prompt(
                self.session.stage,
                errors,
                self.session.contract.prompt_projection(
                    self.session.validation_context
                ),
                self.session.validation_context,
                repair_attempt=repair_attempt,
                repair_limit=CONTRACT_REPAIR_LIMIT,
            )
            if protected_lineage:
                repair_prompt += (
                    "\nProtected source decisions from the original parsed proposal; preserve these IDs, main/other roles and valid contributions without reordering candidates:\n"
                    + json.dumps(protected_lineage, ensure_ascii=False)
                )
            self.session.owner._record_cycle_anomaly(
                self.session.state,
                kind="contract_repair_retry",
                role=self.session.role,
                stage=self.session.stage,
                invocation_id=self.session.invocation_id,
                thread_id=self.session.identifier,
                mechanism="sdk_driven_llm_retry",
                summary="; ".join(
                    (
                        str(
                            item.get("message")
                            or item.get("code")
                            or "contract violation"
                        )
                        for item in errors
                    )
                ),
                attempt=repair_attempt,
                input_kind="contract_repair",
                error_paths=[str(item.get("path") or "") for item in errors],
            )
            self.session.identifier, repaired_raw = (
                self.session.transport.invoke_with_audit(
                    repair_prompt, "contract_repair"
                )
            )
            raw_outputs.append(repaired_raw)
            try:
                repaired_output = self.normalize_with_audit(
                    repaired_raw, "contract_repair"
                )
            except OutputNormalizationError as exc:
                if json_repair_used:
                    self.session.owner._contract_repair_failed(
                        self.session.state,
                        invocation_id=self.session.invocation_id,
                        role=self.session.role,
                        stage=self.session.stage,
                        thread_id=self.session.identifier,
                        raw_outputs=raw_outputs,
                        errors=[
                            {
                                "path": "/",
                                "code": (
                                    "json_content_loss"
                                    if exc.classification == "content_loss"
                                    else "json_syntax_error"
                                ),
                                "expected": "recoverable JSON object",
                                "actual": exc.classification,
                                "message": str(exc),
                            }
                        ],
                        repair_prompt=repair_prompt,
                        invocation_ledger=self.session.invocation_ledger,
                        artifact_tracker=self.session.artifact_tracker,
                    )
                json_repair_used = True
                self.session.state["json_parse_repair_count"] = (
                    int(self.session.state.get("json_parse_repair_count") or 0) + 1
                )
                json_repair_prompt = build_json_repair_prompt(
                    self.session.stage, repaired_raw, str(exc)
                )
                self.session.owner._record_cycle_anomaly(
                    self.session.state,
                    kind="json_repair_retry",
                    role=self.session.role,
                    stage=self.session.stage,
                    invocation_id=self.session.invocation_id,
                    thread_id=self.session.identifier,
                    mechanism="sdk_driven_llm_retry",
                    summary=str(exc),
                    attempt=1,
                    input_kind="contract_repair_output",
                )
                self.session.identifier, json_repaired_raw = (
                    self.session.transport.invoke_with_audit(
                        json_repair_prompt, "json_repair"
                    )
                )
                raw_outputs.append(json_repaired_raw)
                try:
                    repaired_output = self.normalize_with_audit(
                        json_repaired_raw, "json_repair_after_contract"
                    )
                except OutputNormalizationError as json_exc:
                    self.session.owner._contract_repair_failed(
                        self.session.state,
                        invocation_id=self.session.invocation_id,
                        role=self.session.role,
                        stage=self.session.stage,
                        thread_id=self.session.identifier,
                        raw_outputs=raw_outputs,
                        errors=[
                            {
                                "path": "/",
                                "code": (
                                    "json_content_loss"
                                    if json_exc.classification == "content_loss"
                                    else "json_syntax_error"
                                ),
                                "expected": "recoverable JSON object",
                                "actual": json_exc.classification,
                                "message": str(json_exc),
                            }
                        ],
                        repair_prompt=json_repair_prompt,
                        invocation_ledger=self.session.invocation_ledger,
                        artifact_tracker=self.session.artifact_tracker,
                    )
            repaired_validation = validate_agent_output(
                self.session.role,
                self.session.stage,
                repaired_output,
                self.session.state,
                self.session.validation_context,
                self.session.contract,
            )
            try:
                assert_repair_lineage_preserved(protected_lineage, repaired_output)
            except ValueError as exc:
                repaired_validation = ValidationResult(
                    False,
                    repaired_validation.errors
                    + (
                        ValidationError(
                            "/candidates",
                            "lineage_content_loss",
                            "preserved source decisions",
                            "changed lineage",
                            str(exc),
                        ),
                    ),
                )
            if repaired_validation.valid:
                self.session.output = repaired_output
                break
            errors = [item.as_dict() for item in repaired_validation.errors]
            self.session.owner._emit(
                self.session.state,
                "json.contract_violation",
                {
                    "role": self.session.role,
                    "stage": self.session.stage,
                    "classification": "contract_violation",
                    "input_kind": "contract_repair",
                    "repair_attempt": repair_attempt,
                    "repair_limit": CONTRACT_REPAIR_LIMIT,
                    "error_count": len(repaired_validation.errors),
                    "paths": [item.path for item in repaired_validation.errors],
                },
            )
        else:
            self.session.owner._contract_repair_failed(
                self.session.state,
                invocation_id=self.session.invocation_id,
                role=self.session.role,
                stage=self.session.stage,
                thread_id=self.session.identifier,
                raw_outputs=raw_outputs,
                errors=errors,
                repair_prompt=repair_prompt,
                invocation_ledger=self.session.invocation_ledger,
                artifact_tracker=self.session.artifact_tracker,
            )
