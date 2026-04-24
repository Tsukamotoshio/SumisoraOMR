# gui/components/pdf_viewer.py — PDF / 图片预览组件
# 使用 PyMuPDF (fitz) 将 PDF 页面渲染为内存图片，以 base64 流喂给 ft.Image。
# 缩放与平移由 ft.InteractiveViewer 内建手势管理；工具栏按钮通过 IV.zoom()/reset() 精确控制。

from __future__ import annotations

import base64
import io
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Any

import flet as ft

from ..theme import Palette

# 1×1 透明 PNG（base64）——作为 ft.Image 安全的初始 src。
# InteractiveViewer 要求 content.visible=True，因此图片始终 visible，
# 用此占位符代替 src=None，避免 Flutter 端空 src 告警。
_BLANK_PNG_B64 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg'
    'AAAABQABpfZFQAAAAABJRU5ErkJggg=='
)


class PdfViewer(ft.Column):
    """PDF / 图片预览控件。

    示例::

        viewer = PdfViewer()
        viewer.load(Path('score.pdf'))
    """

    MIN_SCALE = 0.3
    MAX_SCALE = 5.0
    SCALE_STEP = 0.15

    def __init__(self, on_page_change=None):
        super().__init__(spacing=0, expand=True)
        self._path: Optional[Path] = None
        self._preview_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_preview_cache = 8
        self._load_token: int = 0
        self._page_count: int = 0
        self._current_page: int = 0
        self._on_page_change = on_page_change
        self._refresh_pending = False
        self._refresh_waiting = False
        self._fitz_doc = None
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._build_ui()

    # ── 构建 UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._page_label = ft.Text('—', size=12, color=Palette.TEXT_SECONDARY)

        toolbar = ft.Row(
            [
                ft.IconButton(ft.Icons.CHEVRON_LEFT_ROUNDED,  icon_size=18, on_click=self._prev_page, tooltip='上一页'),
                self._page_label,
                ft.IconButton(ft.Icons.CHEVRON_RIGHT_ROUNDED, icon_size=18, on_click=self._next_page, tooltip='下一页'),
                ft.VerticalDivider(width=1, color=Palette.DIVIDER_DARK),
                ft.IconButton(ft.Icons.ZOOM_OUT_ROUNDED,      icon_size=18, on_click=self._zoom_out,  tooltip='缩小'),
                ft.IconButton(ft.Icons.ZOOM_IN_ROUNDED,       icon_size=18, on_click=self._zoom_in,   tooltip='放大'),
                ft.IconButton(ft.Icons.FIT_SCREEN_ROUNDED,    icon_size=18, on_click=self._zoom_fit,  tooltip='复位缩放'),
            ],
            spacing=2,
            alignment=ft.MainAxisAlignment.CENTER,
        )
        toolbar_bar = ft.Container(
            content=toolbar,
            bgcolor=Palette.BG_SURFACE,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border=ft.Border.only(bottom=ft.BorderSide(1, Palette.DIVIDER_DARK)),
        )

        # ft.Image 始终 visible=True（InteractiveViewer 要求 content 必须可见）。
        # gapless_playback=True：切换 src 时保持旧帧，避免灰色闪烁。
        # 宽度由 _on_viewer_resize 动态注入，使图像填满视口宽度（无黑边）。
        self._image = ft.Image(
            src=_BLANK_PNG_B64,
            fit=ft.BoxFit.FIT_WIDTH,
            visible=True,
            gapless_playback=True,
        )

        # 占位符列（单独保存，方便 _show_error / reset 修改内容）
        self._placeholder_col = ft.Column(
            [
                ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, size=48, color=Palette.TEXT_DISABLED),
                ft.Text('暂无文件', size=13, color=Palette.TEXT_DISABLED),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        # 占位符作为 Stack 顶层浮层：无文件时覆盖在 IV 之上
        self._placeholder = ft.Container(
            content=self._placeholder_col,
            expand=True,
            alignment=ft.Alignment(0, 0),
        )

        # InteractiveViewer(constrained=False)：允许内容高于视口，从而可垂直拖动查看完整乐谱。
        # 图像宽度由 _on_viewer_resize 设定；自然高度 = 宽度 × 宽高比，超出视口部分可平移。
        self._interactive = ft.InteractiveViewer(
            content=self._image,
            pan_enabled=True,
            scale_enabled=True,
            min_scale=self.MIN_SCALE,
            max_scale=self.MAX_SCALE,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            constrained=False,
            expand=True,
        )

        # Stack：IV 在底层显示图像，placeholder 在顶层遮挡（无文件时可见）
        self._view_stack = ft.Stack(
            [self._interactive, self._placeholder],
            expand=True,
        )

        scroll_view = ft.Container(
            content=self._view_stack,
            expand=True,
            bgcolor=Palette.BG_CARD,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            on_size_change=self._on_viewer_resize,
        )

        self.controls = [toolbar_bar, scroll_view]
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

    # ── 缓存 ─────────────────────────────────────────────────────────────────

    def _cache_key(self, path: Path) -> str:
        return str(path.resolve())

    def _get_cached_preview(self, path: Path) -> Optional[dict[str, Any]]:
        return self._preview_cache.get(self._cache_key(path))

    def _cache_preview(self, path: Path, b64: str, page_count: int) -> None:
        key = self._cache_key(path)
        if key in self._preview_cache:
            self._preview_cache.move_to_end(key)
        self._preview_cache[key] = {'b64': b64, 'page_count': page_count}
        while len(self._preview_cache) > self._max_preview_cache:
            self._preview_cache.popitem(last=False)

    def preload(self, path: Path) -> None:
        key = self._cache_key(path)
        if key in self._preview_cache:
            return
        self._executor.submit(self._preload_path, path, key)

    def _preload_path(self, path: Path, key: str) -> None:
        if key in self._preview_cache:
            return
        data = self._load_preview_data(path)
        if data is None:
            return
        b64, page_count = data
        self._cache_preview(path, b64, page_count)

    def _load_preview_data(self, path: Path) -> Optional[tuple[str, int]]:
        try:
            if path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp', '.webp'):
                with open(path, 'rb') as f:
                    raw_bytes = f.read()
                return base64.b64encode(raw_bytes).decode(), 1
            import fitz
            with fitz.open(str(path)) as doc:
                page_count = len(doc)
                page = doc[0]
                pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                return base64.b64encode(pix.tobytes('png')).decode(), page_count
        except Exception:
            return None

    # ── 加载文件 ─────────────────────────────────────────────────────────────

    def load(self, path: Path) -> None:
        """在后台线程中加载文件，不阻塞 UI 线程。"""
        self._path = path
        self._current_page = 0
        self._load_token += 1
        current_token = self._load_token

        if self._fitz_doc is not None:
            try:
                self._fitz_doc.close()
            except Exception:
                pass
            self._fitz_doc = None

        # 立即进入加载中状态（显示占位符，图像重置为透明占位）
        self._image.src = _BLANK_PNG_B64
        self._placeholder.visible = True
        self._request_page_refresh()

        cache = self._get_cached_preview(path)
        if cache is not None:
            self._page_count = cache['page_count']
            self._set_image_b64(cache['b64'])
            self._update_toolbar()
            if path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.bmp', '.webp'):
                self._executor.submit(self._ensure_document_loaded, path, current_token)
            return

        if path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp', '.webp'):
            self._executor.submit(self._load_image_async, path, current_token)
        else:
            self._executor.submit(self._load_pdf_async, path, current_token)

    def will_unmount(self) -> None:
        self._load_token += 1  # 使所有后台任务尽快放弃
        self._executor.shutdown(wait=False)
        if self._fitz_doc is not None:
            try:
                self._fitz_doc.close()
            except Exception:
                pass
            self._fitz_doc = None

    def reset(self) -> None:
        """重置为空白占位符状态（无文件）。"""
        self._load_token += 1
        self._path = None
        self._page_count = 0
        self._current_page = 0

        if self._fitz_doc is not None:
            try:
                self._fitz_doc.close()
            except Exception:
                pass
            self._fitz_doc = None

        self._image.src = _BLANK_PNG_B64
        self._placeholder_col.controls = [
            ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, size=48, color=Palette.TEXT_DISABLED),
            ft.Text('暂无文件', size=13, color=Palette.TEXT_DISABLED),
        ]
        self._placeholder.visible = True
        self._update_toolbar()

    def _load_image_async(self, path: Path, token: int) -> None:
        try:
            with open(path, 'rb') as f:
                data = f.read()
            if token != self._load_token:
                return
            b64 = base64.b64encode(data).decode()
            self._page_count = 1
            self._cache_preview(path, b64, 1)
            self._set_image_b64(b64)
            self._update_toolbar()
        except Exception as exc:
            if token != self._load_token:
                return
            self._show_error(str(exc))

    def _load_pdf_async(self, path: Path, token: int) -> None:
        try:
            import fitz
            doc = fitz.open(str(path))
            if token != self._load_token:
                try:
                    doc.close()
                except Exception:
                    pass
                return
            self._page_count = len(doc)
            self._fitz_doc = doc
            self._render_current_page(token=token)
            if token != self._load_token:
                return
            self._update_toolbar()
        except ImportError:
            if token != self._load_token:
                return
            self._show_error('需要安装 PyMuPDF：pip install pymupdf')
        except Exception as exc:
            if token != self._load_token:
                return
            self._show_error(str(exc))

    def _ensure_document_loaded(self, path: Path, token: int) -> None:
        """缓存命中时，在后台打开 fitz 文档以支持翻页，不重新渲染首页。"""
        if token != self._load_token:
            return
        try:
            import fitz
            if self._fitz_doc is None:
                doc = fitz.open(str(path))
                if token != self._load_token:
                    try:
                        doc.close()
                    except Exception:
                        pass
                    return
                self._fitz_doc = doc
        except Exception:
            pass

    def _render_current_page(self, token: Optional[int] = None) -> None:
        """渲染当前页为 PNG base64，使用内存缓冲区，不写硬盘。"""
        if token is None:
            token = self._load_token
        if token != self._load_token:
            return
        if self._fitz_doc is None:
            return
        try:
            import fitz
            page = self._fitz_doc[self._current_page]
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)  # ×2 HiDPI
            b64 = base64.b64encode(pix.tobytes('png')).decode()
            if token != self._load_token:
                return
            self._set_image_b64(b64)
        except Exception as exc:
            if token != self._load_token:
                return
            self._show_error(str(exc))

    # ── UI 状态更新辅助 ───────────────────────────────────────────────────────

    def _set_image_b64(self, b64: str) -> None:
        self._image.src = b64
        self._placeholder.visible = False
        self._request_page_refresh()

    def _show_error(self, msg: str) -> None:
        self._placeholder_col.controls = [
            ft.Icon(ft.Icons.ERROR_OUTLINE, size=36, color=Palette.ERROR),
            ft.Text(msg, size=12, color=Palette.ERROR),
        ]
        self._image.src = _BLANK_PNG_B64
        self._placeholder.visible = True
        self._request_page_refresh()

    def _update_toolbar(self) -> None:
        if self._page_count > 0:
            self._page_label.value = f'{self._current_page + 1} / {self._page_count}'
        else:
            self._page_label.value = '—'
        self._request_page_refresh()

    def _request_page_refresh(self) -> None:
        if self._refresh_pending:
            self._refresh_waiting = True
            return
        try:
            pg = self.page
            loop = pg.loop
        except (RuntimeError, AttributeError):
            # 控件尚未挂载到页面树，跳过 UI 刷新
            return
        self._refresh_pending = True

        def _do_refresh() -> None:
            self._refresh_pending = False
            try:
                pg.update(self)
            except Exception:
                pass
            if self._refresh_waiting:
                self._refresh_waiting = False
                self._request_page_refresh()

        loop.call_soon_threadsafe(_do_refresh)

    # ── 工具栏事件 ───────────────────────────────────────────────────────────

    def _prev_page(self, _e) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._executor.submit(self._render_current_page)
            self._update_toolbar()
            if self._on_page_change:
                self._on_page_change(self._current_page)

    def _next_page(self, _e) -> None:
        if self._current_page < self._page_count - 1:
            self._current_page += 1
            self._executor.submit(self._render_current_page)
            self._update_toolbar()
            if self._on_page_change:
                self._on_page_change(self._current_page)

    async def _zoom_in(self, _e) -> None:
        await self._interactive.zoom(1 + self.SCALE_STEP)

    async def _zoom_out(self, _e) -> None:
        await self._interactive.zoom(1.0 / (1 + self.SCALE_STEP))

    async def _zoom_fit(self, _e) -> None:
        await self._interactive.reset()
