# core/utils.py — 基础工具函数
# 拆分自 convert.py
import base64
import getpass
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None  # type: ignore

from .config import (
    AUDIVERIS_RUNTIME_DIR_NAME,
    CJK_FONT_CANDIDATES,
    CONVERSION_HISTORY_FILE,
    CONVERSION_PIPELINE_VERSION,
    ENABLE_LYRICS_OUTPUT,
    LOGGER,
    RUNTIME_ASSETS_DIR_NAME,
    SUPPORTED_INPUT_SUFFIXES,
    AppConfig,
    ConversionSummary,
)

# 可变全局：由 setup_logging 在首次调用时赋值
LOG_FILE_PATH: Optional[Path] = None


LLM_CONFIG_FILENAME = 'llm_config.json'
USER_CONFIG_DIR_NAME = 'ConvertTool'


def get_app_base_dir() -> Path:
    """Return the application root directory (exe parent when frozen, script parent otherwise)."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    # core/utils.py is one level below the project root
    return Path(__file__).resolve().parent.parent


def get_user_config_dir() -> Path:
    """Return a per-user config directory outside the packaged app installation."""
    if platform.system() == 'Windows':
        local_app_data = os.environ.get('LOCALAPPDATA', '')
        if local_app_data:
            return Path(local_app_data) / USER_CONFIG_DIR_NAME
    elif platform.system() == 'Darwin':
        return Path.home() / 'Library' / 'Application Support' / USER_CONFIG_DIR_NAME

    xdg_config = os.environ.get('XDG_CONFIG_HOME')
    if xdg_config:
        return Path(xdg_config) / USER_CONFIG_DIR_NAME
    return Path.home() / '.config' / USER_CONFIG_DIR_NAME


def get_llm_config_path(base_dir: Optional[Path] = None) -> Path:
    config_dir = get_user_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / LLM_CONFIG_FILENAME


def _derive_machine_key() -> bytes:
    """Derive a local machine-specific secret for lightweight API key encryption."""
    seed = platform.node() + '|' + getpass.getuser()
    return hashlib.sha256(seed.encode('utf-8')).digest()


def _xor_encrypt(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _encrypt_api_key(api_key: str) -> str:
    try:
        key = _derive_machine_key()
        encrypted = _xor_encrypt(api_key.encode('utf-8'), key)
        return base64.urlsafe_b64encode(encrypted).decode('ascii')
    except Exception:
        return api_key


def _decrypt_api_key(encoded: str) -> str:
    try:
        key = _derive_machine_key()
        encrypted = base64.urlsafe_b64decode(encoded.encode('ascii'))
        return _xor_encrypt(encrypted, key).decode('utf-8', errors='replace')
    except Exception:
        return encoded


def load_llm_config(base_dir: Path) -> dict[str, str]:
    """Load stored LLM configuration from app base dir, if present."""
    path = get_llm_config_path(base_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        api_key = payload.get('api_key', '')
        return {
            'api_key': _decrypt_api_key(api_key) if api_key else '',
            'provider': str(payload.get('provider', '') or ''),
            'model': str(payload.get('model', '') or ''),
        }
    except Exception:
        return {}


def save_llm_config(base_dir: Path, api_key: str, provider: str = '', model: str = '') -> None:
    """Save LLM configuration to a local config file with lightweight local encryption."""
    try:
        path = get_llm_config_path(base_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'api_key': _encrypt_api_key(api_key.strip()),
            'provider': provider.strip(),
            'model': model.strip(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def load_llm_api_key(base_dir: Path) -> str:
    return load_llm_config(base_dir).get('api_key', '')


def load_llm_provider(base_dir: Path) -> str:
    return load_llm_config(base_dir).get('provider', '')


def load_llm_model(base_dir: Path) -> str:
    return load_llm_config(base_dir).get('model', '')


def save_llm_api_key(base_dir: Path, api_key: str) -> None:
    config = load_llm_config(base_dir)
    save_llm_config(base_dir, api_key.strip(), config.get('provider', ''), config.get('model', ''))


def save_llm_provider(base_dir: Path, provider: str) -> None:
    config = load_llm_config(base_dir)
    save_llm_config(base_dir, config.get('api_key', ''), provider.strip(), config.get('model', ''))


def save_llm_model(base_dir: Path, model: str) -> None:
    config = load_llm_config(base_dir)
    save_llm_config(base_dir, config.get('api_key', ''), config.get('provider', ''), model.strip())


def get_runtime_search_roots() -> list[Path]:
    """Return a deduplicated list of runtime asset search roots."""
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


def _get_logs_dir(base_dir: Path) -> Path:
    """Return the logs directory.

    When frozen (installed under a protected path such as C:\\Program Files),
    redirect logs to %LOCALAPPDATA%\\ConvertTool\\logs so that normal users can
    write log files without requiring administrator privileges.
    """
    if getattr(sys, 'frozen', False):
        local_app_data = os.environ.get('LOCALAPPDATA', '')
        if local_app_data:
            return Path(local_app_data) / 'ConvertTool' / AppConfig().logs_dir_name
    return base_dir / AppConfig().logs_dir_name


def setup_logging(base_dir: Path) -> Optional[Path]:
    """Initialise logging to a timestamped file under logs/; return the log file path."""
    global LOG_FILE_PATH

    if LOGGER.handlers:
        return LOG_FILE_PATH

    logs_dir = _get_logs_dir(base_dir)
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
    try:
        sys.stdout.buffer.write((message + '\n').encode('utf-8', errors='replace'))
    except Exception:
        try:
            print(message)
        except Exception:
            pass
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


def compute_file_sha256(file_path: Path) -> str:
    """Compute the SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    with file_path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_conversion_history(base_dir: Path) -> dict[str, dict]:
    """Load the conversion history from JSON."""
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
    """Return True if the output files already exist and the source file is unchanged.

    Duplicate detection is based purely on:
      1. The final output PDF (and MIDI if requested) must exist in Output/.
      2. The source file SHA-256 must match the recorded value.

    Pipeline version is intentionally NOT checked here — algorithm updates do NOT
    force re-conversion.  If a user wants to regenerate output after an algorithm
    update they should delete the relevant Output/ files manually.
    Intermediate files (editor-workspace .jianpu.txt) are always overwritten by
    _save_editor_files whenever a conversion actually runs.
    """
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
    return record.get('sha256') == current_sha256


def confirm_skip_all_existing(duplicate_names: list[str]) -> bool:
    """Show the user a list of files with existing outputs and ask whether to skip all."""
    if not duplicate_names:
        return False
    log_message('检测到以下文件在 Output 中已存在与上次相同的转换结果:')
    for name in duplicate_names:
        log_message(f'  - {name}')
    answer = input('是否全部跳过这些重复文件？（Y/N） ').strip().upper()
    return answer == 'Y'


def update_conversion_history(history: dict[str, dict], pdf_file: Path, output_pdf: Path, output_midi: Optional[Path]) -> None:
    """Record the conversion result for a file in the history dict."""
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


def build_safe_ascii_name(name: str, fallback: str = 'file') -> str:
    """Convert a string to a safe ASCII filename."""
    safe_name = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('._-')
    return safe_name or fallback


def is_supported_score_file(path: Path) -> bool:
    """Return True if the path is a supported score input file."""
    return path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES


def find_packaged_runtime_dir(dir_name: str) -> Optional[Path]:
    """Find a named subdirectory under any runtime search root."""
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


def find_first_musicxml_file(audiveris_out: Path, preferred_stem: str) -> Optional[Path]:
    """Find the first MXL/MusicXML in an Audiveris output dir, preferring the given stem."""
    candidates = sorted(
        list(audiveris_out.rglob(f'{preferred_stem}*.mxl')) +
        list(audiveris_out.rglob(f'{preferred_stem}*.musicxml'))
    )
    if not candidates:
        candidates = sorted(
            list(audiveris_out.rglob('*.mxl')) +
            list(audiveris_out.rglob('*.musicxml'))
        )
    return candidates[0] if candidates else None


def count_musicxml_notes(mxl_path: Path) -> tuple[int, int, int]:
    """Count note and rest elements in a MusicXML/MXL file.

    Returns
    -------
    (non_rest_notes, total_notes, measures)
    """
    try:
        from music21 import converter as _conv
        score = _conv.parse(str(mxl_path))
        notes = list(score.recurse().notesAndRests)
        non_rest = sum(1 for n in notes if not getattr(n, 'isRest', False))
        total = len(notes)
        measures = len(list(score.recurse().getElementsByClass('Measure')))
        return non_rest, total, measures
    except Exception:
        return 0, 0, 0


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


def cleanup_old_temporary_paths(paths: list[Path], max_age_days: int = 7) -> None:
    """Remove files and directories older than max_age_days under the given roots."""
    threshold = time.time() - max_age_days * 86400
    for root in paths:
        if not root.exists() or not root.is_dir():
            continue
        for entry in root.iterdir():
            try:
                if entry.stat().st_mtime >= threshold:
                    continue
            except OSError:
                continue
            if entry.is_dir():
                safe_remove_tree(entry)
            else:
                safe_remove_file(entry)


def cleanup_output_directory(output_dir: Path, generate_midi: bool) -> None:
    """Remove intermediate files from output dir, keeping only PDF (and MIDI if requested)."""
    allowed_suffixes = {'.pdf'}
    if generate_midi:
        allowed_suffixes.update({'.mid', '.midi'})
    for path in output_dir.iterdir():
        if path.is_dir():
            safe_remove_tree(path)
        elif path.is_file() and path.suffix.lower() not in allowed_suffixes:
            safe_remove_file(path)


def print_conversion_summary(summary: ConversionSummary, generate_midi: bool, output_dir: Path) -> None:
    """Print the batch conversion summary."""
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


# ──────────────────────────────────────────────
# 歌词提取
# ──────────────────────────────────────────────

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
    """Extract lyric lines from the embedded text of a PDF."""
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
    """Lyrics entry point: tries score first, then falls back to the source PDF."""
    if not ENABLE_LYRICS_OUTPUT:
        return []
    lyric_lines = extract_lyrics_lines_from_score(score)
    if lyric_lines:
        return lyric_lines
    if source_path is not None:
        return extract_lyrics_lines_from_pdf(source_path)
    return []
