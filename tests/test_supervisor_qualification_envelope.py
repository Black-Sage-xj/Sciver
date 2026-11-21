from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from theory_master.invocations import stable_hash
from theory_master.supervisor_qualification_recovery import (
    _decision,
    normalize_candidate_envelope,
    parse_review_object,
)


@pytest.fixture
def misplaced_decision():
    return {
        "action": "select_candidate",
        "reason": "The previous test leaves the response to a fixed perturbation open.",
        "candidate": {
            "hypothesis": "The paired improvement survives the fixed perturbation.",
            "scope": "Use the supplied measurements only.",
            "why_now": "The frozen comparison can now be executed on an eligible pair.",
            "goal_advance": "Decide whether the improvement is stable on this pair.",
            "decisive_test": "Compare paired widths with identical eligibility gates.",
            "transition": {
                "prior_gap": "The comparator failed on earlier partitions.",
                "current_change": "Use a fixed observation perturbation.",
                "source_contributions": {"H-0003": "Recorded the unperturbed result."},
            },
        },
        "lineage_main_node_id": "H-0003",
        "lineage_other_node_ids": [],
        "evolution_operator": "refine",
    }


def test_both_envelope_repairs_are_audited_and_use_the_candidate_contract(misplaced_decision):
    original = deepcopy(misplaced_decision)
    raw = json.dumps(misplaced_decision) + "}"
    decoded, removed_closer = parse_review_object(raw)
    normalized, moved = normalize_candidate_envelope(decoded)
    assert removed_closer and decoded == original == misplaced_decision
    assert moved == ("lineage_main_node_id", "lineage_other_node_ids", "evolution_operator")
    assert set(normalized) == {"action", "reason", "candidate"}
    assert normalized["candidate"] == {**original["candidate"], **{key: original[key] for key in moved}}
    assert normalize_candidate_envelope(normalized) == (normalized, ())

    trace = []
    session = SimpleNamespace(append=lambda kind, content, **fields: trace.append((kind, fields)))
    context = {"_known_hypothesis_ids": ["H-0003"]}
    decision = _decision(raw, context, {}, trace=session, thread_id="original-thread")
    assert decision == _decision(json.dumps(normalized), context, {})
    assert decision["status"] == "proposed"
    assert trace == [
        ("normalization", {"thread_id": "original-thread", "edit": "remove_extraneous_root_closer",
                           "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                           "decision_sha256": stable_hash(original)}),
        ("normalization", {"thread_id": "original-thread", "edit": "move_candidate_lineage_fields",
                           "moved_fields": list(moved), "parsed_decision_sha256": stable_hash(original),
                           "decision_sha256": stable_hash(normalized)}),
    ]


@pytest.mark.parametrize("defect", ["unknown_root", "missing_field", "collision", "non_object"])
def test_ambiguous_envelopes_are_rejected(misplaced_decision, defect):
    if defect == "unknown_root":
        misplaced_decision["reconsideration_trigger"] = "A new source exists."
    elif defect == "missing_field":
        del misplaced_decision["lineage_other_node_ids"]
    elif defect == "collision":
        misplaced_decision["candidate"]["evolution_operator"] = misplaced_decision["evolution_operator"]
    else:
        misplaced_decision["candidate"] = [misplaced_decision["candidate"]]

    with pytest.raises(ValueError, match="inside the candidate object"):
        normalize_candidate_envelope(misplaced_decision)


def test_moved_values_still_require_existing_scientific_lineage(misplaced_decision):
    misplaced_decision["lineage_main_node_id"] = "H-9999"
    normalized, _ = normalize_candidate_envelope(misplaced_decision)
    assert normalized["candidate"]["lineage_main_node_id"] == "H-9999"
    with pytest.raises(ValueError, match="lineage anchors must identify existing SQLite H nodes"):
        _decision(json.dumps(misplaced_decision), {"_known_hypothesis_ids": ["H-0003"]}, {})


def _repeat_key(raw, key, first, second):
    fragment = json.dumps({key: first})[1:-1]
    duplicate = json.dumps({key: second})[1:-1]
    assert raw.count(fragment) == 1
    return raw.replace(fragment, f"{fragment}, {duplicate}", 1)


def test_identical_candidate_transition_is_audited_without_changing_lineage(misplaced_decision):
    normalized, _ = normalize_candidate_envelope(misplaced_decision)
    candidate = normalized["candidate"]
    candidate["lineage_other_node_ids"] = ["H-0004"]
    candidate["evolution_operator"] = "derive"
    transition = candidate["transition"]
    transition["source_contributions"]["H-0004"] = "Preserve the fixed comparator and its limitations."
    raw = _repeat_key(json.dumps(normalized), "transition", transition, transition)
    context = {"_known_hypothesis_ids": ["H-0003", "H-0004"]}
    events = []
    trace = SimpleNamespace(append=lambda kind, content, **fields: events.append((kind, fields)))

    proposed = _decision(raw, context, {}, trace=trace, thread_id="original-thread")

    assert proposed == _decision(json.dumps(normalized), context, {})
    assert proposed["status"] == "proposed"
    selected = proposed["frontier"]["candidates"][0]
    for field in ("hypothesis", "decisive_test", "lineage_main_node_id", "lineage_other_node_ids",
                  "evolution_operator", "transition"):
        assert selected[field] == candidate[field]
    [(kind, fields)] = events
    assert kind == "normalization"
    assert fields["edit"] == "collapse_identical_candidate_transition"
    assert fields["thread_id"] == "original-thread"
    assert fields["raw_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert fields["decision_sha256"] == stable_hash(normalized)


@pytest.mark.parametrize("defect,key", [("different_transition", "transition"),
                                        ("other_candidate_key", "lineage_main_node_id"),
                                        ("root_transition", "transition"),
                                        ("nested_transition", "transition")])
def test_other_duplicate_shapes_are_rejected(misplaced_decision, defect, key):
    decision, _ = normalize_candidate_envelope(misplaced_decision)
    candidate = decision["candidate"]
    if defect == "different_transition":
        first = candidate["transition"]
        second = {**first, "prior_gap": "A different source or scientific reason."}
    elif defect == "other_candidate_key":
        first = second = candidate[key]
    else:
        first = second = {"unexpected": "identical values in an unauthorized location"}
        container = decision if defect == "root_transition" else candidate["transition"]["source_contributions"]
        container[key] = first
    raw = _repeat_key(json.dumps(decision), key, first, second)

    with pytest.raises(ValueError, match=f"Duplicate Supervisor decision key: {key}"):
        _decision(raw, {"_known_hypothesis_ids": ["H-0003"]}, {})
