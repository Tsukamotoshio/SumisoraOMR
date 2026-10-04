# tests/test_jianpu_repeats.py — the body repeat syntax `R{ } A{ }`,
# stage 6.2b in the fix-plan doc.
#
# Until this existed the parser refused the syntax outright, so a file using
# it could not be opened in the graphical editor at all — and the whole point
# of 6.2 is that this is how a repeat with first and second endings is really
# written. jianpu-ly turns it into `\repeat volta` + `\alternative`, which is
# a real repeat (it repeats in the MIDI and prints 1./2. brackets), unlike the
# OMR bypass that injects a barline glyph into the .ly afterwards.
#
# The one rule that is easy to get wrong: inside `A{ }` a `|` separates two
# alternatives rather than being a barline check. It still ends a measure --
# first and second endings are drawn as two consecutive measures, and the
# editor's "go to measure N" counts the model's measures -- but it has to be
# written back as the separator, not as an extra barline.
import pytest

from core.notation.jianpu import build_jianpu_ly_text_from_doc
from core.notation.jianpu.doc_json import jianpu_doc_from_dict, jianpu_doc_to_dict
from core.notation.jianpu.parser import JianpuParseError, parse_jianpu_ly_text

HEAD = '% jianpu-ly.py\ntitle=T\n1=C\n4/4\n\n'


def _doc(body: str):
    return parse_jianpu_ly_text(HEAD + body + '\n')


def _repeats(body: str) -> list[dict]:
    return _doc(body).sections[0].repeats


def _measures(body: str) -> list[list[str]]:
    return [[n.symbol for n in m] for m in _doc(body).sections[0].measures]


# ── reading it ───────────────────────────────────────────────────────────────

class TestParsing:
    def test_a_repeat_with_two_endings(self):
        assert _repeats('R{ 1 1 1 1 } A{ 2 2 2 2 | 3 3 3 3 }') == [
            {'at': 0, 'token': 'R{'},
            {'at': 1, 'token': '}'},
            {'at': 1, 'token': 'A{'},
            {'at': 2, 'token': '|'},
            {'at': 3, 'token': '}'},
        ]

    def test_the_endings_are_measures_like_any_other(self):
        # What "go to measure N" counts, and what the renderer draws: three
        # measures, the second and third being the first and second endings.
        assert _measures('R{ 1 1 1 1 } A{ 2 2 2 2 | 3 3 3 3 }') == [
            ['1', '1', '1', '1'], ['2', '2', '2', '2'], ['3', '3', '3', '3'],
        ]

    def test_the_closing_brace_ends_the_measure_inside_the_block(self):
        # jianpu-ly writes no `|` before the `}`; the brace is the barline.
        assert _measures('R{ 1 1 1 1 } 2 2 2 2 |') == [
            ['1', '1', '1', '1'], ['2', '2', '2', '2'],
        ]

    def test_a_barline_outside_an_alternative_block_is_just_a_barline(self):
        assert _repeats('1 1 1 1 | R{ 2 2 2 2 | 3 3 3 3 } 4 4 4 4 |') == [
            {'at': 1, 'token': 'R{'}, {'at': 3, 'token': '}'},
        ]

    def test_a_repeat_count_is_kept_verbatim(self):
        assert _repeats('R3{ 1 1 1 1 } 2 2 2 2 |')[0]['token'] == 'R3{'

    def test_three_endings(self):
        separators = [r for r in _repeats(
            'R{ 1 1 1 1 } A{ 2 2 2 2 | 3 3 3 3 | 4 4 4 4 }') if r['token'] == '|']
        assert [s['at'] for s in separators] == [2, 3]

    def test_a_file_without_repeats_records_none(self):
        assert _repeats('1 1 1 1 | 2 2 2 2 |') == []


# ── refusing what cannot be written back ─────────────────────────────────────

class TestRejected:
    def test_a_closing_brace_with_nothing_open(self):
        with pytest.raises(JianpuParseError, match='多出来的'):
            _doc('1 1 1 1 } 2 2 2 2 |')

    def test_a_block_left_open_at_the_end_of_the_file(self):
        with pytest.raises(JianpuParseError, match='没有闭合'):
            _doc('R{ 1 1 1 1 | 2 2 2 2 |')

    def test_a_block_left_open_at_a_section_boundary(self):
        # jianpu-ly would carry it into the next voice, which is never meant.
        with pytest.raises(JianpuParseError, match='没有闭合'):
            _doc('R{ 1 1 1 1 |\nNextPart\n4/4\n2 2 2 2 |')

    def test_alternatives_that_do_not_follow_their_repeat(self):
        with pytest.raises(JianpuParseError, match='A\\{ 必须紧跟'):
            _doc('R{ 1 1 1 1 } 2 2 2 2 | A{ 3 3 3 3 | 4 4 4 4 }')

    def test_a_block_opening_in_the_middle_of_a_measure(self):
        # The model pins these words to measure boundaries; there is no
        # boundary here, so it could not be written back.
        with pytest.raises(JianpuParseError, match='小节边界'):
            _doc('1 1 R{ 1 1 | 2 2 2 2 }')


# ── writing it back ──────────────────────────────────────────────────────────

ROUND_TRIP = [
    'R{ 1 1 1 1 } A{ 2 2 2 2 | 3 3 3 3 }',
    '1 1 1 1 | R{ 2 2 2 2 | 3 3 3 3 } 4 4 4 4 |',
    'R3{ 1 1 1 1 } A{ 2 2 2 2 | 3 3 3 3 }',
    'R{ 1 1 1 1 } 2 2 2 2 |',
    '1 1 1 1 | 2 2 2 2 | 3 3 3 3 | 4 4 4 4 |',
]


@pytest.mark.parametrize('body', ROUND_TRIP)
def test_serialize_parse_round_trip(body):
    text = HEAD + body + '\n'
    assert build_jianpu_ly_text_from_doc(parse_jianpu_ly_text(text)) == text


def test_a_long_section_wraps_and_stays_stable():
    # The serializer puts four measures on a line, so a hand-written long line
    # comes back wrapped -- including, here, a line that starts with the
    # alternative separator. What matters is that re-reading it changes
    # nothing further, and that jianpu-ly reads it the same way (checked by
    # rendering in the stage's own verification run).
    text = HEAD + 'R{ 1 1 1 1 | 2 2 2 2 } A{ 3 3 3 3 | 4 4 4 4 | 5 5 5 5 }\n'
    once = build_jianpu_ly_text_from_doc(parse_jianpu_ly_text(text))
    twice = build_jianpu_ly_text_from_doc(parse_jianpu_ly_text(once))
    assert once.splitlines()[-2:] == ['R{ 1 1 1 1 | 2 2 2 2 } A{ 3 3 3 3 | 4 4 4 4', '| 5 5 5 5 }']
    assert twice == once


def test_lyrics_still_follow_the_measures_of_a_repeated_section():
    text = HEAD + 'R{ 1 1 1 1 } A{ 2 2 2 2 | 3 3 3 3 }\nL: a b c d e f g h i j k l\n'
    assert build_jianpu_ly_text_from_doc(parse_jianpu_ly_text(text)) == text


# ── across the bridge ────────────────────────────────────────────────────────

class TestBridge:
    def test_the_words_survive_a_trip_to_the_front_end_and_back(self):
        doc = _doc('R{ 1 1 1 1 } A{ 2 2 2 2 | 3 3 3 3 }')
        back = jianpu_doc_from_dict(jianpu_doc_to_dict(doc))
        assert back.sections[0].repeats == doc.sections[0].repeats

    def test_a_token_the_parser_would_not_accept_is_dropped(self):
        # The serializer writes these words straight into the file, so a bad
        # one would produce a document that cannot be opened again.
        doc = _doc('1 1 1 1 |')
        raw = jianpu_doc_to_dict(doc)
        raw['sections'][0]['repeats'] = [
            {'at': 0, 'token': 'R{'},        # fine
            {'at': 0, 'token': 'drop table'},
            {'at': 7, 'token': '}'},          # past the last boundary
            'not even a dict',
            {'at': 'x', 'token': '}'},
        ]
        assert jianpu_doc_from_dict(raw).sections[0].repeats == [{'at': 0, 'token': 'R{'}]
