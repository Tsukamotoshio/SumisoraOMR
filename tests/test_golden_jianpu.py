# tests/test_golden_jianpu.py — golden-file regression tests for jianpu extraction.
#
# 语料：7 个真实 OMR 输出的 MusicXML（覆盖多声部×2/×3、小调、弱起、附点十六分、
# 大/小文件），全部为公有领域曲目或项目自有扫描件。
#
# 变更流程：改动 core/notation/ 后跑 pytest；若失败且差异是有意的，
# 运行 `REGEN_GOLDEN=1 pytest tests/test_golden_jianpu.py` 重新生成金样，
# 并在提交前人工审查金样 diff（这正是"行为有变类"修复的验收动作）。
import os
from pathlib import Path

import pytest

from .golden_helpers import generate_outputs

_FIXTURE_DIR = Path(__file__).parent / 'fixtures'
_MUSICXML = sorted((_FIXTURE_DIR / 'musicxml').glob('*.musicxml'))
_GOLDEN_DIR = _FIXTURE_DIR / 'golden'


@pytest.mark.parametrize('src', _MUSICXML, ids=lambda p: p.stem)
def test_jianpu_extraction_matches_golden(src: Path) -> None:
    outputs = generate_outputs(src)

    if os.environ.get('REGEN_GOLDEN') == '1':
        _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        for suffix, text in outputs.items():
            (_GOLDEN_DIR / f'{src.stem}{suffix}').write_text(text, encoding='utf-8')
        pytest.skip('golden regenerated — review the diff before committing')

    for suffix, text in outputs.items():
        golden_file = _GOLDEN_DIR / f'{src.stem}{suffix}'
        assert golden_file.exists(), (
            f'缺少金样 {golden_file.name}：运行 REGEN_GOLDEN=1 pytest 生成'
        )
        expected = golden_file.read_text(encoding='utf-8')
        assert text == expected, (
            f'{src.stem}{suffix} 与金样不一致。'
            '若该差异是有意变更，REGEN_GOLDEN=1 重新生成并在提交前审查金样 diff。'
        )


def test_fixture_corpus_nonempty() -> None:
    assert len(_MUSICXML) >= 5, '金样语料不应少于 5 个文件'
