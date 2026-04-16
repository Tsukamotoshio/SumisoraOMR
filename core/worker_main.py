# core/worker_main.py — 子进程 Worker 入口点
# 通过 stdin 接收一条 JSON 任务，执行 OMR 转换管道，
# 通过 stdout 输出 JSON 进度/日志/结果消息（每行一条）。
# 由 app.py 以 "--worker" 参数启动，不依赖 Flet。

from __future__ import annotations

import io
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

# ── IPC 管道输出 ─────────────────────────────────────────────────────────────
# stdout 在打包模式 console=False 时原本是 devnull，
# 但 app.py 会在检测到 --worker 后跳过 devnull 重定向，
# 所以此处 sys.stdout 就是与 GUI 通信的管道。
# 获取二进制流，方便写入 UTF-8 JSON 行。
_ipc_out: Optional[Any] = None


def _send(msg: dict[str, Any]) -> None:
    """将 JSON 消息写入 IPC 管道（stdout）。"""
    global _ipc_out
    if _ipc_out is None:
        return
    try:
        line = json.dumps(msg, ensure_ascii=False) + '\n'
        if isinstance(_ipc_out, io.TextIOBase):
            _ipc_out.write(line)
        else:
            _ipc_out.write(line.encode('utf-8', errors='replace'))
        _ipc_out.flush()
    except Exception:
        pass


class _IPCErrorWriter(io.TextIOBase):
    """Wrap stderr so all line-based error output is forwarded via IPC."""

    def __init__(self, original: Any) -> None:
        self._original = original

    def write(self, text: str) -> int:
        if not text:
            return 0
        try:
            self._original.write(text)
        except Exception:
            pass
        try:
            for line in text.splitlines(True):
                if line.endswith('\n'):
                    _send({'type': 'log', 'text': line.rstrip('\r\n')})
        except Exception:
            pass
        return len(text)

    def flush(self) -> None:
        try:
            self._original.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return False


def _patch_log_message() -> None:
    """将 core.utils.log_message 替换为通过 IPC 转发日志的版本。

    同时保留写入日志文件的行为。
    """
    import core.utils as _utils

    def _log_ipc(message: str, level: int = logging.INFO) -> None:
        _send({'type': 'log', 'text': message})
        # 继续写入日志文件
        if not _utils.LOGGER.handlers:
            try:
                _utils.setup_logging(_utils.get_app_base_dir())
            except OSError:
                pass
        if _utils.LOGGER.handlers:
            _utils.LOGGER.log(level, message)

    _utils.log_message = _log_ipc

    # 同步修补 pipeline.py 中已经通过 from ... import 绑定的引用
    try:
        import core.pipeline as _pipeline
        _pipeline.log_message = _log_ipc  # type: ignore[attr-defined]
    except Exception:
        pass


def _restore_stdio_fds() -> None:
    """Ensure stdin/stdout/stderr are available for worker IPC in frozen console=False mode."""
    if sys.stdin is None:
        try:
            sys.stdin = open(0, 'rb', closefd=False)
        except Exception:
            pass
    if sys.stdout is None:
        try:
            sys.stdout = open(1, 'w', encoding='utf-8', errors='replace', closefd=False)
        except Exception:
            try:
                sys.stdout = open(1, 'wb', closefd=False)
            except Exception:
                sys.stdout = open(os.devnull, 'w', encoding='utf-8', errors='replace')
    if sys.stderr is None:
        try:
            sys.stderr = open(2, 'w', encoding='utf-8', errors='replace', closefd=False)
        except Exception:
            sys.stderr = open(os.devnull, 'w', encoding='utf-8', errors='replace')


def run_worker() -> None:
    """Worker 进程主函数，由 app.py 在 --worker 模式下调用。"""
    global _ipc_out

    _restore_stdio_fds()

    # ── 保存 IPC 管道引用，然后把 stdout 重定向到 stderr
    # 防止 pipeline 中残余的 print() / sys.stdout.write() 污染 JSON 流
    stdout_obj = getattr(sys.stdout, 'buffer', None) or getattr(sys.__stdout__, 'buffer', None)
    if stdout_obj is not None:
        _ipc_out = stdout_obj
    else:
        # 极少数情况：无 buffer 属性（文本流 wrap）
        _ipc_out = sys.stdout

    # Wrap stderr so homr / third-party libraries that log to stderr
    # also emit IPC log messages in the GUI.
    sys.stderr = _IPCErrorWriter(sys.stderr)
    # 将 stdout 重定向到 stderr，后续 print() 不会污染 IPC 流
    sys.stdout = sys.stderr

    try:
        # ── 读取任务 ──────────────────────────────────────────────────────────
        # 必须从 sys.stdin.buffer 读取原始字节后手动 UTF-8 解码，
        # 避免 Windows 默认 cp936 编码把 UTF-8 多字节字符（如 em-dash）解析乱码。
        raw_bytes = sys.stdin.buffer.readline()
        raw = raw_bytes.decode('utf-8', errors='replace')
        if not raw.strip():
            _send({'type': 'error', 'message': 'Worker 未收到任务，stdin 为空'})
            sys.exit(1)

        task = json.loads(raw)

        files = [Path(f) for f in task.get('files', [])]
        engine_str = task.get('engine', 'auto')
        output_dir_path = Path(task['output_dir'])
        gen_midi: bool = task.get('gen_midi', True)
        skip_dup: bool = task.get('skip_dup', False)
        dup_files: set[str] = set(task.get('dup_files', []))
        base_dir = Path(task['base_dir'])        # GPU 崩溃重试时传入的偏移量——确保进度条不会重置
        _file_start_offset: int = task.get('files_offset', 0)
        _total_files_orig: int = task.get('total_files_orig', 0)  # 0 = 会在下面用 total 充实
        # ── 修补日志函数 ──────────────────────────────────────────────────────
        _patch_log_message()

        # ── 导入（延迟，避免在修补前触发 import-time 副作用）──────────────────
        from core.config import OMREngine  # noqa: PLC0415
        from core.pipeline import process_single_input_to_jianpu  # noqa: PLC0415
        import core.pipeline as _pipeline

        # ── 注入子进度回调——pipeline 内部调用时发送 IPC sub_progress 消息 ───────
        _pipeline._subprogress_fn = lambda v, m: _send({'type': 'sub_progress', 'value': v, 'message': m})

        engine_map = {
            'auto': OMREngine.AUTO,
            'audiveris': OMREngine.AUDIVERIS,
            'oemer': OMREngine.OEMER,
            'homr': OMREngine.HOMR,
        }
        engine = engine_map.get(engine_str, OMREngine.AUTO)

        total = len(files)
        if _total_files_orig == 0:
            _total_files_orig = _file_start_offset + total
        if total == 0:
            _send({'type': 'done', 'success_count': 0, 'fail_count': 0, 'message': '没有需要处理的文件。'})
            return

        success_count = 0
        fail_count = 0

        for idx, src in enumerate(files):
            base_progress = ((_file_start_offset + idx) / _total_files_orig) * 0.9
            _send({'type': 'progress', 'value': base_progress, 'message': f'[{_file_start_offset+idx+1}/{_total_files_orig}] {src.name}'})
            _send({'type': 'sub_progress', 'value': 0.0, 'message': ''})
            _send({'type': 'log', 'text': f'▶ 开始处理: {src.name}'})

            if skip_dup and src.name in dup_files:
                _send({'type': 'log', 'text': f'  ⏭ 已跳过（输出已存在）: {src.name}'})
                continue

            (base_dir / 'build').mkdir(parents=True, exist_ok=True)
            temp_dir = Path(tempfile.mkdtemp(prefix='convert_', dir=base_dir / 'build'))
            out_pdf = output_dir_path / (src.stem + '_jianpu.pdf')
            out_midi: Optional[Path] = (output_dir_path / (src.stem + '.mid')) if gen_midi else None

            try:
                ok = process_single_input_to_jianpu(
                    source_file=src,
                    file_temp_dir=temp_dir,
                    output_pdf=out_pdf,
                    output_midi=out_midi,
                    engine=engine,
                    editor_workspace_dir=base_dir / 'editor-workspace',
                    xml_scores_dir=base_dir / 'xml-scores',
                    use_gpu_inference=task.get('use_gpu') if isinstance(task.get('use_gpu'), bool) else None,
                )
            except Exception as exc:
                fail_count += 1
                _send({'type': 'log', 'text': f'  ✗ 异常: {exc}'})
                _send({'type': 'result', 'success': False, 'output_pdf': None, 'archived_mxl': None})
                continue

            if ok:
                success_count += 1
                _send({'type': 'log', 'text': f'  ✓ 完成 → {out_pdf.name}'})

                # 在 xml-scores 中定位刚归档的五线谱 MusicXML
                xml_scores_dir_path = base_dir / 'xml-scores'
                archived_mxl: Optional[Path] = None
                for candidate_name in (
                    src.stem + '.musicxml',
                    src.stem + '.mxl',
                    src.stem + '.oemer.musicxml',
                    src.stem + '.oemer.mxl',
                    src.stem + '.audiveris.musicxml',
                    src.stem + '.audiveris.mxl',
                ):
                    c = xml_scores_dir_path / candidate_name
                    if c.exists():
                        archived_mxl = c
                        break

                _send({
                    'type': 'result',
                    'success': True,
                    'output_pdf': str(out_pdf),
                    'archived_mxl': str(archived_mxl) if archived_mxl else None,
                })
            else:
                fail_count += 1
                _send({'type': 'log', 'text': f'  ✗ 失败: {src.name}'})
                _send({'type': 'result', 'success': False, 'output_pdf': None, 'archived_mxl': None})

        # ── 发送最终完成消息 ──────────────────────────────────────────────────
        if success_count > 0:
            msg = f'完成：{success_count} 个成功'
            if fail_count:
                msg += f'，{fail_count} 个失败'
            _send({'type': 'done', 'success_count': success_count, 'fail_count': fail_count, 'message': msg + '。'})
        elif fail_count > 0:
            _send({
                'type': 'done',
                'success_count': 0,
                'fail_count': fail_count,
                'message': f'全部 {fail_count} 个文件转换失败，请查看日志。',
            })
        else:
            _send({'type': 'done', 'success_count': 0, 'fail_count': 0, 'message': '没有需要处理的文件。'})

    except Exception as exc:
        _send({'type': 'error', 'message': str(exc)})
        sys.exit(1)
