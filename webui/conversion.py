# webui/conversion.py — bridge-side conversion service (M1 artery).
"""Wires the existing ConversionRunner (gui/worker_launcher.py, kept as-is per
the migration plan §1.2) to the web frontend.

Design: **Python is the single source of truth.** This service owns the file
tray (pinned/checked) and an ``AppState`` instance that the runner reports
into; every state change is forwarded to the page through the batched
``EventPusher`` as 1:1 named events (``progress_update`` / ``log_line`` / …,
same names as the Flet ``Event`` enum values — migration plan §3.2). The
frontend only renders.

Notes vs. the Flet pages:
- ``AppState.add_file`` is image-only, so the tray lives here and accepts both
  score inputs and audio (``SUPPORTED_INPUT_SUFFIXES | SUPPORTED_AUDIO_SUFFIXES``).
- Cancel semantics match the Flet flow: ``cancel()`` → ``runner.terminate()``
  (taskkill /F /T on the worker tree). The runner may then surface a
  worker-crash ``progress_error`` — the frontend treats errors arriving in the
  'cancelling' state as the cancel confirmation, not a failure dialog.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

from core.config import SUPPORTED_AUDIO_SUFFIXES, SUPPORTED_INPUT_SUFFIXES
from gui.app_state import AppState, Event
from gui.worker_launcher import ConversionOptions, ConversionRunner

from .events import EventPusher
from .server import FileWhitelist

_ACCEPTED_SUFFIXES = SUPPORTED_INPUT_SUFFIXES | SUPPORTED_AUDIO_SUFFIXES


def _kind_of(path: Path) -> str:
    """'audio' or 'score' — the per-view routing key (plan §3.2 允许后缀思路)."""
    return 'audio' if path.suffix.lower() in SUPPORTED_AUDIO_SUFFIXES else 'score'

# AppState 事件 → 前端 CustomEvent 的直转清单（payload 已是 JSON 安全或经 _jsonify）
_FORWARDED_EVENTS = (
    Event.PROGRESS_UPDATE,
    Event.PROGRESS_DONE,
    Event.PROGRESS_ERROR,
    Event.LOG_LINE,
    Event.MXL_READY,
)


def _jsonify(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [_jsonify(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    return value


class ConversionService:
    """File tray + conversion lifecycle, reported to the page via EventPusher."""

    def __init__(self, pusher: EventPusher,
                 whitelist: Optional[FileWhitelist] = None) -> None:
        self._pusher = pusher
        self._whitelist = whitelist  # /file 预览端点的白名单（托盘内文件才可读）
        self._state = AppState()
        self._runner = ConversionRunner(
            self._state,
            on_sub_progress=self._on_sub_progress,
            on_finished=self._on_finished,
        )
        self._tray: list[dict] = []  # [{path: Path, checked: bool}]
        self._tray_lock = threading.Lock()
        self._run_thread: Optional[threading.Thread] = None
        for name in _FORWARDED_EVENTS:
            self._state.on(name, self._make_forwarder(name))

    # ── AppState → 前端 ──────────────────────────────────────────────────────

    def _make_forwarder(self, name: str):
        def _forward(**kwargs: Any) -> None:
            self._pusher.push(name, _jsonify(kwargs))
        return _forward

    def _on_sub_progress(self, value: float, message: str) -> None:
        self._pusher.push('sub_progress', {'value': value, 'message': message})

    def _on_finished(self) -> None:
        """Runner 收尾（无论成败/取消）：把汇总与终态一次性推给前端。"""
        self._pusher.push('conversion_finished', {
            'summary': _jsonify(self._state.conversion_summary),
            'is_processing': self._state.is_processing,
        })

    def _entry_dict(self, e: dict) -> dict:
        return {
            'path': str(e['path']),
            'name': e['path'].name,
            'checked': e['checked'],
            'kind': _kind_of(e['path']),
        }

    def _push_tray(self) -> None:
        with self._tray_lock:
            files = [self._entry_dict(e) for e in self._tray]
        self._pusher.push('files_changed', {'files': files})

    # ── 前端可调用面（经 Bridge 转发）────────────────────────────────────────

    def files_list(self, view: Optional[str] = None) -> list[dict]:
        """Tray entries; *view* ('score'|'audio') filters by kind."""
        with self._tray_lock:
            entries = [self._entry_dict(e) for e in self._tray]
        if view in ('score', 'audio'):
            entries = [e for e in entries if e['kind'] == view]
        return entries

    def files_add(self, paths: list[str]) -> dict:
        """Add files to the tray (dedup; unsupported suffixes rejected)."""
        added, rejected = [], []
        with self._tray_lock:
            known = {e['path'] for e in self._tray}
            for raw in paths or []:
                p = Path(raw).resolve()
                if p.suffix.lower() not in _ACCEPTED_SUFFIXES or not p.is_file():
                    rejected.append(str(p))
                    continue
                if p in known:
                    continue
                self._tray.append({'path': p, 'checked': True})
                known.add(p)
                added.append(str(p))
                if self._whitelist is not None:
                    self._whitelist.allow(p)
        self._push_tray()
        return {'added': added, 'rejected': rejected}

    def files_remove(self, path: str) -> None:
        p = Path(path).resolve()
        with self._tray_lock:
            self._tray = [e for e in self._tray if e['path'] != p]
        if self._whitelist is not None:
            self._whitelist.revoke(p)
        self._push_tray()

    def files_toggle_check(self, path: str) -> None:
        p = Path(path).resolve()
        with self._tray_lock:
            for e in self._tray:
                if e['path'] == p:
                    e['checked'] = not e['checked']
        self._push_tray()

    def convert_start(self, opts: Optional[dict] = None) -> dict:
        """Start converting the checked files on a background thread.

        opts.view ('score'|'audio') restricts to that page's files — the tray
        is shared across pages, per-view filtering happens here (Python side,
        plan §3.2).
        """
        opts = opts or {}
        if self._state.is_processing:
            return {'ok': False, 'error': 'busy'}
        view = opts.get('view')
        with self._tray_lock:
            files = [
                e['path'] for e in self._tray
                if e['checked'] and (view not in ('score', 'audio') or _kind_of(e['path']) == view)
            ]
        if not files:
            return {'ok': False, 'error': 'no_files'}
        conv_opts = ConversionOptions(
            engine=opts.get('engine', 'auto'),
            sr_engine=opts.get('sr_engine', 'waifu2x'),
            gen_midi=bool(opts.get('gen_midi', True)),
            melody_only=bool(opts.get('melody_only', False)),
            skip_dup=False,
            dup_files=[],
        )
        n_workers = max(1, int(opts.get('parallel', 1)))
        self._state.is_processing = True
        self._run_thread = threading.Thread(
            target=self._runner.run, args=(files, n_workers, conv_opts),
            daemon=True, name='ConversionRun',
        )
        self._run_thread.start()
        return {'ok': True, 'count': len(files)}

    def convert_cancel(self) -> dict:
        """Kill the worker process tree; frontend resets on the follow-up events."""
        self._runner.terminate()
        return {'ok': True}

    def shutdown(self) -> None:
        """Window is closing: same clean-sweep as the Flet shell (gate 4)."""
        try:
            self._runner.terminate()
        except Exception:
            pass

    # ── Gate 测试钩子（debug 卡片用；不进正式协议）───────────────────────────

    def debug_flood(self, n: int = 500) -> dict:
        """Gate 1: burst n log lines through the real append_log → pusher path."""
        def _flood() -> None:
            for i in range(int(n)):
                self._state.append_log(f'[flood] line {i + 1}/{n}')
            self._pusher.push('flood_done', {'sent': int(n)})
        threading.Thread(target=_flood, daemon=True).start()
        return {'ok': True}

    def debug_kill_worker(self) -> dict:
        """Gate 3: kill only the worker process (no /T), simulating a crash."""
        procs = list(self._runner.procs)
        if not procs:
            return {'ok': False, 'error': 'no_worker'}
        for p in procs:
            try:
                p.kill()  # 只杀 worker 本体，不动子进程 —— 最接近真实崩溃
            except Exception:
                pass
        return {'ok': True, 'killed': [p.pid for p in procs]}

    def worker_pids(self) -> list[int]:
        return [p.pid for p in self._runner.procs if p.poll() is None]
