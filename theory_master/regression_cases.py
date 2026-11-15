"""Canonical reusable regression cases for TheoryMaster.

The case fixture defines *what scientific task to run*.  Stage/range selection,
repeat count, model routing, and runtime budgets remain responsibilities of the
existing TM/TMLab/TBH execution layers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import subprocess
import sysconfig
import os
import tempfile
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from importlib.resources import files

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# Wheels carry the public fixture beside the SDK; retain support for older
# installations that placed it under the interpreter's shared data directory.
_BUNDLED_CASES = files("theory_master").joinpath("resources", "regression_cases")
CASES_ROOT = (Path(str(_BUNDLED_CASES)) if _BUNDLED_CASES.joinpath("index.json").is_file()
              else PACKAGE_ROOT / "regression_cases")
if not (CASES_ROOT / "index.json").is_file():
    CASES_ROOT = Path(sysconfig.get_path("data")) / "share/sciver/regression_cases"
INDEX_PATH = CASES_ROOT / "index.json"


class RegressionCaseError(RuntimeError):
    pass


def _normalize(value: str) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _load_index() -> dict[str, Any]:
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    if data.get("schema") != "theory-master/regression-case-index/v1":
        raise RegressionCaseError(f"unsupported regression-case index: {INDEX_PATH}")
    return data


@dataclass(frozen=True)
class RegressionCase:
    case_id: str
    root: Path
    metadata: dict[str, Any]

    @property
    def plan_path(self) -> Path:
        return self.root / "research_plan.md"

    @property
    def benchmark_dir(self) -> Path:
        return self.root / "benchmark"

    @property
    def expected_path(self) -> Path:
        return self.root / "expected.json"

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(str(x) for x in self.metadata.get("aliases", []))


def list_cases() -> list[RegressionCase]:
    index = _load_index()
    result: list[RegressionCase] = []
    for entry in index.get("cases", []):
        root = CASES_ROOT / str(entry["path"])
        meta = json.loads((root / "case.json").read_text(encoding="utf-8"))
        result.append(RegressionCase(str(meta["id"]), root, meta))
    return result


def resolve_case(selector: str | int) -> RegressionCase:
    wanted = _normalize(str(selector))
    matches = []
    for case in list_cases():
        names = {case.case_id, *case.aliases}
        if wanted in {_normalize(name) for name in names}:
            matches.append(case)
    if len(matches) != 1:
        known = ", ".join(case.case_id for case in list_cases())
        if not matches:
            raise RegressionCaseError(f"unknown TheoryMaster regression case {selector!r}; known: {known}")
        raise RegressionCaseError(f"ambiguous TheoryMaster regression case {selector!r}")
    return matches[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_case(selector: str | int) -> dict[str, Any]:
    case = resolve_case(selector)
    plan = case.plan_path.read_text(encoding="utf-8")
    if "# Scientific Model" in plan or "# Frontier State" in plan:
        raise RegressionCaseError(f"{case.case_id} fixture contains runtime scientific state")
    manifest_path = case.benchmark_dir / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verified = []
    for item in manifest.get("files", []):
        rel = Path(str(item["path"]))
        path = case.root / rel
        if not path.is_file():
            raise RegressionCaseError(f"{case.case_id} missing fixture input: {rel}")
        expected_size = int(item["size"])
        expected_sha = str(item["sha256"])
        if path.stat().st_size != expected_size:
            raise RegressionCaseError(f"{case.case_id} size mismatch: {rel}")
        actual_sha = _sha256(path)
        if actual_sha != expected_sha:
            raise RegressionCaseError(f"{case.case_id} SHA-256 mismatch: {rel}")
        verified.append(str(rel))
    return {
        "id": case.case_id,
        "root": str(case.root),
        "files_verified": len(verified),
        "verified": verified,
    }


def materialize_case(selector: str | int, destination: Path) -> Path:
    case = resolve_case(selector)
    verify_case(case.case_id)
    destination = destination.expanduser().resolve()
    if destination.exists() and any(destination.iterdir()):
        raise RegressionCaseError(f"destination must be absent or empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(case.plan_path, destination / "research_plan.md")
    shutil.copytree(case.benchmark_dir, destination / "benchmark", dirs_exist_ok=True)

    # Verify the copied public-input manifest too.  expected.json/case.json stay
    # outside the task root so baseline expectations cannot leak into science.
    manifest = json.loads((destination / "benchmark" / "source_manifest.json").read_text(encoding="utf-8"))
    for item in manifest.get("files", []):
        path = destination / str(item["path"])
        if not path.is_file() or path.stat().st_size != int(item["size"]) or _sha256(path) != str(item["sha256"]):
            raise RegressionCaseError(f"materialized fixture verification failed: {item['path']}")
    return destination


def initialize_case(selector: str | int, destination: Path, *, executable: str = "theory_master") -> Path:
    root = materialize_case(selector, destination)
    proc = subprocess.run(
        [executable, "new", str(root / "research_plan.md")],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "TheoryMaster new failed").strip()
        raise RegressionCaseError(detail[-4000:])
    return root




def create_test_instance(selector: str = "Test0", destination: Path | None = None, *,
                         instances_root: Path | None = None, start: bool = True):
    """Create a fresh fixture task without inheriting scientific state or sessions."""
    from .task import bootstrap, task_id_scope_root
    from .specification import TaskSpec
    case = resolve_case(selector)
    if destination is None:
        if instances_root is None:
            from .onboarding import default_workspace
            configured = os.environ.get("SCIVER_TEST_ROOT", "").strip()
            search = os.environ.get("THEORY_MASTER_SEARCH_ROOT", "").strip()
            instances_root = (Path(configured).expanduser() if configured else
                              Path(search).expanduser() / "test-instances" if search else
                              default_workspace() / "test-instances")
        instances_root = instances_root.expanduser().resolve()
        instances_root.mkdir(parents=True, exist_ok=True)
        destination = Path(tempfile.mkdtemp(
            prefix=case.case_id.replace("_", "") + "-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-",
            dir=instances_root))
    root = materialize_case(case.case_id, destination)
    scope = task_id_scope_root(root / "research_plan.md")
    if scope == root or not root.is_relative_to(scope):
        scope = root.parent
    binding = bootstrap(root / "research_plan.md", allocation_root=scope)
    from .onboarding import install_benchmark_inputs
    install_benchmark_inputs(binding)
    record = {"case": case.case_id, "task_id": binding.master_id,
              "task_home": str(root), "research_root": str(binding.research_root),
              "created_at": datetime.now().astimezone().isoformat(), "operation": "run"}
    (root / ".theory-master" / "test-instance.json").write_text(json.dumps(record, indent=2) + "\n")
    outcome = {"status": "prepared"}
    if start:
        from .runtime_manager import launch_managed_runtime
        outcome = launch_managed_runtime(TaskSpec.from_config(binding.config_path),
                                         operation="run", identifier=str(binding.master_id))
        if outcome.get("status") != "started":
            raise RegressionCaseError(f"new test task {binding.master_id} was created but launch failed: {outcome.get('status')}")
    return binding, outcome


def grade_case(selector: str | int, submission: Path) -> dict[str, Any]:
    """Offline post-run grader, outside materialized task input."""
    case = resolve_case(selector)
    grader = case.root / "evaluator" / "grade.py"
    if not grader.is_file():
        raise RegressionCaseError(f"{case.case_id} has no offline grader")
    submission = submission.expanduser().resolve()
    proc = subprocess.run(
        [sys.executable, str(grader), str(submission)],
        cwd=case.root,
        capture_output=True, text=True,
        timeout=30, check=False,
    )
    if proc.returncode not in (0, 1):
        raise RegressionCaseError(
            f"{case.case_id} grader failure (exit {proc.returncode}): {(proc.stderr or proc.stdout)[-2000:]}"
        )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RegressionCaseError(f"{case.case_id} grader returned invalid JSON") from exc
    if result.get("id") != case.case_id or bool(result.get("passed")) != (proc.returncode == 0):
        raise RegressionCaseError(f"{case.case_id} grader returned inconsistent status")
    return result


def _show(case: RegressionCase) -> dict[str, Any]:
    expected = json.loads(case.expected_path.read_text(encoding="utf-8"))
    return {
        "id": case.case_id,
        "aliases": list(case.aliases),
        "fixture_root": str(case.root),
        "purpose": case.metadata.get("purpose"),
        "baseline": expected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m theory_master.regression_cases")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list canonical TheoryMaster regression cases")
    show = sub.add_parser("show", help="show case metadata and historical baseline")
    show.add_argument("case")
    verify = sub.add_parser("verify", help="verify fixture files against public SHA-256 manifest")
    verify.add_argument("case")
    materialize = sub.add_parser("materialize", help="copy a fresh science-only fixture into a task directory")
    materialize.add_argument("case")
    materialize.add_argument("destination", type=Path)
    materialize.add_argument("--initialize", action="store_true", help="also run 'theory_master new' after materializing")
    materialize.add_argument("--tm-executable", default="theory_master")
    grade = sub.add_parser("grade", help="evaluate finished submission offline (no LLM calls)")
    grade.add_argument("case")
    grade.add_argument("submission", type=Path)

    args = parser.parse_args(argv)
    if args.command == "list":
        for case in list_cases():
            print(f"{case.case_id}\taliases={','.join(case.aliases)}\t{case.metadata.get('purpose', '')}")
        return 0
    case = resolve_case(args.case)
    if args.command == "show":
        print(json.dumps(_show(case), ensure_ascii=False, indent=2))
        return 0
    if args.command == "verify":
        print(json.dumps(verify_case(case.case_id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "grade":
        result = grade_case(case.case_id, args.submission)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    if args.command == "materialize":
        root = (
            initialize_case(case.case_id, args.destination, executable=args.tm_executable)
            if args.initialize
            else materialize_case(case.case_id, args.destination)
        )
        print(root)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
