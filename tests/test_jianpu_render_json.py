# tests/test_jianpu_render_json.py — unit tests for the 阶段3.2 conversion
# layer (core/notation/jianpu/render_json.py: jianpu_section_to_render_json()).
from core.notation.jianpu.parser import parse_jianpu_ly_text
from core.notation.jianpu.render_json import (
    DEFAULT_PLAYBACK_TEMPO,
    jianpu_section_to_render_json,
)


def _ref(measure, index):
    return {'measure': measure, 'index': index}


def test_simple_measure_produces_one_note_per_token():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 3 4 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert render['notes'] == [
        {'start': 0.0, 'length': 1.0, 'pitch': 60, 'intensity': 80, 'ref': _ref(0, 0)},
        {'start': 1.0, 'length': 1.0, 'pitch': 62, 'intensity': 80, 'ref': _ref(0, 1)},
        {'start': 2.0, 'length': 1.0, 'pitch': 64, 'intensity': 80, 'ref': _ref(0, 2)},
        {'start': 3.0, 'length': 1.0, 'pitch': 65, 'intensity': 80, 'ref': _ref(0, 3)},
    ]


def test_rests_become_gaps_not_note_entries():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 0 2 0 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    # Two real notes only; the second one's start (2.0) skips over the rest
    # at [1.0, 2.0) — JianpuRender infers that gap as a rest on its own.
    # Note the refs: the second drawn note is model index 2, not 1. That gap
    # between drawn position and model position is exactly why the ref exists.
    assert render['notes'] == [
        {'start': 0.0, 'length': 1.0, 'pitch': 60, 'intensity': 80, 'ref': _ref(0, 0)},
        {'start': 2.0, 'length': 1.0, 'pitch': 62, 'intensity': 80, 'ref': _ref(0, 2)},
    ]


def test_dash_continuations_extend_the_previous_note_length():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\nq1 q- q- q- |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    # Four q (eighth-note, 0.5ql) tokens tied together -> one note, length 2.0.
    # The ref points at the note that was struck, not at any of the dashes.
    assert render['notes'] == [
        {'start': 0.0, 'length': 2.0, 'pitch': 60, 'intensity': 80, 'ref': _ref(0, 0)},
    ]


def test_refs_address_the_right_note_across_measures():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 0 2 | 0 0 3 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    refs = [n['ref'] for n in render['notes']]
    assert refs == [_ref(0, 0), _ref(0, 2), _ref(1, 2)]
    # And each ref really does select the note that was drawn.
    for note, ref in zip(render['notes'], refs, strict=True):
        model_note = doc.sections[0].measures[ref['measure']][ref['index']]
        assert model_note.midi == note['pitch']


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


def test_slots_cover_every_model_note_including_rests_and_dashes():
    # A cursor has to be able to sit on rests and dashes: a blank score is made
    # of nothing else, and that is exactly the score a user needs to type into.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n0 - - - |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert render['notes'] == [], 'a whole-measure rest draws no notes at all'
    assert [s['ref'] for s in render['slots']] == [_ref(0, 0), _ref(0, 1), _ref(0, 2), _ref(0, 3)]
    assert [s['start'] for s in render['slots']] == [0.0, 1.0, 2.0, 3.0]
    assert [s['is_rest'] for s in render['slots']] == [True, False, False, False]
    assert [s['is_dash'] for s in render['slots']] == [False, True, True, True]


def test_slot_starts_match_the_drawn_note_starts():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 0 q2 q3 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    by_ref = {(s['ref']['measure'], s['ref']['index']): s['start'] for s in render['slots']}
    for note in render['notes']:
        key = (note['ref']['measure'], note['ref']['index'])
        assert by_ref[key] == note['start'], f'slot and drawn note disagree at {key}'


def test_slots_span_measures_and_accumulate_time_continuously():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 | 3 4 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert [(s['ref']['measure'], s['ref']['index'], s['start']) for s in render['slots']] == [
        (0, 0, 0.0), (0, 1, 1.0), (1, 0, 2.0), (1, 1, 3.0),
    ]


def test_total_length_covers_rests_the_note_list_omits():
    # The notes stop at beat 1, but the score is four beats long. Without this
    # the renderer sizes the score by its notes and draws nothing after the
    # last one — no trailing rests, and so nowhere for an editing cursor to go.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 0 0 0 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert render['notes'][-1]['start'] + render['notes'][-1]['length'] == 1.0
    assert render['totalLength'] == 4.0


def test_total_length_of_an_all_rest_score_is_still_its_real_length():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n0 - - - | 0 - - - |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert render['notes'] == []
    assert render['totalLength'] == 8.0


def test_total_length_matches_the_end_of_the_last_slot():
    doc = parse_jianpu_ly_text('title=T\n1=C\n3/4\n\n1 2 3 | q4 q5 6 7 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    last = render['slots'][-1]
    assert render['totalLength'] == last['start'] + last['duration']
