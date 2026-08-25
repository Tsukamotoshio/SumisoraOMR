# tests/test_jianpu_doc_json.py — the 阶段5.3 transport boundary.
#
# The property that matters: sending a document to the front-end and getting it
# back must not change what it serializes to. Everything else here exists
# because JSON quietly rewrites dict keys and tuples on the way through, and
# three of those rewrites reach the emitted text.
import json

import pytest

from core.notation.jianpu import build_jianpu_ly_text_from_doc
from core.notation.jianpu.doc_json import jianpu_doc_from_dict, jianpu_doc_to_dict
from core.notation.jianpu.parser import parse_jianpu_ly_text

SOURCES = [
    'title=T\n1=C\n4/4\n4=120\n\n1 2 3 4 | 5 6 7 1 |\n',
    "title=T\n1=C\n4/4\n\nb2' - - | #4 q3, s5'' d1 6,. |\n",
    'title=T\n1=C\n3/4,8\n\nq1 | 1 2 3 | 4 5 6 |\n',
    'title=T\ncomposer=Someone\n6=D\n4/4\n4=90\n\n1 0 2 |\nNextPart\n4/4\n\n5 6 |\n',
    'title=T\n1=C\n4/4\n\n1 2 3 |\nL: one _ three\nL: 2. uno _ tres\n',
    'title=T\n1=C\n4/4\n\n1 2 |\nH: 一 二\n',
]


def _through_json(doc):
    """Exactly what the bridge does: encode, decode, rebuild."""
    return jianpu_doc_from_dict(json.loads(json.dumps(jianpu_doc_to_dict(doc))))


@pytest.mark.parametrize('source', SOURCES, ids=range(len(SOURCES)))
def test_json_round_trip_does_not_change_the_serialized_text(source):
    doc = parse_jianpu_ly_text(source)
    assert build_jianpu_ly_text_from_doc(_through_json(doc)) == build_jianpu_ly_text_from_doc(doc)


@pytest.mark.parametrize('source', SOURCES, ids=range(len(SOURCES)))
def test_json_round_trip_preserves_the_document_itself(source):
    doc = parse_jianpu_ly_text(source)
    assert _through_json(doc) == doc


def test_lyric_verse_keys_come_back_as_integers():
    # The silent one: build_lyric_lines writes a "N. " stanza prefix for any
    # verse != 1, so a verse that returns as the string '1' would add a marker
    # the file never had -- no exception, just wrong output.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 |\nL: aa bb\n')
    rebuilt = _through_json(doc)
    keys = [k for n in rebuilt.sections[0].measures[0] for k in n.lyrics]
    assert keys and all(isinstance(k, int) for k in keys)
    assert '\nL: aa bb' in build_jianpu_ly_text_from_doc(rebuilt)


def test_string_verse_key_is_repaired_rather_than_trusted():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 |\n')
    raw = jianpu_doc_to_dict(doc)
    raw['sections'][0]['measures'][0][0]['lyrics'] = {'1': ['la', True]}
    out = build_jianpu_ly_text_from_doc(jianpu_doc_from_dict(raw))
    assert out.endswith('L: la-'), out
    assert '1. la-' not in out, 'a string key must not introduce a stanza prefix'


def test_mixed_int_and_string_verse_keys_do_not_crash_serialization():
    # Raw, this raises TypeError inside sorted(); coercion collapses the two
    # spellings onto the same verse instead.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 |\n')
    raw = jianpu_doc_to_dict(doc)
    raw['sections'][0]['measures'][0][0]['lyrics'] = {1: ['a', False]}
    raw['sections'][0]['measures'][0][1]['lyrics'] = {'1': ['b', False]}
    assert build_jianpu_ly_text_from_doc(jianpu_doc_from_dict(raw)).endswith('L: a b')


def test_string_tempo_is_repaired_rather_than_trusted():
    # Raw, `if doc.tempo > 0` raises TypeError against a string.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 |\n')
    raw = jianpu_doc_to_dict(doc)
    raw['tempo'] = '132'
    assert '4=132' in build_jianpu_ly_text_from_doc(jianpu_doc_from_dict(raw))


def test_numeric_note_fields_survive_as_numbers():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\nq3,. |\n')
    note = _through_json(doc).sections[0].measures[0][0]
    assert isinstance(note.duration, float) and note.duration == 0.75
    assert isinstance(note.duration_dots, int) and note.duration_dots == 1
    assert isinstance(note.lower_dots, int) and note.lower_dots == 1
    assert isinstance(note.midi, int)
    assert isinstance(note.is_rest, bool) and note.is_rest is False


def test_malformed_payloads_degrade_instead_of_raising():
    # A failure here would reach the user as a lost edit, so the boundary
    # absorbs junk. Anything genuinely unrepresentable was rejected at parse
    # time and never became a document in the first place.
    for junk in [None, {}, [], 'nonsense', {'sections': 'not-a-list'},
                 {'sections': [{'measures': [['not-a-note']]}]},
                 {'sections': [None]}, {'tempo': None, 'title': None}]:
        doc = jianpu_doc_from_dict(junk)
        build_jianpu_ly_text_from_doc(doc)   # must not raise


def test_unparseable_verse_keys_are_dropped_not_propagated():
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 |\n')
    raw = jianpu_doc_to_dict(doc)
    raw['sections'][0]['measures'][0][0]['lyrics'] = {'x': ['la', False], '1': ['ok', False]}
    rebuilt = jianpu_doc_from_dict(raw)
    assert rebuilt.sections[0].measures[0][0].lyrics == {1: ('ok', False)}


def test_editing_a_symbol_re_derives_its_midi():
    # The front-end changes `symbol` when the user presses a number key; it has
    # no reason to also recompute `midi`. But render_json draws from `midi`,
    # not `symbol`, so a stale value would leave the edited note drawing and
    # sounding at its previous pitch while the text disagreed.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 |\n')
    raw = jianpu_doc_to_dict(doc)
    assert raw['sections'][0]['measures'][0][0]['midi'] == 60, 'C4 for symbol 1 in C'

    raw['sections'][0]['measures'][0][0]['symbol'] = '7'   # stale midi left at 60
    note = jianpu_doc_from_dict(raw).sections[0].measures[0][0]
    assert note.midi == 71, f'symbol 7 in C is B4, got {note.midi}'


def test_changing_the_key_header_re_derives_every_midi():
    # Same hazard one level up: transposing by editing the key header must move
    # every pitch, and nothing in the incoming payload would say so.
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 2 3 |\n')
    raw = jianpu_doc_to_dict(doc)
    assert [n['midi'] for n in raw['sections'][0]['measures'][0]] == [60, 62, 64]

    raw['key_header'] = '1=D'
    rebuilt = jianpu_doc_from_dict(raw)
    assert [n.midi for n in rebuilt.sections[0].measures[0]] == [62, 64, 66]


def test_a_note_added_by_the_front_end_serializes_correctly():
    # What a stage 5.3 edit actually looks like arriving back from JS: a plain
    # object with no midi (JS has no reason to compute one).
    doc = parse_jianpu_ly_text('title=T\n1=C\n4/4\n\n1 |\n')
    raw = jianpu_doc_to_dict(doc)
    raw['sections'][0]['measures'][0].append({
        'symbol': '5', 'accidental': '#', 'upper_dots': 1, 'lower_dots': 0,
        'duration': 0.5, 'duration_dots': 0, 'is_rest': False,
    })
    assert build_jianpu_ly_text_from_doc(jianpu_doc_from_dict(raw)).endswith("1 q#5' |")
