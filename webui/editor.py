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
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Optional

from core.app.backend import build_dir, editor_workspace_dir, output_dir
from core.notation.jianpu import (
    JianpuParseError,
    build_jianpu_fragment_text,
    build_jianpu_ly_text_from_doc,
    jianpu_doc_from_dict,
    jianpu_doc_to_dict,
    jianpu_section_to_render_json,
    parse_jianpu_ly_text,
)
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


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_title_for_filename(title: str) -> str:
    """Turn a user-typed title into a safe Windows filename stem."""
    cleaned = _INVALID_FILENAME_CHARS.sub('_', title).strip().strip('.')
    return cleaned or 'Untitled'


def _unique_jianpu_txt_path(ws: Path, stem: str) -> Path:
    """First available ``<stem>[ (n)].jianpu.txt`` under *ws* (Explorer-style
    auto-suffix — new-file creation has no prior "this exact file" to
    overwrite, so silently picking the next free name beats either blocking
    on a scary error or silently clobbering an existing score)."""
    dest = ws / f'{stem}.jianpu.txt'
    n = 2
    while dest.exists():
        dest = ws / f'{stem} ({n}).jianpu.txt'
        n += 1
    return dest


def _blank_measure_text(bar_quarter_length: float) -> str:
    """A full-bar rest matching *bar_quarter_length*, using the same greedy
    duration decomposition already used to repair malformed OMR measures —
    so a hand-typed 5/4 or 7/8 bar chains multiple rest tokens exactly the
    way the rest of the pipeline would, not a bespoke one-off encoding."""
    from core.config import JianpuNote
    from core.notation.jianpu.primitives import jianpu_note_token, split_duration_chunks

    chunks = split_duration_chunks(bar_quarter_length)
    if not chunks:
        return '0'
    tokens = [
        jianpu_note_token(JianpuNote(
            symbol='0', accidental='', upper_dots=0, lower_dots=0,
            duration=chunk, duration_dots=0, midi=None, is_rest=True,
        ))
        for chunk in chunks
    ]
    return ' '.join(tokens)


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

    # ── 新建空白简谱（B8.4 轻量版）───────────────────────────────────────────

    def create_blank(self, params: dict) -> dict:
        """Create a new blank .jianpu.txt from the "新建简谱" wizard's fields,
        write it into editor_workspace_dir() alongside OMR output (so the
        entire downstream render/export/preview chain needs zero changes),
        then load() it exactly like opening any other file.
        """
        from core.notation.jianpu.primitives import _QL_TO_ANACRUSIS_CODE
        from core.render.renderer import _build_editor_header

        title = str(params.get('title') or '').strip() or 'Untitled'
        composer = str(params.get('composer') or '').strip()
        key_mode = params.get('key_mode') if params.get('key_mode') in ('major', 'minor') else 'major'
        tonic = str(params.get('tonic') or 'C').strip()
        try:
            num = max(1, int(params.get('time_num') or 4))
            den = max(1, int(params.get('time_den') or 4))
            tempo = max(1, int(params.get('tempo') or 120))
            measure_count = max(1, int(params.get('measure_count') or 16))
            voice_count = max(1, int(params.get('voice_count') or 1))
        except (TypeError, ValueError):
            return {'ok': False, 'error': 'bad_params'}
        anacrusis_code = str(params.get('anacrusis') or '').strip()
        anacrusis_ql = None
        if anacrusis_code:
            code_to_ql = {code: ql for ql, code in _QL_TO_ANACRUSIS_CODE.items()}
            anacrusis_ql = code_to_ql.get(anacrusis_code)
            if anacrusis_ql is None:
                return {'ok': False, 'error': 'bad_params'}

        ws = editor_workspace_dir()
        ws.mkdir(parents=True, exist_ok=True)
        dest = _unique_jianpu_txt_path(ws, _sanitize_title_for_filename(title))

        bar_ql = 4.0 * num / den
        full_bar = _blank_measure_text(bar_ql)
        first_bar = _blank_measure_text(anacrusis_ql) if anacrusis_ql is not None else full_bar
        key_line = f'1={tonic}' if key_mode == 'major' else f'6={tonic}'
        timesig_line = f'{num}/{den}' + (f',{anacrusis_code}' if anacrusis_code else '')

        def voice_body() -> str:
            bars = [first_bar] + [full_bar] * (measure_count - 1)
            lines = [key_line, timesig_line, f'4={tempo}', '']
            for i in range(0, len(bars), 4):  # 4 小节一行，贴近真实文件排版习惯
                lines.append(' | '.join(bars[i:i + 4]) + ' |')
            return '\n'.join(lines)

        body_lines = ['% jianpu-ly.py', f'title={title}']
        if composer:
            body_lines.append(f'composer={composer}')
        body_lines.append(voice_body())
        for _ in range(voice_count - 1):
            body_lines.append('NextPart')
            body_lines.append(voice_body())
        body = '\n'.join(body_lines) + '\n'

        try:
            dest.write_text(_build_editor_header(title) + body, encoding='utf-8')
        except OSError as exc:
            return {'ok': False, 'error': str(exc)}
        return self.load(str(dest))

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

    # ── 图形化渲染数据（txt → JianpuDoc → JianpuRender JSON，阶段3.2）────────

    def graphical_render_data(self, body: Optional[str] = None) -> dict:
        """Parse the CURRENT buffer into a JianpuRender JianpuInfo JSON per section.

        Read-only and does not save — unlike render_preview() (the LilyPond
        PDF path), this never has to touch disk since the graphical renderer
        only needs the in-memory JianpuDoc, not a saved file.

        One entry per `NextPart` section (阶段3.5): every section becomes its
        own independent staff, stacked vertically by the caller — this is
        deliberately simpler than the OMR pipeline's voice_groups concept
        (which merges some sections onto the *same* polyphonic staff for the
        LilyPond PDF path, see render_preview()'s merge_polyphonic_jianpu_staves
        call). The 阶段2 editor parser doesn't populate voice_groups at all
        (that data lives in the file's hidden #__jianpu_meta__ header comment,
        which parse_jianpu_ly_text never sees — it only receives the body),
        and JianpuRender itself has no concept of multiple voices sharing one
        staff (see the 阶段2.5 spike's B9.3.1 finding). Showing every section
        as its own staff is strictly more information than showing nothing,
        and is what B6 阶段3.5's own "多个 renderer 实例纵向排布" wording
        already describes.
        """
        if self._current is None:
            return {'ok': False, 'error': 'no_file'}
        if body is None:
            try:
                with self._file_lock:
                    text = self._current.read_text(encoding='utf-8-sig', errors='replace')
            except OSError as exc:
                return {'ok': False, 'error': str(exc)}
            _, body = _split_header(text)
        try:
            doc = parse_jianpu_ly_text(body)
        except JianpuParseError as exc:
            return {'ok': False, 'error': 'parse_error', 'message': str(exc), 'line': exc.line, 'col': exc.col}
        if not doc.sections:
            return {'ok': False, 'error': 'empty'}
        return {'ok': True, 'renders': self._renders_of(doc), 'doc': jianpu_doc_to_dict(doc)}

    def _renders_of(self, doc) -> list:
        return [
            jianpu_section_to_render_json(section, doc.key_header, doc.tempo)
            for section in doc.sections
        ]

    def apply_doc(self, doc_raw: Any) -> dict:
        """Serialize an edited model back to text, and re-project it for drawing.

        The other half of ``graphical_render_data``: that one hands the
        front-end the model, this one takes it back after the 阶段5.1 command
        stack has edited it. Returning **both** the text and the render JSON
        from the same ``JianpuDoc`` is deliberate — the textarea and the SVG
        are two projections of one model (B4's first non-negotiable), so
        deriving them in one place is what keeps them from disagreeing.

        Note that the caller only ever holds a document that came out of a
        successful parse, which is what keeps files carrying syntax the parser
        rejects (🟡-tier beyond lyrics) out of this path entirely: no doc is
        ever produced for them, so nothing can be serialized over the top of
        them and their text stays authoritative.
        """
        if self._current is None:
            return {'ok': False, 'error': 'no_file'}
        try:
            doc = jianpu_doc_from_dict(doc_raw)
            if not doc.sections:
                return {'ok': False, 'error': 'empty'}
            text = build_jianpu_ly_text_from_doc(doc)
        except Exception as exc:  # noqa: BLE001 — a lost edit is worse than a reported one
            log_message(f'[webui] 简谱模型序列化失败：{exc}', logging.WARNING)
            return {'ok': False, 'error': str(exc)}
        return {'ok': True, 'text': text, 'renders': self._renders_of(doc)}

    def fragment_text(self, fragment_raw: Any) -> dict:
        """Serialize a copied fragment to bare jianpu-ly note text (阶段5.5b).

        *fragment_raw* is a JianpuDoc-shaped dict whose single section holds
        the copied measures — the front-end reuses the document shape rather
        than inventing a second wire format, so ``jianpu_doc_from_dict``'s
        coercion (string verse keys, string tempo, …) applies here too.

        Purely a convenience: the clipboard the front-end actually pastes from
        is its own in-memory model, and this text is only mirrored onto the
        system clipboard. So a failure here must stay non-fatal — losing the
        system-clipboard copy is a much smaller harm than refusing the copy.

        Unlike ``apply_doc`` this deliberately does not require a loaded file:
        serializing a fragment touches no document state at all.
        """
        try:
            doc = jianpu_doc_from_dict(fragment_raw)
            if not doc.sections:
                return {'ok': False, 'error': 'empty'}
            return {'ok': True, 'text': build_jianpu_fragment_text(doc)}
        except Exception as exc:  # noqa: BLE001 — mirroring is best-effort
            log_message(f'[webui] 简谱片段序列化失败：{exc}', logging.WARNING)
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
                    from core.render.jianpu_runner import (
                        inject_repeat_barlines_to_ly,
                        merge_polyphonic_jianpu_staves,
                    )
                    from core.render.lilypond_runner import render_jianpu_ly, render_lilypond_pdf
                    from core.render.renderer import (
                        parse_jianpu_meta_comment,
                        sanitize_generated_lilypond_file,
                    )
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
                            # P1-4：主管线转换时把多声部分组/反复小节信息存进了本文件
                            # 头部的 #__jianpu_meta__ 行（用户不可见）；这里读回并重放
                            # 同样两步，否则"打开→不改动→重渲染"会悄悄丢掉多声部合并
                            # 与反复记号，产出与原始转换结果不一致。
                            voice_groups, repeat_barlines = parse_jianpu_meta_comment(self._header)
                            merge_polyphonic_jianpu_staves(ly, voice_groups)
                            inject_repeat_barlines_to_ly(ly, repeat_barlines)
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
