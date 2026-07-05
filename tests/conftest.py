# tests/conftest.py — make the repo root importable when pytest runs from anywhere.
import importlib.util
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# test_json_to_musicxml.py 属于 exp/jianpu-vlm-ocr 分支（依赖 core.vlm.json_to_musicxml），
# 在 main 上该模块不存在——按可用性跳过收集，两个分支都能正常跑 pytest。
# 注意探测具体模块而非 core.vlm：工作目录里残留的空 core/vlm/ 会命中命名空间包。
collect_ignore: list[str] = []
try:
    _vlm_available = importlib.util.find_spec('core.vlm.json_to_musicxml') is not None
except (ImportError, ModuleNotFoundError):
    _vlm_available = False
if not _vlm_available:
    collect_ignore.append('test_json_to_musicxml.py')
