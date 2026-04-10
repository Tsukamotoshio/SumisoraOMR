# gui/pages/landing_page.py — 文件管理 + PDF 预览页（Landing Page）
# 左侧：文件钉选侧边栏；右侧：预览区 + 转换按钮。

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import flet as ft

from ..app_state import AppState, Event
from ..components.file_sidebar import FileSidebar
from ..components.pdf_viewer import PdfViewer
from ..components.progress_overlay import ProgressOverlay
from ..theme import Palette


class LandingPage(ft.Row):
    """首页：文件管理 + PDF 预览 + "开始转换"按钮。"""

    def __init__(self, state: AppState, overlay: ProgressOverlay):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._overlay = overlay
        self._build_ui()
        state.on(Event.FILE_SELECTED, self._on_file_selected)

    def _build_ui(self) -> None:
        self._sidebar = FileSidebar(self._state)
        self._viewer = PdfViewer()

        # 引擎选择
        self._engine_dd = ft.Dropdown(
            label='OMR 引擎',
            value='auto',
            options=[
                ft.dropdown.Option('auto',      '自动（推荐）'),
                ft.dropdown.Option('audiveris', 'Audiveris（PDF/图片）'),
                ft.dropdown.Option('oemer',     'Oemer（仅图片）'),
            ],
            width=200,
            text_size=13,
            bgcolor=Palette.BG_INPUT,
            color=Palette.TEXT_PRIMARY,
        )

        # 输出目录选择
        self._output_dir_text = ft.Text(
            '未指定（默认 Output/）',
            size=12,
            color=Palette.TEXT_SECONDARY,
            expand=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._output_dir_picker = ft.FilePicker()
        output_row = ft.Row(
            [
                ft.Icon(ft.Icons.FOLDER_OUTLINED, size=16, color=Palette.TEXT_SECONDARY),
                self._output_dir_text,
                ft.TextButton('选择', on_click=self._on_choose_output, style=ft.ButtonStyle(color=Palette.PRIMARY)),
            ],
            spacing=6,
        )

        convert_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, size=18), ft.Text('开始转换')],
                tight=True, spacing=6,
            ),
            bgcolor=Palette.PRIMARY,
            color='#FFFFFF',
            on_click=self._on_convert,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                elevation={ft.ControlState.PRESSED: 0, ft.ControlState.DEFAULT: 2},
            ),
        )

        open_output_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.FOLDER_OPEN_ROUNDED, size=16), ft.Text('打开输出目录')],
                tight=True, spacing=6,
            ),
            on_click=self._on_open_output_dir,
            style=ft.ButtonStyle(
                color=Palette.TEXT_SECONDARY,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, Palette.DIVIDER_DARK)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        options_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text('转换选项', size=14, weight=ft.FontWeight.W_600,
                            color=Palette.TEXT_PRIMARY),
                    self._engine_dd,
                    ft.Divider(height=1, color=Palette.DIVIDER_DARK),
                    output_row,
                    ft.Container(height=8),
                    convert_btn,
                    open_output_btn,
                ],
                spacing=10,
            ),
            bgcolor=Palette.BG_SURFACE,
            padding=ft.Padding.all(16),
            width=250,
            border=ft.Border.only(left=ft.BorderSide(1, Palette.DIVIDER_DARK)),
        )

        self.controls = [
            self._sidebar,
            ft.Container(content=self._viewer, expand=True),
            options_panel,
        ]
        self.expand = True
        self.vertical_alignment = ft.CrossAxisAlignment.STRETCH

    def did_mount(self):
        self.page._services.register_service(self._output_dir_picker)
        # 延迟扫描：让 did_mount 先返回、Flet 内部锁释放后再推送 UI 更新
        import threading
        threading.Timer(0.1, self._scan_input_on_startup).start()

    def _scan_input_on_startup(self) -> None:
        """在后台线程中扫描 Input/ 并刷新侧边栏（page.update 在此线程中是线程安全的）。"""
        try:
            if self.page is None:
                return
            from core.utils import get_app_base_dir
            input_dir = get_app_base_dir() / 'Input'
            if not input_dir.is_dir():
                return
            # 批量收集，避免每次 add_file 触发中间 update 事件
            newly_added: list[Path] = []
            for ext in ('*.pdf', '*.png', '*.jpg', '*.jpeg'):
                for f in sorted(input_dir.glob(ext)):
                    resolved = f.resolve()
                    if resolved not in self._state.pinned_files:
                        self._state.pinned_files.append(resolved)
                        newly_added.append(resolved)
            if not newly_added:
                return
            # 选中第一个文件
            if not self._state.current_file and self._state.pinned_files:
                self._state.current_file = self._state.pinned_files[0]
            # 重建列表控件
            self._sidebar._refresh_list()
            # 加载预览
            if self._state.current_file:
                self._viewer.load(self._state.current_file)
            # 一次性推送所有 UI 变更
            self.page.update()
        except Exception:
            pass


    # ── 事件 ─────────────────────────────────────────────────────────────────

    def _on_file_selected(self, path: Path, **_kw) -> None:
        self._viewer.load(path)

    def _on_choose_output(self, _e) -> None:
        self.page.run_task(self._pick_output_dir_async)

    async def _pick_output_dir_async(self) -> None:
        path = await self._output_dir_picker.get_directory_path(dialog_title='选择输出目录')
        if path:
            self._output_dir_text.value = path
            try:
                self._output_dir_text.update()
            except Exception:
                pass

    def _on_open_output_dir(self, _e) -> None:
        try:
            from core.utils import get_app_base_dir
            out_dir_text = self._output_dir_text.value
            if out_dir_text and out_dir_text != '未指定（默认 Output/）':
                output_dir = Path(out_dir_text)
            else:
                output_dir = get_app_base_dir() / 'Output'
            output_dir.mkdir(parents=True, exist_ok=True)
            import os
            os.startfile(str(output_dir))
        except Exception as exc:
            self._show_snack(f'无法打开目录: {exc}', Palette.ERROR)

    def _on_convert(self, _e) -> None:
        if not self._state.pinned_files:
            self._show_snack('请先添加至少一个乐谱文件。', Palette.WARNING)
            return
        if self._state.is_processing:
            return

        # 计算输出目录，检测已存在文件
        out_dir_text = self._output_dir_text.value
        try:
            from core.utils import get_app_base_dir
            base_dir = get_app_base_dir()
        except Exception:
            base_dir = Path(__file__).resolve().parents[2]
        output_dir = (
            Path(out_dir_text)
            if out_dir_text and out_dir_text != '未指定（默认 Output/）'
            else base_dir / 'Output'
        )
        existing = [
            src.name for src in self._state.pinned_files
            if (output_dir / (src.stem + '_jianpu.pdf')).exists()
        ]

        # ── 对话框内容 ───────────────────────────────────────────────────
        self._midi_cb = ft.Checkbox(
            label='同时生成 MIDI 文件',
            value=True,
        )
        self._skip_dup_cb: Optional[ft.Checkbox] = None
        warn_items: list[ft.Control] = []
        if existing:
            warn_items.append(
                ft.Row([
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED,
                            color=Palette.WARNING, size=15),
                    ft.Text(
                        f'以下 {len(existing)} 个文件已存在输出：',
                        color=Palette.WARNING, size=12,
                    ),
                ], spacing=4)
            )
            for name in existing[:5]:
                warn_items.append(
                    ft.Text(f'  • {name}', size=11,
                            color=Palette.TEXT_SECONDARY)
                )
            if len(existing) > 5:
                warn_items.append(
                    ft.Text(f'  …等另外 {len(existing)-5} 个',
                            size=11, color=Palette.TEXT_DISABLED)
                )
            self._skip_dup_cb = ft.Checkbox(
                label='跳过重复文件（不重新识别）',
                value=True,
                active_color=Palette.PRIMARY,
            )
            warn_items.append(ft.Container(height=4))
            warn_items.append(self._skip_dup_cb)

        def _do_confirm(_ev) -> None:
            self.page.pop_dialog()
            skip = (self._skip_dup_cb.value if self._skip_dup_cb is not None else False)
            self._start_conversion(
                gen_midi=self._midi_cb.value,
                skip_duplicates=skip,
                duplicate_files=set(existing),
            )

        self._confirm_dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(
                f'转换 {len(self._state.pinned_files)} 个文件',
                size=15, weight=ft.FontWeight.W_600,
            ),
            content=ft.Container(
                content=ft.Column(
                    [self._midi_cb] + warn_items,
                    tight=True,
                    spacing=8,
                ),
                padding=ft.Padding.only(top=6),
                width=400,
            ),
            actions=[
                ft.TextButton(
                    '取消',
                    on_click=lambda _ev: self.page.pop_dialog(),
                ),
                ft.FilledButton(
                    '开始转换',
                    on_click=_do_confirm,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(self._confirm_dlg)

    def _start_conversion(
        self,
        gen_midi: bool = True,
        skip_duplicates: bool = False,
        duplicate_files: set | None = None,
    ) -> None:
        self._gen_midi = gen_midi
        self._skip_dup = skip_duplicates
        self._dup_files: set = duplicate_files or set()
        self._state.is_processing = True
        self._overlay.show('正在运行 OMR 识别…')
        threading.Thread(target=self._run_conversion, daemon=True).start()

    def _run_conversion(self) -> None:
        """在后台线程中执行转换流程，通过 AppState 事件更新进度条。"""
        try:
            from core.config import OMREngine, AppConfig
            from core.pipeline import process_single_input_to_jianpu
            from core.utils import get_app_base_dir, setup_logging
            import tempfile

            base_dir = get_app_base_dir()
            out_dir_text = self._output_dir_text.value
            output_dir = (
                Path(out_dir_text)
                if out_dir_text and out_dir_text != '未指定（默认 Output/）'
                else base_dir / 'Output'
            )
            output_dir.mkdir(parents=True, exist_ok=True)

            engine_val = self._engine_dd.value or 'auto'
            engine_map = {
                'auto': OMREngine.AUTO,
                'audiveris': OMREngine.AUDIVERIS,
                'oemer': OMREngine.OEMER,
            }
            engine = engine_map.get(engine_val, OMREngine.AUTO)
            gen_midi = getattr(self, '_gen_midi', True)

            files = list(self._state.pinned_files)
            total = len(files)
            success_count = 0
            fail_count = 0

            for idx, src in enumerate(files):
                self._state.set_progress((idx / total) * 0.9, f'[{idx+1}/{total}] {src.name}')
                self._state.append_log(f'▶ 开始处理: {src.name}')

                if getattr(self, '_skip_dup', False) and src.name in getattr(self, '_dup_files', set()):
                    self._state.append_log(f'  ⏭ 已跳过（输出已存在）: {src.name}')
                    continue

                temp_dir = Path(tempfile.mkdtemp(prefix='convert_', dir=base_dir / 'build'))
                out_pdf  = output_dir / (src.stem + '_jianpu.pdf')
                out_midi = (output_dir / (src.stem + '.mid')) if gen_midi else None

                try:
                    ok = process_single_input_to_jianpu(
                        source_file=src,
                        file_temp_dir=temp_dir,
                        output_pdf=out_pdf,
                        output_midi=out_midi,
                        engine=engine,
                        editor_workspace_dir=base_dir / 'editor-workspace',
                        xml_scores_dir=base_dir / 'xml-scores',
                    )
                    if ok:
                        success_count += 1
                        self._state.append_log(f'  ✓ 完成 → {out_pdf.name}')
                        self._state.output_pdf = out_pdf
                        # 定位刚刚归档到 xml-scores 的五线谱 MusicXML
                        # 手算顺序：单引擎 → oemer 变体 → audiveris 变体
                        xml_scores_dir = base_dir / 'xml-scores'
                        archived_mxl: Optional[Path] = None
                        for candidate_name in (
                            src.stem + '.musicxml',
                            src.stem + '.mxl',
                            src.stem + '.oemer.musicxml',
                            src.stem + '.oemer.mxl',
                            src.stem + '.audiveris.musicxml',
                            src.stem + '.audiveris.mxl',
                        ):
                            c = xml_scores_dir / candidate_name
                            if c.exists():
                                archived_mxl = c
                                break
                        if archived_mxl is not None:
                            self._state.current_mxl = archived_mxl
                            self._state.emit(Event.MXL_READY, path=archived_mxl)
                    else:
                        fail_count += 1
                        self._state.append_log(f'  ✗ 失败: {src.name}')
                except Exception as exc:
                    fail_count += 1
                    self._state.append_log(f'  ✗ 异常: {exc}')

            if success_count > 0:
                msg = f'完成：{success_count} 个成功'
                if fail_count:
                    msg += f'，{fail_count} 个失败'
                self._state.set_done(msg + '。')
            elif fail_count > 0:
                self._state.set_error(f'全部 {fail_count} 个文件转换失败，请查看日志。')
            else:
                self._state.set_done('没有需要处理的文件。')
        except Exception as exc:
            self._state.set_error(str(exc))

    def _show_snack(self, msg: str, color: str = Palette.INFO) -> None:
        try:
            p = self.page
            if p:
                p.show_dialog(ft.SnackBar(
                    content=ft.Text(msg, color='#FFFFFF'),
                    bgcolor=color,
                    duration=3000,
                ))
        except Exception:
            pass
