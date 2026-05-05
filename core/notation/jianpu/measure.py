# core/notation/jianpu/measure.py — Measure-level operations for Jianpu conversion.
from typing import Optional

from music21 import chord as m21chord, note as m21note

from ...config import JianpuNote
from .primitives import (
    _ALLOWED_UNITS_DESC,
    _ANACRUSIS_CODE_TO_UNITS,
    _DURATION_TO_UNITS,
    _QL_TO_ANACRUSIS_CODE,
    clone_jianpu_note,
    normalize_jianpu_duration,
    split_duration_chunks,
)


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
