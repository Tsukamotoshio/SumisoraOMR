# core/omr_fusion.py — 双引擎 OMR 识别结果融合（Oemer × Audiveris）
"""AUTO 模式图片输入时，Oemer 和 Audiveris 各自独立识别，
然后对识别结果按置信度进行音符级融合：

  · 置信度评估依据：音域（中音区权重高）、是否为自然音（无升降号）、
    旋律平滑度（小跳进比大跳进更常见）、时值规整性。
  · 同一时间偏移处：两引擎均识别为非休止符时，取置信度更高者；
    一方为休止符、一方为音符时，优先取音符（减少漏音率）。
  · 小节数不一致时：差异 ≤ 40% 则按索引对齐融合；差异 > 40% 则取
    小节数更多的引擎结果（通常意味着谱行检测更完整）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .config import JianpuNote
from .utils import log_message


def _clone_note_with_duration(note: JianpuNote, duration: float) -> JianpuNote:
    """Clone a JianpuNote clamping its duration to the nearest allowed value."""
    # Inline to avoid circular import at module load time; lazy-imports jianpu_core.
    from .jianpu_core import normalize_jianpu_duration, infer_duration_dots
    norm = normalize_jianpu_duration(duration)
    return JianpuNote(
        note.symbol, note.accidental,
        note.upper_dots, note.lower_dots,
        norm, infer_duration_dots(norm),
        note.midi, note.is_rest,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 音符置信度评分
# ──────────────────────────────────────────────────────────────────────────────

def _note_confidence(note: JianpuNote, prev_midi: float = 0.0) -> float:
    """估算一个 JianpuNote 被正确识别的置信度（0–1 范围）。

    评分维度（各自独立叠加）：
      · 休止符基础分较低（休止符容易被漏报）
      · 自然音（无升降号）加分
      · 中音区（E3–C6, MIDI 52–84）加分；极端音区减分
      · 旋律平滑度：与上一音相比，跳进越小，置信度越高
      · 时值规整：四分/二分/全音符等加分
    """
    if note.is_rest:
        return 0.25  # 休止符经常被漏报，不给太高权重

    score = 0.50

    # 自然音更常见
    if note.accidental == '':
        score += 0.10

    # 中音区权重
    midi = note.midi if note.midi is not None else 60
    if 52 <= midi <= 84:    # E3–C6（最常见旋律区）
        score += 0.18
    elif 40 <= midi <= 96:  # E2–C7（合理音域）
        score += 0.06
    else:                   # 极端音区 → 很可能是识别错误
        score -= 0.22

    # 旋律平滑度（小跳比大跳更可信）
    if prev_midi > 0 and note.midi is not None:
        interval = abs(note.midi - prev_midi)
        if interval == 0:
            score += 0.10
        elif interval <= 5:   # 四度以内
            score += 0.10
        elif interval <= 12:  # 八度以内
            score += 0.02
        elif interval > 19:   # 超过八度+小六度 → 可疑跳进
            score -= 0.16

    # 时值规整性
    if note.duration in (4.0, 2.0, 1.0, 0.5, 0.25, 0.125):
        score += 0.06

    return max(0.0, min(1.0, score))


def _pick_better_note(
    a: JianpuNote,
    b: JianpuNote,
    prev_midi: float = 0.0,
) -> JianpuNote:
    """在同一时间偏移处对两个音符投票，返回置信度更高的那个。

    规则（按优先级）：
      1. 两者均为休止符 → 取任一（返回 a）
      2. 一方为休止符、一方为音符 → 取音符（减少漏音）
      3. 两者音高接近（±2 半音，含异名同音）→ 取时值更规整者
      4. 音高差异较大 → 比较置信度分数
    """
    if a.is_rest and b.is_rest:
        return a
    if a.is_rest:
        return b
    if b.is_rest:
        return a

    # 音高一致（enharmonic / 细微差异）
    if a.midi is not None and b.midi is not None and abs(a.midi - b.midi) <= 2:
        clean_durs = {4.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.25, 0.125}
        a_clean = a.duration in clean_durs
        b_clean = b.duration in clean_durs
        if a_clean and not b_clean:
            return a
        if b_clean and not a_clean:
            return b
        return a  # tie-break: 优先 Oemer（调用者约定 a=oemer）

    # 音高不同：比较置信度
    ca = _note_confidence(a, prev_midi)
    cb = _note_confidence(b, prev_midi)
    return a if ca >= cb else b


# ──────────────────────────────────────────────────────────────────────────────
# 小节级融合辅助
# ──────────────────────────────────────────────────────────────────────────────

def _to_offset_map(notes: list[JianpuNote]) -> dict[float, JianpuNote]:
    """将 JianpuNote 列表转换为 {累计偏移: 音符} 字典。"""
    d: dict[float, JianpuNote] = {}
    pos = 0.0
    for n in notes:
        d[round(pos, 6)] = n
        pos += n.duration
    return d


def _last_midi(notes: list[JianpuNote], fallback: float) -> float:
    """返回列表中最后一个非休止符的 MIDI 音高，若无则返回 fallback。"""
    for n in reversed(notes):
        if not n.is_rest and n.midi is not None:
            return float(n.midi)
    return fallback


def _merge_measure_pair(
    m_a: list[JianpuNote],
    m_b: list[JianpuNote],
    bar_length: float,
    prev_midi: float,
) -> tuple[list[JianpuNote], float]:
    """对两个并行小节的音符列表进行偏移对齐融合。

    步骤
    ----
    1. 各自构建 offset→JianpuNote 字典
    2. 取两字典所有偏移的并集作为统一时间网格
    3. 对每个偏移：只有一方有音符时直接取用；两方均有时调用 _pick_better_note
    4. 修正最终时值，确保不溢出小节线，不超出 bar_length

    Returns
    -------
    (merged_notes, last_non_rest_midi)
    """
    # 两者均为空 → 全休止符小节（使用合法时值，如 bar_length=4.0 → 全音符休止）
    if not m_a and not m_b:
        from .jianpu_core import normalize_jianpu_duration
        rest_dur = normalize_jianpu_duration(bar_length)
        return [JianpuNote('0', '', 0, 0, rest_dur, 0, None, True)], prev_midi

    if not m_a:
        return m_b, _last_midi(m_b, prev_midi)
    if not m_b:
        return m_a, _last_midi(m_a, prev_midi)

    off_a = _to_offset_map(m_a)
    off_b = _to_offset_map(m_b)

    # 统一时间网格（两方偏移的并集）
    all_offsets = sorted(set(off_a) | set(off_b))

    merged_with_offsets: list[tuple[float, JianpuNote]] = []
    for off in all_offsets:
        note_a = off_a.get(off)
        note_b = off_b.get(off)
        if note_a is None and note_b is None:
            continue
        elif note_a is None:
            chosen = note_b
        elif note_b is None:
            chosen = note_a
        else:
            chosen = _pick_better_note(note_a, note_b, prev_midi)

        if chosen is not None:
            if not chosen.is_rest:
                prev_midi = chosen.midi or prev_midi
            merged_with_offsets.append((off, chosen))

    if not merged_with_offsets:
        from .jianpu_core import normalize_jianpu_duration
        rest_dur = normalize_jianpu_duration(bar_length)
        return [JianpuNote('0', '', 0, 0, rest_dur, 0, None, True)], prev_midi

    # 修正时值：确保不超过下一个音符的起始偏移，且不超过 bar_length，
    # 并将非标准时值（如 1.25）归一化为最近的允许值以防止 jianpu-ly barcheck 错误
    result: list[JianpuNote] = []
    for i, (off, note) in enumerate(merged_with_offsets):
        next_off = merged_with_offsets[i + 1][0] if i + 1 < len(merged_with_offsets) else bar_length
        available = max(round(next_off - off, 6), 0.125)
        if note.duration > available + 0.01 or note.duration not in (
            4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.375, 0.25, 0.1875, 0.125
        ):
            note = _clone_note_with_duration(note, min(note.duration, available))
        result.append(note)

    return result, _last_midi(result, prev_midi)


# ──────────────────────────────────────────────────────────────────────────────
# 小节列表对齐与融合
# ──────────────────────────────────────────────────────────────────────────────

def align_and_merge_measures(
    measures_oemer: list[list[JianpuNote]],
    measures_aud: list[list[JianpuNote]],
    bar_length: float,
) -> list[list[JianpuNote]]:
    """按索引对齐两个小节列表并逐小节融合。

    对齐策略
    --------
    · 小节数差异 ≤ 40%：按索引对齐，较短的用空小节补齐后融合。
    · 小节数差异 > 40%：说明某引擎大量漏检/多检，直接使用小节数更多的结果，
      不尝试强行对齐（强制对齐会产生音符位置错位）。

    Returns
    -------
    融合后的小节列表。
    """
    len_o = len(measures_oemer)
    len_a = len(measures_aud)

    if len_o == 0 and len_a == 0:
        return []
    if len_o == 0:
        log_message('[融合] Oemer 小节列表为空，使用 Audiveris 结果。', logging.WARNING)
        return measures_aud
    if len_a == 0:
        log_message('[融合] Audiveris 小节列表为空，使用 Oemer 结果。', logging.WARNING)
        return measures_oemer

    # 差异过大时直接用更完整的引擎
    max_len = max(len_o, len_a)
    diff_ratio = abs(len_o - len_a) / max_len
    if diff_ratio > 0.40:
        if len_o >= len_a:
            log_message(
                f'[融合] 小节数差异过大（Oemer={len_o}, Audiveris={len_a}, 差异{diff_ratio:.0%}）'
                f'，直接使用 Oemer 结果。',
                logging.WARNING,
            )
            return measures_oemer
        else:
            log_message(
                f'[融合] 小节数差异过大（Oemer={len_o}, Audiveris={len_a}, 差异{diff_ratio:.0%}）'
                f'，直接使用 Audiveris 结果。',
                logging.WARNING,
            )
            return measures_aud

    # 对齐：较短的补空小节
    if diff_ratio > 0:
        log_message(
            f'[融合] 小节数略有差异（Oemer={len_o}, Audiveris={len_a}，差异{diff_ratio:.0%}），'
            f'按索引对齐，较短序列用空小节补齐。',
            logging.DEBUG,
        )
    while len(measures_oemer) < max_len:
        measures_oemer.append([])
    while len(measures_aud) < max_len:
        measures_aud.append([])

    merged: list[list[JianpuNote]] = []
    prev_midi = 62.0  # 从 D4 附近开始旋律平滑度估算

    for m_o, m_a in zip(measures_oemer, measures_aud):
        merged_m, prev_midi = _merge_measure_pair(m_o, m_a, bar_length, prev_midi)
        merged.append(merged_m)

    return merged


# ──────────────────────────────────────────────────────────────────────────────
# 公共入口：融合两个 MusicXML 文件
# ──────────────────────────────────────────────────────────────────────────────

def merge_dual_omr_results(
    mxl_oemer: Path,
    mxl_audiveris: Path,
) -> Optional[tuple[list[list[JianpuNote]], str, str, str]]:
    """解析两个 MusicXML 文件并将识别结果融合为一组简谱小节。

    Parameters
    ----------
    mxl_oemer     : Oemer 引擎输出的 MusicXML 文件路径。
    mxl_audiveris : Audiveris 引擎输出的 MusicXML 文件路径。

    Returns
    -------
    (merged_measures, time_sig, tonic_name, stats_description)
    解析或融合完全失败时返回 None。
    """
    from .jianpu_core import _get_first_note_tonic, extract_jianpu_measures

    def _parse_one(mxl_path: Path):
        """解析单个 MusicXML，返回 (measures, time_sig, tonic_name, bar_length) 或 None。"""
        try:
            from music21 import converter as _conv, stream as m21stream
            from music21.meter.base import TimeSignature as _TS
            score = _conv.parse(str(mxl_path))
            tonic_pc, tonic_name = _get_first_note_tonic(score)
            measures, time_sig = extract_jianpu_measures(score, tonic_pc)
            # 提取小节时值（bar_length），兼容 Score / Part 两种结构
            parts = getattr(score, 'parts', None)
            flat_stream = parts[0] if parts else score.flatten()  # type: ignore[index]
            ts_list = list(flat_stream.recurse().getElementsByClass(_TS))
            bar_len = (
                float(getattr(ts_list[0].barDuration, 'quarterLength', 4.0))
                if ts_list else 4.0
            )
            return measures, time_sig, tonic_name, bar_len
        except Exception as exc:
            log_message(f'[融合] 解析 {mxl_path.name} 失败: {exc}', logging.WARNING)
            return None

    result_oemer = _parse_one(mxl_oemer)
    result_aud   = _parse_one(mxl_audiveris)

    # 任一失败时退化为单引擎
    if result_oemer is None and result_aud is None:
        log_message('[融合] 两个引擎的 MusicXML 均无法解析，融合失败。', logging.ERROR)
        return None

    if result_oemer is None:
        log_message('[融合] Oemer MusicXML 无法解析，使用 Audiveris 单引擎结果。', logging.WARNING)
        assert result_aud is not None
        m, ts, tonic, _ = result_aud
        return m, ts, tonic, f'Audiveris 单引擎（Oemer 解析失败），{len(m)} 小节'

    if result_aud is None:
        log_message('[融合] Audiveris MusicXML 无法解析，使用 Oemer 单引擎结果。', logging.WARNING)
        m, ts, tonic, _ = result_oemer
        return m, ts, tonic, f'Oemer 单引擎（Audiveris 解析失败），{len(m)} 小节'

    measures_o, time_sig_o, tonic_o, bar_len_o = result_oemer
    measures_a, time_sig_a, tonic_a, bar_len_a = result_aud

    # 以 Oemer 的调号和拍号为基准（图片识别主力引擎）
    tonic    = tonic_o
    time_sig = time_sig_o
    bar_len  = bar_len_o

    log_message(
        f'[融合] 开始双引擎融合：Oemer={len(measures_o)} 小节, '
        f'Audiveris={len(measures_a)} 小节, 拍号={time_sig}'
    )

    merged = align_and_merge_measures(measures_o, measures_a, bar_len)
    total_notes = sum(len(m) for m in merged)
    stats = (
        f'双引擎融合（Oemer {len(measures_o)} ×Audiveris {len(measures_a)} → '
        f'融合 {len(merged)} 小节，{total_notes} 音符单元）'
    )
    log_message(f'[融合] {stats}')
    return merged, time_sig, tonic, stats
