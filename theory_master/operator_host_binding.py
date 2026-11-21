"""Host-owned SDK binding for operator controls that may launch protected Tasks."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[1]


class HostBindingRejected(ValueError):
    pass


@dataclass(frozen=True)
class HostSdkBinding:
    sdk_root: Path
    sandbox_helper_dir: Path | None = None

    def environment(self, *, protected: bool) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.sdk_root)
        if protected:
            env["PYTHONSAFEPATH"] = "1"
            if self.sandbox_helper_dir is not None:
                prefix = str(self.sandbox_helper_dir)
                inherited = env.get("PATH", "")
                env["PATH"] = os.pathsep.join(dict.fromkeys([prefix, *filter(None, inherited.split(os.pathsep))]))
        return env


def _canonical_dir(value: object, error: str) -> Path:
    if not isinstance(value, str) or os.pathsep in value:
        raise HostBindingRejected(error)
    path = Path(value)
    if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_dir():
        raise HostBindingRejected(error)
    return path


def _canonical_file(path: Path) -> bool:
    return path.is_file() and path.resolve() == path


def load_host_sdk_binding() -> HostSdkBinding | None:
    """Read the private operator-owned binding with bounded, no-link access."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    if not config_home.is_absolute():
        raise HostBindingRejected("Supervisor control configuration must be absolute")
    binding = config_home / "theory-master" / "supervisor-control.toml"
    try:
        descriptor = os.open(binding, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise HostBindingRejected("unsafe Supervisor control configuration")
        content = stream.read(4097)
    if len(content) > 4096:
        raise HostBindingRejected("Supervisor control configuration is too large")
    config = tomllib.loads(content.decode("utf-8"))
    if "sdk_root" not in config or not config.keys() <= {"sdk_root", "sandbox_helper_dir"}:
        raise HostBindingRejected("invalid Supervisor control configuration")
    sdk_root = _canonical_dir(config["sdk_root"], "invalid Supervisor SDK checkout")
    package = sdk_root / "theory_master"
    if any(not _canonical_file(package / filename) for filename in ("operator_control.py", "__init__.py")):
        raise HostBindingRejected("invalid Supervisor SDK checkout")
    helper_dir = None
    if "sandbox_helper_dir" in config:
        helper_dir = _canonical_dir(config["sandbox_helper_dir"], "invalid protected sandbox helper directory")
        info = helper_dir.stat()
        helper = helper_dir / "codex-linux-sandbox"
        if (info.st_uid not in {0, os.getuid()} or info.st_mode & 0o022
                or not helper.is_file() or not os.access(helper, os.X_OK)):
            raise HostBindingRejected("invalid protected sandbox helper directory")
    return HostSdkBinding(sdk_root, helper_dir)


def protected_launch_binding(config_path: Path) -> HostSdkBinding | None:
    """Return the authoritative launch checkout for an opted-in task."""
    config = json.loads(config_path.read_bytes())
    if not isinstance(config, dict):
        raise HostBindingRejected("invalid task configuration for protected launch")
    if "prequalification_holdout" not in config:
        return None
    binding = load_host_sdk_binding() or HostSdkBinding(SDK_ROOT)
    package = binding.sdk_root / "theory_master"
    if any(not _canonical_file(package / name) for name in ("prequalification_holdout.py", "operator_host_binding.py")):
        raise HostBindingRejected("Protected launch requires an SDK checkout with prequalification holdout enforcement")
    return binding


def verify_protected_scheduler(research_root: Path, binding: HostSdkBinding) -> None:
    """A queued request must not wake a resident legacy scheduler."""
    from .runtime_manager import process_identity_alive

    registry = research_root / ".theory-master" / "worker_scheduler.json"
    try:
        state = json.loads(registry.read_bytes())
    except FileNotFoundError:
        return
    if not isinstance(state, dict):
        raise HostBindingRejected("Invalid Worker Scheduler owner for protected launch")
    if not process_identity_alive(state.get("pid"), state.get("start_ticks")):
        return
    provenance = state.get("runtime_source_provenance")
    if (isinstance(provenance, dict) and provenance.get("source_root") == str(binding.sdk_root)
            and provenance.get("source_coherent") is True and isinstance(provenance.get("module_paths"), dict)
            and isinstance(provenance.get("module_sha256"), dict)):
        for name in ("codex_backend.py", "codex_sdk_compat.py"):
            file = binding.sdk_root / "theory_master" / name
            if (provenance["module_paths"].get(name) != str(file)
                    or provenance["module_sha256"].get(name) != hashlib.sha256(file.read_bytes()).hexdigest()):
                break
        else:
            return
    raise HostBindingRejected("Live Worker Scheduler must be on the protected SDK before queuing new work")
