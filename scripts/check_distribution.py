#!/usr/bin/env python3
"""Prepare runtime dependencies, then build and cold-start wheels offline.

Requires local pip/setuptools/wheel. Dependency preparation uses the configured
package index unless --dependency-wheels supplies a local wheel directory.
No SDK provider, login or model calls are used. Run with the minimum supported
Python as part of release verification.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from urllib.request import urlopen
import venv
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def check_distribution(work: Path, *, dependency_wheels: Path | None = None) -> dict:
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    wheels = work / "wheels"
    wheels.mkdir()
    outside = work / "outside"
    outside.mkdir()
    home = work / "home"
    home.mkdir()
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith(("THEORY_MASTER", "THEORYMASTER", "SCIVER_", "CODEX_", "TM_OBSERVER")) or key in {"PYTHONPATH", "PYTHONHOME", "OPENAI_API_KEY"}:
            env.pop(key, None)
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_DATA_HOME=str(home / ".local/share"),
               THEORY_MASTER_SEARCH_ROOT=str(outside),
               THEORY_MASTER_DATA_ROOT=str(work / "data"),
               PIP_NO_INDEX="1", PYTHONNOUSERSITE="1")
    results = []

    runtime_dependencies = tomllib.loads((ROOT / "web/pyproject.toml").read_text())["project"].get("dependencies", [])
    runtime_dependencies = [requirement for requirement in runtime_dependencies if re.match(
        r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)[1].lower().replace("_", "-") != "sciver"]
    dependency_preparation = {"mode": "local-wheels" if dependency_wheels is not None else "package-index",
                              "index_access": bool(runtime_dependencies) and dependency_wheels is None,
                              "requirements": runtime_dependencies, "exit": 0, "wheel_files": []}
    if runtime_dependencies:
        command = [sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--dest", str(wheels)]
        if dependency_wheels is not None:
            dependency_wheels = dependency_wheels.resolve()
            if not dependency_wheels.is_dir():
                raise ValueError("--dependency-wheels must name a directory containing reviewed runtime wheels")
            command.extend(["--no-index", "--find-links", str(dependency_wheels)])
        command.extend(runtime_dependencies)
        # This outer preparation step uses normal pip configuration. Keep its
        # diagnostics private: index URLs may contain credentials. Only public
        # requirements, exit status and resulting wheel names are reported.
        download_env = dict(os.environ)
        download_env.pop("PIP_NO_INDEX", None)
        download_env.update(PIP_NO_INPUT="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
        try:
            prepared = subprocess.run(command, cwd=outside, env=download_env,
                                      capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Runtime dependency wheel preparation timed out") from None
        dependency_preparation.update(exit=prepared.returncode,
                                      wheel_files=sorted(path.name for path in wheels.glob("*.whl")))
        results.append({"command": command, "exit": prepared.returncode, "stage": "runtime-dependencies"})
        print(json.dumps({"runtime_dependency_preparation": dependency_preparation}), file=sys.stderr, flush=True)
        if prepared.returncode:
            raise RuntimeError("Runtime dependency wheel preparation failed; check pip configuration and compatible wheel availability")

    def run(command, *, cwd=outside):
        result = subprocess.run([str(arg) for arg in command], cwd=cwd, env=env,
                                capture_output=True, text=True, timeout=180)
        results.append({"command": [str(arg) for arg in command], "exit": result.returncode})
        if result.returncode:
            raise RuntimeError(f"{command!r} failed:\n{result.stdout}\n{result.stderr}")
        return result.stdout

    run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "--wheel-dir", wheels, ROOT, ROOT / "web"])
    artifacts = sorted(path for path in wheels.glob("*.whl")
                       if path.name.startswith(("sciver-", "tm_observer-")))
    dependency_artifacts = sorted(path for path in wheels.glob("*.whl") if path not in artifacts)
    assert len(artifacts) == 2, artifacts
    for artifact in artifacts:
        with zipfile.ZipFile(artifact) as archive:
            names = archive.namelist()
            metadata = archive.read(next(name for name in names if name.endswith(".dist-info/METADATA"))).decode()
            assert "Requires-Python: >=3.12" in metadata
            assert not any("config/local/" in name or "/model_profiles/y-" in name for name in names)
            package = "theory_master" if artifact.name.startswith("sciver-") else "tm_observer"
            for name in names:
                if name.endswith("/") or ".dist-info/" in name:
                    continue
                assert name.startswith(package + "/"), f"unexpected wheel member: {name}"
                if name.startswith("theory_master/resources/regression_cases/"):
                    relative = name.removeprefix("theory_master/resources/regression_cases/")
                    source = ROOT / "regression_cases" / relative
                    if relative == "index.json":
                        # The public index is intentionally filtered by the build.
                        continue
                elif name == "tm_observer/resources/WORKFLOW_STAGE_PREVIEW_GUIDE.md":
                    source = ROOT / "web/WORKFLOW_STAGE_PREVIEW_GUIDE.md"
                else:
                    source = (ROOT if package == "theory_master" else ROOT / "web") / name
                assert source.is_file(), f"stale wheel member has no current source: {name}"
                assert archive.read(name) == source.read_bytes(), f"wheel differs from current source: {name}"
            if artifact.name.startswith("sciver-"):
                prefix = "theory_master/resources/regression_cases/"
                fixture = {name.removeprefix(prefix) for name in names if name.startswith(prefix)}
                assert fixture and all(name == "index.json" or name.startswith("test_0/") for name in fixture)
                index = json.loads(archive.read(prefix + "index.json"))
                assert [case["id"] for case in index["cases"]] == ["test_0"]
            else:
                for relative in ("research_chat_dist/research-chat.js", "research_chat_dist/research-chat.css",
                                 "research_chat_dist/research-chat.woff2", "vendor/loquix-LICENSE.txt",
                                 "workflow_versions/2026-10-07-pre-audit-fix.json",
                                 "resources/WORKFLOW_STAGE_PREVIEW_GUIDE.md"):
                    assert "tm_observer/" + relative in names, relative
    environment = work / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / "bin/python"
    run([python, "-m", "pip", "install", "--no-index", "--find-links", wheels, "tm-observer==0.1.1"])
    sciver = environment / "bin/sciver"
    observer = environment / "bin/tm-observer"
    run([sciver, "--help"])
    run([sciver, "smoke"])
    run([observer, "--help"])
    run([sciver, "test", "--list"])
    run([python, "-m", "theory_master.regression_cases", "verify", "Test0"])
    for _ in range(2):
        run([sciver, "test", "Test0", "--prepare-only", "--root", outside / "instances"])
    checks = run([python, "-c", '''
import json
from pathlib import Path
from theory_master.task import TaskBinding
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.scientific_model import load_from_paths
from theory_master.regression_cases import resolve_case, grade_case
from theory_master.specification import make_run_id
import theory_master, tm_observer
import PIL, pypdf
bindings = [TaskBinding.load(path) for path in Path("instances").iterdir() if (path / ".theory-master/task.json").is_file()]
assert len(bindings) == 2
assert len({b.master_id for b in bindings}) == 2
assert len({b.research_root for b in bindings}) == 2
for b in bindings:
    assert make_run_id(research_root=b.research_root, task_mode=True) == "1"
    assert not list(b.research_root.glob("*/research_state.json"))
    assert not list(b.research_root.rglob("*thread*.json*"))
    model = load_from_paths(b.research_root, b.task_home / "research_plan.md", goal_id=b.goal_id)
    assert (model["version"], model["summary"]) == (0, "")
    graph_path = Path(b.config.get("graph_database") or b.branch_canvas.with_name("science_graph.sqlite3"))
    with ScienceGraphStore(graph_path, readonly=True) as graph:
        assert graph.db.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 0
    for private in ("expected.json", "case.json", "evaluator"):
        assert not (b.task_home / private).exists()
case = resolve_case("Test0")
assert "site-packages" in str(case.root)
truth = json.loads(case.expected_path.read_text())["diffusion_coefficient"]
submission = Path("submission.json")
submission.write_text(json.dumps({"D_estimate": truth}))
assert grade_case("Test0", submission)["passed"]
submission.write_text(json.dumps({"D_estimate": -1}))
assert not grade_case("Test0", submission)["passed"]
print(json.dumps({"task_ids": [b.master_id for b in bindings], "scientific_state": "empty", "next_cycle": 1,
                  "research_roots": [str(b.research_root) for b in bindings],
                  "goal": case.plan_path.read_text().split("# Research Contract", 1)[0].removeprefix("# Goal").strip(),
                  "core": theory_master.__file__, "web": tm_observer.__file__, "fixture": str(case.root),
                  "runtime_dependencies": {"Pillow": PIL.__version__, "pypdf": pypdf.__version__}}))
'''])
    installed_checks = json.loads(checks)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    endpoints = []
    prepared_tasks = []
    with (work / "server.log").open("w") as log:
        server = subprocess.Popen([str(observer), "serve", "--search-root", str(outside),
                                   "--state-dir", str(work / "state"), "--port", str(port)],
                                  cwd=outside, env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 25
            while True:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if server.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("installed observer did not start; see server.log")
                    time.sleep(.1)
            expected = dict(zip(map(str, installed_checks["task_ids"]), installed_checks["research_roots"]))
            deadline = time.monotonic() + 15
            while True:
                with urlopen(f"http://127.0.0.1:{port}/observer_state.json", timeout=20) as response:
                    state = json.load(response)
                discovered = {str(row.get("run_id")): row for row in state.get("runs", [])}
                if all(ident in discovered and discovered[ident].get("research_root") == root
                       for ident, root in expected.items()):
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError(f"prepared tasks are missing from observer_state.json: {expected}")
                time.sleep(.2)
            endpoints.append({"path": "/observer_state.json", "status": 200})
            for task_id, research_root in expected.items():
                row = discovered[task_id]
                assert row.get("cycle") is None and row.get("cycle_dir") is None, row
                ident = row["observer_id"]
                for path in (f"/research-plan/?observer_id={ident}", f"/run/{ident}/"):
                    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as response:
                        assert response.status == 200
                        assert response.read(), path
                        endpoints.append({"path": path, "status": response.status})
                path = f"/api/run/{ident}/research-plan"
                with urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as response:
                    assert response.status == 200
                    plan = json.load(response)
                    goal = next(section["text"] for section in plan["sections"] if section["name"] == "Goal")
                    assert goal.strip() == installed_checks["goal"], goal
                    assert plan["observer_id"] == ident
                    endpoints.append({"path": path, "status": response.status})
                path = f"/api/canvas-md/tasks/{ident}/index"
                with urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as response:
                    assert response.status == 200
                    graph = json.load(response)
                    assert graph["observer_id"] == ident
                    assert graph["nodes"] == [] and graph["edges"] == [], graph
                    endpoints.append({"path": path, "status": response.status})
                prepared_tasks.append({"task_id": task_id, "observer_id": ident,
                                       "research_root": research_root, "goal_verified": True,
                                       "empty_graph_verified": True, "cycle": None})
            for path in ("/", "/settings/", "/research-chat/", "/architecture/", "/workflow-stage-preview/",
                         "/favicon.svg", "/research-chat/widget.js", "/vendor/loquix-incident.js",
                         "/research-chat/assets/research-chat.js", "/research-chat/assets/research-chat.css",
                         "/research-chat/assets/research-chat.woff2"):
                with urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as response:
                    assert response.status == 200
                    assert response.read(), path
                    endpoints.append({"path": path, "status": response.status})
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
    result = {"python": sys.version, "artifacts": [str(path) for path in artifacts],
              "dependency_artifacts": [str(path) for path in dependency_artifacts],
              "dependency_preparation": dependency_preparation,
              "commands": results, "installed_checks": installed_checks, "endpoints": endpoints,
              "prepared_tasks": prepared_tasks,
              "model_calls": 0}
    (work / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, help="new directory in which to keep wheels, logs and result.json")
    parser.add_argument("--dependency-wheels", type=Path,
                        help="reviewed local runtime wheel directory; disables network dependency preparation")
    args = parser.parse_args()
    if args.work_dir:
        result = check_distribution(args.work_dir, dependency_wheels=args.dependency_wheels)
    else:
        with tempfile.TemporaryDirectory(prefix="sciver-distribution-") as directory:
            result = check_distribution(Path(directory), dependency_wheels=args.dependency_wheels)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
