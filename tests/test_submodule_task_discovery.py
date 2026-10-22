"""Standalone and nested repository task discovery must both work."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from theory_master import cli_discovery


class SubmoduleTaskDiscoveryTests(TestCase):
    def test_parent_research_repo_used_when_source_is_submodule(self):
        with TemporaryDirectory() as temp:
            superproject = Path(temp) / "example-project"
            submodule = superproject / "tool" / "theory_master"
            module = submodule / "theory_master" / "cli_discovery.py"
            module.parent.mkdir(parents=True)
            (superproject / ".git").mkdir()
            (superproject / "gs_study").mkdir()
            (submodule / ".git").write_text("gitdir: ../.git/modules/tool/theory_master\n")
            with mock.patch.object(cli_discovery, "__file__", str(module)), mock.patch.dict(
                "os.environ", {"THEORY_MASTER_SEARCH_ROOT": ""}
            ):
                self.assertEqual(cli_discovery.default_search_root(), superproject)

    def test_standalone_source_resolves_local_repository(self):
        with TemporaryDirectory() as temp:
            project = Path(temp) / "theory-master"
            module = project / "theory_master" / "cli_discovery.py"
            module.parent.mkdir(parents=True)
            (project / ".git").mkdir()
            with mock.patch.object(cli_discovery, "__file__", str(module)), mock.patch.dict(
                "os.environ", {"THEORY_MASTER_SEARCH_ROOT": ""}
            ):
                self.assertEqual(cli_discovery.default_search_root(), project)

    def test_explicit_search_root_takes_precedence(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.dict("os.environ", {"THEORY_MASTER_SEARCH_ROOT": str(root)}):
                self.assertEqual(cli_discovery.default_search_root(), root)
