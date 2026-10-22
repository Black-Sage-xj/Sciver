"""Read-only Codex app-server model catalog probe; never starts a model turn.

The advertised list is advisory: a listed model can still fail for a particular
account (entitlement, rollout, quota); never treat this as a proven invocation.
"""
from __future__ import annotations

import json
import os
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


def model_catalog(root: Path, worker_id: str | None, *, timeout: float = 9) -> dict[str, Any]:
    auth, account_label = codex_auth_for_worker(root, worker_id)
    base = {"status": "unknown", "account": account_label,
            "note": "仅检查 Codex 模型目录，不调用付费模型；目录列出不保证实际请求成功。"}
    if auth is None:
        return {**base, "reason": "尚未检测到此 Codex 账号的登录状态"}
    executable = shutil.which("codex")
    if not executable:
        return {**base, "reason": "本机尚未安装 Codex CLI"}
    try:
        with tempfile.TemporaryDirectory(prefix="tm-model-probe-") as temp:
            home = Path(temp)
            (home / "auth.json").symlink_to(auth.resolve())
            env = os.environ.copy()
            env["CODEX_HOME"] = str(home)
            proc = subprocess.Popen(
                [executable, "app-server", "--stdio"], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                env=env, bufsize=1)
            try:
                assert proc.stdin is not None and proc.stdout is not None
                for msg in (
                    {"id": 1, "method": "initialize", "params": {"clientInfo": {
                        "name": "theory-master-model-catalog", "version": "1.0"}}},
                    {"id": 2, "method": "model/list", "params": {"includeHidden": False, "limit": 100}},
                ):
                    proc.stdin.write(json.dumps(msg) + "\n")
                    proc.stdin.flush()
                deadline = time.monotonic() + max(.5, min(float(timeout), 15))
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([proc.stdout], [], [], min(.25, max(0, deadline-time.monotonic())))
                    if not ready:
                        if proc.poll() is not None:
                            break
                        continue
                    line = proc.stdout.readline()
                    if not line:
                        break
                    try:
                        response = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if response.get("id") != 2:
                        continue
                    result = response.get("result")
                    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                        return {**base, "reason": "Codex 未提供可读取的模型目录"}
                    models = sorted({str(row.get("model")) for row in result["data"]
                                     if isinstance(row, dict) and isinstance(row.get("model"), str)})
                    if result.get("nextCursor") is not None:
                        return {**base, "reason": "模型目录分页未完成，无法判定缺失模型"}
                    efforts = {row["model"]: [v["reasoningEffort"] for v in row.get("supportedReasoningEfforts", [])
                               if isinstance(v, dict) and isinstance(v.get("reasoningEffort"), str)]
                               for row in result["data"] if isinstance(row, dict) and isinstance(row.get("model"), str)}
                    return {**base, "status": "catalog_read", "models": models, "efforts": efforts,
                            "reason": "已读取账号可见模型目录（未执行实际模型调用）"}
                return {**base, "reason": "Codex 模型目录查询超时"}
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=1)
    except (OSError, ValueError, RuntimeError) as exc:
        return {**base, "reason": f"Codex 模型目录读取失败：{type(exc).__name__}"}
