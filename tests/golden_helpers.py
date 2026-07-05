# tests/golden_helpers.py — shared serialization for the jianpu golden-file tests.
#
# 输出格式必须保持稳定：金样测试逐字节比较。若有意变更提取逻辑导致输出变化，
# 用 REGEN_GOLDEN=1 重新生成金样，并在 code review 中人工审查 diff。
from pathlib import Path


def generate_outputs(musicxml_path: Path) -> dict[str, str]:
    """Run both jianpu extraction paths on a MusicXML file.

    Returns {suffix: text} for the two golden artifacts:
      .jly.txt      — build_jianpu_ly_text (part-flatten by_offset path)
      .measures.txt — parse_score_to_jianpu (measure-level by_offset path),
                      serialized note-by-note
    """
    from music21 import converter
    from core.notation.jianpu import build_jianpu_ly_text, parse_score_to_jianpu

    score = converter.parse(str(musicxml_path))
    stem = musicxml_path.stem

    jly = build_jianpu_ly_text(score, title=stem)

    measures, headers, ts = parse_score_to_jianpu(score)
    lines = [f'# ts={ts}', f'# headers={headers}']
    for i, m in enumerate(measures):
        lines.append(f'M{i + 1}: ' + ' '.join(
            f'{n.symbol}{n.accidental}/u{n.upper_dots}l{n.lower_dots}'
            f'/d{n.duration:g}.{n.duration_dots}/{"R" if n.is_rest else "N"}'
            for n in m
        ))
    return {'.jly.txt': jly, '.measures.txt': '\n'.join(lines)}
