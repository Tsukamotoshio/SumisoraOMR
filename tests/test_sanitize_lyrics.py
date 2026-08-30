# tests/test_sanitize_lyrics.py — the score rebuild in
# renderer.sanitize_generated_lilypond_file must carry jianpu-ly's lyric
# contexts along.
#
# jianpu-ly emits `\new Lyrics { \lyricsto ... }` AFTER the last
# `% === END JIANPU STAFF ===` marker and before the `>>` closing the score's
# simultaneous block. The rebuild used to keep only the region between the
# BEGIN and END markers, so every lyric context was dropped and no rendered PDF
# ever showed a syllable — measured on the three lyric-carrying files in
# editor-workspace/: 1, 2 and 1 contexts, all of them outside the kept span.
from core.render.renderer import _extract_lyric_contexts, sanitize_generated_lilypond_file

# Shaped like the real jianpu-ly output, trimmed to what the rebuild looks at.
LY = '''\\version "2.20.0"
\\paper { }

\\score {
<< \\override Score.BarNumber.break-visibility = #center-visible

%% === BEGIN JIANPU STAFF ===
    \\new RhythmicStaff { \\new Voice="W" { \\note-mod "1" c4 ~ \\note-mod "2" d4 } }
% === END JIANPU STAFF ===

\\new Lyrics = "IX" { \\lyricsto "W" { aa bb } }
>>
\\header{
title="X"
}
\\layout{ } }
\\score {
% === BEGIN MIDI STAFF ===
    \\new Staff { \\new Voice="Y" { c4 d4 } }
}
'''


def test_extract_lyric_contexts_finds_the_block_after_the_end_marker():
    after = LY.index('% === END JIANPU STAFF ===') + len('% === END JIANPU STAFF ===')
    got = _extract_lyric_contexts(LY, after)
    assert '\\new Lyrics' in got
    assert '\\lyricsto "W"' in got
    assert 'aa bb' in got
    assert '\\header' not in got, 'must stop at the score-closing >>'
    assert 'MIDI STAFF' not in got


def test_extract_lyric_contexts_is_silent_when_there_are_none():
    plain = LY.replace('\\new Lyrics = "IX" { \\lyricsto "W" { aa bb } }\n', '')
    after = plain.index('% === END JIANPU STAFF ===') + len('% === END JIANPU STAFF ===')
    assert _extract_lyric_contexts(plain, after) == ''


def test_extract_lyric_contexts_refuses_when_the_closing_marker_is_missing():
    # Without the `>>` there is no way to tell lyrics from the trailing header
    # and MIDI score; carrying those into the rebuilt block would break the
    # file, so returning nothing is the safer failure.
    truncated = LY.replace('\n>>', '')
    after = truncated.index('% === END JIANPU STAFF ===') + len('% === END JIANPU STAFF ===')
    assert _extract_lyric_contexts(truncated, after) == ''


def test_sanitize_keeps_the_lyric_context_in_the_rebuilt_score(tmp_path):
    p = tmp_path / 'x.ly'
    p.write_text(LY, encoding='utf-8')
    sanitize_generated_lilypond_file(p, 'X')
    out = p.read_text(encoding='utf-8')
    assert out.count('\\new Lyrics') == 1, 'the lyric context must survive the rebuild'
    assert '\\lyricsto "W"' in out
    # And it has to land inside the score's << >>, not after the closing brace.
    body = out.split('\\score {', 1)[1]
    assert body.index('\\new Lyrics') < body.index('>>')


def test_sanitize_still_drops_the_separate_midi_score(tmp_path):
    # The rebuild deliberately keeps only the jianpu staff; carrying lyrics
    # along must not accidentally drag the MIDI score back in with them.
    p = tmp_path / 'x.ly'
    p.write_text(LY, encoding='utf-8')
    sanitize_generated_lilypond_file(p, 'X')
    assert 'MIDI STAFF' not in p.read_text(encoding='utf-8')
