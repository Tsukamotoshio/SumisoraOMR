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
    "OCTAVE MARKERS (critical for high/low notes):\n"
    "- Dot ABOVE digit → octave +1 (high); two dots → +2 (very high)\n"
    "- Dot BELOW digit → octave -1 (low); two dots → -2 (very low)\n"
    "- NO dot/marker → octave 0 (middle, normal)\n"
    "- '1' with dot above may render as 'i' or 'í' → {\"p\":\"1\",\"oct\":1}\n"
    "\n"
    "DURATION NOTATION:\n"
    "- No underline = quarter 'q' (in 4/4 time)\n"
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
    "- Default: 'e' in 6/8/12/8; 'q' in 4/4/3/4.\n"
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
    '- oct: Dot ABOVE = +1, BELOW = -1, none = 0. (±2 for two dots)\n'
    '- dur: w/h/q/e/s = whole/half/quarter/eighth/16th. "-" extends.\n'
    '- dots: 1 if "." after digit, else 0.\n'
    "Rules:\n"
    "- '|' separates measures. Read every digit left-to-right between bars.\n"
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

    # 多行：第一行用 FULL prompt 抓 header；其余行用 ROW prompt（更短更专注，准确率更高）
    rows_data: list[dict] = []
    for i, (y0, y1) in enumerate(rows):
        row_img = cropped.crop((0, max(0, y0 - 8), cropped.size[0], min(cropped.size[1], y1 + 8)))
        row_img = _resize_to_max_dim(row_img)
        prompt = _USER_PROMPT_FULL if i == 0 else _USER_PROMPT_ROW
        _LOG.info('[VLM] 识别第 %d/%d 行 (尺寸 %dx%d, prompt=%s)…',
                  i + 1, len(rows), row_img.size[0], row_img.size[1],
                  'full' if prompt is _USER_PROMPT_FULL else 'row')

        def _row_progress(elapsed, idx=i + 1, total=len(rows)):
            if on_progress:
                try:
                    on_progress(elapsed * 100 + idx)
                except Exception:
                    pass

        try:
            image_url = _pil_to_data_url(row_img)
            row_result = _run_vlm_on_image_bytes(image_url, vlm, _row_progress,
                                                  user_prompt=prompt)
            rows_data.append(row_result)
        except Exception as exc:
            _LOG.warning('[VLM] 第 %d 行识别失败: %s', i + 1, exc)
            rows_data.append({'measures': []})

    merged = _merge_row_results(rows_data)
    _LOG.info('[VLM] 多行合并完成: 共 %d 小节', len(merged['measures']))
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
