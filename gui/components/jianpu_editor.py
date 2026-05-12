# gui/components/jianpu_editor.py — Embedded jianpu text editor.
# Right text panel: loads a .jianpu.txt file, supports editing and saving.
# Implements point-to-point line mapping with the left image panel;
# clicking a line emits a JIANPU_TXT_SELECTED event.

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

import flet as ft

from ..app_state import AppState, Event
from ..theme import Palette, section_title


class JianpuEditor(ft.Column):
    """Jianpu text editor panel.

    功能
    ----
    - 加载 / 保存 .jianpu.txt 文件
    - 高亮当前选中行（与左侧图像区联动）
    - 发出 JIANPU_TXT_SELECTED 事件（line_no: int, 0-indexed）
    """

    def __init__(self, state: AppState,
                 on_view_toggle: Optional[Callable[[bool], None]] = None):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._on_view_toggle = on_view_toggle
        self._preview_active: bool = False
        self._path: Optional[Path] = None
        self._lines: list[str] = []
        self._load_token: int = 0
        self._file_lock = threading.Lock()
        self._file_header: str = ''
        self._is_dirty: bool = False
        self._undo_stack: list[str] = []
        self._redo_stack: list[str] = []
        self._snapshot_timer: Optional[threading.Timer] = None
        # buttons stored for enable/disable; created in _build_ui
        self._undo_btn: ft.IconButton
        self._redo_btn: ft.IconButton
        self._view_toggle_btn: ft.TextButton
        self._export_dir_picker = ft.FilePicker()
        self._build_ui()
        state.on(Event.JIANPU_TXT_SELECTED, self._on_external_select)

    def did_mount(self) -> None:
        self.page._services.register_service(self._export_dir_picker)  # type: ignore[attr-defined]
        self.page.on_keyboard_event = self._handle_keyboard

    def will_unmount(self) -> None:
        self.page.on_keyboard_event = None
        if self._snapshot_timer is not None:
            self._snapshot_timer.cancel()
            self._snapshot_timer = None

    # ── 构建 UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._title = section_title('简谱编辑器', self._state.dark_mode)
        self._undo_btn = ft.IconButton(
            icon=ft.Icons.UNDO_ROUNDED,
            icon_size=16,
            tooltip='撤销 (Ctrl+Z)',
            on_click=self._on_undo,
            disabled=True,
            width=32,
            height=32,
        )
        self._redo_btn = ft.IconButton(
            icon=ft.Icons.REDO_ROUNDED,
            icon_size=16,
            tooltip='重做 (Ctrl+Y)',
            on_click=self._on_redo,
            disabled=True,
            width=32,
            height=32,
        )
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

        self._symbol_btn = ft.IconButton(
            icon=ft.Icons.HELP_OUTLINE_ROUNDED,
            icon_size=16,
            tooltip='简谱符号速查',
            on_click=self._toggle_symbol_panel,
            width=32,
            height=32,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self._view_toggle_btn = ft.TextButton(
            '简谱',
            on_click=self._on_view_toggle_click,
            style=ft.ButtonStyle(color=Palette.PRIMARY),
        )

        toolbar = ft.Container(
            content=ft.Row(
                [self._title, ft.Row(
                    [self._view_toggle_btn,
                     self._symbol_btn, self._undo_btn, self._redo_btn, export_btn, save_btn],
                    spacing=2,
                )],
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
            text_size=14,
            text_style=ft.TextStyle(font_family='Consolas', font_family_fallback=['YaHei']),
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

        self._symbol_panel = self._build_symbol_panel()

        self.controls = [
            toolbar,
            ft.Container(content=editor_row, expand=True),
            # _symbol_panel is NOT in controls by default; inserted by _toggle_symbol_panel
        ]
        self.expand = True

    def _build_symbol_panel(self) -> ft.Container:
        def _row(symbol: str, meaning: str) -> ft.Row:
            return ft.Row(
                [
                    ft.Container(
                        ft.Text(symbol, size=13,
                                style=ft.TextStyle(font_family='Consolas'),
                                color=ft.Colors.ON_SURFACE),
                        width=130,
                    ),
                    ft.Text(meaning, size=12, color=ft.Colors.ON_SURFACE_VARIANT,
                            expand=True),
                ],
                spacing=8,
            )

        def _section(label: str) -> ft.Container:
            return ft.Container(
                ft.Text(label, size=11, weight=ft.FontWeight.W_600,
                        color=ft.Colors.ON_SURFACE_VARIANT),
                padding=ft.Padding.only(top=6, bottom=2),
            )

        rows = [
            _section('── 音符'),
            _row('1 2 3 4 5 6 7', 'Do Re Mi Fa Sol La Si'),
            _row('0', '休止符'),
            _row('#1  b3', '升 Do / 降 Mi（写在数字前）'),
            _row("1'  1''", '高八度 / 超高八度'),
            _row('1,  1,,', '低八度 / 超低八度'),
            _section('── 时值'),
            _row('1', '四分音符（1 拍）'),
            _row('1 -', '二分音符（2 拍）'),
            _row('1 - - -', '全音符（4 拍）'),
            _row('q1', '八分音符（½ 拍）'),
            _row('s1', '十六分音符（¼ 拍）'),
            _row('d1', '三十二分音符（⅛ 拍）'),
            _row('1.', '附点四分（1.5 拍）'),
            _row('q1.', '附点八分（0.75 拍）'),
            _section('── 结构'),
            _row('|', '小节线'),
            _row('title=曲名', '标题（文件头部）'),
            _row('1=C  1=G', '调号'),
            _row('4/4,8', '拍号（分母=最小时值）'),
            _section('── 多声部'),
            _row('NextPart', '分隔声部；每个声部单独渲染'),
            _row('NextPart  4/4,8  ...', '换声部后必须立即重写拍号'),
            _row('声部1 / NextPart\n4/4,8 / 声部2', '同组声部并排显示在同一行谱上'),
        ]

        return ft.Container(
            content=ft.Column(rows, spacing=4, tight=True,
                              scroll=ft.ScrollMode.AUTO),
            expand=True,
            border=ft.Border.only(top=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
            bgcolor=ft.Colors.SURFACE,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        )

    def _toggle_symbol_panel(self, _e) -> None:
        if self._symbol_panel in self.controls:
            self.controls.remove(self._symbol_panel)
            self._symbol_btn.icon_color = ft.Colors.ON_SURFACE_VARIANT
        else:
            self.controls.append(self._symbol_panel)
            self._symbol_btn.icon_color = Palette.PRIMARY
        try:
            self.update()
            self._symbol_btn.update()
        except Exception:
            pass

    def _on_view_toggle_click(self, _e) -> None:
        self._preview_active = not self._preview_active
        self._view_toggle_btn.content = '原图' if self._preview_active else '简谱'
        self._view_toggle_btn.style = ft.ButtonStyle(
            color=ft.Colors.ON_SURFACE_VARIANT if self._preview_active else Palette.PRIMARY,
        )
        try:
            self._view_toggle_btn.update()
        except Exception:
            pass
        if self._on_view_toggle:
            self._on_view_toggle(self._preview_active)

    def _update_title(self) -> None:
        self._title.value = '简谱编辑器' + (' *' if self._is_dirty else '')
        try:
            self._title.update()
        except Exception:
            pass

    # ── 加载 / 保存 ──────────────────────────────────────────────────────────

    @staticmethod
    def _split_header(text: str) -> tuple[str, str]:
        """Split leading # comment block (+ one trailing blank separator) from body."""
        lines = text.splitlines(keepends=True)
        i = 0
        while i < len(lines) and lines[i].lstrip().startswith('#'):
            i += 1
        if i < len(lines) and lines[i].strip() == '':
            i += 1
        return ''.join(lines[:i]), ''.join(lines[i:])

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
            self.page.run_task(self._async_apply_loaded_content, text, token)  # type: ignore[attr-defined]
        except Exception:
            self._apply_loaded_content(text, token)

    def _schedule_load_error(self, message: str, token: int) -> None:
        if not hasattr(self, 'page') or self.page is None:
            self._apply_load_error(message, token)
            return
        try:
            self.page.run_task(self._async_apply_load_error, message, token)  # type: ignore[attr-defined]
        except Exception:
            self._apply_load_error(message, token)

    async def _async_apply_loaded_content(self, text: str, token: int) -> None:
        self._apply_loaded_content(text, token)

    async def _async_apply_load_error(self, message: str, token: int) -> None:
        self._apply_load_error(message, token)

    def _apply_loaded_content(self, text: str, token: int) -> None:
        if token != self._load_token:
            return
        if self._snapshot_timer is not None:
            self._snapshot_timer.cancel()
            self._snapshot_timer = None
        if self._preview_active:
            self._preview_active = False
            self._view_toggle_btn.content = '简谱'
            self._view_toggle_btn.style = ft.ButtonStyle(color=Palette.PRIMARY)
        self._file_header, body = self._split_header(text)
        self._lines = body.splitlines()
        self._editor.value = body
        self._undo_stack = [body]
        self._redo_stack = []
        self._is_dirty = False
        self._update_title()
        self._refresh_component()
        if hasattr(self, 'page') and self.page is not None:
            try:
                self.page.run_task(self._async_update_undo_redo_after_load)
            except Exception:
                pass

    async def _async_update_undo_redo_after_load(self) -> None:
        self._update_undo_redo_buttons()

    def _apply_load_error(self, message: str, token: int) -> None:
        if token != self._load_token:
            return
        self._file_header = ''
        self._editor.value = message
        self._refresh_component()

    def _refresh_component(self) -> None:
        try:
            self.update()
        except Exception:
            pass
        if hasattr(self, 'page') and self.page is not None:
            try:
                self.page.schedule_update()  # type: ignore[attr-defined]
            except Exception:
                pass

    def _on_text_change(self, e) -> None:
        if not hasattr(self, 'page') or self.page is None:
            return
        current = self._editor.value or ''
        self._lines = current.splitlines()
        if not self._is_dirty:
            self._is_dirty = True
            self._update_title()
        # Enable undo button eagerly (snapshot is debounced but state will be pushable)
        if getattr(self, '_undo_btn', None) is not None and self._undo_stack and self._undo_btn.disabled:
            self._undo_btn.disabled = False
            try:
                self._undo_btn.update()
            except Exception:
                pass
        if self._snapshot_timer is not None:
            self._snapshot_timer.cancel()
        self._snapshot_timer = threading.Timer(0.8, self._push_snapshot, args=(current,))
        self._snapshot_timer.start()

    def _on_save(self, _e) -> None:
        if self._path is None:
            return
        try:
            content = self._file_header + (self._editor.value or '')
            with self._file_lock:
                self._path.write_text(content, encoding='utf-8')
            self._is_dirty = False
            self._update_title()
            self._state.append_log(f'已保存: {self._path.name}')
        except Exception as exc:
            self._state.append_log(f'保存失败: {exc}')

    def _handle_keyboard(self, e: ft.KeyboardEvent) -> None:
        if not e.ctrl:
            return
        key = e.key.lower()
        if key == 's':
            self._on_save(None)
        elif key == 'z':
            self._on_undo(None)
        elif key == 'y':
            self._on_redo(None)

    def _on_undo(self, _e) -> None:
        if self._snapshot_timer is not None:
            self._snapshot_timer.cancel()
            self._snapshot_timer = None
        if len(self._undo_stack) <= 1:
            return
        current = self._editor.value or ''
        self._redo_stack.append(current)
        self._undo_stack.pop()
        prev = self._undo_stack[-1]
        self._editor.value = prev
        self._lines = prev.splitlines()
        self._is_dirty = True
        self._update_title()
        self._update_undo_redo_buttons()
        self._refresh_component()

    def _on_redo(self, _e) -> None:
        if self._snapshot_timer is not None:
            self._snapshot_timer.cancel()
            self._snapshot_timer = None
        if not self._redo_stack:
            return
        current = self._editor.value or ''
        self._undo_stack.append(current)
        nxt = self._redo_stack.pop()
        self._editor.value = nxt
        self._lines = nxt.splitlines()
        self._is_dirty = True
        self._update_title()
        self._update_undo_redo_buttons()
        self._refresh_component()

    def _push_snapshot(self, text: str) -> None:
        # Called from a background threading.Timer thread.
        # MUST NOT touch any Flet control directly — use page.run_task for UI updates.
        if self._undo_stack and self._undo_stack[-1] == text:
            return
        self._undo_stack.append(text)
        self._redo_stack.clear()
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)

    def _update_undo_redo_buttons(self) -> None:
        self._undo_btn.disabled = len(self._undo_stack) <= 1
        self._redo_btn.disabled = not self._redo_stack
        try:
            self._undo_btn.update()
            self._redo_btn.update()
        except Exception:
            pass

    def save(self) -> None:
        """Public save — callable from parent page."""
        self._on_save(None)

    # ── 导出 PDF ─────────────────────────────────────────────────────────────

    def _on_export_pdf(self, _e) -> None:
        # 弹出目录选择对话框（异步），选好后在后台线程渲染并复制
        self.page.run_task(self._export_pdf_ask_dir)  # type: ignore[attr-defined]

    async def _export_pdf_ask_dir(self) -> None:
        if self._path is None:
            self._state.append_log('导出失败：请先加载简谱文件')
            return
        # 先保存最新内容
        try:
            content = self._file_header + (self._editor.value or '')
            with self._file_lock:
                self._path.write_text(content, encoding='utf-8')
        except Exception as exc:
            self._state.append_log(f'保存失败（导出前）: {exc}')
            return
        dest_str = await self._export_dir_picker.get_directory_path(dialog_title='选择 PDF 导出目录')
        if not dest_str:
            return
        dest_dir = Path(dest_str)
        threading.Thread(target=self._export_pdf_thread, args=(dest_dir,), daemon=True).start()

    def _export_pdf_thread(self, dest_dir: Path) -> None:
        ly_path: Optional[Path] = None
        pdf_path: Optional[Path] = None
        try:
            import shutil
            from core.render.lilypond_runner import render_jianpu_ly, render_lilypond_pdf
            txt_path = self._path
            if txt_path is None:
                return
            ly_path  = txt_path.with_suffix('.ly')
            self._state.append_log(f'正在生成 LilyPond 中间文件: {ly_path.name}')
            with self._file_lock:
                ok = render_jianpu_ly(txt_path, ly_path)
            if not ok:
                self._state.append_log('jianpu-ly 转换失败，请确认 LilyPond 与 jianpu-ly.py 已安装')
                return
            # 规范化 .ly 文件：注入 CJK 字体与 \markup 标题块，与转换流水线保持一致
            try:
                _title = ''
                _txt_content = txt_path.read_text(encoding='utf-8')
                for _ln in _txt_content.splitlines():
                    if _ln.strip().startswith('title='):
                        _title = _ln.strip()[len('title='):]
                        break
                from core.render.renderer import sanitize_generated_lilypond_file
                sanitize_generated_lilypond_file(ly_path, _title)
            except Exception:
                pass
            self._state.append_log('正在渲染 PDF…')
            pdf_path = render_lilypond_pdf(ly_path)
            if pdf_path and pdf_path.exists():
                dest_pdf = dest_dir / pdf_path.name
                shutil.copy2(str(pdf_path), str(dest_pdf))
                self._state.append_log(f'PDF 已导出: {dest_pdf}')
            else:
                self._state.append_log('PDF 渲染失败，请检查 LilyPond 安装')
        except Exception as exc:
            self._state.append_log(f'导出 PDF 出错: {exc}')
        finally:
            # 清理中间文件：.ly、LilyPond 顺带生成的 .midi、以及已复制到目标目录的 .pdf
            for _tmp in (
                ly_path,
                ly_path.with_suffix('.midi') if ly_path is not None else None,
                pdf_path,
            ):
                if _tmp is not None:
                    try:
                        _tmp.unlink(missing_ok=True)
                    except Exception:
                        pass

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
            self.page.run_task(self._async_refresh)  # type: ignore[attr-defined]
        except Exception:
            try:
                self.page.schedule_update()  # type: ignore[attr-defined]
            except Exception:
                pass

    async def _async_refresh(self) -> None:
        try:
            self.update()
        except Exception:
            pass

    def reset(self) -> None:
        if self._snapshot_timer is not None:
            self._snapshot_timer.cancel()
            self._snapshot_timer = None
        self._path = None
        self._file_header = ''
        self._lines = []
        self._editor.value = ''
        self._undo_stack = []
        self._redo_stack = []
        self._is_dirty = False
        self._preview_active = False
        self._view_toggle_btn.content = '简谱'
        self._view_toggle_btn.style = ft.ButtonStyle(color=Palette.PRIMARY)
        if self._symbol_panel in self.controls:
            self.controls.remove(self._symbol_panel)
            self._symbol_btn.icon_color = ft.Colors.ON_SURFACE_VARIANT
        self._update_title()
        self._update_undo_redo_buttons()
        self._request_page_refresh()


    @property
    def text(self) -> str:
        return self._editor.value or ''
