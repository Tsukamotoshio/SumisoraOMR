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
