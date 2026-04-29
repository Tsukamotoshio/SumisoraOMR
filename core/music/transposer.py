# core/transposer.py — MusicXML 移调算法模块
# 独立的 XML / 音频移调，使用 music21 处理升降号补偿。
# 不依赖任何 GUI 库，可被任意调用方导入。
#
# 公开 API:
#   transpose_musicxml(src, dst, semitones=None, from_key=None, to_key=None) -> Path
#   get_transposition_semitones(from_key, to_key) -> int
#   detect_key_from_musicxml(mxl_path) -> str
#   strip_slurs_ties_from_xml(xml_path) -> Path  （CURVES 崩溃救援）

from __future__ import annotations

import logging
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LOGGER = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 内部辅助
# ─────────────────────────────────────────────────────────────────────────────

def _parse_key_name(key_str: str) -> str:
    """将用户输入（如 'F', 'Bb', 'F#'）规范化为 music21 可识别的音名。

    jianpu 编辑器中常用 'b' 替代 '-' 表示降号。
    """
    # 将 'b' 后缀转为 music21 的 '-' 降号（仅处理 [A-G]b 格式）
    normalized = re.sub(r'^([A-Ga-g])b$', lambda m: m.group(1) + '-', key_str.strip())
    return normalized


# 内置音名 → 音级映射，不依赖 music21，用于快速半音计算
_PITCH_CLASS: dict[str, int] = {
    'C': 0, 'B#': 0,
    'C#': 1, 'Db': 1,
    'D': 2,
    'D#': 3, 'Eb': 3,
    'E': 4, 'Fb': 4,
    'F': 5, 'E#': 5,
    'F#': 6, 'Gb': 6,
    'G': 7,
    'G#': 8, 'Ab': 8,
    'A': 9,
    'A#': 10, 'Bb': 10,
    'B': 11, 'Cb': 11,
}


def _key_to_pitch_class(key_str: str) -> int:
    """将调名字符串转为音级（0-11），不依赖 music21。"""
    k = key_str.strip()
    if k in _PITCH_CLASS:
        return _PITCH_CLASS[k]
    # 尝试 music21 作为回退
    try:
        from music21 import pitch as m21pitch
        return m21pitch.Pitch(_parse_key_name(k)).pitchClass
    except Exception:
        return 0


def _semitone_diff(from_pitch_class: int, to_pitch_class: int) -> int:
    """返回从 from 到 to 的最短上行半音数（0-11）。"""
    diff = (to_pitch_class - from_pitch_class) % 12
    return diff


# ─────────────────────────────────────────────────────────────────────────────
# 公开 API
# ─────────────────────────────────────────────────────────────────────────────

# 按五度圈排列的标准调名（与 MuseScore 一致，-7 到 +7）
KEYS_BY_CIRCLE_OF_FIFTHS = [
    'Cb', 'Gb', 'Db', 'Ab', 'Eb', 'Bb', 'F',
    'C',
    'G', 'D', 'A', 'E', 'B', 'F#', 'C#',
]


@dataclass(frozen=True)
class _Interval:
    name: str       # 中文名称（显示用）
    diatonic: int   # 0-7
    chromatic: int  # 0-12


# 26 个标准音程（与 MuseScore allIntervals 对应）
INTERVALS: list[_Interval] = [
    _Interval('纯一度', 0,  0),
    _Interval('增一度', 0,  1),
    _Interval('减二度', 1,  0),
    _Interval('小二度', 1,  1),
    _Interval('大二度', 1,  2),
    _Interval('增二度', 1,  3),
    _Interval('减三度', 2,  2),
    _Interval('小三度', 2,  3),
    _Interval('大三度', 2,  4),
    _Interval('增三度', 2,  5),
    _Interval('减四度', 3,  4),
    _Interval('纯四度', 3,  5),
    _Interval('增四度', 3,  6),
    _Interval('减五度', 4,  6),
    _Interval('纯五度', 4,  7),
    _Interval('增五度', 4,  8),
    _Interval('减六度', 5,  7),
    _Interval('小六度', 5,  8),
    _Interval('大六度', 5,  9),
    _Interval('增六度', 5, 10),
    _Interval('减七度', 6,  9),
    _Interval('小七度', 6, 10),
    _Interval('大七度', 6, 11),
    _Interval('增七度', 6, 12),
    _Interval('减八度', 7, 11),
    _Interval('纯八度', 7, 12),
]

_INTERVAL_BY_NAME: dict[str, _Interval] = {iv.name: iv for iv in INTERVALS}

# 全音移调度数（二度-七度）
DIATONIC_DEGREES: list[tuple[str, int]] = [
    ('二度', 1),
    ('三度', 2),
    ('四度', 3),
    ('五度', 4),
    ('六度', 5),
    ('七度', 6),
]


def key_display_cn(key: str) -> str:
    """将调名转换为中文大调显示（如 'Bb' → 'B♭大调'）。"""
    return key.replace('#', '♯').replace('b', '♭') + '大调'


def get_interval_semitones(interval_name: str, direction: str = 'up') -> int:
    """根据音程名称和方向返回带符号半音数（正=向上，负=向下）。"""
    iv = _INTERVAL_BY_NAME.get(interval_name)
    if iv is None:
        return 0
    c = iv.chromatic
    if direction == 'down':
        return -c
    elif direction == 'closest':
        if c == 0:
            return 0
        return c if c <= 6 else c - 12
    else:  # 'up'
        return c


def get_interval_diatonic(interval_name: str, direction: str = 'up') -> Optional[int]:
    """根据音程名称和方向返回带符号音阶步数（正=向上，负=向下）。

    与 get_interval_semitones 方向逻辑保持一致，供 diatonic-aware 移调使用。
    返回 None 表示未知音程。
    """
    iv = _INTERVAL_BY_NAME.get(interval_name)
    if iv is None:
        return None
    d = iv.diatonic
    if direction == 'down':
        return -d
    elif direction == 'closest':
        c = iv.chromatic
        if c == 0:
            return 0
        return d if c <= 6 else -d
    else:  # 'up'
        return d


def get_transposition_semitones(
    from_key: str,
    to_key: str,
    direction: str = 'closest',
) -> int:
    """计算从 from_key 到 to_key 的移调半音数。

    Parameters
    ----------
    from_key  : 原调音名（如 'C', 'Bb', 'F#'）
    to_key    : 目标调音名
    direction : 'up'     — 始终向上移调（0..+11），同调时 +12
                'down'   — 始终向下移调（0..-11），同调时 -12
                'closest'— 选择距离最近方向（-6..+6），半音差 >6 时向下；
                            同调时返回 0（不动），与 MuseScore 行为一致

    Returns
    -------
    带符号的半音数（正=向上，负=向下，0=不动）
    """
    try:
        pc_from = _key_to_pitch_class(from_key)
        pc_to   = _key_to_pitch_class(to_key)
        chromatic = _semitone_diff(pc_from, pc_to)  # 0-11 上行

        if direction == 'up':
            # 同调 + UP：升一个八度
            return chromatic if chromatic != 0 else 12
        elif direction == 'down':
            # 向下：变为负数；同调 + DOWN：降一个八度
            return (chromatic - 12) if chromatic != 0 else -12
        else:  # 'closest'（MuseScore 默认）
            # 同调：保持不动
            if chromatic == 0:
                return 0
            # chromatic > 6 时向下更近
            if chromatic > 6:
                return chromatic - 12
            return chromatic
    except Exception as exc:
        LOGGER.warning('get_transposition_semitones: 无法解析音名 %s → %s: %s', from_key, to_key, exc)
        return 0


def _major_key_from_fifths(fifths: int) -> str:
    mapping = {
        -7: 'Cb',
        -6: 'Gb',
        -5: 'Db',
        -4: 'Ab',
        -3: 'Eb',
        -2: 'Bb',
        -1: 'F',
         0: 'C',
         1: 'G',
         2: 'D',
         3: 'A',
         4: 'E',
         5: 'B',
         6: 'F#',
         7: 'C#',
    }
    return mapping.get(fifths, 'C')


def _parse_musicxml_key_signature(mxl_path: Path) -> Optional[str]:
    try:
        import zipfile
        if mxl_path.suffix.lower() == '.mxl':
            with zipfile.ZipFile(mxl_path, 'r') as z:
                xml_names = [n for n in z.namelist() if n.lower().endswith('.xml')]
                if not xml_names:
                    return None
                text = z.read(xml_names[0]).decode('utf-8', errors='ignore')
        else:
            text = mxl_path.read_text(encoding='utf-8', errors='ignore')
        import xml.etree.ElementTree as ET
        root = ET.fromstring(text)
        ns = ''
        if root.tag.startswith('{'):
            ns = root.tag.split('}')[0] + '}'
        attributes = root.find(f'.//{ns}attributes')
        if attributes is None:
            return None
        key_elem = attributes.find(f'{ns}key')
        if key_elem is None:
            return None
        fifths_elem = key_elem.find(f'{ns}fifths')
        mode_elem = key_elem.find(f'{ns}mode')
        if fifths_elem is None or fifths_elem.text is None:
            return None
        fifths = int(fifths_elem.text.strip())
        if mode_elem is None or not mode_elem.text:
            return _major_key_from_fifths(fifths)
        mode = mode_elem.text.strip().lower()
        if mode == 'major':
            return _major_key_from_fifths(fifths)
        if mode == 'minor':
            return _major_key_from_fifths(fifths)
        return _major_key_from_fifths(fifths)
    except Exception:
        return None


def detect_key_from_musicxml(mxl_path: Path) -> str:
    """从 MusicXML 文件中检测第一个调号；若无法检测则返回 'C'。"""
    try:
        explicit = _parse_musicxml_key_signature(mxl_path)
        if explicit is not None:
            return explicit
        from music21 import converter
        score = converter.parse(str(mxl_path))
        keys = score.flatten().getElementsByClass('Key')
        if keys:
            k = keys[0]
            tonic = k.tonic.name.replace('-', 'b')
            return tonic
        # 若没有显式 Key 元素，用 music21 分析
        analyzed = score.analyze('key')
        if analyzed is None or not hasattr(analyzed, 'tonic'):
            return 'C'
        return analyzed.tonic.name.replace('-', 'b')
    except Exception as exc:
        LOGGER.warning('detect_key_from_musicxml 失败 (%s): %s', mxl_path.name, exc)
        return 'C'


def extract_metadata_from_musicxml(mxl_path: Path) -> dict[str, str]:
    """从 MusicXML 文件中提取元数据（标题、作曲家、版权等）。

    Returns
    -------
    dict with keys: 'title', 'composer', 'copyright'（如果找不到则为空字符串）
    """
    result = {'title': '', 'composer': '', 'copyright': ''}
    try:
        from music21 import converter
        score = converter.parse(str(mxl_path))
        if hasattr(score, 'metadata') and score.metadata is not None:
            meta = score.metadata
            if hasattr(meta, 'title') and meta.title:
                result['title'] = str(meta.title).strip()
            if hasattr(meta, 'composer') and meta.composer:
                result['composer'] = str(meta.composer).strip()
            if hasattr(meta, 'copyright') and meta.copyright:
                result['copyright'] = str(meta.copyright).strip()
    except Exception as exc:
        LOGGER.debug('extract_metadata_from_musicxml 失败: %s', exc)
    return result


def _strip_music21_creator(dst: Path) -> None:
    """移除 music21 在写出 MusicXML 时自动注入的各种标识符。

    music21 写出时可能注入：
    - ``<creator type="composer">Music21</creator>``
    - ``<software>music21 v...</software>``（出现在 MuseScore 的乐谱属性中）
    - ``<encoding-description>Music21 ...</encoding-description>``

    本函数对写出的文件（.musicxml 或 .mxl）做 XML 后处理，全部删除。
    """
    import zipfile

    def _clean_xml_bytes(raw: bytes) -> bytes:
        cleaned = raw
        # 1. <creator type="...">Music21</creator>
        cleaned = re.sub(
            rb'<creator[^>]*>\s*[Mm]usic21\s*</creator>[ \t]*\n?',
            b'',
            cleaned,
        )
        # 2. <software>music21 v...</software>（位于 <encoding> 块内）
        cleaned = re.sub(
            rb'<software>[^<]*[Mm]usic21[^<]*</software>[ \t]*\n?',
            b'',
            cleaned,
        )
        # 3. <encoding-description>...music21...</encoding-description>
        cleaned = re.sub(
            rb'<encoding-description>[^<]*[Mm]usic21[^<]*</encoding-description>[ \t]*\n?',
            b'',
            cleaned,
        )
        # 4. 清理因上述删除而产生的空 <encoding></encoding> 块
        cleaned = re.sub(rb'<encoding>\s*</encoding>[ \t]*\n?', b'', cleaned)
        return cleaned

    suffix = dst.suffix.lower()
    if suffix in ('.musicxml', '.xml'):
        raw = dst.read_bytes()
        cleaned = _clean_xml_bytes(raw)
        if cleaned != raw:
            dst.write_bytes(cleaned)
            LOGGER.debug('已移除 music21 标识符: %s', dst.name)
    elif suffix == '.mxl':
        # .mxl 是 ZIP 包，需要重建
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(dst, 'r') as zin, zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.lower().endswith('.xml'):
                    data = _clean_xml_bytes(data)
                zout.writestr(item, data)
        dst.write_bytes(buf.getvalue())
        LOGGER.debug('已移除 music21 标识符 (mxl): %s', dst.name)


def _transpose_xml_bytes(
    raw: bytes,
    semitones: int,
    transpose_key_sig: bool = True,
    diatonic_offset: Optional[int] = None,
) -> bytes:
    """在字节层级对 MusicXML 做移调，仅修改 <pitch> 和 <key>/<fifths> 元素。

    完整保留原始文件结构（声部、休止符、布局、格式头、DOCTYPE 等），
    不经过 music21 的序列化，从根本上消除 round-trip 对声部结构的破坏。

    Parameters
    ----------
    diatonic_offset : 若提供（按音程移调时使用），采用 diatonic-aware 拼写法：
        根据音程的音阶步数（diatonic 字段）确定目标音级字母，再由半音差推算
        升降号，从而正确区分增减音程（如增二度上 C→D#，而非 Eb）。
        不提供时沿用原有的目标调升降号方向拼写法（适合按调移调）。
    """
    if semitones == 0 and diatonic_offset == 0:
        return raw
    if semitones == 0 and diatonic_offset is None:
        return raw

    _STEP_PC: dict[str, int] = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
    # 升号调优先用升号拼写
    _SHARP: dict[int, tuple[str, int]] = {
        0: ('C', 0), 1: ('C', 1), 2: ('D', 0), 3: ('D', 1), 4: ('E', 0),
        5: ('F', 0), 6: ('F', 1), 7: ('G', 0), 8: ('G', 1), 9: ('A', 0),
        10: ('A', 1), 11: ('B', 0),
    }
    # 降号调优先用降号拼写
    _FLAT: dict[int, tuple[str, int]] = {
        0: ('C', 0), 1: ('D', -1), 2: ('D', 0), 3: ('E', -1), 4: ('E', 0),
        5: ('F', 0), 6: ('G', -1), 7: ('G', 0), 8: ('A', -1), 9: ('A', 0),
        10: ('B', -1), 11: ('B', 0),
    }
    # 音级（0-11）→ 大调升降号数（五度圈）
    _PC_TO_FIFTHS: dict[int, int] = {
        0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 1: 7,
        5: -1, 10: -2, 3: -3, 8: -4,
    }
    # 升降号数 → 大调主音音级
    _FIFTHS_TO_PC: dict[int, int] = {
        0: 0, 1: 7, 2: 2, 3: 9, 4: 4, 5: 11, 6: 6, 7: 1,
        -1: 5, -2: 10, -3: 3, -4: 8, -5: 1, -6: 6, -7: 11,
    }
    _ALTER_TO_ACC: dict[int, str] = {
        0: 'natural', 1: 'sharp', -1: 'flat', 2: 'double-sharp', -2: 'flat-flat',
    }
    # diatonic-aware 拼写所需常量
    _STEP_IDX: dict[str, int] = {'C': 0, 'D': 1, 'E': 2, 'F': 3, 'G': 4, 'A': 5, 'B': 6}
    _IDX_STEP: list[str] = ['C', 'D', 'E', 'F', 'G', 'A', 'B']
    _STEP_NAT_CHROM: list[int] = [0, 2, 4, 5, 7, 9, 11]  # C D E F G A B 的自然半音值

    # ── 1. 确定目标调的升降号方向 ────────────────────────────────────────────
    fifths_m = re.search(rb'<fifths>(-?\d+)</fifths>', raw)
    orig_fifths = int(fifths_m.group(1)) if fifths_m else 0
    new_tonic_pc = (_FIFTHS_TO_PC.get(orig_fifths, 0) + semitones) % 12
    new_fifths_main = _PC_TO_FIFTHS.get(new_tonic_pc, 0)
    # 原调用降号时，目标调在模糊音级（C#/Db, F#/Gb）优先选降号版本
    if orig_fifths < 0 and new_fifths_main > 5:
        new_fifths_main = {7: -5, 6: -6}.get(new_fifths_main, new_fifths_main)
    new_fifths_main = max(-7, min(7, new_fifths_main))
    use_flats = new_fifths_main < 0
    spell = _FLAT if use_flats else _SHARP

    def _calc_new_fifths(orig_f: int) -> int:
        pc = (_FIFTHS_TO_PC.get(orig_f, 0) + semitones) % 12
        nf = _PC_TO_FIFTHS.get(pc, 0)
        if orig_f < 0 and nf > 5:
            nf = {7: -5, 6: -6}.get(nf, nf)
        return max(-7, min(7, nf))

    # ── 2. 更新所有 <fifths>（transpose_key_sig=False 时跳过）────────────────
    if transpose_key_sig:
        result = re.sub(
            rb'(<fifths>)(-?\d+)(</fifths>)',
            lambda m: m.group(1) + str(_calc_new_fifths(int(m.group(2)))).encode() + m.group(3),
            raw,
        )
    else:
        result = raw

    # ── 3. 移调所有 <pitch>…</pitch> 块（休止符无 <pitch>，安全跳过） ──────
    def _shift_pitch_block(m: re.Match) -> bytes:
        block = m.group(0)
        step_m = re.search(rb'<step>([A-G])</step>', block)
        oct_m  = re.search(rb'<octave>(\d+)</octave>', block)
        if not step_m or not oct_m:
            return block

        step   = step_m.group(1).decode()
        octave = int(oct_m.group(1))
        alter_m = re.search(rb'<alter>([^<]*)</alter>', block)
        alter = 0.0
        if alter_m:
            try:
                alter = float(alter_m.group(1).strip())
            except ValueError:
                pass

        if diatonic_offset is not None:
            # ── Diatonic-aware 拼写（MuseScore 方式）──────────────────────
            # 以音阶步数确定目标音级字母，再由实际半音差推算升降号。
            # 可正确区分增减音程，如增二度 C→D#（而非 Eb）。
            old_step_idx = _STEP_IDX.get(step, 0)
            old_diat = octave * 7 + old_step_idx
            new_diat = old_diat + diatonic_offset
            new_step_idx = new_diat % 7
            new_oct = new_diat // 7
            new_step = _IDX_STEP[new_step_idx]
            # 原始音符的绝对半音值（MIDI pitch）
            old_chrom = (octave + 1) * 12 + _STEP_NAT_CHROM[old_step_idx] + round(alter)
            new_chrom = old_chrom + semitones
            # 目标音级在该八度的自然半音值
            nat_new_chrom = (new_oct + 1) * 12 + _STEP_NAT_CHROM[new_step_idx]
            new_alter_raw = new_chrom - nat_new_chrom
            # 归一化到 [-2, 2] 范围（处理 B#/Cb 跨八度边界）
            if new_alter_raw > 2:
                new_alter_raw -= 12
            elif new_alter_raw < -2:
                new_alter_raw += 12
            new_alter = new_alter_raw
        else:
            # ── 原有目标调升降号拼写法（按调移调时使用）────────────────────
            pc   = int((_STEP_PC.get(step, 0) + round(alter)) % 12)
            midi = (octave + 1) * 12 + pc + semitones
            new_oct   = midi // 12 - 1
            new_step, new_alter = spell[midi % 12]

        nb = re.sub(rb'(<step>)[A-G](</step>)',
                    lambda mm: mm.group(1) + new_step.encode() + mm.group(2), block)
        nb = re.sub(rb'(<octave>)\d+(</octave>)',
                    lambda mm: mm.group(1) + str(new_oct).encode() + mm.group(2), nb)

        if new_alter == 0:
            nb = re.sub(rb'\s*<alter>[^<]*</alter>', b'', nb)
        else:
            av = str(int(new_alter)).encode()
            if alter_m:
                nb = re.sub(rb'(<alter>)[^<]*(</alter>)',
                            lambda mm: mm.group(1) + av + mm.group(2), nb)
            else:
                # 插入 <alter> 紧跟 </step>（符合 MusicXML 规范顺序）
                nb = re.sub(rb'(</step>)',
                            lambda mm: mm.group(1) + b'<alter>' + av + b'</alter>',
                            nb, count=1)

        # 更新 <accidental>（如有）
        if b'<accidental' in nb:
            acc = _ALTER_TO_ACC.get(int(new_alter), 'natural').encode()
            nb = re.sub(rb'(<accidental[^>]*>)[^<]*(</accidental>)',
                        lambda mm: mm.group(1) + acc + mm.group(2), nb)
        return nb

    result = re.sub(rb'<pitch>.*?</pitch>', _shift_pitch_block, result, flags=re.DOTALL)
    return result


def _transpose_xml_bytes_diatonic(raw: bytes, diatonic_steps: int) -> bytes:
    """全音移调：按音阶度数（相对当前调）移调每个音符，不修改 <fifths>。

    diatonic_steps: 正数=向上，负数=向下。步长基于当前调的音阶音名。
    """
    if diatonic_steps == 0:
        return raw

    _STEP_IDX = {'C': 0, 'D': 1, 'E': 2, 'F': 3, 'G': 4, 'A': 5, 'B': 6}
    _IDX_STEP = ['C', 'D', 'E', 'F', 'G', 'A', 'B']
    _SHARP_ORDER = [3, 0, 4, 1, 5, 2, 6]  # F, C, G, D, A, E, B
    _FLAT_ORDER  = [6, 2, 5, 1, 4, 0, 3]  # B, E, A, D, G, C, F
    _ALTER_TO_ACC: dict[int, str] = {
        0: 'natural', 1: 'sharp', -1: 'flat', 2: 'double-sharp', -2: 'flat-flat',
    }

    fifths_m = re.search(rb'<fifths>(-?\d+)</fifths>', raw)
    fifths = int(fifths_m.group(1)) if fifths_m else 0

    def _scale_alter(step_idx: int) -> int:
        if fifths > 0:
            for i in range(min(fifths, 7)):
                if _SHARP_ORDER[i] == step_idx:
                    return 1
        elif fifths < 0:
            for i in range(min(-fifths, 7)):
                if _FLAT_ORDER[i] == step_idx:
                    return -1
        return 0

    def _shift_pitch_diatonic(m: re.Match) -> bytes:  # type: ignore[type-arg]
        block = m.group(0)
        step_m = re.search(rb'<step>([A-G])</step>', block)
        oct_m  = re.search(rb'<octave>(\d+)</octave>', block)
        if not step_m or not oct_m:
            return block

        old_step  = step_m.group(1).decode()
        octave    = int(oct_m.group(1))
        old_idx   = _STEP_IDX[old_step]

        raw_new   = old_idx + diatonic_steps
        new_idx   = raw_new % 7
        oct_delta = raw_new // 7
        new_step  = _IDX_STEP[new_idx]
        new_octave = octave + oct_delta
        new_alter  = _scale_alter(new_idx)

        nb = re.sub(rb'(<step>)[A-G](</step>)',
                    lambda mm: mm.group(1) + new_step.encode() + mm.group(2), block)
        nb = re.sub(rb'(<octave>)\d+(</octave>)',
                    lambda mm: mm.group(1) + str(new_octave).encode() + mm.group(2), nb)

        if new_alter == 0:
            nb = re.sub(rb'\s*<alter>[^<]*</alter>', b'', nb)
        else:
            av = str(new_alter).encode()
            if re.search(rb'<alter>[^<]*</alter>', nb):
                nb = re.sub(rb'(<alter>)[^<]*(</alter>)',
                            lambda mm: mm.group(1) + av + mm.group(2), nb)
            else:
                nb = re.sub(rb'(</step>)',
                            lambda mm: mm.group(1) + b'<alter>' + av + b'</alter>',
                            nb, count=1)

        if b'<accidental' in nb:
            acc = _ALTER_TO_ACC.get(new_alter, 'natural').encode()
            nb = re.sub(rb'(<accidental[^>]*>)[^<]*(</accidental>)',
                        lambda mm: mm.group(1) + acc + mm.group(2), nb)

        return nb

    return re.sub(rb'<pitch>.*?</pitch>', _shift_pitch_diatonic, raw, flags=re.DOTALL)


def transpose_musicxml(
    src: Path,
    dst: Path,
    *,
    semitones: Optional[int] = None,
    from_key: Optional[str] = None,
    to_key: Optional[str] = None,
    direction: str = 'closest',
    transpose_key_sig: bool = True,
    diatonic_offset: Optional[int] = None,
    progress_callback=None,
) -> Path:
    """将 MusicXML（.mxl / .musicxml）移调并写出到 dst。

    使用 XML 层级移调：仅修改 <pitch> 和 <key>/<fifths> 元素，
    完整保留原始文件结构（声部、休止符位置、布局），不经过 music21 序列化。

    Parameters
    ----------
    src               : 原始 MusicXML 路径（.mxl 或 .musicxml）
    dst               : 输出路径
    semitones         : 直接指定半音偏移（正数向上移调，负数向下）
    from_key / to_key : 从哪个调移到哪个调，例如 from_key='C', to_key='F'
    direction         : 'up' | 'down' | 'closest'（仅在由 from_key/to_key 推算时生效）
    transpose_key_sig : False 时只移调音符，不更新 <fifths> 调号元素
    diatonic_offset   : 音阶步数偏移（由 transpose_by_interval 传入），用于 diatonic-aware 拼写
    progress_callback : 若提供，接受 (float 0.0-1.0) 的回调，用于进度更新
    """
    import io
    import zipfile

    if semitones is None:
        if from_key is None or to_key is None:
            raise ValueError('transpose_musicxml: 须提供 semitones 或 (from_key, to_key)')
        semitones = get_transposition_semitones(from_key, to_key, direction=direction)

    if progress_callback:
        progress_callback(0.1)

    LOGGER.info('XML 层级移调 %+d 个半音: %s', semitones, src.name)

    dst.parent.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower()

    if progress_callback:
        progress_callback(0.4)

    if suffix in ('.musicxml', '.xml'):
        raw = src.read_bytes()
        out = _transpose_xml_bytes(raw, semitones, transpose_key_sig=transpose_key_sig,
                                   diatonic_offset=diatonic_offset)
        dst.write_bytes(out)
    elif suffix == '.mxl':
        buf = io.BytesIO()
        with zipfile.ZipFile(src, 'r') as zin, \
             zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.lower().endswith('.xml'):
                    data = _transpose_xml_bytes(data, semitones, transpose_key_sig=transpose_key_sig,
                                               diatonic_offset=diatonic_offset)
                zout.writestr(item, data)
        dst.write_bytes(buf.getvalue())
    else:
        raise ValueError(f'transpose_musicxml: 不支持的文件格式 {suffix}')

    if progress_callback:
        progress_callback(1.0)

    LOGGER.info('移调完成 → %s', dst)
    return dst


def transpose_by_interval(
    src: Path,
    dst: Path,
    interval_name: str,
    direction: str = 'up',
    transpose_key_sig: bool = True,
    progress_callback=None,
) -> Path:
    """按音程名称移调 MusicXML 文件（如 '纯四度'、'小三度'）。

    使用 diatonic-aware 拼写（MuseScore 方式）：由音程的音阶步数字段确定目标
    音级字母，再由半音差推算升降号，可正确处理增减音程（如增二度 C→D# 而非 Eb）。
    """
    semitones = get_interval_semitones(interval_name, direction)
    diatonic = get_interval_diatonic(interval_name, direction)
    return transpose_musicxml(
        src, dst,
        semitones=semitones,
        transpose_key_sig=transpose_key_sig,
        diatonic_offset=diatonic,
        progress_callback=progress_callback,
    )


def transpose_diatonic(
    src: Path,
    dst: Path,
    degree_name: str,
    direction: str = 'up',
    progress_callback=None,
) -> Path:
    """全音移调：按音阶度数名称（'二度'-'七度'）移调，不修改调号。"""
    import io
    import zipfile

    steps_map = dict(DIATONIC_DEGREES)
    steps = steps_map.get(degree_name, 1)
    if direction == 'down':
        steps = -steps

    if progress_callback:
        progress_callback(0.1)

    LOGGER.info('全音移调 %+d 步: %s', steps, src.name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower()

    if progress_callback:
        progress_callback(0.4)

    if suffix in ('.musicxml', '.xml'):
        raw = src.read_bytes()
        dst.write_bytes(_transpose_xml_bytes_diatonic(raw, steps))
    elif suffix == '.mxl':
        buf = io.BytesIO()
        with zipfile.ZipFile(src, 'r') as zin, \
             zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.lower().endswith('.xml'):
                    data = _transpose_xml_bytes_diatonic(data, steps)
                zout.writestr(item, data)
        dst.write_bytes(buf.getvalue())
    else:
        raise ValueError(f'transpose_diatonic: 不支持的文件格式 {suffix}')

    if progress_callback:
        progress_callback(1.0)

    LOGGER.info('全音移调完成 → %s', dst)
    return dst


# ─────────────────────────────────────────────────────────────────────────────
# CURVES 崩溃救援：删除连音线 / 延音线
# ─────────────────────────────────────────────────────────────────────────────

def strip_slurs_ties_from_xml(xml_path: Path, backup: bool = True) -> Path:
    """从 MusicXML 文件中移除所有连音线（slur）和延音线（tied/tie）元素。

    Audiveris 在 CURVES（连音线处理）步骤崩溃时，导出的 MusicXML 可能包含
    格式错误的 <slur> 或 <tied> 标签，导致后续解析失败。此函数通过纯 XML
    操作删除这些元素，不依赖 music21，以保证健壮性。

    Parameters
    ----------
    xml_path : 待处理的 .musicxml 或解压后的 XML 文件路径
    backup   : 是否在原地修改前备份（备份扩展名 .bak）

    Returns
    -------
    修改后（或未修改）的 xml_path（Path）
    """
    if not xml_path.exists():
        LOGGER.warning('strip_slurs_ties_from_xml: 文件不存在 %s', xml_path)
        return xml_path

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        # MusicXML 根元素带命名空间，需要 wildcard 匹配
        ns_prefix = ''
        if root.tag.startswith('{'):
            ns_prefix = root.tag.split('}')[0] + '}'

        removed = 0
        for parent in root.iter():
            # 需要先收集再删除，避免迭代中修改结构
            to_remove = []
            for child in list(parent):
                local = child.tag.replace(ns_prefix, '')
                if local in ('slur', 'tied', 'tie'):
                    to_remove.append(child)
            for child in to_remove:
                parent.remove(child)
                removed += 1

        if removed == 0:
            LOGGER.info('strip_slurs_ties_from_xml: 未找到连音线元素，文件未改变。')
            return xml_path

        if backup:
            shutil.copy2(xml_path, xml_path.with_suffix('.bak'))

        tree.write(xml_path, encoding='unicode', xml_declaration=True)
        LOGGER.info('strip_slurs_ties_from_xml: 已删除 %d 个连音线/延音线元素。', removed)
    except Exception as exc:
        LOGGER.warning('strip_slurs_ties_from_xml 失败: %s', exc)

    return xml_path


def strip_slurs_ties_from_mxl(mxl_path: Path, backup: bool = True) -> Path:
    """对 .mxl 压缩包执行连音线删除，原地替换压缩包。

    .mxl 是一个 ZIP 包，内含一个或多个 XML 文件。此函数对包内所有
    .xml 文件调用 :func:`strip_slurs_ties_from_xml`，然后重新打包。

    Returns
    -------
    修改后的 mxl_path（Path）
    """
    import zipfile
    import io

    if not mxl_path.exists():
        return mxl_path
    if mxl_path.suffix.lower() != '.mxl':
        # 直接处理 .musicxml
        return strip_slurs_ties_from_xml(mxl_path, backup=backup)

    try:
        tmp_dir = mxl_path.parent / f'_mxl_tmp_{mxl_path.stem}'
        tmp_dir.mkdir(exist_ok=True)

        with zipfile.ZipFile(mxl_path, 'r') as zin:
            zin.extractall(tmp_dir)

        for xml_file in tmp_dir.rglob('*.xml'):
            strip_slurs_ties_from_xml(xml_file, backup=False)

        if backup:
            shutil.copy2(mxl_path, mxl_path.with_suffix('.mxl.bak'))

        # 重新打包
        with zipfile.ZipFile(mxl_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for f in tmp_dir.rglob('*'):
                if f.is_file():
                    zout.write(f, f.relative_to(tmp_dir))

        shutil.rmtree(tmp_dir, ignore_errors=True)
        LOGGER.info('strip_slurs_ties_from_mxl: 已重新打包 %s', mxl_path.name)
    except Exception as exc:
        LOGGER.warning('strip_slurs_ties_from_mxl 失败: %s', exc)

    return mxl_path
