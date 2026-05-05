# core/notation/jianpu/primitives.py — Note/duration primitives for Jianpu conversion.
from typing import Optional

from music21 import chord as m21chord, note as m21note

from ...config import (
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

# 安全上限：超过此数量的小节被认为是 MusicXML 解析异常（内部偏移错误）
# 将在日志中发出警告并截断。600 小节 ≈ 150 拍 4/4 = 已足够容纳给小篇音乐。
MAX_SANE_BARS = 600
