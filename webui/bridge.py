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
from .editor import EditorService
from .models import ModelsService
from .notedigger import NoteDiggerService
from .outputs import OutputsService, ScoresService
from .transpose import TransposeService


class Bridge:
    """window.pywebview.api implementation. One instance per window."""

    def __init__(self, pusher: EventPusher, conversion: ConversionService,
                 models: Optional[ModelsService] = None,
                 outputs: Optional[OutputsService] = None,
                 scores: Optional[ScoresService] = None,
                 transpose: Optional[TransposeService] = None,
                 editor: Optional[EditorService] = None,
                 notedigger: Optional[NoteDiggerService] = None) -> None:
        self._pusher = pusher
        self._conversion = conversion
        self._models = models
        self._outputs = outputs
        self._scores = scores
        self._transpose = transpose
        self._editor = editor
        self._notedigger = notedigger
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

    # ── i18n ─────────────────────────────────────────────────────────────────
    def i18n_catalog(self) -> dict:
        """Full string catalog + current language (single source: Python)."""
        from gui.strings import get_language

        from .i18n import merged_catalog
        return {'lang': get_language(), 'strings': merged_catalog()}

    def i18n_set_language(self, lang: str) -> dict:
        """Switch UI language; also flips gui.strings so worker-side summary
        text (ConversionRunner uses t()) follows, and persists the choice."""
        if lang not in ('zh', 'en'):
            return {'ok': False}
        from gui.settings import set_saved_language
        from gui.strings import set_language
        set_language(lang)
        set_saved_language(lang)
        return {'ok': True, 'lang': lang}

    def shell_open_url(self, url: str) -> None:
        """Open a URL in the default browser (about page homepage link)."""
        import webbrowser
        if url.startswith(('https://', 'http://')):
            webbrowser.open_new_tab(url)

    def about_copy_diagnostics(self) -> dict:
        """Collect environment diagnostics and copy to clipboard (may take a
        few seconds — collection can import onnxruntime)."""
        try:
            from core.app.diagnostics import collect_diagnostics, copy_to_clipboard
            ok = copy_to_clipboard(collect_diagnostics())
            return {'ok': ok, 'error': None if ok else 'clipboard'}
        except Exception as exc:  # noqa: BLE001
            return {'ok': False, 'error': str(exc)}

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

    # ── noteDigger 扒谱编辑器：导出 MIDI → 简谱（M5-③-3）────────────────────────
    def notedigger_generate_jianpu(self, name: str, b64: str) -> dict:
        if self._notedigger is None:
            return {'started': False, 'error': 'unavailable'}
        return self._notedigger.generate_jianpu(name, b64)

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

    def transpose_pick_file(self) -> dict:
        """Native open dialog for a MusicXML → transpose_load it. 与 Flet
        transposer 的「打开乐谱」等价（不限于 xml-scores 目录）。"""
        if self._transpose is None or self._window is None:
            return {'ok': False, 'error': 'no service'}
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=False,
            file_types=('MusicXML (*.mxl;*.xml;*.musicxml)', 'All files (*.*)'),
        )
        if not result:
            return {'ok': False, 'error': 'cancelled'}
        path = result[0] if isinstance(result, (list, tuple)) else result
        return self._transpose.load(str(path))

    def shell_open_xml_dir(self) -> dict:
        from core.app.backend import xml_scores_dir
        try:
            d = xml_scores_dir()
            d.mkdir(parents=True, exist_ok=True)
            os.startfile(str(d))  # noqa: S606
            return {'ok': True}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

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

    # ── 简谱编辑器 ───────────────────────────────────────────────────────────
    def editor_load(self, txt_path: str) -> dict:
        return self._editor.load(txt_path) if self._editor else {'ok': False}

    def editor_load_for_pdf(self, pdf_path: str) -> dict:
        return self._editor.load_for_pdf(pdf_path) if self._editor else {'ok': False}

    def editor_save(self, body: str) -> dict:
        return self._editor.save(body) if self._editor else {'ok': False}

    def editor_render_preview(self, body: Optional[str] = None) -> dict:
        return self._editor.render_preview(body) if self._editor else {'ok': False}

    def editor_export_to_output(self) -> dict:
        return self._editor.export_to_output() if self._editor else {'ok': False}

    def editor_pick_open(self) -> dict:
        """Native open dialog for a .jianpu.txt → load it."""
        if self._editor is None or self._window is None:
            return {'ok': False, 'error': 'no service'}
        from core.app.backend import editor_workspace_dir
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=False,
            directory=str(editor_workspace_dir()),
            file_types=('简谱文本 (*.jianpu.txt;*.txt)', 'All files (*.*)'),
        )
        if not result:
            return {'ok': False, 'error': 'cancelled'}
        path = result[0] if isinstance(result, (list, tuple)) else result
        return self._editor.load(str(path))

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

    def shell_pick_files(self, kind: Optional[str] = None) -> list:
        """Native file-open dialog → add selections to the tray. Returns added paths.

        kind: 'score' → 图片/PDF；'audio' → 音频；否则两者合并。
        注意：pywebview 的 file_types 描述必须匹配 ``^[\\w ]+\\(...\\)$``——描述里不能
        含 ``/`` 等非 \\w 字符，否则 create_file_dialog 抛 ValueError、对话框根本不弹。
        """
        if self._window is None:
            return []
        if kind == 'score':
            file_types = ('图片乐谱 (*.pdf;*.png;*.jpg;*.jpeg)', 'All files (*.*)')
        elif kind == 'audio':
            file_types = ('音频 (*.mp3;*.wav;*.flac;*.ogg)', 'All files (*.*)')
        else:
            file_types = ('乐谱与音频 (*.pdf;*.png;*.jpg;*.jpeg;*.mp3;*.wav;*.flac;*.ogg)',
                          'All files (*.*)')
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True, file_types=file_types)
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
        """Toggle maximize; returns new maximized state (前端据此换图标)."""
        if self._window is None:
            return False
        self._set_maximized(not self._maximized)
        return self._maximized

    def _set_maximized(self, maxed: bool) -> None:
        if self._window is None or maxed == self._maximized:
            return
        if maxed:
            # 无边框 form（FormBorderStyle.None）最大化默认盖满整屏、遮住任务栏。
            # 把 MaximizedBounds 限到窗口所在屏的工作区（排除任务栏）后再最大化；
            # 拿不到原生 form 时回退 pywebview 默认 maximize()。
            if not self._maximize_to_workarea():
                self._window.maximize()
        else:
            self._window.restore()
        self._maximized = maxed

    def _maximize_to_workarea(self) -> bool:
        """Maximize within the current monitor's work area (taskbar preserved).

        Returns False if the native WinForms form isn't reachable, so the caller
        can fall back to the default full-screen maximize.
        """
        form = getattr(self._window, 'native', None)
        if form is None:
            return False
        try:
            from System import Action  # noqa: PLC0415 — pythonnet, loaded by pywebview
            from System.Windows.Forms import FormWindowState, Screen  # noqa: PLC0415

            def _apply() -> None:
                # WorkingArea 按窗口当前所在屏取（多屏正确），排除任务栏区域
                form.MaximizedBounds = Screen.FromControl(form).WorkingArea
                form.WindowState = FormWindowState.Maximized

            form.Invoke(Action(_apply))
            return True
        except Exception:
            return False

    def window_is_maximized(self) -> bool:
        return self._maximized

    def window_close(self) -> None:
        if self._window is not None:
            self._window.destroy()

    # ── frameless 边缘拖拽调整大小 ──────────────────────────────────────────
    # WinForms 的 FormBorderStyle=None 无边框窗口**不响应** WM_SYSCOMMAND SC_SIZE，
    # 所以改为直接调 pywebview 线程安全的 resize()，用 fix_point 固定与拖动边相对的
    # 角 → 单次 resize 完成，无需 move()、无闪烁。begin 记录起始矩形，move 传屏幕位移。
    def window_resize_begin(self) -> dict:
        if self._window is None:
            return {}
        if self._maximized:
            self._set_maximized(False)   # 最大化态下开始拖拽 → 先还原
        # 逻辑（DIP）尺寸——与 window.resize() 和 JS screenX/Y 同单位。误用
        # native.Width（物理像素）会在高 DPI 下按缩放比放大（150% → 1.5×）。
        self._resize_start = {'w': int(self._window.width), 'h': int(self._window.height)}
        return {'maximized': False}

    def window_resize_edge(self, direction: str, dx: int, dy: int) -> None:
        if self._window is None or getattr(self, '_resize_start', None) is None:
            return
        from webview.window import FixPoint as FP
        r = self._resize_start
        min_w, min_h = 760, 520
        w = r['w'] - dx if 'left' in direction else (r['w'] + dx if 'right' in direction else r['w'])
        h = r['h'] - dy if 'top' in direction else (r['h'] + dy if 'bottom' in direction else r['h'])
        w = max(min_w, int(w))
        h = max(min_h, int(h))
        fp = (FP.EAST if 'left' in direction else FP.WEST) \
            | (FP.SOUTH if 'top' in direction else FP.NORTH)
        try:
            self._window.resize(w, h, fix_point=fp)
        except Exception:
            pass
