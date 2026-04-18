# core/omr_validator.py — OMR 识别结果逻辑校验器（指令三）
"""对 OMR 引擎输出的 JSON 格式识别结果进行乐理合法性校验。

数据格式约定
-----------
``notes_data`` 为字典列表，每条记录代表一个音符或休止符，必须包含：

    duration      (float) — 时值，以四分音符 = 1.0 为单位。
                            全音符 = 4.0，二分 = 2.0，八分 = 0.5，十六分 = 0.25。
    measure_index (int)   — 所在小节编号（0 或 1 起均可）。

可选字段：

    type  (str)  — 'note' 或 'rest'（不影响校验逻辑）。
    bbox  (list) — [x, y, w, h] 像素边界框，用于重叠启发式检测。

拍号约定
--------
``time_signature = (numerator, denominator)``，每小节期望时值（四分音符单位）：

    expected_beats = numerator × (4 / denominator)

    4/4 → 4.0，3/4 → 3.0，6/8 → 3.0，2/2 → 4.0

公共接口
--------
validate_measures(notes_data, time_signature, tolerance) -> list[dict]
    返回所有异常小节的详情列表；正常小节不出现在列表中。

generate_validation_report(notes_data, time_signature, tolerance) -> None
    打印可读的节拍校验报告到控制台，并写入日志。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from ..utils import log_message


# ──────────────────────────────────────────────────────────────────────────────
# 内部工具函数
# ──────────────────────────────────────────────────────────────────────────────

def _expected_beats(time_signature: tuple[int, int]) -> float:
    """计算 time_signature 每小节期望时值（四分音符 = 1.0 单位）。"""
    numerator, denominator = time_signature
    return numerator * (4.0 / denominator)


def _bbox_iou(a: list[float], b: list[float]) -> float:
    """计算两个 [x, y, w, h] 边界框的 IoU（交并比）。"""
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0

    inter = (ix2 - ix1) * (iy2 - iy1)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _find_bbox_overlaps(
    notes: list[dict[str, Any]],
    iou_threshold: float = 0.10,
) -> list[tuple[int, int]]:
    """在同一小节内查找 bbox 重叠（IoU ≥ iou_threshold）的音符对索引。"""
    bboxes: list[list[float] | None] = []
    for n in notes:
        raw = n.get('bbox')
        if raw and len(raw) == 4:
            bboxes.append([float(v) for v in raw])
        else:
            bboxes.append(None)

    overlaps: list[tuple[int, int]] = []
    for i in range(len(notes)):
        for j in range(i + 1, len(notes)):
            a, b = bboxes[i], bboxes[j]
            if a is not None and b is not None and _bbox_iou(a, b) >= iou_threshold:
                overlaps.append((i, j))
    return overlaps


# ──────────────────────────────────────────────────────────────────────────────
# 公共接口
# ──────────────────────────────────────────────────────────────────────────────

def validate_measures(
    notes_data: list[dict[str, Any]],
    time_signature: tuple[int, int] = (4, 4),
    tolerance: float = 0.05,
) -> list[dict[str, Any]]:
    """校验每个小节的节拍总和是否符合拍号要求。

    Parameters
    ----------
    notes_data     : 音符/休止符列表，格式见模块文档。
    time_signature : 拍号元组 (分子, 分母)，默认 (4, 4)。
    tolerance      : 时值误差容差（默认 0.05 拍），防止浮点累积误差误报。

    Returns
    -------
    list[dict]
        每个元素代表一个异常小节，包含以下字段：

        measure_index     (int)   — 小节编号。
        total_beats       (float) — 实际时值总和。
        expected_beats    (float) — 拍号要求的时值。
        delta             (float) — total - expected（正值 = 超出，负值 = 不足）。
        error             (str)   — 'TOO_SHORT' 或 'TOO_LONG'。
        overlap_candidates(list)  — 重叠音符对索引，仅在启发式匹配时填充。
        hint              (str)   — 启发式建议文字（可选，仅对特定误差量出现）。
    """
    expected = _expected_beats(time_signature)

    # 按小节分组
    measures: dict[int, list[dict]] = defaultdict(list)
    for n in notes_data:
        m_idx = int(n.get('measure_index', 0))
        measures[m_idx].append(n)

    errors: list[dict[str, Any]] = []

    for m_idx in sorted(measures.keys()):
        notes_in_measure = measures[m_idx]
        total = sum(float(n.get('duration', 0.0)) for n in notes_in_measure)
        delta = total - expected

        if abs(delta) <= tolerance:
            continue  # 小节合法，跳过

        error_entry: dict[str, Any] = {
            'measure_index': m_idx,
            'total_beats': round(total, 6),
            'expected_beats': expected,
            'delta': round(delta, 6),
            'error': 'TOO_SHORT' if delta < 0 else 'TOO_LONG',
            'overlap_candidates': [],
        }

        # 启发式：欠缺约 0.5 拍时，检查同小节内是否有坐标重叠的符号
        if abs(delta + 0.5) <= tolerance * 2:
            overlaps = _find_bbox_overlaps(notes_in_measure)
            error_entry['overlap_candidates'] = overlaps
            if overlaps:
                error_entry['hint'] = (
                    f'小节 {m_idx} 少计 0.5 拍，检测到 {len(overlaps)} 对坐标重叠符号，'
                    '疑似音符被遮挡或坐标标注重叠，建议人工核查。'
                )
            else:
                error_entry['hint'] = (
                    f'小节 {m_idx} 少计 0.5 拍，未检测到重叠符号，'
                    '建议人工核查该小节（可能存在漏识别的八分音符或休止符）。'
                )

        errors.append(error_entry)

    return errors


def generate_validation_report(
    notes_data: list[dict[str, Any]],
    time_signature: tuple[int, int] = (4, 4),
    tolerance: float = 0.05,
) -> None:
    """打印 OMR 识别结果的节拍校验报告到控制台，并写入日志。

    Parameters
    ----------
    notes_data     : 音符/休止符列表，格式见模块文档。
    time_signature : 拍号元组，默认 (4, 4)。
    tolerance      : 时值容差，默认 0.05 拍。

    输出示例
    --------
    ============================================================
     OMR 节拍校验报告  拍号: 4/4  每小节期望: 4.0 拍
     共 12 个小节，发现 2 个异常
    ============================================================
    编号    状态          实际拍数    差值      提示
    ------------------------------------------------------------
      3     ↓ TOO_SHORT   3.5         -0.5      小节 3 少计 0.5 拍...
      7     ↑ TOO_LONG    4.75        +0.75
    ============================================================
    """
    ts_str = f'{time_signature[0]}/{time_signature[1]}'
    expected = _expected_beats(time_signature)
    errors = validate_measures(notes_data, time_signature, tolerance)

    all_measures = {int(n.get('measure_index', 0)) for n in notes_data}
    total_measures = len(all_measures)

    sep = '=' * 62
    print(f'\n{sep}')
    print(f' OMR 节拍校验报告  拍号: {ts_str}  每小节期望: {expected} 拍')
    print(f' 共 {total_measures} 个小节，发现 {len(errors)} 个异常')
    print(sep)

    if not errors:
        print(' [OK] 所有小节节拍总和均合法。')
    else:
        print(f'\n{"编号":<8}{"状态":<14}{"实际拍数":<12}{"差值":<10}提示')
        print('-' * 62)
        for e in errors:
            status = '↑ TOO_LONG' if e['delta'] > 0 else '↓ TOO_SHORT'
            delta_str = f'{e["delta"]:+.4g}'
            hint = e.get('hint', '')
            print(f'  {e["measure_index"]:<6}{status:<14}{e["total_beats"]:<12}{delta_str:<10}{hint}')
            if e['overlap_candidates']:
                print(f'       重叠音符对索引: {e["overlap_candidates"][:5]}')

    print(f'{sep}\n')
    log_message(
        f'节拍校验完成：{len(errors)}/{total_measures} 个小节异常，'
        f'拍号 {ts_str}，容差 ±{tolerance}。',
        logging.INFO if not errors else logging.WARNING,
    )
