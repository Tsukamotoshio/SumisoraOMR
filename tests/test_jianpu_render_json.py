# tests/test_jianpu_render_json.py — unit tests for the 阶段3.2 conversion
# layer (core/notation/jianpu/render_json.py: jianpu_section_to_render_json()).
from core.notation.jianpu.parser import parse_jianpu_ly_text
from core.notation.jianpu.render_json import (
    DEFAULT_PLAYBACK_TEMPO,
    jianpu_section_to_render_json,
)


def test_simple_measure_produces_one_note_per_token():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 3 4 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert render['notes'] == [
        {'start': 0.0, 'length': 1.0, 'pitch': 60, 'intensity': 80},
        {'start': 1.0, 'length': 1.0, 'pitch': 62, 'intensity': 80},
        {'start': 2.0, 'length': 1.0, 'pitch': 64, 'intensity': 80},
        {'start': 3.0, 'length': 1.0, 'pitch': 65, 'intensity': 80},
    ]


def test_rests_become_gaps_not_note_entries():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 0 2 0 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    # Two real notes only; the second one's start (2.0) skips over the rest
    # at [1.0, 2.0) — JianpuRender infers that gap as a rest on its own.
    assert render['notes'] == [
        {'start': 0.0, 'length': 1.0, 'pitch': 60, 'intensity': 80},
        {'start': 2.0, 'length': 1.0, 'pitch': 62, 'intensity': 80},
    ]


def test_dash_continuations_extend_the_previous_note_length():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\nq1 q- q- q- |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    # Four q (eighth-note, 0.5ql) tokens tied together -> one note, length 2.0.
    assert render['notes'] == [{'start': 0.0, 'length': 2.0, 'pitch': 60, 'intensity': 80}]


def test_time_signature_parsed_ignoring_anacrusis_suffix():
    doc = parse_jianpu_ly_text('title=T\n1=C\n3/4,8\n\nq1 2 3 4 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert render['timeSignatures'] == [{'start': 0, 'numerator': 3, 'denominator': 4}]


def test_key_signature_uses_relative_major_semitone_for_minor_header():
    # '6=A' (A minor) reduces to its relative major C, semitone 0 — same
    # convention key_header_tonic_semitone()/note_to_jianpu() already use.
    doc = parse_jianpu_ly_text('title=T\n6=A\n4/4\n\n6 7 1 2 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert render['keySignatures'] == [{'start': 0, 'key': 0}]


def test_tempo_is_carried_through_for_playback():
    # 阶段4.1: playback turns the unitless quarter-note start/length values
    # into seconds, so the document's `4=N` line has to reach the renderer.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n4=88\n\n1 2 3 4 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header, doc.tempo)
    assert render['tempos'] == [{'start': 0, 'qpm': 88}]


def test_tempo_falls_back_to_the_project_default_when_absent():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 3 4 |\n')
    assert doc.tempo == 0, 'no 4=N line means JianpuDoc.tempo stays 0'
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header, doc.tempo)
    assert render['tempos'] == [{'start': 0, 'qpm': DEFAULT_PLAYBACK_TEMPO}]
