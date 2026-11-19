"""Receipt-bound, model-free completion of an already delivered invocation.

This narrow recovery starts before Workflow.resume changes the failed state.
It never runs invocation preparation, transport, repair Turns, or a new Goal.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .contracts import role_contract
from .invocation_execution import InvocationSession
from .invocations import InvocationLedger, stable_hash
from .native_goal import NativeGoalResult, read_goal_findings
from .output_validation import validate_agent_output
from .runtime import atomic, now, write_json
from .scientific_objective import frozen_science_digest, locked_science_view


VALIDATOR = "benchmark-provenance-alias/v1"


def _sha(path: Path) -> str:
    if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
        raise ValueError("Unsafe original delivery input: " + str(path))
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _stopped_task(root: Path) -> Iterator[None]:
    """Exclude scheduler allocation and both kinds of execution ownership."""
    from .runtime_admission import admission_lock, assert_no_worker_claims
    from .runtime_manager import _file_lock, launch_lock_path, owner_lock_path, runtime_health

    with ExitStack() as locks:
        directory = root / ".theory-master"
        directory.mkdir(parents=True, exist_ok=True)
        try:
            locks.enter_context(_file_lock(directory / "scheduler.lock", nonblocking=True))
            locks.enter_context(_file_lock(launch_lock_path(root), nonblocking=True))
            locks.enter_context(admission_lock(root))
            locks.enter_context(_file_lock(owner_lock_path(root), nonblocking=True))
        except BlockingIOError as exc:
            raise ValueError("Delivery reprocessing requires a stopped task") from exc
        assert_no_worker_claims(root)
        if runtime_health(root).get("active"):
            raise ValueError("Delivery reprocessing requires an inactive runtime")
        yield


@dataclass(frozen=True)
class _RecoveredManifest:
    """Preserve the failed original before projecting its completed delivery."""

    path: Path
    original: bytes
    archive: Path
    receipt_sha256: str

    def finalize(self, *, status: str) -> Path:
        if status != "completed":
            raise ValueError("Saved delivery can only project a completed inventory")
        self.archive.parent.mkdir(parents=True, exist_ok=True)
        if self.archive.exists():
            if self.archive.is_symlink() or self.archive.read_bytes() != self.original:
                raise ValueError("Original failed artifact inventory archive differs")
        else:
            with self.archive.open("xb") as stream:
                stream.write(self.original)
        inventory = json.loads(self.original)
        write_json(self.path, {
            **inventory, "status": "completed",
            "delivery_reprocessing": {
                "original_failed_manifest": str(self.archive),
                "original_failed_manifest_sha256": hashlib.sha256(self.original).hexdigest(),
                "repair_receipt_sha256": self.receipt_sha256,
            },
        })
        return self.path


def _bound_delivery(owner: Any, state: dict, receipt_path: Path | None) -> tuple:
    from .supervisor_repair_receipts import inspect_repair_receipt
    from .runtime_supervisor_review import resume_blocker

    cycle = owner.cycle_dir
    reference = state.get("supervisor_repair_receipt") or {}
    if receipt_path is not None and str(receipt_path) != reference.get("path"):
        raise ValueError("Recovery receipt does not match the failed Cycle")
    receipt = inspect_repair_receipt(cycle, state)
    if receipt.get("validator") == "native-complete-delivery-alias/v1":
        from .supervisor_delivery_alias_recovery import bound_alias_delivery
        return bound_alias_delivery(owner, state, receipt)
    if receipt.get("validator") != VALIDATOR:
        raise ValueError("Delivery reprocessing requires its explicit SDK validator")
    blocker = resume_blocker(state, runtime_revalidated=True)
    if blocker:
        raise ValueError("Saved delivery recovery guard: " + blocker)
    if (cycle / "recovery_control.json").exists():
        raise ValueError("Pending operator control forbids delivery reprocessing")
    binding = json.loads(receipt["tests"])
    if (
        not isinstance(binding, dict)
        or binding.get("authority") not in {"verified_original_success_return_path", "sdk_return"}
        or binding.get("resume_preparation") != "saved_delivery_semantic_closure"
        or binding.get("role") != "investigator"
        or binding.get("stage") != "investigate"
    ):
        raise ValueError("Receipt does not authorize saved Investigator delivery")
    if (
        binding.get("config_path") != str(owner.spec.config_path)
        or binding.get("benchmark_root") != str(owner.spec.benchmark_root)
        or binding.get("research_root") != str(owner.spec.research_root)
        or state.get("goal_sha256") != owner.spec.goal_sha256
    ):
        raise ValueError("Workflow specification differs from the validated original task")
    invocation = str(binding.get("invocation_id") or "")
    thread = str(binding.get("thread_id") or "")
    if not invocation or Path(invocation).name != invocation or not thread:
        raise ValueError("Invalid original invocation identity")
    ledger = InvocationLedger(cycle / "invocations.jsonl")
    original = ledger.snapshot(invocation)
    failure = state.get("last_invocation_failure") or {}
    if (
        state.get("status") != "failed"
        or state.get("execution_active")
        or state.get("active_invocation_id")
        or state.get("scientific_verdict") is not None
        or state.get("last_invocation_id") != invocation
        or failure.get("invocation_id") != invocation
        or failure.get("failure_phase") != "semantic_closure"
        or (state.get("threads") or {}).get("investigator") != thread
        or (state.get("hypothesis_lock") or {}).get("status") != "locked"
        or not original
        or original.get("state") != "failed"
        or original.get("commit_state") != "not_committed"
        or original.get("failure_phase") != "semantic_closure"
        or original.get("role") != "investigator"
        or original.get("stage") != "investigate"
        or original.get("thread_id") != thread
        or original.get("closure_hash")
        or (cycle / "semantic_closure" / (invocation + ".json")).exists()
    ):
        raise ValueError("Original invocation is not an unclosed semantic failure")
    contract = role_contract("investigator", phase="investigate")
    if not (
        contract.descriptor() == original.get("contract_ref") == binding.get("contract_ref")
        and original.get("logical_key") == binding.get("logical_key")
        and frozen_science_digest(locked_science_view(state["hypothesis_lock"]))
        == binding.get("frozen_objective_sha256")
    ):
        raise ValueError("Original science or output contract changed")
    handoff = cycle / "workspace" / ".tm-handoff" / (invocation + ".json")
    manifest = cycle / "invocation_artifacts" / (invocation + ".json")
    if (
        str(handoff) != binding.get("handoff_path")
        or str(manifest) != binding.get("artifact_manifest_path")
        or str(manifest) != original.get("invocation_artifact_manifest")
        or _sha(handoff) != binding.get("handoff_sha256")
        or _sha(manifest) != binding.get("artifact_manifest_sha256")
    ):
        raise ValueError("Original delivery or artifact manifest changed")
    inventory = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        inventory.get("invocation_id") != invocation
        or inventory.get("role") != "investigator"
        or inventory.get("stage") != "investigate"
        or inventory.get("workspace") != str(owner.cycle_workspace)
        or inventory.get("status") != "failed"
    ):
        raise ValueError("Original artifact inventory is not bound to the invocation")
    for row in [*inventory.get("created", []), *inventory.get("modified", [])]:
        path = Path(row["path"])
        relative = Path(row["relative_path"])
        if (
            relative.is_absolute() or ".." in relative.parts
            or path != owner.cycle_workspace / relative
            or _sha(path) != row.get("sha256")
            or (receipt.get("extra_inputs") or {}).get(str(path)) != row.get("sha256")
        ):
            raise ValueError("Original scientific artifact changed")
    # Completion authority was established by the SDK receipt's original return
    # path proof. This adapter only projects those same authored bytes.
    raw = read_goal_findings(NativeGoalResult("complete", thread, 0, 0), handoff)
    if stable_hash(json.loads(raw)) != binding.get("raw_findings_sha256"):
        raise ValueError("Original Findings projection changed")
    return receipt, binding, original, ledger, contract, raw, manifest


def reprocess_failed_native_delivery(
    owner: Any, *, receipt_path: Path | None = None
) -> dict[str, Any]:
    """Commit one receipt-verified original delivery, without any model call.

    ``owner`` is a Workflow already bound to the failed Cycle. Call this before
    normal resume, after recording the SDK repair receipt against stopped state.
    The Cycle moves to a paused operational boundary; normal resume consumes its canonical closure and
    performs the subsequent independent adjudication.
    """
    cycle = owner.cycle_dir
    if cycle is None or not cycle.is_dir() or cycle.parent != owner.spec.research_root.resolve():
        raise ValueError("Bind the original Cycle before delivery reprocessing")
    with _stopped_task(owner.spec.research_root.resolve()):
        state_path = cycle / "research_state.json"
        _sha(state_path)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        receipt, binding, original, ledger, contract, raw, manifest = _bound_delivery(
            owner, state, receipt_path
        )
        context = {
            "hypothesis_lock": state["hypothesis_lock"],
            "investigation_attempt": state.get("investigation_attempt"),
            "round": int(state.get("investigation_round_count") or 0) + 1,
        }
        logical_key = owner._invocation_logical_key(
            state, "investigator", "investigator", "investigate", context, None
        )
        if logical_key != binding["logical_key"]:
            raise ValueError("Saved delivery no longer belongs to the pending workflow slot")
        session = InvocationSession(
            owner, state, "investigator", context, None, False, None, None,
            "investigator", "investigate", contract, ledger, logical_key,
        )
        session.invocation_id = binding["invocation_id"]
        session.identifier = binding["thread_id"]
        session.thread_key = owner._thread_key(state, "investigator")
        session.raw = raw
        session.validation_context = {
            "goal": owner.spec.goal, "research_contract": owner.spec.contract,
            "final_gate": owner.spec.final_gate, "required_context": owner.spec.required_context,
            **owner._path_anchors(), **context, **owner._evidence_context(),
        }
        # Deliberately bypass _validate_and_repair: its fallback may spend a Turn.
        session.output = session.output_validation.normalize_with_audit(raw, "saved_native_delivery")
        validation = validate_agent_output(
            "investigator", "investigate", session.output, state,
            session.validation_context, contract,
        )
        if not validation.valid:
            raise ValueError("Saved Investigator delivery fails current contract: " + json.dumps(validation.as_dict()))
        original_manifest = manifest.read_bytes()
        archive = cycle / "delivery_reprocessing" / binding["invocation_id"] / "original_invocation_artifacts.json"
        if any(part.is_symlink() for part in (archive, *archive.parents)):
            raise ValueError("Unsafe original failed manifest archive")
        session.artifact_tracker = _RecoveredManifest(
            manifest, original_manifest, archive, state["supervisor_repair_receipt"]["sha256"]
        )
        audit = {
            "schema": "theory-master/native-delivery-reprocessing/v1",
            "invocation_id": binding["invocation_id"], "thread_id": binding["thread_id"],
            "receipt_path": state["supervisor_repair_receipt"]["path"],
            "receipt_sha256": state["supervisor_repair_receipt"]["sha256"],
            "handoff_path": binding["handoff_path"], "handoff_sha256": binding["handoff_sha256"],
            "original_failed_manifest": str(archive),
            "original_failed_manifest_sha256": binding["artifact_manifest_sha256"],
            "original_failed_invocation": original, "extra_model_requests": 0, "at": now(),
        }
        if receipt.get("validator") == "native-complete-delivery-alias/v1":
            audit["delivery_alias"] = {
                key: binding[key] for key in ("authority", "declared_handoff_path", "handoff_path",
                    "polluted_invocation_id", "authored_write_event_sha256", "goal_identity")
            }
        try:
            session.commit._close_output()
            output = session.commit._commit_output()
        except BaseException as exc:
            audit.update(status="failed", error_type=type(exc).__name__, error=str(exc))
            current = ledger.snapshot(binding["invocation_id"]) or {}
            if current.get("state") not in {"completed", "committed"}:
                atomic(manifest, original_manifest.decode("utf-8"))
            raise
        else:
            audit.update(status="committed", closure_hash=session.closure_hash,
                         closure_artifact=str(session.closure_artifact))
            audit["original_failure"] = state.get("recovery")
            state.update({
                "status": "paused", "phase": "engineering_delivery_reprocessed", "resumable": True,
                "recovery": {"action": "engineering_delivery_reprocessed", "kind": "saved_native_delivery",
                             "invocation_id": binding["invocation_id"], "at": now()},
            })
        finally:
            state["active_invocation_id"] = None
            state["active_role"] = None
            state["current_agent"] = None
            state["execution_active"] = False
            state["supervisor_delivery_reprocessing"] = audit
            write_json(cycle / "delivery_reprocessing" / (binding["invocation_id"] + ".json"), audit)
            owner._persist(state)
        return {"invocation_id": binding["invocation_id"], "output": output,
                "closure_artifact": str(session.closure_artifact),
                "closure_hash": session.closure_hash, "extra_model_requests": 0}
