# gui/worker_launcher.py — Conversion worker subprocess orchestration (no Flet).
# Extracted from landing_page.py: the landing page owns UI and callbacks only;
# everything about spawning `app.py --worker` subprocesses, JSON-over-stdout IPC,
# progress aggregation, GPU-crash retry, and process-tree termination lives here.

from __future__ import annotations

import json
import os
import queue as _queue_mod
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.app.backend import app_base_dir, output_dir
from .app_state import AppState, Event
from .strings import t

_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


def build_worker_cmd() -> list[str]:
    """Return the command line that launches a conversion worker subprocess."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--worker']
    return [sys.executable, str(Path(__file__).parent.parent / 'app.py'), '--worker']


def split_file_chunks(files: list, n: int) -> list[list]:
    """Split *files* into at most *n* contiguous chunks for parallel workers."""
    if n <= 1:
        return [list(files)]
    chunk_size = max(1, (len(files) + n - 1) // n)
    return [files[i:i + chunk_size] for i in range(0, len(files), chunk_size)]


def _build_popen_kwargs(n_workers: int) -> dict:
    """Shared Popen kwargs for worker subprocesses (single and parallel paths).

    Injects HOMR_MODELS_DIR so the worker loads weights from
    <app_base_dir>/models/ (where on-demand downloads land) rather than the
    legacy submodule paths.

    并行时（n_workers > 1）额外设置 HOMR_ORT_INTRA_THREADS：并行 worker 会各自把
    ONNX intra-op 线程开到 (核数-2)，n 个 worker 一起就是 n×(核数-2) 抢占核数个核
    → 超额订阅、上下文切换抖动，实测比顺序还慢。按并发数把每个 worker 的线程上限
    压到约 核数/n，让总线程数不超过核数。单 worker 不设，保留全部线程。
    """
    kwargs: dict = {}
    if sys.platform == 'win32':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    from core.app.backend import models_dir as _models_dir
    env = os.environ.copy()
    env['HOMR_MODELS_DIR'] = str(_models_dir())
    if n_workers > 1:
        cpu = os.cpu_count() or 4
        env['HOMR_ORT_INTRA_THREADS'] = str(max(1, cpu // max(1, n_workers) - 1))
    kwargs['env'] = env
    return kwargs


@dataclass
class ConversionOptions:
    """Snapshot of the conversion options taken when a batch is dispatched."""
    engine: str = 'auto'
    sr_engine: str = 'waifu2x'
    gen_midi: bool = True
    skip_dup: bool = False
    dup_files: list = field(default_factory=list)
    melody_only: bool = False  # audio: reduce to a single melody line (skyline)


class ConversionRunner:
    """Drives one conversion batch through worker subprocesses.

    UI 通过两个回调接入：*on_sub_progress*（文件内子进度 → 进度浮层）与
    *on_finished*（批次结束 → 调度结果对话框）。其余状态全部经由共享的
    :class:`AppState`（进度、日志、conversion_summary、is_processing）。
    """

    def __init__(
        self,
        state: AppState,
        on_sub_progress: Callable[[float, str], None],
        on_finished: Callable[[], None],
    ) -> None:
        self._state = state
        self._on_sub_progress = on_sub_progress
        self._on_finished = on_finished
        self.procs: list[subprocess.Popen] = []

    # ── 批次入口 ─────────────────────────────────────────────────────────────

    def run(self, files: list[Path], n: int, opts: ConversionOptions) -> None:
        """Run *files* through *n* parallel workers (1 = sequential single worker)."""
        if n <= 1:
            self._run_single(files, opts)
        else:
            self._run_parallel(files, n, opts)

    def terminate(self) -> None:
        """关闭 GUI 时强制终止所有 Worker 子进程及其子进程（如 java.exe）。"""
        self._state.is_processing = False
        procs = list(self.procs)
        self.procs = []
        if not procs:
            return
        if sys.platform == 'win32':
            for p in procs:
                try:
                    subprocess.run(
                        ['taskkill', '/F', '/T', '/PID', str(p.pid)],
                        capture_output=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        timeout=5,
                    )
                except Exception:
                    pass
        else:
            for p in procs:
                try:
                    import signal as _sig
                    os.killpg(os.getpgid(p.pid), _sig.SIGKILL)
                except Exception:
                    pass
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass

    # ── 单 Worker 路径（含 GPU 崩溃重试，低配默认路径）────────────────────────

    def _run_single(self, files: list[Path], opts: ConversionOptions) -> None:
        _done_or_error_received = False
        _conversion_results = {'success': [], 'fallback': [], 'failed': []}

        try:
            base_dir = app_base_dir()
            output_path = output_dir(None)

            engine_val = opts.engine
            _gpu_crash_count = 0
            _total_files_orig = len(files)
            _files_done_total = 0

            while True:
                task = {
                    'files': [str(f) for f in files],
                    'engine': engine_val,
                    'sr_engine': opts.sr_engine,
                    'output_dir': str(output_path),
                    'gen_midi': opts.gen_midi,
                    'melody_only': opts.melody_only,
                    'skip_dup': opts.skip_dup,
                    'dup_files': list(opts.dup_files),
                    'base_dir': str(base_dir),
                    'use_gpu': engine_val in ('homr', 'auto') and _gpu_crash_count < 2,
                    'files_offset': _files_done_total,
                    'total_files_orig': _total_files_orig,
                }

                proc = subprocess.Popen(
                    build_worker_cmd(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **_build_popen_kwargs(1),
                )
                self.procs = [proc]

                err_lines: list[str] = []
                _current_processing_file: Optional[str] = None

                def _read_stderr(proc=proc, err_lines=err_lines) -> None:
                    try:
                        for raw_line in proc.stderr:  # type: ignore[union-attr]
                            if not self._state.is_processing:
                                break
                            line_str = raw_line.decode('utf-8', errors='replace')  # type: ignore[attr-defined]
                            line_str = _ANSI_ESCAPE_RE.sub('', line_str).strip()
                            if line_str:
                                err_lines.append(line_str)
                    except Exception:
                        pass

                threading.Thread(target=_read_stderr, daemon=True).start()

                proc.stdin.write((json.dumps(task, ensure_ascii=False) + '\n').encode('utf-8'))  # type: ignore[union-attr, arg-type]
                proc.stdin.flush()  # type: ignore[union-attr]
                proc.stdin.close()  # type: ignore[union-attr]

                _files_done_this_run = 0
                for raw_line in proc.stdout:  # type: ignore[union-attr]
                    if not self._state.is_processing:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        break
                    line_str = raw_line.decode('utf-8', errors='replace').strip()  # type: ignore[attr-defined]
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
                        self._on_sub_progress(msg.get('value', 0.0), msg.get('message', ''))
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
                                'reason': reason or t("landing.unknown_reason")
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
                                entry = {
                                    'file': _current_processing_file,
                                    'engine_used': msg.get('engine_used', ''),
                                    'image_type': msg.get('image_type', ''),
                                }
                                # 识别过程中发生过引擎回退（例如 Audiveris 失败后改用
                                # Homr）：单独归入 fallback 列表，不算失败，也不与
                                # 干净成功的文件混在一起，方便用户知晓需要留意质量。
                                if msg.get('fallback_used'):
                                    _conversion_results['fallback'].append(entry)
                                else:
                                    _conversion_results['success'].append(entry)
                        else:
                            if _current_processing_file:
                                if not any(f['file'] == _current_processing_file for f in _conversion_results['failed']):
                                    _conversion_results['failed'].append({
                                        'file': _current_processing_file,
                                        'reason': msg.get('reason') or t("landing.unknown_reason"),
                                    })
                    elif mtype == 'done':
                        _done_or_error_received = True
                        self._state.conversion_summary = {
                            'success_count': len(_conversion_results['success']),
                            'fallback_count': len(_conversion_results['fallback']),
                            'failed_count': len(_conversion_results['failed']),
                            'success_files': _conversion_results['success'],
                            'fallback_files': _conversion_results['fallback'],
                            'failed_files': _conversion_results['failed'],
                            'message': msg.get('message', t("landing.done_message")),
                            'total': len(_conversion_results['success']) + len(_conversion_results['fallback']) + len(_conversion_results['failed']),
                        }
                        self._state.set_done(msg.get('message', t("landing.done_message")))
                    elif mtype == 'error':
                        _done_or_error_received = True
                        self._state.set_error(msg.get('message', t("landing.unknown_error")))

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
                                self._state.append_log(t("landing.log_gpu_crash_retry"))
                            else:
                                self._state.append_log(t("landing.log_gpu_crash_fallback_cpu"))
                            _done_or_error_received = False
                            continue
                        self._state.set_error(
                            t(
                                "landing.worker_crash_error",
                                code=proc.returncode,
                                detail=err_text[:300] if err_text else t("landing.worker_crash_no_detail"),
                            )
                        )
                    else:
                        self._state.set_done(t("landing.done_message"))
                    break
                else:
                    break

        except Exception as exc:
            if not _done_or_error_received:
                self._state.set_error(str(exc))
        finally:
            procs = list(self.procs)
            self.procs = []
            for _p in procs:
                if _p.poll() is None:
                    try:
                        _p.kill()
                        _p.wait(timeout=3)
                    except Exception:
                        pass
            if self._state.is_processing:
                self._state.is_processing = False
            self._on_finished()

    # ── 并行 Worker 路径（高端机加速）─────────────────────────────────────────

    def _run_parallel(self, files: list[Path], n: int, opts: ConversionOptions) -> None:
        """将文件列表分片，同时启动 n 个 Worker 子进程，聚合进度和结果。"""
        _all_results: dict = {'success': [], 'fallback': [], 'failed': []}
        _any_error = False

        try:
            base_dir = app_base_dir()
            output_path = output_dir(None)
            engine_val = opts.engine
            total = len(files)

            chunks = split_file_chunks(files, n)
            n_actual = len(chunks)

            q: _queue_mod.Queue = _queue_mod.Queue()

            # 每个 worker 覆盖总进度条中不重叠的区间；记录各 worker 起始值以计算增量
            _w_progress: list[float] = [0.0] * n_actual
            _w_start: list[Optional[float]] = [None] * n_actual
            _current_files: list[str] = [''] * n_actual
            _worker_done: list[bool] = [False] * n_actual  # 是否已收到该 worker 的 done 消息

            extra_kwargs = _build_popen_kwargs(n_actual)

            worker_cmd = build_worker_cmd()
            procs: list[subprocess.Popen] = []

            # 捕获每个 worker 的最后 stderr 行，用于崩溃时诊断
            _stderr_tails: list[list[str]] = [[] for _ in range(n_actual)]

            def _read_worker(worker_id: int, proc: subprocess.Popen) -> None:
                try:
                    for raw_line in proc.stdout:  # type: ignore[union-attr]
                        line_str = raw_line.decode('utf-8', errors='replace').strip()  # type: ignore[attr-defined]
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
                    for raw_line in proc.stderr:  # type: ignore[union-attr]
                        line_str = raw_line.decode('utf-8', errors='replace').rstrip()  # type: ignore[attr-defined]
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
                    'sr_engine': opts.sr_engine,
                    'output_dir': str(output_path),
                    'gen_midi': opts.gen_midi,
                    'melody_only': opts.melody_only,
                    'skip_dup': opts.skip_dup,
                    'dup_files': list(opts.dup_files),
                    'base_dir': str(base_dir),
                    # 多 Worker 并行时禁用 GPU 推理，防止各 Worker 同时抢占显存导致 OOM 崩溃。
                    # 单 Worker 路径（_run_single）仍保留 GPU + 崩溃重试逻辑。
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
                proc.stdin.write((json.dumps(task, ensure_ascii=False) + '\n').encode('utf-8'))  # type: ignore[union-attr, arg-type]
                proc.stdin.flush()  # type: ignore[union-attr]
                proc.stdin.close()  # type: ignore[union-attr]

                # 每个 Worker 启动后立即开启 reader/stderr 线程，
                # 避免多个 Worker 同时积压输出导致管道缓冲区溢出而阻塞。
                threading.Thread(target=_read_worker, args=(i, proc), daemon=True).start()
                threading.Thread(target=_capture_stderr, args=(i, proc), daemon=True).start()

                names_preview = ', '.join(Path(f).name for f in chunk[:3])
                if len(chunk) > 3:
                    names_preview += t("landing.names_preview_more", n=len(chunk))
                self._state.append_log(t("landing.log_worker_assign", worker=i + 1, names=names_preview))

            self.procs = procs

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
                        _w_progress[i] - float(_w_start[i])  # type: ignore[arg-type]
                        for i in range(n_actual)
                        if _w_start[i] is not None
                    )
                    msg_text = msg.get('message', '')
                    if msg_text and ']' in msg_text:
                        _current_files[worker_id] = msg_text.split('] ', 1)[-1]
                    self._state.set_progress(overall, prefix + msg_text)

                elif mtype == 'sub_progress':
                    self._on_sub_progress(msg.get('value', 0.0), msg.get('message', ''))

                elif mtype == 'log':
                    text = msg.get('text', '').strip()
                    self._state.append_log(prefix + text)
                    if '✗' in text and _current_files[worker_id]:
                        reason = text.replace('✗', '').strip()
                        if reason.startswith('[') and ('：' in reason or ':' in reason):
                            reason = reason.split('：' if '：' in reason else ':', 1)[-1].strip()
                        _all_results['failed'].append({
                            'file': _current_files[worker_id],
                            'reason': reason or t("landing.unknown_reason"),
                        })

                elif mtype == 'result':
                    if msg.get('success'):
                        self._state.output_pdf = Path(msg['output_pdf'])
                        if msg.get('archived_mxl'):
                            archived = Path(msg['archived_mxl'])
                            self._state.current_mxl = archived
                            self._state.emit(Event.MXL_READY, path=archived)
                        if _current_files[worker_id]:
                            entry = {
                                'file': _current_files[worker_id],
                                'engine_used': msg.get('engine_used', ''),
                                'image_type': msg.get('image_type', ''),
                            }
                            if msg.get('fallback_used'):
                                _all_results['fallback'].append(entry)
                            else:
                                _all_results['success'].append(entry)
                    else:
                        cf = _current_files[worker_id]
                        if cf and not any(f['file'] == cf for f in _all_results['failed']):
                            _all_results['failed'].append({
                                'file': cf,
                                'reason': msg.get('reason') or t("landing.unknown_reason"),
                            })

                elif mtype == 'done':
                    _worker_done[worker_id] = True
                    _total_success += msg.get('success_count', 0)
                    _total_fail += msg.get('fail_count', 0)
                    _total_skipped += msg.get('skip_count', 0)

                elif mtype == 'error':
                    _any_error = True
                    err_text = msg.get('message', '')
                    self._state.append_log(t("landing.log_error_prefix", prefix=prefix, err=err_text))
                    # 该 worker 整批文件均失败（task 级别异常，未进入文件循环）
                    for _f in chunks[worker_id]:
                        _fname = Path(_f).name
                        if not any(r.get('file') == _fname for r in _all_results['failed']):
                            _all_results['failed'].append({'file': _fname, 'reason': err_text[:80] or t("landing.process_error")})
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
                    _crash_hint = t("landing.crash_hint_code", rc=rc) + (t("landing.crash_hint_tail", tail=_tail[:120]) if _tail else '')
                    self._state.append_log(t("landing.log_worker_crash", worker=i + 1, hint=_crash_hint))
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
                n_fallback = len(_all_results['fallback'])
                n_failed = len(_all_results['failed'])
                self._state.conversion_summary = {
                    'success_count': n_success,
                    'fallback_count': n_fallback,
                    'failed_count': n_failed,
                    'success_files': _all_results['success'],
                    'fallback_files': _all_results['fallback'],
                    'failed_files': _all_results['failed'],
                    'total': n_success + n_fallback + n_failed,
                    'message': '',
                }
                if n_success == 0 and n_fallback == 0 and _any_error:
                    # 全部失败——保持 overlay 打开，让用户看到错误信息
                    self._state.conversion_summary['message'] = t("landing.all_workers_failed")
                    self._state.set_error(t("landing.all_workers_failed"))
                else:
                    _parts: list[str] = []
                    if _total_success > 0:
                        _parts.append(t("landing.summary_success_part", n=_total_success))
                    if _total_fail > 0:
                        _parts.append(t("landing.summary_fail_part", n=_total_fail))
                    if _total_skipped > 0:
                        _parts.append(t("landing.summary_skipped_part", n=_total_skipped))
                    msg_text = t("landing.summary_done_prefix") + '，'.join(_parts) if _parts else t("landing.summary_done_fallback")
                    self._state.conversion_summary['message'] = msg_text + '。'
                    self._state.set_done(msg_text + '。')

        except Exception as exc:
            if not _any_error:
                self._state.set_error(str(exc))
        finally:
            self.procs = []
            if self._state.is_processing:
                self._state.is_processing = False
            self._on_finished()
