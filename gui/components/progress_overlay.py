# gui/components/progress_overlay.py — 进度条弹出对话框

from __future__ import annotations

import collections
import threading
import time
import flet as ft
from ..app_state import AppState, Event
from ..theme import Palette


class ProgressOverlay(ft.Stack):
    """进度覆盖层（直接内嵌为 Stack 子控件）。"""

    def __init__(self, state: AppState):
        super().__init__(expand=True)
        self._state = state
        self._timer_thread: threading.Thread | None = None
        self._timer_running = False
        self._start_time: float = 0.0
        # ── 线程安全的待处理更新队列 ─────────────────────────────────────────
        # 工作线程只向队列中追加数据（非阻塞），计时器线程负责刷新 UI，
        # 避免工作线程在 Flet/Flutter 侧暂停时被 ctrl.update() 阻塞。
        self._pending_logs: collections.deque[tuple[str, str]] = collections.deque()  # (line, color)
        self._pending_progress: tuple[float, str] | None = None  # (value, message)
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
            bgcolor=Palette.BG_CARD,
            color=Palette.PRIMARY,
            height=6,
        )
        # 旋转进度圈（不确定模式，视觉动画）
        self._spinner = ft.ProgressRing(
            width=20, height=20,
            stroke_width=2.5,
            color=Palette.PRIMARY,
            visible=True,
        )
        self._status_text = ft.Text('', size=13, color=Palette.TEXT_PRIMARY, expand=True)
        self._elapsed_text = ft.Text('00:00', size=11, color=Palette.TEXT_SECONDARY, width=38)
        self._log_list = ft.ListView(
            spacing=2,
            expand=True,
            auto_scroll=True,
        )

        close_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_size=18,
            icon_color=Palette.TEXT_SECONDARY,
            tooltip='关闭',
            on_click=self._on_close_click,
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
                    ft.Container(
                        content=self._log_list,
                        height=200,
                        bgcolor=Palette.BG_DARK,
                        border_radius=ft.BorderRadius.all(6),
                        padding=ft.Padding.all(8),
                    ),
                ],
                spacing=4,
                tight=True,
            ),
            bgcolor=Palette.BG_SURFACE,
            border_radius=ft.BorderRadius.all(14),
            padding=ft.Padding.all(22),
            width=560,
            shadow=ft.BoxShadow(
                blur_radius=40,
                color='#000000AA',
                offset=ft.Offset(0, 8),
            ),
        )

        self._backdrop = ft.Container(
            bgcolor='#00000088',
            expand=True,
            visible=False,
        )
        self._panel_wrapper = ft.Container(
            content=panel_inner,
            alignment=ft.Alignment(0, 0),
            expand=True,
            visible=False,
        )

    # ── 计时器线程 ────────────────────────────────────────────────────────────

    def _start_timer(self) -> None:
        self._start_time = time.monotonic()
        self._timer_running = True
        self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer_thread.start()

    def _stop_timer(self) -> None:
        self._timer_running = False

    def _timer_loop(self) -> None:
        while self._timer_running:
            elapsed = int(time.monotonic() - self._start_time)
            mm, ss = divmod(elapsed, 60)
            self._elapsed_text.value = f'{mm:02d}:{ss:02d}'

            # 消费待处理的进度更新（工作线程非阻塞写入）
            pending = self._pending_progress
            if pending is not None:
                self._pending_progress = None
                self._progress_bar.value = pending[0]
                if pending[1]:
                    self._status_text.value = pending[1]

            # 消费待处理的日志行（批量最多 30 条/秒，防止 UI 积压）
            _drained = 0
            while self._pending_logs and _drained < 30:
                line, color = self._pending_logs.popleft()
                self._log_list.controls.append(
                    ft.Text(line, size=11, font_family='Consolas',
                            color=color, selectable=True)
                )
                _drained += 1
            # 限制日志控件总数
            while len(self._log_list.controls) > 120:
                self._log_list.controls.pop(0)

            # 每秒一次整体刷新，避免频繁单控件 update()
            if _drained > 0 or pending is not None:
                try:
                    p = self.page
                    if p is not None:
                        p.update()
                except Exception:
                    pass
            else:
                # 即使无新内容也刷新计时文本
                try:
                    self._elapsed_text.update()
                except Exception:
                    pass

            time.sleep(1.0)

    # ── 显示 / 隐藏 ──────────────────────────────────────────────────────────

    def show(self, message: str = '处理中…') -> None:
        self._status_text.value = message
        self._spinner.visible = True
        self._spinner.color = Palette.PRIMARY
        self._progress_bar.value = 0.0
        self._progress_bar.color = Palette.PRIMARY
        self._elapsed_text.value = '00:00'
        self._log_list.controls.clear()
        self._pending_logs.clear()
        self._pending_progress = None
        self._backdrop.visible = True
        self._panel_wrapper.visible = True
        self._update_overlay()
        self._start_timer()

    def hide(self) -> None:
        self._stop_timer()
        self._backdrop.visible = False
        self._panel_wrapper.visible = False
        # 清理完成后的日志控件，避免隐藏状态下保留过多 UI 控件导致后续卡顿。
        self._log_list.controls.clear()
        self._update_overlay()

    def _update_overlay(self) -> None:
        try:
            self._backdrop.update()
        except Exception:
            pass
        try:
            self._panel_wrapper.update()
        except Exception:
            pass

    def _on_close_click(self, _e) -> None:
        self.hide()

    # ── 事件回调 ─────────────────────────────────────────────────────────────

    def _on_progress(self, value: float, message: str = '', **_kw) -> None:
        # 工作线程非阻塞：只写入 pending，由 _timer_loop 统一刷新
        self._pending_progress = (value, message)

    def _on_done(self, message: str = '完成', **_kw) -> None:
        self._stop_timer()
        self._progress_bar.value = 1.0
        self._status_text.value = message
        self._spinner.visible = False   # 停止旋转圈
        self._try_update()
        threading.Timer(2.5, self.hide).start()

    def _on_error(self, message: str, **_kw) -> None:
        self._stop_timer()
        self._progress_bar.value = 0.0
        self._progress_bar.color = Palette.ERROR
        self._status_text.value = f'错误：{message}'
        self._spinner.color = Palette.ERROR
        self._try_update()

    def _on_log(self, line: str, **_kw) -> None:
        # 根据内容选颜色：✓ 绿色，✗ 红色，其余默认
        if '✓' in line:
            color = Palette.SUCCESS
        elif '✗' in line or '异常' in line or '失败' in line:
            color = Palette.ERROR
        elif line.startswith('▶'):
            color = Palette.PRIMARY_LIGHT
        else:
            color = Palette.TEXT_SECONDARY
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
