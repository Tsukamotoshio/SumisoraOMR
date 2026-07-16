# webui/editor.py — jianpu text editor service (M4).
"""Backend for the 简谱编辑 page, mirroring the Flet JianpuEditor/EditorPage:

- load: read ``.jianpu.txt`` (utf-8-sig), split the protected leading ``#``
  comment header (kept Python-side, re-prepended on save — the user never
  edits or sees it), locate the matching source reference (image or PDF,
  ``<stem>.source.*``/``<stem>.*`` in the txt's folder or editor-workspace)
  and whitelist it for /file;
- save: header + body under a file lock (same ``_file_lock`` semantics as the
  Flet editor — GUI save vs. export thread races);
- preview: txt → jianpu-ly → sanitize (CJK fonts/title) → LilyPond → flat PDF
  in build/ for pdf.js, pushed as ``editor_preview_ready``;
- entry from the jianpu preview page: derive the txt from the output PDF stem.
"""
from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import Optional

from core.app.backend import build_dir, editor_workspace_dir, output_dir
from core.utils import log_message

from .events import EventPusher
from .server import FileWhitelist

_SOURCE_EXTS = ('.png', '.jpg', '.jpeg', '.pdf')


def _split_header(text: str) -> tuple[str, str]:
    """Split the leading ``#`` comment block (+ one blank separator) from the body.

    与 Flet JianpuEditor._split_header 逐行一致。
    """
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].lstrip().startswith('#'):
        i += 1
    if i < len(lines) and lines[i].strip() == '':
        i += 1
    return ''.join(lines[:i]), ''.join(lines[i:])


def _normalize_stem(stem: str) -> str:
    if stem.endswith('.audiveris'):
        stem = stem[: -len('.audiveris')]
    if stem.endswith('.source'):
        stem = stem[: -len('.source')]
    return stem


class EditorService:
    """Load/save/preview for .jianpu.txt files."""

    def __init__(self, pusher: EventPusher, whitelist: FileWhitelist) -> None:
        self._pusher = pusher
        self._whitelist = whitelist
        self._file_lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._current: Optional[Path] = None
        self._header: str = ''
        self._gen = 0  # 换文件计数，作废旧渲染回调

    # ── 源参考匹配（与 Flet editor_page 一致）────────────────────────────────

    def _find_source(self, stem: str, parent: Path) -> Optional[Path]:
        ws = editor_workspace_dir()
        stem = _normalize_stem(stem)
        for ext in _SOURCE_EXTS:
            for cand in (parent / f'{stem}.source{ext}', ws / f'{stem}.source{ext}',
                         parent / f'{stem}{ext}', ws / f'{stem}{ext}'):
                if cand.exists():
                    return cand
        return None

    # ── 加载 ─────────────────────────────────────────────────────────────────

    def load(self, txt_path: str) -> dict:
        p = Path(txt_path)
        if not p.is_file():
            return {'ok': False, 'error': 'not_found', 'name': p.name}
        try:
            with self._file_lock:
                text = p.read_text(encoding='utf-8-sig', errors='replace')
        except OSError as exc:
            return {'ok': False, 'error': str(exc)}
        self._current = p
        self._gen += 1
        self._header, body = _split_header(text)
        source = self._find_source(p.name[:-len('.jianpu.txt')] if p.name.endswith('.jianpu.txt') else p.stem,
                                   p.parent)
        src_info = None
        if source is not None:
            self._whitelist.allow(source)
            src_info = {'path': str(source),
                        'kind': 'pdf' if source.suffix.lower() == '.pdf' else 'image'}
        return {'ok': True, 'path': str(p), 'name': p.name, 'body': body,
                'has_header': bool(self._header), 'source': src_info}

    def load_for_pdf(self, pdf_path: str) -> dict:
        """Entry from the jianpu preview page: Output/<stem>_jianpu.pdf → txt."""
        name = Path(pdf_path).name
        stem = name[:-len('_jianpu.pdf')] if name.endswith('_jianpu.pdf') else Path(pdf_path).stem
        txt = editor_workspace_dir() / f'{stem}.jianpu.txt'
        if not txt.exists():
            return {'ok': False, 'error': 'no_txt', 'name': txt.name}
        return self.load(str(txt))

    # ── 保存 ─────────────────────────────────────────────────────────────────

    def save(self, body: str) -> dict:
        if self._current is None:
            return {'ok': False, 'error': 'no_file'}
        try:
            with self._file_lock:
                self._current.write_text(self._header + (body or ''), encoding='utf-8')
            return {'ok': True, 'name': self._current.name}
        except OSError as exc:
            log_message(f'[webui] 简谱保存失败：{exc}', logging.WARNING)
            return {'ok': False, 'error': str(exc)}

    # ── 预览渲染（txt → LilyPond → PDF；完成推 editor_preview_ready）─────────

    def render_preview(self, body: Optional[str] = None) -> dict:
        """Render the CURRENT buffer. body 非 None 时先保存（预览即所见即所得）。"""
        if self._current is None:
            return {'ok': False, 'error': 'no_file'}
        if body is not None:
            saved = self.save(body)
            if not saved.get('ok'):
                return saved
        txt = self._current
        stem = txt.name[:-len('.jianpu.txt')] if txt.name.endswith('.jianpu.txt') else txt.stem
        flat = build_dir() / f'_editor_preview_{stem}.pdf'
        gen = self._gen

        def _work() -> None:
            error: Optional[str] = None
            with self._render_lock:
                try:
                    import tempfile
                    from core.render.lilypond_runner import render_jianpu_ly, render_lilypond_pdf
                    from core.render.renderer import sanitize_generated_lilypond_file
                    with tempfile.TemporaryDirectory(prefix='_editor_preview_',
                                                     dir=str(build_dir())) as td:
                        ly = Path(td) / f'{stem}.ly'
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
                                shutil.copy2(str(produced), str(flat))
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
            if gen != self._gen:
                return  # 已切换文件，丢弃
            if error is None:
                self._whitelist.allow(flat)
            self._pusher.push('editor_preview_ready', {
                'txt': str(txt), 'pdf': str(flat) if error is None else None,
                'ok': error is None, 'error': error,
            })

        threading.Thread(target=_work, daemon=True, name='editor-preview').start()
        return {'ok': True, 'started': True}

    # ── 导出：把当前渲染 PDF 覆写回 Output/（与简谱预览页重渲语义一致）───────

    def export_to_output(self) -> dict:
        if self._current is None:
            return {'ok': False, 'error': 'no_file'}
        stem = self._current.name[:-len('.jianpu.txt')] \
            if self._current.name.endswith('.jianpu.txt') else self._current.stem
        flat = build_dir() / f'_editor_preview_{stem}.pdf'
        if not flat.exists():
            return {'ok': False, 'error': 'no_preview'}
        dest = output_dir(None) / f'{stem}_jianpu.pdf'
        try:
            shutil.copy2(str(flat), str(dest))
            return {'ok': True, 'dest': str(dest)}
        except OSError as exc:
            return {'ok': False, 'error': str(exc)}
