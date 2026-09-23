# tests/test_jianpu_dynamics_extract.py — reading <dynamics> out of MusicXML,
# stage 6.1b in the fix-plan doc.
#
# Until this existed the pipeline never looked at <dynamics> at all: every p /
# mf / f an OMR result carried was dropped on the floor, silently. Measured on
# the local MusicXML corpus at the time: 5 of 40 files carried 22 marks.
#
# Where a mark lands follows the rule stage 6.1a established by rendering, not
# by reading LilyPond's warnings (see tests/test_jianpu_dynamics.py's header):
# a mark with no note before it attaches to the NEXT note. So a mark that falls
# between two notes belongs to the later one, and one left over at the end of a
# measure belongs to the first note of the next measure.
import pytest

from core.config import JianpuNote
from core.notation.jianpu.extract import (
    _attach_dynamics_to_measures,
    _extract_part_dynamics,
    extract_jianpu_measures,
)

music21 = pytest.importorskip('music21')


def _part(measure_count: int, notes_per_measure: int = 4, first_number: int = 1):
    """A part of *measure_count* measures, each holding quarter notes."""
    from music21 import meter, note, stream

    part = stream.Part()
    for i in range(measure_count):
        m = stream.Measure(number=first_number + i)
        if i == 0:
            m.insert(0.0, meter.TimeSignature('4/4'))
        for j in range(notes_per_measure):
            m.insert(float(j), note.Note('C4', quarterLength=1.0))
        part.append(m)
    return part, list(part.getElementsByClass('Measure'))


def _mark(measure, offset: float, value: str):
    from music21 import dynamics

    measure.insert(offset, dynamics.Dynamic(value))


def _note(duration: float = 1.0, symbol: str = '1') -> JianpuNote:
    return JianpuNote(symbol=symbol, accidental='', upper_dots=0, lower_dots=0,
                      duration=duration, duration_dots=0, midi=60, is_rest=symbol == '0')


# ── reading them out of the part ─────────────────────────────────────────────

class TestExtraction:
    def test_a_mark_is_keyed_by_its_measure_and_offset(self):
        part, ms = _part(3)
        _mark(ms[1], 2.0, 'mf')
        assert _extract_part_dynamics(part) == {1: [(2.0, 'mf')]}

    def test_the_key_is_the_position_not_the_printed_number(self):
        # OMR output numbers a pickup bar 0 (and sometimes skips numbers), while
        # the jianpu measure list is built by walking the part positionally.
        part, ms = _part(3, first_number=0)
        _mark(ms[2], 0.0, 'p')
        assert _extract_part_dynamics(part) == {2: [(0.0, 'p')]}

    def test_several_marks_in_one_measure_come_back_in_time_order(self):
        part, ms = _part(2)
        _mark(ms[0], 3.0, 'f')
        _mark(ms[0], 1.0, 'p')
        assert _extract_part_dynamics(part) == {0: [(1.0, 'p'), (3.0, 'f')]}

    def test_a_mark_inside_a_voice_container_is_found(self):
        # Polyphonic OMR output puts the notes in <voice> containers; the mark
        # is a direction on the staff and can end up nested with them.
        from music21 import dynamics, note, stream

        part = stream.Part()
        m = stream.Measure(number=1)
        v = stream.Voice(id='1')
        for j in range(4):
            v.insert(float(j), note.Note('C4', quarterLength=1.0))
        v.insert(2.0, dynamics.Dynamic('ff'))
        m.insert(0.0, v)
        part.append(m)
        assert _extract_part_dynamics(part) == {0: [(2.0, 'ff')]}

    def test_a_mark_lilypond_does_not_know_is_skipped(self):
        # jianpu-ly passes any backslash word straight through, so an unknown
        # one would become a LilyPond error and make the file unopenable in the
        # editor. Case matters: LilyPond has \pp but no \PP.
        part, ms = _part(2)
        _mark(ms[0], 0.0, 'PP')
        _mark(ms[1], 0.0, 'molto forte')
        assert _extract_part_dynamics(part) == {}

    def test_all_twenty_two_lilypond_marks_survive(self):
        from core.notation.jianpu.primitives import DYNAMIC_MARKS

        part, ms = _part(len(DYNAMIC_MARKS))
        for i, value in enumerate(sorted(DYNAMIC_MARKS)):
            _mark(ms[i], 0.0, value)
        assert len(_extract_part_dynamics(part)) == len(DYNAMIC_MARKS) == 22

    def test_a_part_without_marks_gives_an_empty_mapping(self):
        part, _ms = _part(3)
        assert _extract_part_dynamics(part) == {}


# ── putting them on the right note ───────────────────────────────────────────

class TestAttaching:
    def test_a_mark_lands_on_the_note_starting_at_its_offset(self):
        measures = [[_note() for _ in range(4)]]
        _attach_dynamics_to_measures(measures, {0: [(2.0, 'mf')]})
        assert [n.dynamic for n in measures[0]] == ['', '', 'mf', '']

    def test_a_mark_between_two_notes_goes_on_the_later_one(self):
        measures = [[_note(2.0), _note(2.0)]]
        _attach_dynamics_to_measures(measures, {0: [(1.5, 'f')]})
        assert [n.dynamic for n in measures[0]] == ['', 'f']

    def test_a_mark_past_the_last_note_moves_to_the_next_measure(self):
        # <direction> after the last note of a bar is how "from here on" is
        # often engraved; LilyPond would attach it to the next note anyway.
        measures = [[_note(), _note()], [_note(), _note()]]
        _attach_dynamics_to_measures(measures, {0: [(3.5, 'pp')]})
        assert [n.dynamic for n in measures[0]] == ['', '']
        assert [n.dynamic for n in measures[1]] == ['pp', '']

    def test_a_mark_past_the_last_note_of_the_piece_is_dropped(self):
        # Nothing left to carry it to. Writing it out would produce a file the
        # editor's parser refuses to open, and LilyPond drops it as well.
        measures = [[_note(), _note()]]
        _attach_dynamics_to_measures(measures, {0: [(3.5, 'pp')]})
        assert [n.dynamic for n in measures[0]] == ['', '']

    def test_the_first_of_two_marks_on_one_note_wins(self):
        # LilyPond keeps the first and discards the rest; the parser rejects a
        # file that writes two on one note, so only one may be written.
        measures = [[_note(), _note()]]
        _attach_dynamics_to_measures(measures, {0: [(0.0, 'p'), (0.0, 'f')]})
        assert [n.dynamic for n in measures[0]] == ['p', '']

    def test_an_empty_mapping_changes_nothing(self):
        measures = [[_note(), _note()]]
        _attach_dynamics_to_measures(measures, {})
        assert [n.dynamic for n in measures[0]] == ['', '']

    def test_a_measure_index_past_the_end_is_ignored(self):
        # extract_jianpu_measures truncates at MAX_SANE_BARS, so the mapping can
        # name a measure that no longer exists.
        measures = [[_note(), _note()]]
        _attach_dynamics_to_measures(measures, {7: [(0.0, 'f')]})
        assert [n.dynamic for n in measures[0]] == ['', '']


# ── through the real extractor ───────────────────────────────────────────────

class TestThroughTheExtractor:
    def test_the_mark_reaches_the_jianpu_note(self):
        from music21 import stream

        part, ms = _part(2)
        _mark(ms[1], 1.0, 'mf')
        score = stream.Score()
        score.insert(0, part)
        measures, _ts = extract_jianpu_measures(score, 0)
        assert [n.dynamic for n in measures[1]] == ['', 'mf', '', '']

    def test_only_the_primary_voice_of_a_part_gets_them(self):
        # A dynamic belongs to the staff. Attaching it to every voice would
        # print the same mark two to four times on one staff.
        from music21 import stream

        part, ms = _part(2)
        _mark(ms[0], 0.0, 'f')
        score = stream.Score()
        score.insert(0, part)
        measures, _ts = extract_jianpu_measures(
            score, 0, _part=part, _voice_id='1',
            _multi_voice_mode=True, _is_primary_voice=False)
        assert all(not n.dynamic for m in measures for n in m)

    def test_the_generated_text_still_parses(self):
        # The editor opens what the pipeline writes: a mark that landed badly
        # (orphaned, or two on one note) would make the file unopenable.
        from music21 import stream

        from core.notation.jianpu import build_jianpu_ly_text
        from core.notation.jianpu.parser import parse_jianpu_ly_text

        part, ms = _part(3)
        _mark(ms[0], 0.0, 'p')       # first note of the piece
        _mark(ms[1], 4.0, 'f')       # past the last note -> next measure
        _mark(ms[2], 2.0, 'ff')
        score = stream.Score()
        score.insert(0, part)
        text = build_jianpu_ly_text(score, title='T')
        assert text.count('\\p') == 1 and text.count('\\f') == 2  # \ff contains \f
        doc = parse_jianpu_ly_text(text)
        marks = [n.dynamic for section in doc.sections for m in section.measures for n in m if n.dynamic]
        assert marks == ['p', 'f', 'ff']
