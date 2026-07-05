# core/app/diagnostics.py — collect environment info for bug reports (P3-2).
#
# Module-level imports are stdlib-only so importing this never pulls onnxruntime/
# music21/flet into the GUI process. Heavy probes (onnxruntime providers, package
# versions) are done lazily inside collect_diagnostics(), each guarded so one
# missing/broken dependency never breaks the whole report. Listing ONNX providers
# does NOT create a session, so it triggers no CUDA init.

from __future__ import annotations

import os
import platform
import sys


def _pkg_version(mod_name: str) -> str:
    try:
        from importlib.metadata import version
        return version(mod_name)
    except Exception:
        return 'not installed'


def _onnx_info() -> str:
    try:
        import onnxruntime as ort
        provs = ort.get_available_providers()  # 仅列举，不建 session → 无 CUDA 初始化
        return f'{ort.__version__} providers={provs}'
    except Exception as exc:
        return f'unavailable ({exc.__class__.__name__})'


def _latest_log_path() -> str:
    try:
        from ..utils import _get_logs_dir
        from .backend import app_base_dir
        logs_dir = _get_logs_dir(app_base_dir())
        logs = sorted(logs_dir.glob('convert-*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
        return str(logs[0]) if logs else f'(none in {logs_dir})'
    except Exception as exc:
        return f'unavailable ({exc.__class__.__name__})'


def _homr_models_state() -> str:
    try:
        from .backend import models_dir
        md = models_dir()
        onnx = list(md.glob('*.onnx'))
        return f'{len(onnx)} weight file(s) in {md}' if onnx else f'not downloaded ({md})'
    except Exception as exc:
        return f'unavailable ({exc.__class__.__name__})'


def copy_to_clipboard(text: str) -> bool:
    """Put *text* on the OS clipboard. Windows via the native Win32 API; True on success.

    Flet 0.85's page.clipboard is a deprecated service that must be mounted before
    use (an unmounted one errors with "inexistent control"), so we set the OS
    clipboard directly instead — self-contained, no service-lifecycle timing.
    ctypes argtypes are declared explicitly for 64-bit / Python 3.14 pointer safety.
    Returns False on non-Windows or any failure (caller shows a copy-failed toast).
    """
    if sys.platform != 'win32':
        return False
    import ctypes
    from ctypes import wintypes

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    k.GlobalAlloc.restype = wintypes.HGLOBAL
    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    u.OpenClipboard.argtypes = [wintypes.HWND]
    u.SetClipboardData.restype = wintypes.HANDLE
    u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

    try:
        if not u.OpenClipboard(None):
            return False
        try:
            u.EmptyClipboard()
            data = text.encode('utf-16-le') + b'\x00\x00'
            handle = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                return False
            ptr = k.GlobalLock(handle)
            if not ptr:
                return False
            ctypes.memmove(ptr, data, len(data))
            k.GlobalUnlock(handle)
            # SetClipboardData 成功后由系统接管 handle，不再由本进程释放
            return bool(u.SetClipboardData(CF_UNICODETEXT, handle))
        finally:
            u.CloseClipboard()
    except Exception:
        return False


def collect_diagnostics() -> str:
    """Return a plaintext environment report for pasting into a bug report.

    Safe to call from a background thread on user action; every probe is
    individually guarded so a broken dependency degrades to a note rather than
    raising.
    """
    try:
        from ..config import APP_VERSION
    except Exception:
        APP_VERSION = '?'

    lines = [
        '===== SumisoraOMR 诊断信息 =====',
        f'App version : {APP_VERSION}',
        f'OS          : {platform.platform()}',
        f'Python      : {sys.version.split()[0]} ({platform.machine()})',
        f'CPU cores   : {os.cpu_count()}',
        f'Executable  : {sys.executable}',
        f'Frozen      : {bool(getattr(sys, "frozen", False))}',
        '',
        f'onnxruntime : {_onnx_info()}',
        f'music21     : {_pkg_version("music21")}',
        f'PyMuPDF     : {_pkg_version("PyMuPDF")}',
        f'flet        : {_pkg_version("flet")}',
        '',
        f'HOMR models : {_homr_models_state()}',
        f'Latest log  : {_latest_log_path()}',
        '================================',
    ]
    return '\n'.join(lines)
