# core/notation/jianpu/doc_json.py — JianpuDoc ⇄ plain dict, for the bridge (阶段5.3).
"""Transport boundary for the editing model.

Up to 阶段3.6 the front-end only ever received the *render* projection
(``render_json.py``), which is deliberately lossy — it drops rests, folds
continuation dashes into the previous note's length, and carries no measure
boundaries, symbols, accidentals, octave dots or lyrics. That is fine for
drawing and playing, but an editor cannot be built on it: you cannot edit
what the projection threw away.

阶段5.3 therefore hands the front-end the *model* itself, edits it there
through the 阶段5.1 command stack, and sends it back to be serialized by
``build_jianpu_ly_text_from_doc`` — the writer stays in Python, in one copy.
(Reimplementing it in JS would create a second thing to keep in step; this
codebase already carries that cost once, where ``parser.py``'s tokenizer has
to mirror ``jianpu-lint.js``, and the comment there exists precisely to stop
the two drifting apart.)

**Why ``from_dict`` coerces rather than trusts.** The dict does not arrive as
Python wrote it — it goes out through JSON, so dict keys come back as strings
and tuples come back as lists. Three of those changes are not cosmetic:

* ``JianpuNote.lyrics`` keys become strings, and ``build_lyric_lines`` writes
  a ``"N. "`` stanza prefix for any verse ``!= 1``. A verse that left as
  ``1`` returns as ``'1'``, compares unequal, and the file silently gains a
  stanza marker it never had — no exception, just wrong output.
* A document mixing ``int`` and ``str`` verse keys raises ``TypeError`` inside
  ``sorted()``.
* ``doc.tempo`` is compared with ``> 0``, so a tempo that came back as a
  string raises ``TypeError`` instead of serializing.

All three were reproduced against the real serializer before this module was
written. Coercing here — at the one place untrusted data enters — keeps every
downstream consumer able to assume the declared types hold.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ...config import JianpuDoc, JianpuNote, JianpuSection
from .primitives import jianpu_note_to_midi, key_header_tonic_semitone


def jianpu_doc_to_dict(doc: JianpuDoc) -> dict:
    """Plain-dict form of *doc*, ready to be JSON-encoded for the front-end.

    ``asdict`` rather than a hand-written projection so that a field added to
    the dataclasses reaches the front-end automatically instead of being
    silently dropped here.
    """
    return asdict(doc)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _lyrics_from_raw(raw: Any) -> dict[int, tuple[str, bool]]:
    """Rebuild ``JianpuNote.lyrics`` from its JSON form.

    Keys back to ``int`` (see the module docstring — a string key silently
    changes the emitted text), values back to a 2-tuple. Entries whose verse
    is not a number at all are dropped rather than allowed to poison the
    ``sorted()`` in ``build_lyric_lines``.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[int, tuple[str, bool]] = {}
    for verse, entry in raw.items():
        try:
            verse_no = int(verse)
        except (TypeError, ValueError):
            continue
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            out[verse_no] = (str(entry[0]), bool(entry[1]))
        elif entry is not None:
            out[verse_no] = (str(entry), False)
    return out


def _note_from_raw(raw: Any) -> JianpuNote:
    raw = raw if isinstance(raw, dict) else {}
    symbol = str(raw.get('symbol', '0'))
    midi_raw = raw.get('midi')
    return JianpuNote(
        symbol=symbol,
        accidental=str(raw.get('accidental', '') or ''),
        upper_dots=_as_int(raw.get('upper_dots')),
        lower_dots=_as_int(raw.get('lower_dots')),
        duration=_as_float(raw.get('duration'), 1.0),
        duration_dots=_as_int(raw.get('duration_dots')),
        # Provisional only — jianpu_doc_from_dict recomputes every midi from
        # the document's own key once the whole document is assembled. See
        # the note there for why the incoming value cannot be trusted.
        midi=None if midi_raw is None else _as_int(midi_raw),
        is_rest=bool(raw.get('is_rest', symbol == '0')),
        lyrics=_lyrics_from_raw(raw.get('lyrics')),
    )


def _section_from_raw(raw: Any) -> JianpuSection:
    raw = raw if isinstance(raw, dict) else {}
    measures_raw = raw.get('measures')
    measures: list[list[JianpuNote]] = []
    if isinstance(measures_raw, list):
        for measure in measures_raw:
            if isinstance(measure, list):
                measures.append([_note_from_raw(n) for n in measure])
    return JianpuSection(time_sig=str(raw.get('time_sig', '4/4')), measures=measures)


def jianpu_doc_from_dict(raw: Any) -> JianpuDoc:
    """Rebuild a ``JianpuDoc`` from the front-end's JSON form.

    Tolerant by construction: missing or malformed pieces fall back to
    defaults rather than raising, because the alternative — an exception
    somewhere inside serialization — would surface to the user as a lost
    edit. Anything genuinely unrepresentable was already rejected earlier,
    at parse time, and never became a document at all.
    """
    raw = raw if isinstance(raw, dict) else {}
    sections_raw = raw.get('sections')
    sections = (
        [_section_from_raw(s) for s in sections_raw]
        if isinstance(sections_raw, list) else []
    )
    doc = JianpuDoc(
        title=str(raw.get('title', '') or ''),
        composer=str(raw.get('composer', '') or ''),
        key_header=str(raw.get('key_header', '1=C') or '1=C'),
        tempo=_as_int(raw.get('tempo')),
        sections=sections,
    )
    _recompute_midi(doc)
    return doc


def _recompute_midi(doc: JianpuDoc) -> None:
    """Re-derive every note's ``midi`` from the document's own key header.

    ``midi`` is a derived field — ``parse_jianpu_ly_text`` recomputes it from
    scratch on every parse rather than reading it from anywhere. An editor
    changing a note's ``symbol`` has no reason to also recompute its MIDI
    pitch, so a document arriving from the front-end routinely carries a
    ``midi`` belonging to the note's *previous* pitch. That matters because
    ``render_json`` draws from ``midi``, not from ``symbol``: left alone, an
    edited note keeps sounding and drawing at its old pitch while the text
    says otherwise.

    Recomputing here means every document that enters through this boundary
    is internally consistent, instead of each caller having to remember.
    """
    tonic = key_header_tonic_semitone(doc.key_header)
    for section in doc.sections:
        for measure in section.measures:
            for note in measure:
                note.midi = jianpu_note_to_midi(note, tonic)
