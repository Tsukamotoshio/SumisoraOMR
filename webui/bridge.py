# webui/bridge.py — JS↔Python bridge (M0: echo + window controls + event push).
"""The ``js_api`` object exposed to the frontend as ``window.pywebview.api``.

M0 scope: prove the artery works both ways —
- frontend → Python: ``echo`` round-trip, window controls (frameless title bar);
- Python → frontend: ``push_event`` via ``evaluate_js`` dispatching a DOM
  ``CustomEvent`` (the pattern all later progress/log streaming will use).

Full protocol (files/convert/models/…) lands in M1; see migration plan §3.
"""
from __future__ import annotations

import json
import platform
from typing import Any, Optional

import webview


class Bridge:
    """window.pywebview.api implementation. One instance per window."""

    def __init__(self) -> None:
        self._window: Optional[webview.Window] = None
        self._maximized = False

    def attach(self, window: webview.Window) -> None:
        self._window = window

    # ── Python → 前端事件推送（后续进度/日志流的统一通道）─────────────────────
    def push_event(self, name: str, payload: Any = None) -> None:
        """Dispatch ``CustomEvent(name, {detail: payload})`` on the frontend window."""
        if self._window is None:
            return
        detail = json.dumps(payload, ensure_ascii=False)
        self._window.evaluate_js(
            f'window.dispatchEvent(new CustomEvent({json.dumps(name)}, {{detail: {detail}}}))'
        )

    # ── 前端可调用 API（M0）───────────────────────────────────────────────────
    def echo(self, value: Any) -> dict:
        """Round-trip test: returns the value plus Python-side context."""
        return {
            'echo': value,
            'python': platform.python_version(),
            'pywebview': webview.__dict__.get('__version__', ''),
        }

    def window_minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()

    def window_toggle_maximize(self) -> bool:
        """Toggle maximize/restore; returns the new maximized state."""
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
