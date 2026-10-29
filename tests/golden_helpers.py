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
            # Marks are appended only when present, so adding a field here does
            # not rewrite the golden of every file that carries none.
            + (f'/dy={n.dynamic}' if n.dynamic else '')
            + (f'/hp={n.hairpin_start}' if n.hairpin_start else '')
            + ('/hp=!' if n.hairpin_end else '')
            for n in m
        ))
    return {
        '.jly.txt': jly,
        '.measures.txt': '\n'.join(lines),
        '.render.txt': render_json_text(jly),
    }


def render_json_text(jly: str) -> str:
    """What the graphical editor hands JianpuRender for this text.

    Mirrors ``webui.editor.EditorService._renders_of`` (one render per section,
    the title block on the first) without importing webui. The golden pins the
    parser and the render projection on real scores, the input half of every
    graphical-editor layout fix.
    """
    import json

    from core.notation.jianpu import jianpu_section_to_render_json, score_header
    from core.notation.jianpu.parser import parse_jianpu_ly_text

    doc = parse_jianpu_ly_text(jly)
    renders = [jianpu_section_to_render_json(s, doc.key_header, doc.tempo) for s in doc.sections]
    if renders:
        renders[0]['header'] = score_header(doc)

    # 每个音符/slot 一行：比逐字段缩进小约 8 倍，diff 仍能定位到单个音符。
    def one(value) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    out = []
    for i, render in enumerate(renders):
        out.append(f'# section {i}')
        for key in sorted(render):
            value = render[key]
            if isinstance(value, list):
                out.append(f'{key}:')
                out.extend(f'  {one(item)}' for item in value)
            else:
                out.append(f'{key}: {one(value)}')
    return '\n'.join(out) + '\n'
