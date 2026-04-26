# gui/components/jianpu_editor.py — 嵌入式简谱文本编辑器
# 右侧文本区：加载 .jianpu.txt 文件，支持编辑与保存。
# 实现与左侧图像区的"点对点"行映射；点击某行时发布 JIANPU_TXT_SELECTED 事件。

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

import flet as ft

from ..app_state import AppState, Event
from ..theme import Palette, section_title


class JianpuEditor(ft.Column):
    """简谱文本编辑器面板。

    功能
    ----
    - 加载 / 保存 .jianpu.txt 文件
    - 高亮当前选中行（与左侧图像区联动）
    - 发出 JIANPU_TXT_SELECTED 事件（line_no: int, 0-indexed）
    """

    def __init__(self, state: AppState):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._path: Optional[Path] = None
        self._lines: list[str] = []
        self._load_token: int = 0
        self._file_lock = threading.Lock()
        self._build_ui()
        state.on(Event.JIANPU_TXT_SELECTED, self._on_external_select)

    # ── 构建 UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._title = section_title('简谱编辑器', self._state.dark_mode)
        save_btn = ft.TextButton(
            '保存',
            icon=ft.Icons.SAVE_OUTLINED,
            on_click=self._on_save,
            style=ft.ButtonStyle(color=Palette.PRIMARY),
        )
        export_btn = ft.TextButton(
            '导出PDF',
            icon=ft.Icons.PICTURE_AS_PDF_OUTLINED,
            on_click=self._on_export_pdf,
            style=ft.ButtonStyle(color=ft.Colors.ON_SURFACE_VARIANT),
            tooltip='将当前简谱文件通过 LilyPond 渲染为 PDF',
        )

        toolbar = ft.Container(
            content=ft.Row(
                [self._title, ft.Row([export_btn, save_btn], spacing=4)],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            bgcolor=ft.Colors.SURFACE,
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        # 主编辑区：用 TextField（多行）支持直接编辑
        self._editor = ft.TextField(
            multiline=True,
            expand=True,
            text_size=13,
            text_style=ft.TextStyle(font_family='Consolas', font_family_fallback='YaHei'),
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            color=ft.Colors.ON_SURFACE,
            border_color='transparent',
            focused_border_color=Palette.PRIMARY,
            cursor_color=Palette.PRIMARY,
            on_change=self._on_text_change,
            on_selection_change=self._on_selection_change,
            hint_text='尚未加载简谱文件…',
            hint_style=ft.TextStyle(color=ft.Colors.OUTLINE),
        )

        editor_row = ft.Row(
            [
                ft.Container(content=self._editor, expand=True, padding=ft.Padding.all(4)),
            ],
            spacing=0,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        self.controls = [
            toolbar,
            ft.Container(content=editor_row, expand=True),
        ]
        self.expand = True

    # ── 加载 / 保存 ──────────────────────────────────────────────────────────

    def load(self, path: Path) -> None:
        self._path = path
        self._load_token += 1
        token = self._load_token
        threading.Thread(target=self._load_async, args=(path, token), daemon=True).start()

    def _load_async(self, path: Path, token: int) -> None:
        try:
            text = path.read_text(encoding='utf-8-sig', errors='replace')
            if token != self._load_token:
                return
            self._schedule_load_complete(text, token)
        except Exception as exc:
            if token != self._load_token:
                return
            self._schedule_load_error(f'# 文件读取失败: {exc}', token)

    def _schedule_load_complete(self, text: str, token: int) -> None:
        if not hasattr(self, 'page') or self.page is None:
            self._apply_loaded_content(text, token)
            return
        try:
            self.page.run_task(self._async_apply_loaded_content, text, token)
        except Exception:
            self._apply_loaded_content(text, token)

    def _schedule_load_error(self, message: str, token: int) -> None:
        if not hasattr(self, 'page') or self.page is None:
            self._apply_load_error(message, token)
            return
        try:
            self.page.run_task(self._async_apply_load_error, message, token)
        except Exception:
            self._apply_load_error(message, token)

    async def _async_apply_loaded_content(self, text: str, token: int) -> None:
        self._apply_loaded_content(text, token)

    async def _async_apply_load_error(self, message: str, token: int) -> None:
        self._apply_load_error(message, token)

    def _apply_loaded_content(self, text: str, token: int) -> None:
        if token != self._load_token:
            return
        self._lines = text.splitlines()
        self._editor.value = text
        self._refresh_component()

    def _apply_load_error(self, message: str, token: int) -> None:
        if token != self._load_token:
            return
        self._editor.value = message
        self._refresh_component()

    def _refresh_component(self) -> None:
        try:
            self.update()
        except Exception:
            pass
        if hasattr(self, 'page') and self.page is not None:
            try:
                self.page.schedule_update()
            except Exception:
                pass

    def _on_text_change(self, e) -> None:
        if self._editor.value:
            self._lines = self._editor.value.splitlines()

    def _on_save(self, _e) -> None:
        if self._path is None:
            return
        try:
            content = self._editor.value or ''
            with self._file_lock:
                self._path.write_text(content, encoding='utf-8')
            self._state.append_log(f'已保存: {self._path.name}')
        except Exception as exc:
            self._state.append_log(f'保存失败: {exc}')

    # ── 导出 PDF ─────────────────────────────────────────────────────────────

    def _on_export_pdf(self, _e) -> None:
        if self._path is None:
            self._state.append_log('导出失败：请先加载简谱文件')
            return
        # 先保存最新内容
        try:
            content = self._editor.value or ''
            with self._file_lock:
                self._path.write_text(content, encoding='utf-8')
        except Exception as exc:
            self._state.append_log(f'保存失败（导出前）: {exc}')
            return
        threading.Thread(target=self._export_pdf_thread, daemon=True).start()

    def _export_pdf_thread(self) -> None:
        try:
            from core.render.lilypond_runner import render_jianpu_ly, render_lilypond_pdf
            txt_path = self._path
            ly_path  = txt_path.with_suffix('.ly')
            self._state.append_log(f'正在生成 LilyPond 中间文件: {ly_path.name}')
            with self._file_lock:
                ok = render_jianpu_ly(txt_path, ly_path)
            if not ok:
                self._state.append_log('jianpu-ly 转换失败，请确认 LilyPond 与 jianpu-ly.py 已安装')
                return
            self._state.append_log(f'正在渲染 PDF…')
            pdf_path = render_lilypond_pdf(ly_path)
            if pdf_path and pdf_path.exists():
                self._state.append_log(f'PDF 已生成: {pdf_path}')
            else:
                self._state.append_log('PDF 渲染失败，请检查 LilyPond 安装')
        except Exception as exc:
            self._state.append_log(f'导出 PDF 出错: {exc}')

    def _on_external_select(self, line_no: int, **_kw) -> None:
        """由图像区触发：当前不再显示行号，因此无需处理。"""
        pass

    def _on_selection_change(self, e) -> None:
        # TextField 的光标变化仍可用于未来扩展，但当前仅保留编辑行为。
        return

    def _request_page_refresh(self) -> None:
        if not hasattr(self, 'page') or self.page is None:
            return
        try:
            self.page.run_task(self._async_refresh)
        except Exception:
            try:
                self.page.schedule_update()
            except Exception:
                pass

    async def _async_refresh(self) -> None:
        try:
            self.update()
        except Exception:
            pass

    def reset(self) -> None:
        self._path = None
        self._lines = []
        self._editor.value = ''
        self._request_page_refresh()

    # ── 对外 API ─────────────────────────────────────────────────────────────

    def scroll_to_line(self, line_no: int) -> None:
        """滚动编辑区到指定行（近似，Flet TextField 不支持精确滚动）。"""
        # 当前编辑器不包含独立行号视图，仅保留方法接口。
        return

    @property
    def text(self) -> str:
        return self._editor.value or ''
