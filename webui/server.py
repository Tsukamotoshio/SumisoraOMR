# webui/server.py — local static file server for the web UI.
"""Serve ``webui/static`` on 127.0.0.1 with COOP/COEP headers.

Why a server at all (instead of file://):
- noteDigger (M5) needs ``SharedArrayBuffer``, which browsers only enable in a
  cross-origin-isolated context — that requires the COOP/COEP response headers
  below, which file:// URLs cannot carry.
- A same-origin http origin also gives pdf.js / fetch / Worker a normal
  security context (file:// is riddled with special cases in Chromium).

The port is random (OS-assigned) and bound to 127.0.0.1 only.
"""
from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC_DIR = Path(__file__).parent / 'static'


class _IsolatedHandler(SimpleHTTPRequestHandler):
    """Static handler + cross-origin-isolation headers (SharedArrayBuffer 前置条件)."""

    def end_headers(self) -> None:  # noqa: D102
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')
        # 静态资源全部本地打包，禁缓存便于开发迭代（发布版可放开）。
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # noqa: D102
        pass  # 静默访问日志；错误仍会以异常形式浮出


def start_server(directory: Path = STATIC_DIR) -> tuple[ThreadingHTTPServer, str]:
    """Start the server on an OS-assigned port; returns (server, base_url).

    The server runs on a daemon thread and dies with the process; call
    ``server.shutdown()`` for an orderly stop.
    """
    handler = partial(_IsolatedHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f'http://127.0.0.1:{port}'
