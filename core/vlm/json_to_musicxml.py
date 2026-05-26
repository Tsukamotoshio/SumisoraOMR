# core/vlm/json_to_musicxml.py — Stage 2: JSON note list → MusicXML / MIDI / jianpu.txt
# Takes the dict produced by jianpu_recognizer and emits:
#   - MusicXML (.musicxml uncompressed, or .mxl compressed)
#   - MIDI (optional, for in-app playback)
#   - jianpu.txt (optional, for manual correction in the existing editor workflow)
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_LOG = logging.getLogger('convert')

_PITCH_MAP = {'1': 'C', '2': 'D', '3': 'E', '4': 'F', '5': 'G', '6': 'A', '7': 'B'}
_DUR_MAP   = {'w': 4.0, 'h': 2.0, 'q': 1.0, 'e': 0.5, 's': 0.25}
_DUR_NAMES = {'w': 'whole', 'h': 'half', 'q': 'quarter', 'e': 'eighth', 's': '16th'}

# jianpu.txt 时值前缀（jianpu-ly 语法）
_DUR_PREFIX = {'w': '', 'h': '', 'q': 'q', 'e': 'q', 's': 's'}
# whole/half 在 jianpu-ly 里用 "1 - - -" / "1 -" 表示，不带前缀
_DUR_TAIL   = {'w': ' - - -', 'h': ' -', 'q': '', 'e': '', 's': ''}


def _safe_int(val: Any, default: int) -> int:
    """Coerce val to int; return default on failure (tolerates VLM garbage values)."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


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

    try:
        part.append(m21_key.Key(data.get('key', 'C')))
    except Exception as exc:
        _LOG.debug(f'[VLM→MXL] 无法识别调号，跳过: {exc}')

    bpm = _safe_int(data.get('tempo', 120), 120)
    part.append(m21_tempo.MetronomeMark(number=bpm))

    measures = data.get('measures', [])
    _LOG.info(f'[VLM→MXL] {len(measures)} measures → {output_path.name}')

    # jianpu-ly 字体把 "1 头顶有点"（高八度 1）渲染成 i/í，部分模型直接输出这些字符
    _HIGH_OCTAVE_ONE = {'i', 'í', "1'", '1+'}
    _LOW_OCTAVE_ONE  = {'1,'}

    for mi, measure_notes in enumerate(measures):
        # 容错：模型可能输出 ["5","3","1"] 而非 [{"p":"5",...},...]
        if not isinstance(measure_notes, list):
            _LOG.warning(f'[VLM→MXL] 跳过非列表小节 #{mi}: type={type(measure_notes).__name__}')
            continue
        m = stream.Measure()
        for n in measure_notes:
            if isinstance(n, str):
                # 字符串形式（如 "5", "í"）当成简化音符处理
                token = n.strip()
                if token in _HIGH_OCTAVE_ONE:
                    n = {'p': '1', 'oct': 1, 'dur': 'q', 'dots': 0}
                elif token in _LOW_OCTAVE_ONE:
                    n = {'p': '1', 'oct': -1, 'dur': 'q', 'dots': 0}
                else:
                    n = {'p': token, 'oct': 0, 'dur': 'q', 'dots': 0}
            elif not isinstance(n, dict):
                _LOG.warning(f'[VLM→MXL] 小节 #{mi} 含非dict音符: {n!r}，跳过')
                continue
            p = str(n.get('p', 'r'))
            dur_key = str(n.get('dur', 'q')).strip()
            dots = _safe_int(n.get('dots', 0), 0)

            # 容错：模型偶尔把音符串塞进 dur 字段，如 "5", "5.", "-"
            if dur_key not in _DUR_NAMES:
                if dur_key == '-':
                    # 单独的 "-" = 延长记号；当成 half note 处理
                    dur_key = 'h'
                elif dur_key.endswith('.') and dur_key[:-1] in '1234567':
                    # 像 "5." → 附点 quarter（音高已在 p 里）
                    dur_key = 'q'
                    dots = max(dots, 1)
                elif dur_key in '1234567':
                    # 像 "5" 误入 dur → 默认 quarter
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
                pitch_letter = _PITCH_MAP.get(p, 'C')
                octave = 4 + _safe_int(n.get('oct', 0), 0)
                elem = note.Note(f'{pitch_letter}{octave}')

            elem.duration = d
            m.append(elem)
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
    dur_key = str(n.get('dur', 'q'))
    dots = _safe_int(n.get('dots', 0), 0)

    if p == 'r':
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
    measures = data.get('measures', [])
    bars: list[str] = []
    for m in measures:
        if not isinstance(m, list):
            continue
        tokens = [_note_to_jianpu(n) for n in m]
        bars.append(' '.join(tokens))
    if bars:
        # 每 4 小节换一行
        for i in range(0, len(bars), 4):
            lines.append(' | '.join(bars[i:i + 4]) + ' |')
    return '\n'.join(lines) + '\n'
