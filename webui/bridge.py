# webui/bridge.py — JS↔Python bridge (window.pywebview.api).
"""The ``js_api`` object exposed to the frontend.

M1 surface (migration plan §3.1, conversion artery):
- ``files_*``   — file tray (list/add/remove/toggle_check)
- ``convert_*`` — start/cancel
- ``debug_*``   — gate test hooks (log flood, worker kill)
- ``window_*``  — frameless window controls
- ``echo``      — M0 round-trip check

pywebview exposes public methods flat as ``window.pywebview.api.<name>``;
domain grouping is by prefix. Python→frontend traffic does NOT go through
here — it flows via EventPusher (batched ``window.__omrEvents``).
"""
from __future__ import annotations

import platform
from typing import Any, Optional

import webview

from .conversion import ConversionService
from .events import EventPusher


class Bridge:
    """window.pywebview.api implementation. One instance per window."""

    def __init__(self, pusher: EventPusher, conversion: ConversionService) -> None:
        self._pusher = pusher
        self._conversion = conversion
        self._window: Optional[webview.Window] = None
        self._maximized = False

    def attach(self, window: webview.Window) -> None:
        self._window = window

    # ── Python → 前端（统一走批量通道）────────────────────────────────────────
    def push_event(self, name: str, payload: Any = None) -> None:
        self._pusher.push(name, payload)

    # ── M0：联通性 ────────────────────────────────────────────────────────────
    def echo(self, value: Any) -> dict:
        return {'echo': value, 'python': platform.python_version()}

    # ── 文件托盘 ──────────────────────────────────────────────────────────────
    def files_list(self) -> list:
        return self._conversion.files_list()

    def files_add(self, paths: list) -> dict:
        return self._conversion.files_add(paths)

    def files_remove(self, path: str) -> None:
        self._conversion.files_remove(path)

    def files_toggle_check(self, path: str) -> None:
        self._conversion.files_toggle_check(path)

    # ── 转换 ─────────────────────────────────────────────────────────────────
    def convert_start(self, opts: Optional[dict] = None) -> dict:
        return self._conversion.convert_start(opts)

    def convert_cancel(self) -> dict:
        return self._conversion.convert_cancel()

    # ── Gate 测试钩子 ─────────────────────────────────────────────────────────
    def debug_flood(self, n: int = 500) -> dict:
        return self._conversion.debug_flood(n)

    def debug_kill_worker(self) -> dict:
        return self._conversion.debug_kill_worker()

    def debug_worker_pids(self) -> list:
        return self._conversion.worker_pids()

    def debug_push_failures(self) -> int:
        return self._pusher.push_failures

    # ── 窗口控制（frameless 标题栏）──────────────────────────────────────────
    def window_minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()

    def window_toggle_maximize(self) -> bool:
        if self._window is None:
            return False
        if self._maximized:
            self._window.restore()
        else:
            self._window.maximize()
        self._maximized = not self._maximized
        return self._maximized

    def window_close(self) -> None:
        if self._window is not None:
            self._window.destroy()
