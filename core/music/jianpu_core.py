# core/jianpu_core.py — 简谱音符/小节转换逻辑
# 拆分自 convert.py
from typing import Optional

from music21 import chord as m21chord, meter, note as m21note, stream

from ..config import (
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


def _get_score_key_tonic(score) -> tuple[int, str]:
    """Extract the tonic (main note) from the score's key signature.

    首调唱名法的关键：从调号中提取主音，而非从第一个实际音符。
    Extracts (pitch_class 0-11, display_name) of the tonic from the Key element.

    Detection priority:
    1. ``Key`` element — explicit tonic + mode (most authoritative).
    2. ``KeySignature`` element (fifths only, no mode) + ``score.analyze('key')``
       to disambiguate major/minor — mirrors the transposer's
       ``_parse_musicxml_key_signature`` + ``detect_key_from_musicxml`` logic
       but adds mode resolution.  Handles OMR engines (e.g. Homr) that write
       ``<fifths>`` without ``<mode>``.
    3. ``score.analyze('key')`` tonic alone — for scores with no key-signature
       element at all.
    4. First actual note — last resort.
    """
    from music21 import key as m21key  # local import to avoid circular at module level
    try:
        # Priority 1: explicit Key element (has both fifths and mode)
        part = score.parts[0] if score.parts else score.flatten()
        key_sigs = part.flatten().getElementsByClass(m21key.Key)
        if key_sigs:
            key = key_sigs[0]
            tonic = key.tonic
            name = tonic.name.replace('-', 'b')
            return tonic.pitchClass, name
    except Exception:
        pass

    try:
        # Priority 2: KeySignature (fifths only) + statistical mode detection
        # music21 parses MusicXML <key><fifths>N</fifths></key> (no <mode>) as
        # KeySignature, not Key.  We combine the explicit accidental count with
        # Krumhansl-Schmuckler analysis to determine major vs minor, then call
        # asKey(mode) to obtain the correct tonic.
        part = score.parts[0] if score.parts else score.flatten()
        ksigs = part.flatten().getElementsByClass(m21key.KeySignature)
        if ksigs:
            ks = ksigs[0]
            analyzed = score.analyze('key')
            mode = analyzed.mode if analyzed else 'major'
            resolved = ks.asKey(mode)
            tonic = resolved.tonic
            name = tonic.name.replace('-', 'b')
            return tonic.pitchClass, name
    except Exception:
        pass

    # Priority 3: pure statistical analysis (no key-signature element at all)
    try:
        analyzed = score.analyze('key')
        tonic = analyzed.tonic
        name = tonic.name.replace('-', 'b')
        return tonic.pitchClass, name
    except Exception:
        pass

    # Priority 4: first actual note — last resort
    try:
        part = score.parts[0] if score.parts else score.flatten()
        for element in part.flatten().notesAndRests:
            if isinstance(element, m21note.Note) and element.pitch is not None:
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

# Jianpu-ly anacrusis (弱起) duration code mapping.
# Key: pickup duration in quarter lengths (QL); Value: duration code written after ',' in time sig.
# The code is the note-type denominator for \partial in LilyPond:
#   '4' = quarter note pickup (1.0 QL), '8' = eighth (0.5 QL), '16' = sixteenth (0.25 QL), etc.
# jianpu-ly.py line ~1170 uses the same table but keyed in quavers (1 quaver = 0.5 QL),
# so quaver key N maps to our QL key N*0.5 (e.g. quaver 2 → QL 1.0 → code '4').
_QL_TO_ANACRUSIS_CODE: dict[float, str] = {
    0.25: '16', 0.375: '16.', 0.5: '8', 0.75: '8.',
    1.0: '4', 1.5: '4.', 2.0: '2', 3.0: '2.', 4.0: '1', 6.0: '1.',
}
# Anacrusis code → 64th-note units (1 quarter = 16 units)
_ANACRUSIS_CODE_TO_UNITS: dict[str, int] = {
    code: round(ql * 16) for ql, code in _QL_TO_ANACRUSIS_CODE.items()
}


def _detect_trailing_rest_pickup(measure, nominal_length: float) -> Optional[float]:
    """Fallback pickup (弱起) detection for measures where MusicXML has ``implicit="no"``.

    Some OMR engines (e.g. Homr) encode a pickup bar as a *full* measure with
    the actual pickup notes at the start followed by a filler rest — rather than
    using ``implicit="yes"`` (which music21 translates to ``paddingLeft``).

    Detection criteria:
    - The last element in the measure is a rest (trailing-rest pattern).
    - The notes before that rest occupy ≤ half the nominal bar length
      (conservative threshold to avoid false positives on normal first bars
      that simply end with a rest).
    - The note portion matches a recognised anacrusis code to within 0.25 QL.

    Returns the pickup QL duration if detected, else ``None``.
    """
    try:
        elements = sorted(measure.flatten().notesAndRests, key=lambda e: float(e.offset))
    except Exception:
        return None
    if not elements:
        return None
    # Must end with a rest (trailing-rest pattern)
    if not isinstance(elements[-1], m21note.Rest):
        return None
    # Find effective end = offset + duration of last non-rest element
    last_note_end = 0.0
    for e in elements:
        if not isinstance(e, m21note.Rest):
            last_note_end = float(e.offset) + float(e.duration.quarterLength or 0.25)
    if last_note_end < 0.01:
        return None  # all rests — not a pickup
    # Pickup must be ≤ half bar (conservative; avoids treating normal bars as pickups)
    if last_note_end > nominal_length * 0.5 + 0.01:
        return None
    # Match to recognised anacrusis code
    _closest = min(_QL_TO_ANACRUSIS_CODE, key=lambda q: abs(q - last_note_end))
    if abs(_closest - last_note_end) < 0.25:
        return _closest
    return None


def _parse_bar_units(time_signature: str) -> int:
    """Convert a 'num/denom[,pickup]' time signature string to jianpu-ly barLength (64th-note units)."""
    try:
        ts = time_signature.split(',')[0]  # strip optional anacrusis suffix, e.g. "4/4,8" → "4/4"
        num_s, denom_s = ts.split('/')
        return int(64 * int(num_s) / int(denom_s))
    except (ValueError, ZeroDivisionError, AttributeError):
        return 64  # fallback: 4/4


def _parse_anacrusis_units(time_signature: str) -> Optional[int]:
    """Return pickup bar length in 64th-note units if the time signature has an anacrusis suffix (e.g. '4/4,8')."""
    if ',' not in time_signature:
        return None
    code = time_signature.split(',', 1)[1].strip()
    return _ANACRUSIS_CODE_TO_UNITS.get(code)


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


# 安全上限：超过此数量的小节被认为是 MusicXML 解析异常（内部偏移错误）
# 将在日志中发出警告并截断。600 小节 ≈ 150 拍 4/4 = 已足够容纳给小篇音乐。
MAX_SANE_BARS = 600


def extract_jianpu_measures(score, key_tonic_semitone: int = 0) -> tuple[list[list[JianpuNote]], str]:
    """
    Extract jianpu measure list and time signature from a music21 score.
    Merges polyphony by offset; fills gaps and overflows with rests at bar boundaries.
    """
    from ..utils import log_message
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

    # ── Pickup (anacrusis / 弱起) detection ───────────────────────────────────
    # A pickup measure has paddingLeft > 0 in music21 (ported from MusicXML's implicit="yes").
    # Actual pickup duration = nominal_measure_length - paddingLeft.
    _first_measure_length_override: Optional[float] = None
    _first_m = measure_streams[0]
    _padding_left = float(getattr(_first_m, 'paddingLeft', 0.0) or 0.0)
    if _padding_left > 0.01 and _padding_left < nominal_measure_length - 0.01:
        _actual_pickup = nominal_measure_length - _padding_left
        _closest_ql = min(_QL_TO_ANACRUSIS_CODE, key=lambda q: abs(q - _actual_pickup))
        if abs(_closest_ql - _actual_pickup) < 0.25:
            time_signature = f'{time_signature},{_QL_TO_ANACRUSIS_CODE[_closest_ql]}'
            _first_measure_length_override = _closest_ql
    # Fallback: detect pickup from trailing-rest pattern (implicit="no" but notes+filler-rest)
    if _first_measure_length_override is None:
        _fallback_ql = _detect_trailing_rest_pickup(_first_m, nominal_measure_length)
        if _fallback_ql is not None:
            time_signature = f'{time_signature},{_QL_TO_ANACRUSIS_CODE[_fallback_ql]}'
            _first_measure_length_override = _fallback_ql

    tol = 0.01
    for _m_idx, measure in enumerate(measure_streams):
        if _m_idx == 0 and _first_measure_length_override is not None:
            measure_length = _first_measure_length_override
        else:
            measure_length = nominal_measure_length or float(getattr(measure.barDuration, 'quarterLength', 4.0) or 4.0)
        by_offset: dict[float, list[object]] = {}

        # 合并同小节延音线（cross-measure tie-stop 音符在本小节内无对应 start，
        # stripTies 会保留其时值不变；仅合并同小节内的 start→stop 对）
        try:
            measure_src = measure.stripTies(retainContainers=True)
        except Exception:
            measure_src = measure

        # 简谱为单声部格式：若小节包含多声部（homr 有时输出 voice 2），
        # 只取 voice 1（主旋律），完全忽略 voice 2+ 的内容，
        # 避免次要音符或占位休止符干扰 repair_jianpu_measure 的预算计算。
        _voices = list(measure_src.voices)
        if _voices:
            _v1 = next((v for v in _voices if str(v.id) == '1'), _voices[0])
            _flat_all = list(_v1.flatten().notesAndRests)
        else:
            _flat_all = list(measure_src.flatten().notesAndRests)

        for element in _flat_all:
            offset = float(element.offset)
            if offset >= measure_length - tol:
                continue
            # 跨小节 tie-stop/continue：上一小节 tie-start 音符在本小节内的延续。
            # 渲染为延音 dash（'-'），而非独立音符或休止符。
            _tie = getattr(element, 'tie', None)
            if _tie is not None and _tie.type in ('stop', 'continue'):
                available = max(measure_length - offset, 0.125)
                cont_dur = normalize_jianpu_duration(
                    min(float(element.duration.quarterLength or 1.0), available)
                )
                cont = JianpuNote(
                    symbol='-',
                    accidental='',
                    upper_dots=0,
                    lower_dots=0,
                    duration=cont_dur,
                    duration_dots=infer_duration_dots(cont_dur),
                    midi=None,
                    is_rest=False,
                )
                existing = by_offset.get(offset)
                if existing is None:
                    by_offset[offset] = [cont]
                else:
                    existing.append(cont)
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
                gap = min(offset - current_offset, measure_length - current_offset)
                rest = m21note.Rest()
                for piece in split_duration_chunks(gap):
                    rest_piece = m21note.Rest()
                    rest_piece.duration.quarterLength = piece
                    measure_notes.append(note_to_jianpu(rest_piece, key_tonic_semitone))
                    current_offset += piece

            next_offset = offsets[idx + 1] if idx + 1 < len(offsets) else measure_length
            next_offset = min(next_offset, measure_length)
            element_end = offset
            for element in by_offset[offset]:
                if isinstance(element, JianpuNote):
                    measure_notes.append(element)
                    element_end = max(element_end, offset + element.duration)
                else:
                    jn = note_to_jianpu(element, key_tonic_semitone)
                    measure_notes.append(jn)
                    element_end = max(element_end, offset + jn.duration)
            # 填充音符实际结束位置到下一个音符开始位置之间的空白（OMR 漏标休止符的常见情形）
            if element_end < next_offset - tol:
                gap = min(next_offset - element_end, measure_length - element_end)
                for piece in split_duration_chunks(gap):
                    rest_piece = m21note.Rest()
                    rest_piece.duration.quarterLength = piece
                    measure_notes.append(note_to_jianpu(rest_piece, key_tonic_semitone))
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
    from ..utils import log_message
    import logging as _logging
    part = score.parts[0] if score.parts else score.flatten()
    # 合并延音线：同小节 start→stop 对合并为单音符；跨小节 tie 的合并音符由
    # append_element_chunks 正确跨 bar 拆分，因此用 retainContainers=True 即可。
    try:
        part = part.stripTies(retainContainers=True)
    except Exception:
        pass
    time_signature = '4/4'
    bar_length = 4.0
    time_sig = part.recurse().getElementsByClass(meter.TimeSignature)
    if time_sig:
        time_signature = time_sig[0].ratioString
        bar_length = float(getattr(time_sig[0].barDuration, 'quarterLength', 4.0) or 4.0)

    max_score_offset = MAX_SANE_BARS * bar_length  # hard ceiling on note offsets

    # ── Pickup (anacrusis / 弱起) detection ───────────────────────────────────
    _first_pickup_ql: Optional[float] = None
    _trailing_rest_pickup = False  # was pickup inferred from trailing-rest pattern?
    _m_list = list(part.getElementsByClass(stream.Measure))
    if _m_list:
        _pl = float(getattr(_m_list[0], 'paddingLeft', 0.0) or 0.0)
        if _pl > 0.01 and _pl < bar_length - 0.01:
            _act = bar_length - _pl
            _closest = min(_QL_TO_ANACRUSIS_CODE, key=lambda q: abs(q - _act))
            if abs(_closest - _act) < 0.25:
                time_signature = f'{time_signature},{_QL_TO_ANACRUSIS_CODE[_closest]}'
                _first_pickup_ql = _closest
    # Fallback: detect pickup from trailing-rest pattern (implicit="no" but notes+filler-rest)
    if _first_pickup_ql is None and _m_list:
        _fallback_ql = _detect_trailing_rest_pickup(_m_list[0], bar_length)
        if _fallback_ql is not None:
            time_signature = f'{time_signature},{_QL_TO_ANACRUSIS_CODE[_fallback_ql]}'
            _first_pickup_ql = _fallback_ql
            _trailing_rest_pickup = True

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

    # For trailing-rest pickups: rebuild by_offset so that the layout matches
    # what append_element_chunks expects (identical to the implicit="yes" case).
    # Remove filler rests in [pickup_ql, bar_length) and shift all notes at
    # offset >= bar_length by -(bar_length - pickup_ql), so bar 1 starts
    # immediately after the pickup with no silent gap.
    if _trailing_rest_pickup and _first_pickup_ql is not None:
        _shift = bar_length - _first_pickup_ql
        _adjusted: dict[float, object] = {}
        for _off, _elem in by_offset.items():
            if _first_pickup_ql <= _off < bar_length:
                continue  # skip filler rests / elements
            elif _off >= bar_length:
                _adjusted[_off - _shift] = _elem  # shift into correct position
            else:
                _adjusted[_off] = _elem  # keep pickup notes as-is
        by_offset = _adjusted

    offsets = sorted(by_offset.keys())
    if not offsets:
        return [], time_signature

    # 警告：如果最大 offset 超过合理上限
    if offsets[-1] >= max_score_offset * 0.5 and offsets[-1] > bar_length * 30:
        from ..utils import log_message
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
            # 弱起小节：第一小节的边界是 _first_pickup_ql，后续小节按 bar_length 推进
            if not measures and _first_pickup_ql:
                capacity = _first_pickup_ql - current_pos
            elif _first_pickup_ql:
                pos_in_bar = (current_pos - _first_pickup_ql) % bar_length
                capacity = bar_length - pos_in_bar if pos_in_bar > tol else bar_length
            else:
                pos_in_bar = current_pos % bar_length
                capacity = bar_length - pos_in_bar if pos_in_bar > tol else bar_length
            piece_total = min(remaining, max(capacity, 0.0))
            for piece in split_duration_chunks(piece_total):
                element_piece = clone_monophonic_element(element_template, piece)
                current_measure.append(note_to_jianpu(element_piece, key_tonic_semitone))
                current_pos += piece
            remaining -= piece_total
            # 判断当前位置是否恰好是小节边界
            if _first_pickup_ql:
                at_bar_end = (
                    (not measures and abs(current_pos - _first_pickup_ql) < tol)
                    or (measures and abs((current_pos - _first_pickup_ql) % bar_length) < tol)
                )
            else:
                at_bar_end = abs(current_pos % bar_length) < tol
            if at_bar_end:
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
        # 单个音符时寄不得超过 MAX_SANE_BARS 个小节（防止 OMR 输出中的超长音符）
        duration = max(min(next_offset - offset, bar_length * MAX_SANE_BARS), 0.125)
        append_element_chunks(by_offset[offset], duration)

    if current_measure:
        if _first_pickup_ql:
            if not measures:
                pos_in_bar = current_pos  # still in pickup bar
            else:
                pos_in_bar = (current_pos - _first_pickup_ql) % bar_length
        else:
            pos_in_bar = current_pos % bar_length
        if pos_in_bar > tol:
            rest = m21note.Rest()
            append_element_chunks(rest, bar_length - pos_in_bar)
        elif current_measure:
            measures.append(current_measure)

    repaired_measures = [
        repair_jianpu_measure(m, (_first_pickup_ql if ri == 0 and _first_pickup_ql else bar_length))
        for ri, m in enumerate(measures)
    ]
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
    from ..utils import log_message
    import logging as _logging

    key_tonic_semitone, tonic_name = _get_score_key_tonic(score)
    key_header = f'1={tonic_name}'

    measures, time_signature = extract_jianpu_measures(score, key_tonic_semitone)
    header_lines: list[str] = [f'{key_header} {time_signature}', '']

    measures_per_line = choose_measures_per_line(measures)
    log_message(f'[jianpu] parse_score_to_jianpu: {len(measures)} 个小节（每行 {measures_per_line} 个）', _logging.DEBUG)

    for i in range(0, len(measures), measures_per_line):
        line_measures = measures[i:i + measures_per_line]
        try:
            measure_texts = [' '.join(format_jianpu_note_text(note) for note in measure) for measure in line_measures]
            header_lines.append(' | '.join(measure_texts) + ' |')
        except Exception as exc:
            log_message(f'[jianpu] parse_score_to_jianpu 处理小节组 [{i}:{i+measures_per_line}] 失败: {exc}', _logging.WARNING)
            raise

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
    pickup_units = _parse_anacrusis_units(time_sig)
    for i in range(0, len(measures), 4):
        line_measures = measures[i:i + 4]
        measure_texts = [
            ' '.join(jianpu_note_token(note) for note in pad_measure_to_bar(
                m, (pickup_units if (i + mi) == 0 and pickup_units is not None else bar_units)
            ))
            for mi, m in enumerate(line_measures)
        ]
        header.append(' | '.join(measure_texts) + ' |')
    return '\n'.join(header)


def build_jianpu_ly_text(score, title: str, use_strict_timing: bool = False) -> str:
    """Build jianpu-ly plain-text (.txt) content, including title, key, time signature, and measures."""
    from ..utils import log_message
    import logging as _logging

    key_tonic_semitone, tonic_name = _get_score_key_tonic(score)

    header = ['% jianpu-ly.py', f'title={title}', f'1={tonic_name}']

    measures, time_signature = extract_strict_jianpu_measures(score, key_tonic_semitone) if use_strict_timing else extract_jianpu_measures(score, key_tonic_semitone)
    header.append(time_signature)
    header.append('')

    bar_units = _parse_bar_units(time_signature)
    pickup_units = _parse_anacrusis_units(time_signature)
    measures_per_line = 4

    log_message(f'[jianpu] 开始处理 {len(measures)} 个小节（每行 {measures_per_line} 个）', _logging.DEBUG)

    for i in range(0, len(measures), measures_per_line):
        line_measures = measures[i:i + measures_per_line]
        try:
            measure_texts = [
                ' '.join(jianpu_note_token(note) for note in pad_measure_to_bar(
                    m, (pickup_units if (i + mi) == 0 and pickup_units is not None else bar_units)
                ))
                for mi, m in enumerate(line_measures)
            ]
            header.append(' | '.join(measure_texts) + ' |')
        except Exception as exc:
            log_message(f'[jianpu] 处理小节组 [{i}:{i+measures_per_line}] 失败: {exc}', _logging.WARNING)
            raise

    return '\n'.join(header)


