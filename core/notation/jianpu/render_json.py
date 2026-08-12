# core/notation/jianpu/render_json.py — JianpuDoc → JianpuRender input JSON (阶段3.2).
"""Converts a single JianpuSection into the plain-dict shape JianpuRender's
``JianpuSVGRender`` constructor expects (its ``JianpuInfo`` interface —
``{notes, keySignatures, timeSignatures}``, see the vendored
``webui/static/vendor/jianpu-render/src/jianpu_info.ts``).

Scope is deliberately single-section: multi-voice ``NextPart`` documents get
one renderer instance per section, laid out vertically — that's 阶段3.5's
job, not this conversion function's. Callers pick which section to convert.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .primitives import key_header_tonic_semitone

if TYPE_CHECKING:
    from ...config import JianpuSection

_TIMESIG_RE = re.compile(r'^(\d+)/(\d+)')

# Quarter-notes-per-minute assumed when a file declares no `4=N` tempo line.
# Matches the default the "新建简谱" wizard fills in (webui/editor.py's
# create_blank and its front-end form), so a blank score plays back at the
# same speed it was created at.
DEFAULT_PLAYBACK_TEMPO = 120


def _parse_time_sig(time_sig: str) -> tuple[int, int]:
    """Parse a 'num/denom[,pickup]' time signature string; fall back to 4/4."""
    m = _TIMESIG_RE.match(time_sig)
    if not m:
        return 4, 4
    return int(m.group(1)), int(m.group(2))


def jianpu_section_to_render_json(
    section: 'JianpuSection', key_header: str, tempo: int = 0
) -> dict:
    """Build a JianpuRender ``JianpuInfo`` dict from one JianpuSection.

    Only real (non-rest, non-dash) notes become ``notes`` entries —
    JianpuRender auto-fills the gaps between notes as rests (see its
    ``jianpu_model.ts:infoToBlocks()``'s "Fill Rests" step), so rests need no
    explicit representation here. A dash-continuation note (``symbol='-'``)
    extends the *previous* emitted note's ``length`` instead of becoming a
    note of its own — that's what a tie/sustain means in jianpu-ly, not a
    new attack, and it's why ``JianpuNote.midi`` is left ``None`` for dashes
    (see primitives.jianpu_note_to_midi's docstring): there's nothing here
    for a dash to contribute beyond duration.

    *tempo* is the document-level quarter-notes-per-minute (``JianpuDoc.tempo``);
    0 or absent means the file declared none. It is emitted as a ``tempos``
    entry — unused by the visual render, but it is what turns the otherwise
    unitless quarter-note ``start``/``length`` values into wall-clock seconds
    for playback (阶段4.1). ``DEFAULT_PLAYBACK_TEMPO`` stands in when the file
    has none, matching what jianpu-ly itself assumes for a header-less score.
    """
    notes: list[dict] = []
    start = 0.0
    for measure in section.measures:
        for note in measure:
            if note.is_rest:
                start += note.duration
                continue
            if note.symbol == '-':
                if notes:
                    notes[-1]['length'] += note.duration
                start += note.duration
                continue
            if note.midi is not None:
                notes.append({
                    'start': start, 'length': note.duration,
                    'pitch': note.midi, 'intensity': 80,
                })
            start += note.duration

    numerator, denominator = _parse_time_sig(section.time_sig)
    return {
        'notes': notes,
        'keySignatures': [{'start': 0, 'key': key_header_tonic_semitone(key_header)}],
        'timeSignatures': [{'start': 0, 'numerator': numerator, 'denominator': denominator}],
        'tempos': [{'start': 0, 'qpm': tempo if tempo > 0 else DEFAULT_PLAYBACK_TEMPO}],
    }
