"""Tie reconstruction: a curve must leave the first note, and grace notes never count.

Found on 音乐的瞬间 (2026-10): with its grace notes gone, the short curve from each
grace note to its main note survived only as a slur stop on the main note, and the
heuristic counted that half-curve as tie evidence. Two re-struck E5s, each with its
own grace note, came out tied — five such ties on that score, and they were the
heuristic's only hits across the 21 sample inputs.
"""
from __future__ import annotations

import pytest

from core.notation.tie_reconstruction import reconstruct_ties_in_musicxml
from tests._grace_fragments import RESTRUCK_WITH_GRACES, SLUR_START, SLUR_STOP, quarter, score

music21 = pytest.importorskip('music21')

# 音乐的瞬间 m5-m6 after the old fix_homr_output: graces gone, their slur stops left behind.
_RESTRUCK_GRACES_DROPPED = score([
    quarter('E', 5, SLUR_STOP) + quarter('E', 5, SLUR_STOP),
    quarter('E', 5, SLUR_STOP) + quarter('E', 5),
])


def _ties(path) -> list[str]:
    return [n.tie.type for n in music21.converter.parse(str(path)).recurse().notes if n.tie]


def test_grace_note_slurs_do_not_become_ties(tmp_path):
    f = tmp_path / 's.musicxml'
    f.write_text(RESTRUCK_WITH_GRACES, encoding='utf-8')
    assert reconstruct_ties_in_musicxml(f) == 0
    assert _ties(f) == []


def test_a_slur_stop_on_the_second_note_alone_is_not_tie_evidence(tmp_path):
    f = tmp_path / 's.musicxml'
    f.write_text(_RESTRUCK_GRACES_DROPPED, encoding='utf-8')
    assert reconstruct_ties_in_musicxml(f) == 0


def test_a_curve_leaving_the_first_note_still_counts(tmp_path):
    """The half-tie hanging at a line end (slur start on a, nothing on b) stays a tie."""
    f = tmp_path / 's.musicxml'
    f.write_text(score([quarter('C', 5) + quarter('E', 5, SLUR_START),
                        quarter('E', 5) + quarter('C', 5)]), encoding='utf-8')
    assert reconstruct_ties_in_musicxml(f) == 1
    assert _ties(f) == ['start', 'stop']
