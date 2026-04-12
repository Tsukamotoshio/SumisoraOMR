# core/jianpu_txt_editor.py — 简谱 txt 格式编辑器基础模块
# 提供 .jianpu.txt 格式的解析、序列化与基础编辑操作，
# 数据结构设计与后续 GUI 编辑器兼容。
"""
.jianpu.txt 文件格式规范
=========================

文件由两个区块组成：[meta] 和 [score]，以空行分隔。
注释以 # 开头，可出现在任意位置。

[meta] 区块（键值对，冒号分隔）
---------------------------------
  title:      歌曲标题（可为空）
  composer:   作曲者（可为空）
  key:        调号，如 C  G  F  Bb  （默认 C）
  time:       拍号，如 4/4  3/4  6/8  （默认 4/4）
  tempo:      速度（BPM，整数，默认 120）

[score] 区块（简谱记谱）
--------------------------
  音符标记（token）规则：
    1–7        简谱音名；1=do 2=re 3=mi 4=fa 5=sol 6=la 7=si
    0          休止符
    #X         升号前缀，如 #4（升 fa）
    bX         降号前缀，如 b7（降 si）
    X'         当前标记音符升高一个八度（可叠加，如 1''）
    X,         当前标记音符降低一个八度（可叠加，如 1,,）
    X-         时值延长一拍（每个 - 延长一拍）
    X_         时值缩短一半（每个 _ 缩短一半；_ = 八分，__ = 十六分）
    X.         附点（在 - 或 _ 后写 .）
    |  或  --- 小节线（仅起可读性分隔作用，不产生音符）

  示例行：
    1 2 3 4 | 5 6 7 1'
    #4 b7, 0_ 0_ 1'-- |
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────────
# 数据结构：与 GUI 编辑器模型兼容（可序列化为 JSON / Qt Model 等）
# ──────────────────────────────────────────────────────────────────────────────

JIANPU_TXT_VERSION = '1.0'
DEFAULT_META: dict[str, str] = {
    'title': '',
    'composer': '',
    'key': 'C',
    'time': '4/4',
    'tempo': '120',
}


@dataclass
class JianpuTxtNote:
    """单个简谱音符或休止符（解析后的内存格式）。

    与 core.config.JianpuNote 不同（后者依赖 music21），本结构仅由文本解析产生，
    完全独立，适合作为 GUI Model 的 leaf 节点。
    """
    symbol: str           # '1'–'7' 或 '0'（休止符）
    accidental: str       # '' | '#' | 'b'
    upper_dots: int       # 高八度点数（'）
    lower_dots: int       # 低八度点数（,）
    dashes: int           # 延音横线数（-），即额外拍数
    underlines: int       # 下划线数（_），每条使时值缩短一半
    aug_dot: bool         # 是否有附点（.）

    @property
    def is_rest(self) -> bool:
        return self.symbol == '0'

    def to_token(self) -> str:
        """序列化回文本标记（规范化格式）。"""
        s = ''
        if self.accidental:
            s += self.accidental
        s += self.symbol
        s += "'" * self.upper_dots
        s += ',' * self.lower_dots
        s += '-' * self.dashes
        s += '_' * self.underlines
        if self.aug_dot:
            s += '.'
        return s

    def quarter_length(self) -> float:
        """返回以四分音符为单位的时值（近似值）。"""
        base = 1.0 / (2 ** self.underlines)
        base += base * self.dashes
        if self.aug_dot:
            base *= 1.5
        return base


@dataclass
class JianpuTxtMeasure:
    """一个小节（Bar）。"""
    notes: list[JianpuTxtNote] = field(default_factory=list)

    def is_empty(self) -> bool:
        return len(self.notes) == 0


@dataclass
class JianpuScoreMeta:
    """乐谱元数据。"""
    title: str = ''
    composer: str = ''
    key: str = 'C'
    time: str = '4/4'
    tempo: int = 120

    def to_dict(self) -> dict[str, str]:
        return {
            'title': self.title,
            'composer': self.composer,
            'key': self.key,
            'time': self.time,
            'tempo': str(self.tempo),
        }


@dataclass
class JianpuTxtScore:
    """完整的简谱乐谱，由元数据和小节列表组成。

    设计目标：
      - 可直接作为 GUI 编辑器的顶层 Model 对象
      - 支持增删改查小节与音符
      - 可往返序列化为 .jianpu.txt 文本格式
    """
    meta: JianpuScoreMeta = field(default_factory=JianpuScoreMeta)
    measures: list[JianpuTxtMeasure] = field(default_factory=list)

    # ── 编辑操作 ──────────────────────────────────────────────────────────

    def append_measure(self, measure: JianpuTxtMeasure | None = None) -> JianpuTxtMeasure:
        m = measure or JianpuTxtMeasure()
        self.measures.append(m)
        return m

    def insert_measure(self, index: int, measure: JianpuTxtMeasure | None = None) -> JianpuTxtMeasure:
        m = measure or JianpuTxtMeasure()
        self.measures.insert(index, m)
        return m

    def remove_measure(self, index: int) -> None:
        del self.measures[index]

    def append_note(self, measure_index: int, note: JianpuTxtNote) -> None:
        self.measures[measure_index].notes.append(note)

    def remove_note(self, measure_index: int, note_index: int) -> None:
        del self.measures[measure_index].notes[note_index]

    def replace_note(self, measure_index: int, note_index: int, note: JianpuTxtNote) -> None:
        self.measures[measure_index].notes[note_index] = note

    def all_notes(self) -> list[JianpuTxtNote]:
        """返回所有音符的扁平列表（只读）。"""
        return [n for m in self.measures for n in m.notes]


# ──────────────────────────────────────────────────────────────────────────────
# 解析器
# ──────────────────────────────────────────────────────────────────────────────

# 音符 token 的正则：可选升降号 + 数字 + 可选八度标记 + 可选时值标记 + 可选附点
_TOKEN_RE = re.compile(
    r'(?P<acc>[#b]?)'          # 可选升降号
    r'(?P<sym>[0-7])'          # 音名 0–7
    r"(?P<upper_dots>'*)"      # 可选高八度 '
    r'(?P<lower_dots>,*)'      # 可选低八度 ,
    r'(?P<dashes>-*)'          # 可选延音横线
    r'(?P<underlines>_*)'      # 可选下划线（缩短时值）
    r'(?P<aug_dot>\.?)',        # 可选附点
)

_BARLINE_RE = re.compile(r'^(\||-{2,})$')


def _parse_token(token: str) -> Optional[JianpuTxtNote]:
    """将单个文本标记解析为 JianpuTxtNote；若不合法返回 None。"""
    m = _TOKEN_RE.fullmatch(token.strip())
    if m is None:
        return None
    return JianpuTxtNote(
        symbol=m.group('sym'),
        accidental=m.group('acc'),
        upper_dots=len(m.group('upper_dots')),
        lower_dots=len(m.group('lower_dots')),
        dashes=len(m.group('dashes')),
        underlines=len(m.group('underlines')),
        aug_dot=bool(m.group('aug_dot')),
    )


class ParseError(ValueError):
    """txt 解析错误，携带行号。"""
    def __init__(self, message: str, line_number: int = 0) -> None:
        super().__init__(f'第 {line_number} 行：{message}' if line_number else message)
        self.line_number = line_number


def parse_txt(text: str) -> JianpuTxtScore:
    """将 .jianpu.txt 文本解析为 JianpuTxtScore。

    Parameters
    ----------
    text:   文件全文（字符串）。

    Returns
    -------
    JianpuTxtScore

    Raises
    ------
    ParseError   解析失败时抛出，含行号与描述。
    """
    score = JianpuTxtScore()
    section: str = ''  # 'meta' | 'score' | ''

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip('\r\n')
        if not line.strip():
            continue
        if re.match(r'^\s*#(\s|$)', line):
            continue
        comment_index = re.search(r'(?<!\S)#(?![0-7])', line)
        if comment_index is not None:
            line = line[:comment_index.start()].rstrip()
            if not line:
                continue

        line = line.strip()

        # 区块标记
        if line.lower() == '[meta]':
            section = 'meta'
            continue
        if line.lower() == '[score]':
            section = 'score'
            score.measures.append(JianpuTxtMeasure())  # 第一个（可能为空）小节
            continue

        if section == 'meta':
            if ':' not in line:
                raise ParseError(f'meta 区块中的键值对缺少冒号：{line!r}', lineno)
            key, _, value = line.partition(':')
            key = key.strip().lower()
            value = value.strip()
            if key == 'title':
                score.meta.title = value
            elif key == 'composer':
                score.meta.composer = value
            elif key == 'key':
                score.meta.key = value
            elif key == 'time':
                score.meta.time = value
            elif key == 'tempo':
                try:
                    score.meta.tempo = int(value)
                except ValueError:
                    raise ParseError(f'tempo 必须为整数，得到：{value!r}', lineno)
            # 未知字段静默忽略（向前兼容）

        elif section == 'score':
            tokens = line.split()
            if not score.measures:
                score.measures.append(JianpuTxtMeasure())
            for token in tokens:
                if _BARLINE_RE.match(token):
                    # 小节线：新建下一个小节
                    score.measures.append(JianpuTxtMeasure())
                    continue
                note = _parse_token(token)
                if note is None:
                    raise ParseError(f'无法解析音符标记：{token!r}', lineno)
                score.measures[-1].notes.append(note)

        else:
            # section 未设置时遇到内容：尝试宽容地跳过（向前兼容未知头部）
            pass

    # 移除末尾空小节
    while score.measures and score.measures[-1].is_empty():
        score.measures.pop()

    return score


def parse_file(path: str | Path) -> JianpuTxtScore:
    """从文件路径读取并解析 .jianpu.txt。"""
    path = Path(path)
    text = path.read_text(encoding='utf-8')
    return parse_txt(text)


# ──────────────────────────────────────────────────────────────────────────────
# 序列化器
# ──────────────────────────────────────────────────────────────────────────────

def _format_measure(measure: JianpuTxtMeasure, notes_per_line: int = 8) -> str:
    """将一个小节格式化为文本片段（含尾部小节线）。"""
    tokens = [n.to_token() for n in measure.notes]
    return ' '.join(tokens)


def serialize_txt(score: JianpuTxtScore, *, notes_per_line: int = 8) -> str:
    """将 JianpuTxtScore 序列化为 .jianpu.txt 格式的字符串。

    Parameters
    ----------
    score:          待序列化的乐谱。
    notes_per_line: 每行最多显示的音符数（超出自动换行）。
    """
    lines: list[str] = []

    # 文件头注释
    lines.append(f'# JianpuScore v{JIANPU_TXT_VERSION}')
    lines.append('# 由简谱转换工具生成；可手动编辑后用于 GUI 编辑器')
    lines.append('')

    # [meta] 区块
    lines.append('[meta]')
    lines.append(f'title:    {score.meta.title}')
    lines.append(f'composer: {score.meta.composer}')
    lines.append(f'key:      {score.meta.key}')
    lines.append(f'time:     {score.meta.time}')
    lines.append(f'tempo:    {score.meta.tempo}')
    lines.append('')

    # [score] 区块
    lines.append('[score]')
    measure_parts: list[str] = []
    for measure in score.measures:
        measure_parts.append(_format_measure(measure))

    # 将小节以 | 拼接，每 notes_per_line 个音符换行以保持可读性
    all_tokens: list[str] = []
    for i, measure in enumerate(score.measures):
        tokens = [n.to_token() for n in measure.notes]
        all_tokens.extend(tokens)
        if i < len(score.measures) - 1:
            all_tokens.append('|')

    # 按 notes_per_line 进行视觉换行（分组，遇到 | 时适当保留）
    current_row: list[str] = []
    note_count = 0
    score_lines: list[str] = []
    for tok in all_tokens:
        current_row.append(tok)
        if tok != '|':
            note_count += 1
        if tok == '|' and note_count >= notes_per_line:
            score_lines.append(' '.join(current_row))
            current_row = []
            note_count = 0
    if current_row:
        score_lines.append(' '.join(current_row))

    lines.extend(score_lines)
    lines.append('')  # 末尾空行

    return '\n'.join(lines)


def save_file(score: JianpuTxtScore, path: str | Path, *, notes_per_line: int = 8) -> None:
    """将乐谱序列化并写入文件（UTF-8，LF 换行）。"""
    path = Path(path)
    path.write_text(serialize_txt(score, notes_per_line=notes_per_line), encoding='utf-8')


# ──────────────────────────────────────────────────────────────────────────────
# 辅助：从 JianpuNote（music21 派生）批量创建 JianpuTxtScore
# ──────────────────────────────────────────────────────────────────────────────

def from_jianpu_notes(
    notes: list,  # list[core.config.JianpuNote]
    *,
    title: str = '',
    composer: str = '',
    key: str = 'C',
    time: str = '4/4',
    tempo: int = 120,
    beats_per_measure: float = 4.0,
) -> JianpuTxtScore:
    """将 JianpuNote 列表（来自 OMR 流程）转换为 JianpuTxtScore。

    每隔 beats_per_measure 拍自动插入小节线。
    """
    meta = JianpuScoreMeta(title=title, composer=composer, key=key, time=time, tempo=tempo)
    score = JianpuTxtScore(meta=meta)
    current_measure = score.append_measure()
    beats_in_measure = 0.0

    for jn in notes:
        # 若超过一小节则新建
        if beats_in_measure >= beats_per_measure:
            current_measure = score.append_measure()
            beats_in_measure = 0.0

        txt_note = JianpuTxtNote(
            symbol=jn.symbol,
            accidental=jn.accidental,
            upper_dots=jn.upper_dots,
            lower_dots=jn.lower_dots,
            # 将 duration→dashes/underlines 映射（近似）
            dashes=max(0, int(jn.duration) - 1),
            underlines=_duration_to_underlines(jn.duration),
            aug_dot=(jn.duration_dots > 0),
        )
        current_measure.notes.append(txt_note)
        beats_in_measure += jn.duration

    return score


def _duration_to_underlines(duration: float) -> int:
    """将四分音符时值（quarterLength）映射到下划线数。"""
    if duration >= 0.999:
        return 0
    if duration >= 0.499:
        return 1
    if duration >= 0.249:
        return 2
    return 3


# ──────────────────────────────────────────────────────────────────────────────
# 简单 CLI 工具（直接运行本模块时可用）
# ──────────────────────────────────────────────────────────────────────────────

def _cli_main() -> None:
    import sys
    if len(sys.argv) < 2:
        print('用法：python -m core.jianpu_txt_editor <文件.jianpu.txt>')
        print('       python -m core.jianpu_txt_editor --example')
        sys.exit(0)

    if sys.argv[1] == '--example':
        # 生成示例文件
        example = JianpuTxtScore(
            meta=JianpuScoreMeta(title='示例乐曲', composer='Tsukamotoshio', tempo=100),
        )
        m1 = example.append_measure()
        for sym in ('1', '2', '3', '4'):
            m1.notes.append(JianpuTxtNote(sym, '', 0, 0, 0, 0, False))
        m2 = example.append_measure()
        for sym in ('5', '6', '7'):
            m2.notes.append(JianpuTxtNote(sym, '', 0, 0, 0, 0, False))
        m2.notes.append(JianpuTxtNote('1', '', 1, 0, 0, 0, False))  # 高八度 do
        out = 'example.jianpu.txt'
        save_file(example, out)
        print(f'已生成示例文件：{out}')
        return

    path = Path(sys.argv[1])
    if not path.exists():
        print(f'文件不存在：{path}')
        sys.exit(1)

    try:
        score = parse_file(path)
    except ParseError as e:
        print(f'解析错误：{e}')
        sys.exit(1)

    print(f'标题：{score.meta.title or "（无）"}')
    print(f'作曲：{score.meta.composer or "（无）"}')
    print(f'调号：{score.meta.key}  拍号：{score.meta.time}  速度：{score.meta.tempo} BPM')
    print(f'共 {len(score.measures)} 个小节，{len(score.all_notes())} 个音符')
    print()
    print(serialize_txt(score))


if __name__ == '__main__':
    _cli_main()
