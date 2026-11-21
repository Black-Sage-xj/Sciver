"""Guarded new Investigator turn after an invalid protected declaration.

The original native delivery is preserved as failed and uncommitted. No
provenance metadata or scientific finding is repaired by the SDK.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


TASK = "22-test2-20261119-101457-0__ywl7o-c47d1ec379e3"
INVOCATION = "inv-4-investigator-a9c5074eb0cf"
THREAD = "01a1f1c9-1fb5-7f80-a0b3-4eb5f2efed80"
ERROR = "protected provenance declaration_ref must be outside the current Cycle workspace"


def _read(path: Path, inputs: dict[str, str], *, limit: int = 16_000_000) -> bytes:
    from .native_goal import _safe_workspace_bytes
    path = path.absolute()
    data = _safe_workspace_bytes(Path("/"), Path(*path.parts[1:]), max_bytes=limit)
    inputs[str(path)] = hashlib.sha256(data).hexdigest()
    return data


def _json(path: Path, inputs: dict[str, str]) -> dict:
    value = json.loads(_read(path, inputs))
    if not isinstance(value, dict):
        raise ValueError("provenance retry input is not an object")
    return value


def _idle_thread(cycle: Path) -> bool:
    from openai_codex import Codex, CodexConfig
    from .codex_accounts import runtime_home
    from .codex_sdk_compat import read_thread
    home = runtime_home().absolute()
    if home.is_symlink() or not (home / "config.toml").is_file():
        return False
    with Codex(CodexConfig(env={"CODEX_HOME": str(home)}, cwd=str(cycle / "workspace"))) as client:
        attached = client.thread_resume(THREAD, include_turns=False)
        observed = read_thread(client, THREAD, include_turns=False).thread
        return (attached.id == observed.id == THREAD
                and getattr(getattr(observed.status, "root", None), "type", None) == "idle")


def validate_provenance_retry(cycle: Path, state: dict, *, engine: Path) -> dict | None:
    """Authorize one fresh Goal only for Master22 Cycle4's exact failed slot."""
    from .evidence import EvidenceRegistry
    from .invocations import InvocationLedger, stable_hash
    from .native_goal import NativeGoalResult, read_goal_findings
    from .runtime_supervisor_review import resume_blocker
    from .specification import TaskSpec
    from .supervisor_repair_receipts import (
        BENCHMARK_PROVENANCE_VALIDATOR, _record_validation, failure_binding,
        source_manifest,
    )
    recovery, native = state.get("recovery") or {}, state.get("native_goal") or {}
    if (cycle.name != "4" or cycle.parent.parent.name != TASK
            or state.get("status") != "failed" or state.get("execution_active")
            or state.get("protocol_health") != "valid" or not state.get("resumable")
            or state.get("scientific_verdict") is not None
            or recovery.get("kind") != "ValueError" or recovery.get("category") != "runtime_validation"
            or recovery.get("message") != ERROR
            or state.get("last_invocation_id") != INVOCATION
            or state.get("active_invocation_id") is not None
            or state.get("last_invocation_failure") != {
                "invocation_id": INVOCATION, "role": "investigator", "stage": "investigate",
                "failure_phase": "semantic_closure", "error_type": "ValueError"}
            or native.get("status") != "complete" or native.get("thread_id") != THREAD
            or native.get("provider_failure") or native.get("runtime_failure_kind")
            or (state.get("threads") or {}).get("investigator") != THREAD
            or resume_blocker(state, runtime_revalidated=True, for_repair=True) is not None):
        return None
    inputs: dict[str, str] = {}
    try:
        pointer = _json(cycle.parent / ".theory-master/task.json", inputs)
        config = Path(str(pointer.get("config_path") or ""))
        if pointer.get("schema") != "theory-master/task/v2" or not config.is_absolute():
            return None
        _json(config, inputs)
        spec = TaskSpec.from_config(config)
        if (spec.research_root.resolve() != cycle.parent.resolve()
                or state.get("goal_sha256") != spec.goal_sha256
                or not spec.benchmark_root.is_dir() or spec.benchmark_root.is_symlink()):
            return None
        benchmark = spec.benchmark_root / "source_manifest.json"
        _json(benchmark, inputs)
        _read(spec.document, inputs)
        _read(Path(state["last_safe_checkpoint"]), inputs)
        ledger = InvocationLedger(cycle / "invocations.jsonl")
        record = ledger.snapshot(INVOCATION) or {}
        _read(cycle / "invocations.jsonl", inputs)
        _read(cycle / "events.jsonl", inputs)
        _read(cycle / "runtime_events.jsonl", inputs)
        if (record.get("state") != "failed" or record.get("commit_state") != "not_committed"
                or record.get("role") != "investigator" or record.get("stage") != "investigate"
                or record.get("thread_id") != THREAD or record.get("failure_phase") != "semantic_closure"
                or record.get("error_type") != "ValueError" or record.get("error") != ERROR
                or record.get("closure_hash")
                or (cycle / "semantic_closure" / (INVOCATION + ".json")).exists()):
            return None
        handoff = cycle / "workspace/.tm-handoff" / (INVOCATION + ".json")
        raw = _read(handoff, inputs)
        mapped = json.loads(read_goal_findings(NativeGoalResult("complete", THREAD, 0, 0), handoff))
        if (len(mapped.get("findings") or []) != 1
                or state.get("agent_outputs", {}).get("investigator") != mapped):
            return None
        provenance = mapped["findings"][0].get("provenance") or {}
        if provenance != {"source": "benchmark_provided", "split": "validation",
                          "declaration_ref": "investigation/independent_check.json"}:
            return None
        registry = EvidenceRegistry(cycle, cycle / "workspace", benchmark_root=spec.benchmark_root)
        if registry.preflight_finding_provenance(mapped) != [{
            "path": "/findings/0/provenance/declaration_ref", "message": ERROR}]:
            return None
        # A real source declaration exists, but only the Investigator may
        # decide how its claims and generated checks should be attributed.
        if not benchmark.is_file() or not (spec.benchmark_root / "inputs/validation_trajectories.csv").is_file():
            return None
        journal = _json(Path(str(native.get("delivery_receipt") or "")), inputs)
        if (journal.get("schema") != "theory-master/native-delivery/v1"
                or journal.get("authority") != "sdk_return"
                or journal.get("invocation_id") != INVOCATION
                or journal.get("thread_id") != THREAD
                or journal.get("handoff_path") != str(handoff)
                or journal.get("handoff_sha256") != inputs[str(handoff)]
                or journal.get("raw_findings_sha256") != stable_hash(mapped)
                or not isinstance(journal.get("raw"), str)
                or hashlib.sha256(journal["raw"].encode()).hexdigest() != journal.get("raw_sha256")
                or json.loads(journal["raw"]) != mapped
                or journal.get("logical_key") != record.get("logical_key")
                or journal.get("contract_ref") != record.get("contract_ref")
                or (journal.get("native_goal") or {}).get("status") != "complete"):
            return None
        inventory_path = cycle / "invocation_artifacts" / (INVOCATION + ".json")
        inventory = _json(inventory_path, inputs)
        if (record.get("invocation_artifact_manifest") != str(inventory_path)
                or inventory.get("invocation_id") != INVOCATION
                or inventory.get("status") != "failed" or inventory.get("modified")
                or inventory.get("deleted")):
            return None
        for row in inventory.get("created", []):
            relative = Path(str(row.get("relative_path") or ""))
            path = cycle / "workspace" / relative
            if relative.is_absolute() or ".." in relative.parts or row.get("path") != str(path):
                return None
            _read(path, inputs)
            if inputs[str(path)] != row.get("sha256"):
                return None
        guidance = _json(cycle / "attention/operator_guidance.json", inputs)
        _read(cycle / "attention/operator_guidance.md", inputs)
        if (guidance.get("status") != "pending"
                or guidance.get("attention_code") != "invalid_workspace_provenance_repair"
                or "benchmark/source_manifest.json" not in guidance.get("text", "")):
            return None
        if not _idle_thread(cycle):
            return None
        manifest = source_manifest(engine)
        binding = failure_binding(cycle, state)
        proof = {"resume_preparation": "fresh_investigator_provenance",
                 "failed_invocation_id": INVOCATION, "thread_id": THREAD,
                 "original_handoff_sha256": inputs[str(handoff)],
                 "source_declaration": str(benchmark), "scientific_verdict": None}
        if source_manifest(engine) != manifest or failure_binding(cycle, state) != binding:
            return None
        return _record_validation(
            cycle, state, engine, validator=BENCHMARK_PROVENANCE_VALIDATOR,
            module="supervisor_provenance_retry.py",
            change_id="fresh-investigator-correction", tests=json.dumps(proof, sort_keys=True),
            manifest=manifest, disposition="already_fixed", extra_inputs=inputs)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return None
