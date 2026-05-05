# core/notation/jianpu/__init__.py — Public API for Jianpu conversion.
# Orchestration functions live here; sub-modules provide the building blocks.
import logging as _logging
from typing import Optional

from ...utils import log_message
from .primitives import (
    _get_score_key_tonic,
    clone_jianpu_note,
    duration_suffix,
    format_jianpu_note_text,
    get_duration_render,
    infer_duration_dots,
    jianpu_note_token,
    jianpu_octave_symbol,
    normalize_jianpu_duration,
    note_to_jianpu,
    split_duration_chunks,
    MAX_SANE_BARS,
    _DIATONIC_SEMITONES,
    _CHROMATIC_SHARP,
    _CHROMATIC_FLAT,
    _FLAT_KEY_SEMITONES,
    _DURATION_TO_UNITS,
    _ALLOWED_UNITS_DESC,
    _QL_TO_ANACRUSIS_CODE,
    _ANACRUSIS_CODE_TO_UNITS,
)
from .measure import (
    _detect_trailing_rest_pickup,
    _parse_bar_units,
    _parse_anacrusis_units,
    clone_monophonic_element,
    pad_measure_to_bar,
    repair_jianpu_measure,
)
from .extract import (
    _extract_part_repeat_barlines,
    _get_voice_ids_in_part,
    _secondary_voice_overlaps_primary,
    extract_jianpu_measures,
    extract_strict_jianpu_measures,
)

from ...config import JianpuNote  # re-export for callers that import it from here


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
    composer: str = '',
    tempo: int = 0,
) -> str:
    """Build jianpu-ly plain-text from pre-computed JianpuNote measures (bypassing score parsing).

    Equivalent to ``build_jianpu_ly_text`` but accepts already-parsed note data
    instead of a music21 Score object.  Used by the dual-engine fusion path.
    """
    header = ['% jianpu-ly.py', f'title={title}']
    if composer:
        header.append(f'composer={composer}')
    header.append(f'1={tonic_name}')
    header.append(time_sig)
    if tempo > 0:
        header.append(f'4={tempo}')
    header.append('')
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


def build_jianpu_ly_text(score, title: str, use_strict_timing: bool = False,
                          composer: str = '', tempo: int = 0,
                          _return_groups: bool = False):
    """Build jianpu-ly plain-text for all parts in the score.

    For single-part scores the output is identical to the previous behaviour,
    except that when the single part contains multiple non-rest voices they are
    each extracted as a separate ``NextPart`` section (capped at 4 voices).

    For multi-part scores each part becomes a separate jianpu section; parts
    that contain multiple non-rest voices are further split so each voice gets
    its own section.

    Parameters
    ----------
    _return_groups
        When *True* the function returns ``(text, voice_groups)`` instead of
        just *text*.  ``voice_groups`` is a ``list[list[int]]`` where each
        inner list contains the 0-based section indices that belong to the same
        Part and should therefore be rendered on the *same* jianpu staff as
        simultaneous polyphonic voices.  Sections with a group of length 1 are
        monophonic and need no merging.
    """
    key_tonic_semitone, tonic_name = _get_score_key_tonic(score)

    if tempo <= 0:
        try:
            from music21 import tempo as m21tempo
            tempos = list(score.flatten().getElementsByClass(m21tempo.MetronomeMark))
            if tempos:
                tempo = int(round(tempos[0].number))
        except Exception:
            pass

    # ── Collect one measures-list per output section ──────────────────────────
    _parts = list(score.parts) if score.parts else []
    _sections: list[list[list[JianpuNote]]] = []
    _voice_groups: list[list[int]] = []   # parallel to _sections groups
    _time_sig: str = '4/4'
    _ts_set = False

    if len(_parts) <= 1:
        part = _parts[0] if _parts else None
        # Detect multiple voices in the single part (skip in strict mode which flattens)
        _v_ids = _get_voice_ids_in_part(part) if (not use_strict_timing and part is not None) else []

        if len(_v_ids) <= 1:
            # Single voice (or strict mode) — existing behaviour
            fn = extract_strict_jianpu_measures if use_strict_timing else extract_jianpu_measures
            measures, _time_sig = fn(score, key_tonic_semitone)
            _sections = [measures]
            _voice_groups = [[0]]
        else:
            # Multiple voices within a single part — extract each voice separately
            log_message(
                f'[jianpu] 单声部多声道: {len(_v_ids)} 个声道 ({", ".join(str(v) for v in _v_ids[:4])})',
                _logging.DEBUG,
            )
            _primary_vid = _v_ids[0]
            _group_idxs: list[int] = []
            for i, vid in enumerate(_v_ids[:4]):
                # 跳过伪复声部：次声部音符 onset 严格落在主声部音符时值内 → OMR 误判
                if i > 0 and part is not None and _secondary_voice_overlaps_primary(part, _primary_vid, vid):
                    log_message(
                        f'[jianpu] 跳过伪复声部 voice={vid}（音符与主声部重叠，判定为 OMR 误判）',
                        _logging.DEBUG,
                    )
                    continue
                measures, ts = extract_jianpu_measures(
                    score, key_tonic_semitone, _part=part, _voice_id=vid,
                    _multi_voice_mode=True, _is_primary_voice=(i == 0))
                if not _ts_set:
                    _time_sig, _ts_set = ts, True
                if measures:
                    _group_idxs.append(len(_sections))
                    _sections.append(measures)
            _voice_groups = [_group_idxs] if _group_idxs else [[0]]
    else:
        # Multi-part path: one section per part / voice combination
        for part in _parts:
            if use_strict_timing:
                # Strict mode flattens all voices; emit one section per part
                measures, ts = extract_strict_jianpu_measures(score, key_tonic_semitone, _part=part)
                if not _ts_set:
                    _time_sig, _ts_set = ts, True
                if measures:
                    _voice_groups.append([len(_sections)])
                    _sections.append(measures)
            else:
                voice_ids = _get_voice_ids_in_part(part)
                if len(voice_ids) <= 1:
                    # No multi-voice structure — one section for the whole part
                    measures, ts = extract_jianpu_measures(score, key_tonic_semitone, _part=part)
                    if not _ts_set:
                        _time_sig, _ts_set = ts, True
                    if measures:
                        _voice_groups.append([len(_sections)])
                        _sections.append(measures)
                else:
                    # Multiple voices: one section per voice (max 4)
                    group_indices: list[int] = []
                    _primary_vid_mp = voice_ids[0]
                    for i, vid in enumerate(voice_ids[:4]):
                        # 跳过伪复声部：次声部音符 onset 严格落在主声部音符时值内 → OMR 误判
                        if i > 0 and _secondary_voice_overlaps_primary(part, _primary_vid_mp, vid):
                            log_message(
                                f'[jianpu] 跳过伪复声部 voice={vid}（音符与主声部重叠，判定为 OMR 误判）',
                                _logging.DEBUG,
                            )
                            continue
                        measures, ts = extract_jianpu_measures(
                            score, key_tonic_semitone, _part=part, _voice_id=vid,
                            _multi_voice_mode=True, _is_primary_voice=(i == 0))
                        if not _ts_set:
                            _time_sig, _ts_set = ts, True
                        if measures:
                            group_indices.append(len(_sections))
                            _sections.append(measures)
                    if group_indices:
                        _voice_groups.append(group_indices)

    # ── Assemble jianpu-ly text ───────────────────────────────────────────────
    header = ['% jianpu-ly.py', f'title={title}']
    if composer:
        header.append(f'composer={composer}')
    header.append(f'1={tonic_name}')
    header.append(_time_sig)
    if tempo > 0:
        header.append(f'4={tempo}')
    header.append('')

    bar_units   = _parse_bar_units(_time_sig)
    pickup_units = _parse_anacrusis_units(_time_sig)
    measures_per_line = 4

    log_message(
        f'[jianpu] build_jianpu_ly_text: {len(_sections)} 个声部段落，共 '
        f'{sum(len(s) for s in _sections)} 个小节', _logging.DEBUG,
    )

    for section_idx, measures in enumerate(_sections):
        if section_idx > 0:
            header.append('NextPart')
            header.append(_time_sig)  # jianpu-ly resets barLength per part; repeat time sig
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
                log_message(
                    f'[jianpu] 处理声部 {section_idx} 小节组 [{i}:{i+measures_per_line}] 失败: {exc}',
                    _logging.WARNING,
                )
                raise

    text = '\n'.join(header)
    if _return_groups:
        _first_part = _parts[0] if _parts else None
        _repeat_barlines: dict[int, dict[str, bool]] = (
            _extract_part_repeat_barlines(_first_part) if _first_part is not None else {}
        )
        return text, _voice_groups, _repeat_barlines
    return text
