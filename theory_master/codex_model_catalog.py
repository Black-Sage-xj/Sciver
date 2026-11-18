"""Read-only Codex app-server model catalog probe; never starts a model turn.

The advertised list is advisory: a listed model can still fail for a particular
account (entitlement, rollout, quota); never treat this as a proven invocation.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import select
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def codex_auth_for_worker(root: Path, worker_id: str | None) -> tuple[Path | None, str]:
    account = None
    if worker_id:
        from .worker_runtime import read_worker
        info = read_worker(root, worker_id)
        if isinstance(info, dict):
            account = str(info.get("account") or "")
    if account in {"1", "2"}:
        candidate = Path.home() / ".codex-accounts" / f"account-{account}" / "auth.json"
        return (candidate if candidate.is_file() else None), f"Codex {account}"
    ambient = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "auth.json"
    return (ambient if ambient.is_file() else None), "Codex 登录账号"


def _catalog_executable() -> str | None:
    direct = Path.home() / "bin" / "codex.real"
    return str(direct) if direct.is_file() else shutil.which("codex")


def model_catalog(root: Path, worker_id: str | None, *, timeout: float = 9,
                  auth_path: Path | None = None, account_label: str | None = None,
                  expected_auth_fingerprint: str | None = None) -> dict[str, Any]:
    if auth_path is None:
        auth, detected_label = codex_auth_for_worker(root, worker_id)
    else:
        auth, detected_label = Path(auth_path), "Codex 登录账号"
    base = {"status": "unknown", "account": account_label or detected_label,
            "note": "仅检查 Codex 模型目录，不调用付费模型；目录列出不保证实际请求成功。"}
    if auth is None or not auth.is_file():
        return {**base, "reason": "尚未检测到此 Codex 账号的登录状态"}
    executable = _catalog_executable()
    if not executable:
        return {**base, "reason": "本机尚未安装 Codex CLI"}
    try:
        with tempfile.TemporaryDirectory(prefix="tm-model-probe-") as temp:
            home = Path(temp)
            # Copy the identity: an app-server token refresh must never write
            # through a symlink into the live account selector.
            if auth.stat().st_size > 1_000_000:
                return {**base, "reason": "Codex 登录状态文件过大"}
            frozen_auth = auth.read_bytes()
            if expected_auth_fingerprint is not None and hashlib.sha256(frozen_auth).hexdigest() != expected_auth_fingerprint:
                return {**base, "reason": "Codex 登录状态已变化，请重新查询"}
            (home / "auth.json").write_bytes(frozen_auth)
            (home / "auth.json").chmod(0o600)
            env = os.environ.copy()
            env["CODEX_HOME"] = str(home)
            proc = subprocess.Popen(
                [executable, "app-server", "--stdio"], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=env, cwd=home, bufsize=0)
            try:
                assert proc.stdin is not None and proc.stdout is not None
                deadline = time.monotonic() + max(.5, min(float(timeout), 15))
                pending = bytearray()

                def send(message: dict[str, Any]) -> None:
                    proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
                    proc.stdin.flush()

                def receive(identifier: int) -> dict[str, Any] | None:
                    while time.monotonic() < deadline:
                        while b"\n" in pending:
                            line, _, rest = pending.partition(b"\n")
                            pending[:] = rest
                            try:
                                response = json.loads(line)
                            except (ValueError, UnicodeError):
                                continue
                            if isinstance(response, dict) and response.get("id") == identifier:
                                return response
                        ready, _, _ = select.select([proc.stdout], [], [], min(.25, max(0, deadline - time.monotonic())))
                        if not ready:
                            if proc.poll() is not None:
                                return None
                            continue
                        chunk = os.read(proc.stdout.fileno(), 65_536)
                        if not chunk or len(pending) + len(chunk) > 2_000_000:
                            return None
                        pending.extend(chunk)
                    return None

                send({"id": 1, "method": "initialize", "params": {"clientInfo": {
                    "name": "theory-master-model-catalog", "version": "1.0"}}})
                initialized = receive(1)
                if not initialized or "result" not in initialized:
                    return {**base, "reason": "Codex 模型目录连接未完成"}
                send({"method": "initialized"})
                rows: list[dict[str, Any]] = []
                cursor = None
                seen: set[str] = set()
                for page in range(10):
                    params: dict[str, Any] = {"includeHidden": False, "limit": 100}
                    if cursor is not None:
                        params["cursor"] = cursor
                    send({"id": page + 2, "method": "model/list", "params": params})
                    response = receive(page + 2)
                    result = response.get("result") if response else None
                    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                        return {**base, "reason": "Codex 未提供可读取的模型目录"}
                    rows.extend(row for row in result["data"] if isinstance(row, dict))
                    cursor = result.get("nextCursor")
                    if cursor is None:
                        valid = [row for row in rows if row.get("hidden") is not True and isinstance(row.get("model"), str)
                                 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", row["model"])]
                        models = sorted({row["model"] for row in valid})
                        efforts = {row["model"]: [v["reasoningEffort"] for v in row["supportedReasoningEfforts"]
                                   if isinstance(v, dict) and isinstance(v.get("reasoningEffort"), str)]
                                   for row in valid if isinstance(row.get("supportedReasoningEfforts"), list)}
                        defaults = {row["model"]: row["defaultReasoningEffort"] for row in valid
                                    if isinstance(row.get("defaultReasoningEffort"), str)}
                        return {**base, "status": "catalog_read", "models": models, "efforts": efforts,
                                "default_efforts": defaults,
                                "reason": "已读取账号可见模型目录（未执行实际模型调用）"}
                    if not isinstance(cursor, str) or cursor in seen:
                        break
                    seen.add(cursor)
                return {**base, "reason": "模型目录分页未完成，无法判定缺失模型"}
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=1)
                proc.stdin.close()
                proc.stdout.close()
    except (OSError, ValueError, RuntimeError) as exc:
        return {**base, "reason": f"Codex 模型目录读取失败：{type(exc).__name__}"}
