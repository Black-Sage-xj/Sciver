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
import re
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
CASES_ROOT = (
    Path(str(_BUNDLED_CASES))
    if _BUNDLED_CASES.joinpath("index.json").is_file()
    else PACKAGE_ROOT / "regression_cases"
)
if not (CASES_ROOT / "index.json").is_file():
    CASES_ROOT = Path(sysconfig.get_path("data")) / "share/sciver/regression_cases"
INDEX_PATH = CASES_ROOT / "index.json"
# Optional user-owned catalog. Its files never become package resources.
USER_CASES_ROOT_ENV = "SCIVER_TEST_CASES_ROOT"


class RegressionCaseError(RuntimeError):
    pass


def _normalize(value: str) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _safe_member(root: Path, relative: str | Path) -> Path:
    member = Path(relative)
    if not str(relative) or member.is_absolute() or ".." in member.parts:
        raise RegressionCaseError(f"unsafe fixture path: {relative}")
    path = root / member
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise RegressionCaseError(f"fixture path traverses a symlink: {path}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise RegressionCaseError(f"fixture path leaves catalog root: {path}")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RegressionCaseError(f"fixture JSON is not a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RegressionCaseError(f"cannot read fixture JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RegressionCaseError(f"fixture JSON must be an object: {path}")
    return value


def _load_index(path: Path | None = None) -> dict[str, Any]:
    path = path or INDEX_PATH
    data = _read_json(path)
    if data.get("schema") != "theory-master/regression-case-index/v1":
        raise RegressionCaseError(f"unsupported regression-case index: {path}")
    if not isinstance(data.get("cases"), list):
        raise RegressionCaseError(
            f"regression-case index requires a cases list: {path}"
        )
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


def _catalog_cases(root: Path, index_path: Path) -> list[RegressionCase]:
    _safe_member(root, ".")
    index = _load_index(_safe_member(root, index_path.relative_to(root)))
    result: list[RegressionCase] = []
    for entry in index.get("cases", []):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not entry["path"]
        ):
            raise RegressionCaseError(f"invalid case entry: {index_path}")
        case_root = _safe_member(root, entry["path"])
        meta = _read_json(_safe_member(case_root, "case.json"))
        ident = meta.get("id")
        if (
            meta.get("schema") != "theory-master/regression-case/v1"
            or not isinstance(ident, str)
            or not _normalize(ident)
            or entry.get("id", ident) != ident
        ):
            raise RegressionCaseError(f"invalid case identity: {case_root}")
        aliases = []
        for value in (meta.get("aliases", []), entry.get("aliases", [])):
            if not isinstance(value, list) or any(
                not isinstance(alias, str) or not _normalize(alias) for alias in value
            ):
                raise RegressionCaseError(f"invalid case aliases: {case_root}")
            aliases.extend(value)
        meta = {**meta, "aliases": list(dict.fromkeys(aliases))}
        result.append(RegressionCase(ident, case_root, meta))
    return result


def list_cases() -> list[RegressionCase]:
    """List official cases plus an explicitly configured, isolated user catalog.

    SCIVER_TEST_CASES_ROOT must name an absolute directory with index.json.
    Official Test0 and all its aliases remain reserved: duplicate normalized
    IDs/aliases across cases reject the catalog rather than replacing a case.
    """
    result = _catalog_cases(CASES_ROOT, INDEX_PATH)
    configured = os.environ.get(USER_CASES_ROOT_ENV, "").strip()
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            raise RegressionCaseError(
                f"{USER_CASES_ROOT_ENV} requires an absolute path"
            )
        result.extend(_catalog_cases(root, root / "index.json"))
    names: dict[str, str] = {}
    for case in result:
        for name in {_normalize(value) for value in (case.case_id, *case.aliases)}:
            if name in names:
                raise RegressionCaseError(
                    f"duplicate regression-case ID/alias {name!r}: {names[name]} and {case.root}"
                )
            names[name] = str(case.root)
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
            raise RegressionCaseError(
                f"unknown TheoryMaster regression case {selector!r}; known: {known}"
            )
        raise RegressionCaseError(
            f"ambiguous TheoryMaster regression case {selector!r}"
        )
    return matches[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_inputs(root: Path, manifest: dict[str, Any]) -> list[str]:
    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise RegressionCaseError("fixture source_manifest requires a files list")
    verified = []
    for item in rows:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise RegressionCaseError("invalid fixture input record")
        rel = Path(item["path"])
        path = _safe_member(root, rel)
        if not rel.parts or rel.parts[0] != "benchmark":
            raise RegressionCaseError(
                f"fixture inputs must stay under benchmark/: {rel}"
            )
        if not path.is_file():
            raise RegressionCaseError(f"missing fixture input: {rel}")
        size, digest = item.get("size"), item.get("sha256")
        if (
            not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise RegressionCaseError(f"fixture input requires size and SHA-256: {rel}")
        if path.stat().st_size != size:
            raise RegressionCaseError(f"fixture input size mismatch: {rel}")
        if _sha256(path) != digest:
            raise RegressionCaseError(f"fixture input SHA-256 mismatch: {rel}")
        verified.append(str(rel))
    return verified


def _source_manifest_relative(case: RegressionCase) -> Path:
    fixture = case.metadata.get("scientific_fixture", {})
    if not isinstance(fixture, dict):
        raise RegressionCaseError(f"invalid scientific_fixture metadata: {case.root}")
    reference = fixture.get("source_manifest", "benchmark/source_manifest.json")
    if not isinstance(reference, str) or not reference:
        raise RegressionCaseError(f"invalid fixture source_manifest path: {case.root}")
    relative = Path(reference)
    _safe_member(case.root, relative)
    if not relative.parts or relative.parts[0] != "benchmark":
        raise RegressionCaseError("fixture source_manifest must stay under benchmark/")
    return relative


def _submission_contract_relative(case: RegressionCase) -> Path | None:
    fixture = case.metadata.get("scientific_fixture", {})
    if not isinstance(fixture, dict):
        raise RegressionCaseError(f"invalid scientific_fixture metadata: {case.root}")
    if "submission_contract" not in fixture:
        return None
    reference = fixture["submission_contract"]
    if not isinstance(reference, str) or not reference:
        raise RegressionCaseError(
            "fixture submission_contract must be an explicit file reference"
        )
    relative = Path(reference)
    _safe_member(case.root, relative)
    if (
        len(relative.parts) < 2
        or relative.parts[0] != "benchmark"
        or relative.as_posix() != reference
        or "\\" in reference
    ):
        raise RegressionCaseError(
            "fixture submission_contract must stay under benchmark/"
        )
    return relative


def _verify_submission_contract(case: RegressionCase, root: Path) -> Path | None:
    reference = _submission_contract_relative(case)
    if reference is None:
        return None
    path = _safe_member(root, reference)
    if not path.is_file():
        raise RegressionCaseError(
            "fixture submission_contract must name a regular file"
        )
    from types import SimpleNamespace
    from .public_submission import load_submission_contract

    try:
        declaration = load_submission_contract(
            SimpleNamespace(
                submission_contract_ref=str(reference),
                benchmark_root=root / "benchmark",
            )
        )
        if declaration is None:
            raise ValueError("explicit fixture submission_contract was not loaded")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RegressionCaseError(
            f"invalid fixture submission_contract: {exc}"
        ) from exc
    return reference


def _configure_new_case_inputs(case: RegressionCase, binding):
    """Connect explicit fixture metadata only after creating a fresh task."""
    from .onboarding import install_benchmark_inputs
    from .runtime import write_json
    from .task import TaskBinding

    install_benchmark_inputs(binding)
    reference = _submission_contract_relative(case)
    if reference is None:
        return binding
    benchmark = binding.benchmark_root
    if benchmark is None:
        raise RegressionCaseError("new case inputs have no explicit benchmark root")
    copied = _safe_member(benchmark, reference.relative_to("benchmark"))
    if (
        not copied.is_file()
        or copied.read_bytes() != _safe_member(case.root, reference).read_bytes()
    ):
        raise RegressionCaseError(
            "new case submission_contract differs from its public fixture"
        )
    config = json.loads(binding.config_path.read_text(encoding="utf-8"))
    config["submission_contract"] = str(reference)
    write_json(binding.config_path, config)
    return TaskBinding.load(binding.task_home)


def verify_case(selector: str | int) -> dict[str, Any]:
    case = resolve_case(selector)
    plan_path = _safe_member(case.root, "research_plan.md")
    if not plan_path.is_file():
        raise RegressionCaseError(f"fixture plan is not a regular file: {plan_path}")
    plan = plan_path.read_text(encoding="utf-8")
    if "# Scientific Model" in plan or "# Frontier State" in plan:
        raise RegressionCaseError(
            f"{case.case_id} fixture contains runtime scientific state"
        )
    benchmark = _safe_member(case.root, "benchmark")
    for path in benchmark.rglob("*"):
        _safe_member(case.root, path.relative_to(case.root))
        if not path.is_file() and not path.is_dir():
            raise RegressionCaseError(f"fixture contains a non-regular input: {path}")
    manifest = _read_json(_safe_member(case.root, _source_manifest_relative(case)))
    verified = _verify_inputs(case.root, manifest)
    _verify_submission_contract(case, case.root)
    return {
        "id": case.case_id,
        "root": str(case.root),
        "files_verified": len(verified),
        "verified": verified,
    }


def materialize_case(selector: str | int, destination: Path) -> Path:
    case = resolve_case(selector)
    verify_case(case.case_id)
    destination = destination.expanduser().absolute()
    if any(part.is_symlink() for part in (destination, *destination.parents)):
        raise RegressionCaseError(
            f"fixture destination traverses a symlink: {destination}"
        )
    destination = destination.resolve()
    if destination.is_relative_to(case.root.resolve()):
        raise RegressionCaseError("destination must not be inside the fixture")
    if destination.exists() and any(destination.iterdir()):
        raise RegressionCaseError(f"destination must be absent or empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(case.plan_path, destination / "research_plan.md")
    shutil.copytree(case.benchmark_dir, destination / "benchmark", dirs_exist_ok=True)

    # Verify the copied public-input manifest too.  expected.json/case.json stay
    # outside the task root so baseline expectations cannot leak into science.
    manifest = _read_json(_safe_member(destination, _source_manifest_relative(case)))
    _verify_inputs(destination, manifest)
    reference = _verify_submission_contract(case, destination)
    if (
        reference is not None
        and (destination / reference).read_bytes()
        != (case.root / reference).read_bytes()
    ):
        raise RegressionCaseError(
            "materialized submission_contract differs from its public fixture"
        )
    return destination


def initialize_case(
    selector: str | int, destination: Path, *, executable: str = "theory_master"
) -> Path:
    case = resolve_case(selector)
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
    from .task import TaskBinding

    _configure_new_case_inputs(case, TaskBinding.load(root))
    return root


def create_test_instance(
    selector: str = "Test0",
    destination: Path | None = None,
    *,
    instances_root: Path | None = None,
    start: bool = True,
):
    """Create a fresh fixture task without inheriting scientific state or sessions."""
    from .task import bootstrap, task_id_scope_root
    from .specification import TaskSpec

    case = resolve_case(selector)
    if destination is None:
        if instances_root is None:
            from .onboarding import default_workspace

            configured = os.environ.get("SCIVER_TEST_ROOT", "").strip()
            search = os.environ.get("THEORY_MASTER_SEARCH_ROOT", "").strip()
            instances_root = (
                Path(configured).expanduser()
                if configured
                else (
                    Path(search).expanduser() / "test-instances"
                    if search
                    else default_workspace() / "test-instances"
                )
            )
        instances_root = instances_root.expanduser().resolve()
        instances_root.mkdir(parents=True, exist_ok=True)
        destination = Path(
            tempfile.mkdtemp(
                prefix=case.case_id.replace("_", "")
                + "-"
                + datetime.now().strftime("%Y%m%d-%H%M%S")
                + "-",
                dir=instances_root,
            )
        )
    root = materialize_case(case.case_id, destination)
    scope = task_id_scope_root(root / "research_plan.md")
    if scope == root or not root.is_relative_to(scope):
        scope = root.parent
    binding = bootstrap(root / "research_plan.md", allocation_root=scope)
    binding = _configure_new_case_inputs(case, binding)
    record = {
        "case": case.case_id,
        "task_id": binding.master_id,
        "task_home": str(root),
        "research_root": str(binding.research_root),
        "created_at": datetime.now().astimezone().isoformat(),
        "operation": "run",
    }
    (root / ".theory-master" / "test-instance.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    outcome = {"status": "prepared"}
    if start:
        from .runtime_manager import launch_managed_runtime

        outcome = launch_managed_runtime(
            TaskSpec.from_config(binding.config_path),
            operation="run",
            identifier=str(binding.master_id),
        )
        if outcome.get("status") != "started":
            raise RegressionCaseError(
                f"new test task {binding.master_id} was created but launch failed: {outcome.get('status')}"
            )
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
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise RegressionCaseError(
            f"{case.case_id} grader failure (exit {proc.returncode}): {(proc.stderr or proc.stdout)[-2000:]}"
        )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RegressionCaseError(
            f"{case.case_id} grader returned invalid JSON"
        ) from exc
    if result.get("id") != case.case_id or bool(result.get("passed")) != (
        proc.returncode == 0
    ):
        raise RegressionCaseError(f"{case.case_id} grader returned inconsistent status")
    return result


def _show(case: RegressionCase) -> dict[str, Any]:
    expected = _read_json(_safe_member(case.root, "expected.json")) if case.expected_path.exists() else None
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
    verify = sub.add_parser(
        "verify", help="verify fixture files against public SHA-256 manifest"
    )
    verify.add_argument("case")
    materialize = sub.add_parser(
        "materialize", help="copy a fresh science-only fixture into a task directory"
    )
    materialize.add_argument("case")
    materialize.add_argument("destination", type=Path)
    materialize.add_argument(
        "--initialize",
        action="store_true",
        help="also run 'theory_master new' after materializing",
    )
    materialize.add_argument("--tm-executable", default="theory_master")
    grade = sub.add_parser(
        "grade", help="evaluate finished submission offline (no LLM calls)"
    )
    grade.add_argument("case")
    grade.add_argument("submission", type=Path)

    args = parser.parse_args(argv)
    if args.command == "list":
        for case in list_cases():
            print(
                f"{case.case_id}\taliases={','.join(case.aliases)}\t{case.metadata.get('purpose', '')}"
            )
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
            initialize_case(
                case.case_id, args.destination, executable=args.tm_executable
            )
            if args.initialize
            else materialize_case(case.case_id, args.destination)
        )
        print(root)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
