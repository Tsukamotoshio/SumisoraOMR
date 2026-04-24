# gui/components/file_sidebar.py — 文件钉选侧边栏
# 左侧边栏：显示已加载的文件列表，支持多选添加和移除。

from __future__ import annotations

import threading
import flet as ft
from pathlib import Path
from typing import Optional

from ..app_state import AppState, Event
from core.app.backend import app_base_dir
from ..theme import Palette, section_title


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
            icon_color=Palette.TEXT_SECONDARY,
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
                            icon_color=Palette.TEXT_SECONDARY,
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
                    ft.Container(content=title_row, padding=ft.Padding.only(left=12, right=4, top=8, bottom=4)),
                    ft.Divider(height=1, color=Palette.DIVIDER_DARK, thickness=1),
                    ft.Container(
                        content=self._file_list_col,
                        expand=True,
                        padding=ft.Padding.symmetric(horizontal=6, vertical=4),
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            bgcolor=Palette.BG_SURFACE,
            expand=True,
            border=ft.Border.only(right=ft.BorderSide(1, Palette.DIVIDER_DARK)),
        )
        self.controls = [container]
        self.expand = True

    def did_mount(self):
        """在页面挂载后将 FilePicker 注册到 ServiceRegistry。"""
        self.page._services.register_service(self._file_picker)
        self.page._services.register_service(self._folder_picker)

    # ── 文件行渲染 ───────────────────────────────────────────────────────────

    def _make_file_row(self, path: Path) -> ft.Container:
        is_selected = (path == self._state.current_file)
        is_checked  = path in self._state.checked_files
        bg = Palette.PRIMARY + '33' if is_selected else 'transparent'
        icon = ft.Icons.PICTURE_AS_PDF if path.suffix.lower() == '.pdf' else ft.Icons.IMAGE_OUTLINED

        def _on_click(_e, p=path):
            self._state.select_file(p)

        def _on_remove(_e, p=path):
            self._state.remove_file(p)

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
                    ft.Icon(icon, size=14, color=Palette.PRIMARY_LIGHT),
                    ft.Text(
                        path.name,
                        size=12,
                        color=Palette.TEXT_PRIMARY if self._state.dark_mode else Palette.TEXT_DARK_PRI,
                        expand=True,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        tooltip=str(path),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE_ROUNDED,
                        icon_size=12,
                        icon_color=Palette.TEXT_DISABLED,
                        tooltip='从列表移除',
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
            self._select_all_btn.icon_color = Palette.TEXT_DISABLED
        elif n_checked >= n_pinned:
            self._select_all_btn.icon       = ft.Icons.CHECK_BOX
            self._select_all_btn.icon_color = Palette.PRIMARY
        else:
            self._select_all_btn.icon       = ft.Icons.INDETERMINATE_CHECK_BOX
            self._select_all_btn.icon_color = Palette.PRIMARY

    # ── 事件处理 ─────────────────────────────────────────────────────────────

    def _on_add_click(self, _e) -> None:
        self.page.run_task(self._pick_files_async)

    def _on_add_folder_click(self, _e) -> None:
        self.page.run_task(self._pick_folder_async)

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
        for f in files:
            if f.path:
                self._state.add_file(Path(f.path))
        self._refresh_list()

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
        added = 0
        for ext in ('*.pdf', '*.png', '*.jpg', '*.jpeg'):
            for f in folder.glob(ext):
                self._state.add_file(f)
                added += 1
        if added:
            self._refresh_list()

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
            p.run_task(_do)
        else:
            # 尚未挂载；先写入数据，did_mount 后会再次触发刷新
            self._file_list_col.controls = [
                self._make_file_row(fp) for fp in pinned
            ]
            self._update_select_all_icon()

