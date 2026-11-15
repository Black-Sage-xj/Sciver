"""Include the workflow guide in ordinary installed observer builds."""
from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class WorkflowGuideBuild(build_py):
    def run(self):
        output = Path(self.build_lib) / "tm_observer"
        source_package = (Path(__file__).resolve().parent / "tm_observer").resolve()
        resolved_output = output.resolve()
        if (output.is_symlink() or resolved_output.is_relative_to(source_package)
                or source_package.is_relative_to(resolved_output)):
            raise ValueError("build output must be separate from the source package")
        if output.exists():
            shutil.rmtree(output)
        super().run()
        source = Path(__file__).resolve().parent / "WORKFLOW_STAGE_PREVIEW_GUIDE.md"
        target = Path(self.build_lib) / "tm_observer/resources" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


setup(cmdclass={"build_py": WorkflowGuideBuild})
