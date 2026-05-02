# gui/pages/score_preview_page.py — 五线谱预览页
# 双列布局：左侧乐谱文件列表，右侧五线谱 PDF 预览。

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Optional

import flet as ft

from ..app_state import AppState, Event
from core.app.backend import xml_scores_dir, build_dir
from ..components.pdf_viewer import PdfViewer
from ..theme import Palette, with_alpha, section_title


class ScorePreviewPage(ft.Row):
    """五线谱预览页：扫描 xml-scores/ 目录，支持五线谱预览、导出和跳转移调。"""

    def __init__(self, state: AppState):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._mxl_paths: list[Path] = []
        self._checked: set[Path] = set()
        self._current_path: Optional[Path] = None
        self._item_rows: dict[Path, ft.Container] = {}
        self._render_token: int = 0
        self._export_token: int = 0
        self._preview_pdf_cache: dict[Path, Path] = {}  # mxl_path → rendered pdf_path
        self._build_ui()
        state.on(Event.MXL_READY, self._on_mxl_ready)

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
                    ft.Icon(ft.Icons.LIBRARY_MUSIC_OUTLINED, size=40, color=ft.Colors.OUTLINE),
                    ft.Text(
                        '请先识别乐谱文件\n或点击刷新按钮',
                        size=13,
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

        refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_size=17,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip='刷新列表',
            on_click=lambda _: self.reload(),
            width=28,
            height=28,
        )

        self._export_picker = ft.FilePicker()
        self._batch_export_picker = ft.FilePicker()

        self._select_all_btn = ft.IconButton(
            icon=ft.Icons.CHECK_BOX_OUTLINE_BLANK,
            icon_size=17,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip='全选 / 全不选',
            on_click=self._on_select_all,
            width=28,
            height=28,
        )

        batch_export_btn = ft.IconButton(
            icon=ft.Icons.DOWNLOAD_ROUNDED,
            icon_size=17,
            icon_color=Palette.PRIMARY,
            tooltip='导出已勾选的五线谱 PDF',
            on_click=self._on_batch_export_click,
            width=28,
            height=28,
        )

        sidebar_header = ft.Container(
            content=ft.Row(
                [
                    ft.Row([self._select_all_btn, section_title('五线谱文件')], spacing=0),
                    ft.Container(expand=True),
                    batch_export_btn,
                    refresh_btn,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            height=48,
            padding=ft.Padding.only(left=4, right=4),
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
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
                [
                    sidebar_header,
                    ft.Container(
                        content=self._list_stack,
                        expand=True,
                        padding=ft.Padding.symmetric(horizontal=6, vertical=4),
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            expand=1,
            bgcolor=ft.Colors.SURFACE,
            border=ft.Border.only(right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        export_btn = ft.OutlinedButton(
            content=ft.Row(
                [ft.Icon(ft.Icons.DOWNLOAD_ROUNDED, size=15), ft.Text('导出乐谱')],
                tight=True, spacing=5,
            ),
            on_click=self._on_export_click,
            style=ft.ButtonStyle(
                color=Palette.PRIMARY,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, Palette.PRIMARY)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        transpose_btn = ft.OutlinedButton(
            content=ft.Row(
                [ft.Icon(ft.Icons.COMPARE_ARROWS_ROUNDED, size=15), ft.Text('乐谱移调')],
                tight=True, spacing=5,
            ),
            on_click=self._on_transpose_click,
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        top_bar = ft.Container(
            content=ft.Row(
                [
                    ft.Container(expand=True),
                    export_btn,
                    transpose_btn,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=ft.Colors.SURFACE,
            height=56,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        self._status = ft.Text('', size=13, color=ft.Colors.ON_SURFACE_VARIANT)
        self._progress = ft.ProgressBar(
            value=0, visible=False,
            bgcolor=ft.Colors.SURFACE_CONTAINER, color=Palette.PRIMARY, height=3,
        )

        status_bar = ft.Container(
            content=ft.Column(
                [
                    ft.Container(content=self._progress, height=3, width=320),
                    ft.Row([self._status], alignment=ft.MainAxisAlignment.START),
                ],
                spacing=4,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            bgcolor=ft.Colors.SURFACE,
            border=ft.Border.only(top=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        right_area = ft.Column(
            [
                top_bar,
                ft.Container(content=self._viewer, expand=True),
                status_bar,
            ],
            spacing=0,
            expand=True,
        )

        self.controls = [sidebar, right_area]
        self.expand = True
        self.vertical_alignment = ft.CrossAxisAlignment.STRETCH

    def did_mount(self) -> None:
        self.page._services.register_service(self._export_picker)  # type: ignore[attr-defined]
        self.page._services.register_service(self._batch_export_picker)  # type: ignore[attr-defined]

    def will_unmount(self) -> None:
        self._viewer.will_unmount()

    # ── 文件列表管理 ──────────────────────────────────────────────────────────

    def reload(self) -> None:
        """扫描 xml-scores/ 目录，刷新文件列表。"""
        xml_dir = xml_scores_dir()
        paths: list[Path] = []
        if xml_dir.exists():
            for ext in ('*.mxl', '*.xml', '*.musicxml'):
                paths.extend(xml_dir.glob(ext))
        paths = sorted(set(paths), key=lambda p: p.stat().st_mtime, reverse=True)
        self._mxl_paths = paths
        self._checked = self._checked.intersection(set(paths))
        self._rebuild_list()
        if paths and (self._current_path is None or self._current_path not in paths):
            self._select_file(paths[0])
        elif not paths:
            self._viewer.reset()
            self._current_path = None
            self._render_token += 1
        self._update_select_all_icon()

    def _rebuild_list(self) -> None:
        self._item_rows.clear()
        self._file_list_col.controls.clear()
        for path in self._mxl_paths:
            row = self._make_item_row(path)
            self._item_rows[path] = row
            self._file_list_col.controls.append(row)
        self._empty_hint.visible = not self._mxl_paths
        try:
            self._list_stack.update()
        except Exception:
            pass

    def _make_item_row(self, path: Path) -> ft.Container:
        is_selected = path == self._current_path
        is_checked = path in self._checked

        chk = ft.Checkbox(
            value=is_checked,
            on_change=lambda e, p=path: self._on_check_change(e, p),
            active_color=Palette.PRIMARY,
            width=28,
            height=28,
        )

        return ft.Container(
            content=ft.Row(
                [
                    chk,
                    ft.Icon(ft.Icons.QUEUE_MUSIC_ROUNDED, size=14, color=ft.Colors.SECONDARY),
                    ft.Text(
                        path.stem,
                        size=13,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        expand=True,
                        color=Palette.PRIMARY if is_selected else ft.Colors.ON_SURFACE,
                        weight=ft.FontWeight.W_700 if is_selected else ft.FontWeight.NORMAL,
                        tooltip=path.name,
                    ),
                    ft.Text(
                        path.suffix.lstrip('.').upper(),
                        size=11,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=with_alpha(Palette.PRIMARY, '33') if is_selected else ft.Colors.TRANSPARENT,
            border_radius=ft.BorderRadius.all(6),
            padding=ft.Padding.only(left=2, right=4, top=3, bottom=3),
            on_click=lambda _, p=path: self._select_file(p),
            ink=True,
        )

    def _select_file(self, path: Path) -> None:
        prev = self._current_path
        self._current_path = path
        if prev and prev in self._item_rows:
            self._refresh_item(prev)
        if path in self._item_rows:
            self._refresh_item(path)
        self._render_preview(path)

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
        if len(self._checked) < len(self._mxl_paths):
            self._checked = set(self._mxl_paths)
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
        if not self._mxl_paths:
            icon = ft.Icons.CHECK_BOX_OUTLINE_BLANK
        elif len(self._checked) == len(self._mxl_paths):
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

    # ── 预览渲染 ──────────────────────────────────────────────────────────────

    def _render_preview(self, path: Path) -> None:
        self._render_token += 1
        current_token = self._render_token
        self._set_status(f'渲染中: {path.name}...')
        self._set_busy(True)
        threading.Thread(
            target=self._render_async, args=(current_token, path), daemon=True
        ).start()

    def _render_async(self, token: int, path: Path) -> None:
        if token != self._render_token:
            return
        try:
            from core.render.lilypond_runner import render_musicxml_staff_pdf
            tmp = build_dir() / f'_score_preview_{path.stem}'
            tmp.mkdir(exist_ok=True)
            pdf = render_musicxml_staff_pdf(path, tmp)
            if token != self._render_token:
                return
            if pdf and pdf.exists():
                self._viewer.load(pdf)
                self._preview_pdf_cache[path] = pdf
                self._set_status(f'已加载: {path.name}')
            else:
                self._set_status('预览渲染失败：LilyPond 不可用或文件有误')
        except Exception as exc:
            if token != self._render_token:
                return
            self._set_status(f'渲染失败: {exc}')
        finally:
            if token == self._render_token:
                self._set_busy(False)

    # ── MXL_READY 事件 ────────────────────────────────────────────────────────

    def _on_mxl_ready(self, path: Path, **_kw) -> None:
        self.reload()

    # ── 导出乐谱（单个）──────────────────────────────────────────────────────

    def _on_export_click(self, _e) -> None:
        if self._current_path is None:
            self._set_status('请先选择乐谱文件。')
            return
        self.page.run_task(self._export_single_async)  # type: ignore[attr-defined]

    async def _export_single_async(self) -> None:
        assert self._current_path is not None
        mxl = self._current_path
        cached = self._preview_pdf_cache.get(mxl)
        default_name = (cached.name if cached else mxl.stem + '_staff.pdf')
        dest_str = await self._export_picker.save_file(
            dialog_title='导出五线谱 PDF',
            file_name=default_name,
            allowed_extensions=['pdf'],
        )
        if not dest_str:
            return
        self._export_token += 1
        current_token = self._export_token
        threading.Thread(
            target=self._export_async, args=(current_token, Path(dest_str)), daemon=True
        ).start()

    def _export_async(self, token: int, dest_path: Path) -> None:
        """Export current MXL as PDF to dest_path (full file path)."""
        if token != self._export_token:
            return
        self._set_busy(True)
        try:
            assert self._current_path is not None
            cached = self._preview_pdf_cache.get(self._current_path)
            if cached and cached.exists():
                shutil.copy2(str(cached), str(dest_path))
                self._set_status(f'导出完成 → {dest_path}')
                return
            from core.render.lilypond_runner import render_musicxml_staff_pdf
            tmp = build_dir() / f'_score_export_{self._current_path.stem}'
            tmp.mkdir(exist_ok=True)
            pdf = render_musicxml_staff_pdf(self._current_path, tmp)
            if token != self._export_token:
                return
            if pdf and pdf.exists():
                shutil.copy2(str(pdf), str(dest_path))
                self._preview_pdf_cache[self._current_path] = pdf
                self._set_status(f'导出完成 → {dest_path}')
            else:
                self._set_status('导出失败：无法生成五线谱 PDF，请检查 LilyPond 是否可用。')
        except Exception as exc:
            if token != self._export_token:
                return
            self._set_status(f'导出失败: {exc}')
        finally:
            if token == self._export_token:
                self._set_busy(False)

    # ── 批量导出五线谱 ──────────────────────────────────────────────────────────

    def _on_batch_export_click(self, _e) -> None:
        if not self._checked:
            self._set_status('请先勾选要导出的五线谱文件。')
            return
        self.page.run_task(self._batch_export_dir_async)  # type: ignore[attr-defined]

    async def _batch_export_dir_async(self) -> None:
        dest_str = await self._batch_export_picker.get_directory_path(dialog_title='选择导出目标目录')
        if not dest_str:
            return
        files = list(self._checked)
        threading.Thread(
            target=self._batch_export_async, args=(Path(dest_str), files), daemon=True
        ).start()

    def _batch_export_async(self, dest_parent: Path, mxl_files: list[Path]) -> None:
        self._set_busy(True)
        self._set_status(f'批量导出 {len(mxl_files)} 个文件...')
        dest = dest_parent / 'score_output'
        dest.mkdir(parents=True, exist_ok=True)
        exported, failed = 0, []
        for mxl in mxl_files:
            try:
                from core.render.lilypond_runner import render_musicxml_staff_pdf
                cached = self._preview_pdf_cache.get(mxl)
                if cached and cached.exists():
                    shutil.copy2(str(cached), str(dest / cached.name))
                    exported += 1
                    self._set_status(f'导出中... {exported}/{len(mxl_files)}')
                    continue
                tmp = build_dir() / f'_score_export_{mxl.stem}'
                tmp.mkdir(exist_ok=True)
                pdf = render_musicxml_staff_pdf(mxl, tmp)
                if pdf and pdf.exists():
                    shutil.copy2(str(pdf), str(dest / pdf.name))
                    self._preview_pdf_cache[mxl] = pdf
                    exported += 1
                    self._set_status(f'导出中... {exported}/{len(mxl_files)}')
                else:
                    failed.append(mxl.name)
            except Exception as exc:
                failed.append(f'{mxl.name}: {exc}')
        if failed:
            self._set_status(f'导出完成：{exported} 个成功，{len(failed)} 个失败')
        else:
            self._set_status(f'已导出 {exported} 个五线谱 PDF 至 {dest}')
        self._set_busy(False)

    # ── 乐谱移调 ──────────────────────────────────────────────────────────────

    def _on_transpose_click(self, _e) -> None:
        self._state.emit(Event.SCORE_TRANSPOSER_REQUESTED, path=self._current_path)

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        self._status.value = msg
        p = self.page
        if p is not None:
            p.loop.call_soon_threadsafe(p.update, self._status)  # type: ignore[attr-defined]

    def _set_busy(self, busy: bool) -> None:
        self._progress.visible = busy
        self._progress.value = None if busy else 0
        p = self.page
        if p is not None:
            p.loop.call_soon_threadsafe(p.update, self._progress)  # type: ignore[attr-defined]
