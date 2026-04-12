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
from ..backend import xml_scores_dir, output_dir, build_dir, open_directory
from ..components.pdf_viewer import PdfViewer
from ..theme import Palette, section_title


# 常用调名列表
_KEYS = ['C', 'G', 'D', 'A', 'E', 'B', 'F#', 'C#', 'F', 'Bb', 'Eb', 'Ab', 'Db', 'Gb', 'Cb']


class TransposerPage(ft.Column):
    """移调引擎：输入调 → 目标调，左右对比预览，一键导出。"""

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

    # ── 构建 UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # 顶部控制条
        self._from_key_dd = ft.Dropdown(
            label='原调',
            value='C',
            options=[ft.dropdown.Option(k) for k in _KEYS],
            width=110,
            bgcolor=Palette.BG_INPUT,
            color=Palette.TEXT_PRIMARY,
            text_size=13,
        )
        self._to_key_dd = ft.Dropdown(
            label='目标调',
            value='G',
            options=[ft.dropdown.Option(k) for k in _KEYS],
            width=110,
            bgcolor=Palette.BG_INPUT,
            color=Palette.TEXT_PRIMARY,
            text_size=13,
        )
        self._semitone_label = ft.Text('↑ 7 个半音', size=12, color=Palette.TEXT_SECONDARY)

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
        self._auto_detect_btn = ft.TextButton(
            '自动检测原调',
            icon=ft.Icons.AUTO_FIX_HIGH_ROUNDED,
            on_click=self._on_auto_detect,
            style=ft.ButtonStyle(color=Palette.TEXT_SECONDARY),
        )

        self._from_key_dd.on_change = self._on_key_change
        self._to_key_dd.on_change   = self._on_key_change

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
            content=ft.Row(
                [
                    section_title('移调引擎', self._state.dark_mode),
                    ft.Row([self._from_key_dd, ft.Text('→', color=Palette.TEXT_SECONDARY), self._to_key_dd], spacing=8),
                    self._semitone_label,
                    self._auto_detect_btn,
                    ft.Container(expand=True),
                    ft.Row([open_btn, transpose_btn, export_btn, open_output_btn], spacing=8),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
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
        self._status = ft.Text('', size=12, color=Palette.TEXT_SECONDARY)
        self._progress = ft.ProgressBar(value=None, visible=False, bgcolor=Palette.BG_CARD, color=Palette.PRIMARY, height=3)

        bottom_bar = ft.Container(
            content=ft.Column([self._progress, self._status], spacing=4),
            padding=ft.Padding.symmetric(horizontal=16, vertical=6),
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
            dialog_title='打开乐谱文件（MusicXML / PDF）',
            allowed_extensions=['mxl', 'musicxml', 'xml', 'pdf', 'png', 'jpg'],
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
            try:
                self._orig_viewer.update()
                self.update()
                if hasattr(self, 'page') and self.page is not None:
                    try:
                        self.page.update()
                    except Exception:
                        pass
            except Exception:
                pass
            self._on_auto_detect(None)
        elif suffix in ('.pdf', '.png', '.jpg'):
            # 直接预览图像 / PDF
            self._clear_transposed_preview()
            self._orig_render_token += 1
            self._orig_viewer.load(path)
            self._set_status(f'预览: {path.name}（仅预览，移调需 MXL 格式）')
            try:
                self._orig_viewer.update()
                self.update()
                if hasattr(self, 'page') and self.page is not None:
                    try:
                        self.page.update()
                    except Exception:
                        pass
            except Exception:
                pass

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

    def _on_key_change(self, _e=None) -> None:
        from core.transposer import get_transposition_semitones
        try:
            semitones = get_transposition_semitones(
                self._from_key_dd.value or 'C',
                self._to_key_dd.value   or 'C',
            )
            direction = '↑' if semitones >= 0 else '↓'
            self._semitone_label.value = f'{direction} {abs(semitones)} 个半音'
            self._state.transpose_from_key = self._from_key_dd.value or 'C'
            self._state.transpose_to_key   = self._to_key_dd.value   or 'C'
            try:
                self._semitone_label.update()
            except Exception:
                pass
        except Exception:
            pass

    def _on_auto_detect(self, _e) -> None:
        if self._orig_mxl is None:
            self._set_status('请先在首页转换文件。')
            return
        self._auto_detect_token += 1
        current_token = self._auto_detect_token
        threading.Thread(target=self._auto_detect_async, args=(current_token,), daemon=True).start()

    def _auto_detect_async(self, token: int) -> None:
        try:
            if token != self._auto_detect_token:
                return
            from core.transposer import detect_key_from_musicxml
            key = detect_key_from_musicxml(self._orig_mxl)
            if token != self._auto_detect_token:
                return
            self._from_key_dd.value = key
            try:
                self._from_key_dd.update()
            except Exception:
                pass
            if token != self._auto_detect_token:
                return
            self._on_key_change()
            if token != self._auto_detect_token:
                return
            self._set_status(f'自动检测到调号: {key}')
        except Exception as exc:
            if token != self._auto_detect_token:
                return
            self._set_status(f'调号检测失败: {exc}')

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
            from core.transposer import transpose_musicxml
            import tempfile

            base = build_dir()
            dst = base / f'{self._orig_mxl.stem}_transposed_{self._to_key_dd.value}.musicxml'

            def _progress(v: float):
                if token != self._transpose_token:
                    return
                self._progress.value = v
                try:
                    self._progress.update()
                except Exception:
                    pass

            transposed = transpose_musicxml(
                self._orig_mxl, dst,
                from_key=self._from_key_dd.value or 'C',
                to_key=self._to_key_dd.value     or 'C',
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
            from core.runtime_finder import render_musicxml_staff_pdf

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
        self._trans_viewer._image.src = None
        self._trans_viewer._image.visible = False
        self._trans_viewer._placeholder.visible = True
        try:
            self._trans_viewer.update()
        except Exception:
            pass

    def _mxl_to_preview_pdf(self, mxl_path: Path, suffix: str) -> Optional[Path]:
        """将 MusicXML 渲染为五线谱 PDF（用于预览）。优先用 musicxml2ly 渲染标准五线谱，
        失败时回退到简谱渲染。"""
        try:
            base = build_dir()
            tmp  = base / f'_preview_tmp_{mxl_path.stem}'
            tmp.mkdir(exist_ok=True)
            out  = base / f'{mxl_path.stem}{suffix}.pdf'

            # 只渲染五线谱预览，不再回退到简谱。
            from core.runtime_finder import render_musicxml_staff_pdf
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
        self._orig_viewer._image.src = None
        self._orig_viewer._image.visible = False
        self._orig_viewer._placeholder.visible = True
        self._trans_viewer._image.src = None
        self._trans_viewer._image.visible = False
        self._trans_viewer._placeholder.visible = True
        self._status.value = ''
        try:
            self._orig_viewer.update()
            self._trans_viewer.update()
            self._status.update()
        except Exception:
            pass

    # ── 辅助 ─────────────────────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        self._status.value = msg
        try:
            self._status.update()
        except Exception:
            pass

    def _set_busy(self, busy: bool) -> None:
        self._progress.visible = busy
        self._progress.value   = None if busy else 0
        try:
            self._progress.update()
        except Exception:
            pass
