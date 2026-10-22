"""End-to-end regression: legacy Decision frontiers must not count as separate H."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from theory_master.science_graph_store import ScienceGraphStore, import_legacy_canvas
from scripts.reconcile_science_frontiers import plan, apply


def test_reconcile_multiple_legacy_frontiers_preserves_judge_and_repairs_lead(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    research = tmp_path / "research"
    research.mkdir()
    with ScienceGraphStore(vault / "science_graph.sqlite3") as db:
        for h in ("H-0089", "H-0090", "H-0091", "H-0010"):
            db.add_hypothesis(h)
        db.append_outcome(outcome_id="128-decision", hypothesis_id="H-0090",
                          cycle_id="128", verdict="rejected",
                          summary="The extra double source information was unnecessary",
                          record_only=True)
        db.db.execute("DELETE FROM frontier_branches WHERE hypothesis_id='H-0089'")
        for i in range(121,127):
            db.db.execute("INSERT INTO frontier_branches(id,hypothesis_id,status,changed_at) VALUES (?,?,?,?)",
                          (f"legacy-outcome:{i}-decision","H-0089","active","2026-10-17"))
        db.db.execute("UPDATE frontier_branches SET id='legacy-outcome:128-decision',status='closed' WHERE hypothesis_id='H-0090'")
        db.db.execute("DELETE FROM frontier_branches WHERE hypothesis_id='H-0010'")
    cycle = research/"128"
    cycle.mkdir()
    (cycle/"frontier_maintenance_out.json").write_text(json.dumps({
        "updates":[{"node_id":"H-0090","frontier_status":"parked",
                    "reason":"Judge rejected the narrow surplus information test, not the wider mechanism"},
                   {"node_id":"121-decision","frontier_status":"parked",
                    "reason":"historical"}]}),encoding="utf-8")
    newer=research/"133"
    newer.mkdir()
    (newer/"frontier_maintenance_out.json").write_text(json.dumps({
        "updates":[{"node_id":"H-0089","frontier_status":"active",
                    "reason":"remaining scientific closure opportunity"}]}),encoding="utf-8")
    choices=plan(vault/"science_graph.sqlite3", research)
    assert choices["historical_records"]==8
    assert choices["active_h"]==["H-0089","H-0091"]
    assert choices["choices"]["H-0090"]["status"]=="parked"
    result=apply(vault/"science_graph.sqlite3",research,choices,vault)
    assert Path(result["backup"]).is_file()
    with ScienceGraphStore(vault/"science_graph.sqlite3",readonly=True) as db:
        view=db.frontier_view()
        assert view["H-0090"]["frontier_status"]=="parked"
        assert view["H-0010"]["frontier_status"] is None
        assert len(view["H-0089"]["branches"])==1
        assert db.db.execute("SELECT count(*) FROM legacy_frontier_archive").fetchone()[0]==8
        assert db.cycle_history("H-0090")[0]["verdict"]=="rejected"
        assert db.db.execute("SELECT verdict FROM outcomes WHERE hypothesis_id='H-0090'").fetchone()[0]=="open"
    with ScienceGraphStore(vault/"science_graph.sqlite3") as db:
        with pytest.raises(ValueError,match="no verified frontier state"):
            db.assert_eligible_hypothesis("H-0010",same_h=True)
        db.set_frontiers_batch([{"hypothesis_id":"H-0089","status":"parked",
                                "reason":"test pause"}])
        assert len(db.frontier_view()["H-0089"]["branches"])==1
        with pytest.raises(sqlite3.IntegrityError):
            db.db.execute("INSERT INTO frontier_branches(id,hypothesis_id,status,changed_at) VALUES ('another','H-0089','active','now')")
    with pytest.raises(RuntimeError,match="Already reconciled"):
        apply(vault/"science_graph.sqlite3",research,plan(vault/"science_graph.sqlite3",research),vault)


def test_import_conflicting_old_decisions_keeps_judge_and_unknown_frontier(tmp_path: Path):
    canvas=tmp_path/"legacy.canvas"
    canvas.write_text(json.dumps({"nodes":[
        {"id":"H-0001","text":"id: H-0001\ntype: Hypothesis\n\n# Hypothesis"},
        {"id":"17-decision","text":"id: 17-decision\ntype: Decision\ncycle_id: 17\nverdict: rejected\nfrontier_status: closed\n\n# Outcome"},
        {"id":"18-decision","text":"id: 18-decision\ntype: Decision\ncycle_id: 18\nverdict: open\nfrontier_status: parked\n\n# Outcome"},
    ],"edges":[{"fromNode":"H-0001","toNode":"17-decision"},
               {"fromNode":"H-0001","toNode":"18-decision"}]}),encoding="utf-8")
    path=tmp_path/"science_graph.sqlite3"
    import_legacy_canvas(canvas,path)
    with ScienceGraphStore(path,readonly=True) as db:
        assert db.frontier_view()["H-0001"]["frontier_status"] is None
        assert len(db.graph_view()["nodes"][0]["cycle_outcomes"])==2
        assert db.db.execute("SELECT COUNT(*) FROM legacy_frontier_archive").fetchone()[0]==2
