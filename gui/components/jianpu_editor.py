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
        self._selected_line: int = -1
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
            style=ft.ButtonStyle(color=Palette.TEXT_SECONDARY),
            tooltip='将当前简谱文件通过 LilyPond 渲染为 PDF',
        )

        toolbar = ft.Container(
            content=ft.Row(
                [self._title, ft.Row([export_btn, save_btn], spacing=4)],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            bgcolor=Palette.BG_SURFACE,
            border=ft.Border.only(bottom=ft.BorderSide(1, Palette.DIVIDER_DARK)),
        )

        # 主编辑区：用 TextField（多行）支持直接编辑
        self._editor = ft.TextField(
            multiline=True,
            expand=True,
            text_size=13,
            text_style=ft.TextStyle(font_family='Consolas', font_family_fallback='YaHei'),
            bgcolor=Palette.BG_INPUT,
            color=Palette.TEXT_PRIMARY,
            border_color='transparent',
            focused_border_color=Palette.PRIMARY,
            cursor_color=Palette.PRIMARY,
            on_change=self._on_text_change,
            hint_text='尚未加载简谱文件…',
            hint_style=ft.TextStyle(color=Palette.TEXT_DISABLED),
        )

        # 行号侧边（只读文字，与编辑区同步）
        self._line_numbers = ft.Column(
            spacing=0,
            width=40,
            scroll=ft.ScrollMode.HIDDEN,
        )

        editor_row = ft.Row(
            [
                ft.Container(
                    content=self._line_numbers,
                    bgcolor=Palette.BG_SURFACE,
                    padding=ft.Padding.only(right=4, top=4),
                    border=ft.Border.only(right=ft.BorderSide(1, Palette.DIVIDER_DARK)),
                ),
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
        threading.Thread(target=self._load_async, args=(path,), daemon=True).start()

    def _load_async(self, path: Path) -> None:
        try:
            text = path.read_text(encoding='utf-8-sig', errors='replace')
            self._lines = text.splitlines()
            self._editor.value = text
            self._refresh_line_numbers()
            try:
                self._editor.update()
            except Exception:
                pass
        except Exception as exc:
            self._editor.value = f'# 文件读取失败: {exc}'
            try:
                self._editor.update()
            except Exception:
                pass

    def _on_text_change(self, e) -> None:
        if self._editor.value:
            self._lines = self._editor.value.splitlines()
            self._refresh_line_numbers()

    def _on_save(self, _e) -> None:
        if self._path is None:
            return
        try:
            content = self._editor.value or ''
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
            self._path.write_text(content, encoding='utf-8')
        except Exception as exc:
            self._state.append_log(f'保存失败（导出前）: {exc}')
            return
        threading.Thread(target=self._export_pdf_thread, daemon=True).start()

    def _export_pdf_thread(self) -> None:
        try:
            from core.runtime_finder import render_jianpu_ly, render_lilypond_pdf
            txt_path = self._path
            ly_path  = txt_path.with_suffix('.ly')
            self._state.append_log(f'正在生成 LilyPond 中间文件: {ly_path.name}')
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

    # ── 行号刷新 ─────────────────────────────────────────────────────────────

    def _refresh_line_numbers(self) -> None:
        n = max(len(self._lines), 1)
        self._line_numbers.controls = [
            ft.Container(
                content=ft.Text(
                    str(i + 1),
                    size=12,
                    font_family='Consolas',
                    font_family_fallback='YaHei',
                    color=Palette.PRIMARY if i == self._selected_line else Palette.TEXT_DISABLED,
                    text_align=ft.TextAlign.RIGHT,
                ),
                height=20,
                alignment=ft.Alignment(1, 0),
                bgcolor=Palette.HIGHLIGHT if i == self._selected_line else 'transparent',
                on_click=lambda _e, li=i: self._on_line_click(li),
            )
            for i in range(n)
        ]
        try:
            self._line_numbers.update()
        except Exception:
            pass

    def _on_line_click(self, line_no: int) -> None:
        self._selected_line = line_no
        self._refresh_line_numbers()
        self._state.emit(Event.JIANPU_TXT_SELECTED, line_no=line_no)

    def _on_external_select(self, line_no: int, **_kw) -> None:
        """由图像区触发：高亮对应行。"""
        self._selected_line = line_no
        self._refresh_line_numbers()

    # ── 对外 API ─────────────────────────────────────────────────────────────

    def scroll_to_line(self, line_no: int) -> None:
        """滚动编辑区到指定行（近似，Flet TextField 不支持精确滚动）。"""
        self._selected_line = line_no
        self._refresh_line_numbers()

    @property
    def text(self) -> str:
        return self._editor.value or ''
