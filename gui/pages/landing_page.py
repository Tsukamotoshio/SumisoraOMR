# gui/pages/landing_page.py — 文件管理 + PDF 预览页（Landing Page）
# 左侧：文件钉选侧边栏；右侧：预览区 + 转换按钮。

from __future__ import annotations

import json
import os
import queue as _queue_mod
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
        self._worker_procs: list[subprocess.Popen] = []
        self._build_ui()
        state.on(Event.FILE_SELECTED,   self._on_file_selected)
        state.on(Event.FILES_CHANGED,   self._on_files_changed)

    def _build_ui(self) -> None:
        self._sidebar = FileSidebar(self._state)
        self._viewer = PdfViewer()

        # 引擎选择
        self._engine_dd = ft.Dropdown(
            label='OMR 引擎',
            value='auto',
            options=[
                ft.dropdown.Option('auto',      '自动选择（推荐）'),
                ft.dropdown.Option('audiveris', 'Audiveris（启发式算法）'),
                ft.dropdown.Option('homr',      'Homr（深度学习）'),
            ],
            width=200,
            text_size=13,
            bgcolor=Palette.BG_INPUT,
            color=Palette.TEXT_PRIMARY,
        )

        # 超分辨率引擎选择
        self._sr_engine_dd = ft.Dropdown(
            label='超分辨率引擎',
            value='waifu2x',
            options=[
                ft.dropdown.Option('waifu2x',     'waifu2x（线条画，默认）'),
                ft.dropdown.Option('realesrgan',  'Real-ESRGAN（anime，更高质量）'),
            ],
            width=200,
            text_size=13,
            bgcolor=Palette.BG_INPUT,
            color=Palette.TEXT_PRIMARY,
        )

        # 并发处理数（高端机加速；默认 1 = 顺序，低配安全）
        self._parallel_dd = ft.Dropdown(
            label='并发处理数',
            value='1',
            options=[
                ft.dropdown.Option('1',    '1（顺序，低配推荐）'),
                ft.dropdown.Option('2',    '2 个并行'),
                ft.dropdown.Option('4',    '4 个并行'),
                ft.dropdown.Option('auto', '自动（按 CPU 核数）'),
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
                    self._sr_engine_dd,
                    self._parallel_dd,
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
                        self._state.checked_files.add(resolved)
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
        checked = [f for f in self._state.pinned_files if f in self._state.checked_files]
        if not checked:
            self._show_snack('请先勾选至少一个乐谱文件。', Palette.WARNING)
            return
        if self._state.is_processing:
            return

        # 计算输出目录，检测已存在文件
        out_dir_text = self._output_dir_text.value
        output_path = output_dir(out_dir_text)
        existing = [
            src.name for src in checked
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
                files=checked,
                gen_midi=bool(self._midi_cb.value),
                skip_duplicates=skip,
                duplicate_files=set(existing),
            )

        self._confirm_dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(
                f'转换 {len(checked)} 个文件',
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
        files: list[Path] | None = None,
        gen_midi: bool = True,
        skip_duplicates: bool = False,
        duplicate_files: set | None = None,
    ) -> None:
        self._conversion_files: list[Path] = files if files is not None else list(self._state.checked_files)
        self._gen_midi = gen_midi
        self._skip_dup = skip_duplicates
        self._dup_files: set = duplicate_files or set()
        self._state.is_processing = True
        self._overlay.show('正在运行 OMR 识别…')
        threading.Thread(target=self._run_conversion, daemon=True).start()

    # ── Worker 辅助 ────────────────────────────────────────────────────────────

    def _build_worker_cmd(self) -> list[str]:
        if getattr(sys, 'frozen', False):
            return [sys.executable, '--worker']
        return [sys.executable, str(Path(__file__).parent.parent.parent / 'app.py'), '--worker']

    @staticmethod
    def _split_file_chunks(files: list, n: int) -> list[list]:
        if n <= 1:
            return [list(files)]
        chunk_size = max(1, (len(files) + n - 1) // n)
        return [files[i:i + chunk_size] for i in range(0, len(files), chunk_size)]

    def _resolve_parallel(self, n_files: int) -> int:
        val = (self._parallel_dd.value or '1').strip()
        if val == 'auto':
            n = max(1, min(4, (os.cpu_count() or 2) // 2))
        else:
            try:
                n = max(1, int(val))
            except ValueError:
                n = 1
        return min(n, max(1, n_files))

    # ── 转换主入口（dispatcher）────────────────────────────────────────────────

    def _run_conversion(self) -> None:
        files = list(getattr(self, '_conversion_files', self._state.pinned_files))
        n = self._resolve_parallel(len(files))
        if n <= 1:
            self._run_single_worker(files)
        else:
            self._run_parallel_workers(files, n)

    # ── 单 Worker 路径（含 GPU 崩溃重试，低配默认路径）────────────────────────

    def _run_single_worker(self, files: list[Path]) -> None:
        _done_or_error_received = False
        _conversion_results = {'success': [], 'failed': []}

        try:
            base_dir = app_base_dir()
            output_path = output_dir(self._output_dir_text.value)

            engine_val = self._engine_dd.value or 'auto'
            gen_midi = getattr(self, '_gen_midi', True)
            _gpu_crash_count = 0
            _total_files_orig = len(files)
            _files_done_total = 0

            while True:
                task = {
                    'files': [str(f) for f in files],
                    'engine': engine_val,
                    'sr_engine': self._sr_engine_dd.value or 'waifu2x',
                    'output_dir': str(output_path),
                    'gen_midi': gen_midi,
                    'skip_dup': getattr(self, '_skip_dup', False),
                    'dup_files': list(getattr(self, '_dup_files', set())),
                    'base_dir': str(base_dir),
                    'use_gpu': engine_val in ('homr', 'auto') and _gpu_crash_count < 2,
                    'files_offset': _files_done_total,
                    'total_files_orig': _total_files_orig,
                }

                extra_kwargs: dict = {}
                if sys.platform == 'win32':
                    extra_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

                proc = subprocess.Popen(
                    self._build_worker_cmd(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **extra_kwargs,
                )
                self._worker_procs = [proc]

                err_lines: list[str] = []
                _current_processing_file: Optional[str] = None

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

                proc.stdin.write((json.dumps(task, ensure_ascii=False) + '\n').encode('utf-8'))
                proc.stdin.flush()
                proc.stdin.close()

                _files_done_this_run = 0
                for raw_line in proc.stdout:
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
                        continue

                    mtype = msg.get('type', '')
                    if mtype == 'progress':
                        self._state.set_progress(msg.get('value', 0.0), msg.get('message', ''))
                        msg_text = msg.get('message', '')
                        if msg_text and ']' in msg_text:
                            _current_processing_file = msg_text.split('] ', 1)[-1]
                    elif mtype == 'sub_progress':
                        self._overlay.set_sub_progress(msg.get('value', 0.0), msg.get('message', ''))
                    elif mtype == 'log':
                        text = msg.get('text', '').strip()
                        self._state.append_log(text)
                        if '✗' in text and _current_processing_file:
                            reason = text.replace('✗', '').strip()
                            if reason.startswith('['):
                                if '：' in reason or ':' in reason:
                                    reason = reason.split('：' if '：' in reason else ':', 1)[-1].strip()
                            _conversion_results['failed'].append({
                                'file': _current_processing_file,
                                'reason': reason or '未知原因'
                            })
                    elif mtype == 'result':
                        _files_done_this_run += 1
                        if msg.get('success'):
                            self._state.output_pdf = Path(msg['output_pdf'])
                            if msg.get('archived_mxl'):
                                archived = Path(msg['archived_mxl'])
                                self._state.current_mxl = archived
                                self._state.emit(Event.MXL_READY, path=archived)
                            if _current_processing_file:
                                _conversion_results['success'].append(_current_processing_file)
                        else:
                            if _current_processing_file:
                                if not any(f['file'] == _current_processing_file for f in _conversion_results['failed']):
                                    _conversion_results['failed'].append({
                                        'file': _current_processing_file,
                                        'reason': '未知原因'
                                    })
                    elif mtype == 'done':
                        _done_or_error_received = True
                        self._state.conversion_summary = {
                            'success_count': len(_conversion_results['success']),
                            'failed_count': len(_conversion_results['failed']),
                            'failed_files': _conversion_results['failed'],
                            'message': msg.get('message', '完成。')
                        }
                        self._state.set_done(msg.get('message', '完成。'))
                    elif mtype == 'error':
                        _done_or_error_received = True
                        self._state.set_error(msg.get('message', '未知错误'))

                proc.wait()

                if not _done_or_error_received:
                    err_text = '\n'.join(err_lines).strip()
                    if proc.returncode != 0:
                        gpu_access_violation_codes = {-1073741819, 3221225477}
                        if engine_val in ('homr', 'auto') and task.get('use_gpu') and proc.returncode in gpu_access_violation_codes:
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
            procs = list(self._worker_procs)
            self._worker_procs = []
            for _p in procs:
                if _p.poll() is None:
                    try:
                        _p.kill()
                        _p.wait(timeout=3)
                    except Exception:
                        pass
            if self._state.is_processing:
                self._state.is_processing = False
            self._schedule_show_results()

    # ── 并行 Worker 路径（高端机加速）─────────────────────────────────────────

    def _run_parallel_workers(self, files: list[Path], n: int) -> None:
        """将文件列表分片，同时启动 n 个 Worker 子进程，聚合进度和结果。"""
        _all_results: dict = {'success': [], 'failed': []}
        _any_error = False

        try:
            base_dir = app_base_dir()
            output_path = output_dir(self._output_dir_text.value)
            engine_val = self._engine_dd.value or 'auto'
            gen_midi = getattr(self, '_gen_midi', True)
            total = len(files)

            chunks = self._split_file_chunks(files, n)
            n_actual = len(chunks)

            q: _queue_mod.Queue = _queue_mod.Queue()

            # 每个 worker 覆盖总进度条中不重叠的区间；记录各 worker 起始值以计算增量
            _w_progress: list[float] = [0.0] * n_actual
            _w_start: list[Optional[float]] = [None] * n_actual
            _current_files: list[str] = [''] * n_actual
            _worker_done: list[bool] = [False] * n_actual  # 是否已收到该 worker 的 done 消息

            extra_kwargs: dict = {}
            if sys.platform == 'win32':
                extra_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

            worker_cmd = self._build_worker_cmd()
            procs: list[subprocess.Popen] = []

            # 捕获每个 worker 的最后 stderr 行，用于崩溃时诊断
            _stderr_tails: list[list[str]] = [[] for _ in range(n_actual)]

            def _read_worker(worker_id: int, proc: subprocess.Popen) -> None:
                try:
                    for raw_line in proc.stdout:
                        line_str = raw_line.decode('utf-8', errors='replace').strip()
                        if not line_str:
                            continue
                        try:
                            q.put((worker_id, json.loads(line_str)))
                        except json.JSONDecodeError:
                            pass
                except Exception:
                    pass
                finally:
                    q.put((worker_id, None))  # sentinel：此 worker 读取完毕

            def _capture_stderr(worker_id: int, proc: subprocess.Popen) -> None:
                try:
                    for raw_line in proc.stderr:
                        line_str = raw_line.decode('utf-8', errors='replace').rstrip()
                        if line_str:
                            tail = _stderr_tails[worker_id]
                            tail.append(line_str)
                            if len(tail) > 20:
                                tail.pop(0)
                except Exception:
                    pass

            offset = 0
            for i, chunk in enumerate(chunks):
                task = {
                    'files': [str(f) for f in chunk],
                    'engine': engine_val,
                    'sr_engine': self._sr_engine_dd.value or 'waifu2x',
                    'output_dir': str(output_path),
                    'gen_midi': gen_midi,
                    'skip_dup': getattr(self, '_skip_dup', False),
                    'dup_files': list(getattr(self, '_dup_files', set())),
                    'base_dir': str(base_dir),
                    # 多 Worker 并行时禁用 GPU 推理，防止各 Worker 同时抢占显存导致 OOM 崩溃。
                    # 单 Worker 路径（_run_single_worker）仍保留 GPU + 崩溃重试逻辑。
                    'use_gpu': False,
                    'files_offset': offset,
                    'total_files_orig': total,
                }
                offset += len(chunk)

                proc = subprocess.Popen(
                    worker_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **extra_kwargs,
                )
                procs.append(proc)
                proc.stdin.write((json.dumps(task, ensure_ascii=False) + '\n').encode('utf-8'))
                proc.stdin.flush()
                proc.stdin.close()

                # 每个 Worker 启动后立即开启 reader/stderr 线程，
                # 避免多个 Worker 同时积压输出导致管道缓冲区溢出而阻塞。
                threading.Thread(target=_read_worker, args=(i, proc), daemon=True).start()
                threading.Thread(target=_capture_stderr, args=(i, proc), daemon=True).start()

                names_preview = ', '.join(Path(f).name for f in chunk[:3])
                if len(chunk) > 3:
                    names_preview += f' 等 {len(chunk)} 个'
                self._state.append_log(f'[W{i + 1}] 分配: {names_preview}')

            self._worker_procs = procs

            done_workers = 0
            _total_success = 0
            _total_fail = 0
            _total_skipped = 0

            while done_workers < n_actual:
                if not self._state.is_processing:
                    for proc in procs:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    break

                try:
                    worker_id, msg = q.get(timeout=0.1)
                except _queue_mod.Empty:
                    continue

                if msg is None:
                    done_workers += 1
                    continue

                prefix = f'[W{worker_id + 1}] ' if n_actual > 1 else ''
                mtype = msg.get('type', '')

                if mtype == 'progress':
                    v = msg.get('value', 0.0)
                    if _w_start[worker_id] is None:
                        _w_start[worker_id] = v
                    _w_progress[worker_id] = v
                    # 整体进度 = 各 worker 相对其起始值的增量之和
                    overall = sum(
                        _w_progress[i] - _w_start[i]
                        for i in range(n_actual)
                        if _w_start[i] is not None
                    )
                    msg_text = msg.get('message', '')
                    if msg_text and ']' in msg_text:
                        _current_files[worker_id] = msg_text.split('] ', 1)[-1]
                    self._state.set_progress(overall, prefix + msg_text)

                elif mtype == 'sub_progress':
                    self._overlay.set_sub_progress(msg.get('value', 0.0), msg.get('message', ''))

                elif mtype == 'log':
                    text = msg.get('text', '').strip()
                    self._state.append_log(prefix + text)
                    if '✗' in text and _current_files[worker_id]:
                        reason = text.replace('✗', '').strip()
                        if reason.startswith('[') and ('：' in reason or ':' in reason):
                            reason = reason.split('：' if '：' in reason else ':', 1)[-1].strip()
                        _all_results['failed'].append({
                            'file': _current_files[worker_id],
                            'reason': reason or '未知原因',
                        })

                elif mtype == 'result':
                    if msg.get('success'):
                        self._state.output_pdf = Path(msg['output_pdf'])
                        if msg.get('archived_mxl'):
                            archived = Path(msg['archived_mxl'])
                            self._state.current_mxl = archived
                            self._state.emit(Event.MXL_READY, path=archived)
                        if _current_files[worker_id]:
                            _all_results['success'].append(_current_files[worker_id])
                    else:
                        cf = _current_files[worker_id]
                        if cf and not any(f['file'] == cf for f in _all_results['failed']):
                            _all_results['failed'].append({'file': cf, 'reason': '未知原因'})

                elif mtype == 'done':
                    _worker_done[worker_id] = True
                    _total_success += msg.get('success_count', 0)
                    _total_fail += msg.get('fail_count', 0)
                    _total_skipped += msg.get('skip_count', 0)

                elif mtype == 'error':
                    _any_error = True
                    err_text = msg.get('message', '')
                    self._state.append_log(f'{prefix}错误: {err_text}')
                    # 该 worker 整批文件均失败（task 级别异常，未进入文件循环）
                    for _f in chunks[worker_id]:
                        _fname = Path(_f).name
                        if not any(r.get('file') == _fname for r in _all_results['failed']):
                            _all_results['failed'].append({'file': _fname, 'reason': err_text[:80] or '进程错误'})
                    _total_fail += len(chunks[worker_id])

            # 等待所有子进程完全退出，同时检测无声崩溃（无 done/error 消息）
            for i, proc in enumerate(procs):
                rc: Optional[int] = None
                try:
                    rc = proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # 超时后强制终止，再等 5 秒取 returncode
                    try:
                        proc.kill()
                        rc = proc.wait(timeout=5)
                    except Exception:
                        pass
                except Exception:
                    pass

                if not _worker_done[i] and rc != 0:
                    # rc=None（仍在运行/无法取到）或非零均视为崩溃
                    _tail = ' | '.join(_stderr_tails[i][-3:]) if _stderr_tails[i] else ''
                    _crash_hint = f'（代码 {rc}）' + (f'：{_tail[:120]}' if _tail else '')
                    self._state.append_log(f'[W{i + 1}] 进程异常退出 {_crash_hint}')
                    _any_error = True
                    for _f in chunks[i]:
                        _fname = Path(_f).name
                        already = (
                            any(r.get('file') == _fname for r in _all_results['failed'])
                            or _fname in _all_results['success']
                        )
                        if not already:
                            _all_results['failed'].append({
                                'file': _fname,
                                'reason': _crash_hint,
                            })
                            _total_fail += 1

            if self._state.is_processing:
                n_success = len(_all_results['success'])
                n_failed = len(_all_results['failed'])
                self._state.conversion_summary = {
                    'success_count': n_success,
                    'failed_count': n_failed,
                    'failed_files': _all_results['failed'],
                    'message': '',
                }
                if n_success == 0 and _any_error:
                    # 全部失败——保持 overlay 打开，让用户看到错误信息
                    self._state.conversion_summary['message'] = '所有 Worker 进程均失败，请检查日志。'
                    self._state.set_error('所有 Worker 进程均失败，请检查日志。')
                else:
                    _parts: list[str] = []
                    if _total_success > 0:
                        _parts.append(f'{_total_success} 个成功')
                    if _total_fail > 0:
                        _parts.append(f'{_total_fail} 个失败')
                    if _total_skipped > 0:
                        _parts.append(f'{_total_skipped} 个已跳过')
                    msg_text = '完成：' + '，'.join(_parts) if _parts else '完成'
                    self._state.conversion_summary['message'] = msg_text + '。'
                    self._state.set_done(msg_text + '。')

        except Exception as exc:
            if not _any_error:
                self._state.set_error(str(exc))
        finally:
            self._worker_procs = []
            if self._state.is_processing:
                self._state.is_processing = False
            self._schedule_show_results()

    def _schedule_show_results(self) -> None:
        """将结果对话框调度到 asyncio 事件循环，避免从 worker 线程直接调用 page.show_dialog()。"""
        p = self.page
        if p is not None:
            async def _do():
                self._show_conversion_results()
            p.run_task(_do)
        else:
            self._show_conversion_results()

    def _show_conversion_results(self) -> None:
        """显示转换结果详情（成功数、失败数、失败文件列表）。"""
        try:
            if self.page is None:
                return

            summary = self._state.conversion_summary
            if not summary:
                return

            success_count = summary.get('success_count', 0)
            failed_count = summary.get('failed_count', 0)
            failed_files = summary.get('failed_files', [])

            # 如果没有失败文件，不需要显示详细对话框
            if failed_count == 0:
                return

            # 构建失败详情内容
            details_items: list[ft.Control] = []

            # 摘要行
            summary_text = f'✓ 成功 {success_count} 个'
            if failed_count > 0:
                summary_text += f'  ✗ 失败 {failed_count} 个'
            details_items.append(
                ft.Text(summary_text, size=13, weight=ft.FontWeight.W_600, color=Palette.TEXT_PRIMARY)
            )
            details_items.append(ft.Container(height=8))

            # 失败文件列表
            if failed_files:
                details_items.append(
                    ft.Text('失败文件：', size=12, weight=ft.FontWeight.W_500, color=Palette.ERROR)
                )
                for idx, item in enumerate(failed_files[:10]):  # 最多显示前 10 个
                    file_name = item.get('file', '未知') if isinstance(item, dict) else item
                    reason = item.get('reason', '') if isinstance(item, dict) else ''
                    details_items.append(
                        ft.Column(
                            [
                                ft.Text(f'  • {file_name}', size=11, color=Palette.TEXT_PRIMARY),
                                ft.Text(
                                    f'    原因：{reason}',
                                    size=10,
                                    color=Palette.TEXT_SECONDARY,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                            ],
                            spacing=2,
                        )
                    )

                if len(failed_files) > 10:
                    details_items.append(
                        ft.Text(
                            f'  …以及另外 {len(failed_files)-10} 个失败的文件',
                            size=10,
                            color=Palette.TEXT_DISABLED,
                        )
                    )

            # 显示对话框
            def _close_dialog(_ev=None):
                if self.page:
                    self.page.pop_dialog()

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text('转换结果详情', size=15, weight=ft.FontWeight.W_600),
                content=ft.Container(
                    content=ft.Column(
                        details_items,
                        tight=True,
                        spacing=6,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    padding=ft.Padding.only(top=6),
                    width=450,
                    height=300,
                    max_height=400,
                ),
                actions=[
                    ft.TextButton(
                        '关闭',
                        on_click=_close_dialog,
                    ),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            self.page.show_dialog(dialog)
        except Exception:
            pass

    def terminate_worker(self) -> None:
        """关闭 GUI 时强制终止所有 Worker 子进程及其子进程（如 java.exe）。"""
        self._state.is_processing = False
        procs = list(self._worker_procs)
        self._worker_procs = []
        if not procs:
            return
        if sys.platform == 'win32':
            for p in procs:
                try:
                    subprocess.run(
                        ['taskkill', '/F', '/T', '/PID', str(p.pid)],
                        capture_output=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except Exception:
                    pass
        else:
            for p in procs:
                try:
                    import os as _os2
                    import signal as _sig
                    _os2.killpg(_os2.getpgid(p.pid), _sig.SIGKILL)
                except Exception:
                    pass
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
