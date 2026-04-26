# core/staff_slicer.py — 谱线检测 / DPI 归一化 / 谱行切片
# 拆分自 image_preprocess.py
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from .image_preprocess import (
    HAS_PILLOW,
    LOW_RES_PIXEL_THRESHOLD,
    detect_and_correct_rotation,
    upscale_image,
)
from ..utils import log_message

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

try:
    from PIL import Image
except ImportError:
    pass


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

        # 放大：优先超分辨率，失败则 INTER_CUBIC
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_in = Path(tmp_dir) / 'dpi_in.png'
            tmp_out = Path(tmp_dir) / 'dpi_out.png'
            _cv2.imwrite(str(tmp_in), img_array)
            sr_scale = 4 if scale > 3.0 else 2
            ok = upscale_image(tmp_in, tmp_out, scale=sr_scale)
            if ok:
                sr_img = _cv2.imread(str(tmp_out))
                if sr_img is not None:
                    result = _cv2.resize(sr_img, (new_w, new_h), interpolation=_cv2.INTER_AREA)
                    print(f'[DPI归一化] SR {sr_scale}× 超分+精确缩放完成，'
                          f'预计间距 ≈ {spacing * scale:.2f} px')
                    return result

        # 回退：INTER_CUBIC 双三次插值
        result = _cv2.resize(img_array, (new_w, new_h), interpolation=_cv2.INTER_CUBIC)
        print(f'[DPI归一化] INTER_CUBIC 放大完成（超分辨率不可用），'
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
        import hashlib as _hs
        import shutil as _sh
        _h = _hs.sha1(str(image_path.resolve()).encode('utf-8')).hexdigest()[:8]
        _cv2_tmp = Path(tempfile.gettempdir()) / f'slice_in_{_h}{image_path.suffix.lower()}'
        try:
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

    # 大谱表拆分：10-12 线的组 = 两个相邻 5-6 线谱系统间距过小被合并
    # 在最大相邻质心间距处切分，若两半各为 5-6 线则展开为两个子组
    split_groups: list[list[float]] = []
    for _grp in system_groups:
        if 10 <= len(_grp) <= 12:
            _diffs_in = [_grp[i + 1] - _grp[i] for i in range(len(_grp) - 1)]
            _split_idx = int(np.argmax(_diffs_in))
            _sub1, _sub2 = _grp[:_split_idx + 1], _grp[_split_idx + 1:]
            if 5 <= len(_sub1) <= 6 and 5 <= len(_sub2) <= 6:
                split_groups.append(_sub1)
                split_groups.append(_sub2)
                print(
                    '[切片] 大谱表拆分：%d线组 y=%.0f-%.0f → %d+%d线子组' % (
                        len(_grp), _grp[0], _grp[-1], len(_sub1), len(_sub2)
                    )
                )
                continue
        split_groups.append(_grp)
    system_groups = split_groups

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

    # 内容密度过滤：去除无音符内容的背景噪声组
    # 原理：真正的五线谱行除谱线外还有音符头、符干、连线等内容；
    # 背景纹理被误识别为谱线的区域几乎没有附加墨水内容。
    if len(system_groups) >= 2:
        try:
            h_img, w_img = img.shape[:2]
            gray_for_density = _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()
            _, binary_for_density = _cv2.threshold(
                gray_for_density, 0, 255, _cv2.THRESH_BINARY_INV + _cv2.THRESH_OTSU
            )
            # 与 _detect_staff_line_centroids 相同的水平核，用于从区域中减去谱线
            _dens_kern_len = max(3, int(w_img * 0.40))
            _dens_h_kern = _cv2.getStructuringElement(_cv2.MORPH_RECT, (_dens_kern_len, 1))
            density_valid: list[list[float]] = []
            for _grp in system_groups:
                _top_c, _bot_c = _grp[0], _grp[-1]
                _yr0 = max(0, int(_top_c - spacing * 2))
                _yr1 = min(h_img, int(_bot_c + spacing * 2))
                _region = binary_for_density[_yr0:_yr1, :]
                _staff_mask = _cv2.morphologyEx(_region, _cv2.MORPH_OPEN, _dens_h_kern)
                _notes_only = _cv2.subtract(_region, _staff_mask)
                _total_px = _notes_only.shape[0] * _notes_only.shape[1]
                _note_px = int(_notes_only.sum() / 255)
                _density = _note_px / _total_px if _total_px > 0 else 0.0
                if _density >= 0.010:  # ≥1% 音符内容才视为真正谱行
                    density_valid.append(_grp)
                else:
                    print(
                        f'[切片] 内容密度过滤：跳过低密度组 y={_top_c:.0f}–{_bot_c:.0f}'
                        f'（音符密度={_density:.4f} < 0.010）'
                    )
            if len(density_valid) >= 2:
                system_groups = density_valid
            elif len(density_valid) == 1:
                print('[切片] 内容密度过滤后仅剩 1 个有效组，不切片。')
                return [image_path]
            # len==0: 异常情况，保留原 system_groups 继续处理
        except Exception as _dens_exc:
            print(f'[切片] 内容密度过滤失败（已跳过）: {_dens_exc}')

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
