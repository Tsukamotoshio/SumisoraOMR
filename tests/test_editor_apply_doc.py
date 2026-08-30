# tests/test_editor_apply_doc.py — service-level coverage for the 阶段5.3
# write-back path: EditorService.graphical_render_data hands the front-end a
# model, EditorService.apply_doc takes the edited model back and turns it into
# text plus a fresh render projection.
#
# The doc<->dict boundary itself is covered by test_jianpu_doc_json.py; what is
# tested here is the service contract the bridge exposes.
import json
from typing import cast

from core.notation.jianpu.parser import parse_jianpu_ly_text
from webui.editor import EditorService
from webui.events import EventPusher
from webui.server import FileWhitelist

BODY = (
    '% jianpu-ly.py\n'
    'title=Sample\n'
    '1=C\n'
    '4/4\n'
    '4=120\n'
    '\n'
    '1 2 3 4 | 5 0 6 - |\n'
    'L: do re mi fa so _ la\n'
)
HEADER = '# protected header\n#__jianpu_meta__: {"voice_groups":[[0]]}\n\n'


class _Pusher:
    """Stand-in for EventPusher: apply_doc never pushes, and load() only would
    on failure paths these tests do not take."""

    def push(self, *args, **kwargs):
        pass


class _Whitelist:
    """Stand-in for FileWhitelist: only load() touches it, to allow a source image."""

    def allow(self, path):
        pass


def _new_service() -> EditorService:
    # cast because these are duck-typed stubs, not subclasses -- the service
    # only ever calls .push()/.allow() on them.
    return EditorService(cast(EventPusher, _Pusher()), cast(FileWhitelist, _Whitelist()))


def _service(tmp_path):
    path = tmp_path / 'sample.jianpu.txt'
    path.write_text(HEADER + BODY, encoding='utf-8')
    service = _new_service()
    loaded = service.load(str(path))
    assert loaded['ok'], loaded
    return service, path, loaded['body']


def _bridge_trip(value):
    """Everything handed to the front-end goes through JSON and back."""
    return json.loads(json.dumps(value))


def test_graphical_data_now_also_returns_the_model(tmp_path):
    service, _path, body = _service(tmp_path)
    data = service.graphical_render_data(body)
    assert data['ok']
    assert 'renders' in data, 'the drawing projection must still be there'
    doc = data['doc']
    assert doc['title'] == 'Sample'
    assert doc['key_header'] == '1=C'
    assert doc['tempo'] == 120
    assert len(doc['sections']) == 1
    assert [n['symbol'] for n in doc['sections'][0]['measures'][0]] == ['1', '2', '3', '4']


def test_applying_an_unedited_model_is_a_no_op(tmp_path):
    service, _path, body = _service(tmp_path)
    doc = _bridge_trip(service.graphical_render_data(body)['doc'])
    applied = service.apply_doc(doc)
    assert applied['ok']
    # Same text as serializing the freshly parsed document: the trip through
    # JSON must not be observable in the output.
    from core.notation.jianpu import build_jianpu_ly_text_from_doc
    assert applied['text'] == build_jianpu_ly_text_from_doc(parse_jianpu_ly_text(body))


def test_an_edited_note_reaches_the_text_and_keeps_its_lyric(tmp_path):
    service, _path, body = _service(tmp_path)
    doc = _bridge_trip(service.graphical_render_data(body)['doc'])
    doc['sections'][0]['measures'][0][0]['symbol'] = '7'   # what pressing "7" does

    applied = service.apply_doc(doc)
    assert applied['ok']
    rebuilt = parse_jianpu_ly_text(applied['text'])
    note = rebuilt.sections[0].measures[0][0]
    assert note.symbol == '7'
    assert note.lyrics == {1: ('do', False)}, 'the syllable stays on the note it was anchored to'


def test_apply_doc_returns_renders_in_step_with_the_text(tmp_path):
    service, _path, body = _service(tmp_path)
    doc = _bridge_trip(service.graphical_render_data(body)['doc'])
    doc['sections'][0]['measures'][0][0]['symbol'] = '7'

    applied = service.apply_doc(doc)
    # Both projections come from the same document, so re-deriving the render
    # from the returned text must agree with the render that was returned.
    from core.notation.jianpu.render_json import jianpu_section_to_render_json
    rebuilt = parse_jianpu_ly_text(applied['text'])
    expected = [
        jianpu_section_to_render_json(s, rebuilt.key_header, rebuilt.tempo)
        for s in rebuilt.sections
    ]
    assert applied['renders'] == expected


def test_apply_doc_does_not_touch_the_file(tmp_path):
    # Serializing is not saving: the protected '#' header and the on-disk
    # content must be untouched until the front-end actually calls save().
    service, path, body = _service(tmp_path)
    doc = _bridge_trip(service.graphical_render_data(body)['doc'])
    doc['sections'][0]['measures'][0][0]['symbol'] = '7'
    service.apply_doc(doc)
    assert path.read_text(encoding='utf-8') == HEADER + BODY


def test_saving_after_an_edit_keeps_the_protected_header(tmp_path):
    service, path, body = _service(tmp_path)
    doc = _bridge_trip(service.graphical_render_data(body)['doc'])
    doc['sections'][0]['measures'][0][0]['symbol'] = '7'
    applied = service.apply_doc(doc)

    assert service.save(applied['text'])['ok']
    on_disk = path.read_text(encoding='utf-8')
    assert on_disk.startswith(HEADER), 'the # header (and its meta line) must survive'
    assert '#__jianpu_meta__' in on_disk


def test_apply_doc_without_a_loaded_file_is_refused(tmp_path):
    service = _new_service()
    assert service.apply_doc({'sections': []}) == {'ok': False, 'error': 'no_file'}


def test_apply_doc_rejects_an_empty_model_rather_than_writing_nothing(tmp_path):
    service, _path, _body = _service(tmp_path)
    assert service.apply_doc({'sections': []})['error'] == 'empty'


def test_apply_doc_reports_failure_instead_of_raising(tmp_path):
    service, _path, _body = _service(tmp_path)
    result = service.apply_doc('not a document')
    assert result['ok'] is False and 'error' in result


# ── fragment_text: the clipboard mirror (阶段5.5b) ───────────────────────────


def _fragment(measures, time_sig='4/4'):
    """A JianpuDoc-shaped dict holding just the copied measures."""
    return {
        'title': '', 'composer': '', 'key_header': '1=C', 'tempo': 0,
        'sections': [{'time_sig': time_sig, 'measures': measures}],
    }


def test_fragment_text_has_no_header_block(tmp_path):
    service, _path, body = _service(tmp_path)
    measures = _bridge_trip(service.graphical_render_data(body)['doc'])['sections'][0]['measures']
    out = service.fragment_text(_fragment(measures[:1]))
    assert out['ok'], out
    # A fragment is not a document: no title/key/time signature may ride along,
    # or pasting it somewhere else would carry a bogus header with it.
    assert 'title=' not in out['text']
    assert '1=C' not in out['text']
    assert '4/4' not in out['text']
    # The syllables ride along with the notes they are attached to — copying a
    # phrase and losing its words would not be much of a copy.
    assert out['text'] == '1 2 3 4 |' + chr(10) + 'L: do re mi fa'


def test_fragment_text_serializes_several_measures_on_one_line(tmp_path):
    service, _path, body = _service(tmp_path)
    measures = _bridge_trip(service.graphical_render_data(body)['doc'])['sections'][0]['measures']
    out = service.fragment_text(_fragment(measures))
    assert out['ok'], out
    assert out['text'] == '1 2 3 4 | 5 0 6 - |' + chr(10) + 'L: do re mi fa so _ la'


def test_fragment_text_uses_the_real_note_tokeniser_for_dash_expansion(tmp_path):
    service, _path, _body = _service(tmp_path)
    # A four-beat rest is written `0 - - -`, not as one big-duration token —
    # the whole reason this runs in Python instead of being rebuilt in JS.
    whole_rest = [
        {'symbol': '0', 'accidental': '', 'upper_dots': 0, 'lower_dots': 0,
         'duration': 4.0, 'duration_dots': 0, 'midi': None, 'is_rest': True, 'lyrics': {}},
    ]
    out = service.fragment_text(_fragment([whole_rest]))
    assert out['ok'], out
    assert out['text'] == '0 - - - |'


def test_fragment_text_carries_lyrics_attached_to_the_copied_notes(tmp_path):
    service, _path, body = _service(tmp_path)
    measures = _bridge_trip(service.graphical_render_data(body)['doc'])['sections'][0]['measures']
    out = service.fragment_text(_fragment(measures[:1]))
    assert out['ok'], out
    assert 'L: do re mi fa' in out['text']


def test_fragment_text_needs_no_loaded_file(tmp_path):
    # Unlike apply_doc, serializing a fragment touches no document state, so a
    # copy must work even on a service that has not loaded anything.
    service = _new_service()
    note = {'symbol': '1', 'accidental': '', 'upper_dots': 0, 'lower_dots': 0,
            'duration': 1.0, 'duration_dots': 0, 'midi': 60, 'is_rest': False, 'lyrics': {}}
    out = service.fragment_text(_fragment([[note]]))
    assert out['ok'], out
    assert out['text'] == '1 |'


def test_fragment_text_reports_an_empty_or_malformed_fragment(tmp_path):
    service = _new_service()
    assert service.fragment_text({'sections': []})['ok'] is False
    assert service.fragment_text(None)['ok'] is False
