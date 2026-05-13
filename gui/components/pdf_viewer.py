# gui/components/pdf_viewer.py — PDF / 图片预览组件
# 使用 pypdfium2 将 PDF 页面渲染为内存图片，以 base64 流喂给 ft.Image。
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

# PDFium (the C++ library underlying pypdfium2) is NOT thread-safe.
# Concurrent FPDF_* calls from multiple Python threads can segfault and
# kill the entire process — which is exactly what was killing the Flet
# WebSocket and freezing the UI. All pypdfium2 calls must hold this lock.
_PYPDFIUM2_LOCK = threading.Lock()


def _render_pdf_page(path: Path, page_index: int) -> Optional[tuple[str, int]]:
    """Open a PDF, render one page to PNG base64, close everything.

    Returns (b64_png, total_page_count) or None on failure.
    Serialized via _PYPDFIUM2_LOCK — PDFium native code is not thread-safe.
    """
    import pypdfium2 as pdfium
    with _PYPDFIUM2_LOCK:
        doc = pdfium.PdfDocument(str(path))
        page = None
        bitmap = None
        try:
            page_count = len(doc)
            if page_count == 0:
                return None
            page = doc[page_index]
            bitmap = page.render(scale=2.0)
            buf = io.BytesIO()
            bitmap.to_pil().save(buf, 'PNG')
            return base64.b64encode(buf.getvalue()).decode(), page_count
        finally:
            for obj in (bitmap, page, doc):
                if obj is None:
                    continue
                try:
                    obj.close()
                except Exception:
                    pass


class PdfViewer(ft.Column):
    """PDF / 图片预览控件。

    示例::

        viewer = PdfViewer()
        viewer.load(Path('score.pdf'))
    """

    MIN_SCALE = 0.3
    MAX_SCALE = 5.0
    SCALE_STEP = 0.15

    def __init__(self, on_page_change=None, extra_controls: list | None = None):
        super().__init__(spacing=0, expand=True)
        self._path: Optional[Path] = None
        self._preview_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_preview_cache = 8
        self._load_token: int = 0
        self._page_count: int = 0
        self._current_page: int = 0
        self._on_page_change = on_page_change
        self._extra_controls: list = extra_controls or []
        self._refresh_pending = False
        self._refresh_waiting = False
        self._is_image: bool = False
        self._container_width: int = 0
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._build_ui()

    # ── 构建 UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._page_label = ft.Text('—', size=13, color=ft.Colors.ON_SURFACE_VARIANT)

        toolbar = ft.Row(
            [
                ft.IconButton(ft.Icons.CHEVRON_LEFT_ROUNDED,  icon_size=18, on_click=self._prev_page, tooltip='上一页'),
                self._page_label,
                ft.IconButton(ft.Icons.CHEVRON_RIGHT_ROUNDED, icon_size=18, on_click=self._next_page, tooltip='下一页'),
                ft.VerticalDivider(width=1, color=ft.Colors.OUTLINE_VARIANT),
                ft.IconButton(ft.Icons.ZOOM_OUT_ROUNDED,      icon_size=18, on_click=self._zoom_out,  tooltip='缩小'),
                ft.IconButton(ft.Icons.ZOOM_IN_ROUNDED,       icon_size=18, on_click=self._zoom_in,   tooltip='放大'),
                ft.IconButton(ft.Icons.FIT_SCREEN_ROUNDED,    icon_size=18, on_click=self._zoom_fit,  tooltip='复位缩放'),
                *(([ft.VerticalDivider(width=1, color=ft.Colors.OUTLINE_VARIANT)] + self._extra_controls)
                  if self._extra_controls else []),
            ],
            spacing=2,
            alignment=ft.MainAxisAlignment.CENTER,
        )
        toolbar_bar = ft.Container(
            content=toolbar,
            bgcolor=ft.Colors.SURFACE,
            height=48,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        # ft.Image 始终 visible=True（InteractiveViewer 要求 content 必须可见）。
        # gapless_playback=True：切换 src 时保持旧帧，避免灰色闪烁。
        # PDF 模式：宽度由 _on_viewer_resize 注入（FIT_WIDTH）。
        # 图片模式：不设显式尺寸，InteractiveViewer(constrained=True) 自动填满视口。
        self._image = ft.Image(
            src=_BLANK_PNG_B64,
            fit=ft.BoxFit.FIT_WIDTH,
            visible=True,
            gapless_playback=True,
        )

        # 占位符列（单独保存，方便 _show_error / reset 修改内容）
        self._placeholder_col = ft.Column(
            [
                ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, size=48, color=ft.Colors.OUTLINE),
                ft.Text('暂无文件', size=14, color=ft.Colors.OUTLINE),
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

        # 初始为 constrained=False（PDF 模式）：允许内容高于视口，可垂直拖动查看完整乐谱。
        # 图片模式下切换为 constrained=True：Flutter 以视口大小为紧约束传给 Image，自动填满。
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
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            on_size_change=self._on_viewer_resize,
        )

        self.controls = [toolbar_bar, scroll_view]
        self.expand = True

    def _on_viewer_resize(self, e) -> None:
        """视口尺寸变化时同步 PDF 图像宽度；图片模式由 Flutter 布局自动处理，无需 Python 干预。"""
        w = int(e.width)
        # Track container width so _on_page_resize can skip invisible viewers.
        self._container_width = w
        self._apply_viewer_size()
        # Image mode: no Python-side refresh needed — Flutter resizes the image
        # automatically via InteractiveViewer(constrained=True) layout constraints.
        if not self._is_image and w > 0:
            self._request_page_refresh()

    # ── 缓存 ─────────────────────────────────────────────────────────────────

    def _cache_key(self, path: Path) -> str:
        # 将文件修改时间纳入缓存键：文件被覆写后 mtime 变化，
        # 确保不会返回旧内容的缓存，解决重新渲染后预览未刷新的问题。
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return f"{path.resolve()}@{mtime}"

    def _get_cached_preview(self, path: Path) -> Optional[dict[str, Any]]:
        return self._preview_cache.get(self._cache_key(path))

    def _cache_preview(self, path: Path, b64: str, page_count: int) -> None:
        key = self._cache_key(path)
        if key in self._preview_cache:
            self._preview_cache.move_to_end(key)
        self._preview_cache[key] = {'b64': b64, 'page_count': page_count}
        while len(self._preview_cache) > self._max_preview_cache:
            self._preview_cache.popitem(last=False)

    def _apply_viewer_size(self) -> None:
        # Image mode: InteractiveViewer(constrained=True) passes tight constraints
        # equal to the viewport to its child, so the Image fills the viewport
        # automatically via Flutter's layout system — no explicit Python dimensions
        # needed. Setting them would fight the tight constraints and cause stale
        # sizing when the window is maximized/restored and on_size_change lags.
        if self._is_image:
            return
        if self._container_width <= 0:
            return
        self._image.width = self._container_width
        self._image.height = None

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
            return _render_pdf_page(path, 0)
        except Exception:
            return None

    # ── 加载文件 ─────────────────────────────────────────────────────────────

    def load(self, path: Path) -> None:
        """在后台线程中加载文件，不阻塞 UI 线程。"""
        self._path = path
        self._current_page = 0
        self._load_token += 1
        current_token = self._load_token

        # 按文件类型切换 InteractiveViewer 模式：
        # 图片用 constrained=True + CONTAIN：Flutter 以视口尺寸为紧约束自动填满，无需 Python 追踪尺寸；
        # PDF 用 constrained=False + FIT_WIDTH：允许高页面垂直拖动，宽度由 _on_viewer_resize 注入。
        is_image = path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp', '.webp')
        if is_image != self._is_image:
            self._is_image = is_image
            self._interactive.constrained = is_image
            self._image.fit = ft.BoxFit.CONTAIN if is_image else ft.BoxFit.FIT_WIDTH
            if is_image:
                # Clear any previously set dimensions so Flutter sizes the image
                # via InteractiveViewer(constrained=True) tight constraints.
                self._image.width = None
                self._image.height = None

        # 立即进入加载中状态（显示占位符，图像重置为透明占位）
        self._image.src = _BLANK_PNG_B64
        self._placeholder.visible = True
        self._request_page_refresh()

        # 切换文件时重置缩放 / 平移状态
        try:
            self.page.run_task(self._reset_zoom_async)
        except Exception:
            pass

        cache = self._get_cached_preview(path)
        if cache is not None:
            self._page_count = cache['page_count']
            self._set_image_b64(cache['b64'])
            self._update_toolbar()
            return

        if is_image:
            self._executor.submit(self._load_image_async, path, current_token)
        else:
            self._executor.submit(self._load_pdf_async, path, current_token)

    def did_mount(self) -> None:
        # Register a page.on_resize handler (chaining any previously registered
        # handler) so we can force a layout pass when the window is resized.
        # on_size_change on the scroll_view Container is reliable for gradual
        # resizes but can lag on instant window actions such as maximize /
        # restore — page.on_resize closes that gap.
        self._prev_page_resize_handler = self.page.on_resize
        self.page.on_resize = self._on_page_resize

    def _on_page_resize(self, _e) -> None:
        """Window maximized / restored: chain to previous handler then force a
        PDF width re-apply in case on_size_change lagged behind the window event."""
        prev = getattr(self, '_prev_page_resize_handler', None)
        if prev is not None:
            try:
                prev(_e)
            except Exception:
                pass
        # Image mode: Flutter's layout system handles resizing automatically via
        # InteractiveViewer(constrained=True) — no Python refresh needed here.
        # PDF mode: on_size_change is reliable for gradual drags but can lag on
        # instant maximize/restore; nudge a refresh so the width stays correct.
        if not self._is_image and getattr(self, '_container_width', 0) > 0:
            self._request_page_refresh()

    def will_unmount(self) -> None:
        self._load_token += 1  # 使所有后台任务尽快放弃
        self._executor.shutdown(wait=False)
        try:
            self.page.on_resize = getattr(self, '_prev_page_resize_handler', None)
        except Exception:
            pass

    def reset(self) -> None:
        """重置为空白占位符状态（无文件）。"""
        self._load_token += 1
        self._path = None
        self._page_count = 0
        self._current_page = 0
        self._is_image = False

        self._interactive.constrained = False
        self._image.fit = ft.BoxFit.FIT_WIDTH
        self._image.width = None
        self._image.height = None
        self._image.src = _BLANK_PNG_B64
        self._placeholder_col.controls = [
            ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, size=48, color=ft.Colors.OUTLINE),
            ft.Text('暂无文件', size=14, color=ft.Colors.OUTLINE),
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
            result = _render_pdf_page(path, 0)
            if result is None:
                if token != self._load_token:
                    return
                self._show_error('PDF 无法渲染（文件可能已损坏或格式不受支持）')
                self._update_toolbar()
                return
            b64, page_count = result
            if token != self._load_token:
                return
            self._page_count = page_count
            self._cache_preview(path, b64, page_count)
            self._set_image_b64(b64)
            self._update_toolbar()
        except Exception as exc:
            if token != self._load_token:
                return
            self._show_error(str(exc))

    def _render_current_page(self, token: Optional[int] = None) -> None:
        """渲染当前页为 PNG base64，使用内存缓冲区，不写硬盘。"""
        if token is None:
            token = self._load_token
        if token != self._load_token:
            return
        path = self._path
        if path is None:
            return
        page_index = self._current_page
        if self._page_count == 0 or page_index < 0 or page_index >= self._page_count:
            return
        try:
            result = _render_pdf_page(path, page_index)
            if result is None:
                return
            b64, _ = result
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
        self._apply_viewer_size()
        self._request_page_refresh()

    def _show_error(self, msg: str) -> None:
        self._placeholder_col.controls = [
            ft.Icon(ft.Icons.ERROR_OUTLINE, size=36, color=Palette.ERROR),
            ft.Text(msg, size=13, color=Palette.ERROR),
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
            loop = pg.loop  # type: ignore[union-attr]
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

    async def _reset_zoom_async(self) -> None:
        try:
            await self._interactive.reset()
        except Exception:
            pass

    async def _zoom_in(self, _e) -> None:
        await self._interactive.zoom(1 + self.SCALE_STEP)

    async def _zoom_out(self, _e) -> None:
        await self._interactive.zoom(1.0 / (1 + self.SCALE_STEP))

    async def _zoom_fit(self, _e) -> None:
        await self._interactive.reset()
