"""Grace notes survive HOMR post-processing into the MusicXML, and only there.

`fix_homr_output` used to drop every grace note as a "zero-length note" (music21
gives them quarterLength 0), which stripped them from the archived MusicXML the
transposer and the staff PDF read. They are kept now, and taken out only where they
cannot be represented: the jianpu text has no notation for them, and music21's MIDI
export plays the first one for a full beat and drops the rest.
"""
from __future__ import annotations

import pytest

from tests._grace_fragments import RESTRUCK_WITH_GRACES, SLUR_STOP, grace, quarter, score

music21 = pytest.importorskip('music21')


def test_fix_homr_output_keeps_grace_notes(tmp_path):
    from core.omr.dl_fix import fix_homr_output

    src = tmp_path / 'in.musicxml'
    # a short second measure makes the jianpu alignment rewrite the file
    src.write_text(score([
        grace('B', 4) + quarter('E', 5, SLUR_STOP) + grace('B', 4) + quarter('E', 5, SLUR_STOP),
        grace('F', 5) + quarter('E', 5, SLUR_STOP),
    ]), encoding='utf-8')
    out = fix_homr_output(src, tmp_path / 'work', align_for_jianpu=True)
    assert out is not None and out != src  # the padding rest did get written
    notes = list(music21.converter.parse(str(out)).recurse().notes)
    assert sum(n.duration.isGrace for n in notes) == 3
    assert sum(not n.duration.isGrace for n in notes) == 3


def test_strip_grace_notes_leaves_the_sounding_notes():
    from core.notation.jianpu import strip_grace_notes

    parsed = music21.converter.parseData(RESTRUCK_WITH_GRACES, format='musicxml')
    assert strip_grace_notes(parsed) == 3
    notes = list(parsed.recurse().notes)
    assert [n.pitch.nameWithOctave for n in notes] == ['E5'] * 4
    assert [float(n.quarterLength) for n in notes] == [1.0] * 4
