# 复现：对真实 Output 简谱跑完整 OCR → musicxml → 渲染链路，同时比对真值。
# 用法: .venv/Scripts/python.exe _repro_score.py 红河谷
import sys, json, re, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pypdfium2 as pdfium

name = sys.argv[1]

# 1) 渲染 Output/<name>_jianpu.pdf 所有页 → 合成一张高 PNG（与 GUI 输入一致用单图）
src_pdf = Path('Output') / f'{name}_jianpu.pdf'
pdf = pdfium.PdfDocument(str(src_pdf))
imgs = []
for i in range(len(pdf)):
    imgs.append(pdf[i].render(scale=2.0).to_pil())
pdf.close()
from PIL import Image
W = max(im.width for im in imgs)
H = sum(im.height for im in imgs)
canvas = Image.new('RGB', (W, H), 'white')
y = 0
for im in imgs:
    canvas.paste(im, (0, y)); y += im.height
png = Path('jianpu-Input') / f'{name}.png'
png.parent.mkdir(exist_ok=True)
canvas.save(png)
print(f'[render] {src_pdf.name}: {len(imgs)} page(s) -> {png} {canvas.size}')

# 2) 识别
from core.app.backend import vlm_models_dir, xml_scores_dir
from core.config import VLM_MODEL_FILENAME, VLM_MMPROJ_FILENAME
from core.vlm.jianpu_recognizer import recognize_image
mdir = vlm_models_dir()
data = recognize_image(png, mdir / VLM_MODEL_FILENAME, mdir / VLM_MMPROJ_FILENAME)
Path(f'_ocr_{name}.json').write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
print(f"[recognize] measures={len(data.get('measures',[]))} key={data.get('key')} time={data.get('time_signature')}")

# 3) 转 musicxml
from core.vlm.json_to_musicxml import convert, measures_as_token_lists
mxl = xml_scores_dir() / f'{name}_ocr.musicxml'
convert(data, mxl, None)
print(f'[convert] -> {mxl}')

# 4) 试渲染（与预览页相同路径）
from core.render.lilypond_runner import render_musicxml_staff_pdf
with tempfile.TemporaryDirectory() as tmp:
    pdf_out = render_musicxml_staff_pdf(mxl, Path(tmp))
    print(f'[render musicxml] -> {"OK " + str(pdf_out) if pdf_out else "FAILED (returned None)"}')

# 5) 比对真值（token 相似度）
def parse_truth(path):
    body = []
    for ln in path.read_text(encoding='utf-8').splitlines():
        s = ln.strip()
        if not s or s.startswith('%') or s.startswith('title='): continue
        if s.startswith('#') and not re.match(r'^#[1-7]', s): continue
        if re.match(r'^[0-9]+=', s) or re.match(r'^[0-9]+/[0-9]+', s): continue
        body.append(s)
    text = ' '.join(body).replace('NextPart',' ')
    return [seg.split() for seg in text.split('|') if seg.strip()]

truth = parse_truth(Path('editor-workspace') / f'{name}.jianpu.txt')
ocr = [t for t in measures_as_token_lists(data) if t]
import difflib
print(f'[measures] truth={len(truth)} ocr={len(ocr)}')
n_show = min(len(truth), len(ocr))
exact = 0
for i in range(max(len(truth), len(ocr))):
    t = ' '.join(truth[i]) if i < len(truth) else '(none)'
    o = ' '.join(ocr[i]) if i < len(ocr) else '(none)'
    ok = (i < len(truth) and i < len(ocr) and truth[i] == ocr[i])
    if ok: exact += 1
    print(f'{"OK " if ok else "!! "}M{i+1:<2} T: {t:<34} | O: {o}')
ft = [tk for m in truth for tk in m]; fo = [tk for m in ocr for tk in m]
print(f'[exact-measure] {exact}/{len(truth)}  [token-sim] {difflib.SequenceMatcher(a=ft,b=fo,autojunk=False).ratio()*100:.1f}%')
