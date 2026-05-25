# gui/components/vlm_download_dialog.py — Modal dialog for Qwen2.5-VL GGUF download
# Three-state UI: PICKER → DOWNLOADING → ERROR
# Threading mirrors model_download_dialog.py exactly.
from __future__ import annotations

import threading
from typing import Callable, Optional

import flet as ft

from ..app_state import AppState


class VlmDownloadDialog:
    """Modal dialog for downloading Qwen2.5-VL GGUF weights.

    Usage:
        dlg = VlmDownloadDialog(page, state, on_complete=callback)
        dlg.show()
    """

    _SOURCE_OPTIONS = [
        ('huggingface', 'HuggingFace', 'HuggingFace — unsloth/Qwen2.5-VL-7B-Instruct-GGUF'),
    ]

    def __init__(
        self,
        page: ft.Page,
        state: AppState,
        on_complete: Optional[Callable[[], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
    ):
        self._page = page
        self._state = state
        self._on_complete = on_complete
        self._on_cancel = on_cancel
        self._cancel_event = threading.Event()
        self._selected_source = 'huggingface'
        self._download_thread: Optional[threading.Thread] = None
        self._dialog: Optional[ft.AlertDialog] = None
        self._source_selector: Optional[ft.Dropdown] = None
        self._source_desc: Optional[ft.Text] = None
        self._source_text: Optional[ft.Text] = None
        self._file_text: Optional[ft.Text] = None
        self._size_text: Optional[ft.Text] = None
        self._overall_text: Optional[ft.Text] = None
        self._overall_bar: Optional[ft.ProgressBar] = None

    def show(self) -> None:
        if self._dialog is None:
            self._dialog = self._build_shell()
        self._render_picker()
        try:
            self._page.show_dialog(self._dialog)
        except Exception:
            pass

    def _build_shell(self) -> ft.AlertDialog:
        """Build a bare AlertDialog whose title/content/actions are set per state."""
        return ft.AlertDialog(modal=True, title=ft.Text(''))

    # ── States ────────────────────────────────────────────────────────────────

    def _render_picker(self) -> None:
        self._source_selector = ft.Dropdown(
            label='下载源',
            value='huggingface',
            options=[ft.dropdown.Option(n, label) for n, label, _ in self._SOURCE_OPTIONS],
            on_select=self._on_picker_source_changed,
            text_size=12,
            width=400,
        )
        self._source_desc = ft.Text(
            self._SOURCE_OPTIONS[0][2],
            size=12, color=ft.Colors.ON_SURFACE_VARIANT, italic=True,
        )
        intro = ft.Text(
            '将下载两个文件：\n'
            '• 主模型（Q4_K_M）约 4.4 GB\n'
            '• 视觉投影模型（mmproj-F16）约 1.3 GB\n'
            '共约 5.7 GB，存入 models/vlm/ 目录。\n'
            '需要能访问 HuggingFace（大陆需 VPN）。',
            size=12, color=ft.Colors.ON_SURFACE_VARIANT,
        )
        if self._dialog is None:
            return
        self._dialog.title = ft.Text('下载 Qwen2.5-VL 模型权重', size=16, weight=ft.FontWeight.W_600)
        self._dialog.content = ft.Column(
            [intro, ft.Container(height=8), self._source_selector, self._source_desc],
            tight=True, spacing=4, width=420,
        )
        self._dialog.actions = [
            ft.TextButton('取消', on_click=self._on_picker_cancel),
            ft.ElevatedButton('开始下载', on_click=self._on_picker_confirm),
        ]
        try:
            self._page.update()
        except Exception:
            pass

    def _render_progress(self) -> None:
        self._source_text  = ft.Text('当前源: 测试中…', size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._file_text    = ft.Text('准备中…', size=13)
        self._size_text    = ft.Text('', size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._overall_text = ft.Text('0 / ? MB', size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._overall_bar  = ft.ProgressBar(value=0, expand=True)
        if self._dialog is None:
            return
        self._dialog.title = ft.Text('正在下载 Qwen2.5-VL 模型权重', size=16, weight=ft.FontWeight.W_600)
        self._dialog.content = ft.Column(
            [
                self._source_text,
                ft.Container(height=4),
                self._file_text,
                self._size_text,
                ft.Container(height=8),
                ft.Row([self._overall_bar, self._overall_text], spacing=8),
            ],
            tight=True, spacing=4, width=420,
        )
        self._dialog.actions = [ft.TextButton('取消', on_click=self._on_progress_cancel)]
        try:
            self._page.update()
        except Exception:
            pass

    def _render_error(self, msg: str) -> None:
        if self._dialog is None:
            return
        self._dialog.title = ft.Text('下载出错', size=16, weight=ft.FontWeight.W_600)
        self._dialog.content = ft.Column([ft.Text(msg, size=13)], tight=True, spacing=4, width=420)
        self._dialog.actions = [
            ft.TextButton('重试', on_click=self._on_error_retry),
            ft.TextButton('关闭', on_click=self._on_error_close),
        ]
        try:
            self._page.update()
        except Exception:
            pass

    # ── Handlers ─────────────────────────────────────────────────────────────

    def _on_picker_source_changed(self, e) -> None:
        name = e.control.value
        if self._source_desc is None:
            return
        for n, _, desc in self._SOURCE_OPTIONS:
            if n == name:
                self._source_desc.value = desc
                break
        try:
            self._source_desc.update()
        except Exception:
            pass

    def _on_picker_cancel(self, _) -> None:
        self._close()
        if self._on_cancel:
            self._on_cancel()

    def _on_picker_confirm(self, _) -> None:
        self._selected_source = (self._source_selector.value or 'auto') if self._source_selector else 'auto'
        self._cancel_event = threading.Event()
        self._render_progress()
        self._download_thread = threading.Thread(target=self._run_download, daemon=True)
        self._download_thread.start()

    def _on_error_retry(self, _) -> None:
        self._cancel_event.set()          # stop any still-running download thread
        self._cancel_event = threading.Event()   # fresh event for next attempt
        self._render_picker()

    def _on_progress_cancel(self, _) -> None:
        self._cancel_event.set()
        self._close()
        if self._on_cancel:
            self._on_cancel()

    def _on_error_close(self, _) -> None:
        self._close()
        if self._on_cancel:
            self._on_cancel()

    def _close(self) -> None:
        try:
            self._page.pop_dialog()
        except Exception:
            pass

    # ── Download worker ───────────────────────────────────────────────────────

    def _run_download(self) -> None:
        from core.app.backend import vlm_models_dir
        from core.vlm.gguf_downloader import (
            download_all_weights,
            base_url_for_name,
            DownloadCancelled,
            HashMismatch,
            NoSourceAvailable,
        )
        forced = base_url_for_name(self._selected_source)
        my_cancel = self._cancel_event
        try:
            download_all_weights(
                vlm_models_dir(),
                self._on_progress_cb,
                my_cancel,
                self._on_source_cb,
                forced_base_url=forced,
            )
        except DownloadCancelled:
            return
        except NoSourceAvailable:
            self._marshal(self._render_error, '下载失败 — 请检查网络连接')
            return
        except HashMismatch as exc:
            self._marshal(self._render_error, f'权重文件校验失败：{exc}')
            return
        except Exception as exc:
            self._marshal(self._render_error, f'下载失败：{exc}')
            return

        def _done():
            self._state.vlm_available = True
            self._close()
            if self._on_complete:
                self._on_complete()

        self._marshal(_done)

    def _on_progress_cb(self, idx, fname, file_done, file_total,
                         overall_done, overall_total, total_files):
        def _update():
            if self._file_text is None:
                return
            display = fname[:50] + ('…' if len(fname) > 50 else '')
            self._file_text.value = f'下载中 ({idx + 1}/{total_files}): {display}'
            if file_total > 0:
                pct = file_done * 100 // file_total
                self._size_text.value = (
                    f'{file_done // 1024 // 1024} / {file_total // 1024 // 1024} MB ({pct}%)'
                )
            else:
                self._size_text.value = f'{file_done // 1024 // 1024} MB'
            if overall_total > 0 and self._overall_bar is not None:
                self._overall_bar.value = overall_done / overall_total
                self._overall_text.value = (
                    f'{overall_done // 1024 // 1024} / {overall_total // 1024 // 1024} MB'
                )
            try:
                self._page.update()
            except Exception:
                pass
        self._marshal(_update)

    def _on_source_cb(self, source: str) -> None:
        def _update():
            if self._source_text is None:
                return
            is_modelscope = 'modelscope' in source
            if self._selected_source == 'auto':
                label = 'ModelScope（自动选择）' if is_modelscope else 'HuggingFace（自动选择）'
            else:
                label = 'ModelScope' if is_modelscope else 'HuggingFace（备用）'
            self._source_text.value = f'当前源: {label}'
            try:
                self._page.update()
            except Exception:
                pass
        self._marshal(_update)

    def _marshal(self, fn, *args) -> None:
        try:
            self._page.loop.call_soon_threadsafe(lambda: fn(*args))
        except Exception:
            pass
