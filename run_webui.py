# run_webui.py — Frozen (PyInstaller) entry stub for the pywebview shell.
"""PyInstaller entry point for the packaged build.

``webui/main.py`` uses package-relative imports (``from .bridge import ...``) so
it cannot be a PyInstaller entry directly; this top-level stub dispatches to it.

Two roles, mirroring app.py's frozen entry:
  * ``SumisoraOMR.exe``            → launch the pywebview shell (webui.main.main).
  * ``SumisoraOMR.exe --worker``   → run the conversion worker (JSON-over-stdout
    IPC), spawned by gui.worker_launcher.build_worker_cmd() in frozen builds.

The ``--worker`` branch must run **before** importing webui.main, which pulls in
pywebview/WebView2 — a worker has no GUI and must not load it.
"""
import sys

# 打包版早期环境/stdio 设置（线程封顶；worker 保留 stdout 作 IPC）——须在重导入前。
from core.app.startup import early_frozen_setup

early_frozen_setup()


if __name__ == '__main__':
    if '--worker' in sys.argv:
        import multiprocessing
        multiprocessing.freeze_support()
        from core.omr.worker_main import run_worker
        run_worker()
        import os
        os._exit(0)   # 强制退出：绕过 onnxruntime 等库遗留的非 daemon 线程
    from webui.main import main
    main()
