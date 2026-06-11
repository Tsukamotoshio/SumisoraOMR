"""对 editor-workspace/<name>.jianpu.txt（人工校对真值）评测 VLM-OCR 结果。

用法:
  python _diff_jianpu.py <name>            # 复用已存的 _ocr_<name>.json
  python _diff_jianpu.py <name> --run      # 重跑 VLM 推理
两侧都转成 jianpu-ly token 序列，按小节 / token 对比。
"""
import sys, json, difflib, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from core.vlm.json_to_musicxml import measures_as_token_lists

name = sys.argv[1]
do_run = '--run' in sys.argv


def parse_truth(path: Path) -> list[list[str]]:
    """解析 .jianpu.txt → 每小节 token 列表。"""
    body = []
    for ln in path.read_text(encoding='utf-8').splitlines():
        s = ln.strip()
        # "#6. q1' #6" 以升号开头的乐谱行不是注释，只跳过非音符的 # 行
        if not s or s.startswith('%'):
            continue
        if s.startswith('#') and not re.match(r'^#[1-7]', s):
            continue
        if s.startswith('title='):
            continue
        if re.match(r'^[0-9]+=', s):          # 1=E
            continue
        if re.match(r'^[0-9]+/[0-9]+', s):    # 3/4 或 12/8,2.
            continue
        body.append(s)
    text = ' '.join(body).replace('NextPart', ' ')
    measures = [seg.strip() for seg in text.split('|')]
    return [seg.split() for seg in measures if seg.strip()]


def ocr_measures(data: dict) -> list[list[str]]:
    return [toks for toks in measures_as_token_lists(data) if toks]


truth = parse_truth(Path('editor-workspace') / f'{name}.jianpu.txt')

json_path = Path(f'_ocr_{name}.json')
if do_run or not json_path.exists():
    from core.app.backend import vlm_models_dir
    from core.config import VLM_MODEL_FILENAME, VLM_MMPROJ_FILENAME
    from core.vlm.jianpu_recognizer import recognize_image
    mdir = vlm_models_dir()
    data = recognize_image(Path('jianpu-Input') / f'{name}.png',
                           mdir / VLM_MODEL_FILENAME, mdir / VLM_MMPROJ_FILENAME)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
else:
    data = json.loads(json_path.read_text(encoding='utf-8'))

ocr = ocr_measures(data)

print('[measures] truth=%d ocr=%d' % (len(truth), len(ocr)))
print('==== measure-by-measure (first 30) ====')
exact = 0
for i in range(min(30, max(len(truth), len(ocr)))):
    t = ' '.join(truth[i]) if i < len(truth) else '(none)'
    o = ' '.join(ocr[i]) if i < len(ocr) else '(none)'
    ok = (i < len(truth) and i < len(ocr) and truth[i] == ocr[i])
    print('%s M%-2d T: %-28s | O: %s' % ('OK ' if ok else '!! ', i + 1, t, o))
for i in range(min(len(truth), len(ocr))):
    if truth[i] == ocr[i]:
        exact += 1
print('[measure-exact-match] %d/%d = %.0f%%' % (exact, len(truth), 100.0 * exact / max(1, len(truth))))

flat_t = [tk for m in truth for tk in m]
flat_o = [tk for m in ocr for tk in m]
print('[token counts] truth=%d ocr=%d' % (len(flat_t), len(flat_o)))
sm = difflib.SequenceMatcher(a=flat_t, b=flat_o, autojunk=False)
print('[token similarity] %.1f%%' % (sm.ratio() * 100))
