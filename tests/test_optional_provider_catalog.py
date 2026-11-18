"""Offline route compatibility for explicitly registered optional providers."""
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from theory_master import backend_config as routes
from theory_master.codex_backend import CodexBackend
from theory_master.provider_extensions import configured_optional_provider_ids
from theory_master.worker_runtime import worker_route_overrides


class OptionalProviderCatalogTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = self.root / "provider_connections.toml"
        self.enterContext(patch.dict(os.environ, {"HOME": str(self.root)}, clear=True))
        self.enterContext(patch("theory_master.provider_extensions.local_provider_config",
                                return_value=self.config))
        self.enterContext(patch.object(CodexBackend, "_env_file_values",
                                      side_effect=AssertionError("no credential reads")))
        self.enterContext(patch.object(CodexBackend, "__init__",
                                      side_effect=AssertionError("no model clients")))
        self.enterContext(patch("socket.create_connection",
                                side_effect=AssertionError("no network")))
        self.enterContext(patch("urllib.request.urlopen",
                                side_effect=AssertionError("no network")))

    def register(self, text='[model_providers.FixtureProvider]\nsource_file = "absent.toml"\n'):
        self.config.write_bytes(text if isinstance(text, bytes) else text.encode())
        self.config.chmod(0o600)

    def optional_profile(self, provider="FixtureProvider"):
        profile = {role: routes.RoleRoute("fixture-model", provider,
                                         reasoning_effort="medium")
                   for role in routes.ROUTE_ROLES}
        return patch.dict(routes.MODEL_PROFILES, {"fixture": profile})

    def save_routes(self, model="fixture-model", provider="FixtureProvider"):
        roles = {"investigator": {"model": model, "provider": provider,
                                  "reasoning_effort": "medium"}}
        guard = self.root / ".theory-master"
        worker = guard / "workers/worker-1"
        worker.mkdir(parents=True, exist_ok=True)
        (guard / "web-role-routes.json").write_text(json.dumps({
            "schema": "theory-master/web-role-routes/v1", "roles": roles}))
        (worker / "role_routes.json").write_text(json.dumps({
            "schema": "theory-master/worker-role-routes/v1", "roles": roles}))

    def test_registry_ids_do_not_resolve_credentials_or_source_files(self):
        self.register()
        self.assertEqual(configured_optional_provider_ids(), frozenset({"FixtureProvider"}))
        self.assertFalse((self.root / "absent.toml").exists())

    def test_missing_registry_has_no_optional_providers(self):
        self.assertEqual(configured_optional_provider_ids(), frozenset())

    def test_registry_shape_and_provider_identifiers_are_validated(self):
        for body in ('model_providers = []\n',
                     '[model_providers."Bad-Id"]\n',
                     'model_providers.FixtureProvider = "invalid"\n',
                     '[model_providers.FixtureProvider]\nunexpected = true\n'):
            with self.subTest(body=body):
                self.register(body)
                with self.assertRaisesRegex(RuntimeError, "invalid optional provider"):
                    configured_optional_provider_ids()

    def test_insecure_registry_and_file_symlink_are_rejected(self):
        self.register()
        self.config.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, "unsafe operator-owned"):
            configured_optional_provider_ids()
        self.config.chmod(0o600)
        link = self.root / "registry-link.toml"
        link.symlink_to(self.config)
        with patch("theory_master.provider_extensions.local_provider_config", return_value=link):
            with self.assertRaisesRegex(RuntimeError, "unsafe operator-owned"):
                configured_optional_provider_ids()

    def test_parent_directory_link_keeps_profiles_and_registry_readable(self):
        self.register()
        profiles = self.root / "model_profiles"
        profiles.mkdir()
        (profiles / "fixture.toml").write_text("\n".join(
            f'[roles.{role}]\nmodel = "fixture-model"\nprovider = "FixtureProvider"\n'
            for role in routes.ROUTE_ROLE_ORDER))
        link = self.root / "directory-link"
        link.symlink_to(self.root, target_is_directory=True)
        with patch("theory_master.provider_extensions.local_provider_config",
                   return_value=link / self.config.name), patch.object(
                       routes, "LOCAL_MODEL_PROFILE_DIR", link / "model_profiles"):
            self.assertEqual(configured_optional_provider_ids(), frozenset({"FixtureProvider"}))
            self.assertIn("fixture", routes._load_model_profiles())

    def test_registered_profile_restores_saved_task_and_worker_overrides(self):
        baseline = routes.task_route_catalog()
        self.register()
        self.save_routes()
        with self.optional_profile():
            catalog = routes.task_route_catalog()
            self.assertTrue(all(row in catalog for row in baseline))
            self.assertIn({"model": "fixture-model", "provider": "FixtureProvider"}, catalog)
            self.assertEqual(routes.read_task_role_overrides(self.root)["investigator"].provider,
                             "FixtureProvider")
            self.assertEqual(worker_route_overrides(self.root, "worker-1")["investigator"].provider,
                             "FixtureProvider")

    def test_unregistered_profile_cannot_restore_saved_overrides(self):
        self.save_routes()
        with self.optional_profile():
            self.assertNotIn({"model": "fixture-model", "provider": "FixtureProvider"},
                             routes.task_route_catalog())
            with self.assertRaisesRegex(ValueError, "unknown task role"):
                routes.read_task_role_overrides(self.root)
            with self.assertRaisesRegex(ValueError, "unsupported Worker"):
                worker_route_overrides(self.root, "worker-1")

    def test_registration_does_not_add_routes_without_a_profile(self):
        baseline = routes.task_route_catalog()
        self.register()
        self.assertEqual(routes.task_route_catalog(), baseline)

    def test_other_provider_profiles_remain_rejected(self):
        self.register()
        with self.optional_profile("UnregisteredProvider"):
            self.assertFalse(any(row["provider"] == "UnregisteredProvider"
                                 for row in routes.task_route_catalog()))

    def test_invalid_optional_registry_preserves_saved_account_routes(self):
        baseline = routes.task_route_catalog()
        account = next(row for row in baseline if row["provider"] in routes.ACCOUNT_PROVIDERS)
        for body, mode in (("[malformed\n", 0o600), (b"\xff", 0o600),
                           ('[model_providers.FixtureProvider]\n', 0o644)):
            with self.subTest(body=body, mode=mode):
                self.register(body)
                self.config.chmod(mode)
                with self.assertRaises((RuntimeError, UnicodeError, tomllib.TOMLDecodeError)):
                    configured_optional_provider_ids()
                with self.optional_profile():
                    self.assertEqual(routes.task_route_catalog(), baseline)
                    self.save_routes(account["model"], account["provider"])
                    self.assertEqual(routes.read_task_role_overrides(self.root)["investigator"].provider,
                                     account["provider"])
                    self.assertEqual(worker_route_overrides(self.root, "worker-1")["investigator"].provider,
                                     account["provider"])
                    self.save_routes()
                    with self.assertRaisesRegex(ValueError, "unknown task role"):
                        routes.read_task_role_overrides(self.root)
                    with self.assertRaisesRegex(ValueError, "unsupported Worker"):
                        worker_route_overrides(self.root, "worker-1")


if __name__ == "__main__":
    unittest.main()
