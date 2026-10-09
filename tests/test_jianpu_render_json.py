# tests/test_jianpu_render_json.py — unit tests for the 阶段3.2 conversion
# layer (core/notation/jianpu/render_json.py: jianpu_section_to_render_json()).
from core.notation.jianpu.parser import parse_jianpu_ly_text
from core.notation.jianpu.render_json import (
    DEFAULT_PLAYBACK_TEMPO,
    jianpu_section_to_render_json,
)


def _ref(measure, index):
    return {'measure': measure, 'index': index}


def _written(digit, dots=0, accidental=0):
    """The written-form keys every drawn note carries (stage V0)."""
    return {'jianpuNumber': digit, 'octaveDot': dots, 'accidental': accidental}


def test_simple_measure_produces_one_note_per_token():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 3 4 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert render['notes'] == [
        {'start': 0.0, 'length': 1.0, 'pitch': 60, 'intensity': 80, 'ref': _ref(0, 0), **_written(1)},
        {'start': 1.0, 'length': 1.0, 'pitch': 62, 'intensity': 80, 'ref': _ref(0, 1), **_written(2)},
        {'start': 2.0, 'length': 1.0, 'pitch': 64, 'intensity': 80, 'ref': _ref(0, 2), **_written(3)},
        {'start': 3.0, 'length': 1.0, 'pitch': 65, 'intensity': 80, 'ref': _ref(0, 3), **_written(4)},
    ]


def test_rests_become_gaps_not_note_entries():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 0 2 0 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    # Two real notes only; the second one's start (2.0) skips over the rest
    # at [1.0, 2.0) — JianpuRender infers that gap as a rest on its own.
    # Note the refs: the second drawn note is model index 2, not 1. That gap
    # between drawn position and model position is exactly why the ref exists.
    assert render['notes'] == [
        {'start': 0.0, 'length': 1.0, 'pitch': 60, 'intensity': 80, 'ref': _ref(0, 0), **_written(1)},
        {'start': 2.0, 'length': 1.0, 'pitch': 62, 'intensity': 80, 'ref': _ref(0, 2), **_written(2)},
    ]


def test_dash_continuations_extend_the_previous_note_length():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\nq1 q- q- q- |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    # Four q (eighth-note, 0.5ql) tokens tied together -> one note, length 2.0.
    # The ref points at the note that was struck, not at any of the dashes.
    assert render['notes'] == [
        {'start': 0.0, 'length': 2.0, 'pitch': 60, 'intensity': 80, 'ref': _ref(0, 0), **_written(1)},
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
    assert render['keySignatures'] == [{'start': 0, 'key': 0, 'label': '6=A'}]


def test_key_signature_label_keeps_what_the_pitch_class_cannot_say():
    # The number is lossy in two independent ways, and the caption is drawn
    # from it, so both used to reach the screen as the wrong key:
    #   * mode      — A minor and C major are both pitch class 0
    #   * spelling  — pitch class 10 reads back as A#, never Bb
    # 23 of the 74 scores in editor-workspace/ were captioned wrongly by that
    # reconstruction. The label is the header verbatim, so it cannot drift.
    for header, semitone in [('6=A', 0), ('1=C', 0), ('6=E', 7), ('1=G', 7),
                             ('1=Bb', 10), ('6=G', 10), ('6=C', 3)]:
        doc = parse_jianpu_ly_text(f'title=T\n{header}\n4/4\n\n1 2 3 4 |\n')
        render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
        assert render['keySignatures'] == [
            {'start': 0, 'key': semitone, 'label': header}
        ], header


def test_key_label_does_not_disturb_the_digits():
    # Only the caption changes: the digit mapping runs off `key`, which is
    # untouched, so a minor-key score must render exactly the notes it did.
    doc = parse_jianpu_ly_text('title=T\n6=A\n4/4\n\n6 7 1 2 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert [n['pitch'] for n in render['notes']] == [69, 71, 60, 62]


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


# ── dynamics reaching the renderer (stage 6.1d) ──────────────────────────────

def test_a_dynamic_rides_along_on_the_note_it_belongs_to():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 \\mf 3 4 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert [n.get('dynamic') for n in render['notes']] == [None, 'mf', None, None]


def test_notes_without_a_dynamic_carry_no_such_key():
    # Absent rather than empty: the payload of a score with no dynamics --
    # nearly all of them -- must stay exactly as it was before 6.1d.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 3 4 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert all('dynamic' not in note for note in render['notes'])


def test_a_dynamic_waiting_at_the_start_of_a_measure_lands_on_the_next_note():
    # The parser moves it onto the note it attaches to, so by the time the
    # renderer sees it there is nothing positional left to decide.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 3 4 | \\p 5 6 7 1 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    marked = [(n['start'], n['dynamic']) for n in render['notes'] if 'dynamic' in n]
    assert marked == [(4.0, 'p')]


def test_a_dynamic_on_a_rest_is_not_drawn_but_does_not_shift_anything():
    # A rest never becomes a drawn note, so its mark has nowhere to go. What
    # matters is that the remaining notes keep their positions and refs.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 0 \\f 3 4 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert [(n['start'], n['ref']['index']) for n in render['notes']] == [
        (0.0, 0), (2.0, 2), (3.0, 3),
    ]


# ── hairpins reaching the renderer (stage 6.1d-2) ────────────────────────────

def test_the_two_halves_of_a_hairpin_ride_on_their_own_notes():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 \\< 2 3 4 \\! |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert [(n['start'], n.get('hairpinStart'), n.get('hairpinEnd'))
            for n in render['notes']] == [
        (0.0, '<', None), (1.0, None, None), (2.0, None, None), (3.0, None, True),
    ]


def test_a_decrescendo_keeps_its_direction():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 \\> 2 3 4 \\! |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert render['notes'][0]['hairpinStart'] == '>'


def test_notes_without_hairpins_carry_no_such_keys():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 3 4 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert all('hairpinStart' not in n and 'hairpinEnd' not in n for n in render['notes'])


def test_an_unfinished_hairpin_is_still_sent():
    # Normal while typing, and the renderer is the one that decides what an
    # unpaired half looks like (nothing, as in the PDF). Dropping it here
    # would make the editor unable to show the state the file is really in.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 \\< 2 3 4 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert render['notes'][0]['hairpinStart'] == '<'


def test_a_note_can_end_one_hairpin_and_start_the_next():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 \\< 2 \\! \\> 3 4 \\! |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    middle = render['notes'][1]
    assert (middle['hairpinEnd'], middle['hairpinStart']) == (True, '>')


def test_a_hairpin_and_a_dynamic_can_share_one_note():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 \\p \\< 2 3 4 \\f |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    first, last = render['notes'][0], render['notes'][3]
    assert (first['dynamic'], first['hairpinStart']) == ('p', '<')
    assert last['dynamic'] == 'f'


# ── the written form reaching the renderer (stage V0) ────────────────────────
# The renderer used to re-derive digit, octave dots and accidental from
# `pitch` with its own conventions, and disagreed with the text on 5887 of the
# 6980 notes in editor-workspace/. These pin that every drawn note now says
# exactly how it is written.

def _written_of(render):
    return [(n['jianpuNumber'], n['octaveDot'], n['accidental']) for n in render['notes']]


def test_written_form_follows_the_text_in_a_key_other_than_c():
    # The renderer's own convention put an extra dot on every one of these.
    doc = parse_jianpu_ly_text("title=T\n1=D\n4/4\n\n2 2' 5, b7 |\n")
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert _written_of(render) == [(2, 0, 0), (2, 1, 0), (5, -1, 0), (7, 0, 2)]


def test_written_form_in_a_minor_key_header_keeps_the_true_pitch_alongside():
    # Scarborough Fair (6=B): `2 - 2` is printed without dots, and must be
    # drawn without dots; `pitch` stays the real sounding pitch for playback.
    doc = parse_jianpu_ly_text('title=T\n6=B\n3/4\n\n2 - 2 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert _written_of(render) == [(2, 0, 0), (2, 0, 0)]
    assert [n['pitch'] for n in render['notes']] == [64, 64]


def test_written_form_keeps_the_spelling_of_enharmonic_notes():
    # #5 and b6 sound the same; the renderer alone would draw both as b6.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n#5 b6 #4 b5 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert _written_of(render) == [(5, 0, 1), (6, 0, 2), (4, 0, 1), (5, 0, 2)]
    assert render['notes'][0]['pitch'] == render['notes'][1]['pitch']


def test_written_form_counts_every_octave_dot():
    doc = parse_jianpu_ly_text("title=T\n1=C\n4/4\n\n1'' 1,, 1''' 1 |\n")
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert [n['octaveDot'] for n in render['notes']] == [2, -2, 3, 0]


def test_written_form_of_a_sustained_note_is_that_of_the_struck_note():
    doc = parse_jianpu_ly_text("title=T\n1=G\n4/4\n\nq#4' q- q- q- 0 0 |\n")
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert len(render['notes']) == 1
    assert _written_of(render) == [(4, 1, 1)]


# ── a `-` after a rest lengthens the rest (stage V1a) ────────────────────────
# It used to lengthen notes[-1] -- the last note *before* the rest -- so the
# note sounded right through the rest in playback and swallowed it on screen.

def _spans(render):
    return [(n['start'], n['length']) for n in render['notes']]


def test_a_dash_after_a_rest_does_not_stretch_the_note_before_it():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n3 1 - - | - 0 - - |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    # 1 starts on beat 2 and is held to the end of beat 1 of bar 2: 4 beats,
    # not 6 -- the two dashes after the rest belong to the rest.
    assert _spans(render) == [(0.0, 1.0), (1.0, 4.0)]


def test_the_note_after_a_lengthened_rest_starts_where_the_rest_ends():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 0 - 2 |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert _spans(render) == [(0.0, 1.0), (3.0, 1.0)]


def test_a_rest_lengthened_across_the_bar_line_stays_a_rest():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 - - - | 0 - 2 - |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert _spans(render) == [(0.0, 4.0), (6.0, 2.0)]


def test_dashes_in_a_rest_intro_before_any_note_are_still_harmless():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n0 - - - | 1 - - - |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert _spans(render) == [(4.0, 4.0)]


def test_a_note_held_across_the_bar_line_is_still_one_note():
    # The other half of the rule must not move: a dash after a note, even in
    # the next bar, keeps lengthening that note.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 - - - | - 2 - - |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert _spans(render) == [(0.0, 5.0), (5.0, 3.0)]


def test_drawn_notes_and_rests_add_up_to_the_score_length():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n3 1 - - | - 0 - - | 0 - 5 - |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    sounding = sum(length for _, length in _spans(render))
    silent = 1 + 1 + 1 + 1 + 1        # 0 - - in bar 2, then 0 - in bar 3
    assert sounding + silent == render['totalLength'] == 12.0


# ── how each slot is written (stage V1c) ─────────────────────────────────────

def _shapes(render):
    return [(s['lines'], s['dots'], s['dashes']) for s in render['slots']]


def test_slots_say_how_each_note_is_written():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 q1 s1 d1 1. q1. s1. d1. |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert _shapes(render) == [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0),
                               (0, 1, 0), (1, 1, 0), (2, 1, 0), (3, 1, 0)]


def test_rests_and_dashes_carry_their_own_underlines_and_dots():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n0 q0 s0. 1 - q- -. |\n')
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert _shapes(render) == [(0, 0, 0), (1, 0, 0), (2, 1, 0),
                               (0, 0, 0), (0, 0, 0), (1, 0, 0), (0, 1, 0)]


def test_a_flat_is_not_mistaken_for_a_duration_prefix():
    doc = parse_jianpu_ly_text("title=T\n1=C\n4/4\n\nb3, #5' qb3, sb7'. |\n")
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert _shapes(render) == [(0, 0, 0), (0, 0, 0), (1, 0, 0), (2, 1, 0)]


def test_a_note_lengthened_by_an_edit_is_written_with_dashes():
    # The parser gives every `-` a note of its own, but a graphical edit can
    # set one note to two, three or four beats, which the serializer writes as
    # `1 -`, `1 - -`, `1 - - -`. The slot has to say so, or nothing would know
    # to draw those dashes.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 3 4 |\n')
    notes = doc.sections[0].measures[0]
    for note, length in zip(notes, (2.0, 3.0, 4.0, 1.5), strict=True):
        note.duration = length
    notes[3].duration_dots = 1
    render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
    assert _shapes(render) == [(0, 0, 1), (0, 0, 2), (0, 0, 3), (0, 1, 0)]


# ── a declared pickup reaches the renderer (stage V1g) ───────────────────────

def test_a_declared_pickup_is_sent_in_quarters():
    # jianpu-ly counts the pickup measure's beats back from where a full bar
    # would end; the renderer needs the length to group beams the same way.
    for time_sig, quarters in (('2/4,8', 0.5), ('4/4,4', 1.0), ('3/4,4.', 1.5), ('6/8,8.', 0.75)):
        doc = parse_jianpu_ly_text(f'title=T\n1=C\n{time_sig}\n\n1 |\n')
        render = jianpu_section_to_render_json(doc.sections[0], doc.key_header)
        assert render['anacrusis'] == quarters, time_sig


def test_no_pickup_means_no_anacrusis_key():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 3 4 |\n')
    assert 'anacrusis' not in jianpu_section_to_render_json(doc.sections[0], doc.key_header)


# ── the title block above the first staff (stage V2a) ────────────────────────

def test_score_header_carries_what_the_file_gives():
    from core.notation.jianpu.render_json import score_header
    doc = parse_jianpu_ly_text('title=Song\ncomposer=Someone\n1=C\n4/4\n4=88\n\n1 2 3 4 |\n')
    assert score_header(doc) == {'title': 'Song', 'composer': 'Someone', 'tempo': 88}


def test_score_header_leaves_out_what_the_file_does_not_say():
    # No `4=N` line: the playback default must not be printed as if written.
    from core.notation.jianpu.render_json import score_header
    doc = parse_jianpu_ly_text('title=Song\n1=C\n4/4\n\n1 2 3 4 |\n')
    assert score_header(doc) == {'title': 'Song'}
