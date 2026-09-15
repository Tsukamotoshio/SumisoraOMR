# tests/test_volta_injection.py — coverage for drawing volta (1./2. ending)
# brackets into the generated .ly, stage 6.2a-2 in the fix-plan doc.
#
# These exercise the string transform directly on the shape jianpu-ly actually
# emits — bar markers of the form `| %{ bar N: %}`, where N is the 1-based
# number of the measure that STARTS after the marker — so they need neither
# LilyPond nor music21. Rendering with the real LilyPond was checked separately
# while building this (see the stage 6.2a-2 report in the plan doc).
from pathlib import Path

from core.render.jianpu_runner import (
    _first_voice_block_span,
    _inject_voltas_into_voice_block,
    _insert_repeat_bar_commands,
    _volta_label,
    inject_volta_brackets_to_ly,
)

OPEN_1 = "\\set Score.repeatCommands = #'((volta \"1.\"))"
CLOSE = "\\set Score.repeatCommands = #'((volta #f))"
SWAP_2 = "\\set Score.repeatCommands = #'((volta #f) (volta \"2.\"))"


def _block(measure_count: int, final_bar: bool = True) -> str:
    """A voice block of *measure_count* one-note measures, marked like jianpu-ly."""
    parts = ['c4']
    for n in range(2, measure_count + 1):
        parts.append(f'| %{{ bar {n}: %}} c4')
    body = ' '.join(parts)
    return body + (' | \\bar "|."' if final_bar else '')


def _measure_of(block: str, needle: str) -> int:
    """0-based index of the measure *needle* is written into."""
    head = block[:block.index(needle)]
    markers = [int(t.split(':')[0]) for t in head.split('%{ bar ')[1:]]
    return (markers[-1] - 1) if markers else 0


class TestVoltaPlacement:
    def test_bracket_opens_on_its_first_measure_and_closes_after_its_last(self):
        out = _inject_voltas_into_voice_block(_block(8), [{'number': '1', 'first': 2, 'last': 4}])
        assert out.count(OPEN_1) == 1
        assert out.count(CLOSE) == 1
        assert _measure_of(out, OPEN_1) == 2
        # closing command sits at the start of the measure AFTER the bracket
        assert _measure_of(out, CLOSE) == 5

    def test_adjacent_brackets_share_one_command_with_close_first(self):
        # `\set` is an assignment: two commands at the same moment and the
        # second overwrites the first, so bracket 1 would never close. The two
        # items have to travel in one list, close before open.
        brackets = [{'number': '1', 'first': 2, 'last': 3}, {'number': '2', 'first': 4, 'last': 4}]
        out = _inject_voltas_into_voice_block(_block(8), brackets)
        assert out.count(SWAP_2) == 1
        assert out.count('repeatCommands') == 3      # open 1, swap, close 2
        assert _measure_of(out, SWAP_2) == 4

    def test_bracket_on_the_first_measure_is_prepended(self):
        out = _inject_voltas_into_voice_block(_block(4), [{'number': '1', 'first': 0, 'last': 1}])
        assert out.startswith(OPEN_1)

    def test_bracket_on_the_last_measure_closes_before_the_final_barline(self):
        # No marker follows the last measure, so the close has nowhere to hang
        # except in front of the terminating `| \bar "|."` — this is the shape
        # 让我们荡起双桨's second ending has.
        out = _inject_voltas_into_voice_block(_block(6), [{'number': '2', 'first': 4, 'last': 5}])
        assert out.rstrip().endswith(CLOSE + ' | \\bar "|."')

    def test_no_brackets_leaves_the_block_byte_identical(self):
        block = _block(6)
        assert _inject_voltas_into_voice_block(block, []) == block

    def test_a_start_that_cannot_be_located_draws_nothing(self):
        # A bracket whose start is misplaced reads as deliberate; no bracket is
        # the honest failure.
        out = _inject_voltas_into_voice_block(_block(4), [{'number': '1', 'first': 9, 'last': 10}])
        assert 'repeatCommands' not in out

    def test_malformed_entries_are_skipped_not_raised(self):
        block = _block(6)
        out = _inject_voltas_into_voice_block(block, [
            {'number': '1'},                               # no range
            {'number': '1', 'first': 'x', 'last': 2},      # not a number
            {'number': '1', 'first': 4, 'last': 2},        # reversed
            {'number': '', 'first': 1, 'last': 2},         # nothing to print
        ])
        assert out == block

    def test_one_bad_entry_does_not_take_the_good_brackets_down_with_it(self):
        # Regression: entries used to be sorted before being validated, so a
        # string `first` in one entry raised TypeError inside sorted() — the
        # outer try swallowed it and EVERY bracket in the score vanished. The
        # meta line is a # comment a user can hand-edit, so this is reachable.
        out = _inject_voltas_into_voice_block(_block(8), [
            {'number': '1', 'first': 'x', 'last': 2},
            {'number': '1', 'first': 2, 'last': 4},
        ])
        assert out.count(OPEN_1) == 1
        assert out.count(CLOSE) == 1

    def test_markers_are_still_found_after_repeat_barlines_went_in(self):
        # The pipeline runs inject_repeat_barlines_to_ly first, which puts
        # `\bar ":|."` right in front of the `|` of a marker. The volta pass must
        # still see that marker.
        content = '\\new Voice { ' + _block(6) + ' }'
        with_repeats = _insert_repeat_bar_commands(content, {3: {'start': False, 'end': True}})
        assert '\\bar ":|."' in with_repeats
        span = _first_voice_block_span(with_repeats)
        assert span is not None
        block = with_repeats[span[0]:span[1]]
        out = _inject_voltas_into_voice_block(block, [{'number': '1', 'first': 2, 'last': 3}])
        assert out.count(OPEN_1) == 1 and out.count(CLOSE) == 1


class TestVoltaLabel:
    def test_plain_number_gets_a_dot(self):
        assert _volta_label('1') == '1.'

    def test_multi_pass_ending_keeps_its_comma(self):
        assert _volta_label('1, 2') == '1, 2.'

    def test_existing_dot_is_not_doubled(self):
        assert _volta_label('2.') == '2.'

    def test_characters_that_could_break_a_lilypond_string_are_dropped(self):
        assert _volta_label('1"') == '1.'
        assert _volta_label('x') == ''


class TestInjectFile:
    def test_empty_bracket_list_does_not_touch_the_file(self, tmp_path: Path):
        # The three repeat-only files in the corpus rely on this to stay
        # byte-identical: not "rewritten with the same bytes", untouched.
        ly = tmp_path / 'x.ly'
        ly.write_text('\\new Voice { c4 }', encoding='utf-8')
        before = ly.stat().st_mtime_ns
        inject_volta_brackets_to_ly(ly, [])
        assert ly.stat().st_mtime_ns == before

    def test_writes_brackets_into_the_first_voice_only(self, tmp_path: Path):
        ly = tmp_path / 'x.ly'
        first = '\\new Voice = "A" { ' + _block(6) + ' }'
        second = '\\new Voice = "B" { ' + _block(6) + ' }'
        ly.write_text(first + '\n' + second, encoding='utf-8')
        inject_volta_brackets_to_ly(ly, [{'number': '1', 'first': 2, 'last': 3}])
        text = ly.read_text(encoding='utf-8')
        a_end = text.index('\\new Voice = "B"')
        assert text[:a_end].count('repeatCommands') == 2
        assert text[a_end:].count('repeatCommands') == 0
