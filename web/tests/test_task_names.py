"""Task names stay short without rewriting the reviewed scientific Plan."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tm_observer import research_preparation as prep
from tm_observer.collector import TaskLocation, collect_run
from tm_observer.research_preparation_ui import session_panel
from tm_observer.task_names import MAX_TASK_NAME_LENGTH, task_name, validate_task_name


IDENT = "b" * 32
LONG_GOAL = "检验多种机制在不同实验条件下对观测结果的影响，" * 6 + "并比较其不确定性。"


def _plan(goal: str) -> str:
    return (f"# Goal\n\n{goal}\n\n# Research Contract\n\nUse independent samples.\n\n"
            "# Final Gate\n\nResolve the signed comparison with uncertainty.\n\n"
            "# Required Context\n\nUse the supplied definitions.\n")


def _draft(tmp_path, monkeypatch, *, title: str = "", goal: str = LONG_GOAL):
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "data"))
    state, search = tmp_path / "state", tmp_path / "workspace"
    search.mkdir()
    initial = prep.initialize(state, IDENT, "clear", title)
    current = prep.update(state, IDENT, {"expected_revision": initial["revision"], "draft": _plan(goal)})
    return state, search, current


@pytest.mark.parametrize("goal,expected", [
    (LONG_GOAL, "研究任务 7"),
    ("A short complete objective.", "A short complete objective."),
    ("比较两种机制。" + LONG_GOAL, "比较两种机制。"),
    ("Compare two mechanisms. " + LONG_GOAL, "Compare two mechanisms."),
    ("# 简短研究目标\n" + LONG_GOAL, "简短研究目标"),
])
def test_goal_fallback_keeps_complete_short_text(goal, expected):
    assert task_name(task_id=7, goal=goal) == expected
    assert len(expected) <= MAX_TASK_NAME_LENGTH


def test_explicit_short_name_has_priority_over_goal():
    assert task_name("  Short   study  ", task_id=7, goal=LONG_GOAL) == "Short study"


@pytest.mark.parametrize("name", ["x" * 49, "a\nb", "a\tb", "a\x7fb", "", None])
def test_new_names_reject_paragraphs_and_control_characters(name):
    with pytest.raises(ValueError):
        validate_task_name(name)


def test_create_long_goal_preserves_plan_and_saves_short_task_identity(tmp_path, monkeypatch):
    state, search, current = _draft(tmp_path, monkeypatch)
    receipt = prep.create_project(state, search, IDENT, {"confirm": True, "expected_revision": current["revision"]})
    assert receipt["task_name"] == "研究任务 " + str(receipt["task_id"])
    root = Path(receipt["task_root"])
    assert json.loads((root / "theory-master.config.json").read_text())["task_name"] == receipt["task_name"]
    assert (root / "preparation_snapshot" / "research_plan.md").read_text() == _plan(LONG_GOAL)
    assert LONG_GOAL in (root / "research_plan.md").read_text()


def test_create_respects_explicit_short_title(tmp_path, monkeypatch):
    state, search, current = _draft(tmp_path, monkeypatch, title="短任务名称")
    receipt = prep.create_project(state, search, IDENT, {"confirm": True, "expected_revision": current["revision"]})
    assert receipt["task_name"] == "短任务名称"


def test_legacy_long_title_can_save_other_fields_and_create(tmp_path, monkeypatch):
    state, search, _ = _draft(tmp_path, monkeypatch)
    meta_path = prep.workspace(state, IDENT) / "metadata.json"
    legacy = json.loads(meta_path.read_text())
    legacy["title"] = "Long historical title " * 8
    meta_path.write_text(json.dumps(legacy))
    current = prep.snapshot(state, IDENT)
    # The UI sends every field even when only the intent changed.
    current = prep.update(state, IDENT, {"expected_revision": current["revision"],
                                       "title": legacy["title"], "intent": "Revised intent"})
    receipt = prep.create_project(state, search, IDENT, {"confirm": True, "expected_revision": current["revision"]})
    assert receipt["task_name"] == "研究任务 " + str(receipt["task_id"])
    assert json.loads((Path(receipt["snapshot_path"]) / "preparation.json").read_text())["title"] == legacy["title"]


def test_new_long_title_fails_before_draft_changes(tmp_path, monkeypatch):
    state, _, current = _draft(tmp_path, monkeypatch)
    with pytest.raises(prep.PreparationError, match="48"):
        prep.update(state, IDENT, {"expected_revision": current["revision"], "title": "x" * 49,
                                   "draft": _plan("Changed objective.")})
    assert prep.snapshot(state, IDENT)["draft"] == current["draft"]


def test_initial_long_title_rejected_without_preparation_state(tmp_path):
    state = tmp_path / "state"
    with pytest.raises(prep.PreparationError, match="48"):
        prep.initialize(state, IDENT, "clear", "x" * 49)
    assert not (state / "research-preparations" / IDENT / "metadata.json").exists()


def test_collector_applies_short_identity_after_every_name_source(tmp_path, monkeypatch):
    config = {"task_id": 7, "task_name": LONG_GOAL}
    location = TaskLocation(tmp_path, tmp_path, tmp_path / "task.json", tmp_path / "config.json", config)
    assert collect_run(location)["task_name"] == "研究任务 7"
    # Exercise the shared normalization boundary independently of the source,
    # including an operator display-name override from newer route versions.
    monkeypatch.setattr("tm_observer.collector._task_display_name", lambda _: LONG_GOAL)
    assert collect_run(location)["task_name"] == "研究任务 7"
    monkeypatch.setattr("tm_observer.collector._task_display_name", lambda _: "Short operator name")
    assert collect_run(location)["task_name"] == "Short operator name"


def test_preparation_name_field_sets_short_input_limit():
    assert 'data-prep-title maxlength="48"' in session_panel({})
