# gui/components/pdf_viewer.py — PDF / 图片预览组件
# 使用 PyMuPDF (fitz) 将 PDF 页面渲染为内存图片，以 base64 流喂给 ft.Image。
# 支持鼠标滚轮缩放和页面跳转；提供放大镜浮层。

from __future__ import annotations

import base64
import io
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Any

import flet as ft

from ..theme import Palette


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
        self._raw_b64: Optional[str] = None  # 缓存原始图片数据，供放大镜用
        self._current_render_bytes: Optional[bytes] = None
        self._current_render_image = None
        self._preview_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_preview_cache = 8
        self._load_token: int = 0
        self._page_count: int = 0
        self._current_page: int = 0
        self._scale: float = 1.0
        self._on_page_change = on_page_change
        self._mag_visible = False
        self._mag_rendering = False   # 节流标志：防止 hover 事件堆积线程
        self._mag_pending: Optional[tuple] = None   # 最新待渲染位置
        self._render_timer: Optional[threading.Timer] = None
        self._refresh_pending = False
        self._refresh_waiting = False
        self._executor = ThreadPoolExecutor(max_workers=4)
        # 平移状态（scale > 1 时生效）
        self._pan_x: float = 0.0
        self._pan_y: float = 0.0
        self._build_ui()

    # ── 构建 UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # 顶部工具栏：页码 + 缩放
        self._page_label = ft.Text('—', size=12, color=Palette.TEXT_SECONDARY)
        self._scale_label = ft.Text('100%', size=12, color=Palette.TEXT_SECONDARY, width=44)

        toolbar = ft.Row(
            [
                ft.IconButton(ft.Icons.CHEVRON_LEFT_ROUNDED,   icon_size=18, on_click=self._prev_page,    tooltip='上一页'),
                self._page_label,
                ft.IconButton(ft.Icons.CHEVRON_RIGHT_ROUNDED,  icon_size=18, on_click=self._next_page,    tooltip='下一页'),
                ft.VerticalDivider(width=1, color=Palette.DIVIDER_DARK),
                ft.IconButton(ft.Icons.ZOOM_OUT_ROUNDED,       icon_size=18, on_click=self._zoom_out,     tooltip='缩小'),
                self._scale_label,
                ft.IconButton(ft.Icons.ZOOM_IN_ROUNDED,        icon_size=18, on_click=self._zoom_in,      tooltip='放大'),
                ft.IconButton(ft.Icons.FIT_SCREEN_ROUNDED,     icon_size=18, on_click=self._zoom_fit,     tooltip='适应宽度'),
                ft.IconButton(ft.Icons.SEARCH_ROUNDED,         icon_size=18, on_click=self._toggle_mag,   tooltip='放大镜'),
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

        # 主图片区域（初始不可见，避免 src=None 触发 Flutter 告警）
        self._image = ft.Image(
            src=None,
            fit=ft.BoxFit.CONTAIN,
            expand=True,
            visible=False,
        )
        self._placeholder = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, size=48, color=Palette.TEXT_DISABLED),
                    ft.Text('暂无文件', size=13, color=Palette.TEXT_DISABLED),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            expand=True,
            alignment=ft.Alignment(0, 0),
        )

        # 放大镜浮层（初始不可见）
        self._mag_crop = ft.Image(
            src=None,
            width=200,
            height=200,
            fit=ft.BoxFit.FILL,
            border_radius=ft.BorderRadius.all(8),
            visible=False,
        )
        self._mag_container = ft.Container(
            content=self._mag_crop,
            bgcolor=Palette.MAGNIFIER_BG,
            border_radius=ft.BorderRadius.all(10),
            border=ft.Border.all(2, Palette.PRIMARY),
            visible=False,
            width=204,
            height=204,
            left=0,
            top=0,
        )

        # 手势检测：滚轮缩放 + 鼠标移动（放大镜）+ 拖动平移
        self._gesture = ft.GestureDetector(
            content=ft.Stack(
                [
                    self._placeholder,
                    self._image,
                    self._mag_container,   # 直接作为 Stack 子控件，left/top 才能生效
                ],
                expand=True,
            ),
            on_scroll=self._on_scroll,
            on_hover=self._on_hover,
            on_pan_update=self._on_pan_update,
            expand=True,
        )

        scroll_view = ft.Container(
            content=self._gesture,
            expand=True,
            bgcolor=Palette.BG_DARK,
            border_radius=ft.BorderRadius.all(0),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

        self.controls = [toolbar_bar, scroll_view]
        self.expand = True

    # ── 加载文件 ─────────────────────────────────────────────────────────────

    def _cache_key(self, path: Path) -> str:
        return str(path.resolve())

    def _get_cached_preview(self, path: Path) -> Optional[dict[str, Any]]:
        return self._preview_cache.get(self._cache_key(path))

    def _cache_preview(self, path: Path, b64: str, raw_bytes: bytes, image: Optional[Any], page_count: int) -> None:
        key = self._cache_key(path)
        if key in self._preview_cache:
            self._preview_cache.move_to_end(key)
        self._preview_cache[key] = {
            'b64': b64,
            'bytes': raw_bytes,
            'image': image,
            'page_count': page_count,
        }
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
        b64, raw_bytes, image, page_count = data
        self._cache_preview(path, b64, raw_bytes, image, page_count)

    def _load_preview_data(self, path: Path) -> Optional[tuple[str, bytes, Optional[Any], int]]:
        try:
            if path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp', '.webp'):
                with open(path, 'rb') as f:
                    raw_bytes = f.read()
                b64 = base64.b64encode(raw_bytes).decode()
                image = None
                try:
                    from PIL import Image as PILImage
                    image = PILImage.open(io.BytesIO(raw_bytes)).convert('RGBA')
                except Exception:
                    image = None
                return b64, raw_bytes, image, 1
            import fitz
            with fitz.open(str(path)) as doc:
                page_count = len(doc)
                page = doc[0]
                mat = fitz.Matrix(self._scale * 2.0, self._scale * 2.0)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                raw_bytes = pix.tobytes('png')
                b64 = base64.b64encode(raw_bytes).decode()
                image = None
                try:
                    from PIL import Image as PILImage
                    image = PILImage.open(io.BytesIO(raw_bytes)).convert('RGBA')
                except Exception:
                    image = None
                return b64, raw_bytes, image, page_count
        except Exception:
            return None

    def load(self, path: Path) -> None:
        """在后台线程中加载文件，不阻塞 UI 线程。"""
        self._path = path
        self._current_page = 0
        self._scale = 1.0
        self._mag_visible = False
        self._mag_container.visible = False
        self._mag_crop.visible = False
        self._load_token += 1
        current_token = self._load_token

        if self._render_timer is not None:
            self._render_timer.cancel()
            self._render_timer = None

        if hasattr(self, '_fitz_doc') and self._fitz_doc is not None:
            try:
                self._fitz_doc.close()
            except Exception:
                pass
            self._fitz_doc = None

        cache = self._get_cached_preview(path)
        self._image.visible = False
        self._placeholder.visible = True
        self._image.src = None
        self._current_render_bytes = None
        self._current_render_image = None
        self._request_page_refresh()

        if cache is not None:
            self._raw_b64 = cache['b64']
            self._current_render_bytes = cache['bytes']
            self._current_render_image = cache['image']
            self._page_count = cache['page_count']
            self._set_image_b64(self._raw_b64)
            self._update_toolbar()
            if path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.bmp', '.webp'):
                self._executor.submit(self._ensure_document_loaded, path, current_token)
            return

        if path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp', '.webp'):
            # 直接图片格式，用 BytesIO → base64
            self._executor.submit(self._load_image_async, path, current_token)
        else:
            # PDF 用 PyMuPDF
            self._executor.submit(self._load_pdf_async, path, current_token)

    def _load_image_async(self, path: Path, token: int) -> None:
        try:
            with open(path, 'rb') as f:
                data = f.read()
            if token != self._load_token:
                return
            b64 = base64.b64encode(data).decode()
            self._raw_b64 = b64  # 缓存原始数据
            self._current_render_bytes = data
            try:
                from PIL import Image as PILImage
                self._current_render_image = PILImage.open(io.BytesIO(self._current_render_bytes)).convert('RGBA')
            except Exception:
                self._current_render_image = None
            self._page_count = 1
            self._set_image_b64(b64)
            self._update_toolbar()
        except Exception as exc:
            if token != self._load_token:
                return
            self._show_error(str(exc))

    def _load_pdf_async(self, path: Path, token: int) -> None:
        try:
            import fitz  # PyMuPDF
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

    def _render_current_page(self, token: Optional[int] = None) -> None:
        """渲染当前页到 base64，使用内存缓冲区，不写硬盘。"""
        if token is None:
            token = self._load_token
        if token != self._load_token:
            return
        if not hasattr(self, '_fitz_doc') or self._fitz_doc is None:
            return
        try:
            import fitz
            page = self._fitz_doc[self._current_page]
            mat = fitz.Matrix(self._scale * 2.0, self._scale * 2.0)  # ×2 for HiDPI
            pix = page.get_pixmap(matrix=mat, alpha=False)
            buf = io.BytesIO(pix.tobytes('png'))
            image_bytes = buf.getvalue()
            if token != self._load_token:
                return
            self._current_render_bytes = image_bytes
            try:
                from PIL import Image as PILImage
                self._current_render_image = PILImage.open(io.BytesIO(image_bytes)).convert('RGBA')
            except Exception:
                self._current_render_image = None
            b64 = base64.b64encode(image_bytes).decode()
            if token != self._load_token:
                return
            self._set_image_b64(b64)
        except Exception as exc:
            if token != self._load_token:
                return
            self._show_error(str(exc))

    # ── 放大镜 ───────────────────────────────────────────────────────────────

    def _render_magnifier(self, lx: float, ly: float) -> None:
        """在鼠标位置 (lx, ly)（局部像素坐标）生成 200×200 放大镜截图。
        注意：只写属性，不调用 update()，由调用方统一刷新。
        """
        if not self._mag_visible:
            return
        if self._current_render_image is not None:
            try:
                from PIL import Image as PILImage
                img = self._current_render_image
                w, h = img.size
                radius = max(int(min(w, h) * 0.08), 40)
                cx = int(min(max(lx / self._VIEWER_W_EST, 0.0), 1.0) * w)
                cy = int(min(max(ly / self._VIEWER_H_EST, 0.0), 1.0) * h)
                x0 = max(cx - radius, 0)
                y0 = max(cy - radius, 0)
                x1 = min(cx + radius, w)
                y1 = min(cy + radius, h)
                crop = img.crop((x0, y0, x1, y1)).resize((200, 200), PILImage.LANCZOS)
                out = io.BytesIO()
                crop.save(out, format='PNG')
                self._mag_crop.src = base64.b64encode(out.getvalue()).decode()
                self._mag_crop.visible = True
            except Exception:
                self._mag_crop.visible = False
        else:
            if hasattr(self, '_fitz_doc') and self._fitz_doc is not None:
                try:
                    import fitz
                    page = self._fitz_doc[self._current_page]
                    pw, ph = page.rect.width, page.rect.height
                    rel_x = lx / max(1.0, pw * self._scale)
                    rel_y = ly / max(1.0, ph * self._scale)
                    hw, hh = 40, 40
                    cx, cy = rel_x * pw, rel_y * ph
                    clip = fitz.Rect(cx - hw, cy - hh, cx + hw, cy + hh) & page.rect
                    zoom = 200 / max(1, 2 * min(hw, hh))
                    mat = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
                    buf = io.BytesIO(pix.tobytes('png'))
                    b64 = base64.b64encode(buf.getvalue()).decode()
                    self._mag_crop.src = b64
                    self._mag_crop.visible = True
                except Exception:
                    self._mag_crop.visible = False
            elif self._raw_b64 is not None:
                try:
                    from PIL import Image as PILImage
                    buf = io.BytesIO(base64.b64decode(self._raw_b64))
                    with PILImage.open(buf) as img:
                        w, h = img.size
                        radius = max(int(min(w, h) * 0.08), 40)
                        cx = int(lx / 800.0 * w)
                        cy = int(ly / 1000.0 * h)
                        x0 = max(cx - radius, 0); y0 = max(cy - radius, 0)
                        x1 = min(cx + radius, w); y1 = min(cy + radius, h)
                        crop = img.crop((x0, y0, x1, y1)).resize((200, 200), PILImage.LANCZOS)
                        out = io.BytesIO()
                        crop.save(out, format='PNG')
                        self._mag_crop.src = base64.b64encode(out.getvalue()).decode()
                        self._mag_crop.visible = True
                except Exception:
                    self._mag_crop.visible = False

    # ── UI 状态更新辅助 ───────────────────────────────────────────────────────

    def _set_image_b64(self, b64: str) -> None:
        self._image.src = b64
        self._image.visible = True
        self._placeholder.visible = False
        self._request_page_refresh()

    def _show_error(self, msg: str) -> None:
        self._placeholder.controls = [
            ft.Icon(ft.Icons.ERROR_OUTLINE, size=36, color=Palette.ERROR),
            ft.Text(msg, size=12, color=Palette.ERROR),
        ]
        self._image.visible = False
        self._placeholder.visible = True
        self._request_page_refresh()

    def _update_toolbar(self) -> None:
        if self._page_count > 0:
            self._page_label.value = f'{self._current_page + 1} / {self._page_count}'
        else:
            self._page_label.value = '—'
        self._scale_label.value = f'{int(self._scale * 100)}%'
        self._request_page_refresh()

    def _request_page_refresh(self) -> None:
        if not hasattr(self, 'page') or self.page is None:
            return
        if self._refresh_pending:
            self._refresh_waiting = True
            return
        self._refresh_pending = True
        try:
            self.page.run_task(self._async_refresh)
        except Exception:
            self._refresh_pending = False
            try:
                self.page.schedule_update()
            except Exception:
                pass

    async def _async_refresh(self) -> None:
        try:
            self.update()
        except Exception:
            pass
        finally:
            self._refresh_pending = False
            if self._refresh_waiting:
                self._refresh_waiting = False
                self._request_page_refresh()

    # ── 工具栏事件 ───────────────────────────────────────────────────────────

    def _prev_page(self, _e) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            threading.Thread(target=self._render_current_page, daemon=True).start()
            self._update_toolbar()
            if self._on_page_change:
                self._on_page_change(self._current_page)

    def _next_page(self, _e) -> None:
        if self._current_page < self._page_count - 1:
            self._current_page += 1
            threading.Thread(target=self._render_current_page, daemon=True).start()
            self._update_toolbar()
            if self._on_page_change:
                self._on_page_change(self._current_page)

    def _zoom_in(self, _e) -> None:
        self._scale = min(self.MAX_SCALE, self._scale + self.SCALE_STEP)
        self._apply_scale()

    def _zoom_out(self, _e) -> None:
        self._scale = max(self.MIN_SCALE, self._scale - self.SCALE_STEP)
        self._apply_scale()

    def _zoom_fit(self, _e) -> None:
        self._scale = 1.0
        self._apply_scale()

    def _apply_scale(self) -> None:
        """立即应用视觉缩放变换；PDF 模式异步重渲以获得清晰度。"""
        # 每次缩放后重置平移，避免图像跑出边界
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._image.scale = ft.Scale(scale=self._scale)
        self._image.offset = ft.Offset(0, 0)
        try:
            self._image.update()
        except Exception:
            pass
        self._update_toolbar()
        if hasattr(self, '_fitz_doc') and self._fitz_doc is not None:
            self._schedule_render_current_page()

    def _schedule_render_current_page(self) -> None:
        if self._render_timer is not None:
            self._render_timer.cancel()
        self._render_timer = threading.Timer(0.2, self._render_current_page)
        self._render_timer.daemon = True
        self._render_timer.start()

    # ── 拖动平移 ─────────────────────────────────────────────────────────────
    # offset 单位 = 控件自身尺寸的分数（0.5 = 移动半个控件宽度）
    # 预估查看区宽 ~600px、高 ~700px。
    _VIEWER_W_EST = 600.0
    _VIEWER_H_EST = 700.0

    def _on_pan_update(self, e: ft.DragUpdateEvent) -> None:
        if self._scale <= 1.0:
            return
        ld = e.local_delta
        if ld is None:
            return
        self._pan_x += ld.x / self._VIEWER_W_EST
        self._pan_y += ld.y / self._VIEWER_H_EST
        # 限制平移范围，防止图像完全滑出视口
        max_pan = (self._scale - 1.0) * 0.5
        self._pan_x = max(-max_pan, min(max_pan, self._pan_x))
        self._pan_y = max(-max_pan, min(max_pan, self._pan_y))
        self._image.offset = ft.Offset(self._pan_x, self._pan_y)
        try:
            self._image.update()
        except Exception:
            pass

    def _toggle_mag(self, _e) -> None:
        self._mag_visible = not self._mag_visible
        self._mag_container.visible = self._mag_visible
        if not self._mag_visible:
            self._mag_pending = None
        try:
            self._mag_container.update()
        except Exception:
            pass

    def _rerender(self) -> None:
        self._apply_scale()

    def _on_scroll(self, e: ft.ScrollEvent) -> None:
        delta = e.scroll_delta.y if hasattr(e, 'scroll_delta') else 0
        if delta is None:
            return
        if delta < 0:
            self._zoom_in(None)
        elif delta > 0:
            self._zoom_out(None)

    def _on_hover(self, e: ft.HoverEvent) -> None:
        if not self._mag_visible:
            return
        lx = e.local_position.x if hasattr(e, 'local_position') else 0
        ly = e.local_position.y if hasattr(e, 'local_position') else 0
        # 只记录最新位置，不做立即更新——渲染循环会一次性更新位置 + 内容
        self._mag_pending = (lx, ly)
        if not self._mag_rendering:
            self._mag_rendering = True
            threading.Thread(target=self._mag_render_loop, daemon=True).start()

    def _mag_render_loop(self) -> None:
        """消费 pending 位置并限速到 ~20fps，每次循环仅发出一次 update。"""
        while True:
            pos = self._mag_pending
            if pos is None:
                break
            self._mag_pending = None
            lx, ly = pos
            # 将放大镜窗口显示在光标右下方，避免遮挡当前光标位置
            offset_x = lx + 24
            offset_y = ly + 24
            mag_w = self._mag_container.width or 204
            mag_h = self._mag_container.height or 204
            max_x = max(8.0, self._VIEWER_W_EST - mag_w - 8.0)
            max_y = max(8.0, self._VIEWER_H_EST - mag_h - 8.0)
            self._mag_container.left = min(max(offset_x, 8.0), max_x)
            self._mag_container.top = min(max(offset_y, 8.0), max_y)
            # 渲染内容（只设值，不调用 update）
            self._render_magnifier(lx, ly)
            # 一次性刷新容器（位置 + 内容 同时生效）
            try:
                self._mag_container.update()
            except Exception:
                pass
            # 限速 ~12fps，避免频繁更新导致 UI 卡顿
            time.sleep(0.08)
        self._mag_rendering = False
