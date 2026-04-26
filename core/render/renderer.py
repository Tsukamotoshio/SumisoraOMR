# core/renderer.py — 乐谱渲染（LilyPond PDF / MIDI / 直接 PDF）
# 拆分自 convert.py
import logging
import os
import re
import shutil
import textwrap
from pathlib import Path
from typing import Optional

from music21 import converter, metadata as m21metadata

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from ..config import (
    LOGGER,
    JianpuNote,
)
from ..music.jianpu_core import (
    build_jianpu_ly_text,
    build_jianpu_ly_text_from_measures,
    choose_measures_per_line,
    extract_jianpu_measures,
    format_jianpu_note_text,
    get_duration_render,
    jianpu_note_token,
    parse_score_to_jianpu,
)
from .lilypond_runner import (
    merge_polyphonic_jianpu_staves,
    render_jianpu_ly,
    render_lilypond_pdf,
)
from ..utils import (
    collect_preserved_lyrics_lines,
    log_message,
    register_pdf_font,
    resolve_lilypond_font_name,
    safe_remove_file,
)


def render_midi_from_score(score, midi_path: Path) -> bool:
    """Export a music21 score to a MIDI file; return True on success."""
    try:
        midi_path.parent.mkdir(parents=True, exist_ok=True)
        score.write('midi', fp=str(midi_path))
        log_message(f'已生成 MIDI 文件: {midi_path.name}')
        return True
    except Exception as exc:
        log_message(f'生成 MIDI 失败: {midi_path.name}，原因: {exc}', logging.WARNING)
        return False


def build_score_from_jianpu_measures(
    measures: list[list[JianpuNote]],
    time_signature: str = '4/4',
    source_score=None,
):
    """Reconstruct a music21 Score from extracted jianpu note measures for MIDI export.

    Uses the MIDI pitch stored in each JianpuNote so the exported MIDI exactly
    matches the notes rendered in the jianpu PDF (monophonic melody from part 0,
    polyphony collapsed to top note).  Tempo is copied from *source_score* when
    supplied so the playback speed matches the original score.
    """
    from music21 import meter as m21meter, note as m21note, stream as m21stream, tempo as m21tempo

    s = m21stream.Score()
    p = m21stream.Part()
    p.append(m21meter.TimeSignature(time_signature))

    # Copy the first MetronomeMark from the source score so MIDI tempo matches.
    if source_score is not None:
        try:
            tempos = list(source_score.flatten().getElementsByClass(m21tempo.MetronomeMark))
            if tempos:
                p.append(tempos[0])
        except Exception:
            pass

    for measure_notes in measures:
        m = m21stream.Measure()
        for jn in measure_notes:
            if jn.is_rest or jn.midi is None:
                r = m21note.Rest()
                r.duration.quarterLength = jn.duration
                m.append(r)
            else:
                n = m21note.Note()
                n.pitch.midi = jn.midi
                n.duration.quarterLength = jn.duration
                m.append(n)
        p.append(m)

    s.append(p)
    return s


def load_score_from_midi(midi_path: Path):
    """Rebuild a music21 score from a MIDI file."""
    try:
        rebuilt_score = converter.parse(str(midi_path))
        log_message(f'已从 MIDI 重建乐谱: {midi_path.name}')
        return rebuilt_score
    except Exception as exc:
        log_message(f'读取 MIDI 失败: {midi_path.name}，原因: {exc}', logging.WARNING)
        return None


def apply_score_title(score, title: str) -> None:
    """Embed a title into the music21 score metadata."""
    if not title:
        return
    try:
        if getattr(score, 'metadata', None) is None:
            score.metadata = m21metadata.Metadata()
        score.metadata.title = title
        score.metadata.movementName = title
    except Exception:
        pass


def draw_jianpu_measure(c: canvas.Canvas, x: float, y: float, notes: list[JianpuNote], width: float, font_name: str) -> None:
    """Draw a single jianpu measure on the PDF canvas with notes, octave dots, underlines, and bar lines."""
    baseline_y = y
    c.setLineWidth(0.5)
    c.setStrokeColorRGB(0.5, 0.5, 0.5)
    c.line(x, baseline_y - 12, x + width, baseline_y - 12)

    cell_width = width / max(len(notes), 1)
    font_size = max(9, min(18, cell_width * 0.78))
    half_span = max(3.5, min(8.0, cell_width * 0.3))
    right_step = max(4.0, min(7.0, cell_width * 0.24))
    dash_len = max(4.0, min(7.0, cell_width * 0.28))
    dot_radius = max(1.0, min(1.8, font_size * 0.09))
    upper_start = note_y_offset = font_size * 0.95

    for idx, note in enumerate(notes):
        cx = x + idx * cell_width + cell_width / 2
        note_y = y
        c.setFont(font_name, font_size)
        c.drawCentredString(cx, note_y, note.accidental + note.symbol)

        for k in range(note.upper_dots):
            c.circle(cx, note_y + upper_start + k * (dot_radius * 3.2), dot_radius, fill=1)
        for k in range(note.lower_dots):
            c.circle(cx, note_y - (font_size * 0.55) - k * (dot_radius * 3.2), dot_radius, fill=1)

        dashes, underlines, right_dots = get_duration_render(note.duration, note.duration_dots)
        c.setLineWidth(1)
        if underlines > 0:
            for k in range(underlines):
                line_y = note_y - (font_size * 0.9) - k * (dot_radius * 3.0)
                c.line(cx - half_span, line_y, cx + half_span, line_y)
        if dashes > 0:
            for k in range(dashes):
                dash_x = cx + half_span + 2 + k * (dash_len + 2)
                c.line(dash_x, note_y - 3, dash_x + dash_len, note_y - 3)
        if right_dots > 0:
            for k in range(right_dots):
                dot_x = cx + half_span + 3 + k * right_step
                c.circle(dot_x, note_y + 2, dot_radius, fill=1)

    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(1.0)
    c.line(x, baseline_y - 18, x, baseline_y + 14)
    c.line(x + width, baseline_y - 18, x + width, baseline_y + 14)


def create_pdf(
    pdf_path: Path,
    title: str,
    measures: list[list[JianpuNote]],
    header_lines: list[str],
    font_name: str,
    lyrics_lines: Optional[list[str]] = None,
) -> None:
    """Render the jianpu score to a PDF using reportlab, supporting lyrics and multi-page output."""
    page_width, page_height = A4
    c = canvas.Canvas(str(pdf_path), pagesize=A4)

    def draw_page_header(current_y: float) -> float:
        """Draw the title and key/time header at the top of the current page; return updated y."""
        c.setFont(font_name, 14)
        c.drawString(50, current_y, title)
        current_y -= 26
        c.setFont(font_name, 12)
        for header_line in header_lines[:2]:
            c.drawString(50, current_y, header_line)
            current_y -= 18
        return current_y - 10

    y = draw_page_header(page_height - 50)
    measures_per_line = choose_measures_per_line(measures)
    measure_width = (page_width - 100) / measures_per_line
    lyric_index = 0
    normalized_lyrics = [line for line in (lyrics_lines or []) if line.strip()]

    for i in range(0, len(measures), measures_per_line):
        line_measures = measures[i:i + measures_per_line]
        needed_height = 78 + (28 if lyric_index < len(normalized_lyrics) else 0)
        if y < 60 + needed_height:
            c.showPage()
            y = draw_page_header(page_height - 50)

        x = 50
        for measure in line_measures:
            draw_jianpu_measure(c, x, y, measure, measure_width, font_name)
            x += measure_width
        y -= 58

        if lyric_index < len(normalized_lyrics):
            c.setFont(font_name, 9)
            lyric_line = normalized_lyrics[lyric_index]
            lyric_index += 1
            wrapped = textwrap.wrap(lyric_line, width=72) or ['']
            for idx, wrapped_line in enumerate(wrapped[:2]):
                prefix = '歌词: ' if idx == 0 else '      '
                c.drawString(55, y, prefix + wrapped_line)
                y -= 12
            y -= 6
        else:
            y -= 10

    if lyric_index < len(normalized_lyrics):
        if y < 140:
            c.showPage()
            y = draw_page_header(page_height - 50)
        c.setFont(font_name, 12)
        c.drawString(50, y, '剩余歌词参考:')
        y -= 18
        c.setFont(font_name, 10)
        for lyric_line in normalized_lyrics[lyric_index:]:
            for wrapped_line in textwrap.wrap(lyric_line, width=60) or ['']:
                if y < 60:
                    c.showPage()
                    y = draw_page_header(page_height - 50)
                    c.setFont(font_name, 10)
                c.drawString(50, y, wrapped_line)
                y -= 14

    c.save()


def escape_lilypond_text(text: str) -> str:
    """Escape backslashes and double quotes for LilyPond string literals."""
    return text.replace('\\', '\\\\').replace('"', '\\"')


def build_lilypond_font_block() -> str:
    """Build a LilyPond `#(define fonts ...)` block mapping all font families to the detected CJK font."""
    font_name = resolve_lilypond_font_name()
    if not font_name:
        return ''

    escaped_font_name = escape_lilypond_text(font_name)
    return (
        '#(define fonts\n'
        '  (set-global-fonts\n'
        '   #:music "emmentaler"\n'
        '   #:brace "emmentaler"\n'
        f'   #:roman "{escaped_font_name}"\n'
        f'   #:sans "{escaped_font_name}"\n'
        f'   #:typewriter "{escaped_font_name}"))\n'
    )


def ensure_lilypond_font_block(text: str) -> str:
    """Insert the font block after \\version (or at the top) if not already present."""
    font_block = build_lilypond_font_block()
    if not font_block or '#(define fonts' in text:
        return text

    if text.startswith('\\version'):
        first_line, separator, remainder = text.partition('\n')
        return first_line + separator + font_block + remainder
    return font_block + text


def build_lilypond_title_markup(title: str, composer: str = '') -> str:
    """Build a LilyPond \\markup block that displays the title (and optional composer) at the top of the page."""
    safe_title = escape_lilypond_text(title.strip())
    if not safe_title:
        return ''

    lilypond_font_name = resolve_lilypond_font_name() or 'Sans'
    safe_font_name = escape_lilypond_text(lilypond_font_name)
    composer_line = ''
    if composer.strip():
        safe_composer = escape_lilypond_text(composer.strip())
        composer_line = f'    \\fill-line {{ "" \\fontsize #1 \\italic "{safe_composer}" }}\n'
    return (
        '\\markup {\n'
        f'  \\override #\'(font-name . "{safe_font_name}")\n'
        '  \\column {\n'
        f'    \\fill-line {{ \\fontsize #3 \\bold "{safe_title}" }}\n'
        f'{composer_line}'
        '    \\vspace #1\n'
        '  }\n'
        '}\n'
    )


def build_lilypond_lyrics_markup(lyrics_lines: Optional[list[str]]) -> str:
    """Build a LilyPond \\markup block that displays lyrics below the score."""
    if not lyrics_lines:
        return ''

    lilypond_font_name = resolve_lilypond_font_name() or 'Sans'
    safe_font_name = escape_lilypond_text(lilypond_font_name)
    body_lines = '\n'.join(
        f'    \\line {{ "{escape_lilypond_text(line)}" }}' for line in lyrics_lines[:20] if line.strip()
    )
    if not body_lines:
        return ''

    return (
        '\\markup {\n'
        f'  \\override #\'(font-name . "{safe_font_name}")\n'
        '  \\override #\'(baseline-skip . 4.6)\n'
        '  \\column {\n'
        '    \\vspace #0.6\n'
        '    \\line { \\bold "歌词参考" }\n'
        f'{body_lines}\n'
        '    \\vspace #1.2\n'
        '  }\n'
        '}\n'
    )


def render_lilypond_markup_pdf(
    output_pdf_path: Path,
    title: str,
    measures: list[list[JianpuNote]],
    header_lines: list[str],
    temp_dir: Path,
    lyrics_lines: Optional[list[str]] = None,
) -> bool:
    """Generate a jianpu PDF using LilyPond plain-text markup mode; fallback when direct PDF fails."""
    ly_path = temp_dir / f'{output_pdf_path.stem}.direct.ly'
    line_texts = [line for line in header_lines if line.strip()]
    measures_per_line = choose_measures_per_line(measures)
    for i in range(0, len(measures), measures_per_line):
        line_measures = measures[i:i + measures_per_line]
        measure_texts = [' '.join(format_jianpu_note_text(note) for note in measure) for measure in line_measures]
        line_texts.append(' | '.join(measure_texts) + ' |')

    if lyrics_lines:
        line_texts.append('')
        line_texts.append('歌词参考:')
        line_texts.extend(lyrics_lines[:20])

    markup_lines = '\n'.join(f'      \\line {{ "{escape_lilypond_text(line)}" }}' for line in line_texts)
    lilypond_font_name = resolve_lilypond_font_name() or 'Courier New'
    ly_content = f'''\\version "2.24.4"
{build_lilypond_font_block()}\\paper {{
  #(set-paper-size "a4")
  indent = 0
  print-page-number = ##f
  top-margin = 18\\mm
  bottom-margin = 12\\mm
  left-margin = 12\\mm
  right-margin = 12\\mm
}}
\\header {{
  tagline = ##f
}}
\\markup {{
  \\override #'(baseline-skip . 8)
  \\override #'(font-name . "{escape_lilypond_text(lilypond_font_name)}")
  \\column {{
    \\fill-line {{ \\fontsize #3 \\bold "{escape_lilypond_text(title)}" }}
    \\vspace #2.5
{markup_lines}
  }}
}}
'''
    try:
        ly_path.write_text(ly_content, encoding='utf-8')
        pdf_path = render_lilypond_pdf(ly_path)
        if pdf_path is not None and pdf_path.exists():
            if str(pdf_path.resolve()) != str(output_pdf_path.resolve()):
                shutil.copy(pdf_path, output_pdf_path)
            log_message(f'已通过 LilyPond 文字版简谱生成 PDF: {output_pdf_path.name}')
            return True
    except OSError as exc:
        log_message(f'LilyPond 文字版简谱生成失败: {exc}', logging.WARNING)
    return False


def _ensure_tagline_suppressed(text: str) -> str:
    """Inject `tagline = ##f` into the global LilyPond \\header to suppress the default footer."""
    score_idx = text.find('\\score')
    global_text = text[:score_idx] if score_idx >= 0 else text
    if 'tagline' in global_text:
        return text  # already handled in global section
    if '\\header' in global_text:
        text = re.sub(r'(\\header\s*\{)', r'\1\n  tagline = ##f', text, count=1)
    elif score_idx >= 0:
        text = text[:score_idx] + '\\header { tagline = ##f }\n' + text[score_idx:]
    else:
        text = '\\header { tagline = ##f }\n' + text
    return text


def _fix_deprecated_override_syntax(text: str) -> str:
    r"""Convert deprecated LilyPond 2.24 property-path syntax to dot notation.

    Old (deprecated since LilyPond 2.24):  \override Foo #'bar = ...
    New (required in LilyPond 2.25+):       \override Foo.bar = ...

    Handles both \override and \tweak, with or without \once / \temporary prefix.
    This is a pure text substitution — safe to apply to any generated .ly content.
    """
    # Pattern: ClassName followed by whitespace then #'property-name
    # Replace with dot notation: ClassName.property-name
    # Covers: Slur, Tie, Beam, NoteHead, Rest, ... and any kebab-case property
    return re.sub(
        r'(\b[A-Z][A-Za-z]+)\s+#\'([a-z][a-z0-9-]*)',
        r'\1.\2',
        text,
    )


def sanitize_generated_lilypond_file(
    ly_path: Path,
    preferred_title: str,
    lyrics_lines: Optional[list[str]] = None,
    composer: str = '',
) -> None:
    """
    Post-process a jianpu-ly .ly file: set title, strip 5-line staves,
    insert title/lyrics markup, configure fonts, and suppress the tagline.
    """
    if not ly_path.exists():
        return

    text = ly_path.read_text(encoding='utf-8', errors='ignore')

    # If caller didn't supply composer, try to read it from the generated \header block
    # (jianpu-ly.py emits \header { composer="..." } when the .txt file has composer=... ).
    if not composer:
        _cm = re.search(r'composer\s*=\s*"([^"]*)"', text)
        if _cm:
            composer = _cm.group(1).strip()

    title_markup = build_lilypond_title_markup(preferred_title, composer)
    lyrics_markup = build_lilypond_lyrics_markup(lyrics_lines)

    # Convert deprecated #'property syntax to dot notation (LilyPond 2.24+)
    text = _fix_deprecated_override_syntax(text)

    # Suppress title and composer from LilyPond's default header — custom markup handles display.
    _header_clear = '\\header { title="" composer="" instrument="" tagline=##f }'

    if '% === BEGIN JIANPU STAFF ===' in text and '% === END JIANPU STAFF ===' in text:
        preamble = text.split('\\score {', 1)[0]
        preamble = re.sub(r'^instrument=.*$', '', preamble, flags=re.M)
        preamble = preamble.replace('WithStaff NextPart', 'NextPart')
        # Capture ALL sections from the first BEGIN to the last END (greedy match).
        match = re.search(r'(%+\s*===\s*BEGIN JIANPU STAFF\s*===.*%+\s*===\s*END JIANPU STAFF\s*===)', text, flags=re.S)
        if match:
            jianpu_section = match.group(1).replace('WithStaff NextPart', 'NextPart')
            rebuilt = (
                preamble
                + '\n\\score {\n<<\n'
                + jianpu_section
                + f'\n>>\n\\header{{\n  title=""\n  composer=""\n  instrument=""\n  tagline=##f\n}}\n\\layout{{}}\n}}\n'
            )
            extra_markup = title_markup + lyrics_markup
            if extra_markup and '\\score {' in rebuilt and '\\fill-line { \\fontsize #3 \\bold' not in rebuilt.split('\\score {', 1)[0]:
                rebuilt = rebuilt.replace('\\score {', extra_markup + '\\score {', 1)
            ly_path.write_text(_ensure_tagline_suppressed(ensure_lilypond_font_block(rebuilt)), encoding='utf-8')
            return

    text = text.replace('WithStaff NextPart', 'NextPart')
    text = re.sub(r'% === BEGIN 5-LINE STAFF ===.*?% === END 5-LINE STAFF ===\s*', '', text, flags=re.S)
    text = re.sub(r'^instrument=.*$', '', text, flags=re.M)
    text = re.sub(r'instrument="[^"]*"', 'instrument=""', text)
    # Use custom markup for title/composer; clear them from LilyPond's default header to avoid duplicates
    if '\\paper {' in text:
        text = text.replace('\\paper {', f'{_header_clear}\n\\paper {{', 1)
    else:
        text = f'{_header_clear}\n' + text
    extra_markup = title_markup + lyrics_markup
    if extra_markup and '\\score {' in text and '\\fill-line { \\fontsize #3 \\bold' not in text.split('\\score {', 1)[0]:
        text = text.replace('\\score {', extra_markup + '\\score {', 1)
    ly_path.write_text(_ensure_tagline_suppressed(ensure_lilypond_font_block(text)), encoding='utf-8')


def copy_generated_pdf(src: Path, output_pdf_path: Path) -> None:
    """Copy the generated PDF to the target path, skipping if source and dest are the same."""
    try:
        if str(src.resolve()) != str(output_pdf_path.resolve()):
            shutil.copy(src, output_pdf_path)
    except OSError:
        if str(src) != str(output_pdf_path):
            shutil.copy(src, output_pdf_path)


def render_score_to_jianpu_pdf(
    score,
    title: str,
    output_pdf_path: Path,
    temp_dir: Path,
    txt_path: Path,
    ly_path: Path,
    lyrics_lines: Optional[list[str]] = None,
    composer: str = '',
    tempo: int = 0,
) -> bool:
    """
    Render a music21 score to jianpu PDF.
    Tries in order: standard jianpu-ly → strict-timing → reportlab fallback → LilyPond markup fallback.
    """
    try:
        txt_content, _voice_groups = build_jianpu_ly_text(
            score, title, composer=composer, tempo=tempo, _return_groups=True)
    except Exception as exc:
        _voice_groups = []
        log_message(f'[jianpu] 标准 jianpu-ly 文本生成失败 ({exc})，尝试使用简化处理', logging.WARNING)
        try:
            from ..music.jianpu_core import parse_score_to_jianpu, build_jianpu_ly_text_from_measures
            measures, header_lines, time_sig = parse_score_to_jianpu(score)
            key_name = header_lines[0].split()[0] if header_lines else '1=C'
            txt_content = build_jianpu_ly_text_from_measures(measures, time_sig, key_name, title,
                                                              composer=composer, tempo=tempo)
        except Exception as exc2:
            log_message(f'[jianpu] 简化处理也失败: {exc2}，继续尝试其他方式', logging.WARNING)
            txt_content = None

    if txt_content:
        txt_path.write_text(txt_content, encoding='utf-8')
        log_message(f'已生成 jianpu-ly 文本文件: {txt_path.name}')

    try:
        from ..music.jianpu_core import parse_score_to_jianpu
        measures, header_lines, _ = parse_score_to_jianpu(score)
    except Exception as exc:
        log_message(f'[jianpu] parse_score_to_jianpu 失败: {exc}，跳过中间产物保存', logging.WARNING)
        measures, header_lines = [], []

    if render_jianpu_ly(txt_path, ly_path):
        log_message(f'已生成 jianpu-ly LilyPond 文件: {ly_path.name}')
        sanitize_generated_lilypond_file(ly_path, title, lyrics_lines, composer=composer)
        merge_polyphonic_jianpu_staves(ly_path, _voice_groups)
        pdf_path = render_lilypond_pdf(ly_path)
        if pdf_path is not None:
            copy_generated_pdf(pdf_path, output_pdf_path)
            log_message(f'已通过 LilyPond 生成简谱 PDF: {output_pdf_path.name}')
            return True
        log_message('标准 jianpu-ly 路径失败，尝试严格按拍号重建简谱。', logging.WARNING)
    else:
        log_message('文本版 jianpu-ly 失败，尝试严格按拍号重建简谱。', logging.WARNING)

    strict_txt_content = build_jianpu_ly_text(score, title, use_strict_timing=True,
                                               composer=composer, tempo=tempo)
    txt_path.write_text(strict_txt_content, encoding='utf-8')
    if render_jianpu_ly(txt_path, ly_path):
        log_message(f'已通过严格拍号重建生成 jianpu-ly 文件: {ly_path.name}')
        sanitize_generated_lilypond_file(ly_path, title, lyrics_lines, composer=composer)
        pdf_path = render_lilypond_pdf(ly_path)
        if pdf_path is not None:
            copy_generated_pdf(pdf_path, output_pdf_path)
            log_message(f'已通过 LilyPond 生成简谱 PDF: {output_pdf_path.name}')
            return True
        log_message('MIDI 中转后的严格拍号路径仍失败，切换到备用简谱排版。', logging.WARNING)
    else:
        log_message('MIDI 中转后的严格拍号路径也失败，切换到备用简谱排版。', logging.WARNING)

    font_name = register_pdf_font()
    try:
        create_pdf(output_pdf_path, title, measures, header_lines, font_name, lyrics_lines)
        log_message(f'已生成备用简谱 PDF: {output_pdf_path.name}')
        return True
    except Exception as exc:
        log_message(f'图形化简谱回退失败，尝试文字版回退：{exc}', logging.WARNING)

    if render_lilypond_markup_pdf(output_pdf_path, title, measures, header_lines, temp_dir, lyrics_lines):
        return True

    return False


# ── Editor workspace helpers ─────────────────────────────────────────────────

_EDITOR_HEADER_TEMPLATE = """\
# ================================================================
# 简谱校对文件 — {title}
# 本文件由 OMR（光学乐谱识别）自动生成，您可以手动修改后重新生成 PDF
# 保存并关闭记事本，工具将使用本文件重新排版输出简谱 PDF
# ================================================================
#
# 【简谱符号说明】
#  数字音符：  1=Do  2=Re  3=Mi  4=Fa  5=Sol  6=La  7=Si  0=休止符
#  升降号：    #1=升Do  b3=降Mi（写在数字前面）
#  高八度：    1'=高音Do  1''=超高音Do（单引号 ' 写在数字后面）
#  低八度：    1,=低音Do  1,,=超低音Do（逗号 , 写在数字后面）
#  时值（四分音符=1拍）：
#    1        四分音符（1 拍）
#    1 -      二分音符（2 拍）
#    1 - -    附点二分（3 拍）
#    1 - - -  全音符（4 拍）
#    q1       八分音符（半拍）
#    s1       十六分音符（1/4 拍）
#    d1       三十二分音符（1/8 拍）
#    1.       附点四分音符（1.5 拍）
#    q1.      附点八分音符（0.75 拍）
#  小节线：    用 | 分隔小节
#  换行排版：  NextPart（不影响音高，仅控制排版换行）
#  注意：以 # 开头的行是注释，不影响转换
# ================================================================

"""


def _build_editor_header(title: str) -> str:
    """Return a #-prefixed instructional header for the editor .jianpu.txt file."""
    return _EDITOR_HEADER_TEMPLATE.format(title=title)


def _build_validation_annotation(errors: list) -> str:
    """将 omr_validator 的错误列表序列化为 % 注释块，追加到编辑器文件末尾。

    使用 ``%`` 前缀确保：
    - jianpu-ly.py 将这些行视为注释并忽略（官方文档：``% a comment`` 被忽略）。
    - 若这些行意外出现在 .ly 中，LilyPond 也会将 ``%`` 视为单行注释，不会报错。
    """
    sep = '% ' + '─' * 58
    lines: list[str] = [
        '',
        sep,
        '% ⚠  OMR 节拍校验结果（自动生成，供人工核查参考）',
        sep,
    ]
    for e in errors:
        status = '↑ TOO_LONG' if e['delta'] > 0 else '↓ TOO_SHORT'
        lines.append(
            f"% 小节 {e['measure_index']:>4}: {status}"
            f"（实际 {e['total_beats']} 拍，期望 {e['expected_beats']} 拍，"
            f"差 {e['delta']:+.3g}）"
        )
        if e.get('hint'):
            lines.append(f"%{'':>12}提示：{e['hint']}")
    lines.append(sep)
    return '\n'.join(lines) + '\n'


def _save_editor_files(
    title: str,
    txt_path: Path,
    source_path: Optional[Path],
    editor_workspace_dir: Path,
    validation_errors: Optional[list] = None,
) -> None:
    """Copy the jianpu.txt (prepending a human-readable header) and the original
    source file into *editor_workspace_dir* so the user can later manually edit
    and re-render the score.

    If *validation_errors* is provided (from omr_validator.validate_measures),
    a ``%``-prefixed comment block is appended at the end of the .jianpu.txt
    to flag rhythm-inconsistent measures for manual review.  The ``%`` prefix
    ensures these lines are treated as comments by both jianpu-ly.py and LilyPond.
    """
    try:
        editor_workspace_dir.mkdir(parents=True, exist_ok=True)

        # Sanitise the title for use as a filename stem
        safe_title = title.strip()
        for ch in r'\/:*?"<>|':
            safe_title = safe_title.replace(ch, '_')
        if not safe_title:
            safe_title = 'untitled'

        # Save annotated jianpu.txt
        dest_txt = editor_workspace_dir / f'{safe_title}.jianpu.txt'
        if txt_path.exists():
            original = txt_path.read_text(encoding='utf-8', errors='ignore')
            content = _build_editor_header(title) + original
            if validation_errors:
                content += _build_validation_annotation(validation_errors)
            dest_txt.write_text(content, encoding='utf-8')

        # Copy the original source file as a reference image
        if source_path is not None and source_path.exists():
            dest_src = editor_workspace_dir / f'{safe_title}.source{source_path.suffix.lower()}'
            # 删除同名但后缀不同的旧 source 文件（例如重新识别时 .jpg → .png），
            # 防止旧版本残留在工作区中误导用户。
            for old_src in editor_workspace_dir.glob(f'{safe_title}.source.*'):
                if old_src != dest_src:
                    try:
                        old_src.unlink()
                    except OSError:
                        pass
            shutil.copy2(str(source_path), str(dest_src))
    except OSError as exc:
        log_message(f'保存编辑器工作区文件失败: {exc}', logging.WARNING)


def generate_jianpu_pdf_from_mxl(
    mxl_path: Path,
    output_pdf_path: Path,
    temp_dir: Path,
    midi_output_path: Optional[Path] = None,
    preferred_title: Optional[str] = None,
    source_path: Optional[Path] = None,
    editor_workspace_dir: Optional[Path] = None,
    composer: str = '',
    tempo_bpm: int = 0,
) -> bool:
    """
    Generate a jianpu PDF from MusicXML: parse MXL → render jianpu PDF directly.
    MIDI is only created when midi_output_path is explicitly provided.
    Using MXL-parsed score directly avoids MIDI quantization errors (pitch/rhythm loss).

    If *editor_workspace_dir* is given, the intermediate .jianpu.txt and the
    original source file are preserved there so the user can manually proofread
    and re-render via the built-in editor.
    """
    txt_path = temp_dir / f'{mxl_path.stem}.jianpu.txt'
    ly_path = temp_dir / f'{mxl_path.stem}.jianpu.ly'

    # Titles that OMR engines write when no real title is found — fall back to filename for these.
    _GENERIC_TITLES = {'', 'music21', 'untitled', 'title', 'score', 'new score', 'unknown'}

    try:
        source_score = converter.parse(str(mxl_path))

        # Prefer a meaningful title from score metadata; fall back to the caller-supplied name.
        _score_meta = getattr(source_score, 'metadata', None)
        _raw_title = (getattr(_score_meta, 'title', None) or '').strip()
        if _raw_title.lower() not in _GENERIC_TITLES:
            title = _raw_title
        else:
            title = (preferred_title or output_pdf_path.stem.replace('.jianpu', '') or mxl_path.stem).strip()

        apply_score_title(source_score, title)

        # Extract composer from score if not pre-supplied.
        if not composer:
            _raw_composer = (getattr(_score_meta, 'composer', None) or '').strip()
            if _raw_composer.lower() not in ('', 'music21', 'composer'):
                composer = _raw_composer

        # Extract tempo from score if not pre-supplied.
        if tempo_bpm <= 0:
            try:
                from music21 import tempo as m21tempo
                _tempos = list(source_score.flatten().getElementsByClass(m21tempo.MetronomeMark))
                if _tempos:
                    tempo_bpm = int(round(_tempos[0].number))
            except Exception:
                pass

        lyrics_lines = collect_preserved_lyrics_lines(source_score, source_path)

        # Use the original parsed score for MIDI — preserves all parts and voices.
        if midi_output_path is not None:
            if not render_midi_from_score(source_score, midi_output_path):
                log_message('简谱 MIDI 生成失败，跳过 MIDI 输出，继续生成简谱 PDF。', logging.WARNING)

        # Use the MXL-parsed score directly — preserves exact note durations and pitches
        log_message('当前转换链路: 乐谱文件(PDF/JPG/PNG) -> MXL/MusicXML -> 简谱 PDF')
        result = render_score_to_jianpu_pdf(
            source_score, title, output_pdf_path, temp_dir, txt_path, ly_path,
            lyrics_lines, composer=composer, tempo=tempo_bpm,
        )

        # Preserve editor workspace files when the conversion succeeded
        if result and editor_workspace_dir is not None:
            _ws_stem = (preferred_title or output_pdf_path.stem.replace('.jianpu', '') or mxl_path.stem).strip()
            _save_editor_files(_ws_stem, txt_path, source_path, editor_workspace_dir)

        return result
    except Exception as exc:
        import traceback
        tb_str = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log_message(f'生成简谱 PDF 失败: {mxl_path.name}，原因: {exc}', logging.WARNING)
        log_message(f'详细错误信息:\n{tb_str}', logging.DEBUG)
        return False
    finally:
        safe_remove_file(txt_path)
        safe_remove_file(ly_path)
