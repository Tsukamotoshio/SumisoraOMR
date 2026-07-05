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
