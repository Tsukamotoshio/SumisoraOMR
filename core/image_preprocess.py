# core/image_preprocess.py — 图像预处理与综合增强管道（OMR 专用）
# 拆分自 convert.py；image_enhance.py 已整合至本模块
# 基于 Audiveris 官方文档 https://audiveris.github.io/audiveris/_pages/guides/advanced/improved_input/
# 流程：waifu2x GPU 超分辨率（低分辨率图像）+ Pillow 去噪/锐化/亮度对比度增强
#       + 完整六步增强管道：白边裁剪→旋转校正→梯度修正→去噪锐化→SR→降采样
from __future__ import annotations

import logging
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageFilter, ImageOps, ImageStat
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    import numpy as _numpy_sentinel  # noqa: F401 — availability check only
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

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

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

# 图像最小边像素阈值：低于此值的图像使用 waifu2x 超分辨率放大
LOW_RES_PIXEL_THRESHOLD = 1200
# Audiveris 允许处理的最大像素总数（超出则报 Too large image 并拒绝识别）
AUDIVERIS_MAX_PIXELS = 20_000_000
# oemer 深度学习模型建议的最大像素总数（超出则降采样以节省 GPU 内存）
# 注意：调低此阈值可以显著减少 GPU 显存占用，降低 DirectML 驱动过载风险；
#       8 MP 约等于 2828×2828 px，已足够 300 DPI A4 谱面的 OMR 识别精度。
OEMER_MAX_PIXELS = 8_000_000
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


def autocrop_image(img: 'Image.Image', padding: int = 30) -> 'Image.Image':
    """Crop away content-free borders from a sheet-music image.

    Two detection modes are selected automatically:

    **Scan mode** (image mean > 180 — mostly white background like a flatbed scan):
        Finds the axis-aligned bounding box of dark content pixels (< 240) and
        crops to it.

    **Photo mode** (image mean ≤ 180 — photo of a page on a dark / patterned surface):
        Finds rows and columns whose *average brightness* exceeds the overall
        image mean.  These are "bright" rows / columns that belong to the white
        paper page, rather than the dark background material behind it.
        This is robust to complex backgrounds (fabric, desk, etc.) that would
        fool a simple per-pixel threshold.

    In both modes the crop is skipped when the result would shrink the image
    by less than 5  % on either axis (negligible margins).
    """
    if not HAS_NUMPY:
        return img
    try:
        import numpy as np
    except ImportError:
        return img

    gray = np.array(img.convert('L') if img.mode != 'L' else img, dtype=np.float32)
    h, w = gray.shape
    avg_brightness = float(gray.mean())

    if avg_brightness > 180:
        # ── Scan mode ────────────────────────────────────────────────────
        mask = gray.astype(np.uint8) < 240
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        if not rows.any() or not cols.any():
            return img
        rmin = int(np.where(rows)[0][0])
        rmax = int(np.where(rows)[0][-1])
        cmin = int(np.where(cols)[0][0])
        cmax = int(np.where(cols)[0][-1])
    else:
        # ── Photo mode ──────────────────────────────────────────────────
        # Rows / columns that mostly contain white paper have HIGH average
        # brightness; rows of dark background material have LOW average
        # brightness.  Using the overall image mean as the threshold naturally
        # splits the two groups for a wide range of ambient conditions.
        threshold = avg_brightness
        row_means = gray.mean(axis=1)
        col_means = gray.mean(axis=0)
        paper_rows = np.where(row_means > threshold)[0]
        paper_cols = np.where(col_means > threshold)[0]
        if not len(paper_rows) or not len(paper_cols):
            return img
        rmin = int(paper_rows[0])
        rmax = int(paper_rows[-1])
        cmin = int(paper_cols[0])
        cmax = int(paper_cols[-1])

    rmin = max(0, rmin - padding)
    rmax = min(h - 1, rmax + padding)
    cmin = max(0, cmin - padding)
    cmax = min(w - 1, cmax + padding)

    new_w = cmax - cmin + 1
    new_h = rmax - rmin + 1
    if new_w >= w * 0.95 and new_h >= h * 0.95:
        return img  # margins are negligible — skip crop

    log_message(f'自动裁剪：{w}×{h} → {new_w}×{new_h}')
    return img.crop((cmin, rmin, cmax + 1, rmax + 1))


def deskew_image(img: 'Image.Image', max_angle: float = 7.0, step: float = 0.5) -> 'Image.Image':
    """Detect and correct page rotation (skew) using the projection-profile method.

    Scans angles in [-*max_angle*, +*max_angle*] at *step*-degree increments.
    For each angle the binary analysis thumbnail is rotated and its row-sum
    (horizontal projection) is computed.  The angle whose projection variance is
    highest corresponds to the configuration where staff lines are most sharply
    aligned with the horizontal axis — i.e., the image is level.

    The correction is then applied to *img* at full resolution (bicubic resampling,
    expanded canvas, white fill for newly-exposed corners).

    Returns *img* unchanged when:
    - numpy is unavailable
    - the detected correction angle is < 0.2° (no meaningful skew)
    """
    if not HAS_NUMPY:
        return img
    try:
        import numpy as np
    except ImportError:
        return img

    # Downscale to ≤ 800 px wide for fast analysis (preserves angle resolution)
    gray = img.convert('L') if img.mode != 'L' else img
    w, h = gray.size
    thumb = gray.resize((800, int(h * 800 / w)), Image.LANCZOS) if w > 800 else gray

    # Binarise: dark ink (content) → 255, white background → 0
    arr = np.array(thumb, dtype=np.float32)
    threshold = float(arr.mean())
    binary_pil = Image.fromarray(((arr < threshold) * 255).astype(np.uint8))

    best_angle = 0.0
    best_score = -1.0
    for angle in np.arange(-max_angle, max_angle + step / 2.0, step):
        rotated = binary_pil.rotate(float(angle), expand=False, fillcolor=0)
        score = float(np.array(rotated, dtype=np.float32).sum(axis=1).var())
        if score > best_score:
            best_score = score
            best_angle = float(angle)

    if abs(best_angle) < 0.2:
        return img  # skew is negligible

    log_message(f'梯度修正：检测到倒斜 {best_angle:+.1f}°，正在修正...')
    fill = 255 if img.mode in ('L', 'RGB', 'RGBA') else 0
    return img.rotate(
        best_angle,
        expand=True,
        fillcolor=fill,
        resample=Image.BICUBIC,
    )


def enhance_image_with_pillow(input_path: Path, output_path: Path, keep_color: bool = False) -> bool:
    """Apply adaptive image enhancements to improve OMR accuracy using Pillow.
    Pipeline follows the official Audiveris/GIMP guide:
      1. Color mode selection:
         - keep_color=False (Audiveris): Convert to grayscale — prefers grayscale for its binarizer.
         - keep_color=True  (Oemer):    Keep RGB — deep-learning model benefits from color info.
      2. Sharpness detection   — selects normal vs. blurry enhancement mode.

      - Normal mode  (stddev >= BLURRY_SHARPNESS_THRESHOLD):
          Gaussian blur (1.5) → autocontrast 10% cutoff → Unsharp mask (1.0, 150).
      - Blurry mode  (stddev < threshold):
          autocontrast 10% cutoff → strong Unsharp mask (radius=3.0, percent=300).
    Always saves to output_path as a lossless PNG.
    Returns True on success."""
    if not HAS_PILLOW:
        return False
    try:
        with Image.open(input_path) as img:
            if keep_color:
                # Keep color channels for deep-learning OMR engines (e.g. oemer)
                working = img.convert('RGB')
            else:
                # Convert to grayscale — matches Audiveris scanning guide recommendation
                working = img.convert('L')
                working = autocrop_image(working)
                working = deskew_image(working)
            # Sharpness measurement always uses grayscale luminance
            gray_for_measure = working.convert('L') if keep_color else working
            sharpness = _measure_laplacian_stddev(gray_for_measure)

            if sharpness < BLURRY_SHARPNESS_THRESHOLD:
                log_message(
                    f'检测到模糊图像（锐度指数={sharpness:.1f}），使用强化锐化模式进行增强。')
                # Blurry mode: autocontrast first to recover range, then strong sharpening
                leveled = ImageOps.autocontrast(working, cutoff=10)
                result_img = leveled.filter(
                    ImageFilter.UnsharpMask(radius=3.0, percent=300, threshold=2))
            else:
                # Normal mode: Gaussian blur → autocontrast (GIMP color curve step) → Unsharp mask
                denoised = working.filter(ImageFilter.GaussianBlur(radius=1.5))
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


def preprocess_image_for_omr(
    image_path: Path,
    work_dir: Path,
    keep_color: bool = False,
    max_pixels: int = AUDIVERIS_MAX_PIXELS,
) -> Optional[Path]:
    """Pre-process a raster image (PNG/JPG) to improve OMR accuracy.
    Based on the Audiveris improved-input guide (applied to both Audiveris and Oemer):
      Step 1 (low-res only): upscale 2x with waifu2x-ncnn-vulkan (Vulkan GPU, default).
      Step 2: denoise + sharpen + brightness/contrast enhancement via Pillow.
              keep_color=True keeps RGB for deep-learning engines (e.g. oemer),
              keep_color=False converts to grayscale for Audiveris binarizer.
      Step 3: downscale to max_pixels limit if still oversized.
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
    if enhance_image_with_pillow(current_path, enhanced_path, keep_color=keep_color):
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

    # Step 3: enforce engine pixel limit — downscale if still oversized
    rescaled = fit_image_within_pixel_limit(current_path, work_dir, max_pixels=max_pixels)
    if rescaled is not None:
        safe_remove_file(current_path)
        current_path = rescaled

    log_message('图像预处理完成，将使用增强图像进行 OMR 识别。')
    return current_path


def preprocess_image_for_oemer(
    image_path: Path,
    work_dir: Path,
    max_pixels: int = OEMER_MAX_PIXELS,
) -> Optional[Path]:
    """oemer 专用轻量预处理：自动剪裁白边 + 梯度（亮度）修正。

    基于 oemer 官方 README 建议（减少人工增强，避免引入影响深度学习模型的伪影）：
      步骤 1 — 自动剪裁：去除四边空白/白色边距，保留乐谱内容区域。
      步骤 2 — 梯度修正：使用 autocontrast（2% 截断）校正不均匀光照，
                           等同于对扫描件的亮度梯度进行修正。
      步骤 3 — 像素上限：超出 OEMER_MAX_PIXELS 时等比例缩小，节省 GPU 显存。

    保留 RGB 彩色通道（oemer 深度学习模型利用颜色信息）。
    成功时返回预处理后的图片路径；失败或不适用时返回 None。
    """
    if not HAS_PILLOW:
        return None
    if image_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(image_path) as img:
            working = img.convert('RGB')

            # 步骤 1：自动剪裁——找到非白色内容的边界框，去除外侧空白边距
            # 将灰度图中亮度 ≥ 240 的像素视为背景（白色），反转后获取内容 bbox
            gray = working.convert('L')
            # 二值化：内容像素（暗）→ 255（白），背景（亮）→ 0（黑），再取 bbox
            content_mask = gray.point(lambda px: 0 if px >= 240 else 255, '1')
            bbox = content_mask.getbbox()
            if bbox is not None:
                w, h = working.size
                # 在内容边界外各加约 1% 的安全边距，避免裁切到边缘符号
                margin_x = max(4, int(w * 0.01))
                margin_y = max(4, int(h * 0.01))
                x0 = max(0, bbox[0] - margin_x)
                y0 = max(0, bbox[1] - margin_y)
                x1 = min(w, bbox[2] + margin_x)
                y1 = min(h, bbox[3] + margin_y)
                working = working.crop((x0, y0, x1, y1))

            # 步骤 2：梯度修正——autocontrast（2% 截断）校正光照不均
            working = ImageOps.autocontrast(working, cutoff=2)

            enhanced_path = work_dir / f'enhanced_{image_path.stem}.png'
            working.save(enhanced_path)
    except Exception as exc:
        log_message(f'oemer 图像预处理失败: {exc}', logging.WARNING)
        return None

    current_path = enhanced_path

    # 步骤 3：像素上限——超出则等比例缩小
    rescaled = fit_image_within_pixel_limit(current_path, work_dir, max_pixels=max_pixels)
    if rescaled is not None:
        safe_remove_file(current_path)
        current_path = rescaled

    log_message('图像预处理完成（自动剪裁 + 梯度修正），将使用增强图像进行 OMR 识别。')
    return current_path


# ══════════════════════════════════════════════════════════════════════════════
# 综合增强管道（原 image_enhance.py）
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EnhanceResult:
    """图像增强管道的输出记录。"""

    enhanced_path: Path
    """最终增强图像路径（已就绪，可直接传入 OMR 引擎）。"""

    blur_score: float = 0.0
    """增强后 Laplacian 边缘响应标准差（越高越清晰，< 15 为严重模糊）。"""

    tilt_angle: float = 0.0
    """检测到的原始倾斜角度（°），正值为顺时针偏转。"""

    border_ratio: float = 0.0
    """裁剪掉的白边占原图像素的比例（0–1）。"""

    sr_applied: bool = False
    """是否执行了 waifu2x 超分辨率放大。"""

    downscaled: bool = False
    """是否执行了像素上限降采样。"""

    steps_applied: list[str] = field(default_factory=list)
    """实际执行的增强步骤列表（用于日志和调试）。"""


def crop_white_border(img: 'Image.Image') -> tuple['Image.Image', float]:
    """移除图像四边的白色空白边距，返回 (裁剪后图像, 白边像素占比)。

    检测逻辑：将亮度 >= 240 的像素视为白色背景，反转后取内容边界框。
    在内容边界外各加约 1% 的安全边距，防止裁掉边缘符号。
    """
    try:
        gray = img.convert('L')
        content_mask = gray.point(lambda px: 0 if px >= 240 else 255, '1')
        bbox = content_mask.getbbox()
        if bbox is None:
            return img, 1.0  # 全白图，跳过裁剪
        w, h = img.size
        x0, y0, x1, y1 = bbox
        content_area = (x1 - x0) * (y1 - y0)
        border_ratio = max(0.0, 1.0 - content_area / (w * h))
        margin_x = max(4, int(w * 0.01))
        margin_y = max(4, int(h * 0.01))
        cx0 = max(0, x0 - margin_x)
        cy0 = max(0, y0 - margin_y)
        cx1 = min(w, x1 + margin_x)
        cy1 = min(h, y1 + margin_y)
        return img.crop((cx0, cy0, cx1, cy1)), border_ratio
    except Exception:
        return img, 0.0


def detect_and_correct_rotation(img: 'Image.Image') -> tuple['Image.Image', float]:
    """通过投影方差最大化检测倾斜角度并旋转纠偏。

    在 300×300 缩略图上搜索 ±10° 范围（步长 0.5°），选取使行方向投影方差
    最大的角度（对应水平线条最清晰）。仅当 numpy 可用时执行；否则返回原图。

    Returns
    -------
    (旋转后图像, 检测到的倾斜角度°)
    """
    if not _HAS_NUMPY:
        return img, 0.0
    try:
        thumb = img.convert('L').resize((300, 300), Image.LANCZOS)
        arr = np.array(thumb, dtype=np.uint8)
        best_angle = 0.0
        best_variance = -1.0
        for step in range(-20, 21):          # ±10°，步长 0.5°
            angle = step * 0.5
            rotated = Image.fromarray(arr).rotate(angle, expand=False, fillcolor=255)
            rows = np.array(rotated, dtype=np.float32).sum(axis=1)
            var = float(np.var(rows))
            if var > best_variance:
                best_variance = var
                best_angle = angle
        if abs(best_angle) < 0.5:
            return img, best_angle
        corrected = img.rotate(-best_angle, expand=False, fillcolor=255)
        return corrected, best_angle
    except Exception:
        return img, 0.0


def correct_gradient(img: 'Image.Image') -> 'Image.Image':
    """使用 autocontrast（2% 截断）校正扫描件的光照梯度不均匀。"""
    try:
        return ImageOps.autocontrast(img, cutoff=2)
    except Exception:
        return img


def denoise_and_sharpen(img: 'Image.Image') -> 'Image.Image':
    """自适应去噪 + 锐化，输出灰度图（OMR 引擎通常偏好灰度/二值化输入）。

    清晰图像（Laplacian stddev >= BLURRY_SHARPNESS_THRESHOLD）：
        GaussianBlur(1.5) 温和去噪 → autocontrast → UnsharpMask(r=1, p=150)
    模糊图像（stddev < 阈值）：
        autocontrast 恢复动态范围 → 强力 UnsharpMask(r=3, p=300)
    """
    try:
        gray = img.convert('L')
        sharpness = _measure_laplacian_stddev(gray)
        if sharpness < BLURRY_SHARPNESS_THRESHOLD:
            leveled = ImageOps.autocontrast(gray, cutoff=10)
            result = leveled.filter(
                ImageFilter.UnsharpMask(radius=3.0, percent=300, threshold=2))
        else:
            denoised = gray.filter(ImageFilter.GaussianBlur(radius=1.5))
            leveled = ImageOps.autocontrast(denoised, cutoff=10)
            result = leveled.filter(
                ImageFilter.UnsharpMask(radius=1.0, percent=150, threshold=3))
        return result
    except Exception:
        return img.convert('L')


def enhance_image(
    image_path: Path,
    work_dir: Path,
    max_pixels: int = AUDIVERIS_MAX_PIXELS,
) -> Optional[EnhanceResult]:
    """对乐谱图像执行完整的六步增强管道。

    步骤顺序
    --------
    白边裁剪 → 旋转校正 → 梯度修正 → 去噪锐化 →
    超分辨率（低分辨率时）→ 像素上限降采样（过大时）

    Parameters
    ----------
    image_path : 原始输入图像（PNG / JPG / JPEG）。
    work_dir   : 中间文件输出目录（函数自动创建）。
    max_pixels : 输出图像像素上限（默认 20 M px，即 Audiveris 上限）。

    Returns
    -------
    EnhanceResult  成功时包含增强后图像路径及处理元数据。
    None           如果 Pillow 不可用或输入格式不支持。
    """
    if not HAS_PILLOW:
        log_message('Pillow 未安装，跳过图像增强管道。', logging.WARNING)
        return None
    if image_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    meta = EnhanceResult(enhanced_path=image_path)
    steps: list[str] = []

    try:
        with Image.open(image_path) as img:
            working = img.convert('RGB')

        # ── 步骤 1：自动白边裁剪 ──────────────────────────────────
        working, border_ratio = crop_white_border(working)
        meta.border_ratio = border_ratio
        if border_ratio > 0.02:
            steps.append('白边裁剪')
            log_message(f'  [增强] 自动白边裁剪，移除 {border_ratio:.1%} 白边。')

        # ── 步骤 2：旋转校正 ─────────────────────────────────────
        working, angle = detect_and_correct_rotation(working)
        meta.tilt_angle = angle
        if abs(angle) >= 0.5:
            steps.append(f'旋转校正 {angle:+.1f}°')
            log_message(f'  [增强] 旋转校正：检测到倾斜 {angle:+.1f}°，已纠偏。')

        # ── 步骤 3：梯度 / 光照修正 ──────────────────────────────
        working = correct_gradient(working)
        steps.append('梯度修正')

        # ── 步骤 4：去噪 + 锐化（转灰度输出）────────────────────
        working = denoise_and_sharpen(working)
        steps.append('去噪锐化')

        # 保存中间结果以供后续文件操作（超分 / 下采样需要路径）
        intermediate_path = work_dir / f'enhanced_stage_{image_path.stem}.png'
        working.save(intermediate_path)
        current_path = intermediate_path

        # ── 步骤 5：超分辨率放大（低分辨率时）───────────────────
        with Image.open(current_path) as chk:
            min_dim = min(chk.size)
        if min_dim < LOW_RES_PIXEL_THRESHOLD:
            upscaled_path = work_dir / f'sr_{image_path.stem}.png'
            ok = upscale_image_with_waifu2x(current_path, upscaled_path, scale=2)
            if ok:
                safe_remove_file(current_path)
                current_path = upscaled_path
                meta.sr_applied = True
                steps.append('waifu2x 超分辨率')
                log_message(
                    f'  [增强] 最短边 {min_dim}px < {LOW_RES_PIXEL_THRESHOLD}px，'
                    '已执行 2× 超分辨率放大。'
                )
            else:
                log_message('  [增强] 超分辨率放大失败，使用现有分辨率继续。', logging.WARNING)

        # ── 步骤 6：像素上限降采样 ────────────────────────────────
        rescaled = fit_image_within_pixel_limit(current_path, work_dir, max_pixels=max_pixels)
        if rescaled is not None:
            safe_remove_file(current_path)
            current_path = rescaled
            meta.downscaled = True
            steps.append('降采样')

        # ── 测量最终锐度 ──────────────────────────────────────────
        with Image.open(current_path) as final_img:
            meta.blur_score = _measure_laplacian_stddev(final_img)

        # ── 重命名为规范输出文件名 ─────────────────────────────────
        final_path = work_dir / f'omr_ready_{image_path.stem}.png'
        if current_path != final_path:
            current_path.rename(final_path)
        meta.enhanced_path = final_path
        meta.steps_applied = steps

        log_message(
            f'  [增强] 预处理完成 | 步骤：{" → ".join(steps) if steps else "（无需调整）"}'
            f' | 最终锐度 {meta.blur_score:.1f}'
        )
        return meta

    except Exception as exc:
        log_message(f'图像增强管道失败: {exc}', logging.WARNING)
        return None


def create_display_reference(
    image_path: Path,
    work_dir: Path,
) -> Optional[Path]:
    """创建供人工校对使用的可读参考图像（编辑器工作区专用）。

    与 OMR 专用的 enhance_image（输出灰度、经降噪模糊处理）不同，
    本函数仅对图像做轻量化的视觉优化，保留 RGB 彩色，确保人眼可清晰辨认谱面：
      1. 白边裁剪 —— 去除多余空白，使谱面内容更突出。
      2. 旋转校正 —— 纠正拍摄/扫描时的轻微倾斜（需 numpy）。
      3. 轻度对比度增强 —— autocontrast 2% 截断，改善光照不均。
    不应用 GaussianBlur 或灰度转换，避免图像模糊而影响人工阅读。

    Parameters
    ----------
    image_path : 原始输入图像（PNG / JPG / JPEG）。
    work_dir   : 输出目录（自动创建）。

    Returns
    -------
    参考图像路径（PNG），失败时返回 None。
    """
    if not HAS_PILLOW:
        return None
    if image_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(image_path) as img:
            working = img.convert('RGB')

        # Step 1: 白边裁剪
        working, border_ratio = crop_white_border(working)
        if border_ratio > 0.02:
            log_message(f'  [显示参考] 白边裁剪，移除 {border_ratio:.1%} 白边。')

        # Step 2: 旋转校正
        working, angle = detect_and_correct_rotation(working)
        if abs(angle) >= 0.5:
            log_message(f'  [显示参考] 旋转校正 {angle:+.1f}°。')

        # Step 3: 轻度对比度增强（保留 RGB）
        working = ImageOps.autocontrast(working, cutoff=2)

        ref_path = work_dir / f'display_ref_{image_path.stem}.png'
        working.save(ref_path)
        log_message(f'  [显示参考] 已生成编辑器参考图像（白边裁剪 + 旋转校正 + 对比度增强）。')
        return ref_path

    except Exception as exc:
        log_message(f'创建显示参考图像失败: {exc}', logging.WARNING)
        return None
