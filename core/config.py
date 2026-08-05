# core/config.py — constants, dataclasses, global logger
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# Jianpu note name map (Western pitch → numbered notation digit)
JIANPU_MAP = {
    'C': '1',
    'D': '2',
    'E': '3',
    'F': '4',
    'G': '5',
    'A': '6',
    'B': '7',
}

CJK_FONT_CANDIDATES = [
    ('Meiryo', r'%SystemRoot%\Fonts\meiryo.ttc'),
    ('Yu Gothic', r'%SystemRoot%\Fonts\YuGothM.ttc'),
    ('MS Gothic', r'%SystemRoot%\Fonts\msgothic.ttc'),
    ('Microsoft YaHei', r'%SystemRoot%\Fonts\msyh.ttc'),
    ('Microsoft JhengHei', r'%SystemRoot%\Fonts\msjh.ttc'),
    ('SimSun', r'%SystemRoot%\Fonts\simsun.ttc'),
    ('SimHei', r'%SystemRoot%\Fonts\simhei.ttf'),
    ('Microsoft YaHei Bold', r'%SystemRoot%\Fonts\msyhbd.ttc'),
]

ALLOWED_JIANPU_DURATIONS = [4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.375, 0.25, 0.1875, 0.125]
SUPPORTED_INPUT_SUFFIXES = {'.pdf', '.png', '.jpg', '.jpeg'}
# Audio inputs routed to the piano-transcription engine (audio → MIDI → MusicXML).
# Only formats decodable by the bundled libsndfile (via soundfile) are accepted:
# WAV/FLAC/OGG/MP3 are all supported by libsndfile 1.2.2. .m4a/AAC is intentionally
# excluded — libsndfile can't decode it and no ffmpeg is bundled, so accepting it
# would only produce a silent "audio read failed" for the user (verified 2026-07-11).
SUPPORTED_AUDIO_SUFFIXES = {'.mp3', '.wav', '.flac', '.ogg'}
ENABLE_LYRICS_OUTPUT = True
MAX_AUDIVERIS_SECONDS = 1800
MAX_HOMR_SECONDS = 900
MAX_LILYPOND_SECONDS = 600
MAX_JIANPU_LY_SECONDS = 120
DEFAULT_AUDIVERIS_MIN_JAVA_VERSION = 25
RUNTIME_ASSETS_DIR_NAME = 'package-assets'
AUDIVERIS_RUNTIME_DIR_NAME = 'audiveris-runtime'
LILYPOND_RUNTIME_DIR_NAME = 'lilypond-runtime'
WAIFU2X_RUNTIME_DIR_NAME = 'waifu2x-runtime'
REALESRGAN_RUNTIME_DIR_NAME = 'realesrgan-runtime'
OMR_ENGINE_DIR_NAME = 'omr_engine'
AUDIVERIS_INSTALL_DIR_NAME = 'Audiveris'
AUDIVERIS_SOURCE_DIR_NAMES = ('audiveris', 'audiveris-5.10.2')
HOMR_SOURCE_DIR_NAME = 'homr'
CONVERSION_HISTORY_FILE = 'conversion_history.json'
CONVERSION_PIPELINE_VERSION = 7
APP_VERSION = '0.5.1'
AUDIVERIS_MSI_NAMES = [
    'Audiveris-5.10.2-windows-x86_64.msi',
    'Audiveris.msi',
    'audiveris.msi',
]


# Super-resolution engine
class SREngine(Enum):
    """Available super-resolution upscaling engines.

    WAIFU2X:    waifu2x-ncnn-vulkan; optimised for line-art (current default).
    REALESRGAN: Real-ESRGAN anime model; generally better quality for sheet music.
                Prefers realesrgan-ncnn-vulkan binary, falls back to Python script.
    """
    WAIFU2X = 'waifu2x'
    REALESRGAN = 'realesrgan'


# OMR engine
class OMREngine(Enum):
    """Available optical music recognition engines.

    AUTO:      Auto-select by input (recommended): images → Homr; vector PDF →
               Audiveris; bitmap (scanned) PDF → Homr. See pipeline.py routing.
    AUDIVERIS: Java-based traditional OMR; accepts PDF/image; outputs MusicXML (.mxl).
               Requires a local JDK + Audiveris installation.
    HOMR:      End-to-end DL OMR via homr; accepts images and PDFs (multi-page
               supported, pages converted to PNG); outputs .musicxml.
    """
    AUTO = 'auto'
    AUDIVERIS = 'audiveris'
    HOMR = 'homr'

# Global logger (initialised lazily by utils.setup_logging)
# LOG_FILE_PATH 是可变状态，定义在 core/utils.py
LOGGER = logging.getLogger('convert')


# Dataclasses
@dataclass(frozen=True)
class AppConfig:
    """Application directory layout and OMR engine selection."""
    input_dir_name: str = 'Input'
    output_dir_name: str = 'Output'
    temp_dir_name: str = 'omr-temp'
    logs_dir_name: str = 'logs'
    omr_engine: OMREngine = OMREngine.AUDIVERIS


@dataclass
class ConversionSummary:
    """Batch conversion counters and per-status file lists."""
    total: int = 0
    success: int = 0
    skipped: int = 0
    failed: int = 0
    generated_pdfs: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    failed_files: list[str] = field(default_factory=list)


@dataclass
class JianpuNote:
    """A single jianpu note or rest: pitch, accidental, octave dots, duration, MIDI pitch."""
    symbol: str
    accidental: str
    upper_dots: int
    lower_dots: int
    duration: float
    duration_dots: int
    midi: Optional[int]
    is_rest: bool
    # Multi-verse lyrics anchored to this note: verse number -> (syllable text,
    # hyphenated-to-next-syllable). Anchoring to the note (rather than a free-floating
    # text stream) means inserting/deleting notes never desyncs the lyrics — see
    # docs/修复计划2与简谱编辑器规划.md's B10.4 for the rationale.
    lyrics: dict[int, tuple[str, bool]] = field(default_factory=dict)


@dataclass
class JianpuSection:
    """One ``NextPart``-delimited segment of a jianpu-ly document.

    Only ``time_sig`` varies per section in real jianpu-ly output — title,
    composer, key and tempo are whole-document properties that appear once
    and apply uniformly across every section (verified against real
    multi-voice files and the current serializer; see the 2026-08-05
    correction in docs/jianpu-ly-syntax.md §1.6 and the fix-plan doc's B3).
    """
    time_sig: str
    measures: list[list[JianpuNote]] = field(default_factory=list)


@dataclass
class JianpuDoc:
    """Editor-model form of a jianpu-ly document (修复计划2 B3, 方案 C).

    The 唯一事实源 (single source of truth) for the graphical editor: parsed
    from jianpu-ly text (``core.notation.jianpu.parser.parse_jianpu_ly_text``)
    and serialized back to it (``core.notation.jianpu.build_jianpu_ly_text_from_doc``).
    ``midi`` on each note is intentionally left ``None`` by the parser (not
    needed for text round-tripping; computing it requires reversing the
    key-tonic → semitone math in primitives.py, deferred to whichever stage
    first needs it for playback).
    """
    title: str
    composer: str = ''
    key_header: str = '1=C'
    tempo: int = 0
    sections: list[JianpuSection] = field(default_factory=list)
    voice_groups: list[list[int]] = field(default_factory=list)
    repeat_barlines: dict[int, dict[str, bool]] = field(default_factory=dict)
