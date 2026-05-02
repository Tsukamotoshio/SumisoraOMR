# core/config.py — constants, dataclasses, global logger
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
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
    ('Meiryo', r'%SystemRoot%\\Fonts\\meiryo.ttc'),
    ('Yu Gothic', r'%SystemRoot%\\Fonts\\YuGothM.ttc'),
    ('MS Gothic', r'%SystemRoot%\\Fonts\\msgothic.ttc'),
    ('Microsoft YaHei', r'%SystemRoot%\\Fonts\\msyh.ttc'),
    ('Microsoft JhengHei', r'%SystemRoot%\\Fonts\\msjh.ttc'),
    ('SimSun', r'%SystemRoot%\\Fonts\\simsun.ttc'),
    ('SimHei', r'%SystemRoot%\\Fonts\\simhei.ttf'),
    ('Microsoft YaHei Bold', r'%SystemRoot%\\Fonts\\msyhbd.ttc'),
]

# jianpu-ly.py download fallback URLs
JIANPU_LY_URLS = [
    'https://ssb22.user.srcf.net/mwrhome/jianpu-ly.py',
    'http://ssb22.user.srcf.net/mwrhome/jianpu-ly.py',
    'https://ssb22.gitlab.io/mwrhome/jianpu-ly.py',
]

ALLOWED_JIANPU_DURATIONS = [4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.375, 0.25, 0.1875, 0.125]
SUPPORTED_INPUT_SUFFIXES = {'.pdf', '.png', '.jpg', '.jpeg'}
ENABLE_LYRICS_OUTPUT = True
MAX_AUDIVERIS_SECONDS = 1800
MAX_HOMR_SECONDS = 900
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
APP_VERSION = '0.3.1'
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

    AUTO:      Auto-select by input format (recommended). Currently always Audiveris.
    AUDIVERIS: Java-based traditional OMR; accepts PDF/image; outputs MusicXML (.mxl).
               Requires a local JDK + Audiveris installation.
    HOMR:      End-to-end DL OMR via homr; accepts images and the first page of PDFs;
               outputs .musicxml. Uses omr_engine/homr repo directly.
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
