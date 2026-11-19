"""Build the public Test0 fixture from its single canonical source copy."""
from pathlib import Path
import json
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


# Explicit allowlist: private/legacy cases and runtime state must never enter
# the wheel simply because they exist alongside the original teaching example.
PUBLIC_FIXTURE_FILES = (
    "index.json",
    "test_0/case.json",
    "test_0/research_plan.md",
    "test_0/expected.json",
    "test_0/PROTOCOL.md",
    "test_0/benchmark/instruction.md",
    "test_0/benchmark/source_manifest.json",
    "test_0/benchmark/submission_contract.json",
    "test_0/benchmark/datasets/brownian_2d/trajectories.csv",
    "test_0/evaluator/generate_data.py",
    "test_0/evaluator/grade.py",
    "test_0/evaluator/score_trajectory.py",
)


class PublicFixtureBuild(build_py):
    def run(self):
        output = Path(self.build_lib) / "theory_master"
        source_package = (Path(__file__).resolve().parent / "theory_master").resolve()
        resolved_output = output.resolve()
        if (output.is_symlink() or resolved_output.is_relative_to(source_package)
                or source_package.is_relative_to(resolved_output)):
            raise ValueError("build output must be separate from the source package")
        # setuptools reuses build/lib but does not remove deleted sources.
        # Recreate only this command's package output, preserving sibling
        # build products and preventing stale modules/resources entering wheels.
        if output.exists():
            shutil.rmtree(output)
        super().run()
        source = Path(__file__).resolve().parent / "regression_cases"
        target = Path(self.build_lib) / "theory_master/resources/regression_cases"
        index = json.loads((source / "index.json").read_text(encoding="utf-8"))
        index["cases"] = [entry for entry in index["cases"]
                          if entry["id"] == "test_0" and entry["path"] == "test_0"]
        if len(index["cases"]) != 1:
            raise ValueError("public fixture index must contain original test_0")
        for relative in PUBLIC_FIXTURE_FILES:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, destination)
        (target / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


setup(cmdclass={"build_py": PublicFixtureBuild})
