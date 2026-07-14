# webui/outputs.py — output-file service (jianpu/staff PDFs in Output/).
"""List / delete / export / play-MIDI / re-render for converted outputs.

Mirrors the Flet jianpu_preview_page behaviours 1:1:
- an output set is ``<stem>_jianpu.pdf`` + optional ``Output/<stem>.mid`` +
  optional ``editor-workspace/<stem>.jianpu.txt``;
- delete removes all three;
- re-render runs ``<stem>.jianpu.txt`` → jianpu-ly → LilyPond and overwrites
  the PDF (background thread, result pushed as ``rerender_done``).

Listed PDFs are whitelisted for the /file endpoint so pdf.js can fetch them.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Optional

from core.app.backend import editor_workspace_dir, output_dir
from core.utils import log_message

from .events import EventPusher
from .server import FileWhitelist

_JIANPU_SUFFIX = '_jianpu.pdf'


class OutputsService:
    """Converted-output management for the preview pages."""

    def __init__(self, pusher: EventPusher, whitelist: FileWhitelist) -> None:
        self._pusher = pusher
        self._whitelist = whitelist

    # ── 列表 ─────────────────────────────────────────────────────────────────

    def list_jianpu(self) -> list[dict]:
        """All Output/*_jianpu.pdf, newest first; whitelists them for /file."""
        out = output_dir(None)
        ws = editor_workspace_dir()
        entries: list[dict] = []
        try:
            pdfs = sorted(out.glob(f'*{_JIANPU_SUFFIX}'),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            pdfs = []
        for pdf in pdfs:
            stem = pdf.name[:-len(_JIANPU_SUFFIX)]
            self._whitelist.allow(pdf)
            entries.append({
                'path': str(pdf),
                'name': pdf.name,
                'stem': stem,
                'mtime': int(pdf.stat().st_mtime),
                'has_midi': (out / f'{stem}.mid').exists(),
                'has_txt': (ws / f'{stem}.jianpu.txt').exists(),
            })
        return entries

    # ── 删除（与 Flet 版一致：PDF + .mid + .jianpu.txt 三件套）───────────────

    def delete(self, paths: list[str]) -> dict:
        out = output_dir(None)
        ws = editor_workspace_dir()
        removed = 0
        for raw in paths or []:
            pdf = Path(raw)
            if pdf.suffix.lower() != '.pdf' or not pdf.name.endswith(_JIANPU_SUFFIX):
                continue
            stem = pdf.name[:-len(_JIANPU_SUFFIX)]
            try:
                pdf.unlink(missing_ok=True)
                removed += 1
            except OSError as exc:
                log_message(f'[webui] 删除失败 {pdf.name}：{exc}', logging.WARNING)
                continue
            self._whitelist.revoke(pdf)
            for sibling in (out / f'{stem}.mid', ws / f'{stem}.jianpu.txt'):
                try:
                    sibling.unlink(missing_ok=True)
                except OSError:
                    pass
        return {'ok': True, 'removed': removed}

    # ── 导出（复制勾选文件到目标目录；目录由 Bridge 弹原生对话框选定）────────

    def export_to(self, paths: list[str], dest_dir: str) -> dict:
        dest = Path(dest_dir)
        if not dest.is_dir():
            return {'ok': False, 'error': 'dest not a directory'}
        copied, failed = [], []
        for raw in paths or []:
            src = Path(raw)
            try:
                shutil.copy2(src, dest / src.name)
                copied.append(src.name)
            except OSError as exc:
                failed.append({'file': src.name, 'error': str(exc)})
        return {'ok': not failed, 'copied': copied, 'failed': failed}

    # ── MIDI ─────────────────────────────────────────────────────────────────

    def play_midi(self, pdf_path: str) -> dict:
        stem = Path(pdf_path).name
        if stem.endswith(_JIANPU_SUFFIX):
            stem = stem[:-len(_JIANPU_SUFFIX)]
        midi = output_dir(None) / f'{stem}.mid'
        if not midi.exists():
            return {'ok': False, 'error': 'not_found', 'name': midi.name}
        try:
            os.startfile(str(midi))  # noqa: S606 — 用系统默认播放器打开自家输出
            return {'ok': True}
        except OSError as exc:
            return {'ok': False, 'error': str(exc)}

    # ── 从 .jianpu.txt 重渲（后台线程；结果推 rerender_done）─────────────────

    def rerender(self, pdf_path: str) -> dict:
        pdf = Path(pdf_path)
        stem = pdf.name[:-len(_JIANPU_SUFFIX)] if pdf.name.endswith(_JIANPU_SUFFIX) else pdf.stem
        txt = editor_workspace_dir() / f'{stem}.jianpu.txt'
        if not txt.exists():
            return {'ok': False, 'error': 'no_txt', 'name': txt.name}

        def _work() -> None:
            # 与 Flet 版 _regenerate_pdf_thread 语义一致：txt → .ly →
            # sanitize（注入 CJK 字体与标题）→ LilyPond PDF → 覆写 Output/ 旧文件
            error: Optional[str] = None
            try:
                import tempfile
                from core.render.lilypond_runner import render_jianpu_ly, render_lilypond_pdf
                from core.render.renderer import sanitize_generated_lilypond_file
                with tempfile.TemporaryDirectory(prefix='rerender_') as td:
                    ly = Path(td) / '_regen.ly'
                    if not render_jianpu_ly(txt, ly) or not ly.exists():
                        error = 'jianpu-ly 转换失败'
                    else:
                        title = ''
                        try:
                            for ln in txt.read_text(encoding='utf-8').splitlines():
                                if ln.strip().startswith('title='):
                                    title = ln.strip()[len('title='):]
                                    break
                        except Exception:
                            pass
                        sanitize_generated_lilypond_file(ly, title)
                        produced = render_lilypond_pdf(ly)
                        if produced is None or not produced.exists():
                            error = 'LilyPond 渲染失败'
                        else:
                            shutil.copy2(str(produced), str(pdf))
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            self._pusher.push('rerender_done', {
                'path': str(pdf), 'ok': error is None, 'error': error,
            })

        threading.Thread(target=_work, daemon=True, name='rerender').start()
        return {'ok': True, 'started': True}
