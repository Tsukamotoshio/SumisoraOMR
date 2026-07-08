# gui/pages/audio_page.py — Audio recognition page.
# Left: audio file sidebar; center: info placeholder (audio has no page preview);
# right: recognition options + "Start Recognition" button.
#
# Audio inputs are transcribed by basic-pitch (audio → MIDI → MusicXML) and then
# flow through the same jianpu/staff render chain as scores. The conversion is
# driven by the shared ConversionRunner exactly like the landing page; the
# pipeline auto-routes audio by suffix, so no engine selection is needed here.

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import flet as ft

from ..app_state import AppState, Event
from ..worker_launcher import ConversionOptions, ConversionRunner
from core.app.backend import app_base_dir, output_dir, open_directory
from core.config import SUPPORTED_AUDIO_SUFFIXES
from ..components.file_sidebar import FileSidebar
from ..components.progress_overlay import ProgressOverlay
from ..theme import Palette, FONT_EMPHASIS
from ..strings import t


class AudioPage(ft.Row):
    """Audio recognition: audio file management + "Start Recognition" button."""

    def __init__(self, state: AppState, overlay: ProgressOverlay):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._overlay = overlay
        self._selected_name: Optional[str] = None
        self._runner = ConversionRunner(
            state,
            on_sub_progress=overlay.set_sub_progress,
            on_finished=self._schedule_show_results,
        )
        self._build_ui()
        state.on(Event.FILE_SELECTED, self._on_file_selected)
        self.vertical_alignment = ft.CrossAxisAlignment.STRETCH

    def did_mount(self) -> None:
        # 启动时扫描 Input/ 中已存在的音频文件并加入托盘（乐谱识别页只扫图片/PDF，
        # 音频文件从不会被它收录，所以这里需要单独扫一遍音频后缀）。
        threading.Thread(target=self._scan_input_audio, daemon=True).start()

    def _scan_input_audio(self) -> None:
        try:
            if self.page is None:
                return
            input_dir = app_base_dir() / 'Input'
            if not input_dir.is_dir():
                return
            newly: list[Path] = []
            for suf in sorted(SUPPORTED_AUDIO_SUFFIXES):
                for f in sorted(input_dir.glob(f'*{suf}')):
                    resolved = f.resolve()
                    if resolved not in self._state.pinned_files:
                        self._state.pinned_files.append(resolved)
                        self._state.checked_files.add(resolved)
                        newly.append(resolved)
            if not newly:
                return
            self._state.emit(Event.FILES_CHANGED, files=list(self._state.pinned_files))

            async def _do_update():
                try:
                    self.page.update()
                except Exception:
                    pass
            self.page.run_task(_do_update)  # type: ignore[attr-defined]
        except Exception:
            pass

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._sidebar = FileSidebar(self._state, allowed_suffixes=set(SUPPORTED_AUDIO_SUFFIXES))

        # 中心区：音频无页面预览，展示图标 + 提示 + 当前选中文件名。
        self._selected_text = ft.Text(
            '', size=13, color=ft.Colors.ON_SURFACE_VARIANT,
            text_align=ft.TextAlign.CENTER,
        )
        self._center_title = ft.Text(
            t('audio.center_title'), size=18, font_family=FONT_EMPHASIS,
            color=ft.Colors.ON_SURFACE, text_align=ft.TextAlign.CENTER,
        )
        self._center_hint = ft.Text(
            t('audio.center_hint'), size=13, color=ft.Colors.ON_SURFACE_VARIANT,
            text_align=ft.TextAlign.CENTER,
        )
        center_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.GRAPHIC_EQ_ROUNDED, size=64, color=ft.Colors.OUTLINE),
                    ft.Container(height=12),
                    self._center_title,
                    ft.Container(height=6),
                    self._center_hint,
                    ft.Container(height=10),
                    self._selected_text,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                expand=True,
            ),
            expand=True,
            alignment=ft.Alignment(0, 0),
            padding=ft.Padding.all(24),
        )

        # 右侧选项面板：仅识别相关，无 OMR 引擎 / 超分 / 模型下载。
        self._convert_label = ft.Text(t('audio.button_start'), size=15, font_family=FONT_EMPHASIS)
        self._convert_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, size=20), self._convert_label],
                tight=True, spacing=6, alignment=ft.MainAxisAlignment.CENTER,
            ),
            height=44,
            bgcolor=Palette.PRIMARY,
            color='#FFFFFF',
            on_click=self._on_convert,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                elevation={ft.ControlState.PRESSED: 0, ft.ControlState.DEFAULT: 2},
            ),
        )

        self._open_output_label = ft.Text(t('landing.button_open_output_dir'))
        open_output_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.FOLDER_OPEN_ROUNDED, size=16), self._open_output_label],
                tight=True, spacing=6, alignment=ft.MainAxisAlignment.CENTER,
            ),
            on_click=self._on_open_output_dir,
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        self._section_options = ft.Text(
            t('audio.section_options'), size=15, font_family=FONT_EMPHASIS,
            color=ft.Colors.ON_SURFACE,
        )
        self._engine_note = ft.Text(
            t('audio.engine_note'), size=12, color=ft.Colors.ON_SURFACE_VARIANT,
            expand=True, no_wrap=False,
        )
        self._melody_only_cb = ft.Checkbox(
            label=t('audio.melody_only'),
            value=False,
            active_color=Palette.PRIMARY,
            tooltip=t('audio.tooltip_melody_only'),
        )
        options_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=self._section_options,
                        height=48,
                        padding=ft.Padding.only(left=16, right=16),
                        alignment=ft.Alignment(-1, 0),
                        border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
                    ),
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Container(
                                    content=ft.Row(
                                        [
                                            ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=15,
                                                    color=ft.Colors.ON_SURFACE_VARIANT),
                                            self._engine_note,
                                        ],
                                        spacing=6,
                                        vertical_alignment=ft.CrossAxisAlignment.START,
                                    ),
                                    tooltip=t('audio.tooltip_engine'),
                                    padding=ft.Padding.symmetric(vertical=2),
                                ),
                                self._melody_only_cb,
                                ft.Container(height=4),
                                self._convert_btn,
                                open_output_btn,
                            ],
                            spacing=10,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                        padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                        expand=True,
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            bgcolor=ft.Colors.SURFACE,
            width=250,
            border=ft.Border.only(left=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        self.controls = [
            self._sidebar,
            ft.Container(content=center_panel, expand=True),
            options_panel,
        ]

    def retranslate(self) -> None:
        """Re-apply active-language text (called on Event.LANGUAGE_CHANGED)."""
        self._convert_label.value = t('audio.button_start')
        self._center_title.value = t('audio.center_title')
        self._center_hint.value = t('audio.center_hint')
        self._section_options.value = t('audio.section_options')
        self._engine_note.value = t('audio.engine_note')
        self._open_output_label.value = t('landing.button_open_output_dir')
        self._melody_only_cb.label = t('audio.melody_only')
        self._melody_only_cb.tooltip = t('audio.tooltip_melody_only')
        self._selected_text.value = (
            t('audio.selected_file', name=self._selected_name) if self._selected_name else ''
        )
        try:
            self.update()
        except Exception:
            pass

    # ── Event handlers ─────────────────────────────────────────────────────────

    def _on_file_selected(self, path: Path, **_kw) -> None:
        # 仅在本页可见且选中的是音频时更新提示，避免与乐谱识别页互相干扰。
        if path.suffix.lower() in SUPPORTED_AUDIO_SUFFIXES:
            self._selected_name = path.name
            self._selected_text.value = t('audio.selected_file', name=path.name)
        else:
            self._selected_name = None
            self._selected_text.value = ''
        try:
            self._selected_text.update()
        except Exception:
            pass

    def _show_snack(self, msg: str, color: str = Palette.INFO) -> None:
        if self.page is None:
            return
        try:
            self.page.show_dialog(ft.SnackBar(
                content=ft.Text(msg, color='#FFFFFF'), bgcolor=color, duration=3500,
            ))
        except Exception:
            pass

    def _on_open_output_dir(self, _e) -> None:
        try:
            open_directory(output_dir(None))
        except Exception as exc:
            self._show_snack(t('common.open_dir_failed', exc=exc), Palette.ERROR)

    # ── Conversion ─────────────────────────────────────────────────────────────

    def _on_convert(self, _e) -> None:
        # 仅处理勾选的音频文件（乐谱文件由「乐谱识别」页处理）。
        checked_audio = [
            f for f in self._state.pinned_files
            if f in self._state.checked_files and f.suffix.lower() in SUPPORTED_AUDIO_SUFFIXES
        ]
        if not checked_audio:
            self._show_snack(t('audio.error_no_audio'), Palette.WARNING)
            return
        if self._state.is_processing:
            return

        self._conversion_files = checked_audio
        self._state.is_processing = True
        self._overlay.show(t('audio.running'))
        threading.Thread(target=self._run_conversion, daemon=True).start()

    def _run_conversion(self) -> None:
        files = list(self._conversion_files)
        # engine='auto' — pipeline auto-routes audio to basic-pitch regardless.
        opts = ConversionOptions(
            engine='auto', gen_midi=True,
            melody_only=bool(self._melody_only_cb.value),
        )
        # 单 worker 顺序处理（音频转录本身受 CPU/GPU 限制，串行足够且更稳）。
        self._runner.run(files, 1, opts)

    def _schedule_show_results(self) -> None:
        p = self.page
        if p is not None:
            async def _do():
                self._show_results()
            p.run_task(_do)  # type: ignore[attr-defined]
        else:
            self._show_results()

    def _show_results(self) -> None:
        summary = self._state.conversion_summary
        if not summary:
            return
        ok = summary.get('success_count', 0) + summary.get('fallback_count', 0)
        fail = summary.get('failed_count', 0)
        total = summary.get('total', ok + fail)
        color = Palette.SUCCESS if fail == 0 and ok > 0 else (
            Palette.WARNING if ok > 0 else Palette.ERROR
        )
        self._show_snack(t('audio.result_summary', ok=ok, fail=fail, total=total), color)
        if ok > 0:
            self._show_view_jianpu_dialog()

    def _show_view_jianpu_dialog(self) -> None:
        if self.page is None:
            return

        def _goto_jianpu(_ev):
            self.page.pop_dialog()
            self._state.emit(Event.NAVIGATE, name='editor')

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(t('audio.center_title'), size=16, font_family=FONT_EMPHASIS),
            content=ft.Text(
                t('audio.result_summary',
                  ok=self._state.conversion_summary.get('success_count', 0)
                  + self._state.conversion_summary.get('fallback_count', 0),
                  fail=self._state.conversion_summary.get('failed_count', 0),
                  total=self._state.conversion_summary.get('total', 0)),
                size=13, color=ft.Colors.ON_SURFACE,
            ),
            actions=[
                ft.TextButton(t('common.close'), on_click=lambda _ev: self.page.pop_dialog()),
                ft.FilledButton(t('audio.button_view_jianpu'), on_click=_goto_jianpu),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        try:
            self.page.show_dialog(dlg)
        except Exception:
            pass

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def terminate_worker(self) -> None:
        """Force-terminate any in-flight audio worker subprocess on app close."""
        self._runner.terminate()
