# core/notation/jianpu/parser.py — jianpu-ly text → JianpuDoc parser (阶段2).
"""Parses the jianpu-ly BODY text (i.e. what webui/editor.py:_split_header
leaves after stripping the leading ``#`` comment block) into a JianpuDoc —
修复计划2与简谱编辑器规划.md's B3 方案 C editor model.

Scope is deliberately the same 🟢 grammar tier documented in
docs/jianpu-ly-syntax.md — the subset this project's own pipeline actually
produces (verified against 48 real files). 🟡-tier syntax (valid jianpu-ly
the pipeline never emits — lyrics, ties, grace notes, dynamics, repeat
brackets, ...) and ⚪ explicitly-excluded constructs (``LP: ... :LP``,
``chords=`` and friends) are **not** parsed — a ``JianpuParseError`` is
raised instead of silently dropping them, per that doc's §3 rule ("解析器
必须报错，不得静默丢弃或吞入音符名"). Extending coverage to 🟡 syntax is
future work, not needed for the 48-file round-trip acceptance test this
stage is scoped to.

``JianpuNote.midi`` is computed after parsing (阶段3.1) via
``primitives.jianpu_note_to_midi`` — the reverse of ``note_to_jianpu``'s
key-tonic → semitone math, keyed off the document's own ``key_header``.
Dash-continuation notes (``symbol='-'``) and rests still get ``None``: a
dash's pitch is a function of the note it continues, not of the token
itself, so resolving it is the job of whatever merges consecutive dashes
into one sustained note (the JianpuDoc -> renderer-JSON conversion), not
this parser.
"""
from __future__ import annotations

import re

from ...config import JianpuDoc, JianpuNote, JianpuSection
from .primitives import jianpu_note_to_midi, key_header_tonic_semitone

_NOTE_RE = re.compile(r"^([qsd]?)([#b]?)([0-7])('+|,+)?(\.?)$")
_DASH_RE = re.compile(r"^([qsd]?)(-)(\.?)$")
_TIMESIG_RE = re.compile(r"^(\d+)/(\d+)(?:,(\S+))?$")
_KEY_RE = re.compile(r"^([16])=(\S+)$")
_TEMPO_RE = re.compile(r"^4=(\d+)$")
_WHOLE_LINE_VALUE_RE = re.compile(r'^(title=|composer=)')

_PREFIX_QL = {'': 1.0, 'q': 0.5, 's': 0.25, 'd': 0.125}


class JianpuParseError(ValueError):
    """A token outside this parser's 🟢-tier grammar coverage."""

    def __init__(self, message: str, line: int, col: int, token: str) -> None:
        super().__init__(f'{message}（第 {line} 行第 {col} 列：{token!r}）')
        self.line = line
        self.col = col
        self.token = token


class _Word:
    __slots__ = ('text', 'line', 'col')

    def __init__(self, text: str, line: int, col: int) -> None:
        self.text = text
        self.line = line
        self.col = col


def _tokenize(text: str) -> list[_Word]:
    """Whitespace/line-aware tokenizer — mirrors jianpu-lint.js's tokenize()
    (same grammar, same comment/whole-line-value handling) so the future
    editor's parser and its real-time linter never drift apart on what
    counts as a "word"."""
    words: list[_Word] = []
    for line_no, raw_line in enumerate(text.split('\n'), start=1):
        line = raw_line[:-1] if raw_line.endswith('\r') else raw_line
        trimmed = line.lstrip()
        if not trimmed:
            continue
        if trimmed.startswith('%'):
            continue
        if _WHOLE_LINE_VALUE_RE.match(trimmed):
            col = len(line) - len(trimmed) + 1
            words.append(_Word(trimmed, line_no, col))
            continue
        first_word = trimmed.split(None, 1)[0]
        if first_word in ('L:', 'H:'):
            # 歌词行属 🟡（docs §2）——本解析器不覆盖，整行报错定位到行首。
            col = len(line) - len(trimmed) + 1
            raise JianpuParseError('歌词行（L:/H:）不在阶段2解析器覆盖范围内', line_no, col, first_word)
        for m in re.finditer(r'\S+', line):
            words.append(_Word(m.group(0), line_no, m.start() + 1))
    return words


def _decode_note_or_dash(word: _Word) -> JianpuNote:
    m = _NOTE_RE.match(word.text)
    if m:
        prefix, accidental, digit, octave_marks, dot = m.groups()
        is_rest = digit == '0'
        return JianpuNote(
            symbol='0' if is_rest else digit,
            accidental='' if is_rest else accidental,
            upper_dots=octave_marks.count("'") if octave_marks else 0,
            lower_dots=octave_marks.count(',') if octave_marks else 0,
            duration=_PREFIX_QL[prefix] * (1.5 if dot else 1.0),
            duration_dots=1 if dot else 0,
            midi=None, is_rest=is_rest,
        )
    m = _DASH_RE.match(word.text)
    if m:
        prefix, _dash, dot = m.groups()
        return JianpuNote(
            symbol='-', accidental='', upper_dots=0, lower_dots=0,
            duration=_PREFIX_QL[prefix] * (1.5 if dot else 1.0),
            duration_dots=1 if dot else 0,
            midi=None, is_rest=False,
        )
    raise JianpuParseError('非法记号，不属于本解析器覆盖的 🟢 文法', word.line, word.col, word.text)


def parse_jianpu_ly_text(body: str) -> JianpuDoc:
    """Parse jianpu-ly body text into a JianpuDoc.

    Raises JianpuParseError on the first token outside the 🟢 grammar tier
    (see module docstring for exactly what that excludes).
    """
    words = _tokenize(body)
    title = ''
    composer = ''
    key_header = '1=C'
    tempo = 0
    sections: list[JianpuSection] = []
    current_section: JianpuSection | None = None
    current_measure: list[JianpuNote] = []

    def close_measure_on_bar() -> None:
        """`|` always ends a measure — even an empty one (`| |`) is a real,
        intentionally-empty measure, not nothing, so this always appends."""
        nonlocal current_measure
        if current_section is not None:
            current_section.measures.append(current_measure)
        current_measure = []

    def flush_pending_defensively() -> None:
        """Called at a section boundary (NextPart) or EOF, where a
        well-formed file has *nothing* left to flush (the last `|` already
        closed the measure) — only append if a malformed file left real
        content dangling without a closing `|`."""
        nonlocal current_measure
        if current_measure:
            close_measure_on_bar()

    for word in words:
        text = word.text
        if text == '|':
            close_measure_on_bar()
            continue
        if text == 'NextPart':
            flush_pending_defensively()
            current_section = None
            continue
        if text.startswith('title='):
            title = text[len('title='):]
            continue
        if text.startswith('composer='):
            composer = text[len('composer='):]
            continue
        m = _KEY_RE.match(text)
        if m:
            key_header = text
            continue
        m = _TEMPO_RE.match(text)
        if m:
            tempo = int(m.group(1))
            continue
        m = _TIMESIG_RE.match(text)
        if m:
            # 拍号总是开启一个新 section——真实文件里 NextPart 后一定紧跟一条
            # 拍号行（§1.6 更正），开局第一条拍号行同理开启第 0 个 section。
            flush_pending_defensively()
            current_section = JianpuSection(time_sig=text)
            sections.append(current_section)
            continue
        if current_section is None:
            # 拍号缺失（不应发生在真实文件里,但要给出可定位的错误而不是 None 解引用）
            raise JianpuParseError('小节内容出现在拍号声明之前', word.line, word.col, text)
        current_measure.append(_decode_note_or_dash(word))

    flush_pending_defensively()

    key_tonic_semitone = key_header_tonic_semitone(key_header)
    for section in sections:
        for measure in section.measures:
            for note in measure:
                note.midi = jianpu_note_to_midi(note, key_tonic_semitone)

    return JianpuDoc(
        title=title, composer=composer, key_header=key_header, tempo=tempo,
        sections=sections,
    )
