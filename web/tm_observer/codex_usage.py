from __future__ import annotations

import fcntl
import json
import os
import select
import signal
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


# Account locations are local to each collaborator; none belong in the Git repo.
_ACCOUNT_HOME = Path(os.environ.get("THEORYMASTER_ACCOUNT_HOME") or Path.home() / ".codex-accounts")
DEFAULT_ACCOUNTS = (
    ("Codex 1", _ACCOUNT_HOME / "account-1"),
    ("Codex 2", _ACCOUNT_HOME / "account-2"),
)
DEFAULT_CODEX_BIN = Path(os.environ.get("THEORYMASTER_CODEX_BIN") or Path.home() / "bin/codex.real")
ACCOUNT_SELECTOR = _ACCOUNT_HOME / "current"
SHARED_RUNTIME_HOMES = (
    Path.home() / ".codex",
    Path.home() / ".codex-theory-master",
)


def _rpc_request(
    codex_home: Path,
    method: str,
    params: dict[str, Any],
    *,
    codex_bin: Path = DEFAULT_CODEX_BIN,
    timeout_s: float = 8.0,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    proc = subprocess.Popen(
        [str(codex_bin), "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        bufsize=1,
    )
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        initialize = {
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "tm-observer-codex-usage", "version": "1.0.0"}},
        }
        request = {"id": 2, "method": method, "params": params}
        for message in (initialize, request):
            proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            proc.stdin.flush()

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], min(0.25, max(0.0, deadline - time.monotonic())))
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            line = proc.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == 2:
                if "result" in message and isinstance(message["result"], dict):
                    return message["result"]
                raise RuntimeError(str(message.get("error") or "Codex RPC failed"))
        raise TimeoutError("Codex RPC timed out")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.5)


def _rpc_rate_limits(codex_home: Path, *, codex_bin: Path = DEFAULT_CODEX_BIN, timeout_s: float = 8.0) -> dict[str, Any]:
    return _rpc_request(
        codex_home,
        "account/rateLimits/read",
        {"excludeResetCreditDetails": True, "supportsLunaReserve": False},
        codex_bin=codex_bin,
        timeout_s=timeout_s,
    )


def consume_reset_credit(
    account_name: str,
    idempotency_key: str,
    *,
    codex_bin: Path = DEFAULT_CODEX_BIN,
    timeout_s: float = 15.0,
) -> str:
    """Redeem one backend-owned reset credit for an explicitly named local identity."""
    import uuid

    accounts = {home.name: home for _, home in DEFAULT_ACCOUNTS}
    if account_name not in accounts:
        raise ValueError("unknown Codex account")
    try:
        key = uuid.UUID(idempotency_key)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("invalid reset attempt ID") from exc
    if key.version != 4 or str(key) != idempotency_key:
        raise ValueError("invalid reset attempt ID")

    result = _rpc_request(
        accounts[account_name],
        "account/rateLimitResetCredit/consume",
        {"idempotencyKey": idempotency_key},
        codex_bin=codex_bin,
        timeout_s=timeout_s,
    )
    outcome = result.get("outcome")
    if outcome not in {"reset", "nothingToReset", "noCredit", "alreadyRedeemed"}:
        raise RuntimeError("unexpected reset-credit response")
    return outcome


def _window_payload(window: Any) -> dict[str, Any] | None:
    if not isinstance(window, dict):
        return None
    used = window.get("usedPercent")
    remaining = None
    if isinstance(used, (int, float)):
        remaining = max(0, min(100, 100 - float(used)))
        if remaining.is_integer():
            remaining = int(remaining)
    return {
        "used_percent": used,
        "remaining_percent": remaining,
        "window_duration_mins": window.get("windowDurationMins"),
        "resets_at": window.get("resetsAt"),
    }


def _sanitize_snapshot(label: str, codex_home: Path, result: dict[str, Any]) -> dict[str, Any]:
    rate_limits = result.get("rateLimits") if isinstance(result.get("rateLimits"), dict) else {}
    credits = rate_limits.get("credits") if isinstance(rate_limits.get("credits"), dict) else {}
    reset_summary = result.get("rateLimitResetCredits")
    count = reset_summary.get("availableCount") if isinstance(reset_summary, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        count = None
    return {
        "label": label,
        "ok": True,
        "plan_type": rate_limits.get("planType"),
        "ordinary_usage_allowed": result.get("ordinaryUsageAllowed"),
        "primary": _window_payload(rate_limits.get("primary")),
        "secondary": _window_payload(rate_limits.get("secondary")),
        "credits": {
            "has_credits": credits.get("hasCredits"),
            "unlimited": credits.get("unlimited"),
            "balance": credits.get("balance"),
        },
        "rate_limit_reached_type": rate_limits.get("rateLimitReachedType"),
        "reset_credits": {"available_count": count},
        "queried_at": int(time.time()),
        "account_home": codex_home.name,
    }




def collect_codex_usage(
    accounts: tuple[tuple[str, Path], ...] = DEFAULT_ACCOUNTS,
    *,
    codex_bin: Path = DEFAULT_CODEX_BIN,
    timeout_s: float = 8.0,
) -> dict[str, Any]:
    snapshots: list[dict[str, Any]] = []
    for label, home in accounts:
        try:
            result = _rpc_rate_limits(home, codex_bin=codex_bin, timeout_s=timeout_s)
            snapshots.append(_sanitize_snapshot(label, home, result))
        except Exception as exc:
            snapshots.append(
                {
                    "label": label,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "queried_at": int(time.time()),
                    "account_home": home.name,
                }
            )
    return {
        "accounts": snapshots,
        "active_account": active_account(),
        "provider_priority": "codex_quota_wait_no_automatic_provider_fallback",
        "generated_at": int(time.time()),
    }


def active_account(selector: Path = ACCOUNT_SELECTOR) -> str | None:
    try:
        target = selector.resolve(strict=True)
    except OSError:
        return None
    for label, home in DEFAULT_ACCOUNTS:
        try:
            if home.resolve(strict=True) == target:
                return home.name
        except OSError:
            continue
    return None

def _restore_runtime_auth_link(runtime_home: Path, selector: Path, selected_home: Path) -> None:
    """Keep runtime state shared while preserving a refreshed selected credential."""
    if not runtime_home.is_dir():
        return
    auth = runtime_home / "auth.json"
    desired = selector / "auth.json"
    if auth.is_symlink():
        try:
            correct = auth.resolve(strict=False) == desired.resolve(strict=False)
        except OSError:
            correct = False
        if not correct:
            auth.unlink()
    elif auth.is_file():
        selected_auth = selected_home / "auth.json"
        selected_auth.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(auth, selected_auth)
        selected_auth.chmod(0o600)
        auth.unlink()
    elif auth.exists():
        raise OSError(f"unexpected Codex auth path: {auth}")
    if not auth.exists() and not auth.is_symlink():
        auth.symlink_to(desired)


class AccountSwitchConflict(OSError):
    """Refuse an unsafe account switch without altering the selected identity."""


_SWITCH_LOCK = threading.Lock()


@contextmanager
def _account_switch_process_lock(selector: Path):
    """Serialize web/UI switches and the managed CLI updater across processes."""
    lock_path = selector.parent / ".switch.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AccountSwitchConflict("已有账号切换或 Codex 升级正在执行，请稍后重试。") from exc
        yield
    finally:
        os.close(fd)


def _is_isolated_scientific_session(process: Path) -> bool:
    """An SDK Worker with an independent pinned CODEX_HOME cannot be rebound
    by switching the shared Desktop selector. Unknown sessions fail closed.
    """
    try:
        env = (process / "environ").read_bytes().split(b"\0")
    except OSError:
        return False
    return any(value.startswith(b"THEORYMASTER_WORKER_CODEX_HOME=") and
               value.partition(b"=")[2].startswith(b"/") for value in env)


def _assert_no_active_codex_sessions(process_root: Path = Path("/proc")) -> None:
    """Protect shared-auth CLI model turns; ignore separately pinned Workers.

    Desktop proxy processes are handled by the explicit, verified shutdown
    function before the selector is changed. Never kill CLI exec/resume.
    """
    for process in process_root.iterdir():
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            args = [a.decode("utf-8", "replace") for a in
                    (process / "cmdline").read_bytes().split(b"\0") if a]
        except (OSError, PermissionError):
            continue
        if not args or "codex" not in Path(args[0]).name.lower():
            continue
        active_cli = any(arg in {"exec", "resume"} for arg in args[1:])
        if active_cli and not _is_isolated_scientific_session(process):
            raise AccountSwitchConflict(
                f"检测到共用身份的活动 Codex CLI（PID {process.name}），账号没有更改。"
                "不能中断正在进行的模型调用；请在安全边界切换。"
            )
        if ("app-server" in args and "proxy" in args
                and not _is_isolated_scientific_session(process)):
            raise AccountSwitchConflict(
                f"Codex Desktop proxy（PID {process.name}）仍连接；"
                "需要先安全关闭该代理，账号没有更改。"
            )


def _close_desktop_proxies(process_root: Path = Path("/proc"), *, timeout_s: float = 3.0) -> tuple[int, ...]:
    """Close *only* verified same-user Codex Desktop proxy processes.

    User explicitly authorized disconnecting Desktop proxies on account
    switch. No broad pkill, no SIGKILL, no termination of scientific Workers,
    CLI exec, or other unknown process. An unverified/stuck proxy blocks the
    subsequent switch instead of risking another process's work.
    """
    stopped = []
    for process in process_root.iterdir():
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            args = [a.decode("utf-8", "replace") for a in
                    (process / "cmdline").read_bytes().split(b"\0") if a]
        except OSError:
            continue
        if (not args or "codex" not in Path(args[0]).name.lower()
                or "app-server" not in args or "proxy" not in args):
            continue
        if _is_isolated_scientific_session(process):
            continue  # Private science environment does not follow Desktop.
        pid = int(process.name)
        try:
            status = (process / "status").read_text(encoding="utf-8")
            uid_line = next(x for x in status.splitlines() if x.startswith("Uid:"))
            if int(uid_line.split()[1]) != os.geteuid():
                raise ValueError("proxy owned by another user")
            if _is_isolated_scientific_session(process):
                raise ValueError("proxy belongs to a pinned scientific Worker")
            # Verify exact executable identity, not a process title rewritten
            # by an unrelated program.
            real_exe = Path(os.readlink(process / "exe")).name.lower()
            if "codex" not in real_exe:
                raise ValueError("proxy executable identity is not Codex")
            os.kill(pid, signal.SIGTERM)
            import time
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                if not process.exists() or (process / "stat").read_text().split(") ")[1].startswith("Z "):
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("proxy did not exit after SIGTERM")
        except (OSError, ValueError, RuntimeError, StopIteration) as exc:
            raise AccountSwitchConflict(
                f"无法安全关闭 Desktop proxy PID {pid}：{type(exc).__name__}；账号未更改。"
            ) from exc
        stopped.append(pid)
    return tuple(stopped)



def _verify_selected_account(target: Path, selector: Path,
                             runtime_homes: tuple[Path, ...]) -> None:
    """Verify disk identity and a fresh non-generative Codex account/read RPC."""
    if selector.resolve(strict=True) != target.resolve(strict=True):
        raise RuntimeError("selected identity changed during restart")
    for home in runtime_homes:
        if home.is_dir() and (home / "auth.json").resolve(strict=True) != (target / "auth.json").resolve(strict=True):
            raise RuntimeError("runtime identity does not match selected account")
    runtime = next((h for h in runtime_homes if h.is_dir()), None)
    if runtime is None:
        raise RuntimeError("shared Codex runtime is missing")
    params = {"refreshToken": False}
    expected = _rpc_request(target, "account/read", params, timeout_s=12).get("account")
    actual = _rpc_request(runtime, "account/read", params, timeout_s=12).get("account")
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        raise RuntimeError("account/read identity missing")
    if expected.get("type") != "chatgpt" or actual.get("type") != "chatgpt":
        raise RuntimeError("non-ChatGPT account identity")
    if not expected.get("email") or expected.get("email") != actual.get("email"):
        raise RuntimeError("live Codex identity mismatch")



def _assert_managed_app_server(
    *,
    codex_bin: Path = DEFAULT_CODEX_BIN,
    process_root: Path = Path("/proc"),
) -> None:
    """Read-only ownership guard before the first auth mutation.

    `daemon version` can say running despite an independent app-server having
    claimed the shared control socket. Verify the executable of *every* live
    socket-listening app-server, not just the daemon's claimed version.
    """
    advice = ("账号没有更改。请在运行中的 Codex/科研任务结束后，"
              "统一 app-server 的启动管理方式。")
    try:
        result = subprocess.run(
            [str(codex_bin), "app-server", "daemon", "version"],
            env={**os.environ, "CODEX_HOME": str(SHARED_RUNTIME_HOMES[0])},
            text=True, capture_output=True, check=False, timeout=8,
        )
        if result.returncode != 0:
            raise ValueError("daemon 状态查询失败")
        info = json.loads(result.stdout)
        if not isinstance(info, dict) or info.get("status") != "running":
            raise ValueError("daemon 尚未运行")
        managed_path = info.get("managedCodexPath")
        if not isinstance(managed_path, str) or not managed_path:
            raise ValueError("daemon 未提供受管理的二进制位置")
        managed_bin = Path(managed_path).resolve(strict=True)
        found_managed = False
        for process in process_root.iterdir():
            if not process.name.isdecimal():
                continue
            try:
                args = (process / "cmdline").read_bytes().split(b"\0")
            except OSError:
                continue  # Other users' processes may be inaccessible.
            if b"app-server" not in args:
                continue
            index = args.index(b"app-server")
            if b"--listen" not in args[index + 1:]:
                continue  # Exclude short-lived --stdio RPC, daemon and proxy.
            try:
                running_exe = os.readlink(process / "exe")
                # A CLI upgrade can unlink the old executable while leaving an
                # independently launched server alive on the same socket.
                if running_exe.endswith(" (deleted)"):
                    raise AccountSwitchConflict(
                        f"检测到旧版 app-server（PID {process.name}）仍在运行，"
                        f"其可执行文件已被升级移除，daemon 无法接管。{advice}"
                    )
                executable = Path(running_exe).resolve(strict=True)
            except AccountSwitchConflict:
                raise
            except OSError as exc:
                raise AccountSwitchConflict(
                    f"无法核实 app-server（PID {process.name}）的管理归属。{advice}"
                ) from exc
            if executable != managed_bin:
                raise AccountSwitchConflict(
                    f"检测到不归 daemon 管理的 app-server（PID {process.name}），"
                    f"daemon 无法安全重启该进程。{advice}"
                )
            found_managed = True
        if not found_managed:
            raise AccountSwitchConflict(
                f"无法确认 daemon 管理的 app-server 正在运行。{advice}"
            )
    except AccountSwitchConflict:
        raise
    except (OSError, ValueError, subprocess.SubprocessError, TimeoutError) as exc:
        raise AccountSwitchConflict(
            f"无法安全核实 Codex daemon 管理状态（{type(exc).__name__}）。{advice}"
        ) from exc


def _write_account_selector(selector: Path, target: Path) -> None:
    """Atomically replace the selector symlink, never an account directory."""
    temp = selector.with_name(selector.name + f".{os.getpid()}.tmp")
    try:
        temp.unlink(missing_ok=True)
        temp.symlink_to(target.name)
        temp.replace(selector)
    finally:
        temp.unlink(missing_ok=True)


def _restart_direct_app_servers() -> tuple[int, ...]:
    """Restart the managed daily Codex daemon so it reloads the selected auth."""
    if not DEFAULT_CODEX_BIN.exists():
        return ()
    env = os.environ.copy()
    env["CODEX_HOME"] = str(SHARED_RUNTIME_HOMES[0])
    result = subprocess.run(
        [str(DEFAULT_CODEX_BIN), "app-server", "daemon", "restart"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "Codex App daemon restart failed").strip()
        raise OSError(message)
    return ()


def switch_active_account(
    account_name: str,
    selector: Path = ACCOUNT_SELECTOR,
    runtime_homes: tuple[Path, ...] = SHARED_RUNTIME_HOMES,
    *,
    restart_app: bool = True,
) -> str:
    allowed = {home.name: home for _, home in DEFAULT_ACCOUNTS}
    if account_name not in allowed:
        raise ValueError("unknown Codex account")
    target = allowed[account_name]
    if not target.is_dir():
        raise FileNotFoundError(target)

    with _SWITCH_LOCK, _account_switch_process_lock(selector):
        # Validate daemon ownership and session quiescence before auth writes.
        try:
            selected_before = selector.resolve(strict=True)
        except OSError as exc:
            raise AccountSwitchConflict("当前 Codex 账号选择器不可用，账号未更改。") from exc
        if selected_before not in {home.resolve() for home in allowed.values()}:
            raise AccountSwitchConflict("当前 Codex 账号归属不明，账号未更改。")
        safe_daemon_restart = False
        if restart_app:
            _close_desktop_proxies()
            _assert_no_active_codex_sessions()
            # Absent, unmanaged, or stale daemon must NEVER be restarted.
            # The default selector can still be switched independently of
            # existing Worker runtimes, with direct RPC identity verification.
            try:
                _assert_managed_app_server()
                safe_daemon_restart = True
            except AccountSwitchConflict:
                safe_daemon_restart = False
        if selected_before == target.resolve():
            # Auto-quota switches need not restart the Desktop daemon. A manual
            # request for that same account must still reconcile and verify it.
            if safe_daemon_restart:
                _restart_direct_app_servers()
                _assert_managed_app_server()
            if restart_app:
                _verify_selected_account(target, selector, runtime_homes)
            return account_name

        # Codex refresh can atomically replace an auth.json symlink. Preserve
        # refreshed credentials for the prior account before switching.
        try:
            for runtime_home in runtime_homes:
                _restore_runtime_auth_link(runtime_home, selector, selected_before)
            _write_account_selector(selector, target)
            for runtime_home in runtime_homes:
                _restore_runtime_auth_link(runtime_home, selector, target)
            if safe_daemon_restart:
                _restart_direct_app_servers()
                _assert_managed_app_server()
            if restart_app:
                _verify_selected_account(target, selector, runtime_homes)
        except Exception as exc:
            # A race can still invalidate the preflight; compensate filesystem
            # changes and do not report the switch as successful.
            try:
                _write_account_selector(selector, selected_before)
                for runtime_home in runtime_homes:
                    _restore_runtime_auth_link(runtime_home, selector, selected_before)
                if safe_daemon_restart:
                    _restart_direct_app_servers()
                    _assert_managed_app_server()
                if restart_app:
                    _verify_selected_account(selected_before, selector, runtime_homes)
            except Exception as rollback_error:
                raise AccountSwitchConflict(
                    "切换失败且服务身份回滚未通过验证；已停止自动重试，"
                    "请人工核实当前账号后再运行任务。"
                ) from rollback_error
            raise AccountSwitchConflict(
                "切换未完成，账号选择已回滚且验证通过。"
                f"原始原因：{type(exc).__name__}: {exc}"
            ) from exc
    return account_name
