# core/jianpu_core.py — 简谱音符/小节转换逻辑
# 拆分自 convert.py
from typing import Optional

from music21 import chord as m21chord, meter, note as m21note, stream

from .config import (
    ALLOWED_JIANPU_DURATIONS,
    JIANPU_MAP,
    JianpuNote,
)


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


def note_to_jianpu(element) -> JianpuNote:
    """Convert a music21 note or rest to a JianpuNote dataclass."""
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
    base = JIANPU_MAP.get(pitch.step, '?') if pitch is not None else '?'
    accidental = ''
    if pitch is not None and pitch.accidental is not None:
        alter = pitch.accidental.alter
        if alter == 1:
            accidental = '#'
        elif alter == -1:
            accidental = 'b'

    upper_dots = 0
    lower_dots = 0
    if pitch is not None and pitch.octave is not None:
        diff = pitch.octave - 4
        if diff > 0:
            upper_dots = diff
        elif diff < 0:
            lower_dots = -diff

    return JianpuNote(
        symbol=base,
        accidental=accidental,
        upper_dots=upper_dots,
        lower_dots=lower_dots,
        duration=float(element.duration.quarterLength),
        duration_dots=int(getattr(element.duration, 'dots', 0)),
        midi=getattr(pitch, 'midi', None),
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


def extract_jianpu_measures(score) -> tuple[list[list[JianpuNote]], str]:
    """
    Extract jianpu measure list and time signature from a music21 score.
    Merges polyphony by offset; fills gaps and overflows with rests at bar boundaries.
    """
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
        fallback_notes = [note_to_jianpu(element) for element in part.flatten().notesAndRests]
        return ([fallback_notes] if fallback_notes else []), time_signature

    tol = 0.01
    for measure in measure_streams:
        measure_length = nominal_measure_length or float(getattr(measure.barDuration, 'quarterLength', 4.0) or 4.0)
        by_offset: dict[float, object] = {}

        for element in measure.flatten().notesAndRests:
            offset = float(element.offset)
            if offset >= measure_length - tol:
                continue
            available = max(measure_length - offset, 0.125)
            candidate = clone_monophonic_element(element, min(float(element.duration.quarterLength or 0.25), available))
            existing = by_offset.get(offset)
            if existing is None:
                by_offset[offset] = candidate
            elif isinstance(existing, m21note.Rest) and not isinstance(candidate, m21note.Rest):
                by_offset[offset] = candidate
            elif isinstance(existing, m21note.Note) and isinstance(candidate, m21note.Note) and candidate.pitch.midi > existing.pitch.midi:
                by_offset[offset] = candidate

        if not by_offset:
            rest = m21note.Rest()
            rest.duration.quarterLength = measure_length
            measures.append(repair_jianpu_measure([note_to_jianpu(rest)], measure_length))
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
                measure_notes.append(note_to_jianpu(rest))

            next_offset = offsets[idx + 1] if idx + 1 < len(offsets) else measure_length
            next_offset = min(next_offset, measure_length)
            span = max(min(next_offset - offset, measure_length - offset), 0.125)
            element = clone_monophonic_element(by_offset[offset], span)
            measure_notes.append(note_to_jianpu(element))
            current_offset = next_offset

        if current_offset < measure_length - tol:
            rest = m21note.Rest()
            rest.duration.quarterLength = normalize_jianpu_duration(measure_length - current_offset)
            measure_notes.append(note_to_jianpu(rest))

        measures.append(repair_jianpu_measure(measure_notes, measure_length))

    return measures, time_signature


def extract_strict_jianpu_measures(score) -> tuple[list[list[JianpuNote]], str]:
    """
    Extract jianpu measures by re-slicing all notes strictly by bar length,
    ignoring the score's own Measure objects.
    """
    part = score.parts[0] if score.parts else score.flatten()
    time_signature = '4/4'
    bar_length = 4.0
    time_sig = part.recurse().getElementsByClass(meter.TimeSignature)
    if time_sig:
        time_signature = time_sig[0].ratioString
        bar_length = float(getattr(time_sig[0].barDuration, 'quarterLength', 4.0) or 4.0)

    by_offset: dict[float, object] = {}
    for element in part.flatten().notesAndRests:
        offset = float(element.offset)
        if offset < 0:
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

    measures: list[list[JianpuNote]] = []
    current_measure: list[JianpuNote] = []
    current_pos = 0.0
    tol = 0.01

    def append_element_chunks(element_template, total_duration: float) -> None:
        """Split an element across bar boundaries and append chunks to the current measure."""
        nonlocal current_measure, current_pos, measures
        remaining = total_duration
        while remaining > tol:
            pos_in_bar = current_pos % bar_length
            capacity = bar_length - pos_in_bar if pos_in_bar > tol else bar_length
            piece_total = min(remaining, capacity)
            for piece in split_duration_chunks(piece_total):
                element_piece = clone_monophonic_element(element_template, piece)
                current_measure.append(note_to_jianpu(element_piece))
                current_pos += piece
            remaining -= piece_total
            if abs(current_pos % bar_length) < tol:
                measures.append(current_measure)
                current_measure = []

    for idx, offset in enumerate(offsets):
        if offset > current_pos + tol:
            rest = m21note.Rest()
            append_element_chunks(rest, offset - current_pos)

        next_offset = offsets[idx + 1] if idx + 1 < len(offsets) else offset + float(by_offset[offset].duration.quarterLength or 0.25)
        duration = max(next_offset - offset, 0.125)
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
    try:
        key_obj = score.analyze('key') if score else None
    except Exception:
        key_obj = None

    key_header = '1=C'
    if key_obj is not None and key_obj.tonic is not None:
        key_header = f'1={key_obj.tonic.name}'

    measures, time_signature = extract_jianpu_measures(score)
    header_lines: list[str] = [f'{key_header} {time_signature}', '']

    measures_per_line = choose_measures_per_line(measures)
    for i in range(0, len(measures), measures_per_line):
        line_measures = measures[i:i + measures_per_line]
        measure_texts = [' '.join(format_jianpu_note_text(note) for note in measure) for measure in line_measures]
        header_lines.append(' | '.join(measure_texts) + ' |')

    return measures, header_lines, time_signature


def build_jianpu_ly_text(score, title: str, use_strict_timing: bool = False) -> str:
    """Build jianpu-ly plain-text (.txt) content, including title, key, time signature, and measures."""
    try:
        key_obj = score.analyze('key') if score else None
    except Exception:
        key_obj = None

    header = ['% jianpu-ly.py', f'title={title}']
    if key_obj is not None and key_obj.tonic is not None:
        tonic = key_obj.tonic.name
        if key_obj.mode == 'minor':
            header.append(f'6={tonic}')
        else:
            header.append(f'1={tonic}')
    else:
        header.append('1=C')

    measures, time_signature = extract_strict_jianpu_measures(score) if use_strict_timing else extract_jianpu_measures(score)
    header.append(time_signature)
    header.append('')

    measures_per_line = 4
    for i in range(0, len(measures), measures_per_line):
        line_measures = measures[i:i + measures_per_line]
        measure_texts = [' '.join(jianpu_note_token(note) for note in measure) for measure in line_measures]
        header.append(' | '.join(measure_texts) + ' |')

    return '\n'.join(header)
