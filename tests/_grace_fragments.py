"""MusicXML fragments in homr's shape, with grace notes slurred into their main notes.

Modelled on 音乐的瞬间 m5-m6, where each E5 is struck again after its own grace note.
Shared by the grace-note and tie-evidence tests.
"""
from __future__ import annotations

_HEAD = '''<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0"><part-list><score-part id="P1">
<part-name>P</part-name></score-part></part-list>
<part id="P1">'''
_ATTRS = ('<attributes><divisions>4</divisions><time><beats>2</beats><beat-type>4</beat-type>'
          '</time><clef><sign>G</sign><line>2</line></clef></attributes>')

SLUR_STOP = '<notations><slur type="stop" number="1" /></notations>'
SLUR_START = '<notations><slur type="start" number="1" /></notations>'


def grace(step: str, octave: int) -> str:
    return (f'<note><grace /><pitch><step>{step}</step><octave>{octave}</octave></pitch>'
            '<voice>1</voice><type>eighth</type><staff>1</staff>' + SLUR_START + '</note>')


def quarter(step: str, octave: int, notations: str = '') -> str:
    return (f'<note><pitch><step>{step}</step><octave>{octave}</octave></pitch>'
            '<duration>4</duration><voice>1</voice><type>quarter</type><staff>1</staff>'
            f'{notations}</note>')


def score(measures: list[str]) -> str:
    """A one-part 2/4 score, one string of <note> elements per measure."""
    body = ''.join(f'<measure number="{i}">{_ATTRS if i == 1 else ""}{m}</measure>'
                   for i, m in enumerate(measures, 1))
    return _HEAD + body + '</part></score-partwise>'


RESTRUCK_WITH_GRACES = score([
    grace('B', 4) + quarter('E', 5, SLUR_STOP) + grace('B', 4) + quarter('E', 5, SLUR_STOP),
    grace('F', 5) + quarter('E', 5, SLUR_STOP) + quarter('E', 5),
])
