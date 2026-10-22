"""Worker-owned automatic preservation after an SDK-authenticated 3+3 stop.

The scheduler may request preservation once, but only the existing validated
preserve_release path may call the archiver or free a scientific Cycle claim.
All recorded reminders are durable and shown in Important Events.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .observability.incident_registry import register_cycle_incident
from .runtime import now, write_json

_CODES = frozenset({
    "investigator_budget_review_rejected",
    "investigator_budget_supervisor_declined",
    "investigator_budget_cap_reached",
})


def budget_preservation_notice(root: Path, worker_id: str, cycle: str, *,
                               success: bool, detail: str = "",
                               hypothesis_id: str = "") -> dict[str, Any]:
    """Per-Cycle reminder, including failed preserves; no secrets or raw paths."""
    root = Path(root).resolve()
    folder = root / cycle
    if not cycle.isascii() or not cycle.isdigit() or not folder.is_dir():
        raise ValueError("invalid preservation Cycle")
    # The registry is idempotent by Cycle/code and remains readable after
    # the Worker starts a different Cycle.
    return register_cycle_incident(
        folder, severity="important",
        kind="budget_preservation",
        source_event="sdk.budget_preservation_" + ("completed" if success else "failed"),
        code="budget_preserved" if success else "budget_preservation_failed",
        discriminator=str(worker_id),
        summary=(f"{worker_id} · Cycle {cycle} "
                 + ("科研成果已保全，前沿已释放；请查看阶段成果与原因"
                    if success else "科研成果保全失败，原检查点与前沿归属已保留；请处理")
                 + (f"；{detail[:250]}" if detail else "")),
        fields={"worker_id": worker_id, "hypothesis_id": str(hypothesis_id)[:100],
                "persistent_reminder": True,
                "outcome": "preserved_released" if success else "failed_claim_retained"},
    )[0]


def _verified_legacy_budget_rejection(folder: Path, scientific: dict[str, Any]) -> bool:
    """Accept only an actual SDK Reviewer denial in an old frozen Cycle.

    Legacy work predates 3+3 auto-preserve markers; do not turn technical
    failures or ordinary need-attention into an authorization to archive.
    """
    from .investigator_budget import budget_status
    if (scientific.get("investigator_budget_policy") == "three_plus_three_v1"
            or scientific.get("phase") != "investigator:budget_denied"
            or (scientific.get("attention") or {}).get("code") !=
            "investigator_budget_review_rejected"):
        return False
    try:
        result = budget_status(folder, folder.parent, scientific)
    except (ValueError, OSError, TypeError):
        return False
    return result.get("policy_version") == "legacy" and result.get("rejected") is True


def _cached_preservation_replay_ready(folder: Path, worker_folder: Path,
                                      cycle: str) -> bool:
    """Permit ONE automatic replay only of validated previously paid output.

    Missing/ambiguous evidence never authorizes another model turn. The
    preserved response stays immutable; structural and source checks are
    performed before the one-shot process is launched.
    """
    from .contract_stages import _preserve_research_structure
    from .research_preservation import PreservationGuard, verified_sources, blocked_checkpoint

    progress = worker_folder / "preservation_result.json"
    cached = folder / "preservation" / "last_model_output.txt"
    closure = folder / "preservation" / "closure.json"
    if (not progress.is_file() or progress.is_symlink()
            or not cached.is_file() or cached.is_symlink()
            or closure.exists() or closure.is_symlink()):
        return False
    try:
        previous = json.loads(progress.read_text(encoding="utf-8"))
        if (previous.get("cycle") != cycle
                or previous.get("status") != "failed_preservation_retained_claim"):
            return False
        blocked_checkpoint(folder)  # no new budget approval or science turn
        output = json.loads(cached.read_text(encoding="utf-8"))
        if not isinstance(output, dict):
            return False
        _preserve_research_structure(output, {})
        verified_sources(folder, output)
        return True
    except (OSError, ValueError, TypeError, KeyError, PreservationGuard):
        return False


def _launch_cached_preservation_once(spec: Any, *, root: Path,
                                     worker_id: str, cycle: str,
                                     original_receipt: dict[str, Any]) -> bool:
    """Exactly one fenced retry of a paid, now verifiable archived response."""
    from .worker_runtime import worker_action, worker_dir
    folder = root / cycle
    work = worker_dir(root, worker_id)
    if (original_receipt.get("schema") !=
            "theory-master/automatic-budget-preservation/v1"
            or original_receipt.get("cycle") != cycle
            or original_receipt.get("worker_id") != worker_id
            or original_receipt.get("status") not in {"launched", "requested_once"}):
        return False
    if not _cached_preservation_replay_ready(folder, work, cycle):
        return False
    # Arm a persistent, content-bound NO-MODEL guard before starting a
    # separate process. If the cached file disappears between verification
    # and dispatch, the preserve stage must fail closed instead of calling
    # Investigator again.
    cached_path = folder / "preservation" / "last_model_output.txt"
    replay_guard = folder / "preservation" / "cached_replay_required.json"
    try:
        expected = hashlib.sha256(cached_path.read_bytes()).hexdigest()
        if replay_guard.exists() or replay_guard.is_symlink():
            existing = json.loads(replay_guard.read_text(encoding="utf-8"))
            if (replay_guard.is_symlink()
                    or existing.get("cycle") != cycle
                    or existing.get("sha256") != expected):
                return False
        else:
            write_json(replay_guard, {
                "schema": "theory-master/preservation-cached-replay/v1",
                "cycle": cycle, "sha256": expected, "at": now(),
            })
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    marker = work / f"budget_cached_replay_{cycle}.json"
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"schema": "theory-master/budget-cached-preservation-replay/v1",
                   "worker_id": worker_id, "cycle": cycle,
                   "status": "requested_once", "at": now()}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        result = worker_action(spec, worker_id, "preserve_release")
        write_json(marker, {
            "schema": "theory-master/budget-cached-preservation-replay/v1",
            "worker_id": worker_id, "cycle": cycle, "status": "launched",
            "result_status": result.get("status"), "at": now(),
        })
    except (ValueError, RuntimeError, OSError) as exc:
        write_json(marker, {
            "schema": "theory-master/budget-cached-preservation-replay/v1",
            "worker_id": worker_id, "cycle": cycle, "status": "blocked",
            "error_type": type(exc).__name__, "at": now(),
        })
        budget_preservation_notice(root, worker_id, cycle, success=False,
                                   detail="已缓存科研保全自动重放失败，需人工审查")
    return True


def dispatch_auto_budget_preservation(spec: Any) -> bool:
    """One-shot scheduler handoff; never resume science or guess a missing guard."""
    from .worker_runtime import (
        list_workers, read_worker, worker_dir, worker_action,
    )
    root = Path(spec.research_root).resolve()
    for item in list_workers(root):
        if item.get("status") != "NEEDS_ATTENTION" or item.get("process_alive"):
            continue
        wid = str(item.get("worker_id") or "")
        cycle = str(item.get("current_cycle") or "")
        if not cycle.isascii() or not cycle.isdigit():
            continue
        folder = root / cycle
        state_file = folder / "research_state.json"
        if state_file.is_symlink() or not state_file.is_file():
            continue
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        marker = state.get("budget_auto_preserve")
        code = str((state.get("attention") or {}).get("code") or "")
        authorized_3plus3 = (
            isinstance(marker, dict)
            and marker.get("policy") == "three_plus_three_v1"
            and state.get("investigator_budget_policy") == "three_plus_three_v1"
            and marker.get("source") == "sdk_authorized_budget_terminal"
            and marker.get("cycle") == cycle and marker.get("code") == code
        )
        authorized_legacy = (
            not isinstance(marker, dict)
            and _verified_legacy_budget_rejection(folder, state)
        )
        if not ((authorized_3plus3 or authorized_legacy)
                and code in _CODES and state.get("status") == "needs_attention"
                and state.get("next_executor") != "judge"
                and state.get("execution_active") is False
                and not state.get("scientific_verdict")):
            continue
        receipt = worker_dir(root, wid) / f"budget_auto_preserve_{cycle}.json"
        if receipt.exists():
            # A failed preservation may have a fully cached, hash-verifiable
            # single paid Stage output. Restarting the original guarded path
            # reuses that immutable output with no fresh model request.
            # Unknown/interrupted paid attempts remain blocked.
            recorded = {}
            if receipt.is_file() and not receipt.is_symlink():
                try:
                    recorded = json.loads(receipt.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            if isinstance(recorded, dict) and _launch_cached_preservation_once(
                    spec, root=root, worker_id=wid, cycle=cycle,
                    original_receipt=recorded):
                return True
            try:
                created = datetime.fromisoformat(
                    str(recorded.get("at") or "").replace("Z", "+00:00")
                ).astimezone(timezone.utc)
                if (recorded.get("status") == "requested_once"
                        and (datetime.now(timezone.utc) - created).total_seconds() > 300):
                    budget_preservation_notice(root, wid, cycle, success=False,
                                               detail="Preserve 启动状态未知，需核对后人工恢复")
            except (OSError, ValueError, AttributeError):
                pass
            continue
        try:
            from .research_preservation import blocked_checkpoint
            blocked_checkpoint(folder)  # validate before irreversible one-shot claim
            try:
                descriptor = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue  # another scheduler won the same Cycle
            # Exclusive durable claim before starting a paid preserve model.
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({
                    "schema": "theory-master/automatic-budget-preservation/v1",
                    "cycle": cycle, "worker_id": wid, "source_attention_code": code,
                    "at": now(), "status": "requested_once",
                }, handle)
                handle.flush()
                os.fsync(handle.fileno())
            result = worker_action(spec, wid, "preserve_release")
            write_json(receipt, {
                "schema": "theory-master/automatic-budget-preservation/v1",
                "cycle": cycle, "worker_id": wid, "source_attention_code": code,
                "at": now(), "status": "launched", "result_status": result.get("status"),
            })
        except (ValueError, RuntimeError, OSError) as exc:
            try:
                write_json(receipt, {
                    "schema": "theory-master/automatic-budget-preservation/v1",
                    "cycle": cycle, "worker_id": wid, "source_attention_code": code,
                    "at": now(), "status": "failed_closed", "error_type": type(exc).__name__,
                })
            except (OSError, ValueError):
                pass  # notice below still attempted when receipt write fails
            try:
                budget_preservation_notice(root, wid, cycle, success=False,
                                           detail=type(exc).__name__)
            except (OSError, ValueError):
                pass  # receipt remains durable even if the notice writer fails
        return True
    return False
