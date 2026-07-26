# core/app/startup.py — Framework-independent frozen startup steps.
"""Startup steps shared by the pywebview entry points — the GUI shell
(webui/main.py) and the frozen entry stub / worker (run_webui.py). Nothing here
touches a GUI toolkit — each caller owns its own UX (message boxes, i18n) and
calls these for the plumbing, so the dev and packaged builds behave identically.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
from typing import Callable, Optional

APP_MUTEX_NAME = 'SumisoraOMR_RunningMutex'


def early_frozen_setup() -> None:
    """Env/stdio setup that MUST run before heavy imports (onnxruntime, GUI).

    Call as the very first thing in each process entry point. Idempotent.

    - Packaged builds: point SSL verification at the bundled certifi bundle as a
      **fallback** (truststore is the preferred path — see core.app.ssl_setup);
      and replace the ``None`` stdout/stderr that ``console=False`` leaves behind
      so a stray ``print()`` can't crash the process. The worker subprocess keeps
      stdout as its JSON-over-stdout IPC pipe, so it is skipped.
    - All builds: cap ONNX/OpenMP thread pools to leave one core free, so neither
      the asyncio loop (Flet) nor the UI thread (pywebview) gets starved by a
      full-core inference burst. Set before onnxruntime import / OpenMP init.
    """
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass is not None:
            cert = os.path.join(meipass, 'certifi', 'cacert.pem')
            if os.path.exists(cert):
                os.environ['SSL_CERT_FILE'] = cert
                os.environ['REQUESTS_CA_BUNDLE'] = cert
                os.environ['CURL_CA_BUNDLE'] = cert
        if '--worker' not in sys.argv:
            if sys.stdout is None:
                sys.stdout = open(os.devnull, 'w', encoding='utf-8', errors='replace')
            if sys.stderr is None:
                sys.stderr = open(os.devnull, 'w', encoding='utf-8', errors='replace')

    # setdefault so an explicit external override still wins.
    threads = str(max(1, (os.cpu_count() or 4) - 1))
    for var in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ.setdefault(var, threads)


def install_crash_handlers(
    on_uncaught: Optional[Callable[[str], None]] = None,
    suppress_dialog: bool = False,
) -> None:
    """Install process-wide handlers so an uncaught exception is logged (and,
    on the main thread, reported to the user) instead of a silent crash.

    Restores the P0-era `sys.excepthook` safety net that lived in the Flet-era
    `app.py` and was lost when that file was deleted in the v0.5.0 pywebview
    rewrite — no equivalent was ever added to `webui/main.py`.

    Two layers, not three: there is no asyncio-loop handler here, because
    unlike the Flet GUI this replaces, pywebview drives everything through
    plain threads and the WinForms message pump — there is no asyncio event
    loop in this process to attach a handler to.

    - ``sys.excepthook`` (main thread): logs the full traceback at CRITICAL,
      then calls ``on_uncaught(log_path)`` so the caller can show its own
      native dialog (this module never touches a GUI toolkit itself).
    - ``threading.excepthook`` (background threads): logs at ERROR only —
      deliberately does **not** call ``on_uncaught``, so a misbehaving
      background thread cannot pop up a dialog storm.

    Set ``suppress_dialog=True`` (or env ``SUMISORA_NO_CRASH_DIALOG=1``) to
    keep logging but skip ``on_uncaught`` — for ``--selftest`` / ``--gateN``
    automation runs, mirroring ``acquire_single_instance``'s own opt-out.
    """
    from ..utils import log_message
    from .. import utils as _utils  # module ref: LOG_FILE_PATH is set lazily, must read live

    callback = on_uncaught if not suppress_dialog and os.environ.get('SUMISORA_NO_CRASH_DIALOG') != '1' else None

    def _excepthook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log_message(f'未捕获异常，程序即将退出：\n{text}', logging.CRITICAL)
        if callback is not None:
            try:
                callback(str(_utils.LOG_FILE_PATH or ''))
            except Exception:
                pass  # 弹窗失败不应阻断原有的默认异常处理
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
        text = ''.join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        thread_name = args.thread.name if args.thread is not None else '?'
        log_message(f'后台线程 {thread_name} 未捕获异常：\n{text}', logging.ERROR)

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


def acquire_single_instance() -> bool:
    """Create the app's named mutex; return True if this is the first instance.

    Returns True (proceed) on non-Windows, in the worker subprocess, or when the
    mutex is newly created. Returns False when another instance already holds it —
    the caller should notify the user and exit. No i18n / message box here so each
    shell controls its own UX.
    """
    if sys.platform != 'win32' or '--worker' in sys.argv:
        return True
    try:
        import ctypes  # noqa: PLC0415
        ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
        return ctypes.windll.kernel32.GetLastError() != 183  # 183 = ERROR_ALREADY_EXISTS
    except Exception:
        return True  # 互斥量创建失败不应阻断启动
