# 批量乐谱(PDF/JPG/PNG) -> 简谱 PDF 转换工具
# Batch sheet music (PDF/JPG/PNG) to numbered notation (jianpu) PDF converter.
# Pipeline: input image/PDF -> Audiveris (OMR) -> MusicXML -> music21 -> MIDI -> jianpu-ly -> LilyPond -> jianpu PDF
#
# Copyright (c) 2026 Tsukamotoshio. All rights reserved.
# SPDX-License-Identifier: MIT
# See LICENSE file for full license text.

import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from music21 import chord as m21chord, converter, metadata as m21metadata, meter, note as m21note, stream
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

JIANPU_MAP = {
    'C': '1',
    'D': '2',
    'E': '3',
    'F': '4',
    'G': '5',
    'A': '6',
    'B': '7',
}

# CJK font candidates in priority order, for PDF and LilyPond output
CJK_FONT_CANDIDATES = [
    ('Meiryo', r'%SystemRoot%\\Fonts\\meiryo.ttc'),
    ('Yu Gothic', r'%SystemRoot%\\Fonts\\YuGothM.ttc'),
    ('MS Gothic', r'%SystemRoot%\\Fonts\\msgothic.ttc'),
    ('Microsoft YaHei', r'%SystemRoot%\\Fonts\\msyh.ttc'),
    ('Microsoft JhengHei', r'%SystemRoot%\\Fonts\\msjh.ttc'),
    ('SimSun', r'%SystemRoot%\\Fonts\\simsun.ttc'),
    ('SimHei', r'%SystemRoot%\\Fonts\\simhei.ttf'),
    ('Microsoft YaHei Bold', r'%SystemRoot%\\Fonts\\msyhbd.ttc'),
]

# Fallback download URLs for jianpu-ly.py
JIANPU_LY_URLS = [
    'https://ssb22.user.srcf.net/mwrhome/jianpu-ly.py',
    'http://ssb22.user.srcf.net/mwrhome/jianpu-ly.py',
    'https://ssb22.gitlab.io/mwrhome/jianpu-ly.py',
]

ALLOWED_JIANPU_DURATIONS = [4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.375, 0.25, 0.1875, 0.125]
SUPPORTED_INPUT_SUFFIXES = {'.pdf', '.png', '.jpg', '.jpeg'}
ENABLE_LYRICS_OUTPUT = False
MAX_AUDIVERIS_SECONDS = 1800
DEFAULT_AUDIVERIS_MIN_JAVA_VERSION = 25
RUNTIME_ASSETS_DIR_NAME = 'package-assets'
AUDIVERIS_RUNTIME_DIR_NAME = 'audiveris-runtime'
LILYPOND_RUNTIME_DIR_NAME = 'lilypond-runtime'
AUDIVERIS_INSTALL_DIR_NAME = 'Audiveris'
AUDIVERIS_SOURCE_DIR_NAMES = ('audiveris-5.10.2', 'audiveris')
CONVERSION_HISTORY_FILE = 'conversion_history.json'
CONVERSION_PIPELINE_VERSION = 5
APP_VERSION = '0.1.1'
AUDIVERIS_MSI_NAMES = [
    'Audiveris-5.10.2-windows-x86_64.msi',
    'Audiveris.msi',
    'audiveris.msi',
]

LOGGER = logging.getLogger('convert')
LOG_FILE_PATH: Optional[Path] = None


@dataclass(frozen=True)
class AppConfig:
    """Application directory structure: names of input, output, temp, and logs folders."""
    input_dir_name: str = 'Input'
    output_dir_name: str = 'Output'
    temp_dir_name: str = 'audiveris-temp'
    logs_dir_name: str = 'logs'


@dataclass
class ConversionSummary:
    """Statistics for a batch conversion run: counts and file lists for successes, skips, and failures."""
    total: int = 0
    success: int = 0
    skipped: int = 0
    failed: int = 0
    generated_pdfs: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    failed_files: list[str] = field(default_factory=list)


@dataclass
class JianpuNote:
    """A single jianpu note or rest, carrying pitch, accidentals, octave dots, duration, and MIDI pitch."""
    symbol: str
    accidental: str
    upper_dots: int
    lower_dots: int
    duration: float
    duration_dots: int
    midi: Optional[int]
    is_rest: bool


def get_app_base_dir() -> Path:
    """Return the application root directory (exe parent when frozen, script parent otherwise)."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_runtime_search_roots() -> list[Path]:
    """Return a deduplicated list of runtime asset search roots: app base dir, _internal, and PyInstaller _MEIPASS."""
    roots: list[Path] = []
    seen: set[str] = set()
    base_dir = get_app_base_dir()
    candidates: list[Path] = [base_dir, base_dir / '_internal']

    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        candidates.append(Path(meipass))

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def setup_logging(base_dir: Path) -> Optional[Path]:
    """Initialise logging to a timestamped file under logs/; return the log file path."""
    global LOG_FILE_PATH

    if LOGGER.handlers:
        return LOG_FILE_PATH

    logs_dir = base_dir / AppConfig().logs_dir_name
    logs_dir.mkdir(parents=True, exist_ok=True)
    LOG_FILE_PATH = logs_dir / f'convert-{time.strftime("%Y%m%d-%H%M%S")}.log'

    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False

    file_handler = logging.FileHandler(LOG_FILE_PATH, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    LOGGER.addHandler(file_handler)
    return LOG_FILE_PATH


def log_message(message: str, level: int = logging.INFO) -> None:
    """Print message to stdout and write it to the log file, initialising logging lazily if needed."""
    print(message)
    if not LOGGER.handlers:
        try:
            setup_logging(get_app_base_dir())
        except OSError:
            return
    if LOGGER.handlers:
        LOGGER.log(level, message)


def safe_remove_file(path: Path) -> None:
    """Delete a file silently, ignoring missing-file or OS errors."""
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def safe_remove_tree(path: Path) -> None:
    """Delete an entire directory tree, silently ignoring errors."""
    shutil.rmtree(path, ignore_errors=True)


def get_pdf_page_count(pdf_path: Path) -> int:
    """Return the page count of a PDF, or 0 if unreadable."""
    if PdfReader is None or pdf_path.suffix.lower() != '.pdf' or not pdf_path.exists():
        return 0
    try:
        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return 0


# ========== 歌词提取 ==========

def clean_lyrics_line(text: str) -> str:
    """Strip a lyrics line of special Unicode, leading numbers, and excess whitespace."""
    text = text.replace('\u00a0', ' ')
    text = re.sub(r'[\uE000-\uF8FF]+', ' ', text)
    text = re.sub(r'[^\w\s\u4e00-\u9fff,.;:!?()&\-\'"/]+', ' ', text)
    text = re.sub(r'^\d+\s*', '', text)
    text = re.sub(r'\s+([,.;:?!])', r'\1', text)
    text = re.sub(r'\s+', ' ', text).strip(' -')
    return text.strip()


def extract_lyrics_lines_from_score(score) -> list[str]:
    """Extract lyric lines from a music21 score object, returning up to 24 lines."""
    tokens: list[str] = []
    for note in score.recurse().notes:
        lyric_values: list[str] = []
        raw_lyrics = getattr(note, 'lyrics', None)
        if raw_lyrics:
            for item in raw_lyrics:
                text = getattr(item, 'text', None) or str(item)
                if text:
                    lyric_values.append(text)
        else:
            text = getattr(note, 'lyric', None)
            if text:
                lyric_values.append(text)

        for text in lyric_values:
            cleaned = clean_lyrics_line(str(text))
            if cleaned:
                tokens.extend(cleaned.split())

    if not tokens:
        return []

    lines: list[str] = []
    chunk: list[str] = []
    for token in tokens:
        chunk.append(token)
        if len(chunk) >= 8 or token.endswith(('.', '!', '?', ';', ':')):
            lines.append(' '.join(chunk))
            chunk = []
    if chunk:
        lines.append(' '.join(chunk))
    return lines[:24]


def extract_lyrics_lines_from_pdf(pdf_path: Path) -> list[str]:
    """Extract lyric lines from the embedded text of a PDF, returning up to 40 lines."""
    if PdfReader is None or pdf_path.suffix.lower() != '.pdf' or not pdf_path.exists():
        return []

    lyric_lines: list[str] = []
    seen: set[str] = set()
    try:
        reader = PdfReader(str(pdf_path))
        for page in reader.pages:
            try:
                text = page.extract_text(extraction_mode='layout') or page.extract_text() or ''
            except TypeError:
                text = page.extract_text() or ''
            for raw_line in text.splitlines():
                cleaned = clean_lyrics_line(raw_line)
                if len(cleaned) < 6:
                    continue
                letter_count = sum(ch.isalpha() or ('\u4e00' <= ch <= '\u9fff') for ch in cleaned)
                if letter_count < 4:
                    continue
                if cleaned.lower() in seen:
                    continue
                seen.add(cleaned.lower())
                lyric_lines.append(cleaned)
    except Exception:
        return []

    return lyric_lines[:40]


def collect_preserved_lyrics_lines(score, source_path: Optional[Path] = None) -> list[str]:
    """Lyrics entry point: tries score first, then falls back to the source PDF. Disabled by default."""
    if not ENABLE_LYRICS_OUTPUT:
        return []

    lyric_lines = extract_lyrics_lines_from_score(score)
    if lyric_lines:
        return lyric_lines
    if source_path is not None:
        return extract_lyrics_lines_from_pdf(source_path)
    return []


def find_packaged_runtime_dir(dir_name: str) -> Optional[Path]:
    """Find a named subdirectory under any runtime search root (e.g. bundled LilyPond/Audiveris)."""
    for root in get_runtime_search_roots():
        for candidate in [root / dir_name, root / RUNTIME_ASSETS_DIR_NAME / dir_name]:
            if candidate.exists() and candidate.is_dir():
                return candidate
    return None


def find_local_tessdata_dir() -> Optional[Path]:
    """Locate the local Tesseract tessdata directory used by Audiveris for OCR."""
    candidates: list[Path] = []
    for base_dir in get_runtime_search_roots():
        candidates.extend([
            base_dir / 'tessdata',
            base_dir / RUNTIME_ASSETS_DIR_NAME / 'tessdata',
            base_dir / 'audiveris-5.10.2' / 'app' / 'dev' / 'tessdata',
            base_dir / 'audiveris-5.10.2' / 'dev' / 'tessdata',
            base_dir / 'audiveris' / 'app' / 'dev' / 'tessdata',
            base_dir / 'audiveris' / 'dev' / 'tessdata',
        ])

    packaged_audiveris_dir = find_packaged_runtime_dir(AUDIVERIS_RUNTIME_DIR_NAME)
    if packaged_audiveris_dir is not None:
        candidates.append(packaged_audiveris_dir / 'tessdata')

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def build_runtime_paths(base_dir: Path, config: AppConfig) -> tuple[Path, Path, Path]:
    """Build Path objects for the input, output, and temp directories."""
    input_dir = base_dir / config.input_dir_name
    output_dir = base_dir / config.output_dir_name
    temp_dir = base_dir / config.temp_dir_name
    return input_dir, output_dir, temp_dir


def resolve_font_path() -> Optional[Path]:
    """Return the first available CJK font file path for direct PDF rendering."""
    for _, candidate in CJK_FONT_CANDIDATES:
        path = Path(os.path.expandvars(candidate))
        if path.exists():
            return path
    return None


def resolve_lilypond_font_name() -> Optional[str]:
    """Return the first available CJK font name for LilyPond font configuration."""
    for font_name, candidate in CJK_FONT_CANDIDATES:
        path = Path(os.path.expandvars(candidate))
        if path.exists():
            return font_name
    return None


def compute_file_sha256(file_path: Path) -> str:
    """Compute the SHA-256 hex digest of a file, used to detect unchanged inputs."""
    hasher = hashlib.sha256()
    with file_path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_conversion_history(base_dir: Path) -> dict[str, dict]:
    """Load the conversion history from JSON, returning an empty dict if missing or invalid."""
    history_path = base_dir / CONVERSION_HISTORY_FILE
    if not history_path.exists():
        return {}
    try:
        data = json.loads(history_path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        log_message(f'读取转换记录失败，将忽略旧记录：{exc}', logging.WARNING)
        return {}


def save_conversion_history(base_dir: Path, history: dict[str, dict]) -> None:
    """Serialise the conversion history dict to JSON and write it to disk."""
    history_path = base_dir / CONVERSION_HISTORY_FILE
    try:
        history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError as exc:
        log_message(f'保存转换记录失败: {exc}', logging.WARNING)


def has_existing_output_match(pdf_file: Path, output_pdf: Path, output_midi: Optional[Path], history: dict[str, dict]) -> bool:
    """Return True if the input already has a matching output with the same SHA-256 and pipeline version."""
    if not output_pdf.exists():
        return False
    if output_midi is not None and not output_midi.exists():
        return False

    record = history.get(pdf_file.name)
    if not isinstance(record, dict):
        return False

    try:
        current_sha256 = compute_file_sha256(pdf_file)
    except OSError as exc:
        log_message(f'计算文件摘要失败，将继续转换 {pdf_file.name}：{exc}', logging.WARNING)
        return False

    return record.get('sha256') == current_sha256 and record.get('pipeline_version') == CONVERSION_PIPELINE_VERSION


def confirm_skip_all_existing(duplicate_names: list[str]) -> bool:
    """Show the user a list of files with existing outputs and ask whether to skip all; return True to skip."""
    if not duplicate_names:
        return False

    log_message('检测到以下文件在 Output 中已存在与上次相同的转换结果:')
    for name in duplicate_names:
        log_message(f'  - {name}')

    answer = input('是否全部跳过这些重复文件？（Y/N） ').strip().upper()
    return answer == 'Y'


def update_conversion_history(history: dict[str, dict], pdf_file: Path, output_pdf: Path, output_midi: Optional[Path]) -> None:
    """Record the conversion result for a file in the history dict (SHA-256, pipeline version, timestamps)."""
    try:
        source_stat = pdf_file.stat()
        sha256 = compute_file_sha256(pdf_file)
    except OSError as exc:
        log_message(f'更新转换记录失败: {pdf_file.name}，原因: {exc}', logging.WARNING)
        return

    history[pdf_file.name] = {
        'sha256': sha256,
        'pipeline_version': CONVERSION_PIPELINE_VERSION,
        'source_size': source_stat.st_size,
        'source_mtime_ns': source_stat.st_mtime_ns,
        'output_pdf': output_pdf.name,
        'output_midi': output_midi.name if output_midi is not None and output_midi.exists() else '',
        'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }


# ========== 简谱记谱与音符转换 ==========

def register_pdf_font() -> str:
    """Register a CJK font with reportlab and return the font name used; falls back to Helvetica."""
    font_path = resolve_font_path()
    if font_path is not None:
        font_name = 'ChineseFont'
        try:
            if font_path.suffix.lower() == '.ttc':
                pdfmetrics.registerFont(TTFont(font_name, str(font_path), subfontIndex=0))
            else:
                pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
            return font_name
        except Exception:
            pass

    try:
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        return 'STSong-Light'
    except Exception:
        return 'Helvetica'


def duration_suffix(q_len: float, dots: int) -> str:
    """Convert a quarter-length duration to a jianpu-ly text notation suffix (dashes, underscores, dots)."""
    tol = 0.01
    if q_len >= 1.0 - tol:
        base_quarters = round(q_len)
        dash_count = max(base_quarters - 1, 0)
        suffix = '-' * dash_count
        if dots > 0:
            suffix += '.' * dots
        return suffix

    if abs(q_len - 0.75) < tol:
        suffix = '_.'
    elif abs(q_len - 0.5) < tol:
        suffix = '_'
    elif abs(q_len - 0.375) < tol:
        suffix = '__.'
    elif abs(q_len - 0.25) < tol:
        suffix = '__'
    elif abs(q_len - 0.1875) < tol:
        suffix = '___.'
    elif abs(q_len - 0.125) < tol:
        suffix = '___'
    else:
        suffix = '_'

    if dots > 0 and '.' not in suffix:
        suffix += '.' * dots
    return suffix


def get_duration_render(duration: float, dots: int) -> tuple[int, int, int]:
    """Convert a duration to a (dashes, underlines, right_dots) tuple for direct PDF drawing."""
    tol = 0.01
    if duration >= 1.0 - tol:
        dashes = max(round(duration) - 1, 0)
        return dashes, 0, dots

    if abs(duration - 0.75) < tol:
        return 0, 1, 1
    if abs(duration - 0.5) < tol:
        return 0, 1, 0
    if abs(duration - 0.375) < tol:
        return 0, 2, 1
    if abs(duration - 0.25) < tol:
        return 0, 2, 0
    if abs(duration - 0.1875) < tol:
        return 0, 3, 1
    if abs(duration - 0.125) < tol:
        return 0, 3, 0
    return 0, 1, dots


def note_to_jianpu(element) -> JianpuNote:
    """Convert a music21 note or rest to a JianpuNote dataclass."""
    if isinstance(element, m21note.Rest):
        return JianpuNote(
            symbol='0',
            accidental='',
            upper_dots=0,
            lower_dots=0,
            duration=float(element.duration.quarterLength),
            duration_dots=int(getattr(element.duration, 'dots', 0)),
            midi=None,
            is_rest=True,
        )

    pitch = element.pitch
    base = JIANPU_MAP.get(pitch.step, '?') if pitch is not None else '?'
    accidental = ''
    if pitch is not None and pitch.accidental is not None:
        alter = pitch.accidental.alter
        if alter == 1:
            accidental = '#'
        elif alter == -1:
            accidental = 'b'

    upper_dots = 0
    lower_dots = 0
    if pitch is not None and pitch.octave is not None:
        diff = pitch.octave - 4
        if diff > 0:
            upper_dots = diff
        elif diff < 0:
            lower_dots = -diff

    return JianpuNote(
        symbol=base,
        accidental=accidental,
        upper_dots=upper_dots,
        lower_dots=lower_dots,
        duration=float(element.duration.quarterLength),
        duration_dots=int(getattr(element.duration, 'dots', 0)),
        midi=getattr(pitch, 'midi', None),
        is_rest=False,
    )


def format_jianpu_note_text(note: JianpuNote) -> str:
    """Format a JianpuNote as the display string used in direct PDF rendering."""
    if note.is_rest:
        return '0'
    text = ''.join('·' for _ in range(note.upper_dots))
    text += note.accidental + note.symbol
    text += ''.join('·' for _ in range(note.lower_dots))
    text += duration_suffix(note.duration, note.duration_dots)
    return text


def jianpu_octave_symbol(upper_dots: int, lower_dots: int) -> str:
    """Return the jianpu-ly octave marker: single-quotes for high, commas for low."""
    if upper_dots > 0:
        return "'" * upper_dots
    if lower_dots > 0:
        return ',' * lower_dots
    return ''


def jianpu_note_token(note: JianpuNote) -> str:
    """Produce the jianpu-ly token string for a single note (pitch, octave marker, duration)."""
    if note.is_rest:
        base_note = '0'
    else:
        base_note = note.accidental + note.symbol + jianpu_octave_symbol(note.upper_dots, note.lower_dots)

    tol = 0.01
    d = note.duration

    if note.is_rest:
        if abs(d - 4.0) < tol:
            return '0 - - -'
        if abs(d - 3.0) < tol:
            return '0 - -'
        if abs(d - 2.0) < tol:
            return '0 -'
        if abs(d - 1.5) < tol:
            return '0.'
        if abs(d - 1.0) < tol:
            return '0'
        if abs(d - 0.75) < tol:
            return 'q0.'
        if abs(d - 0.5) < tol:
            return 'q0'
        if abs(d - 0.375) < tol:
            return 's0.'
        if abs(d - 0.25) < tol:
            return 's0'
        if abs(d - 0.1875) < tol:
            return 'd0.'
        if abs(d - 0.125) < tol:
            return 'd0'
        return '0'

    if abs(d - 4.0) < tol:
        return f'{base_note} - - -'
    if abs(d - 3.0) < tol:
        return f'{base_note} - -'
    if abs(d - 2.0) < tol:
        return f'{base_note} -'
    if abs(d - 1.5) < tol:
        return f'{base_note}.'
    if abs(d - 1.0) < tol:
        return base_note
    if abs(d - 0.75) < tol:
        return f'q{base_note}.'
    if abs(d - 0.5) < tol:
        return f'q{base_note}'
    if abs(d - 0.375) < tol:
        return f's{base_note}.'
    if abs(d - 0.25) < tol:
        return f's{base_note}'
    if abs(d - 0.1875) < tol:
        return f'd{base_note}.'
    if abs(d - 0.125) < tol:
        return f'd{base_note}'
    return f'q{base_note}'


def normalize_jianpu_duration(duration: float) -> float:
    """Round a duration down to the nearest allowed jianpu duration value."""
    tol = 0.01
    for candidate in ALLOWED_JIANPU_DURATIONS:
        if duration + tol >= candidate:
            return candidate
    return 0.125


def infer_duration_dots(duration: float) -> int:
    """Return 1 if the duration matches a dotted note value, otherwise 0."""
    for candidate in (1.5, 0.75, 0.375, 0.1875):
        if abs(duration - candidate) < 0.01:
            return 1
    return 0


def clone_jianpu_note(note: JianpuNote, duration: float) -> JianpuNote:
    """Clone a JianpuNote with a new duration (auto-normalised and dotted)."""
    normalized_duration = normalize_jianpu_duration(duration)
    return JianpuNote(
        symbol=note.symbol,
        accidental=note.accidental,
        upper_dots=note.upper_dots,
        lower_dots=note.lower_dots,
        duration=normalized_duration,
        duration_dots=infer_duration_dots(normalized_duration),
        midi=note.midi,
        is_rest=note.is_rest,
    )


def repair_jianpu_measure(measure_notes: list[JianpuNote], measure_length: float) -> list[JianpuNote]:
    """Trim overflowing notes and pad with rests so the measure total equals the time-signature length."""
    tol = 0.01
    current_total = 0.0
    repaired: list[JianpuNote] = []
    rest_template = JianpuNote('0', '', 0, 0, 1.0, 0, None, True)

    for note in measure_notes:
        if current_total >= measure_length - tol:
            break

        remaining = measure_length - current_total
        if remaining <= tol:
            break

        note_duration = max(float(note.duration), 0.125)
        if note_duration <= remaining + tol:
            new_note = clone_jianpu_note(note, note_duration)
            repaired.append(new_note)
            current_total += new_note.duration
            continue

        for piece in split_duration_chunks(remaining):
            if current_total >= measure_length - tol:
                break
            new_note = clone_jianpu_note(note, piece)
            repaired.append(new_note)
            current_total += new_note.duration

    remaining = measure_length - current_total
    if remaining > tol:
        for piece in split_duration_chunks(remaining):
            new_rest = clone_jianpu_note(rest_template, piece)
            repaired.append(new_rest)
            current_total += new_rest.duration

    return repaired


def clone_monophonic_element(element, duration: float):
    """Clone a music21 element as a monophonic note: chords become the top pitch."""
    normalized_duration = normalize_jianpu_duration(duration)
    if isinstance(element, m21note.Rest):
        new_element = m21note.Rest()
    elif isinstance(element, m21chord.Chord):
        top_pitch = max(element.pitches, key=lambda pitch: pitch.midi)
        new_element = m21note.Note(top_pitch)
    else:
        new_element = m21note.Note(element.pitch)
    new_element.duration.quarterLength = normalized_duration
    return new_element


def extract_jianpu_measures(score) -> tuple[list[list[JianpuNote]], str]:
    """
    Extract jianpu measure list and time signature from a music21 score.
    Merges polyphony by offset; fills gaps and overflows with rests at bar boundaries.
    """
    part = score.parts[0] if score.parts else score.flatten()
    time_signature = '4/4'
    nominal_measure_length = 4.0
    time_sig = part.recurse().getElementsByClass(meter.TimeSignature)
    if time_sig:
        time_signature = time_sig[0].ratioString
        nominal_measure_length = float(getattr(time_sig[0].barDuration, 'quarterLength', 4.0) or 4.0)

    measures: list[list[JianpuNote]] = []
    measure_streams = list(part.getElementsByClass(stream.Measure))
    if not measure_streams:
        fallback_notes = [note_to_jianpu(element) for element in part.flatten().notesAndRests]
        return ([fallback_notes] if fallback_notes else []), time_signature

    tol = 0.01
    for measure in measure_streams:
        measure_length = nominal_measure_length or float(getattr(measure.barDuration, 'quarterLength', 4.0) or 4.0)
        by_offset: dict[float, object] = {}

        for element in measure.flatten().notesAndRests:
            offset = float(element.offset)
            if offset >= measure_length - tol:
                continue
            available = max(measure_length - offset, 0.125)
            candidate = clone_monophonic_element(element, min(float(element.duration.quarterLength or 0.25), available))
            existing = by_offset.get(offset)
            if existing is None:
                by_offset[offset] = candidate
            elif isinstance(existing, m21note.Rest) and not isinstance(candidate, m21note.Rest):
                by_offset[offset] = candidate
            elif isinstance(existing, m21note.Note) and isinstance(candidate, m21note.Note) and candidate.pitch.midi > existing.pitch.midi:
                by_offset[offset] = candidate

        if not by_offset:
            rest = m21note.Rest()
            rest.duration.quarterLength = measure_length
            measures.append(repair_jianpu_measure([note_to_jianpu(rest)], measure_length))
            continue

        offsets = sorted(by_offset.keys())
        current_offset = 0.0
        measure_notes: list[JianpuNote] = []

        for idx, offset in enumerate(offsets):
            if current_offset >= measure_length - tol:
                break
            if offset > current_offset + tol:
                rest = m21note.Rest()
                rest.duration.quarterLength = normalize_jianpu_duration(min(offset - current_offset, measure_length - current_offset))
                measure_notes.append(note_to_jianpu(rest))

            next_offset = offsets[idx + 1] if idx + 1 < len(offsets) else measure_length
            next_offset = min(next_offset, measure_length)
            span = max(min(next_offset - offset, measure_length - offset), 0.125)
            element = clone_monophonic_element(by_offset[offset], span)
            measure_notes.append(note_to_jianpu(element))
            current_offset = next_offset

        if current_offset < measure_length - tol:
            rest = m21note.Rest()
            rest.duration.quarterLength = normalize_jianpu_duration(measure_length - current_offset)
            measure_notes.append(note_to_jianpu(rest))

        measures.append(repair_jianpu_measure(measure_notes, measure_length))

    return measures, time_signature


def split_duration_chunks(duration: float) -> list[float]:
    """Split a remaining duration into allowed jianpu chunks (greedy, largest first)."""
    remaining = max(float(duration), 0.0)
    chunks: list[float] = []
    tol = 0.01
    while remaining > tol:
        piece = normalize_jianpu_duration(remaining)
        piece = min(piece, remaining)
        if piece <= tol:
            piece = min(0.125, remaining)
        chunks.append(piece)
        remaining -= piece
    return chunks or [0.125]


def extract_strict_jianpu_measures(score) -> tuple[list[list[JianpuNote]], str]:
    """
    Extract jianpu measures by re-slicing all notes strictly by bar length,
    ignoring the score's own Measure objects.
    """
    part = score.parts[0] if score.parts else score.flatten()
    time_signature = '4/4'
    bar_length = 4.0
    time_sig = part.recurse().getElementsByClass(meter.TimeSignature)
    if time_sig:
        time_signature = time_sig[0].ratioString
        bar_length = float(getattr(time_sig[0].barDuration, 'quarterLength', 4.0) or 4.0)

    by_offset: dict[float, object] = {}
    for element in part.flatten().notesAndRests:
        offset = float(element.offset)
        if offset < 0:
            continue
        candidate = clone_monophonic_element(element, float(element.duration.quarterLength or 0.25))
        existing = by_offset.get(offset)
        if existing is None:
            by_offset[offset] = candidate
        elif isinstance(existing, m21note.Rest) and not isinstance(candidate, m21note.Rest):
            by_offset[offset] = candidate
        elif isinstance(existing, m21note.Note) and isinstance(candidate, m21note.Note) and candidate.pitch.midi > existing.pitch.midi:
            by_offset[offset] = candidate

    offsets = sorted(by_offset.keys())
    if not offsets:
        return [], time_signature

    measures: list[list[JianpuNote]] = []
    current_measure: list[JianpuNote] = []
    current_pos = 0.0
    tol = 0.01

    def append_element_chunks(element_template, total_duration: float) -> None:
        """Split an element across bar boundaries and append chunks to the current measure."""
        nonlocal current_measure, current_pos, measures
        remaining = total_duration
        while remaining > tol:
            pos_in_bar = current_pos % bar_length
            capacity = bar_length - pos_in_bar if pos_in_bar > tol else bar_length
            piece_total = min(remaining, capacity)
            for piece in split_duration_chunks(piece_total):
                element_piece = clone_monophonic_element(element_template, piece)
                current_measure.append(note_to_jianpu(element_piece))
                current_pos += piece
            remaining -= piece_total
            if abs(current_pos % bar_length) < tol:
                measures.append(current_measure)
                current_measure = []

    for idx, offset in enumerate(offsets):
        if offset > current_pos + tol:
            rest = m21note.Rest()
            append_element_chunks(rest, offset - current_pos)

        next_offset = offsets[idx + 1] if idx + 1 < len(offsets) else offset + float(by_offset[offset].duration.quarterLength or 0.25)
        duration = max(next_offset - offset, 0.125)
        append_element_chunks(by_offset[offset], duration)

    if current_measure:
        pos_in_bar = current_pos % bar_length
        if pos_in_bar > tol:
            rest = m21note.Rest()
            append_element_chunks(rest, bar_length - pos_in_bar)
        elif current_measure:
            measures.append(current_measure)

    repaired_measures = [repair_jianpu_measure(measure, bar_length) for measure in measures]
    return repaired_measures, time_signature


def build_jianpu_ly_text(score, title: str, use_strict_timing: bool = False) -> str:
    """Build jianpu-ly plain-text (.txt) content, including title, key, time signature, and measures."""
    try:
        key_obj = score.analyze('key') if score else None
    except Exception:
        key_obj = None

    header = ['% jianpu-ly.py', f'title={title}']
    if key_obj is not None and key_obj.tonic is not None:
        tonic = key_obj.tonic.name
        if key_obj.mode == 'minor':
            header.append(f'6={tonic}')
        else:
            header.append(f'1={tonic}')
    else:
        header.append('1=C')

    measures, time_signature = extract_strict_jianpu_measures(score) if use_strict_timing else extract_jianpu_measures(score)
    header.append(time_signature)
    header.append('')

    measures_per_line = 4
    for i in range(0, len(measures), measures_per_line):
        line_measures = measures[i:i + measures_per_line]
        measure_texts = [' '.join(jianpu_note_token(note) for note in measure) for measure in line_measures]
        header.append(' | '.join(measure_texts) + ' |')

    return '\n'.join(header)


def find_jianpu_ly_command() -> Optional[str]:
    """Look for a jianpu-ly command on PATH."""
    for candidate in ['jianpu-ly', 'jianpu-ly.py']:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def find_jianpu_ly_module() -> bool:
    """Check whether jianpu_ly is installed as a Python module."""
    try:
        return importlib.util.find_spec('jianpu_ly') is not None
    except Exception:
        return False


def find_jianpu_ly_script() -> Optional[Path]:
    """Look for jianpu-ly.py in cwd and the app base directory."""
    script_dir = get_app_base_dir()
    for base in [Path.cwd(), script_dir]:
        path = base / 'jianpu-ly.py'
        if path.exists():
            return path
    return None


def download_jianpu_ly_script(dest: Path) -> bool:
    """Download jianpu-ly.py from the fallback URL list and write it to dest."""
    for url in JIANPU_LY_URLS:
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                if resp.status != 200:
                    continue
                dest.write_bytes(resp.read())
            return True
        except Exception:
            continue
    return False


def _ensure_jianpu_script() -> Optional[Path]:
    """Ensure jianpu-ly.py is available, downloading it to the app dir if necessary."""
    script_path = find_jianpu_ly_script()
    if script_path is not None:
        return script_path
    script_path = get_app_base_dir() / 'jianpu-ly.py'
    if script_path.exists() or download_jianpu_ly_script(script_path):
        return script_path
    return None


def find_python_script_command() -> Optional[list[str]]:
    """Find a usable Python interpreter command, preferring the bundled one."""
    candidates: list[list[str]] = []
    packaged_lilypond_dir = find_packaged_runtime_dir(LILYPOND_RUNTIME_DIR_NAME)
    if packaged_lilypond_dir is not None:
        candidates.append([str(packaged_lilypond_dir / 'bin' / 'python.exe')])

    for base_dir in get_runtime_search_roots():
        candidates.extend([
            [str(base_dir / 'python.exe')],
            [str(base_dir / 'Python' / 'python.exe')],
            [str(base_dir / '_internal' / 'python.exe')],
        ])

    sys_executable_path = Path(sys.executable)
    if sys_executable_path.name.lower().startswith('python'):
        candidates.insert(0, [str(sys_executable_path)])

    seen: set[str] = set()
    for candidate in candidates:
        candidate_path = Path(candidate[0])
        candidate_key = str(candidate_path).lower()
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        if candidate_path.exists() and candidate_path.is_file():
            return candidate

    for command_name in ('python.exe', 'python'):
        found = shutil.which(command_name)
        if found:
            return [found]

    py_launcher = shutil.which('py')
    if py_launcher:
        return [py_launcher, '-3']

    return None


def render_jianpu_ly(txt_path: Path, ly_path: Path) -> bool:
    """Convert a jianpu-ly text file to a LilyPond .ly file (tries command > module > local script)."""
    env = os.environ.copy()
    env['j2ly_sloppy_bars'] = '1'
    txt_path = txt_path.resolve()
    ly_path = ly_path.resolve()

    cmd = find_jianpu_ly_command()
    if cmd is not None:
        try:
            with ly_path.open('w', encoding='utf-8') as out:
                subprocess.run([cmd, str(txt_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(txt_path.parent), env=env)
            return True
        except subprocess.CalledProcessError as exc:
            print('jianpu-ly 命令执行失败:', exc.stderr.decode('utf-8', errors='ignore'))

    if find_jianpu_ly_module():
        try:
            with ly_path.open('w', encoding='utf-8') as out:
                subprocess.run([sys.executable, '-m', 'jianpu_ly', str(txt_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(txt_path.parent), env=env)
            return True
        except subprocess.CalledProcessError as exc:
            print('jianpu_ly 模块执行失败:', exc.stderr.decode('utf-8', errors='ignore'))

    script_path = _ensure_jianpu_script()
    if script_path is None:
        return False

    python_cmd = find_python_script_command()
    if python_cmd is None:
        log_message('未找到可用于执行 jianpu-ly.py 的 Python 解释器。', logging.WARNING)
        return False

    try:
        with ly_path.open('w', encoding='utf-8') as out:
            subprocess.run([*python_cmd, str(script_path), str(txt_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(txt_path.parent), env=env)
        return True
    except subprocess.CalledProcessError as exc:
        print('jianpu-ly 脚本执行失败:', exc.stderr.decode('utf-8', errors='ignore'))
        return False


def render_jianpu_ly_from_mxl(mxl_path: Path, ly_path: Path) -> bool:
    """Convert a MusicXML file directly to a LilyPond .ly file via jianpu-ly."""
    env = os.environ.copy()
    env['j2ly_sloppy_bars'] = '1'
    mxl_path = mxl_path.resolve()
    ly_path = ly_path.resolve()

    cmd = find_jianpu_ly_command()
    if cmd is not None:
        try:
            with ly_path.open('w', encoding='utf-8') as out:
                subprocess.run([cmd, str(mxl_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(mxl_path.parent), env=env)
            return True
        except subprocess.CalledProcessError as exc:
            print('jianpu-ly 命令处理 MXL 失败:', exc.stderr.decode('utf-8', errors='ignore'))

    if find_jianpu_ly_module():
        try:
            with ly_path.open('w', encoding='utf-8') as out:
                subprocess.run([sys.executable, '-m', 'jianpu_ly', str(mxl_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(mxl_path.parent), env=env)
            return True
        except subprocess.CalledProcessError as exc:
            print('jianpu_ly 模块处理 MXL 失败:', exc.stderr.decode('utf-8', errors='ignore'))

    script_path = _ensure_jianpu_script()
    if script_path is None:
        return False

    python_cmd = find_python_script_command()
    if python_cmd is None:
        log_message('未找到可用于执行 jianpu-ly.py 的 Python 解释器。', logging.WARNING)
        return False

    try:
        with ly_path.open('w', encoding='utf-8') as out:
            subprocess.run([*python_cmd, str(script_path), str(mxl_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(mxl_path.parent), env=env)
        return True
    except subprocess.CalledProcessError as exc:
        print('jianpu-ly 脚本处理 MXL 失败:', exc.stderr.decode('utf-8', errors='ignore'))
        return False


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
        subprocess.run([lilypond_exe, str(ly_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, cwd=str(ly_path.parent.resolve()))
        pdf_path = ly_path.with_suffix('.pdf')
        return pdf_path if pdf_path.exists() else None
    except subprocess.CalledProcessError as exc:
        log_message('LilyPond 生成失败: ' + exc.stderr.decode('utf-8', errors='ignore'), logging.WARNING)
        return None
    except OSError as exc:
        log_message(f'LilyPond 生成时出现异常: {exc}', logging.WARNING)
        return None


def find_audiveris_executable() -> Optional[Path]:
    """Locate the Audiveris launcher via env vars, bundled runtime, or source build paths."""
    script_dir = get_app_base_dir()
    env_path = os.environ.get('AUDIVERIS_EXE_PATH') or os.environ.get('AUDIVERIS_PATH')
    candidates: list[Path] = []
    if env_path:
        env_base = Path(env_path)
        candidates.extend([
            env_base,
            env_base / 'audiveris.exe',
            env_base / 'Audiveris.exe',
            env_base / 'audiveris.bat',
            env_base / 'Audiveris.bat',
            env_base / 'bin' / 'audiveris.exe',
            env_base / 'bin' / 'Audiveris.bat',
            env_base / 'bin' / 'audiveris.bat',
        ])

    packaged_audiveris_dir = find_packaged_runtime_dir(AUDIVERIS_RUNTIME_DIR_NAME)
    if packaged_audiveris_dir is not None:
        candidates.extend([
            packaged_audiveris_dir / 'bin' / 'Audiveris.bat',
            packaged_audiveris_dir / 'bin' / 'audiveris.bat',
            packaged_audiveris_dir / 'bin' / 'audiveris.exe',
        ])

    local_install_dir = script_dir / AUDIVERIS_INSTALL_DIR_NAME
    candidates.extend([
        script_dir / 'Audiveris.exe',
        script_dir / 'audiveris.exe',
        script_dir / 'Audiveris.bat',
        script_dir / 'audiveris.bat',
        local_install_dir / 'Audiveris.exe',
        local_install_dir / 'audiveris.exe',
        local_install_dir / 'bin' / 'audiveris.exe',
        local_install_dir / 'bin' / 'Audiveris.bat',
        local_install_dir / 'bin' / 'audiveris.bat',
    ])

    source_dir = find_audiveris_source_dir()
    if source_dir is not None:
        candidates.extend([
            source_dir / 'Audiveris.bat',
            source_dir / 'audiveris.bat',
            source_dir / 'app' / 'build' / 'install' / 'Audiveris' / 'bin' / 'Audiveris.bat',
            source_dir / 'app' / 'build' / 'install' / 'Audiveris' / 'bin' / 'audiveris.bat',
            source_dir / 'app' / 'build' / 'install' / 'app' / 'bin' / 'Audiveris.bat',
            source_dir / 'app' / 'build' / 'install' / 'app' / 'bin' / 'audiveris.bat',
            source_dir / 'app' / 'build' / 'install' / 'app' / 'bin' / 'app.bat',
            source_dir / 'build' / 'install' / 'Audiveris' / 'bin' / 'Audiveris.bat',
            source_dir / 'build' / 'install' / 'Audiveris' / 'bin' / 'audiveris.bat',
            source_dir / 'build' / 'install' / 'app' / 'bin' / 'Audiveris.bat',
            source_dir / 'build' / 'install' / 'app' / 'bin' / 'audiveris.bat',
            source_dir / 'build' / 'install' / 'app' / 'bin' / 'app.bat',
            source_dir / 'audiveris.exe',
            source_dir / 'bin' / 'audiveris.exe',
        ])

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    for candidate in ['audiveris.exe', 'Audiveris.bat', 'audiveris.bat', 'audiveris']:
        found = shutil.which(candidate)
        if found:
            return Path(found)
    return None


def find_audiveris_msi() -> Optional[Path]:
    """Look for an Audiveris MSI installer in the app base directory."""
    script_dir = get_app_base_dir()
    for name in AUDIVERIS_MSI_NAMES:
        candidate = script_dir / name
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def ensure_audiveris_executable() -> Optional[Path]:
    """Ensure Audiveris is available: try existing path, then Gradle build from source, then MSI unpack."""
    existing = find_audiveris_executable()
    if existing is not None:
        return existing

    source_dir = find_audiveris_source_dir()
    wrapper = find_audiveris_wrapper()
    required_java_version = get_audiveris_required_java_version()
    if source_dir is not None and wrapper is not None:
        java_exe = find_java_executable(required_java_version)
        if java_exe is None:
            log_message(f'检测到 Audiveris 源码目录，但未找到可用的 Java {required_java_version}+/JDK {required_java_version}+。请先安装匹配版本，或将便携版放到程序目录下的 jdk 文件夹。', logging.WARNING)
        else:
            log_message(f'检测到 Audiveris 源码目录: {source_dir}')
            log_message(f'检测到 Java {required_java_version}+: {java_exe}')
            log_message('正在根据源码准备 Audiveris 启动器，首次执行可能需要几分钟...')
            cmd = [str(wrapper), '--console=plain', ':app:installDist']
            return_code, stdout, stderr = run_subprocess_with_spinner(cmd, cwd=str(wrapper.parent), java_exe=java_exe)
            if return_code == 0:
                installed = find_audiveris_executable()
                if installed is not None:
                    log_message(f'已从源码准备 Audiveris 启动器: {installed}')
                    return installed
            else:
                detail = (stderr or stdout or '').strip()
                if detail:
                    log_message(f'从源码准备 Audiveris 失败: {detail}', logging.WARNING)

    msi_path = find_audiveris_msi()
    if msi_path is None:
        return None

    base_dir = get_app_base_dir()
    install_dir = base_dir / AUDIVERIS_INSTALL_DIR_NAME
    log_path = base_dir / 'audiveris-install.log'
    base_dir.mkdir(parents=True, exist_ok=True)

    log_message(f'未找到源码启动器，正在从 MSI 解包到: {install_dir}')
    cmd = [
        'msiexec',
        '/a',
        str(msi_path),
        '/qn',
        f'TARGETDIR={base_dir}',
        '/L*v',
        str(log_path),
    ]

    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=MAX_AUDIVERIS_SECONDS, check=False)
    except OSError as exc:
        log_message(f'调用 MSI 解包 Audiveris 失败: {exc}', logging.WARNING)
        return None

    if result.returncode != 0:
        log_message(f'Audiveris 解包失败，退出码: {result.returncode}', logging.WARNING)
        if result.stderr:
            log_message(result.stderr.strip(), logging.WARNING)
        log_message(f'详细日志见: {log_path}', logging.WARNING)
        return None

    installed = find_audiveris_executable()
    if installed is not None:
        log_message(f'Audiveris 已就绪: {installed}')
        return installed

    log_message(f'解包过程已完成，但未找到 Audiveris 启动器，请检查日志: {log_path}', logging.WARNING)
    return None


def find_audiveris_wrapper() -> Optional[Path]:
    """Find the Gradle wrapper script (gradlew.bat / gradlew) in the Audiveris source directory."""
    source_dir = find_audiveris_source_dir()
    if source_dir is None:
        return None

    for candidate in [
        source_dir / 'gradlew.bat',
        source_dir / 'gradlew',
        source_dir / 'app' / 'gradlew.bat',
        source_dir / 'app' / 'gradlew',
    ]:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def run_subprocess_with_spinner(
    cmd: list[str],
    cwd: str,
    timeout: int = MAX_AUDIVERIS_SECONDS,
    java_exe: Optional[Path] = None,
) -> tuple[int, str, str]:
    """
    Run a subprocess with a spinner animation, setting Java env vars and TESSDATA_PREFIX.
    Returns (exit_code, stdout, stderr); returns -1 on timeout.
    """
    spinner = ['|', '/', '-', '\\']
    start_time = time.time()
    prepared_cmd = prepare_subprocess_command(cmd)
    env = os.environ.copy()
    env.setdefault('JAVA_TOOL_OPTIONS', '-Dfile.encoding=UTF-8')
    if java_exe is not None:
        java_home = java_exe.parent.parent
        env['JAVA_HOME'] = str(java_home)
        env['APP_JAVA_HOME'] = str(java_home)
        env['JAVACMD'] = str(java_exe)
        env['PATH'] = str(java_exe.parent) + os.pathsep + env.get('PATH', '')

    tessdata_dir = find_local_tessdata_dir()
    if tessdata_dir is not None and not env.get('TESSDATA_PREFIX'):
        env['TESSDATA_PREFIX'] = str(tessdata_dir)

    stdout_handle = tempfile.TemporaryFile(mode='w+t', encoding='utf-8', errors='ignore')
    stderr_handle = tempfile.TemporaryFile(mode='w+t', encoding='utf-8', errors='ignore')

    try:
        with subprocess.Popen(
            prepared_cmd,
            cwd=cwd,
            stdout=stdout_handle,
            stderr=stderr_handle,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='ignore',
            env=env,
        ) as proc:
            while proc.poll() is None:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    proc.kill()
                    proc.wait(timeout=5)
                    stdout_handle.seek(0)
                    stderr_handle.seek(0)
                    stdout = stdout_handle.read()
                    stderr = stderr_handle.read()
                    sys.stdout.write('\r')
                    sys.stdout.flush()
                    return -1, stdout or '', stderr or 'Process timed out.'
                idx = int(elapsed) % len(spinner)
                sys.stdout.write(f'\r{spinner[idx]} Audiveris 正在运行... 已用 {int(elapsed)}s')
                sys.stdout.flush()
                time.sleep(0.25)

            return_code = proc.wait(timeout=5)
            stdout_handle.seek(0)
            stderr_handle.seek(0)
            stdout = stdout_handle.read()
            stderr = stderr_handle.read()
            sys.stdout.write('\r')
            sys.stdout.flush()
            return return_code, stdout or '', stderr or ''
    finally:
        stdout_handle.close()
        stderr_handle.close()


def build_safe_ascii_name(name: str, fallback: str = 'file') -> str:
    """Convert a string to a safe ASCII filename for temp dirs and copy paths."""
    safe_name = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('._-')
    return safe_name or fallback


def is_supported_score_file(path: Path) -> bool:
    """Return True if the path is a supported score input file (PDF/PNG/JPG/JPEG)."""
    return path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES


def parse_java_major_version(version_output: str) -> Optional[int]:
    """Parse the Java major version number from `java -version` output."""
    match = re.search(r'version\s+"([^"]+)"', version_output)
    if not match:
        return None

    version_text = match.group(1)
    if version_text.startswith('1.'):
        parts = version_text.split('.')
        return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None

    major_text = version_text.split('.', 1)[0]
    return int(major_text) if major_text.isdigit() else None


def find_java_executable(min_major_version: int = DEFAULT_AUDIVERIS_MIN_JAVA_VERSION) -> Optional[Path]:
    """Find a Java executable meeting the minimum version (searches app dir, JAVA_HOME, common paths, PATH)."""
    candidates: list[Path] = []

    for base_dir in get_runtime_search_roots():
        candidates.extend([
            base_dir / 'jdk' / 'bin' / 'java.exe',
            base_dir / 'jdk' / 'bin' / 'java',
            base_dir / 'java' / 'bin' / 'java.exe',
            base_dir / 'java' / 'bin' / 'java',
            base_dir / 'jre' / 'bin' / 'java.exe',
            base_dir / 'jre' / 'bin' / 'java',
        ])
        if base_dir.exists() and base_dir.is_dir():
            for child in sorted(base_dir.iterdir(), reverse=True):
                if child.is_dir() and child.name.lower().startswith(('jdk', 'jre', 'java')):
                    candidates.extend([child / 'bin' / 'java.exe', child / 'bin' / 'java'])

    java_home = os.environ.get('JAVA_HOME')
    if java_home:
        java_base = Path(java_home)
        candidates.extend([java_base / 'bin' / 'java.exe', java_base / 'bin' / 'java'])

    local_app_data = os.environ.get('LOCALAPPDATA', '')
    common_roots = [
        Path(r'C:\Program Files\Java'),
        Path(r'C:\Program Files\Eclipse Adoptium'),
        Path(r'C:\Program Files\Microsoft'),
        Path(r'C:\Program Files\Zulu'),
        Path(local_app_data) / 'Programs' / 'Microsoft' if local_app_data else Path(),
        Path(local_app_data) / 'Programs' / 'Eclipse Adoptium' if local_app_data else Path(),
        Path(local_app_data) / 'Programs' / 'Zulu' if local_app_data else Path(),
    ]
    for root in common_roots:
        if root.exists() and root.is_dir():
            for child in sorted(root.iterdir(), reverse=True):
                if child.is_dir():
                    candidates.extend([child / 'bin' / 'java.exe', child / 'bin' / 'java'])

    for candidate_name in ('java.exe', 'java'):
        found = shutil.which(candidate_name)
        if found:
            candidates.append(Path(found))

    seen: set[str] = set()
    for candidate in candidates:
        candidate_str = str(candidate)
        if candidate_str in seen:
            continue
        seen.add(candidate_str)
        if not candidate.exists() or not candidate.is_file():
            continue

        try:
            result = subprocess.run(
                [str(candidate), '-version'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue

        version_text = (result.stderr or result.stdout or '').strip()
        major_version = parse_java_major_version(version_text)
        if major_version is not None and major_version >= min_major_version:
            return candidate

    return None


def find_audiveris_source_dir() -> Optional[Path]:
    """Find the Audiveris source directory (audiveris-5.10.2 or audiveris) under the app root."""
    script_dir = get_app_base_dir()
    for name in AUDIVERIS_SOURCE_DIR_NAMES:
        candidate = script_dir / name
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def get_audiveris_required_java_version(default: int = DEFAULT_AUDIVERIS_MIN_JAVA_VERSION) -> int:
    """Read theMinJavaVersion from Audiveris gradle.properties to determine the required Java version."""
    source_dir = find_audiveris_source_dir()
    if source_dir is None:
        return default

    properties_path = source_dir / 'gradle.properties'
    if not properties_path.exists():
        return default

    try:
        text = properties_path.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return default

    match = re.search(r'^\s*theMinJavaVersion\s*=\s*(\d+)\s*$', text, flags=re.M)
    if match:
        return int(match.group(1))
    return default


def prepare_subprocess_command(cmd: list[str]) -> list[str]:
    """On Windows, wrap .bat/.cmd commands with `cmd.exe /c` to ensure correct invocation."""
    if os.name == 'nt' and cmd:
        suffix = Path(cmd[0]).suffix.lower()
        if suffix in {'.bat', '.cmd'}:
            return ['cmd.exe', '/c', *cmd]
    return cmd


def prepare_audiveris_paths(input_path: Path, output_dir: Path) -> tuple[Path, Path, Optional[Path]]:
    """
    Copy the input to an ASCII-safe filename and create an isolated Audiveris job directory.
    Returns (safe_input_path, safe_output_dir, cleanup_copy).
    """
    input_path = input_path.resolve()
    output_dir = output_dir.resolve()
    token = hashlib.sha1(str(input_path).encode('utf-8', errors='ignore')).hexdigest()[:10]
    safe_parent = output_dir.parent.resolve()
    safe_parent.mkdir(parents=True, exist_ok=True)

    safe_stem = build_safe_ascii_name(input_path.stem, fallback='input')
    safe_input_path = safe_parent / f'{safe_stem}_{token}{input_path.suffix.lower()}'
    safe_output_dir = safe_parent / f'audiveris_job_{token}'

    if safe_output_dir.exists():
        shutil.rmtree(safe_output_dir, ignore_errors=True)
    safe_output_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(input_path, safe_input_path)
    return safe_input_path, safe_output_dir, safe_input_path


def run_audiveris_batch(input_path: Path, output_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Run Audiveris OMR on the input file; falls back to per-page processing for multi-page PDFs.
    Returns the output directory containing MusicXML, or None on failure.
    """
    required_java_version = get_audiveris_required_java_version()
    java_exe = find_java_executable(required_java_version)
    exe = ensure_audiveris_executable()
    if output_dir is None:
        output_dir = get_app_base_dir() / 'audiveris-output'
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_input_path, safe_output_dir, cleanup_input_copy = prepare_audiveris_paths(input_path, output_dir)

    def invoke_audiveris(target_dir: Path, sheet_number: Optional[int] = None) -> tuple[int, str, str, Optional[Path]]:
        """Run Audiveris once for a given output dir and optional sheet number; return (exit_code, stdout, stderr, mxl_path)."""
        target_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(exe),
            '-batch',
            '-constant', 'org.audiveris.omr.text.Language.defaultSpecification=eng+chi_sim',
            '-export',
        ]
        if sheet_number is not None:
            cmd.extend(['-sheets', str(sheet_number)])
        cmd.extend(['-output', str(target_dir), str(safe_input_path)])
        return_code, stdout, stderr = run_subprocess_with_spinner(cmd, cwd=str(exe.parent), java_exe=java_exe)
        exported_file = find_first_musicxml_file(target_dir, safe_input_path.stem)
        return return_code, stdout, stderr, exported_file

    try:
        if exe is None:
            log_message('未能生成或定位 Audiveris 启动器。根据官方文档，应先完成源码构建，再使用生成的 Audiveris.bat 运行。', logging.WARNING)
            log_message('请确认 audiveris-5.10.2 已构建成功，或重新运行程序让其自动准备启动器。', logging.WARNING)
            return None

        log_message(f'调用 Audiveris 启动器: {exe}')
        return_code, stdout, stderr, exported_file = invoke_audiveris(safe_output_dir)
        if return_code == 0 or exported_file is not None:
            if return_code != 0:
                log_message('Audiveris 返回了非零退出码，但已成功导出 MusicXML，继续后续处理。', logging.WARNING)
            log_message('Audiveris 执行完成。')
            return safe_output_dir

        page_count = get_pdf_page_count(safe_input_path)
        if safe_input_path.suffix.lower() == '.pdf' and page_count > 1:
            log_message(f'检测到多页 PDF（共 {page_count} 页），正在按页回退处理，跳过识别失败的页面...', logging.WARNING)
            success_pages: list[int] = []
            for page_number in range(1, page_count + 1):
                page_output_dir = safe_output_dir / f'sheet_{page_number:03d}'
                safe_remove_tree(page_output_dir)
                page_output_dir.mkdir(parents=True, exist_ok=True)
                page_code, page_stdout, page_stderr, page_exported = invoke_audiveris(page_output_dir, sheet_number=page_number)
                if page_code == 0 or page_exported is not None:
                    success_pages.append(page_number)
                    log_message(f'第 {page_number} 页已成功导出。')
                else:
                    detail_parts = [part.strip() for part in (page_stderr, page_stdout) if part and part.strip()]
                    detail = '\n'.join(detail_parts)
                    log_message(f'第 {page_number} 页无法识别为有效五线谱，已跳过。{detail[:240]}', logging.WARNING)
            if success_pages:
                log_message(f'Audiveris 已按页完成导出，成功页: {success_pages}')
                return safe_output_dir

        detail_parts = [part.strip() for part in (stderr, stdout) if part and part.strip()]
        detail = '\n'.join(detail_parts)
        log_message('\nAudiveris 执行失败: ' + (detail or '未知错误。'), logging.WARNING)
        return None
    finally:
        if cleanup_input_copy is not None:
            safe_remove_file(cleanup_input_copy)


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


def choose_measures_per_line(measures: list[list[JianpuNote]]) -> int:
    """Choose measures per line based on average note density (dense → 3, otherwise 4)."""
    if not measures:
        return 4
    sample = measures[: min(len(measures), 24)]
    avg_notes = sum(len(measure) for measure in sample) / max(len(sample), 1)
    if avg_notes >= 9:
        return 3
    return 4


def parse_score_to_jianpu(score) -> tuple[list[list[JianpuNote]], list[str], str]:
    """Parse a score into jianpu format; return (measures, header_lines, time_sig)."""
    try:
        key_obj = score.analyze('key') if score else None
    except Exception:
        key_obj = None

    key_header = '1=C'
    if key_obj is not None and key_obj.tonic is not None:
        key_header = f'1={key_obj.tonic.name}'

    measures, time_signature = extract_jianpu_measures(score)
    header_lines: list[str] = [f'{key_header} {time_signature}', '']

    measures_per_line = choose_measures_per_line(measures)
    for i in range(0, len(measures), measures_per_line):
        line_measures = measures[i:i + measures_per_line]
        measure_texts = [' '.join(format_jianpu_note_text(note) for note in measure) for measure in line_measures]
        header_lines.append(' | '.join(measure_texts) + ' |')

    return measures, header_lines, time_signature


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


def build_lilypond_title_markup(title: str) -> str:
    """Build a LilyPond \\markup block that displays the title at the top of the page."""
    safe_title = escape_lilypond_text(title.strip())
    if not safe_title:
        return ''

    lilypond_font_name = resolve_lilypond_font_name() or 'Sans'
    safe_font_name = escape_lilypond_text(lilypond_font_name)
    return (
        '\\markup {\n'
        f'  \\override #\'(font-name . "{safe_font_name}")\n'
        '  \\column {\n'
        f'    \\fill-line {{ \\fontsize #3 \\bold "{safe_title}" }}\n'
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


def sanitize_generated_lilypond_file(ly_path: Path, preferred_title: str, lyrics_lines: Optional[list[str]] = None) -> None:
    """
    Post-process a jianpu-ly .ly file: set title, strip 5-line staves,
    insert title/lyrics markup, configure fonts, and suppress the tagline.
    """
    if not ly_path.exists():
        return

    safe_title = escape_lilypond_text(preferred_title)
    title_markup = build_lilypond_title_markup(preferred_title)
    lyrics_markup = build_lilypond_lyrics_markup(lyrics_lines)
    text = ly_path.read_text(encoding='utf-8', errors='ignore')

    if '% === BEGIN JIANPU STAFF ===' in text and '% === END JIANPU STAFF ===' in text:
        preamble = text.split('\\score {', 1)[0]
        preamble = re.sub(r'^instrument=.*$', '', preamble, flags=re.M)
        preamble = preamble.replace('WithStaff NextPart', 'NextPart')
        match = re.search(r'(% === BEGIN JIANPU STAFF ===.*?% === END JIANPU STAFF ===)', text, flags=re.S)
        if match:
            jianpu_section = match.group(1).replace('WithStaff NextPart', 'NextPart')
            rebuilt = (
                preamble
                + '\n\\score {\n<<\n'
                + jianpu_section
                + f'\n>>\n\\header{{\n  title="{safe_title}"\n  instrument=""\n  tagline=##f\n}}\n\\layout{{}}\n}}\n'
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
    if f'title="{safe_title}"' not in text and f'title = "{safe_title}"' not in text:
        if '\\paper {' in text:
            text = text.replace('\\paper {', f'\\header {{ title="{safe_title}" tagline=##f }}\n\\paper {{', 1)
        else:
            text = f'\\header {{ title="{safe_title}" tagline=##f }}\n' + text
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
) -> bool:
    """
    Render a music21 score to jianpu PDF.
    Tries in order: standard jianpu-ly → strict-timing → reportlab fallback → LilyPond markup fallback.
    """
    txt_content = build_jianpu_ly_text(score, title)
    txt_path.write_text(txt_content, encoding='utf-8')
    log_message(f'已生成 jianpu-ly 文本文件: {txt_path.name}')

    measures, header_lines, _ = parse_score_to_jianpu(score)

    if render_jianpu_ly(txt_path, ly_path):
        log_message(f'已生成 jianpu-ly LilyPond 文件: {ly_path.name}')
        sanitize_generated_lilypond_file(ly_path, title, lyrics_lines)
        pdf_path = render_lilypond_pdf(ly_path)
        if pdf_path is not None:
            copy_generated_pdf(pdf_path, output_pdf_path)
            log_message(f'已通过 LilyPond 生成简谱 PDF: {output_pdf_path.name}')
            return True
        log_message('标准 jianpu-ly 路径失败，尝试严格按拍号重建简谱。', logging.WARNING)
    else:
        log_message('文本版 jianpu-ly 失败，尝试严格按拍号重建简谱。', logging.WARNING)

    strict_txt_content = build_jianpu_ly_text(score, title, use_strict_timing=True)
    txt_path.write_text(strict_txt_content, encoding='utf-8')
    if render_jianpu_ly(txt_path, ly_path):
        log_message(f'已通过严格拍号重建生成 jianpu-ly 文件: {ly_path.name}')
        sanitize_generated_lilypond_file(ly_path, title, lyrics_lines)
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


def generate_jianpu_pdf_from_mxl(
    mxl_path: Path,
    output_pdf_path: Path,
    temp_dir: Path,
    midi_output_path: Path | None = None,
    preferred_title: str | None = None,
    source_path: Path | None = None,
) -> bool:
    """
    Generate a jianpu PDF from MusicXML: parse → export MIDI → rebuild score → render jianpu PDF.
    Uses a temporary MIDI file when midi_output_path is None.
    """
    txt_path = temp_dir / f'{mxl_path.stem}.jianpu.txt'
    ly_path = temp_dir / f'{mxl_path.stem}.jianpu.ly'
    midi_path = midi_output_path or (temp_dir / f'{mxl_path.stem}.normalized.mid')
    cleanup_temp_midi = midi_output_path is None

    try:
        source_score = converter.parse(str(mxl_path))
        title = (preferred_title or output_pdf_path.stem.replace('.jianpu', '') or mxl_path.stem).strip()
        apply_score_title(source_score, title)
        lyrics_lines = collect_preserved_lyrics_lines(source_score, source_path)

        if not render_midi_from_score(source_score, midi_path):
            log_message('MXL -> MIDI 失败，无法继续生成简谱 PDF。', logging.WARNING)
            return False

        score = load_score_from_midi(midi_path)
        if score is None:
            log_message('MIDI -> 乐谱重建失败，无法继续生成简谱 PDF。', logging.WARNING)
            return False

        apply_score_title(score, title)
        log_message('当前转换链路: 乐谱文件(PDF/JPG/PNG) -> MXL/MusicXML -> MIDI -> 简谱 PDF')
        return render_score_to_jianpu_pdf(score, title, output_pdf_path, temp_dir, txt_path, ly_path, lyrics_lines)
    except Exception as exc:
        log_message(f'生成简谱 PDF 失败: {mxl_path.name}，原因: {exc}', logging.WARNING)
        return False
    finally:
        safe_remove_file(txt_path)
        safe_remove_file(ly_path)
        if cleanup_temp_midi:
            safe_remove_file(midi_path)


def collect_duplicate_names(
    source_files: list[Path],
    output_dir: Path,
    generate_midi: bool,
    history: dict[str, dict],
) -> list[str]:
    """Return input filenames that already have matching outputs in the conversion history."""
    duplicate_names: list[str] = []
    for source_file in source_files:
        output_pdf = output_dir / f'{source_file.stem}.jianpu.pdf'
        output_midi = output_dir / f'{source_file.stem}.mid' if generate_midi else None
        if has_existing_output_match(source_file, output_pdf, output_midi, history):
            duplicate_names.append(source_file.name)
    return duplicate_names


def find_first_musicxml_file(audiveris_out: Path, preferred_stem: str) -> Optional[Path]:
    """Find the first MXL/MusicXML in an Audiveris output dir, preferring the given stem."""
    candidates = sorted(list(audiveris_out.rglob(f'{preferred_stem}*.mxl')) + list(audiveris_out.rglob(f'{preferred_stem}*.musicxml')))
    if not candidates:
        candidates = sorted(list(audiveris_out.rglob('*.mxl')) + list(audiveris_out.rglob('*.musicxml')))
    return candidates[0] if candidates else None


def cleanup_output_directory(output_dir: Path, generate_midi: bool) -> None:
    """Remove intermediate files from output dir, keeping only PDF (and MIDI if generate_midi=True)."""
    allowed_suffixes = {'.pdf'}
    if generate_midi:
        allowed_suffixes.update({'.mid', '.midi'})

    for path in output_dir.iterdir():
        if path.is_dir():
            safe_remove_tree(path)
        elif path.is_file() and path.suffix.lower() not in allowed_suffixes:
            safe_remove_file(path)


def process_single_input_to_jianpu(source_file: Path, file_temp_dir: Path, output_pdf: Path, output_midi: Optional[Path]) -> bool:
    """Process one input file: Audiveris → find MXL → generate jianpu PDF (optionally MIDI)."""
    audiveris_out = run_audiveris_batch(source_file, output_dir=file_temp_dir)
    if audiveris_out is None:
        log_message(f'跳过 {source_file.name}，Audiveris 处理失败。', logging.WARNING)
        return False

    mxl_file = find_first_musicxml_file(audiveris_out, source_file.stem)
    if mxl_file is None:
        log_message(f'未找到 Audiveris 输出的 MXL 文件，跳过 {source_file.name}。', logging.WARNING)
        return False

    return generate_jianpu_pdf_from_mxl(
        mxl_file,
        output_pdf,
        file_temp_dir,
        output_midi,
        preferred_title=source_file.stem,
        source_path=source_file,
    )


def process_single_pdf_to_jianpu(pdf_file: Path, file_temp_dir: Path, output_pdf: Path, output_midi: Optional[Path]) -> bool:
    """Backward-compatible alias for process_single_input_to_jianpu."""
    return process_single_input_to_jianpu(pdf_file, file_temp_dir, output_pdf, output_midi)


def print_conversion_summary(summary: ConversionSummary, generate_midi: bool, output_dir: Path) -> None:
    """Print the batch conversion summary: counts and skipped/failed file lists."""
    log_message('\n处理汇总:')
    log_message(f'  - 总文件数: {summary.total}')
    log_message(f'  - 成功: {summary.success}')
    log_message(f'  - 跳过: {summary.skipped}')
    log_message(f'  - 失败: {summary.failed}')

    if summary.skipped_files:
        log_message('本次已跳过以下文件:')
        for skipped_name in summary.skipped_files:
            log_message(f'  - {skipped_name}')

    if summary.failed_files:
        log_message('本次失败文件:')
        for failed_name in summary.failed_files:
            log_message(f'  - {failed_name}', logging.WARNING)

    if summary.success == 0:
        log_message('已完成，但无新增文件。')
    else:
        if generate_midi:
            log_message('已完成。简谱 PDF 和 MIDI 文件已保存在 Output 文件夹。')
        else:
            log_message('已完成。简谱 PDF 文件已保存在 Output 文件夹，仅保留 PDF 文件。')
        try:
            os.startfile(str(output_dir))
        except Exception:
            pass


def process_bulk_input_to_jianpu(config: AppConfig | None = None) -> ConversionSummary:
    """
    Batch-process all supported files in Input/.
    Prompts the user to confirm and choose MIDI output, skips existing files, and cleans up temp files.
    """
    config = config or AppConfig()
    script_dir = get_app_base_dir()
    input_dir, output_dir, temp_dir = build_runtime_paths(script_dir, config)
    history = load_conversion_history(script_dir)
    summary = ConversionSummary()

    if not input_dir.exists() or not input_dir.is_dir():
        log_message('未找到 Input 文件夹。请在脚本目录下创建 Input 文件夹并放入 PDF/JPG/PNG 乐谱文件。', logging.WARNING)
        return summary

    source_files = sorted([p for p in input_dir.iterdir() if is_supported_score_file(p)])
    if not source_files:
        log_message('Input 文件夹中未找到可处理的 PDF/JPG/PNG 文件。', logging.WARNING)
        return summary

    summary.total = len(source_files)
    log_message('待转换乐谱文件列表:')
    for source_file in source_files:
        log_message(f'  - {source_file.name}')
    log_message(f'总文件数: {len(source_files)}')

    answer = input('是否转换以上所有 PDF/JPG/PNG 乐谱为简谱 PDF?（Y/N） ').strip().upper()
    if answer != 'Y':
        log_message('已取消转换。')
        return summary

    midi_answer = input('是否同时生成 MIDI 文件?（Y/N） ').strip().upper()
    generate_midi = midi_answer == 'Y'

    output_dir.mkdir(parents=True, exist_ok=True)
    duplicate_names = collect_duplicate_names(source_files, output_dir, generate_midi, history)
    skip_all_existing = confirm_skip_all_existing(duplicate_names)

    safe_remove_tree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    for index, source_file in enumerate(source_files, start=1):
        log_message(f'\n=== 正在处理: {source_file.name} ===')
        output_pdf = output_dir / f'{source_file.stem}.jianpu.pdf'
        output_midi = output_dir / f'{source_file.stem}.mid' if generate_midi else None

        if skip_all_existing and source_file.name in duplicate_names:
            log_message(f'已跳过: {source_file.name}')
            summary.skipped += 1
            summary.skipped_files.append(source_file.name)
            continue

        job_token = hashlib.sha1(str(source_file).encode('utf-8', errors='ignore')).hexdigest()[:8]
        file_temp_dir = temp_dir / f'job_{index:03d}_{job_token}'
        safe_remove_tree(file_temp_dir)
        file_temp_dir.mkdir(parents=True, exist_ok=True)

        if process_single_input_to_jianpu(source_file, file_temp_dir, output_pdf, output_midi):
            log_message(f'已生成简谱 PDF: {output_pdf.name}')
            summary.success += 1
            summary.generated_pdfs.append(output_pdf.name)
            update_conversion_history(history, source_file, output_pdf, output_midi)
            save_conversion_history(script_dir, history)
        else:
            summary.failed += 1
            summary.failed_files.append(source_file.name)
            log_message(f'生成简谱 PDF 失败: {source_file.name}', logging.WARNING)

    log_message('\n开始删除中间文件...')
    safe_remove_tree(temp_dir)
    cleanup_output_directory(output_dir, generate_midi)
    print_conversion_summary(summary, generate_midi, output_dir)
    return summary


def process_bulk_pdf_to_jianpu(config: AppConfig | None = None) -> ConversionSummary:
    """Backward-compatible alias for process_bulk_input_to_jianpu."""
    return process_bulk_input_to_jianpu(config)


def wait_for_exit_key(prompt: str = 'Conversion complete. Press any key to exit...') -> None:
    """Wait for a keypress before exiting (msvcrt on Windows, falls back to input())."""
    try:
        import msvcrt

        print(prompt, end='', flush=True)
        msvcrt.getwch()
        print()
    except Exception:
        try:
            input(prompt)
        except EOFError:
            print('\n程序已退出。')


def main() -> None:
    """Entry point: initialise logging, run batch conversion, wait for keypress before exit."""
    setup_logging(get_app_base_dir())
    log_message('=========================================')
    log_message(f'批量乐谱(PDF/JPG/PNG) -> 简谱 PDF 转换工具 v{APP_VERSION}')
    log_message('版权所有 © 2026 Tsukamotoshio  保留所有权利')
    log_message('=========================================')
    try:
        process_bulk_input_to_jianpu(AppConfig())
    except EOFError:
        log_message('\n输入已结束，程序退出。', logging.WARNING)
        return
    except KeyboardInterrupt:
        log_message('\n已取消，程序退出。', logging.WARNING)
        return

    wait_for_exit_key()


if __name__ == '__main__':
    main()
