# tests/test_jianpu_dynamics.py — dynamics in the editor model, stage 6.1a.
#
# The accept/reject rules below were set by rendering each placement through
# the real jianpu-ly and the bundled LilyPond 2.24.4 and comparing the pages
# pixel for pixel -- not by reading LilyPond's warnings. That distinction cost
# a wrong rule once: "AbsoluteDynamicEvent 缺少附属对象" was taken to mean a
# mark first in a measure is dropped, and such marks were rejected. The pages
# show the mark attaches to the next note instead (``| \p 5`` renders exactly
# as ``| 5 \p``). LilyPond exits 0 and makes a PDF either way, so only the
# render tells which warnings mean a loss.
import pytest

from core.config import JianpuNote
from core.notation.jianpu import build_jianpu_ly_text_from_doc
from core.notation.jianpu.doc_json import jianpu_doc_from_dict, jianpu_doc_to_dict
from core.notation.jianpu.parser import JianpuParseError, parse_jianpu_ly_text
from core.notation.jianpu.primitives import DYNAMIC_MARKS, clone_jianpu_note, jianpu_note_token

BS = '\\'
HEAD = '% jianpu-ly.py\ntitle=T\n1=C\n4/4\n4=120\n\n'


def _doc(body: str):
    return parse_jianpu_ly_text(HEAD + body + '\n')


def _notes(doc):
    return [n for m in doc.sections[0].measures for n in m]


def _note(symbol='1', duration=1.0, dynamic=''):
    return JianpuNote(symbol=symbol, accidental='', upper_dots=0, lower_dots=0,
                      duration=duration, duration_dots=0, midi=60, is_rest=symbol == '0',
                      dynamic=dynamic)


# ── the whitelist ────────────────────────────────────────────────────────────

def test_whitelist_is_exactly_lilypond_absolute_dynamics():
    # Copied from package-assets/lilypond-runtime/.../dynamic-scripts-init.ly
    # (LilyPond 2.24.4). If LilyPond is upgraded, re-read that file.
    assert DYNAMIC_MARKS == frozenset(
        'ppppp pppp ppp pp p mp mf f ff fff ffff fffff '
        'fp sf sfp sff sfz fz sp spp rfz n'.split())


def test_hairpins_are_not_dynamic_marks():
    # Spanner start/stop events, kept in their own fields (see the hairpin
    # tests at the bottom), not in the dynamic-mark whitelist.
    assert not ({'<', '>', '!'} & DYNAMIC_MARKS)


def test_the_real_time_linter_knows_exactly_the_same_marks():
    # The parser and webui/static/js/jianpu-lint.js each hold a hand-written
    # copy of this list, and they cannot import each other. A hand-written list
    # drifting from the truth is precisely how 12 legal marks came to be
    # underlined as errors in the text view (the linter had 10). This reads the
    # JS source so the two copies cannot quietly disagree again.
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parent.parent
           / 'webui' / 'static' / 'js' / 'jianpu-lint.js').read_text(encoding='utf-8')
    block = re.search(r'const DYNAMIC_MARKS = new Set\(\[(.*?)\]\);', src, re.S)
    assert block, 'DYNAMIC_MARKS not found in jianpu-lint.js -- renamed? update this test'
    js_marks = set(re.findall(r"'([^']+)'", block.group(1)))
    assert js_marks == DYNAMIC_MARKS, (
        f'only in the linter: {sorted(js_marks - DYNAMIC_MARKS)}; '
        f'only in the parser: {sorted(DYNAMIC_MARKS - js_marks)}')


def test_every_lint_warning_toast_renders_in_both_languages():
    # The editor picks a toast per warning code from WARNING_TOASTS. A key
    # missing from the catalog would show the raw key; a placeholder the
    # front-end does not fill would show "{token}". The browser harness serves
    # no strings table, so this is where the wording is actually pinned. Keys
    # are read from editor.js so a newly registered code cannot slip past.
    import pathlib
    import re

    from webui.i18n import merged_catalog

    src = (pathlib.Path(__file__).resolve().parent.parent
           / 'webui' / 'static' / 'js' / 'editor.js').read_text(encoding='utf-8')
    block = re.search(r'const WARNING_TOASTS = \{(.*?)\n\};', src, re.S)
    assert block, 'WARNING_TOASTS not found in editor.js -- renamed? update this test'
    pairs = re.findall(r"one: '([^']+)', many: '([^']+)'", block.group(1))
    assert len(pairs) >= 4, pairs

    catalog = merged_catalog()
    # the params edShowLintToasts passes: the diagnostic's own params + line / n
    one_params = {'line': 7, 'token': chr(92) + 'p', 'got': '3', 'expected': '4'}
    for one_key, many_key in pairs:
        for key, params in ((one_key, one_params), (many_key, {'n': 2})):
            assert key in catalog, f'{key} missing from the catalog'
            for lang in ('zh', 'en'):
                out = catalog[key][lang]
                for name, value in params.items():
                    out = out.replace('{' + name + '}', str(value))
                assert '{' not in out, f'{key} [{lang}] left a placeholder: {out}'


def test_the_linter_names_only_registered_warning_keys():
    # jianpu-lint.js attaches a messageKey to each mark warning; it has to be one
    # editor.js actually knows how to show.
    import pathlib
    import re

    js = pathlib.Path(__file__).resolve().parent.parent / 'webui' / 'static' / 'js'
    lint_src = (js / 'jianpu-lint.js').read_text(encoding='utf-8')
    lint_keys = set(re.findall(r"'(w\.ed\.lint\.(?:mark|dynamic|hairpin)_[a-z_]+)'", lint_src))
    toast_match = re.search(r'const WARNING_TOASTS = \{(.*?)\n\};',
                            (js / 'editor.js').read_text(encoding='utf-8'), re.S)
    assert toast_match is not None, 'editor.js no longer has a WARNING_TOASTS table'
    toast_block = toast_match.group(1)
    assert lint_keys == {
        'w.ed.lint.mark_no_note_at', 'w.ed.lint.dynamic_twice_at', 'w.ed.lint.hairpin_twice_at',
        'w.ed.lint.hairpin_end_twice_at', 'w.ed.lint.hairpin_unterminated_at',
    }
    for key in lint_keys:
        assert f"'{key}'" in toast_block, f'{key} is produced by the linter but has no toast'
    # the rejected-by-mistake warning must not come back
    assert 'dynamic_no_note' not in lint_src and 'dynamic_no_note' not in toast_block


# ── parsing: accepted placements ─────────────────────────────────────────────

class TestAccepted:
    def test_attaches_to_the_note_written_just_before_it(self):
        notes = _notes(_doc(f'1 {BS}p 2 3 4 | 5 6 7 1 |'))
        assert [n.dynamic for n in notes] == ['p', '', '', '', '', '', '', '']

    def test_on_a_rest(self):
        notes = _notes(_doc(f'0 {BS}mf 2 3 4 | 5 6 7 1 |'))
        assert notes[0].is_rest and notes[0].dynamic == 'mf'

    def test_on_a_continuation_dash(self):
        # The dash is its own model object; the mark stays on it so the text
        # comes back exactly as written.
        notes = _notes(_doc(f'1 - {BS}f 4 | 5 6 7 1 |'))
        assert notes[1].symbol == '-' and notes[1].dynamic == 'f'
        assert notes[0].dynamic == ''

    def test_last_note_before_a_barline(self):
        notes = _notes(_doc(f'1 2 3 4 {BS}ff | 5 6 7 1 |'))
        assert notes[3].dynamic == 'ff'

    def test_on_prefixed_dotted_octave_and_accidental_notes(self):
        notes = _notes(_doc(f"q1 {BS}mp q2 3. q4 1' {BS}sfz | #4, {BS}pp 6 7 1 |"))
        assert (notes[0].dynamic, notes[4].dynamic, notes[5].dynamic) == ('mp', 'sfz', 'pp')

    def test_every_whitelisted_mark_parses(self):
        body = ' | '.join(f'1 {BS}{m} 1 1 1' for m in sorted(DYNAMIC_MARKS)) + ' |'
        doc = _doc(body)
        found = {m[0].dynamic for m in doc.sections[0].measures}
        assert found == DYNAMIC_MARKS

    def test_a_dynamic_is_not_a_lyric_slot(self):
        # Lyrics align positionally over notes; a dynamic between two notes must
        # not eat a syllable.
        doc = _doc(f'1 {BS}p 2 3 4 |\nL: a b c d')
        assert [n.lyrics[1][0] for n in _notes(doc)] == ['a', 'b', 'c', 'd']

    def test_a_dynamic_does_not_change_pitch(self):
        with_dyn = [n.midi for n in _notes(_doc(f'1 {BS}p 2 3 4 |'))]
        without = [n.midi for n in _notes(_doc('1 2 3 4 |'))]
        assert with_dyn == without


# ── parsing: a mark with no note before it ───────────────────────────────────

class TestAttachesToTheNextNote:
    # Rendered: each of these is pixel-identical to the mark written after the
    # note it lands on. LilyPond warns "缺少附属对象" for all of them regardless.

    def test_first_thing_in_a_measure_goes_on_that_measures_first_note(self):
        notes = _notes(_doc(f'1 2 3 4 | {BS}p 5 6 7 1 |'))
        assert [n.dynamic for n in notes] == ['', '', '', '', 'p', '', '', '']

    def test_not_onto_the_previous_measures_last_note(self):
        notes = _notes(_doc(f'1 2 3 4 | {BS}p 5 6 7 1 |'))
        assert notes[3].dynamic == '' and notes[4].dynamic == 'p'

    def test_first_thing_in_the_piece_goes_on_the_first_note(self):
        assert _notes(_doc(f'{BS}p 1 2 3 4 |'))[0].dynamic == 'p'

    def test_an_empty_measure_passes_it_on_to_the_next_measure(self):
        doc = _doc(f'1 2 3 4 | {BS}p | 5 6 7 1 |')
        measures = doc.sections[0].measures
        assert measures[1] == [] and measures[2][0].dynamic == 'p'

    def test_it_is_written_back_after_that_note(self):
        # A normalisation, safe because the two spellings render identically.
        out = build_jianpu_ly_text_from_doc(_doc(f'1 2 3 4 | {BS}p 5 6 7 1 |'))
        assert f'| 5 {BS}p 6 7 1 |' in out

    def test_several_waiting_marks_all_go_on_that_note(self):
        note = _notes(_doc(f'1 2 3 4 | {BS}! {BS}p {BS}< 5 6 7 1 {BS}! |'))[4]
        assert (note.hairpin_end, note.dynamic, note.hairpin_start) == (True, 'p', '<')


# ── parsing: rejected placements ─────────────────────────────────────────────

class TestRejected:
    def test_a_mark_after_the_last_note_of_the_piece(self):
        # Rendered: the page matches one without the mark, and LilyPond adds a
        # programming error ("no broken bound"). This one really is lost.
        with pytest.raises(JianpuParseError, match='后面没有音符'):
            _doc(f'1 2 3 4 | 5 6 7 1 | {BS}p')

    def test_a_mark_after_the_last_note_of_a_section(self):
        with pytest.raises(JianpuParseError, match='后面没有音符'):
            _doc(f'1 2 3 4 | {BS}f\nNextPart\n4/4\n5 6 7 1 |')

    def test_a_mark_before_a_bare_time_signature_change(self):
        # A signature line starts a section on its own, so this reaches the
        # timesig branch's check, not NextPart's -- both call sites are needed.
        with pytest.raises(JianpuParseError, match='后面没有音符'):
            _doc(f'1 2 3 4 | {BS}f\n3/4\n5 6 7 |')

    def test_a_mark_before_a_NextPart_that_has_no_signature_line(self):
        # Malformed (the notes after it have no signature), but the mark is what
        # the reader hits first, so it must be the error that comes out.
        with pytest.raises(JianpuParseError, match='后面没有音符'):
            _doc(f'1 2 3 4 | {BS}f\nNextPart\n5 6 7 1 |')

    def test_two_on_one_note(self):
        # Rendered: identical to the first mark alone, so the later is discarded.
        with pytest.raises(JianpuParseError, match='已经有力度记号'):
            _doc(f'1 {BS}p {BS}f 2 3 4 |')

    def test_two_on_one_note_when_one_was_waiting_for_it(self):
        with pytest.raises(JianpuParseError, match='已经有力度记号'):
            _doc(f'1 2 3 4 | {BS}p 5 {BS}f 6 7 1 |')

    def test_unknown_backslash_word(self):
        with pytest.raises(JianpuParseError, match='不认识的反斜杠记号'):
            _doc(f'1 {BS}foo 2 3 4 |')

    def test_wrong_case_gets_a_pointed_message(self):
        with pytest.raises(JianpuParseError, match='区分大小写') as exc:
            _doc(f'1 {BS}PP 2 3 4 |')
        assert f'{BS}pp' in str(exc.value)

    def test_error_points_at_the_offending_token(self):
        with pytest.raises(JianpuParseError) as exc:
            _doc(f'1 2 3 4 |\n5 {BS}foo 7 1 |')
        # HEAD is 6 lines; the body's second line is line 8, the token column 3.
        assert (exc.value.line, exc.value.col, exc.value.token) == (8, 3, f'{BS}foo')


# ── serialization ────────────────────────────────────────────────────────────

class TestSerialize:
    @pytest.mark.parametrize('body', [
        f'1 {BS}p 2 3 4 | 5 6 7 1 |',
        f'0 {BS}mf 2 3 4 | 5 6 7 1 {BS}ff |',
        f'1 - {BS}f 4 | 5 6 7 1 |',
        f"q1 {BS}mp q2 3. q4 1' {BS}sfz | #4, {BS}pp 6 7 1 |",
    ])
    def test_round_trip_is_byte_identical(self, body):
        text = HEAD + body + '\n'
        out = build_jianpu_ly_text_from_doc(parse_jianpu_ly_text(text))
        assert body in out

    def test_round_trip_is_stable(self):
        once = build_jianpu_ly_text_from_doc(_doc(f'1 {BS}p 2 3 4 | 5 6 7 {BS}f 1 |'))
        twice = build_jianpu_ly_text_from_doc(parse_jianpu_ly_text(once))
        assert once == twice

    def test_on_a_multi_beat_note_it_goes_on_the_attack(self):
        # A single 4-beat object (as extraction builds) expands to "1 - - -".
        # After the last dash the mark would land three beats late.
        assert jianpu_note_token(_note(duration=4.0, dynamic='f')) == f'1 {BS}f - - -'

    def test_on_a_short_note(self):
        assert jianpu_note_token(_note(duration=1.0, dynamic='p')) == f'1 {BS}p'

    def test_no_dynamic_leaves_the_token_unchanged(self):
        assert jianpu_note_token(_note(duration=2.0)) == '1 -'

    def test_a_value_outside_the_whitelist_is_not_written(self):
        # Writing it would make LilyPond reject the whole file.
        assert jianpu_note_token(_note(dynamic='foo')) == '1'


# ── splitting a note ─────────────────────────────────────────────────────────

def test_only_the_first_fragment_of_a_split_note_keeps_the_dynamic():
    src = _note(duration=4.0, dynamic='f')
    first = clone_jianpu_note(src, 2.0, is_first_fragment=True)
    rest = clone_jianpu_note(src, 2.0, is_first_fragment=False)
    assert (first.dynamic, rest.dynamic) == ('f', '')


# ── the bridge ───────────────────────────────────────────────────────────────

class TestBridge:
    def test_survives_the_round_trip_through_the_front_end(self):
        # jianpu_doc_to_dict (asdict) already carried it out; this pins the
        # inbound half, without which every dynamic would vanish on the first
        # graphical edit.
        doc = _doc(f'1 {BS}p 2 3 4 | 5 6 7 {BS}sfz 1 |')
        back = jianpu_doc_from_dict(jianpu_doc_to_dict(doc))
        assert [n.dynamic for n in _notes(back)] == [n.dynamic for n in _notes(doc)]

    def test_a_value_that_is_not_a_dynamic_is_dropped_at_the_boundary(self):
        raw = jianpu_doc_to_dict(_doc('1 2 3 4 |'))
        notes = raw['sections'][0]['measures'][0]
        notes[0]['dynamic'] = 'foo'
        notes[1]['dynamic'] = 42
        notes[2]['dynamic'] = 'PP'
        back = jianpu_doc_from_dict(raw)
        assert [n.dynamic for n in _notes(back)] == ['', '', '', '']

    def test_a_file_with_dynamics_is_not_reported_as_lossy(self):
        # Before 6.1a such a file did not parse at all. Now that it does, the
        # "a graphical edit would change this file" warning must stay silent
        # for it — otherwise every open would nag about a loss that is not
        # there. (That check compares comments and layout, not note content,
        # so dynamics being *kept* is pinned by the bridge test above.)
        from webui.editor import _writeback_losses
        body = build_jianpu_ly_text_from_doc(_doc(f'1 {BS}p 2 3 4 | 5 6 7 {BS}f 1 |'))
        assert _writeback_losses(body, parse_jianpu_ly_text(body)) == {}

    def test_a_note_without_the_key_reads_as_no_dynamic(self):
        # What the editor's own new-note constructors send.
        raw = jianpu_doc_to_dict(_doc('1 2 3 4 |'))
        for note in raw['sections'][0]['measures'][0]:
            note.pop('dynamic', None)
        assert all(n.dynamic == '' for n in _notes(jianpu_doc_from_dict(raw)))


# ══ hairpins ═════════════════════════════════════════════════════════════════
# Same method as the marks above: every rule below was first run through the
# real jianpu-ly and LilyPond. What those runs established:
#   * starts and ends attach to the note written just before, like a dynamic;
#   * with no note before them in their measure they attach to the NEXT note --
#     a start, and an end too (``| \! 5`` renders exactly as ``| 5 \!``);
#   * two starts on one note: the page matches the first alone, the later dropped;
#   * a \! ends only a hairpin begun on an EARLIER note, as does a dynamic;
#   * unfinished: "crescendo 缺少结尾" -- the wedge is not drawn, and a dynamic on
#     its start note disappears with it; dynamics on other notes are unaffected;
#   * the order of end / dynamic / start on one note changes neither the page
#     (pixel-identical) nor the MIDI (byte-identical).
LT, GT, END = BS + '<', BS + '>', BS + '!'


def _marks(doc):
    return [(n.hairpin_end, n.dynamic, n.hairpin_start) for n in _notes(doc)]


class TestHairpinAccepted:
    def test_start_and_end_attach_to_the_notes_before_them(self):
        notes = _notes(_doc(f'1 {LT} 2 3 4 {END} | 5 6 7 1 |'))
        assert notes[0].hairpin_start == '<' and not notes[0].hairpin_end
        assert notes[3].hairpin_end and notes[3].hairpin_start == ''
        assert all(not n.hairpin_start and not n.hairpin_end for n in notes[4:])

    def test_decrescendo(self):
        assert _notes(_doc(f'1 {GT} 2 3 4 {END} |'))[0].hairpin_start == '>'

    def test_across_a_barline(self):
        notes = _notes(_doc(f'1 {LT} 2 3 4 | 5 6 7 1 {END} |'))
        assert (notes[0].hairpin_start, notes[7].hairpin_end) == ('<', True)

    def test_on_a_rest_and_on_a_dash(self):
        notes = _notes(_doc(f'0 {LT} 2 - {END} 4 |'))
        assert notes[0].is_rest and notes[0].hairpin_start == '<'
        assert notes[2].symbol == '-' and notes[2].hairpin_end

    def test_an_unfinished_hairpin_is_accepted(self):
        # LilyPond drops it, but the model can hold it and it is the normal state
        # of a line being typed; the linter is what warns.
        notes = _notes(_doc(f'1 {LT} 2 3 4 | 5 6 7 1 |'))
        assert notes[0].hairpin_start == '<'
        assert not any(n.hairpin_end for n in notes)

    def test_a_stray_end_is_accepted(self):
        # LilyPond ignores it without a word.
        assert _notes(_doc(f'1 2 3 4 {END} |'))[3].hairpin_end

    def test_end_dynamic_and_start_on_one_note_in_any_order_read_the_same(self):
        orders = [f'{END} {BS}f {GT}', f'{GT} {BS}f {END}', f'{BS}f {END} {GT}', f'{GT} {END} {BS}f']
        results = {tuple(_marks(_doc(f'1 {LT} 2 {o} 3 4 {END} |'))) for o in orders}
        assert len(results) == 1
        (only,) = results
        assert only[1] == (True, 'f', '>')

    def test_hairpins_take_no_lyric_syllable_and_change_no_pitch(self):
        doc = _doc(f'1 {LT} 2 3 4 {END} |\nL: a b c d')
        assert [n.lyrics[1][0] for n in _notes(doc)] == ['a', 'b', 'c', 'd']
        assert [n.midi for n in _notes(doc)] == [n.midi for n in _notes(_doc('1 2 3 4 |'))]


class TestHairpinWaitingForANote:
    def test_start_first_in_a_measure(self):
        assert _notes(_doc(f'1 2 3 4 | {LT} 5 6 7 1 {END} |'))[4].hairpin_start == '<'

    def test_start_first_in_the_piece(self):
        assert _notes(_doc(f'{LT} 1 2 3 4 {END} |'))[0].hairpin_start == '<'

    def test_end_first_in_a_measure_goes_on_the_next_note(self):
        notes = _notes(_doc(f'1 {LT} 2 3 4 | {END} 5 6 7 1 |'))
        assert not notes[3].hairpin_end and notes[4].hairpin_end


class TestHairpinRejected:
    def test_a_start_after_the_last_note(self):
        # Rendered: no effect, plus LilyPond "程序错误：目标音量无效：nan".
        with pytest.raises(JianpuParseError, match='后面没有音符'):
            _doc(f'1 2 3 4 | 5 6 7 1 | {LT}')

    def test_two_starts_on_one_note(self):
        with pytest.raises(JianpuParseError, match='已经开始了'):
            _doc(f'1 {LT} {GT} 2 3 4 {END} |')

    def test_two_ends_on_one_note(self):
        # Harmless to LilyPond, but the model holds one end per note; better an
        # error than silently rewriting the text.
        with pytest.raises(JianpuParseError, match='已经有'):
            _doc(f'1 {LT} 2 3 4 {END} {END} |')

    def test_unknown_backslash_message_now_mentions_hairpins(self):
        with pytest.raises(JianpuParseError, match='渐强渐弱'):
            _doc(f'1 {BS}foo 2 3 4 |')


class TestHairpinSerialize:
    def test_canonical_text_round_trips_byte_for_byte(self):
        body = f'1 {BS}p {LT} 2 3 4 {END} {BS}f {GT} | 5 6 7 1 {END} |'
        assert body in build_jianpu_ly_text_from_doc(_doc(body))

    @pytest.mark.parametrize('written, canonical', [
        (f'1 {LT} {BS}p 2 3 4 {END} |', f'1 {BS}p {LT} 2 3 4 {END} |'),
        (f'1 {LT} 2 3 4 {BS}f {END} |', f'1 {LT} 2 3 4 {END} {BS}f |'),
        (f'1 {LT} 2 {GT} {BS}f {END} 3 4 {END} |', f'1 {LT} 2 {END} {BS}f {GT} 3 4 {END} |'),
    ])
    def test_other_orders_are_normalised_to_end_dynamic_start(self, written, canonical):
        # Safe only because reordering was shown to change neither page nor MIDI.
        assert canonical in build_jianpu_ly_text_from_doc(_doc(written))

    def test_marks_go_on_the_attack_of_a_multi_beat_note(self):
        note = _note(duration=4.0, dynamic='f')
        note.hairpin_start, note.hairpin_end = '<', True
        assert jianpu_note_token(note) == f'1 {END} {BS}f {LT} - - -'

    def test_a_hairpin_value_that_is_not_a_hairpin_is_not_written(self):
        note = _note()
        note.hairpin_start = 'x'
        assert jianpu_note_token(note) == '1'


def test_only_the_first_fragment_of_a_split_note_keeps_its_hairpin_marks():
    src = _note(duration=4.0)
    src.hairpin_start, src.hairpin_end = '>', True
    first = clone_jianpu_note(src, 2.0, is_first_fragment=True)
    rest = clone_jianpu_note(src, 2.0, is_first_fragment=False)
    assert (first.hairpin_start, first.hairpin_end) == ('>', True)
    assert (rest.hairpin_start, rest.hairpin_end) == ('', False)


class TestHairpinBridge:
    def test_survives_the_round_trip_through_the_front_end(self):
        doc = _doc(f'1 {LT} 2 3 4 {END} {GT} | 5 6 7 1 {END} |')
        assert _marks(jianpu_doc_from_dict(jianpu_doc_to_dict(doc))) == _marks(doc)

    def test_values_that_are_not_hairpins_are_dropped_at_the_boundary(self):
        raw = jianpu_doc_to_dict(_doc('1 2 3 4 |'))
        notes = raw['sections'][0]['measures'][0]
        notes[0]['hairpin_start'] = 'x'
        notes[1]['hairpin_start'] = '<<'
        notes[2]['hairpin_end'] = 'false'     # a string, not a bool
        notes[3]['hairpin_end'] = 1
        back = _notes(jianpu_doc_from_dict(raw))
        assert [(n.hairpin_start, n.hairpin_end) for n in back] == [('', False)] * 4

    def test_a_file_with_hairpins_is_not_reported_as_lossy(self):
        from webui.editor import _writeback_losses
        body = build_jianpu_ly_text_from_doc(_doc(f'1 {LT} 2 3 4 | 5 6 7 1 {END} {BS}f |'))
        assert _writeback_losses(body, parse_jianpu_ly_text(body)) == {}
