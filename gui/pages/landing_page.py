# gui/pages/landing_page.py — 文件管理 + PDF 预览页（Landing Page）
# 左侧：文件钉选侧边栏；右侧：预览区 + 转换按钮。

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

import flet as ft

from ..app_state import AppState, Event
from core.app.backend import app_base_dir, output_dir, open_directory
from ..components.file_sidebar import FileSidebar
from ..components.pdf_viewer import PdfViewer
from ..components.progress_overlay import ProgressOverlay
from ..theme import Palette


_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


class LandingPage(ft.Row):
    """首页：文件管理 + PDF 预览 + "开始转换"按钮。"""

    def __init__(self, state: AppState, overlay: ProgressOverlay):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._overlay = overlay
        self._scan_token: int = 0
        self._preloaded_paths: set[str] = set()
        self._worker_proc: Optional[subprocess.Popen] = None
        self._build_ui()
        state.on(Event.FILE_SELECTED,   self._on_file_selected)
        state.on(Event.FILES_CHANGED,   self._on_files_changed)

    def _build_ui(self) -> None:
        self._sidebar = FileSidebar(self._state)
        self._viewer = PdfViewer()

        # 引擎选择（'auto' 已关闭，待重新设计后恢复）
        self._engine_dd = ft.Dropdown(
            label='OMR 引擎',
            value='audiveris',
            options=[
                # ft.dropdown.Option('auto',  '自动（暂未开放）'),
                ft.dropdown.Option('audiveris', 'Audiveris（启发式算法）'),
                ft.dropdown.Option('homr',      'Homr（实验性）'),
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

        self._convert_btn = ft.Button(
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
                    self._convert_btn,
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
        self._scan_token += 1
        current_token = self._scan_token
        import threading
        threading.Thread(target=self._scan_input_on_startup, args=(current_token,), daemon=True).start()

    def _scan_input_on_startup(self, token: int) -> None:
        """在后台线程中扫描 Input/ 并刷新侧边栏（page.update 在此线程中是线程安全的）。"""
        if token != self._scan_token:
            return
        try:
            if self.page is None:
                return
            input_dir = app_base_dir() / 'Input'
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
            # 发出统一文件变化事件，刷新列表
            self._state.emit(Event.FILES_CHANGED, files=list(self._state.pinned_files))
            # 选中第一个文件并触发预览
            if not self._state.current_file and self._state.pinned_files:
                self._state.select_file(self._state.pinned_files[0])
            elif self._state.current_file and token == self._scan_token:
                self._viewer.load(self._state.current_file)
            # 预加载所有文件，减少后续切换延迟
            threading.Thread(target=self._preload_all_files, args=(list(self._state.pinned_files), token), daemon=True).start()
            # 一次性推送所有 UI 变更（通过 asyncio 事件循环，避免跨线程 page.update）
            async def _do_page_update():
                try:
                    self.page.update()
                except Exception:
                    pass
            self.page.run_task(_do_page_update)
        except Exception:
            pass

    def _preload_all_files(self, files: list[Path], token: int) -> None:
        if token != self._scan_token:
            return
        for path in files:
            if token != self._scan_token:
                return
            try:
                self._viewer.preload(path)
            except Exception:
                pass

    # ── 事件 ─────────────────────────────────────────────────────────────────

    def _on_file_selected(self, path: Path, **_kw) -> None:
        self._viewer.load(path)

    def _on_files_changed(self, files: list[Path], **_kw) -> None:
        for path in files:
            try:
                self._viewer.preload(path)
            except Exception:
                pass


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

    def _show_snack(self, msg: str, color: str = Palette.INFO) -> None:
        self.page.show_dialog(ft.SnackBar(
            content=ft.Text(msg, color='#FFFFFF'),
            bgcolor=color,
            duration=3500,
        ))

    def _on_open_output_dir(self, _e) -> None:
        try:
            output_dir_path = output_dir(self._output_dir_text.value)
            open_directory(output_dir_path)
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
        output_path = output_dir(out_dir_text)
        existing = [
            src.name for src in self._state.pinned_files
            if (output_path / (src.stem + '_jianpu.pdf')).exists()
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
            skip = bool(self._skip_dup_cb.value) if self._skip_dup_cb is not None else False
            self._start_conversion(
                gen_midi=bool(self._midi_cb.value),
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
        """在后台线程中启动 Worker 子进程并通过 JSON IPC 更新进度。"""
        _done_or_error_received = False
        try:
            base_dir = app_base_dir()
            output_path = output_dir(self._output_dir_text.value)

            engine_val = self._engine_dd.value or 'auto'
            gen_midi = getattr(self, '_gen_midi', True)
            files = list(self._state.pinned_files)
            _gpu_crash_count = 0
            _total_files_orig = len(files)   # GPU 崩溃重试时保持总数不变
            _files_done_total = 0             # 跨次 worker 运行累计完成数

            while True:
                task = {
                    'files': [str(f) for f in files],
                    'engine': engine_val,
                    'output_dir': str(output_path),
                    'gen_midi': gen_midi,
                    'skip_dup': getattr(self, '_skip_dup', False),
                    'dup_files': list(getattr(self, '_dup_files', set())),
                    'base_dir': str(base_dir),
                    'use_gpu': engine_val == 'homr' and _gpu_crash_count < 2,
                    'files_offset': _files_done_total,       # GPU崩溃重试用
                    'total_files_orig': _total_files_orig,   # GPU崩溃重试用
                }

                # ── 确定 Worker 命令 ──────────────────────────────────────────────
                if getattr(sys, 'frozen', False):
                    # 打包版：直接复用自身可执行文件
                    worker_cmd = [sys.executable, '--worker']
                else:
                    # 开发模式：通过 Python 解释器运行 app.py
                    worker_cmd = [sys.executable, str(Path(__file__).parent.parent.parent / 'app.py'), '--worker']

                # CREATE_NO_WINDOW 防止 Windows 弹出控制台窗口
                extra_kwargs: dict = {}
                if sys.platform == 'win32':
                    extra_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

                proc = subprocess.Popen(
                    worker_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **extra_kwargs,
                )
                self._worker_proc = proc

                err_lines: list[str] = []

                def _read_stderr() -> None:
                    try:
                        for raw_line in proc.stderr:
                            if not self._state.is_processing:
                                break
                            line_str = raw_line.decode('utf-8', errors='replace')
                            line_str = _ANSI_ESCAPE_RE.sub('', line_str).strip()
                            if line_str:
                                err_lines.append(line_str)
                    except Exception:
                        pass

                threading.Thread(target=_read_stderr, daemon=True).start()

                # ── 发送任务 ──────────────────────────────────────────────────────
                proc.stdin.write((json.dumps(task, ensure_ascii=False) + '\n').encode('utf-8'))
                proc.stdin.flush()
                proc.stdin.close()

                # ── 逐行读取 Worker 响应 ──────────────────────────────────────────
                _files_done_this_run = 0  # 本次 worker 运行中已完成（收到 result）的文件数
                for raw_line in proc.stdout:
                    # 用户关闭了进度浮层：终止子进程，退出读取循环
                    if not self._state.is_processing:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        break
                    line_str = raw_line.decode('utf-8', errors='replace').strip()
                    if not line_str:
                        continue
                    try:
                        msg = json.loads(line_str)
                    except json.JSONDecodeError:
                        # 非 JSON 行（Worker 意外输出）忽略
                        continue

                    mtype = msg.get('type', '')
                    if mtype == 'progress':
                        self._state.set_progress(msg.get('value', 0.0), msg.get('message', ''))
                    elif mtype == 'sub_progress':
                        self._overlay.set_sub_progress(msg.get('value', 0.0), msg.get('message', ''))
                    elif mtype == 'log':
                        text = msg.get('text', '').strip()
                        self._state.append_log(text)
                    elif mtype == 'result':
                        _files_done_this_run += 1
                        if msg.get('success'):
                            self._state.output_pdf = Path(msg['output_pdf'])
                            if msg.get('archived_mxl'):
                                archived = Path(msg['archived_mxl'])
                                self._state.current_mxl = archived
                                self._state.emit(Event.MXL_READY, path=archived)
                    elif mtype == 'done':
                        _done_or_error_received = True
                        self._state.set_done(msg.get('message', '完成。'))
                    elif mtype == 'error':
                        _done_or_error_received = True
                        self._state.set_error(msg.get('message', '未知错误'))

                proc.wait()

                # ── Worker 异常退出且未发送 done/error ────────────────────────────
                if not _done_or_error_received:
                    err_text = '\n'.join(err_lines).strip()
                    if proc.returncode != 0:
                        gpu_access_violation_codes = {-1073741819, 3221225477}
                        if engine_val == 'homr' and task.get('use_gpu') and proc.returncode in gpu_access_violation_codes:
                            # 0xC0000005 访问冲突，GPU 模式崩溃
                            # 裁掉已处理完的文件，从崩溃位置继续重试
                            _files_done_total += _files_done_this_run
                            files = files[_files_done_this_run:]
                            _gpu_crash_count += 1
                            if _gpu_crash_count < 2:
                                self._state.append_log('[homr] GPU 模式发生崩溃，正在以 GPU 模式重试…')
                            else:
                                self._state.append_log('[homr] GPU 模式再次崩溃，已回退到 CPU 模式重试…')
                            _done_or_error_received = False
                            continue
                        self._state.set_error(
                            f'Worker 进程异常退出（{proc.returncode}）：{err_text[:300] if err_text else "无详情"}'
                        )
                    else:
                        self._state.set_done('完成。')
                    break
                else:
                    break

        except Exception as exc:
            if not _done_or_error_received:
                self._state.set_error(str(exc))
        finally:
            self._worker_proc = None
            if self._state.is_processing:
                self._state.is_processing = False

    def terminate_worker(self) -> None:
        """关闭 GUI 时强制终止 Worker 子进程及其所有子进程（如 java.exe）。"""
        self._state.is_processing = False
        p = self._worker_proc
        if p is None:
            return
        self._worker_proc = None
        if sys.platform == 'win32':
            # taskkill /F /T 递归终止整个进程树（包含 Audiveris 启动的 java.exe）
            try:
                subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(p.pid)],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception:
                pass
        else:
            try:
                import os as _os2
                import signal as _sig
                _os2.killpg(_os2.getpgid(p.pid), _sig.SIGKILL)
            except Exception:
                pass
        try:
            p.kill()
        except Exception:
            pass
