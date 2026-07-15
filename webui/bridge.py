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

import os
import platform
from pathlib import Path
from typing import Any, Optional

import webview

from .conversion import ConversionService
from .events import EventPusher
from .models import ModelsService
from .outputs import OutputsService, ScoresService
from .transpose import TransposeService


class Bridge:
    """window.pywebview.api implementation. One instance per window."""

    def __init__(self, pusher: EventPusher, conversion: ConversionService,
                 models: Optional[ModelsService] = None,
                 outputs: Optional[OutputsService] = None,
                 scores: Optional[ScoresService] = None,
                 transpose: Optional[TransposeService] = None) -> None:
        self._pusher = pusher
        self._conversion = conversion
        self._models = models
        self._outputs = outputs
        self._scores = scores
        self._transpose = transpose
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

    def app_info(self) -> dict:
        from core.config import APP_VERSION
        return {'version': APP_VERSION}

    # ── 文件托盘 ──────────────────────────────────────────────────────────────
    def files_list(self, view: Optional[str] = None) -> list:
        return self._conversion.files_list(view)

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

    # ── 模型管理 ─────────────────────────────────────────────────────────────
    def models_status(self) -> dict:
        return self._models.status() if self._models else {}

    def models_download(self, kind: str) -> dict:
        return self._models.download(kind) if self._models else {'ok': False, 'error': 'no service'}

    def models_cancel_download(self, kind: str) -> dict:
        return self._models.cancel_download(kind) if self._models else {'ok': False}

    def models_delete(self, kind: str) -> dict:
        return self._models.delete(kind) if self._models else {'ok': False, 'error': 'no service'}

    # ── 输出文件（预览页）────────────────────────────────────────────────────
    def outputs_list_jianpu(self) -> list:
        return self._outputs.list_jianpu() if self._outputs else []

    def outputs_delete(self, paths: list) -> dict:
        return self._outputs.delete(paths) if self._outputs else {'ok': False}

    def outputs_export(self, paths: list) -> dict:
        """Pick a folder via native dialog, then copy *paths* into it."""
        if self._outputs is None or self._window is None:
            return {'ok': False, 'error': 'no service'}
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return {'ok': False, 'error': 'cancelled'}
        dest = result[0] if isinstance(result, (list, tuple)) else result
        out = self._outputs.export_to(paths, str(dest))
        out['dest'] = str(dest)
        return out

    def outputs_play_midi(self, pdf_path: str) -> dict:
        return self._outputs.play_midi(pdf_path) if self._outputs else {'ok': False}

    def outputs_rerender(self, pdf_path: str) -> dict:
        return self._outputs.rerender(pdf_path) if self._outputs else {'ok': False}

    # ── 五线谱（xml-scores）──────────────────────────────────────────────────
    def scores_list(self) -> list:
        return self._scores.list_scores() if self._scores else []

    def scores_preview(self, path: str) -> dict:
        return self._scores.preview(path) if self._scores else {'ok': False}

    def scores_midi_for(self, path: str) -> dict:
        return self._scores.midi_for(path) if self._scores else {'exists': False}

    def scores_generate_play_midi(self, path: str) -> dict:
        return self._scores.generate_and_play_midi(path) if self._scores else {'ok': False}

    def scores_delete(self, paths: list) -> dict:
        return self._scores.delete(paths) if self._scores else {'ok': False}

    def scores_export(self, paths: list) -> dict:
        if self._scores is None or self._window is None:
            return {'ok': False, 'error': 'no service'}
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return {'ok': False, 'error': 'cancelled'}
        dest = result[0] if isinstance(result, (list, tuple)) else result
        return self._scores.export_to(paths, str(dest))

    # ── 移调 ─────────────────────────────────────────────────────────────────
    def transpose_options(self) -> dict:
        return self._transpose.options() if self._transpose else {}

    def transpose_load(self, path: str) -> dict:
        return self._transpose.load(path) if self._transpose else {'ok': False}

    def transpose_preview(self, which: str) -> dict:
        return self._transpose.render_preview(which) if self._transpose else {'ok': False}

    def transpose_run(self, mode: str, params: dict) -> dict:
        return self._transpose.run(mode, params or {}) if self._transpose else {'ok': False}

    def transpose_export(self, which: str) -> dict:
        """Save-as dialog for the original/transposed staff PDF, then render+copy."""
        if self._transpose is None or self._window is None:
            return {'ok': False, 'error': 'no service'}
        default = self._transpose.default_export_name(which)
        if default is None:
            return {'ok': False, 'error': 'no_file'}
        result = self._window.create_file_dialog(
            webview.FileDialog.SAVE, save_filename=default,
            file_types=('PDF (*.pdf)',),
        )
        if not result:
            return {'ok': False, 'error': 'cancelled'}
        dest = result[0] if isinstance(result, (list, tuple)) else result
        return self._transpose.export_to(which, str(dest))

    # ── 系统集成 ─────────────────────────────────────────────────────────────
    def shell_open_output_dir(self) -> dict:
        from core.app.backend import output_dir
        try:
            d = output_dir(None)
            d.mkdir(parents=True, exist_ok=True)
            os.startfile(str(d))  # noqa: S606 — 本地桌面应用打开自家输出目录
            return {'ok': True}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def shell_pick_files(self) -> list:
        """Native file-open dialog → add selections to the tray. Returns added paths."""
        if self._window is None:
            return []
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True,
            file_types=('乐谱/音频 (*.pdf;*.png;*.jpg;*.jpeg;*.mp3;*.wav;*.flac;*.ogg)',
                        'All files (*.*)'),
        )
        paths = [str(Path(p)) for p in (result or [])]
        if paths:
            self._conversion.files_add(paths)
        return paths

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
