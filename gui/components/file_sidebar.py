# gui/components/file_sidebar.py — 文件钉选侧边栏
# 左侧边栏：显示已加载的文件列表，支持多选添加和移除。

from __future__ import annotations

import asyncio
import shutil
import flet as ft
from pathlib import Path

from ..app_state import AppState, Event
from core.app.backend import app_base_dir
from ..theme import Palette, with_alpha, section_title, FONT_EMPHASIS
from ..strings import t


class FileSidebar(ft.Column):
    """左侧文件钉选栏。

    用法::

        sidebar = FileSidebar(state)
        page.add(sidebar)
    """

    # Default accepted inputs: score images / PDF. The audio recognition page
    # passes an audio suffix set so its picker/import accept audio instead.
    _DEFAULT_SUFFIXES = {'.pdf', '.png', '.jpg', '.jpeg'}

    def __init__(
        self,
        state: AppState,
        allowed_suffixes: set[str] | None = None,
        section_title_key: str = 'file_sidebar.section_title',
        empty_hint_key: str = 'file_sidebar.empty_hint',
        empty_formats_key: str = 'file_sidebar.empty_supported_formats',
    ):
        super().__init__(
            spacing=0,
            width=220,
            expand=False,
        )
        self._state = state
        self._allowed_suffixes = {
            s.lower() for s in (allowed_suffixes or self._DEFAULT_SUFFIXES)
        }
        self._section_title_key = section_title_key
        self._empty_hint_key = empty_hint_key
        self._empty_formats_key = empty_formats_key
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
            tooltip=t('common.tooltip_toggle_select_all'),
            on_click=self._on_select_all_click,
            width=28,
            height=28,
        )

        self._delete_checked_btn = ft.IconButton(
            icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
            icon_size=18,
            icon_color=Palette.ERROR,
            tooltip=t('file_sidebar.tooltip_delete_checked'),
            on_click=self._on_batch_delete_click,
            disabled=True,
        )

        self._section_title = section_title(t(self._section_title_key), self._state.dark_mode)
        self._add_folder_btn = ft.IconButton(
            icon=ft.Icons.CREATE_NEW_FOLDER_ROUNDED,
            icon_size=18,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip=t('file_sidebar.tooltip_add_folder'),
            on_click=self._on_add_folder_click,
        )
        self._add_file_btn = ft.IconButton(
            icon=ft.Icons.ADD_ROUNDED,
            icon_size=18,
            icon_color=Palette.PRIMARY,
            tooltip=t('file_sidebar.tooltip_add_file'),
            on_click=self._on_add_click,
        )
        title_row = ft.Row(
            [
                ft.Row(
                    [
                        self._select_all_btn,
                        self._section_title,
                    ],
                    spacing=0,
                ),
                ft.Row(
                    [
                        self._delete_checked_btn,
                        self._add_folder_btn,
                        self._add_file_btn,
                    ],
                    spacing=0,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # FilePicker 在 Flet 0.84 是 Service，在 did_mount 时注册到 page.services
        self._file_picker   = ft.FilePicker()
        self._folder_picker = ft.FilePicker()

        # 空状态引导：首次打开时列表为空，给出明确的下一步操作入口
        self._empty_hint_text = ft.Text(
            t(self._empty_hint_key),
            size=13,
            color=ft.Colors.OUTLINE,
            text_align=ft.TextAlign.CENTER,
        )
        self._empty_add_label = ft.Text(t('file_sidebar.button_add_file'), size=13)
        self._empty_formats_text = ft.Text(
            t(self._empty_formats_key),
            size=11,
            color=ft.Colors.OUTLINE,
            text_align=ft.TextAlign.CENTER,
        )
        self._empty_hint = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.QUEUE_MUSIC_ROUNDED, size=40, color=ft.Colors.OUTLINE),
                    self._empty_hint_text,
                    ft.TextButton(
                        content=ft.Row(
                            [ft.Icon(ft.Icons.ADD_ROUNDED, size=16), self._empty_add_label],
                            tight=True, spacing=4,
                        ),
                        on_click=self._on_add_click,
                        style=ft.ButtonStyle(color=Palette.PRIMARY),
                    ),
                    self._empty_formats_text,
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            expand=True,
            alignment=ft.Alignment(0, 0),
        )

        self._list_stack = ft.Stack(
            [
                ft.Container(content=self._file_list_col, expand=True),
                self._empty_hint,
            ],
            expand=True,
        )

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
                        content=self._list_stack,
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
        _suffix = path.suffix.lower()
        if _suffix == '.pdf':
            icon = ft.Icons.PICTURE_AS_PDF
        elif _suffix in {'.mp3', '.wav', '.flac', '.m4a', '.ogg'}:
            icon = ft.Icons.AUDIOTRACK_ROUNDED
        else:
            icon = ft.Icons.IMAGE_OUTLINED

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
                title=ft.Text(t('file_sidebar.delete_dialog_title'), size=15, font_family=FONT_EMPHASIS),
                content=ft.Text(
                    t('file_sidebar.delete_dialog_body', name=p.name),
                    size=13,
                    color=ft.Colors.ON_SURFACE,
                ),
                actions=[
                    ft.TextButton(t('common.cancel'), on_click=_cancel),
                    ft.FilledButton(t('common.delete'), on_click=_do_delete),
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
                        tooltip=t('file_sidebar.tooltip_remove_from_input'),
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
        """根据勾选状态更新全选按钮图标（仅统计本页可见文件）。"""
        n_pinned  = len(self._visible_pinned())
        n_checked = len(self._visible_checked())
        if n_pinned == 0 or n_checked == 0:
            self._select_all_btn.icon       = ft.Icons.CHECK_BOX_OUTLINE_BLANK
            self._select_all_btn.icon_color = ft.Colors.OUTLINE
        elif n_checked >= n_pinned:
            self._select_all_btn.icon       = ft.Icons.CHECK_BOX
            self._select_all_btn.icon_color = Palette.PRIMARY
        else:
            self._select_all_btn.icon       = ft.Icons.INDETERMINATE_CHECK_BOX
            self._select_all_btn.icon_color = Palette.PRIMARY

    def _update_delete_btn(self) -> None:
        self._delete_checked_btn.disabled = len(self._visible_checked()) == 0
        try:
            self._delete_checked_btn.update()
        except Exception:
            pass

    def _on_batch_delete_click(self, _e) -> None:
        # 只删除本页可见（已勾选）的文件，不波及另一页的勾选文件。
        to_delete = self._visible_checked()
        if not to_delete:
            return
        input_dir = (app_base_dir() / 'Input').resolve()
        in_input = [p for p in to_delete if p.resolve().parent == input_dir]
        n = len(to_delete)
        m = len(in_input)

        if m > 0:
            msg = (
                t('file_sidebar.batch_delete_body_mixed', m=m, n=n - m)
                if n > m else
                t('file_sidebar.batch_delete_body_all_input', n=n)
            )
        else:
            msg = t('file_sidebar.batch_delete_body_list_only', n=n)

        def _do(_e) -> None:
            self.page.pop_dialog()
            deleted_set = set(to_delete)
            for p in in_input:
                try:
                    p.resolve().unlink(missing_ok=True)
                except Exception:
                    pass
            self._state.pinned_files = [f for f in self._state.pinned_files if f not in deleted_set]
            self._state.checked_files -= deleted_set
            if self._state.current_file in deleted_set:
                self._state.current_file = self._state.pinned_files[0] if self._state.pinned_files else None
            self._state.emit(Event.FILES_CHANGED, files=list(self._state.pinned_files))
            self._update_delete_btn()

        def _cancel(_e) -> None:
            self.page.pop_dialog()

        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text(t('file_sidebar.batch_delete_dialog_title'), size=15, font_family=FONT_EMPHASIS),
            content=ft.Text(msg, size=13, color=ft.Colors.ON_SURFACE),
            actions=[
                ft.TextButton(t('common.cancel'), on_click=_cancel),
                ft.FilledButton(
                    t('common.delete'),
                    on_click=_do,
                    style=ft.ButtonStyle(bgcolor=Palette.ERROR, color='#FFFFFF'),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    # ── 事件处理 ─────────────────────────────────────────────────────────────

    def _on_add_click(self, _e) -> None:
        self.page.run_task(self._pick_files_async)  # type: ignore[attr-defined]

    def _on_add_folder_click(self, _e) -> None:
        self.page.run_task(self._pick_folder_async)  # type: ignore[attr-defined]

    async def _pick_files_async(self) -> None:
        input_dir = app_base_dir() / 'Input'
        init_dir = str(input_dir) if input_dir.exists() else None
        files = await self._file_picker.pick_files(
            dialog_title=t('file_sidebar.file_picker_select_score'),
            allowed_extensions=sorted(s.lstrip('.') for s in self._allowed_suffixes),
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
            dialog_title=t('file_sidebar.folder_picker_select'),
            initial_directory=init_dir,
        )
        if not folder_path:
            return
        folder = Path(folder_path)
        paths: list[Path] = []
        for suf in sorted(self._allowed_suffixes):
            paths.extend(folder.glob(f'*{suf}'))
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
        _prog_text = ft.Text(t('file_sidebar.import_preparing'), size=13, color=ft.Colors.ON_SURFACE)
        _prog_bar  = ft.ProgressBar(
            value=0,
            width=340,
            color=Palette.PRIMARY,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border_radius=ft.BorderRadius.all(4),
        )
        _dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(t('file_sidebar.import_dialog_title', n=n), size=15, font_family=FONT_EMPHASIS),
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
            _prog_text.value = t('file_sidebar.import_progress', index=i + 1, total=n, name=src.name)
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
        _prog_text.value = t('file_sidebar.import_organizing', n=len(copied))
        _prog_bar.value  = 1.0
        self.page.update()
        await asyncio.sleep(0)  # let the "整理中" state render

        for path in copied:
            resolved = path.resolve()
            if resolved.suffix.lower() in self._allowed_suffixes and resolved not in self._state.pinned_files:
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
        # 全选/全不选仅作用于本页可见文件，不影响另一页（另一后缀集）的勾选状态。
        visible = self._visible_pinned()
        visible_checked = [f for f in visible if f in self._state.checked_files]
        if len(visible_checked) < len(visible):
            self._state.checked_files.update(visible)
        else:
            self._state.checked_files.difference_update(visible)
        self._state.emit(Event.FILES_CHECK_CHANGED, checked=set(self._state.checked_files))

    def retranslate(self) -> None:
        """Re-apply UI text in the active language (called on Event.LANGUAGE_CHANGED)."""
        self._select_all_btn.tooltip = t('common.tooltip_toggle_select_all')
        self._delete_checked_btn.tooltip = t('file_sidebar.tooltip_delete_checked')
        self._section_title.value = t(self._section_title_key)
        self._add_folder_btn.tooltip = t('file_sidebar.tooltip_add_folder')
        self._add_file_btn.tooltip = t('file_sidebar.tooltip_add_file')
        self._empty_hint_text.value = t(self._empty_hint_key)
        self._empty_add_label.value = t('file_sidebar.button_add_file')
        self._empty_formats_text.value = t(self._empty_formats_key)
        try:
            self.update()
        except Exception:
            pass
        # 重建文件行：每行的「从 Input 删除」tooltip 随语言切换（_refresh_list 从
        # state 重建并保留勾选状态）。
        self._refresh_list()

    # ── Visibility scoping ─────────────────────────────────────────────────────
    # 该侧栏只显示/操作 allowed_suffixes 内的文件（乐谱识别页=图片/PDF，音频识别页=音频）。
    # 底层 AppState.pinned_files/checked_files 是两页共享的单一托盘，因此显示、计数、
    # 全选、批量删除都必须先过滤到本页可见的子集，避免误显示或误操作另一页的文件。

    def _visible_pinned(self) -> list[Path]:
        return [f for f in self._state.pinned_files
                if f.suffix.lower() in self._allowed_suffixes]

    def _visible_checked(self) -> list[Path]:
        return [f for f in self._visible_pinned() if f in self._state.checked_files]

    def _refresh_list(self) -> None:
        # 在调用线程中先快照，避免竞态
        pinned = self._visible_pinned()
        p = self.page
        if p is not None:
            async def _do():
                # 所有 Flet 控件写操作必须在事件循环线程中执行，
                # 否则脏标记不会被触发，update() 不会发现变化。
                try:
                    self._file_list_col.controls = [
                        self._make_file_row(fp) for fp in pinned
                    ]
                    self._empty_hint.visible = not pinned
                    self._update_select_all_icon()
                    self._list_stack.update()
                    self._select_all_btn.update()
                    self._update_delete_btn()
                except Exception:
                    pass
            p.run_task(_do)  # type: ignore[attr-defined]
        else:
            # 尚未挂载；先写入数据，did_mount 后会再次触发刷新
            self._file_list_col.controls = [
                self._make_file_row(fp) for fp in pinned
            ]
            self._empty_hint.visible = not pinned
            self._update_select_all_icon()
            self._update_delete_btn()

