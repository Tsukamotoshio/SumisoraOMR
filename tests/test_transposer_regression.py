# tests/test_transposer_regression.py — regression tests for two transposer
# constraints that were previously enforced only by a paragraph of prose in
# CLAUDE.md, not by any test (P1-2 in the fix-plan doc):
#
#   1. Rest elements' <display-step>/<display-octave> (intentional staff-position
#      info, e.g. voice-1 rests displaced upward in polyphonic scores) must
#      survive transposition byte-for-byte — _transpose_xml_bytes only touches
#      <pitch> and <key>/<fifths> elements.
#   2. musicxml2ly must never be invoked with --nrp — that strips the same
#      display-step/display-octave info at render time, breaking multi-voice
#      layout the same way stripping it at transpose time would.
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

from core.notation.transposer import _transpose_xml_bytes

_FIXTURE = Path(__file__).parent / 'fixtures' / 'musicxml' / 'Scarborough Fair-Flauta.musicxml'


def _rest_positions(xml_bytes: bytes) -> list[tuple[Optional[str], Optional[str]]]:
    """Return (display-step, display-octave) for every <rest> element, in document order."""
    root = ET.fromstring(xml_bytes)
    positions = []
    for rest in root.iter('rest'):
        step = rest.findtext('display-step')
        octave = rest.findtext('display-octave')
        if step is not None or octave is not None:
            positions.append((step, octave))
    return positions


def _all_pitches(xml_bytes: bytes) -> list[tuple[Optional[str], Optional[str]]]:
    """Return (step, octave) for every <pitch> element, in document order."""
    root = ET.fromstring(xml_bytes)
    return [(p.findtext('step'), p.findtext('octave')) for p in root.iter('pitch')]


class TestRestPositionPreservedAcrossTranspose:
    """P1-2: transposition must never touch rest display-position info."""

    def test_fixture_actually_has_positioned_rests(self):
        # Sanity check on the fixture itself, not the transposer: if this ever
        # returns 0, the whole test class below is silently vacuous.
        raw = _FIXTURE.read_bytes()
        assert len(_rest_positions(raw)) >= 20

    def test_display_step_and_octave_survive_transposition(self):
        raw = _FIXTURE.read_bytes()
        transposed = _transpose_xml_bytes(raw, semitones=3)
        assert _rest_positions(transposed) == _rest_positions(raw)

    def test_pitches_actually_changed(self):
        # Guards against the above passing vacuously because semitones=0 or
        # the regex silently no-opped.
        raw = _FIXTURE.read_bytes()
        transposed = _transpose_xml_bytes(raw, semitones=3)
        assert _all_pitches(transposed) != _all_pitches(raw)

    def test_negative_and_larger_shifts_also_preserve_rests(self):
        raw = _FIXTURE.read_bytes()
        for semitones in (-5, 7, 11):
            assert _rest_positions(_transpose_xml_bytes(raw, semitones)) == _rest_positions(raw), semitones

    def test_diatonic_offset_path_also_preserves_rests(self):
        # diatonic_offset engages the separate diatonic-aware pitch-spelling
        # branch inside _shift_pitch_block; it must not touch rests either.
        raw = _FIXTURE.read_bytes()
        transposed = _transpose_xml_bytes(raw, semitones=2, diatonic_offset=1)
        assert _rest_positions(transposed) == _rest_positions(raw)


class TestMusicxml2lyNeverGetsNrpFlag:
    """P1-2: --nrp strips the same rest-position info musicxml2ly would
    otherwise preserve by default, breaking multi-voice layout the same way
    stripping it at transpose time would (see the class above).

    A subprocess-level test would need the bundled LilyPond runtime present
    (CI's requirements-ci.txt deliberately excludes it — see CLAUDE.md's CI
    section), so this asserts the weaker-but-CI-safe property instead: the
    flag does not appear anywhere in the module that shells out to
    musicxml2ly. That module's only subprocess.run call is the musicxml2ly
    invocation itself, so absence from the whole file is equivalent to
    absence from that call.
    """

    def test_no_nrp_flag_in_lilypond_runner_source(self):
        source = Path('core/render/lilypond_runner.py').read_text(encoding='utf-8')
        assert '--nrp' not in source

    def test_no_nrp_flag_anywhere_in_core(self):
        for path in Path('core').rglob('*.py'):
            assert '--nrp' not in path.read_text(encoding='utf-8', errors='ignore'), path
