# gui/pages/transposer_page.py — 五线谱移调功能页面
# 双栏布局：左侧原调预览，右侧实时渲染移调后预览。
# 使用 core/transposer.py 处理移调。

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import flet as ft

from ..app_state import AppState, Event
from core.app.backend import xml_scores_dir, build_dir, open_directory
from ..components.pdf_viewer import PdfViewer
from ..strings import t
from ..theme import Palette, section_title, FONT_EMPHASIS
from core.notation.transposer import INTERVALS, DIATONIC_DEGREES, key_display_cn


# 按五度圈排列的调名列表
_KEYS = ['Cb', 'Gb', 'Db', 'Ab', 'Eb', 'Bb', 'F', 'C', 'G', 'D', 'A', 'E', 'B', 'F#', 'C#']
_KEY_DISPLAY = {k: k.replace('#', '♯').replace('b', '♭') for k in _KEYS}

_DIRECTIONS_3 = [
    ('closest', t("transposer.dir_recent")),
    ('up',      t("transposer.dir_up")),
    ('down',    t("transposer.dir_down")),
]
_DIRECTIONS_2 = [
    ('up',   t("transposer.dir_up")),
    ('down', t("transposer.dir_down")),
]

_DD_STYLE = dict(
    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
    color=ft.Colors.ON_SURFACE,
    text_size=14,
    border_color=Palette.BORDER_BLUE,
    focused_border_color=Palette.PRIMARY,
    dense=True,
)


def _dd(label: str, value: str, options, width: int) -> ft.Dropdown:
    return ft.Dropdown(label=label, value=value, options=options, width=width, **_DD_STYLE)  # type: ignore[arg-type]


class TransposerPage(ft.Column):
    """移调功能：三种移调模式，左右对比预览，一键导出。"""

    def __init__(self, state: AppState):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._orig_mxl: Optional[Path] = None
        self._transposed_mxl: Optional[Path] = None
        self._orig_render_token: int = 0
        self._trans_render_token: int = 0
        self._auto_detect_token: int = 0
        self._transpose_token: int = 0
        self._export_token: int = 0
        self._orig_export_token: int = 0

        # ── 移调模式状态 ────────────────────────────────────────────────────
        self._mode: str = 'interval'  # 'interval' | 'key' | 'diatonic'
        self._interval_name: str = t("transposer.interval_default")
        self._interval_dir: str = 'up'
        self._interval_keysig: bool = True
        self._key_from: str = 'C'
        self._key_to: str = 'G'
        self._key_dir: str = 'closest'
        self._key_keysig: bool = True
        self._diatonic_degree: str = t("transposer.degree_default")
        self._diatonic_dir: str = 'up'

        self._file_picker = ft.FilePicker()
        self._export_dir_picker = ft.FilePicker()
        self._xml_dir = xml_scores_dir()
        self._build_adv_dialog()
        self._build_ui()
        state.on(Event.MXL_READY,      self._on_mxl_ready)
        state.on(Event.FILE_SELECTED,  self._on_file_selected)

    def did_mount(self):
        self.page._services.register_service(self._file_picker)       # type: ignore[attr-defined]
        self.page._services.register_service(self._export_dir_picker) # type: ignore[attr-defined]

    # ── 构建高级选项对话框 ────────────────────────────────────────────────────

    def _build_adv_dialog(self) -> None:
        _iv_opts  = [ft.dropdown.Option(iv.name) for iv in INTERVALS]
        _dir2_opts = [ft.dropdown.Option(key=v, text=t) for v, t in _DIRECTIONS_2]
        _dir3_opts = [ft.dropdown.Option(key=v, text=t) for v, t in _DIRECTIONS_3]
        _key_opts  = [ft.dropdown.Option(key=k, text=_KEY_DISPLAY[k]) for k in _KEYS]
        _diat_opts = [ft.dropdown.Option(name) for name, _ in DIATONIC_DEGREES]

        # 按音程
        self._adv_iv_dd  = _dd(t("transposer.label_interval"), t("transposer.interval_default"), _iv_opts, 140)
        self._adv_iv_dir = _dd(t("transposer.quick_label_direction"), 'up', _dir3_opts, 100)
        self._adv_iv_ks  = ft.Checkbox(label=t("transposer.checkbox_transpose_key_signature_adv"), value=True)
        self._adv_iv_sec = ft.Column(
            [ft.Row([self._adv_iv_dd, self._adv_iv_dir], spacing=8), self._adv_iv_ks],
            spacing=8, visible=True,
        )

        # 按调
        self._adv_key_from = _dd(t("transposer.label_from_key"), 'C', _key_opts, 110)
        self._adv_key_to   = _dd(t("transposer.label_to_key"), 'G', _key_opts, 110)
        self._adv_key_dir  = _dd(t("transposer.quick_label_direction"), 'closest', _dir3_opts, 100)
        self._adv_key_ks   = ft.Checkbox(label=t("transposer.checkbox_transpose_key_signature_adv"), value=True)
        self._adv_key_sec  = ft.Column(
            [
                ft.Row(
                    [self._adv_key_from,
                     ft.Text(t("transposer.arrow"), color=ft.Colors.ON_SURFACE_VARIANT, size=15),
                     self._adv_key_to,
                     self._adv_key_dir],
                    spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self._adv_key_ks,
            ],
            spacing=8, visible=False,
        )

        # 全音移调
        self._adv_diat_deg = _dd(t("transposer.label_degree"), t("transposer.degree_default"), _diat_opts, 100)
        self._adv_diat_dir = _dd(t("transposer.quick_label_direction"), 'up', _dir2_opts, 100)
        self._adv_diat_sec = ft.Column(
            [ft.Row([self._adv_diat_deg, self._adv_diat_dir], spacing=8)],
            visible=False,
        )

        self._adv_radio_iv   = ft.Radio(value='interval', label=t("transposer.mode_interval"))
        self._adv_radio_key  = ft.Radio(value='key',      label=t("transposer.mode_key"))
        self._adv_radio_diat = ft.Radio(value='diatonic', label=t("transposer.mode_chromatic"))
        self._adv_mode_radio = ft.RadioGroup(
            value='interval',
            on_change=self._on_adv_mode_change,
            content=ft.Row([
                self._adv_radio_iv,
                self._adv_radio_key,
                self._adv_radio_diat,
            ], spacing=16),
        )

        self._adv_title = ft.Text(t("transposer.advanced_options_title"), size=16, font_family=FONT_EMPHASIS)
        self._adv_cancel_btn = ft.TextButton(t("common.cancel"), on_click=lambda _: self.page.pop_dialog())
        self._adv_confirm_btn = ft.FilledButton(t("common.confirm"), on_click=self._confirm_adv_dialog)
        self._adv_dialog = ft.AlertDialog(
            modal=True,
            title=self._adv_title,
            content=ft.Container(
                content=ft.Column(
                    [
                        self._adv_mode_radio,
                        ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                        self._adv_iv_sec,
                        self._adv_key_sec,
                        self._adv_diat_sec,
                    ],
                    spacing=12, tight=True,
                ),
                width=440,
                padding=ft.Padding.only(top=4),
            ),
            actions=[
                self._adv_cancel_btn,
                self._adv_confirm_btn,
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def _on_adv_mode_change(self, e) -> None:
        mode = e.control.value
        self._adv_iv_sec.visible   = mode == 'interval'
        self._adv_key_sec.visible  = mode == 'key'
        self._adv_diat_sec.visible = mode == 'diatonic'
        try:
            self._adv_dialog.update()
        except Exception:
            pass

    def _open_adv_dialog(self, _e) -> None:
        self._adv_mode_radio.value = self._mode
        self._adv_iv_sec.visible   = self._mode == 'interval'
        self._adv_key_sec.visible  = self._mode == 'key'
        self._adv_diat_sec.visible = self._mode == 'diatonic'
        self._adv_iv_dd.value  = self._interval_name
        self._adv_iv_dir.value = self._interval_dir
        self._adv_iv_ks.value  = self._interval_keysig
        self._adv_key_from.value = self._key_from
        self._adv_key_to.value   = self._key_to
        self._adv_key_dir.value  = self._key_dir
        self._adv_key_ks.value   = self._key_keysig
        self._adv_diat_deg.value = self._diatonic_degree
        self._adv_diat_dir.value = self._diatonic_dir
        self.page.show_dialog(self._adv_dialog)  # type: ignore[attr-defined]

    def _confirm_adv_dialog(self, _e) -> None:
        mode = self._adv_mode_radio.value or 'interval'
        self._mode = mode
        if mode == 'interval':
            self._interval_name  = self._adv_iv_dd.value  or t("transposer.fallback_interval")
            self._interval_dir   = self._adv_iv_dir.value or 'up'
            self._interval_keysig = bool(self._adv_iv_ks.value)
            self._quick_iv_dd.value  = self._interval_name
            self._quick_dir_dd.value = self._interval_dir
            self._quick_keysig_cb.value = self._interval_keysig
        elif mode == 'key':
            self._key_from  = self._adv_key_from.value or 'C'
            self._key_to    = self._adv_key_to.value   or 'G'
            self._key_dir   = self._adv_key_dir.value  or 'closest'
            self._key_keysig = bool(self._adv_key_ks.value)
        elif mode == 'diatonic':
            self._diatonic_degree = self._adv_diat_deg.value or t("transposer.degree_default")
            self._diatonic_dir    = self._adv_diat_dir.value or 'up'
        self.page.pop_dialog()  # type: ignore[attr-defined]
        try:
            self.page.update(self._quick_iv_dd, self._quick_dir_dd, self._quick_keysig_cb)  # type: ignore[attr-defined]
        except Exception:
            pass
        # 确认后自动触发移调预览
        self._on_transpose(None)

    # ── 构建主 UI ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        _iv_opts   = [ft.dropdown.Option(iv.name) for iv in INTERVALS]
        _dir2_opts = [ft.dropdown.Option(key=v, text=t) for v, t in _DIRECTIONS_2]

        self._quick_iv_dd  = _dd(t("transposer.mode_interval"), t("transposer.interval_default"), _iv_opts, 140)
        self._quick_dir_dd = _dd(t("transposer.quick_label_direction"), 'up', _dir2_opts, 100)
        self._quick_iv_dd.on_select  = self._on_quick_change  # type: ignore[attr-defined]
        self._quick_dir_dd.on_select = self._on_quick_change  # type: ignore[attr-defined]

        self._quick_keysig_cb = ft.Checkbox(
            label=t("transposer.checkbox_transpose_key_signature"),
            value=True,
            on_change=self._on_quick_change,
        )

        self._adv_btn_label = ft.Text(t("transposer.advanced_options_title"))
        adv_btn = ft.OutlinedButton(
            content=ft.Row(
                [ft.Icon(ft.Icons.TUNE_ROUNDED, size=15), self._adv_btn_label],
                tight=True, spacing=5,
            ),
            on_click=self._open_adv_dialog,
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        self._export_transposed_btn = export_transposed_btn = ft.TextButton(
            t("transposer.button_export_transposed"),
            icon=ft.Icons.PICTURE_AS_PDF_OUTLINED,
            on_click=self._on_export,
            style=ft.ButtonStyle(color=Palette.PRIMARY),
        )

        self._export_original_btn = export_original_btn = ft.TextButton(
            t("transposer.button_export_original"),
            icon=ft.Icons.PICTURE_AS_PDF_OUTLINED,
            on_click=self._on_export_original,
            style=ft.ButtonStyle(color=ft.Colors.ON_SURFACE_VARIANT),
        )

        self._back_btn = back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_ROUNDED,
            icon_size=20,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip=t("transposer.tooltip_back_to_score_preview"),
            on_click=lambda _: self._state.emit(Event.SCORE_TRANSPOSER_BACK),
        )

        self._open_label = ft.Text(t("common.open_score"))
        open_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.FOLDER_OPEN_ROUNDED, size=16), self._open_label],
                tight=True, spacing=6,
            ),
            on_click=self._on_open_click,
            style=ft.ButtonStyle(
                color=Palette.PRIMARY,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, Palette.PRIMARY)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        self._xml_dir_label = ft.Text(t("transposer.button_open_score_dir"))
        xml_dir_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.FOLDER_OPEN_ROUNDED, size=16), self._xml_dir_label],
                tight=True, spacing=6,
            ),
            on_click=self._on_open_xml_dir,
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        right_btns = ft.Row(
            [open_btn, xml_dir_btn],
            spacing=6,
            tight=True,
        )

        top_bar = ft.Container(
            content=ft.Row(
                [
                    back_btn,
                    ft.Column(
                        [
                            (_sec := section_title(t("transposer.section_title"), self._state.dark_mode)),
                            ft.Row(
                                [
                                    self._quick_iv_dd,
                                    self._quick_dir_dd,
                                    self._quick_keysig_cb,
                                    adv_btn,
                                ],
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                spacing=8,
                            ),
                        ],
                        spacing=6,
                        expand=True,
                    ),
                    right_btns,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=ft.Colors.SURFACE,
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        self._section_title = _sec

        # 双栏预览
        self._orig_viewer  = PdfViewer()
        self._trans_viewer = PdfViewer()

        orig_col = ft.Column(
            [
                ft.Container(
                    content=ft.Row(
                        [
                            (_orig_hdr := ft.Text(t("transposer.label_from_key"), size=13, font_family=FONT_EMPHASIS,
                                    color=ft.Colors.ON_SURFACE_VARIANT)),
                            ft.Container(expand=True),
                            export_original_btn,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                    bgcolor=ft.Colors.SURFACE,
                    border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
                ),
                ft.Container(content=self._orig_viewer, expand=True),
            ],
            spacing=0, expand=True,
        )
        trans_col = ft.Column(
            [
                ft.Container(
                    content=ft.Row(
                        [
                            (_trans_hdr := ft.Text(t("transposer.label_transposed_col"), size=13, font_family=FONT_EMPHASIS,
                                    color=Palette.PRIMARY)),
                            ft.Container(expand=True),
                            export_transposed_btn,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                    bgcolor=ft.Colors.SURFACE,
                    border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
                ),
                ft.Container(content=self._trans_viewer, expand=True),
            ],
            spacing=0, expand=True,
        )

        preview_row = ft.Row(
            [
                ft.Container(content=orig_col, expand=True),
                ft.VerticalDivider(width=1, color=ft.Colors.OUTLINE_VARIANT),
                ft.Container(content=trans_col, expand=True),
            ],
            spacing=0, expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        self._status   = ft.Text(t("transposer.status_open_score_first"), size=13, color=ft.Colors.ON_SURFACE_VARIANT)
        self._progress = ft.ProgressBar(
            value=0, visible=False,
            bgcolor=ft.Colors.SURFACE_CONTAINER, color=Palette.PRIMARY, height=3,
        )

        bottom_bar = ft.Container(
            content=ft.Column(
                [
                    ft.Container(content=self._progress, height=3, width=320),
                    ft.Row([self._status], alignment=ft.MainAxisAlignment.START),
                ],
                spacing=8,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            bgcolor=ft.Colors.SURFACE,
            border=ft.Border.only(top=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        self._orig_header = _orig_hdr
        self._trans_header = _trans_hdr

        self.controls = [top_bar, ft.Container(content=preview_row, expand=True), bottom_bar]
        self.expand = True

    def retranslate(self) -> None:
        """Re-apply UI text in the active language (called on Event.LANGUAGE_CHANGED).

        NOTE: interval / key / degree dropdown OPTION texts come from the core
        music vocabulary (INTERVALS / DIATONIC_DEGREES / key symbols) and stay
        Chinese in both languages — only the chrome (labels, buttons, mode radios,
        direction options) switches. The transient status line is left untouched;
        it refreshes to the active language on the next action.
        """
        # Direction option lists are rebuilt with stable keys so the current
        # selection survives (only the visible text changes).
        dir3 = [
            ft.dropdown.Option(key='closest', text=t("transposer.dir_recent")),
            ft.dropdown.Option(key='up',      text=t("transposer.dir_up")),
            ft.dropdown.Option(key='down',    text=t("transposer.dir_down")),
        ]
        dir2 = [
            ft.dropdown.Option(key='up',   text=t("transposer.dir_up")),
            ft.dropdown.Option(key='down', text=t("transposer.dir_down")),
        ]
        # Quick bar
        self._quick_iv_dd.label = t("transposer.mode_interval")
        self._quick_dir_dd.label = t("transposer.quick_label_direction")
        self._quick_dir_dd.options = dir2
        self._quick_keysig_cb.label = t("transposer.checkbox_transpose_key_signature")
        self._adv_btn_label.value = t("transposer.advanced_options_title")
        self._export_transposed_btn.content = t("transposer.button_export_transposed")
        self._export_original_btn.content = t("transposer.button_export_original")
        self._back_btn.tooltip = t("transposer.tooltip_back_to_score_preview")
        self._open_label.value = t("common.open_score")
        self._xml_dir_label.value = t("transposer.button_open_score_dir")
        self._section_title.value = t("transposer.section_title")
        self._orig_header.value = t("transposer.label_from_key")
        self._trans_header.value = t("transposer.label_transposed_col")
        # Advanced dialog (persistent, built once)
        self._adv_iv_dd.label = t("transposer.label_interval")
        self._adv_iv_dir.label = t("transposer.quick_label_direction")
        self._adv_iv_dir.options = dir3
        self._adv_iv_ks.label = t("transposer.checkbox_transpose_key_signature_adv")
        self._adv_key_from.label = t("transposer.label_from_key")
        self._adv_key_to.label = t("transposer.label_to_key")
        self._adv_key_dir.label = t("transposer.quick_label_direction")
        self._adv_key_dir.options = dir3
        self._adv_key_ks.label = t("transposer.checkbox_transpose_key_signature_adv")
        self._adv_diat_deg.label = t("transposer.label_degree")
        self._adv_diat_dir.label = t("transposer.quick_label_direction")
        self._adv_diat_dir.options = dir2
        self._adv_radio_iv.label = t("transposer.mode_interval")
        self._adv_radio_key.label = t("transposer.mode_key")
        self._adv_radio_diat.label = t("transposer.mode_chromatic")
        self._adv_title.value = t("transposer.advanced_options_title")
        self._adv_cancel_btn.content = t("common.cancel")
        self._adv_confirm_btn.content = t("common.confirm")
        try:
            self.update()
        except Exception:
            pass
        self._orig_viewer.retranslate()
        self._trans_viewer.retranslate()

    # ── 快速选择器回调 ────────────────────────────────────────────────────────

    def _on_quick_change(self, _e) -> None:
        self._mode = 'interval'
        self._interval_name   = self._quick_iv_dd.value      or t("transposer.fallback_interval")
        self._interval_dir    = self._quick_dir_dd.value     or 'up'
        self._interval_keysig = bool(self._quick_keysig_cb.value)
        self._on_transpose(None)

    # ── 文件打开 ──────────────────────────────────────────────────────────────

    def _on_open_click(self, _e) -> None:
        self.page.run_task(self._pick_file_async)  # type: ignore[attr-defined]

    def _on_open_xml_dir(self, _e) -> None:
        try:
            open_directory(xml_scores_dir())
        except Exception:
            pass

    def _on_save_transposed(self, _e) -> None:
        if self._transposed_mxl is None:
            self._set_status(t("transposer.status_wait_preview_save"))
            return
        try:
            import shutil
            dest = xml_scores_dir() / self._transposed_mxl.name
            shutil.copy2(str(self._transposed_mxl), str(dest))
            self._set_status(t("transposer.status_saved", name=dest.name))
        except Exception as exc:
            self._set_status(t("transposer.status_save_failed", exc=exc))

    async def _pick_file_async(self) -> None:
        _init_dir = str(self._xml_dir) if self._xml_dir.exists() else None
        files = await self._file_picker.pick_files(
            dialog_title=t("transposer.file_picker_open_musicxml"),
            allowed_extensions=['musicxml', 'mxl', 'xml'],
            allow_multiple=False,
            initial_directory=_init_dir,
        )
        if not files:
            return
        path = Path(files[0].path)  # type: ignore[arg-type]
        suffix = path.suffix.lower()
        if suffix in ('.mxl', '.musicxml', '.xml'):
            self._clear_transposed_preview()
            self._orig_mxl = path
            self._state.current_mxl = path
            self._orig_render_token += 1
            current_token = self._orig_render_token
            self._set_status(t("common.loaded_file", name=path.name))
            threading.Thread(target=self._render_orig, args=(current_token,), daemon=True).start()
            self.page.update(self._orig_viewer)  # type: ignore[attr-defined]
            self._on_auto_detect(None)
        elif suffix in ('.pdf', '.png', '.jpg'):
            self._clear_transposed_preview()
            self._orig_render_token += 1
            self._orig_viewer.load(path)
            self._set_status(t("transposer.status_preview_only", name=path.name))
            self.page.update(self._orig_viewer)  # type: ignore[attr-defined]

    def _on_mxl_ready(self, path: Path, **_kw) -> None:
        self._clear_transposed_preview()
        self._orig_mxl = path
        self._state.current_mxl = path
        self._orig_render_token += 1
        current_token = self._orig_render_token
        self._set_status(t("common.loaded_file", name=path.name))
        threading.Thread(target=self._render_orig, args=(current_token,), daemon=True).start()
        self._on_auto_detect(None)

    def _on_file_selected(self, path: Path, **_kw) -> None:
        if path.suffix.lower() in ('.mxl', '.musicxml', '.xml'):
            self._clear_transposed_preview()
            self._orig_mxl = path
            self._orig_render_token += 1
            current_token = self._orig_render_token
            threading.Thread(target=self._render_orig, args=(current_token,), daemon=True).start()
            self._on_auto_detect(None)

    # ── 调号检测 ──────────────────────────────────────────────────────────────

    def _on_auto_detect(self, _e) -> None:
        self._auto_detect_token += 1
        current_token = self._auto_detect_token
        threading.Thread(target=self._auto_detect_async, args=(current_token,), daemon=True).start()

    def _auto_detect_async(self, token: int) -> None:
        try:
            if token != self._auto_detect_token:
                return
            if self._orig_mxl is None:
                return
            from core.notation.transposer import detect_key_from_musicxml
            key = detect_key_from_musicxml(self._orig_mxl)
            if token != self._auto_detect_token:
                return
            # 更新按调模式的原调
            self._key_from = key
            self._adv_key_from.value = key
            self._set_status(t("transposer.status_detected_key", key=key_display_cn(key)))
        except Exception as exc:
            if token != self._auto_detect_token:
                return
            self._set_status(t("transposer.status_key_detect_failed", exc=exc))

    # ── 移调 ──────────────────────────────────────────────────────────────────

    def _on_transpose(self, _e) -> None:
        if self._orig_mxl is None:
            self._set_status(t("transposer.status_convert_or_open_first"))
            return
        self._transpose_token += 1
        current_token = self._transpose_token
        threading.Thread(target=self._transpose_async, args=(current_token,), daemon=True).start()

    def _transpose_async(self, token: int) -> None:
        if token != self._transpose_token:
            return
        self._set_busy(True)
        _render_started = False
        try:
            from core.notation.transposer import (
                transpose_musicxml, transpose_by_interval, transpose_diatonic,
            )
            base = build_dir()

            def _progress(v: float) -> None:
                if token != self._transpose_token:
                    return
                self._progress.value = v
                pp = self.page
                if pp is not None:
                    pp.loop.call_soon_threadsafe(pp.update, self._progress)  # type: ignore[attr-defined]

            assert self._orig_mxl is not None
            stem = self._orig_mxl.stem

            if self._mode == 'interval':
                dir_tag = {'up': 'up', 'down': 'dn', 'closest': 'cl'}.get(self._interval_dir, 'up')
                dst = base / f'{stem}_T{self._interval_name}_{dir_tag}.musicxml'
                transposed = transpose_by_interval(
                    self._orig_mxl, dst,
                    interval_name=self._interval_name,
                    direction=self._interval_dir,
                    transpose_key_sig=self._interval_keysig,
                    progress_callback=_progress,
                )
            elif self._mode == 'key':
                dst = base / f'{stem}_transposed_{self._key_to}.musicxml'
                transposed = transpose_musicxml(
                    self._orig_mxl, dst,
                    from_key=self._key_from,
                    to_key=self._key_to,
                    direction=self._key_dir,
                    transpose_key_sig=self._key_keysig,
                    progress_callback=_progress,
                )
            else:  # diatonic
                dir_tag = 'up' if self._diatonic_dir == 'up' else 'dn'
                dst = base / f'{stem}_Tdiat_{self._diatonic_degree}_{dir_tag}.musicxml'
                transposed = transpose_diatonic(
                    self._orig_mxl, dst,
                    degree_name=self._diatonic_degree,
                    direction=self._diatonic_dir,
                    progress_callback=_progress,
                )

            if token != self._transpose_token:
                return
            self._transposed_mxl = transposed
            self._state.transposed_mxl = transposed
            self._state.emit(Event.TRANSPOSED_READY, path=transposed)
            self._trans_render_token += 1
            trans_token = self._trans_render_token
            self._set_status(t("transposer.status_transpose_done", name=transposed.name))
            threading.Thread(target=self._render_transposed, args=(trans_token,), daemon=True).start()
            _render_started = True  # busy 将由 _render_transposed 关闭

        except Exception as exc:
            if token != self._transpose_token:
                return
            self._set_status(t("transposer.status_transpose_failed", exc=exc))
        finally:
            if token == self._transpose_token and not _render_started:
                self._set_busy(False)

    # ── 导出 ──────────────────────────────────────────────────────────────────

    def _on_export(self, _e) -> None:
        if self._transposed_mxl is None:
            self._set_status(t("transposer.status_wait_preview_export"))
            return
        self.page.run_task(self._export_transposed_async)  # type: ignore[attr-defined]

    async def _export_transposed_async(self) -> None:
        assert self._transposed_mxl is not None
        _fname = f'{self._transposed_mxl.stem}_staff.pdf'
        self._export_dir_picker.file_name = _fname
        dest_str = await self._export_dir_picker.save_file(
            dialog_title=t("transposer.file_picker_export_transposed"),
            file_name=_fname,
            allowed_extensions=['pdf'],
        )
        if not dest_str:
            return
        self._export_token += 1
        current_token = self._export_token
        threading.Thread(
            target=self._export_async, args=(current_token, Path(dest_str)), daemon=True
        ).start()

    def _on_export_original(self, _e) -> None:
        if self._orig_mxl is None:
            self._set_status(t("transposer.status_open_original_first"))
            return
        self.page.run_task(self._export_original_async)  # type: ignore[attr-defined]

    async def _export_original_async(self) -> None:
        assert self._orig_mxl is not None
        _fname = f'{self._orig_mxl.stem}_staff.pdf'
        self._export_dir_picker.file_name = _fname
        dest_str = await self._export_dir_picker.save_file(
            dialog_title=t("transposer.file_picker_export_original"),
            file_name=_fname,
            allowed_extensions=['pdf'],
        )
        if not dest_str:
            return
        self._orig_export_token += 1
        current_token = self._orig_export_token
        threading.Thread(
            target=self._export_original_thread, args=(current_token, Path(dest_str)), daemon=True
        ).start()

    def _export_async(self, token: int, dest_path: Path) -> None:
        if token != self._export_token:
            return
        self._set_busy(True)
        try:
            import shutil
            from core.render.lilypond_runner import render_musicxml_staff_pdf

            assert self._transposed_mxl is not None
            tmp = build_dir() / f'_trans_export_{self._transposed_mxl.stem}'
            tmp.mkdir(exist_ok=True)
            pdf = render_musicxml_staff_pdf(self._transposed_mxl, tmp)
            if token != self._export_token:
                return
            if pdf and pdf.exists():
                shutil.copy2(str(pdf), str(dest_path))
                self._set_status(t("transposer.export_done", dest=dest_path))
                self._state.output_pdf = dest_path
            else:
                self._set_status(t("transposer.export_failed_transposed"))
        except Exception as exc:
            if token != self._export_token:
                return
            self._set_status(t("common.export_failed_exc", exc=exc))
        finally:
            if token == self._export_token:
                self._set_busy(False)

    def _export_original_thread(self, token: int, dest_path: Path) -> None:
        if token != self._orig_export_token:
            return
        self._set_busy(True)
        try:
            import shutil
            from core.render.lilypond_runner import render_musicxml_staff_pdf

            assert self._orig_mxl is not None
            tmp = build_dir() / f'_orig_export_{self._orig_mxl.stem}'
            tmp.mkdir(exist_ok=True)
            pdf = render_musicxml_staff_pdf(self._orig_mxl, tmp)
            if token != self._orig_export_token:
                return
            if pdf and pdf.exists():
                shutil.copy2(str(pdf), str(dest_path))
                self._set_status(t("transposer.export_done", dest=dest_path))
            else:
                self._set_status(t("transposer.export_failed_original"))
        except Exception as exc:
            if token != self._orig_export_token:
                return
            self._set_status(t("common.export_failed_exc", exc=exc))
        finally:
            if token == self._orig_export_token:
                self._set_busy(False)

    # ── 渲染预览 ─────────────────────────────────────────────────────────────

    def _render_orig(self, token: int) -> None:
        if token != self._orig_render_token or self._orig_mxl is None:
            return
        self._set_busy(True)
        try:
            pdf = self._mxl_to_preview_pdf(self._orig_mxl, '_orig_preview')
            if token != self._orig_render_token:
                return
            if pdf:
                self._orig_viewer.load(pdf)
                self._set_status(t("common.loaded_file", name=self._orig_mxl.name))
        finally:
            if token == self._orig_render_token:
                self._set_busy(False)

    def _render_transposed(self, token: int) -> None:
        if token != self._trans_render_token or self._transposed_mxl is None:
            self._set_busy(False)
            return
        try:
            pdf = self._mxl_to_preview_pdf(self._transposed_mxl, '_trans_preview')
            if token != self._trans_render_token:
                return
            if pdf:
                self._trans_viewer.load(pdf)
        finally:
            if token == self._trans_render_token:
                self._set_busy(False)

    def _clear_transposed_preview(self) -> None:
        self._transposed_mxl = None
        self._state.transposed_mxl = None
        self._trans_render_token += 1
        self._trans_viewer.reset()
        p = self.page
        if p is not None:
            p.loop.call_soon_threadsafe(p.update, self._trans_viewer)  # type: ignore[attr-defined]

    def _mxl_to_preview_pdf(self, mxl_path: Path, suffix: str) -> Optional[Path]:
        try:
            base = build_dir()
            tmp  = base / f'_preview_tmp_{mxl_path.stem}'
            tmp.mkdir(exist_ok=True)
            out  = base / f'{mxl_path.stem}{suffix}.pdf'
            from core.render.lilypond_runner import render_musicxml_staff_pdf
            pdf = render_musicxml_staff_pdf(mxl_path, tmp)
            if pdf and pdf.exists():
                import shutil
                shutil.copy2(str(pdf), str(out))
                shutil.rmtree(tmp, ignore_errors=True)
                return out
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        except Exception as exc:
            self._set_status(t("transposer.status_preview_render_failed", exc=exc))
            return None

    def reset_view(self) -> None:
        self._orig_mxl = None
        self._transposed_mxl = None
        self._orig_viewer.reset()
        self._trans_viewer.reset()
        self._status.value = t("transposer.status_open_score_first")
        p = self.page
        if p is not None:
            p.loop.call_soon_threadsafe(  # type: ignore[attr-defined]
                p.update, self._orig_viewer, self._trans_viewer, self._status
            )

    def load_mxl(self, path: Path) -> None:
        """从五线谱预览页跳转时，预加载指定 MXL 文件。"""
        if path is None or not path.exists():
            return
        self._clear_transposed_preview()
        self._orig_mxl = path
        self._state.current_mxl = path
        self._orig_render_token += 1
        current_token = self._orig_render_token
        self._set_status(t("common.loaded_file", name=path.name))
        threading.Thread(target=self._render_orig, args=(current_token,), daemon=True).start()
        self._on_auto_detect(None)

    # ── 辅助 ─────────────────────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        self._status.value = msg
        p = self.page
        if p is not None:
            p.loop.call_soon_threadsafe(p.update, self._status)  # type: ignore[attr-defined]

    def _set_busy(self, busy: bool) -> None:
        self._progress.visible = busy
        self._progress.value   = None if busy else 0
        p = self.page
        if p is not None:
            p.loop.call_soon_threadsafe(p.update, self._progress)  # type: ignore[attr-defined]
