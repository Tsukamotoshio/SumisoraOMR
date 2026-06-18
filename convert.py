# 批量乐谱(PDF/JPG/PNG) -> 简谱 PDF 转换工具（TUI 入口）
# Batch sheet music (PDF/JPG/PNG) to numbered notation (jianpu) PDF converter.
# Pipeline: input image/PDF -> Audiveris (OMR) -> MusicXML -> music21 -> MIDI -> jianpu-ly -> LilyPond -> jianpu PDF
#
# Copyright (c) 2026 Tsukamotoshio. All rights reserved.
# SPDX-License-Identifier: AGPL-3.0-only
# See LICENSE file for full license text.
#
# 本文件是薄包装器（thin wrapper）。所有实现均位于 core/ 目录。
# This file is a thin wrapper; all logic lives in the core/ package.
#
# ┌──────────────────────────────────────────────────────────────────────────┐
# │ DEPRECATED — 此 TUI 入口已正式弃用，不再主动维护。                          │
# │ 请使用 GUI：`python app.py`。该文件保留仅供调试 / CI 的无头管线调用，       │
# │ 可能落后于 GUI 的功能与修复。                                              │
# │ This terminal entry point is deprecated and no longer actively maintained. │
# │ Use the GUI (`python app.py`) instead.                                     │
# └──────────────────────────────────────────────────────────────────────────┘

# ---------------------------------------------------------------------------
# Bootstrap: if core dependencies are missing (e.g. running with system Python
# instead of the project venv), try to re-launch with the venv interpreter so
# the user gets a working program rather than a cryptic ModuleNotFoundError.
# ---------------------------------------------------------------------------
import sys as _sys
import os as _os

def _bootstrap_venv() -> None:
    """Re-launch with the venv Python when dependencies are not importable."""
    try:
        import rich  # noqa: F401 — cheapest sentinel for "venv is active"
        return  # already in the right environment
    except ImportError:
        pass

    _here = _os.path.dirname(_os.path.abspath(__file__))
    # Candidate venv interpreters (Windows first, then Unix)
    candidates = [
        _os.path.join(_here, '.venv', 'Scripts', 'python.exe'),
        _os.path.join(_here, '.venv', 'bin', 'python'),
        _os.path.join(_here, 'venv', 'Scripts', 'python.exe'),
        _os.path.join(_here, 'venv', 'bin', 'python'),
    ]
    for _venv_py in candidates:
        if _os.path.isfile(_venv_py):
            import subprocess
            _result = subprocess.run([_venv_py] + _sys.argv)
            _sys.exit(_result.returncode)

    # No venv found — print a friendly message and exit
    print(
        "\n[错误] 无法找到虚拟环境，请先运行以下命令安装依赖：\n"
        "  python -m venv .venv\n"
        "  .venv\\Scripts\\activate   (Windows) 或  source .venv/bin/activate  (macOS/Linux)\n"
        "  pip install -r requirements.txt\n"
        "然后重新运行 convert.py。\n",
        file=_sys.stderr,
    )
    _sys.exit(1)

_bootstrap_venv()
# ---------------------------------------------------------------------------

from core.app.pipeline import main

_DEPRECATION_NOTICE = (
    "\n[弃用提示] 命令行(TUI)入口已正式弃用，不再主动维护，功能可能落后于 GUI。\n"
    "           请改用图形界面：python app.py\n"
)

if __name__ == '__main__':
    print(_DEPRECATION_NOTICE, file=_sys.stderr)
    main()