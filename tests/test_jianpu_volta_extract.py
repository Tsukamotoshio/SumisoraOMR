# tests/test_jianpu_volta_extract.py — coverage for volta (1./2. ending)
# extraction, stage 6.2a in the fix-plan doc.
#
# Until this existed the pipeline read repeat *barlines* off
# leftBarline/rightBarline and nothing else, so every 1./2. bracket an OMR
# result carried was dropped with no warning: the repeat signs printed, the
# brackets saying which bar to take on each pass did not. Measured on the
# local MusicXML corpus at the time: 5 of 40 files carried brackets.
import pytest

from core.notation.jianpu.extract import _extract_part_volta_brackets

music21 = pytest.importorskip('music21')


def _part(measure_count: int):
    """A part of *measure_count* single-note measures."""
    from music21 import note, stream

    part = stream.Part()
    for i in range(measure_count):
        m = stream.Measure(number=i + 1)
        m.append(note.Note('C4', quarterLength=4.0))
        part.append(m)
    return part, list(part.getElementsByClass('Measure'))


def _bracket(part, measures, number):
    from music21 import spanner

    br = spanner.RepeatBracket(measures, number=number)
    part.insert(0, br)
    return br


class TestVoltaExtraction:
    def test_endpoint_only_bracket_becomes_a_full_range(self):
        # The one that matters. MusicXML writes a volta as a start/stop pair,
        # so music21's RepeatBracket holds only its two endpoints — a 4-measure
        # bracket arrives as 2 elements. Reading getSpannedElements() as "the
        # measures covered" would silently lose the middle of every bracket
        # longer than two bars.
        part, ms = _part(8)
        _bracket(part, [ms[2], ms[5]], 1)
        assert _extract_part_volta_brackets(part) == [
            {'number': '1', 'first': 2, 'last': 5},
        ]

    def test_single_measure_bracket(self):
        part, ms = _part(4)
        _bracket(part, [ms[3]], 2)
        assert _extract_part_volta_brackets(part) == [
            {'number': '2', 'first': 3, 'last': 3},
        ]

    def test_two_brackets_come_back_in_score_order(self):
        part, ms = _part(8)
        _bracket(part, [ms[6]], 2)          # inserted second-ending first
        _bracket(part, [ms[4], ms[5]], 1)
        assert _extract_part_volta_brackets(part) == [
            {'number': '1', 'first': 4, 'last': 5},
            {'number': '2', 'first': 6, 'last': 6},
        ]

    def test_reversed_endpoints_are_normalised(self):
        part, ms = _part(6)
        _bracket(part, [ms[4], ms[1]], 1)
        assert _extract_part_volta_brackets(part) == [
            {'number': '1', 'first': 1, 'last': 4},
        ]

    def test_unnumbered_bracket_is_skipped(self):
        # Nothing to print in the bracket, and nothing downstream can decide
        # which pass it belongs to — better dropped than guessed at.
        part, ms = _part(4)
        _bracket(part, [ms[1], ms[2]], '')
        assert _extract_part_volta_brackets(part) == []

    def test_no_brackets_at_all(self):
        part, _ms = _part(4)
        assert _extract_part_volta_brackets(part) == []

    def test_number_is_a_string_so_it_survives_json(self):
        # The value rides to the editor inside a #__jianpu_meta__ JSON line and
        # is printed verbatim as the bracket label, so it stays text — music21
        # hands back an int for a plain "1".
        part, ms = _part(4)
        _bracket(part, [ms[1]], 1)
        (only,) = _extract_part_volta_brackets(part)
        assert isinstance(only['number'], str)
