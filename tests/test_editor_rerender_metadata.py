# tests/test_editor_rerender_metadata.py — regression coverage for P1-4 in the
# fix-plan doc: the editor's own re-render path used to skip
# merge_polyphonic_jianpu_staves/inject_repeat_barlines_to_ly entirely, because
# voice_groups/repeat_barlines only exist as long as the MusicXML score is in
# hand, and the editor only has the saved .jianpu.txt. Fixed by persisting both
# into a #__jianpu_meta__ header line the editor reads back on re-render.
from core.render.renderer import _build_editor_header, parse_jianpu_meta_comment


class TestMetaCommentRoundTrip:
    def test_voice_groups_and_repeat_barlines_survive_round_trip(self):
        header = _build_editor_header(
            'Synthetic',
            [[0, 1, 2], [3]],
            {5: {'start': True, 'end': False}, 10: {'start': False, 'end': True}},
        )
        voice_groups, repeat_barlines = parse_jianpu_meta_comment(header)
        assert voice_groups == [[0, 1, 2], [3]]
        assert repeat_barlines == {5: {'start': True, 'end': False}, 10: {'start': False, 'end': True}}

    def test_repeat_barlines_keys_are_int_not_str(self):
        # JSON always stringifies dict keys; inject_repeat_barlines_to_ly indexes
        # this dict by an int loop variable, so a str-keyed dict would silently
        # match nothing and inject zero repeat marks.
        header = _build_editor_header('T', [[0]], {5: {'start': True, 'end': False}})
        _, repeat_barlines = parse_jianpu_meta_comment(header)
        assert all(isinstance(k, int) for k in repeat_barlines)

    def test_meta_line_is_hash_prefixed(self):
        # Must land inside the #-prefixed block webui/editor.py's _split_header
        # strips (and never shows the user) — a %-prefixed line (jianpu-ly's own
        # comment marker) would instead land in the editable body.
        header = _build_editor_header('T', [[0, 1]], {})
        meta_lines = [ln for ln in header.splitlines() if 'jianpu_meta' in ln]
        assert len(meta_lines) == 1
        assert meta_lines[0].startswith('#')

    def test_no_meta_line_when_both_empty(self):
        # The three degraded-fallback render paths (strict-timing / reportlab /
        # markup) have no real voice-group or repeat data to offer; asserting
        # nothing here keeps those files from carrying a misleading empty stub.
        header = _build_editor_header('T', [], {})
        assert 'jianpu_meta' not in header


class TestMetaCommentDefensiveFallback:
    """A missing/malformed meta line must degrade to "no groups, no repeats"
    (the pre-P1-4 behaviour) rather than raise — covers hand-edited files,
    files predating this feature, and truncated copies."""

    def test_missing_meta_line(self):
        voice_groups, repeat_barlines = parse_jianpu_meta_comment('# just a comment\n\n')
        assert voice_groups == []
        assert repeat_barlines == {}

    def test_malformed_json(self):
        voice_groups, repeat_barlines = parse_jianpu_meta_comment(
            '#__jianpu_meta__: {this is not valid json\n'
        )
        assert voice_groups == []
        assert repeat_barlines == {}

    def test_empty_header(self):
        voice_groups, repeat_barlines = parse_jianpu_meta_comment('')
        assert voice_groups == []
        assert repeat_barlines == {}
