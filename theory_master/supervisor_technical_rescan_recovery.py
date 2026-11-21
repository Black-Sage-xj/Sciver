"""One task-local, receipt-bound reentry after an authorized parser rescan.

The old blocked Goal remains terminal. This validator only permits a fresh
Investigator Goal to consider an already computed audit; it certifies no science.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


TASK = "18-nls-education-ce37e1e233bc"
THREAD = "01a1efc2-3559-7563-a106-4f569a393141"
ORIGIN = "inv-7-investigator-202175d6205c"
LAST = "inv-7-investigator-4d3c96fde900"


def _read(path: Path, inputs: dict[str, str], *, limit: int = 16_000_000) -> bytes:
    from .native_goal import _safe_workspace_bytes
    data = _safe_workspace_bytes(Path("/"), Path(*path.absolute().parts[1:]), max_bytes=limit)
    inputs[str(path.absolute())] = hashlib.sha256(data).hexdigest()
    return data


def _json(path: Path, inputs: dict[str, str]) -> dict:
    value = json.loads(_read(path, inputs))
    if not isinstance(value, dict):
        raise ValueError("technical rescan input is not an object")
    return value


def _idle_thread(cycle: Path) -> bool:
    from .codex_accounts import runtime_home
    from .codex_sdk_compat import read_thread
    from openai_codex import Codex, CodexConfig
    home = runtime_home().absolute()
    if home.is_symlink() or not (home / "config.toml").is_file():
        return False
    with Codex(CodexConfig(env={"CODEX_HOME": str(home)}, cwd=str(cycle / "workspace"))) as client:
        attached = client.thread_resume(THREAD, include_turns=False)
        observed = read_thread(client, THREAD, include_turns=False).thread
        return (attached.id == observed.id == THREAD
                and getattr(getattr(observed.status, "root", None), "type", None) == "idle")


def validate_technical_rescan(cycle: Path, state: dict, *, engine: Path) -> dict | None:
    """Recognize only Master18 Cycle7's original blocked parser attempt."""
    from .invocations import InvocationLedger
    from .native_goal_recovery import verify_existing_goal
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_native_goal_recovery import read_existing_goal
    from .supervisor_repair_receipts import (
        EXECUTION_LIMIT_PROTOCOL_VALIDATOR, _record_validation, failure_binding,
        source_manifest,
    )
    recovery, native = state.get("recovery") or {}, state.get("native_goal") or {}
    if (cycle.name != "7" or cycle.parent.parent.name != TASK
            or state.get("status") != "failed" or state.get("execution_active")
            or state.get("protocol_health") != "valid" or state.get("scientific_verdict") is not None
            or recovery.get("kind") != "NativeGoalRuntimeFailure"
            or native.get("status") != "blocked"
            or native.get("runtime_failure_kind") != "native_goal_dependency_blocked"
            or native.get("thread_id") != THREAD or native.get("provider_failure")
            or state.get("last_invocation_id") != LAST
            or state.get("active_invocation_id") is not None
            or (state.get("threads") or {}).get("investigator") != THREAD
            or resume_blocker(state, runtime_revalidated=True) is not None):
        return None
    inputs: dict[str, str] = {}
    try:
        ledger = InvocationLedger(cycle / "invocations.jsonl")
        old, last = ledger.snapshot(ORIGIN) or {}, ledger.snapshot(LAST) or {}
        _read(cycle / "invocations.jsonl", inputs)
        _read(cycle / "events.jsonl", inputs)
        _read(cycle / "runtime_events.jsonl", inputs)
        if (old.get("state") != "interrupted" or old.get("commit_state") != "not_committed"
                or old.get("thread_id") != THREAD or old.get("role") != "investigator"
                or last.get("state") != "failed" or last.get("commit_state") != "not_committed"
                or last.get("thread_id") != THREAD or last.get("role") != "investigator"
                or old.get("logical_key") != last.get("logical_key")
                or (last.get("native_goal_origin") or {}).get("invocation_id") != ORIGIN
                or (native.get("origin_binding") or {}).get("invocation_id") != ORIGIN
                or (cycle / "semantic_closure" / (LAST + ".json")).exists()
                or (cycle / "workspace/.tm-handoff" / (LAST + ".json")).exists()):
            return None
        origin = native["origin_binding"]
        observed = verify_existing_goal(
            read_existing_goal(cycle, origin), origin, previous=native.get("persistent_goal"))
        if observed["status"] != "blocked" or not _idle_thread(cycle):
            return None
        audit = cycle / "workspace/audit"
        authorization = _json(audit / "technical-rescan-authorization.json", inputs)
        first = _json(audit / "result-first-scan-invalid.json", inputs)
        result = _json(audit / "result.json", inputs)
        _read(audit / "run.py", inputs)
        _read(audit / "README.md", inputs)
        guidance = _json(cycle / "attention/operator_guidance.json", inputs)
        _read(cycle / "attention/operator_guidance.md", inputs)
        manifest = _json(Path(result["inputs"]["manifest_path"]), inputs)
        if (authorization.get("schema") != "theory-master/task-technical-rescan/v1"
                or authorization.get("original_result_sha256") != inputs[str(audit / "result-first-scan-invalid.json")]
                or authorization.get("corrected_result_sha256") != inputs[str(audit / "result.json")]
                or authorization.get("script_sha256") != inputs[str(audit / "run.py")]
                or authorization.get("csv_sha256") != result["inputs"]["csv_sha256"]
                or authorization.get("csv_bytes") != result["inputs"]["csv_bytes"]
                or not str(first.get("audit_status") or "").startswith("incomplete: numeric parser")
                or first.get("witness_cell") is not None
                or result.get("rows") != 12686 or result.get("respondents") != 12686
                or result.get("base_1979_age_15_21_grade_0_12") != {"12": 3658, "13-20": 1290}
                or result.get("complete_four_1981_asvab_nonnegative_numeric") != {"12": 3198, "13-20": 1155}
                or result.get("witness_cell") != {"age_1979": 16, "sex_raw": 1, "race_raw": 3,
                                                   "mother_12": 183, "mother_13_20": 81}
                or not all(result.get("checks", {}).values())
                or len(result.get("field_coverage") or []) != 61
                or result["inputs"]["csv_sha256"] != manifest["data_files"][0]["sha256"]
                or result["inputs"]["csv_bytes"] != manifest["data_files"][0]["bytes"]
                or guidance.get("status") != "pending" or guidance.get("source") != "human"
                or guidance.get("attention_code") != "technical_rescan_authorized"
                or "workspace/audit/result.json" not in guidance.get("text", "")):
            return None
        _read(Path(state["last_safe_checkpoint"]), inputs)
        manifest_source = source_manifest(engine)
        binding = failure_binding(cycle, state)
        tests = {"resume_preparation": "technical_rescan_new_goal",
                 "origin_invocation_id": ORIGIN, "blocked_thread_id": THREAD,
                 "blocked_goal_identity": observed,
                 "corrected_result_sha256": inputs[str(audit / "result.json")],
                 "scientific_verdict": None}
        if source_manifest(engine) != manifest_source or failure_binding(cycle, state) != binding:
            return None
        return _record_validation(
            cycle, state, engine, validator=EXECUTION_LIMIT_PROTOCOL_VALIDATOR,
            module="supervisor_technical_rescan_recovery.py",
            change_id="user-authorized-technical-rescan", tests=json.dumps(tests, sort_keys=True),
            manifest=manifest_source, disposition="already_fixed", extra_inputs=inputs)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return None
