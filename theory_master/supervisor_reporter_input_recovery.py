"""Prove an oversized, uncommitted Reporter invocation can fit on reentry."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .invocations import stable_hash
from .public_submission import read_bounded_regular_file
from .report_materials import compact_report_materials

VALIDATOR = "reporter-input-size-reentry/v1"
_ERROR = re.compile(r"JSON-RPC error -32602: Input exceeds the maximum length of ([1-9][0-9]*) characters\.")
_MAX_READ = 32 * 1024 * 1024


def is_reporter_input_overflow(state: dict) -> bool:
    recovery = state.get("recovery") or {}
    failure = state.get("last_invocation_failure") or {}
    return bool(state.get("status") == "failed" and recovery.get("action") == "error"
                and recovery.get("kind") == "InvalidParamsError"
                and recovery.get("category") == "runtime"
                and _ERROR.fullmatch(str(recovery.get("message") or ""))
                and failure.get("role") == "reporter" and failure.get("stage") == "report"
                and failure.get("failure_phase") == "invoke"
                and failure.get("error_type") == "InvalidParamsError")


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in read_bounded_regular_file(path, _MAX_READ).splitlines()
            if line.strip()]


def _proof(cycle: Path, state: dict) -> tuple[dict, dict[str, str]]:
    if (not is_reporter_input_overflow(state) or state.get("active_invocation_id")
            or state.get("execution_active") or state.get("protocol_health") != "valid"):
        raise ValueError("No stopped Reporter input overflow")
    checkpoint = Path(str(state.get("last_safe_checkpoint") or ""))
    if checkpoint.parent != cycle / "checkpoints":
        raise ValueError("Reporter recovery requires the original Cycle checkpoint")
    saved = json.loads(read_bounded_regular_file(checkpoint, _MAX_READ))
    outputs = state.get("role_outputs") or {}
    lock = state.get("hypothesis_lock") or {}
    if (state.get("cycle_id") != cycle.name or lock.get("status") != "locked" or not lock.get("id")
            or not all(outputs.get(role) for role in ("investigator", "judge", "research_lead"))
            or "reporter" in outputs or state.get("assessment_state") != "terminal"
            or state.get("scientific_phase") != "committed"
            or any(saved.get(key) != state.get(key) for key in
                   ("cycle_id", "worker_id", "hypothesis_lock", "assessment_state",
                    "scientific_phase", "scientific_verdict", "role_outputs"))):
        raise ValueError("Reporter checkpoint differs from the committed research state")
    inputs = {}
    for filename, role in (("cycle_synthesis_out.json", "research_lead"),
                           ("frontier_state_out.json", "frontier_maintenance"),
                           ("scientific_model_out.json", None), ("scientific_model_update.json", None)):
        path = cycle / filename
        blob = read_bounded_regular_file(path, _MAX_READ)
        value = json.loads(blob)
        if not isinstance(value, dict) or (role and (not value or value != outputs.get(role))):
            raise ValueError("Committed Reporter prerequisite changed")
        inputs[str(path)] = hashlib.sha256(blob).hexdigest()

    invocation = state.get("last_invocation_id")
    thread = (state.get("threads") or {}).get("reporter")
    error = state["recovery"]["message"]
    events = _rows(cycle / "invocations.jsonl")
    linked = [row for row in events if row.get("invocation_id") == invocation]
    actual = {}
    for row in linked:
        actual.update(row)
    expected = {
        "kind": "agent", "role": "reporter", "stage": "report", "state": "failed",
        "commit_state": "not_committed", "error": error, "thread_id": thread,
        "failure_phase": "invoke", "error_type": "InvalidParamsError",
    }
    if (not invocation or invocation != state["last_invocation_failure"].get("invocation_id")
            or not thread or any(actual.get(key) != value for key, value in expected.items())
            or actual.get("invocation_artifact_manifest") or actual.get("closure_hash")):
        raise ValueError("Original uncommitted Reporter attempt could not be verified")

    logs = _rows(cycle / "runtime_events.jsonl")
    starts = [row for row in logs if row.get("event") == "AGENT_STARTED"
              and row.get("invocation_id") == invocation and row.get("thread_id") == thread
              and row.get("role") == "reporter" and row.get("stage") == "report"]
    match = _ERROR.fullmatch(error)
    limit = int(match[1])
    matched = []
    if len(starts) == 1:
        for row in logs:
            if (row.get("event") != "AGENT_INPUT" or row.get("role") != "reporter"
                    or row.get("stage") != "report" or row.get("input_kind") != "task"):
                continue
            prompt = row.get("content")
            if (row.get("invocation_id") not in (None, invocation)
                    or row.get("thread_id") not in (None, thread) or not isinstance(prompt, str)
                    or len(prompt) != starts[0].get("prompt_chars") or len(prompt) <= limit):
                continue
            prefix, marker, payload = prompt.partition("Context:\n")
            if not marker:
                continue
            context = json.loads(payload)
            if isinstance(context, dict) and stable_hash(context) == actual.get("input_hash"):
                matched.append((prompt, prefix + marker, context))
    if len(matched) != 1:
        raise ValueError("Original oversized request does not match the invocation input hash")

    original, prefix, context = matched[0]
    materials = context.get("report_materials")
    if not isinstance(materials, list) or not materials:
        raise ValueError("Original request has no projected report materials")
    compact = compact_report_materials(materials)
    replay = prefix + json.dumps({**context, "report_materials": compact}, ensure_ascii=False)
    if len(replay.encode()) > limit // 2:
        raise ValueError("Rebuilt Reporter request still lacks transport headroom")
    manifest_found = False
    for row in compact:
        if not row.get("manifest_path") or not row.get("manifest_sha256"):
            continue
        path = Path(row["manifest_path"])
        if path.parent != cycle / "invocation_artifacts" or path.suffix != ".json":
            raise ValueError("Reporter material manifest is outside the original Cycle")
        digest = hashlib.sha256(read_bounded_regular_file(path, _MAX_READ)).hexdigest()
        if digest != row["manifest_sha256"]:
            raise ValueError("Reporter material manifest changed")
        inputs[str(path)] = digest
        manifest_found = True
    if not manifest_found:
        raise ValueError("No original report material manifest survived compaction")
    return {
        "invocation_id": invocation, "thread_id": thread, "input_hash": actual["input_hash"],
        "original_prompt_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "projected_prompt_sha256": hashlib.sha256(replay.encode()).hexdigest(),
        "original_chars": len(original), "projected_chars": len(replay),
        "projected_bytes": len(replay.encode()), "limit_chars": limit,
    }, inputs


def validate_reporter_input_reentry(cycle: Path, state: dict, *, engine: Path) -> dict | None:
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_repair_receipts import _digest, _record_validation, source_manifest

    if not is_reporter_input_overflow(state) or resume_blocker(state, for_repair=True) is not None:
        return None
    manifest = source_manifest(engine)
    proof, inputs = _proof(cycle, state)
    if source_manifest(engine) != manifest:
        raise ValueError("Reporter projection source changed during validation")
    return _record_validation(cycle, state, engine, validator=VALIDATOR,
        module="prompt_stages.py", change_id="existing-source:" + _digest(manifest),
        tests=json.dumps(proof, sort_keys=True), manifest=manifest,
        disposition="already_fixed", extra_inputs=inputs)


def recheck_receipt_inputs(receipt: dict, state: dict) -> None:
    proof, inputs = _proof(Path(receipt["cycle"]), state)
    if proof != json.loads(receipt["tests"]) or inputs != receipt.get("extra_inputs"):
        raise ValueError("Verified Reporter request or manifest identity changed")


def require_reporter_input_reentry(cycle: Path, state: dict) -> None:
    """Recheck the approved retry in the execution owner before changing state."""
    if not is_reporter_input_overflow(state):
        return
    from .supervisor_repair_receipts import inspect_repair_receipt, repair_credit_key

    control = cycle / "recovery_control.json"
    if control.exists() or control.is_symlink():
        raise ValueError("Pending operator control forbids Reporter input reentry")
    receipt = inspect_repair_receipt(cycle, state)
    if (receipt["validator"] != VALIDATOR
            or repair_credit_key(state) not in (state.get("supervisor_consumed_repairs") or [])):
        raise ValueError("Reporter input reentry requires the authorized Supervisor repair")
