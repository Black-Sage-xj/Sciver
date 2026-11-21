"""Task-selected scientific Python, separate from the SDK's own interpreter."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .runtime import write_json


COMPUTE_PYTHON_ENV = "THEORYMASTER_COMPUTE_PYTHON"


def _available(path: Path, source: str) -> Path:
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RuntimeError(
            f"TheoryMaster compute interpreter is unavailable ({source}): {path}. "
            "Configure an executable task compute_python or THEORYMASTER_COMPUTE_PYTHON."
        )
    # Resolving a venv's bin/python symlink would discard its environment.
    return path.absolute()


def task_compute_python(config: Mapping[str, Any]) -> Path | None:
    """Validate only explicit task configuration; absence keeps legacy behavior."""
    if "compute_python" not in config:
        return None
    value = config["compute_python"]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("task compute_python must be a non-empty absolute path")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError("task compute_python must be an absolute path")
    return _available(path, "task_config")


@dataclass(frozen=True)
class ComputeEnvironment:
    python: Path
    source: str
    task_config_path: Path | None = None

    def provenance(self) -> dict[str, str]:
        result = {"selected_python": str(self.python), "source": self.source}
        if self.task_config_path is not None:
            result["task_config_path"] = str(self.task_config_path)
        if self.source == "environment":
            result["environment_key"] = COMPUTE_PYTHON_ENV
        return result


def resolve_compute_environment(task_config_path: Path | None = None) -> ComputeEnvironment:
    """Select task config > operator environment > established SDK default."""
    if task_config_path is not None:
        config_path = Path(task_config_path).expanduser().resolve()
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read task compute configuration: {config_path}") from exc
        if not isinstance(config, dict):
            raise ValueError("task compute configuration must be a JSON object")
        configured = task_compute_python(config)
        if configured is not None:
            return ComputeEnvironment(configured, "task_config", config_path)
    selected = os.environ.get(COMPUTE_PYTHON_ENV, "").strip()
    path = Path(selected).expanduser() if selected else Path.home() / "miniconda3" / "bin" / "python"
    source = "environment" if selected else "default"
    return ComputeEnvironment(_available(path, source), source)


_PROBE = (
    "import json, sys; print(json.dumps({"
    "'executable': sys.executable, 'version': sys.version, "
    "'implementation': sys.implementation.name, "
    "'prefix': sys.prefix, 'base_prefix': sys.base_prefix}))"
)


def probe_compute_environment(selection: ComputeEnvironment) -> dict[str, Any]:
    """Run a bounded local stdlib probe, without SDK or model access."""
    try:
        proc = subprocess.run(
            [str(selection.python), "-I", "-c", _PROBE],
            capture_output=True, text=True, timeout=10, check=True,
        )
        interpreter = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError(f"Scientific Python preflight failed: {selection.python}") from exc
    keys = ("executable", "version", "implementation", "prefix", "base_prefix")
    if not isinstance(interpreter, dict) or any(
            not isinstance(interpreter.get(key), str) or not interpreter[key] for key in keys):
        raise RuntimeError(f"Scientific Python returned an invalid preflight probe: {selection.python}")
    return {
        "schema": "theory-master/compute-environment-preflight/v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed",
        **selection.provenance(),
        "interpreter": {key: interpreter[key] for key in keys},
    }


def record_compute_preflight(root: Path, result: Mapping[str, Any]) -> Path:
    """Keep a separate receipt for each local compute probe, not scientific state."""
    parent = Path(root) / "runtime_preflight"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = parent / "compute-python"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.json"
    write_json(path, dict(result))
    return path
