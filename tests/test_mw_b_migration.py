"""MW-B migration of legacy H claims into Cycle claims (idempotent, backed up)."""
from __future__ import annotations

import json
import os

import pytest

from theory_master.multi_worker import CycleClaims, _save_json
from theory_master.worker_migration import migrate_branch_claims
from theory_master.worker_runtime import save_worker


def _legacy(root, claims):
    _save_json(root / ".theory-master" / "branch_claims.json",
               {"schema": "theory-master/branch-claims/v1", "epoch": 9, "claims": claims})


def _claim(worker, h, claim_id, epoch, pid=None):
    return {"claim_id": claim_id, "fencing_epoch": epoch, "hypothesis_id": h, "entry_node_id": h,
            "worker_id": worker, "status": "claimed", "owner_pid": pid, "owner_start_ticks": None,
            "lease_expires_at": "2026-10-21T00:00:00+00:00"}


def test_migrate_branch_claims_idempotent_with_backup(tmp_path):
    root = tmp_path
    assert migrate_branch_claims(root)["migrated"] == []
    _legacy(root, {"H-0005": _claim("worker-1", "H-0005", "old-a", 4),
                   "H-0006": _claim("worker-2", "H-0006", "old-b", 7)})
    save_worker(root, "worker-1", status="PAUSED", current_cycle="5", claim_id="old-a", target_h_id="H-0005")
    save_worker(root, "worker-2", status="PAUSED", current_cycle="9", claim_id="stale", target_h_id="H-0006")
    original = (root / ".theory-master" / "branch_claims.json").read_bytes()

    result = migrate_branch_claims(root)
    by_worker = {c["worker_id"]: c for c in result["migrated"]}
    assert by_worker["worker-1"]["cycle_id"] == "5" and by_worker["worker-1"]["hypothesis_id"] == "H-0005"
    assert by_worker["worker-2"]["cycle_id"] is None  # registry disagrees on claim: not trusted
    assert by_worker["worker-2"]["needs_attention"]
    assert all(c["status"] == "paused" and c["owner_pid"] is None for c in result["migrated"])
    assert all(c["claim_id"] not in {"old-a", "old-b"} for c in result["migrated"])
    assert (root / ".theory-master" / "branch_claims.json").read_bytes() == original
    assert open(result["backup"], "rb").read() == original

    again = migrate_branch_claims(root)
    assert again["migrated"] == [] and again["backup"] == result["backup"]
    claims = CycleClaims(root)
    assert len(claims.list_claims()) == 2
    assert claims.running_hypotheses()[0] == {
        "worker_id": "worker-1", "cycle_id": "5", "hypothesis_id": "H-0005", "status": "paused"}
    with pytest.raises(ValueError, match="paused"):
        claims.assert_writer("worker-1", by_worker["worker-1"]["claim_id"],
                             by_worker["worker-1"]["fencing_epoch"], cycle_id="5")


def test_migration_refuses_live_legacy_owner(tmp_path):
    _legacy(tmp_path, {"H-0001": _claim("worker-1", "H-0001", "old", 1, pid=os.getpid())})
    with pytest.raises(RuntimeError, match="still running"):
        migrate_branch_claims(tmp_path)
    assert not (tmp_path / ".theory-master" / "cycle_claims.json").exists()


def test_migration_skips_conflicting_identity(tmp_path):
    claims = CycleClaims(tmp_path)
    live = claims.claim_cycle("worker-3")
    claims.bind_cycle("worker-3", live["claim_id"], live["fencing_epoch"], "2")
    claims.bind_hypothesis("worker-3", live["claim_id"], live["fencing_epoch"], "H-0002")
    _legacy(tmp_path, {"H-0002": _claim("worker-1", "H-0002", "old", 1)})
    result = migrate_branch_claims(tmp_path)
    assert result["migrated"] == [] and result["skipped"][0]["reason"].startswith("hypothesis already")
    assert json.loads((tmp_path / ".theory-master" / "cycle_claims.json").read_text())["claims"].keys() == {
        live["claim_id"]}
