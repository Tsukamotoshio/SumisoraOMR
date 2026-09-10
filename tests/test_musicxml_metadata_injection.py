"""Header-metadata injection: one <work-title>, in a schema-valid position.

Regression cover for the two defects the 2026-09 homr upstream merge exposed:
homr writes a self-closing `<work-title />` when `title_detection=False`, which the
old paired-only regex missed — it then inserted a *second* work-title, and where the
document already carried a music21-written `<movement-title>` it put the new `<work>`
*after* it, violating the score-partwise content order
(`work?, movement-number?, movement-title?, identification?, ...`).

The five shapes below are the ones actually observed across the 21-input golden run.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from core.app.pipeline import _inject_musicxml_metadata

_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<score-partwise version="4.0">'
_TAIL = '<part-list /></score-partwise>'

SHAPES = {
    # homr with title detection on
    'paired_work_title': f'{_HEAD}<work><work-title>homr_input</work-title></work>'
                         f'<identification><encoding /></identification>{_TAIL}',
    # homr with title_detection=False  → the D1 trigger
    'self_closing_work_title': f'{_HEAD}<work><work-title /></work>'
                               f'<identification><encoding /></identification>{_TAIL}',
    # music21 re-serialised, work-title empty → the D2 trigger
    'movement_title_only': f'{_HEAD}<movement-title>homr_input</movement-title>'
                           f'<identification><encoding /></identification>{_TAIL}',
    # already-duplicated input (seen on two inputs pre-merge): must converge to one
    'duplicated_work_title': f'{_HEAD}<work><work-title>keep</work-title>'
                             f'<work-title /></work>'
                             f'<identification><encoding /></identification>{_TAIL}',
    # no header metadata at all
    'bare': f'{_HEAD}<identification><encoding /></identification>{_TAIL}',
}

# score-partwise: work?, movement-number?, movement-title?, identification?, defaults?
_ORDER = ['work', 'movement-number', 'movement-title', 'identification', 'defaults']


@pytest.mark.parametrize('shape', sorted(SHAPES))
def test_exactly_one_work_title_with_the_requested_text(tmp_path, shape):
    f = tmp_path / 'score.musicxml'
    f.write_text(SHAPES[shape], encoding='utf-8')

    _inject_musicxml_metadata(f, title='涛声依旧')
    out = f.read_text(encoding='utf-8')

    titles = re.findall(r'<work-title[^>]*>([^<]*)</work-title>|<work-title[^>]*/>', out)
    assert len(titles) == 1, f'{shape}: expected 1 work-title, got {len(titles)}'
    root = ET.fromstring(out)
    assert root.findtext('work/work-title') == '涛声依旧'


@pytest.mark.parametrize('shape', sorted(SHAPES))
def test_header_children_stay_in_schema_order(tmp_path, shape):
    f = tmp_path / 'score.musicxml'
    f.write_text(SHAPES[shape], encoding='utf-8')

    _inject_musicxml_metadata(f, title='Scarborough Fair')
    root = ET.fromstring(f.read_text(encoding='utf-8'))

    seen = [c.tag for c in root if c.tag in _ORDER]
    ranks = [_ORDER.index(t) for t in seen]
    assert ranks == sorted(ranks), f'{shape}: header order {seen} violates MusicXML'


def test_movement_title_is_overwritten_but_never_invented(tmp_path):
    f = tmp_path / 'score.musicxml'
    f.write_text(SHAPES['movement_title_only'], encoding='utf-8')
    _inject_musicxml_metadata(f, title='T')
    assert '<movement-title>T</movement-title>' in f.read_text(encoding='utf-8')

    f.write_text(SHAPES['bare'], encoding='utf-8')
    _inject_musicxml_metadata(f, title='T')
    assert '<movement-title' not in f.read_text(encoding='utf-8')


def test_injection_is_idempotent(tmp_path):
    f = tmp_path / 'score.musicxml'
    f.write_text(SHAPES['self_closing_work_title'], encoding='utf-8')
    _inject_musicxml_metadata(f, title='T')
    once = f.read_text(encoding='utf-8')
    _inject_musicxml_metadata(f, title='T')
    assert f.read_text(encoding='utf-8') == once


def test_title_is_xml_escaped(tmp_path):
    f = tmp_path / 'score.musicxml'
    f.write_text(SHAPES['bare'], encoding='utf-8')
    _inject_musicxml_metadata(f, title='A & B <x>')
    root = ET.fromstring(f.read_text(encoding='utf-8'))
    assert root.findtext('work/work-title') == 'A & B <x>'
