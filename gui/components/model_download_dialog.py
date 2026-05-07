# gui/components/model_download_dialog.py — Modal dialog for HOMR weight download
#
# UX flow (state machine):
#   PICKER     — initial. User picks source + clicks 开始下载.
#   DOWNLOADING — progress UI. Cancel button stops + closes.
#   ERROR      — error message + [重试][关闭]; 重试 returns to PICKER.
#
# Threading: the actual download runs in a daemon Thread. UI updates marshal
# back to the Flet event loop via page.loop.call_soon_threadsafe (same pattern
# as gui/components/pdf_viewer.py).

from __future__ import annotations

import threading
from typing import Optional, Callable

import flet as ft

from ..app_state import AppState, Event


class ModelDownloadDialog:
    """Modal dialog wrapping the HOMR weight download orchestrator.

    Use:
        dlg = ModelDownloadDialog(page, state, on_complete=callback)
        dlg.show()
    show() opens the dialog in PICKER state — download does NOT start until
    the user clicks 开始下载. On success, dialog auto-closes,
    state.homr_available becomes True, and Event.MODELS_DOWNLOADED is emitted.
    """

    # 下载源选项 (name, label, description). 'auto' 默认（探测最快源）。
    _SOURCE_OPTIONS = [
        ('auto',       '自动选择',          '自动检测延迟最低的可用源（推荐）'),
        ('modelscope', 'ModelScope',       'ModelScope CDN — 大陆访问优先'),
        ('github',     'GitHub Releases',  'GitHub — 海外访问优先'),
    ]

    def __init__(self,
                 page: ft.Page,
                 state: AppState,
                 on_complete: Optional[Callable[[], None]] = None,
                 on_cancel: Optional[Callable[[], None]] = None):
        self._page = page
        self._state = state
        self._on_complete = on_complete
        self._on_cancel = on_cancel
        self._cancel_event = threading.Event()
        self._download_thread: Optional[threading.Thread] = None

        # Persistent UI controls (built once in show()).
        self._dialog:          Optional[ft.AlertDialog] = None
        self._source_selector: Optional[ft.Dropdown] = None
        self._source_desc:     Optional[ft.Text] = None
        self._source_text:     Optional[ft.Text] = None
        self._file_text:       Optional[ft.Text] = None
        self._size_text:       Optional[ft.Text] = None
        self._overall_text:    Optional[ft.Text] = None
        self._overall_bar:     Optional[ft.ProgressBar] = None

    # ── Public API ────────────────────────────────────────────────────────

    def show(self) -> None:
        if self._dialog is None:
            self._dialog = self._build_shell()
        self._render_picker()
        try:
            self._page.show_dialog(self._dialog)
        except Exception:
            pass

    # ── Dialog rendering by state ─────────────────────────────────────────

    def _build_shell(self) -> ft.AlertDialog:
        """Build a bare AlertDialog whose title/content/actions are set per state."""
        return ft.AlertDialog(
            modal=True,
            title=ft.Text("HOMR 模型权重", size=16, weight=ft.FontWeight.W_600),
        )

    def _render_picker(self) -> None:
        """PICKER state: source selector + confirm/cancel buttons."""
        self._source_selector = ft.Dropdown(
            label="下载源",
            value='auto',
            options=[ft.dropdown.Option(name, label)
                     for name, label, _desc in self._SOURCE_OPTIONS],
            on_select=self._on_picker_source_changed,
            text_size=12,
            width=400,
        )
        self._source_desc = ft.Text(
            self._SOURCE_OPTIONS[0][2],
            size=12, color=ft.Colors.ON_SURFACE_VARIANT, italic=True,
        )
        intro = ft.Text(
            "选择下载源后点击「开始下载」。HOMR 模型约 292 MB，下载到 models/ 目录。",
            size=12, color=ft.Colors.ON_SURFACE_VARIANT,
        )

        if self._dialog is None:
            return
        self._dialog.title = ft.Text("下载 HOMR 模型权重",
                                     size=16, weight=ft.FontWeight.W_600)
        self._dialog.content = ft.Column(
            [
                intro,
                ft.Container(height=8),
                self._source_selector,
                self._source_desc,
            ],
            tight=True, spacing=4, width=420,
        )
        self._dialog.actions = [
            ft.TextButton("取消",     on_click=self._on_picker_cancel),
            ft.ElevatedButton("开始下载", on_click=self._on_picker_confirm),
        ]
        try:
            self._page.update()
        except Exception:
            pass

    def _render_progress(self) -> None:
        """DOWNLOADING state: progress UI + cancel button."""
        self._source_text  = ft.Text("当前源: 测试中…",
                                     size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._file_text    = ft.Text("准备中…", size=13)
        self._size_text    = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._overall_text = ft.Text("0 / ? MB", size=12,
                                     color=ft.Colors.ON_SURFACE_VARIANT)
        self._overall_bar  = ft.ProgressBar(value=0, expand=True)

        if self._dialog is None:
            return
        self._dialog.title = ft.Text("正在下载 HOMR 模型权重",
                                     size=16, weight=ft.FontWeight.W_600)
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
        self._dialog.actions = [
            ft.TextButton("取消", on_click=self._on_progress_cancel),
        ]
        try:
            self._page.update()
        except Exception:
            pass

    def _render_error(self, msg: str) -> None:
        """ERROR state: error message + retry/close buttons."""
        if self._dialog is None:
            return
        self._dialog.title = ft.Text("下载出错",
                                     size=16, weight=ft.FontWeight.W_600)
        self._dialog.content = ft.Column(
            [ft.Text(msg, size=13)],
            tight=True, spacing=4, width=420,
        )
        self._dialog.actions = [
            ft.TextButton("重试", on_click=self._on_error_retry),
            ft.TextButton("关闭", on_click=self._on_error_close),
        ]
        try:
            self._page.update()
        except Exception:
            pass

    # ── State transitions / button handlers ───────────────────────────────

    def _on_picker_source_changed(self, e) -> None:
        """User picked a different source from the dropdown — update description."""
        name = e.control.value
        if self._source_desc is None:
            return
        for opt_name, _label, desc in self._SOURCE_OPTIONS:
            if opt_name == name:
                self._source_desc.value = desc
                break
        try:
            self._source_desc.update()
        except Exception:
            pass

    def _on_picker_cancel(self, _e) -> None:
        self._close()
        if self._on_cancel:
            self._on_cancel()

    def _on_picker_confirm(self, _e) -> None:
        # Capture the selected source BEFORE swapping UI so the value isn't lost.
        self._selected_source = (
            self._source_selector.value if self._source_selector else 'auto'
        )
        self._cancel_event = threading.Event()
        self._render_progress()
        self._download_thread = threading.Thread(
            target=self._run_download, daemon=True,
        )
        self._download_thread.start()

    def _on_progress_cancel(self, _e) -> None:
        self._cancel_event.set()
        self._close()
        if self._on_cancel:
            self._on_cancel()

    def _on_error_retry(self, _e) -> None:
        # Back to picker so user can switch source if they want
        self._render_picker()

    def _on_error_close(self, _e) -> None:
        self._close()
        if self._on_cancel:
            self._on_cancel()

    def _close(self) -> None:
        try:
            self._page.pop_dialog()
        except Exception:
            pass

    # ── Download worker ───────────────────────────────────────────────────

    def _run_download(self) -> None:
        from core.app.backend import models_dir
        from core.omr.homr_downloader import (
            download_all_weights,
            base_url_for_name,
            DownloadCancelled,
            HashMismatch,
            NoSourceAvailable,
        )

        forced = base_url_for_name(getattr(self, '_selected_source', 'auto'))
        my_cancel = self._cancel_event

        try:
            download_all_weights(
                models_dir(),
                self._on_progress_threadsafe,
                my_cancel,
                self._on_source_threadsafe,
                forced_base_url=forced,
            )
        except DownloadCancelled:
            return  # dialog closed or user cancelled
        except NoSourceAvailable:
            self._marshal(self._render_error, "下载失败 — 请检查网络连接")
            return
        except HashMismatch as e:
            self._marshal(self._render_error, f"权重文件校验失败：{e}")
            return
        except Exception as e:
            self._marshal(self._render_error, f"下载失败：{e}")
            return

        # Success
        def _on_done():
            self._state.homr_available = True
            self._state.emit(Event.MODELS_DOWNLOADED)
            from core.app.backend import models_dir as _models_dir
            flag = _models_dir() / '.pending_download'
            if flag.exists():
                try:
                    flag.unlink()
                except Exception:
                    pass
            self._close()
            if self._on_complete:
                self._on_complete()

        self._marshal(_on_done)

    # ── Progress callbacks (called from download thread) ──────────────────

    def _on_progress_threadsafe(self, idx, fname, file_done, file_total,
                                overall_done, overall_total, total_files):
        def _update():
            if self._file_text is None:
                return  # dialog closed or in non-progress state
            display_name = fname[:50] + ('…' if len(fname) > 50 else '')
            self._file_text.value = f"下载中 ({idx + 1}/{total_files}): {display_name}"
            if file_total > 0:
                pct = file_done * 100 // file_total
                self._size_text.value = (
                    f"{file_done // 1024 // 1024} / "
                    f"{file_total // 1024 // 1024} MB ({pct}%)"
                )
            else:
                self._size_text.value = f"{file_done // 1024 // 1024} MB"
            if overall_total > 0 and self._overall_bar is not None:
                self._overall_bar.value = overall_done / overall_total
                self._overall_text.value = (
                    f"{overall_done // 1024 // 1024} / "
                    f"{overall_total // 1024 // 1024} MB"
                )
            try:
                self._page.update()
            except Exception:
                pass
        self._marshal(_update)

    def _on_source_threadsafe(self, source: str) -> None:
        def _update():
            if self._source_text is None:
                return
            is_modelscope = "modelscope" in source
            sel = getattr(self, '_selected_source', 'auto')
            if sel == 'auto':
                label = "ModelScope" if is_modelscope else "GitHub（备用）"
            else:
                label = "ModelScope" if is_modelscope else "GitHub"
            self._source_text.value = f"当前源: {label}"
            try:
                self._page.update()
            except Exception:
                pass
        self._marshal(_update)

    def _marshal(self, fn, *args) -> None:
        """Schedule fn(*args) on the Flet event loop from a background thread."""
        try:
            self._page.loop.call_soon_threadsafe(lambda: fn(*args))
        except Exception:
            pass
