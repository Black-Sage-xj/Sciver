"""SDK-internal, independent and bounded Investigator resource review.

The model only proposes an operational decision; investigator_budget.apply_review
is the sole durable allowance writer. No tmux, subprocess, or external agent.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any

from .investigator_budget import (
    apply_review, budget_status, format_k_tokens, workspace_review_progress,
)
from .runtime import write_json, now
from .budget_review_transcript import conversation_review_progress, dialogue_source

REVIEW_MODEL = "gpt-6-luna"
REVIEW_FALLBACK_MODEL = "gpt-6-sol"  # Only after a proven missing-final delivery fault.
REVIEW_PROVIDER = "CodexHTTPS"
REVIEW_EFFORT = "medium"


MAX_TRANSIENT_REVIEW_ATTEMPTS = 3  # initial attempt plus two fresh retries


def _transient_reviewer_failure(exc: Exception) -> bool:
    """Only delivery/format faults qualify; never a budget veto or ledger error."""
    return (
        (isinstance(exc, ValueError) and str(exc) in {
            "Budget Reviewer did not return one valid JSON object",
            "Budget Reviewer decision must be approve or reject",
            "Budget Reviewer output fails typed decision contract",
        })
        or (isinstance(exc, RuntimeError)
            and "turn completed without assistant final response" in str(exc).lower())
    )


def _invoke_reviewer_retry(backend: Any, prompt: str, scope: dict[str, Any],
                           trace_folder: Path, review_index: int
                           ) -> tuple[str | None, str, dict[str, Any], str, str, str]:
    """Fresh isolated attempts; no budget decision or grant is made here."""
    missing_final_seen = False
    for attempt in range(1, MAX_TRANSIENT_REVIEW_ATTEMPTS + 1):
        model = REVIEW_FALLBACK_MODEL if missing_final_seen else REVIEW_MODEL
        thread_id: str | None = None
        trace_id = uuid.uuid4().hex
        snapshot_name = f"reviewer_visible_thread_{review_index:04d}_{trace_id}.jsonl"
        snapshot: dict[str, Any] = {"status": "unavailable",
                                    "reason": "Reviewer native thread not archived"}
        try:
            invoke = getattr(backend, "invoke_budget_review", None)
            if callable(invoke):
                thread_id, raw = invoke(prompt, model, REVIEW_PROVIDER)
            else:
                thread_id, raw = backend.invoke(
                    "budget_reviewer", prompt, None, model, REVIEW_PROVIDER)
            try:
                _parse_decision(raw, scope)
            except InvalidBudgetReviewReferences:
                # A real scientific approve/reject exists: let the independent
                # immutable-decision reference-only correction handle it.
                pass
        except Exception as exc:
            if not _transient_reviewer_failure(exc):
                raise
            if (isinstance(exc, RuntimeError)
                    and "turn completed without assistant final response" in str(exc).lower()):
                missing_final_seen = True
            # This receipt records a technical attempt, never an approval.
            write_json(trace_folder / f"technical_retry_{review_index:04d}_{trace_id}.json", {
                "schema": "theory-master/budget-review-technical-retry/v1",
                "at": now(), "attempt": attempt, "model": model,
                "limit": MAX_TRANSIENT_REVIEW_ATTEMPTS,
                "error_type": type(exc).__name__, "error": str(exc)[:360],
                "budget_granted": False,
            })
            if attempt == MAX_TRANSIENT_REVIEW_ATTEMPTS:
                raise
            continue
        finally:
            if thread_id:
                try:
                    capture = getattr(backend, "snapshot_budget_review_thread", None)
                    if callable(capture):
                        snapshot = capture(thread_id, trace_folder / snapshot_name)
                    else:
                        snapshot = {"status": "unavailable",
                                    "reason": "Backend has no SDK thread/read snapshot"}
                except Exception as exc:
                    snapshot = {"status": "unavailable",
                                "reason": f"{type(exc).__name__}: {str(exc)[:180]}"}
                finally:
                    drop = getattr(backend, "drop_thread", None)
                    if callable(drop):
                        drop(thread_id)
        return thread_id, raw, snapshot, trace_id, snapshot_name, model
    raise AssertionError("bounded Reviewer retry exhausted")


def _compact(value: Any, limit: int = 1500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _pending_progress(cycle_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    # The scientific research_state deliberately need not embed runtime paths.
    status = budget_status(cycle_dir, Path(state.get("research_root") or cycle_dir.parent), state)
    already_evidence = set(status["reviewed_evidence_ids"])
    already_progress = set(status["reviewed_progress_refs"])
    evidence: list[dict[str, str]] = []
    evidence_path = cycle_dir / "evidence" / "registry.jsonl"
    if evidence_path.is_file():
        for line in evidence_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            eid = str(row.get("evidence_id") or "")
            if eid and eid not in already_evidence:
                evidence.append({
                    "id": eid, "summary": _compact(row.get("summary") or row.get("statement")),
                })
    progress: list[dict[str, Any]] = []
    for attempt in (state.get("investigation_attempts") or {}).values():
        if not isinstance(attempt, dict):
            continue
        for item in attempt.get("outputs") or []:
            if not isinstance(item, dict) or str(item.get("executor_role") or item.get("role") or "") != "investigator":
                continue
            invocation = str(item.get("invocation_id") or "")
            if not invocation or invocation in already_progress:
                continue
            output = item.get("output") or {}
            if not isinstance(output, dict) or not (output.get("findings") or output.get("evidence")):
                continue
            findings = output.get("findings") or output.get("evidence") or []
            progress.append({
                "invocation_id": invocation,
                "findings": [
                    {"statement": _compact(row.get("statement") or row.get("summary"), 750),
                     "limitations": _compact(row.get("limitations"), 350)}
                    for row in findings[-8:] if isinstance(row, dict)
                ],
            })
    # Original human-visible Investigator dialogue is the PRIMARY source of
    # ongoing Native Goal work. Unlike formal Findings, its SHA-authenticated
    # new events are available *during* the Goal and are not curated by SDK.
    progress.extend(conversation_review_progress(cycle_dir, already_progress))
    # Preserve old report-SHA refs for historical/offline ledger compatibility,
    # but the independent Reviewer investigates the entire workspace itself.
    for item in workspace_review_progress(cycle_dir):
        if item["invocation_id"] in already_progress:
            continue
        progress.append({
            "invocation_id": item["invocation_id"], "kind": item["kind"],
            "artifact": item["artifact"], "sha256": item["sha256"],
            "summary": _compact(item["summary"], 650),
        })
    # Only reference IDs actually supplied to the reviewer may be accepted.
    return {
        "budget": {k: status[k] for k in (
            "observed_tokens", "allowance_tokens", "tranche_tokens", "review_index")},
        "evidence": evidence[-40:],
        "progress": (
            [row for row in progress if row.get("kind") == "unfinished_native_goal_original_dialogue_not_finding"]
            + [row for row in progress if row.get("kind") != "unfinished_native_goal_original_dialogue_not_finding"][:12]
        ),
    }


def _recent_incidents(cycle_dir: Path) -> list[dict[str, str]]:
    """Expose bounded objective fault signals, never a whole raw tool log."""
    from .observability.incident_registry import _rows
    rows: list[dict[str, str]] = []
    for record in _rows(cycle_dir)[-80:]:
        if not isinstance(record, dict) or record.get("lifecycle") != "registered":
            continue
        observed_at = str(record.get("at") or "")
        try:
            event_time = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        # Stale, already-resolved incidents are history, not active faults.
        if datetime.now(timezone.utc) - event_time.astimezone(timezone.utc) > timedelta(hours=3):
            continue
        rows.append({
            "at": observed_at,
            "severity": _compact(record.get("severity"), 30),
            "code": _compact(record.get("code"), 100),
            "summary": _compact(record.get("summary"), 450),
        })
    return rows[-12:]


class InvalidBudgetReviewReferences(ValueError):
    """A valid resource decision cited identifiers not offered by the SDK."""
    def __init__(self, bad_evidence: set[str], bad_progress: set[str]):
        self.bad_evidence = sorted(bad_evidence)
        self.bad_progress = sorted(bad_progress)
        super().__init__("Budget Reviewer referenced unknown or previously reviewed progress")


def _parse_decision(text: str, scope: dict[str, Any]) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", raw, flags=re.I).strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Budget Reviewer did not return one valid JSON object") from exc
    if not isinstance(value, dict) or value.get("decision") not in {"approve", "reject"}:
        raise ValueError("Budget Reviewer decision must be approve or reject")
    reason = value.get("reason")
    next_test = value.get("next_test")
    evidence_ids = value.get("evidence_ids")
    progress_refs = value.get("progress_refs")
    if (not isinstance(reason, str) or len(reason.strip()) < 30
            or not isinstance(next_test, str) or len(next_test.strip()) < 20
            or not isinstance(evidence_ids, list) or any(not isinstance(v, str) for v in evidence_ids)
            or not isinstance(progress_refs, list) or any(not isinstance(v, str) for v in progress_refs)):
        raise ValueError("Budget Reviewer output fails typed decision contract")
    allowed_evidence = {row["id"] for row in scope["evidence"]}
    allowed_progress = {row["invocation_id"] for row in scope["progress"]}
    unknown_evidence = set(evidence_ids) - allowed_evidence
    unknown_progress = set(progress_refs) - allowed_progress
    if unknown_evidence or unknown_progress:
        raise InvalidBudgetReviewReferences(unknown_evidence, unknown_progress)
    return {"decision": value["decision"], "reason": reason.strip(),
            "next_test": next_test.strip(), "evidence_ids": evidence_ids,
            "progress_refs": progress_refs}


def _parse_decision_core(text: str) -> dict[str, Any]:
    """Parse an already typed decision without accepting unverified IDs."""
    # Reuse the complete typed contract, but supply its own referenced IDs
    # as the local scope: this NEVER authorizes any ledger mutation.
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", raw, flags=re.I).strip()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Budget Reviewer decision is not an object")
    evidence = value.get("evidence_ids")
    progress = value.get("progress_refs")
    if not isinstance(evidence, list) or not isinstance(progress, list):
        raise ValueError("Budget Reviewer references are not lists")
    return _parse_decision(text, {
        "evidence": [{"id": v} for v in evidence if isinstance(v, str)],
        "progress": [{"invocation_id": v} for v in progress if isinstance(v, str)],
    })


def budget_review_prompt(context: dict[str, Any]) -> str:
    """Fixed wrapper + bounded context; raw dialogue stays on disk for inspection."""
    return (
        "Independent, read-only TheoryMaster Budget Reviewer. Inspect the "
        "ORIGINAL Investigator dialogue, then selectively verify scientific "
        "work in the provided workspace using read-only tools before judging "
        "whether another bounded resource tranche is justified. Focus on "
        "the frozen test, actual progress and its limitations; use targeted "
        "reads rather than rereading the entire multi-megabyte archive or "
        "reproducing the Investigator research. Use at most 10 targeted "
        "read-only tool calls: inspect early, middle and recent dialogue plus "
        "the most decisive reports, then immediately finish. When a source "
        "is inaccessible, explicitly distinguish operational uncertainty "
        "from a scientific rejection. Finish with the required JSON "
        "decision (or reject with a specific "
        "reason if insufficient progress); never leave the turn without a "
        "final answer. Cite valid progress identifiers only.\nContext:\n"
        + json.dumps(context, ensure_ascii=False)
    )


def review_budget_in_sdk(*, backend: Any, cycle_dir: Path, state: dict[str, Any],
                         goal: str, research_contract: str, final_gate: str) -> dict[str, Any]:
    """One fresh, read-only model call. No workflow or research-agent handoff."""
    scope = _pending_progress(cycle_dir, state)
    lock = state.get("hypothesis_lock") or {}
    dialogue = dialogue_source(cycle_dir)
    runtime = state.get("threads") or {}
    investigator_thread_id = str(
        runtime.get("investigator") or (state.get("attention") or {}).get("thread_id") or ""
    )
    # Provider's original visible items add command/MCP stdout where available.
    # thread/read is a read-only SDK operation on the *existing* Investigator
    # thread; it does not resume its Goal or trigger a new scientific Turn.
    snapshot: dict[str, Any] = {"status": "unavailable"}
    capture = getattr(backend, "snapshot_budget_review_thread", None)
    if callable(capture) and investigator_thread_id:
        try:
            snapshot = capture(
                investigator_thread_id,
                Path(cycle_dir) / "budget" /
                f"investigator_visible_thread_{scope['budget']['review_index']:04d}.jsonl",
            )
        except Exception as exc:
            snapshot = {"status": "unavailable", "reason": f"{type(exc).__name__}: {str(exc)[:180]}"}
    if not dialogue["exists"] and snapshot.get("status") != "available" and not (scope["evidence"] or scope["progress"]):
        # Missing observational inputs are an operational error, NOT scientific
        # evidence of no progress. Leave the budget ledger untouched.
        raise RuntimeError("Budget Reviewer has no readable native Goal dialogue or verified research progress")
    source_access = {
        "original_dialogue": dialogue,
        "provider_visible_thread_snapshot": snapshot,
        "workspace_root": str((Path(cycle_dir) / "workspace").resolve()),
        "cycle_root": str(Path(cycle_dir).resolve()),
        "source_limit": "Current Cycle scientific workspace, transcript, scoped task reference files",
        "inspector_role": "Independent, read-only Codex Budget Reviewer; NOT Judge",
    }
    context = {
        "goal": _compact(goal, 1800),
        "research_contract": _compact(research_contract, 2000),
        "final_gate": _compact(final_gate, 1500),
        "frozen_hypothesis": _compact(lock.get("hypothesis"), 1600),
        "frozen_decisive_test": _compact(lock.get("decisive_test"), 2000),
        "token_usage": {
            key: format_k_tokens(value) for key, value in scope["budget"].items()
            if key.endswith("tokens")
        },
        "review_index": scope["budget"]["review_index"],
        "source_access": source_access,
        "new_registered_evidence": scope["evidence"],
        "new_unreviewed_progress_references_not_scientific_verdicts": scope["progress"],
        "human_operator_direction": _compact(
            (state.get("operator_guidance") or {}).get("text"), 1500
        ) if isinstance(state.get("operator_guidance"), dict) else "",
        "recent_execution_incidents": _recent_incidents(cycle_dir),
        "protocol_health": _compact(state.get("protocol_health"), 140),
        "policy": (
            "This is a RESOURCE decision, NOT Judge science adjudication. "
            "FIRST use read-only file/shell tools to investigate the original chronological "
            "Investigator dialogue recorded at source_access.original_dialogue.path; read "
            "early, middle, and recent scientific actions including unsuccessful attempts, "
            "not merely a file count or SDK-selected Findings. The event log preserves "
            "assistant messages and tool activity labels but not command stdout; when "
            "available, inspect the provider-visible thread snapshot for original tool "
            "results, and verify important conclusions by opening actual reports, source "
            "code and outputs anywhere under source_access.workspace_root. You may "
            "search the workspace yourself; no fixed directory or report whitelist. "
            "Compare new work against previously reviewed progress to distinguish "
            "scientific advances from repeated/null work. Approve only if there is "
            "credible new science and a worthwhile bounded next experiment; a negative "
            "or unfinished result may be progress, but repetitions or seven failed "
            "equivalent qualification screens without strategy improvement may not be. "
            "Do NOT demand formal Evidence/Findings before a Native Goal completes. "
            "If a data source is unavailable or tool outputs are absent, say so; do "
            "not pretend to have inspected it or call invisibility research failure. "
            "Never change scientific state, inspect secrets, write files, invoke agents "
            "or change the token budget yourself. Output only JSON with decision, "
            "reason, next_test, evidence_ids and progress_refs, citing ONLY provided "
            "new identifiers. The SDK verifies claims and applies any token grant."
        ),
    }
    prompt = budget_review_prompt(context)
    thread_id: str | None = None
    # Keep the exact actual Reviewer input and available provider-visible
    # messages/tool results separate from the Investigator's source snapshot.
    # Per-invocation names avoid collisions if a rejected/failed review is retried.
    trace_index = int(scope["budget"]["review_index"])
    trace_id = uuid.uuid4().hex
    trace_basename = f"reviewer_dialogue_{trace_index:04d}_{trace_id}.json"
    snapshot_basename = f"reviewer_visible_thread_{trace_index:04d}_{trace_id}.jsonl"
    trace_folder = Path(cycle_dir) / "budget"
    # The first quota review may precede creation of the ledger directory.
    trace_folder.mkdir(parents=True, exist_ok=True)
    thread_id, raw, reviewer_snapshot, trace_id, snapshot_basename, reviewer_model = _invoke_reviewer_retry(
        backend, prompt, scope, trace_folder, trace_index,
    )
    trace_basename = f"reviewer_dialogue_{trace_index:04d}_{trace_id}.json"
    try:
        decision = _parse_decision(raw, scope)
    except InvalidBudgetReviewReferences as bad_refs:
        # A typed, scientific decision is already available; only its ID list
        # may change. One SDK-controlled correction attempt is allowed.
        original = _parse_decision_core(raw)
        allowed_evidence = [row["id"] for row in scope["evidence"]]
        allowed_progress = [row["invocation_id"] for row in scope["progress"]]
        repair_prompt = (
            "SDK Budget Reviewer reference-only contract repair. The original "
            "resource decision is IMMUTABLE: repeat decision, reason, next_test "
            "EXACTLY. Correct ONLY evidence_ids and progress_refs using exact "
            "identifiers from the allowed lists. Do not reassess science. "
            "Return JSON with all five fields and no prose.\n"
            + json.dumps({
                "original_decision": original,
                "allowed_evidence_ids": allowed_evidence,
                "allowed_progress_refs": allowed_progress,
                "invalid_evidence_ids": bad_refs.bad_evidence,
                "invalid_progress_refs": bad_refs.bad_progress,
            }, ensure_ascii=False)
        )
        correction_raw: str | None = None
        correction_problem: str | None = None
        corrected: dict[str, Any] | None = None
        repair_thread: str | None = None
        try:
            internal_invoke = getattr(backend, "invoke_budget_review", None)
            if callable(internal_invoke):
                repair_thread, correction_raw = internal_invoke(
                    repair_prompt, reviewer_model, REVIEW_PROVIDER,
                )
            else:
                repair_thread, correction_raw = backend.invoke(
                    "budget_reviewer", repair_prompt, None, reviewer_model, REVIEW_PROVIDER,
                )
            corrected = _parse_decision(correction_raw, scope)
            if any(corrected[key] != original[key]
                   for key in ("decision", "reason", "next_test")):
                raise ValueError("reference repair tried to change the resource decision")
        except Exception as exc:
            correction_problem = f"{type(exc).__name__}: {exc}"
            corrected = None
        finally:
            if repair_thread:
                drop = getattr(backend, "drop_thread", None)
                if callable(drop):
                    drop(repair_thread)
        if corrected is None:
            if original["decision"] != "reject":
                # In particular an approval must NEVER be minted by pruning
                # model-hallucinated IDs or by a failed repair attempt.
                write_json(trace_folder / f"reference_repair_{trace_index:04d}_{trace_id}.json", {
                    "status": "failed_closed", "original_raw": raw,
                    "correction_raw": correction_raw, "error": correction_problem,
                    "invalid_evidence_ids": bad_refs.bad_evidence,
                    "invalid_progress_refs": bad_refs.bad_progress,
                })
                raise ValueError("Budget Reviewer approval reference repair failed closed: "
                                 + str(correction_problem)) from bad_refs
            # A rejection never grants resources. Preserve the independent
            # science decision and retain only SDK-verified references.
            corrected = dict(original)
            corrected["evidence_ids"] = sorted(
                set(original["evidence_ids"]).intersection(allowed_evidence))
            corrected["progress_refs"] = sorted(
                set(original["progress_refs"]).intersection(allowed_progress))
            correction_method = "safe_reject_reference_prune"
        else:
            correction_method = "one_reviewer_reference_retry"
        write_json(trace_folder / f"reference_repair_{trace_index:04d}_{trace_id}.json", {
            "status": "corrected", "method": correction_method,
            "original_raw": raw, "correction_raw": correction_raw,
            "original_decision": original["decision"],
            "validated_decision": corrected["decision"],
            "invalid_evidence_ids": bad_refs.bad_evidence,
            "invalid_progress_refs": bad_refs.bad_progress,
            "correction_problem": correction_problem,
            "accepted_evidence_ids": corrected["evidence_ids"],
            "accepted_progress_refs": corrected["progress_refs"],
        })
        decision = corrected
    record = apply_review(
        cycle_dir, decision=decision["decision"], reason=decision["reason"],
        next_test=decision["next_test"], evidence_ids=decision["evidence_ids"],
        progress_refs=decision["progress_refs"],
    )
    from .investigator_budget import _entries
    review_path = cycle_dir / "budget" / f"internal_review_{record['review_index']:04d}.json"
    if review_path.exists():
        review_path = cycle_dir / "budget" / (
            f"internal_review_{record['review_index']:04d}_ledger{len(_entries(cycle_dir)):04d}.json"
        )
        if review_path.exists():
            raise ValueError("internal budget review output collision; refusing overwrite")
    # Write a separate durable trace receipt only after SDK commits the ledger.
    # It is never part of the token authorization and never mutates old reviews.
    reviewer_trace = {"status": "unavailable",
                      "reason": "Reviewer trace receipt could not be persisted"}
    try:
        trace_file = trace_folder / trace_basename
        if trace_file.exists():
            raise FileExistsError("Reviewer trace file already exists")
        write_json(trace_file, {
            "schema": "theory-master/budget-reviewer-dialogue/v1",
            "review_index": record["review_index"], "thread_id": thread_id,
            "model": reviewer_model, "provider": REVIEW_PROVIDER,
            "prompt": prompt, "model_output": raw,
            "visible_thread": {
                "status": reviewer_snapshot.get("status", "unavailable"),
                "file": snapshot_basename if reviewer_snapshot.get("status") == "available" else None,
                "sha256": reviewer_snapshot.get("sha256"),
                "visible_items": reviewer_snapshot.get("visible_items", 0),
                "excluded_private_items": reviewer_snapshot.get("excluded_private_items", 0),
                "archive_truncated": reviewer_snapshot.get("archive_truncated", False),
                "reason": reviewer_snapshot.get("reason", ""),
            },
        })
        reviewer_trace = {"status": "available", "file": trace_basename}
    except (OSError, ValueError, TypeError) as exc:
        reviewer_trace = {"status": "unavailable",
                          "reason": f"{type(exc).__name__}: {str(exc)[:180]}"}
    write_json(review_path, {
        "at": now(), "schema": "theory-master/internal-budget-review/v1",
        "research_sources": source_access,
        "reviewer_trace": reviewer_trace,
        "read_paths_reported_by_sdk": list(getattr(backend, "last_budget_review_reads", []) or [])[:150],
        "dialogue_progress_refs": [
            row["invocation_id"] for row in scope["progress"]
            if row.get("kind") == "unfinished_native_goal_original_dialogue_not_finding"
        ],
        "review_index": record["review_index"], "model": REVIEW_MODEL,
        "provider": REVIEW_PROVIDER, "model": reviewer_model, "reasoning_effort": REVIEW_EFFORT,
        "decision": record["decision"], "evidence_ids": record["evidence_ids"],
        "progress_refs": record["progress_refs"], "new_allowance": record["new_allowance"],
        "reason": record["reason"], "next_test": record["next_bounded_test"],
    })
    return record
