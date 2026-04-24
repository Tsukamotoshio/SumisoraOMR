# gui/pages/transposer_page.py — 五线谱移调引擎页面
# 双栏布局：左侧原调预览，右侧实时渲染移调后预览。
# 使用 core/transposer.py + music21 处理升降号补偿。

from __future__ import annotations

import base64
import io
import threading
from pathlib import Path
from typing import Optional

import flet as ft

from ..app_state import AppState, Event
from core.app.backend import xml_scores_dir, output_dir, build_dir, open_directory
from ..components.pdf_viewer import PdfViewer
from ..theme import Palette, section_title


# 按五度圈排列的调名列表（与 MuseScore 一致：-7 Cb 到 +7 C#）
# key = 机读值（传给 transposer），text = 带音乐符号的显示文字
_KEYS = ['Cb', 'Gb', 'Db', 'Ab', 'Eb', 'Bb', 'F', 'C', 'G', 'D', 'A', 'E', 'B', 'F#', 'C#']
_KEY_DISPLAY = {k: k.replace('#', '♯').replace('b', '♭') for k in _KEYS}

# 移调方向选项
_DIRECTIONS = [
    ('closest', '最近'),  # MuseScore 默认
    ('up',      '向上'),
    ('down',    '向下'),
]


class TransposerPage(ft.Column):
    """移调引擎：输入调 → 目标调，左右对比预览，一键导出。"""

    def __init__(self, state: AppState):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._has_been_shown = False
        self._orig_mxl: Optional[Path] = None
        self._transposed_mxl: Optional[Path] = None
        self._orig_render_token: int = 0
        self._trans_render_token: int = 0
        self._auto_detect_token: int = 0
        self._transpose_token: int = 0
        self._export_token: int = 0
        self._export_orig_token: int = 0
        self._file_picker = ft.FilePicker()
        self._xml_dir = self._ensure_xml_dir()
        self._build_ui()
        state.on(Event.MXL_READY, self._on_mxl_ready)
        state.on(Event.FILE_SELECTED, self._on_file_selected)

    @staticmethod
    def _ensure_xml_dir() -> Path:
        """确保 xml-scores 目录存在，并返回其路径。"""
        return xml_scores_dir()

    def did_mount(self):
        self.page._services.register_service(self._file_picker)
        self._trigger_key_change()  # 页面挂载后初始化半音标签（后台线程）

    # ── 构建 UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # 顶部控制条
        self._from_key_dd = ft.Dropdown(
            label='原调',
            value='C',
            options=[ft.dropdown.Option(key=k, text=_KEY_DISPLAY[k]) for k in _KEYS],
            width=110,
            bgcolor=Palette.BG_INPUT,
            color=Palette.TEXT_PRIMARY,
            text_size=13,
        )
        self._to_key_dd = ft.Dropdown(
            label='目标调',
            value='G',
            options=[ft.dropdown.Option(key=k, text=_KEY_DISPLAY[k]) for k in _KEYS],
            width=110,
            bgcolor=Palette.BG_INPUT,
            color=Palette.TEXT_PRIMARY,
            text_size=13,
        )
        self._direction_dd = ft.Dropdown(
            label='方向',
            value='closest',
            options=[ft.dropdown.Option(key=v, text=label) for v, label in _DIRECTIONS],
            width=100,
            bgcolor=Palette.BG_INPUT,
            color=Palette.TEXT_PRIMARY,
            text_size=13,
            tooltip='最近：自动选择半音距离更近的上/下行（同调时不动）',
        )
        self._semitone_label = ft.Text('', size=12, color=Palette.TEXT_SECONDARY)

        transpose_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.TRANSFORM_ROUNDED, size=16), ft.Text('移调预览')],
                tight=True, spacing=6,
            ),
            bgcolor=Palette.PRIMARY,
            color='#FFFFFF',
            on_click=self._on_transpose,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
        )
        export_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.DOWNLOAD_ROUNDED, size=16), ft.Text('导出移调版')],
                tight=True, spacing=6,
            ),
            on_click=self._on_export,
            style=ft.ButtonStyle(
                color=Palette.PRIMARY,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, Palette.PRIMARY)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )
        self._detect_key_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.SEARCH_ROUNDED, size=16), ft.Text('检测调号')],
                tight=True, spacing=6,
            ),
            on_click=self._on_auto_detect,
            style=ft.ButtonStyle(
                color=Palette.TEXT_SECONDARY,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, Palette.DIVIDER_DARK)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )
        export_orig_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, size=16), ft.Text('导出原谱')],
                tight=True, spacing=6,
            ),
            on_click=self._on_export_orig,
            style=ft.ButtonStyle(
                color=Palette.TEXT_SECONDARY,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, Palette.DIVIDER_DARK)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        self._from_key_dd.on_select  = self._trigger_key_change
        self._to_key_dd.on_select    = self._trigger_key_change
        self._direction_dd.on_select = self._trigger_key_change

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

        open_output_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.FOLDER_OPEN_ROUNDED, size=16), ft.Text('打开输出目录')],
                tight=True, spacing=6,
            ),
            on_click=self._on_open_output_dir,
            style=ft.ButtonStyle(
                color=Palette.TEXT_SECONDARY,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, Palette.DIVIDER_DARK)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        top_bar = ft.Container(
            content=ft.Column(
                [
                    # 第一行：标题 + 文件操作
                    ft.Row(
                        [
                            section_title('移调引擎', self._state.dark_mode),
                            ft.Container(expand=True),
                            ft.Row([open_btn, open_output_btn], spacing=8),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    # 第二行：调号选择 + 动作按钮
                    ft.Row(
                        [
                            # 左侧：调号控件 + 半音标签
                            ft.Row(
                                [self._from_key_dd, ft.Text('→', color=Palette.TEXT_SECONDARY), self._to_key_dd, self._direction_dd, self._semitone_label],
                                spacing=6,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Container(expand=True),
                            # 右侧：全部动作按钮，统一高度
                            ft.Row(
                                [self._detect_key_btn, transpose_btn, export_orig_btn, export_btn],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=10,
                    ),
                ],
                spacing=6,
            ),
            bgcolor=Palette.BG_SURFACE,
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            border=ft.Border.only(bottom=ft.BorderSide(1, Palette.DIVIDER_DARK)),
        )

        # 双栏预览
        self._orig_viewer  = PdfViewer()
        self._trans_viewer = PdfViewer()

        orig_col = ft.Column(
            [
                ft.Container(
                    content=ft.Text('原调', size=12, weight=ft.FontWeight.W_600, color=Palette.TEXT_SECONDARY),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                    bgcolor=Palette.BG_SURFACE,
                    border=ft.Border.only(bottom=ft.BorderSide(1, Palette.DIVIDER_DARK)),
                ),
                ft.Container(content=self._orig_viewer, expand=True),
            ],
            spacing=0, expand=True,
        )
        trans_col = ft.Column(
            [
                ft.Container(
                    content=ft.Text('移调后', size=12, weight=ft.FontWeight.W_600, color=Palette.PRIMARY),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                    bgcolor=Palette.BG_SURFACE,
                    border=ft.Border.only(bottom=ft.BorderSide(1, Palette.DIVIDER_DARK)),
                ),
                ft.Container(content=self._trans_viewer, expand=True),
            ],
            spacing=0, expand=True,
        )

        preview_row = ft.Row(
            [
                ft.Container(content=orig_col, expand=True),
                ft.VerticalDivider(width=1, color=Palette.DIVIDER_DARK),
                ft.Container(content=trans_col, expand=True),
            ],
            spacing=0,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        # 底部状态栏
        self._status = ft.Text('请先打开乐谱文件。', size=12, color=Palette.TEXT_SECONDARY)
        self._progress = ft.ProgressBar(value=0, visible=False, bgcolor=Palette.BG_CARD, color=Palette.PRIMARY, height=3)

        bottom_bar = ft.Container(
            content=ft.Column(
                [
                    ft.Container(content=self._progress, height=3, width=320),
                    ft.Row([self._status], alignment=ft.MainAxisAlignment.START),
                ],
                spacing=8,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            bgcolor=Palette.BG_SURFACE,
            border=ft.Border.only(top=ft.BorderSide(1, Palette.DIVIDER_DARK)),
        )

        self.controls = [top_bar, ft.Container(content=preview_row, expand=True), bottom_bar]
        self.expand = True

    # ── 事件 ─────────────────────────────────────────────────────────────────

    def _on_open_click(self, _e) -> None:
        self.page.run_task(self._pick_file_async)

    def _on_open_output_dir(self, _e) -> None:
        try:
            open_directory(output_dir(None))
        except Exception:
            pass

    async def _pick_file_async(self) -> None:
        # 优先用 xml-scores 目录作初始目录
        _init_dir = str(self._xml_dir) if self._xml_dir.exists() else None
        files = await self._file_picker.pick_files(
            dialog_title='打开乐谱文件（MusicXML）',
            allowed_extensions=['musicxml'],
            allow_multiple=False,
            initial_directory=_init_dir,
        )
        if not files:
            return
        path = Path(files[0].path)
        suffix = path.suffix.lower()
        if suffix in ('.mxl', '.musicxml', '.xml'):
            self._clear_transposed_preview()
            self._orig_mxl = path
            self._state.current_mxl = path
            self._orig_render_token += 1
            current_token = self._orig_render_token
            self._set_status(f'已加载: {path.name}')
            threading.Thread(target=self._render_orig, args=(current_token,), daemon=True).start()
            self.page.update(self._orig_viewer)
            self._on_auto_detect(None)
        elif suffix in ('.pdf', '.png', '.jpg'):
            # 直接预览图像 / PDF
            self._clear_transposed_preview()
            self._orig_render_token += 1
            self._orig_viewer.load(path)
            self._set_status(f'预览: {path.name}（仅预览，移调需 MXL 格式）')
            self.page.update(self._orig_viewer)

    def _on_mxl_ready(self, path: Path, **_kw) -> None:
        self._clear_transposed_preview()
        self._orig_mxl = path
        self._state.current_mxl = path
        self._orig_render_token += 1
        current_token = self._orig_render_token
        self._set_status(f'已加载: {path.name}')
        # 渲染原谱预览（转为简谱 PDF）
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

    async def _trigger_key_change(self, _e=None) -> None:
        """on_select 异步处理器：在 asyncio 事件循环内运行，page.update() 安全有效。"""
        self._compute_semitone_label()
        self.page.update(self._semitone_label)

    def _compute_semitone_label(self) -> None:
        """计算半音数并设置标签值（纯数据，不触发 UI 更新）。线程安全。"""
        from core.music.transposer import get_transposition_semitones
        try:
            semitones = get_transposition_semitones(
                self._from_key_dd.value or 'C',
                self._to_key_dd.value   or 'C',
                direction=self._direction_dd.value or 'closest',
            )
            if semitones == 0:
                label = '不动（同调）'
            elif semitones > 0:
                label = f'↑ {semitones} 个半音'
            else:
                label = f'↓ {abs(semitones)} 个半音'
            self._state.transpose_from_key = self._from_key_dd.value or 'C'
            self._state.transpose_to_key   = self._to_key_dd.value   or 'C'
        except Exception:
            label = ''
        self._semitone_label.value = label

    def _on_key_change(self) -> None:
        """后台线程调用：计算标签并通过 call_soon_threadsafe 安全通知事件循环刷新 UI。"""
        self._compute_semitone_label()
        p = self.page
        if p is not None:
            p.loop.call_soon_threadsafe(p.update, self._semitone_label)

    def _on_auto_detect(self, _e) -> None:
        self._auto_detect_token += 1
        current_token = self._auto_detect_token
        threading.Thread(target=self._auto_detect_async, args=(current_token,), daemon=True).start()

    def _auto_detect_async(self, token: int) -> None:
        try:
            if token != self._auto_detect_token:
                return
            # 有文件时先检测调号，再计算偏移；无文件时直接计算偏移
            if self._orig_mxl is not None:
                from core.music.transposer import detect_key_from_musicxml
                key = detect_key_from_musicxml(self._orig_mxl)
                if token != self._auto_detect_token:
                    return
                self._from_key_dd.value = key
                self._compute_semitone_label()
                p = self.page
                if p is not None:
                    # 同时刷新 from_key 下拉框和半音标签
                    p.loop.call_soon_threadsafe(p.update, self._from_key_dd, self._semitone_label)
                if token != self._auto_detect_token:
                    return
                self._set_status(f'检测到原调: {key}，已计算偏移')
            else:
                self._on_key_change()
                if token != self._auto_detect_token:
                    return
                self._set_status('已计算半音偏移')
        except Exception as exc:
            if token != self._auto_detect_token:
                return
            self._set_status(f'计算失败: {exc}')

    def _on_transpose(self, _e) -> None:
        if self._orig_mxl is None:
            self._set_status('请先在首页转换文件。')
            return
        self._transpose_token += 1
        current_token = self._transpose_token
        threading.Thread(target=self._transpose_async, args=(current_token,), daemon=True).start()

    def _transpose_async(self, token: int) -> None:
        if token != self._transpose_token:
            return
        self._set_busy(True)
        try:
            from core.music.transposer import transpose_musicxml
            import tempfile

            base = build_dir()
            dst = base / f'{self._orig_mxl.stem}_transposed_{self._to_key_dd.value}.musicxml'

            def _progress(v: float):
                if token != self._transpose_token:
                    return
                self._progress.value = v
                pp = self.page
                if pp is not None:
                    pp.loop.call_soon_threadsafe(pp.update, self._progress)

            transposed = transpose_musicxml(
                self._orig_mxl, dst,
                from_key=self._from_key_dd.value  or 'C',
                to_key=self._to_key_dd.value      or 'C',
                direction=self._direction_dd.value or 'closest',
                progress_callback=_progress,
            )
            if token != self._transpose_token:
                return
            self._transposed_mxl = transposed
            self._state.transposed_mxl = transposed
            self._state.emit(Event.TRANSPOSED_READY, path=transposed)
            self._trans_render_token += 1
            current_token = self._trans_render_token
            if token != self._transpose_token:
                return
            self._set_status(f'移调完成 → {transposed.name}')
            # 渲染移调后预览
            threading.Thread(target=self._render_transposed, args=(current_token,), daemon=True).start()
        except Exception as exc:
            if token != self._transpose_token:
                return
            self._set_status(f'移调失败: {exc}')
        finally:
            if token == self._transpose_token:
                self._set_busy(False)

    def _on_export(self, _e) -> None:
        if self._transposed_mxl is None:
            self._set_status('请先点击「移调预览」。')
            return
        self._export_token += 1
        current_token = self._export_token
        threading.Thread(target=self._export_async, args=(current_token,), daemon=True).start()

    def _export_async(self, token: int) -> None:
        if token != self._export_token:
            return
        self._set_busy(True)
        try:
            from core.render.lilypond_runner import render_musicxml_staff_pdf

            base = output_dir(None)
            out_pdf = base / f'{self._transposed_mxl.stem}_staff.pdf'
            tmp = build_dir() / f'_trans_export_{self._transposed_mxl.stem}'
            tmp.mkdir(exist_ok=True)

            pdf = render_musicxml_staff_pdf(self._transposed_mxl, tmp)
            if token != self._export_token:
                return
            if pdf and pdf.exists():
                import shutil
                shutil.copy2(str(pdf), str(out_pdf))
                self._set_status(f'导出完成 → {out_pdf.name}')
                self._state.output_pdf = out_pdf
            else:
                self._set_status('导出失败：无法生成五线谱 PDF，请检查 LilyPond / musicxml2ly 是否可用。')
        except Exception as exc:
            if token != self._export_token:
                return
            self._set_status(f'导出失败: {exc}')
        finally:
            if token == self._export_token:
                self._set_busy(False)

    def _on_export_orig(self, _e) -> None:
        if self._orig_mxl is None:
            self._set_status('请先打开乐谱文件。')
            return
        self._export_orig_token += 1
        current_token = self._export_orig_token
        threading.Thread(target=self._export_orig_async, args=(current_token,), daemon=True).start()

    def _export_orig_async(self, token: int) -> None:
        if token != self._export_orig_token:
            return
        self._set_busy(True)
        try:
            from core.render.lilypond_runner import render_musicxml_staff_pdf

            base = output_dir(None)
            out_pdf = base / f'{self._orig_mxl.stem}_staff.pdf'
            tmp = build_dir() / f'_orig_export_{self._orig_mxl.stem}'
            tmp.mkdir(exist_ok=True)

            pdf = render_musicxml_staff_pdf(self._orig_mxl, tmp)
            if token != self._export_orig_token:
                return
            if pdf and pdf.exists():
                import shutil
                shutil.copy2(str(pdf), str(out_pdf))
                self._set_status(f'原谱导出完成 → {out_pdf.name}')
                self._state.output_pdf = out_pdf
            else:
                self._set_status('导出失败：无法生成五线谱 PDF，请检查 LilyPond / musicxml2ly 是否可用。')
        except Exception as exc:
            if token != self._export_orig_token:
                return
            self._set_status(f'原谱导出失败: {exc}')
        finally:
            if token == self._export_orig_token:
                self._set_busy(False)

    # ── 渲染预览 ─────────────────────────────────────────────────────────────

    def _render_orig(self, token: int) -> None:
        if token != self._orig_render_token:
            return
        if self._orig_mxl is None:
            return
        pdf = self._mxl_to_preview_pdf(self._orig_mxl, '_orig_preview')
        if token != self._orig_render_token:
            return
        if pdf:
            self._orig_viewer.load(pdf)

    def _render_transposed(self, token: int) -> None:
        if token != self._trans_render_token:
            return
        if self._transposed_mxl is None:
            return
        pdf = self._mxl_to_preview_pdf(self._transposed_mxl, '_trans_preview')
        if token != self._trans_render_token:
            return
        if pdf:
            self._trans_viewer.load(pdf)

    def _clear_transposed_preview(self) -> None:
        self._transposed_mxl = None
        self._state.transposed_mxl = None
        self._trans_render_token += 1
        self._trans_viewer.reset()
        p = self.page
        if p is not None:
            p.loop.call_soon_threadsafe(p.update, self._trans_viewer)

    def _mxl_to_preview_pdf(self, mxl_path: Path, suffix: str) -> Optional[Path]:
        """将 MusicXML 渲染为五线谱 PDF（用于预览）。优先用 musicxml2ly 渲染标准五线谱，
        失败时回退到简谱渲染。"""
        try:
            base = build_dir()
            tmp  = base / f'_preview_tmp_{mxl_path.stem}'
            tmp.mkdir(exist_ok=True)
            out  = base / f'{mxl_path.stem}{suffix}.pdf'

            # 只渲染五线谱预览，不再回退到简谱。
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
            p.loop.call_soon_threadsafe(
                p.update, self._orig_viewer, self._trans_viewer, self._status
            )

    # ── 辅助 ─────────────────────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        self._status.value = msg
        p = self.page
        if p is not None:
            p.loop.call_soon_threadsafe(p.update, self._status)

    def _set_busy(self, busy: bool) -> None:
        self._progress.visible = busy
        self._progress.value   = None if busy else 0
        p = self.page
        if p is not None:
            p.loop.call_soon_threadsafe(p.update, self._progress)
