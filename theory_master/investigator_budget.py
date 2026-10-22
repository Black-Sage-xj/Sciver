"""One Investigator token budget, evidence-backed reviews, cumulative tranche grants.

Budget decisions are operational resource choices, never scientific Judge verdicts.
Only this module's locked apply_review() writes the ledger. The SDK-internal
Reviewer supplies a proposal; the standalone CLI remains a manual recovery path.
"""
from __future__ import annotations
import hashlib

import argparse
import fcntl
import json
import os
from pathlib import Path
from typing import Any

from .runtime import atomic, now

BUDGET_ATTENTION_CODE = "investigator_budget_review_required"
DEFAULT_TRANCHE_TOKENS = 50_000_000
TRANCHE_ENV = "THEORY_MASTER_INVESTIGATOR_BUDGET_TOKENS"
LEDGER_REL = Path("budget") / "investigator_reviews.jsonl"
BUDGET_POLICY_3_PLUS_3 = "three_plus_three_v1"
REVIEWER_TRANCHES = 3
MAX_TRANCHES = 6
SUPERVISOR_DECISION_CODE = "investigator_budget_supervisor_review_required"
BUDGET_CAP_CODE = "investigator_budget_cap_reached"
SUPERVISOR_DECLINED_CODE = "investigator_budget_supervisor_declined"

LEGACY_BRIDGE_REL = Path("budget") / "three_plus_three_legacy_review_bridge.json"


class BudgetLedgerError(ValueError):
    """Budget data/policy validation failed; not an Agent output violation."""


def _review_record_sha256(record: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _bridged_first_review(cycle_dir: Path, record: dict[str, Any]) -> bool:
    """Allow only an audited, original first approval written by a stale run_state.

    The append-only ledger row is never edited. A one-time external receipt
    binds this exact original row to the real Reviewer decision.
    """
    marker_path = cycle_dir / LEGACY_BRIDGE_REL
    receipt_path = cycle_dir / "budget" / "internal_review_0001.json"
    if (marker_path.is_symlink() or receipt_path.is_symlink()
            or not marker_path.is_file() or not receipt_path.is_file()):
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return False
    return bool(
        isinstance(marker, dict) and isinstance(receipt, dict)
        and marker.get("schema") == "theory-master/three-plus-three-ledger-bridge/v1"
        and marker.get("source") == "operator_audited_run_state_policy_mismatch"
        and marker.get("original_row_sha256") == _review_record_sha256(record)
        and marker.get("reviewer_receipt_sha256") == _review_record_sha256(receipt)
        and record.get("policy_version") is None
        and record.get("review_index") == 1
        and record.get("decision") == "approve"
        and record.get("allowance_basis") == "prior_allowance"
        and int(record.get("prior_allowance") or -1) == int(record.get("tranche_tokens") or -2)
        and int(record.get("new_allowance") or -1) == 2 * int(record.get("tranche_tokens") or 0)
        and receipt.get("decision") == "approve"
        and receipt.get("review_index") == 1
        and receipt.get("reason") == record.get("reason")
        and receipt.get("new_allowance") == record.get("new_allowance")
    )


def bridge_verified_first_review(cycle_dir: Path) -> dict[str, Any]:
    """One-time no-grant operator reconciliation of an already-approved review.

    Never issues a model decision, increments allowance, or modifies ledger.
    """
    cycle_dir = Path(cycle_dir).resolve()
    ledger = cycle_dir / LEDGER_REL
    marker_path = cycle_dir / LEGACY_BRIDGE_REL
    with (ledger.parent / "investigator_review.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if marker_path.exists() or marker_path.is_symlink():
            raise ValueError("budget policy bridge already exists")
        rows = _entries(cycle_dir)
        if len(rows) != 1 or rows[0].get("policy_version") is not None:
            raise ValueError("bridge requires exactly one original legacy review")
        scientific = json.loads((cycle_dir / "research_state.json").read_text(encoding="utf-8"))
        run = json.loads((cycle_dir / "run_state.json").read_text(encoding="utf-8"))
        if (not three_plus_three_enabled(scientific)
                or three_plus_three_enabled(run)
                or scientific.get("status") not in {"failed", "needs_attention", "interrupted"}
                or scientific.get("resumable") is not True
                or not scientific.get("last_safe_checkpoint")):
            raise ValueError("authoritative 3+3 scientific stopped checkpoint is missing")
        receipt_path = cycle_dir / "budget" / "internal_review_0001.json"
        if receipt_path.is_symlink() or not receipt_path.is_file():
            raise ValueError("signed Reviewer receipt is missing")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        row = rows[0]
        if (row.get("review_index") != 1 or row.get("decision") != "approve"
                or receipt.get("review_index") != 1 or receipt.get("decision") != "approve"
                or receipt.get("reason") != row.get("reason")
                or receipt.get("new_allowance") != row.get("new_allowance")
                or row.get("allowance_basis") != "prior_allowance"
                or int(row.get("prior_allowance") or -1) != int(row.get("tranche_tokens") or -2)
                or int(row.get("new_allowance") or -1) != 2 * int(row.get("tranche_tokens") or 0)):
            raise ValueError("Reviewer approval and ledger do not match")
        marker = {
            "schema": "theory-master/three-plus-three-ledger-bridge/v1",
            "source": "operator_audited_run_state_policy_mismatch",
            "at": now(), "cycle_id": cycle_dir.name,
            "original_row_sha256": _review_record_sha256(row),
            "reviewer_receipt_sha256": _review_record_sha256(receipt),
            "token_grant": False,
        }
        atomic(marker_path, json.dumps(marker, ensure_ascii=False, sort_keys=True) + "\n")
        if not _bridged_first_review(cycle_dir, row):
            raise RuntimeError("budget bridge verification failed; no further review is permitted")
        return marker




def three_plus_three_enabled(state: dict[str, Any]) -> bool:
    return state.get("investigator_budget_policy") == BUDGET_POLICY_3_PLUS_3




def format_k_tokens(value: int) -> str:
    """Human-readable token amount; machine ledgers always store integers."""
    return f"{int(value) / 1_000:,.1f}k"


def _config_tokens(research_root: Path) -> int:
    raw = os.environ.get(TRANCHE_ENV, str(DEFAULT_TRANCHE_TOKENS))
    # Current task layout: TaskHome/.theory-master/research/<Cycle>.
    # Historical layouts also accept TaskHome/.theory-master/env.
    env_path = (research_root.parent / "env"
                if research_root.name == "research" and research_root.parent.name == ".theory-master"
                else research_root / ".theory-master" / "env")
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == TRANCHE_ENV:
                raw = value.strip().strip("'\"")
    try:
        amount = int(raw)
    except ValueError as exc:
        raise ValueError(f"{TRANCHE_ENV} must be a positive integer") from exc
    if amount < 1:
        raise ValueError(f"{TRANCHE_ENV} must be greater than zero")
    return amount


def _entries(cycle_dir: Path) -> list[dict[str, Any]]:
    path = cycle_dir / LEDGER_REL
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(not isinstance(row, dict) or row.get("schema") != "theory-master/investigator-budget-review/v1" for row in rows):
        raise ValueError("invalid Investigator budget review ledger")
    return rows


def _tokens(state: dict[str, Any], cycle_dir: Path) -> int:
    """Read the most recent durable Investigator token total; not Cycle-wide usage."""
    available = [state]
    state_path = cycle_dir / "run_state.json"
    if state_path.is_file():
        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(persisted, dict):
            available.append(persisted)
    # The workflow's durable research_state may include the latest accounting
    # while the standalone run_state projection is lagging (or under test).
    # Never lower observed consumption when applying an internal review.
    scientific_path = cycle_dir / "research_state.json"
    if scientific_path.is_file():
        scientific = json.loads(scientific_path.read_text(encoding="utf-8"))
        if isinstance(scientific, dict):
            available.append(scientific)
    amounts = []
    for item in available:
        investigator = (item.get("agents") or {}).get("investigator") or {}
        if isinstance(investigator, dict):
            amounts.extend(int(investigator.get(key) or 0)
                           for key in ("tokens_total", "tokens_total_live"))
            raw = investigator.get("raw_usage")
            # Native Codex thread/tokenUsage.total counts cached input and is
            # cumulative over the entire native thread, including an unfinished
            # Goal. NEVER add it to completed-turn usage (same tokens).
            if isinstance(raw, dict):
                amounts.append(int(raw.get("input_tokens") or 0) + int(raw.get("output_tokens") or 0))
        snapshot = item.get("investigator_budget")
        if isinstance(snapshot, dict):
            amounts.append(int(snapshot.get("observed_tokens") or 0))
    return max(amounts, default=0)


def budget_status(cycle_dir: Path, research_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    try:
        return _budget_status(cycle_dir, research_root, state)
    except ValueError as exc:
        raise BudgetLedgerError(str(exc)) from exc


def _budget_status(cycle_dir: Path, research_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    cycle_dir = Path(cycle_dir)
    rows = _entries(cycle_dir)
    # A recorded tranche is immutable under the v1 ledger contract. Existing
    # reviewed Cycles retain their frozen tranche; new Cycles use the current
    # configured (now 50,000k) tranche without rewriting historical grants.
    initial = int(rows[0]["tranche_tokens"]) if rows else _config_tokens(Path(research_root))
    tokens = _tokens(state, cycle_dir)
    allowance = initial
    last_reviewed = 0
    review_index = 1
    reviewed_ids: set[str] = set()
    reviewed_progress_refs: set[str] = set()
    rejected = False
    supervisor_authorized = False
    supervisor_declined = False
    policy = three_plus_three_enabled(state)
    preceding_rejection: dict[str, Any] | None = None
    for entry_index, row in enumerate(rows):
        if (policy and row.get("policy_version") != BUDGET_POLICY_3_PLUS_3
                and not (entry_index == 0 and _bridged_first_review(cycle_dir, row))):
            raise ValueError("3+3 Cycle contains a legacy budget ledger entry")
        if row.get("decision") in {"supervisor_authorize", "supervisor_decline", "supervisor_grant"}:
            if not policy or rejected or int(row.get("tranche_tokens") or -1) != initial:
                raise ValueError("invalid Supervisor budget ledger entry")
            if int(row.get("prior_allowance") or -1) != allowance or int(row.get("review_index") or -1) != review_index:
                raise ValueError("nonmonotone Supervisor budget record")
            decision = row["decision"]
            if decision in {"supervisor_authorize", "supervisor_decline"}:
                if allowance != REVIEWER_TRANCHES * initial or supervisor_authorized or supervisor_declined or int(row.get("new_allowance") or -1) != allowance:
                    raise ValueError("Supervisor can decide only once at the third tranche")
                if row.get("authority") != "supervisor" or len(str(row.get("reason") or "").strip()) < 30:
                    raise ValueError("Supervisor decision is missing authoritative rationale")
                if decision == "supervisor_authorize":
                    supervisor_authorized = True
                else:
                    supervisor_declined = True
                    rejected = True
            else:
                if (not supervisor_authorized or allowance < REVIEWER_TRANCHES * initial
                        or allowance >= MAX_TRANCHES * initial
                        or int(row.get("new_allowance") or -1) != allowance + initial
                        or row.get("grant_number") != allowance // initial + 1
                        or row.get("authority") not in {"sdk", "supervisor"}
                        or (row.get("authority") == "supervisor" and allowance != REVIEWER_TRANCHES * initial)
                        or (row.get("authority") == "sdk" and allowance == REVIEWER_TRANCHES * initial)):
                    raise ValueError("invalid or excessive Supervisor tranche grant")
                allowance += initial
                review_index += 1
            continue
        if rejected:
            if row.get("decision") == "invalidate":
                # One explicitly operator-authorized protocol correction.
                # The old denial stays in this append-only ledger; no tokens
                # are granted by a correction. A subsequent model decision
                # MUST again pass all normal evidence/resource checks.
                if preceding_rejection is None:
                    raise ValueError("budget correction lacks a rejected review")
                expected_hash = hashlib.sha256(
                    json.dumps(preceding_rejection, ensure_ascii=False,
                               sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                if (
                    row.get("supersedes_sha256") != expected_hash
                    or row.get("source") != "explicit_user_requested_re_review_after_protocol_error"
                    or int(row.get("review_index") or 0) != review_index
                    or int(row.get("prior_allowance") or -1) != allowance
                    or int(row.get("new_allowance") or -1) != allowance
                    or int(row.get("tranche_tokens") or -1) != initial
                ):
                    raise ValueError("invalid operator-authorized budget correction")
                # A protocol-invalidated rejection did not genuinely assess
                # these identifiers. Restore them as eligible for one fresh
                # independent scientific resource decision, preserving history.
                reviewed_ids.difference_update(
                    str(x) for x in preceding_rejection.get("evidence_ids_since_prior_review") or []
                )
                reviewed_progress_refs.difference_update(
                    str(x) for x in preceding_rejection.get("progress_refs_since_prior_review") or []
                )
                rejected = False
                preceding_rejection = None
                continue
            raise ValueError("budget ledger contains an entry after a terminal rejection")
        if row["tranche_tokens"] != initial:
            raise ValueError("Investigator tranche changed after a recorded review; explicit migration required")
        if int(row["prior_allowance"]) != allowance or int(row["review_index"]) != review_index:
            raise ValueError("nonmonotone or inconsistent Investigator budget review ledger")
        if row.get("decision") not in {"approve", "reject"}:
            raise ValueError("invalid Investigator review decision")
        last_reviewed = max(last_reviewed, int(row["observed_tokens"]))
        reviewed_ids.update(str(x) for x in row.get("evidence_ids_since_prior_review") or [])
        reviewed_progress_refs.update(str(x) for x in row.get("progress_refs_since_prior_review") or [])
        if row["decision"] == "approve":
            if policy and (supervisor_authorized or allowance >= REVIEWER_TRANCHES * initial):
                raise ValueError("Budget Reviewer cannot grant a fourth tranche")
            # v1 approvals already committed under the historical overshoot
            # rule remain immutable and valid. New v2 approvals add a fixed
            # tranche to the PRIOR AUTHORIZED allowance, never to consumption.
            basis = row.get("allowance_basis")
            if basis not in (None, "prior_allowance"):
                raise ValueError("unrecognized Investigator allowance basis")
            expected = ((allowance if basis == "prior_allowance" else
                         max(allowance, int(row["observed_tokens"]))) + initial)
            if int(row["new_allowance"]) != expected:
                raise ValueError("invalid Investigator allowance increment")
            allowance = expected
            review_index += 1
        else:
            if int(row["new_allowance"]) != allowance:
                raise ValueError("rejected Investigator budget review changed allowance")
            rejected = True
            preceding_rejection = row
    return {
        "policy_version": BUDGET_POLICY_3_PLUS_3 if policy else "legacy",
        "granted_tranches": allowance // initial,
        "supervisor_authorized": supervisor_authorized,
        "supervisor_declined": supervisor_declined,
        "hard_cap_reached": policy and allowance >= MAX_TRANCHES * initial and tokens >= allowance,
        "tranche_tokens": initial, "allowance_tokens": allowance,
        "observed_tokens": tokens, "remaining_tokens": max(0, allowance - tokens),
        "exhausted": tokens >= allowance or rejected, "rejected": rejected,
        "review_index": review_index, "reviews": len(rows),
        "reviewed_evidence_ids": sorted(reviewed_ids),
        "reviewed_progress_refs": sorted(reviewed_progress_refs),
    }


def _evidence_ids(cycle_dir: Path) -> set[str]:
    registry = cycle_dir / "evidence" / "registry.jsonl"
    if not registry.is_file():
        return set()
    return {
        str(row["evidence_id"])
        for line in registry.read_text(encoding="utf-8").splitlines() if line.strip()
        for row in [json.loads(line)] if isinstance(row, dict) and row.get("evidence_id")
    }


def _finding_progress_refs(state: dict[str, Any]) -> set[str]:
    """Only persisted, nonempty Investigator Findings are budget-review anchors.

    This is not scientific endorsement: the independent reviewer must inspect
    the findings for genuine progress and serious process failures.
    """
    refs: set[str] = set()
    for attempt in (state.get("investigation_attempts") or {}).values():
        if not isinstance(attempt, dict):
            continue
        for item in attempt.get("outputs") or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("executor_role") or item.get("role") or "")
            result = item.get("output") or {}
            if role != "investigator" or not isinstance(result, dict):
                continue
            if not (result.get("findings") or result.get("evidence")):
                continue
            invocation = str(item.get("invocation_id") or "").strip()
            if invocation:
                refs.add(invocation)
    return refs


def workspace_review_progress(cycle_dir: Path) -> list[dict[str, str]]:
    """Backward-compatible hashed provisional reports across the ENTIRE workspace.

    Report identity is a SHA of original bytes, never an SDK scientific verdict.
    The independent Reviewer now browses the full workspace and original dialogue;
    these optional refs only support old ledgers and independently verifiable grants.
    """
    root = Path(cycle_dir) / "workspace"
    if not root.is_dir():
        return []
    rows: list[dict[str, str]] = []
    for file in root.rglob("*.md"):
        if file.is_symlink() or not file.is_file() or file.stat().st_size > 150_000:
            continue
        content = file.read_bytes()
        if not content.strip():
            continue
        digest = hashlib.sha256(content).hexdigest()
        relative = file.relative_to(root).as_posix()
        rows.append({
            "invocation_id": f"workspace-sha256:{relative}:{digest}",
            "artifact": relative, "sha256": digest,
            "summary": " ".join(content.decode("utf-8", errors="replace").split())[:1000],
            "modified_ns": str(file.stat().st_mtime_ns),
            "kind": "unfinished_workspace_scientific_report_not_finding",
        })
    return sorted(rows, key=lambda row: (int(row["modified_ns"]), row["artifact"]), reverse=True)


def _verified_workspace_refs(cycle_dir: Path) -> set[str]:
    return {item["invocation_id"] for item in workspace_review_progress(cycle_dir)}


def invalidate_protocol_flawed_review(cycle_dir: Path, *, reason: str,
                                      evidence: str) -> dict[str, Any]:
    """Explicit, locked administrative re-review of a FACTUALLY invalid rejection.

    No allowance is granted.  Only after a fresh independent Reviewer approves
    may this same Cycle resume spending; old rejection remains auditable.
    Never use this for a scientifically valid negative approval decision.
    """
    cycle_dir = Path(cycle_dir).resolve()
    if len(reason.strip()) < 60 or len(evidence.strip()) < 35:
        raise ValueError("re-review needs concrete protocol-error reason/evidence")
    ledger = cycle_dir / LEDGER_REL
    if not ledger.is_file():
        raise ValueError("no budget review ledger")
    with (ledger.parent / "investigator_review.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            rows = _entries(cycle_dir)
            if not rows or rows[-1].get("decision") != "reject":
                raise ValueError("only an immediately previous rejection is eligible")
            state = json.loads((cycle_dir / "run_state.json").read_text(encoding="utf-8"))
            if (state.get("status") or "").lower() != "needs_attention":
                raise ValueError("only a stopped NEEDS_ATTENTION Cycle may re-review")
            if str((state.get("attention") or {}).get("code")) != BUDGET_ATTENTION_CODE:
                raise ValueError("Cycle is not stopped at a budget review boundary")
            previous = rows[-1]
            # Narrowly allowed to correct known false premise: a reviewer
            # stated that official Evidence is REQUIRED despite eligible SHA
            # progress, or incorrectly called a resolved old network incident
            # the latest failed execution.
            previous_reason = str(previous.get("reason") or "").lower()
            if not ("evidence id" in previous_reason and "cannot rely" in previous_reason
                    and "network" in previous_reason and "unreachable" in previous_reason):
                raise ValueError("no grounded reviewer-protocol contradiction")
            # The mistaken rejection may have marked all SHA refs as
            # reviewed; inspect the current files directly before restoring
            # the erroneously consumed eligibility.
            claimed = set(str(x) for x in previous.get("progress_refs_since_prior_review") or [])
            if not claimed.intersection(_verified_workspace_refs(cycle_dir)):
                raise ValueError("no eligible SHA-verified progress exists")
            prior_status = budget_status(cycle_dir, cycle_dir.parent, state)
            if not prior_status["rejected"]:
                raise ValueError("review is not rejected")
            hash_value = hashlib.sha256(json.dumps(
                previous, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest()
            record = {
                "schema": "theory-master/investigator-budget-review/v1",
                "at": now(), "decision": "invalidate",
                "source": "explicit_user_requested_re_review_after_protocol_error",
                "reason": reason.strip(), "evidence": evidence.strip(),
                "review_index": prior_status["review_index"],
                "tranche_tokens": prior_status["tranche_tokens"],
                "prior_allowance": prior_status["allowance_tokens"],
                "new_allowance": prior_status["allowance_tokens"],
                "observed_tokens": prior_status["observed_tokens"],
                "supersedes_sha256": hash_value,
                "budget_authorized": False,
            }
            existing = ledger.read_text(encoding="utf-8")
            atomic(ledger, existing + json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            verified = budget_status(cycle_dir, cycle_dir.parent, state)
            if verified["rejected"] or verified["allowance_tokens"] != prior_status["allowance_tokens"]:
                raise RuntimeError("protocol correction failed invariants")
            return record
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def apply_review(cycle_dir: Path, *, decision: str, reason: str,
                 next_test: str, evidence_ids: list[str],
                 progress_refs: list[str] | None = None) -> dict[str, Any]:
    """Approve one exactly-sized future tranche, or persist a terminal budget rejection.

    Requires the SDK-persisted budget boundary. Idempotency is enforced by the
    persisted ledger and captured allowance; no model text can grant tokens.
    """
    cycle_dir = Path(cycle_dir).resolve()
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    if len(reason.strip()) < 30 or len(next_test.strip()) < 20:
        raise ValueError("review requires specific scientific reasoning and a bounded next test")
    ledger = cycle_dir / LEDGER_REL
    ledger.parent.mkdir(parents=True, exist_ok=True)
    # Lock the check+append transaction: SDK resume and manual CLI cannot
    # independently approve the identical budget boundary.
    with (ledger.parent / "investigator_review.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return _apply_review_locked(cycle_dir, decision=decision, reason=reason,
                                        next_test=next_test, evidence_ids=evidence_ids,
                                        progress_refs=progress_refs or [])
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _validate_supervisor_budget_boundary(cycle_dir: Path) -> dict[str, Any]:
    run = json.loads((cycle_dir / "run_state.json").read_text(encoding="utf-8"))
    scientific = _validate_budget_review_state(
        cycle_dir, run, expected_code=SUPERVISOR_DECISION_CODE,
    )
    status = budget_status(cycle_dir, cycle_dir.parent, scientific)
    if (status["policy_version"] != BUDGET_POLICY_3_PLUS_3
            or status["granted_tranches"] != REVIEWER_TRANCHES
            or status["rejected"] or status["supervisor_authorized"]
            or not status["exhausted"]):
        raise ValueError("Supervisor may decide only once at the third tranche")
    return status


def apply_supervisor_budget_decision(cycle_dir: Path, *, authorize: bool,
                                     reason: str, next_step: str) -> dict[str, Any]:
    """Commit one Supervisor decision and, if approved, tranche #4 atomically."""
    cycle_dir = Path(cycle_dir).resolve()
    if len(reason.strip()) < 30 or len(next_step.strip()) < 20:
        raise ValueError("Supervisor decision requires reason and bounded continuation plan")
    ledger = cycle_dir / LEDGER_REL
    with (ledger.parent / "investigator_review.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        status = _validate_supervisor_budget_boundary(cycle_dir)
        base = status["tranche_tokens"]
        common = {
            "schema": "theory-master/investigator-budget-review/v1",
            "policy_version": BUDGET_POLICY_3_PLUS_3,
            "at": now(), "cycle_id": cycle_dir.name,
            "review_index": status["review_index"], "tranche_tokens": base,
            "prior_allowance": status["allowance_tokens"],
            "observed_tokens": status["observed_tokens"],
            "reason": reason.strip(), "next_bounded_test": next_step.strip(),
            "authority": "supervisor",
        }
        decision = {**common, "decision": "supervisor_authorize" if authorize else "supervisor_decline",
                    "new_allowance": status["allowance_tokens"]}
        records = [decision]
        if authorize:
            records.append({**common, "decision": "supervisor_grant",
                            "new_allowance": status["allowance_tokens"] + base,
                            "grant_number": REVIEWER_TRANCHES + 1})
        existing = ledger.read_text(encoding="utf-8") if ledger.is_file() else ""
        atomic(ledger, existing + "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records))
        verified = budget_status(cycle_dir, cycle_dir.parent, json.loads(
            (cycle_dir / "research_state.json").read_text(encoding="utf-8")))
        if authorize and (not verified["supervisor_authorized"] or verified["granted_tranches"] != 4):
            raise RuntimeError("Supervisor authorization ledger invariant failed")
        if not authorize and not verified["supervisor_declined"]:
            raise RuntimeError("Supervisor rejection ledger invariant failed")
        return dict(decision, status=verified)


def grant_supervisor_tranche(cycle_dir: Path) -> dict[str, Any]:
    """Grant the next installment of an already-authorized #4..#6 window."""
    cycle_dir = Path(cycle_dir).resolve()
    ledger = cycle_dir / LEDGER_REL
    with (ledger.parent / "investigator_review.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        run = json.loads((cycle_dir / "run_state.json").read_text(encoding="utf-8"))
        scientific = _validate_budget_review_state(
            cycle_dir, run, expected_code=BUDGET_ATTENTION_CODE)
        status = budget_status(cycle_dir, cycle_dir.parent, scientific)
        if (status["policy_version"] != BUDGET_POLICY_3_PLUS_3
                or not status["supervisor_authorized"] or status["rejected"]
                or not status["exhausted"]
                or status["granted_tranches"] not in (4, 5)):
            raise ValueError("Supervisor extension is not available at this boundary")
        number = status["granted_tranches"] + 1
        row = {
            "schema": "theory-master/investigator-budget-review/v1",
            "policy_version": BUDGET_POLICY_3_PLUS_3,
            "at": now(), "cycle_id": cycle_dir.name,
            "decision": "supervisor_grant", "authority": "sdk",
            "grant_number": number, "review_index": status["review_index"],
            "tranche_tokens": status["tranche_tokens"],
            "prior_allowance": status["allowance_tokens"],
            "observed_tokens": status["observed_tokens"],
            "new_allowance": status["allowance_tokens"] + status["tranche_tokens"],
            "reason": "Installment of one committed Supervisor 3-tranche authorization",
        }
        existing = ledger.read_text(encoding="utf-8")
        atomic(ledger, existing + json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return row


def _validate_budget_review_state(cycle_dir: Path, projection: dict[str, Any], *,
                                  expected_code: str = BUDGET_ATTENTION_CODE) -> dict[str, Any]:
    """Fail closed unless run_state and the authoritative workflow checkpoint agree."""
    scientific_path = cycle_dir / "research_state.json"
    if not scientific_path.is_file():
        raise ValueError("authoritative research_state.json is missing; budget review cannot rely on run_state projection alone")
    try:
        scientific = json.loads(scientific_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("authoritative research_state.json is unreadable") from exc
    if not isinstance(scientific, dict):
        raise ValueError("authoritative research_state.json is invalid")

    def normalized_status(value: dict[str, Any]) -> str:
        return str(value.get("status") or "").lower()

    def attention_code(value: dict[str, Any]) -> str:
        attention = value.get("attention") if isinstance(value.get("attention"), dict) else {}
        return str(attention.get("code") or "")

    def cycle_identity(value: dict[str, Any]) -> str:
        return str(value.get("cycle_id") or value.get("run_id") or cycle_dir.name)

    expected_root = cycle_dir.parent.resolve()
    for label, value in (("run_state", projection), ("research_state", scientific)):
        if normalized_status(value) != "needs_attention":
            raise ValueError(f"{label} is not awaiting an Investigator budget review")
        if attention_code(value) != expected_code:
            raise ValueError(f"{label} attention code is not an Investigator budget review")
        if value.get("resumable") is not True:
            raise ValueError(f"{label} is not safely resumable")
        if cycle_identity(value) != cycle_dir.name:
            raise ValueError(f"{label} refers to another Cycle")
        recorded_root = value.get("research_root")
        if recorded_root and Path(str(recorded_root)).expanduser().resolve() != expected_root:
            raise ValueError(f"{label} refers to another research root")

    if (normalized_status(projection) != normalized_status(scientific)
            or attention_code(projection) != attention_code(scientific)
            or projection.get("resumable") is not scientific.get("resumable")
            or cycle_identity(projection) != cycle_identity(scientific)):
        raise ValueError("budget review state projection is stale or inconsistent with research_state.json")
    projection_root = projection.get("research_root")
    scientific_root = scientific.get("research_root")
    if projection_root and scientific_root and Path(str(projection_root)).expanduser().resolve() != Path(str(scientific_root)).expanduser().resolve():
        raise ValueError("budget review research_root differs between state files")
    return scientific


def _apply_review_locked(cycle_dir: Path, *, decision: str, reason: str,
                         next_test: str, evidence_ids: list[str],
                         progress_refs: list[str]) -> dict[str, Any]:
    state_path = cycle_dir / "run_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("run_state.json is missing or unreadable") from exc
    if not isinstance(state, dict):
        raise ValueError("run_state.json is invalid")
    scientific = _validate_budget_review_state(cycle_dir, state)
    # The run_state is a Web/runtime projection; the research_state owns the
    # frozen 3+3 policy. Never infer a legacy grant from missing projection key.
    status = budget_status(cycle_dir, Path(state["research_root"]), scientific)
    if status["rejected"] or not status["exhausted"]:
        raise ValueError("budget review already resolved or allowance is no longer exhausted")
    if status["policy_version"] == BUDGET_POLICY_3_PLUS_3 and status["granted_tranches"] >= REVIEWER_TRANCHES:
        raise ValueError("Budget Reviewer may authorize only tranches 2 and 3")
    approved_known = _evidence_ids(cycle_dir)
    requested = list(dict.fromkeys(str(x).strip() for x in evidence_ids if str(x).strip()))
    if set(requested) - approved_known:
        raise ValueError("review refers to unregistered scientific Evidence")
    prior = set(status["reviewed_evidence_ids"])
    fresh = sorted(approved_known - prior)
    # Lifecycle authorization was cross-checked against the authoritative
    # workflow checkpoint above. Scientific Findings remain sourced from that
    # checkpoint whenever available; do not let a stale projection authorize spend.
    progress_source = scientific if scientific.get("investigation_attempts") else state
    from .budget_review_transcript import conversation_review_progress
    # SDK verifies the dialogue's exact recorded bytes and subtracts previously
    # consumed spans; the Reviewer must judge the science independently.
    conversation = conversation_review_progress(cycle_dir, status["reviewed_progress_refs"])
    registered_progress = (
        _finding_progress_refs(progress_source)
        | _verified_workspace_refs(cycle_dir)
        | {row["invocation_id"] for row in conversation}
    )
    requested_progress = list(dict.fromkeys(str(x).strip() for x in progress_refs if str(x).strip()))
    if set(requested_progress) - registered_progress:
        raise ValueError("review refers to unregistered Investigator progress")
    prior_progress = set(status["reviewed_progress_refs"])
    fresh_progress = sorted(registered_progress - prior_progress)
    if decision == "approve" and not (set(requested).intersection(fresh) or set(requested_progress).intersection(fresh_progress)):
        raise ValueError("approval requires new registered Evidence or Investigator Findings, or SHA-verified new Investigator dialogue/research, not previously reviewed progress")
    base = status["tranche_tokens"]
    new = (status["allowance_tokens"] + base
           if decision == "approve" else status["allowance_tokens"])
    record = {
        "schema": "theory-master/investigator-budget-review/v1",
        **({"policy_version": BUDGET_POLICY_3_PLUS_3} if status["policy_version"] == BUDGET_POLICY_3_PLUS_3 else {}),
        "at": now(), "cycle_id": cycle_dir.name,
        "review_index": status["review_index"],
        "decision": decision, "reason": reason.strip(), "next_bounded_test": next_test.strip(),
        "evidence_ids": requested, "evidence_ids_since_prior_review": fresh,
        "progress_refs": requested_progress, "progress_refs_since_prior_review": fresh_progress,
        "tranche_tokens": base, "prior_allowance": status["allowance_tokens"],
        "observed_tokens": status["observed_tokens"], "new_allowance": new,
        "allowance_basis": "prior_allowance",
        "investigator_thread_id": (progress_source.get("threads") or state.get("threads") or {}).get("investigator"),
        "checkpoint": state.get("last_safe_checkpoint"),
    }
    path = cycle_dir / LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.is_file() else ""
    atomic(path, previous + json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Commit one audited Investigator budget decision")
    parser.add_argument("--cycle-dir", type=Path, required=True)
    parser.add_argument("--decision", choices=["approve", "reject"], required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--next-test", required=True)
    parser.add_argument("--evidence-id", action="append", default=[])
    parser.add_argument("--progress-ref", action="append", default=[])
    args = parser.parse_args()
    result = apply_review(args.cycle_dir, decision=args.decision,
                          reason=args.reason, next_test=args.next_test,
                          evidence_ids=args.evidence_id, progress_refs=args.progress_ref)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
