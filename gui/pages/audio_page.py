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
from ..components.piano_model_dialog import PianoModelDownloadDialog
from ..theme import Palette, FONT_EMPHASIS, section_title
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
        state.on(Event.PIANO_MODEL_CHANGED, self._on_piano_model_changed)
        self._refresh_piano_model_status()
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
        self._sidebar = FileSidebar(
            self._state,
            allowed_suffixes=set(SUPPORTED_AUDIO_SUFFIXES),
            section_title_key='audio.section_title',
            empty_hint_key='audio.empty_hint',
            empty_formats_key='audio.empty_supported_formats',
        )

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

        # 钢琴转录模型（ByteDance，~172 MB，按需下载）管理区，镜像乐谱识别页的
        # 「HOMR 模型管理」小节：状态提示 + 下载/删除按钮。
        self._section_piano_model = section_title(t('audio.section_piano_model'))
        self._piano_model_label = ft.Text('', size=11, color=ft.Colors.ON_SURFACE_VARIANT)
        self._download_model_label = ft.Text(t('audio.button_download_model'))
        self._download_model_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.DOWNLOAD_ROUNDED, size=16), self._download_model_label],
                tight=True, spacing=6, alignment=ft.MainAxisAlignment.CENTER,
            ),
            on_click=self._on_download_model_click,
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )
        self._delete_model_label = ft.Text(t('audio.button_delete_model'))
        self._delete_model_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.DELETE_OUTLINE_ROUNDED, size=16), self._delete_model_label],
                tight=True, spacing=6, alignment=ft.MainAxisAlignment.CENTER,
            ),
            on_click=self._on_delete_model_click,
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
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
                                ft.Container(height=4),
                                ft.Divider(height=1, thickness=1, color=ft.Colors.OUTLINE_VARIANT),
                                self._section_piano_model,
                                self._download_model_btn,
                                self._delete_model_btn,
                                ft.Container(
                                    content=self._piano_model_label,
                                    padding=ft.Padding.only(left=4),
                                ),
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
        self._sidebar.retranslate()
        self._convert_label.value = t('audio.button_start')
        self._center_title.value = t('audio.center_title')
        self._center_hint.value = t('audio.center_hint')
        self._section_options.value = t('audio.section_options')
        self._engine_note.value = t('audio.engine_note')
        self._open_output_label.value = t('landing.button_open_output_dir')
        self._melody_only_cb.label = t('audio.melody_only')
        self._melody_only_cb.tooltip = t('audio.tooltip_melody_only')
        self._section_piano_model.value = t('audio.section_piano_model')
        self._download_model_label.value = t('audio.button_download_model')
        self._delete_model_label.value = t('audio.button_delete_model')
        self._selected_text.value = (
            t('audio.selected_file', name=self._selected_name) if self._selected_name else ''
        )
        try:
            self.update()
        except Exception:
            pass
        self._refresh_piano_model_status()

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

    # ── Piano transcription model management ────────────────────────────────────
    # 与乐谱识别页的 HOMR 模型管理是同一模式：状态提示 + 下载/删除按钮，均由
    # AppState 里的可用性标志驱动，下载/删除后发事件刷新。

    def _refresh_piano_model_status(self, **_kw) -> None:
        present = self._state.piano_model_available
        self._download_model_btn.disabled = present
        self._delete_model_btn.disabled = not present
        self._piano_model_label.value = t(
            'audio.piano_model_ready' if present else 'audio.piano_model_missing'
        )
        try:
            self._download_model_btn.update()
            self._delete_model_btn.update()
            self._piano_model_label.update()
        except Exception:
            pass

    def _on_piano_model_changed(self, **_kw) -> None:
        self._refresh_piano_model_status()

    def _on_download_model_click(self, _e) -> None:
        if self._state.piano_model_available or self.page is None:
            return
        PianoModelDownloadDialog(self.page, self._state).show()

    def _on_delete_model_click(self, _e) -> None:
        if not self._state.piano_model_available or self.page is None:
            return

        def _do_delete(_ev) -> None:
            self.page.pop_dialog()
            from core.omr.audio_runner import delete_piano_model
            delete_piano_model()
            self._state.piano_model_available = False
            self._state.emit(Event.PIANO_MODEL_CHANGED)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(t('audio.delete_model_dialog_title'), size=15, font_family=FONT_EMPHASIS),
            content=ft.Text(t('audio.delete_model_dialog_body'), size=13, color=ft.Colors.ON_SURFACE),
            actions=[
                ft.TextButton(t('common.cancel'), on_click=lambda _ev: self.page.pop_dialog()),
                ft.FilledButton(t('common.delete'), on_click=_do_delete),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dlg)

    def _prompt_piano_model_download(self) -> None:
        """转换前预检：模型缺失时先弹窗确认下载（镜像 landing 的 HOMR 预检）。

        音频识别没有备用引擎，用户拒绝下载则直接中止（不像 HOMR 预检那样可以
        回退到 Audiveris）。下载完成后自动重新发起本次转换请求。
        """
        if self.page is None:
            return

        def _on_yes(_ev) -> None:
            self.page.pop_dialog()
            PianoModelDownloadDialog(
                self.page, self._state,
                on_complete=lambda: self._on_convert(None),
            ).show()

        def _on_no(_ev) -> None:
            self.page.pop_dialog()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(t('audio.download_prompt_title'), size=16, font_family=FONT_EMPHASIS),
            content=ft.Text(t('audio.download_prompt_body'), size=13),
            actions=[
                ft.TextButton(t('common.cancel'), on_click=_on_no),
                ft.ElevatedButton(t('audio.button_download_now'), on_click=_on_yes),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dlg)

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

        # 模型缺失守卫：与乐谱识别页的 HOMR 预检对应，转换前发现模型未下载，
        # 弹窗确认后台下载（无备用引擎，拒绝则中止本次转换）。
        if not self._state.piano_model_available:
            self._prompt_piano_model_download()
            return

        # 重复文件检查：与「乐谱识别」页一致，命名约定（<stem>_jianpu.pdf）由
        # worker_main.py 统一处理，与引擎无关，此处逻辑可直接复用。
        output_path = output_dir(None)
        existing = [
            src.name for src in checked_audio
            if (output_path / (src.stem + '_jianpu.pdf')).exists()
        ]

        self._skip_dup_cb: Optional[ft.Checkbox] = None
        warn_items: list[ft.Control] = []
        if existing:
            warn_items.append(
                ft.Row([
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED,
                            color=Palette.WARNING, size=16),
                    ft.Text(
                        t("landing.existing_outputs_warning", n=len(existing)),
                        color=Palette.WARNING, size=13,
                    ),
                ], spacing=4)
            )
            for name in existing[:5]:
                warn_items.append(
                    ft.Text(f'  • {name}', size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT)
                )
            if len(existing) > 5:
                warn_items.append(
                    ft.Text(t("landing.existing_outputs_more", n=len(existing) - 5),
                            size=12, color=ft.Colors.OUTLINE)
                )
            self._skip_dup_cb = ft.Checkbox(
                label=t("landing.checkbox_skip_duplicates"),
                value=True,
                active_color=Palette.PRIMARY,
            )
            warn_items.append(ft.Container(height=4))
            warn_items.append(self._skip_dup_cb)

        def _do_confirm(_ev) -> None:
            self.page.pop_dialog()
            skip = bool(self._skip_dup_cb.value) if self._skip_dup_cb is not None else False
            self._start_conversion(
                checked_audio, skip_duplicates=skip, duplicate_files=set(existing),
            )

        if not existing:
            # 无重复文件，跳过确认弹窗直接开始（与乐谱识别页行为一致：仅在有
            # 冲突需要用户决策时才弹窗打断操作）。
            self._start_conversion(checked_audio)
            return

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(
                t("landing.convert_dialog_title", n=len(checked_audio)),
                size=16, font_family=FONT_EMPHASIS,
            ),
            content=ft.Container(
                content=ft.Column(warn_items, tight=True, spacing=8),
                padding=ft.Padding.only(top=6),
                width=400,
            ),
            actions=[
                ft.TextButton(t("common.cancel"), on_click=lambda _ev: self.page.pop_dialog()),
                ft.FilledButton(t("audio.button_start"), on_click=_do_confirm),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dlg)

    def _start_conversion(
        self,
        files: list[Path],
        skip_duplicates: bool = False,
        duplicate_files: Optional[set] = None,
    ) -> None:
        self._conversion_files = files
        self._skip_dup = skip_duplicates
        self._dup_files = duplicate_files or set()
        self._state.is_processing = True
        self._overlay.show(t('audio.running'))
        threading.Thread(target=self._run_conversion, daemon=True).start()

    def _run_conversion(self) -> None:
        files = list(self._conversion_files)
        # engine='auto' — pipeline auto-routes audio to basic-pitch regardless.
        opts = ConversionOptions(
            engine='auto', gen_midi=True,
            melody_only=bool(self._melody_only_cb.value),
            skip_dup=getattr(self, '_skip_dup', False),
            dup_files=list(getattr(self, '_dup_files', set())),
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
        """显示转换结果详情（总览 + 每文件成功/失败信息，滚动列表）。

        镜像 landing_page._show_conversion_results 的模式：之前这里全成功
        才弹持久对话框，失败时只有一条会自动消失的 SnackBar，且不带具体
        失败原因（worker_launcher 早已在 conversion_summary['failed_files']
        里带上了 reason，只是没被读取显示）。
        """
        try:
            if self.page is None:
                return

            summary = self._state.conversion_summary
            if not summary:
                return

            success_count = summary.get('success_count', 0) + summary.get('fallback_count', 0)
            failed_count  = summary.get('failed_count', 0)
            total         = summary.get('total', success_count + failed_count)
            success_files = summary.get('success_files', []) + summary.get('fallback_files', [])
            failed_files  = summary.get('failed_files', [])

            def _stat_chip(label: str, color: str) -> ft.Container:
                return ft.Container(
                    content=ft.Text(label, size=13, color=color, font_family=FONT_EMPHASIS),
                    padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                    border_radius=12,
                    bgcolor=ft.Colors.with_opacity(0.12, color),
                )

            header_row = ft.Row(
                [
                    ft.Text(t('audio.stat_total', n=total), size=14, color=ft.Colors.ON_SURFACE),
                    _stat_chip(t('audio.stat_success', n=success_count), Palette.SUCCESS) if success_count else ft.Container(),
                    _stat_chip(t('audio.stat_failed', n=failed_count), Palette.ERROR) if failed_count else ft.Container(),
                ],
                spacing=8,
                wrap=True,
            )

            list_items: list[ft.Control] = []

            if success_files:
                list_items.append(
                    ft.Container(
                        ft.Text(t('audio.section_success'), size=12, font_family=FONT_EMPHASIS, color=Palette.SUCCESS),
                        padding=ft.Padding.only(top=8, bottom=2),
                    )
                )
                for item in success_files:
                    file_name = item.get('file', t('audio.unknown_file')) if isinstance(item, dict) else item
                    list_items.append(
                        ft.Container(
                            content=ft.Text(file_name, size=12, color=ft.Colors.ON_SURFACE, no_wrap=False),
                            padding=ft.Padding.symmetric(vertical=3),
                        )
                    )

            if failed_files:
                list_items.append(
                    ft.Container(
                        ft.Text(t('audio.section_failed'), size=12, font_family=FONT_EMPHASIS, color=Palette.ERROR),
                        padding=ft.Padding.only(top=10, bottom=2),
                    )
                )
                for item in failed_files:
                    file_name = item.get('file', t('audio.unknown_file')) if isinstance(item, dict) else item
                    reason    = (item.get('reason', '') if isinstance(item, dict) else '') or t('audio.unknown_reason')
                    list_items.append(
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Text(file_name, size=12, color=ft.Colors.ON_SURFACE, no_wrap=False),
                                    ft.Text(
                                        t('audio.list_item_reason', reason=reason),
                                        size=11, color=Palette.ERROR, no_wrap=False,
                                    ),
                                ],
                                spacing=1,
                                tight=True,
                            ),
                            padding=ft.Padding.symmetric(vertical=3),
                        )
                    )

            def _close_dialog(_ev=None):
                if self.page:
                    self.page.pop_dialog()

            def _goto_jianpu(_ev=None):
                _close_dialog()
                self._state.emit(Event.NAVIGATE, name='editor')

            actions: list[ft.Control] = [
                ft.TextButton(t('landing.button_open_output_dir'), on_click=self._on_open_output_dir),
                ft.TextButton(t('common.close'), on_click=_close_dialog),
            ]
            if success_count > 0:
                actions.append(ft.FilledButton(t('audio.button_view_jianpu'), on_click=_goto_jianpu))

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text(t('audio.result_dialog_title'), size=16, font_family=FONT_EMPHASIS),
                content=ft.Column(
                    [
                        header_row,
                        ft.Divider(height=1, thickness=1),
                        ft.Container(
                            content=ft.Column(list_items, tight=True, spacing=0, scroll=ft.ScrollMode.AUTO),
                            height=320,
                            width=460,
                        ),
                    ],
                    tight=True,
                    spacing=8,
                ),
                actions=actions,
                actions_alignment=ft.MainAxisAlignment.END,
            )
            self.page.show_dialog(dialog)
        except Exception:
            pass

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def terminate_worker(self) -> None:
        """Force-terminate any in-flight audio worker subprocess on app close."""
        self._runner.terminate()
