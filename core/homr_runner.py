# core/homr_runner.py — homr OMR 引擎封装
# 本模块将本地 homr 仓库目录作为可选 OMR 引擎，支持图片输入。

import shutil
import sys
from pathlib import Path
from typing import Optional

from .config import HOMR_SOURCE_DIR_NAME, OMR_ENGINE_DIR_NAME
from .oemer_runner import _pdf_first_page_to_png
from .utils import find_first_musicxml_file, get_app_base_dir, log_message


def find_homr_source_dir() -> Optional[Path]:
    """Find the local homr source directory under the app root or omr_engine."""
    base_dir = get_app_base_dir()
    candidates = [
        base_dir / HOMR_SOURCE_DIR_NAME,
        base_dir / OMR_ENGINE_DIR_NAME / HOMR_SOURCE_DIR_NAME,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _homr_api_available() -> bool:
    """Check whether homr can be imported from the local repository or installed package."""
    try:
        import homr.main  # noqa: F401
        return True
    except Exception:
        source_dir = find_homr_source_dir()
        if source_dir is None:
            return False
        if str(source_dir) not in sys.path:
            sys.path.insert(0, str(source_dir))
            added = True
        else:
            added = False
        try:
            import homr.main  # noqa: F401
            return True
        except Exception:
            return False
        finally:
            if added and str(source_dir) in sys.path:
                sys.path.remove(str(source_dir))


def check_homr_available() -> bool:
    """Return True when homr can be imported or found locally."""
    if _homr_api_available():
        return True
    log_message(
        'homr 引擎不可用。请确认已将 homr 仓库 clone 到 omr_engine/homr，或安装可用的 homr Python 包。',
    )
    return False


def _ensure_homr_import_path() -> Optional[Path]:
    source_dir = find_homr_source_dir()
    if source_dir is None:
        return None
    if str(source_dir) not in sys.path:
        sys.path.insert(0, str(source_dir))
        return source_dir
    return None


def run_homr_batch(
    source_image: Path,
    output_dir: Path,
    use_gpu_inference: bool = False,
) -> Optional[Path]:
    """Run homr on a single image file or a PDF's first page, returning the output directory."""
    if source_image.suffix.lower() == '.pdf':
        image_path = _pdf_first_page_to_png(source_image, output_dir)
        if image_path is None:
            return None
    else:
        image_path = source_image

    if image_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        log_message(f'[homr] 仅支持图片输入，跳过: {source_image.name}', )
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    if source_image.suffix.lower() != '.pdf':
        image_path = output_dir / source_image.name
        if not image_path.exists() or image_path.stat().st_size != source_image.stat().st_size:
            shutil.copy2(str(source_image), str(image_path))

    source_dir = _ensure_homr_import_path()
    try:
        import homr.main as homr_main
        homr_main.download_weights(use_gpu_inference)
        config = homr_main.ProcessingConfig(
            enable_debug=False,
            enable_cache=False,
            write_staff_positions=False,
            read_staff_positions=False,
            selected_staff=-1,
            use_gpu_inference=use_gpu_inference,
        )
        xml_args = homr_main.XmlGeneratorArguments(False, None, None)
        homr_main.process_image(str(image_path), config, xml_args)
    except Exception as exc:
        log_message(f'[homr] 识别失败: {exc}', )
        return None
    finally:
        if source_dir is not None and str(source_dir) in sys.path:
            sys.path.remove(str(source_dir))

    mxl = find_first_musicxml_file(output_dir, image_path.stem)
    if mxl is None:
        log_message(f'[homr] 未找到输出 MusicXML，可能识别失败。', )
        return None
    return output_dir
