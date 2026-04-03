# core/image_preprocess.py — 图像预处理（OMR 质量提升）
# 拆分自 convert.py
# 基于 Audiveris 官方文档 https://audiveris.github.io/audiveris/_pages/guides/advanced/improved_input/
# 流程：waifu2x GPU 超分辨率（低分辨率图像）+ Pillow 去噪/锐化/亮度对比度增强
import logging
import math
import subprocess
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageFilter, ImageOps, ImageStat
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

from .config import (
    LOGGER,
    RUNTIME_ASSETS_DIR_NAME,
    WAIFU2X_RUNTIME_DIR_NAME,
)
from .utils import (
    find_packaged_runtime_dir,
    get_runtime_search_roots,
    log_message,
    safe_remove_file,
)

# 图像最小边像素阈值：低于此值的图像使用 waifu2x 超分辨率放大
LOW_RES_PIXEL_THRESHOLD = 1200
# Audiveris 允许处理的最大像素总数（超出则报 Too large image 并拒绝识别）
AUDIVERIS_MAX_PIXELS = 20_000_000
# Laplacian stddev on 500×500 thumbnail below this → image is blurry, use aggressive sharpening
BLURRY_SHARPNESS_THRESHOLD = 30.0


def find_waifu2x_executable() -> Optional[Path]:
    """Search for waifu2x-ncnn-vulkan in app directory, common install paths, and PATH.
    waifu2x-ncnn-vulkan uses Vulkan GPU acceleration by default."""
    import os
    import shutil

    exe_names = ['waifu2x-ncnn-vulkan.exe', 'waifu2x-ncnn-vulkan']
    candidates: list[Path] = []

    for base_dir in get_runtime_search_roots():
        for exe_name in exe_names:
            candidates.extend([
                base_dir / exe_name,
                base_dir / WAIFU2X_RUNTIME_DIR_NAME / exe_name,
                base_dir / RUNTIME_ASSETS_DIR_NAME / WAIFU2X_RUNTIME_DIR_NAME / exe_name,
                base_dir / 'waifu2x-ncnn-vulkan' / exe_name,
                base_dir / 'tools' / exe_name,
            ])

    packaged_waifu2x_dir = find_packaged_runtime_dir(WAIFU2X_RUNTIME_DIR_NAME)
    if packaged_waifu2x_dir is not None:
        for exe_name in exe_names:
            candidates.append(packaged_waifu2x_dir / exe_name)

    program_files = os.environ.get('PROGRAMFILES', r'C:\Program Files')
    local_app_data = os.environ.get('LOCALAPPDATA', '')
    for root in [Path(program_files), Path(local_app_data) / 'Programs']:
        for exe_name in exe_names:
            candidates.append(root / 'waifu2x-ncnn-vulkan' / exe_name)

    for exe_name in exe_names:
        found = shutil.which(exe_name)
        if found:
            candidates.append(Path(found))

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def is_low_resolution_image(image_path: Path) -> bool:
    """Return True if either image dimension is below LOW_RES_PIXEL_THRESHOLD."""
    if not HAS_PILLOW:
        return False
    try:
        with Image.open(image_path) as img:
            return min(img.size) < LOW_RES_PIXEL_THRESHOLD
    except Exception:
        return False


def upscale_image_with_waifu2x(input_path: Path, output_path: Path, scale: int = 2) -> bool:
    """Upscale an image using waifu2x-ncnn-vulkan (GPU via Vulkan by default, scale 2x or 4x).
    Falls back gracefully if waifu2x-ncnn-vulkan is not installed."""
    waifu2x = find_waifu2x_executable()
    if waifu2x is None:
        log_message(
            '未找到 waifu2x-ncnn-vulkan，跳过超分辨率放大。'
            '（可从 https://github.com/nihui/waifu2x-ncnn-vulkan 下载）',
            logging.WARNING,
        )
        return False
    cmd = [str(waifu2x), '-i', str(input_path), '-o', str(output_path), '-s', str(scale), '-n', '1']
    log_message(f'使用 waifu2x-ncnn-vulkan 进行 {scale}x GPU 超分辨率处理...')
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            log_message(f'waifu2x 超分辨率完成: {output_path.name}')
            return True
        detail = (result.stderr or result.stdout or '').strip()[:200]
        log_message(f'waifu2x 处理失败（退出码 {result.returncode}）: {detail}', logging.WARNING)
        return False
    except (OSError, subprocess.SubprocessError) as exc:
        log_message(f'waifu2x 调用异常: {exc}', logging.WARNING)
        return False


def _measure_laplacian_stddev(img: 'Image.Image') -> float:
    """Return the edge-response stddev of a 500×500 thumbnail — proxy for image sharpness.
    Uses Pillow's built-in FIND_EDGES filter (3×3 Laplacian kernel).
    Lower value means blurrier image.  Returns 100.0 if measurement fails (assume sharp)."""
    try:
        thumb = img.convert('L').resize((500, 500), Image.LANCZOS)
        edges = thumb.filter(ImageFilter.FIND_EDGES)
        return ImageStat.Stat(edges).stddev[0]
    except Exception:
        return 100.0


def enhance_image_with_pillow(input_path: Path, output_path: Path) -> bool:
    """Apply adaptive image enhancements to improve OMR accuracy using Pillow.
    Pipeline follows the official Audiveris/GIMP guide:
      1. Convert to grayscale  — Audiveris prefers grayscale input for its adaptive binarizer.
      2. Sharpness detection   — selects normal vs. blurry enhancement mode.
      - Normal mode  (stddev >= BLURRY_SHARPNESS_THRESHOLD):
          Gaussian blur (1.5) → autocontrast 10% cutoff → Unsharp mask (1.0, 150).
      - Blurry mode  (stddev < threshold):
          autocontrast 10% cutoff → strong Unsharp mask (radius=3.0, percent=300).
    Always saves to output_path as a lossless PNG (grayscale).
    Returns True on success."""
    if not HAS_PILLOW:
        return False
    try:
        with Image.open(input_path) as img:
            # Step 1: Convert to grayscale — matches Audiveris scanning guide recommendation
            gray = img.convert('L')
            sharpness = _measure_laplacian_stddev(gray)
            if sharpness < BLURRY_SHARPNESS_THRESHOLD:
                log_message(
                    f'检测到模糊图像（锐度指数={sharpness:.1f}），使用强化锐化模式进行增强。')
                # Blurry mode: autocontrast first to recover range, then strong sharpening
                leveled = ImageOps.autocontrast(gray, cutoff=10)
                result_img = leveled.filter(
                    ImageFilter.UnsharpMask(radius=3.0, percent=300, threshold=2))
            else:
                # Normal mode: Gaussian blur → autocontrast (GIMP color curve step) → Unsharp mask
                denoised = gray.filter(ImageFilter.GaussianBlur(radius=1.5))
                leveled = ImageOps.autocontrast(denoised, cutoff=10)
                result_img = leveled.filter(
                    ImageFilter.UnsharpMask(radius=1.0, percent=150, threshold=3))
            result_img.save(output_path)
        return True
    except Exception as exc:
        log_message(f'Pillow 图像增强失败: {exc}', logging.WARNING)
        return False


def fit_image_within_pixel_limit(image_path: Path, work_dir: Path, max_pixels: int = AUDIVERIS_MAX_PIXELS) -> Optional[Path]:
    """If the image has more pixels than max_pixels, proportionally downscale it and save
    to a new file in work_dir.  Returns the downscaled path, or None if no resize was needed
    (or Pillow is unavailable)."""
    if not HAS_PILLOW:
        return None
    try:
        with Image.open(image_path) as img:
            w, h = img.size
            if w * h <= max_pixels:
                return None
            scale = math.sqrt(max_pixels / (w * h))
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            log_message(
                f'图像尺寸 {w}×{h} ({w * h:,} px) 超出 Audiveris 上限 {max_pixels:,} px，'
                f'自动缩小至 {new_w}×{new_h} ({new_w * new_h:,} px)。'
            )
            resized = img.resize((new_w, new_h), Image.LANCZOS)
            out_path = work_dir / f'resized_{image_path.stem}.png'
            resized.save(out_path)
            return out_path
    except Exception as exc:
        log_message(f'图像降采样失败: {exc}', logging.WARNING)
        return None


def preprocess_image_for_omr(image_path: Path, work_dir: Path) -> Optional[Path]:
    """Pre-process a raster image (PNG/JPG) to improve Audiveris OMR accuracy.
    Based on the Audiveris improved-input guide:
      Step 1 (low-res only): upscale 2x with waifu2x-ncnn-vulkan (Vulkan GPU, default).
      Step 2: denoise + sharpen + brightness/contrast enhancement via Pillow.
      Step 3: downscale to Audiveris pixel limit (20 M px) if still oversized.
    Returns the preprocessed image path on success, or None if preprocessing was not applied."""
    if not HAS_PILLOW:
        return None
    if image_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    current_path = image_path
    intermediate: Optional[Path] = None

    # Step 1: super-resolution upscaling for low-resolution images (GPU accelerated)
    if is_low_resolution_image(current_path):
        upscaled_path = work_dir / f'upscaled_{image_path.name}'
        if upscale_image_with_waifu2x(current_path, upscaled_path, scale=2):
            current_path = upscaled_path
            intermediate = upscaled_path
        else:
            log_message('超分辨率放大失败，将直接进行图像增强。', logging.WARNING)

    # Step 2: Pillow noise-reduction + sharpening + brightness/contrast (lossless PNG output)
    enhanced_path = work_dir / f'enhanced_{image_path.stem}.png'
    if enhance_image_with_pillow(current_path, enhanced_path):
        if intermediate is not None:
            safe_remove_file(intermediate)
        current_path = enhanced_path
        intermediate = None
    elif intermediate is not None:
        # Enhancement failed but upscaling succeeded; keep the upscaled copy
        current_path = intermediate
        intermediate = None
    else:
        return None

    # Step 3: enforce Audiveris pixel limit — downscale if still oversized
    rescaled = fit_image_within_pixel_limit(current_path, work_dir)
    if rescaled is not None:
        safe_remove_file(current_path)
        current_path = rescaled

    log_message('图像预处理完成，将使用增强图像进行 OMR 识别。')
    return current_path
