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
from ..theme import Palette, section_title
from core.music.transposer import INTERVALS, DIATONIC_DEGREES, key_display_cn


# 按五度圈排列的调名列表
_KEYS = ['Cb', 'Gb', 'Db', 'Ab', 'Eb', 'Bb', 'F', 'C', 'G', 'D', 'A', 'E', 'B', 'F#', 'C#']
_KEY_DISPLAY = {k: k.replace('#', '♯').replace('b', '♭') for k in _KEYS}

_DIRECTIONS_3 = [
    ('closest', '最近'),
    ('up',      '向上'),
    ('down',    '向下'),
]
_DIRECTIONS_2 = [
    ('up',   '向上'),
    ('down', '向下'),
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
        self._interval_name: str = '纯一度'
        self._interval_dir: str = 'up'
        self._interval_keysig: bool = True
        self._key_from: str = 'C'
        self._key_to: str = 'G'
        self._key_dir: str = 'closest'
        self._key_keysig: bool = True
        self._diatonic_degree: str = '三度'
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
        self._adv_iv_dd  = _dd('音程', '纯一度', _iv_opts, 140)
        self._adv_iv_dir = _dd('方向', 'up',     _dir3_opts, 100)
        self._adv_iv_ks  = ft.Checkbox(label='同时移调调号', value=True)
        self._adv_iv_sec = ft.Column(
            [ft.Row([self._adv_iv_dd, self._adv_iv_dir], spacing=8), self._adv_iv_ks],
            spacing=8, visible=True,
        )

        # 按调
        self._adv_key_from = _dd('原调',   'C', _key_opts, 110)
        self._adv_key_to   = _dd('目标调', 'G', _key_opts, 110)
        self._adv_key_dir  = _dd('方向', 'closest', _dir3_opts, 100)
        self._adv_key_ks   = ft.Checkbox(label='同时移调调号', value=True)
        self._adv_key_sec  = ft.Column(
            [
                ft.Row(
                    [self._adv_key_from,
                     ft.Text('→', color=ft.Colors.ON_SURFACE_VARIANT, size=15),
                     self._adv_key_to,
                     self._adv_key_dir],
                    spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self._adv_key_ks,
            ],
            spacing=8, visible=False,
        )

        # 全音移调
        self._adv_diat_deg = _dd('度数', '三度', _diat_opts, 100)
        self._adv_diat_dir = _dd('方向', 'up',   _dir2_opts, 100)
        self._adv_diat_sec = ft.Column(
            [ft.Row([self._adv_diat_deg, self._adv_diat_dir], spacing=8)],
            visible=False,
        )

        self._adv_mode_radio = ft.RadioGroup(
            value='interval',
            on_change=self._on_adv_mode_change,
            content=ft.Row([
                ft.Radio(value='interval', label='按音程'),
                ft.Radio(value='key',      label='按调'),
                ft.Radio(value='diatonic', label='全音移调'),
            ], spacing=16),
        )

        self._adv_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text('高级选项', size=16, weight=ft.FontWeight.W_700),
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
                ft.TextButton('取消', on_click=lambda _: self.page.pop_dialog()),
                ft.FilledButton('确认', on_click=self._confirm_adv_dialog),
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
            self._interval_name  = self._adv_iv_dd.value  or '纯四度'
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
            self._diatonic_degree = self._adv_diat_deg.value or '三度'
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

        self._quick_iv_dd  = _dd('按音程', '纯一度', _iv_opts, 140)
        self._quick_dir_dd = _dd('方向',   'up',     _dir2_opts, 100)
        self._quick_iv_dd.on_select  = self._on_quick_change  # type: ignore[attr-defined]
        self._quick_dir_dd.on_select = self._on_quick_change  # type: ignore[attr-defined]

        self._quick_keysig_cb = ft.Checkbox(
            label='移调调号',
            value=True,
            on_change=self._on_quick_change,
        )

        adv_btn = ft.OutlinedButton(
            content=ft.Row(
                [ft.Icon(ft.Icons.TUNE_ROUNDED, size=15), ft.Text('高级选项')],
                tight=True, spacing=5,
            ),
            on_click=self._open_adv_dialog,
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        export_transposed_btn = ft.TextButton(
            '导出移调乐谱',
            icon=ft.Icons.PICTURE_AS_PDF_OUTLINED,
            on_click=self._on_export,
            style=ft.ButtonStyle(color=Palette.PRIMARY),
        )

        export_original_btn = ft.TextButton(
            '导出原调乐谱',
            icon=ft.Icons.PICTURE_AS_PDF_OUTLINED,
            on_click=self._on_export_original,
            style=ft.ButtonStyle(color=ft.Colors.ON_SURFACE_VARIANT),
        )

        open_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.FOLDER_OPEN_ROUNDED, size=16), ft.Text('打开乐谱')],
                tight=True, spacing=6,
            ),
            on_click=self._on_open_click,
            style=ft.ButtonStyle(
                color=Palette.PRIMARY,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, Palette.PRIMARY)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        xml_dir_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.FOLDER_OPEN_ROUNDED, size=16), ft.Text('打开乐谱目录')],
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
                    ft.Column(
                        [
                            section_title('移调功能', self._state.dark_mode),
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

        # 双栏预览
        self._orig_viewer  = PdfViewer()
        self._trans_viewer = PdfViewer()

        orig_col = ft.Column(
            [
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Text('原调', size=13, weight=ft.FontWeight.W_700,
                                    color=ft.Colors.ON_SURFACE_VARIANT),
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
                            ft.Text('移调后', size=13, weight=ft.FontWeight.W_700,
                                    color=Palette.PRIMARY),
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

        self._status   = ft.Text('请先打开乐谱文件。', size=13, color=ft.Colors.ON_SURFACE_VARIANT)
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

        self.controls = [top_bar, ft.Container(content=preview_row, expand=True), bottom_bar]
        self.expand = True

    # ── 快速选择器回调 ────────────────────────────────────────────────────────

    def _on_quick_change(self, _e) -> None:
        self._mode = 'interval'
        self._interval_name   = self._quick_iv_dd.value      or '纯四度'
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
            self._set_status('请先等待移调预览生成后再保存。')
            return
        try:
            import shutil
            dest = xml_scores_dir() / self._transposed_mxl.name
            shutil.copy2(str(self._transposed_mxl), str(dest))
            self._set_status(f'已保存移调版乐谱 → {dest.name}')
        except Exception as exc:
            self._set_status(f'保存失败: {exc}')

    async def _pick_file_async(self) -> None:
        _init_dir = str(self._xml_dir) if self._xml_dir.exists() else None
        files = await self._file_picker.pick_files(
            dialog_title='打开乐谱文件（MusicXML）',
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
            self._set_status(f'已加载: {path.name}')
            threading.Thread(target=self._render_orig, args=(current_token,), daemon=True).start()
            self.page.update(self._orig_viewer)  # type: ignore[attr-defined]
            self._on_auto_detect(None)
        elif suffix in ('.pdf', '.png', '.jpg'):
            self._clear_transposed_preview()
            self._orig_render_token += 1
            self._orig_viewer.load(path)
            self._set_status(f'预览: {path.name}（仅预览，移调需 MXL 格式）')
            self.page.update(self._orig_viewer)  # type: ignore[attr-defined]

    def _on_mxl_ready(self, path: Path, **_kw) -> None:
        self._clear_transposed_preview()
        self._orig_mxl = path
        self._state.current_mxl = path
        self._orig_render_token += 1
        current_token = self._orig_render_token
        self._set_status(f'已加载: {path.name}')
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
            from core.music.transposer import detect_key_from_musicxml
            key = detect_key_from_musicxml(self._orig_mxl)
            if token != self._auto_detect_token:
                return
            # 更新按调模式的原调
            self._key_from = key
            self._adv_key_from.value = key
            self._set_status(f'检测到原调: {key_display_cn(key)}')
        except Exception as exc:
            if token != self._auto_detect_token:
                return
            self._set_status(f'调号检测失败: {exc}')

    # ── 移调 ──────────────────────────────────────────────────────────────────

    def _on_transpose(self, _e) -> None:
        if self._orig_mxl is None:
            self._set_status('请先在首页转换文件或打开乐谱。')
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
            from core.music.transposer import (
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
            self._set_status(f'移调完成 → {transposed.name}')
            threading.Thread(target=self._render_transposed, args=(trans_token,), daemon=True).start()
            _render_started = True  # busy 将由 _render_transposed 关闭

        except Exception as exc:
            if token != self._transpose_token:
                return
            self._set_status(f'移调失败: {exc}')
        finally:
            if token == self._transpose_token and not _render_started:
                self._set_busy(False)

    # ── 导出 ──────────────────────────────────────────────────────────────────

    def _on_export(self, _e) -> None:
        if self._transposed_mxl is None:
            self._set_status('请先等待移调预览生成后再导出。')
            return
        self.page.run_task(self._export_transposed_async)  # type: ignore[attr-defined]

    async def _export_transposed_async(self) -> None:
        assert self._transposed_mxl is not None
        dest_str = await self._export_dir_picker.save_file(
            dialog_title='导出移调版 PDF',
            file_name=f'{self._transposed_mxl.stem}_staff.pdf',
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
            self._set_status('请先加载原调乐谱后再导出。')
            return
        self.page.run_task(self._export_original_async)  # type: ignore[attr-defined]

    async def _export_original_async(self) -> None:
        assert self._orig_mxl is not None
        dest_str = await self._export_dir_picker.save_file(
            dialog_title='导出原调乐谱 PDF',
            file_name=f'{self._orig_mxl.stem}_staff.pdf',
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
                self._set_status(f'导出完成 → {dest_path}')
                self._state.output_pdf = dest_path
            else:
                self._set_status('导出失败：无法生成五线谱 PDF，请检查 LilyPond / musicxml2ly 是否可用。')
        except Exception as exc:
            if token != self._export_token:
                return
            self._set_status(f'导出失败: {exc}')
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
                self._set_status(f'导出完成 → {dest_path}')
            else:
                self._set_status('导出失败：无法生成原调乐谱 PDF，请检查 LilyPond / musicxml2ly 是否可用。')
        except Exception as exc:
            if token != self._orig_export_token:
                return
            self._set_status(f'导出失败: {exc}')
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
                self._set_status(f'已加载: {self._orig_mxl.name}')
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
                return out
            return None
        except Exception as exc:
            self._set_status(f'预览渲染失败: {exc}')
            return None

    def reset_view(self) -> None:
        self._orig_mxl = None
        self._transposed_mxl = None
        self._orig_viewer.reset()
        self._trans_viewer.reset()
        self._status.value = '请先打开乐谱文件。'
        p = self.page
        if p is not None:
            p.loop.call_soon_threadsafe(  # type: ignore[attr-defined]
                p.update, self._orig_viewer, self._trans_viewer, self._status
            )

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
