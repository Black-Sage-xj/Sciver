"""Test0 evaluator-side dual-status report; never materialize inside a research task.

Read already-written scientific evidence rather than requiring an additional
submission.json.  The hidden truth remains inside grade.py/expected.json.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from grade import grade_payload


def _positive_finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


def _scientific_payload(path: Path) -> dict[str, Any]:
    """Extract one final training estimate from a completed Investigation."""
    data = json.loads(path.read_text(encoding="utf-8"))
    findings = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(findings, list):
        raise ValueError("investigation_result.json must contain findings")
    candidates = [
        finding for finding in findings
        if isinstance(finding, dict)
        and isinstance(finding.get("result"), dict)
        and "train_D" in finding["result"]
    ]
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one reported train_D, found {len(candidates)}")
    finding = candidates[0]
    result = finding["result"]
    train_d = _positive_finite(result["train_D"], "train_D")
    validation_d = _positive_finite(result.get("validation_D"), "validation_D")
    method = finding.get("method")
    statement = finding.get("statement")
    if not isinstance(method, str) or not method.strip():
        raise ValueError("method is missing")
    if not isinstance(statement, str) or "validat" not in statement.lower():
        raise ValueError("independent validation narrative is missing")
    return {
        "D_estimate": train_d,
        "method": method,
        "validation": f"held-out validation D={validation_d}; {statement}",
    }


def _internal_status(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"verdict": "unavailable"}
    data = json.loads(path.read_text(encoding="utf-8"))
    verdict = data.get("verdict") if isinstance(data, dict) else None
    if verdict not in {"passed", "not_passed"}:
        raise ValueError("Goal Completion receipt must have passed|not_passed")
    return {"verdict": verdict, "rationale": str(data.get("rationale") or "")}


def report(scientific_result: Path, goal_completion: Path | None = None) -> dict[str, Any]:
    payload = _scientific_payload(scientific_result)
    internal = _internal_status(goal_completion)
    oracle = grade_payload(payload)
    accepted = bool(oracle["passed"])
    goal = internal["verdict"]
    if goal == "unavailable":
        classification = "goal_unavailable"
    elif goal == "passed":
        classification = "both_pass" if accepted else "goal_pass_oracle_fail"
    else:
        classification = "oracle_pass_goal_not_passed" if accepted else "both_fail"
    return {
        "id": "test_0",
        "internal_goal_completion": internal,
        "external_oracle": oracle,
        "classification": classification,
        "evidence_source": "existing Investigator finding, not a new submission artifact",
        "note": "Historical internal verdict is read-only; external oracle does not change Goal Completion or trigger a Cycle.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scientific-result", type=Path, required=True)
    parser.add_argument("--goal-completion", type=Path)
    args = parser.parse_args()
    try:
        result = report(args.scientific_result, args.goal_completion)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(json.dumps({"id": "test_0", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["classification"] == "both_pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
