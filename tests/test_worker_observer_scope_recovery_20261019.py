"""Deterministic regression: detached Worker service and orphan checkpoint recovery."""
from pathlib import Path
from unittest.mock import patch
import io
from theory_master.worker_runtime import _launch_detached_service


def test_serviced_launch_uses_independent_systemd_service(tmp_path: Path):
    env = {"PYTHONPATH": "/tmp/example"}
    with patch("theory_master.worker_runtime.subprocess.run") as invoke:
        invoke.return_value.returncode = 0
        _launch_detached_service(["/usr/bin/sleep", "1"], root=tmp_path, env=env,
                       output=io.StringIO(), kind="worker-2")
    args = invoke.call_args.args[0]
    assert args[:3] == ["systemd-run", "--user", "--collect"]
    assert args[-2:] == ["/usr/bin/sleep", "1"]
    assert "--working-directory" in args
    assert invoke.call_args.kwargs["env"] is env
    assert invoke.call_args.kwargs["stdin"] is not None


def test_serviced_launch_failure_is_not_success(tmp_path: Path):
    with patch("theory_master.worker_runtime.subprocess.run", side_effect=FileNotFoundError("systemd-run")):
        try:
            _launch_detached_service(["/usr/bin/sleep", "1"], root=tmp_path, env={},
                           output=io.StringIO(), kind="worker-2")
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("missing systemd launcher must fail closed")
