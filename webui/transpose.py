"""Mirrors the Flet transposer_page:

- three modes: 'key' (按调), 'interval' (按音程/半音), 'diatonic' (按度数/全音),
  dispatching to core.notation.transposer's three entry points;
- key auto-detection via ``detect_key_from_musicxml``;
- previews are staff PDFs rendered with the same flat-cache pattern as the
  score preview page (``build/<stem>_orig_preview.pdf`` / ``_trans_preview``);
- export renders the staff PDF and copies to a user-picked destination.

Transposition runs on a background thread; progress and completion flow to
the page as ``transpose_progress`` / ``transpose_done`` events. The transposed
MusicXML lands in build/ with the same suffixed naming as the Flet page.
"""
from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import Optional

from core.app.backend import build_dir
from core.notation.transposer import (
    DIATONIC_DEGREES,
    INTERVALS,
    detect_key_from_musicxml,
    key_display_cn,
)
from core.utils import log_message

from .events import EventPusher
from .server import FileWhitelist

# 按五度圈排列的调名（与 Flet 页一致）
KEYS = ['Cb', 'Gb', 'Db', 'Ab', 'Eb', 'Bb', 'F', 'C', 'G', 'D', 'A', 'E', 'B', 'F#', 'C#']


class TransposeService:
    """Transposition lifecycle for the web shell."""

    def __init__(self, pusher: EventPusher, whitelist: FileWhitelist) -> None:
        self._pusher = pusher
        self._whitelist = whitelist
        self._render_lock = threading.Lock()
        self._orig: Optional[Path] = None
        self._transposed: Optional[Path] = None
        # 两个独立代际：_file_gen 仅在 load() 换文件时递增（作废旧渲染回调）；
        # _run_token 仅门控移调任务的 progress/done。移调不作废预览渲染——
        # 否则「原谱渲染中就点移调」会让原谱面板永远停在渲染中。
        self._file_gen = 0
        self._run_token = 0

    # ── 常量表（前端下拉框）──────────────────────────────────────────────────

    def options(self) -> dict:
        return {
            'keys': [{'value': k, 'label': k.replace('#', '♯').replace('b', '♭')} for k in KEYS],
            'intervals': [iv.name for iv in INTERVALS],
            'degrees': [name for name, _ in DIATONIC_DEGREES],
        }

    # ── 载入 + 键检测 ────────────────────────────────────────────────────────

    def load(self, path: str) -> dict:
        """Set the original MXL; returns detected key. Preview render is a
        separate call (frontend drives it so the placeholder can show first)."""
        p = Path(path)
        if not p.is_file():
            return {'ok': False, 'error': 'not_found'}
        self._orig = p
        self._transposed = None
        self._file_gen += 1   # 作废上一个文件的渲染回调
        self._run_token += 1  # 作废上一个文件的移调任务
        key = 'C'
        try:
            key = detect_key_from_musicxml(p)
        except Exception as exc:  # noqa: BLE001 — 检测失败回退 C，不阻断
            log_message(f'[webui] 键检测失败：{exc}', logging.WARNING)
        return {'ok': True, 'path': str(p), 'name': p.name,
                'key': key, 'key_cn': key_display_cn(key)}

    # ── 预览渲染（orig / transposed 共用；完成推 transpose_preview_ready）────

    def render_preview(self, which: str) -> dict:
        mxl = self._orig if which == 'orig' else self._transposed
        if mxl is None:
            return {'ok': False, 'error': 'no_file'}
        suffix = '_orig_preview' if which == 'orig' else '_trans_preview'
        flat = build_dir() / f'{mxl.stem}{suffix}.pdf'
        if flat.exists() and flat.stat().st_mtime >= mxl.stat().st_mtime:
            self._whitelist.allow(flat)
            return {'ok': True, 'pdf': str(flat)}
        if which == 'orig':
            # 复用五线谱预览页的扁平缓存（同一 MXL 同一渲染产物）——用户几乎总是
            # 从五线谱页进入移调页，刚看过的谱子无需再跑一次 LilyPond。
            score_cache = build_dir() / f'_score_preview_{mxl.stem}.pdf'
            if score_cache.exists() and score_cache.stat().st_mtime >= mxl.stat().st_mtime:
                self._whitelist.allow(score_cache)
                return {'ok': True, 'pdf': str(score_cache)}

        token = self._file_gen

        def _work() -> None:
            error = None
            with self._render_lock:
                try:
                    import tempfile
                    from core.render.lilypond_runner import render_musicxml_staff_pdf
                    with tempfile.TemporaryDirectory(
                            prefix=f'_trans_render_{mxl.stem}_', dir=str(build_dir())) as td:
                        pdf = render_musicxml_staff_pdf(mxl, Path(td))
                        if pdf and pdf.exists():
                            shutil.copy2(str(pdf), str(flat))
                        else:
                            error = '五线谱渲染失败'
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
            if token != self._file_gen:
                return  # 页面已切换文件，丢弃
            if error is None:
                self._whitelist.allow(flat)
            self._pusher.push('transpose_preview_ready', {
                'which': which, 'pdf': str(flat) if error is None else None,
                'ok': error is None, 'error': error,
            })

        threading.Thread(target=_work, daemon=True, name=f'trans-render-{which}').start()
        return {'ok': True, 'started': True}

    # ── 移调 ─────────────────────────────────────────────────────────────────

    def run(self, mode: str, params: dict) -> dict:
        """Start a transposition; completion → 'transpose_done' event."""
        if self._orig is None:
            return {'ok': False, 'error': 'no_file'}
        src = self._orig
        self._run_token += 1
        token = self._run_token

        def _progress(v: float) -> None:
            if token == self._run_token:
                self._pusher.push('transpose_progress', {'value': v})

        def _work() -> None:
            error = None
            dst: Optional[Path] = None
            try:
                from core.notation.transposer import (
                    transpose_by_interval, transpose_diatonic, transpose_musicxml,
                )
                base = build_dir()
                stem = src.stem
                if mode == 'interval':
                    name = params.get('interval', '大二度')
                    direction = params.get('direction', 'up')
                    dir_tag = {'up': 'up', 'down': 'dn', 'closest': 'cl'}.get(direction, 'up')
                    dst = base / f'{stem}_T{name}_{dir_tag}.musicxml'
                    dst = transpose_by_interval(
                        src, dst, interval_name=name, direction=direction,
                        transpose_key_sig=bool(params.get('keysig', True)),
                        progress_callback=_progress)
                elif mode == 'diatonic':
                    degree = params.get('degree', '二度')
                    direction = params.get('direction', 'up')
                    dst = base / f'{stem}_Tdiat_{degree}_{"up" if direction == "up" else "dn"}.musicxml'
                    dst = transpose_diatonic(
                        src, dst, degree_name=degree, direction=direction,
                        progress_callback=_progress)
                else:  # key
                    to_key = params.get('to_key', 'C')
                    dst = base / f'{stem}_transposed_{to_key}.musicxml'
                    dst = transpose_musicxml(
                        src, dst,
                        from_key=params.get('from_key', 'C'), to_key=to_key,
                        direction=params.get('direction', 'closest'),
                        transpose_key_sig=bool(params.get('keysig', True)),
                        progress_callback=_progress)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            if token != self._run_token:
                return
            if error is None and dst is not None:
                self._transposed = Path(dst)
            self._pusher.push('transpose_done', {
                'ok': error is None,
                'name': Path(dst).name if (error is None and dst is not None) else None,
                'error': error,
            })

        threading.Thread(target=_work, daemon=True, name='transpose').start()
        return {'ok': True, 'started': True}

    # ── 导出（渲染五线谱 PDF → 复制到目标路径；由 Bridge 弹保存对话框）───────

    def default_export_name(self, which: str) -> Optional[str]:
        mxl = self._orig if which == 'orig' else self._transposed
        return f'{mxl.stem}_staff.pdf' if mxl is not None else None

    def export_to(self, which: str, dest_path: str) -> dict:
        mxl = self._orig if which == 'orig' else self._transposed
        if mxl is None:
            return {'ok': False, 'error': 'no_file'}
        try:
            import tempfile
            from core.render.lilypond_runner import render_musicxml_staff_pdf
            suffix = '_orig_preview' if which == 'orig' else '_trans_preview'
            flat = build_dir() / f'{mxl.stem}{suffix}.pdf'
            if not (flat.exists() and flat.stat().st_mtime >= mxl.stat().st_mtime):
                with self._render_lock, tempfile.TemporaryDirectory(
                        prefix=f'_trans_export_{mxl.stem}_', dir=str(build_dir())) as td:
                    pdf = render_musicxml_staff_pdf(mxl, Path(td))
                    if not (pdf and pdf.exists()):
                        return {'ok': False, 'error': '渲染失败'}
                    shutil.copy2(str(pdf), str(flat))
            shutil.copy2(str(flat), dest_path)
            return {'ok': True, 'dest': dest_path}
        except Exception as exc:  # noqa: BLE001
            return {'ok': False, 'error': str(exc)}
