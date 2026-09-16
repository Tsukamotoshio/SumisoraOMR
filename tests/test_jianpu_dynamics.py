# tests/test_jianpu_dynamics.py — dynamics in the editor model, stage 6.1a.
#
# The accept/reject rules below were not reasoned out from either tool's
# source: each placement was run through the real jianpu-ly and the bundled
# LilyPond 2.24.4 first. That matters because LilyPond's failures here are
# quiet — a dropped or discarded dynamic still exits 0 and still produces a
# PDF, only a localized warning says the mark is gone. The rejected cases are
# exactly the ones where that happened.
import pytest

from core.config import JianpuNote
from core.notation.jianpu import build_jianpu_ly_text_from_doc
from core.notation.jianpu.doc_json import jianpu_doc_from_dict, jianpu_doc_to_dict
from core.notation.jianpu.parser import JianpuParseError, parse_jianpu_ly_text
from core.notation.jianpu.primitives import DYNAMIC_MARKS, clone_jianpu_note, jianpu_note_token

BS = '\\'
HEAD = '% jianpu-ly.py\ntitle=T\n1=C\n4/4\n4=120\n\n'


def _doc(body: str):
    return parse_jianpu_ly_text(HEAD + body + '\n')


def _notes(doc):
    return [n for m in doc.sections[0].measures for n in m]


def _note(symbol='1', duration=1.0, dynamic=''):
    return JianpuNote(symbol=symbol, accidental='', upper_dots=0, lower_dots=0,
                      duration=duration, duration_dots=0, midi=60, is_rest=symbol == '0',
                      dynamic=dynamic)


# ── the whitelist ────────────────────────────────────────────────────────────

def test_whitelist_is_exactly_lilypond_absolute_dynamics():
    # Copied from package-assets/lilypond-runtime/.../dynamic-scripts-init.ly
    # (LilyPond 2.24.4). If LilyPond is upgraded, re-read that file.
    assert DYNAMIC_MARKS == frozenset(
        'ppppp pppp ppp pp p mp mf f ff fff ffff fffff '
        'fp sf sfp sff sfz fz sp spp rfz n'.split())


def test_hairpins_are_not_dynamic_marks():
    # Spanner start/stop events, not a mark on one note: a different data shape.
    assert not ({'<', '>', '!'} & DYNAMIC_MARKS)


# ── parsing: accepted placements ─────────────────────────────────────────────

class TestAccepted:
    def test_attaches_to_the_note_written_just_before_it(self):
        notes = _notes(_doc(f'1 {BS}p 2 3 4 | 5 6 7 1 |'))
        assert [n.dynamic for n in notes] == ['p', '', '', '', '', '', '', '']

    def test_on_a_rest(self):
        notes = _notes(_doc(f'0 {BS}mf 2 3 4 | 5 6 7 1 |'))
        assert notes[0].is_rest and notes[0].dynamic == 'mf'

    def test_on_a_continuation_dash(self):
        # The dash is its own model object; the mark stays on it so the text
        # comes back exactly as written.
        notes = _notes(_doc(f'1 - {BS}f 4 | 5 6 7 1 |'))
        assert notes[1].symbol == '-' and notes[1].dynamic == 'f'
        assert notes[0].dynamic == ''

    def test_last_note_before_a_barline(self):
        notes = _notes(_doc(f'1 2 3 4 {BS}ff | 5 6 7 1 |'))
        assert notes[3].dynamic == 'ff'

    def test_on_prefixed_dotted_octave_and_accidental_notes(self):
        notes = _notes(_doc(f"q1 {BS}mp q2 3. q4 1' {BS}sfz | #4, {BS}pp 6 7 1 |"))
        assert (notes[0].dynamic, notes[4].dynamic, notes[5].dynamic) == ('mp', 'sfz', 'pp')

    def test_every_whitelisted_mark_parses(self):
        body = ' | '.join(f'1 {BS}{m} 1 1 1' for m in sorted(DYNAMIC_MARKS)) + ' |'
        doc = _doc(body)
        found = {m[0].dynamic for m in doc.sections[0].measures}
        assert found == DYNAMIC_MARKS

    def test_a_dynamic_is_not_a_lyric_slot(self):
        # Lyrics align positionally over notes; a dynamic between two notes must
        # not eat a syllable.
        doc = _doc(f'1 {BS}p 2 3 4 |\nL: a b c d')
        assert [n.lyrics[1][0] for n in _notes(doc)] == ['a', 'b', 'c', 'd']

    def test_a_dynamic_does_not_change_pitch(self):
        with_dyn = [n.midi for n in _notes(_doc(f'1 {BS}p 2 3 4 |'))]
        without = [n.midi for n in _notes(_doc('1 2 3 4 |'))]
        assert with_dyn == without


# ── parsing: rejected placements ─────────────────────────────────────────────

class TestRejected:
    def test_first_thing_in_a_measure(self):
        # LilyPond: "AbsoluteDynamicEvent 缺少附属对象", mark dropped, exit 0, PDF made.
        with pytest.raises(JianpuParseError, match='前面没有音符'):
            _doc(f'1 2 3 4 | {BS}p 5 6 7 1 |')

    def test_it_is_not_quietly_moved_onto_the_previous_measure(self):
        # Re-attaching would turn a mark that never rendered into one that does.
        with pytest.raises(JianpuParseError):
            _doc(f'1 2 3 4 | {BS}p 5 6 7 1 |')

    def test_first_thing_in_the_piece(self):
        with pytest.raises(JianpuParseError, match='前面没有音符'):
            _doc(f'{BS}p 1 2 3 4 | 5 6 7 1 |')

    def test_two_on_one_note(self):
        # LilyPond: events conflict, one discarded.
        with pytest.raises(JianpuParseError, match='已经有力度记号'):
            _doc(f'1 {BS}p {BS}f 2 3 4 |')

    def test_unknown_backslash_word(self):
        with pytest.raises(JianpuParseError, match='不认识的反斜杠记号'):
            _doc(f'1 {BS}foo 2 3 4 |')

    def test_wrong_case_gets_a_pointed_message(self):
        with pytest.raises(JianpuParseError, match='区分大小写') as exc:
            _doc(f'1 {BS}PP 2 3 4 |')
        assert f'{BS}pp' in str(exc.value)

    def test_hairpin_is_rejected(self):
        with pytest.raises(JianpuParseError):
            _doc(f'1 {BS}< 2 3 4 |')

    def test_error_points_at_the_offending_token(self):
        with pytest.raises(JianpuParseError) as exc:
            _doc(f'1 2 3 4 |\n5 {BS}foo 7 1 |')
        # HEAD is 6 lines; the body's second line is line 8, the token column 3.
        assert (exc.value.line, exc.value.col, exc.value.token) == (8, 3, f'{BS}foo')


# ── serialization ────────────────────────────────────────────────────────────

class TestSerialize:
    @pytest.mark.parametrize('body', [
        f'1 {BS}p 2 3 4 | 5 6 7 1 |',
        f'0 {BS}mf 2 3 4 | 5 6 7 1 {BS}ff |',
        f'1 - {BS}f 4 | 5 6 7 1 |',
        f"q1 {BS}mp q2 3. q4 1' {BS}sfz | #4, {BS}pp 6 7 1 |",
    ])
    def test_round_trip_is_byte_identical(self, body):
        text = HEAD + body + '\n'
        out = build_jianpu_ly_text_from_doc(parse_jianpu_ly_text(text))
        assert body in out

    def test_round_trip_is_stable(self):
        once = build_jianpu_ly_text_from_doc(_doc(f'1 {BS}p 2 3 4 | 5 6 7 {BS}f 1 |'))
        twice = build_jianpu_ly_text_from_doc(parse_jianpu_ly_text(once))
        assert once == twice

    def test_on_a_multi_beat_note_it_goes_on_the_attack(self):
        # A single 4-beat object (as extraction builds) expands to "1 - - -".
        # After the last dash the mark would land three beats late.
        assert jianpu_note_token(_note(duration=4.0, dynamic='f')) == f'1 {BS}f - - -'

    def test_on_a_short_note(self):
        assert jianpu_note_token(_note(duration=1.0, dynamic='p')) == f'1 {BS}p'

    def test_no_dynamic_leaves_the_token_unchanged(self):
        assert jianpu_note_token(_note(duration=2.0)) == '1 -'

    def test_a_value_outside_the_whitelist_is_not_written(self):
        # Writing it would make LilyPond reject the whole file.
        assert jianpu_note_token(_note(dynamic='foo')) == '1'


# ── splitting a note ─────────────────────────────────────────────────────────

def test_only_the_first_fragment_of_a_split_note_keeps_the_dynamic():
    src = _note(duration=4.0, dynamic='f')
    first = clone_jianpu_note(src, 2.0, is_first_fragment=True)
    rest = clone_jianpu_note(src, 2.0, is_first_fragment=False)
    assert (first.dynamic, rest.dynamic) == ('f', '')


# ── the bridge ───────────────────────────────────────────────────────────────

class TestBridge:
    def test_survives_the_round_trip_through_the_front_end(self):
        # jianpu_doc_to_dict (asdict) already carried it out; this pins the
        # inbound half, without which every dynamic would vanish on the first
        # graphical edit.
        doc = _doc(f'1 {BS}p 2 3 4 | 5 6 7 {BS}sfz 1 |')
        back = jianpu_doc_from_dict(jianpu_doc_to_dict(doc))
        assert [n.dynamic for n in _notes(back)] == [n.dynamic for n in _notes(doc)]

    def test_a_value_that_is_not_a_dynamic_is_dropped_at_the_boundary(self):
        raw = jianpu_doc_to_dict(_doc('1 2 3 4 |'))
        notes = raw['sections'][0]['measures'][0]
        notes[0]['dynamic'] = 'foo'
        notes[1]['dynamic'] = 42
        notes[2]['dynamic'] = 'PP'
        back = jianpu_doc_from_dict(raw)
        assert [n.dynamic for n in _notes(back)] == ['', '', '', '']

    def test_a_file_with_dynamics_is_not_reported_as_lossy(self):
        # Before 6.1a such a file did not parse at all. Now that it does, the
        # "a graphical edit would change this file" warning must stay silent
        # for it — otherwise every open would nag about a loss that is not
        # there. (That check compares comments and layout, not note content,
        # so dynamics being *kept* is pinned by the bridge test above.)
        from webui.editor import _writeback_losses
        body = build_jianpu_ly_text_from_doc(_doc(f'1 {BS}p 2 3 4 | 5 6 7 {BS}f 1 |'))
        assert _writeback_losses(body, parse_jianpu_ly_text(body)) == {}

    def test_a_note_without_the_key_reads_as_no_dynamic(self):
        # What the editor's own new-note constructors send.
        raw = jianpu_doc_to_dict(_doc('1 2 3 4 |'))
        for note in raw['sections'][0]['measures'][0]:
            note.pop('dynamic', None)
        assert all(n.dynamic == '' for n in _notes(jianpu_doc_from_dict(raw)))
