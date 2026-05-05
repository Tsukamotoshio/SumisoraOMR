# core/tie_reconstruction.py — 延音线（tie）后处理重建
"""针对 homr 等不输出 tie 符号的 OMR 引擎，在 MusicXML 后处理阶段重建延音线。

算法分两层：
  1. 确定性规则（优先级最高，按序匹配即停）
     A. explicit_tie_flag       → 必然延音
     B. 不同声部               → 必然非延音
     C. 断音装饰（staccato/accent 在第二音符上）→ 必然非延音
     D. 歌词对齐（第二音符带歌词）→ 必然非延音
     E. slur 存在但无 tie 符号  → 计入启发式 -2，不作为硬规则

  2. 启发式加权评分（剩余对）
     跨小节:              +3
     时值合并对齐半音符边界: +2
     同声部无装饰:         +1
     第二音符有装饰/动态:   -3
     beam 组边界:         -2
     slur 存在:           -2
     阈值: score>=2 → tie; score<=-2 → no_tie; else → ambiguous（保守不写）

公开 API
--------
reconstruct_ties_in_musicxml(xml_path: Path) -> int
    原地修改 .musicxml/.xml 文件，返回写入的 tie 对数。

reconstruct_ties_in_mxl(mxl_path: Path) -> int
    对 .mxl（ZIP）内所有 XML 原地修改，返回总 tie 对数。
"""
from __future__ import annotations

import logging
import shutil
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

LOGGER = logging.getLogger(__name__)

# ─── 音高转换 ────────────────────────────────────────────────────────────────

_STEP_SEMITONE: dict[str, int] = {
    'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11,
}


def _pitch_to_midi(step: str, alter: float, octave: int) -> int:
    """MusicXML pitch 字段 → MIDI 音高数（C4 = 60）。"""
    return (octave + 1) * 12 + _STEP_SEMITONE.get(step.upper(), 0) + round(alter)


# ─── 启发式权重 ───────────────────────────────────────────────────────────────

_W_CROSS_MEASURE   =  3   # 跨小节
_W_BEAT_ALIGN      =  2   # 合并时值对齐半音符边界
_W_CLEAN_CONTEXT   =  1   # 同声部且无任何装饰
_W_ARTICULATION    = -3   # 第二音符含装饰或动态
_W_BEAM_BOUNDARY   = -2   # beam 组边界（独立发音证据）
_W_SLUR_PRESENT    = -2   # slur 存在（非 tie 的连线）

_THRESHOLD_TIE     =  2   # score >= 2 → 必然延音
_THRESHOLD_NON_TIE = -2   # score <= -2 → 必然非延音


# ─── 决策枚举 ────────────────────────────────────────────────────────────────

class TieDecision(Enum):
    DEFINITE_TIE     = 'tie'
    DEFINITE_NON_TIE = 'no_tie'
    AMBIGUOUS        = 'ambiguous'


# ─── 音符节点数据结构 ─────────────────────────────────────────────────────────

@dataclass
class _NoteNode:
    """规范化的音符节点，供 tie 决策使用。"""
    element: ET.Element      # 对应的 <note> XML 元素
    part_id: str
    pitch_midi: int          # MIDI 音高（0-127）
    start_tick: int          # 绝对 tick（从乐曲开头）
    duration_ticks: int      # 时值（ticks）
    voice_id: str            # 声部标识
    measure_index: int       # 小节索引（0-based）
    beat_pos_ticks: int      # 在小节内的 tick 偏移
    measure_length_ticks: int
    divisions: int           # ticks / 四分音符
    has_staccato: bool       = False
    has_accent: bool         = False
    has_lyrics: bool         = False
    has_tie_start: bool      = False   # 已有 <tie type="start">
    has_tie_stop: bool       = False   # 已有 <tie type="stop">
    has_slur_start: bool     = False
    beam_end: bool           = False   # beam 组结束（此音符）
    beam_begin: bool         = False   # beam 组开始（此音符）

    @property
    def end_tick(self) -> int:
        return self.start_tick + self.duration_ticks


# ─── XML 命名空间辅助 ─────────────────────────────────────────────────────────

def _get_ns(root: ET.Element) -> str:
    """提取根元素的命名空间前缀，如 '{http://...}' 或 ''。"""
    tag = root.tag
    if tag.startswith('{'):
        return tag[: tag.index('}') + 1]
    return ''


def _txt(elem: Optional[ET.Element], default: str = '') -> str:
    if elem is None:
        return default
    return (elem.text or default).strip()


def _local(tag: str) -> str:
    """去除命名空间前缀，返回本地标签名。"""
    return tag.split('}')[-1] if '}' in tag else tag


# ─── MusicXML 解析 ────────────────────────────────────────────────────────────

def _parse_score(root: ET.Element, ns: str) -> list[_NoteNode]:
    """从 score-partwise 根元素解析所有非休止音符，返回按绝对 tick 排序的列表。"""
    notes: list[_NoteNode] = []

    for part_elem in root.findall(f'{ns}part'):
        part_id = part_elem.get('id', '')
        divisions = 1
        beats = 4
        beat_type = 4
        cumulative_tick = 0

        for meas_idx, measure_elem in enumerate(part_elem.findall(f'{ns}measure')):
            measure_start_tick = cumulative_tick
            # 小节长度（ticks）= beats × (4 × divisions / beat_type)
            measure_length_ticks = beats * 4 * divisions // beat_type

            # 用于 chord 音符的上一非 chord 起始 tick
            last_start_tick = cumulative_tick

            for child in measure_elem:
                loc = _local(child.tag)

                if loc == 'attributes':
                    div_e = child.find(f'{ns}divisions')
                    if div_e is not None and div_e.text:
                        divisions = int(div_e.text)
                    time_e = child.find(f'{ns}time')
                    if time_e is not None:
                        b_e = time_e.find(f'{ns}beats')
                        bt_e = time_e.find(f'{ns}beat-type')
                        if b_e is not None and b_e.text:
                            beats = int(b_e.text)
                        if bt_e is not None and bt_e.text:
                            beat_type = int(bt_e.text)
                    measure_length_ticks = beats * 4 * divisions // beat_type

                elif loc == 'note':
                    is_chord = child.find(f'{ns}chord') is not None
                    is_rest  = child.find(f'{ns}rest') is not None

                    dur_e = child.find(f'{ns}duration')
                    duration_ticks = int(_txt(dur_e, '0')) if dur_e is not None else 0

                    if is_chord:
                        note_start_tick = last_start_tick
                    else:
                        note_start_tick  = cumulative_tick
                        last_start_tick  = cumulative_tick
                        cumulative_tick += duration_ticks

                    if is_rest:
                        continue

                    pitch_e = child.find(f'{ns}pitch')
                    if pitch_e is None:
                        continue

                    step   = _txt(pitch_e.find(f'{ns}step'),   'C')
                    alter  = float(_txt(pitch_e.find(f'{ns}alter'), '0') or '0')
                    octave = int(_txt(pitch_e.find(f'{ns}octave'), '4') or '4')
                    midi   = _pitch_to_midi(step, alter, octave)

                    voice_id = _txt(child.find(f'{ns}voice'), '1')

                    # — 装饰音 —
                    has_staccato = has_accent = has_lyrics = False
                    has_tie_start = has_tie_stop = has_slur_start = False
                    beam_begin = beam_end = False

                    notations_e = child.find(f'{ns}notations')
                    if notations_e is not None:
                        art_e = notations_e.find(f'{ns}articulations')
                        if art_e is not None:
                            has_staccato = art_e.find(f'{ns}staccato') is not None
                            has_accent   = art_e.find(f'{ns}accent')   is not None
                        for slur_e in notations_e.findall(f'{ns}slur'):
                            if slur_e.get('type') == 'start':
                                has_slur_start = True
                        for tied_e in notations_e.findall(f'{ns}tied'):
                            t = tied_e.get('type', '')
                            if t == 'start':
                                has_tie_start = True
                            elif t == 'stop':
                                has_tie_stop = True

                    # <tie> 作为 <note> 直接子元素
                    for tie_e in child.findall(f'{ns}tie'):
                        t = tie_e.get('type', '')
                        if t == 'start':
                            has_tie_start = True
                        elif t == 'stop':
                            has_tie_stop = True

                    # 歌词
                    has_lyrics = child.find(f'{ns}lyric') is not None

                    # beam
                    for beam_e in child.findall(f'{ns}beam'):
                        if beam_e.get('number', '1') == '1':
                            val = (beam_e.text or '').strip()
                            if val == 'begin':
                                beam_begin = True
                            elif val == 'end':
                                beam_end = True

                    beat_pos_ticks = note_start_tick - measure_start_tick

                    notes.append(_NoteNode(
                        element=child,
                        part_id=part_id,
                        pitch_midi=midi,
                        start_tick=note_start_tick,
                        duration_ticks=duration_ticks,
                        voice_id=voice_id,
                        measure_index=meas_idx,
                        beat_pos_ticks=beat_pos_ticks,
                        measure_length_ticks=measure_length_ticks,
                        divisions=divisions,
                        has_staccato=has_staccato,
                        has_accent=has_accent,
                        has_lyrics=has_lyrics,
                        has_tie_start=has_tie_start,
                        has_tie_stop=has_tie_stop,
                        has_slur_start=has_slur_start,
                        beam_end=beam_end,
                        beam_begin=beam_begin,
                    ))

                elif loc == 'backup':
                    dur_e = child.find(f'{ns}duration')
                    if dur_e is not None and dur_e.text:
                        cumulative_tick -= int(dur_e.text)

                elif loc == 'forward':
                    dur_e = child.find(f'{ns}duration')
                    if dur_e is not None and dur_e.text:
                        cumulative_tick += int(dur_e.text)

    notes.sort(key=lambda n: (n.part_id, n.voice_id, n.start_tick, n.pitch_midi))
    return notes


# ─── 确定性规则 ───────────────────────────────────────────────────────────────

def _apply_rules(a: _NoteNode, b: _NoteNode) -> Optional[TieDecision]:
    """按优先级应用确定性规则，匹配即返回；否则返回 None 进入启发式评分。"""

    # 规则 A：已存在显式 tie → 必然延音
    if a.has_tie_start:
        return TieDecision.DEFINITE_TIE

    # 规则 B：不同声部 → 必然非延音
    if a.voice_id != b.voice_id:
        return TieDecision.DEFINITE_NON_TIE

    # 规则 C：第二音符含 staccato 或 accent → 必然非延音
    if b.has_staccato or b.has_accent:
        return TieDecision.DEFINITE_NON_TIE

    # 规则 D：第二音符带歌词 → 必然非延音
    if b.has_lyrics:
        return TieDecision.DEFINITE_NON_TIE

    # 规则 E（软规则）：slur 存在但无 tie → 计入启发式 -2，此处不作硬判断
    return None


# ─── 启发式评分 ───────────────────────────────────────────────────────────────

def _heuristic_score(a: _NoteNode, b: _NoteNode) -> float:
    """计算 (a, b) 对的延音可能性分数。"""
    score = 0.0

    # 跨小节：强延音指标
    if a.measure_index != b.measure_index:
        score += _W_CROSS_MEASURE

    # 合并时值对齐到半音符边界（2 × divisions ticks）
    combined = a.duration_ticks + b.duration_ticks
    half_note_ticks = 2 * a.divisions
    if half_note_ticks > 0 and combined % half_note_ticks == 0:
        score += _W_BEAT_ALIGN

    # 同声部无任何装饰
    clean = not (a.has_staccato or a.has_accent or b.has_staccato or b.has_accent)
    if clean:
        score += _W_CLEAN_CONTEXT

    # 第二音符含装饰（此处对 b 二次检查，与确定性规则 C 重叠但权重独立）
    if b.has_staccato or b.has_accent:
        score += _W_ARTICULATION

    # beam 组边界（a 结束一个 beam，b 开始一个新 beam）
    if a.beam_end and b.beam_begin:
        score += _W_BEAM_BOUNDARY

    # slur 存在（规则 E 的软惩罚）
    if a.has_slur_start:
        score += _W_SLUR_PRESENT

    return score


# ─── 综合决策 ─────────────────────────────────────────────────────────────────

def _decide(a: _NoteNode, b: _NoteNode) -> TieDecision:
    """对相邻同音高同声部音符对作出延音线判断。"""
    decision = _apply_rules(a, b)
    if decision is not None:
        return decision

    score = _heuristic_score(a, b)
    LOGGER.debug(
        'Tie 候选: midi=%d tick=%d→%d 小节=%d→%d score=%.1f',
        a.pitch_midi, a.start_tick, b.start_tick,
        a.measure_index, b.measure_index, score,
    )

    if score >= _THRESHOLD_TIE:
        return TieDecision.DEFINITE_TIE
    if score <= _THRESHOLD_NON_TIE:
        return TieDecision.DEFINITE_NON_TIE
    return TieDecision.AMBIGUOUS


# ─── XML 写入 ─────────────────────────────────────────────────────────────────

def _has_tie_elem(note_elem: ET.Element, tie_type: str, ns: str) -> bool:
    for tie_e in note_elem.findall(f'{ns}tie'):
        if tie_e.get('type') == tie_type:
            return True
    return False


def _has_tied_in_notations(note_elem: ET.Element, tied_type: str, ns: str) -> bool:
    notations_e = note_elem.find(f'{ns}notations')
    if notations_e is None:
        return False
    for tied_e in notations_e.findall(f'{ns}tied'):
        if tied_e.get('type') == tied_type:
            return True
    return False


def _add_tie_to_note(note_elem: ET.Element, tie_type: str, ns: str) -> None:
    """为 <note> 元素添加 <tie type="..."/> 及 <notations><tied type="..."/>。"""
    if _has_tie_elem(note_elem, tie_type, ns):
        return  # 已存在，跳过

    # 找到 <notations> 的插入位置（<tie> 应位于 <notations> 之前）
    children_tags = [_local(c.tag) for c in note_elem]
    tie_elem = ET.Element(f'{ns}tie')
    tie_elem.set('type', tie_type)
    try:
        ins = children_tags.index('notations')
        note_elem.insert(ins, tie_elem)
    except ValueError:
        note_elem.append(tie_elem)

    # 在 <notations> 内添加 <tied>
    if not _has_tied_in_notations(note_elem, tie_type, ns):
        notations_e = note_elem.find(f'{ns}notations')
        if notations_e is None:
            notations_e = ET.SubElement(note_elem, f'{ns}notations')
        tied_elem = ET.Element(f'{ns}tied')
        tied_elem.set('type', tie_type)
        notations_e.insert(0, tied_elem)


# ─── 公开 API ─────────────────────────────────────────────────────────────────

def reconstruct_ties_in_musicxml(xml_path: Path) -> int:
    """解析 MusicXML 文件，重建延音线，原地写回。返回新增的 tie 对数。"""
    try:
        tree = ET.parse(str(xml_path))
    except ET.ParseError as exc:
        LOGGER.warning('reconstruct_ties: XML 解析失败 %s: %s', xml_path.name, exc)
        return 0

    root = tree.getroot()
    ns = _get_ns(root)

    # 注册命名空间以避免写回时出现 ns0: 前缀
    if ns:
        ET.register_namespace('', ns[1:-1])

    notes = _parse_score(root, ns)

    # 按 (part_id, voice_id, pitch_midi) 分组，同组内按 start_tick 排序
    groups: dict[tuple[str, str, int], list[_NoteNode]] = defaultdict(list)
    for n in notes:
        groups[(n.part_id, n.voice_id, n.pitch_midi)].append(n)

    tie_count = 0
    for group_notes in groups.values():
        group_notes.sort(key=lambda n: n.start_tick)
        for i in range(len(group_notes) - 1):
            a = group_notes[i]
            b = group_notes[i + 1]

            # 仅处理时间上紧邻的对（a 结束 = b 开始）
            if a.end_tick != b.start_tick:
                continue
            # 同一 tick 的重复音高（记谱错误）跳过
            if a.start_tick == b.start_tick:
                continue

            decision = _decide(a, b)

            if decision is TieDecision.DEFINITE_TIE:
                _add_tie_to_note(a.element, 'start', ns)
                _add_tie_to_note(b.element, 'stop',  ns)
                tie_count += 1
                LOGGER.debug(
                    'reconstruct_ties: 写入 tie midi=%d tick=%d→%d',
                    a.pitch_midi, a.start_tick, b.start_tick,
                )
            elif decision is TieDecision.AMBIGUOUS:
                LOGGER.debug(
                    'reconstruct_ties: AMBIGUOUS midi=%d tick=%d→%d（跳过）',
                    a.pitch_midi, a.start_tick, b.start_tick,
                )

    if tie_count > 0:
        tree.write(str(xml_path), encoding='unicode', xml_declaration=True)
        LOGGER.info('reconstruct_ties: %s 新增 %d 对延音线', xml_path.name, tie_count)
    else:
        LOGGER.debug('reconstruct_ties: %s 未新增延音线', xml_path.name)

    return tie_count


def reconstruct_ties_in_mxl(mxl_path: Path) -> int:
    """对 .mxl 压缩包内所有 XML 文件重建延音线，原地重新打包。返回总 tie 对数。

    若 mxl_path 后缀不是 .mxl，则直接调用 reconstruct_ties_in_musicxml。
    """
    if not mxl_path.exists():
        return 0
    if mxl_path.suffix.lower() != '.mxl':
        return reconstruct_ties_in_musicxml(mxl_path)

    tmp_dir = mxl_path.parent / f'_tiefix_{mxl_path.stem}'
    total = 0
    try:
        with zipfile.ZipFile(mxl_path, 'r') as zin:
            zin.extractall(tmp_dir)

        for xml_file in sorted(tmp_dir.rglob('*.xml')):
            total += reconstruct_ties_in_musicxml(xml_file)

        if total > 0:
            with zipfile.ZipFile(mxl_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
                for f in tmp_dir.rglob('*'):
                    if f.is_file():
                        zout.write(f, f.relative_to(tmp_dir))
            LOGGER.info(
                'reconstruct_ties_in_mxl: 已重新打包 %s（%d 对延音线）',
                mxl_path.name, total,
            )
    except Exception as exc:
        LOGGER.warning('reconstruct_ties_in_mxl 失败: %s', exc)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return total
