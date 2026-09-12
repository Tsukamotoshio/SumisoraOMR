"""Repeat marks music21 cannot expand must not cost the score its MIDI.

music21 expands repeats before writing MIDI and raises ExpanderException on a set
it cannot resolve, which killed the export outright — the batch produced no .mid
at all. 4 of the 40 archived sample scores were affected.

The trigger is a backward repeat followed by a volta bracket, with no matching
`|:`: a lone `:|` exports fine, a lone volta exports fine, and both on the same
measure exports fine — only a volta after the repeat fails.
OMR reads closing repeat dots and volta brackets far more reliably than the
opening `|:`, so that pairing is the normal shape of a recognised score.
"""
from __future__ import annotations

import pytest

from core.render.renderer import (
    _is_repeat_expansion_failure,
    _strip_repeats_for_midi,
    render_midi_from_score,
)

music21 = pytest.importorskip('music21')


def _score(*, backward_at: tuple[int, ...] = (), forward_at: tuple[int, ...] = (),
           volta_at: tuple[int, ...] = ()):
    from music21 import bar, note, spanner, stream
    sc = stream.Score()
    part = stream.Part()
    measures = []
    for i in range(1, 6):
        m = stream.Measure(number=i)
        m.append(note.Note('C4', quarterLength=4.0))
        if i in backward_at:
            m.rightBarline = bar.Repeat(direction='end')
        if i in forward_at:
            m.leftBarline = bar.Repeat(direction='start')
        part.append(m)
        measures.append(m)
    for n, i in enumerate(volta_at, 1):
        part.insert(0, spanner.RepeatBracket(measures[i - 1], number=str(n)))
    sc.append(part)
    return sc


def test_the_premise_a_repeat_plus_a_volta_really_is_rejected(tmp_path):
    """Without the fallback music21 refuses this exact shape."""
    with pytest.raises(Exception) as err:
        _score(backward_at=(2,), volta_at=(3,)).write('midi', fp=str(tmp_path / 'x.mid'))
    assert _is_repeat_expansion_failure(err.value)


@pytest.mark.parametrize('kwargs', [
    {'backward_at': (3,)},                 # closing repeat alone
    {'volta_at': (3,)},                    # volta alone
    {'forward_at': (2,), 'backward_at': (4,)},   # properly balanced
])
def test_shapes_music21_already_handles_are_left_alone(tmp_path, kwargs):
    """Guards the boundary: these must not be diverted into the fallback."""
    _score(**kwargs).write('midi', fp=str(tmp_path / 'ok.mid'))  # no exception


def test_midi_is_still_written_for_the_rejected_shape(tmp_path):
    dest = tmp_path / 'x.mid'
    assert render_midi_from_score(_score(backward_at=(2,), volta_at=(3,)), dest) is True
    assert dest.is_file() and dest.stat().st_size > 0


def test_balanced_repeats_still_sound_their_repeat(tmp_path):
    """The fallback must not fire on a score music21 can expand: a repeated
    section makes a longer file than the same score with the repeat removed."""
    with_repeat = tmp_path / 'a.mid'
    without = tmp_path / 'b.mid'
    assert render_midi_from_score(_score(forward_at=(2,), backward_at=(4,)), with_repeat)
    assert render_midi_from_score(_score(), without)
    assert with_repeat.stat().st_size > without.stat().st_size


def test_strip_repeats_reports_its_count_and_is_idempotent():
    sc = _score(forward_at=(1,), backward_at=(3, 5))
    assert _strip_repeats_for_midi(sc) == 3
    assert _strip_repeats_for_midi(sc) == 0


def test_volta_brackets_are_deliberately_kept():
    from music21 import spanner
    sc = _score(backward_at=(2,), volta_at=(3,))
    _strip_repeats_for_midi(sc)
    assert len(list(sc.parts[0].getElementsByClass(spanner.RepeatBracket))) == 1


def test_only_repeat_failures_are_retried():
    assert _is_repeat_expansion_failure(ValueError('something else')) is False
    assert _is_repeat_expansion_failure(ValueError('badly formed repeats here')) is True


def test_an_unrelated_export_failure_still_fails(tmp_path):
    dest = tmp_path / 'dir.mid'
    dest.mkdir()  # cannot be written as a file: not a repeat problem
    assert render_midi_from_score(_score(), dest) is False
