# gui/components/file_sidebar.py — 文件钉选侧边栏
# 左侧边栏：显示已加载的文件列表，支持多选添加和移除。

from __future__ import annotations

import asyncio
import shutil
import threading
import flet as ft
from pathlib import Path
from typing import Optional

from ..app_state import AppState, Event
from core.app.backend import app_base_dir
from ..theme import Palette, with_alpha, section_title


class FileSidebar(ft.Column):
    """左侧文件钉选栏。

    用法::

        sidebar = FileSidebar(state)
        page.add(sidebar)
    """

    def __init__(self, state: AppState):
        super().__init__(
            spacing=0,
            width=220,
            expand=False,
        )
        self._state = state
        self._file_list_col: ft.Column = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)
        self._build_ui()
        state.on(Event.FILES_CHANGED,       self._on_files_changed)
        state.on(Event.FILE_SELECTED,       self._on_file_selected)
        state.on(Event.FILES_CHECK_CHANGED, self._on_check_changed)

    # ── 构建静态 UI ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # 全选/全不选 按钮（图标随状态变化）
        self._select_all_btn = ft.IconButton(
            icon=ft.Icons.CHECK_BOX_OUTLINE_BLANK,
            icon_size=17,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip='全选 / 全不选',
            on_click=self._on_select_all_click,
            width=28,
            height=28,
        )

        title_row = ft.Row(
            [
                ft.Row(
                    [
                        self._select_all_btn,
                        section_title('文件列表', self._state.dark_mode),
                    ],
                    spacing=0,
                ),
                ft.Row(
                    [
                        ft.IconButton(
                            icon=ft.Icons.CREATE_NEW_FOLDER_ROUNDED,
                            icon_size=18,
                            icon_color=ft.Colors.ON_SURFACE_VARIANT,
                            tooltip='添加文件夹',
                            on_click=self._on_add_folder_click,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.ADD_ROUNDED,
                            icon_size=18,
                            icon_color=Palette.PRIMARY,
                            tooltip='添加文件',
                            on_click=self._on_add_click,
                        ),
                    ],
                    spacing=0,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # FilePicker 在 Flet 0.84 是 Service，在 did_mount 时注册到 page.services
        self._file_picker   = ft.FilePicker()
        self._folder_picker = ft.FilePicker()

        container = ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=title_row,
                        height=48,
                        padding=ft.Padding.only(left=12, right=4),
                        alignment=ft.Alignment(-1, 0),
                        border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
                    ),
                    ft.Container(
                        content=self._file_list_col,
                        expand=True,
                        padding=ft.Padding.symmetric(horizontal=6, vertical=4),
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            bgcolor=ft.Colors.SURFACE,
            expand=True,
            border=ft.Border.only(right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )
        self.controls = [container]
        self.expand = True

    def did_mount(self):
        """在页面挂载后将 FilePicker 注册到 ServiceRegistry。"""
        self.page._services.register_service(self._file_picker)  # type: ignore[attr-defined]
        self.page._services.register_service(self._folder_picker)  # type: ignore[attr-defined]

    # ── 文件行渲染 ───────────────────────────────────────────────────────────

    def _make_file_row(self, path: Path) -> ft.Container:
        is_selected = (path == self._state.current_file)
        is_checked  = path in self._state.checked_files
        bg = with_alpha(Palette.PRIMARY, '33') if is_selected else 'transparent'
        icon = ft.Icons.PICTURE_AS_PDF if path.suffix.lower() == '.pdf' else ft.Icons.IMAGE_OUTLINED

        def _on_click(_e, p=path):
            self._state.select_file(p)

        def _on_remove(_e, p=path):
            input_dir = app_base_dir() / 'Input'
            p_resolved = p.resolve()
            in_input = p_resolved.parent == input_dir.resolve()

            if not in_input:
                # File is not in Input/ — just remove from list
                self._state.remove_file(p)
                return

            def _do_delete(_ev):
                self.page.pop_dialog()
                try:
                    p_resolved.unlink(missing_ok=True)
                except Exception:
                    pass
                self._state.remove_file(p)

            def _cancel(_ev):
                self.page.pop_dialog()

            _confirm_dlg = ft.AlertDialog(
                modal=True,
                title=ft.Text('删除文件', size=15, weight=ft.FontWeight.W_600),
                content=ft.Text(
                    f'将从 Input 文件夹中永久删除：\n{p.name}',
                    size=13,
                    color=ft.Colors.ON_SURFACE,
                ),
                actions=[
                    ft.TextButton('取消', on_click=_cancel),
                    ft.FilledButton('删除', on_click=_do_delete),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            self.page.show_dialog(_confirm_dlg)

        def _on_check_change(_e, p=path):
            self._state.toggle_check(p)

        return ft.Container(
            content=ft.Row(
                [
                    ft.Checkbox(
                        value=is_checked,
                        on_change=_on_check_change,
                        active_color=Palette.PRIMARY,
                        width=28,
                        height=28,
                    ),
                    ft.Icon(icon, size=14, color=ft.Colors.SECONDARY),
                    ft.Text(
                        path.name,
                        size=13,
                        color=ft.Colors.ON_SURFACE,
                        expand=True,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        tooltip=str(path),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE_ROUNDED,
                        icon_size=12,
                        icon_color=ft.Colors.OUTLINE,
                        tooltip='从 Input 文件夹删除',
                        on_click=_on_remove,
                        width=24,
                        height=24,
                    ),
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=bg,
            border_radius=ft.BorderRadius.all(6),
            padding=ft.Padding.only(left=2, right=4, top=3, bottom=3),
            on_click=_on_click,
            ink=True,
        )

    def _update_select_all_icon(self) -> None:
        """根据勾选状态更新全选按钮图标。"""
        n_pinned  = len(self._state.pinned_files)
        n_checked = len(self._state.checked_files)
        if n_pinned == 0 or n_checked == 0:
            self._select_all_btn.icon       = ft.Icons.CHECK_BOX_OUTLINE_BLANK
            self._select_all_btn.icon_color = ft.Colors.OUTLINE
        elif n_checked >= n_pinned:
            self._select_all_btn.icon       = ft.Icons.CHECK_BOX
            self._select_all_btn.icon_color = Palette.PRIMARY
        else:
            self._select_all_btn.icon       = ft.Icons.INDETERMINATE_CHECK_BOX
            self._select_all_btn.icon_color = Palette.PRIMARY

    # ── 事件处理 ─────────────────────────────────────────────────────────────

    def _on_add_click(self, _e) -> None:
        self.page.run_task(self._pick_files_async)  # type: ignore[attr-defined]

    def _on_add_folder_click(self, _e) -> None:
        self.page.run_task(self._pick_folder_async)  # type: ignore[attr-defined]

    async def _pick_files_async(self) -> None:
        input_dir = app_base_dir() / 'Input'
        init_dir = str(input_dir) if input_dir.exists() else None
        files = await self._file_picker.pick_files(
            dialog_title='选择乐谱文件',
            allowed_extensions=['pdf', 'png', 'jpg', 'jpeg'],
            allow_multiple=True,
            initial_directory=init_dir,
        )
        if not files:
            return
        paths = [Path(f.path) for f in files if f.path]
        await self._import_with_progress(paths)

    async def _pick_folder_async(self) -> None:
        input_dir = app_base_dir() / 'Input'
        init_dir = str(input_dir) if input_dir.exists() else None
        folder_path = await self._folder_picker.get_directory_path(
            dialog_title='选择包含乐谱文件的文件夹',
            initial_directory=init_dir,
        )
        if not folder_path:
            return
        folder = Path(folder_path)
        paths: list[Path] = []
        for ext in ('*.pdf', '*.png', '*.jpg', '*.jpeg'):
            paths.extend(folder.glob(ext))
        if paths:
            await self._import_with_progress(paths)

    async def _import_with_progress(self, source_paths: list[Path]) -> None:
        """Copy source_paths into Input/, showing a progress dialog.

        Files already inside Input/ are registered directly without copying.
        Name conflicts are resolved by appending _1, _2, … to the stem.
        """
        if not source_paths:
            return

        input_dir = app_base_dir() / 'Input'
        await asyncio.to_thread(input_dir.mkdir, parents=True, exist_ok=True)

        n = len(source_paths)

        # ── Progress dialog ──────────────────────────────────────────────────
        _prog_text = ft.Text('准备中…', size=13, color=ft.Colors.ON_SURFACE)
        _prog_bar  = ft.ProgressBar(
            value=0,
            width=340,
            color=Palette.PRIMARY,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border_radius=ft.BorderRadius.all(4),
        )
        _dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f'导入 {n} 个文件', size=15, weight=ft.FontWeight.W_600),
            content=ft.Column(
                [_prog_text, _prog_bar],
                spacing=14,
                tight=True,
                width=360,
            ),
        )
        self.page.show_dialog(_dlg)
        await asyncio.sleep(0)  # yield so the dialog has a chance to render

        # ── Copy loop ────────────────────────────────────────────────────────
        copied: list[Path] = []
        for i, src in enumerate(source_paths):
            _prog_text.value = f'({i + 1}/{n})  {src.name}'
            _prog_bar.value  = i / n
            self.page.update()  # push progress delta before yielding to copy thread

            # Files already inside Input/ need no copy
            try:
                src_resolved = src.resolve()
                in_input_dir = src_resolved.parent == input_dir.resolve()
            except Exception:
                in_input_dir = False

            if in_input_dir:
                copied.append(src_resolved)
                continue

            dest = input_dir / src.name
            if dest.exists():
                stem, suffix = src.stem, src.suffix
                j = 1
                while dest.exists():
                    dest = input_dir / f'{stem}_{j}{suffix}'
                    j += 1

            try:
                await asyncio.to_thread(shutil.copy2, str(src), str(dest))
                copied.append(dest)
            except Exception:
                copied.append(src)  # fallback: keep original path

        # ── Registration phase (batch, single UI update) ─────────────────────
        _prog_text.value = f'正在整理 {len(copied)} 个文件…'
        _prog_bar.value  = 1.0
        self.page.update()
        await asyncio.sleep(0)  # let the "整理中" state render

        _supported = {'.pdf', '.png', '.jpg', '.jpeg'}
        for path in copied:
            resolved = path.resolve()
            if resolved.suffix.lower() in _supported and resolved not in self._state.pinned_files:
                self._state.pinned_files.append(resolved)
                self._state.checked_files.add(resolved)

        self.page.pop_dialog()

        # Single emit – triggers exactly one list rebuild instead of N
        self._state.emit(Event.FILES_CHANGED, files=list(self._state.pinned_files))
        if copied:
            self._state.emit(Event.FILES_IMPORTED, paths=copied)

    def _on_files_changed(self, files, **_kw) -> None:
        self._refresh_list()

    def _on_file_selected(self, path, **_kw) -> None:
        self._refresh_list()

    def _on_check_changed(self, **_kw) -> None:
        self._refresh_list()

    def _on_select_all_click(self, _e) -> None:
        n_pinned  = len(self._state.pinned_files)
        n_checked = len(self._state.checked_files)
        if n_checked < n_pinned:
            self._state.check_all()
        else:
            self._state.uncheck_all()

    def _refresh_list(self) -> None:
        # 在调用线程中先快照，避免竞态
        pinned = list(self._state.pinned_files)
        p = self.page
        if p is not None:
            async def _do():
                # 所有 Flet 控件写操作必须在事件循环线程中执行，
                # 否则脏标记不会被触发，update() 不会发现变化。
                try:
                    self._file_list_col.controls = [
                        self._make_file_row(fp) for fp in pinned
                    ]
                    self._update_select_all_icon()
                    self._file_list_col.update()
                    self._select_all_btn.update()
                except Exception:
                    pass
            p.run_task(_do)  # type: ignore[attr-defined]
        else:
            # 尚未挂载；先写入数据，did_mount 后会再次触发刷新
            self._file_list_col.controls = [
                self._make_file_row(fp) for fp in pinned
            ]
            self._update_select_all_icon()

