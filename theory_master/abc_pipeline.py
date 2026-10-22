"""Three-stage scientific output pipeline.

Stage A is authored by the scientific role.  The SDK then assigns invocation-local
references without changing scientific content.  Stage B annotates only those
existing objects with closed semantic decisions.  Stage C remains in
``contract_closure`` and the workflow/Registry.

The module deliberately has no hard dependency on TypeSafe.  ``JevBackend`` loads
the optional ``typesafe-sdk`` package only when selected; tests and offline smoke
runs can inject ``DeterministicSemanticBackend``.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .cycle_criteria import criterion_rows


FINDING_RELATIONS = ("supports", "contradicts", "limits_scope", "invalidates", "neutral")
GAP_KINDS = (
    "none", "theoretical", "numerical", "local_source_review", "external_artifact",
    "literature_landscape", "literature_targeted", "literature_novelty",
)
GAP_PROGRESS = ("new_or_refined", "repeated_no_progress")
GAP_RESOLVABILITY = ("actionable_now", "structurally_unidentifiable")


def _semantic_tokens(value: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", str(value or "").lower()))


class SemanticDecisionBackend(Protocol):
    """Backend for B-layer judgments; it must never generate new science."""

    name: str

    def decide(self, *, task: str, state: Mapping[str, Any], questions: list[dict[str, Any]]) -> dict[str, Any]: ...


@dataclass
class DeterministicSemanticBackend:
    """Offline/test backend using explicit hints and conservative defaults."""

    name: str = "deterministic"

    def decide(self, *, task: str, state: Mapping[str, Any], questions: list[dict[str, Any]]) -> dict[str, Any]:
        answers: dict[str, Any] = {}
        for question in questions:
            qid = str(question["id"])
            options = list(question.get("options") or [])
            hint = question.get("hint")
            if hint in options:
                answers[qid] = hint
            elif task == "specialist_semantic_closure" and "/criterion:" in qid:
                ref, cid = qid.split("/criterion:", 1)
                finding = next((item for item in state.get("findings") or [] if item.get("ref") == ref), {})
                criteria = state.get("frozen_criteria") or []
                rows = criterion_rows(criteria)
                criterion = next((item for item in rows if str(item.get("id")) == cid), {})
                finding_text = str(finding.get("statement") or "").casefold()
                criterion_text = str(criterion.get("criterion") or "").casefold()
                finding_tokens = _semantic_tokens(finding_text)
                criterion_tokens = _semantic_tokens(criterion_text)
                overlap = len(finding_tokens & criterion_tokens) / max(
                    1, min(len(finding_tokens), len(criterion_tokens))
                )
                # Offline/test semantic closure must not require the A-layer
                # Agent to echo SDK-only criterion text. Accept a finding that
                # clearly overlaps the frozen criterion while remaining
                # conservative for unrelated statements.
                answers[qid] = (
                    "related"
                    if criterion_text and (
                        criterion_text in finding_text
                        or finding_text in criterion_text
                        or (finding_tokens and criterion_tokens and overlap >= 0.35)
                    )
                    else "unrelated"
                )
            elif task == "specialist_semantic_closure" and "/objection:" in qid:
                ref, oid = qid.split("/objection:", 1)
                finding = next((item for item in state.get("findings") or [] if item.get("ref") == ref), {})
                objection = next((item for item in state.get("judge_objections") or []
                                  if str(item.get("objection_id") or "") == oid), {})
                finding_tokens = _semantic_tokens(" ".join((
                    str(finding.get("statement") or ""), str(finding.get("scope") or ""),
                    str(finding.get("limitations") or ""),
                )))
                objection_tokens = _semantic_tokens(" ".join((
                    str(objection.get("reason") or ""), str(objection.get("requested_resolution") or ""),
                    " ".join(str(value) for value in (objection.get("evidence_that_would_resolve_it") or [])),
                )))
                overlap = len(finding_tokens & objection_tokens) / max(1, min(len(finding_tokens), len(objection_tokens)))
                answers[qid] = "relevant" if finding_tokens and objection_tokens and overlap >= 0.12 else "unrelated"
            else:
                answers[qid] = options[0] if options else None
        return {"answers": answers, "confidence": {}, "backend": self.name}


class JevBackend:
    """TypeSafe/Jev Choice adapter.

    One request batches independent closed questions over the same state.  Low
    confidence is surfaced to the caller instead of silently becoming authority.
    """

    name = "jev"

    def __init__(self, *, client: Any | None = None, model: str | None = None,
                 min_confidence: float = 0.60):
        if client is None:
            try:
                from typesafe_sdk import TypeSafeClient  # type: ignore
            except ImportError as exc:  # pragma: no cover - optional integration
                raise RuntimeError("Jev backend requires the optional 'typesafe-sdk' package") from exc
            client = TypeSafeClient()
        self.client = client
        self.model = model or os.environ.get("THEORYMASTER_JEV_MODEL") or "jev-latest"
        self.min_confidence = float(min_confidence)

    def decide(self, *, task: str, state: Mapping[str, Any], questions: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            from typesafe_sdk import Choice  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Jev backend requires the optional 'typesafe-sdk' package") from exc
        typed = {
            str(item["id"]): Choice(
                instructions=str(item["instruction"]),
                criteria={str(option): None for option in item.get("options") or []},
            )
            for item in questions
        }
        response = self.client.system_one(state=dict(state), questions=typed, model=self.model)
        answers: dict[str, Any] = {}
        confidence: dict[str, float] = {}
        uncertain: list[str] = []
        for qid in typed:
            answer = response.choices[qid]
            answers[qid] = answer.choice
            confidence[qid] = float(answer.confidence)
            if confidence[qid] < self.min_confidence:
                uncertain.append(qid)
        return {"answers": answers, "confidence": confidence, "uncertain": uncertain,
                "backend": self.name, "model": getattr(response, "model", self.model)}


class AgentSemanticBackend:
    """Strict JSON Agent-B fallback used for benchmarks and Jev outages."""

    name = "agent"

    def __init__(self, backend: Any, *, model: str, provider: str):
        self.backend, self.model, self.provider = backend, model, provider

    def decide(self, *, task: str, state: Mapping[str, Any], questions: list[dict[str, Any]]) -> dict[str, Any]:
        schema = {str(item["id"]): list(item.get("options") or []) for item in questions}
        prompt = (
            "You are the TheoryMaster B-layer semantic annotator. Select only among supplied options. "
            "Do not add scientific claims, requirements, findings, or identifiers. Return exactly one JSON "
            "object shaped as {\"answers\": {question_id: selected_option}}.\n"
            f"Task: {task}\nState:\n{json.dumps(dict(state), ensure_ascii=False)}\n"
            f"Questions:\n{json.dumps(questions, ensure_ascii=False)}\nAllowed options:\n{json.dumps(schema)}"
        )
        # Semantic closure is a pure, bounded classification request.  A
        # server-side turn can occasionally complete without exposing its final
        # item to the client.  Retrying that completed writer is ineffective;
        # retry once in a fresh thread instead.  This is deliberately limited
        # to the auxiliary semantic role, never a scientific role that might
        # create artifacts or advance a branch.
        try:
            thread = self.backend.ensure_thread("semantic_annotator", None, self.model, self.provider)
            _, raw = self.backend.invoke("semantic_annotator", prompt, thread, self.model, self.provider)
        except RuntimeError as exc:
            if "turn completed without assistant final response" not in str(exc):
                raise
            thread = self.backend.ensure_thread("semantic_annotator", None, self.model, self.provider)
            _, raw = self.backend.invoke("semantic_annotator", prompt, thread, self.model, self.provider)
        value = json.loads(raw)
        answers = value.get("answers") if isinstance(value, dict) else None
        if not isinstance(answers, dict):
            raise ValueError("Agent-B must return an answers object")
        for qid, options in schema.items():
            if answers.get(qid) not in options:
                raise ValueError(f"Agent-B returned an invalid option for {qid}")
        return {"answers": answers, "confidence": {}, "backend": self.name}


class FallbackSemanticBackend:
    """Run Jev first and escalate uncertain/service-failure cases to Agent-B."""

    name = "jev_with_agent_fallback"

    def __init__(self, primary: SemanticDecisionBackend, fallback: SemanticDecisionBackend):
        self.primary, self.fallback = primary, fallback

    def decide(self, *, task: str, state: Mapping[str, Any], questions: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            primary = self.primary.decide(task=task, state=state, questions=questions)
            if not primary.get("uncertain"):
                return primary
            reason = "low_confidence"
        except Exception as exc:  # service failure must not silently write state
            primary = {"backend": getattr(self.primary, "name", type(self.primary).__name__),
                       "error": f"{type(exc).__name__}: {exc}"}
            reason = "primary_error"
        fallback = self.fallback.decide(task=task, state=state, questions=questions)
        return {**fallback, "escalated": True, "escalation_reason": reason,
                "primary_decisions": primary}


def annotate_specialist(artifact: Mapping[str, Any], context: Mapping[str, Any],
                        backend: SemanticDecisionBackend) -> tuple[dict[str, Any], dict[str, Any]]:
    """Annotate A findings against frozen criteria/objections without new science."""
    findings = []
    questions: list[dict[str, Any]] = []
    for index, finding in enumerate(artifact.get("findings") or []):
        ref = f"finding:{index}"
        findings.append({"ref": ref, **dict(finding)})
        questions.append({"id": f"{ref}/relation", "instruction": "How does this finding relate to the locked hypothesis?",
                          "options": list(FINDING_RELATIONS)})
    lock = context.get("hypothesis_lock") if isinstance(context.get("hypothesis_lock"), dict) else {}
    criteria = (
        context.get("cycle_criteria") or context.get("cycle_progress_criteria")
        or lock.get("cycle_criteria") or lock.get("cycle_progress_criteria") or {}
    )
    frozen = criterion_rows(criteria)
    for item in frozen:
        cid = str(item.get("id") or "")
        if cid:
            for index, _ in enumerate(findings):
                questions.append({"id": f"finding:{index}/criterion:{cid}",
                                  "instruction": "Does this specific finding supply evidence for this frozen criterion?",
                                  "options": ["unrelated", "related"]})
    for item in context.get("judge_objections") or []:
        oid = str(item.get("objection_id") or "")
        if oid:
            for index, _ in enumerate(findings):
                questions.append({"id": f"finding:{index}/objection:{oid}",
                                  "instruction": "Is this specific finding relevant evidence for this existing Judge objection?",
                                  "options": ["unrelated", "relevant"]})
    # Current Investigator semantic closure only binds authored findings to
    # criteria/objections.  Workflow routing and unresolved-gap adjudication
    # belong to the independent Judge, not a B-layer classification.
    state = {"hypothesis": context.get("hypothesis_lock") or context.get("branch"),
             "frozen_criteria": context.get("cycle_criteria") or context.get("cycle_progress_criteria"),
             "judge_objections": context.get("judge_objections") or [], "findings": findings}
    if not questions:
        # No findings and no authored remaining question leaves B with nothing
        # to classify. Close that case locally without a semantic model call.
        closed = {"evidence": []}
        return closed, {"a_artifact": dict(artifact), "local_objects": [],
                        "semantic_decisions": {"backend": "deterministic_noop", "answers": {}},
                        "c_input": closed}
    result = backend.decide(task="specialist_semantic_closure", state=state, questions=questions)
    if result.get("uncertain"):
        raise ValueError("semantic closure uncertain for: " + ", ".join(result["uncertain"]))
    answers = result["answers"]
    evidence = []
    for index, finding in enumerate(artifact.get("findings") or []):
        ref = f"finding:{index}"
        evidence.append({"summary": finding["statement"], "relation": answers[f"{ref}/relation"],
                          "criterion_ids": [str(item.get("id")) for item in frozen
                                            if answers.get(f"{ref}/criterion:{item.get('id')}") == "related"],
                          "objection_ids": [str(item.get("objection_id")) for item in (context.get("judge_objections") or [])
                                            if answers.get(f"{ref}/objection:{item.get('objection_id')}") == "relevant"],
                          "scope": finding.get("scope") or "",
                          "limitations": finding.get("limitations") or "", "local_ref": ref})
        if finding.get("provenance"):
            evidence[-1]["provenance"] = dict(finding["provenance"])
    closed = {"evidence": evidence}
    return closed, {"a_artifact": dict(artifact), "local_objects": findings,
                    "semantic_decisions": result, "c_input": closed}


def annotate_literature(artifact: Mapping[str, Any], context: Mapping[str, Any],
                        backend: SemanticDecisionBackend) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind Literature source findings to H/criteria without making the Literature Agent echo workflow state."""
    records = [dict(item) for item in artifact.get("evidence_records") or [] if isinstance(item, dict)]
    if not records:
        return dict(artifact), {
            "a_artifact": dict(artifact), "local_objects": [],
            "semantic_decisions": {"backend": "deterministic_noop", "answers": {}},
            "c_input": {"evidence_records": []},
        }
    synthetic = {
        "findings": [{
            "statement": str(item.get("scientific_insight") or ""),
            "limitations": str(item.get("limitations") or ""),
        } for item in records],
        # Literature routing is owned by the surrounding workflow.  B binds only
        # already-authored source claims, so it must not invent a new remaining gap.
        "remaining_scientific_question": "",
    }
    bound, audit = annotate_specialist(synthetic, context, backend)
    bindings = list(bound.get("evidence") or [])
    annotated = dict(artifact)
    annotated_records: list[dict[str, Any]] = []
    for index, item in enumerate(records):
        binding = bindings[index] if index < len(bindings) and isinstance(bindings[index], dict) else {}
        row = dict(item)
        row["relation"] = str(binding.get("relation") or "neutral")
        row["criterion_ids"] = list(binding.get("criterion_ids") or [])
        if binding.get("objection_ids"):
            row["objection_ids"] = list(binding.get("objection_ids") or [])
        annotated_records.append(row)
    annotated["evidence_records"] = annotated_records
    return annotated, {
        **audit,
        "a_artifact": dict(artifact),
        "c_input": {"evidence_records": annotated_records},
    }
