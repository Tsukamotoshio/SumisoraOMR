# core/image/image_preprocess.py — Pillow-based image processing pipeline (OMR-optimised)
# SR engine discovery and upscaling have been moved to sr_upscale.py
# Based on the Audiveris official guide:
# https://audiveris.github.io/audiveris/_pages/guides/advanced/improved_input/
# Pipeline: white-border crop → rotation correction → gradient fix → denoise/sharpen → SR → downsample
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageFilter, ImageOps, ImageStat
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

from ..utils import log_message, safe_remove_file
from .sr_upscale import _current_sr_engine, upscale_image

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    import cv2 as _cv2
    _HAS_CV2 = True
except ImportError:
    _cv2 = None  # type: ignore[assignment]
    _HAS_CV2 = False


# Image thresholds and limits
LOW_RES_PIXEL_THRESHOLD = 1200
AUDIVERIS_MAX_PIXELS = 20_000_000
BLURRY_SHARPNESS_THRESHOLD = 30.0
NORMAL_MODE_GAUSSIAN_RADIUS = 0.75


def is_low_resolution_image(image_path: Path) -> bool:
    """Return True if either image dimension is below LOW_RES_PIXEL_THRESHOLD."""
    if not HAS_PILLOW:
        return False
    try:
        with Image.open(image_path) as img:
            return min(img.size) < LOW_RES_PIXEL_THRESHOLD
    except Exception:
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


def enhance_image_with_pillow(input_path: Path, output_path: Path, keep_color: bool = False) -> bool:
    """Apply adaptive image enhancements to improve OMR accuracy using Pillow.
    Pipeline follows the official Audiveris/GIMP guide:
      1. Color mode selection:
         - keep_color=False (Audiveris): Convert to grayscale — prefers grayscale for its binarizer.
         - keep_color=True  (deep-learning engines e.g. Homr): Keep RGB — model benefits from color info.
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
                working = img.convert('RGB')
            else:
                working = img.convert('L')
            gray_for_measure = working.convert('L') if keep_color else working
            sharpness = _measure_laplacian_stddev(gray_for_measure)

            if sharpness < BLURRY_SHARPNESS_THRESHOLD:
                log_message(
                    f'检测到模糊图像（锐度指数={sharpness:.1f}），使用强化锐化模式进行增强。')
                leveled = ImageOps.autocontrast(working, cutoff=10)
                result_img = leveled.filter(
                    ImageFilter.UnsharpMask(radius=3.0, percent=300, threshold=2))
            else:
                denoised = working.filter(ImageFilter.GaussianBlur(radius=NORMAL_MODE_GAUSSIAN_RADIUS))
                leveled = ImageOps.autocontrast(denoised, cutoff=10)
                result_img = leveled.filter(
                    ImageFilter.UnsharpMask(radius=1.0, percent=150, threshold=3))
            result_img.save(output_path)
        return True
    except Exception as exc:
        log_message(f'Pillow 图像增强失败: {exc}', logging.WARNING)
        return False


def fit_image_within_pixel_limit(
    image_path: Path,
    work_dir: Path,
    max_pixels: int = AUDIVERIS_MAX_PIXELS,
) -> Optional[Path]:
    """If the image exceeds max_pixels, proportionally downscale it into work_dir.
    Returns the downscaled path, or None if no resize was needed (or Pillow unavailable)."""
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
    """Full OMR preprocessing pipeline delegating to :func:`enhance_image`.

    Steps (executed by enhance_image): white-border crop → rotation correction →
    gradient fix → denoise/sharpen → SR (if low-res) → downsample (if oversized).
    """
    if not HAS_PILLOW:
        return None
    if image_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    result = enhance_image(image_path, work_dir, max_pixels=max_pixels, keep_color=keep_color)
    if result is None:
        return None
    log_message('图像预处理完成，将使用增强图像进行 OMR 识别。')
    return result.enhanced_path


def preprocess_geometry_for_omr(
    image_path: Path,
    work_dir: Path,
    max_pixels: int = AUDIVERIS_MAX_PIXELS,
) -> Optional[Path]:
    """Lightweight geometric preprocessing: gradient fix + rotation correction + white-border crop only.

    Designed for Audiveris input — Audiveris has its own high-quality internal binariser;
    aggressive denoising/sharpening/grayscale conversion degrades its recognition.

    Steps
    -----
    1. autocontrast gradient fix
    2. Rotation correction
    3. White-border crop
    4. Pixel-limit downsample (if oversized)
    """
    if not HAS_PILLOW:
        return None
    if image_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(image_path) as img:
            working = img.convert('RGB')

        working, border_ratio = crop_white_border(working)
        if border_ratio > 0.02:
            log_message(f'  [几何预处理] 白边裁剪 {border_ratio:.1%}')

        working, angle = detect_and_correct_rotation(working)
        if abs(angle) >= 0.5:
            log_message(f'  [几何预处理] 旋转校正 {angle:+.1f}°')

        working = correct_gradient(working)

        out_path = work_dir / f'geo_{image_path.stem}.png'
        working.save(out_path)
        current_path = out_path

        rescaled = fit_image_within_pixel_limit(current_path, work_dir, max_pixels=max_pixels)
        if rescaled is not None:
            safe_remove_file(current_path)
            current_path = rescaled

        return current_path
    except Exception as exc:
        log_message(f'几何预处理失败: {exc}', logging.WARNING)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Full enhancement pipeline
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EnhanceResult:
    """Output record from the image enhancement pipeline."""

    enhanced_path: Path
    blur_score: float = 0.0
    tilt_angle: float = 0.0
    border_ratio: float = 0.0
    sr_applied: bool = False
    downscaled: bool = False
    steps_applied: list[str] = field(default_factory=list)


def crop_white_border(img: 'Image.Image') -> tuple['Image.Image', float]:
    """Remove white/near-white margins from all four edges. Returns (cropped image, border pixel ratio).

    Detection logic:
    - If corner mean < 180 (dark photo background): find the bright paper region boundary.
    - Otherwise (normal scan with white background): use 92nd-percentile global threshold.
    Adds ~1% safety margin around the detected content bbox to avoid clipping edge symbols.
    """
    try:
        gray = img.convert('L')
        w, h = img.size

        if _HAS_NUMPY:
            import numpy as _np
            gray_arr = _np.array(gray)

            cy = max(1, h // 20)
            cx = max(1, w // 20)
            corner_vals = _np.concatenate([
                gray_arr[:cy, :cx].ravel(),
                gray_arr[:cy, -cx:].ravel(),
                gray_arr[-cy:, :cx].ravel(),
                gray_arr[-cy:, -cx:].ravel(),
            ])
            corner_mean = float(_np.mean(corner_vals))

            if corner_mean < 180:
                # Dark/photo background — find the bright paper region
                # Step 1: use middle 50% rows to determine column bounds
                mid_h_start = h // 4
                mid_h_end   = max(mid_h_start + 1, h - h // 4)
                col_region_init = gray_arr[mid_h_start:mid_h_end, :]
                col_means_init  = col_region_init.mean(axis=0)
                col_thresh_init = (float(col_means_init.min()) + float(col_means_init.max())) * 0.5
                cols_ok_init    = col_means_init > col_thresh_init
                cmin = int(_np.where(cols_ok_init)[0][0])  if cols_ok_init.any() else 0
                cmax = int(_np.where(cols_ok_init)[0][-1]) if cols_ok_init.any() else w - 1

                # Step 2: use bright-pixel fraction per row to find row bounds
                # Threshold 190: paper rows (note spacing whitespace) have bright_frac >= 14.9%
                #                 wood/desk background JPEG noise stays <= 7.5%
                inner_left  = cmin + (cmax - cmin) // 10
                inner_right = max(inner_left + 1, cmax - (cmax - cmin) // 10)
                row_strip   = gray_arr[:, inner_left:inner_right]
                _BRIGHT_THRESH    = 190
                _BRIGHT_FRAC_MIN  = 0.10
                row_bright  = (row_strip > _BRIGHT_THRESH).mean(axis=1)
                rows_ok     = row_bright >= _BRIGHT_FRAC_MIN

                _detected_span = (
                    int(_np.where(rows_ok)[0][-1]) - int(_np.where(rows_ok)[0][0])
                    if rows_ok.any() else 0
                )
                if not rows_ok.any() or _detected_span < h * 0.50:
                    row_means  = row_strip.mean(axis=1)
                    row_thresh = (float(row_means.min()) + float(row_means.max())) * 0.5
                    rows_ok    = row_means > row_thresh
                    _fg_max = float(row_means.max())
                    if _fg_max > corner_mean + 20:
                        _trim_thresh = corner_mean + (_fg_max - corner_mean) * 0.30
                        _rows_trimmed = rows_ok & (row_means > _trim_thresh)
                        if _rows_trimmed.sum() >= h * 0.15:
                            rows_ok = _rows_trimmed
                if not rows_ok.any():
                    return img, 0.0
                rmin = int(_np.where(rows_ok)[0][0])
                rmax = int(_np.where(rows_ok)[0][-1])
            else:
                # Normal scan: white/near-white background — percentile threshold
                p92 = float(_np.percentile(gray_arr, 92))
                bg_thresh = max(200, int(p92) - 15)
                content_mask = _np.array(gray.point(lambda px: 0 if px >= bg_thresh else 255, '1'))
                rows = content_mask.any(axis=1)
                cols = content_mask.any(axis=0)
                if not rows.any() or not cols.any():
                    return img, 1.0
                rmin, rmax = int(_np.where(rows)[0][0]),  int(_np.where(rows)[0][-1])
                cmin, cmax = int(_np.where(cols)[0][0]),  int(_np.where(cols)[0][-1])
        else:
            content_mask = gray.point(lambda px: 0 if px >= 225 else 255, '1')
            bbox = content_mask.getbbox()
            if bbox is None:
                return img, 1.0
            cmin, rmin, cmax, rmax = bbox

        content_area = (cmax - cmin) * (rmax - rmin)
        border_ratio = max(0.0, 1.0 - content_area / max(w * h, 1))
        margin_x = max(4, int(w * 0.01))
        margin_y = max(4, int(h * 0.01))
        cx0 = max(0, cmin - margin_x)
        cy0 = max(0, rmin - margin_y)
        cx1 = min(w, cmax + margin_x)
        cy1 = min(h, rmax + margin_y)
        return img.crop((cx0, cy0, cx1, cy1)), border_ratio
    except Exception:
        return img, 0.0


def detect_and_correct_rotation(img: 'Image.Image') -> tuple['Image.Image', float]:
    """Detect tilt angle via staff-line fitting and rotate to correct it.

    Primary path (cv2): Otsu binarise → morphological horizontal-line extraction
    (kernel width 20% of image) → fitLine on each contour → median of all angles.
    Only lines spanning > 15% of image width are used.

    Fallback (no cv2): projection-variance search over ±15° on an aspect-preserving
    thumbnail (long side ≤ 1200px).

    Returns (corrected image, detected tilt angle in degrees).
    """
    if not _HAS_NUMPY:
        return img, 0.0

    best_angle = 0.0

    if _HAS_CV2:
        try:
            arr = np.array(img.convert('L'), dtype=np.uint8)
            h_arr, w_arr = arr.shape
            _, binary = _cv2.threshold(arr, 0, 255, _cv2.THRESH_BINARY_INV + _cv2.THRESH_OTSU)
            kernel_len = max(3, int(w_arr * 0.20))
            h_kernel = _cv2.getStructuringElement(_cv2.MORPH_RECT, (kernel_len, 1))
            lines_img = _cv2.morphologyEx(binary, _cv2.MORPH_OPEN, h_kernel)
            contours, _ = _cv2.findContours(lines_img, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
            min_line_width = w_arr * 0.15
            angles: list[float] = []
            for cnt in contours:
                x, y, cw, ch = _cv2.boundingRect(cnt)
                if cw < min_line_width:
                    continue
                fit = _cv2.fitLine(cnt, _cv2.DIST_L2, 0, 0.01, 0.01)
                vx, vy = float(fit[0]), float(fit[1])
                angle_deg = float(np.degrees(np.arctan2(vy, vx)))
                if abs(angle_deg) <= 15:
                    angles.append(angle_deg)
            if len(angles) >= 3:
                best_angle = float(np.median(angles))
        except Exception:
            best_angle = 0.0

    if abs(best_angle) < 0.5 and _HAS_NUMPY:
        try:
            w_img, h_img = img.size
            long_side = max(w_img, h_img)
            scale = min(1200 / long_side, 1.0)
            thumb_w = max(1, int(w_img * scale))
            thumb_h = max(1, int(h_img * scale))
            thumb = img.convert('L').resize((thumb_w, thumb_h), Image.LANCZOS)
            arr_t = np.array(thumb, dtype=np.uint8)
            best_var = -1.0
            for step in range(-30, 31):
                angle = step * 0.5
                rotated = Image.fromarray(arr_t).rotate(angle, expand=False, fillcolor=255)
                rows = np.array(rotated, dtype=np.float32).sum(axis=1)
                var = float(np.var(rows))
                if var > best_var:
                    best_var = var
                    best_angle = angle
        except Exception:
            best_angle = 0.0

    if abs(best_angle) < 0.5:
        return img, best_angle
    corrected = img.rotate(-best_angle, expand=True, fillcolor=255)
    return corrected, best_angle


def correct_gradient(img: 'Image.Image') -> 'Image.Image':
    """Correct uneven scan lighting via autocontrast (2% clip)."""
    try:
        return ImageOps.autocontrast(img, cutoff=2)
    except Exception:
        return img


def denoise_and_sharpen(img: 'Image.Image', keep_color: bool = False) -> 'Image.Image':
    """Adaptive denoise + sharpen.

    keep_color=False: output grayscale (preferred by most OMR engines).
    keep_color=True : keep RGB (for deep-learning models that use colour).

    Sharp (Laplacian stddev >= BLURRY_SHARPNESS_THRESHOLD):
        GaussianBlur(1.5) → autocontrast → UnsharpMask(r=1, p=150)
    Blurry (stddev < threshold):
        autocontrast → strong UnsharpMask(r=3, p=300)
    """
    try:
        gray = img.convert('L')
        sharpness = _measure_laplacian_stddev(gray)
        target = img if keep_color else gray
        if sharpness < BLURRY_SHARPNESS_THRESHOLD:
            leveled = ImageOps.autocontrast(target, cutoff=10)
            result = leveled.filter(
                ImageFilter.UnsharpMask(radius=3.0, percent=300, threshold=2))
        else:
            denoised = target.filter(ImageFilter.GaussianBlur(radius=NORMAL_MODE_GAUSSIAN_RADIUS))
            leveled = ImageOps.autocontrast(denoised, cutoff=10)
            result = leveled.filter(
                ImageFilter.UnsharpMask(radius=1.0, percent=150, threshold=3))
        return result
    except Exception:
        return img if keep_color else img.convert('L')


def enhance_image(
    image_path: Path,
    work_dir: Path,
    max_pixels: int = AUDIVERIS_MAX_PIXELS,
    keep_color: bool = False,
    apply_geometry: bool = True,
) -> Optional[EnhanceResult]:
    """Full image enhancement pipeline for OMR input.

    Steps: white-border crop → rotation correction → gradient fix →
    denoise/sharpen → SR (if low-res) → pixel-limit downsample (if oversized).

    apply_geometry=False skips the first three steps (for callers that already
    did geometric preprocessing).
    """
    from ..config import SREngine
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

        if apply_geometry:
            working, border_ratio = crop_white_border(working)
            meta.border_ratio = border_ratio
            if border_ratio > 0.02:
                steps.append('白边裁剪')
                log_message(f'  [增强] 自动白边裁剪，移除 {border_ratio:.1%} 白边。')

            working, angle = detect_and_correct_rotation(working)
            meta.tilt_angle = angle
            if abs(angle) >= 0.5:
                steps.append(f'旋转校正 {angle:+.1f}°')
                log_message(f'  [增强] 旋转校正：检测到倾斜 {angle:+.1f}°，已纠偏。')

            working = correct_gradient(working)
            steps.append('梯度修正')

        working = denoise_and_sharpen(working, keep_color=keep_color)
        steps.append('去噪锐化')

        intermediate_path = work_dir / f'enhanced_stage_{image_path.stem}.png'
        working.save(intermediate_path)
        current_path = intermediate_path

        with Image.open(current_path) as chk:
            min_dim = min(chk.size)
        if min_dim < LOW_RES_PIXEL_THRESHOLD:
            upscaled_path = work_dir / f'sr_{image_path.stem}.png'
            ok = upscale_image(current_path, upscaled_path, scale=2)
            if ok:
                safe_remove_file(current_path)
                current_path = upscaled_path
                meta.sr_applied = True
                engine_label = 'Real-ESRGAN' if _current_sr_engine == SREngine.REALESRGAN.value else 'waifu2x'
                steps.append(f'{engine_label} 超分辨率')
                log_message(
                    f'  [增强] 最短边 {min_dim}px < {LOW_RES_PIXEL_THRESHOLD}px，'
                    f'已执行超分辨率放大（{engine_label}）。'
                )
            else:
                log_message('  [增强] 超分辨率放大失败，使用现有分辨率继续。', logging.WARNING)

        rescaled = fit_image_within_pixel_limit(current_path, work_dir, max_pixels=max_pixels)
        if rescaled is not None:
            safe_remove_file(current_path)
            current_path = rescaled
            meta.downscaled = True
            steps.append('降采样')

        with Image.open(current_path) as final_img:
            meta.blur_score = _measure_laplacian_stddev(final_img)

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
    """Create a human-readable reference image for the editor workspace.

    Unlike enhance_image (which outputs grayscale + blur for OMR), this keeps RGB
    and only applies lightweight visual improvements for human readability:
      1. White-border crop
      2. Rotation correction
      3. Light contrast enhancement (autocontrast 2%)
    No GaussianBlur or grayscale conversion.
    """
    if not HAS_PILLOW:
        return None
    if image_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(image_path) as img:
            working = img.convert('RGB')

        working, border_ratio = crop_white_border(working)
        if border_ratio > 0.02:
            log_message(f'  [显示参考] 白边裁剪，移除 {border_ratio:.1%} 白边。')

        working, angle = detect_and_correct_rotation(working)
        if abs(angle) >= 0.5:
            log_message(f'  [显示参考] 旋转校正 {angle:+.1f}°。')

        working = ImageOps.autocontrast(working, cutoff=2)

        ref_path = work_dir / f'display_ref_{image_path.stem}.png'
        working.save(ref_path)
        log_message(f'  [显示参考] 已生成编辑器参考图像（白边裁剪 + 旋转校正 + 对比度增强）。')
        return ref_path

    except Exception as exc:
        log_message(f'创建显示参考图像失败: {exc}', logging.WARNING)
        return None
