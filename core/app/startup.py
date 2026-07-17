# core/app/startup.py — Framework-independent frozen startup steps.
"""Startup steps shared by the Flet shell (app.py) and the pywebview shell
(webui/main.py), so the packaged build behaves identically whichever shell is the
entry point. Nothing here touches a GUI toolkit — each shell owns its own UX
(message boxes, i18n) and calls these for the plumbing.
"""
from __future__ import annotations

import os
import sys

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
