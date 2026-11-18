"""Offline authority for the historical, missing benchmark alias fault.

This certifies an engine repair and a preserved delivery, never its science.
The compatibility proof is deliberately restricted to one reviewed source
revision whose only normal native delivery path required Goal completion.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

VALIDATOR = "benchmark-provenance-alias/v1"
ERROR = "provenance declaration_ref must resolve to exactly one local artifact"
ORIGINAL_HEAD = "44ead6990584e2cc172708482b435a6a306adec7"
# Reviewed complete call chain, including the parser and failure boundary.
ORIGINAL_HASHES = {
    "codex_backend.py": "7caedee3e0ca2fed4301d0c27a73d43a92e6dc5e565e31e70865ef52c06b68e8",
    "codex_goal_transport.py": "d7d0041d6b231b278ea8fa5c3d49fb3d1d11a62be507c9913a0cb5dd6c0d0d3b",
    "native_goal.py": "dcf8a33e78d639668a16e26d9210912efbcab0ac4f66208c3661df56d00c01f9",
    "invocation_transport.py": "d70dea16e721acfc3ef1ebe55e7566fbedcd189f92427a2b5dc98ff1d273c028",
    "invocation_execution.py": "3b47790a5a5d3e642c408656c95e5440dab10a5e49c9294096d9fbb16cf99912",
    "invocation_output.py": "a251a0d1180be86dffefbd6e9b246c68dca082de90dce5a2151057cb334a1975",
    "invocation_commit.py": "686c9b6fd35d5792970b4c8ffec71ffd69ae390d56dd1d650f0fd92c516ea4cf",
    "workflow_invocation.py": "0526312b694c8114344d5ca941e658594ffb2fc1c938577e18d4f93394091c24",
    "invocation_lifecycle.py": "2894e0127016f97b81aa55bfadf8213ac239d92570d39c586cb61fbc82d578f2",
    "evidence.py": "356446868db26f549c57f42597111344d3e7928f793866d34d271024d6697d5d",
    "evidence_provenance.py": "04643b43f2e84961ea13b34703b16ee55bb55b79ba424d484ac871466f621c02",
}
_RECORDED_CHAIN = {
    "codex_backend.py", "codex_goal_transport.py", "invocation_transport.py",
    "invocation_execution.py", "invocation_output.py", "invocation_commit.py",
    "invocation_lifecycle.py",
}


def _read(path: Path, inputs: dict[str, str], limit: int = 16 * 1024 * 1024) -> bytes:
    from .native_goal import _safe_workspace_bytes
    path = path.absolute()
    data = _safe_workspace_bytes(Path("/"), Path(*path.parts[1:]), max_bytes=limit)
    inputs[str(path)] = hashlib.sha256(data).hexdigest()
    return data


def _json(path: Path, inputs: dict[str, str], limit: int = 1024 * 1024) -> dict:
    data = json.loads(_read(path, inputs, limit))
    if not isinstance(data, dict):
        raise ValueError("recovery input is not an object")
    return data


def _rows(path: Path, inputs: dict[str, str]) -> list[dict]:
    rows = [json.loads(line) for line in _read(path, inputs).splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("recovery ledger contains a non-object")
    return rows


def _original_chain(state: dict, engine: Path) -> dict[str, str]:
    provenance = state.get("runtime_source_provenance") or {}
    if (provenance.get("sdk_git_sha") != ORIGINAL_HEAD
            or provenance.get("source_coherent") is not True
            or provenance.get("source_coherence_errors")
            or provenance.get("sdk_git_root") != str(engine.parent.resolve())
            or provenance.get("source_root") != str(engine.parent.resolve())):
        raise ValueError("unreviewed original execution source")
    hashes, paths = provenance.get("module_sha256", {}), provenance.get("module_paths", {})
    for name in _RECORDED_CHAIN:
        if hashes.get(name) != ORIGINAL_HASHES[name] or paths.get(name) != str(engine / name):
            raise ValueError("original execution provenance does not bind the call chain")
    for name, expected in ORIGINAL_HASHES.items():
        source = subprocess.run(
            ["git", "-C", str(engine.parent), "show", f"{ORIGINAL_HEAD}:theory_master/{name}"],
            capture_output=True, check=True, timeout=10,
        ).stdout
        if hashlib.sha256(source).hexdigest() != expected:
            raise ValueError("reviewed original source object changed")
    return dict(ORIGINAL_HASHES)


def _evidence(cycle: Path, state: dict, engine: Path) -> tuple[dict, dict[str, str]]:
    import ast
    from .contract_stages import role_contract
    from .evidence import EvidenceRegistry
    from .evidence_provenance import normalize_provenance
    from .invocations import stable_hash
    from .native_goal import (NativeGoalResult, read_goal_findings, execution_limit_setup_binding,
                              verify_native_goal_context_reference, FROZEN_CONTEXT_SHA_MARKER)
    from .scientific_objective import frozen_science_digest, serialize_frozen_science
    from .task import effective_path_model

    inputs: dict[str, str] = {}
    control = cycle / "recovery_control.json"
    if control.exists() or control.is_symlink():
        pending = _json(control, inputs)
        if pending.get("action") in {"pause", "interrupt", "kill", "abandon_cycle"}:
            raise ValueError("an operator control remains pending")
    for name in ("test_supervisor_benchmark_recovery_20261119.py", "test_benchmark_evidence_binding_20261119.py"):
        _read(engine.parent / "tests" / name, inputs)
    wiring = ast.parse((engine / "workflow_state.py").read_text())
    factories = [node for node in ast.walk(wiring) if isinstance(node, ast.FunctionDef)
                 and node.name == "_evidence_registry"]
    if len(factories) != 1 or not any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "EvidenceRegistry"
            and any(keyword.arg == "benchmark_root"
                    and ast.unparse(keyword.value) == "self.spec.benchmark_root"
                    for keyword in node.keywords) for node in ast.walk(factories[0])):
        raise ValueError("current Workflow does not bind the explicit benchmark root")
    invocation = str(state.get("last_invocation_id") or "")
    if not re.fullmatch(r"inv-[A-Za-z0-9_-]+", invocation):
        raise ValueError("missing original invocation")
    failure = state.get("last_invocation_failure") or {}
    if failure != {"invocation_id": invocation, "role": "investigator", "stage": "investigate",
                   "failure_phase": "semantic_closure", "error_type": "ValueError"}:
        raise ValueError("not the original post-delivery closure failure")
    if state.get("active_invocation_id") is not None:
        raise ValueError("another invocation is active")
    ledger = _rows(cycle / "invocations.jsonl", inputs)
    own = [row for row in ledger if row.get("invocation_id") == invocation]
    folded = {}
    for row in own:
        folded.update(row)
    if (not own or ledger[-1].get("invocation_id") != invocation
            or folded.get("state") != "failed" or folded.get("commit_state") != "not_committed"
            or folded.get("role") != "investigator" or folded.get("stage") != "investigate"
            or folded.get("failure_phase") != "semantic_closure"
            or folded.get("error_type") != "ValueError" or folded.get("error") != ERROR
            or not folded.get("logical_key") or not isinstance(folded.get("contract_ref"), dict)
            or folded.get("contract_ref") != role_contract("investigator", phase="investigate").descriptor()
            or folded.get("closure_hash")):
        raise ValueError("invocation terminal record is inconsistent")
    thread = str(folded.get("thread_id") or "")
    threads = state.get("threads") or {}
    if not thread or thread not in [value for key, value in threads.items()
                                   if key == "investigator" or key.endswith(":investigator")]:
        raise ValueError("original Investigator thread changed")
    if (cycle / "semantic_closure" / f"{invocation}.json").exists():
        raise ValueError("closure already exists")
    events = _rows(cycle / "events.jsonl", inputs)
    starts = [r for r in events if r.get("type") == "role.started"
              and (r.get("payload") or {}).get("invocation_id") == invocation]
    if (len(starts) != 1 or starts[0]["payload"].get("thread_id") != thread
            or starts[0]["payload"].get("role") != "investigator"
            or starts[0]["payload"].get("stage") != "investigate"):
        raise ValueError("ambiguous invocation start")
    start, finish = starts[0]["at"], (state.get("recovery") or {}).get("at")
    if not isinstance(finish, str) or not start <= finish:
        raise ValueError("invalid failure window")
    rows = [r for r in _rows(cycle / "runtime_events.jsonl", inputs)
            if start <= str(r.get("ts") or "") <= finish]
    recovery = state["recovery"]

    def original_failure_projection(row: dict) -> bool:
        # Collector's final RUN_FAILED mirrors this exact semantic-closure
        # exception. It is not a provider error; no other error is waived.
        return (row.get("event") == "RUN_FAILED" and row.get("ts") == finish
                and row.get("error") == recovery["message"] == ERROR
                and row.get("error_kind") == recovery["kind"] == "ValueError"
                and row.get("failure_category") == recovery["category"] == "runtime_validation"
                and row.get("resumable") is state.get("resumable")
                and all(row.get(key) is None for key in ("thread_id", "role", "agent")))

    if any((r.get("event") in {"LLM_FAILED", "LLM_TURN_FAILED"} or r.get("error"))
           and (r.get("thread_id") in (None, thread) or r.get("role") == "investigator"
                or r.get("agent") == "investigator")
           and not original_failure_projection(r) for r in rows):
        raise ValueError("provider error invalidates the success-return proof")
    setups = [r for r in rows if r.get("event") == "AGENT_INPUT"
              and r.get("role") == "investigator" and r.get("stage") == "investigate"
              and r.get("input_kind") == "native_goal_setup_turn"]
    objectives = [r for r in rows if r.get("event") == "AGENT_INPUT"
                  and r.get("role") == "investigator" and r.get("stage") == "investigate"
                  and r.get("input_kind") == "native_goal_objective"]
    if len(setups) != 1 or len(objectives) != 1:
        raise ValueError("ambiguous native delivery setup")
    for row in [*setups, *objectives]:
        if row.get("thread_id", thread) != thread or row.get("invocation_id", invocation) != invocation:
            raise ValueError("foreign native delivery setup")
    handoff = cycle / "workspace" / ".tm-handoff" / f"{invocation}.json"
    setup = setups[0]["content"].partition("\n")[2]
    binding = execution_limit_setup_binding(setup, handoff)
    if (binding is None or binding["invocation_id"] != invocation
            or binding["frozen_objective_sha256"] != frozen_science_digest(state["hypothesis_lock"])):
        raise ValueError("native handoff is not bound to the frozen invocation")
    objective = objectives[0]["content"]
    verify_native_goal_context_reference(objective, setup)
    if (FROZEN_CONTEXT_SHA_MARKER not in objective
            and serialize_frozen_science(state["hypothesis_lock"]) not in objective):
        raise ValueError("original native objective does not contain the frozen science")
    _read(handoff, inputs, 1024 * 1024)
    # This is only the reviewed parser's deterministic mapping, not a fabricated
    # Goal event. Actual successful return is proved by the pinned call chain.
    mapped = json.loads(read_goal_findings(NativeGoalResult("complete", thread, 0, 0), handoff))
    if state.get("agent_outputs", {}).get("investigator") != mapped:
        raise ValueError("preserved authored output does not equal the delivered handoff")
    pointer = cycle.parent / ".theory-master" / "task.json"
    pointer_value = _json(pointer, inputs)
    config_path = Path(str(pointer_value.get("config_path") or ""))
    if pointer_value.get("schema") != "theory-master/task/v2" or not config_path.is_absolute():
        raise ValueError("invalid canonical config binding")
    config = _json(config_path, inputs)
    _, research, _ = effective_path_model(config)
    if (research != cycle.parent or config.get("schema") != "theory-master/system/v1"
            or config_path.parent != Path(config.get("task_home") or "")):
        raise ValueError("config does not bind this research root")
    benchmark = Path(str(config.get("benchmark_root") or ""))
    if not benchmark.is_absolute() or benchmark.is_symlink() or not benchmark.is_dir():
        raise ValueError("benchmark root must be explicit")
    registry = EvidenceRegistry(cycle, cycle / "workspace", benchmark_root=benchmark)
    declarations = {}
    for finding in mapped["findings"]:
        provenance = finding.get("provenance")
        if not provenance:
            continue
        ref = provenance.get("declaration_ref")
        if not isinstance(ref, str) or not ref.startswith("benchmark/"):
            raise ValueError("the historical failure is not a benchmark alias")
        normalized = normalize_provenance(finding, workspace=cycle / "workspace",
                                          resolve_local_ref=registry._provenance_declaration_matches)
        declaration = Path(normalized["declaration_ref"])
        _read(declaration, inputs)
        if inputs[str(declaration)] != normalized["declaration_sha256"]:
            raise ValueError("declaration changed during normalization")
        declarations[str(declaration)] = inputs[str(declaration)]
    if not declarations:
        raise ValueError("no benchmark provenance declaration")
    manifest_path = cycle / "invocation_artifacts" / f"{invocation}.json"
    manifest = _json(manifest_path, inputs)
    if (manifest.get("invocation_id") != invocation or manifest.get("role") != "investigator"
            or manifest.get("stage") != "investigate" or manifest.get("status") != "failed"
            or manifest.get("workspace") != str(cycle / "workspace")
            or folded.get("invocation_artifact_manifest") != str(manifest_path)
            or manifest.get("modified") or manifest.get("deleted")):
        raise ValueError("unpreserved original artifact inventory")
    for row in manifest.get("created", []):
        relative = Path(str(row.get("relative_path") or ""))
        path = cycle / "workspace" / relative
        if relative.is_absolute() or ".." in relative.parts or str(path) != row.get("path"):
            raise ValueError("unsafe original artifact")
        if hashlib.sha256(_read(path, inputs)).hexdigest() != row.get("sha256"):
            raise ValueError("original artifact changed")
    for name in ("coverage_matrix.json", "evidence_manifest.json"):
        path = cycle / "workspace" / name
        if path.exists() or path.is_symlink():
            _read(path, inputs)
    attempt = int(state.get("investigation_attempt") or 0)
    if attempt:
        directory = cycle / "investigation_attempts" / f"attempt-{attempt:04d}"
        for name in ("state.json", "handoff.json", "judge_objections.json", "readiness.json", "threads.json"):
            path = directory / name
            if path.exists() or path.is_symlink():
                _read(path, inputs)
    _read(Path(state["last_safe_checkpoint"]), inputs)
    log_path = cycle.parent / ".theory-master" / "runner.log"
    log = _read(log_path, inputs).decode("utf-8")
    trace = log[log.rfind("Traceback (most recent call last):"):]
    frames = re.findall(r'File "([^"\n]+\.py)", line \d+', trace)
    if (not trace.endswith(ERROR + "\n")
            or str(engine / "evidence.py") not in frames
            or str(engine / "evidence_provenance.py") not in frames
            or str(engine / "invocation_commit.py") not in frames):
        raise ValueError("failure is not attributable to the benchmark registration chain")
    native = state.get("native_goal") or {}
    if native.get("delivery_receipt"):
        journal_path = cycle / "native_deliveries" / f"{invocation}.json"
        if native["delivery_receipt"] != str(journal_path):
            raise ValueError("foreign native delivery journal")
        journal = _json(journal_path, inputs)
        expected = {
            "schema": "theory-master/native-delivery/v1", "authority": "sdk_return",
            "invocation_id": invocation, "thread_id": thread, "role": "investigator",
            "stage": "investigate", "logical_key": folded["logical_key"],
            "contract_ref": folded["contract_ref"], "handoff_path": str(handoff),
            "handoff_sha256": inputs[str(handoff)], "raw_findings_sha256": stable_hash(mapped),
            "objective_sha256": hashlib.sha256(objective.encode()).hexdigest(),
            "frozen_objective_sha256": binding["frozen_objective_sha256"],
        }
        observed = journal.get("native_goal") or {}
        raw = journal.get("raw")
        if (any(journal.get(key) != value for key, value in expected.items())
                or observed.get("status") != "complete" or observed.get("thread_id") != thread
                or observed.get("provider_failure") or observed.get("runtime_failure_kind")
                or native.get("status") != "complete" or native.get("thread_id") != thread
                or native.get("provider_failure") or native.get("runtime_failure_kind")
                or native.get("invocation_id") != invocation or not isinstance(raw, str)
                or hashlib.sha256(raw.encode()).hexdigest() != journal.get("raw_sha256")
                or json.loads(raw) != mapped):
            raise ValueError("native delivery journal does not prove this successful return")
        authority, originals = "sdk_return", {}
    else:
        if native:
            raise ValueError("historical proof cannot override another native terminal record")
        originals = _original_chain(state, engine)
        authority = "verified_original_success_return_path"
    if sum(Path(path).stat().st_size for path in inputs) > 64 * 1024 * 1024:
        raise ValueError("benchmark recovery inputs exceed total size limit")
    return {
        "authority": authority, "original_git_head": (state.get("runtime_source_provenance") or {}).get("sdk_git_sha"),
        "original_source_hashes": originals, "invocation_id": invocation, "thread_id": thread,
        "role": "investigator", "stage": "investigate", "logical_key": folded["logical_key"],
        "contract_ref": folded["contract_ref"], "handoff_path": str(handoff),
        "config_path": str(config_path), "benchmark_root": str(benchmark), "research_root": str(cycle.parent),
        "handoff_sha256": inputs[str(handoff)], "raw_findings_sha256": stable_hash(mapped),
        "objective_sha256": hashlib.sha256(objective.encode()).hexdigest(),
        "frozen_objective_sha256": binding["frozen_objective_sha256"],
        "artifact_manifest_path": str(manifest_path), "artifact_manifest_sha256": inputs[str(manifest_path)],
        "declaration_paths": declarations, "resume_preparation": "saved_delivery_semantic_closure",
        "evidence_paths": sorted(inputs),
    }, inputs


def recheck_receipt_inputs(receipt: dict) -> None:
    """The historical reader's no-link/size boundary also applies on reuse."""
    inputs: dict[str, str] = {}
    total = 0
    for path, digest in receipt.get("extra_inputs", {}).items():
        try:
            total += len(_read(Path(path), inputs))
        except OSError as exc:
            raise ValueError("benchmark recovery input is missing or unsafe") from exc
        if inputs[path] != digest or total > 64 * 1024 * 1024:
            raise ValueError("benchmark recovery inputs changed or exceed their bound")


def validate_existing_benchmark_provenance(cycle: Path, state: dict, *, engine: Path) -> dict | None:
    """Validate only the reviewed post-delivery alias fault, without model calls."""
    from .runtime_supervisor_review import resume_blocker
    from .native_goal import NativeGoalDeliveryError
    from .supervisor_repair_receipts import failure_binding, source_manifest, _record_validation, _file_hash
    recovery = state.get("recovery") or {}
    if any(state.get(key) is not None and not isinstance(state[key], dict) for key in (
            "recovery", "native_goal", "investigator_budget", "hypothesis_lock", "threads",
            "runtime_source_provenance", "agent_outputs", "last_invocation_failure")):
        return None
    if (state.get("status") != "failed" or state.get("execution_active")
            or state.get("protocol_health") != "valid"
            or state.get("scientific_verdict") is not None
            or recovery.get("kind") != "ValueError" or recovery.get("message") != ERROR
            or recovery.get("category") != "runtime_validation"
            or resume_blocker(state, runtime_revalidated=True, for_repair=True) is not None):
        return None
    try:
        manifest, binding = source_manifest(engine), failure_binding(cycle, state)
        evidence, inputs = _evidence(cycle, state, engine)
        if (source_manifest(engine) != manifest or failure_binding(cycle, state) != binding
                or any(_file_hash(Path(path)) != digest for path, digest in inputs.items())):
            raise ValueError("benchmark recovery evidence changed")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, NativeGoalDeliveryError,
            subprocess.SubprocessError):
        return None
    return _record_validation(cycle, state, engine, validator=VALIDATOR, module="evidence.py",
                              change_id="existing-source:" + hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                              tests=json.dumps(evidence, sort_keys=True), manifest=manifest,
                              disposition="already_fixed", extra_inputs=inputs)


def prepare_benchmark_recovery(research_root: Path) -> dict:
    """Install an offline receipt under the ordinary recovery gate, never resume."""
    import fcntl
    from .runtime import write_json
    from .specification import latest_cycle_dir
    from .runtime_admission import admission_lock, assert_no_single_owner, assert_no_worker_claims
    from .runtime_manager import runtime_health
    root = Path(research_root).absolute()
    gate = root / ".theory-master" / "scheduler.lock"
    with admission_lock(root), gate.open("a+") as handle:
        assert_no_single_owner(root)
        assert_no_worker_claims(root)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if runtime_health(root).get("active"):
            raise ValueError("runtime ownership changed")
        cycle = latest_cycle_dir(root, task_mode=True)
        if cycle is None:
            raise ValueError("no existing Cycle")
        state_path = cycle / "research_state.json"
        state = json.loads(state_path.read_text())
        receipt = validate_existing_benchmark_provenance(cycle, state, engine=Path(__file__).resolve().parent)
        if receipt is None:
            raise ValueError("benchmark recovery proof did not validate")
        from .supervisor_repair_receipts import failure_binding
        current = json.loads(state_path.read_text())
        if failure_binding(cycle, current) != receipt["failure_binding"]:
            raise ValueError("persisted failure changed before receipt installation")
        control = cycle / "recovery_control.json"
        if control.exists() and json.loads(control.read_text()).get("action") in {"pause", "interrupt", "kill", "abandon_cycle"}:
            raise ValueError("operator control changed before receipt installation")
        write_json(state_path, state)
        return receipt
