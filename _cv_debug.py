# 调试单个小节块的 measure_cv 分析：打印组件分类 + 输出带框可视化 PNG。
# 用法: .venv/Scripts/python.exe _cv_debug.py image_test0 12   (1-based 小节号)
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
from PIL import Image, ImageDraw

from _cv_eval import parse_truth, chunks_for
from core.vlm import measure_cv as mc

name, midx = sys.argv[1], int(sys.argv[2])
truth = parse_truth(Path('editor-workspace') / f'{name}.jianpu.txt')
chunks = chunks_for(name)
img = chunks[midx - 1]
toks = truth[midx - 1] if midx - 1 < len(truth) else []
n_digits = sum(1 for t in toks if t.strip() != '-')
print(f'M{midx} truth: {" ".join(toks)}  (n_digits={n_digits})  chunk={img.size}')

g = np.array(img.convert('L'))
H, W = g.shape
dark = mc._erase_barlines(g < mc._DARK_THRESH)
comps = mc._components(dark)
print(f'H={H} W={W} comps={len(comps)}')
for c in sorted(comps, key=lambda c: c.x0):
    flat = (c.h <= mc._FLAT_MAX_H_FRAC * H and c.w >= mc._FLAT_AR * c.h)
    bar = (c.h >= mc._BARLINE_H_FRAC * H and c.w <= 0.12 * c.h)
    kind = 'bar' if bar else ('flat' if flat else 'glyph')
    print(f'  {kind:<5} x={c.x0:<4} y={c.y0:<4} w={c.w:<4} h={c.h:<4} area={c.area}')

cv = mc.analyze_measure(img, n_digits)
if cv is None:
    print('analyze_measure -> None')
else:
    for d in cv.digits:
        print(f'  DIGIT x={d.x0}-{d.x1} y={d.y0}-{d.y1} oct={d.oct} ul={d.underlines} dot={d.dotted}')
    print(f'  dashes at x={cv.dash_xs}')

vis = img.convert('RGB')
dr = ImageDraw.Draw(vis)
for c in comps:
    flat = (c.h <= mc._FLAT_MAX_H_FRAC * H and c.w >= mc._FLAT_AR * c.h)
    dr.rectangle([c.x0, c.y0, c.x1, c.y1], outline=(0, 160, 255) if flat else (200, 200, 0))
if cv:
    for d in cv.digits:
        dr.rectangle([d.x0, d.y0, d.x1, d.y1], outline=(255, 0, 0), width=2)
out = Path('build/cvdbg') / f'dbg_{name}_M{midx}.png'
vis.save(out)
print('saved', out)
