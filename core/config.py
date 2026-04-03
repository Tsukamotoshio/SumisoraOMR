# core/config.py — 常量、数据类、全局 logger
# 拆分自 convert.py
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────
# 简谱音名映射
# ──────────────────────────────────────────────
JIANPU_MAP = {
    'C': '1',
    'D': '2',
    'E': '3',
    'F': '4',
    'G': '5',
    'A': '6',
    'B': '7',
}

# CJK 字体候选列表（优先顺序），用于 PDF 和 LilyPond 输出
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

# jianpu-ly.py 下载 fallback URL 列表
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
WAIFU2X_RUNTIME_DIR_NAME = 'waifu2x-runtime'
AUDIVERIS_INSTALL_DIR_NAME = 'Audiveris'
AUDIVERIS_SOURCE_DIR_NAMES = ('audiveris-5.10.2', 'audiveris')
CONVERSION_HISTORY_FILE = 'conversion_history.json'
CONVERSION_PIPELINE_VERSION = 5
APP_VERSION = '0.1.2'
AUDIVERIS_MSI_NAMES = [
    'Audiveris-5.10.2-windows-x86_64.msi',
    'Audiveris.msi',
    'audiveris.msi',
]

# ──────────────────────────────────────────────
# 全局 Logger（延迟由 utils.setup_logging 初始化）
# LOG_FILE_PATH 是可变状态，定义在 core/utils.py
# ──────────────────────────────────────────────
LOGGER = logging.getLogger('convert')


# ──────────────────────────────────────────────
# 数据类
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class AppConfig:
    """应用目录结构：输入、输出、临时、日志文件夹名称。"""
    input_dir_name: str = 'Input'
    output_dir_name: str = 'Output'
    temp_dir_name: str = 'audiveris-temp'
    logs_dir_name: str = 'logs'


@dataclass
class ConversionSummary:
    """批量转换统计：成功/跳过/失败计数及文件列表。"""
    total: int = 0
    success: int = 0
    skipped: int = 0
    failed: int = 0
    generated_pdfs: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    failed_files: list[str] = field(default_factory=list)


@dataclass
class JianpuNote:
    """单个简谱音符或休止符，携带音高、升降号、八度点、时值、MIDI 音高。"""
    symbol: str
    accidental: str
    upper_dots: int
    lower_dots: int
    duration: float
    duration_dots: int
    midi: Optional[int]
    is_rest: bool
