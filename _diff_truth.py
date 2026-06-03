import sys, os, difflib
from pathlib import Path
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
sys.path.insert(0, str(Path(__file__).parent))
import music21 as m21
from core.app.backend import vlm_models_dir
from core.config import VLM_MODEL_FILENAME, VLM_MMPROJ_FILENAME
from core.vlm.jianpu_recognizer import recognize_image
from core.vlm.json_to_musicxml import convert
import tempfile

mdir = vlm_models_dir()
model, mmproj = mdir / VLM_MODEL_FILENAME, mdir / VLM_MMPROJ_FILENAME
name = sys.argv[1]

def measures_from_score(s):
    out = []
    for mm in s.recurse().getElementsByClass('Measure'):
        toks = []
        for n in mm.notesAndRests:
            if n.isRest:
                toks.append('r:%.3g' % float(n.quarterLength))
            else:
                toks.append('%s%d:%.3g' % (n.pitch.step, n.pitch.octave, float(n.quarterLength)))
        out.append(toks)
    return out

def flat(meass):
    return [t for m in meass for t in m]

truth_m = measures_from_score(m21.converter.parse(
    r'e:/Project_Convert/xml-scores/%s.musicxml' % name))

img = Path(__file__).parent / 'jianpu-Input' / (name + '.png')
data = recognize_image(img, model, mmproj)
import json
Path(__file__).parent.joinpath('_ocr_%s.json' % name).write_text(
    json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
tmp = Path(tempfile.mkstemp(suffix='.musicxml')[1])
convert(data, tmp, None)
ocr_m = measures_from_score(m21.converter.parse(str(tmp)))

print('[measures] truth=%d ocr=%d' % (len(truth_m), len(ocr_m)))
print('==== measure-by-measure (first 30) ====')
correct = 0
for i in range(min(30, max(len(truth_m), len(ocr_m)))):
    t = ' '.join(truth_m[i]) if i < len(truth_m) else '(none)'
    o = ' '.join(ocr_m[i]) if i < len(ocr_m) else '(none)'
    mark = 'OK ' if (i < len(truth_m) and i < len(ocr_m) and truth_m[i] == ocr_m[i]) else '!! '
    print('%s M%-2d T: %-34s | O: %s' % (mark, i + 1, t, o))
for i in range(min(len(truth_m), len(ocr_m))):
    if truth_m[i] == ocr_m[i]:
        correct += 1
print('[measure-exact-match] %d/%d = %.0f%%' % (correct, len(truth_m), 100.0 * correct / max(1, len(truth_m))))
truth = flat(truth_m); ocr = flat(ocr_m)

print('[counts] truth=%d ocr=%d' % (len(truth), len(ocr)))
sm = difflib.SequenceMatcher(a=truth, b=ocr, autojunk=False)
print('[similarity ratio] %.1f%%' % (sm.ratio() * 100))
sub = ins = dele = eq = 0
pitch_only = dur_only = both = 0
def split(t):
    p, d = t.rsplit(':', 1); return p, d
for tag, i1, i2, j1, j2 in sm.get_opcodes():
    if tag == 'equal':
        eq += i2 - i1
    elif tag == 'replace':
        for k in range(max(i2 - i1, j2 - j1)):
            a = truth[i1 + k] if i1 + k < i2 else '----'
            b = ocr[j1 + k] if j1 + k < j2 else '----'
            sub += 1
            if a != '----' and b != '----':
                pa, da = split(a); pb, db = split(b)
                if pa != pb and da != db: both += 1
                elif pa != pb: pitch_only += 1
                else: dur_only += 1
            print('  SUB truth=%-10s ocr=%-10s' % (a, b))
    elif tag == 'delete':
        dele += i2 - i1
        for k in range(i1, i2): print('  MISS truth=%-10s (ocr none)' % truth[k])
    elif tag == 'insert':
        ins += j2 - j1
        for k in range(j1, j2): print('  EXTRA ocr=%-10s (truth none)' % ocr[k])
print('[summary] equal=%d sub=%d miss=%d extra=%d' % (eq, sub, dele, ins))
print('  of subs: pitch-only=%d dur-only=%d both=%d' % (pitch_only, dur_only, both))
