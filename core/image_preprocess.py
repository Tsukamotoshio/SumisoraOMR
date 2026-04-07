# core/image_preprocess.py — 图像预处理与综合增强管道（OMR 专用）
# 拆分自 convert.py；image_enhance.py 已整合至本模块
# 基于 Audiveris 官方文档 https://audiveris.github.io/audiveris/_pages/guides/advanced/improved_input/
# 流程：waifu2x GPU 超分辨率（低分辨率图像）+ Pillow 去噪/锐化/亮度对比度增强
#       + 完整六步增强管道：白边裁剪→旋转校正→梯度修正→去噪锐化→SR→降采样
from __future__ import annotations

import logging
import math
import subprocess
import tempfile

from dataclasses import dataclass, field
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
    """对栅格图像执行完整的 OMR 预处理管道，委托给 :func:`enhance_image`。

    新流程顺序（步骤由 enhance_image 统一执行）：
      梯度修正 → 旋转校正 → 白边裁剪 → 去噪锐化 → SR（低分辨率时）→ 降采样

    Parameters
    ----------
    image_path  : 输入图像（PNG / JPG / JPEG）。
    work_dir    : 中间文件输出目录。
    keep_color  : 保留此参数以兼容旧调用，此函数输出灰度（Audiveris 偏好）。
    max_pixels  : 输出像素上限。

    Returns
    -------
    Path  预处理后图像路径；失败或不适用时返回 None。
    """

    if not HAS_PILLOW:
        return None
    if image_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    result = enhance_image(image_path, work_dir, max_pixels=max_pixels)
    if result is None:
        return None
    log_message('图像预处理完成，将使用增强图像进行 OMR 识别。')
    return result.enhanced_path


def preprocess_geometry_for_omr(
    image_path: Path,
    work_dir: Path,
    max_pixels: int = AUDIVERIS_MAX_PIXELS,
) -> Optional[Path]:
    """轻量几何预处理：仅执行梯度修正 + 旋转校正 + 白边裁剪，保留 RGB，不做去噪锐化。

    专为 Audiveris 输入设计：Audiveris 自带高质量内部二值化器，对原始质轻微处理后
    的彩色图效果更好；过度的去噪/锐化/灰度转换会导致识别率下降。

    步骤
    ----
    1. autocontrast 梯度修正 —— 校正光照不均，不改变轮廓
    2. 旋转校正 —— 消除拍摄倾斜，改善谱线平行度
    3. 白边裁剪 —— 去除无信息边框，减小图像尺寸
    4. 像素上限降采样（超出时）

    Parameters
    ----------
    image_path  : 输入图像（PNG / JPG / JPEG）。
    work_dir    : 输出目录（自动创建）。
    max_pixels  : 输出像素上限（默认 Audiveris 20MP 上限）。

    Returns
    -------
    Path  轻量处理后图像路径（RGB PNG）；失败时返回 None。
    """
    if not HAS_PILLOW:
        return None
    if image_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(image_path) as img:
            working = img.convert('RGB')

        # 步骤 1: 梯度/光照修正
        working = correct_gradient(working)

        # 步骤 2: 旋转校正
        working, angle = detect_and_correct_rotation(working)
        if abs(angle) >= 0.5:
            log_message(f'  [几何预处理] 旋转校正 {angle:+.1f}°')

        # 步骤 3: 白边裁剪
        working, border_ratio = crop_white_border(working)
        if border_ratio > 0.02:
            log_message(f'  [几何预处理] 白边裁剪 {border_ratio:.1%}')

        out_path = work_dir / f'geo_{image_path.stem}.png'
        working.save(out_path)
        current_path = out_path

        # 步骤 4: 像素上限降采样
        rescaled = fit_image_within_pixel_limit(current_path, work_dir, max_pixels=max_pixels)
        if rescaled is not None:
            safe_remove_file(current_path)
            current_path = rescaled

        return current_path
    except Exception as exc:
        log_message(f'几何预处理失败: {exc}', logging.WARNING)
        return None


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
    """通过水平谱线拟合检测倾斜角度并旋转纠偏。

    优先路径（需要 cv2）：
        对灰度图做 Otsu 反二值化 → 形态学水平线提取（内核宽 20% 图像宽度）
        → 对每条候选谱线做轮廓直线拟合 → 取所有拟合角度的中位数。
        仅保留长度 > 图像宽度 15% 的线段，避免短横线干扰。

    回退路径（无 cv2 / cv2 路径失败）：
        使用保持宽高比的缩略图（长边 ≤ 1200px）搜索 ±15° 范围，
        选取使行方向投影方差最大的角度。


    Returns
    -------
    (旋转后图像, 检测到的倾斜角度°)
    """
    if not _HAS_NUMPY:
        return img, 0.0

    best_angle = 0.0

    # ── 主路径：cv2 形态学 + 直线拟合 ────────────────────────────────────
    if _HAS_CV2:
        try:
            arr = np.array(img.convert('L'), dtype=np.uint8)
            h_arr, w_arr = arr.shape
            _, binary = _cv2.threshold(arr, 0, 255, _cv2.THRESH_BINARY_INV + _cv2.THRESH_OTSU)
            # 形态学提取水平线：内核宽度 20%，只保留连续跨度 ≥ 20% 的暗段
            kernel_len = max(3, int(w_arr * 0.20))
            h_kernel = _cv2.getStructuringElement(_cv2.MORPH_RECT, (kernel_len, 1))
            lines_img = _cv2.morphologyEx(binary, _cv2.MORPH_OPEN, h_kernel)
            # 找每条线段的角度（轮廓 + fitLine）
            contours, _ = _cv2.findContours(lines_img, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
            min_line_width = w_arr * 0.15   # 至少跨越 15% 宽度才视为谱线
            angles: list[float] = []
            for cnt in contours:
                x, y, cw, ch = _cv2.boundingRect(cnt)
                if cw < min_line_width:
                    continue
                fit = _cv2.fitLine(cnt, _cv2.DIST_L2, 0, 0.01, 0.01)
                vx, vy = float(fit[0]), float(fit[1])
                angle_deg = float(np.degrees(np.arctan2(vy, vx)))
                if abs(angle_deg) <= 15:   # 只关心 ±15° 范围内的近水平线
                    angles.append(angle_deg)
            if len(angles) >= 3:
                best_angle = float(np.median(angles))
        except Exception:
            best_angle = 0.0

    # ── 回退路径：投影方差法（保持宽高比缩略图）────────────────────────
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
            for step in range(-30, 31):   # ±15°，步长 0.5°
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
    梯度修正 → 旋转校正 → 白边裁剪 → 去噪锐化 →

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

        # ── 步骤 1：梯度 / 光照修正 ──────────────────────────────
        working = correct_gradient(working)
        steps.append('梯度修正')


        # ── 步骤 2：旋转校正 ─────────────────────────────────────
        working, angle = detect_and_correct_rotation(working)
        meta.tilt_angle = angle
        if abs(angle) >= 0.5:
            steps.append(f'旋转校正 {angle:+.1f}°')
            log_message(f'  [增强] 旋转校正：检测到倾斜 {angle:+.1f}°，已纠偏。')

        # ── 步骤 3：自动白边裁剪 ──────────────────────────────────
        working, border_ratio = crop_white_border(working)
        meta.border_ratio = border_ratio
        if border_ratio > 0.02:
            steps.append('白边裁剪')
            log_message(f'  [增强] 自动白边裁剪，移除 {border_ratio:.1%} 白边。')


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


# ══════════════════════════════════════════════════════════════════════════════
# 指令一：自动 DPI 与 Interline 归一化网关
# ══════════════════════════════════════════════════════════════════════════════

def _detect_staff_line_centroids(
    img_array: 'np.ndarray',
) -> 'tuple[list[float], float]':
    """检测五线谱横线质心列表及中位间距（内部辅助函数）。

    算法：灰度 → Otsu 反二值化 → 形态学水平线提取（去除非全跨度特征）
          → 聚类行段取质心。

    形态学方法：使用宽度 = 图像宽度 × 40% 的水平结构元做开运算，
    仅保留在宽度方向连续延伸超过 40% 的暗区段（即真正的谱线），
    过滤单音符、文字、竖线等非谱线特征。

    Returns
    -------
    (centroids, median_spacing)
        centroids      : 按从上到下排序的谱线行质心 y 坐标列表。
        median_spacing : 相邻质心间距的中位数（px）；< 2 行时为 0.0。
    """
    try:
        gray = _cv2.cvtColor(img_array, _cv2.COLOR_BGR2GRAY) if len(img_array.shape) == 3 else img_array.copy()
        _, binary = _cv2.threshold(gray, 0, 255, _cv2.THRESH_BINARY_INV + _cv2.THRESH_OTSU)
        h, w = binary.shape

        # 形态学方法：水平结构元开运算，只保留跨越 ≥ 40% 宽度的连续暗段
        kernel_len = max(3, int(w * 0.40))
        h_kernel = _cv2.getStructuringElement(_cv2.MORPH_RECT, (kernel_len, 1))
        staff_lines_only = _cv2.morphologyEx(binary, _cv2.MORPH_OPEN, h_kernel)
        row_sums = np.sum(staff_lines_only // 255, axis=1)
        is_line_row = row_sums > 0  # 任何存活像素均表示有跨幅谱线

        centroids: list[float] = []
        in_cluster = False
        cluster_start = 0
        for r in range(h):
            if is_line_row[r] and not in_cluster:
                in_cluster = True
                cluster_start = r
            elif not is_line_row[r] and in_cluster:
                in_cluster = False
                centroids.append((cluster_start + r - 1) / 2.0)
        if in_cluster:
            centroids.append((cluster_start + h - 1) / 2.0)
        if len(centroids) < 2:
            return centroids, 0.0
        diffs = [centroids[i + 1] - centroids[i] for i in range(len(centroids) - 1)]
        return centroids, float(np.median(diffs))
    except Exception:
        return [], 0.0


def detect_interline_spacing(img_array: 'np.ndarray') -> float:
    """使用水平投影检测五线谱线平均间距（像素）。

    调用 _detect_staff_line_centroids() 完成实际检测，并打印 print 日志
    显示候选谱线数与原始中位间距。

    Returns
    -------
    float
        相邻谱线平均像素间距；无法检测时返回 0.0。
    """
    if not _HAS_CV2 or not _HAS_NUMPY:
        print('[interline] 需要 opencv-python 和 numpy，检测跳过。')
        return 0.0
    centroids, median_diff = _detect_staff_line_centroids(img_array)
    if len(centroids) < 2:
        print(f'[interline] 仅检测到 {len(centroids)} 条候选谱线，无法计算间距。')
        return 0.0
    print(f'[interline] 检测到 {len(centroids)} 条候选谱线，原始中位间距 = {median_diff:.2f} px')
    return median_diff


def normalize_dpi_by_interline(
    image_path: Path,
    target_spacing: int = 20,
    spacing_low: int = 18,
    spacing_high: int = 24,
) -> 'Optional[np.ndarray]':
    """DPI 归一化网关：将五线谱间距自动缩放至 target_spacing px。

    决策逻辑
    --------
    - 间距在 [spacing_low, spacing_high]：返回 None，调用方保持原路径。
    - 间距 < spacing_low（图太小）：优先 waifu2x 超分辨率放大后精确缩放；
      waifu2x 不可用时回退 INTER_CUBIC。
    - 间距 > spacing_high（图太大）：INTER_AREA 高质量降采样。

    打印 print 日志显示原始间距、缩放比例及预计缩放后间距。

    Parameters
    ----------
    image_path     : 输入图像路径（PNG/JPG）。
    target_spacing : 目标谱线间距（px），默认 20（对应 Audiveris 推荐 300 DPI）。
    spacing_low    : 间距下限（px），低于此值触发放大。
    spacing_high   : 间距上限（px），高于此值触发缩小。

    Returns
    -------
    np.ndarray (BGR)  需要缩放时返回处理后的图像数组；无需缩放或失败时返回 None。
    """
    if not _HAS_CV2 or not _HAS_NUMPY:
        log_message('normalize_dpi_by_interline: 需要 opencv-python 和 numpy。', logging.WARNING)
        return None
    try:
        img_array = _cv2.imread(str(image_path))
        if img_array is None:
            log_message(f'cv2.imread 无法读取: {image_path}', logging.WARNING)
            return None

        spacing = detect_interline_spacing(img_array)
        if spacing < 2.0:
            print('[DPI归一化] 未检测到有效间距，返回原始图像数组。')
            return img_array

        print(f'[DPI归一化] 原始间距 = {spacing:.2f} px，目标 = {target_spacing} px，'
              f'接受范围 [{spacing_low}, {spacing_high}]')

        if spacing_low <= spacing <= spacing_high:
            print('[DPI归一化] 间距已在接受范围内，无需缩放。')
            return None

        scale = target_spacing / spacing
        h, w = img_array.shape[:2]
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        print(f'[DPI归一化] 缩放比例 = {scale:.4f}，尺寸 {w}×{h} → {new_w}×{new_h}')

        if scale < 1.0:
            # 缩小：INTER_AREA（适合下采样，保留高频细节）
            result = _cv2.resize(img_array, (new_w, new_h), interpolation=_cv2.INTER_AREA)
            print(f'[DPI归一化] 降采样完成，预计间距 ≈ {spacing * scale:.2f} px')
            return result

        # 放大：优先 waifu2x，失败则 INTER_CUBIC
        waifu2x_exe = find_waifu2x_executable()
        if waifu2x_exe is not None:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_in = Path(tmp_dir) / 'dpi_in.png'
                tmp_out = Path(tmp_dir) / 'dpi_out.png'
                _cv2.imwrite(str(tmp_in), img_array)
                waifu_scale = 4 if scale > 3.0 else 2
                ok = upscale_image_with_waifu2x(tmp_in, tmp_out, scale=waifu_scale)
                if ok:
                    sr_img = _cv2.imread(str(tmp_out))
                    if sr_img is not None:
                        result = _cv2.resize(sr_img, (new_w, new_h), interpolation=_cv2.INTER_AREA)
                        print(f'[DPI归一化] waifu2x {waifu_scale}× 超分+精确缩放完成，'
                              f'预计间距 ≈ {spacing * scale:.2f} px')
                        return result

        # 回退：INTER_CUBIC 双三次插值
        result = _cv2.resize(img_array, (new_w, new_h), interpolation=_cv2.INTER_CUBIC)
        print(f'[DPI归一化] INTER_CUBIC 放大完成（waifu2x 不可用），'
              f'预计间距 ≈ {spacing * scale:.2f} px')
        return result

    except Exception as exc:
        log_message(f'normalize_dpi_by_interline 失败: {exc}', logging.WARNING)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 指令二：五线谱线"分离与增强"预处理器
# ══════════════════════════════════════════════════════════════════════════════

def separate_staff_lines(
    img_array: 'np.ndarray',
) -> 'tuple[Optional[np.ndarray], Optional[np.ndarray]]':
    """利用形态学操作将五线谱线与音符分层。

    流程
    ----
    1. **二值化**：``cv2.adaptiveThreshold`` 处理光照不均，
       输出暗像素（谱线/音符）= 255，背景 = 0。
    2. **提取横线**：水平 Kernel（宽 = 图像宽度 / 30，高 = 1）做形态学开运算
       （先腐蚀再膨胀），保留足够长的横线，过滤掉音符笔画。
    3. **擦除谱线**：二值图减去谱线蒙版，得到"去线版"。
    4. **修复符干**：用竖向 1×3 Kernel 轻微 dilate，
       填补符干因擦线产生的断痕。

    Parameters
    ----------
    img_array : BGR 或灰度 numpy 数组。

    Returns
    -------
    (staff_lines_mask, notes_image)
        staff_lines_mask : 仅含五线谱横线的二值图（255 = 线，0 = 背景）。
        notes_image      : 已去除谱线、轻微修补后的二值图，更适合音符检测。
        失败时两值均为 None。
    """
    if not _HAS_CV2 or not _HAS_NUMPY:
        log_message('separate_staff_lines: 需要 opencv-python 和 numpy。', logging.WARNING)
        return None, None
    try:
        gray = _cv2.cvtColor(img_array, _cv2.COLOR_BGR2GRAY) if len(img_array.shape) == 3 else img_array.copy()
        h, w = gray.shape

        # 步骤 1：自适应二值化（处理扫描件光照不均）
        binary = _cv2.adaptiveThreshold(
            gray, 255,
            _cv2.ADAPTIVE_THRESH_MEAN_C,
            _cv2.THRESH_BINARY_INV,
            blockSize=15, C=10,
        )

        # 步骤 2：水平 Kernel 形态学开运算，提取五线谱横线
        kernel_w = max(10, w // 30)  # 宽度 ≈ 图像宽度 / 30
        h_kernel = _cv2.getStructuringElement(_cv2.MORPH_RECT, (kernel_w, 1))
        staff_lines_mask = _cv2.morphologyEx(binary, _cv2.MORPH_OPEN, h_kernel)

        # 步骤 3：从二值图中减去谱线蒙版 → 仅保留音符
        notes_only = _cv2.subtract(binary, staff_lines_mask)

        # 步骤 4：竖向 1×3 Kernel dilate，修补符干断裂
        repair_kernel = _cv2.getStructuringElement(_cv2.MORPH_RECT, (1, 3))
        notes_repaired = _cv2.dilate(notes_only, repair_kernel, iterations=1)

        log_message(
            f'[谱线分离] Kernel {kernel_w}×1 提取谱线蒙版，'
            f'音符图层已修补（1×3 dilate）。'
        )
        return staff_lines_mask, notes_repaired

    except Exception as exc:
        log_message(f'separate_staff_lines 失败: {exc}', logging.WARNING)
        return None, None


# ══════════════════════════════════════════════════════════════════════════════
# 谱行切片（Staff-row slicing）— 供切片 OMR 管道使用
# ══════════════════════════════════════════════════════════════════════════════

def slice_staff_rows(
    image_path: Path,
    work_dir: Path,
    margin_ratio: float = 0.15,
) -> 'list[Path]':
    """将整张乐谱图像按谱系统（每行五线谱）切片成子图列表。

    算法
    ----
    1. 调用 ``_detect_staff_line_centroids()`` 获取所有谱线质心与中位间距。
    2. 以 gap > 2.5× median_spacing 的空白区段作为谱系统边界，
       将质心序列分组为若干谱系统（system）。
    3. 每个系统上下加 max(spacing, system_height × margin_ratio) 边距后裁剪。
    4. 每个裁剪子图保存为 ``staff_row_NNN.png``。

    当检测到的谱系统不足 2 个时，返回 ``[image_path]``（不切片，调用方使用原图）。

    Parameters
    ----------
    image_path   : 输入图像路径（PNG/JPG）。
    work_dir     : 切片子图输出目录（自动创建）。
    margin_ratio : 上下安全边距占谱系统高度的比例，默认 0.15。

    Returns
    -------
    list[Path]
        切片后的子图路径列表；不切片时为 ``[image_path]``。
    """
    if not _HAS_CV2 or not _HAS_NUMPY:
        return [image_path]

    # cv2.imread 在 Windows 上无法读取含非 ASCII 字符的路径（含父目录）
    # 若路径含中文等非 ASCII 字符，先复制到系统 TEMP 目录读取
    _cv2_path = image_path
    _cv2_tmp: 'Optional[Path]' = None
    try:
        str(image_path.resolve()).encode('ascii')
    except UnicodeEncodeError:
        import tempfile, hashlib as _hs
        _h = _hs.sha1(str(image_path.resolve()).encode('utf-8')).hexdigest()[:8]
        _cv2_tmp = Path(tempfile.gettempdir()) / f'slice_in_{_h}{image_path.suffix.lower()}'
        try:
            import shutil as _sh
            _sh.copy2(str(image_path), str(_cv2_tmp))
            _cv2_path = _cv2_tmp
        except Exception:
            _cv2_tmp = None

    img = _cv2.imread(str(_cv2_path))
    if _cv2_tmp is not None:
        try:
            _cv2_tmp.unlink()
        except Exception:
            pass
    if img is None:
        log_message(f'[切片] cv2 无法读取图像: {image_path}', logging.WARNING)
        return [image_path]

    centroids, spacing = _detect_staff_line_centroids(img)
    if len(centroids) < 2 or spacing < 2.0:
        print('[切片] 未能检测到足够谱线质心，不切片。')
        return [image_path]

    # 将质心序列分组：相邻质心间距 > 2.5× spacing → 新系统起点
    system_groups: list[list[float]] = []
    current_group: list[float] = [centroids[0]]
    for c in centroids[1:]:
        if c - current_group[-1] > spacing * 2.5:
            system_groups.append(current_group)
            current_group = [c]
        else:
            current_group.append(c)
    system_groups.append(current_group)

    if len(system_groups) <= 1:
        print('[切片] 仅检测到 1 个谱系统，不切片。')
        return [image_path]

    # 质量过滤：只计算线数在 [5, 6] 范围内的「正常五线谱」系统
    valid_groups = [g for g in system_groups if 5 <= len(g) <= 6]
    if len(valid_groups) < 2:
        print(
            f'[切片] 检测到 {len(system_groups)} 个分组，但仅 {len(valid_groups)} 个为'
            f' 5-6 线标准五线谱，结构不可信，不切片。'
        )
        return [image_path]
    system_groups = valid_groups

    work_dir.mkdir(parents=True, exist_ok=True)
    h, w = img.shape[:2]
    slices: list[Path] = []

    for i, group in enumerate(system_groups):
        top_c = group[0]
        bot_c = group[-1]
        system_height = max(bot_c - top_c + spacing, spacing)
        margin = max(int(spacing), int(system_height * margin_ratio))
        y0 = max(0, int(top_c - spacing / 2 - margin))
        y1 = min(h, int(bot_c + spacing / 2 + margin))
        crop = img[y0:y1, :]
        out_path = work_dir / f'staff_row_{i:03d}.png'
        _cv2.imwrite(str(out_path), crop)
        slices.append(out_path)
        print(
            f'[切片] 谱行 {i + 1}/{len(system_groups)}: y={y0}–{y1}'
            f'（高 {y1 - y0}px，含 {len(group)} 条谱线）'
        )

    log_message(
        f'[切片] 共切出 {len(slices)} 个谱行，已保存至 {work_dir.name}/'
    )
    return slices


def correct_slice_rotation(image_path: Path, work_dir: Path) -> Optional[Path]:
    """对单张切片图像进行旋转校正。

    使用 :func:`detect_and_correct_rotation` 检测倾斜角度；仅当 |angle| >= 0.5° 时
    才写出新文件（避免不必要的 I/O）。需要 Pillow 和 numpy。

    Parameters
    ----------
    image_path : 切片图像路径（PNG/JPG）。
    work_dir   : 校正后图像的输出目录（自动创建）。

    Returns
    -------
    Path  校正后图像路径（校正后文件名带 ``rot_`` 前缀）；
          倾斜角度 < 0.5° 或校正失败时返回 None（调用方应继续使用原图）。
    """
    if not HAS_PILLOW or not _HAS_NUMPY:
        return None
    try:
        with Image.open(image_path) as img:
            working = img.convert('RGB')
        corrected, angle = detect_and_correct_rotation(working)
        if abs(angle) < 0.5:
            return None  # 倾斜可忽略，不写新文件
        work_dir.mkdir(parents=True, exist_ok=True)
        out_path = work_dir / f'rot_{image_path.name}'
        corrected.save(out_path)
        log_message(
            f'  [切片旋转校正] {image_path.name}: 检测到倾斜 {angle:+.1f}°，已纠偏。'
        )
        return out_path
    except Exception as exc:
        log_message(f'  [切片旋转校正] {image_path.name} 失败: {exc}', logging.WARNING)
        return None

