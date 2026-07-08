# gui/components/piano_model_dialog.py — Modal dialog for the piano transcription
# checkpoint download (ByteDance piano_transcription_inference, ~172 MB, Zenodo).
#
# Mirrors gui/components/model_download_dialog.py (HOMR weights) but trimmed for
# a single download source — no source picker step, straight to progress.
#
# UX flow (state machine):
#   CONFIRM     — initial. Shows size + destination, [开始下载]/[取消].
#   DOWNLOADING — progress UI. Cancel button stops + closes.
#   ERROR       — error message + [重试][关闭]; 重试 returns to CONFIRM.
#
# Threading: the actual download runs in a daemon Thread (core.omr.audio_runner's
# _ensure_piano_model). UI updates marshal back to the Flet event loop via
# page.loop.call_soon_threadsafe (same pattern as model_download_dialog.py).

from __future__ import annotations

import threading
from typing import Optional, Callable

import flet as ft

from ..app_state import AppState, Event
from ..strings import t
from ..theme import FONT_EMPHASIS


class PianoModelDownloadDialog:
    """Modal dialog wrapping the piano transcription checkpoint download.

    Use:
        dlg = PianoModelDownloadDialog(page, state, on_complete=callback)
        dlg.show()
    show() opens the dialog in CONFIRM state — download does NOT start until
    the user clicks 开始下载. On success, dialog auto-closes,
    state.piano_model_available becomes True, and Event.PIANO_MODEL_CHANGED fires.
    """

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

        self._dialog:       Optional[ft.AlertDialog] = None
        self._status_text:  Optional[ft.Text] = None
        self._overall_text: Optional[ft.Text] = None
        self._overall_bar:  Optional[ft.ProgressBar] = None

    # ── Public API ────────────────────────────────────────────────────────

    def show(self) -> None:
        if self._dialog is None:
            self._dialog = self._build_shell()
        self._render_confirm()
        try:
            self._page.show_dialog(self._dialog)
        except Exception:
            pass

    # ── Dialog rendering by state ─────────────────────────────────────────

    def _build_shell(self) -> ft.AlertDialog:
        return ft.AlertDialog(
            modal=True,
            title=ft.Text(t("piano_download.dialog_title"), size=16, font_family=FONT_EMPHASIS),
        )

    def _render_confirm(self) -> None:
        if self._dialog is None:
            return
        self._dialog.title = ft.Text(t("piano_download.dialog_title"),
                                      size=16, font_family=FONT_EMPHASIS)
        self._dialog.content = ft.Column(
            [ft.Text(t("piano_download.intro_text"), size=12, color=ft.Colors.ON_SURFACE_VARIANT)],
            tight=True, spacing=4, width=420,
        )
        self._dialog.actions = [
            ft.TextButton(t("common.cancel"), on_click=self._on_confirm_cancel),
            ft.ElevatedButton(t("piano_download.button_start_download"), on_click=self._on_confirm_start),
        ]
        try:
            self._page.update()
        except Exception:
            pass

    def _render_progress(self) -> None:
        self._status_text  = ft.Text(t("piano_download.status_preparing"), size=13)
        self._overall_text = ft.Text(t("model_download.progress_initial"), size=12,
                                      color=ft.Colors.ON_SURFACE_VARIANT)
        self._overall_bar  = ft.ProgressBar(value=0, expand=True)

        if self._dialog is None:
            return
        self._dialog.title = ft.Text(t("piano_download.downloading_dialog_title"),
                                      size=16, font_family=FONT_EMPHASIS)
        self._dialog.content = ft.Column(
            [
                self._status_text,
                ft.Container(height=8),
                ft.Row([self._overall_bar, self._overall_text], spacing=8),
            ],
            tight=True, spacing=4, width=420,
        )
        self._dialog.actions = [
            ft.TextButton(t("common.cancel"), on_click=self._on_progress_cancel),
        ]
        try:
            self._page.update()
        except Exception:
            pass

    def _render_error(self, msg: str) -> None:
        if self._dialog is None:
            return
        self._dialog.title = ft.Text(t("model_download.error_dialog_title"),
                                      size=16, font_family=FONT_EMPHASIS)
        self._dialog.content = ft.Column([ft.Text(msg, size=13)], tight=True, spacing=4, width=420)
        self._dialog.actions = [
            ft.TextButton(t("model_download.button_retry"), on_click=self._on_error_retry),
            ft.TextButton(t("common.close"), on_click=self._on_error_close),
        ]
        try:
            self._page.update()
        except Exception:
            pass

    # ── State transitions / button handlers ───────────────────────────────

    def _on_confirm_cancel(self, _e) -> None:
        self._close()
        if self._on_cancel:
            self._on_cancel()

    def _on_confirm_start(self, _e) -> None:
        self._cancel_event = threading.Event()
        self._render_progress()
        self._download_thread = threading.Thread(target=self._run_download, daemon=True)
        self._download_thread.start()

    def _on_progress_cancel(self, _e) -> None:
        self._cancel_event.set()
        self._close()
        if self._on_cancel:
            self._on_cancel()

    def _on_error_retry(self, _e) -> None:
        self._render_confirm()

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
        from core.omr.audio_runner import _ensure_piano_model

        result = _ensure_piano_model(self._on_progress_threadsafe, self._cancel_event)

        if result is None:
            if self._cancel_event.is_set():
                return  # user cancelled — dialog already closed
            self._marshal(self._render_error, t("piano_download.error_generic"))
            return

        def _on_done():
            self._state.piano_model_available = True
            self._state.emit(Event.PIANO_MODEL_CHANGED)
            self._close()
            if self._on_complete:
                self._on_complete()

        self._marshal(_on_done)

    # ── Progress callback (called from download thread) ────────────────────

    def _on_progress_threadsafe(self, value: float, message: str) -> None:
        def _update():
            if self._status_text is None:
                return  # dialog closed or in non-progress state
            self._status_text.value = message
            if self._overall_bar is not None:
                # value 空间是 0.05–0.25（见 audio_runner._ensure_piano_model），
                # 归一化到 0–1 供进度条使用。
                self._overall_bar.value = max(0.0, min(1.0, (value - 0.05) / 0.20))
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
