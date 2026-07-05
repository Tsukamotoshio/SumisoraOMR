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
