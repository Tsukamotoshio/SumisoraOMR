# gui/pages/editor_page.py — Jianpu Editing Suite.
# Two-column layout: left — binarised image preview (scroll-zoom + magnifier);
# right — jianpu text editor with point-to-point image↔text line linking.

from __future__ import annotations

import base64
import threading
from pathlib import Path
from typing import Callable, Optional

import flet as ft

from ..app_state import AppState, Event
from core.app.backend import editor_workspace_dir
from ..components.jianpu_editor import JianpuEditor
from ..components.pdf_viewer import _render_pdf_page
from ..theme import Palette

def _do_render_preview(txt_path: Path) -> tuple[Optional[str], Optional[str]]:
    """Render jianpu txt → LilyPond → PDF → PNG base64.

    Runs in a background thread. Returns (b64_png, None) on success or
    (None, error_message) on failure. Cleans up all temp files before returning.
    """
    import tempfile
    import shutil as _shutil
    from core.render.lilypond_runner import render_jianpu_ly, render_lilypond_pdf

    tmp_dir = Path(tempfile.mkdtemp(prefix='sumisora_preview_'))
    try:
        ly_path = tmp_dir / txt_path.with_suffix('.ly').name
        if not render_jianpu_ly(txt_path, ly_path):
            return None, 'jianpu-ly 转换失败，请确认安装'

        try:
            title = ''
            for ln in txt_path.read_text(encoding='utf-8').splitlines():
                if ln.strip().startswith('title='):
                    title = ln.strip()[len('title='):]
                    break
            from core.render.renderer import sanitize_generated_lilypond_file
            sanitize_generated_lilypond_file(ly_path, title)
        except Exception:
            pass

        pdf_path = render_lilypond_pdf(ly_path)
        if not pdf_path or not pdf_path.exists():
            return None, 'LilyPond 渲染失败，请检查文件语法'

        result = _render_pdf_page(pdf_path, 0)
        if result is None:
            return None, 'PDF 无法解析'
        return result[0], None
    except Exception as exc:
        return None, f'渲染出错: {exc}'
    finally:
        _shutil.rmtree(str(tmp_dir), ignore_errors=True)


# 1×1 透明 PNG，用于保持 InteractiveViewer content 始终 visible=True
_BLANK_PNG_B64 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg'
    'AAAABQABpfZFQAAAAABJRU5ErkJggg=='
)


# ─────────────────────────────────────────────────────────────────────────────
# Left panel: binarised image viewer (InteractiveViewer with zoom/pan + line highlight)
# ─────────────────────────────────────────────────────────────────────────────

class _BinaryImageView(ft.Column):
    """Displays the preprocessed binarised image with built-in zoom/pan and per-line highlight linking."""

    MIN_SCALE = 0.3
    MAX_SCALE = 8.0
    SCALE_STEP = 0.15

    def __init__(self, state: AppState, on_preview_requested: Optional[Callable[[], None]] = None):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._on_preview_requested = on_preview_requested or (lambda: None)
        self._path: Optional[Path] = None
        self._raw_b64: Optional[str] = None
        self._highlighted_line: int = -1
        self._load_token: int = 0
        self._active_tab: str = 'source'
        self._build_ui()
        state.on(Event.JIANPU_TXT_SELECTED, self._on_line_selected)

    def _build_ui(self) -> None:
        self._tab_source_btn = ft.TextButton(
            '源图像',
            on_click=self._on_tab_source,
            style=ft.ButtonStyle(color=Palette.PRIMARY),
        )
        self._tab_preview_btn = ft.TextButton(
            '简谱预览',
            on_click=self._on_tab_preview,
            style=ft.ButtonStyle(color=ft.Colors.ON_SURFACE_VARIANT),
        )

        toolbar = ft.Container(
            content=ft.Row(
                [
                    ft.Row(
                        [
                            ft.IconButton(ft.Icons.ZOOM_OUT_ROUNDED,    icon_size=18, on_click=self._zoom_out, tooltip='缩小'),
                            ft.IconButton(ft.Icons.ZOOM_IN_ROUNDED,     icon_size=18, on_click=self._zoom_in,  tooltip='放大'),
                            ft.IconButton(ft.Icons.FIT_SCREEN_ROUNDED,  icon_size=18, on_click=self._zoom_fit, tooltip='适应'),
                        ],
                        spacing=2,
                    ),
                    ft.Row(
                        [self._tab_source_btn, self._tab_preview_btn],
                        spacing=0,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=ft.Colors.SURFACE,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        self._image = ft.Image(
            src=_BLANK_PNG_B64, fit=ft.BoxFit.FIT_WIDTH,
            visible=True, gapless_playback=True,
        )
        self._tap_detector = ft.GestureDetector(
            content=self._image,
            on_tap=self._on_tap,
        )
        self._interactive = ft.InteractiveViewer(
            content=self._tap_detector,
            pan_enabled=True,
            scale_enabled=True,
            min_scale=self.MIN_SCALE,
            max_scale=self.MAX_SCALE,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            constrained=False,
            expand=True,
        )

        self._placeholder_col = ft.Column(
            [
                ft.Icon(ft.Icons.IMAGE_OUTLINED, size=40, color=ft.Colors.OUTLINE),
                ft.Text('请先在首页选择并转换文件，或在此页打开对应图像/简谱文件',
                        size=12, color=ft.Colors.OUTLINE, text_align=ft.TextAlign.CENTER),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._placeholder = ft.Container(
            content=self._placeholder_col,
            expand=True,
            alignment=ft.Alignment(0, 0),
        )

        self._view_container = ft.Container(
            content=ft.Stack([self._interactive, self._placeholder], expand=True),
            expand=True,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            on_size_change=self._on_viewer_resize,
        )

        # ── Preview area ─────────────────────────────────────────────────────
        self._preview_loading_col = ft.Column(
            [
                ft.ProgressRing(width=28, height=28, stroke_width=3),
                ft.Text('渲染中…', size=12, color=ft.Colors.OUTLINE),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=12,
        )
        self._preview_img = ft.Image(
            src=_BLANK_PNG_B64,
            fit=ft.BoxFit.FIT_WIDTH,
            visible=True,
            gapless_playback=True,
        )
        self._preview_img_container = ft.Container(
            content=self._preview_img,
            expand=True,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            on_size_change=self._on_preview_resize,
            visible=False,
        )
        self._preview_placeholder_col = ft.Column(
            [
                ft.Icon(ft.Icons.PREVIEW_OUTLINED, size=40, color=ft.Colors.OUTLINE),
                ft.Text('点击「简谱预览」生成渲染预览', size=12,
                        color=ft.Colors.OUTLINE, text_align=ft.TextAlign.CENTER),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._preview_state_container = ft.Container(
            content=self._preview_placeholder_col,
            expand=True,
            alignment=ft.Alignment(0, 0),
        )
        self._preview_area = ft.Container(
            content=ft.Stack(
                [self._preview_img_container, self._preview_state_container],
                expand=True,
            ),
            expand=True,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            visible=False,
        )

        self.controls = [
            toolbar,
            ft.Stack([self._view_container, self._preview_area], expand=True),
        ]
        self.expand = True

    def _on_viewer_resize(self, e) -> None:
        """视口尺寸变化时将图像宽度同步为视口宽度，消除左右黑边并允许垂直拖动。"""
        w = int(e.width)
        if w > 0 and self._image.width != w:
            self._image.width = w
            try:
                self._image.update()
            except Exception:
                pass

    def _on_preview_resize(self, e) -> None:
        w = int(e.width)
        if w > 0 and self._preview_img.width != w:
            self._preview_img.width = w
            try:
                self._preview_img.update()
            except Exception:
                pass

    # ── 加载图像 ─────────────────────────────────────────────────────────────

    def load(self, path: Path) -> None:
        self._path = path
        self._load_token += 1
        token = self._load_token
        if path.suffix.lower() == '.pdf':
            threading.Thread(target=self._load_pdf_async, args=(path, token), daemon=True).start()
        else:
            threading.Thread(target=self._load_async, args=(path, token), daemon=True).start()

    def _load_pdf_async(self, path: Path, token: int) -> None:
        """PDF 文件：用 pdf_viewer._render_pdf_page 渲染第一页（带 _PYPDFIUM2_LOCK）。"""
        try:
            result = _render_pdf_page(path, 0)
            if result is None:
                self._schedule_load_error('PDF 无法渲染', token)
                return
            raw_b64, _ = result
            if token != self._load_token:
                return
            self._schedule_image_load(raw_b64, token)
        except Exception as exc:
            self._schedule_load_error(f'加载失败: {exc}', token)

    def _load_async(self, path: Path, token: int) -> None:
        try:
            with open(path, 'rb') as f:
                raw_b64 = base64.b64encode(f.read()).decode()
            if token != self._load_token:
                return
            self._schedule_image_load(raw_b64, token)
        except Exception as exc:
            if token != self._load_token:
                return
            self._schedule_load_error(f'加载失败: {exc}', token)

    def _schedule_image_load(self, raw_b64: str, token: int) -> None:
        if not hasattr(self, 'page') or self.page is None:
            self._apply_image_load(raw_b64, token)
            return
        try:
            self.page.run_task(self._async_apply_image_load, raw_b64, token)  # type: ignore[attr-defined]
        except Exception:
            self._apply_image_load(raw_b64, token)

    def _schedule_load_error(self, message: str, token: int) -> None:
        if not hasattr(self, 'page') or self.page is None:
            self._apply_load_error(message, token)
            return
        try:
            self.page.run_task(self._async_apply_load_error, message, token)  # type: ignore[attr-defined]
        except Exception:
            self._apply_load_error(message, token)

    async def _async_apply_image_load(self, raw_b64: str, token: int) -> None:
        self._apply_image_load(raw_b64, token)

    async def _async_apply_load_error(self, message: str, token: int) -> None:
        self._apply_load_error(message, token)

    def _apply_image_load(self, raw_b64: str, token: int) -> None:
        if token != self._load_token:
            return
        self._raw_b64 = raw_b64
        self._image.src = self._raw_b64
        self._placeholder.visible = False
        try:
            self.update()
        except Exception:
            pass

    def _apply_load_error(self, message: str, token: int) -> None:
        if token != self._load_token:
            return
        self._placeholder_col.controls[-1] = ft.Text(message, size=12, color=Palette.ERROR)
        self._placeholder.visible = True
        try:
            self.update()
        except Exception:
            pass

    # ── 缩放（InteractiveViewer 内建）────────────────────────────────────────

    async def _zoom_in(self, _e=None) -> None:
        await self._interactive.zoom(1 + self.SCALE_STEP)

    async def _zoom_out(self, _e=None) -> None:
        await self._interactive.zoom(1.0 / (1 + self.SCALE_STEP))

    async def _zoom_fit(self, _e=None) -> None:
        await self._interactive.reset()

    # ── Tab 切换 ─────────────────────────────────────────────────────────────

    def _on_tab_source(self, _e) -> None:
        self._active_tab = 'source'
        self._tab_source_btn.style  = ft.ButtonStyle(color=Palette.PRIMARY)
        self._tab_preview_btn.style = ft.ButtonStyle(color=ft.Colors.ON_SURFACE_VARIANT)
        self._view_container.visible  = True
        self._preview_area.visible = False
        try:
            self.update()
        except Exception:
            pass

    def _on_tab_preview(self, _e) -> None:
        self._active_tab = 'preview'
        self._tab_source_btn.style  = ft.ButtonStyle(color=ft.Colors.ON_SURFACE_VARIANT)
        self._tab_preview_btn.style = ft.ButtonStyle(color=Palette.PRIMARY)
        self._view_container.visible  = False
        self._preview_area.visible = True
        try:
            self.update()
        except Exception:
            pass
        self._on_preview_requested()

    # ── 行联动 ───────────────────────────────────────────────────────────────

    def _on_tap(self, e: ft.TapEvent) -> None:
        """将点击的 Y 坐标映射为简谱行号并广播事件。"""
        if self._raw_b64 is None:
            return
        ly = e.local_position.y if hasattr(e, 'local_position') else 0  # type: ignore[union-attr]
        try:
            num_lines = max(len(self._state.log_lines), 1)
            if self._state.current_jianpu_txt and self._state.current_jianpu_txt.exists():
                lines = self._state.current_jianpu_txt.read_text(encoding='utf-8-sig', errors='replace').splitlines()
                num_lines = max(len(lines), 1)
            rel_y = ly / max(600, 1)
            line_no = int(rel_y * num_lines)
            self._highlighted_line = line_no
            self._state.emit(Event.JIANPU_TXT_SELECTED, line_no=line_no)
        except Exception:
            pass

    def _on_line_selected(self, line_no: int, **_kw) -> None:
        """外部（文本编辑器）选中行时记录状态（供未来扩展行高亮）。"""
        self._highlighted_line = line_no

    def show_preview_loading(self) -> None:
        if not self._preview_area.visible:
            return
        self._preview_img_container.visible = False
        self._preview_state_container.content = self._preview_loading_col
        self._preview_state_container.visible = True
        try:
            self._preview_area.update()
        except Exception:
            pass

    def show_preview_image(self, b64: str) -> None:
        if not self._preview_area.visible:
            return
        self._preview_img.src = b64
        self._preview_img_container.visible = True
        self._preview_state_container.visible = False
        try:
            self._preview_area.update()
        except Exception:
            pass

    def show_preview_error(self, message: str) -> None:
        if not self._preview_area.visible:
            return
        error_col = ft.Column(
            [
                ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, size=36, color=Palette.ERROR),
                ft.Text(message, size=12, color=Palette.ERROR,
                        text_align=ft.TextAlign.CENTER),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        )
        self._preview_img_container.visible = False
        self._preview_state_container.content = error_col
        self._preview_state_container.visible = True
        try:
            self._preview_area.update()
        except Exception:
            pass

    def reset(self) -> None:
        self._path = None
        self._raw_b64 = None
        self._highlighted_line = -1
        self._image.src = _BLANK_PNG_B64
        self._placeholder.visible = True
        self._active_tab = 'source'
        self._tab_source_btn.style  = ft.ButtonStyle(color=Palette.PRIMARY)
        self._tab_preview_btn.style = ft.ButtonStyle(color=ft.Colors.ON_SURFACE_VARIANT)
        self._view_container.visible  = True
        self._preview_area.visible = False
        self._preview_img.src = _BLANK_PNG_B64
        self._preview_img_container.visible = False
        self._preview_state_container.content = self._preview_placeholder_col
        self._preview_state_container.visible = True
        try:
            self.update()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# 编辑套件页面
# ─────────────────────────────────────────────────────────────────────────────

class EditorPage(ft.Row):
    """编辑套件：左侧二值化图像预览 + 右侧简谱文本编辑器。"""

    def __init__(self, state: AppState):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._has_been_shown = False
        self._img_view = _BinaryImageView(state, on_preview_requested=self._render_preview)
        self._editor   = JianpuEditor(state)
        self._open_picker = ft.FilePicker()
        self._build_ui()
        state.on(Event.FILE_SELECTED,        self._on_file_selected)
        state.on(Event.MXL_READY,            self._on_mxl_ready)
        state.on(Event.JIANPU_EDIT_REQUESTED, self._on_edit_requested)

    def did_mount(self):
        self.page._services.register_service(self._open_picker)  # type: ignore[attr-defined]

    def _build_ui(self) -> None:
        back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_ROUNDED,
            icon_size=18,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip='返回简谱预览',
            on_click=lambda _: self._state.emit(Event.JIANPU_PREVIEW_BACK),
            width=32,
            height=32,
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
        top_bar = ft.Container(
            content=ft.Row(
                [
                    back_btn,
                    ft.Text('简谱编辑', size=13, weight=ft.FontWeight.W_700,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Container(expand=True),
                    open_btn,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            bgcolor=ft.Colors.SURFACE,
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )
        splitter = ft.VerticalDivider(width=1, color=ft.Colors.OUTLINE_VARIANT, thickness=1)
        body = ft.Row(
            [
                ft.Container(content=self._img_view, expand=True),
                splitter,
                ft.Container(content=self._editor,  expand=True),
            ],
            spacing=0,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self.controls = [
            ft.Column(
                [top_bar, ft.Container(content=body, expand=True)],
                spacing=0,
                expand=True,
            ),
        ]
        self.expand = True
        self.vertical_alignment = ft.CrossAxisAlignment.STRETCH

    def _on_open_click(self, _e) -> None:
        self.page.run_task(self._pick_open_async)  # type: ignore[attr-defined]

    async def _pick_open_async(self) -> None:
        init_dir = editor_workspace_dir()
        files = await self._open_picker.pick_files(
            dialog_title='打开图像文件（PDF / PNG / JPG）',
            allowed_extensions=['png', 'jpg', 'jpeg', 'pdf'],
            allow_multiple=False,
            initial_directory=str(init_dir),
        )
        if not files:
            return
        path = Path(files[0].path)  # type: ignore[arg-type]
        self._open_file(path)

    def _normalize_editor_stem(self, stem: str) -> str:
        if stem.endswith('.audiveris'):
            stem = stem[: -len('.audiveris')]
        if stem.endswith('.source'):
            stem = stem[: -len('.source')]
        return stem

    def _find_matching_jianpu(self, stem: str, parent: Path, ws: Path) -> Optional[Path]:
        stem = self._normalize_editor_stem(stem)
        candidates = list(parent.glob(f'{stem}*.jianpu.txt')) + list(ws.glob(f'{stem}*.jianpu.txt'))
        for cand in candidates:
            if cand.exists():
                return cand
        return None

    def _find_matching_source(self, stem: str, parent: Path, ws: Path) -> Optional[Path]:
        stem = self._normalize_editor_stem(stem)
        for ext in ('.png', '.jpg', '.jpeg', '.pdf'):
            for cand in [
                parent / f'{stem}.source{ext}',
                ws / f'{stem}.source{ext}',
                parent / f'{stem}{ext}',
                ws / f'{stem}{ext}',
            ]:
                if cand.exists():
                    return cand
        return None

    def _open_file(self, path: Path) -> None:
        """智能打开文件，并自动配对另一半。"""
        ws = editor_workspace_dir()
        suffix_lo = path.suffix.lower()

        if suffix_lo in ('.png', '.jpg', '.jpeg', '.pdf'):
            self._img_view.load(path)
            stem = path.stem
            matched_txt = self._find_matching_jianpu(stem, path.parent, ws)
            if matched_txt is not None:
                self._editor.load(matched_txt)
                self._state.current_jianpu_txt = matched_txt
            else:
                self._editor.reset()
                self._state.current_jianpu_txt = None
        elif suffix_lo == '.txt':
            self._editor.load(path)
            self._state.current_jianpu_txt = path
            stem = path.stem.replace('.jianpu', '')
            matched_source = self._find_matching_source(stem, path.parent, ws)
            if matched_source is not None:
                self._img_view.load(matched_source)
            else:
                self._img_view.reset()
                self._state.current_jianpu_txt = path

    def _on_file_selected(self, path: Path, **_kw) -> None:
        # 只响应用户在编辑器页面内主动触发的选择；
        # 来自识别页等其他页面的选择事件不应联动到编辑器。
        if getattr(self._state, 'current_page', 'editor') != 'editor':
            return
        ws = editor_workspace_dir()
        suffix_lo = path.suffix.lower()

        if suffix_lo in ('.png', '.jpg', '.jpeg', '.pdf'):
            self._img_view.load(path)
            matched_txt = self._find_matching_jianpu(path.stem, path.parent, ws)
            if matched_txt is not None:
                self._editor.load(matched_txt)
                self._state.current_jianpu_txt = matched_txt
            else:
                self._editor.reset()
                self._state.current_jianpu_txt = None
        elif suffix_lo in ('.mxl', '.musicxml'):
            matched_txt = self._find_matching_jianpu(path.stem, path.parent, ws)
            if matched_txt is not None:
                self._editor.load(matched_txt)
                self._state.current_jianpu_txt = matched_txt
            else:
                self._editor.reset()
                self._state.current_jianpu_txt = None

            matched_source = self._find_matching_source(path.stem, path.parent, ws)
            if matched_source is not None:
                self._img_view.load(matched_source)
            else:
                self._img_view.reset()
        else:
            self._editor.reset()
            self._state.current_jianpu_txt = None

    def _on_mxl_ready(self, path: Path, **_kw) -> None:
        """MXL 就绪后尝试自动加载对应的 jianpu.txt 和源图像。"""
        if getattr(self._state, 'current_page', 'editor') != 'editor':
            return
        ws = editor_workspace_dir()
        stem = path.stem
        matched_txt = self._find_matching_jianpu(stem, path.parent, ws)
        if matched_txt is not None:
            self._editor.load(matched_txt)
            self._state.current_jianpu_txt = matched_txt
        else:
            self._editor.reset()
            self._state.current_jianpu_txt = None

        matched_source = self._find_matching_source(stem, path.parent, ws)
        if matched_source is not None:
            self._img_view.load(matched_source)
        else:
            self._img_view.reset()

    def load_from_output_pdf(self, pdf_path: Path) -> None:
        """根据 Output/ 中的 Jianpu PDF，查找并加载 editor-workspace/ 中对应的源图像和 jianpu.txt。"""
        ws = editor_workspace_dir()
        # 输出 PDF 命名为 '{源文件名}_jianpu.pdf'，editor-workspace 用源文件名存储；还原源文件名
        stem = pdf_path.stem
        if stem.endswith('_jianpu'):
            stem = stem[: -len('_jianpu')]
        matched_txt = self._find_matching_jianpu(stem, pdf_path.parent, ws)
        if matched_txt is not None:
            self._editor.load(matched_txt)
            self._state.current_jianpu_txt = matched_txt
        else:
            self._editor.reset()
            self._state.current_jianpu_txt = None
        matched_source = self._find_matching_source(stem, pdf_path.parent, ws)
        if matched_source is not None:
            self._img_view.load(matched_source)
        else:
            self._img_view.reset()

    def _on_edit_requested(self, path: Optional[Path] = None, **_kw) -> None:
        if path is not None:
            self.load_from_output_pdf(path)
        self._has_been_shown = True

    def _render_preview(self) -> None:
        txt_path = self._state.current_jianpu_txt
        if txt_path is None or not txt_path.exists():
            self._img_view.show_preview_error('未加载简谱文件')
            return
        self._editor.save()
        self._img_view.show_preview_loading()
        threading.Thread(
            target=self._render_preview_thread,
            args=(txt_path,),
            daemon=True,
        ).start()

    def _render_preview_thread(self, txt_path: Path) -> None:
        b64, err = _do_render_preview(txt_path)
        try:
            if self.page is not None:
                self.page.run_task(self._async_preview_done, b64, err)  # type: ignore[attr-defined]
        except Exception:
            pass

    async def _async_preview_done(self, b64: Optional[str], err: Optional[str]) -> None:
        if b64:
            self._img_view.show_preview_image(b64)
        else:
            self._img_view.show_preview_error(err or '渲染失败')

    def reset_view(self) -> None:
        self._img_view.reset()
        self._editor.reset()
        self._state.current_jianpu_txt = None
