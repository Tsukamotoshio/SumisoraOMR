# gui/components/progress_overlay.py — 进度条弹出对话框

from __future__ import annotations

import asyncio
import collections
import time
import flet as ft
from ..app_state import AppState, Event
from ..theme import Palette
from ..strings import t


class ProgressOverlay(ft.Stack):
    """进度覆盖层（直接内嵌为 Stack 子控件）。"""

    def __init__(self, state: AppState):
        super().__init__(expand=True)
        self._state = state
        self._timer_running = False
        self._start_time: float = 0.0
        # ── 跨线程安全的待处理更新队列 ──────────────────────────────────────
        # 工作线程只向队列中追加数据（非阻塞），计时器异步任务负责刷新 UI，
        # 避免工作线程在 Flet/Flutter 侧暂停时被 ctrl.update() 阻塞。
        self._log_auto_scroll: bool = True   # smart scroll 状态：True=跟随底部
        self._pending_logs: collections.deque[tuple[str, str]] = collections.deque()  # (line, color)
        self._pending_progress: collections.deque[tuple[float, str]] = collections.deque()  # (value, message)
        self._pending_sub_progress: tuple[float, str] | None = None  # 子步骤进度
        # 计时器线程在此时刻后自动隐藏浮层（_on_done 设置，避免额外线程）
        self._should_hide_after: float | None = None
        self._build_panel()
        self.controls = [self._backdrop, self._panel_wrapper]
        state.on(Event.PROGRESS_UPDATE, self._on_progress)
        state.on(Event.PROGRESS_DONE,   self._on_done)
        state.on(Event.PROGRESS_ERROR,  self._on_error)
        state.on(Event.LOG_LINE,        self._on_log)

    # ── 构建浮层 UI ────────────────────────────────────────────────────────────

    def _build_panel(self) -> None:
        self._progress_bar = ft.ProgressBar(
            value=0.0,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            color=Palette.PRIMARY,
            height=6,
        )
        # 每个文件内子步骤进度条（细，辅色）
        self._sub_progress_bar = ft.ProgressBar(
            value=0.0,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            color=Palette.INFO,
            height=3,
            visible=False,
        )
        self._sub_status_text = ft.Text('', size=12, color=ft.Colors.ON_SURFACE_VARIANT, visible=False)
        # 旋转进度圈（不确定模式，视觉动画）
        self._spinner = ft.ProgressRing(
            width=20, height=20,
            stroke_width=2.5,
            color=Palette.PRIMARY,
            visible=True,
        )
        self._status_text = ft.Text('', size=14, color=ft.Colors.ON_SURFACE, expand=True)
        self._elapsed_text = ft.Text('00:00', size=12, color=ft.Colors.ON_SURFACE_VARIANT, width=38)
        self._log_list = ft.ListView(
            spacing=2,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            auto_scroll=True,
            on_scroll=self._on_log_scroll,
        )

        close_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_size=18,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip=t('common.close'),
            on_click=self._on_close_click,
        )

        # ── 可折叠的详细日志区（识别耗时较长，给用户「正在发生什么」的反馈）──
        self._log_visible = False
        self._log_toggle_icon = ft.Icon(
            ft.Icons.EXPAND_MORE_ROUNDED, size=16, color=ft.Colors.ON_SURFACE_VARIANT)
        self._log_toggle_text = ft.Text(t('progress_overlay.button_show_log'), size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._log_toggle_btn = ft.TextButton(
            content=ft.Row(
                [self._log_toggle_icon, self._log_toggle_text],
                tight=True, spacing=4,
            ),
            on_click=self._on_toggle_log,
        )
        self._log_container = ft.Container(
            content=self._log_list,
            height=180,
            visible=False,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=ft.BorderRadius.all(8),
            padding=ft.Padding.all(8),
        )

        panel_inner = ft.Container(
            content=ft.Column(
                [
                    # ── 标题行：旋转圈 + 状态文字 + 计时 + 关闭按钮 ──
                    ft.Row(
                        [
                            self._spinner,
                            self._status_text,
                            self._elapsed_text,
                            close_btn,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(content=self._progress_bar,
                                 padding=ft.Padding.symmetric(vertical=8)),
                    # 子步骤进度（隶时隐藏）
                    ft.Container(
                        content=ft.Column(
                            [
                                self._sub_status_text,
                                ft.Container(content=self._sub_progress_bar,
                                             padding=ft.Padding.only(bottom=4)),
                            ],
                            spacing=2,
                            tight=True,
                        ),
                        visible=True,
                    ),
                    ft.Row([self._log_toggle_btn], alignment=ft.MainAxisAlignment.START),
                    self._log_container,

                ],
                spacing=4,
                tight=True,
            ),
            bgcolor=ft.Colors.SURFACE,
            border_radius=ft.BorderRadius.all(14),
            padding=ft.Padding.all(22),
            width=560,
            shadow=ft.BoxShadow(
                blur_radius=40,
                color='#AA000000',
                offset=ft.Offset(0, 8),
            ),
        )

        self._backdrop = ft.Container(
            bgcolor='#88000000',
            expand=True,
            visible=False,
        )
        self._panel_wrapper = ft.Container(
            content=panel_inner,
            alignment=ft.Alignment(0, 0),
            expand=True,
            visible=False,
        )

    # ── 计时器异步任务 ────────────────────────────────────────────────────────

    def _start_timer(self) -> None:
        self._start_time = time.monotonic()
        self._timer_running = True
        p = self.page
        if p is not None:
            p.run_task(self._timer_task)  # type: ignore[union-attr]

    def _stop_timer(self) -> None:
        self._timer_running = False

    async def _timer_task(self) -> None:
        """运行于 asyncio 事件循环，协作式调度，不阻塞 Flet 心跳帧。"""
        _last_page_update: float = 0.0
        while self._timer_running:
            now = time.monotonic()
            elapsed = int(now - self._start_time)
            mm, ss = divmod(elapsed, 60)
            self._elapsed_text.value = f'{mm:02d}:{ss:02d}'

            # 消费待处理的进度更新（FIFO，每次处理一条，保证顺序显示）
            pending = None
            if self._pending_progress:
                pending = self._pending_progress.popleft()
                self._progress_bar.value = pending[0]
                if pending[1]:
                    self._status_text.value = pending[1]

            # 消费子步骤进度
            pending_sub = self._pending_sub_progress
            if pending_sub is not None:
                self._pending_sub_progress = None
                v, m = pending_sub
                self._sub_progress_bar.value = v
                self._sub_progress_bar.visible = True
                self._sub_status_text.value = m
                self._sub_status_text.visible = bool(m)

            # 消费待处理的日志行（批量最多 8 条/次，防止 UI 积压）
            _drained = 0
            while self._pending_logs and _drained < 8:
                line, color = self._pending_logs.popleft()
                self._log_list.controls.append(
                    ft.Text(line, size=12, font_family='Consolas',
                            color=color, selectable=True)
                )
                _drained += 1
            # 限制日志控件总数
            while len(self._log_list.controls) > 80:
                self._log_list.controls.pop(0)

            # ── 限速：只在有实际内容变化时推送 page.update()，
            #    或距上次更新超过 3 秒时推送一次（仅刷新计时文字）。
            # 运行于 asyncio 事件循环，page.update() 在当前协程调度点执行，
            # 与 Flet WebSocket 处理器协作，彻底消除跨线程竞争。
            has_changes = _drained > 0 or pending is not None or pending_sub is not None
            if has_changes or (now - _last_page_update) >= 3.0:
                _last_page_update = now
                try:
                    p = self.page
                    if p is not None:
                        p.update()
                except Exception:
                    pass
                # update() 完成后立即让出事件循环，确保 Flet 心跳帧可以被及时处理
                await asyncio.sleep(0)

            # 延迟自动隐藏（由 _on_done 设置）
            if self._should_hide_after is not None and now >= self._should_hide_after:
                self._should_hide_after = None
                self._timer_running = False
                self._backdrop.visible = False
                self._panel_wrapper.visible = False
                self._log_list.controls.clear()
                try:
                    p = self.page
                    if p is not None:
                        p.update()
                except Exception:
                    pass
                await asyncio.sleep(0)
                break

            # 0.1s 睐眠：更高频地让出事件循环给 Flet 心跳任务，同时保证进度更新响应灵敏。
            await asyncio.sleep(0.1)

    # ── 显示 / 隐藏 ──────────────────────────────────────────────────────────

    def show(self, message: str = t('progress_overlay.default_message')) -> None:
        self._status_text.value = message
        self._spinner.visible = True
        self._spinner.color = Palette.PRIMARY
        self._progress_bar.value = 0.0
        self._progress_bar.color = Palette.PRIMARY
        self._elapsed_text.value = t('progress_overlay.elapsed_initial')
        self._log_list.controls.clear()
        self._pending_logs.clear()
        self._pending_progress.clear()
        self._pending_sub_progress = None
        self._log_auto_scroll = True
        self._log_list.auto_scroll = True
        self._sub_progress_bar.value = 0.0
        self._sub_progress_bar.visible = False
        self._sub_status_text.value = ''
        self._sub_status_text.visible = False
        self._should_hide_after = None  # 取消上次可能未完成的自动隐藏
        self._set_log_visible(False)  # 每次开始时折叠日志区
        self._backdrop.visible = True
        self._panel_wrapper.visible = True
        self._update_overlay()
        self._start_timer()

    def hide(self) -> None:
        self._stop_timer()
        self._should_hide_after = None  # 取消计时器任务的延迟隐藏
        self._backdrop.visible = False
        self._panel_wrapper.visible = False
        # 清理完成后的日志控件，避免隐藏状态下保留过多 UI 控件导致后续卡顿。
        self._log_list.controls.clear()
        self._update_overlay()

    def _update_overlay(self) -> None:
        # 统一走 page.update()，避免从不同线程调用单控件 update() 竞争 Flet socket
        try:
            p = self.page
            if p is not None:
                p.update()
        except Exception:
            pass

    async def _on_log_scroll(self, e: ft.OnScrollEvent) -> None:
        """Smart auto-scroll：用户向上滚时暂停，滚回底部时恢复。"""
        _THRESHOLD = 40  # 距底部 ≤40px 视为「在底部」
        at_bottom = e.extent_after <= _THRESHOLD
        if at_bottom != self._log_auto_scroll:
            self._log_auto_scroll = at_bottom
            self._log_list.auto_scroll = at_bottom
            try:
                self._log_list.update()
            except Exception:
                pass

    def _on_close_click(self, _e) -> None:
        # 用户主动关闭：重置处理标志，_run_conversion 检测到后会终止子进程
        self._state.is_processing = False
        self.hide()

    def _set_log_visible(self, visible: bool) -> None:
        self._log_visible = visible
        self._log_container.visible = visible
        if visible:
            self._log_toggle_icon.icon = ft.Icons.EXPAND_LESS_ROUNDED
            self._log_toggle_text.value = t('progress_overlay.button_hide_log')
        else:
            self._log_toggle_icon.icon = ft.Icons.EXPAND_MORE_ROUNDED
            self._log_toggle_text.value = t('progress_overlay.button_show_log')

    def _on_toggle_log(self, _e) -> None:
        self._set_log_visible(not self._log_visible)
        self._update_overlay()

    # ── 事件回调 ─────────────────────────────────────────────────────────────

    def set_sub_progress(self, value: float, message: str = '') -> None:
        """面向工作线程的非阻塞接口：设置当前文件内子步骤进度。"""
        self._pending_sub_progress = (max(0.0, min(1.0, value)), message)

    def _on_progress(self, value: float, message: str = '', **_kw) -> None:
        # 工作线程非阻塞：追加到队列，由 _timer_task 按序刷新
        self._pending_progress.append((value, message))

    def _on_done(self, message: str = t('progress_overlay.done_default'), **_kw) -> None:
        # 追加到队列，由计时器线程统一渲染，避免工作线程直接调用 page.update()
        self._pending_progress.append((1.0, message))
        self._spinner.visible = False  # 属性修改，计时器下次刷新时生效
        # 在计时器线程内延迟 2.5 秒自动隐藏，无需额外的 threading.Timer 线程
        self._should_hide_after = time.monotonic() + 2.5

    def _on_error(self, message: str, **_kw) -> None:
        self._stop_timer()
        self._should_hide_after = None  # 错误状态需用户手动关闭
        # 可能从工作线程调用，通过 run_task 将 UI 更新调度到 asyncio 事件循环
        async def _apply():
            self._progress_bar.value = 0.0
            self._progress_bar.color = Palette.ERROR
            self._status_text.value = t('progress_overlay.error_status', message=message)
            self._spinner.color = Palette.ERROR
            self._set_log_visible(True)  # 出错时自动展开日志，方便定位原因
            # 计时器已停止，不会再消费队列；把剩余日志一次性排空到列表
            while self._pending_logs:
                line, color = self._pending_logs.popleft()
                self._log_list.controls.append(
                    ft.Text(line, size=12, font_family='Consolas',
                            color=color, selectable=True)
                )
            while len(self._log_list.controls) > 80:
                self._log_list.controls.pop(0)
            self._try_update()
        p = self.page
        if p is not None:
            p.run_task(_apply)  # type: ignore[union-attr]
        else:
            self._progress_bar.value = 0.0
            self._progress_bar.color = Palette.ERROR
            self._status_text.value = t('progress_overlay.error_status', message=message)
            self._spinner.color = Palette.ERROR
            self._set_log_visible(True)

    def _on_log(self, line: str, **_kw) -> None:
        # 根据内容选颜色：✓ 绿色，✗ 红色，其余默认
        if '✓' in line:
            color = Palette.SUCCESS
        elif '✗' in line or '异常' in line or '失败' in line:
            color = Palette.ERROR
        elif line.startswith('▶'):
            color = ft.Colors.SECONDARY
        else:
            color = ft.Colors.ON_SURFACE_VARIANT
        # 非阻塞：追加到队列，由 _timer_loop 批量渲染，
        # 避免工作线程被 ctrl.update() 阻塞而挂起识别进程。
        self._pending_logs.append((line, color))

    def _try_update(self) -> None:
        try:
            p = self.page
            if p is not None:
                p.update()
        except Exception:
            pass
