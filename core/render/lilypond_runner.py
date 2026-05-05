# core/render/lilypond_runner.py — LilyPond / jianpu-ly tool discovery and rendering.
# Split from runtime_finder.py.
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional

# 在 Windows 上，作为 GUI 程序运行时防止子进程弹出新控制台窗口
_WIN_NO_WINDOW: int = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

from ..config import (
    JIANPU_LY_URLS,
    LILYPOND_RUNTIME_DIR_NAME,
    LOGGER,
)
from ..utils import (
    find_packaged_runtime_dir,
    get_app_base_dir,
    get_runtime_search_roots,
    log_message,
    resolve_font_path,
)


# ──────────────────────────────────────────────
# LilyPond
# ──────────────────────────────────────────────

def find_lilypond_executable() -> Optional[str]:
    """Locate the LilyPond executable via env vars, bundled runtime, or common install paths."""
    env_path = os.environ.get('LILYPOND_PATH') or os.environ.get('LILYPOND_HOME')
    candidates: list[str] = []
    if env_path:
        env_base = Path(env_path)
        candidates.extend([
            str(env_base),
            str(env_base / 'lilypond.exe'),
            str(env_base / 'usr' / 'bin' / 'lilypond.exe'),
            str(env_base / 'LilyPond' / 'usr' / 'bin' / 'lilypond.exe'),
        ])

    packaged_lilypond_dir = find_packaged_runtime_dir(LILYPOND_RUNTIME_DIR_NAME)
    if packaged_lilypond_dir is not None:
        candidates.extend([
            str(packaged_lilypond_dir / 'bin' / 'lilypond.exe'),
            str(packaged_lilypond_dir / 'usr' / 'bin' / 'lilypond.exe'),
        ])

    candidates.extend([
        str(get_app_base_dir() / 'lilypond-2.24.4' / 'bin' / 'lilypond.exe'),
        'lilypond',
        'lilypond.exe',
        r'C:\Program Files\LilyPond\usr\bin\lilypond.exe',
        r'C:\Program Files (x86)\LilyPond\usr\bin\lilypond.exe',
    ])

    checked: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        candidate_path = Path(candidate)
        if candidate_path.is_file():
            return str(candidate_path)
        found = shutil.which(candidate)
        if found:
            return found
        checked.append(candidate)

    log_message('未找到 LilyPond，可尝试以下路径或设置环境变量 LILYPOND_PATH / LILYPOND_HOME:', logging.WARNING)
    for candidate in checked:
        log_message(f'  - {candidate}', logging.WARNING)
    return None


def render_lilypond_pdf(ly_path: Path) -> Optional[Path]:
    """Invoke LilyPond to render a .ly file to PDF; return the PDF path or None on failure."""
    lilypond_exe = find_lilypond_executable()
    if lilypond_exe is None:
        return None
    ly_path = ly_path.resolve()
    # Suppress the LilyPond tagline by uncommenting the line jianpu-ly leaves in the .ly file,
    # or injecting \header { tagline = ##f } if it isn't already present.
    try:
        ly_content = ly_path.read_text(encoding='utf-8')
        if '% \\header { tagline="" }' in ly_content:
            ly_content = ly_content.replace('% \\header { tagline="" }', '\\header { tagline = ##f }')
        elif '\\header { tagline' not in ly_content:
            ly_content += '\n\\header { tagline = ##f }\n'
        ly_path.write_text(ly_content, encoding='utf-8')
    except Exception:
        pass
    log_message(f'使用 LilyPond 执行: {lilypond_exe}')
    try:
        subprocess.run([lilypond_exe, str(ly_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, cwd=str(ly_path.parent.resolve()), creationflags=_WIN_NO_WINDOW)
        pdf_path = ly_path.with_suffix('.pdf')
        return pdf_path if pdf_path.exists() else None
    except subprocess.CalledProcessError as exc:
        raw_stderr = exc.stderr.decode('utf-8', errors='ignore')
        # 过滤纯弃用警告行，仅保留实际错误行，防止数万行警告涌入日志
        error_lines = [
            ln for ln in raw_stderr.splitlines()
            if ln.strip() and not (
                '警告' in ln or 'warning' in ln.lower() or '已弃用' in ln
            )
        ]
        summary = '\n'.join(error_lines[:60]) if error_lines else raw_stderr[:2000]
        log_message('LilyPond 生成失败:\n' + summary, logging.WARNING)
        return None
    except OSError as exc:
        log_message(f'LilyPond 生成时出现异常: {exc}', logging.WARNING)
        return None


def _fix_adjacent_backward_repeats_in_mxl(mxl_path: Path) -> Path:
    """Return a path to a MusicXML file with adjacent backward-repeat barlines fixed.

    Some OMR engines (Homr, Audiveris) split a combined ``:|.|:`` barline into
    two consecutive backward-repeat (``direction="backward"``) barlines — one
    on the right side of measure N, one on the right side of measure N+1.
    This is invalid: two adjacent end-repeats without a start-repeat between
    them cannot logically occur in standard notation.

    This function detects such pairs and converts the second measure's right-side
    backward-repeat into a left-side forward-repeat, restoring the intended
    ``:|.|:`` semantics.

    A ``direction="forward"`` on a RIGHT barline is ignored by both music21 and
    musicxml2ly; it must appear as a LEFT barline of the following measure to be
    recognised as a start-repeat by downstream tools.  The corrected XML is:

    Before (measure N+1):
        <barline location="right"><repeat direction="backward"/></barline>

    After (measure N+1):
        <barline location="left"><repeat direction="forward"/></barline>

    If no fix is needed the original path is returned unchanged.  If a fix is
    applied a sibling temp file ``_staff_fixed.musicxml`` is written and returned.
    """
    try:
        content = mxl_path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return mxl_path

    measure_re = re.compile(
        r'(<measure\b[^>]*>)(.*?)(</measure>)',
        re.DOTALL,
    )
    right_backward_re = re.compile(
        r'<barline\s+location=["\']right["\'][^>]*>.*?<repeat\s+direction=["\']backward["\'].*?</barline>',
        re.DOTALL,
    )
    left_any_re = re.compile(
        r'<barline\s+location=["\']left["\']',
        re.DOTALL,
    )

    measures = list(measure_re.finditer(content))
    replacements: list[tuple[int, int, str]] = []  # (start, end, new_text)

    for i in range(len(measures) - 1):
        ma, mb = measures[i], measures[i + 1]
        body_a, body_b = ma.group(2), mb.group(2)
        # Both measures must have a right-side backward-repeat, and the second
        # must not already have a left-side barline (to avoid double-insertion).
        if (
            right_backward_re.search(body_a)
            and right_backward_re.search(body_b)
            and not left_any_re.search(body_b)
        ):
            # Replace the right-side backward-repeat in measure N+1 with a
            # left-side forward-repeat so both music21 and musicxml2ly see a
            # valid start-repeat at the beginning of that measure.
            new_body_b = right_backward_re.sub(
                '<barline location="left"><repeat direction="forward"/></barline>',
                body_b,
                count=1,
            )
            full_new = mb.group(1) + new_body_b + mb.group(3)
            replacements.append((mb.start(), mb.end(), full_new))

    if not replacements:
        return mxl_path

    # Apply replacements in reverse order to preserve string offsets.
    new_content = content
    for start, end, new_text in sorted(replacements, key=lambda x: x[0], reverse=True):
        new_content = new_content[:start] + new_text + new_content[end:]

    fixed_path = mxl_path.parent / '_staff_fixed.musicxml'
    try:
        fixed_path.write_text(new_content, encoding='utf-8')
        LOGGER.debug(
            '_fix_adjacent_backward_repeats_in_mxl: fixed %d pair(s) in %s',
            len(replacements), mxl_path.name,
        )
        return fixed_path
    except Exception:
        return mxl_path


def _fix_deprecated_ly_syntax(text: str) -> str:
    r"""Convert deprecated LilyPond #'property syntax to .property dot notation.

    musicxml2ly may emit ``\set Staff #'instrumentName`` style syntax that is
    deprecated since LilyPond 2.24.  Safe to apply to any generated .ly file.
    """
    return re.sub(
        r'(\b[A-Z][A-Za-z]+)\s+#\'([a-z][a-z0-9-]*)',
        r'\1.\2',
        text,
    )


def _ascii_only(s: str) -> str:
    """仅保留可打印 ASCII 字符（0x20-0x7E），剥离 CJK 等非 ASCII 内容。

    LilyPond 默认字体不含 CJK 字形，注入含汉字的 title 会导致 PDF 出现方块
    和排版溢出。此函数在写入 \\header 前对 title/composer 做净化。
    """
    return ''.join(c for c in s if 0x20 <= ord(c) <= 0x7E).strip()


def _has_cjk(s: str) -> bool:
    """Return True if *s* contains any character outside the ASCII printable range."""
    return any(ord(c) > 0x7E for c in s)


def _find_cjk_font_for_overlay() -> Optional[Path]:
    """Locate a CJK-capable TrueType/OpenType font file for fitz text overlay.

    Preference order:
    1. Bundled NotoSansSC font in assets/fonts/ (always available in this repo).
    2. First available system CJK font via :func:`resolve_font_path`.
    """
    bundled = get_app_base_dir() / 'assets' / 'fonts' / 'NotoSansSC-VariableFont_wght.ttf'
    if bundled.exists():
        return bundled
    return resolve_font_path()


def _overlay_cjk_title_on_staff_pdf(pdf_path: Path, title: str) -> None:
    """Overlay a Unicode/CJK title onto the first page of a LilyPond staff PDF.

    When the score title is CJK-only, musicxml2ly emits the raw CJK bytes into
    the LilyPond ``\\header`` block, and LilyPond's default Century Schoolbook font
    renders them as empty boxes or partial glyphs.  ``_inject_metadata_to_lilypond``
    ensures LilyPond always reserves the title-area vertical space (by injecting
    a ``"."`` placeholder when the MusicXML had no title).  This function then:

    1. Finds the title text block position dynamically via ``page.get_text``.
    2. Draws a white rectangle over the garbled/placeholder header area.
    3. Draws the correct CJK title text centered in that area using the
       bundled Noto Sans SC TTF via PyMuPDF's TextWriter API.

    Note: ``insert_textbox``'s ``fontfile=`` keyword is *not* sufficient for CJK
    fonts — fitz silently falls back to a built-in font when the supplied file
    contains non-Latin glyphs.  The correct way is to create a ``fitz.Font`` object
    from the file and pass it as the ``font=`` keyword argument.
    """
    if not title:
        return
    font_path = _find_cjk_font_for_overlay()
    if font_path is None:
        LOGGER.debug('_overlay_cjk_title_on_staff_pdf: 未找到 CJK 字体，跳过叠加')
        return
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return
    try:
        doc = fitz.open(str(pdf_path))
        if len(doc) == 0:
            doc.close()
            return
        page = doc[0]
        pw = page.rect.width
        # Dynamically locate the title area using word-level extraction.
        # PyMuPDF sometimes merges adjacent text blocks (e.g. "." title and
        # "Piano" instrument label into one block), making block-level y-bounds
        # unreliable.  Word-level extraction gives individual bounding boxes so
        # we can filter by x-position: the centered title sits at x > 25 % of
        # page width, while left-margin instrument/voice labels sit at x ≈ 35 pt.
        #
        # Words are kept if:
        #   • y0 < 45 pt  (title area only; voice labels at y≈39 but x≈35 → excluded)
        #   • x0 > pw*0.25  (centered, not a left-margin instrument/voice label)
        #   • more than 50 % of chars are above 0x1F  (not binary music-font data)
        def _is_readable_word(text: str) -> bool:
            if not text.strip():
                return False
            return sum(1 for c in text if ord(c) > 0x1F) / len(text) > 0.5

        title_words = [
            (x0, y0, x1, y1)
            for x0, y0, x1, y1, word, *_ in page.get_text('words')
            if y0 < 45 and x0 > pw * 0.25 and _is_readable_word(word)
        ]
        if title_words:
            cover_y0 = max(0.0, min(b[1] for b in title_words) - 4)
            cover_y1 = min(max(b[3] for b in title_words) + 4, 55.0)
        else:
            # Fallback: single title-line estimate.  With the "." placeholder
            # injected by _inject_metadata_to_lilypond, LilyPond always reserves
            # title space, so the music never starts before y ≈ 25–35 pt.
            cover_y0, cover_y1 = 5.0, 35.0
        cover_rect = fitz.Rect(0, cover_y0, pw, cover_y1)
        title_rect = fitz.Rect(36, cover_y0, pw - 36, cover_y1)
        # Must use fitz.Font + fitz.TextWriter — passing fontfile= directly to
        # insert_textbox does not work for CJK glyphs; fitz silently reverts to a
        # built-in font.  TextWriter.fill_textbox() accepts a fitz.Font object and
        # correctly embeds CJK glyphs from the TTF file.
        # Blank out the garbled LilyPond-rendered title area with white, then
        # draw the correct CJK text on top using the Noto Sans SC font.
        page.draw_rect(cover_rect, color=(1, 1, 1), fill=(1, 1, 1))
        cjk_font = fitz.Font(fontfile=str(font_path))
        tw = fitz.TextWriter(page.rect)
        tw.fill_textbox(
            title_rect,
            title,
            font=cjk_font,
            fontsize=15,
            align=1,  # 1 = TEXT_ALIGN_CENTER
        )
        # render_mode=2 (fill + stroke with same color) makes the glyphs appear
        # slightly bolder.  Note: write_text() does NOT accept border_width or
        # fill_color — both raise TypeError in PyMuPDF 1.27.x.
        tw.write_text(page, render_mode=2, color=(0, 0, 0))
        # Save to a sibling temp file then replace the original.  On Windows,
        # doc.save(same_path, incremental=False) silently discards the changes
        # because the original file handle is still held during the write.
        tmp_path = pdf_path.with_suffix('.tmp.pdf')
        doc.save(str(tmp_path), incremental=False, garbage=4, encryption=fitz.PDF_ENCRYPT_NONE)
        doc.close()
        tmp_path.replace(pdf_path)
        LOGGER.debug('_overlay_cjk_title_on_staff_pdf: 已叠加标题 "%s"', title)
    except Exception as exc:
        LOGGER.debug('_overlay_cjk_title_on_staff_pdf: 叠加失败: %s', exc)

def _inject_metadata_to_lilypond(ly_path: Path, mxl_path: Path) -> str:
    """Append a \\header block with title/composer from MusicXML metadata at EOF.

    Returns the raw (pre-ASCII-stripping) title string so callers can use it
    for post-processing (e.g. CJK overlay) without a second metadata parse.

    Appending is safe for any LilyPond file structure, including complex
    multi-voice/multi-staff output from musicxml2ly.  In LilyPond, later
    global \\header blocks override earlier ones for conflicting keys, so the
    appended block wins over the generic header musicxml2ly generates.
    """
    _GENERIC = {'', 'music21', 'composer', 'title', 'score', 'untitled', 'new score', 'unknown'}
    raw_title: str = mxl_path.stem
    try:
        from ..notation.transposer import extract_metadata_from_musicxml
        metadata = extract_metadata_from_musicxml(mxl_path)

        # Raw title (before ASCII stripping) — returned for CJK overlay use
        _raw = (metadata.get('title', '') or '').strip()
        if _raw and _raw.lower() not in _GENERIC:
            raw_title = _raw

        # 过滤非 ASCII，LilyPond 默认字体不支持 CJK
        title = _ascii_only(raw_title)
        if not title or title.lower() in _GENERIC:
            title = _ascii_only(mxl_path.stem)
        composer = _ascii_only((metadata.get('composer', '') or '').strip())
        if composer.lower() in _GENERIC:
            composer = ''

        def _esc(s: str) -> str:
            return s.replace('\\', '\\\\').replace('"', '\\"')

        # Build header override block:
        # - subtitle: always cleared (musicxml2ly mirrors title → subtitle).
        # - composer: always cleared if it's a generic placeholder like "Music21".
        # - title: only overridden when the ASCII-filtered title is non-empty.
        #   When the raw title is CJK-only (ascii title == ""), we leave the
        #   original musicxml2ly CJK title intact so LilyPond preserves its normal
        #   title-area spacing.  The garbled rendered title is wiped and replaced
        #   by _overlay_cjk_title_on_staff_pdf() after LilyPond renders the PDF.
        parts: list[str] = [
            '  subtitle = ""',
            f'  composer = "{_esc(composer)}"',
        ]
        if title:  # non-empty ASCII title → override
            parts.insert(0, f'  title = "{_esc(title)}"')
        elif _has_cjk(raw_title):
            # CJK-only title: musicxml2ly may or may not have written a title into
            # the .ly file.  If the MusicXML had no title tag, LilyPond will not
            # reserve any vertical space for the header, making the music start at
            # y≈5.  Injecting a visible non-whitespace placeholder forces LilyPond
            # to always reserve the title area, so the CJK overlay step can later
            # place the correct text there without covering the first staff line.
            parts.insert(0, '  title = "."')

        ly_content = ly_path.read_text(encoding='utf-8', errors='ignore')
        header_block = '\\header {\n' + '\n'.join(parts) + '\n}\n'
        ly_path.write_text(ly_content.rstrip('\n') + '\n' + header_block, encoding='utf-8')
        LOGGER.debug('_inject_metadata_to_lilypond: 追加元数据头 title="%s"', title)
    except Exception as exc:
        LOGGER.debug('_inject_metadata_to_lilypond 失败: %s', exc)
    return raw_title


def render_musicxml_staff_pdf(mxl_path: Path, out_dir: Path) -> Optional[Path]:
    """将 MusicXML 渲染为标准五线谱 PDF（不经简谱转换）。

    流程：musicxml2ly.py（LilyPond 附带）→ .ly → [注入元数据] → LilyPond → PDF。
    返回生成的 PDF 路径，失败返回 None。
    """
    lilypond_exe = find_lilypond_executable()
    if lilypond_exe is None:
        log_message('未找到 LilyPond，无法渲染五线谱预览。', logging.WARNING)
        return None

    # 找 musicxml2ly.py：优先取 lilypond.exe 同目录
    lilypond_bin = Path(lilypond_exe).parent
    musicxml2ly = lilypond_bin / 'musicxml2ly.py'
    if not musicxml2ly.exists():
        log_message('未找到 musicxml2ly.py，无法将 MusicXML 转换为 LilyPond 格式。', logging.WARNING)
        return None

    # 找可运行 musicxml2ly.py 的 Python（优先 LilyPond 捆绑版）
    python_exe: Optional[Path] = None
    for candidate in [
        lilypond_bin / 'python.exe',
        lilypond_bin / 'python',
    ]:
        if candidate.exists():
            python_exe = candidate
            break
    if python_exe is None:
        import sys as _sys
        python_exe = Path(_sys.executable)

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    mxl_path = mxl_path.resolve()
    # Use a fixed ASCII-only filename for the .ly file.  musicxml2ly (LilyPond's
    # bundled Python 3.10) cannot open output files whose absolute path contains
    # non-ASCII (CJK) characters on Windows — open() raises FileNotFoundError.
    # The out_dir already uniquely identifies the score, so a constant basename
    # is safe.  The final PDF is renamed to include the stem at the end.
    ly_path = out_dir / '_staff.ly'

    # Step 0: Pre-fix adjacent backward-repeat barlines in the MusicXML (OMR
    # artefact: combined :|.|: barline split into two backward-repeats).
    mxl_for_ly = _fix_adjacent_backward_repeats_in_mxl(mxl_path)

    # Step 1: musicxml2ly → .ly
    try:
        result = subprocess.run(
            [str(python_exe), str(musicxml2ly), '-o', str(ly_path), str(mxl_for_ly)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(out_dir), timeout=60,
            creationflags=_WIN_NO_WINDOW,
        )
        if result.returncode != 0 or not ly_path.exists():
            err = (result.stderr or b'').decode('utf-8', errors='ignore').strip()
            log_message(f'musicxml2ly 转换失败: {err[:500]}', logging.WARNING)
            return None
    except Exception as exc:
        log_message(f'musicxml2ly 执行出错: {exc}', logging.WARNING)
        return None

    # Step 2: Fix deprecated #'property syntax emitted by older musicxml2ly builds
    try:
        raw = ly_path.read_text(encoding='utf-8', errors='ignore')
        fixed = _fix_deprecated_ly_syntax(raw)
        if fixed != raw:
            ly_path.write_text(fixed, encoding='utf-8')
    except Exception:
        pass

    # Step 3: Append title/composer from MusicXML metadata; raw_title is the
    # pre-ASCII-stripping title (may contain CJK) returned for the overlay step.
    raw_title = _inject_metadata_to_lilypond(ly_path, mxl_path)

    # Step 4: LilyPond → PDF（使用已有的 render_lilypond_pdf）
    pdf_path = render_lilypond_pdf(ly_path)

    # Step 5: Overlay CJK title if the raw title contains non-ASCII characters
    # (LilyPond rendered with title="" leaving a blank title area at the top).
    if pdf_path is not None and _has_cjk(raw_title):
        _overlay_cjk_title_on_staff_pdf(pdf_path, raw_title)

    return pdf_path




# ──────────────────────────────────────────────
# 向后兼容再导出（jianpu 相关功能已移至 jianpu_runner.py）
# ──────────────────────────────────────────────
from .jianpu_runner import (  # noqa: E402, F401
    find_jianpu_ly_command,
    find_jianpu_ly_module,
    find_jianpu_ly_script,
    download_jianpu_ly_script,
    find_python_script_command,
    render_jianpu_ly,
    render_jianpu_ly_from_mxl,
    merge_polyphonic_jianpu_staves,
    inject_repeat_barlines_to_ly,
)
