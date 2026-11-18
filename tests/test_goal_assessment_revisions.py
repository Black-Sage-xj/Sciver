"""Delivery-only Goal reassessment appends without creating another scientific Cycle."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from theory_master.goal_terminal import GoalSealedError
from theory_master.science_graph_store import ScienceGraphStore


SCIENCE = "a" * 64
PLAN = "b" * 64


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(path) as store:
        store.add_hypothesis("H-0001", title="Supported scientific result")
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
        store.append_outcome(outcome_id="cycle-outcome-1", hypothesis_id="H-0001",
                             cycle_id="1", verdict="supported", summary="Scientific result frozen")
        store.add_hypothesis("H-0002", title="An unfinished research frontier")
        store.register_cycle_hypothesis(cycle_id="2", hypothesis_id="H-0002")
    return path


def metadata(revision: str = "1", **changes: str) -> dict[str, str]:
    digest = revision * 64
    return {"assessment_id": f"goal:1:{digest}", "delivery_revision": digest,
            "science_digest": SCIENCE, "receipt_path": f"1/goal_receipts/{digest}.json", **changes}


def fail(store: ScienceGraphStore, revision: str = "1", **changes: str) -> None:
    store.record_goal_gate_not_passed(cycle_id="1", rationale="Reproduction script is missing",
                                     **metadata(revision, **changes))


def seal(store: ScienceGraphStore, revision: str = "2", **changes: str) -> dict:
    return store.seal_goal(goal_id="goal", source_cycle="1", research_plan_sha256=PLAN,
                           judge_receipt_ref="1/goal_completion_out.json",
                           detail={"rationale": "Reproduction materials now satisfy the Goal"},
                           **metadata(revision, **changes))


def revision_number(store: ScienceGraphStore) -> str:
    return store.db.execute("SELECT value FROM graph_meta WHERE key='graph_revision'").fetchone()[0]


def event_rows(store: ScienceGraphStore) -> list[tuple]:
    return [tuple(row) for row in store.db.execute("SELECT * FROM graph_events ORDER BY rowid")]


def test_same_supported_cycle_can_fail_then_pass_after_delivery_revision(db: Path) -> None:
    with ScienceGraphStore(db) as store:
        fail(store)
        previous = store.goal_gate_assessment("1")
        assert previous["verdict"] == "not_passed"
        assert previous["supersedes"] is None
        sealed = seal(store)
        history = store.goal_gate_history("1")
        assert sealed["newly_sealed"] is True
        assert [row["verdict"] for row in history] == ["not_passed", "passed"]
        assert history[1]["supersedes"] == history[0]["assessment_id"]
        assert store.goal_gate_assessment("1", delivery_revision="1" * 64) == previous
        assert store.goal_gate_assessment("1", assessment_id=history[1]["assessment_id"]) == history[1]
        assert store.goal_gate_assessment("1", delivery_revision="9" * 64) is None
        assert store.db.execute("SELECT COUNT(*) FROM cycle_evaluations").fetchone()[0] == 1
        assert store.db.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 2
        assert store.db.execute("SELECT COUNT(*) FROM goal_gate_assessments").fetchone()[0] == 0
        assert store.agent_cycle_detail("1")["goal_gate"] == history[-1]
        assert store.agent_cycle_detail("1")["goal_gate_history"] == history
        outcome = store.graph_view()["nodes"][0]["cycle_outcomes"][0]
        assert outcome["goal_gate"] == history[-1]
        assert outcome["goal_gate_history"] == history
        detail = store.agent_hypothesis_detail("H-0001")["cycle_outcomes"][0]
        assert detail["goal_gate_history"] == history
        assert store.agent_goal_completion_view()["goal_gate_history"] == history
        assert store.agent_view()["goal_gate_attention"] == []


def test_legacy_row_is_untouched_and_remains_the_first_history_entry(db: Path) -> None:
    with ScienceGraphStore(db) as store:
        store.record_goal_gate_not_passed(cycle_id="1", rationale="Legacy missing materials")
        before = tuple(store.db.execute("SELECT * FROM goal_gate_assessments").fetchone())
        store.record_goal_gate_not_passed(cycle_id="1", rationale="Legacy missing materials")
        with pytest.raises(ValueError, match="replay conflicts"):
            store.record_goal_gate_not_passed(cycle_id="1", rationale="changed legacy verdict")
        fail(store)
        seal(store)
        history = store.goal_gate_history("1")
        assert tuple(store.db.execute("SELECT * FROM goal_gate_assessments").fetchone()) == before
        assert [row["assessment_id"] for row in history] == ["legacy:1", metadata()["assessment_id"],
                                                           metadata("2")["assessment_id"]]
        assert history[0]["legacy"] is True
        assert history[1]["supersedes"] == "legacy:1"
        assert history[2]["supersedes"] == history[1]["assessment_id"]


def test_same_assessment_is_exact_replay_without_events_or_graph_revision(db: Path) -> None:
    with ScienceGraphStore(db) as store:
        fail(store)
        before = revision_number(store), store.goal_gate_history("1"), event_rows(store)
        fail(store)
        assert (revision_number(store), store.goal_gate_history("1"), event_rows(store)) == before
        for changes in ({"receipt_path": "1/different.json"}, {"science_digest": "f" * 64},
                        {"delivery_revision": "3" * 64}):
            with pytest.raises(ValueError, match="replay conflicts"):
                fail(store, **changes)
        with pytest.raises(ValueError, match="replay conflicts"):
            store.record_goal_gate_not_passed(cycle_id="1", rationale="Changed reason", **metadata())
        with pytest.raises(ValueError, match="replay conflicts"):
            seal(store, "1")
        assert (revision_number(store), store.goal_gate_history("1"), event_rows(store)) == before
        assert store.goal_terminal() is None


def test_same_delivery_revision_cannot_get_a_second_assessment_id(db: Path) -> None:
    with ScienceGraphStore(db) as store:
        fail(store)
        with pytest.raises(ValueError, match="different assessment ID"):
            fail(store, assessment_id="second-id")
        assert len(store.goal_gate_history("1")) == 1


def test_delivery_only_reassessment_rejects_science_drift(db: Path) -> None:
    with ScienceGraphStore(db) as store:
        fail(store)
        with pytest.raises(ValueError, match="science_digest"):
            fail(store, "2", science_digest="f" * 64)
        with pytest.raises(ValueError, match="science_digest"):
            seal(store, "3", science_digest="f" * 64)
        assert len(store.goal_gate_history("1")) == 1
        assert store.goal_terminal() is None
        assert {row[0] for row in store.db.execute("SELECT status FROM frontier_branches")} == {"active"}


@pytest.mark.parametrize("changes", [
    {"assessment_id": ""}, {"delivery_revision": ""}, {"science_digest": ""}, {"receipt_path": ""},
    {"delivery_revision": "1" * 63}, {"science_digest": "z" * 64}, {"assessment_id": " padded "},
    {"receipt_path": None},
])
def test_revision_metadata_is_all_or_none_and_canonical(db: Path, changes: dict) -> None:
    with ScienceGraphStore(db) as store:
        with pytest.raises(ValueError, match="metadata|sha256"):
            fail(store, **changes)
        assert store.goal_gate_history("1") == []


@pytest.mark.parametrize("verdict", [None, "open", "rejected"])
def test_assessment_requires_a_supported_scientific_outcome(tmp_path: Path, verdict: str | None) -> None:
    with ScienceGraphStore(tmp_path / "graph.sqlite3") as store:
        store.add_hypothesis("H-0001")
        if verdict:
            store.append_outcome(outcome_id="cycle-outcome-1", hypothesis_id="H-0001",
                                 cycle_id="1", verdict=verdict)
        with pytest.raises(ValueError, match="supported Cycle Outcome"):
            fail(store)
        with pytest.raises(ValueError, match="supported Cycle Outcome"):
            seal(store)
        assert store.goal_gate_history("1") == []
        assert store.goal_terminal() is None


def test_passed_revision_and_seal_and_frontiers_rollback_together(db: Path, monkeypatch) -> None:
    with ScienceGraphStore(db) as store:
        fail(store)
        before = store.goal_gate_history("1"), revision_number(store)
        original = store._event

        def failing_event(action, subject, detail):
            if action == "goal.sealed":
                raise RuntimeError("seal publication failed")
            return original(action, subject, detail)

        monkeypatch.setattr(store, "_event", failing_event)
        with pytest.raises(RuntimeError, match="publication failed"):
            seal(store)
        assert (store.goal_gate_history("1"), revision_number(store)) == before
        assert store.goal_terminal() is None
        assert {row[0] for row in store.db.execute("SELECT status FROM frontier_branches")} == {"active"}
        monkeypatch.setattr(store, "_event", original)
        assert seal(store)["newly_sealed"]


def test_sealed_revisions_are_immutable_even_for_derived_index_connection(db: Path) -> None:
    with ScienceGraphStore(db) as store:
        fail(store)
        first = seal(store)
        before = revision_number(store), store.goal_gate_history("1")
        replay = seal(store)
        assert replay["newly_sealed"] is False
        assert replay["sealed_at"] == first["sealed_at"]
        assert (revision_number(store), store.goal_gate_history("1")) == before
        with pytest.raises(ValueError, match="replay conflicts"):
            seal(store, receipt_path="changed")
        with pytest.raises(GoalSealedError):
            seal(store, "3")
    with ScienceGraphStore(db, allow_after_goal_seal=True) as store:
        with pytest.raises(GoalSealedError):
            fail(store, "3")
        with pytest.raises(GoalSealedError):
            store.record_goal_gate_not_passed(cycle_id="2", rationale="late legacy assessment")
        assert (revision_number(store), store.goal_gate_history("1")) == before


def test_legacy_api_cannot_bypass_revision_history(db: Path) -> None:
    with ScienceGraphStore(db) as store:
        fail(store)
        with pytest.raises(ValueError, match="explicit assessment metadata"):
            store.record_goal_gate_not_passed(cycle_id="1", rationale="legacy bypass")
        assert store.db.execute("SELECT COUNT(*) FROM goal_gate_assessments").fetchone()[0] == 0


def test_readonly_legacy_database_without_revision_table_is_not_migrated(db: Path) -> None:
    with ScienceGraphStore(db) as store:
        store.record_goal_gate_not_passed(cycle_id="1", rationale="Legacy missing materials")
    raw = sqlite3.connect(db)
    raw.execute("DROP TABLE goal_gate_assessment_revisions")
    raw.commit()
    raw.close()
    before = db.read_bytes()
    with ScienceGraphStore(db, readonly=True) as store:
        assert store.goal_gate_history("1")[0]["assessment_id"] == "legacy:1"
        assert store.agent_cycle_detail("1")["goal_gate"] == {
            "verdict": "not_passed", "rationale": "Legacy missing materials"}
        assert store.graph_view()["nodes"][0]["outcome"]["goal_gate_history"][0]["legacy"]
    assert db.read_bytes() == before
