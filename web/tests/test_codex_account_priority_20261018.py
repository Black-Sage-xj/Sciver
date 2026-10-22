"""Web switch must close only verified Desktop proxies and spare isolated Workers."""
from __future__ import annotations

import json
import os
import shutil
import signal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pytest

from tm_observer import codex_usage


def _fake_proc(root: Path, pid: int, *, argv: bytes, isolated: bool = False,
               exe_name="codex.real", uid=None) -> Path:
    proc=root/str(pid)
    proc.mkdir(parents=True)
    (proc/"cmdline").write_bytes(argv)
    (proc/"environ").write_bytes(
        b"THEORYMASTER_WORKER_CODEX_HOME=/tmp/scientific-worker\0" if isolated else b"")
    (proc/"status").write_text(f"Name:\tcodex\nUid:\t{os.geteuid()}  {os.geteuid()}  {os.geteuid()}  {os.geteuid()}\n"
                               if uid is None else f"Uid:\t{uid} {uid} {uid} {uid}\n")
    executable=root/exe_name
    if not executable.exists():executable.write_text("fake")
    (proc/"exe").symlink_to(executable)
    (proc/"stat").write_text(f"{pid} (codex) S 1 1 1\n")
    return proc


def test_desktop_proxy_shutdown_only_targets_verified_proxy():
    with TemporaryDirectory() as folder:
        proc=Path(folder)/"proc";proc.mkdir()
        proxy=_fake_proc(proc,9101,argv=b"/usr/bin/codex.real\0app-server\0proxy\0")
        cli=_fake_proc(proc,9102,argv=b"/usr/bin/codex.real\0exec\0foo\0")
        worker=_fake_proc(proc,9103,argv=b"/usr/bin/codex.real\0app-server\0proxy\0",
                          isolated=True)
        calls=[]
        def kill(pid, signal_num):
            calls.append((pid,signal_num))
            if pid==9101:shutil.rmtree(proxy)
        with mock.patch.object(codex_usage.os, "kill", side_effect=kill):
            # Worker proxy is intentionally excluded, not killed.
            assert codex_usage._close_desktop_proxies(proc,timeout_s=.05)==(9101,)
        assert (9101,signal.SIGTERM) in calls
        assert all(pid!=9102 and pid!=9103 for pid,_ in calls)


def test_private_worker_codex_exec_does_not_block_global_switch():
    with TemporaryDirectory() as folder:
        proc=Path(folder)/"proc";proc.mkdir()
        private=_fake_proc(proc,9101,argv=b"/usr/bin/codex.real\0exec\0",
                           isolated=True)
        codex_usage._assert_no_active_codex_sessions(proc)
        (private/"environ").write_bytes(b"")
        with pytest.raises(codex_usage.AccountSwitchConflict,match="CLI"):
            codex_usage._assert_no_active_codex_sessions(proc)


def test_unmanaged_daemon_allows_selector_only_with_verified_auth():
    from test_codex_usage import CodexSwitchSafetyTests
    suite=CodexSwitchSafetyTests()
    with TemporaryDirectory() as folder:
        first,second,selector,runtime=suite.fixture(Path(folder))
        with mock.patch.object(codex_usage,"DEFAULT_ACCOUNTS",(
                ("Codex 1",first),("Codex 2",second))), \
             mock.patch.object(codex_usage,"_close_desktop_proxies") as close_proxy, \
             mock.patch.object(codex_usage,"_assert_no_active_codex_sessions"), \
             mock.patch.object(codex_usage,"_assert_managed_app_server",
                               side_effect=codex_usage.AccountSwitchConflict("daemon missing")), \
             mock.patch.object(codex_usage,"_restart_direct_app_servers") as daemon_restart, \
             mock.patch.object(codex_usage,"_verify_selected_account") as verify:
            result=codex_usage.switch_active_account("account-2",selector=selector,
                                                      runtime_homes=(runtime,))
        assert result=="account-2"
        assert selector.resolve()==second.resolve()
        daemon_restart.assert_not_called()
        verify.assert_called_once_with(second,selector,(runtime,))
        close_proxy.assert_called_once()
