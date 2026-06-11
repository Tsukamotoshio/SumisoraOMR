# 离线评测 measure_cv（不跑 VLM）：用真值 token 提供 n_expected 锚点，
# 检验 CV 检测的八度/下划线时值/附点/延音线是否与真值一致。
# 用法: .venv/Scripts/python.exe _cv_eval.py image_test2 [image_test0 ...]
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from PIL import Image

from core.vlm.jianpu_recognizer import (
    _autocrop_content, _split_rows, _detect_barlines, _upscale_to_height,
    _CHUNK_X_MARGIN, _CHUNK_TARGET_H,
)
from core.vlm.measure_cv import analyze_measure, _apply_accidentals, _UL_TO_DUR


def parse_truth(path: Path) -> list[list[str]]:
    body = []
    for ln in path.read_text(encoding='utf-8').splitlines():
        s = ln.strip()
        # 注意: "#6. q1' #6" 这种以升号开头的乐谱行不是注释 → 只跳过 "#x"(非音符)行
        if not s or s.startswith('%') or s.startswith('title='):
            continue
        if s.startswith('#') and not re.match(r'^#[1-7]', s):
            continue
        if re.match(r'^[0-9]+=', s) or re.match(r'^[0-9]+/[0-9]+', s):
            continue
        body.append(s)
    text = ' '.join(body).replace('NextPart', ' ')
    return [seg.split() for seg in text.split('|') if seg.strip()]


def truth_token_fields(tok: str):
    """token → (kind, oct, dur, dots)；kind: 'note'/'dash'。"""
    s = tok.strip()
    if s == '-':
        return ('dash', 0, '', 0)
    dur = 'q'
    if s[0] == 'q':
        dur, s = 'e', s[1:]
    elif s[0] == 's':
        dur, s = 's', s[1:]
    elif s[0] == 'd':
        dur, s = 's', s[1:]
    if s and s[0] in '#b':
        s = s[1:]
    s = s[1:]  # 音高字符
    octv = 0
    while s and s[0] in ("'", '’'):
        octv += 1
        s = s[1:]
    while s and s[0] == ',':
        octv -= 1
        s = s[1:]
    dots = 1 if s.startswith('.') else 0
    return ('note', octv, dur, dots)


def chunks_for(name: str):
    img = Image.open(f'jianpu-Input/{name}.png')
    cropped = _autocrop_content(img)
    rows = [(y0, y1) for (y0, y1) in _split_rows(cropped) if (y1 - y0) >= 30]
    W = cropped.size[0]
    out = []
    for (y0, y1) in rows:
        ry0 = max(0, y0 - 6)
        ry1 = min(cropped.size[1], y1 + 6)
        row_img = cropped.crop((0, ry0, W, ry1))
        bars = _detect_barlines(row_img)
        if len(bars) < 2:
            out.append(cropped.crop((0, ry0, W, ry1)))
            continue
        edges = [0] + bars
        for i in range(len(bars)):
            x0 = max(0, edges[i] - _CHUNK_X_MARGIN)
            x1 = min(W, edges[i + 1] + _CHUNK_X_MARGIN)
            if x1 - x0 < 8:
                continue
            ch = cropped.crop((x0, ry0, x1, ry1))
            out.append(_upscale_to_height(ch, _CHUNK_TARGET_H))
    return out


def main(name: str) -> None:
    truth = parse_truth(Path('editor-workspace') / f'{name}.jianpu.txt')
    chunks = chunks_for(name)
    n = min(len(truth), len(chunks))
    print(f'== {name}: truth measures={len(truth)} chunks={len(chunks)} (compare {n})')
    n_ok = n_fail = n_none = 0
    field_err = {'oct': 0, 'dur': 0, 'dots': 0, 'seq': 0, 'acc': 0}
    for i in range(n):
        toks = truth[i]
        exp = [truth_token_fields(t) for t in toks]
        n_digits = sum(1 for e in exp if e[0] == 'note')
        cv = analyze_measure(chunks[i], n_digits)
        if cv is None:
            n_none += 1
            print(f'!! M{i+1:<3} CV=None  T: {" ".join(toks)}')
            continue
        # 升降号重建校验：身份用真值数字，前缀由 CV 状态机重建
        exp_pres = []
        tr_digits = []
        for t in toks:
            s = re.sub(r'^[qsd]', '', t.strip())
            if s == '-':
                continue
            pre = s[0] if s[0] in '#b' else ''
            exp_pres.append(pre)
            s2 = s.lstrip('#b')
            tr_digits.append('r' if s2 and s2[0] == '0' else (s2[0] if s2 else 'r'))
        got_pres = [p[:-1] if len(p) > 1 else ''
                    for p in _apply_accidentals(cv.digits, tr_digits)]
        acc_errs = [f'acc@{k}({e!r}->{g!r})'
                    for k, (e, g) in enumerate(zip(exp_pres, got_pres)) if e != g]
        field_err['acc'] += len(acc_errs)
        got = []
        for kind, obj in cv.events():
            if kind == 'dash':
                got.append(('dash', 0, '', 0))
            else:
                got.append(('note', obj.oct, _UL_TO_DUR.get(obj.underlines, 'q'),
                            1 if obj.dotted else 0))
        errs = list(acc_errs)
        if [g[0] for g in got] != [e[0] for e in exp]:
            errs.append('seq')
            field_err['seq'] += 1
        else:
            for k, (e, g) in enumerate(zip(exp, got)):
                if e[0] != 'note':
                    continue
                if e[1] != g[1]:
                    errs.append(f'oct@{k}({e[1]}->{g[1]})')
                    field_err['oct'] += 1
                if e[2] != g[2]:
                    errs.append(f'dur@{k}({e[2]}->{g[2]})')
                    field_err['dur'] += 1
                if e[3] != g[3]:
                    errs.append(f'dots@{k}({e[3]}->{g[3]})')
                    field_err['dots'] += 1
        if errs:
            n_fail += 1
            gs = ' '.join(('-' if g[0] == 'dash' else
                           f'{g[2]}/{g[1]:+d}{"." if g[3] else ""}') for g in got)
            print(f'!! M{i+1:<3} {",".join(errs):<28} T: {" ".join(toks):<30} CV: {gs}')
        else:
            n_ok += 1
    print(f'[CV] ok={n_ok} fail={n_fail} none={n_none}  field_errors={field_err}')


if __name__ == '__main__':
    for nm in sys.argv[1:] or ['image_test2', 'image_test0']:
        main(nm)
