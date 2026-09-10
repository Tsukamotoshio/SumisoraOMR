"""`<tie>` must land in the one slot MusicXML allows, and doing so must not move music.

The 2026-09 homr upstream comparison showed our reconstruction inserting `<tie>` just
before `<notations>` — i.e. after `<staff>` — while upstream's own converter inserts it
straight after `<duration>`. The DTD allows only the latter:

    normal note : (pitch|rest|unpitched), duration, tie*, instrument*, ..., staff?, notations*
    grace note  : grace, (pitch|rest|unpitched), tie*            (no duration at all)

Files that happened to pass through music21 were silently renormalised; files written
straight out of homr kept the invalid order.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from core.notation.tie_reconstruction import (
    _add_tie_to_note,
    _tie_insert_index,
    reconstruct_ties_in_musicxml,
)

# Everything <tie> must come after, and everything it must come before. Deliberately
# not a full child-order check: homr emits <type> ahead of <voice>, which is itself out
# of DTD order, but that is upstream's and unrelated to where we put <tie>.
_BEFORE_TIE = ('grace', 'chord', 'pitch', 'rest', 'unpitched', 'duration')
_AFTER_TIE = ('instrument', 'footnote', 'level', 'voice', 'type', 'dot', 'accidental',
              'time-modification', 'stem', 'notehead', 'staff', 'beam', 'notations',
              'lyric', 'play', 'listen')


def _note(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def test_tie_follows_duration_on_a_normal_note():
    n = _note('<note><pitch><step>A</step><octave>4</octave></pitch><duration>4</duration>'
              '<type>quarter</type><voice>1</voice><staff>1</staff><notations /></note>')
    _add_tie_to_note(n, 'start', '')
    tags = [c.tag for c in n]
    assert tags.index('tie') == tags.index('duration') + 1


def test_tie_follows_the_full_note_on_a_grace_note_with_no_duration():
    n = _note('<note><grace /><pitch><step>A</step><octave>4</octave></pitch>'
              '<voice>1</voice><notations /></note>')
    assert _tie_insert_index(n) == 2  # after <grace><pitch>
    _add_tie_to_note(n, 'start', '')
    tags = [c.tag for c in n]
    assert tags.index('tie') == tags.index('pitch') + 1


def test_tie_never_precedes_grace():
    n = _note('<note><grace /><rest /><voice>1</voice></note>')
    _add_tie_to_note(n, 'stop', '')
    tags = [c.tag for c in n]
    assert tags.index('tie') > tags.index('grace')


@pytest.mark.parametrize('tie_type', ['start', 'stop'])
def test_tie_separates_the_full_note_group_from_everything_after_it(tie_type):
    # real homr shape, <type> ahead of <voice> included
    n = _note('<note><chord /><pitch><step>C</step><octave>5</octave></pitch>'
              '<duration>8</duration><type>half</type><voice>2</voice>'
              '<staff>2</staff><notations /></note>')
    _add_tie_to_note(n, tie_type, '')
    tags = [c.tag for c in n]
    at = tags.index('tie')
    assert all(tags.index(t) < at for t in _BEFORE_TIE if t in tags), tags
    assert all(tags.index(t) > at for t in _AFTER_TIE if t in tags), tags


def test_add_tie_is_idempotent():
    n = _note('<note><pitch><step>A</step><octave>4</octave></pitch><duration>4</duration>'
              '<notations /></note>')
    _add_tie_to_note(n, 'start', '')
    once = ET.tostring(n)
    _add_tie_to_note(n, 'start', '')
    assert ET.tostring(n) == once


def test_placement_does_not_change_what_music21_reads(tmp_path):
    """The fix is a position change only: the parsed score must be unaffected."""
    music21 = pytest.importorskip('music21')

    # a homr-shaped fragment: tie-as-slur on an adjacent same-pitch pair (rule E)
    src = '''<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0"><part-list><score-part id="P1">
<part-name>P</part-name></score-part></part-list>
<part id="P1"><measure number="1">
<attributes><divisions>4</divisions><time><beats>4</beats><beat-type>4</beat-type></time>
<clef><sign>G</sign><line>2</line></clef></attributes>
<note><pitch><step>A</step><octave>4</octave></pitch><duration>8</duration>
<type>half</type><voice>1</voice><staff>1</staff>
<notations><slur type="start" number="1" /></notations></note>
<note><pitch><step>A</step><octave>4</octave></pitch><duration>8</duration>
<type>half</type><voice>1</voice><staff>1</staff>
<notations><slur type="stop" number="1" /></notations></note>
</measure></part></score-partwise>'''
    f = tmp_path / 's.musicxml'
    f.write_text(src, encoding='utf-8')

    assert reconstruct_ties_in_musicxml(f) == 1
    out = f.read_text(encoding='utf-8')

    # schema slot, and the slur consumed rather than drawn on top of the tie
    root = ET.fromstring(out)
    for n in root.iter('note'):
        tags = [c.tag for c in n]
        assert tags.index('tie') == tags.index('duration') + 1
    assert '<slur' not in out

    score = music21.converter.parse(str(f))
    notes = list(score.recurse().notes)
    assert len(notes) == 2
    assert notes[0].tie is not None and notes[0].tie.type == 'start'
    assert notes[1].tie is not None and notes[1].tie.type == 'stop'
    assert [n.pitch.midi for n in notes] == [69, 69]
    assert [float(n.quarterLength) for n in notes] == [2.0, 2.0]


_TWO_TIED_HALVES = '''<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0"><part-list><score-part id="P1">
<part-name>P</part-name></score-part></part-list>
<part id="P1"><measure number="1">
<attributes><divisions>4</divisions><time><beats>4</beats><beat-type>4</beat-type></time>
<clef><sign>G</sign><line>2</line></clef></attributes>
<note><pitch><step>A</step><octave>4</octave></pitch><duration>8</duration>
<type>half</type><voice>1</voice><staff>1</staff>{first}</note>
<note><pitch><step>A</step><octave>4</octave></pitch><duration>8</duration>
<type>half</type><voice>1</voice><staff>1</staff>{second}</note>
</measure></part></score-partwise>'''


def _write(tmp_path, first, second):
    f = tmp_path / 's.musicxml'
    f.write_text(_TWO_TIED_HALVES.format(first=first, second=second), encoding='utf-8')
    return f


def test_return_value_counts_writes_not_decisions(tmp_path):
    """A pair that is already fully tied is still *decided*, but nothing is written.

    The count feeds the user-facing "重建延音线 N 对" log line, so counting decisions
    made it overstate badly: re-running over an already-reconstructed score reported
    tens of pairs while touching nothing.
    """
    already = _write(
        tmp_path,
        '<tie type="start" /><notations><tied type="start" /></notations>',
        '<tie type="stop" /><notations><tied type="stop" /></notations>',
    )
    assert reconstruct_ties_in_musicxml(already) == 0

    fresh = _write(
        tmp_path,
        '<notations><slur type="start" number="1" /></notations>',
        '<notations><slur type="stop" number="1" /></notations>',
    )
    assert reconstruct_ties_in_musicxml(fresh) == 1


def test_a_half_written_tie_still_counts_as_a_write(tmp_path):
    """<tied> present but <tie> missing (or vice versa) is an incomplete tie."""
    half = _write(
        tmp_path,
        '<notations><tied type="start" /></notations>',   # no <tie>
        '<notations><tied type="stop" /></notations>',
    )
    assert reconstruct_ties_in_musicxml(half) == 1
    out = half.read_text(encoding='utf-8')
    assert out.count('<tie ') == 2


def test_rerunning_is_a_no_op(tmp_path):
    f = _write(
        tmp_path,
        '<notations><slur type="start" number="1" /></notations>',
        '<notations><slur type="stop" number="1" /></notations>',
    )
    assert reconstruct_ties_in_musicxml(f) == 1
    once = f.read_text(encoding='utf-8')
    assert reconstruct_ties_in_musicxml(f) == 0
    assert f.read_text(encoding='utf-8') == once
