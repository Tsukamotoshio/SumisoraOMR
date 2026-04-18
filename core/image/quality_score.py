# core/quality_score.py — 乐谱图像质量评分模块
"""对乐谱图像进行多维度质量评分（综合分 0–10）。

评分维度与权重
--------------
| 维度              | 满分 | 评估方法                              |
|-------------------|------|---------------------------------------|
| 分辨率            |  2   | 最短边像素数                          |
| 模糊度            |  3   | Laplacian 边缘响应标准差（越高越清晰）|
| 倾斜角度          |  2   | 投影方差法检测水平偏转角              |
| 白边比例          |  1   | 白色背景占原图像素比                  |
| 五线谱线条检测    |  2   | 行像素密度峰值计数                    |

总分 >= 6 → Audiveris 直接识别（高质量路径）
总分 <  6 → Audiveris + 深度学习辅助修复（低质量路径）
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

from ..config import LOGGER
from .image_preprocess import _measure_laplacian_stddev
from ..utils import log_message

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# ──────────────────────────────────────────────────────────────────────────────
# 公共类型
# ──────────────────────────────────────────────────────────────────────────────

class QualityResult(TypedDict):
    """质量评分结果字典（兼容 engine_router.route_engine 输入）。"""

    score: float
    """综合质量分（0–10）。"""

    resolution_ok: bool
    """最短边 >= 800 px 时为 True。"""

    blur_score: float
    """Laplacian 标准差（越高越清晰；< 15 为严重模糊）。"""

    border_ratio: float
    """白边占原图像素比例（0–1；越小越好）。"""

    tilt_angle: float
    """检测到的倾斜角度（°）。"""

    staff_lines_detected: bool
    """是否检测到五线谱水平线条。"""


# ──────────────────────────────────────────────────────────────────────────────
# 内部评分阈值
# ──────────────────────────────────────────────────────────────────────────────

_MIN_DIM_GOOD = 1200   # px — 分辨率良好（2 分）
_MIN_DIM_OK   = 800    # px — 分辨率可接受（1 分）
_BLUR_GOOD    = 30.0   # Laplacian stddev — 清晰（3 分）
_BLUR_OK      = 15.0   # Laplacian stddev — 勉强可接受（1.5 分）
_TILT_GOOD    = 1.0    # 度 — 倾斜轻微（2 分）
_TILT_OK      = 3.0    # 度 — 倾斜适中（1 分）


# ──────────────────────────────────────────────────────────────────────────────
# 内部测量函数
# ──────────────────────────────────────────────────────────────────────────────

# _measure_laplacian_stddev 来自 image_preprocess（唯一实现，已在上方 import）


def _measure_border_ratio(img: 'Image.Image') -> float:
    """返回白色边距占原图像素的比例（0–1）。亮度 >= 240 视为白色背景。"""
    try:
        gray = img.convert('L')
        content_mask = gray.point(lambda px: 0 if px >= 240 else 255, '1')
        bbox = content_mask.getbbox()
        if bbox is None:
            return 1.0
        w, h = img.size
        x0, y0, x1, y1 = bbox
        return max(0.0, 1.0 - (x1 - x0) * (y1 - y0) / (w * h))
    except Exception:
        return 0.0


def _measure_tilt_angle(img: 'Image.Image') -> float:
    """投影方差法检测倾斜角度（°）；需要 numpy，否则返回 0.0。

    在 400×400 缩略图上搜索 ±10°（步长 0.5°），选取最大化行方向
    投影方差的角度（水平线条最清晰时方差最大）。
    """
    if not _HAS_NUMPY:
        return 0.0
    try:
        thumb = img.convert('L').resize((400, 400), Image.LANCZOS)
        arr = np.array(thumb, dtype=np.uint8)
        best_angle = 0.0
        best_variance = -1.0
        for step in range(-20, 21):       # ±10°，步长 0.5°
            angle = step * 0.5
            rotated = Image.fromarray(arr).rotate(angle, expand=False, fillcolor=255)
            rows = np.array(rotated, dtype=np.float32).sum(axis=1)
            var = float(np.var(rows))
            if var > best_variance:
                best_variance = var
                best_angle = angle
        return best_angle
    except Exception:
        return 0.0


def _detect_staff_lines(img: 'Image.Image') -> bool:
    """检测图像中是否存在五线谱水平线条。

    算法：将图像缩至 600 宽保持比例，二值化后统计行像素黑色密度 > 0.15
    的行数（视为谱线行），≥ 5 行则判定为含五线谱（对应至少一组五线）。
    需要 numpy；否则保守返回 False。
    """
    if not _HAS_NUMPY:
        return False
    try:
        w, h = img.size
        scale = 600 / max(w, 1)
        small = img.convert('L').resize(
            (600, max(1, int(h * scale))), Image.LANCZOS
        )
        arr = np.array(small, dtype=np.float32)
        binary = (arr < 128).astype(np.float32)
        row_density = binary.mean(axis=1)
        peak_count = int((row_density > 0.15).sum())
        return peak_count >= 5
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# 公共接口
# ──────────────────────────────────────────────────────────────────────────────

def score_sheet_quality(image_path: Path) -> QualityResult:
    """对乐谱图像进行多维质量评分，返回 QualityResult 字典。

    Parameters
    ----------
    image_path : 已（或未）增强的乐谱图像路径。

    Returns
    -------
    QualityResult  包含综合分和各分项指标。
    若 Pillow 不可用，返回默认分数 5.0（中性值，路由到低质量路径）。
    """
    _default: QualityResult = {
        'score': 5.0,
        'resolution_ok': True,
        'blur_score': 100.0,
        'border_ratio': 0.0,
        'tilt_angle': 0.0,
        'staff_lines_detected': False,
    }

    if not _HAS_PIL:
        log_message('Pillow 未安装，跳过图像质量评分，使用默认分数 5.0。', logging.WARNING)
        return _default

    try:
        with Image.open(image_path) as img:
            w, h = img.size
            min_dim        = min(w, h)
            blur_score     = _measure_laplacian_stddev(img)
            border_ratio   = _measure_border_ratio(img)
            tilt_angle     = _measure_tilt_angle(img)
            staff_lines    = _detect_staff_lines(img)

        # ── 分项评分 ──────────────────────────────────────────────

        # 分辨率  (0 / 1 / 2 pts)
        if min_dim >= _MIN_DIM_GOOD:
            res_pts = 2.0
        elif min_dim >= _MIN_DIM_OK:
            res_pts = 1.0
        else:
            res_pts = 0.0

        # 模糊度  (0 / 1.5 / 3 pts)
        if blur_score >= _BLUR_GOOD:
            blur_pts = 3.0
        elif blur_score >= _BLUR_OK:
            blur_pts = 1.5
        else:
            blur_pts = 0.0

        # 倾斜    (0 / 1 / 2 pts)
        abs_tilt = abs(tilt_angle)
        if abs_tilt < _TILT_GOOD:
            tilt_pts = 2.0
        elif abs_tilt < _TILT_OK:
            tilt_pts = 1.0
        else:
            tilt_pts = 0.0

        # 白边比例 (0 / 0.5 / 1 pt)
        if border_ratio < 0.10:
            border_pts = 1.0
        elif border_ratio < 0.30:
            border_pts = 0.5
        else:
            border_pts = 0.0

        # 五线谱检测 (0 / 2 pts)
        staff_pts = 2.0 if staff_lines else 0.0

        total = max(0.0, min(10.0, res_pts + blur_pts + tilt_pts + border_pts + staff_pts))

        result: QualityResult = {
            'score': round(total, 2),
            'resolution_ok': min_dim >= _MIN_DIM_OK,
            'blur_score': round(blur_score, 2),
            'border_ratio': round(border_ratio, 4),
            'tilt_angle': round(tilt_angle, 2),
            'staff_lines_detected': staff_lines,
        }

        log_message(
            f'  [质量评分] 总分 {total:.1f}/10 | '
            f'分辨率 {w}×{h} ({res_pts:.0f}pt) | '
            f'模糊度 {blur_score:.1f} ({blur_pts:.1f}pt) | '
            f'倾斜 {tilt_angle:.1f}° ({tilt_pts:.0f}pt) | '
            f'白边 {border_ratio:.1%} ({border_pts:.1f}pt) | '
            f'五线谱 {"✓" if staff_lines else "✗"} ({staff_pts:.0f}pt)'
        )
        return result

    except Exception as exc:
        log_message(f'质量评分失败: {exc}', logging.WARNING)
        return _default
