# tests/test_primitives_edges.py — boundary unit tests for jianpu primitives.
# P1-3 / P1-4 修复的边界语义在此钉死，防止回退。
from core.notation.jianpu.extract import _canonical_offset
from core.notation.jianpu.primitives import normalize_jianpu_duration, split_duration_chunks


class TestSplitDurationChunks:
    def test_zero_returns_empty(self):
        # P1-3：不再凭空造 [0.125]
        assert split_duration_chunks(0) == []

    def test_negative_returns_empty(self):
        assert split_duration_chunks(-1) == []

    def test_below_tolerance_returns_empty(self):
        assert split_duration_chunks(0.005) == []

    def test_exact_quarter(self):
        assert split_duration_chunks(1.0) == [1.0]

    def test_greedy_split(self):
        assert split_duration_chunks(2.5) == [2.0, 0.5]

    def test_allowed_durations_map_to_themselves(self):
        for d in (4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.375, 0.25, 0.125):
            assert split_duration_chunks(d) == [d], d


class TestCanonicalOffset:
    def test_epsilon_variants_merge_to_first_seen(self):
        # P1-4：同一音乐位置的浮点误差变体合并为首见原始值
        seen: dict[int, float] = {}
        first = _canonical_offset(seen, 1 / 3)
        variant = _canonical_offset(seen, 0.33333333334)
        assert first == variant == 1 / 3

    def test_distinct_positions_stay_distinct(self):
        seen: dict[int, float] = {}
        assert _canonical_offset(seen, 0.0) == 0.0
        assert _canonical_offset(seen, 2 / 3) == 2 / 3

    def test_triplet_trio_keys_distinct(self):
        # 三连音 0, 1/3, 2/3 在 1/64 网格上必须保持三个独立身份
        assert len({round(x * 16) for x in (0.0, 1 / 3, 2 / 3)}) == 3

    def test_returns_original_not_grid_value(self):
        # 量化仅做身份判定：返回值必须是原始 offset（1/3 ≠ 5/16）
        seen: dict[int, float] = {}
        assert _canonical_offset(seen, 1 / 3) == 1 / 3
        assert _canonical_offset(seen, 1 / 3) != 5 / 16


class TestNormalizeJianpuDuration:
    def test_allowed_values_unchanged(self):
        for d in (4.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.25, 0.125):
            assert normalize_jianpu_duration(d) == d, d


# ── lyric alignment: the writer half (5.2.5 follow-up) ──────────────────────


def _note(symbol, duration=1.0, lyric=None):
    from core.config import JianpuNote
    n = JianpuNote(symbol, '', 0, 0, duration, 0, None, symbol == '0')
    if lyric:
        n.lyrics = {1: (lyric, False)}
    return n


def test_lyric_target_notes_skips_rests_and_dashes():
    from core.notation.jianpu.primitives import lyric_target_notes
    measures = [[_note('1'), _note('-'), _note('0'), _note('2')]]
    assert [n.symbol for n in lyric_target_notes(measures)] == ['1', '2']


def test_build_lyric_lines_emits_one_token_per_target():
    from core.notation.jianpu.primitives import build_lyric_lines
    # `1 - 2` is two syllable slots, not three: jianpu-ly ties the dash to the
    # note before it and \lyricsto gives a tied group a single syllable.
    measures = [[_note('1', lyric='aa'), _note('-'), _note('2', lyric='bb')]]
    assert build_lyric_lines(measures) == ['L: aa bb']


def test_build_lyric_lines_pads_gaps_but_not_dashes():
    from core.notation.jianpu.primitives import build_lyric_lines
    measures = [[_note('1', lyric='aa'), _note('-'), _note('2'), _note('3', lyric='cc')]]
    # One '_' for the note that genuinely has no syllable; none for the dash.
    assert build_lyric_lines(measures) == ['L: aa _ cc']


def test_lyrics_survive_a_parse_serialize_round_trip_across_a_dash():
    from core.notation.jianpu import build_jianpu_ly_text_from_doc
    from core.notation.jianpu.parser import parse_jianpu_ly_text
    body = 'title=T\n1=C\n4/4\n\n1 - 2 3 |\nL: aa bb cc\n'
    once = build_jianpu_ly_text_from_doc(parse_jianpu_ly_text(body))
    twice = build_jianpu_ly_text_from_doc(parse_jianpu_ly_text(once))
    assert 'L: aa bb cc' in once
    assert once == twice, 'reader and writer must agree, or a file drifts on every save'
