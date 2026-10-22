"""Generic optional provider security checks; no vendor-specific dependency."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theory_master.backend_config import resolve_routes
from theory_master.codex_backend import CodexBackend
from theory_master.provider_extensions import optional_provider_overrides


def _config(root: Path, body: str) -> Path:
    path = root / "provider_connections.toml"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)
    return path


class ProviderEnvironmentBoundaryTests(unittest.TestCase):
    def test_runtime_config_accepts_optional_provider_env_key(self):
        routes = resolve_routes(model="fixture-model@FixtureProvider", env={})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = _config(root, (
                '[model_providers.FixtureProvider]\n'
                'base_url = "https://example.test/v1"\n'
                'env_key = "FIXTURE_API_KEY"\n'
            ))
            with patch.dict(os.environ, {"FIXTURE_API_KEY": "fixture-token"}, clear=True), (
                    patch("theory_master.provider_extensions.local_provider_config", return_value=config)), (
                    patch.object(CodexBackend, "_env_file_values", return_value={})), (
                    patch("theory_master.codex_accounts.runtime_home", return_value=root)):
                overrides, environment = CodexBackend._runtime_config(routes)
        self.assertEqual(environment["FIXTURE_API_KEY"], "fixture-token")
        self.assertIn('model_providers.FixtureProvider.base_url="https://example.test/v1"', overrides)
        self.assertNotIn("fixture-token", " ".join(overrides))

    def test_runtime_config_fails_when_optional_key_missing(self):
        routes = resolve_routes(model="fixture-model@FixtureProvider", env={})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = _config(root, (
                '[model_providers.FixtureProvider]\n'
                'base_url = "https://example.test/v1"\n'
                'env_key = "FIXTURE_API_KEY"\n'
            ))
            with patch.dict(os.environ, {}, clear=True), (
                    patch("theory_master.provider_extensions.local_provider_config", return_value=config)), (
                    patch.object(CodexBackend, "_env_file_values", return_value={})), (
                    patch("theory_master.codex_accounts.runtime_home", return_value=root)):
                with self.assertRaisesRegex(RuntimeError, "Missing API key"):
                    CodexBackend._runtime_config(routes)

    def test_worker_uses_shared_provider_secret_without_commandline_leak(self):
        routes = resolve_routes(model="fixture-model@FixtureProvider", env={})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / "shared"
            shared.mkdir()
            config = _config(root, (
                '[model_providers.FixtureProvider]\n'
                'source_file = "extra.config.toml"\n'
                'env_key = "FIXTURE_API_KEY"\n'
            ))
            source = shared / "extra.config.toml"
            source.write_text(
                '[model_providers.FixtureProvider]\n'
                'base_url = "https://example.test/v1"\n'
                'experimental_bearer_token = "shared-fixture-token"\n'
            )
            source.chmod(0o600)
            with patch.dict(os.environ, {}, clear=True), (
                    patch("theory_master.provider_extensions.local_provider_config", return_value=config)), (
                    patch.object(CodexBackend, "_env_file_values", return_value={})), (
                    patch("theory_master.codex_accounts.runtime_home", return_value=shared)):
                overrides, environment = CodexBackend._runtime_config(routes)
            self.assertEqual(environment["FIXTURE_API_KEY"], "shared-fixture-token")
            self.assertNotIn("shared-fixture-token", " ".join(overrides))

    def test_unregistered_provider_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = _config(root, "[model_providers]\n")
            with patch("theory_master.provider_extensions.local_provider_config", return_value=config):
                with self.assertRaisesRegex(RuntimeError, "not explicitly configured"):
                    optional_provider_overrides({"FixtureProvider"}, {}, runtime_home=root)

    def test_insecure_local_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = _config(root, "[model_providers]\n")
            config.chmod(0o644)
            with patch("theory_master.provider_extensions.local_provider_config", return_value=config):
                with self.assertRaisesRegex(RuntimeError, "unsafe operator-owned"):
                    optional_provider_overrides({"FixtureProvider"}, {}, runtime_home=root)


if __name__ == "__main__":
    unittest.main()
