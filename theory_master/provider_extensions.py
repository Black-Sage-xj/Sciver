"""Optional, operator-owned model provider configurations.

Public SDK knows only this neutral TOML contract. Each optional provider
requires explicit local configuration; no third-party account credentials,
names, URLs or billing API integrations belong in the distribution.
"""
from __future__ import annotations

import json
from functools import lru_cache
import os
import re
import stat
import tomllib
from pathlib import Path
from typing import Mapping


_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,95}\Z")
_ALLOWED = {"base_url", "source_file", "wire_api", "env_key", "requires_openai_auth",
            "supports_websockets", "name"}


def local_provider_config() -> Path:
    """Prefer user config, with a Git-ignored development overlay."""
    user = Path.home() / ".config" / "theory-master" / "provider_connections.toml"
    if user.is_file():
        return user
    return Path(__file__).resolve().parents[1] / "config" / "local" / "provider_connections.toml"


def _private_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    info = path.lstat()
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode)
            or info.st_size > 131072 or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise RuntimeError("unsafe operator-owned provider configuration")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def optional_provider_overrides(
    providers: set[str], stored: Mapping[str, str], *, runtime_home: Path
) -> tuple[list[str], dict[str, str]]:
    """Return nonsecret Codex SDK settings plus runtime-only token environment.

    The configuration file must be owned by the operator and mode 0600.
    Tokens are NEVER inserted into config overrides (which can be logged).
    """
    if not providers:
        return [], {}
    registry = _private_toml(local_provider_config()).get("model_providers", {})
    if not isinstance(registry, dict):
        raise RuntimeError("invalid optional provider registry")
    if providers - registry.keys():
        raise RuntimeError("optional model provider is not explicitly configured")
    result: list[str] = []
    environment: dict[str, str] = {}
    for provider in sorted(providers & registry.keys()):
        spec = registry[provider]
        if not isinstance(spec, dict) or not _ID.fullmatch(provider) or set(spec) - _ALLOWED:
            raise RuntimeError("invalid optional provider definition")
        source_name = spec.get("source_file")
        source = {}
        if source_name:
            if (not isinstance(source_name, str) or not re.fullmatch(r"[\w.-]+\.toml", source_name)
                    or source_name.startswith(".")):
                raise RuntimeError("invalid provider source filename")
            source = _private_toml(runtime_home / source_name).get("model_providers", {}).get(provider, {})
            if not isinstance(source, dict):
                raise RuntimeError("invalid local provider source")
        base_url = spec.get("base_url") or source.get("base_url")
        env_key = spec.get("env_key")
        wire_api = spec.get("wire_api") or source.get("wire_api") or "responses"
        if (not isinstance(base_url, str) or not base_url.startswith("https://")
                or any(c in base_url for c in ("\n", "\r", "\t", " "))
                or not isinstance(env_key, str) or not _ENV.fullmatch(env_key)
                or wire_api not in {"responses", "chat"}):
            raise RuntimeError("invalid optional provider endpoint or credential mapping")
        token = (os.environ.get("THEORYMASTER_" + env_key) or os.environ.get(env_key)
                 or stored.get("THEORYMASTER_" + env_key) or stored.get(env_key)
                 or source.get("experimental_bearer_token"))
        if not isinstance(token, str) or not token.strip():
            raise RuntimeError("Missing API key for configured optional provider " + provider)
        environment[env_key] = token
        label = spec.get("name", provider)
        if not isinstance(label, str) or len(label) > 100:
            raise RuntimeError("invalid optional provider label")
        auth = spec.get("requires_openai_auth", False)
        websockets = spec.get("supports_websockets", False)
        if type(auth) is not bool or type(websockets) is not bool:
            raise RuntimeError("invalid optional provider flags")
        key = "model_providers." + provider + "."
        result.extend((
            key + "name=" + json.dumps(label),
            key + "base_url=" + json.dumps(base_url),
            key + "wire_api=" + json.dumps(wire_api),
            key + "env_key=" + json.dumps(env_key),
            key + "requires_openai_auth=" + str(auth).lower(),
            key + "supports_websockets=" + str(websockets).lower(),
        ))
    return result, environment


@lru_cache(maxsize=1)
def optional_account_quota_plugin():
    """Load an operator-owned quota monitor when explicitly installed locally.

    Public wheels do not ship this module. Its absence disables quota
    monitoring without affecting public Services availability.
    """
    from importlib.util import module_from_spec, spec_from_file_location
    path = Path(__file__).resolve().parents[1] / "config" / "local" / "account_quota_extension.py"
    if not path.is_file() or path.is_symlink():
        return None
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeError("unsafe optional account quota extension")
    spec = spec_from_file_location("_sciver_optional_account_quota", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load optional account quota extension")
    plugin = module_from_spec(spec)
    spec.loader.exec_module(plugin)
    if not callable(getattr(plugin, "read_snapshot", None)) or not callable(getattr(plugin, "watch", None)):
        raise RuntimeError("optional account quota extension lacks required API")
    return plugin
