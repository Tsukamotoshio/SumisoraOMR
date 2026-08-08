# tests/test_jianpu_parser.py — unit tests for the 阶段2 jianpu-ly parser
# (core/notation/jianpu/parser.py). See 修复计划2与简谱编辑器规划.md B3/B6.
import glob
import os

import pytest

from core.notation.jianpu.parser import JianpuParseError, parse_jianpu_ly_text

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), 'fixtures', 'golden')


def test_parses_title_composer_key_tempo():
    body = '% jianpu-ly.py\ntitle=My Title\ncomposer=Someone\n1=C\n4/4\n4=120\n\n1 2 3 4 |\n'
    doc = parse_jianpu_ly_text(body)
    assert doc.title == 'My Title'
    assert doc.composer == 'Someone'
    assert doc.key_header == '1=C'
    assert doc.tempo == 120


def test_title_and_composer_values_may_contain_spaces():
    body = 'title=Scarborough Fair-Flauta\n1=C\n4/4\n\n1 2 3 4 |\n'
    doc = parse_jianpu_ly_text(body)
    assert doc.title == 'Scarborough Fair-Flauta'


def test_minor_key_header_preserved_verbatim():
    doc = parse_jianpu_ly_text('title=T\n6=G\n4/4\n\n6 7 1 2 |\n')
    assert doc.key_header == '6=G'


def test_single_section_measure_grouping():
    doc = parse_jianpu_ly_text('4/4\n\n1 2 3 4 | 5 6 7 1 |\n')
    assert len(doc.sections) == 1
    assert doc.sections[0].time_sig == '4/4'
    assert len(doc.sections[0].measures) == 2
    assert [n.symbol for n in doc.sections[0].measures[0]] == ['1', '2', '3', '4']
    assert [n.symbol for n in doc.sections[0].measures[1]] == ['5', '6', '7', '1']


@pytest.mark.parametrize(('token', 'expect'), [
    ('1', {'symbol': '1', 'accidental': '', 'upper_dots': 0, 'lower_dots': 0, 'duration': 1.0, 'is_rest': False}),
    ('0', {'symbol': '0', 'accidental': '', 'upper_dots': 0, 'lower_dots': 0, 'duration': 1.0, 'is_rest': True}),
    ('b2', {'symbol': '2', 'accidental': 'b', 'upper_dots': 0, 'lower_dots': 0, 'duration': 1.0, 'is_rest': False}),
    ("#4", {'symbol': '4', 'accidental': '#', 'upper_dots': 0, 'lower_dots': 0, 'duration': 1.0, 'is_rest': False}),
    ("1'", {'symbol': '1', 'accidental': '', 'upper_dots': 1, 'lower_dots': 0, 'duration': 1.0, 'is_rest': False}),
    ("4''", {'symbol': '4', 'accidental': '', 'upper_dots': 2, 'lower_dots': 0, 'duration': 1.0, 'is_rest': False}),
    ('1,', {'symbol': '1', 'accidental': '', 'upper_dots': 0, 'lower_dots': 1, 'duration': 1.0, 'is_rest': False}),
    ("q2'.", {'symbol': '2', 'accidental': '', 'upper_dots': 1, 'lower_dots': 0, 'duration': 0.75, 'is_rest': False}),
    ("s3'", {'symbol': '3', 'accidental': '', 'upper_dots': 1, 'lower_dots': 0, 'duration': 0.25, 'is_rest': False}),
    ("d1'", {'symbol': '1', 'accidental': '', 'upper_dots': 1, 'lower_dots': 0, 'duration': 0.125, 'is_rest': False}),
    ('6,.', {'symbol': '6', 'accidental': '', 'upper_dots': 0, 'lower_dots': 1, 'duration': 1.5, 'is_rest': False}),
])
def test_note_token_shapes_decode_correctly(token, expect):
    doc = parse_jianpu_ly_text(f'4/4\n\n{token} |\n')
    note = doc.sections[0].measures[0][0]
    for field, value in expect.items():
        assert getattr(note, field) == value, f'{token}: {field}'
    # 阶段3.1: 休止符仍不计算 midi，真实音符现在都会算出 midi（见 parser.py 模块文档）
    if expect['is_rest']:
        assert note.midi is None
    else:
        assert note.midi is not None


@pytest.mark.parametrize(('token', 'prefix_ql'), [('-', 1.0), ('q-', 0.5), ('s-', 0.25), ('d-', 0.125)])
def test_dash_continuation_tokens_decode_as_their_own_note(token, prefix_ql):
    doc = parse_jianpu_ly_text(f'4/4\n\n1 {token} |\n')
    dash_note = doc.sections[0].measures[0][1]
    assert dash_note.symbol == '-'
    assert dash_note.is_rest is False
    assert dash_note.accidental == ''
    assert dash_note.upper_dots == 0 and dash_note.lower_dots == 0
    assert dash_note.duration == prefix_ql


def test_multi_dash_note_is_one_token_group_not_merged():
    # "1 - -" is jianpu-ly's own encoding for a single 3.0-QL note (docs §1.5) —
    # the parser must NOT try to collapse it into one JianpuNote; each word
    # decodes independently (1.0 + 1.0 + 1.0), and it's the *serializer's* job
    # (not the parser's) to reconstitute the compact multi-dash form on output.
    doc = parse_jianpu_ly_text('4/4\n\n1 - - |\n')
    measure = doc.sections[0].measures[0]
    assert [n.symbol for n in measure] == ['1', '-', '-']
    assert [n.duration for n in measure] == [1.0, 1.0, 1.0]


def test_anacrusis_preserved_verbatim_in_time_sig():
    doc = parse_jianpu_ly_text('3/4,8\n\nq1 | 1 2 3 |\n')
    assert doc.sections[0].time_sig == '3/4,8'


def test_nextpart_starts_a_new_section_and_resets_time_sig_tracking():
    body = '4/4\n\n1 2 3 4 |\nNextPart\n2/4\n\n5 6 |\n'
    doc = parse_jianpu_ly_text(body)
    assert len(doc.sections) == 2
    assert doc.sections[0].time_sig == '4/4'
    assert doc.sections[1].time_sig == '2/4'
    assert len(doc.sections[0].measures) == 1
    assert len(doc.sections[1].measures) == 1


def test_key_title_composer_tempo_are_document_level_not_per_section():
    # 2026-08-05 correction (docs/jianpu-ly-syntax.md §1.6): only time_sig
    # repeats after NextPart in real files; key/title/composer/tempo don't.
    body = 'title=T\ncomposer=C\n1=D\n4/4\n4=100\n\n1 |\nNextPart\n3/4\n\n2 3 4 |\n'
    doc = parse_jianpu_ly_text(body)
    assert doc.title == 'T' and doc.composer == 'C' and doc.key_header == '1=D' and doc.tempo == 100
    assert len(doc.sections) == 2


def test_comment_lines_are_ignored():
    body = '% jianpu-ly.py\n% a full-line comment\ntitle=T\n1=C\n4/4\n\n1 2 3 4 |\n% trailing comment\n'
    doc = parse_jianpu_ly_text(body)
    assert doc.title == 'T'
    assert len(doc.sections[0].measures) == 1


def test_crlf_line_endings_do_not_corrupt_header_lines():
    body = 'title=T\r\n1=C\r\n4/4\r\n\r\n1 2 3 4 |\r\n'
    doc = parse_jianpu_ly_text(body)
    assert doc.title == 'T'
    assert doc.key_header == '1=C'
    assert len(doc.sections[0].measures) == 1


def test_unrecognized_token_raises_with_position():
    with pytest.raises(JianpuParseError) as exc_info:
        parse_jianpu_ly_text("4/4\n\n1 2 qi' 4 |\n")
    err = exc_info.value
    assert err.token == "qi'"
    assert err.line == 3


def test_historical_ocr_garbage_tokens_raise():
    for bad in ('qo', '?,'):
        with pytest.raises(JianpuParseError):
            parse_jianpu_ly_text(f'4/4\n\n{bad} 1 |\n')


def test_lyric_lines_are_out_of_scope_and_raise():
    with pytest.raises(JianpuParseError):
        parse_jianpu_ly_text('4/4\n\n1 2 3 4 |\nL: syl- la- ble\n')


def test_excluded_lilypond_block_raises():
    with pytest.raises(JianpuParseError):
        parse_jianpu_ly_text('4/4\n\nLP:\nsome code\n:LP\n1 2 3 4 |\n')


# "Scarborough Fair-Flauta" is the one golden fixture with real lyric lines
# (L:/H:) — 🟡-tier syntax this stage's parser deliberately doesn't cover
# (see parser.py's module docstring). Expected to raise; everything else
# in the golden set is pure 🟢 grammar and must parse cleanly.
_KNOWN_OUT_OF_SCOPE = {'Scarborough Fair-Flauta.jly.txt'}


def test_parses_all_golden_fixtures_without_raising():
    files = glob.glob(os.path.join(GOLDEN_DIR, '*.jly.txt'))
    assert len(files) >= 10, 'expected the golden fixture set to be present'
    for path in files:
        with open(path, encoding='utf-8') as f:
            body = f.read()
        if os.path.basename(path) in _KNOWN_OUT_OF_SCOPE:
            with pytest.raises(JianpuParseError):
                parse_jianpu_ly_text(body)
            continue
        doc = parse_jianpu_ly_text(body)
        assert doc.sections, f'{path}: expected at least one section'
        assert any(m for section in doc.sections for m in section.measures), \
            f'{path}: expected at least one non-empty measure'


def test_golden_fixtures_get_non_none_midi_for_real_notes():
    # 阶段3.1: every real (non-rest, non-dash) note parsed from a golden
    # fixture must come out with a computed .midi — only rests and dash
    # continuations are allowed to keep None (see parser.py module docstring).
    files = glob.glob(os.path.join(GOLDEN_DIR, '*.jly.txt'))
    checked_any_note = False
    for path in files:
        if os.path.basename(path) in _KNOWN_OUT_OF_SCOPE:
            continue
        with open(path, encoding='utf-8') as f:
            doc = parse_jianpu_ly_text(f.read())
        for section in doc.sections:
            for measure in section.measures:
                for note in measure:
                    if note.is_rest or note.symbol in ('-', '?'):
                        assert note.midi is None, f'{path}: expected None midi for {note.symbol!r}'
                        continue
                    checked_any_note = True
                    assert note.midi is not None, f'{path}: expected computed midi for {note.symbol!r}'
    assert checked_any_note, 'expected at least one real note across the golden fixture set'
