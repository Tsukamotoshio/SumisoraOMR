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


def _parse_time_sig(time_sig: str) -> tuple[int, int]:
    """Parse a 'num/denom[,pickup]' time signature string; fall back to 4/4."""
    m = _TIMESIG_RE.match(time_sig)
    if not m:
        return 4, 4
    return int(m.group(1)), int(m.group(2))


def jianpu_section_to_render_json(section: 'JianpuSection', key_header: str) -> dict:
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
    }
