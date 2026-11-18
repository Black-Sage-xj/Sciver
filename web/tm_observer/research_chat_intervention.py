"""Explicit human directions in the canonical graph, independent of runtime launch."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .research_plan_editor import ResearchPlanError, read_plan


class ResearchInterventionError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > limit:
        raise ResearchInterventionError(f"{label}必须填写 1–{limit} 字", 400)
    return value


@contextmanager
def _idle_runtime_scope(root: Path) -> Iterator[dict[str, Any]]:
    """Prevent allocation/legacy launch while checking actual Worker identities."""
    from theory_master.runtime_manager import runtime_health
    from theory_master.worker_runtime import list_workers

    metadata = root / ".theory-master"
    if metadata.is_symlink():
        raise ResearchInterventionError("科研运行目录不安全", 403)
    metadata.mkdir(exist_ok=True)
    with ExitStack() as stack:
        # Match existing launch and allocation locks. Holding the owner lock
        # also excludes a foreground runner whose registry is not yet written.
        for name in ("launch.lock", "scheduler.lock", "runner.lock"):
            path = metadata / name
            try:
                fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
                stream = stack.enter_context(os.fdopen(fd, "a+"))
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ResearchInterventionError(
                    "任务正在运行或分配 Worker；请先安全暂停，并确认进程已停止后重试。暂停请求尚未生效时不能写入。"
                ) from exc
        health = runtime_health(root)
        workers = list_workers(root)
        busy = [row["worker_id"] for row in workers
                if row.get("process_alive") is True or row.get("status") in {"RESERVED", "STARTING"}]
        if health.get("active") or busy:
            suffix = "：" + ", ".join(busy) if busy else ""
            raise ResearchInterventionError(
                "请先安全暂停当前研究，并确认 Runner 和 Worker 已停止后重试" + suffix
                + "。已请求暂停不代表已停止。"
            )
        yield {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "legacy_active": False, "live_worker_ids": []}


def _canonical_graph(task_root: Path, research_root: Path) -> tuple[Path, dict[str, Any]]:
    from theory_master.science_graph_store import ScienceGraphStore
    from theory_master.task import TaskBinding

    binding = TaskBinding.load(task_root)
    config = binding.config
    if binding.research_root != research_root:
        raise ResearchInterventionError("任务的科研运行目录已变化，请重新读取任务", 409)
    raw_database = Path(config["graph_database"]) if config.get("graph_database") else (
        Path(config["branch_canvas"]).parent / "science_graph.sqlite3")
    database = raw_database.expanduser().resolve()
    if not raw_database.is_absolute() or raw_database.is_symlink() or not database.is_file():
        raise ResearchInterventionError("规范 SQLite 科研图谱不存在或不安全；请先修复任务绑定", 409)
    # Read-only validation must precede saving the Plan. Never create/migrate a
    # missing database by opening the ordinary writable Store constructor.
    with ScienceGraphStore(database, readonly=True):
        pass
    return database, config


def _publish_projection(database: Path, config: dict[str, Any]) -> None:
    """Refresh current registered notebooks or the original canonical Canvas."""
    from theory_master.science_canvas_export import canvas_projection, write_external_detail_documents
    from theory_master.science_graph_store import ScienceGraphStore

    # Explicitly recording a new human node also publishes its readable source.
    # A generic Canvas refresh is only a projection and may not create notes.
    with ScienceGraphStore(database, readonly=True) as store:
        write_external_detail_documents(store, notes_root=Path(config["notes_dir"]))
    try:
        from theory_master.vault_canvas import refresh_registered_canvases
    except ModuleNotFoundError as exc:
        if exc.name != "theory_master.vault_canvas":
            raise
    else:
        results = refresh_registered_canvases(database)
        if results and any(row.get("status") != "ready" for row in results):
            raise ResearchInterventionError("科研节点已保存，Canvas 投影尚待重试")
        if results:
            return
    from theory_master.runtime import atomic

    canvas = Path(config["branch_canvas"])
    if canvas.is_symlink() or not canvas.is_absolute():
        raise ResearchInterventionError("Canvas 投影路径不安全", 403)
    with ScienceGraphStore(database, readonly=True) as store:
        projection = canvas_projection(store)
    atomic(canvas, json.dumps(projection, ensure_ascii=False, indent=2) + "\n")


def _node_recorded(database: Path, external_id: str, content: str) -> bool:
    from theory_master.science_graph_store import ScienceGraphStore

    try:
        with ScienceGraphStore(database, readonly=True) as store:
            row = store.db.execute(
                "SELECT content_type, source, content FROM external_records WHERE id=?", (external_id,)
            ).fetchone()
            return bool(row and tuple(row) == ("ResearchFocus", "human", content))
    except (OSError, ValueError, sqlite3.Error):
        return False


def record_conjecture(search_root: Path, task: dict, *, hypothesis: str,
                     decisive_test: str, reason: str, expected_revision: str,
                     source_session: str = "", timing: str = "next_proposal",
                     replace_external_id: str = "",
                     source_references: list[dict] | None = None) -> dict:
    """Register one proposed human direction; qualification and launch are separate.

    Default registration is safe while a frozen Cycle runs. Only a later fresh
    proposal may claim it. ``immediate`` additionally verifies actual runtime
    idleness, but still neither changes a checkpoint nor starts execution.
    ``expected_revision`` checks the Required Context used in the discussion;
    registration preserves every Research Plan section verbatim.
    """
    from theory_master.goal_terminal import assert_goal_open, science_commit_barrier
    from theory_master.multi_worker import canvas_write_lock
    from theory_master.operator_direction import format_requested_conjecture, transition_pending, _directions
    from theory_master.science_graph_store import ScienceGraphStore
    from .research_plan_editor import _file, _read, _parse
    from .research_plan_history import plan_lock

    hypothesis = _text(hypothesis, "研究猜想", 4000)
    decisive_test = _text(decisive_test, "判别检验", 4000)
    reason = _text(reason, "干预原因", 1200)
    if timing not in {"next_proposal", "immediate"}:
        raise ResearchInterventionError("生效时机必须为 next_proposal 或 immediate", 400)
    if not isinstance(source_session, str) or len(source_session) > 160 or "\x00" in source_session:
        raise ResearchInterventionError("对话来源标识无效", 400)
    if not isinstance(replace_external_id, str) or len(replace_external_id) > 80:
        raise ResearchInterventionError("取代的猜想标识无效", 400)
    plan = read_plan(search_root, task)
    section = next(row for row in plan["sections"] if row["name"] == "Required Context")
    if expected_revision != section["revision"]:
        raise ResearchPlanError("Required Context 已变化，请重新读取后再提交猜想", 409)
    task_root = Path(task["task_root"]).resolve()
    research_path = Path(task["research_root"]).expanduser()
    research_root = research_path.resolve()
    if research_path != research_root or not research_root.is_dir():
        raise ResearchInterventionError("科研运行目录不存在或不是规范路径", 409)
    database, config = _canonical_graph(task_root, research_root)
    external_id = "human-conjecture-" + uuid.uuid4().hex[:20]
    marker = format_requested_conjecture(
        external_id=external_id, hypothesis=hypothesis, decisive_test=decisive_test,
        reason=reason, source_session=source_session,
    )
    created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    def quoted(value: str) -> str:
        return "\n".join("> " + line for line in value.splitlines())

    receipt: dict[str, Any] = {
        "schema": "theory-master/research-chat-intervention/v2", "operation": "record_conjecture",
        "status": "partial", "external_id": external_id, "node_type": "External",
        "content_type": "ResearchFocus", "formal_hypothesis_id": None,
        "plan_saved": False, "plan_unchanged": True, "node_recorded": False,
        "execution_started": False, "projection_refreshed": False,
        "qualification_required": True, "existing_checkpoint_unchanged": True,
        "timing": timing, "delivery_status": "pending", "effective_at": "next_fresh_frontier_proposal",
        "runtime": {"idle_verified": False, "execution_authorized": False},
    }
    scope = _idle_runtime_scope(research_root) if timing == "immediate" else nullcontext(None)
    with scope as runtime:
        if runtime is not None:
            receipt["runtime"] = {**runtime, "idle_verified": True, "execution_authorized": False}
        with science_commit_barrier(research_root, "shared"):
            assert_goal_open(research_root, database)
            with canvas_write_lock(research_root):
                with plan_lock(Path(config["plan_path"])):
                    current = _read(_file(search_root, task))
                    current_context = _parse(current)["Required Context"]
                    if hashlib.sha256(current_context.encode("utf-8")).hexdigest() != expected_revision:
                        raise ResearchPlanError("Required Context 已变化，请重新读取后再提交猜想", 409)
                    if source_references is not None:
                        from .research_chat_sources import validate_task_sources
                        from .canvas_md import ExplorerError
                        try:
                            sources = validate_task_sources(search_root, task, source_references)
                        except ExplorerError as exc:
                            raise ResearchInterventionError(str(exc), exc.status) from exc
                    else:
                        sources = []
                    content = (
                        "## 用户提出的研究方向\n\n"
                        "人工提出，尚待 Research Lead 与 Challenger 资格审查。\n\n"
                        "**猜想**\n\n" + quoted(hypothesis) + "\n\n"
                        "**判别检验**\n\n" + quoted(decisive_test) + "\n\n"
                        "**调整原因**\n\n" + quoted(reason) + "\n\n"
                        "**讨论来源快照（不代表当前事实或科学证据）**\n\n"
                        + quoted(json.dumps(sources, ensure_ascii=False)) + "\n\n" + marker
                    )
                    with ScienceGraphStore(database) as store:
                        with store.transaction():
                            if replace_external_id:
                                try:
                                    transition_pending(store, replace_external_id, status="superseded",
                                                       reason=reason, replacement_id=external_id,
                                                       source_session=source_session)
                                except ValueError as exc:
                                    raise ResearchInterventionError(str(exc)) from exc
                            store.db.execute(
                                "INSERT INTO external_records(id,content_type,source,title,content,created_at) "
                                "VALUES (?,?,?,?,?,?)",
                                (external_id, "ResearchFocus", "human", "Human research conjecture", content, created_at),
                            )
                            store._event("external.human_conjecture.pending", external_id,
                                         {"source_session": source_session, "reason": reason,
                                          "context_revision": expected_revision,
                                          "source_references": sources, "timing": timing,
                                          "replaces": replace_external_id})
                            pending = [item["record"]["external_id"] for item in _directions(store)
                                       if item["status"] == "pending"]
                            receipt["queue_position"] = pending.index(external_id) + 1
                            receipt["effective_at"] = ("next_fresh_frontier_proposal" if receipt["queue_position"] == 1
                                                       else "fresh_frontier_proposal_in_fifo_order")
                    receipt.update(node_recorded=True, source_references=sources,
                                   replaces=replace_external_id, section_revision=expected_revision)
                try:
                    _publish_projection(database, config)
                    receipt.update(status="recorded", projection_refreshed=True)
                except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                    receipt["error"] = {"stage": "publish_projection", "type": type(exc).__name__,
                                        "message": str(exc)[:600]}
                return receipt


def revoke_conjecture(search_root: Path, task: dict, *, external_id: str,
                      reason: str, source_session: str = "") -> dict:
    """Withdraw an unclaimed direction without affecting any running science."""
    from theory_master.goal_terminal import assert_goal_open, science_commit_barrier
    from theory_master.multi_worker import canvas_write_lock
    from theory_master.operator_direction import transition_pending
    from theory_master.science_graph_store import ScienceGraphStore

    reason = _text(reason, "撤销原因", 1200)
    if not isinstance(source_session, str) or len(source_session) > 160 or "\x00" in source_session:
        raise ResearchInterventionError("对话来源标识无效", 400)
    if not isinstance(external_id, str) or not external_id.startswith("human-conjecture-"):
        raise ResearchInterventionError("人工猜想标识无效", 400)
    read_plan(search_root, task)
    root = Path(task["research_root"]).resolve()
    database, _ = _canonical_graph(Path(task["task_root"]).resolve(), root)
    with science_commit_barrier(root, "shared"):
        assert_goal_open(root, database)
        with canvas_write_lock(root):
            with ScienceGraphStore(database) as store:
                with store.transaction():
                    try:
                        transition_pending(store, external_id, status="revoked", reason=reason,
                                           source_session=source_session)
                    except ValueError as exc:
                        raise ResearchInterventionError(str(exc)) from exc
    return {"schema": "theory-master/research-chat-intervention/v2",
            "operation": "revoke_conjecture", "external_id": external_id,
            "status": "revoked", "delivery_status": "revoked", "plan_unchanged": True,
            "execution_started": False, "existing_checkpoint_unchanged": True}
