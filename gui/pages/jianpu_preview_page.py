# gui/pages/jianpu_preview_page.py — 简谱预览页
# 扫描 Output/*.pdf，左侧文件列表（复选框 + 编辑按钮），右侧 PdfViewer，底部导出功能。

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Optional

import flet as ft

from ..app_state import AppState, Event
from core.app.backend import output_dir
from ..components.pdf_viewer import PdfViewer
from ..theme import Palette, with_alpha


class JianpuPreviewPage(ft.Row):
    """简谱预览页：扫描 Output/*.pdf，支持预览、勾选导出、跳转编辑。"""

    def __init__(self, state: AppState):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._pdf_paths: list[Path] = []
        self._checked: set[Path] = set()
        self._current_path: Optional[Path] = None
        self._item_rows: dict[Path, ft.Container] = {}
        self._has_been_shown = False
        self._build_ui()

    # ── UI 构建 ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._viewer = PdfViewer()

        self._file_list_col = ft.Column(
            spacing=2,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        self._empty_hint = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.MUSIC_NOTE_OUTLINED, size=40, color=ft.Colors.OUTLINE),
                    ft.Text(
                        'Output 目录暂无简谱文件\n请先在乐谱识别页完成转换',
                        size=12,
                        color=ft.Colors.OUTLINE,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
            ),
            expand=True,
            alignment=ft.Alignment(0, 0),
        )

        self._select_all_btn = ft.IconButton(
            icon=ft.Icons.CHECK_BOX_OUTLINE_BLANK,
            icon_size=17,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip='全选 / 全不选',
            on_click=self._on_select_all,
            width=28,
            height=28,
        )

        refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_size=17,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip='刷新列表',
            on_click=lambda _: self.reload(),
            width=28,
            height=28,
        )

        sidebar_header = ft.Container(
            content=ft.Row(
                [
                    ft.Row(
                        [
                            self._select_all_btn,
                            ft.Text(
                                '简谱文件',
                                size=12,
                                weight=ft.FontWeight.W_700,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                        spacing=2,
                    ),
                    ft.Container(expand=True),
                    refresh_btn,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=ft.Colors.SURFACE,
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        self._export_picker = ft.FilePicker()
        self._export_picker.on_result = self._on_export_result

        export_btn = ft.ElevatedButton(
            content=ft.Row(
                [ft.Icon(ft.Icons.DOWNLOAD_ROUNDED, size=16), ft.Text('导出简谱', size=13)],
                tight=True,
                spacing=6,
            ),
            style=ft.ButtonStyle(
                color=ft.Colors.ON_PRIMARY,
                bgcolor=Palette.PRIMARY,
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            ),
            on_click=self._on_export_click,
        )

        sidebar_footer = ft.Container(
            content=export_btn,
            padding=ft.Padding.all(8),
            border=ft.Border.only(top=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        self._list_stack = ft.Stack(
            [
                ft.Container(content=self._file_list_col, expand=True),
                self._empty_hint,
            ],
            expand=True,
        )

        sidebar = ft.Container(
            content=ft.Column(
                [sidebar_header, self._list_stack, sidebar_footer],
                spacing=0,
                expand=True,
            ),
            width=220,
            bgcolor=ft.Colors.SURFACE,
            border=ft.Border.only(right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        top_bar = ft.Container(
            content=ft.Row(
                [
                    ft.Text(
                        '简谱预览',
                        size=13,
                        weight=ft.FontWeight.W_700,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=ft.Colors.SURFACE,
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        right_area = ft.Column(
            [top_bar, ft.Container(content=self._viewer, expand=True)],
            spacing=0,
            expand=True,
        )

        self.controls = [sidebar, right_area]
        self.expand = True
        self.vertical_alignment = ft.CrossAxisAlignment.STRETCH

    def did_mount(self) -> None:
        self.page._services.register_service(self._export_picker)  # type: ignore[attr-defined]

    def will_unmount(self) -> None:
        self._viewer.will_unmount()

    # ── 文件列表管理 ──────────────────────────────────────────────────────────

    def reload(self) -> None:
        """扫描 Output/ 目录，刷新文件列表。"""
        out = output_dir(None)
        paths = sorted(out.glob('*.pdf'), key=lambda p: p.stat().st_mtime, reverse=True) if out.exists() else []
        self._pdf_paths = paths
        self._checked = self._checked.intersection(set(paths))
        self._rebuild_list()
        if paths and (self._current_path is None or self._current_path not in paths):
            self._select_file(paths[0])
        elif not paths:
            self._viewer.reset()
            self._current_path = None
        self._update_select_all_icon()

    def _rebuild_list(self) -> None:
        self._item_rows.clear()
        self._file_list_col.controls.clear()
        for path in self._pdf_paths:
            row = self._make_item_row(path)
            self._item_rows[path] = row
            self._file_list_col.controls.append(row)
        self._empty_hint.visible = not self._pdf_paths
        try:
            self._list_stack.update()
        except Exception:
            pass

    def _make_item_row(self, path: Path) -> ft.Container:
        is_checked = path in self._checked
        is_selected = path == self._current_path

        chk = ft.Checkbox(
            value=is_checked,
            on_change=lambda e, p=path: self._on_check_change(e, p),
        )

        edit_btn = ft.IconButton(
            icon=ft.Icons.EDIT_OUTLINED,
            icon_size=15,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip='编辑简谱',
            on_click=lambda _, p=path: self._on_edit_click(p),
            width=28,
            height=28,
        )

        display_name = path.stem[: -len('_jianpu')] if path.stem.endswith('_jianpu') else path.stem
        name_text = ft.Text(
            display_name,
            size=12,
            overflow=ft.TextOverflow.ELLIPSIS,
            expand=True,
            color=Palette.PRIMARY if is_selected else ft.Colors.ON_SURFACE,
            weight=ft.FontWeight.W_700 if is_selected else ft.FontWeight.NORMAL,
        )

        return ft.Container(
            content=ft.Row(
                [chk, name_text, edit_btn],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=4, vertical=2),
            border_radius=ft.BorderRadius.all(4),
            bgcolor=with_alpha(Palette.PRIMARY, '22') if is_selected else ft.Colors.TRANSPARENT,
            on_click=lambda _, p=path: self._select_file(p),
        )

    def _select_file(self, path: Path) -> None:
        prev = self._current_path
        self._current_path = path
        self._viewer.load(path)
        if prev and prev in self._item_rows:
            self._refresh_item(prev)
        if path in self._item_rows:
            self._refresh_item(path)

    def _refresh_item(self, path: Path) -> None:
        if path not in self._item_rows:
            return
        old_row = self._item_rows[path]
        new_row = self._make_item_row(path)
        self._item_rows[path] = new_row
        idx = self._file_list_col.controls.index(old_row)
        self._file_list_col.controls[idx] = new_row
        try:
            self._file_list_col.update()
        except Exception:
            pass

    # ── 复选框逻辑 ────────────────────────────────────────────────────────────

    def _on_check_change(self, e, path: Path) -> None:
        if e.control.value:
            self._checked.add(path)
        else:
            self._checked.discard(path)
        self._update_select_all_icon()

    def _on_select_all(self, _e) -> None:
        if len(self._checked) < len(self._pdf_paths):
            self._checked = set(self._pdf_paths)
            for row in self._item_rows.values():
                row.content.controls[0].value = True  # type: ignore[union-attr]
            self._select_all_btn.icon = ft.Icons.CHECK_BOX_ROUNDED
        else:
            self._checked.clear()
            for row in self._item_rows.values():
                row.content.controls[0].value = False  # type: ignore[union-attr]
            self._select_all_btn.icon = ft.Icons.CHECK_BOX_OUTLINE_BLANK
        try:
            self._file_list_col.update()
            self._select_all_btn.update()
        except Exception:
            pass

    def _update_select_all_icon(self) -> None:
        if not self._pdf_paths:
            icon = ft.Icons.CHECK_BOX_OUTLINE_BLANK
        elif len(self._checked) == len(self._pdf_paths):
            icon = ft.Icons.CHECK_BOX_ROUNDED
        elif self._checked:
            icon = ft.Icons.INDETERMINATE_CHECK_BOX_ROUNDED
        else:
            icon = ft.Icons.CHECK_BOX_OUTLINE_BLANK
        self._select_all_btn.icon = icon
        try:
            self._select_all_btn.update()
        except Exception:
            pass

    # ── 编辑跳转 ──────────────────────────────────────────────────────────────

    def _on_edit_click(self, path: Path) -> None:
        self._state.jianpu_edit_source = path
        self._state.emit(Event.JIANPU_EDIT_REQUESTED, path=path)

    # ── 导出 ──────────────────────────────────────────────────────────────────

    def _on_export_click(self, _e) -> None:
        if not self._checked:
            return
        self.page.run_task(self._pick_export_dir_async)  # type: ignore[attr-defined]

    async def _pick_export_dir_async(self) -> None:
        await self._export_picker.get_directory_path(dialog_title='选择导出目标目录')

    def _on_export_result(self, e: ft.FilePickerResultEvent) -> None:
        if not e.path:
            return
        dest = Path(e.path) / 'jianpu_output'
        dest.mkdir(parents=True, exist_ok=True)
        exported, failed = 0, []
        for pdf in list(self._checked):
            try:
                shutil.copy2(pdf, dest / pdf.name)
                exported += 1
            except Exception as exc:
                failed.append(f'{pdf.name}: {exc}')
        if failed:
            msg = f'导出完成：{exported} 个成功，{len(failed)} 个失败'
        else:
            msg = f'已导出 {exported} 个简谱至 {dest}'
        self._state.emit(Event.PROGRESS_DONE, message=msg)
