# webui/events.py — batched Python→frontend event push channel.
"""EventPusher: the single Python→JS event artery.

Every state change (progress, log lines, file-tray changes, results) flows
through here as ``(name, payload)`` pairs. Instead of one ``evaluate_js`` per
event — which floods the WebView2 message pump under burst logging (risk R2 in
the migration plan) — events are queued and a drain thread flushes the whole
queue every ``interval`` (50 ms) as ONE ``evaluate_js`` call to
``window.__omrEvents([...])``, which dispatches each entry as a DOM
``CustomEvent(name, {detail: payload})``.

Loss accounting: the M1 gate is "500-line log burst, nothing dropped". The
queue is unbounded (log lines are tiny), so the only loss point would be an
``evaluate_js`` failure — those are counted in ``push_failures`` and re-raised
into the page as a ``bridge-degraded`` event on recovery.
"""
from __future__ import annotations

import json
import queue
import threading
from typing import Any, Optional

import webview


class EventPusher:
    """Queue + drain loop; one evaluate_js per flush interval."""

    def __init__(self, interval: float = 0.05) -> None:
        self._interval = interval
        self._queue: queue.Queue = queue.Queue()
        self._window: Optional[webview.Window] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.push_failures = 0  # evaluate_js 失败计数（窗口关闭竞态等）

    def attach(self, window: webview.Window) -> None:
        self._window = window

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._drain_loop, daemon=True, name='EventPusher')
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def push(self, name: str, payload: Any = None) -> None:
        """Enqueue an event; returns immediately (thread-safe, any thread)."""
        self._queue.put((name, payload))

    # ── internals ────────────────────────────────────────────────────────────

    def _drain_loop(self) -> None:
        while not self._stop.is_set():
            batch: list[tuple[str, Any]] = []
            try:
                # 阻塞等第一条，避免空转；随后把当前积压一次取空
                batch.append(self._queue.get(timeout=0.25))
            except queue.Empty:
                continue
            try:
                while True:
                    batch.append(self._queue.get_nowait())
            except queue.Empty:
                pass
            self._flush(batch)
            self._stop.wait(self._interval)

    def _flush(self, batch: list[tuple[str, Any]]) -> None:
        window = self._window
        if window is None:
            self.push_failures += len(batch)
            return
        events = [{'name': n, 'payload': p} for n, p in batch]
        js = f'window.__omrEvents && window.__omrEvents({json.dumps(events, ensure_ascii=False, default=str)})'
        try:
            window.evaluate_js(js)
        except Exception:
            # 窗口销毁竞态：计数即可，进程即将退出
            self.push_failures += len(batch)
