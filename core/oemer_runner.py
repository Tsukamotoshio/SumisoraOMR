# core/oemer_runner.py — oemer OMR 引擎封装（experimental-v0.2.0）
#
# oemer 是一款基于深度学习的端到端 OMR 引擎：
#   pip install oemer
#   oemer <img_path> -o <output_dir>  → 输出 <stem>.musicxml
#
# 局限：
#   - 仅支持图片输入（PNG/JPG），不支持 PDF。
#     本模块针对 PDF 输入会先将首页渲染为 PNG，再交给 oemer 处理。
#   - 需要在当前 Python 环境中安装 oemer（不依赖 Java）。
#
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .config import (
    LOGGER,
    MAX_OEMER_SECONDS,
)
from .utils import (
    find_first_musicxml_file,
    get_app_base_dir,
    log_message,
)


# ──────────────────────────────────────────────
# oemer 可用性检查
# ──────────────────────────────────────────────

def find_oemer_executable() -> Optional[str]:
    """返回 oemer 命令路径，若未安装则返回 None。"""
    # 优先在当前 Python 环境的 Scripts/bin 目录查找
    scripts_dir = Path(sys.executable).parent
    for candidate in ('oemer', 'oemer.exe'):
        p = scripts_dir / candidate
        if p.is_file():
            return str(p)
    # 退回到 PATH 全局查找
    found = shutil.which('oemer')
    return found


def check_oemer_available() -> bool:
    """若 oemer 可被调用则返回 True，否则返回 False 并打印提示。"""
    exe = find_oemer_executable()
    if exe is None:
        log_message(
            'oemer 未安装或不在 PATH 中。\n'
            '请执行以下命令安装：\n'
            '  pip install oemer\n'
            '安装后重新运行程序即可使用 oemer 引擎。',
            logging.ERROR,
        )
        return False
    return True


# ──────────────────────────────────────────────
# PDF → 图片（oemer 不支持 PDF，需先转换）
# ──────────────────────────────────────────────

def _pdf_first_page_to_png(pdf_path: Path, output_dir: Path) -> Optional[Path]:
    """将 PDF 首页渲染为 PNG，返回生成的图片路径；失败返回 None。

    优先使用 Pillow + pdf2image（需依赖 poppler），若不可用则尝试 PyMuPDF (fitz)。
    """
    png_path = output_dir / f'{pdf_path.stem}_page1.png'
    output_dir.mkdir(parents=True, exist_ok=True)

    # 方案 A：pdf2image (poppler)
    try:
        from pdf2image import convert_from_path  # type: ignore
        images = convert_from_path(str(pdf_path), first_page=1, last_page=1, dpi=300)
        if images:
            images[0].save(str(png_path), 'PNG')
            log_message(f'[oemer] PDF 首页已转换为图片: {png_path.name}')
            return png_path
    except ImportError:
        pass
    except Exception as exc:
        log_message(f'[oemer] pdf2image 转换失败: {exc}', logging.WARNING)

    # 方案 B：PyMuPDF (fitz)
    try:
        import fitz  # type: ignore  # PyMuPDF
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        mat = fitz.Matrix(300 / 72, 300 / 72)
        pix = page.get_pixmap(matrix=mat)
        pix.save(str(png_path))
        doc.close()
        log_message(f'[oemer] PDF 首页已转换为图片 (PyMuPDF): {png_path.name}')
        return png_path
    except ImportError:
        pass
    except Exception as exc:
        log_message(f'[oemer] PyMuPDF 转换失败: {exc}', logging.WARNING)

    log_message(
        '[oemer] PDF 转图片失败：请安装 pdf2image（需 poppler）或 PyMuPDF：\n'
        '  pip install pdf2image   # 还需安装 poppler-utils\n'
        '  pip install pymupdf',
        logging.ERROR,
    )
    return None


# ──────────────────────────────────────────────
# 核心调用
# ──────────────────────────────────────────────

def run_oemer(image_path: Path, output_dir: Path) -> Optional[Path]:
    """对单张图片调用 oemer，返回生成的 MusicXML 文件路径；失败返回 None。

    oemer 输出约定：命令行传入 ``-o <output_dir>``，oemer 在该目录下生成
    ``<image_stem>.musicxml``（部分版本为 ``result.musicxml``）。
    """
    exe = find_oemer_executable()
    if exe is None:
        log_message('[oemer] 未找到 oemer 可执行文件，请先 pip install oemer', logging.ERROR)
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [exe, str(image_path), '-o', str(output_dir)]
    log_message(f'[oemer] 调用: {" ".join(cmd)}')

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=MAX_OEMER_SECONDS,
        )
    except subprocess.TimeoutExpired:
        log_message(f'[oemer] 超时（>{MAX_OEMER_SECONDS}s），识别已中断。', logging.ERROR)
        return None
    except Exception as exc:
        log_message(f'[oemer] 运行异常: {exc}', logging.ERROR)
        return None

    if result.stdout:
        for line in result.stdout.strip().splitlines():
            log_message(f'[oemer] {line}')
    if result.stderr:
        for line in result.stderr.strip().splitlines():
            log_message(f'[oemer][err] {line}', logging.DEBUG)

    if result.returncode != 0:
        log_message(f'[oemer] 退出码 {result.returncode}，识别失败。', logging.ERROR)
        return None

    # 定位输出的 MusicXML 文件
    mxl = find_first_musicxml_file(output_dir, image_path.stem)
    if mxl is None:
        # oemer 某些版本将结果固定命名为 result.musicxml
        fallback = output_dir / 'result.musicxml'
        if fallback.exists():
            mxl = fallback
    if mxl is None:
        log_message('[oemer] 识别完毕，但未找到输出的 MusicXML 文件。', logging.ERROR)
        return None

    log_message(f'[oemer] 输出 MusicXML: {mxl.name}')
    return mxl


def run_oemer_batch(input_path: Path, output_dir: Optional[Path] = None) -> Optional[Path]:
    """oemer 批处理入口，接口与 ``run_audiveris_batch`` 对齐。

    - 若输入为 PDF，自动将首页转换为 PNG 再送入 oemer。
    - 返回包含 MusicXML 的目录（与 Audiveris 接口对齐），失败返回 None。

    注意：oemer 目前每次只处理单张图片；多页 PDF 仅处理第 1 页。
    后续版本可扩展为逐页处理并合并结果。
    """
    input_path = input_path.resolve()
    if output_dir is None:
        output_dir = get_app_base_dir() / 'oemer-output'
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # oemer 仅接受图片：若为 PDF，先转换首页
    if input_path.suffix.lower() == '.pdf':
        log_message('[oemer] 检测到 PDF 输入，正在将首页转换为图片…')
        img_path = _pdf_first_page_to_png(input_path, output_dir)
        if img_path is None:
            return None
    else:
        img_path = input_path

    mxl_file = run_oemer(img_path, output_dir)
    if mxl_file is None:
        return None

    # 返回包含 MusicXML 的目录（与 run_audiveris_batch 返回值对齐）
    return mxl_file.parent
