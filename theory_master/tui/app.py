from __future__ import annotations

import os

import time
from typing import Any

from theory_master.tui.render import (
    current_thread_id,
    render_activity,
    render_attention,
    render_artifacts,
    render_error,
    render_events,
    render_header,
    render_identity,
    render_investigation,
    render_judge,
    render_last,
    render_last_action,
    render_logs,
    render_thread,
    render_tokens,
    quota_waiting_active,
    render_quota_wait_popup,
    render_research,
    render_workflow,
)


def launch_tui(
    source: Any,
    *,
    running_workflow: bool = True,
    workflow: Any | None = None,
    close_on_repair_handoff: bool = False,
    interrupt_on_exit: bool = False,
) -> None:
    TheoryMasterApp(
        source,
        running_workflow=running_workflow,
        workflow=workflow,
        close_on_repair_handoff=close_on_repair_handoff,
        interrupt_on_exit=interrupt_on_exit,
    ).run()


def TheoryMasterApp(
    source: Any,
    *,
    running_workflow: bool = True,
    workflow: Any | None = None,
    close_on_repair_handoff: bool = False,
    interrupt_on_exit: bool = False,
):
    """Build a Textual App. Imported lazily so stdlib tests do not need textual."""
    try:
        from textual.app import App
        from textual.binding import Binding
        from textual.containers import Horizontal, Vertical, VerticalScroll
        from textual.screen import ModalScreen, Screen
        from textual.widgets import Footer, Input, Static
    except ImportError as exc:
        raise RuntimeError("Textual is required for the TUI. Install with: pip install textual") from exc

    class Pane(Static):
        def __init__(self, renderer, **kwargs):
            # Runtime logs and model text are data, not Rich markup.  A shell command
            # such as "[foo='/tmp/x]" must not be able to terminate the dashboard.
            super().__init__("", markup=False, **kwargs)
            self._renderer = renderer

        def show(self, state: dict[str, Any]) -> None:
            text = self._renderer(state)
            if getattr(self, "_text", None) == text:
                return
            self._text = text
            self.update(text)

    class TextScreen(Screen):
        BINDINGS = [
            Binding("escape", "app.pop_screen", "Back"),
            Binding("q", "app.pop_screen", "Back"),
            Binding("o", "copy_value", "Copy"),
        ]

        def __init__(
            self,
            heading: str,
            text: str,
            copy_value: str | None = None,
            *,
            text_provider=None,
            copy_text: bool = False,
            follow_tail: bool = False,
        ):
            super().__init__()
            self.heading = heading
            self.text = text
            self.copy_value = copy_value or ""
            self.text_provider = text_provider
            self.copy_text = copy_text
            self.follow_tail = follow_tail

        def compose(self):
            yield Static(self.heading, id="title", markup=False)
            with VerticalScroll(id="body-scroll"):
                yield Static(self.text, id="body", markup=False)
            yield Static("[esc] back" + ("  [o] copy" if self.copy_value or self.copy_text else ""), id="help")

        def on_mount(self) -> None:
            if self.follow_tail:
                self.call_after_refresh(self._scroll_to_end)
            if self.text_provider is not None:
                self.set_interval(0.5, self._refresh_text)

        def _scroll_to_end(self) -> None:
            self.query_one("#body-scroll", VerticalScroll).scroll_end(animate=False)

        def _refresh_text(self) -> None:
            if self.text_provider is None:
                return
            text = str(self.text_provider())
            if text == self.text:
                return
            scroll = self.query_one("#body-scroll", VerticalScroll)
            keep_following = self.follow_tail and scroll.is_vertical_scroll_end
            self.text = text
            if self.copy_text:
                self.copy_value = text
            self.query_one("#body", Static).update(text)
            if keep_following:
                self.call_after_refresh(self._scroll_to_end)

        def action_copy_value(self) -> None:
            if self.copy_value:
                self.app.copy_to_clipboard(self.copy_value)

    class NoticeScreen(ModalScreen):
        BINDINGS = [
            Binding("escape", "app.pop_screen", "Close"),
            Binding("enter", "app.pop_screen", "Close"),
        ]

        def __init__(self, heading: str, detail: str, help_text: str = "[esc/enter] close"):
            super().__init__()
            self.heading = heading
            self.detail = detail
            self.help_text = help_text

        def compose(self):
            yield Static(self.heading, id="notice-title", markup=False)
            yield Static(self.detail, id="notice-detail", markup=False)
            yield Static(self.help_text, id="notice-help", markup=False)

        def update_detail(self, detail: str, help_text: str | None = None) -> None:
            self.detail = detail
            if help_text is not None:
                self.help_text = help_text
            self.query_one("#notice-detail", Static).update(self.detail)
            self.query_one("#notice-help", Static).update(self.help_text)

    class AttentionScreen(ModalScreen):
        BINDINGS = [Binding("escape", "app.pop_screen", "Close")]

        def __init__(self, detail: str, on_submit, *, cycle_id: str, attention_key: str):
            super().__init__()
            self.detail = detail
            self.on_submit = on_submit
            self.cycle_id = cycle_id
            self.attention_key = attention_key

        def compose(self):
            yield Static("THEORYMASTER NEEDS ATTENTION", id="notice-title", markup=False)
            yield Static(self.detail, id="notice-detail", markup=False)
            yield Input(placeholder="输入给下一次 AI 运行的具体指导…", id="attention-guidance-input")
            yield Static("[Enter] save guidance    [Esc] close", id="notice-help", markup=False)

        def on_mount(self) -> None:
            self.query_one("#attention-guidance-input", Input).focus()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            text = event.value.strip()
            if not text:
                self.query_one("#notice-help", Static).update("指导不能为空。 [Esc] close")
                return
            try:
                result = self.on_submit(text)
            except Exception as exc:
                self.query_one("#notice-help", Static).update(f"保存失败：{exc}    [Esc] close")
                return
            self.query_one("#notice-detail", Static).update(str(result))
            self.query_one("#notice-help", Static).update("指导已保存；下一次 resume 会交给第一个 Agent。 [Esc] close")
            event.input.value = ""

    class ControlNoticeScreen(NoticeScreen):
        BINDINGS = [
            Binding("escape", "app.pop_screen", "Cancel"),
            Binding("p", "confirm_pause", "Confirm"),
            Binding("k", "confirm_kill", "Confirm"),
        ]

        def __init__(self, action: str, description: str, on_confirm):
            self.action = action
            self.on_confirm = on_confirm
            super().__init__(
                "THEORYMASTER CONTROL",
                f"Action: {action.upper()}\n\n{description}\n\n再按一次才可以启动。",
                "[same key] confirm    [Esc] exit",
            )

        def _confirm(self, action: str) -> None:
            if action != self.action:
                return
            result = self.on_confirm(action)
            self.update_detail(str(result), "操作已保留在此框中。  [Esc] exit")

        def action_confirm_pause(self) -> None:
            self._confirm("pause")

        def action_confirm_kill(self) -> None:
            self._confirm("kill")

    class MaxCyclesScreen(ModalScreen):
        BINDINGS = [
            Binding("escape", "app.pop_screen", "Cancel"),
        ]

        def __init__(self, current: int, on_submit):
            super().__init__()
            self.current = current
            self.on_submit = on_submit

        def compose(self):
            yield Static("SET MAX CYCLES", id="notice-title", markup=False)
            yield Static(
                "修改当前任务的 Cycle 上限。正在运行的当前 Cycle 不会被中断；新值会在下一个 Cycle 边界生效。",
                id="notice-detail", markup=False,
            )
            yield Input(value=str(self.current), placeholder="max_cycles >= 1", id="max-cycles-input", type="integer")
            yield Static("[Enter] apply    [Esc] cancel", id="notice-help", markup=False)

        def on_mount(self) -> None:
            self.query_one("#max-cycles-input", Input).focus()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            raw = event.value.strip()
            try:
                value = int(raw)
                result = self.on_submit(value)
            except Exception as exc:
                self.query_one("#notice-help", Static).update(f"无效设置：{exc}    [Esc] cancel")
                return
            self.query_one("#notice-detail", Static).update(str(result))
            self.query_one("#notice-help", Static).update("已保存。 [Esc] close")

    class AppImpl(App):
        CSS = """
        Screen { background: #111; color: #eee; }
        #titlebar { height: 1; }
        #title { width: auto; text-style: bold; color: cyan; padding: 0 1; }
        #identity { width: 1fr; color: #ddd; padding: 0 2; }
        #header { border: solid cyan; height: auto; }
        #header-meta, #workflow { width: 1fr; padding: 0 1; height: auto; }
        #header-meta { border-bottom: solid cyan; }
        #workflow { text-wrap: nowrap; overflow-x: hidden; }
        #activity-col { width: 1fr; height: 1fr; border-right: solid cyan; }
        #activity-meta { height: auto; padding: 0 1; }
        #error { height: auto; padding: 0 1; color: #ff8080; }
        #last-scroll { height: 1fr; padding: 0 1; border: solid #555; }
        #last { height: auto; }
        #last-action { height: auto; min-height: 1; padding: 0 1; text-style: bold; color: #ddd; border: solid #555; }
        #center { width: 1fr; height: 1fr; overflow-y: auto; padding: 0 1; }
        #investigation { height: auto; }
        #judge { height: auto; }
        #tokens { height: auto; }
        #activity { height: auto; }
        #body-scroll { height: 1fr; }
        .section-title { text-style: bold; color: cyan; margin-top: 1; height: 1; }
        #help { color: #aaa; height: 1; }
        NoticeScreen, AttentionScreen, ControlNoticeScreen, MaxCyclesScreen { align: center middle; background: rgba(0, 0, 0, 0.75); }
        .quota-waiting #notice-title { color: yellow; }
        .quota-waiting #notice-detail { border: solid yellow; background: #221f10; }
        #notice-title { width: 70; max-width: 90%; color: #ff8080; text-style: bold; padding: 0 1; height: 1; }
        #notice-detail { width: 70; max-width: 90%; height: auto; border: solid #ff8080; padding: 1 2; background: #211; }
        #notice-help { width: 70; max-width: 90%; color: #aaa; padding: 0 1; height: 1; }
        #max-cycles-input { width: 70; max-width: 90%; margin: 0 1; }
        #attention-guidance-input { width: 70; max-width: 90%; margin: 0 1; }
        #mid { height: 1fr; }
        """
        BINDINGS = [
            Binding("1", "show_logs", "Logs"),
            Binding("2", "show_thread", "Thread"),
            Binding("3", "show_events", "Events"),
            Binding("4", "show_artifacts", "Artifacts"),
            Binding("r", "show_research", "Research"),
            Binding("p", "pause_workflow", "Pause"),
            Binding("k", "kill_computation", "Kill Cycle"),
            Binding("c", "set_max_cycles", "Max cycles"),
            Binding("a", "toggle_attention_takeover", "Supervisor"),
            Binding("u", "toggle_quota_auto_adjust", "Quota auto adjust"),
            Binding("f12", "show_maintenance_notice", "TUI deprecation", show=False),
            Binding("ctrl+c", "exit_program", "Exit"),
            Binding("q", "quit_now", "Quit"),
        ]
        TITLE = "TheoryMaster"

        def __init__(self, src: Any):
            super().__init__()
            self.source = src
            self.running_workflow = running_workflow
            self.workflow = workflow
            self.close_on_repair_handoff = close_on_repair_handoff
            self.interrupt_on_exit = interrupt_on_exit
            self._state: dict[str, Any] = {}
            self._shown_error: str | None = None
            self._shown_attention: tuple[str, str] | None = None
            self._attention_screen: AttentionScreen | None = None
            self._quota_wait_screen: NoticeScreen | None = None
            self._quota_wait_notified = False
            self._quit_armed_until = 0.0
            self._help_text = ""
            self._panes: dict[str, Pane] = {}
            self._help_widget: Static | None = None

        def _make_pane(self, renderer, widget_id: str) -> Pane:
            pane = Pane(renderer, id=widget_id)
            self._panes[widget_id] = pane
            return pane

        def compose(self):
            with Horizontal(id="titlebar"):
                yield Static("TheoryMaster", id="title")
                yield self._make_pane(render_identity, "identity")
            with Vertical(id="header"):
                yield self._make_pane(render_header, "header-meta")
                yield self._make_pane(render_workflow, "workflow")
            with Horizontal(id="mid"):
                with Vertical(id="activity-col"):
                    yield Static("CURRENT ACTIVITY", classes="section-title")
                    yield self._make_pane(render_activity, "activity-meta")
                    yield Static("LAST / HANDOFFS", classes="section-title")
                    with VerticalScroll(id="last-scroll"):
                        yield self._make_pane(render_last, "last")
                    yield self._make_pane(render_last_action, "last-action")
                with Vertical(id="center"):
                    yield Static("INVESTIGATION", classes="section-title")
                    yield self._make_pane(render_investigation, "investigation")
                    yield Static("JUDGE", classes="section-title")
                    yield self._make_pane(render_judge, "judge")
                    yield Static("USAGE DETAIL", classes="section-title")
                    yield self._make_pane(render_tokens, "tokens")
            quit_hint = "[q] Detach TUI (press twice)" if not interrupt_on_exit else "[q] Pause + exit (press twice)"
            controls = "[p] Pause  [k] Kill Cycle  [c] Max cycles  [a] Attention takeover  [u] 额度自调" if running_workflow and self.workflow is not None else "[p/k/c/a/u] Controls unavailable (offline demo)"
            self._help_text = f"[1] Logs  [2] Thread  [3] Events  [4] Artifacts  [r] Research  {controls}  {quit_hint}"
            self._help_widget = Static(self._help_text, id="help")
            yield self._help_widget
            yield Footer()

        def on_mount(self) -> None:
            self.refresh_state()
            self.set_interval(0.4, self.refresh_state)
            self.action_show_maintenance_notice()

        def action_show_maintenance_notice(self) -> None:
            # Fresh launches and tmux reattachments share this dialog.
            # Never persist acknowledgement or disable the legacy dashboard.
            if isinstance(self.screen, NoticeScreen) and self.screen.heading == "THEORYMASTER TUI · 停止维护":
                return
            self.push_screen(NoticeScreen(
                "THEORYMASTER TUI · 停止维护",
                "此 TUI 已停止维护，建议使用 Web 端：\n"
                f"{os.environ.get('THEORYMASTER_WEB_URL', 'http://127.0.0.1:8787/')}\n\n"
                "旧版 TUI 仍可继续使用，但不再维护。",
                "[Enter/Esc] 知道了，继续使用 TUI（下次进入仍会提示）",
            ))

        def _sync_quota_wait_popup(self) -> None:
            """Show once per waiting episode; keep snapshot fresh without popup loops."""
            waiting = quota_waiting_active(self._state)
            tracked = self._quota_wait_screen
            stack = getattr(self, "screen_stack", ())
            tracked_is_open = tracked is not None and tracked in stack
            if not waiting:
                self._quota_wait_notified = False
                if tracked_is_open and self.screen is tracked:
                    tracked.dismiss()
                    self._quota_wait_screen = None
                elif not tracked_is_open:
                    self._quota_wait_screen = None
                return
            if tracked_is_open:
                tracked.update_detail(render_quota_wait_popup(self._state))
                return
            if self._quota_wait_notified:
                return
            # Do not cover a user-initiated dialog or the unrelated Attention UI.
            if len(stack) > 1:
                return
            screen = NoticeScreen(
                "CODEX 额度等待 · 自动刷新",
                render_quota_wait_popup(self._state),
                "[Esc/Enter] close · 关闭后不重复弹出，等待状态持续显示在顶栏",
            )
            screen.add_class("quota-waiting")
            self._quota_wait_notified = True
            self._quota_wait_screen = screen
            self.push_screen(screen)

        def refresh_state(self) -> None:
            self._state = self.source.snapshot()
            # A secondary screen (Events, Logs, Artifacts, or a modal) may be
            # on top while the timer continues refreshing. Keep direct references
            # to dashboard panes because screen-scoped queries do not see App children.
            for pane in self._panes.values():
                pane.show(self._state)
            status = str(self._state.get("status") or "").upper()
            cycle_id = str(self._state.get("cycle_id") or self._state.get("run_id") or "")
            detail = str(self._state.get("current_activity") or self._state.get("last_event") or status.lower())
            error_key = f"{status}:{detail}"
            handoff = self._state.get("repair_handoff") or {}
            if (
                status == "FAILED"
                and self.close_on_repair_handoff
                and handoff.get("status") == "started"
            ):
                self.exit()
                return
            attention = self._state.get("attention") if isinstance(self._state.get("attention"), dict) else {}
            attention_key = f"{attention.get('code') or 'needs_attention'}:{attention.get('reason') or self._state.get('human_attention') or detail}"
            attention_identity = (cycle_id, attention_key)
            tracked = self._attention_screen
            stack = getattr(self, "screen_stack", ())
            tracked_is_open = tracked is not None and tracked in stack
            should_dismiss_attention = (
                status != "NEEDS_ATTENTION"
                or (tracked_is_open and (tracked.cycle_id, tracked.attention_key) != attention_identity)
            )
            if should_dismiss_attention:
                self._shown_attention = None
                if tracked_is_open and self.screen is tracked:
                    tracked.dismiss()
                    self._attention_screen = None
                    tracked_is_open = False
                elif not tracked_is_open:
                    self._attention_screen = None
                # If another modal covers the tracked AttentionScreen, leave
                # that modal untouched and dismiss the old screen when exposed.
            if status == "NEEDS_ATTENTION":
                if tracked_is_open and (tracked.cycle_id, tracked.attention_key) != attention_identity:
                    pass  # The stale AttentionScreen is covered by another modal.
                elif not tracked_is_open and attention_identity != self._shown_attention:
                    self._shown_attention = attention_identity
                    screen = AttentionScreen(
                        render_attention(self._state),
                        self._submit_attention_guidance,
                        cycle_id=cycle_id,
                        attention_key=attention_key,
                    )
                    self._attention_screen = screen
                    self.push_screen(screen)
            self._sync_quota_wait_popup()
            if status == "FAILED" and error_key != self._shown_error:
                self._shown_error = error_key
                self.push_screen(NoticeScreen("THEORYMASTER ERROR", f"{status}: {detail}"))
            if self._quit_armed_until and time.monotonic() >= self._quit_armed_until:
                self._quit_armed_until = 0.0
                if self._help_widget is not None:
                    self._help_widget.update(self._help_text)

        def _confirm_exit(self, label: str) -> None:
            now = time.monotonic()
            if now < self._quit_armed_until:
                self._quit_armed_until = 0.0
                if self.interrupt_on_exit and self.running_workflow and self.workflow is not None:
                    self._request_control("pause")
                self.exit()
                return
            self._quit_armed_until = now + 2.0
            action = "pause safely and exit" if self.interrupt_on_exit else "detach this TUI without stopping the Runner"
            if self._help_widget is not None:
                self._help_widget.update(f"[{label}] Press {label} again to {action}")

        def action_exit_program(self) -> None:
            self._confirm_exit("Control+C")

        def action_quit_now(self) -> None:
            self._confirm_exit("q")

        def _request_control(self, action: str) -> str:
            if not self.running_workflow or self.workflow is None:
                return "Recovery controls are unavailable in offline demo TUI"
            role = str(self._state.get("current_agent") or "") if action == "kill" else None
            thread_id = current_thread_id(self._state) if action == "kill" else None
            try:
                self.workflow.request_control(action, role=role or None, thread_id=thread_id or None)
            except Exception as exc:
                return f"控制失败：{exc}"
            labels = {"pause": "Pause requested; current safe unit will finish", "kill": "Current Cycle abandoned permanently; only a new Run is allowed"}
            current = self.source.snapshot()
            return f"{labels[action]}\n\n当前状态：{current.get('status') or 'requested'}"

        def _confirm_control(self, action: str) -> None:
            descriptions = {
                "pause": "暂停整个 Workflow。当前安全单元完成后，不再启动下一个 Agent 阶段；已经完成的结果保留，可用 resume 继续。",
                "kill": "永久作废当前 Cycle，不可 Resume。PAUSED 时立即登记取消；RUNNING 时等待 SDK 安全检查点，不会异步强杀正在运行的模型 Turn。保留日志和计算文件，下次必须手动 Run 新 Cycle。",
            }
            self.push_screen(ControlNoticeScreen(action, descriptions[action], self._request_control))

        def action_pause_workflow(self) -> None:
            self._confirm_control("pause")

        def action_kill_computation(self) -> None:
            self._confirm_control("kill")

        def _set_max_cycles(self, value: int) -> str:
            if not self.running_workflow or self.workflow is None:
                return "Cycle budget control is unavailable in offline demo TUI"
            budget = self.workflow.set_max_cycles(value)
            return (
                f"max_cycles 已修改为 {budget}。\n\n"
                "当前正在执行的 Cycle 会正常完成；Runner 会在下一个 Cycle 边界重新读取该值。"
            )

        def action_set_max_cycles(self) -> None:
            if not self.running_workflow or self.workflow is None:
                return
            try:
                current = self.workflow.current_max_cycles()
            except Exception:
                current = int(self._state.get("max_cycles") or 1)
            self.push_screen(MaxCyclesScreen(current, self._set_max_cycles))

        def _submit_attention_guidance(self, text: str) -> str:
            if not self.running_workflow or self.workflow is None:
                raise ValueError("Attention guidance is unavailable in offline demo TUI")
            payload = self.workflow.submit_attention_guidance(text, source="human")
            master_id = self._state.get("master_id", self._state.get("master_run_id"))
            return (
                "指导已保存为一次性 operator guidance。\n\n"
                f"来源：{payload.get('source')}\n"
                f"文件：{payload.get('path')}\n\n"
                f"下一步：tm resume {master_id if master_id is not None else ''}".rstrip()
            )

        def _toggle_attention_takeover(self) -> str:
            if not self.running_workflow or self.workflow is None:
                return "Supervisor is unavailable in offline demo TUI"
            current = self._state.get("supervisor_enabled")
            if current is None:
                current = self._state.get("attention_takeover_enabled")
            enabled = not bool(current)
            result = self.workflow.set_attention_takeover(enabled)
            state = "开启" if result.get("enabled") else "关闭"
            handoff = result.get("handoff")
            suffix = f"\n接管状态：{handoff}" if handoff else ""
            return f"Supervisor 已{state}。{suffix}"

        def action_toggle_attention_takeover(self) -> None:
            result = self._toggle_attention_takeover()
            self.push_screen(NoticeScreen("SUPERVISOR", result))

        def _toggle_quota_auto_adjust(self) -> str:
            if not self.running_workflow or self.workflow is None:
                return "Quota auto adjust is unavailable in offline demo TUI"
            enabled = not bool(self._state.get("quota_auto_adjust_enabled"))
            result = self.workflow.set_quota_auto_adjust(enabled)
            state = "开启" if result.get("enabled") else "关闭"
            if result.get("enabled") and result.get("quota_paused"):
                suffix = "\n\n当前 Cycle 已因额度暂停；若原 Supervisor 已退出，请执行 tm resume 后由额度控制器接管。"
            elif not result.get("enabled"):
                suffix = "\n\n若 Supervisor 正在 WAITING_QUOTA，会在下一次额度轮询时停止自动等待。"
            else:
                suffix = ""
            return f"额度自调已{state}。{suffix}"

        def action_toggle_quota_auto_adjust(self) -> None:
            result = self._toggle_quota_auto_adjust()
            self.push_screen(NoticeScreen("QUOTA AUTO ADJUST", result))

        def action_show_logs(self) -> None:
            self.push_screen(TextScreen("LOGS", render_logs(self.source.logs())))

        def action_show_thread(self) -> None:
            self.push_screen(TextScreen("THREAD", render_thread(self._state), copy_value=current_thread_id(self._state)))

        def action_show_events(self) -> None:
            provider = lambda: render_events(self.source.runtime_events(limit=0))
            text = provider()
            self.push_screen(TextScreen(
                "EVENTS · FULL CONVERSATION",
                text,
                copy_value=text,
                text_provider=provider,
                copy_text=True,
                follow_tail=True,
            ))

        def action_show_artifacts(self) -> None:
            self.push_screen(TextScreen("ARTIFACTS", render_artifacts(self._state)))

        def action_show_research(self) -> None:
            self.push_screen(TextScreen("RESEARCH", render_research(self._state)))

    return AppImpl(source)
