# core/jianpu_core.py — 简谱音符/小节转换逻辑
# 拆分自 convert.py
from typing import Optional

from music21 import chord as m21chord, meter, note as m21note, stream

from .config import (
    ALLOWED_JIANPU_DURATIONS,
    JianpuNote,
)

# ── Movable-Do (首调唱名法) semitone → jianpu numeral tables ─────────────────
# Diatonic intervals from the reference tonic (0=do, 2=re, 4=mi, …)
_DIATONIC_SEMITONES: dict[int, str] = {0: '1', 2: '2', 4: '3', 5: '4', 7: '5', 9: '6', 11: '7'}
# Chromatic (accidental) intervals — (accidental_prefix, numeral)
_CHROMATIC_SHARP: dict[int, tuple[str, str]] = {
    1: ('#', '1'), 3: ('#', '2'), 6: ('#', '4'), 8: ('#', '5'), 10: ('#', '6'),
}
_CHROMATIC_FLAT: dict[int, tuple[str, str]] = {
    1: ('b', '2'), 3: ('b', '3'), 6: ('b', '5'), 8: ('b', '6'), 10: ('b', '7'),
}
# Keys whose accidentals lean towards flats (pitch-class of the tonic, 0-based)
# F=5, Bb=10, Eb=3, Ab=8, Db=1, Gb=6, Cb=11
_FLAT_KEY_SEMITONES: frozenset = frozenset({1, 3, 5, 6, 8, 10, 11})


def _get_first_note_tonic(score) -> tuple[int, str]:
    """Return (pitch_class 0-11, display_name) of the first non-rest sounding note.

    This is used as the Movable-Do reference: the very first note is treated as
    scale degree 1 (do).  Falls back to C (pitch class 0) if no note is found.
    """
    try:
        part = score.parts[0] if score.parts else score.flatten()
        for element in part.flatten().notesAndRests:
            if isinstance(element, m21note.Note) and element.pitch is not None:
                # music21 uses '-' for flats (e.g. 'B-'), jianpu-ly expects 'b' (e.g. 'Bb')
                name = element.pitch.name.replace('-', 'b')
                return element.pitch.pitchClass, name
            if isinstance(element, m21chord.Chord) and element.pitches:
                top = max(element.pitches, key=lambda p: p.midi)
                name = top.name.replace('-', 'b')
                return top.pitchClass, name
    except Exception:
        pass
    return 0, 'C'


def duration_suffix(q_len: float, dots: int) -> str:
    """Convert a quarter-length duration to a jianpu-ly text notation suffix (dashes, underscores, dots)."""
    tol = 0.01
    if q_len >= 1.0 - tol:
        base_quarters = round(q_len)
        dash_count = max(base_quarters - 1, 0)
        suffix = '-' * dash_count
        if dots > 0:
            suffix += '.' * dots
        return suffix

    if abs(q_len - 0.75) < tol:
        suffix = '_.'
    elif abs(q_len - 0.5) < tol:
        suffix = '_'
    elif abs(q_len - 0.375) < tol:
        suffix = '__.'
    elif abs(q_len - 0.25) < tol:
        suffix = '__'
    elif abs(q_len - 0.1875) < tol:
        suffix = '___.'
    elif abs(q_len - 0.125) < tol:
        suffix = '___'
    else:
        suffix = '_'

    if dots > 0 and '.' not in suffix:
        suffix += '.' * dots
    return suffix


def get_duration_render(duration: float, dots: int) -> tuple[int, int, int]:
    """Convert a duration to a (dashes, underlines, right_dots) tuple for direct PDF drawing."""
    tol = 0.01
    if duration >= 1.0 - tol:
        dashes = max(round(duration) - 1, 0)
        return dashes, 0, dots

    if abs(duration - 0.75) < tol:
        return 0, 1, 1
    if abs(duration - 0.5) < tol:
        return 0, 1, 0
    if abs(duration - 0.375) < tol:
        return 0, 2, 1
    if abs(duration - 0.25) < tol:
        return 0, 2, 0
    if abs(duration - 0.1875) < tol:
        return 0, 3, 1
    if abs(duration - 0.125) < tol:
        return 0, 3, 0
    return 0, 1, dots


def note_to_jianpu(element, key_tonic_semitone: int = 0) -> JianpuNote:
    """Convert a music21 note or rest to a JianpuNote dataclass (Movable-Do / 首调唱名法).

    *key_tonic_semitone* is the pitch-class (0–11) of the reference tonic
    (the note that maps to '1' / do).  For C major supply 0, for G major 7, etc.
    For minor keys pass the *relative-major* tonic (e.g. A-minor → C=0).
    """
    if isinstance(element, m21note.Rest):
        return JianpuNote(
            symbol='0',
            accidental='',
            upper_dots=0,
            lower_dots=0,
            duration=float(element.duration.quarterLength),
            duration_dots=int(getattr(element.duration, 'dots', 0)),
            midi=None,
            is_rest=True,
        )

    pitch = element.pitch
    if pitch is None:
        return JianpuNote(
            symbol='?', accidental='', upper_dots=0, lower_dots=0,
            duration=float(element.duration.quarterLength),
            duration_dots=int(getattr(element.duration, 'dots', 0)),
            midi=None, is_rest=False,
        )

    # Determine MIDI pitch (C4 = 60)
    note_midi = pitch.midi
    if note_midi is None:
        _step_pc = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
        _alter = int(pitch.accidental.alter) if pitch.accidental else 0
        _octave = pitch.octave if pitch.octave is not None else 4
        note_midi = _step_pc.get(pitch.step, 0) + _alter + (_octave + 1) * 12

    # Reference: key tonic at octave 4 (MIDI 60 = C4)
    key_midi_ref = (key_tonic_semitone % 12) + 60
    diff = note_midi - key_midi_ref
    relative_semitone = diff % 12
    octave_offset = diff // 12

    if relative_semitone in _DIATONIC_SEMITONES:
        symbol = _DIATONIC_SEMITONES[relative_semitone]
        accidental = ''
    else:
        if key_tonic_semitone in _FLAT_KEY_SEMITONES:
            accidental, symbol = _CHROMATIC_FLAT[relative_semitone]
        else:
            accidental, symbol = _CHROMATIC_SHARP[relative_semitone]

    upper_dots = max(octave_offset, 0)
    lower_dots = max(-octave_offset, 0)

    return JianpuNote(
        symbol=symbol,
        accidental=accidental,
        upper_dots=upper_dots,
        lower_dots=lower_dots,
        duration=float(element.duration.quarterLength),
        duration_dots=int(getattr(element.duration, 'dots', 0)),
        midi=note_midi,
        is_rest=False,
    )


def format_jianpu_note_text(note: JianpuNote) -> str:
    """Format a JianpuNote as the display string used in direct PDF rendering."""
    if note.is_rest:
        return '0'
    text = ''.join('·' for _ in range(note.upper_dots))
    text += note.accidental + note.symbol
    text += ''.join('·' for _ in range(note.lower_dots))
    text += duration_suffix(note.duration, note.duration_dots)
    return text


def jianpu_octave_symbol(upper_dots: int, lower_dots: int) -> str:
    """Return the jianpu-ly octave marker: single-quotes for high, commas for low."""
    if upper_dots > 0:
        return "'" * upper_dots
    if lower_dots > 0:
        return ',' * lower_dots
    return ''


def jianpu_note_token(note: JianpuNote) -> str:
    """Produce the jianpu-ly token string for a single note (pitch, octave marker, duration)."""
    if note.is_rest:
        base_note = '0'
    else:
        base_note = note.accidental + note.symbol + jianpu_octave_symbol(note.upper_dots, note.lower_dots)

    tol = 0.01
    d = note.duration

    if note.is_rest:
        if abs(d - 4.0) < tol:
            return '0 - - -'
        if abs(d - 3.0) < tol:
            return '0 - -'
        if abs(d - 2.0) < tol:
            return '0 -'
        if abs(d - 1.5) < tol:
            return '0.'
        if abs(d - 1.0) < tol:
            return '0'
        if abs(d - 0.75) < tol:
            return 'q0.'
        if abs(d - 0.5) < tol:
            return 'q0'
        if abs(d - 0.375) < tol:
            return 's0.'
        if abs(d - 0.25) < tol:
            return 's0'
        if abs(d - 0.1875) < tol:
            return 'd0.'
        if abs(d - 0.125) < tol:
            return 'd0'
        return '0'

    if abs(d - 4.0) < tol:
        return f'{base_note} - - -'
    if abs(d - 3.0) < tol:
        return f'{base_note} - -'
    if abs(d - 2.0) < tol:
        return f'{base_note} -'
    if abs(d - 1.5) < tol:
        return f'{base_note}.'
    if abs(d - 1.0) < tol:
        return base_note
    if abs(d - 0.75) < tol:
        return f'q{base_note}.'
    if abs(d - 0.5) < tol:
        return f'q{base_note}'
    if abs(d - 0.375) < tol:
        return f's{base_note}.'
    if abs(d - 0.25) < tol:
        return f's{base_note}'
    if abs(d - 0.1875) < tol:
        return f'd{base_note}.'
    if abs(d - 0.125) < tol:
        return f'd{base_note}'
    return f'q{base_note}'


def normalize_jianpu_duration(duration: float) -> float:
    """Round a duration down to the nearest allowed jianpu duration value."""
    tol = 0.01
    for candidate in ALLOWED_JIANPU_DURATIONS:
        if duration + tol >= candidate:
            return candidate
    return 0.125


def infer_duration_dots(duration: float) -> int:
    """Return 1 if the duration matches a dotted note value, otherwise 0."""
    for candidate in (1.5, 0.75, 0.375, 0.1875):
        if abs(duration - candidate) < 0.01:
            return 1
    return 0


def clone_jianpu_note(note: JianpuNote, duration: float) -> JianpuNote:
    """Clone a JianpuNote with a new duration (auto-normalised and dotted)."""
    normalized_duration = normalize_jianpu_duration(duration)
    return JianpuNote(
        symbol=note.symbol,
        accidental=note.accidental,
        upper_dots=note.upper_dots,
        lower_dots=note.lower_dots,
        duration=normalized_duration,
        duration_dots=infer_duration_dots(normalized_duration),
        midi=note.midi,
        is_rest=note.is_rest,
    )


def split_duration_chunks(duration: float) -> list[float]:
    """Split a remaining duration into allowed jianpu chunks (greedy, largest first)."""
    remaining = max(float(duration), 0.0)
    chunks: list[float] = []
    tol = 0.01
    while remaining > tol:
        piece = normalize_jianpu_duration(remaining)
        piece = min(piece, remaining)
        if piece <= tol:
            piece = min(0.125, remaining)
        chunks.append(piece)
        remaining -= piece
    return chunks or [0.125]


# ── Integer 64th-note unit validation (matches jianpu-ly.py's barLength arithmetic) ──────────

# jianpu-ly.py sets barLength = int(64 * num / denom) and uses integer Fraction arithmetic.
# Map each allowed quarter-length duration to its 64th-note unit count.
_DURATION_TO_UNITS: dict[float, int] = {
    4.0: 64, 3.0: 48, 2.0: 32, 1.5: 24, 1.0: 16,
    0.75: 12, 0.5: 8, 0.375: 6, 0.25: 4, 0.1875: 3, 0.125: 2,
}
_ALLOWED_UNITS_DESC: list[tuple[int, float]] = sorted(
    ((u, d) for d, u in _DURATION_TO_UNITS.items()), reverse=True
)  # [(64, 4.0), (48, 3.0), ..., (2, 0.125)]


def _parse_bar_units(time_signature: str) -> int:
    """Convert a 'num/denom' time signature string to jianpu-ly barLength (integer 64th-note units)."""
    try:
        num_s, denom_s = time_signature.split('/')
        return int(64 * int(num_s) / int(denom_s))
    except (ValueError, ZeroDivisionError, AttributeError):
        return 64  # fallback: 4/4


def pad_measure_to_bar(notes: list[JianpuNote], bar_units: int) -> list[JianpuNote]:
    """Guarantee notes sum to exactly *bar_units* in integer 64th-note units.

    This is the final safety net before the jianpu-ly text is assembled.
    jianpu-ly.py raises a fatal barcheck error whenever barPos > barLength; using
    integer unit arithmetic here matches its internal Fraction-based calculation and
    eliminates the floating-point edge cases that repair_jianpu_measure can miss.

    * Overflow: the note that would cross the barline is replaced by a rest that
      fills exactly the remaining space (split into allowed chunks if needed).
    * Underflow: a rest is appended to fill the remaining space.
    """
    if bar_units <= 0:
        return list(notes)

    rest_proto = JianpuNote('0', '', 0, 0, 1.0, 0, None, True)
    total = 0
    result: list[JianpuNote] = []

    def _fill_rest(remaining: int) -> None:
        """Append rest chunk(s) to result until *remaining* units are consumed."""
        while remaining >= 2:
            for u, dur in _ALLOWED_UNITS_DESC:
                if u <= remaining:
                    result.append(clone_jianpu_note(rest_proto, dur))
                    remaining -= u
                    break
            else:
                break  # no chunk fits (remaining < 2 units)

    for note in notes:
        u = _DURATION_TO_UNITS.get(note.duration) or max(2, round(note.duration * 16))
        if total >= bar_units:
            break
        if total + u > bar_units:
            # This note overflows — replace its tail with rest(s)
            _fill_rest(bar_units - total)
            total = bar_units
            break
        result.append(note)
        total += u

    # Pad any underflow with rest(s)
    if total < bar_units:
        _fill_rest(bar_units - total)

    return result


def repair_jianpu_measure(measure_notes: list[JianpuNote], measure_length: float) -> list[JianpuNote]:
    """Trim overflowing notes and pad with rests so the measure total equals the time-signature length."""
    tol = 0.01
    current_total = 0.0
    repaired: list[JianpuNote] = []
    rest_template = JianpuNote('0', '', 0, 0, 1.0, 0, None, True)

    for note in measure_notes:
        if current_total >= measure_length - tol:
            break

        remaining = measure_length - current_total
        if remaining <= tol:
            break

        note_duration = max(float(note.duration), 0.125)
        if note_duration <= remaining + tol:
            new_note = clone_jianpu_note(note, note_duration)
            repaired.append(new_note)
            current_total += new_note.duration
            continue

        for piece in split_duration_chunks(remaining):
            if current_total >= measure_length - tol:
                break
            new_note = clone_jianpu_note(note, piece)
            repaired.append(new_note)
            current_total += new_note.duration

    remaining = measure_length - current_total
    if remaining > tol:
        for piece in split_duration_chunks(remaining):
            new_rest = clone_jianpu_note(rest_template, piece)
            repaired.append(new_rest)
            current_total += new_rest.duration

    return repaired


def clone_monophonic_element(element, duration: float):
    """Clone a music21 element as a monophonic note: chords become the top pitch."""
    normalized_duration = normalize_jianpu_duration(duration)
    if isinstance(element, m21note.Rest):
        new_element = m21note.Rest()
    elif isinstance(element, m21chord.Chord):
        top_pitch = max(element.pitches, key=lambda pitch: pitch.midi)
        new_element = m21note.Note(top_pitch)
    else:
        new_element = m21note.Note(element.pitch)
    new_element.duration.quarterLength = normalized_duration
    return new_element


# 安全上限：超过此数量的小节被认为是 MusicXML 解析异常（如 Oemer 输出内部偏移错误）
# 将在日志中发出警告并截断。600 小节 ≈ 150 拍 4/4 = 已足够容纳给小篇音乐。
MAX_SANE_BARS = 600


def extract_jianpu_measures(score, key_tonic_semitone: int = 0) -> tuple[list[list[JianpuNote]], str]:
    """
    Extract jianpu measure list and time signature from a music21 score.
    Merges polyphony by offset; fills gaps and overflows with rests at bar boundaries.
    """
    from .utils import log_message
    import logging as _logging
    part = score.parts[0] if score.parts else score.flatten()
    time_signature = '4/4'
    nominal_measure_length = 4.0
    time_sig = part.recurse().getElementsByClass(meter.TimeSignature)
    if time_sig:
        time_signature = time_sig[0].ratioString
        nominal_measure_length = float(getattr(time_sig[0].barDuration, 'quarterLength', 4.0) or 4.0)

    measures: list[list[JianpuNote]] = []
    measure_streams = list(part.getElementsByClass(stream.Measure))
    if not measure_streams:
        fallback_notes = [note_to_jianpu(element, key_tonic_semitone) for element in part.flatten().notesAndRests]
        return ([fallback_notes] if fallback_notes else []), time_signature

    if len(measure_streams) > MAX_SANE_BARS:
        log_message(
            f'[jianpu] 注意：MusicXML 包含 {len(measure_streams)} 个小节（超过安全限制 {MAX_SANE_BARS}），'
            f'可能是 OMR 解析异常。将截尾处理前 {MAX_SANE_BARS} 个小节。',
            _logging.WARNING,
        )
        measure_streams = measure_streams[:MAX_SANE_BARS]

    tol = 0.01
    for measure in measure_streams:
        measure_length = nominal_measure_length or float(getattr(measure.barDuration, 'quarterLength', 4.0) or 4.0)
        by_offset: dict[float, list[object]] = {}

        for element in measure.flatten().notesAndRests:
            offset = float(element.offset)
            if offset >= measure_length - tol:
                continue
            available = max(measure_length - offset, 0.125)
            candidate = clone_monophonic_element(element, min(float(element.duration.quarterLength or 0.25), available))
            existing = by_offset.get(offset)
            if existing is None:
                by_offset[offset] = [candidate]
            else:
                existing.append(candidate)

        if not by_offset:
            rest = m21note.Rest()
            rest.duration.quarterLength = measure_length
            measures.append(repair_jianpu_measure([note_to_jianpu(rest, key_tonic_semitone)], measure_length))
            continue

        offsets = sorted(by_offset.keys())
        current_offset = 0.0
        measure_notes: list[JianpuNote] = []

        for idx, offset in enumerate(offsets):
            if current_offset >= measure_length - tol:
                break
            if offset > current_offset + tol:
                rest = m21note.Rest()
                rest.duration.quarterLength = normalize_jianpu_duration(min(offset - current_offset, measure_length - current_offset))
                measure_notes.append(note_to_jianpu(rest, key_tonic_semitone))

            next_offset = offsets[idx + 1] if idx + 1 < len(offsets) else measure_length
            next_offset = min(next_offset, measure_length)
            span = max(min(next_offset - offset, measure_length - offset), 0.125)
            for element in by_offset[offset]:
                measure_notes.append(note_to_jianpu(element, key_tonic_semitone))
            current_offset = next_offset

        if current_offset < measure_length - tol:
            rest = m21note.Rest()
            rest.duration.quarterLength = normalize_jianpu_duration(measure_length - current_offset)
            measure_notes.append(note_to_jianpu(rest, key_tonic_semitone))

        measures.append(repair_jianpu_measure(measure_notes, measure_length))

    return measures, time_signature


def extract_strict_jianpu_measures(score, key_tonic_semitone: int = 0) -> tuple[list[list[JianpuNote]], str]:
    """
    Extract jianpu measures by re-slicing all notes strictly by bar length,
    ignoring the score's own Measure objects.
    """
    from .utils import log_message
    import logging as _logging
    part = score.parts[0] if score.parts else score.flatten()
    time_signature = '4/4'
    bar_length = 4.0
    time_sig = part.recurse().getElementsByClass(meter.TimeSignature)
    if time_sig:
        time_signature = time_sig[0].ratioString
        bar_length = float(getattr(time_sig[0].barDuration, 'quarterLength', 4.0) or 4.0)

    max_score_offset = MAX_SANE_BARS * bar_length  # hard ceiling on note offsets

    by_offset: dict[float, object] = {}
    for element in part.flatten().notesAndRests:
        offset = float(element.offset)
        if offset < 0 or offset >= max_score_offset:
            continue
        candidate = clone_monophonic_element(element, float(element.duration.quarterLength or 0.25))
        existing = by_offset.get(offset)
        if existing is None:
            by_offset[offset] = candidate
        elif isinstance(existing, m21note.Rest) and not isinstance(candidate, m21note.Rest):
            by_offset[offset] = candidate
        elif isinstance(existing, m21note.Note) and isinstance(candidate, m21note.Note) and candidate.pitch.midi > existing.pitch.midi:
            by_offset[offset] = candidate

    offsets = sorted(by_offset.keys())
    if not offsets:
        return [], time_signature

    # 警告：如果最大 offset 超过合理上限
    if offsets[-1] >= max_score_offset * 0.5 and offsets[-1] > bar_length * 30:
        from .utils import log_message
        import logging as _logging
        log_message(
            f'[jianpu] 注意：MusicXML 中最大音符偏移量为 {offsets[-1]:.1f} 拍'
            f'（约 {offsets[-1] / bar_length:.0f} 小节），可能是 OMR 解析异常。',
            _logging.WARNING,
        )

    measures: list[list[JianpuNote]] = []
    current_measure: list[JianpuNote] = []
    current_pos = 0.0
    tol = 0.01

    def append_element_chunks(element_template, total_duration: float) -> None:
        """Split an element across bar boundaries and append chunks to the current measure."""
        nonlocal current_measure, current_pos, measures
        remaining = total_duration
        while remaining > tol:
            # 安全检查：超过小节上限时提前退出
            if len(measures) >= MAX_SANE_BARS:
                return
            pos_in_bar = current_pos % bar_length
            capacity = bar_length - pos_in_bar if pos_in_bar > tol else bar_length
            piece_total = min(remaining, capacity)
            for piece in split_duration_chunks(piece_total):
                element_piece = clone_monophonic_element(element_template, piece)
                current_measure.append(note_to_jianpu(element_piece, key_tonic_semitone))
                current_pos += piece
            remaining -= piece_total
            if abs(current_pos % bar_length) < tol:
                measures.append(current_measure)
                current_measure = []

    for idx, offset in enumerate(offsets):
        if len(measures) >= MAX_SANE_BARS:
            break
        if offset > current_pos + tol:
            gap = offset - current_pos
            # 跳过异常大的间隙（可能是 OMR 输出中的宫位异常）而不是用休止符充填
            if gap > bar_length * MAX_SANE_BARS:
                current_pos = offset
            else:
                rest = m21note.Rest()
                append_element_chunks(rest, gap)

        next_offset = offsets[idx + 1] if idx + 1 < len(offsets) else offset + float(by_offset[offset].duration.quarterLength or 0.25)
        # 单个音符时寄不得超过 MAX_SANE_BARS 个小节（防止 Oemer 输出中的超长音符）
        duration = max(min(next_offset - offset, bar_length * MAX_SANE_BARS), 0.125)
        append_element_chunks(by_offset[offset], duration)

    if current_measure:
        pos_in_bar = current_pos % bar_length
        if pos_in_bar > tol:
            rest = m21note.Rest()
            append_element_chunks(rest, bar_length - pos_in_bar)
        elif current_measure:
            measures.append(current_measure)

    repaired_measures = [repair_jianpu_measure(measure, bar_length) for measure in measures]
    return repaired_measures, time_signature


def choose_measures_per_line(measures: list[list[JianpuNote]]) -> int:
    """Choose measures per line based on average note density (dense → 3, otherwise 4)."""
    if not measures:
        return 4
    sample = measures[: min(len(measures), 24)]
    avg_notes = sum(len(measure) for measure in sample) / max(len(sample), 1)
    if avg_notes >= 9:
        return 3
    return 4


def parse_score_to_jianpu(score) -> tuple[list[list[JianpuNote]], list[str], str]:
    """Parse a score into jianpu format; return (measures, header_lines, time_sig)."""
    key_tonic_semitone, tonic_name = _get_first_note_tonic(score)
    key_header = f'1={tonic_name}'

    measures, time_signature = extract_jianpu_measures(score, key_tonic_semitone)
    header_lines: list[str] = [f'{key_header} {time_signature}', '']

    measures_per_line = choose_measures_per_line(measures)
    for i in range(0, len(measures), measures_per_line):
        line_measures = measures[i:i + measures_per_line]
        measure_texts = [' '.join(format_jianpu_note_text(note) for note in measure) for measure in line_measures]
        header_lines.append(' | '.join(measure_texts) + ' |')

    return measures, header_lines, time_signature


def build_jianpu_ly_text_from_measures(
    measures: 'list[list[JianpuNote]]',
    time_sig: str,
    tonic_name: str,
    title: str,
) -> str:
    """Build jianpu-ly plain-text from pre-computed JianpuNote measures (bypassing score parsing).

    Equivalent to ``build_jianpu_ly_text`` but accepts already-parsed note data
    instead of a music21 Score object.  Used by the dual-engine fusion path.
    """
    header = ['% jianpu-ly.py', f'title={title}', f'1={tonic_name}', time_sig, '']
    bar_units = _parse_bar_units(time_sig)
    for i in range(0, len(measures), 4):
        line_measures = measures[i:i + 4]
        measure_texts = [
            ' '.join(jianpu_note_token(note) for note in pad_measure_to_bar(m, bar_units))
            for m in line_measures
        ]
        header.append(' | '.join(measure_texts) + ' |')
    return '\n'.join(header)


def build_jianpu_ly_text(score, title: str, use_strict_timing: bool = False) -> str:
    """Build jianpu-ly plain-text (.txt) content, including title, key, time signature, and measures."""
    key_tonic_semitone, tonic_name = _get_first_note_tonic(score)

    header = ['% jianpu-ly.py', f'title={title}', f'1={tonic_name}']

    measures, time_signature = extract_strict_jianpu_measures(score, key_tonic_semitone) if use_strict_timing else extract_jianpu_measures(score, key_tonic_semitone)
    header.append(time_signature)
    header.append('')

    bar_units = _parse_bar_units(time_signature)
    measures_per_line = 4
    for i in range(0, len(measures), measures_per_line):
        line_measures = measures[i:i + measures_per_line]
        measure_texts = [
            ' '.join(jianpu_note_token(note) for note in pad_measure_to_bar(m, bar_units))
            for m in line_measures
        ]
        header.append(' | '.join(measure_texts) + ' |')

    return '\n'.join(header)
