"""Release smoke exercises installed artifacts rather than editable imports."""
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_installed_wheels_cold_start(tmp_path):
    check = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/check_distribution.py"))
    result = check["check_distribution"](tmp_path)
    assert len(set(result["installed_checks"]["task_ids"])) == 2
    assert result["endpoints"]


@pytest.mark.parametrize("web", [False, True], ids=["sdk", "observer"])
def test_repeated_wheel_build_removes_deleted_package_files(tmp_path, web):
    source = ROOT / "web" if web else ROOT
    project = tmp_path / "project"
    project.mkdir()
    for name in ("setup.py", "pyproject.toml"):
        shutil.copyfile(source / name, project / name)
    if web:
        shutil.copyfile(source / "WORKFLOW_STAGE_PREVIEW_GUIDE.md", project / "WORKFLOW_STAGE_PREVIEW_GUIDE.md")
    else:
        (project / "README.md").write_text("Build regression fixture\n")
        shutil.copytree(source / "regression_cases/test_0", project / "regression_cases/test_0")
        shutil.copyfile(source / "regression_cases/index.json", project / "regression_cases/index.json")
    package = "tm_observer" if web else "theory_master"
    home = project / package
    home.mkdir()
    (home / "__init__.py").write_text("")
    deleted_module = home / "deleted_module.py"
    deleted_resource = home / ("static/deleted.svg" if web else "model_profiles/test.toml")
    deleted_resource.parent.mkdir()
    deleted_module.write_text("STALE_MODULE = True\n")
    deleted_resource.write_text("stale resource\n")

    def build(destination):
        result = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps",
                                 "--no-build-isolation", "--wheel-dir", str(destination), str(project)],
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        with zipfile.ZipFile(next(destination.glob("*.whl"))) as wheel:
            return set(wheel.namelist())

    module_name = deleted_module.relative_to(project).as_posix()
    resource_name = deleted_resource.relative_to(project).as_posix()
    first = build(tmp_path / "first")
    assert {module_name, resource_name} <= first
    deleted_module.unlink()
    deleted_resource.unlink()
    # Reuse the *same* build directory as an ordinary repeated pip wheel call.
    # Both stale outputs must actually exist before the second build.
    assert (project / "build/lib" / module_name).is_file()
    assert (project / "build/lib" / resource_name).is_file()
    second = build(tmp_path / "second")
    assert module_name not in second
    assert resource_name not in second
    assert package + "/__init__.py" in second
    generated = ("tm_observer/resources/WORKFLOW_STAGE_PREVIEW_GUIDE.md" if web else
                 "theory_master/resources/regression_cases/index.json")
    assert generated in second

    sibling = project / "build/lib/unrelated-build-product.txt"
    sibling.write_text("preserve sibling build output\n")
    build(tmp_path / "third")
    assert sibling.read_text() == "preserve sibling build output\n"
    # A custom output directory must never turn cleanup into source deletion.
    result = subprocess.run([sys.executable, "setup.py", "build_py", "--build-lib", "."],
                            cwd=project, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "build output must be separate" in result.stderr
    assert (home / "__init__.py").is_file()
    output = project / "build/lib" / package
    shutil.rmtree(output)
    unrelated = tmp_path / "unrelated-directory"
    unrelated.mkdir()
    sentinel = unrelated / "keep.txt"
    sentinel.write_text("preserve symlink destination\n")
    output.symlink_to(unrelated, target_is_directory=True)
    result = subprocess.run([sys.executable, "setup.py", "build_py"], cwd=project,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "build output must be separate" in result.stderr
    assert sentinel.read_text() == "preserve symlink destination\n"
