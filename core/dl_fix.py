# core/dl_fix.py — 深度学习辅助 MusicXML 修复模块
"""对 Audiveris 输出的 MusicXML 进行局部自动修复。

修复分两层
----------
第一层 — 规则型修复（始终应用，无需任何模型）
    • 小节时值完整性检查：时值之和不足时补全休止符，溢出时截断末尾元素
    • 删除零时值（无效）音符 / 休止符
    • 统计修复数量并写入日志

第二层 — ONNX 模型修复（可选，需 dl_models/music_fix.onnx）
    • 将每小节音符编码为 MIDI 音高向量（长 32，不足补 0）
    • ONNX 模型输出校正后的 MIDI 序列
    • 当预测音高与原始差异 > 2 个半音时覆写
    • 模型文件不存在时静默跳过，不影响其他流程

公共接口
--------
fix_with_dl(mxl_path, image_path, work_dir) -> Optional[Path]
    修复后写入 work_dir / dl_fixed_<stem>.musicxml；
    若无需修复则直接返回原路径，失败返回 None。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .config import LOGGER
from .utils import get_app_base_dir, log_message

try:
    from music21 import converter, note, stream
    _HAS_MUSIC21 = True
except ImportError:
    _HAS_MUSIC21 = False

try:
    import numpy as np
    import onnxruntime as ort
    _HAS_ONNX = True
except ImportError:
    _HAS_ONNX = False


# ──────────────────────────────────────────────────────────────────────────────
# 内部常量
# ──────────────────────────────────────────────────────────────────────────────

_DL_MODEL_SUBPATH = 'dl_models/music_fix.onnx'
"""ONNX 修复模型相对于应用根目录的路径。"""

_MAX_NOTES_PER_MEASURE = 32
"""编码时每小节最多取前 N 个音符（超出部分不修复）。"""


# ──────────────────────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────────────────────

def _find_dl_model() -> Optional[Path]:
    """查找 ONNX 修复模型；不存在时返回 None。"""
    candidate = get_app_base_dir() / _DL_MODEL_SUBPATH
    return candidate if candidate.exists() else None


# ──────────────────────────────────────────────────────────────────────────────
# 第一层：规则型修复
# ──────────────────────────────────────────────────────────────────────────────

def _rule_based_fix(
    score_obj: 'stream.Score',
) -> tuple['stream.Score', int]:
    """对 music21 Score 对象应用基于规则的修复。

    修复项目
    --------
    1. 删除时值为 0 的无效音符 / 休止符。
    2. 小节时值不足：在小节末尾追加休止符补齐到拍号要求。
    3. 小节时值溢出：截短末尾元素的 quarterLength。

    Returns
    -------
    (修复后的 Score, 修复计数)
    """
    fix_count = 0
    try:
        for part in score_obj.parts:
            for measure in part.getElementsByClass('Measure'):
                # ── 修复 1：删除零时值元素 ─────────────────────────
                zero_dur = [
                    el for el in measure.notesAndRests
                    if el.duration.quarterLength <= 0
                ]
                for el in zero_dur:
                    measure.remove(el)
                    fix_count += 1

                # ── 修复 2 & 3：时值完整性 ─────────────────────────
                ts = measure.getContextByClass('TimeSignature')
                if ts is None:
                    continue
                expected_ql = ts.barDuration.quarterLength
                # 重新计算（已删除零时值元素后）
                actual_ql = sum(
                    el.duration.quarterLength for el in measure.notesAndRests
                )
                diff = round(expected_ql - actual_ql, 6)

                if diff > 0.01:
                    # 不足：追加休止符
                    filler = note.Rest(quarterLength=diff)
                    measure.append(filler)
                    fix_count += 1

                elif diff < -0.01:
                    # 溢出：截短末尾元素
                    elements = list(measure.notesAndRests)
                    if elements:
                        last = elements[-1]
                        surplus = -diff
                        if last.duration.quarterLength > surplus + 0.01:
                            last.duration.quarterLength -= surplus
                            fix_count += 1

    except Exception as exc:
        log_message(f'  [DL修复] 规则型修复异常: {exc}', logging.WARNING)

    return score_obj, fix_count


# ──────────────────────────────────────────────────────────────────────────────
# 第二层：ONNX 模型修复
# ──────────────────────────────────────────────────────────────────────────────

def _onnx_fix(
    score_obj: 'stream.Score',
    model_path: Path,
) -> tuple['stream.Score', int]:
    """使用 ONNX 模型对每小节音符序列进行音高校正。

    编码协议（与模型训练端对齐）
    --------------------------
    输入  : float32[1, 32]  — 小节前 32 个音符的 MIDI 音高（0 表示休止/填充）
    输出  : float32[1, 32]  — 模型预测的校正 MIDI 音高序列

    修正条件：|原始 MIDI − 预测 MIDI| > 2 个半音，且预测值 > 0（非休止符）。
    模型文件不存在或推理失败时静默返回原对象。
    """
    if not _HAS_ONNX:
        return score_obj, 0

    fix_count = 0
    try:
        sess = ort.InferenceSession(str(model_path))
        input_name = sess.get_inputs()[0].name

        for part in score_obj.parts:
            for measure in part.getElementsByClass('Measure'):
                notes_in = list(measure.notes)[:_MAX_NOTES_PER_MEASURE]
                if not notes_in:
                    continue

                # 编码
                pitches: list[float] = []
                for n in notes_in:
                    if hasattr(n, 'pitch'):
                        pitches.append(float(n.pitch.midi))
                    elif hasattr(n, 'pitches') and n.pitches:
                        pitches.append(float(n.pitches[0].midi))
                    else:
                        pitches.append(0.0)

                vec = np.zeros((1, _MAX_NOTES_PER_MEASURE), dtype=np.float32)
                vec[0, : len(pitches)] = pitches

                # 推理
                pred = sess.run(None, {input_name: vec})[0][0]

                # 解码并校正
                for idx, n in enumerate(notes_in):
                    if idx >= len(pred):
                        break
                    predicted_midi = int(round(float(pred[idx])))
                    if predicted_midi > 0 and hasattr(n, 'pitch'):
                        if abs(n.pitch.midi - predicted_midi) > 2:
                            n.pitch.midi = predicted_midi
                            fix_count += 1

    except Exception as exc:
        log_message(f'  [DL修复] ONNX 推理失败，跳过模型修复: {exc}', logging.WARNING)

    return score_obj, fix_count


# ──────────────────────────────────────────────────────────────────────────────
# 公共接口
# ──────────────────────────────────────────────────────────────────────────────

def fix_with_dl(
    mxl_path: Path,
    image_path: Path,
    work_dir: Path,
) -> Optional[Path]:
    """对 Audiveris 输出的 MusicXML 应用深度学习辅助修复。

    Parameters
    ----------
    mxl_path   : Audiveris 输出的 MusicXML 文件路径（.mxl 或 .musicxml）。
    image_path : 对应的已增强乐谱图像（保留供扩展模型使用）。
    work_dir   : 输出临时目录（修复后文件写入此处）。

    Returns
    -------
    修复后 MusicXML 路径    —— 检测到并修复了至少一处问题。
    原始 mxl_path          —— 未检测到需修复的问题（直通）。
    None                   —— music21 不可用，或解析 / 写入失败。
    """
    if not _HAS_MUSIC21:
        log_message(
            '  [DL修复] music21 未安装，跳过深度学习辅助修复。\n'
            '    → 安装方式：pip install music21',
            logging.WARNING,
        )
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    log_message(f'  [DL修复] 开始分析 {mxl_path.name} ...')

    # ── 解析 MusicXML ─────────────────────────────────────────────
    try:
        score_obj = converter.parse(str(mxl_path))
    except Exception as exc:
        log_message(f'  [DL修复] 无法解析 MusicXML: {exc}', logging.WARNING)
        return None

    # ── 第一层：规则型修复 ────────────────────────────────────────
    score_obj, rule_fixes = _rule_based_fix(score_obj)
    if rule_fixes:
        log_message(f'  [DL修复] 规则型修复：{rule_fixes} 处小节时值已修正。')
    else:
        log_message('  [DL修复] 规则型检查：小节时值均正确。')

    # ── 第二层：ONNX 模型修复（可选）────────────────────────────
    onnx_fixes = 0
    dl_model = _find_dl_model()
    if dl_model is not None:
        score_obj, onnx_fixes = _onnx_fix(score_obj, dl_model)
        if onnx_fixes:
            log_message(f'  [DL修复] ONNX 模型修复：{onnx_fixes} 个音符音高已校正。')
        else:
            log_message('  [DL修复] ONNX 模型校验：所有音符音高在预期范围内。')
    else:
        log_message(
            f'  [DL修复] 未找到 ONNX 模型（{_DL_MODEL_SUBPATH}），跳过模型推理层。'
        )

    total_fixes = rule_fixes + onnx_fixes

    # 若无修复，直接返回原始文件（避免不必要的重新序列化）
    if total_fixes == 0:
        log_message('  [DL修复] 未检测到需要修复的问题，使用原始 MusicXML。')
        return mxl_path

    # ── 写入修复后的文件 ──────────────────────────────────────────
    fixed_path = work_dir / f'dl_fixed_{mxl_path.stem}.musicxml'
    try:
        score_obj.write('musicxml', fp=str(fixed_path))
        log_message(
            f'  [DL修复] 修复完成，共修正 {total_fixes} 处 → {fixed_path.name}'
        )
        return fixed_path
    except Exception as exc:
        log_message(f'  [DL修复] 写入修复结果失败: {exc}', logging.WARNING)
        return None
