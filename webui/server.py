# webui/server.py — local static file server for the web UI.
"""Serve ``webui/static`` on 127.0.0.1 with COOP/COEP headers, plus a
whitelisted ``/file?path=...`` endpoint for previewing user files.

Why a server at all (instead of file://):
- noteDigger (M5) needs ``SharedArrayBuffer``, which browsers only enable in a
  cross-origin-isolated context — that requires the COOP/COEP response headers
  below, which file:// URLs cannot carry.
- A same-origin http origin also gives pdf.js / fetch / Worker a normal
  security context (file:// is riddled with special cases in Chromium).

Security: the server binds 127.0.0.1 on a random port, but any local process
could still hit it — so ``/file`` only serves paths the application has
explicitly whitelisted (files the user added to the tray). No directory
listing, no arbitrary path reads.
"""
from __future__ import annotations

import threading
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

STATIC_DIR = Path(__file__).parent / 'static'

_MIME = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.pdf': 'application/pdf',
    '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.flac': 'audio/flac', '.ogg': 'audio/ogg',
}


class FileWhitelist:
    """Thread-safe set of user files the /file endpoint may serve."""

    def __init__(self) -> None:
        self._paths: set[Path] = set()
        self._lock = threading.Lock()

    def allow(self, path: Path) -> None:
        with self._lock:
            self._paths.add(Path(path).resolve())

    def revoke(self, path: Path) -> None:
        with self._lock:
            self._paths.discard(Path(path).resolve())

    def is_allowed(self, path: Path) -> bool:
        with self._lock:
            return Path(path).resolve() in self._paths


class _IsolatedHandler(SimpleHTTPRequestHandler):
    """Static handler + COOP/COEP headers + whitelisted /file endpoint."""

    whitelist: FileWhitelist  # injected via subclass attr in start_server

    def end_headers(self) -> None:  # noqa: D102
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')
        # 静态资源全部本地打包，禁缓存便于开发迭代（发布版可放开）。
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def do_GET(self) -> None:  # noqa: D102
        if self.path.startswith('/file?'):
            self._serve_user_file()
            return
        super().do_GET()

    def _serve_user_file(self) -> None:
        query = urllib.parse.urlparse(self.path).query
        raw = urllib.parse.parse_qs(query).get('path', [''])[0]
        p = Path(raw) if raw else None
        if p is None or not self.whitelist.is_allowed(p) or not p.is_file():
            self.send_error(403, 'file not whitelisted')
            return
        mime = _MIME.get(p.suffix.lower(), 'application/octet-stream')
        try:
            data = p.read_bytes()
        except OSError:
            self.send_error(500, 'read failed')
            return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # noqa: D102
        pass  # 静默访问日志；错误仍会以异常形式浮出


def start_server(directory: Path = STATIC_DIR,
                 whitelist: Optional[FileWhitelist] = None,
                 ) -> tuple[ThreadingHTTPServer, str, FileWhitelist]:
    """Start the server on an OS-assigned port; returns (server, base_url, whitelist).

    The server runs on a daemon thread and dies with the process; call
    ``server.shutdown()`` for an orderly stop.
    """
    wl = whitelist or FileWhitelist()
    handler_cls = type('_Handler', (_IsolatedHandler,), {'whitelist': wl})
    handler_factory = partial(handler_cls, directory=str(directory))
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler_factory)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f'http://127.0.0.1:{port}', wl
