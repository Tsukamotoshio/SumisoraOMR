# tests/test_jianpu_roundtrip.py — 阶段2's actual acceptance bar:
# serialize(parse(x)) == x（规范化后）on the golden fixture set.
# See 修复计划2与简谱编辑器规划.md B6 阶段2 and core/notation/jianpu/parser.py.
import glob
import os
import re

import pytest

from core.notation.jianpu import build_jianpu_ly_text_from_doc
from core.notation.jianpu.parser import parse_jianpu_ly_text

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), 'fixtures', 'golden')

# Same exception as test_jianpu_parser.py: real lyric lines (🟡, out of scope).
_KNOWN_OUT_OF_SCOPE = {'Scarborough Fair-Flauta.jly.txt'}


def _normalized_words(text: str) -> list[str]:
    """"规范化后" for the round-trip comparison: drop `%` comment lines and
    blank lines (neither carries musical content, and the model doesn't
    retain them — see build_jianpu_ly_text_from_doc's docstring), then
    compare the remaining whitespace-separated word sequence. This is what
    "the same document" means at the model's level of fidelity, as opposed
    to byte-for-byte text identity."""
    words: list[str] = []
    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped or stripped.startswith('%'):
            continue
        words.extend(re.findall(r'\S+', line))
    return words


def _assert_round_trips(body: str) -> None:
    doc = parse_jianpu_ly_text(body)
    rebuilt = build_jianpu_ly_text_from_doc(doc)
    assert _normalized_words(rebuilt) == _normalized_words(body)


def test_round_trips_a_simple_single_section_score():
    _assert_round_trips('title=T\n1=C\n4/4\n4=120\n\n1 2 3 4 | 5 6 7 1 |\n')


def test_round_trips_dashes_dots_accidentals_and_octaves():
    # title=/key are always present — build_jianpu_ly_text_from_doc emits
    # them unconditionally, matching the real pipeline's own serializers,
    # which never omit either (see build_jianpu_ly_text_from_measures).
    _assert_round_trips("title=T\n1=C\n4/4\n\nb2' - - | #4 q3, s5'' d1 6,. |\n")


def test_round_trips_an_anacrusis_score():
    _assert_round_trips('title=T\n1=C\n3/4,8\n\nq1 | 1 2 3 | 4 5 6 |\n')


def test_round_trips_multi_voice_nextpart():
    body = 'title=T\n1=C\n4/4\n\n1 2 3 4 |\nNextPart\n4/4\n\n5 6 7 1 |\n'
    _assert_round_trips(body)


def test_round_trips_a_malformed_measure_without_silently_padding_it():
    # A measure with only 3 beats in a 4/4 bar must come back exactly as 3
    # beats, not get padded up to 4 — padding here would mean every re-save
    # quietly "fixes" data the linter is supposed to flag instead (see
    # build_jianpu_ly_text_from_doc's docstring).
    _assert_round_trips('title=T\n1=C\n4/4\n\n1 2 3 |\n')


def test_round_trips_composer_and_tempo():
    _assert_round_trips('title=T\ncomposer=Someone\n1=C\n4/4\n4=90\n\n1 |\n')


@pytest.mark.parametrize('path', [
    p for p in glob.glob(os.path.join(GOLDEN_DIR, '*.jly.txt'))
    if os.path.basename(p) not in _KNOWN_OUT_OF_SCOPE
], ids=os.path.basename)
def test_round_trips_every_golden_fixture(path):
    with open(path, encoding='utf-8') as f:
        body = f.read()
    _assert_round_trips(body)


def test_golden_fixture_set_is_present():
    files = glob.glob(os.path.join(GOLDEN_DIR, '*.jly.txt'))
    assert len(files) >= 10
