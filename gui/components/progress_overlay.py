# gui/components/progress_overlay.py — 进度条弹出对话框

from __future__ import annotations

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
            new_val = f'{mm:02d}:{ss:02d}'
            if self._elapsed_text.value != new_val:
                self._elapsed_text.value = new_val
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
        self._backdrop.visible = True
        self._panel_wrapper.visible = True
        self._update_overlay()
        self._start_timer()

    def hide(self) -> None:
        self._stop_timer()
        self._backdrop.visible = False
        self._panel_wrapper.visible = False
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
        self._progress_bar.value = value
        if message:
            self._status_text.value = message
        self._try_update()

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
        self._log_list.controls.append(
            ft.Text(line, size=11, font_family='Consolas',
                    color=color, selectable=True)
        )
        try:
            self._log_list.update()
        except Exception:
            pass

    def _try_update(self) -> None:
        for ctrl in (self._progress_bar, self._status_text, self._spinner):
            try:
                ctrl.update()
            except Exception:
                pass
