# core/image_preprocess.py — 图像预处理与综合增强管道（OMR 专用）
# 拆分自 convert.py；image_enhance.py 已整合至本模块
# 基于 Audiveris 官方文档 https://audiveris.github.io/audiveris/_pages/guides/advanced/improved_input/
# 流程：waifu2x GPU 超分辨率（低分辨率图像）+ Pillow 去噪/锐化/亮度对比度增强
#       + 完整六步增强管道：白边裁剪→旋转校正→梯度修正→去噪锐化→SR→降采样
from __future__ import annotations

import logging
import math
import subprocess
import sys

# 防止子进程在 Windows 上弹出控制台窗口（GUI 分发版）
_WIN_NO_WINDOW: int = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
import tempfile

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageFilter, ImageOps, ImageStat
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

from ..config import (
    LOGGER,
    REALESRGAN_RUNTIME_DIR_NAME,
    RUNTIME_ASSETS_DIR_NAME,
    SREngine,
    WAIFU2X_RUNTIME_DIR_NAME,
)
from ..utils import (
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


# 当前超分辨率引擎（由 worker_main 通过 set_sr_engine() 注入）
_current_sr_engine: str = SREngine.WAIFU2X.value


def set_sr_engine(engine: str) -> None:
    """设置当前超分辨率引擎（'waifu2x' 或 'realesrgan'）。由 worker 进程在启动时调用。"""
    global _current_sr_engine
    _current_sr_engine = engine


# 图像最小边像素阈值：低于此值的图像使用超分辨率放大
LOW_RES_PIXEL_THRESHOLD = 1200
# Audiveris 允许处理的最大像素总数（超出则报 Too large image 并拒绝识别）
AUDIVERIS_MAX_PIXELS = 20_000_000
# Laplacian stddev on 500×500 thumbnail below this → image is blurry, use aggressive sharpening
BLURRY_SHARPNESS_THRESHOLD = 30.0
NORMAL_MODE_GAUSSIAN_RADIUS = 0.75


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


def find_realesrgan_executable() -> Optional[Path]:
    """Search for realesrgan-ncnn-vulkan binary in app directory and PATH."""
    import os
    import shutil

    exe_names = ['realesrgan-ncnn-vulkan.exe', 'realesrgan-ncnn-vulkan']
    candidates: list[Path] = []

    for base_dir in get_runtime_search_roots():
        for exe_name in exe_names:
            candidates.extend([
                base_dir / exe_name,
                base_dir / REALESRGAN_RUNTIME_DIR_NAME / exe_name,
                base_dir / RUNTIME_ASSETS_DIR_NAME / REALESRGAN_RUNTIME_DIR_NAME / exe_name,
                base_dir / 'realesrgan-ncnn-vulkan' / exe_name,
                base_dir / 'tools' / exe_name,
            ])

    packaged = find_packaged_runtime_dir(REALESRGAN_RUNTIME_DIR_NAME)
    if packaged is not None:
        for exe_name in exe_names:
            candidates.append(packaged / exe_name)

    program_files = os.environ.get('PROGRAMFILES', r'C:\Program Files')
    local_app_data = os.environ.get('LOCALAPPDATA', '')
    for root in [Path(program_files), Path(local_app_data) / 'Programs']:
        for exe_name in exe_names:
            candidates.append(root / 'realesrgan-ncnn-vulkan' / exe_name)

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


def _find_realesrgan_python_script() -> Optional[Path]:
    """Find inference_realesrgan.py from the cloned Real-ESRGAN repo at omr_engine/realesrgan/."""
    for base_dir in get_runtime_search_roots():
        candidate = base_dir / 'omr_engine' / 'realesrgan' / 'inference_realesrgan.py'
        if candidate.exists():
            return candidate
    return None


def upscale_with_realesrgan(input_path: Path, output_path: Path, scale: int = 4) -> bool:
    """Upscale using Real-ESRGAN anime model (4×). Tries ncnn-vulkan binary first, Python script fallback.

    Always uses the anime/line-art model which is best suited for sheet music.
    The scale parameter is accepted for API compatibility but Real-ESRGAN always runs at 4×;
    callers that request 2× will get a 4× result which fit_image_within_pixel_limit can downsample.
    """
    # ── 优先：realesrgan-ncnn-vulkan 二进制（无 Python 依赖，Vulkan 加速）
    exe = find_realesrgan_executable()
    if exe is not None:
        models_dir = exe.parent / 'models'
        cmd = [
            str(exe),
            '-i', str(input_path),
            '-o', str(output_path),
            '-n', 'realesrgan-x4plus-anime',
            '-s', '4',
        ]
        if models_dir.is_dir():
            cmd += ['-m', str(models_dir)]
        log_message('使用 realesrgan-ncnn-vulkan 进行 4× 超分辨率处理 (anime 模型)...')
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600,
                check=False,
                creationflags=_WIN_NO_WINDOW,
            )
            if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                log_message(f'Real-ESRGAN (ncnn) 超分辨率完成: {output_path.name}')
                return True
            detail = (result.stderr or result.stdout or '').strip()[:200]
            log_message(f'realesrgan-ncnn-vulkan 失败（退出码 {result.returncode}）: {detail}', logging.WARNING)
        except (OSError, subprocess.SubprocessError) as exc:
            log_message(f'realesrgan-ncnn-vulkan 调用异常: {exc}', logging.WARNING)

    # ── 回退：Python 脚本（需 pip install realesrgan 及 PyTorch）
    script = _find_realesrgan_python_script()
    if script is not None:
        import shutil as _shutil
        import tempfile as _tempfile
        with _tempfile.TemporaryDirectory() as tmp_dir:
            cmd = [
                sys.executable, str(script),
                '-n', 'RealESRGAN_x4plus_anime_6B',
                '-i', str(input_path),
                '-o', tmp_dir,
                '-s', '4',
                '--fp32',
            ]
            log_message('使用 Real-ESRGAN Python 脚本进行 4× 超分辨率处理 (anime 模型)...')
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=600,
                    check=False,
                )
                out_file = Path(tmp_dir) / (input_path.stem + '.png')
                if not out_file.exists():
                    out_file = Path(tmp_dir) / input_path.name
                if result.returncode == 0 and out_file.exists() and out_file.stat().st_size > 0:
                    _shutil.copy2(str(out_file), str(output_path))
                    log_message(f'Real-ESRGAN (Python) 超分辨率完成: {output_path.name}')
                    return True
                detail = (result.stderr or result.stdout or '').strip()[:300]
                log_message(f'Real-ESRGAN Python 脚本失败: {detail}', logging.WARNING)
            except (OSError, subprocess.SubprocessError) as exc:
                log_message(f'Real-ESRGAN Python 脚本调用异常: {exc}', logging.WARNING)

    log_message(
        '未找到 Real-ESRGAN 可执行文件。\n'
        '  → 方案 A（推荐）：下载 realesrgan-ncnn-vulkan 二进制，\n'
        '    解压至 realesrgan-runtime/ 目录\n'
        '  → 方案 B：在 venv 中执行 pip install realesrgan，\n'
        '    并确保 omr_engine/realesrgan/ 子模块已克隆。',
        logging.WARNING,
    )
    return False


def upscale_image(input_path: Path, output_path: Path, scale: int = 2) -> bool:
    """Route SR upscale to the configured engine (waifu2x or Real-ESRGAN).

    Falls back to waifu2x if Real-ESRGAN is selected but unavailable.
    Falls back to bicubic resize (returns False) if neither tool is found.
    """
    if _current_sr_engine == SREngine.REALESRGAN.value:
        if upscale_with_realesrgan(input_path, output_path, scale):
            return True
        log_message('Real-ESRGAN 失败，回退到 waifu2x...', logging.WARNING)
    return upscale_image_with_waifu2x(input_path, output_path, scale)


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
            creationflags=_WIN_NO_WINDOW,
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
                # Keep color channels for deep-learning OMR engines (e.g. Homr)
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
                denoised = working.filter(ImageFilter.GaussianBlur(radius=NORMAL_MODE_GAUSSIAN_RADIUS))
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
      白边裁剪 → 旋转校正 → 梯度修正 → 去噪锐化 → SR（低分辨率时）→ 降采样

    Parameters
    ----------
    image_path  : 输入图像（PNG / JPG / JPEG）。
    work_dir    : 中间文件输出目录。
    keep_color  : True 时保留 RGB 输出，适用于需要彩色输入的深度学习模型；
                  False 时输出灰度图（Audiveris 偏好）。
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

        # 步骤 1: 白边裁剪
        working, border_ratio = crop_white_border(working)
        if border_ratio > 0.02:
            log_message(f'  [几何预处理] 白边裁剪 {border_ratio:.1%}')

        # 步骤 2: 旋转校正
        working, angle = detect_and_correct_rotation(working)
        if abs(angle) >= 0.5:
            log_message(f'  [几何预处理] 旋转校正 {angle:+.1f}°')

        # 步骤 3: 梯度/光照修正
        working = correct_gradient(working)

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
    """移除图像四边的白色/近白色空白边距，返回 (裁剪后图像, 白边像素占比)。

    检测逻辑：
    - 检查四角亮度：若四角均值 < 80（深色背景照片），则改为寻找"亮区"（纸张）
      的边界框，以去除相机/手机拍摄时的深色桌面/背景边缘。
    - 普通扫描件（白色/近白色背景）：取灰度第 92 百分位数 - 15 作为背景阈值
      （兼容纯白 255 和轻微泛黄/灰 200-230 的纸张）。
    在内容边界框外各加约 1% 的安全边距，防止裁掉边缘符号。
    """
    try:
        gray = img.convert('L')
        w, h = img.size

        if _HAS_NUMPY:
            import numpy as _np
            gray_arr = _np.array(gray)

            # 采样四角 5% 区域判断背景类型
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
                # 非近白色背景（深色、木纹、桌面等照片背景）
                # 策略参照文档扫描仪方案的核心思路（找图像中文档区域的边界），但由于
                # 乐谱内部音符/谱线产生的边缘信号远强于纸张-背景界面，Canny/轮廓法
                # 对本场景效果较差。改用「明亮像素占比」作为区分依据：
                #   - 纸张行：含大量白色/近白色像素（谱线间空白），占比通常 ≥ 14%
                #   - 背景行：几乎无此类像素（木纹/桌面 JPEG 噪点最高约 7%）
                #
                # 步骤1: 用图像中央 50% 行确定列边界（避免上下背景污染列均值）
                mid_h_start = h // 4
                mid_h_end   = max(mid_h_start + 1, h - h // 4)
                col_region_init = gray_arr[mid_h_start:mid_h_end, :]
                col_means_init  = col_region_init.mean(axis=0)
                col_thresh_init = (float(col_means_init.min()) + float(col_means_init.max())) * 0.5
                cols_ok_init    = col_means_init > col_thresh_init
                cmin = int(_np.where(cols_ok_init)[0][0])  if cols_ok_init.any() else 0
                cmax = int(_np.where(cols_ok_init)[0][-1]) if cols_ok_init.any() else w - 1

                # 步骤2: 用内侧 80% 列条带计算每行"明亮像素占比"定行边界
                # 参考文档扫描仪思路：用「纸张有明显亮白区域，背景无」这一特征区分边界。
                # 阈值 190 是经过实验标定的安全值：
                #   - 木纹/桌面背景的 JPEG 噪点最高使 bright_frac ≤ 7.5%
                #   - 纸张行（谱线间留白）的 bright_frac ≥ 14.9%
                inner_left  = cmin + (cmax - cmin) // 10
                inner_right = max(inner_left + 1, cmax - (cmax - cmin) // 10)
                row_strip   = gray_arr[:, inner_left:inner_right]
                _BRIGHT_THRESH    = 190
                _BRIGHT_FRAC_MIN  = 0.10   # ≥10% 明亮像素 → 纸张行
                row_bright  = (row_strip > _BRIGHT_THRESH).mean(axis=1)
                rows_ok     = row_bright >= _BRIGHT_FRAC_MIN

                # 回退：若无足够明亮行（极端低曝光/泛黄纸张），或检测到的纸张跨度
                # < 50% 图像高度（说明仅检测到部分纸张），改用行均值阈值
                # 注：50% 是对"摄影输入中纸张至少占帧高一半"的保守假设
                _detected_span = (
                    int(_np.where(rows_ok)[0][-1]) - int(_np.where(rows_ok)[0][0])
                    if rows_ok.any() else 0
                )
                if not rows_ok.any() or _detected_span < h * 0.50:
                    row_means  = row_strip.mean(axis=1)
                    row_thresh = (float(row_means.min()) + float(row_means.max())) * 0.5
                    rows_ok    = row_means > row_thresh
                    # 修剪：均值阈值可能将背景行纳入，用四角背景均值对结果做进一步过滤
                    # （30% 动态范围偏移，比 midpoint 更保守地排除背景行）
                    _fg_max = float(row_means.max())
                    if _fg_max > corner_mean + 20:
                        _trim_thresh = corner_mean + (_fg_max - corner_mean) * 0.30
                        _rows_trimmed = rows_ok & (row_means > _trim_thresh)
                        if _rows_trimmed.sum() >= h * 0.15:   # 至少 15% 行通过修剪才采用
                            rows_ok = _rows_trimmed
                if not rows_ok.any():
                    return img, 0.0
                rmin = int(_np.where(rows_ok)[0][0])
                rmax = int(_np.where(rows_ok)[0][-1])
            else:
                # 普通扫描件：白色/近白色边框——百分位数全局阈值法
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
            # 无 numpy：固定阈值裁白边
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


def denoise_and_sharpen(img: 'Image.Image', keep_color: bool = False) -> 'Image.Image':
    """自适应去噪 + 锐化。

    keep_color=False：输出灰度图（OMR 引擎通常偏好灰度/二值化输入）。
    keep_color=True ：保留 RGB 彩色通道，适用于深度学习模型需要颜色信息的引擎。

    清晰图像（Laplacian stddev >= BLURRY_SHARPNESS_THRESHOLD）：
        GaussianBlur(1.5) 温和去噪 → autocontrast → UnsharpMask(r=1, p=150)
    模糊图像（stddev < 阈值）：
        autocontrast 恢复动态范围 → 强力 UnsharpMask(r=3, p=300)
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
    """对乐谱图像执行完整的增强管道。

    步骤顺序
    --------
    白边裁剪 → 旋转校正 → 梯度修正 → 去噪锐化 →
    超分辨率（低分辨率时）→ 像素上限降采样（过大时）

    Parameters
    ----------
    image_path     : 原始输入图像（PNG / JPG / JPEG）。
    work_dir       : 中间文件输出目录（函数自动创建）。
    max_pixels     : 输出图像像素上限（默认 20 M px，即 Audiveris 上限）。
    apply_geometry : 是否执行前三步几何校正（白边裁剪、旋转校正、梯度修正）。
                     设为 False 可跳过，适用于调用方已完成几何预处理的情况。

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

        # ── 步骤 1-3：几何校正（apply_geometry=False 时跳过）────────────────
        if apply_geometry:
            # ── 步骤 1：白边裁剪 ─────────────────────────────────────
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

        # ── 步骤 4：去噪 + 锐化────────────────────
        working = denoise_and_sharpen(working, keep_color=keep_color)
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
