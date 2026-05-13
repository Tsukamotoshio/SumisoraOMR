# core/notation/jianpu/extract.py — Voice analysis and score extraction for Jianpu.
import logging as _logging
from typing import Optional

from music21 import chord as m21chord, note as m21note, stream

from ...config import JianpuNote
from ...utils import log_message
from .primitives import (
    MAX_SANE_BARS,
    _QL_TO_ANACRUSIS_CODE,
    infer_duration_dots,
    normalize_jianpu_duration,
    note_to_jianpu,
    split_duration_chunks,
)
from .measure import (
    _detect_trailing_rest_pickup,
    _parse_anacrusis_units,
    clone_monophonic_element,
    repair_jianpu_measure,
)


def _get_voice_ids_in_part(part) -> list[str]:
    """Return sorted voice IDs that contain significant non-rest content.

    A voice is significant — and warrants its own jianpu polyphony track — when:
      1. it has actual notes in at least 2 distinct measures (the original guard
         against single-measure harmony chords triggering multi-voice extraction), AND
      2. it is either the most prolific voice in the part (always kept), OR
         it meets the secondary-voice significance test (see below).

    Secondary-voice significance test (both of the following must be true):
      a. note count >= 10 % of the primary voice's note count, AND
      b. EITHER note count >= 8 (the original guard against 2-measure OMR stray voices)
         OR the voice spans at least 4 distinct measures.

    Rationale for the OR branch: OMR stray voices typically appear in exactly 2
    (occasionally 3) consecutive measures with a handful of notes.  A voice spread
    across 4 or more measures — even with fewer than 8 total notes — is far more
    likely to be a genuine polyphonic voice (e.g. a later-entering canon voice that
    is only sparsely tagged with voice numbers in the MusicXML) than an OMR artefact.
    The 10 % floor prevents near-empty one-off notes from being promoted regardless.
    """
    voice_stats: dict[str, tuple[int, int]] = {}  # vid -> (measure_count, note_count)
    for measure in part.getElementsByClass(stream.Measure):
        for voice in measure.voices:
            n_notes = sum(1 for e in voice.flatten().notes if isinstance(e, m21note.Note))
            if n_notes > 0:
                vid = str(voice.id)
                m_count, prev = voice_stats.get(vid, (0, 0))
                voice_stats[vid] = (m_count + 1, prev + n_notes)

    candidates = [vid for vid, (m_count, _) in voice_stats.items() if m_count >= 2]
    if len(candidates) <= 1:
        return sorted(candidates)

    primary_vid = max(candidates, key=lambda v: voice_stats[v][1])
    primary_notes = voice_stats[primary_vid][1]
    return sorted(
        vid for vid in candidates
        if vid == primary_vid
        or (
            voice_stats[vid][1] >= primary_notes * 0.10
            and (voice_stats[vid][1] >= 8 or voice_stats[vid][0] >= 4)
        )
    )


def _secondary_voice_overlaps_primary(
    part, primary_vid: str, secondary_vid: str, overlap_threshold: float = 0.5
) -> bool:
    """Return True when the secondary voice is likely OMR false-polyphony.

    For each non-rest note in the secondary voice, checks whether its onset falls
    *strictly inside* the duration span of any primary-voice note in the same measure
    (i.e. p_start < onset < p_end).  Notes that start simultaneously with a primary
    note (chords) or exactly at the primary note's end are intentionally excluded.

    If the fraction of such "inside" notes exceeds *overlap_threshold* the secondary
    voice is treated as a duplicate detection artifact rather than genuine counterpoint,
    and the caller should skip it to prevent visual number overlap in the jianpu output.
    """
    total = 0
    inside = 0
    for measure in part.getElementsByClass(stream.Measure):
        v_list = list(measure.voices)
        if not v_list:
            continue
        v1 = next((v for v in v_list if str(v.id) == primary_vid), None)
        v2 = next((v for v in v_list if str(v.id) == secondary_vid), None)
        if v1 is None or v2 is None:
            continue
        # 主声部非休止符元素的时值区间 (onset, onset+dur)
        p_spans: list[tuple[float, float]] = [
            (float(el.offset), float(el.offset) + float(el.duration.quarterLength or 0.25))
            for el in v1.flatten().notesAndRests
            if isinstance(el, (m21note.Note, m21chord.Chord))
        ]
        for el in v2.flatten().notesAndRests:
            if not isinstance(el, (m21note.Note, m21chord.Chord)):
                continue
            total += 1
            onset = float(el.offset)
            # 严格落在主声部音符时值内部（不含同时起音和恰好在结束点的情形）
            if any(p_start < onset < p_end for p_start, p_end in p_spans):
                inside += 1
    return total > 0 and (inside / total) >= overlap_threshold


def _extract_part_repeat_barlines(part) -> dict[int, dict[str, bool]]:
    """Return {measure_index: {'start': bool, 'end': bool}} for measures with repeat barlines.

    'start' — measure's left barline is a start-repeat (⌃|:)
    'end'   — measure's right barline is an end-repeat (:|)
    """
    try:
        from music21 import bar as m21bar
    except ImportError:
        return {}

    result: dict[int, dict[str, bool]] = {}
    measures = list(part.getElementsByClass('Measure'))
    if len(measures) > MAX_SANE_BARS:
        measures = measures[:MAX_SANE_BARS]

    for i, measure in enumerate(measures):
        lb = getattr(measure, 'leftBarline', None)
        rb = getattr(measure, 'rightBarline', None)
        has_start = isinstance(lb, m21bar.Repeat) and getattr(lb, 'direction', None) == 'start'
        has_end   = isinstance(rb, m21bar.Repeat) and getattr(rb, 'direction', None) == 'end'
        if has_start or has_end:
            result[i] = {'start': has_start, 'end': has_end}

    # Validate: drop repeat sections that span too few notes (likely Audiveris false positives).
    # Pair each start-repeat measure with the nearest following end-repeat measure;
    # if the section contains fewer than 4 non-rest notes, discard the entire pair.
    _MIN_NOTES = 4
    starts = sorted(mi for mi, f in result.items() if f.get('start'))
    ends   = sorted(mi for mi, f in result.items() if f.get('end'))
    _used_ends: set[int] = set()
    _drop: set[int] = set()
    for _s in starts:
        _e = next((x for x in ends if x >= _s and x not in _used_ends), None)
        if _e is not None:
            _used_ends.add(_e)
            _note_count = sum(
                1 for _mi in range(_s, min(_e + 1, len(measures)))
                for _el in measures[_mi].flatten().notesAndRests
                if not getattr(_el, 'isRest', True)
            )
            if _note_count < _MIN_NOTES:
                _drop.add(_s)
                _drop.add(_e)
    for _mi in _drop:
        result.pop(_mi, None)

    return result


def extract_jianpu_measures(score, key_tonic_semitone: int = 0,
                             _part=None, _voice_id: str = '1',
                             _multi_voice_mode: bool = False,
                             _is_primary_voice: bool = True) -> tuple[list[list[JianpuNote]], str]:
    """
    Extract jianpu measure list and time signature from a music21 score.
    Merges polyphony by offset; fills gaps and overflows with rests at bar boundaries.

    Parameters
    ----------
    _part              : If provided, use this Part stream directly instead of score.parts[0].
    _voice_id          : Which voice ID to extract when a measure contains multiple voices.
                         Defaults to '1' (main melody).
    _multi_voice_mode  : When True (multi-voice extraction), a measure that lacks the
                         requested voice emits a whole-measure rest instead of falling
                         back to another voice's content.  This prevents secondary voices
                         from duplicating the primary melody in monophonic measures.
    _is_primary_voice  : When *_multi_voice_mode* is True, the primary voice still gets the
                         flat measure content when the measure has no explicit voice
                         containers.  Secondary voices (``_is_primary_voice=False``) emit
                         rests in such measures.
    """
    part = _part if _part is not None else (score.parts[0] if score.parts else score.flatten())
    time_signature = '4/4'
    nominal_measure_length = 4.0
    time_sig = part.recurse().getElementsByClass('TimeSignature')
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

        # 简谱为单声部格式；使用 _voice_id 参数选取目标声部。
        # 单声部模式下 _voice_id='1'，多声部渲染时由调用方传入具体 ID。
        _voices = list(measure_src.voices)
        if _voices:
            _matching_voice = next((v for v in _voices if str(v.id) == _voice_id), None)
            if _matching_voice is not None:
                _flat_all = list(_matching_voice.flatten().notesAndRests)
            elif _multi_voice_mode:
                # Multi-voice mode: this voice doesn't exist in this measure → whole-measure rest.
                # Do NOT fall back to another voice's content.
                _flat_all = []
            else:
                # Single-voice mode fallback: pick the voice with the most non-rest notes.
                # music21 sometimes places a rest-only Voice container alongside other voices
                # that hold the actual notes (e.g. when an OMR-tagged sub-voice steals the
                # measure's content from the nominal main voice); falling back to _voices[0]
                # in that case would silently drop all real notes for the measure.
                _best_voice = max(
                    _voices,
                    key=lambda v: sum(
                        1 for e in v.flatten().notes if isinstance(e, m21note.Note)
                    ),
                )
                _flat_all = list(_best_voice.flatten().notesAndRests)
        else:
            if _multi_voice_mode and not _is_primary_voice:
                # No voice containers in this measure + secondary voice.
                # HOMR sometimes encodes 2-voice polyphony with <chord/> tags instead of
                # separate <voice> containers.  When chord objects exist, the lower pitch
                # belongs to the secondary voice; recover those notes rather than emitting
                # a whole-measure rest.  Single notes (no chord sibling) belong solely to
                # the primary voice and are left alone.
                _chord_lowers: list[m21note.Note] = []
                for _el in measure_src.flatten().notesAndRests:
                    if isinstance(_el, m21chord.Chord) and len(_el.pitches) >= 2:
                        _lower_p = min(_el.pitches, key=lambda p: p.midi)
                        _lower_n = m21note.Note(_lower_p)
                        _lower_n.duration.quarterLength = float(_el.duration.quarterLength or 0.25)
                        _lower_n.offset = float(_el.offset)
                        _chord_lowers.append(_lower_n)
                _flat_all = _chord_lowers  # remains [] when no chords → whole-measure rest
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


def extract_strict_jianpu_measures(score, key_tonic_semitone: int = 0,
                                    _part=None) -> tuple[list[list[JianpuNote]], str]:
    """
    Extract jianpu measures by re-slicing all notes strictly by bar length,
    ignoring the score's own Measure objects.

    Parameters
    ----------
    _part : If provided, use this Part stream directly instead of score.parts[0].
            All voices are merged (flattened) — use extract_jianpu_measures with
            _voice_id for per-voice extraction.
    """
    part = _part if _part is not None else (score.parts[0] if score.parts else score.flatten())
    # 合并延音线：同小节 start→stop 对合并为单音符；跨小节 tie 的合并音符由
    # append_element_chunks 正确跨 bar 拆分，因此用 retainContainers=True 即可。
    try:
        part = part.stripTies(retainContainers=True)
    except Exception:
        pass
    time_signature = '4/4'
    bar_length = 4.0
    time_sig = part.recurse().getElementsByClass('TimeSignature')
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

        next_offset = offsets[idx + 1] if idx + 1 < len(offsets) else offset + float(by_offset[offset].duration.quarterLength or 0.25)  # type: ignore[union-attr]
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
