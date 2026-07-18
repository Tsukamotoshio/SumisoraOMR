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
import shutil
import threading
from pathlib import Path
from typing import Optional

from core.app.backend import build_dir, editor_workspace_dir, output_dir, xml_scores_dir
from core.utils import log_message

from .events import EventPusher
from .server import FileWhitelist

_JIANPU_SUFFIX = '_jianpu.pdf'
_SCORE_EXTS = ('*.mxl', '*.xml', '*.musicxml')


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
        # 内置播放器：白名单放行，返回路径供前端经 /file 取字节 → WebAudioTinySynth 播放
        self._whitelist.allow(midi)
        return {'ok': True, 'path': str(midi), 'name': midi.name}

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


class ScoresService:
    """五线谱预览页后端：xml-scores/ 列表 + 按需 LilyPond 渲染（带扁平缓存）。

    Mirrors the Flet score_preview_page:
    - preview: ``render_musicxml_staff_pdf`` → flat cache
      ``build/_score_preview_<stem>.pdf``（mxl 更新则重渲）；
    - MIDI: ``Output/<stem>.mid``，缺失时由前端确认后 ``render_midi_from_musicxml``
      生成再播放；
    - delete: 仅删 MXL 与其缓存 PDF；
    - export: 渲染（或拷缓存）到目标目录的 ``score_output/`` 子目录。
    """

    def __init__(self, pusher: EventPusher, whitelist: FileWhitelist) -> None:
        self._pusher = pusher
        self._whitelist = whitelist
        self._render_lock = threading.Lock()  # LilyPond 逐个渲染，避免并发风暴

    # ── 列表 ─────────────────────────────────────────────────────────────────

    def list_scores(self) -> list[dict]:
        d = xml_scores_dir()
        paths: list[Path] = []
        if d.exists():
            for ext in _SCORE_EXTS:
                paths.extend(d.glob(ext))
        paths = sorted(set(paths), key=lambda p: p.stat().st_mtime, reverse=True)
        out = output_dir(None)
        return [{
            'path': str(p),
            'name': p.name,
            'stem': p.stem,
            'mtime': int(p.stat().st_mtime),
            'has_midi': (out / f'{p.stem}.mid').exists(),
        } for p in paths]

    # ── 预览渲染（后台线程；完成推 score_preview_ready）──────────────────────

    def _flat_pdf_for(self, mxl: Path) -> Path:
        return build_dir() / f'_score_preview_{mxl.stem}.pdf'

    def preview(self, path: str) -> dict:
        """Return the cached staff PDF immediately, or start a background render."""
        mxl = Path(path)
        flat = self._flat_pdf_for(mxl)
        if flat.exists() and mxl.exists() and flat.stat().st_mtime >= mxl.stat().st_mtime:
            self._whitelist.allow(flat)
            return {'ok': True, 'pdf': str(flat)}

        def _work() -> None:
            error = None
            with self._render_lock:
                try:
                    import shutil as _sh
                    import tempfile
                    from core.render.lilypond_runner import render_musicxml_staff_pdf
                    with tempfile.TemporaryDirectory(
                            prefix=f'_score_preview_{mxl.stem}_', dir=str(build_dir())) as td:
                        pdf = render_musicxml_staff_pdf(mxl, Path(td))
                        if pdf and pdf.exists():
                            _sh.copy2(str(pdf), str(flat))
                        else:
                            error = '五线谱渲染失败'
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
            if error is None:
                self._whitelist.allow(flat)
            self._pusher.push('score_preview_ready', {
                'mxl': str(mxl), 'pdf': str(flat) if error is None else None,
                'ok': error is None, 'error': error,
            })

        threading.Thread(target=_work, daemon=True, name='staff-render').start()
        return {'ok': True, 'started': True}

    # ── MIDI（缺失时生成再播放；进度经事件）──────────────────────────────────

    def midi_for(self, path: str) -> dict:
        midi = output_dir(None) / f'{Path(path).stem}.mid'
        return {'exists': midi.exists(), 'name': midi.name}

    def generate_and_play_midi(self, path: str) -> dict:
        mxl = Path(path)
        midi = output_dir(None) / f'{mxl.stem}.mid'

        def _work() -> None:
            ok = False
            error = None
            try:
                if midi.exists():
                    ok = True
                else:
                    from core.render.renderer import render_midi_from_musicxml
                    ok = bool(render_midi_from_musicxml(mxl, midi)) and midi.exists()
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            path = None
            if ok:
                # 内置播放器：白名单放行，路径随事件回前端 → /file → WebAudioTinySynth
                self._whitelist.allow(midi)
                path = str(midi)
            self._pusher.push('score_midi_done', {
                'mxl': str(mxl), 'ok': ok, 'error': error, 'name': midi.name, 'path': path,
            })

        threading.Thread(target=_work, daemon=True, name='midi-gen').start()
        return {'ok': True, 'started': True}

    # ── 删除 / 导出 ──────────────────────────────────────────────────────────

    def delete(self, paths: list[str]) -> dict:
        removed = 0
        for raw in paths or []:
            p = Path(raw)
            try:
                p.unlink(missing_ok=True)
                removed += 1
            except OSError as exc:
                log_message(f'[webui] 删除失败 {p.name}：{exc}', logging.WARNING)
                continue
            flat = self._flat_pdf_for(p)
            self._whitelist.revoke(flat)
            try:
                flat.unlink(missing_ok=True)
            except OSError:
                pass
        return {'ok': True, 'removed': removed}

    def export_to(self, paths: list[str], dest_dir: str) -> dict:
        """Render (or reuse cache) each MXL's staff PDF into <dest>/score_output/."""
        dest = Path(dest_dir) / 'score_output'
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {'ok': False, 'error': str(exc)}
        copied, failed = [], []
        for raw in paths or []:
            mxl = Path(raw)
            out_pdf = dest / f'{mxl.stem}_staff.pdf'
            try:
                flat = self._flat_pdf_for(mxl)
                if not (flat.exists() and flat.stat().st_mtime >= mxl.stat().st_mtime):
                    import shutil as _sh
                    import tempfile
                    from core.render.lilypond_runner import render_musicxml_staff_pdf
                    with self._render_lock, tempfile.TemporaryDirectory(
                            prefix=f'_score_export_{mxl.stem}_', dir=str(build_dir())) as td:
                        pdf = render_musicxml_staff_pdf(mxl, Path(td))
                        if not (pdf and pdf.exists()):
                            failed.append({'file': mxl.name, 'error': '渲染失败'})
                            continue
                        _sh.copy2(str(pdf), str(flat))
                shutil.copy2(str(flat), str(out_pdf))
                copied.append(out_pdf.name)
            except Exception as exc:  # noqa: BLE001
                failed.append({'file': mxl.name, 'error': str(exc)})
        return {'ok': not failed, 'copied': copied, 'failed': failed, 'dest': str(dest)}
