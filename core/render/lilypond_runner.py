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
    """Locate a CJK-capable TrueType/OpenType font file for text overlay.

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

    When the score title is CJK-only, musicxml2ly emits raw CJK bytes into the
    LilyPond \\header block which LilyPond's default font renders as empty boxes.
    This function blanks out the garbled title area with a white rectangle and
    draws the correct CJK text centered above it using ReportLab + pypdf.

    Strategy: create a single-page overlay PDF (white rect + CJK title) with
    ReportLab, then merge it on top of the original PDF with pypdf.  LilyPond
    always reserves title-area space via the "." placeholder injected by
    _inject_metadata_to_lilypond, so a fixed cover band (y ≈ 5–40 pt from top)
    reliably blanks the right region without needing text-position detection.
    """
    if not title:
        return
    font_path = _find_cjk_font_for_overlay()
    if font_path is None:
        LOGGER.debug('_overlay_cjk_title_on_staff_pdf: 未找到 CJK 字体，跳过叠加')
        return
    try:
        import io as _io
        import pypdf
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.units import pt

        # ── 读取原始 PDF，取第一页尺寸 ──────────────────────────────────────
        reader = pypdf.PdfReader(str(pdf_path))
        if len(reader.pages) == 0:
            return
        first_page = reader.pages[0]
        pw = float(first_page.mediabox.width)   # points
        ph = float(first_page.mediabox.height)  # points

        # ── 用 ReportLab 生成叠加层（白色遮盖矩形 + CJK 标题文字）──────────
        # LilyPond 坐标系原点在页面左下角；标题区在顶部，
        # 即 y ∈ [ph-40, ph-5]（pt）
        cover_top    = ph - 5.0
        cover_bottom = ph - 40.0

        font_name = 'NotoSansSC'
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))

        overlay_buf = _io.BytesIO()
        c = rl_canvas.Canvas(overlay_buf, pagesize=(pw, ph))
        # 白色遮盖矩形
        c.setFillColorRGB(1, 1, 1)
        c.setStrokeColorRGB(1, 1, 1)
        c.rect(0, cover_bottom, pw, cover_top - cover_bottom, fill=1, stroke=0)
        # CJK 标题文字（居中，字号 15pt）
        c.setFillColorRGB(0, 0, 0)
        c.setFont(font_name, 15)
        text_y = cover_bottom + (cover_top - cover_bottom - 15) / 2
        c.drawCentredString(pw / 2, text_y, title)
        c.save()
        overlay_buf.seek(0)

        # ── 用 pypdf 把叠加层合并到原始 PDF 第一页上 ────────────────────────
        overlay_reader = pypdf.PdfReader(overlay_buf)
        overlay_page  = overlay_reader.pages[0]
        first_page.merge_page(overlay_page)

        writer = pypdf.PdfWriter()
        writer.add_page(first_page)
        for p in reader.pages[1:]:
            writer.add_page(p)

        tmp_path = pdf_path.with_suffix('.tmp.pdf')
        with open(str(tmp_path), 'wb') as f:
            writer.write(f)
        tmp_path.replace(pdf_path)
        LOGGER.debug('_overlay_cjk_title_on_staff_pdf: 已叠加标题 "%s"', title)
    except Exception as exc:
        LOGGER.debug('_overlay_cjk_title_on_staff_pdf: 叠加失败: %s', exc)

def _inject_metadata_to_lilypond(ly_path: Path, mxl_path: Path) -> tuple[str, str]:
    """Append a \\header block with title/composer from MusicXML metadata at EOF.

    Returns ``(raw_title, raw_composer)`` — the pre-ASCII-stripping values so
    callers can pass the full Unicode text to the markup-injection step without
    a second metadata parse.  The appended header always sets ``title = ""``;
    the actual title display is handled by _apply_title_markup_to_staff_ly().

    Appending is safe for any LilyPond file structure, including complex
    multi-voice/multi-staff output from musicxml2ly.  In LilyPond, later
    global \\header blocks override earlier ones for conflicting keys, so the
    appended block wins over the generic header musicxml2ly generates.
    """
    _GENERIC = {'', 'music21', 'composer', 'title', 'score', 'untitled', 'new score', 'unknown'}
    raw_title: str = mxl_path.stem
    raw_composer: str = ''
    try:
        from ..notation.transposer import extract_metadata_from_musicxml
        metadata = extract_metadata_from_musicxml(mxl_path)

        # Raw title/composer (before ASCII stripping) — returned for markup use
        _raw = (metadata.get('title', '') or '').strip()
        if _raw and _raw.lower() not in _GENERIC:
            raw_title = _raw

        raw_composer = (metadata.get('composer', '') or '').strip()
        if raw_composer.lower() in _GENERIC:
            raw_composer = ''

        # ASCII-only composer for the header block (font block will handle CJK
        # in the markup; the header composer is only a fallback).
        composer_ascii = _ascii_only(raw_composer)

        def _esc(s: str) -> str:
            return s.replace('\\', '\\\\').replace('"', '\\"')

        # Build header override block:
        # - title: always "" — _apply_title_markup_to_staff_ly() injects a
        #   \markup block with font support before \score {}, which both
        #   reserves the title area and renders CJK correctly.
        # - subtitle: always cleared (musicxml2ly mirrors title → subtitle).
        # - composer: ASCII-stripped fallback; the markup block shows the full text.
        parts: list[str] = [
            '  title = ""',
            '  subtitle = ""',
            f'  composer = "{_esc(composer_ascii)}"',
            '  tagline = ##f',
        ]

        ly_content = ly_path.read_text(encoding='utf-8', errors='ignore')
        header_block = '\\header {\n' + '\n'.join(parts) + '\n}\n'
        ly_path.write_text(ly_content.rstrip('\n') + '\n' + header_block, encoding='utf-8')
        LOGGER.debug('_inject_metadata_to_lilypond: 追加元数据头 title="%s"', raw_title)
    except Exception as exc:
        LOGGER.debug('_inject_metadata_to_lilypond 失败: %s', exc)
    return raw_title, raw_composer


def _apply_title_markup_to_staff_ly(ly_path: Path, raw_title: str, raw_composer: str = '') -> None:
    """Inject a CJK-capable font block and \\markup title into a staff .ly file.

    Mirrors the title-injection logic in sanitize_generated_lilypond_file() for
    musicxml2ly-generated staff files: inserts a LilyPond \\markup block (with
    the bundled CJK font) before the first \\score {}, so both CJK and ASCII
    titles render correctly without any post-render overlay.
    """
    try:
        from .renderer import ensure_lilypond_font_block, build_lilypond_title_markup
        text = ly_path.read_text(encoding='utf-8', errors='ignore')
        text = ensure_lilypond_font_block(text)
        title_markup = build_lilypond_title_markup(raw_title, raw_composer)
        if (
            title_markup
            and '\\score {' in text
            and '\\fill-line { \\fontsize #3 \\bold' not in text.split('\\score {', 1)[0]
        ):
            text = text.replace('\\score {', title_markup + '\\score {', 1)
        ly_path.write_text(text, encoding='utf-8')
        LOGGER.debug('_apply_title_markup_to_staff_ly: 已注入标题标记 "%s"', raw_title)
    except Exception as exc:
        LOGGER.debug('_apply_title_markup_to_staff_ly 失败: %s', exc)


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

    # Step 3: Append title/composer from MusicXML metadata.
    raw_title, raw_composer = _inject_metadata_to_lilypond(ly_path, mxl_path)

    # Step 3b: Inject font block + \markup title block — same approach as the
    # jianpu rendering pipeline.  This handles both CJK and ASCII titles via
    # the bundled NotoSansSC font, replacing the previous ReportLab overlay hack.
    _apply_title_markup_to_staff_ly(ly_path, raw_title, raw_composer)

    # Step 4: LilyPond → PDF（使用已有的 render_lilypond_pdf）
    pdf_path = render_lilypond_pdf(ly_path)

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
