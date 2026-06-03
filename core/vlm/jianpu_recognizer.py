# core/vlm/jianpu_recognizer.py — Stage 1: jianpu image/PDF → JSON note list
# Uses Qwen2.5-VL-7B-Instruct GGUF via llama-cpp-python (Qwen25VLChatHandler).
# Pipeline: open → autocrop content → row-split (multi-row jianpu) → per-row VLM
# inference → JSON parse with truncation/structure recovery → merge.
# The Llama instance is cached as a module singleton; call release_vlm() to free memory.
from __future__ import annotations

import base64
import json
import logging
import tempfile
import threading
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger('convert')

try:
    import llama_cpp
    from llama_cpp import Llama
    from llama_cpp.llama_chat_format import Qwen25VLChatHandler
    _LLAMA_AVAILABLE = True
except ImportError:
    _LLAMA_AVAILABLE = False

_vlm: Optional['Llama'] = None          # type: ignore[name-defined]
_vlm_model_path: Optional[Path] = None
_vlm_lock = threading.Lock()

_SYSTEM_PROMPT = (
    "You are a jianpu OCR engine. Output ONE compact JSON object describing the "
    "whole score, then immediately stop. NEVER repeat a measure. No markdown."
)

# ===== VARIANT A: Explicit Jianpu Digit Recognition (Targets 91.7% pitch error) =====
# Key insight: The VLM treats jianpu like staff notation and tries to transpose digits
# based on key signature. This variant makes explicit that digits 1-7 are fixed,
# regardless of key. The key only affects which Western note each digit represents.
_USER_PROMPT_FULL = (
    "CRITICAL: You are reading JIANPU (numbered notation), NOT staff notation.\n"
    "In JIANPU, the digits 1-7 ALWAYS represent the same fixed scale, independent of key.\n"
    "The key signature (e.g., '1=C' or '1=A') tells you which Western note digit 1 represents,\n"
    "but does NOT change how you transcribe the JIANPU DIGITS THEMSELVES.\n"
    "\n"
    "JIANPU DIGIT SCALE (what you see on the page, output exactly as is):\n"
    "  1 = DO   2 = RE   3 = MI   4 = FA   5 = SOL   6 = LA   7 = TI\n"
    "\n"
    "CRITICAL RULE: When you see digit '1' on the page → output {\"p\":\"1\"}\n"
    "               When you see digit '2' on the page → output {\"p\":\"2\"}\n"
    "               ... and so on for 3,4,5,6,7\n"
    "NEVER change or transpose the digit value based on the key signature.\n"
    "\n"
    "OCTAVE MARKERS (CRITICAL — ONLY a dot vertically centered ABOVE or BELOW the digit):\n"
    "- A dot DIRECTLY ABOVE the digit (centered over it) → octave +1 (high)\n"
    "- A dot DIRECTLY BELOW the digit (centered under it) → octave -1 (low)\n"
    "- NO dot above or below → octave 0 (middle, standard)\n"
    "- Two stacked dots ABOVE → octave +2;  two stacked dots BELOW → octave -2\n"
    "DO NOT confuse octave dots with duration marks (this is the #1 error source):\n"
    "- A dot to the RIGHT of a digit on the SAME line (e.g. '4.', '6.', '1.') is a DURATION dot → dots:1, oct UNCHANGED. NOT a low octave.\n"
    "- An UNDERLINE beneath a digit is a DURATION mark (eighth/sixteenth) → oct UNCHANGED. NOT a low octave.\n"
    "- Only a small dot floating directly over/under the digit body changes the octave.\n"
    "- CORRECT: 'i' = '1' with oct:+1.  digit with dot underneath = oct:-1.  '4.' = '4' oct:0 dots:1.  plain '1' = oct:0.\n"
    "\n"
    "DURATION NOTATION (the time signature does NOT change these — only the marks do):\n"
    "- A bare digit with NO underline and NO dash is ALWAYS a quarter 'q', in EVERY time signature (incl. 6/8, 12/8).\n"
    "- ONE underline below digit = eighth 'e'\n"
    "- TWO underlines = sixteenth 's'\n"
    "- Dash after digit: '5 -' = half, '5 - -' = dotted-half, '5 - - -' = whole\n"
    "\n"
    "Transcribe to ONE compact JSON (no spaces or newlines).\n"
    "STRUCTURE: \"measures\" is a list of MEASURES. Each measure is a list of NOTE objects.\n"
    "  CORRECT:  \"measures\":[[{...},{...}],[{...}]]            ← outer=measures, inner=notes\n"
    "  WRONG:    \"measures\":[{...},{...}]                      ← flat (no measure grouping)\n"
    "  WRONG:    \"measures\":[{\"notes\":[...]},{\"notes\":[...]}]   ← do NOT wrap notes in objects\n"
    "  WRONG:    \"measures\":[[{\"notes\":[...]}]]                ← do NOT add 'notes' key at all\n"
    "Example for key='C' (1=do=C), 2 measures in 4/4:\n"
    '{"time_signature":"4/4","key":"C","tempo":120,"measures":['
    '[{"p":"5","oct":0,"dur":"q","dots":0},{"p":"3","oct":0,"dur":"q","dots":0},'
    '{"p":"1","oct":0,"dur":"h","dots":0}],'
    '[{"p":"r","oct":0,"dur":"q","dots":0},{"p":"6","oct":-1,"dur":"e","dots":0},'
    '{"p":"7","oct":-1,"dur":"e","dots":1}]'
    "]}\n"
    "Note fields:\n"
    '- p: "1"-"7" digit EXACTLY AS SEEN. "0" → "r" (rest). Accidentals: "#"/"b".\n'
    '- oct: +1/-1 (±2 for two dots). 0 if no marker.\n'
    '- dur: w/h/q/e/s = whole/half/quarter/eighth/sixteenth.\n'
    '- dots: 1 if literal "." follows digit, else 0.\n'
    "\n"
    "Header:\n"
    "- Key: Extract from '1=X'. Output {\"key\":\"X\"}. Absent → \"C\".\n"
    "- Time: Stacked fraction; read both numerator digits.\n"
    "- Tempo: Usually on page. Absent → 120.\n"
    "\n"
    "Rules:\n"
    "- '|' separates measures. '||' or final bar = end.\n"
    "- A measure showing only '0' (often with dashes, e.g. '0 - -') is a FULL-MEASURE REST. You MUST output it as a measure containing one rest note (p:'r'). NEVER skip or merge a rest measure — it keeps measures aligned.\n"
    "- Output EVERY measure between bar lines, left to right, even short or rest-only ones. Do not drop measures.\n"
    "- A bare digit is a quarter in ALL meters; do NOT default to eighth just because the meter is 6/8 or 12/8.\n"
    "- Ignore: lyrics, measure numbers, fingerings, breath marks.\n"
    "- Read left-to-right, top-to-bottom.\n"
    "- After LAST measure write ']}' and STOP."
)

# Row-mode prompt: same as FULL minus the Header section. Used for rows 2..N
# after row-splitting; caller takes header from row 1 only and concatenates the
# rest. Shorter prompt → fewer input tokens → more output budget per row.
# Also applies Variant A improvements (explicit jianpu digit rule).
_USER_PROMPT_ROW = (
    "This image shows ONE row of a jianpu score. Transcribe ONLY the measures.\n"
    'Output exactly: {"measures":[[...],[...]]}  (no time_signature/key fields).\n'
    "\n"
    "CRITICAL: Read jianpu digits 1-7 EXACTLY AS WRITTEN. Never transpose by key.\n"
    "Digit '1' on page → {\"p\":\"1\"}  (not shifted)\n"
    "Digit '2' on page → {\"p\":\"2\"}  (not shifted)\n"
    "...and so on for 3,4,5,6,7\n"
    "\n"
    "STRUCTURE: \"measures\" is a list of MEASURES. Each measure is a list of NOTE objects.\n"
    "  CORRECT:  \"measures\":[[{...},{...}],[{...}]]            ← outer=measures, inner=notes\n"
    "  WRONG:    \"measures\":[{...},{...}]                      ← flat (no measure grouping)\n"
    "  WRONG:    \"measures\":[{\"notes\":[...]},{\"notes\":[...]}]   ← do NOT wrap notes in objects\n"
    "Example for 2 measures (measures only, no header):\n"
    '{"measures":[[{"p":"5","oct":0,"dur":"q","dots":0},{"p":"3","oct":0,"dur":"q","dots":0},'
    '{"p":"1","oct":0,"dur":"h","dots":0}],'
    '[{"p":"r","oct":0,"dur":"q","dots":0},{"p":"6","oct":-1,"dur":"e","dots":0},'
    '{"p":"7","oct":-1,"dur":"e","dots":1}]]}\n'
    "Note fields:\n"
    '- p: "1"-"7" digit EXACTLY AS SEEN. "0" → "r". Accidentals: "#"/"b".\n'
    '- oct: ONLY a dot vertically centered ABOVE the digit=+1, centered BELOW=-1, none=0 (±2 for two dots).\n'
    '       A dot to the RIGHT (e.g. "4.") or an UNDERLINE is a DURATION mark, NOT an octave change → oct stays 0.\n'
    '       HIGH: "i"="1"+1, LOW: digit with dot underneath=-1, STANDARD: "1"=0\n'
    '- dur: w/h/q/e/s. A bare digit (no underline, no dash) is ALWAYS quarter "q", in EVERY meter (incl. 6/8, 12/8).\n'
    '       One underline=eighth "e", two underlines=sixteenth "s".\n'
    '- dots: 1 if a duration dot "." follows the digit on the same line, else 0.\n'
    "Rules:\n"
    "- '|' separates measures. Read every digit left-to-right between bars.\n"
    "- A measure showing only '0' (often with dashes, e.g. '0 - -') is a FULL-MEASURE REST → output a measure with one rest note (p:'r'). NEVER skip or merge it.\n"
    "- Output EVERY measure between bar lines, even rest-only ones. Do not drop measures.\n"
    "- Ignore lyrics, measure-number superscripts, fingerings, breath marks.\n"
    "- After the LAST measure write ']}' and STOP."
)

# Default prompt for backward-compat single-pass entries
_USER_PROMPT = _USER_PROMPT_FULL


def get_vlm(model_path: Path, mmproj_path: Path) -> 'Llama':  # type: ignore[name-defined]
    """Return (and cache) the Llama singleton. Reloads if model_path changed."""
    global _vlm, _vlm_model_path
    if not _LLAMA_AVAILABLE:
        raise RuntimeError(
            'llama-cpp-python 未安装。\n'
            '安装命令（已安装 v0.3.23 cu124，若重装请用）：\n'
            'pip install llama-cpp-python==0.3.23 '
            '--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124'
        )
    with _vlm_lock:
        if _vlm is None or _vlm_model_path != model_path:
            _LOG.info(f'[VLM] 加载模型中: {model_path.name}（首次约 10–30 秒）')
            chat_handler = Qwen25VLChatHandler(clip_model_path=str(mmproj_path))
            _vlm = Llama(
                model_path=str(model_path),
                chat_handler=chat_handler,
                # -1=全GPU；n_batch=32已防TDR；部分卸载(n_gpu_layers=20)会导致CUDA buffer mismatch
                n_gpu_layers=-1,
                # KV cache 458 MB；本次实测输入2606 token，需4096才能容纳+输出空间
                n_ctx=4096,
                # 32: 每次GPU突发运算量小，不超过Windows 2秒TDR限制（防蓝屏的关键）
                n_batch=32,
                offload_kqv=True,
                verbose=False,
            )
            _vlm_model_path = model_path
            _LOG.info('[VLM] 模型加载完成')
        return _vlm


def release_vlm() -> None:
    """Release the cached Llama instance and free GPU memory immediately."""
    import gc
    global _vlm, _vlm_model_path
    with _vlm_lock:
        if _vlm is not None:
            # 调用 Llama 析构会释放 llama_context (含 GPU KV cache、模型权重 buffers)
            try:
                _vlm.close() if hasattr(_vlm, 'close') else None
            except Exception:
                pass
            _vlm = None
        _vlm_model_path = None
    gc.collect()    # 强制 CPython 立即销毁对象，触发 llama-cpp-python 的 C++ 析构
    _LOG.info('[VLM] 模型已释放（GPU 显存归还）')


_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB 警告阈值
_VLM_MAX_DIM = 1400       # 1400÷14=100 patch/side（更高分辨率，便于识别"i"字符上的小点）
_CROP_MARGIN_PX = 16      # 裁剪后保留的白边
_CROP_THRESHOLD = 240     # 灰度像素低于此值视为内容（0=黑, 255=白）
_MIN_CONTENT_RATIO = 0.02 # 裁剪后内容像素占比下限，太小则跳过裁剪（防误裁纯色图）
_ROW_GAP_MIN_PX = 24      # 行间空白超过此像素视为换行分隔
_ROW_PIXEL_THRESHOLD = 5  # 一行内深色像素数低于此值视为空行
_CHUNK_MEASURES = 3       # 每个识别块包含的小节数（宽行按竖线切块，降低单次推理密度）
_CHUNK_TARGET_H = 150     # 切块后放大到的目标高度（px），让数字/八度点清晰可见
_CHUNK_X_MARGIN = 6       # 切块左右保留的像素余量，避免裁掉竖线或边缘字符


def _autocrop_content(img: 'Image.Image') -> 'Image.Image':  # type: ignore[name-defined]
    """裁剪到非空白内容的紧凑边界框 + 余量。

    简谱通常打印在 A4 顶部，下方大片空白会让视觉 token 浪费在白纸上，
    每个数字字符在缩放后只剩几个像素。裁掉空白后再缩放，字符像素提升 4-10x。
    """
    from PIL import Image, ImageChops
    # 转灰度，反相后用 getbbox 找非零区域
    gray = img.convert('L')
    # 把"足够白"的像素映射到 0，其它保留 → bbox 自动定位内容
    mask = gray.point(lambda v: 0 if v >= _CROP_THRESHOLD else 255)
    bbox = mask.getbbox()
    if bbox is None:
        return img   # 全白，不裁

    w, h = img.size
    x0, y0, x1, y1 = bbox
    # 内容面积过小（如纯色图、单点噪声）→ 不裁
    if (x1 - x0) * (y1 - y0) < _MIN_CONTENT_RATIO * w * h:
        return img

    # 加余量，clamp 到图像范围
    x0 = max(0, x0 - _CROP_MARGIN_PX)
    y0 = max(0, y0 - _CROP_MARGIN_PX)
    x1 = min(w, x1 + _CROP_MARGIN_PX)
    y1 = min(h, y1 + _CROP_MARGIN_PX)
    return img.crop((x0, y0, x1, y1))


def _split_rows(img: 'Image.Image') -> list[tuple[int, int]]:  # type: ignore[name-defined]
    """通过水平空白带检测分行，返回每行的 (y0, y1) 像素边界。

    输入应已 _autocrop_content 过的紧凑图。
    标题、署名等水平跨度 < 50% 的窄行会被过滤掉，避免把它们当成简谱内容行去识别。
    若返回单个区间 = 单行简谱；多个区间 = 多行需要逐行识别。
    """
    from PIL import Image
    gray = img.convert('L')
    arr_bytes = gray.tobytes()
    w, h = gray.size
    # row_density[y] = 该行深色像素数
    density = [0] * h
    for y in range(h):
        row = arr_bytes[y * w:(y + 1) * w]
        density[y] = sum(1 for b in row if b < _CROP_THRESHOLD)

    # 找连续的"有内容"区间，相邻区间之间空白带 >= _ROW_GAP_MIN_PX 才算分行
    rows: list[tuple[int, int]] = []
    in_row = False
    start = 0
    last_content_y = -1
    for y in range(h):
        if density[y] > _ROW_PIXEL_THRESHOLD:
            if not in_row:
                if rows and (y - last_content_y) < _ROW_GAP_MIN_PX:
                    start = rows.pop()[0]
                else:
                    start = y
                in_row = True
            last_content_y = y
        else:
            if in_row and (y - last_content_y) >= _ROW_GAP_MIN_PX:
                rows.append((max(0, start - 4), min(h, last_content_y + 4)))
                in_row = False
    if in_row:
        rows.append((max(0, start - 4), min(h, last_content_y + 4)))

    # 过滤窄行（标题/署名/版权信息）: 水平内容跨度 < 50% 图宽则丢弃
    content_rows: list[tuple[int, int]] = []
    for y0, y1 in rows:
        min_x, max_x = w, 0
        for y in range(y0, y1):
            row = arr_bytes[y * w:(y + 1) * w]
            for x in range(w):
                if row[x] < _CROP_THRESHOLD:
                    if x < min_x:
                        min_x = x
                    if x > max_x:
                        max_x = x
        span = (max_x - min_x) / w if max_x > min_x else 0
        if span >= 0.5:
            content_rows.append((y0, y1))
        else:
            _LOG.debug('[VLM] 跳过窄行 y=%d-%d (跨度仅 %.0f%% 图宽)', y0, y1, span * 100)

    return content_rows


def _detect_barlines(row_img: 'Image.Image') -> list[int]:  # type: ignore[name-defined]
    """检测一行简谱内的小节竖线 x 坐标，返回升序的竖线中心列表。

    竖线特征：又高(接近内容满高)、又窄(≤5px)、左右被空白包夹。
    这三个条件共同把竖线与数字"1"/"i"等细长字形区分开。
    用于把宽行切成「每块若干小节」的小图——降低单次推理密度，
    并配合放大让八度小圆点清晰可见。
    """
    g = row_img.convert('L')
    w, h = g.size
    px = g.load()
    # 每列最长连续深色竖直游程
    colmax = [0] * w
    for x in range(w):
        run = 0
        mx = 0
        for y in range(h):
            if px[x, y] < 128:
                run += 1
                if run > mx:
                    mx = run
            else:
                run = 0
        colmax[x] = mx
    content_h = max(colmax) if colmax else 0
    if content_h <= 0:
        return []

    # 候选：游程达内容满高 80% 的列，按相邻聚成组
    cand = [x for x in range(w) if colmax[x] >= 0.8 * content_h]
    groups: list[list[int]] = []
    for x in cand:
        if groups and x - groups[-1][-1] <= 3:
            groups[-1].append(x)
        else:
            groups.append([x])

    def _is_white_col(x: int) -> bool:
        return 0 <= x < w and all(px[x, y] >= 128 for y in range(h))

    def _white_count(a: int, b: int) -> int:
        return sum(1 for x in range(max(0, a), min(w, b)) if _is_white_col(x))

    bars: list[int] = []
    for gp in groups:
        if gp[-1] - gp[0] + 1 > 5:   # 太宽 → 是数字而非竖线
            continue
        cx = (gp[0] + gp[-1]) // 2
        # 左右各有空白列包夹
        if _white_count(cx - 7, cx - 2) >= 2 and _white_count(cx + 3, cx + 8) >= 2:
            if not bars or cx - bars[-1] >= 30:   # 强制最小间距，避免相邻笔画误判
                bars.append(cx)
    return bars


def _upscale_to_height(img: 'Image.Image', target_h: int,  # type: ignore[name-defined]
                       max_w: int = None) -> 'Image.Image':  # type: ignore[name-defined]
    """把小图等比放大到目标高度，宽度不超过 max_w（超出则按宽度回退）。

    简谱行裁出来通常只有 ~50px 高，八度小圆点仅 2-4px、几乎不可见。
    切成窄块后放大，数字和上下八度点才足够清晰。
    """
    from PIL import Image
    max_w = max_w or _VLM_MAX_DIM
    w, h = img.size
    if h <= 0 or w <= 0:
        return img
    scale = target_h / h
    if w * scale > max_w:
        scale = max_w / w
    if scale <= 1.0:
        return img
    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)


def _resize_to_max_dim(img: 'Image.Image', max_dim: int = None) -> 'Image.Image':  # type: ignore[name-defined]
    """长边等比缩放到 max_dim 以内。"""
    from PIL import Image
    max_dim = max_dim or _VLM_MAX_DIM
    w, h = img.size
    if max(w, h) <= max_dim:
        return img
    scale = max_dim / max(w, h)
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def _resize_image(image_path: Path) -> tuple[Path, bool]:
    """裁剪空白 + 等比缩放，返回 (路径, 是否生成了临时文件)。"""
    from PIL import Image
    img = Image.open(image_path)
    orig_w, orig_h = img.size

    cropped = _autocrop_content(img)
    crop_w, crop_h = cropped.size
    cropped_changed = (crop_w, crop_h) != (orig_w, orig_h)

    # 缩放：长边压到 _VLM_MAX_DIM 以内
    if max(crop_w, crop_h) > _VLM_MAX_DIM:
        scale = _VLM_MAX_DIM / max(crop_w, crop_h)
        final = cropped.resize((int(crop_w * scale), int(crop_h * scale)), Image.LANCZOS)
        resized_changed = True
    else:
        final = cropped
        resized_changed = False

    if not (cropped_changed or resized_changed):
        return image_path, False

    _LOG.info('[VLM] 图像预处理 原%dx%d → 裁剪%dx%d → 最终%dx%d',
              orig_w, orig_h, crop_w, crop_h, final.size[0], final.size[1])

    import os
    fd, tmp = tempfile.mkstemp(suffix='.png')
    os.close(fd)
    final.save(tmp)
    return Path(tmp), True


def _image_to_data_url(image_path: Path) -> str:
    mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}.get(
        image_path.suffix.lower(), 'image/png'
    )
    data = base64.b64encode(image_path.read_bytes()).decode()
    return f'data:{mime};base64,{data}'


def _normalize_measures(data: dict) -> dict:
    """修正模型常见结构错误。"""
    if not isinstance(data, dict):
        return data

    # key 字段可能带 "1=" 前缀，剥离
    key = data.get('key')
    if isinstance(key, str) and key.upper().startswith('1='):
        data['key'] = key[2:].strip() or 'C'

    measures = data.get('measures')
    if not isinstance(measures, list) or not measures:
        return data

    # 形式 A: measures = [{notes:[...]}, {notes:[...]}, ...]  外层缺少嵌套
    if all(isinstance(m, dict) and isinstance(m.get('notes'), list) for m in measures):
        _LOG.warning('[VLM] 检测到 {"notes":...} 包装结构，提取 notes 列表')
        data['measures'] = [m['notes'] for m in measures]
        return data

    # 形式 B: measures = [[{notes:[...]}, {notes:[...]}, ...]]  双层包裹
    if (len(measures) == 1 and isinstance(measures[0], list)
            and all(isinstance(m, dict) and isinstance(m.get('notes'), list)
                    for m in measures[0])):
        _LOG.warning('[VLM] 检测到嵌套 {"notes":...} 包装结构，展平')
        data['measures'] = [m['notes'] for m in measures[0]]
        return data

    # 形式 C: 扁平 — measures 元素全是 note dict 而非 measure list
    if all(isinstance(m, dict) for m in measures):
        _LOG.warning('[VLM] 检测到扁平 measures（%d 个音符），自动包成单一 measure', len(measures))
        data['measures'] = [measures]
        return data

    return data


def _parse_response(content: str) -> dict:
    """Extract JSON from model response, with truncation recovery fallback."""
    content = content.strip()
    if '```' in content:
        inner = content.split('```')[1]
        inner = inner.lstrip('json').strip()
        start = inner.find('{')
        end = inner.rfind('}')
        if start != -1 and end != -1:
            content = inner[start:end + 1]

    try:
        return _normalize_measures(json.loads(content))
    except json.JSONDecodeError:
        pass

    # 截断恢复：从不完整 JSON 中提取已完成的小节
    recovered = _recover_truncated(content)
    if recovered is not None:
        recovered = _normalize_measures(recovered)   # 恢复出来的也可能是 {notes:...} 包装
        _LOG.warning('[VLM] JSON 截断，已恢复 %d 个小节', len(recovered.get('measures', [])))
        return recovered

    raise RuntimeError(
        f'VLM 返回内容无法解析为 JSON\n内容片段: {content[:300]}'
    )


def _recover_truncated(content: str) -> dict | None:
    """从被截断的 JSON 字符串中提取元数据和完整小节列表。线性扫描，O(n)。"""
    import re
    result: dict = {'time_signature': '4/4', 'key': 'C', 'tempo': 120, 'measures': []}

    for key, pattern in [
        ('time_signature', r'"time_signature"\s*:\s*"([^"]+)"'),
        ('key',            r'"key"\s*:\s*"([^"]+)"'),
    ]:
        m = re.search(pattern, content)
        if m:
            result[key] = m.group(1)

    m = re.search(r'"tempo"\s*:\s*(\d+)', content)
    if m:
        result['tempo'] = int(m.group(1))

    measures_pos = content.find('"measures"')
    if measures_pos == -1:
        return None

    tail = content[measures_pos:]
    outer = tail.find('[')   # measures 数组的外层 [
    if outer == -1:
        return None

    # 探测嵌套 vs 扁平：跳过空白后看第一个非空字符是 [ (嵌套) 还是 { (扁平)
    probe = outer + 1
    n = len(tail)
    while probe < n and tail[probe] in ' \t\n\r':
        probe += 1
    if probe >= n:
        return None

    flat = (tail[probe] == '{')
    notes_flat: list = []

    # 线性扫描，用括号/引号状态机提取完整的 {...} (扁平) 或 [...] (嵌套)
    pos = outer + 1
    open_char = '{' if flat else '['
    close_char = '}' if flat else ']'

    while pos < n:
        while pos < n and tail[pos] in ' \t\n\r,':
            pos += 1
        if pos >= n or tail[pos] != open_char:
            break
        depth = 0
        start = pos
        in_str = False
        escape = False
        while pos < n:
            ch = tail[pos]
            if escape:
                escape = False
            elif in_str:
                if ch == '\\':
                    escape = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == open_char:
                depth += 1
            elif ch == close_char:
                depth -= 1
                if depth == 0:
                    chunk_str = tail[start:pos + 1]
                    try:
                        obj = json.loads(chunk_str)
                        if flat and isinstance(obj, dict):
                            notes_flat.append(obj)
                        elif (not flat) and isinstance(obj, list):
                            result['measures'].append(obj)
                    except json.JSONDecodeError:
                        pass
                    pos += 1
                    break
            pos += 1
        else:
            break   # 未平衡，截断

    # 扁平格式：所有音符包成一个小节
    if flat and notes_flat:
        _LOG.warning('[VLM] 截断的扁平 measures，包成单一 measure (%d 音符)', len(notes_flat))
        result['measures'] = [notes_flat]

    if not result['measures']:
        return None
    return result


def _run_vlm_on_image_bytes(image_url: str, vlm: 'Llama',  # type: ignore[name-defined]
                              on_progress: Optional[object] = None,
                              user_prompt: str = _USER_PROMPT_FULL) -> dict:
    """对一张 base64 编码图片跑一次推理，返回 _parse_response 后的 dict。"""
    import time
    _done = threading.Event()

    def _heartbeat() -> None:
        start = time.monotonic()
        while not _done.wait(timeout=10):
            elapsed = int(time.monotonic() - start)
            if on_progress:
                try:
                    on_progress(elapsed)   # type: ignore[operator]
                except Exception:
                    pass

    hb = threading.Thread(target=_heartbeat, daemon=True)
    hb.start()

    try:
        response = vlm.create_chat_completion(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": user_prompt},
                    ],
                },
            ],
            temperature=0.1,
            repeat_penalty=1.15,
            max_tokens=1500,
            stream=False,
        )
    finally:
        _done.set()

    if isinstance(response, dict):
        content = response["choices"][0]["message"]["content"]
    else:
        raise RuntimeError(f'VLM 返回类型异常: {type(response).__name__}')

    _LOG.info('[VLM] raw response (%d chars): %s', len(content), repr(content[:300]))
    parsed = _parse_response(content)
    measures = parsed.get('measures', []) if isinstance(parsed, dict) else []
    _LOG.info('[VLM] parsed: n_measures=%d', len(measures))
    return parsed


def _pil_to_data_url(img: 'Image.Image') -> str:  # type: ignore[name-defined]
    """把 PIL 图片转为 base64 data URL（PNG）。"""
    import io
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    data = base64.b64encode(buf.getvalue()).decode()
    return f'data:image/png;base64,{data}'


def _merge_row_results(rows_data: list[dict]) -> dict:
    """合并按行识别的结果。元数据取第一个非空行；measures 顺序拼接。"""
    merged: dict = {'time_signature': '4/4', 'key': 'C', 'tempo': 120, 'measures': []}
    for row in rows_data:
        if not isinstance(row, dict):
            continue
        # 取第一个有效的元数据
        for k in ('time_signature', 'key', 'tempo'):
            v = row.get(k)
            if v and merged[k] in ('4/4', 'C', 120):
                merged[k] = v
        ms = row.get('measures', [])
        if isinstance(ms, list):
            for m in ms:
                if isinstance(m, list) and m:   # 跳过空小节
                    merged['measures'].append(m)
    return merged


def recognize_image(
    image_path: Path,
    model_path: Path,
    mmproj_path: Path,
    on_progress: Optional[object] = None,
) -> dict:
    """Run VLM inference on one image (auto row-split for multi-row jianpu).

    Strategy:
      1. Open + autocrop image to content
      2. Detect rows via horizontal whitespace gaps
      3. If >1 row, run inference per row and concatenate measures
      4. Otherwise fall back to single-pass inference
    """
    from PIL import Image

    vlm = get_vlm(model_path, mmproj_path)
    img = Image.open(image_path)
    cropped = _autocrop_content(img)
    rows = _split_rows(cropped)

    # 过滤掉太矮的行（标题、注释等）— 真实简谱行至少 30px
    rows = [(y0, y1) for (y0, y1) in rows if (y1 - y0) >= 30]
    _LOG.info('[VLM] 检测到 %d 个内容行（裁剪后 %dx%d）',
              len(rows), cropped.size[0], cropped.size[1])

    if len(rows) <= 1:
        # 单行（或无明显分行）：整图直接送，用完整 prompt 提取 header
        final = _resize_to_max_dim(cropped)
        image_url = _pil_to_data_url(final)
        try:
            return _run_vlm_on_image_bytes(image_url, vlm, on_progress,
                                            user_prompt=_USER_PROMPT_FULL)
        except AttributeError as exc:
            import traceback
            _LOG.error('[VLM] 推理 AttributeError:\n%s', traceback.format_exc())
            raise RuntimeError(f'VLM 推理失败 (AttributeError)：{exc}') from exc

    # 多行：先把每行按小节竖线切成「每块 _CHUNK_MEASURES 个小节」的窄块，
    # 放大后逐块识别。这样每次推理只面对少数小节（避免密集行触发幻觉/循环），
    # 且放大让八度小圆点清晰（修复行从不放大导致的八度漏检）。
    # 整张图的第一个块用 FULL prompt 抓 header，其余块用 ROW prompt。
    chunk_boxes: list[tuple[int, int, int, int]] = []   # 每块 (x0, y0, x1, y1)
    W = cropped.size[0]
    for (y0, y1) in rows:
        ry0 = max(0, y0 - 6)
        ry1 = min(cropped.size[1], y1 + 6)
        row_img = cropped.crop((0, ry0, W, ry1))
        bars = _detect_barlines(row_img)
        if len(bars) < 2:
            # 检测不到足够竖线 → 整行作为一个块（回退到原行级行为）
            chunk_boxes.append((0, ry0, W, ry1))
            continue
        edges = [0] + bars            # n=len(bars) 个小节: (edges[i], edges[i+1])
        n_meas = len(bars)
        for start in range(0, n_meas, _CHUNK_MEASURES):
            end = min(start + _CHUNK_MEASURES, n_meas)
            x0 = max(0, edges[start] - _CHUNK_X_MARGIN)
            x1 = min(W, edges[end] + _CHUNK_X_MARGIN)
            if x1 - x0 < 8:
                continue
            chunk_boxes.append((x0, ry0, x1, ry1))

    _LOG.info('[VLM] %d 行 → 切成 %d 个识别块（每块约 %d 小节）',
              len(rows), len(chunk_boxes), _CHUNK_MEASURES)

    chunks_data: list[dict] = []
    total = len(chunk_boxes)
    for ci, (x0, y0c, x1, y1c) in enumerate(chunk_boxes):
        chunk_img = cropped.crop((x0, y0c, x1, y1c))
        chunk_img = _upscale_to_height(chunk_img, _CHUNK_TARGET_H)
        prompt = _USER_PROMPT_FULL if ci == 0 else _USER_PROMPT_ROW
        _LOG.info('[VLM] 识别块 %d/%d (尺寸 %dx%d, prompt=%s)…',
                  ci + 1, total, chunk_img.size[0], chunk_img.size[1],
                  'full' if prompt is _USER_PROMPT_FULL else 'row')

        def _chunk_progress(elapsed, idx=ci + 1):
            if on_progress:
                try:
                    on_progress(elapsed * 100 + idx)
                except Exception:
                    pass

        try:
            image_url = _pil_to_data_url(chunk_img)
            chunk_result = _run_vlm_on_image_bytes(image_url, vlm, _chunk_progress,
                                                    user_prompt=prompt)
            chunks_data.append(chunk_result)
        except Exception as exc:
            _LOG.warning('[VLM] 块 %d 识别失败: %s', ci + 1, exc)
            chunks_data.append({'measures': []})

    merged = _merge_row_results(chunks_data)
    _LOG.info('[VLM] 多块合并完成: 共 %d 小节', len(merged['measures']))
    return merged


def recognize_pdf(pdf_path: Path, model_path: Path, mmproj_path: Path) -> dict:
    """Recognize a PDF: render each page → recognize_image → merge measures.

    Pages that fail recognition are skipped with a warning log.
    Returns a merged dict using the first page's time/key/tempo metadata.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise RuntimeError('PyMuPDF (fitz) 未安装，无法渲染 PDF 页面。')

    get_vlm(model_path, mmproj_path)   # 提前验证模型可用，PDF 渲染前即失败

    doc = fitz.open(str(pdf_path))
    all_measures: list = []
    meta = {'time_signature': '4/4', 'key': 'C', 'tempo': 120}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for page_no in range(doc.page_count):
                page = doc[page_no]
                mat = fitz.Matrix(2.0, 2.0)   # 2× upscale for legibility
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img_path = Path(tmp) / f'page_{page_no:03d}.png'
                pix.save(str(img_path))
                try:
                    result = recognize_image(img_path, model_path, mmproj_path)
                    if page_no == 0:
                        meta['time_signature'] = result.get('time_signature', '4/4')
                        meta['key'] = result.get('key', 'C')
                        meta['tempo'] = result.get('tempo', 120)
                    all_measures.extend(result.get('measures', []))
                except Exception as exc:
                    _LOG.warning(f'[VLM] 第 {page_no + 1} 页识别失败，跳过: {exc}')
    finally:
        doc.close()
    return {**meta, 'measures': all_measures}
