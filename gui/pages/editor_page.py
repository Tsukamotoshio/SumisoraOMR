# gui/pages/editor_page.py — 简谱编辑套件（Editing Suite）
# 双栏布局：左侧二值化图像预览（含滚轮缩放和放大镜），右侧简谱文本编辑器。
# 实现图像坐标 ↔ 文本行的"点对点"联动。

from __future__ import annotations

import base64
import io
import threading
from pathlib import Path
from typing import Optional

import flet as ft

from ..app_state import AppState, Event
from core.backend import editor_workspace_dir, open_directory
from ..components.jianpu_editor import JianpuEditor
from ..theme import Palette


# ─────────────────────────────────────────────────────────────────────────────
# 左侧：二值化图像浏览器（带缩放 + 放大镜 + 行高亮）
# ─────────────────────────────────────────────────────────────────────────────

class _BinaryImageView(ft.Column):
    """显示预处理后的二值化图像，支持鼠标滚轮缩放、放大镜和行高亮。"""

    MIN_SCALE = 0.25
    MAX_SCALE = 8.0
    SCALE_STEP = 0.1

    def __init__(self, state: AppState):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._scale = 1.0
        self._path: Optional[Path] = None
        self._raw_b64: Optional[str] = None
        self._highlighted_line: int = -1
        self._mag_visible = False
        self._mag_rendering = False   # 节流标志
        self._mag_pending: Optional[tuple] = None   # 最新位置
        self._load_token: int = 0
        self._build_ui()
        state.on(Event.JIANPU_TXT_SELECTED, self._on_line_selected)

    def _build_ui(self) -> None:
        self._scale_label = ft.Text('100%', size=12, color=Palette.TEXT_SECONDARY, width=44)
        self._mag_btn = ft.IconButton(ft.Icons.SEARCH_ROUNDED, icon_size=18, on_click=self._toggle_mag, tooltip='放大镜')

        toolbar = ft.Container(
            content=ft.Row(
                [
                    ft.IconButton(ft.Icons.ZOOM_OUT_ROUNDED, icon_size=18, on_click=self._zoom_out, tooltip='缩小'),
                    self._scale_label,
                    ft.IconButton(ft.Icons.ZOOM_IN_ROUNDED,  icon_size=18, on_click=self._zoom_in,  tooltip='放大'),
                    ft.IconButton(ft.Icons.FIT_SCREEN_ROUNDED, icon_size=18, on_click=self._zoom_fit, tooltip='适应'),
                    self._mag_btn,
                ],
                spacing=2,
            ),
            bgcolor=Palette.BG_SURFACE,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border=ft.Border.only(bottom=ft.BorderSide(1, Palette.DIVIDER_DARK)),
        )

        self._image = ft.Image(src=None, fit=ft.BoxFit.CONTAIN, expand=True, visible=False)
        self._placeholder = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.IMAGE_OUTLINED, size=40, color=Palette.TEXT_DISABLED),
                    ft.Text('请先在首页选择并转换文件，或在此页打开对应图像/简谱文件',
                            size=12, color=Palette.TEXT_DISABLED, text_align=ft.TextAlign.CENTER),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            expand=True,
            alignment=ft.Alignment(0, 0),
        )

        # 放大镜浮层（初始不可见）
        self._mag_crop = ft.Image(src=None, width=200, height=200, fit=ft.BoxFit.FILL,
                                  border_radius=ft.BorderRadius.all(6), visible=False)
        self._mag_container = ft.Container(
            content=self._mag_crop,
            bgcolor=Palette.MAGNIFIER_BG,
            border_radius=ft.BorderRadius.all(10),
            border=ft.Border.all(2, Palette.PRIMARY),
            visible=False, width=204, height=204,
            left=0, top=0,
        )

        self._gesture = ft.GestureDetector(
            content=ft.Stack(
                [self._placeholder, self._image, self._mag_container],   # 直接放入 Stack
                expand=True,
            ),
            on_scroll=self._on_scroll,
            on_hover=self._on_hover,
            on_tap=self._on_tap,
            expand=True,
        )

        self.controls = [
            toolbar,
            ft.Container(content=self._gesture, expand=True, bgcolor=Palette.BG_DARK,
                         clip_behavior=ft.ClipBehavior.HARD_EDGE),
        ]
        self.expand = True

    # ── 加载图像 ─────────────────────────────────────────────────────────────

    def load(self, path: Path) -> None:
        self._path = path
        self._scale = 1.0
        self._load_token += 1
        token = self._load_token
        # 重置缩放变换
        self._image.scale = None
        if path.suffix.lower() == '.pdf':
            threading.Thread(target=self._load_pdf_async, args=(path, token), daemon=True).start()
        else:
            threading.Thread(target=self._load_async, args=(path, token), daemon=True).start()

    def _load_pdf_async(self, path: Path, token: int) -> None:
        """PDF 文件：用 PyMuPDF 渲染第一页为 PNG，再二値化。"""
        try:
            import fitz
            with fitz.open(str(path)) as doc:
                page = doc[0]
                mat = fitz.Matrix(2.0, 2.0)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                buf = io.BytesIO(pix.tobytes('png'))
                b64_raw = base64.b64encode(buf.getvalue()).decode()
            if token != self._load_token:
                return
            try:
                from PIL import Image as PILImage
                buf2 = io.BytesIO(base64.b64decode(b64_raw))
                with PILImage.open(buf2) as img:
                    gray = img.convert('L')
                    binary = gray.point(lambda p: 255 if p > 128 else 0, '1').convert('RGB')
                    out = io.BytesIO()
                    binary.save(out, format='PNG')
                    raw_b64 = base64.b64encode(out.getvalue()).decode()
            except Exception:
                raw_b64 = b64_raw
            if token != self._load_token:
                return
            self._schedule_image_load(raw_b64, token)
        except ImportError:
            self._schedule_load_error('需要安装 PyMuPDF：pip install pymupdf', token)
        except Exception as exc:
            self._schedule_load_error(f'加载失败: {exc}', token)

    def _load_async(self, path: Path, token: int) -> None:
        try:
            try:
                from PIL import Image as PILImage
                with PILImage.open(path) as img:
                    gray = img.convert('L')
                    binary = gray.point(lambda p: 255 if p > 128 else 0, '1').convert('RGB')
                    buf = io.BytesIO()
                    binary.save(buf, format='PNG')
                    raw_b64 = base64.b64encode(buf.getvalue()).decode()
            except ImportError:
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
            self.page.run_task(self._async_apply_image_load, raw_b64, token)
        except Exception:
            self._apply_image_load(raw_b64, token)

    def _schedule_load_error(self, message: str, token: int) -> None:
        if not hasattr(self, 'page') or self.page is None:
            self._apply_load_error(message, token)
            return
        try:
            self.page.run_task(self._async_apply_load_error, message, token)
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
        self._image.visible = True
        self._placeholder.visible = False
        try:
            self.update()
        except Exception:
            pass

    def _apply_load_error(self, message: str, token: int) -> None:
        if token != self._load_token:
            return
        self._placeholder.controls[-1] = ft.Text(message, size=12, color=Palette.ERROR)
        self._placeholder.visible = True
        try:
            self.update()
        except Exception:
            pass

    # ── 缩放 ─────────────────────────────────────────────────────────────────

    def _zoom_in(self, _e=None) -> None:
        self._scale = min(self.MAX_SCALE, self._scale + self.SCALE_STEP)
        self._apply_scale()

    def _zoom_out(self, _e=None) -> None:
        self._scale = max(self.MIN_SCALE, self._scale - self.SCALE_STEP)
        self._apply_scale()

    def _zoom_fit(self, _e=None) -> None:
        self._scale = 1.0
        self._apply_scale()

    def _apply_scale(self) -> None:
        self._scale_label.value = f'{int(self._scale * 100)}%'
        # 用 Scale 变换实现视觉缩放（即时生效，无需 PIL 重渲染）
        self._image.scale = ft.Scale(scale=self._scale)
        try:
            self._scale_label.update()
            self._image.update()
        except Exception:
            pass

    def _rerender_scaled(self) -> None:
        # 保留兼容，内部改用 scale 变换
        self._apply_scale()

    def _on_scroll(self, e: ft.ScrollEvent) -> None:
        delta = e.scroll_delta.y if hasattr(e, 'scroll_delta') else 0
        if delta < 0:
            self._zoom_in()
        elif delta > 0:
            self._zoom_out()

    # ── 放大镜 ───────────────────────────────────────────────────────────────

    def _toggle_mag(self, _e) -> None:
        self._mag_visible = not self._mag_visible
        self._mag_container.visible = self._mag_visible
        try:
            self._mag_container.update()
        except Exception:
            pass

    def _on_hover(self, e: ft.HoverEvent) -> None:
        if not self._mag_visible or self._raw_b64 is None:
            return
        lx = e.local_position.x if hasattr(e, 'local_position') else 0
        ly = e.local_position.y if hasattr(e, 'local_position') else 0
        # 先更新位置（无 PIL 开销）——放大镜以鼠标为中心
        self._mag_container.left = lx - 102
        self._mag_container.top  = ly - 102
        try:
            self._mag_container.update()
        except Exception:
            pass
        # 节流：只在无渲染时才启动
        self._mag_pending = (lx, ly)
        if not self._mag_rendering:
            self._mag_rendering = True
            threading.Thread(target=self._mag_render_loop, daemon=True).start()

    def _mag_render_loop(self) -> None:
        """消费待渲染位置，确保每次只跑一个 PIL 线程。"""
        while True:
            pos = self._mag_pending
            if pos is None:
                break
            self._mag_pending = None
            self._render_magnifier(pos[0], pos[1])
        self._mag_rendering = False

    def _render_magnifier(self, px: float, py: float) -> None:
        try:
            from PIL import Image as PILImage
            buf = io.BytesIO(base64.b64decode(self._raw_b64))
            with PILImage.open(buf) as img:
                w, h = img.size
                radius = max(int(min(w, h) * 0.08), 40)
                cx = int(px / 800.0 * w)
                cy = int(py / 1000.0 * h)
                x0, y0 = max(cx - radius, 0), max(cy - radius, 0)
                x1, y1 = min(cx + radius, w), min(cy + radius, h)
                crop = img.crop((x0, y0, x1, y1)).resize((200, 200), PILImage.LANCZOS)
                out = io.BytesIO()
                crop.save(out, format='PNG')
                self._mag_crop.src = base64.b64encode(out.getvalue()).decode()
                self._mag_crop.visible = True
                try:
                    self._mag_crop.update()
                except Exception:
                    pass
        except Exception:
            pass

    # ── 行联动 ───────────────────────────────────────────────────────────────

    def _on_tap(self, e: ft.TapEvent) -> None:
        """将点击的 Y 坐标映射为简谱行号并广播事件。"""
        if self._raw_b64 is None:
            return
        lx = e.local_position.x if hasattr(e, 'local_position') else 0
        ly = e.local_position.y if hasattr(e, 'local_position') else 0
        try:
            from PIL import Image as PILImage
            buf = io.BytesIO(base64.b64decode(self._raw_b64))
            with PILImage.open(buf) as img:
                _, img_h = img.size
            num_lines = max(len(self._state.log_lines), 1)  # fallback
            if self._state.current_jianpu_txt and self._state.current_jianpu_txt.exists():
                lines = self._state.current_jianpu_txt.read_text(encoding='utf-8-sig', errors='replace').splitlines()
                num_lines = max(len(lines), 1)
            rel_y = ly / max(600, 1)  # 归一化
            line_no = int(rel_y * num_lines)
            self._highlighted_line = line_no
            self._state.emit(Event.JIANPU_TXT_SELECTED, line_no=line_no)
        except Exception:
            pass

    def _on_line_selected(self, line_no: int, **_kw) -> None:
        """外部（文本编辑器）选中行时，高亮图像对应区域（叠加颜色条）。"""
        self._highlighted_line = line_no
        # 在图像上叠加一条半透明横条（通过对整张图片应用 color_filter 近似）
        # Flet Image 不支持精确区域染色；此处仅记录状态供未来扩展。

    def reset(self) -> None:
        self._path = None
        self._raw_b64 = None
        self._highlighted_line = -1
        self._image.src = None
        self._image.visible = False
        self._placeholder.visible = True
        self._mag_container.visible = False
        self._mag_crop.visible = False
        try:
            self._image.update()
            self._placeholder.update()
            self._mag_container.update()
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
        self._img_view = _BinaryImageView(state)
        self._editor   = JianpuEditor(state)
        self._open_picker = ft.FilePicker()
        self._build_ui()
        state.on(Event.FILE_SELECTED, self._on_file_selected)
        state.on(Event.MXL_READY,     self._on_mxl_ready)

    def did_mount(self):
        self.page._services.register_service(self._open_picker)

    def _build_ui(self) -> None:
        open_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.FOLDER_OPEN_ROUNDED, size=16), ft.Text('打开')],
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
                    ft.Text('简谱编辑套件', size=13, weight=ft.FontWeight.W_600,
                            color=Palette.TEXT_SECONDARY),
                    ft.Container(expand=True),
                    open_output_btn,
                    open_btn,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            bgcolor=Palette.BG_SURFACE,
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            border=ft.Border.only(bottom=ft.BorderSide(1, Palette.DIVIDER_DARK)),
        )
        splitter = ft.VerticalDivider(width=1, color=Palette.DIVIDER_DARK, thickness=1)
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
        self.page.run_task(self._pick_open_async)

    def _on_open_output_dir(self, _e) -> None:
        try:
            open_directory(editor_workspace_dir().parent / 'Output')
        except Exception:
            pass

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
        path = Path(files[0].path)
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

    def reset_view(self) -> None:
        self._img_view.reset()
        self._editor.reset()
        self._state.current_jianpu_txt = None
