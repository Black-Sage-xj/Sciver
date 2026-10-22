"""Offline evidence-source safety for nested Investigator workspace paths."""
import json
from pathlib import Path

import pytest

from theory_master.research_preservation import PreservationGuard, verified_sources


def _output(*paths):
    return {"observations": [{
        "statement": "Observed bounded finite-graph quantity",
        "conditions": "existing archived inputs only",
        "limitations": "no blind prediction claim",
        "evidence_paths": list(paths),
    }]}


def test_nested_workspace_paths_are_validated_and_normalized(tmp_path):
    cycle = tmp_path / "184"
    nested = cycle / "workspace" / "full_shell_smoke"
    nested.mkdir(parents=True)
    (nested / "README.md").write_text("Frozen shell diagnostics", encoding="utf-8")
    (nested / "pair_analysis.json").write_text('{"roots": 8}', encoding="utf-8")
    (cycle / "workspace" / "c17_convergence_extension_report.md").write_text(
        "Historical optimizer outcomes", encoding="utf-8")
    results = verified_sources(cycle, _output(
        "full_shell_smoke/README.md",
        "full_shell_smoke/pair_analysis.json",
        "c17_convergence_extension_report.md"))
    assert [x["path"] for x in results] == [
        "workspace/full_shell_smoke/README.md",
        "workspace/full_shell_smoke/pair_analysis.json",
        "workspace/c17_convergence_extension_report.md",
    ]
    assert all(len(x["sha256"]) == 64 for x in results)


def test_prefer_exact_cycle_relative_path_and_deduplicate(tmp_path):
    cycle = tmp_path / "185"
    (cycle / "workspace").mkdir(parents=True)
    (cycle / "report.json").write_text('{"original": true}')
    (cycle / "workspace/report.json").write_text('{"original": false}')
    srcs = verified_sources(cycle, _output(
        "report.json", "report.json", "workspace/report.json"))
    assert [row["path"] for row in srcs] == [
        "report.json", "workspace/report.json"]


def test_no_traversal_absolute_or_symlink_bypass(tmp_path):
    cycle = tmp_path / "184"
    sub = cycle / "workspace" / "a"
    sub.mkdir(parents=True)
    outside = tmp_path / "forbidden.json"
    outside.write_text('{"secret": true}')
    (sub / "link.json").symlink_to(outside)
    for bad in ("../forbidden.json", str(outside), "a/link.json",
                "a/does_not_exist.json"):
        with pytest.raises(PreservationGuard, match="existing Cycle-local"):
            verified_sources(cycle, _output(bad))


def test_cached_cycle184_paths_are_real_without_model_use(tmp_path):
    cycle = tmp_path / "184"
    folder = cycle / "workspace/full_shell_smoke"
    folder.mkdir(parents=True)
    for name in ("README.md", "pair_analysis.json", "budget_identity.md", "budget_check.json"):
        (folder / name).write_text(name)
    (cycle / "workspace/c17_convergence_extension_report.md").write_text("c17")
    (cycle / "workspace/homogeneous_baseline_fit_report.md").write_text("baseline")
    cached = {"observations": [
        {"evidence_paths": ["full_shell_smoke/README.md", "full_shell_smoke/pair_analysis.json"]},
        {"evidence_paths": ["full_shell_smoke/budget_identity.md", "full_shell_smoke/budget_check.json"]},
        {"evidence_paths": ["c17_convergence_extension_report.md", "homogeneous_baseline_fit_report.md"]},
    ]}
    (cycle / "preservation").mkdir()
    (cycle / "preservation/last_model_output.txt").write_text(json.dumps(cached))
    actual = json.loads((cycle / "preservation/last_model_output.txt").read_text())
    sources = verified_sources(cycle, actual)
    assert len(sources) == 6
    assert all(src["path"].startswith("workspace/") for src in sources)
