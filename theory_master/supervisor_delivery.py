"""Evidence-bound administrative delivery from already adjudicated materials."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

from .goal_materials import required_benchmark_references, resolve_material
from .runtime import atomic, write_json

SCHEMA = "theory-master/supervisor-delivery/v1"
_CSV_LIMIT = 32_000_000
_RECORD_LIMIT = 16_000_000


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe(path: Path, root: Path) -> None:
    if (not path.resolve().is_relative_to(root.resolve())
            or any(part.is_symlink() for part in (path, *path.parents))):
        raise ValueError("Delivery path leaves its registered root or traverses a symlink")


def _read(path: Path, root: Path, limit: int = _RECORD_LIMIT) -> bytes:
    _safe(path, root)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Missing or oversized delivery input")
    return path.read_bytes()


def _records(path: Path, root: Path) -> list[dict]:
    return [json.loads(line) for line in _read(path, root).splitlines() if line]


def _header(path: Path, root: Path) -> list[str]:
    _safe(path, root)
    with path.open("rb") as handle:
        first = handle.readline(1_000_001)
    if not first.endswith(b"\n") or len(first) > 1_000_000:
        raise ValueError("Missing or oversized delivery CSV header")
    return next(csv.reader([first.decode("utf-8-sig")], strict=True), [])


def _csv(data: bytes) -> list[list[str]]:
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True))
    if (len(rows) < 2 or not rows[0] or len(set(rows[0])) != len(rows[0])
            or any(len(row) != len(rows[0]) for row in rows[1:])):
        raise ValueError("Delivery CSV must have a unique header and rectangular rows")
    return rows


def _sources(spec: Any, graph: Path, *, anchor: Path | None = None,
             assessment_id: str = "") -> tuple[dict, Path, dict, dict]:
    """One supported Outcome and its committed, SDK-captured source files."""
    from .delivery_materials import bind_invocation_materials
    from .goal_terminal import goal_sealed
    from .invocations import InvocationLedger
    from .science_graph_store import ScienceGraphStore
    from .specification import cycle_number, list_cycle_dirs

    root = Path(spec.research_root)
    if not graph.is_file() or (anchor is None and goal_sealed(root, graph, spec=spec)):
        raise ValueError("Goal is not open for delivery repair")
    with ScienceGraphStore(graph, readonly=True) as store:
        assessment = (store.goal_gate_assessment(anchor.name, assessment_id=assessment_id)
                      if anchor is not None else store.latest_goal_gate_assessment())
        if not assessment or assessment["verdict"] != "not_passed":
            raise ValueError("Delivery repair requires a prior Goal Judge gap")
        outcome = store.db.execute(
            "SELECT verdict,source_json FROM cycle_evaluations WHERE id=? AND cycle_id=?",
            (assessment["outcome_id"], assessment["cycle_id"]),
        ).fetchone()
    if not outcome or outcome["verdict"] != "supported":
        raise ValueError("Delivery repair requires an existing supported Outcome")
    cycle = next((p for p in list_cycle_dirs(root, task_mode=spec.task_mode)
                  if p.name == assessment["cycle_id"]), None)
    if cycle is None:
        raise ValueError("Goal source Cycle is unavailable")
    authority = json.loads(_read(cycle / "research_state.json", root))
    closed = authority.get("status") in {"over", "completed"} and not authority.get("execution_active")
    pending = authority.get("goal_delivery_pending") or {}
    # During assessment the existing Judge invocation projects the anchor as
    # active. A previously published supplement retains its closed authority.
    reassessing = (anchor is not None and pending.get("cycle_id") == cycle.name
                   and pending.get("closed_status") in {"over", "completed"})
    if authority.get("scientific_verdict") != "supported" or not (closed or reassessing):
        raise ValueError("Goal source Cycle is not a closed supported Cycle")

    evidence_ids = set(json.loads(outcome["source_json"]).get("evidence_refs") or [])
    evidence = [row for row in _records(cycle / "evidence/registry.jsonl", cycle)
                if row.get("evidence_id") in evidence_ids]
    lifecycle_path = cycle / "evidence/lifecycle.jsonl"
    lifecycle = ({row["evidence_id"]: row.get("action") for row in _records(lifecycle_path, cycle)
                  if row.get("evidence_id")} if lifecycle_path.is_file() else {})
    commits = {row.get("evidence_batch_id"): row for row in _records(
        cycle / "evidence/registration_batches.jsonl", cycle)
        if row.get("event") == "batch_committed"}
    ledger_path = cycle / "invocations.jsonl"
    _read(ledger_path, cycle)
    ledger = InvocationLedger(ledger_path)
    required = set(required_benchmark_references(spec))
    inputs, predictions = {}, {}
    frozen_workspace = None
    for record in evidence:
        batch = commits.get(record.get("evidence_batch_id"), {})
        producer = record.get("producer_invocation") or {}
        if (record.get("evidence_id") not in batch.get("evidence_ids", [])
                or lifecycle.get(record["evidence_id"]) not in {None, "restore"}
                or record.get("evidence_batch_hash") != batch.get("evidence_batch_hash")
                or producer != batch.get("producer_invocation")
                or producer.get("role") != "investigator"):
            continue
        provenance = record.get("provenance") or {}
        declaration = Path(str(provenance.get("declaration_ref") or ""))
        if not declaration.is_absolute():
            continue
        try:
            manifest_data = _read(declaration, Path(spec.benchmark_root))
        except (OSError, ValueError):
            continue
        if _digest(manifest_data) != provenance.get("declaration_sha256"):
            continue
        paired_inputs = {}
        for row in json.loads(manifest_data).get("files", []):
            ref = row.get("path", "")
            if ref not in required or not ref.endswith(".csv") or row.get("role") != "public_input":
                continue
            resolved = resolve_material(spec, ref)
            if resolved.get("status") == "available" and row.get("sha256") == resolved.get("sha256"):
                paired_inputs[ref] = resolved

        ident = producer.get("invocation_id")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", ident):
            continue
        manifest = cycle / "invocation_artifacts" / (ident + ".json")
        saved = ledger.snapshot(ident) or {}
        if (saved.get("state") != "committed" or saved.get("commit_state") != "committed"
                or any(saved.get(key) != producer.get(key) for key in ("role", "stage", "thread_id"))
                or saved.get("invocation_artifact_manifest") != str(manifest)):
            continue
        binding = bind_invocation_materials(manifest, cycle_dir=cycle, workspace=cycle / "workspace",
                                             producer_invocation=producer)
        pinned = record.get("execution_materials")
        if pinned and binding != pinned:
            continue
        if not pinned and frozen_workspace is None:
            # Legacy registries omitted the manifest binding. Require the exact
            # workspace frozen by the SDK before adjudication as independent
            # byte authority for the retained Investigator output.
            from .cycle_replay import _fingerprint_tree

            snapshot = cycle / "replay_checkpoints/judge_start.json"
            check = json.loads(_read(snapshot, cycle))
            frozen_workspace = snapshot.parent / "judge_start.inputs/workspace"
            _safe(frozen_workspace, cycle)
            if (check.get("boundary") != "after_complete_findings_before_judge"
                    or check.get("logical_cycle") != cycle_number(cycle.name)
                    or check.get("inputs_dir") != "judge_start.inputs"
                    or not frozen_workspace.is_dir()
                    or _fingerprint_tree(frozen_workspace) != check.get("workspace_sha256")):
                raise ValueError("Legacy prediction artifact has no matching frozen Judge checkpoint")
        artifacts = set((record.get("result") or {}).get("artifacts", []))
        captured = json.loads(_read(manifest, cycle))
        for row in [*(captured.get("created") or []), *(captured.get("modified") or [])]:
            name = row.get("relative_path", "")
            if not name.endswith(".csv") or name not in artifacts or Path(name).is_absolute():
                continue
            path = cycle / "workspace" / name
            if str(path.resolve()) != row.get("path"):
                continue
            sha256 = row.get("sha256")
            if (_digest(_read(path, cycle / "workspace", _CSV_LIMIT)) == sha256
                    and (pinned or _digest(_read(frozen_workspace / name, frozen_workspace, _CSV_LIMIT)) == sha256)):
                inputs.update(paired_inputs)
                predictions[name] = {
                    "path": str(path), "sha256": sha256, "inputs": sorted(paired_inputs),
                    "evidence_id": record["evidence_id"], "invocation_id": ident,
                }
    if not inputs or not predictions:
        raise ValueError("No verified public input and adjudicated prediction pair")
    return assessment, cycle, inputs, predictions


def delivery_repair_context(workflow: Any) -> dict | None:
    """Advertise only SDK-verifiable source identities in the SPV prompt."""
    try:
        assessment, cycle, inputs, predictions = _sources(workflow.spec, workflow._graph_database_path())
        return {
            "operation": "csv_label_projection",
            "goal_assessment": assessment["assessment_id"],
            "source_cycle": cycle.name,
            "inputs": [
                {"reference": name, "first_columns": _header(Path(row["path"]), Path(workflow.spec.benchmark_root))[:8]}
                for name, row in inputs.items()
            ],
            "predictions": [
                {"reference": name, "columns": _csv(_read(Path(row["path"]), cycle, _CSV_LIMIT))[0]}
                for name, row in predictions.items()
            ],
        }
    except (OSError, ValueError, TypeError, csv.Error):
        return None


def _project(request: dict, original: bytes, predicted: bytes) -> dict[str, str]:
    table, predictions = _csv(original), _csv(predicted)
    label, labels, targets = request["label_column"], request["allowed_labels"], request["outputs"]
    if (not isinstance(label, str) or not label.strip() or label in table[0]
            or not isinstance(labels, list) or not 2 <= len(labels) <= 32
            or any(not isinstance(value, str) or not value for value in labels)
            or len(set(labels)) != len(labels)
            or not isinstance(targets, dict) or not 1 <= len(targets) <= 16):
        raise ValueError("Invalid output label declaration")
    if (any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.csv", name)
            for name in targets.values()) or len(set(targets.values())) != len(targets)):
        raise ValueError("Output filenames must be unique CSV basenames")
    left_key, right_key = request["input_key"], request["prediction_key"]
    if left_key not in table[0] or right_key not in predictions[0]:
        raise ValueError("Declared CSV row key is absent")
    left, right = table[0].index(left_key), predictions[0].index(right_key)
    keys = [row[left] for row in table[1:]]
    if (any(not key for key in keys) or len(set(keys)) != len(keys)
            or keys != [row[right] for row in predictions[1:]]):
        raise ValueError("Adjudicated predictions do not have identical input keys and order")
    result = {}
    for column, name in targets.items():
        if column == right_key or column not in predictions[0]:
            raise ValueError("Requested prediction column is absent or is the row key")
        index = predictions[0].index(column)
        values = [row[index] for row in predictions[1:]]
        if set(values) - set(labels):
            raise ValueError("Adjudicated predictions contain a value outside the declared label set")
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow([*table[0], label])
        writer.writerows([*row, value] for row, value in zip(table[1:], values, strict=True))
        result[name] = output.getvalue()
    return result


def materialize_supervisor_delivery(workflow: Any, request: dict) -> dict:
    """Publish a deterministic CSV supplement inside the closed source Cycle."""
    from .goal_terminal import science_commit_barrier

    if (not workflow.supervisor_enabled() or not isinstance(request, dict)
            or set(request) != {"operation", "input", "predictions", "input_key", "prediction_key",
                                "label_column", "allowed_labels", "outputs"}
            or request["operation"] != "csv_label_projection"
            or any(not isinstance(request[key], str)
                   for key in ("input", "predictions", "input_key", "prediction_key"))):
        raise ValueError("Invalid or unauthorized Supervisor delivery request")
    with science_commit_barrier(workflow.spec.research_root, "exclusive"):
        assessment, cycle, inputs, predictions = _sources(workflow.spec, workflow._graph_database_path())
        source, prediction = inputs.get(request["input"]), predictions.get(request["predictions"])
        if source is None or prediction is None or request["input"] not in prediction["inputs"]:
            raise ValueError("Delivery sources are outside the closed Outcome's authorized inputs")
        original = _read(Path(source["path"]), Path(workflow.spec.benchmark_root), _CSV_LIMIT)
        predicted = _read(Path(prediction["path"]), cycle / "workspace", _CSV_LIMIT)
        if (_digest(original) != source["sha256"] or _digest(predicted) != prediction["sha256"]):
            raise ValueError("Delivery inputs changed during preparation")
        projected = _project(request, original, predicted)
        identity = {"goal_assessment": assessment["assessment_id"], "request": request,
                    "input_source": source, "prediction_source": prediction}
        ident = _digest(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode())
        folder = cycle / "supervisor_delivery" / ident
        path = folder / "receipt.json"
        _safe(path, cycle)
        assets = [{"path": str(folder / name), "sha256": _digest(text.encode())}
                  for name, text in sorted(projected.items())]
        receipt = {"schema": SCHEMA, "cycle_id": cycle.name, **identity,
                   "assets": assets, "receipt_path": str(path)}
        if path.exists() and json.loads(_read(path, cycle)) != receipt:
            raise ValueError("Delivery receipt has changed")
        for name, text in projected.items():
            target = folder / name
            _safe(target, cycle)
            if target.exists():
                if _read(target, cycle, _CSV_LIMIT) != text.encode():
                    raise ValueError("Existing delivery output differs from the verified projection")
            else:
                atomic(target, text)
        if not path.exists():
            write_json(path, receipt)
        return receipt


def supervisor_delivery_materials(spec: Any, graph: Path, cycle: Path) -> list[dict]:
    """Include only SDK-derived outputs and their still-identical source files."""
    folder = cycle / "supervisor_delivery"
    if not folder.is_dir() or folder.is_symlink():
        return []
    receipts = []
    for path in sorted(folder.glob("*/receipt.json")):
        try:
            receipt = json.loads(_read(path, cycle))
            if (receipt.get("schema") != SCHEMA or receipt.get("cycle_id") != cycle.name
                    or receipt.get("receipt_path") != str(path)):
                continue
            identity = {key: receipt[key] for key in
                        ("goal_assessment", "request", "input_source", "prediction_source")}
            if _digest(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()) != path.parent.name:
                continue
            request = receipt["request"]
            _, _, inputs, predictions = _sources(spec, graph, anchor=cycle,
                                                 assessment_id=receipt["goal_assessment"])
            source, prediction = inputs.get(request["input"]), predictions.get(request["predictions"])
            if (not source or not prediction or (source, prediction) !=
                    (receipt["input_source"], receipt["prediction_source"])
                    or request["input"] not in prediction["inputs"]):
                continue
            original = _read(Path(source["path"]), Path(spec.benchmark_root), _CSV_LIMIT)
            predicted = _read(Path(prediction["path"]), cycle / "workspace", _CSV_LIMIT)
            if (_digest(original) != source["sha256"] or _digest(predicted) != prediction["sha256"]):
                continue
            projected = _project(receipt["request"], original, predicted)
            assets = [{"path": str(path.parent / name), "sha256": _digest(text.encode())}
                      for name, text in sorted(projected.items())]
            if (receipt["assets"] == assets and all(
                    _read(Path(asset["path"]), path.parent, _CSV_LIMIT) == projected[Path(asset["path"]).name].encode()
                    for asset in assets)):
                receipts.append(receipt)
        except (OSError, KeyError, ValueError, TypeError, csv.Error):
            continue
    return receipts
