"""Operator-supplied Python delivery with separate, immutable provenance."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from .public_submission import (MAX_PYTHON_BYTES, PYTHON_SCHEMA, _python_errors,
                                load_submission_contract, read_bounded_regular_file, strict_json)

SCHEMA = "theory-master/operator-public-submission/v1"
ORIGIN = "operator_supplied"
DIRECTORY = "operator_public_submissions"
RECEIPT_LIMIT = 1024 * 1024
SELECTION = "selected.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _folder(cycle: Path, assessment: dict) -> Path:
    return cycle / DIRECTORY / _sha(assessment["assessment_id"].encode())


def _identity(cycle: Path, assessment: dict, declaration: dict, submission: bytes,
              verification: bytes | None) -> dict:
    return {"schema": SCHEMA, "origin": ORIGIN, "cycle_id": cycle.name,
            "goal_assessment": assessment["assessment_id"], "outcome_id": assessment["outcome_id"],
            "prior_delivery_revision": assessment["delivery_revision"],
            "contract_sha256": declaration["sha256"], "submission_sha256": _sha(submission),
            "verification_sha256": _sha(verification) if verification is not None else None}


def _read_selection(path: Path) -> str:
    value = strict_json(read_bounded_regular_file(path, 256))
    selected = value.get("submission_id") if isinstance(value, dict) else None
    if not isinstance(selected, str) or not re.fullmatch(r"[a-f0-9]{64}", selected):
        raise ValueError("Invalid operator submission selection")
    return selected


def _read_folder(cycle: Path, assessment: dict, declaration: dict, selected: str) -> tuple[dict, dict[str, bytes]]:
    folder = _folder(cycle, assessment) / selected
    receipt = read_bounded_regular_file(folder / "receipt.json", RECEIPT_LIMIT)
    identity = strict_json(receipt)
    if not isinstance(identity, dict) or _sha(receipt) != selected:
        raise ValueError("Invalid operator submission receipt")
    filename = declaration["filename"]
    submission = read_bounded_regular_file(folder / filename, MAX_PYTHON_BYTES)
    verification = (read_bounded_regular_file(folder / "verification.json", RECEIPT_LIMIT)
                    if identity.get("verification_sha256") is not None else None)
    if identity != _identity(cycle, assessment, declaration, submission, verification):
        raise ValueError("Operator submission receipt or material changed")

    def asset(name: str, data: bytes, role: str) -> dict:
        return {"path": str(folder / name), "role": role, "sha256": _sha(data),
                "status": "available", "size": len(data)}

    files = {"receipt.json": receipt, filename: submission}
    result = {**identity, "receipt": asset("receipt.json", receipt, "operator_receipt"),
              "submission": asset(filename, submission, "implementation")}
    if verification is not None:
        files["verification.json"] = verification
        result["verification"] = asset("verification.json", verification, "operator_verification")
    return result, files


def _matches_outcome(assessment: dict | None, outcome: dict | None, cycle: Path) -> bool:
    return bool(assessment and not assessment.get("legacy") and assessment["verdict"] == "not_passed"
                and assessment["cycle_id"] == cycle.name and outcome and outcome["verdict"] == "supported"
                and assessment["outcome_id"] == outcome["id"])


def _check_operator_control(cycle: Path) -> dict:
    state = strict_json(read_bounded_regular_file(cycle / "research_state.json", 4_000_000))
    if not isinstance(state, dict):
        raise ValueError("Invalid Cycle state")
    control = cycle / "recovery_control.json"
    recovery = state.get("recovery") or {}
    if (not isinstance(recovery, dict) or control.exists() or control.is_symlink() or state.get("abandoned_cycle")
            or recovery.get("action") in {"pause", "interrupt", "kill", "abandon_cycle"}):
        raise ValueError("Cycle operator control blocks public submission attachment")
    return state


def _check_frozen_assessment(root: Path, cycle: Path, assessment: dict) -> None:
    from .delivery_repair import _canonical

    revision = assessment["delivery_revision"]
    if not re.fullmatch(r"[a-f0-9]{64}", revision):
        raise ValueError("Invalid prior Goal delivery revision")
    frozen = strict_json(read_bounded_regular_file(root / "delivery_revisions" / f"{revision}.json", 16_000_000))
    receipt = root / assessment["receipt_path"]
    if not receipt.is_relative_to(cycle / "goal_assessments"):
        raise ValueError("Prior Goal assessment receipt is outside its Cycle")
    record = strict_json(read_bounded_regular_file(receipt, 2_000_000))
    if (not isinstance(frozen, dict) or frozen.get("cycle_id") != cycle.name
            or _sha(_canonical(frozen)) != revision or not isinstance(record, dict)
            or any(record.get(key) != assessment[key] for key in
                   ("verdict", "assessment_id", "delivery_revision", "science_digest"))):
        raise ValueError("Prior Goal assessment or delivery revision changed")


def _require_idle_owner(root: Path, latest: Path, latest_state: dict) -> None:
    from .runtime_admission import assert_no_single_owner
    from .runtime_manager import runtime_health
    from .supervisor_activation import qualification_wait_owned
    from .worker_store import list_workers

    health = runtime_health(root)
    waiting = qualification_wait_owned(health) and health["registry"]["current_cycle"] == latest.name
    if not waiting:
        try:
            assert_no_single_owner(root)
        except (RuntimeError, ValueError) as exc:
            raise ValueError("An active or unverified Runner blocks public submission attachment") from exc
    if latest_state.get("execution_active") or any(worker["process_alive"] for worker in list_workers(root)):
        raise ValueError("Active scientific execution or a live Worker blocks public submission attachment")


def attach_public_submission(spec: Any, *, cycle_id: int, source: Path,
                             verification_receipt: Path | None = None) -> dict:
    """Register a local public deliverable without creating a scientific invocation."""
    from .goal_terminal import GoalSealedError, goal_sealed, science_commit_barrier
    from .notebook import atomic_json
    from .runtime_admission import admission_lock
    from .science_graph_store import ScienceGraphStore
    from .specification import find_cycle, latest_cycle_dir

    root = Path(spec.research_root)
    graph = spec.graph_database
    with admission_lock(root), science_commit_barrier(root, "exclusive"):
        if goal_sealed(root, graph, spec=spec):
            raise GoalSealedError("sealed Goal cannot accept a new public submission")
        cycle = find_cycle(root, str(cycle_id), task_mode=spec.task_mode)
        state = _check_operator_control(cycle)
        latest = latest_cycle_dir(root, task_mode=spec.task_mode) or cycle
        latest_state = _check_operator_control(latest) if latest != cycle else state
        _require_idle_owner(root, latest, latest_state)
        if state.get("status") not in {"over", "completed"} or state.get("execution_active"):
            raise ValueError("Operator submission requires a closed supported Cycle")
        with ScienceGraphStore(graph, readonly=True) as store:
            assessment = store.latest_goal_gate_assessment()
            outcome = store.cycle_evaluation(cycle.name)
        if not _matches_outcome(assessment, outcome, cycle):
            raise ValueError("Operator submission requires the latest frozen not_passed Goal and its supported Outcome")
        _check_frozen_assessment(root, cycle, assessment)
        declaration = load_submission_contract(spec)
        if not declaration or declaration["schema"] != PYTHON_SCHEMA:
            raise ValueError("Operator submission requires an explicit public Python submission contract")
        raw = read_bounded_regular_file(source.expanduser().absolute(), MAX_PYTHON_BYTES)
        if errors := _python_errors(raw, declaration):
            raise ValueError(errors[0]["message"])
        verification = None
        if verification_receipt is not None:
            verification = read_bounded_regular_file(verification_receipt.expanduser().absolute(), RECEIPT_LIMIT)
            proof = strict_json(verification)
            if not isinstance(proof, dict):
                raise ValueError("Operator verification receipt must be a JSON object")
            if "solution_sha256" in proof and proof["solution_sha256"] != _sha(raw):
                raise ValueError("Operator verification receipt names a different solution SHA-256")
        base = _folder(cycle, assessment)
        selection = base / SELECTION
        if (base.resolve() != base or selection.is_symlink()
                or (selection.exists() and not selection.is_file())):
            raise ValueError("Unsafe operator submission directory")
        identity = _identity(cycle, assessment, declaration, raw, verification)
        files = {declaration["filename"]: raw,
                 "receipt.json": (json.dumps(identity, sort_keys=True, indent=2) + "\n").encode()}
        selected = _sha(files["receipt.json"])
        folder = base / selected
        if folder.is_symlink():
            raise ValueError("Unsafe operator submission directory")
        if verification is not None:
            files["verification.json"] = verification
        if not folder.exists():
            base.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".attach-", dir=base) as staging:
                staged = Path(staging) / "delivery"
                staged.mkdir(mode=0o700)
                for name, data in files.items():
                    target = staged / name
                    target.write_bytes(data)
                    target.chmod(0o444)
                staged.rename(folder)
        result, existing = _read_folder(cycle, assessment, declaration, selected)
        if existing != files:
            raise ValueError("Operator submission bytes conflict with the immutable receipt")
        try:
            previous_selection = _read_selection(selection) if selection.exists() else None
        except ValueError:
            previous_selection = None
        if previous_selection != selected:
            atomic_json(selection, {"submission_id": selected})
        return result


def current_operator_submission(graph: Path, cycle: Path, declaration: dict | None) -> dict | None:
    """Read the most recent attachment; an invalid newer receipt cannot fall back."""
    from .science_graph_store import ScienceGraphStore

    base = cycle / DIRECTORY
    if not declaration or declaration["schema"] != PYTHON_SCHEMA:
        return None
    unavailable = {"schema": SCHEMA, "origin": ORIGIN, "status": "unavailable"}
    if base.is_symlink() or (base.exists() and not base.is_dir()):
        return unavailable
    if not base.is_dir():
        return None
    with ScienceGraphStore(graph, readonly=True) as store:
        outcome = store.cycle_evaluation(cycle.name)
        history = store.goal_gate_history(cycle.name)
    for assessment in reversed(history):
        selected = _folder(cycle, assessment) / SELECTION
        if not _matches_outcome(assessment, outcome, cycle) or not (selected.exists() or selected.is_symlink()):
            continue
        try:
            return _read_folder(cycle, assessment, declaration, _read_selection(selected))[0]
        except (OSError, ValueError, RecursionError):
            return unavailable
    return None


def checked_operator_asset(record: dict | None, declaration: dict, hashes: dict) -> dict | None:
    """Check the same explicit receipt and roles in live and frozen payloads."""
    if not isinstance(record, dict) or record.get("schema") != SCHEMA or record.get("origin") != ORIGIN:
        return None
    try:
        submission, receipt = record["submission"], record["receipt"]
        if record["contract_sha256"] != declaration["sha256"]:
            return None
        files = [(submission, declaration["filename"], "implementation", record["submission_sha256"]),
                 (receipt, "receipt.json", "operator_receipt", receipt["sha256"])]
        if record["verification_sha256"] is not None:
            files.append((record["verification"], "verification.json", "operator_verification",
                          record["verification_sha256"]))
        folder = Path(receipt["path"]).parent
        if (folder.name != receipt["sha256"] or folder.parent.name != _sha(record["goal_assessment"].encode())
                or folder.parent.parent.name != DIRECTORY or folder.parent.parent.parent.name != record["cycle_id"]):
            return None
        if any(asset.get("status") != "available" or asset.get("role") != role or asset["sha256"] != digest
               or asset["path"] != str(folder / name) or hashes.get(asset["path"]) != digest
               for asset, name, role, digest in files):
            return None
        raw = read_bounded_regular_file(Path(receipt["path"]), RECEIPT_LIMIT)
        value = strict_json(raw)
        identity = {k: v for k, v in record.items() if k not in {"submission", "receipt", "verification"}}
        if _sha(raw) == receipt["sha256"] and value == identity:
            return {**submission, "origin": ORIGIN}
    except (AttributeError, KeyError, OSError, TypeError, ValueError, RecursionError):
        return None
    return None
