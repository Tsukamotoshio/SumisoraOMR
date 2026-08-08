# tests/test_jianpu_note_midi.py — unit tests for the 阶段3.1 midi reverse
# math (core/notation/jianpu/primitives.py: key_header_tonic_semitone(),
# jianpu_note_to_midi()). See 修复计划2与简谱编辑器规划.md's B6 阶段3 子阶段表.
import pytest
from music21 import note as m21note, pitch as m21pitch

from core.notation.jianpu.primitives import (
    JianpuNote,
    jianpu_note_to_midi,
    key_header_tonic_semitone,
    note_to_jianpu,
)

# 7 sharp-leaning + 6 flat-leaning major tonics (all 12 pitch classes covered,
# Db/C# and Gb/F# both included since jianpu key headers can spell either way).
_MAJOR_HEADERS = [
    ('C', 'C'), ('G', 'G'), ('D', 'D'), ('A', 'A'), ('E', 'E'), ('B', 'B'),
    ('F#', 'F#'), ('C#', 'C#'), ('F', 'F'), ('Bb', 'B-'), ('Eb', 'E-'),
    ('Ab', 'A-'), ('Db', 'D-'), ('Gb', 'G-'), ('Cb', 'C-'),
]

_MINOR_HEADERS = [
    ('A', 'A'), ('E', 'E'), ('D', 'D'), ('G', 'G'), ('F#', 'F#'), ('Bb', 'B-'),
]


@pytest.mark.parametrize(('jianpu_name', 'm21_name'), _MAJOR_HEADERS)
def test_key_header_tonic_semitone_major(jianpu_name, m21_name):
    # Independent oracle: music21's own pitchClass for the same tonic, not
    # anything reusing key_header_tonic_semitone()'s own substitution logic.
    expected = m21pitch.Pitch(m21_name).pitchClass
    assert key_header_tonic_semitone(f'1={jianpu_name}') == expected


@pytest.mark.parametrize(('jianpu_name', 'm21_name'), _MINOR_HEADERS)
def test_key_header_tonic_semitone_minor_reduces_to_relative_major(jianpu_name, m21_name):
    # Minor headers ('6=X') must reduce to X's relative major, mirroring
    # primitives._relative_major_tonic() — computed here independently via
    # music21's own transpose, not by calling that private helper.
    expected = m21pitch.Pitch(m21_name).transpose('m3').pitchClass
    assert key_header_tonic_semitone(f'6={jianpu_name}') == expected


@pytest.mark.parametrize('key_tonic_semitone', [0, 7, 2, 9, 4, 11, 6, 1, 5, 10, 3, 8])
@pytest.mark.parametrize('midi_value', list(range(48, 85)))
def test_jianpu_note_to_midi_round_trips_note_to_jianpu(key_tonic_semitone, midi_value):
    # note_to_jianpu() only reads pitch.midi (never pitch spelling), so a
    # bare Note with .pitch.midi assigned is a sufficient, independent oracle:
    # forward-convert then invert and expect the exact original midi back.
    n = m21note.Note()
    n.pitch.midi = midi_value
    jn = note_to_jianpu(n, key_tonic_semitone)
    assert jianpu_note_to_midi(jn, key_tonic_semitone) == midi_value


def test_jianpu_note_to_midi_returns_none_for_rest():
    rest = JianpuNote(symbol='0', accidental='', upper_dots=0, lower_dots=0,
                       duration=1.0, duration_dots=0, midi=None, is_rest=True)
    assert jianpu_note_to_midi(rest, 0) is None


def test_jianpu_note_to_midi_returns_none_for_dash_continuation():
    dash = JianpuNote(symbol='-', accidental='', upper_dots=0, lower_dots=0,
                       duration=1.0, duration_dots=0, midi=None, is_rest=False)
    assert jianpu_note_to_midi(dash, 0) is None


def test_jianpu_note_to_midi_handles_noncanonical_sharp_and_flat_spellings():
    # note_to_jianpu() never emits '#3'/'#7'/'b1'/'b4' — they're enharmonically
    # identical to the neighbouring natural degree, so its canonical-spelling
    # tables always pick that other name instead. But real jianpu-ly text can
    # still contain them (found in a real local-corpus file), and they must
    # resolve to the correct pitch rather than returning None.
    sharp_3 = JianpuNote(symbol='3', accidental='#', upper_dots=0, lower_dots=0,
                          duration=1.0, duration_dots=0, midi=None, is_rest=False)
    natural_4 = JianpuNote(symbol='4', accidental='', upper_dots=0, lower_dots=0,
                            duration=1.0, duration_dots=0, midi=None, is_rest=False)
    assert jianpu_note_to_midi(sharp_3, 0) == jianpu_note_to_midi(natural_4, 0) == 65

    flat_4 = JianpuNote(symbol='4', accidental='b', upper_dots=0, lower_dots=0,
                         duration=1.0, duration_dots=0, midi=None, is_rest=False)
    natural_3 = JianpuNote(symbol='3', accidental='', upper_dots=0, lower_dots=0,
                            duration=1.0, duration_dots=0, midi=None, is_rest=False)
    assert jianpu_note_to_midi(flat_4, 0) == jianpu_note_to_midi(natural_3, 0) == 64


def test_jianpu_note_to_midi_returns_none_for_unknown_symbol():
    unknown = JianpuNote(symbol='?', accidental='', upper_dots=0, lower_dots=0,
                          duration=1.0, duration_dots=0, midi=None, is_rest=False)
    assert jianpu_note_to_midi(unknown, 0) is None
