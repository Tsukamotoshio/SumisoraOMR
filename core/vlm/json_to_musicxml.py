# core/vlm/json_to_musicxml.py — Stage 2: JSON note list → MusicXML / MIDI / jianpu.txt
# Takes the dict produced by jianpu_recognizer and emits:
#   - MusicXML (.musicxml uncompressed, or .mxl compressed)
#   - MIDI (optional, for in-app playback)
#   - jianpu.txt (optional, for manual correction in the existing editor workflow)
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

_LOG = logging.getLogger('convert')

_DUR_MAP   = {'w': 4.0, 'h': 2.0, 'q': 1.0, 'e': 0.5, 's': 0.25}
_DUR_NAMES = {'w': 'whole', 'h': 'half', 'q': 'quarter', 'e': 'eighth', 's': '16th'}


def _build_scale_pitches(key_pitch: str, base_octave: int = 4) -> list:
    """简谱数字 1-7 在给定调中的实际音高（含 key 自带的升降号）。

    jianpu 数字是相对调号的：1=A 意味着 1→A、2→B、3→C#、…、7→G#。
    简单的 _PITCH_MAP 等于硬编码 1=C，丢失调式信息。
    返回 [pitch_for_1, pitch_for_2, ..., pitch_for_7]，octave 已设为 base_octave 起步。
    """
    from music21 import scale, pitch as m21_pitch
    try:
        sc = scale.MajorScale(key_pitch)
        # 取一个完整八度，从 key_pitch{octave} 开始
        pitches = sc.getPitches(f'{key_pitch}{base_octave}',
                                 f'{key_pitch}{base_octave + 1}')
        return [m21_pitch.Pitch(str(p)) for p in pitches[:7]]
    except Exception as exc:
        _LOG.warning(f'[VLM→MXL] 无法构建 {key_pitch} 大调音阶: {exc}，回退到 C')
        sc = scale.MajorScale('C')
        pitches = sc.getPitches(f'C{base_octave}', f'C{base_octave + 1}')
        return [m21_pitch.Pitch(str(p)) for p in pitches[:7]]

# jianpu.txt 时值前缀（jianpu-ly 语法）
# 注意命名撞车：VLM 的 'q'=四分音符，但 jianpu-ly 前缀 'q'=八分(quaver)。
# 四分音符在 jianpu-ly 是裸数字(无前缀)，八分音符才加 'q' 前缀。
_DUR_PREFIX = {'w': '', 'h': '', 'q': '', 'e': 'q', 's': 's'}
# whole/half 在 jianpu-ly 里用 "1 - - -" / "1 -" 表示，不带前缀
_DUR_TAIL   = {'w': ' - - -', 'h': ' -', 'q': '', 'e': '', 's': ''}
# VLM dur 码 → quarterLength（用于小节溢出判定）
_DUR_QL = {'w': 4.0, 'h': 2.0, 'q': 1.0, 'e': 0.5, 's': 0.25}


def _expected_quarter_len(time_sig: str) -> float:
    """拍号 → 一小节的 quarterLength。'3/4'→3.0, '6/8'→3.0, '12/8'→6.0。"""
    try:
        num, den = str(time_sig).split('/')
        return int(num) * (4.0 / int(den))
    except Exception:
        return 4.0


def _resolve_i_glyph(p: str, oct_off: int) -> tuple[str, int]:
    """字体把『1+上方点』渲染成 'i'/'í'，等价于高八度 1。归一化为 ('1', oct+1)。"""
    s = str(p).strip()
    primes = 0
    while s and s[-1] in ("'", "’"):
        primes += 1
        s = s[:-1]
    if s in ('i', 'í', 'I'):
        return '1', oct_off + 1 + primes
    return p, oct_off


def _token_quarter_len(n: Any) -> float:
    """单个 token（dict 或字符串）的 quarterLength。延音 '-' 记 1 拍。"""
    if isinstance(n, str):
        if n.strip() == '-':
            return 1.0
        n = {'p': n.strip()}
    if not isinstance(n, dict):
        return 0.0
    if str(n.get('p', '')).strip() == '-':
        return 1.0
    base = _DUR_QL.get(str(n.get('dur', 'q')).strip(), 1.0)
    if _safe_int(n.get('dots', 0), 0):
        base *= 1.5
    return base


def _is_rest_token(n: Any) -> bool:
    p = n.strip() if isinstance(n, str) else str(n.get('p', 'r')).strip()
    return p in ('0', 'r', 'o', 'O')   # 'o'/'O' 是 VLM 把休止符 '0' 误读成字母


def _trim_overflow_rests(notes: list, expected_ql: float) -> list:
    """删除使小节超过拍号的尾部多余休止（VLM 常在单小节末尾幻觉一个休止）。"""
    if not isinstance(notes, list) or expected_ql <= 0:
        return notes
    out = list(notes)
    while len(out) > 1 and _is_rest_token(out[-1]):
        total = sum(_token_quarter_len(x) for x in out)
        if total - _token_quarter_len(out[-1]) >= expected_ql - 1e-6:
            out.pop()
        else:
            break
    return out


def _safe_int(val: Any, default: int) -> int:
    """Coerce val to int; return default on failure (tolerates VLM garbage values)."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _parse_jianpu_shorthand(token: str) -> dict | None:
    """jianpu-ly 简写字符串 → 音符 dict，无法解析返回 None。

    支持组合形式（基本顺序：dur 前缀 → 升降号 → 音高 → 八度记号 → 附点）：
      "5"     四分 5
      "5'"    高八度 5（一个撇 = +1 八度，叠加可达 +2）
      "5,"    低八度 5（一个逗号 = -1 八度，叠加可达 -2）
      "q5"    八分 5（jianpu-ly 前缀 q = eighth）
      "s5"    十六分 5
      "#4"    升 4
      "b3"    降 3
      "5."    附点 5
      "i"/"í" 高八度 1（字体把"1+上方点"渲染成 i）
      "0"/"r" 休止符
    返回 {'_extend': 1} 表示这是延长记号 "-"，调用方应延长前一个音符。
    """
    s = token.strip()
    if not s:
        return None
    if s == '-':
        return {'_extend': 1}

    # 1) 时值前缀
    dur = 'q'
    if s[0] == 'q':
        dur, s = 'e', s[1:]
    elif s[0] == 's':
        dur, s = 's', s[1:]
    elif s[0] == 'd':  # 32分降级处理为16分
        dur, s = 's', s[1:]

    # 2) 升降号前缀（jianpu 写在数字前）
    acc = ''
    if s and s[0] == '#':
        acc, s = '#', s[1:]
    elif s and s[0] == 'b' and len(s) > 1 and s[1] in '1234567':
        acc, s = 'b', s[1:]

    # 3) 音高字符
    if not s:
        return None
    ch = s[0]
    s = s[1:]

    if ch in ('i', 'í', 'I', 'Í'):
        pitch, oct_off = '1', 1
    elif ch in '1234567':
        pitch, oct_off = ch, 0
    elif ch in ('0', 'r', 'R'):
        pitch, oct_off = 'r', 0
    else:
        return None

    # 4) 八度记号（' 加, , 减；可叠加）
    while s and s[0] in ("'", "′"):
        oct_off += 1
        s = s[1:]
    while s and s[0] in (',',):
        oct_off -= 1
        s = s[1:]

    # 5) 附点
    dots = 1 if s.startswith('.') else 0

    if pitch == 'r':
        return {'p': 'r', 'oct': 0, 'dur': dur, 'dots': dots}
    return {'p': acc + pitch if acc else pitch,
            'oct': oct_off, 'dur': dur, 'dots': dots}


def convert(data: dict[str, Any], output_path: Path, midi_path: Path | None = None) -> Path:
    """Convert JSON note list dict to a MusicXML file, optionally also write MIDI.

    data keys:
        time_signature: str  e.g. "4/4"
        key:            str  e.g. "C", "G"
        tempo:          int  BPM
        measures:       list[list[dict]]  each inner list is one measure

    note dict keys:
        p:    "1"–"7" or "r" (rest)
        oct:  int  octave shift relative to octave 4 (0 = middle octave)
        dur:  "w"/"h"/"q"/"e"/"s"
        dots: 0 or 1

    Args:
        output_path: where to write the MusicXML (.musicxml or .mxl).
        midi_path:   if given, also write a .mid file there (caller chooses location).

    Returns output_path after writing.
    Raises ValueError if output_path does not have a .musicxml or .mxl suffix.
    """
    suffix = output_path.suffix.lower()
    if suffix not in ('.musicxml', '.mxl'):
        raise ValueError(f'output_path must end with .musicxml or .mxl, got: {output_path}')

    from music21 import stream, note, meter, duration as m21_dur
    from music21 import tempo as m21_tempo
    from music21 import key as m21_key

    s = stream.Score()
    part = stream.Part()

    part.append(meter.TimeSignature(data.get('time_signature', '4/4')))

    key_str = data.get('key', 'C')
    try:
        part.append(m21_key.Key(key_str))
    except Exception as exc:
        _LOG.debug(f'[VLM→MXL] 无法识别调号 {key_str!r}，跳过 key signature: {exc}')
        key_str = 'C'

    bpm = _safe_int(data.get('tempo', 120), 120)
    part.append(m21_tempo.MetronomeMark(number=bpm))

    # 构建该调的 1-7 音阶映射（C 大调以外，3=C#/5=#=升号等会被正确处理）
    scale_pitches = _build_scale_pitches(key_str, base_octave=4)

    measures = data.get('measures', [])
    expected_ql = _expected_quarter_len(data.get('time_signature', '4/4'))
    _LOG.info(f'[VLM→MXL] {len(measures)} measures → {output_path.name}  (key={key_str})')

    for mi, measure_notes in enumerate(measures):
        # 容错：模型可能输出 ["5","3","1"] 而非 [{"p":"5",...},...]
        if not isinstance(measure_notes, list):
            _LOG.warning(f'[VLM→MXL] 跳过非列表小节 #{mi}: type={type(measure_notes).__name__}')
            continue
        measure_notes = _trim_overflow_rests(measure_notes, expected_ql)   # 删尾部溢出休止
        m = stream.Measure()
        prev_elem: Optional['note.GeneralNote'] = None   # type: ignore[name-defined]
        for n in measure_notes:
            if isinstance(n, str):
                parsed = _parse_jianpu_shorthand(n)
                if parsed is None:
                    continue
                # 延长记号 "-": 把前一个音符的时值加一拍
                if '_extend' in parsed:
                    if prev_elem is not None:
                        try:
                            prev_elem.duration.quarterLength += 1.0
                        except Exception:
                            pass
                    continue
                n = parsed
            elif not isinstance(n, dict):
                _LOG.warning(f'[VLM→MXL] 小节 #{mi} 含非dict音符: {n!r}，跳过')
                continue
            p = str(n.get('p', 'r')).strip()
            # 延长记号 "-" 也可能以 dict 形式 {"p":"-"} 出现：把前一个音符加一拍
            if p == '-':
                if prev_elem is not None:
                    try:
                        prev_elem.duration.quarterLength += 1.0
                    except Exception:
                        pass
                continue
            oct_off = _safe_int(n.get('oct', 0), 0)
            p, oct_off = _resolve_i_glyph(p, oct_off)   # 'i'/'í' → '1' 高八度
            dur_key = str(n.get('dur', 'q')).strip()
            dots = _safe_int(n.get('dots', 0), 0)

            # 容错：模型偶尔把音符串塞进 dur 字段
            if dur_key not in _DUR_NAMES:
                if dur_key == '-':
                    dur_key = 'h'
                elif dur_key.endswith('.') and dur_key[:-1] in '1234567':
                    dur_key, dots = 'q', max(dots, 1)
                elif dur_key in '1234567':
                    dur_key = 'q'
                else:
                    _LOG.warning(f'[VLM→MXL] 未知时值 "{dur_key}"，回退为四分音符')
                    dur_key = 'q'
            dur_type = _DUR_NAMES[dur_key]
            d = m21_dur.Duration(type=dur_type)
            d.dots = dots

            if p == 'r':
                elem: note.GeneralNote = note.Rest()
            else:
                # 处理升降号前缀 "#5" / "b3"
                acc_char = ''
                digit = p
                if p and p[0] in ('#', 'b') and len(p) > 1:
                    acc_char, digit = p[0], p[1:]
                if digit not in '1234567':
                    digit = '1'

                # 用 key 对应的音阶查表，得到正确的 step + 自带升降
                import copy as _copy
                base = _copy.deepcopy(scale_pitches[int(digit) - 1])
                base.octave += oct_off

                # 显式升降号覆盖（在调内音符上额外加 # 或 b）
                if acc_char == '#':
                    base.transpose('A1', inPlace=True)
                elif acc_char == 'b':
                    base.transpose('-A1', inPlace=True)

                elem = note.Note(base)

            elem.duration = d
            m.append(elem)
            prev_elem = elem
        part.append(m)

    s.append(part)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # .musicxml = uncompressed XML; .mxl = compressed ZIP
    fmt = 'musicxml' if suffix == '.musicxml' else 'mxl'
    s.write(fmt, fp=str(output_path))

    if midi_path is not None:
        try:
            midi_path.parent.mkdir(parents=True, exist_ok=True)
            s.write('midi', fp=str(midi_path))
            _LOG.info(f'[VLM→MIDI] 写入 → {midi_path.name}')
        except Exception as exc:
            _LOG.warning(f'[VLM→MIDI] MIDI 写入失败: {exc}')

    return output_path


def _note_to_jianpu(n: dict) -> str:
    """单个音符 dict → jianpu-ly 文本表示（如 5, 5', q#3, 5., 5 -）。"""
    if isinstance(n, str):
        s = n.strip()
        if s in ('i', 'í', "1'", '1+'):
            return "1'"
        if s in ('1,',):
            return '1,'
        n = {'p': s, 'oct': 0, 'dur': 'q', 'dots': 0}

    p = str(n.get('p', 'r'))
    oct_off = _safe_int(n.get('oct', 0), 0)
    p, oct_off = _resolve_i_glyph(p, oct_off)   # 'i'/'í' → '1' 高八度
    dur_key = str(n.get('dur', 'q'))
    dots = _safe_int(n.get('dots', 0), 0)

    if p in ('r', '0', 'o', 'O'):   # 'o'/'O': VLM 把休止符 '0' 误读成字母
        body = '0'
    else:
        # accidentals 已包含在 p 里（"#5", "b3"），原样用
        body = p
    # 八度标记
    if oct_off > 0:
        body += "'" * oct_off
    elif oct_off < 0:
        body += ',' * (-oct_off)

    prefix = _DUR_PREFIX.get(dur_key, 'q' if dur_key == 'e' else '')
    tail = _DUR_TAIL.get(dur_key, '')
    out = f'{prefix}{body}{tail}'
    if dots:
        out += '.'
    return out


def measures_as_token_lists(data: dict[str, Any]) -> list[list[str]]:
    """把 VLM 输出的 measures 渲染成逐小节 jianpu token 列表。

    统一入口：已应用尾部溢出休止 trim 与 'i'/'í' 高八度归一化，
    供 .jianpu.txt 生成与离线评测共用，保证两边记号一致。
    """
    expected = _expected_quarter_len(str(data.get('time_signature', '4/4')))
    out: list[list[str]] = []
    for m in data.get('measures', []):
        if not isinstance(m, list):
            continue
        trimmed = _trim_overflow_rests(m, expected)
        out.append([_note_to_jianpu(n) for n in trimmed])
    return out


def to_jianpu_text(data: dict[str, Any], title: str) -> str:
    """把 VLM 输出转为 jianpu-ly 的 .jianpu.txt 文本，可在编辑器中校对。"""
    lines = [
        '# ================================================================',
        f'# 简谱校对文件 — {title}',
        '# 本文件由 VLM-OCR 自动生成，可手动修改并重新生成 PDF',
        '# ================================================================',
        '',
        '% jianpu-ly.py',
        f'title={title}',
        f'1={data.get("key", "C")}',
        f'{data.get("time_signature", "4/4")}',
        '',
    ]
    bars = [' '.join(tokens) for tokens in measures_as_token_lists(data)]
    if bars:
        # 每 4 小节换一行
        for i in range(0, len(bars), 4):
            lines.append(' | '.join(bars[i:i + 4]) + ' |')
    return '\n'.join(lines) + '\n'
