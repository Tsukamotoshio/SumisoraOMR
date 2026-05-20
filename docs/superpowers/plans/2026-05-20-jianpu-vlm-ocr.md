# Jianpu VLM OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an experimental "简谱识别" feature: jianpu images/PDFs → VLM (Qwen2-VL-7B GGUF) → JSON → MusicXML → auto-navigate to score preview page.

**Architecture:** Two-stage pipeline in-process: `jianpu_recognizer.py` calls a llama-cpp-python `Llama` singleton (lazy-loaded on first use, released on page leave) to produce a structured JSON note list; `json_to_musicxml.py` converts that deterministically to `.mxl` via music21. The GUI adds a new sub-page (`jianpu_ocr_page.py`) reachable from a new button on the landing page.

**Tech Stack:** Python, llama-cpp-python (CUDA), Qwen2-VL-7B-Instruct GGUF + mmproj GGUF, music21 (existing dep), PyMuPDF/fitz (existing dep), Flet (existing dep).

**Branch:** `exp/jianpu-vlm-ocr`

---

## Pre-flight: verify model filenames on HuggingFace

Before starting Task 1, open https://huggingface.co/Qwen/Qwen2-VL-7B-Instruct-GGUF/tree/main and confirm the exact filenames for:
- The Q4_K_M main model (expected pattern: `qwen2_vl-7b-instruct-q4_k_m.gguf`)
- The multimodal projector file (expected pattern: `mmproj-qwen2_vl-7b-instruct-f16.gguf`)

Update the constants in Task 1 if the actual names differ.

---

## Task 1: Foundation — config, backend, AppState, events

**Files:**
- Modify: `core/config.py`
- Modify: `core/app/backend.py`
- Modify: `gui/app_state.py`
- Create: `core/vlm/__init__.py`

---

- [ ] **Step 1: Add VLM constants to `core/config.py`**

At the end of `core/config.py`, after the `JianpuNote` dataclass, add:

```python
# ── VLM (Qwen2-VL) model constants ───────────────────────────────────────────
VLM_MODEL_DIR_NAME   = 'vlm'
VLM_MODEL_FILENAME   = 'qwen2_vl-7b-instruct-q4_k_m.gguf'
VLM_MMPROJ_FILENAME  = 'mmproj-qwen2_vl-7b-instruct-f16.gguf'
# SHA256 hashes — fill in after first successful download (certutil -hashfile <file> SHA256)
# Empty string = skip hash verification (acceptable for experimental feature)
VLM_MODEL_HASH       = ''
VLM_MMPROJ_HASH      = ''
VLM_WEIGHT_BASE_URLS = [
    'https://modelscope.cn/models/Qwen/Qwen2-VL-7B-Instruct-GGUF/resolve/master/',
    'https://huggingface.co/Qwen/Qwen2-VL-7B-Instruct-GGUF/resolve/main/',
]
```

- [ ] **Step 2: Add directory helpers to `core/app/backend.py`**

After the `models_dir()` function (line 23), add:

```python
def jianpu_input_dir() -> Path:
    """Input directory for jianpu images/PDFs. Not auto-created; caller does mkdir."""
    return app_base_dir() / 'jianpu-Input'


def vlm_models_dir() -> Path:
    """VLM (Qwen2-VL) GGUF weights directory (created on demand)."""
    path = app_base_dir() / 'models' / 'vlm'
    path.mkdir(parents=True, exist_ok=True)
    return path
```

- [ ] **Step 3: Add two events to `gui/app_state.py`**

In the `Event` class (lines 17–35), add after `MODELS_DOWNLOADED`:

```python
    JIANPU_OCR_REQUESTED = 'jianpu_ocr_requested'  # landing button → show OCR page
    JIANPU_OCR_DONE      = 'jianpu_ocr_done'        # OCR complete → show score_preview
```

- [ ] **Step 4: Add `vlm_available` field to `AppState` dataclass**

In `gui/app_state.py`, inside `AppState`, after `homr_available: bool = False` (line 75), add:

```python
    vlm_available:  bool = False
```

- [ ] **Step 5: Create `core/vlm/__init__.py`**

```python
# core/vlm — VLM-based jianpu OCR engine (experimental)
```

- [ ] **Step 6: Verify imports work**

```
python -c "from core.config import VLM_MODEL_FILENAME, VLM_MMPROJ_FILENAME, VLM_WEIGHT_BASE_URLS; print('OK', VLM_MODEL_FILENAME)"
python -c "from core.app.backend import jianpu_input_dir, vlm_models_dir; print('OK')"
python -c "from gui.app_state import Event; print(Event.JIANPU_OCR_REQUESTED, Event.JIANPU_OCR_DONE)"
```

Expected:
```
OK qwen2_vl-7b-instruct-q4_k_m.gguf
OK
jianpu_ocr_requested jianpu_ocr_done
```

- [ ] **Step 7: Commit**

```bash
git add core/config.py core/app/backend.py gui/app_state.py core/vlm/__init__.py
git commit -m "feat(vlm): add VLM config constants, backend helpers, and app_state fields"
```

---

## Task 2: GGUF Downloader (`core/vlm/gguf_downloader.py`)

**Files:**
- Create: `core/vlm/gguf_downloader.py`
- Create: `tests/test_vlm_downloader.py`

---

- [ ] **Step 1: Write failing tests**

Create `tests/__init__.py` (empty) and `tests/test_vlm_downloader.py`:

```python
import hashlib
import tempfile
from pathlib import Path

# tests/test_vlm_downloader.py


def test_build_url_modelscope():
    from core.vlm.gguf_downloader import _build_url
    base = 'https://modelscope.cn/models/Qwen/Qwen2-VL-7B-Instruct-GGUF/resolve/master/'
    assert _build_url(base, 'model.gguf') == base + 'model.gguf'


def test_build_url_trailing_slash_stripped():
    from core.vlm.gguf_downloader import _build_url
    base = 'https://example.com/path/'
    assert _build_url(base, 'file.gguf') == 'https://example.com/path/file.gguf'


def test_verify_sha256_empty_skips():
    from core.vlm.gguf_downloader import verify_sha256
    with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
        f.write(b'test')
        tmp = Path(f.name)
    assert verify_sha256(str(tmp), '') is True
    tmp.unlink()


def test_verify_sha256_correct():
    from core.vlm.gguf_downloader import verify_sha256
    data = b'hello world'
    expected = hashlib.sha256(data).hexdigest()
    with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
        f.write(data)
        tmp = Path(f.name)
    assert verify_sha256(str(tmp), expected) is True
    tmp.unlink()


def test_verify_sha256_wrong():
    from core.vlm.gguf_downloader import verify_sha256
    with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
        f.write(b'hello world')
        tmp = Path(f.name)
    assert verify_sha256(str(tmp), 'deadbeef' * 8) is False
    tmp.unlink()
```

- [ ] **Step 2: Run tests — expect FAIL (ImportError)**

```
python -m pytest tests/test_vlm_downloader.py -v
```

Expected: `ImportError: cannot import name '_build_url' from 'core.vlm.gguf_downloader'` (module doesn't exist yet)

- [ ] **Step 3: Create `core/vlm/gguf_downloader.py`**

```python
# core/vlm/gguf_downloader.py — Qwen2-VL GGUF weight download orchestrator
# Downloads two files: main model GGUF + multimodal projector (mmproj) GGUF.
# Mirrors the pattern of core/omr/homr_downloader.py.
from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

import requests

from core.config import (
    VLM_WEIGHT_BASE_URLS,
    VLM_MODEL_FILENAME,
    VLM_MODEL_HASH,
    VLM_MMPROJ_FILENAME,
    VLM_MMPROJ_HASH,
)

_PROBE_TIMEOUT_SEC = 5.0
_VLM_FILES   = [VLM_MMPROJ_FILENAME, VLM_MODEL_FILENAME]   # mmproj first (smaller → probe target)
_VLM_HASHES  = {VLM_MODEL_FILENAME: VLM_MODEL_HASH, VLM_MMPROJ_FILENAME: VLM_MMPROJ_HASH}

# Progress callback: (file_index, filename, bytes_done, file_total,
#                     overall_done, overall_total, total_files)
ProgressCallback = Callable[[int, str, int, int, int, int, int], None]


class DownloadCancelled(Exception):
    """Raised when cancel_event is set mid-transfer."""


class HashMismatch(Exception):
    """SHA256 of downloaded file did not match expected hash."""


class NoSourceAvailable(Exception):
    """All base URLs failed the HEAD probe."""


def _build_url(base: str, filename: str) -> str:
    return base.rstrip('/') + '/' + filename


def verify_sha256(path: str, expected: str) -> bool:
    """Return True if file at `path` matches `expected` SHA256. Empty expected → always True."""
    if not expected:
        return True
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest().lower() == expected.lower()


def _probe_one(base: str) -> Optional[tuple[str, float]]:
    url = _build_url(base, VLM_MMPROJ_FILENAME)   # probe with smaller file
    t0 = time.monotonic()
    try:
        r = requests.head(url, timeout=_PROBE_TIMEOUT_SEC, allow_redirects=True)
        if 200 <= r.status_code < 300:
            return (base, time.monotonic() - t0)
    except requests.exceptions.RequestException:
        pass
    return None


def probe_sources(urls: Optional[list[str]] = None) -> Optional[str]:
    """Concurrent HEAD probe; returns lowest-latency reachable base URL."""
    if urls is None:
        urls = VLM_WEIGHT_BASE_URLS
    results: list[tuple[str, float]] = []
    with ThreadPoolExecutor(max_workers=len(urls)) as ex:
        for r in ex.map(_probe_one, urls):
            if r is not None:
                results.append(r)
    return min(results, key=lambda x: x[1])[0] if results else None


def _file_size_at(base: str, filename: str) -> int:
    """Return file size in bytes from HEAD (or GET-stream fallback), or 0 if unknown."""
    url = _build_url(base, filename)
    for method in ('head', 'get'):
        try:
            if method == 'head':
                r = requests.head(url, timeout=_PROBE_TIMEOUT_SEC, allow_redirects=True)
            else:
                r = requests.get(url, stream=True, timeout=_PROBE_TIMEOUT_SEC, allow_redirects=True)
            try:
                if 200 <= r.status_code < 300:
                    cl = r.headers.get('content-length')
                    if cl:
                        return int(cl)
            finally:
                if method == 'get':
                    r.close()
        except requests.exceptions.RequestException:
            pass
    return 0


def _download_to_path(
    url: str,
    dest: Path,
    on_bytes: Callable[[int], None],
    cancel: threading.Event,
) -> None:
    """Resume-capable streaming download. Calls on_bytes(bytes_written) after each chunk."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = dest.stat().st_size if dest.exists() else 0
    headers = {'Range': f'bytes={existing}-'} if existing else {}
    r = requests.get(url, stream=True, timeout=(15, 120), headers=headers)
    if r.status_code == 416:
        return   # file already complete
    r.raise_for_status()
    mode = 'ab' if existing else 'wb'
    with open(dest, mode) as f:
        for chunk in r.iter_content(chunk_size=65536):
            if cancel.is_set():
                raise DownloadCancelled()
            if chunk:
                f.write(chunk)
                on_bytes(f.tell())


def base_url_for_name(name: str) -> Optional[str]:
    """Map UI source-selection name → base URL, or None for auto-probe."""
    if name == 'auto':
        return None
    for url in VLM_WEIGHT_BASE_URLS:
        if name == 'modelscope' and 'modelscope' in url:
            return url
        if name == 'huggingface' and 'huggingface' in url:
            return url
    return None


def download_all_weights(
    models_dir: Path,
    on_progress: ProgressCallback,
    cancel_event: threading.Event,
    on_source_change: Optional[Callable[[str], None]] = None,
    forced_base_url: Optional[str] = None,
) -> None:
    """Download missing VLM weights into `models_dir`.

    Downloads mmproj first (smaller, faster indicator of working connection),
    then main model. Resume-aware via HTTP Range. Hash check after each file.
    On hash mismatch: delete, retry on alternate source once (unless forced source).
    """
    models_dir.mkdir(parents=True, exist_ok=True)
    primary = forced_base_url if forced_base_url else probe_sources()
    if primary is None:
        raise NoSourceAvailable('All VLM weight mirrors are unreachable.')
    if on_source_change:
        on_source_change(primary)

    todo = []
    for fname in _VLM_FILES:
        target = models_dir / fname
        if target.exists() and verify_sha256(str(target), _VLM_HASHES.get(fname, '')):
            continue
        target.unlink(missing_ok=True)
        todo.append(fname)

    if not todo:
        return

    # Pre-fetch sizes for progress display (concurrent HEAD requests)
    file_sizes: dict[str, int] = {}
    overall_total = 0
    with ThreadPoolExecutor(max_workers=min(4, len(todo))) as ex:
        future_to = {ex.submit(_file_size_at, primary, f): f for f in todo}
        for fut in as_completed(future_to):
            if cancel_event.is_set():
                raise DownloadCancelled()
            f = future_to[fut]
            sz = fut.result()
            file_sizes[f] = sz
            overall_total += sz

    overall_done = 0
    for idx, fname in enumerate(todo):
        if cancel_event.is_set():
            raise DownloadCancelled()
        target = models_dir / fname
        file_total = file_sizes.get(fname, 0)
        bytes_holder = {'v': 0}

        def _cb(b: int, _fname=fname, _idx=idx, _ft=file_total) -> None:
            bytes_holder['v'] = b
            on_progress(_idx, _fname, b, _ft, overall_done + b, overall_total, len(todo))

        _download_to_path(_build_url(primary, fname), target, _cb, cancel_event)

        if verify_sha256(str(target), _VLM_HASHES.get(fname, '')):
            overall_done += file_sizes.get(fname, 0)
            continue

        # Hash mismatch — try alternate source
        target.unlink(missing_ok=True)
        if forced_base_url:
            raise HashMismatch(f'{fname}: hash mismatch on forced source')
        alts = [u for u in VLM_WEIGHT_BASE_URLS if u != primary]
        if not alts:
            raise HashMismatch(f'{fname}: hash mismatch, no alternate source')
        alt = alts[0]
        if on_source_change:
            on_source_change(alt)
        bytes_holder['v'] = 0
        _download_to_path(_build_url(alt, fname), target, _cb, cancel_event)
        if not verify_sha256(str(target), _VLM_HASHES.get(fname, '')):
            raise HashMismatch(f'{fname}: hash mismatch on both sources')
        overall_done += file_sizes.get(fname, 0)
```

- [ ] **Step 4: Run tests — expect PASS**

```
python -m pytest tests/test_vlm_downloader.py -v
```

Expected:
```
PASSED tests/test_vlm_downloader.py::test_build_url_modelscope
PASSED tests/test_vlm_downloader.py::test_build_url_trailing_slash_stripped
PASSED tests/test_vlm_downloader.py::test_verify_sha256_empty_skips
PASSED tests/test_vlm_downloader.py::test_verify_sha256_correct
PASSED tests/test_vlm_downloader.py::test_verify_sha256_wrong
5 passed
```

- [ ] **Step 5: Commit**

```bash
git add core/vlm/gguf_downloader.py tests/__init__.py tests/test_vlm_downloader.py
git commit -m "feat(vlm): add GGUF downloader with resume, hash verify, and mirror fallback"
```

---

## Task 3: Jianpu Recognizer (`core/vlm/jianpu_recognizer.py`)

**Files:**
- Create: `core/vlm/jianpu_recognizer.py`

Note: This module requires `llama-cpp-python`. Install it before running this task:
```bash
# CUDA 12.4 (RTX 50xx Blackwell / 40xx / 30xx)
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124

# CPU fallback if CUDA install fails
pip install llama-cpp-python
```

---

- [ ] **Step 1: Create `core/vlm/jianpu_recognizer.py`**

```python
# core/vlm/jianpu_recognizer.py — Stage 1: jianpu image/PDF → JSON note list
# Uses Qwen2-VL-7B-Instruct GGUF via llama-cpp-python.
# The Llama instance is cached as a module singleton; call release_vlm() to free memory.
from __future__ import annotations

import base64
import json
import logging
import tempfile
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger('convert')

try:
    import llama_cpp
    from llama_cpp import Llama
    from llama_cpp.llama_chat_format import Qwen2VLChatHandler
    _LLAMA_AVAILABLE = True
except ImportError:
    _LLAMA_AVAILABLE = False

_vlm: Optional['Llama'] = None          # type: ignore[name-defined]
_vlm_model_path: Optional[Path] = None

_SYSTEM_PROMPT = "你是简谱识别专家。仅输出JSON，不要任何解释或额外文字。"

_USER_PROMPT = """分析图片中的简谱，以严格JSON格式输出（无其他文字）：

{
  "time_signature": "4/4",
  "key": "C",
  "tempo": 120,
  "measures": [
    [{"p": "5", "oct": 0, "dur": "q", "dots": 0}]
  ]
}

字段说明：
- p: "1"到"7"（音符），或"r"（休止符）
- oct: 1=高八度, 0=原位, -1=低八度（音符上方的点=高八度，下方=低八度）
- dur: "w"全音符, "h"二分, "q"四分, "e"八分, "s"十六分
- dots: 0=无附点, 1=附点
请完整识别所有小节。"""


def get_vlm(model_path: Path, mmproj_path: Path) -> 'Llama':  # type: ignore[name-defined]
    """Return (and cache) the Llama singleton. Reloads if model_path changed."""
    global _vlm, _vlm_model_path
    if not _LLAMA_AVAILABLE:
        raise RuntimeError(
            'llama-cpp-python 未安装。\n'
            '安装命令（CUDA 12.4）：\n'
            'pip install llama-cpp-python '
            '--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124'
        )
    if _vlm is None or _vlm_model_path != model_path:
        _LOG.info(f'[VLM] 加载模型中: {model_path.name}（首次约 10–30 秒）')
        chat_handler = Qwen2VLChatHandler(clip_model_path=str(mmproj_path))
        _vlm = Llama(
            model_path=str(model_path),
            chat_handler=chat_handler,
            n_gpu_layers=-1,
            n_ctx=4096,
            verbose=False,
        )
        _vlm_model_path = model_path
        _LOG.info('[VLM] 模型加载完成')
    return _vlm


def release_vlm() -> None:
    """Release the cached Llama instance and free GPU memory."""
    global _vlm, _vlm_model_path
    _vlm = None
    _vlm_model_path = None
    _LOG.info('[VLM] 模型已释放')


def _image_to_data_url(image_path: Path) -> str:
    mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}.get(
        image_path.suffix.lower(), 'image/png'
    )
    data = base64.b64encode(image_path.read_bytes()).decode()
    return f'data:{mime};base64,{data}'


def _parse_response(content: str) -> dict:
    """Extract JSON from model response, stripping markdown fences if present."""
    content = content.strip()
    if '```' in content:
        start = content.find('{')
        end = content.rfind('}')
        if start != -1 and end != -1:
            content = content[start:end + 1]
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f'VLM 返回内容无法解析为 JSON: {e}\n'
            f'内容片段: {content[:300]}'
        )


def recognize_image(image_path: Path, model_path: Path, mmproj_path: Path) -> dict:
    """Run VLM inference on one image. Returns parsed dict with 'measures' key.

    Raises RuntimeError on import failure or unparseable response.
    """
    vlm = get_vlm(model_path, mmproj_path)
    image_url = _image_to_data_url(image_path)
    response = vlm.create_chat_completion(
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": _USER_PROMPT},
                ],
            },
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    content = response["choices"][0]["message"]["content"]
    return _parse_response(content)


def recognize_pdf(pdf_path: Path, model_path: Path, mmproj_path: Path) -> dict:
    """Recognize a PDF: render each page → recognize_image → merge measures.

    Pages that fail recognition are skipped with a warning log.
    Returns a merged dict using the first page's time/key/tempo metadata.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise RuntimeError('PyMuPDF (fitz) 未安装，无法渲染 PDF 页面。')

    doc = fitz.open(str(pdf_path))
    all_measures: list = []
    meta = {'time_signature': '4/4', 'key': 'C', 'tempo': 120}

    with tempfile.TemporaryDirectory() as tmp:
        for page_no in range(doc.page_count):
            page = doc[page_no]
            mat = fitz.Matrix(2.0, 2.0)   # 2× upscale for legibility
            pix = page.get_pixmap(matrix=mat)
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

    doc.close()
    return {**meta, 'measures': all_measures}
```

- [ ] **Step 2: Verify import (no GPU test yet)**

```
python -c "from core.vlm.jianpu_recognizer import recognize_image, recognize_pdf, release_vlm; print('OK')"
```

Expected: `OK`  
If `llama-cpp-python` not yet installed: install it first (see note above), then re-run.

- [ ] **Step 3: Commit**

```bash
git add core/vlm/jianpu_recognizer.py
git commit -m "feat(vlm): add jianpu_recognizer with Qwen2-VL singleton and PDF multi-page support"
```

---

## Task 4: JSON → MusicXML Converter (`core/vlm/json_to_musicxml.py`)

**Files:**
- Create: `core/vlm/json_to_musicxml.py`
- Create: `tests/test_json_to_musicxml.py`

---

- [ ] **Step 1: Write failing tests**

Create `tests/test_json_to_musicxml.py`:

```python
# tests/test_json_to_musicxml.py
import tempfile
from pathlib import Path


_SAMPLE = {
    "time_signature": "4/4",
    "key": "C",
    "tempo": 120,
    "measures": [
        [
            {"p": "1", "oct": 0, "dur": "q", "dots": 0},
            {"p": "3", "oct": 0, "dur": "q", "dots": 0},
            {"p": "5", "oct": 0, "dur": "h", "dots": 0},
        ],
        [
            {"p": "r", "oct": 0, "dur": "q", "dots": 0},
            {"p": "5", "oct": 1,  "dur": "q", "dots": 0},
            {"p": "3", "oct": 0, "dur": "e", "dots": 1},
            {"p": "2", "oct": 0, "dur": "e", "dots": 0},
        ],
    ],
}


def test_convert_creates_mxl_file():
    from core.vlm.json_to_musicxml import convert
    with tempfile.TemporaryDirectory() as tmp:
        out = convert(_SAMPLE, Path(tmp) / 'test.mxl')
        assert out.exists(), f"Expected {out} to exist"
        assert out.suffix == '.mxl'
        assert out.stat().st_size > 0


def test_convert_empty_measures():
    from core.vlm.json_to_musicxml import convert
    data = {"time_signature": "4/4", "key": "C", "tempo": 120, "measures": []}
    with tempfile.TemporaryDirectory() as tmp:
        out = convert(data, Path(tmp) / 'empty.mxl')
        assert out.exists()


def test_convert_rest_note():
    from core.vlm.json_to_musicxml import convert
    data = {
        "time_signature": "4/4", "key": "C", "tempo": 80,
        "measures": [[{"p": "r", "oct": 0, "dur": "w", "dots": 0}]],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = convert(data, Path(tmp) / 'rest.mxl')
        assert out.exists()


def test_convert_dotted_note():
    from core.vlm.json_to_musicxml import convert
    data = {
        "time_signature": "3/4", "key": "G", "tempo": 90,
        "measures": [[{"p": "5", "oct": 0, "dur": "h", "dots": 1}]],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = convert(data, Path(tmp) / 'dotted.mxl')
        assert out.exists()
```

- [ ] **Step 2: Run tests — expect FAIL (ImportError)**

```
python -m pytest tests/test_json_to_musicxml.py -v
```

Expected: `ImportError: No module named 'core.vlm.json_to_musicxml'`

- [ ] **Step 3: Create `core/vlm/json_to_musicxml.py`**

```python
# core/vlm/json_to_musicxml.py — Stage 2: JSON note list → MusicXML (.mxl)
# Takes the dict produced by jianpu_recognizer and converts it to a music21 Score.
from __future__ import annotations

from pathlib import Path
from typing import Any

_PITCH_MAP = {'1': 'C', '2': 'D', '3': 'E', '4': 'F', '5': 'G', '6': 'A', '7': 'B'}
_DUR_MAP   = {'w': 4.0, 'h': 2.0, 'q': 1.0, 'e': 0.5, 's': 0.25}


def convert(data: dict[str, Any], output_path: Path) -> Path:
    """Convert JSON note list dict to a MusicXML .mxl file.

    data keys:
        time_signature: str  e.g. "4/4"
        key:            str  e.g. "C", "G"
        tempo:          int  BPM
        measures:       list[list[dict]]  each inner list is one measure

    note dict keys:
        p:    "1"–"7" or "r" (rest)
        oct:  int  octave shift relative to octave 4 (0 = middle octave)
        dur:  "w"/"h"/"q"/"e"/"s"
        dots: 0 or 1

    Returns output_path after writing.
    """
    from music21 import stream, note, meter
    from music21 import tempo as m21_tempo
    from music21 import key as m21_key

    s = stream.Score()
    part = stream.Part()

    part.append(meter.TimeSignature(data.get('time_signature', '4/4')))

    try:
        part.append(m21_key.Key(data.get('key', 'C')))
    except Exception:
        pass  # skip unrecognised key strings gracefully

    bpm = int(data.get('tempo', 120))
    part.append(m21_tempo.MetronomeMark(number=bpm))

    for measure_notes in data.get('measures', []):
        m = stream.Measure()
        for n in measure_notes:
            p = str(n.get('p', 'r'))
            dur_base = _DUR_MAP.get(str(n.get('dur', 'q')), 1.0)
            if int(n.get('dots', 0)):
                dur_base *= 1.5

            if p == 'r':
                elem: note.GeneralNote = note.Rest()
            else:
                pitch_letter = _PITCH_MAP.get(p, 'C')
                octave = 4 + int(n.get('oct', 0))
                elem = note.Note(f'{pitch_letter}{octave}')

            elem.quarterLength = dur_base
            m.append(elem)
        part.append(m)

    s.append(part)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    s.write('mxl', fp=str(output_path))
    return output_path
```

- [ ] **Step 4: Run tests — expect PASS**

```
python -m pytest tests/test_json_to_musicxml.py -v
```

Expected:
```
PASSED tests/test_json_to_musicxml.py::test_convert_creates_mxl_file
PASSED tests/test_json_to_musicxml.py::test_convert_empty_measures
PASSED tests/test_json_to_musicxml.py::test_convert_rest_note
PASSED tests/test_json_to_musicxml.py::test_convert_dotted_note
4 passed
```

- [ ] **Step 5: Commit**

```bash
git add core/vlm/json_to_musicxml.py tests/test_json_to_musicxml.py
git commit -m "feat(vlm): add json_to_musicxml converter (Stage 2) with music21"
```

---

## Task 5: VLM Download Dialog (`gui/components/vlm_download_dialog.py`)

**Files:**
- Create: `gui/components/vlm_download_dialog.py`

This dialog mirrors `gui/components/model_download_dialog.py` exactly in structure.
The main differences from the HOMR dialog are:
- Source options: 自动 / ModelScope / HuggingFace (not GitHub)
- Description text mentions two-file download (~5 GB + ~400 MB)
- Calls `core.vlm.gguf_downloader.download_all_weights()`
- On success: sets `state.vlm_available = True`

---

- [ ] **Step 1: Create `gui/components/vlm_download_dialog.py`**

```python
# gui/components/vlm_download_dialog.py — Modal dialog for Qwen2-VL GGUF download
# Three-state UI: PICKER → DOWNLOADING → ERROR
# Threading mirrors model_download_dialog.py exactly.
from __future__ import annotations

import threading
from typing import Callable, Optional

import flet as ft

from ..app_state import AppState


class VlmDownloadDialog:
    """Modal dialog for downloading Qwen2-VL GGUF weights.

    Usage:
        dlg = VlmDownloadDialog(page, state, on_complete=callback)
        dlg.show()
    """

    _SOURCE_OPTIONS = [
        ('auto',        '自动选择',    '自动检测延迟最低的可用源（推荐）'),
        ('modelscope',  'ModelScope', 'ModelScope CDN — 大陆访问优先'),
        ('huggingface', 'HuggingFace', 'HuggingFace — 海外访问优先'),
    ]

    def __init__(
        self,
        page: ft.Page,
        state: AppState,
        on_complete: Optional[Callable[[], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
    ):
        self._page = page
        self._state = state
        self._on_complete = on_complete
        self._on_cancel = on_cancel
        self._cancel_event = threading.Event()
        self._selected_source = 'auto'
        self._dialog: Optional[ft.AlertDialog] = None
        self._source_selector: Optional[ft.Dropdown] = None
        self._source_desc: Optional[ft.Text] = None
        self._source_text: Optional[ft.Text] = None
        self._file_text: Optional[ft.Text] = None
        self._size_text: Optional[ft.Text] = None
        self._overall_text: Optional[ft.Text] = None
        self._overall_bar: Optional[ft.ProgressBar] = None

    def show(self) -> None:
        if self._dialog is None:
            self._dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text('VLM 模型权重', size=16, weight=ft.FontWeight.W_600),
            )
        self._render_picker()
        try:
            self._page.show_dialog(self._dialog)
        except Exception:
            pass

    # ── States ────────────────────────────────────────────────────────────────

    def _render_picker(self) -> None:
        self._source_selector = ft.Dropdown(
            label='下载源',
            value='auto',
            options=[ft.dropdown.Option(n, label) for n, label, _ in self._SOURCE_OPTIONS],
            on_select=self._on_source_changed,
            text_size=12,
            width=400,
        )
        self._source_desc = ft.Text(
            self._SOURCE_OPTIONS[0][2],
            size=12, color=ft.Colors.ON_SURFACE_VARIANT, italic=True,
        )
        intro = ft.Text(
            '将下载两个文件：主模型约 5 GB + 视觉编码器（mmproj）约 400 MB，'
            '共约 5.4 GB，存入 models/vlm/ 目录。',
            size=12, color=ft.Colors.ON_SURFACE_VARIANT,
        )
        if self._dialog is None:
            return
        self._dialog.title = ft.Text('下载 Qwen2-VL 模型权重', size=16, weight=ft.FontWeight.W_600)
        self._dialog.content = ft.Column(
            [intro, ft.Container(height=8), self._source_selector, self._source_desc],
            tight=True, spacing=4, width=420,
        )
        self._dialog.actions = [
            ft.TextButton('取消', on_click=self._on_picker_cancel),
            ft.ElevatedButton('开始下载', on_click=self._on_picker_confirm),
        ]
        try:
            self._page.update()
        except Exception:
            pass

    def _render_progress(self) -> None:
        self._source_text  = ft.Text('当前源: 测试中…', size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._file_text    = ft.Text('准备中…', size=13)
        self._size_text    = ft.Text('', size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._overall_text = ft.Text('0 / ? MB', size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._overall_bar  = ft.ProgressBar(value=0, expand=True)
        if self._dialog is None:
            return
        self._dialog.title = ft.Text('正在下载 Qwen2-VL 模型权重', size=16, weight=ft.FontWeight.W_600)
        self._dialog.content = ft.Column(
            [
                self._source_text,
                ft.Container(height=4),
                self._file_text,
                self._size_text,
                ft.Container(height=8),
                ft.Row([self._overall_bar, self._overall_text], spacing=8),
            ],
            tight=True, spacing=4, width=420,
        )
        self._dialog.actions = [ft.TextButton('取消', on_click=self._on_progress_cancel)]
        try:
            self._page.update()
        except Exception:
            pass

    def _render_error(self, msg: str) -> None:
        if self._dialog is None:
            return
        self._dialog.title = ft.Text('下载出错', size=16, weight=ft.FontWeight.W_600)
        self._dialog.content = ft.Column([ft.Text(msg, size=13)], tight=True, width=420)
        self._dialog.actions = [
            ft.TextButton('重试', on_click=lambda _: self._render_picker()),
            ft.TextButton('关闭', on_click=self._on_error_close),
        ]
        try:
            self._page.update()
        except Exception:
            pass

    # ── Handlers ─────────────────────────────────────────────────────────────

    def _on_source_changed(self, e) -> None:
        name = e.control.value
        if self._source_desc is None:
            return
        for n, _, desc in self._SOURCE_OPTIONS:
            if n == name:
                self._source_desc.value = desc
                break
        try:
            self._source_desc.update()
        except Exception:
            pass

    def _on_picker_cancel(self, _) -> None:
        self._close()
        if self._on_cancel:
            self._on_cancel()

    def _on_picker_confirm(self, _) -> None:
        self._selected_source = (self._source_selector.value or 'auto') if self._source_selector else 'auto'
        self._cancel_event = threading.Event()
        self._render_progress()
        threading.Thread(target=self._run_download, daemon=True).start()

    def _on_progress_cancel(self, _) -> None:
        self._cancel_event.set()
        self._close()
        if self._on_cancel:
            self._on_cancel()

    def _on_error_close(self, _) -> None:
        self._close()
        if self._on_cancel:
            self._on_cancel()

    def _close(self) -> None:
        try:
            self._page.pop_dialog()
        except Exception:
            pass

    # ── Download worker ───────────────────────────────────────────────────────

    def _run_download(self) -> None:
        from core.app.backend import vlm_models_dir
        from core.vlm.gguf_downloader import (
            download_all_weights,
            base_url_for_name,
            DownloadCancelled,
            HashMismatch,
            NoSourceAvailable,
        )
        forced = base_url_for_name(self._selected_source)
        my_cancel = self._cancel_event
        try:
            download_all_weights(
                vlm_models_dir(),
                self._on_progress_cb,
                my_cancel,
                self._on_source_cb,
                forced_base_url=forced,
            )
        except DownloadCancelled:
            return
        except NoSourceAvailable:
            self._marshal(self._render_error, '下载失败 — 请检查网络连接')
            return
        except HashMismatch as exc:
            self._marshal(self._render_error, f'权重文件校验失败：{exc}')
            return
        except Exception as exc:
            self._marshal(self._render_error, f'下载失败：{exc}')
            return

        def _done():
            self._state.vlm_available = True
            self._close()
            if self._on_complete:
                self._on_complete()

        self._marshal(_done)

    def _on_progress_cb(self, idx, fname, file_done, file_total,
                         overall_done, overall_total, total_files):
        def _update():
            if self._file_text is None:
                return
            display = fname[:50] + ('…' if len(fname) > 50 else '')
            self._file_text.value = f'下载中 ({idx + 1}/{total_files}): {display}'
            if file_total > 0:
                pct = file_done * 100 // file_total
                self._size_text.value = (
                    f'{file_done // 1024 // 1024} / {file_total // 1024 // 1024} MB ({pct}%)'
                )
            else:
                self._size_text.value = f'{file_done // 1024 // 1024} MB'
            if overall_total > 0 and self._overall_bar is not None:
                self._overall_bar.value = overall_done / overall_total
                self._overall_text.value = (
                    f'{overall_done // 1024 // 1024} / {overall_total // 1024 // 1024} MB'
                )
            try:
                self._page.update()
            except Exception:
                pass
        self._marshal(_update)

    def _on_source_cb(self, source: str) -> None:
        def _update():
            if self._source_text is None:
                return
            label = 'ModelScope' if 'modelscope' in source else 'HuggingFace'
            self._source_text.value = f'当前源: {label}'
            try:
                self._page.update()
            except Exception:
                pass
        self._marshal(_update)

    def _marshal(self, fn, *args) -> None:
        try:
            self._page.loop.call_soon_threadsafe(lambda: fn(*args))
        except Exception:
            pass
```

- [ ] **Step 2: Verify import**

```
python -c "from gui.components.vlm_download_dialog import VlmDownloadDialog; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add gui/components/vlm_download_dialog.py
git commit -m "feat(vlm): add VLM download dialog (mirrors HOMR model_download_dialog)"
```

---

## Task 6: Jianpu OCR Page (`gui/pages/jianpu_ocr_page.py`)

**Files:**
- Create: `gui/pages/jianpu_ocr_page.py`

---

- [ ] **Step 1: Create `gui/pages/jianpu_ocr_page.py`**

```python
# gui/pages/jianpu_ocr_page.py — 简谱识别页（实验性）
# 左侧：文件列表（FilePicker / jianpu-Input/ 扫描）
# 右侧：模型状态 + 进度/日志 + 开始识别按钮
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import flet as ft

from ..app_state import AppState, Event
from core.app.backend import jianpu_input_dir, vlm_models_dir, xml_scores_dir
from core.config import VLM_MODEL_FILENAME, VLM_MMPROJ_FILENAME
from ..components.vlm_download_dialog import VlmDownloadDialog
from ..theme import Palette, with_alpha

_ALLOWED_SUFFIXES = {'.png', '.jpg', '.jpeg', '.pdf'}


class JianpuOcrPage(ft.Row):
    """实验性简谱识别页：jianpu 图片/PDF → MusicXML → 跳转五线谱预览。"""

    def __init__(self, state: AppState):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._input_paths: list[Path] = []
        self._cancel_event = threading.Event()
        self._recognize_thread: Optional[threading.Thread] = None
        self._file_picker: Optional[ft.FilePicker] = None
        self._folder_picker: Optional[ft.FilePicker] = None
        self._build_ui()

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Sidebar: file list
        self._file_list_col = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)

        refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_size=16,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip='刷新 jianpu-Input 目录',
            on_click=lambda _: self._scan_input_dir(),
        )
        add_file_btn = ft.IconButton(
            icon=ft.Icons.ADD_ROUNDED,
            icon_size=16,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip='添加文件',
            on_click=self._on_add_file,
        )
        add_folder_btn = ft.IconButton(
            icon=ft.Icons.FOLDER_OPEN_ROUNDED,
            icon_size=16,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip='添加文件夹',
            on_click=self._on_add_folder,
        )

        sidebar_header = ft.Container(
            content=ft.Row(
                [ft.Text('输入文件', size=13, weight=ft.FontWeight.W_600,
                          color=ft.Colors.ON_SURFACE, expand=True),
                 refresh_btn, add_file_btn, add_folder_btn],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            height=40,
            padding=ft.Padding.only(left=12, right=4),
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        sidebar = ft.Container(
            content=ft.Column([sidebar_header, self._file_list_col],
                              spacing=0, expand=True),
            width=240,
            bgcolor=ft.Colors.SURFACE,
            border=ft.Border.only(right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        # Main area: model status + log + button
        self._model_status_row = ft.Row([], spacing=8)
        self._log_col = ft.Column(
            spacing=2, scroll=ft.ScrollMode.AUTO, expand=True,
            auto_scroll=True,
        )
        self._progress_bar = ft.ProgressBar(value=0, visible=False)

        self._start_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.DOCUMENT_SCANNER_ROUNDED, size=18),
                 ft.Text('开始识别')],
                tight=True, spacing=6,
            ),
            bgcolor=Palette.PRIMARY,
            color='#FFFFFF',
            on_click=self._on_start_recognize,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                elevation={ft.ControlState.PRESSED: 0, ft.ControlState.DEFAULT: 2},
            ),
        )
        self._cancel_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.STOP_ROUNDED, size=18), ft.Text('取消')],
                tight=True, spacing=6,
            ),
            on_click=self._on_cancel,
            visible=False,
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        main_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Text('简谱识别（实验性）', size=15,
                                        weight=ft.FontWeight.W_700,
                                        color=ft.Colors.ON_SURFACE),
                        height=48,
                        padding=ft.Padding.only(left=16, right=16),
                        alignment=ft.Alignment(-1, 0),
                        border=ft.Border.only(
                            bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
                    ),
                    ft.Container(
                        content=ft.Column(
                            [
                                self._model_status_row,
                                ft.Divider(height=1),
                                ft.Text('识别日志', size=12,
                                        color=ft.Colors.ON_SURFACE_VARIANT),
                                ft.Container(
                                    content=self._log_col,
                                    expand=True,
                                    bgcolor=ft.Colors.SURFACE_CONTAINER,
                                    border_radius=6,
                                    padding=ft.Padding.all(8),
                                ),
                                self._progress_bar,
                                ft.Row([self._start_btn, self._cancel_btn], spacing=8),
                            ],
                            spacing=8,
                            expand=True,
                        ),
                        padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                        expand=True,
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            expand=True,
            bgcolor=ft.Colors.SURFACE,
        )

        self.controls = [sidebar, main_panel]

    # ── Flet lifecycle ────────────────────────────────────────────────────────

    def did_mount(self) -> None:
        self._file_picker = ft.FilePicker(on_result=self._on_files_picked)
        self._folder_picker = ft.FilePicker(on_result=self._on_folder_picked)
        self.page.overlay.extend([self._file_picker, self._folder_picker])
        self.page.update()
        self._scan_input_dir()
        self._refresh_model_status()

    def will_unmount(self) -> None:
        for picker in [self._file_picker, self._folder_picker]:
            if picker and picker in self.page.overlay:
                self.page.overlay.remove(picker)
        from core.vlm.jianpu_recognizer import release_vlm
        release_vlm()

    def reload(self) -> None:
        """Called by app.py when navigating to this page."""
        self._scan_input_dir()
        self._refresh_model_status()

    # ── File management ───────────────────────────────────────────────────────

    def _scan_input_dir(self) -> None:
        """Scan jianpu-Input/ and rebuild sidebar file list."""
        d = jianpu_input_dir()
        d.mkdir(parents=True, exist_ok=True)
        paths = sorted(
            p for p in d.iterdir()
            if p.is_file() and p.suffix.lower() in _ALLOWED_SUFFIXES
        )
        self._input_paths = paths
        self._rebuild_file_list()

    def _rebuild_file_list(self) -> None:
        self._file_list_col.controls.clear()
        if not self._input_paths:
            self._file_list_col.controls.append(
                ft.Container(
                    content=ft.Text('暂无文件\n请点击 + 添加',
                                    size=12, color=ft.Colors.OUTLINE,
                                    text_align=ft.TextAlign.CENTER),
                    padding=ft.Padding.all(16),
                    alignment=ft.Alignment(0, 0),
                )
            )
        else:
            for p in self._input_paths:
                row = self._make_file_row(p)
                self._file_list_col.controls.append(row)
        try:
            self._file_list_col.update()
        except Exception:
            pass

    def _make_file_row(self, path: Path) -> ft.Container:
        remove_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_size=12,
            icon_color=ft.Colors.OUTLINE,
            width=24,
            height=24,
            tooltip='从列表移除',
            on_click=lambda _, p=path: self._remove_path(p),
        )
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.IMAGE_OUTLINED if path.suffix.lower() != '.pdf'
                            else ft.Icons.PICTURE_AS_PDF_OUTLINED,
                            size=14, color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Text(path.name, size=12, expand=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            color=ft.Colors.ON_SURFACE),
                    remove_btn,
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border_radius=4,
        )

    def _remove_path(self, path: Path) -> None:
        if path in self._input_paths:
            self._input_paths.remove(path)
        self._rebuild_file_list()

    def _on_add_file(self, _) -> None:
        if self._file_picker:
            self._file_picker.pick_files(
                allow_multiple=True,
                allowed_extensions=['png', 'jpg', 'jpeg', 'pdf'],
            )

    def _on_add_folder(self, _) -> None:
        if self._folder_picker:
            self._folder_picker.get_directory_path()

    def _on_files_picked(self, e: ft.FilePickerResultEvent) -> None:
        if not e.files:
            return
        for f in e.files:
            p = Path(f.path)
            if p.suffix.lower() in _ALLOWED_SUFFIXES and p not in self._input_paths:
                self._input_paths.append(p)
        self._rebuild_file_list()

    def _on_folder_picked(self, e: ft.FilePickerResultEvent) -> None:
        if not e.path:
            return
        folder = Path(e.path)
        for p in sorted(folder.iterdir()):
            if p.is_file() and p.suffix.lower() in _ALLOWED_SUFFIXES:
                if p not in self._input_paths:
                    self._input_paths.append(p)
        self._rebuild_file_list()

    # ── Model status ──────────────────────────────────────────────────────────

    def _refresh_model_status(self) -> None:
        model_path = vlm_models_dir() / VLM_MODEL_FILENAME
        mmproj_path = vlm_models_dir() / VLM_MMPROJ_FILENAME
        both_present = model_path.exists() and mmproj_path.exists()
        self._state.vlm_available = both_present

        self._model_status_row.controls.clear()
        if both_present:
            self._model_status_row.controls += [
                ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED,
                        color=Palette.SUCCESS, size=16),
                ft.Text('模型已就绪', size=13, color=ft.Colors.ON_SURFACE),
            ]
            self._start_btn.disabled = False
        else:
            self._model_status_row.controls += [
                ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED,
                        color=ft.Colors.ORANGE, size=16),
                ft.Text('模型未下载', size=13, color=ft.Colors.ON_SURFACE),
                ft.TextButton(
                    '下载模型权重',
                    icon=ft.Icons.DOWNLOAD_ROUNDED,
                    on_click=self._on_download_model,
                ),
            ]
            self._start_btn.disabled = True
        try:
            self._model_status_row.update()
            self._start_btn.update()
        except Exception:
            pass

    def _on_download_model(self, _) -> None:
        dlg = VlmDownloadDialog(
            self.page,
            self._state,
            on_complete=self._on_model_downloaded,
        )
        dlg.show()

    def _on_model_downloaded(self) -> None:
        self._refresh_model_status()
        self._log('模型下载完成，可以开始识别。')

    # ── Recognition ──────────────────────────────────────────────────────────

    def _on_start_recognize(self, _) -> None:
        if not self._input_paths:
            self._log('请先添加简谱图片或 PDF 文件。')
            return
        self._cancel_event = threading.Event()
        self._start_btn.visible = False
        self._cancel_btn.visible = True
        self._progress_bar.visible = True
        self._progress_bar.value = 0
        try:
            self._start_btn.update()
            self._cancel_btn.update()
            self._progress_bar.update()
        except Exception:
            pass
        self._recognize_thread = threading.Thread(
            target=self._run_recognition, daemon=True
        )
        self._recognize_thread.start()

    def _on_cancel(self, _) -> None:
        self._cancel_event.set()
        self._log('正在取消…')

    def _run_recognition(self) -> None:
        from core.vlm.jianpu_recognizer import recognize_image, recognize_pdf
        from core.vlm.json_to_musicxml import convert

        model_path  = vlm_models_dir() / VLM_MODEL_FILENAME
        mmproj_path = vlm_models_dir() / VLM_MMPROJ_FILENAME
        out_dir     = xml_scores_dir()
        results: list[Path] = []

        total = len(self._input_paths)
        for i, src in enumerate(self._input_paths):
            if self._cancel_event.is_set():
                self._marshal(self._log, '已取消。')
                break
            self._marshal(self._log, f'[{i + 1}/{total}] 识别 {src.name}…')
            self._marshal(self._set_progress, i / total)

            try:
                if src.suffix.lower() == '.pdf':
                    data = recognize_pdf(src, model_path, mmproj_path)
                else:
                    data = recognize_image(src, model_path, mmproj_path)

                out_path = out_dir / f'{src.stem}_ocr.mxl'
                convert(data, out_path)
                results.append(out_path)
                self._marshal(self._log, f'  → 已保存: {out_path.name}')
            except Exception as exc:
                self._marshal(self._log, f'  ✗ 失败: {exc}')

        self._marshal(self._log, f'完成。共 {len(results)}/{total} 个文件识别成功。')
        self._marshal(self._set_progress, 1.0)
        self._marshal(self._finish_recognition, bool(results))

    def _finish_recognition(self, has_results: bool) -> None:
        self._start_btn.visible = True
        self._cancel_btn.visible = False
        self._progress_bar.visible = False
        try:
            self._start_btn.update()
            self._cancel_btn.update()
            self._progress_bar.update()
        except Exception:
            pass
        if has_results:
            self._state.emit(Event.JIANPU_OCR_DONE)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self._log_col.controls.append(
            ft.Text(msg, size=12, selectable=True,
                    color=ft.Colors.ON_SURFACE_VARIANT)
        )
        if len(self._log_col.controls) > 200:
            self._log_col.controls.pop(0)
        try:
            self._log_col.update()
        except Exception:
            pass

    def _set_progress(self, value: float) -> None:
        self._progress_bar.value = max(0.0, min(1.0, value))
        try:
            self._progress_bar.update()
        except Exception:
            pass

    def _marshal(self, fn, *args) -> None:
        try:
            self.page.loop.call_soon_threadsafe(lambda: fn(*args))
        except Exception:
            pass
```

- [ ] **Step 2: Verify import**

```
python -c "from gui.pages.jianpu_ocr_page import JianpuOcrPage; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add gui/pages/jianpu_ocr_page.py
git commit -m "feat(vlm): add jianpu OCR page with file picker, model status, and recognition UI"
```

---

## Task 7: App Wiring — landing_page.py + app.py

**Files:**
- Modify: `gui/pages/landing_page.py`
- Modify: `app.py`

---

- [ ] **Step 1: Add "简谱识别（实验性）" button to `landing_page.py`**

In `gui/pages/landing_page.py`, find the `options_panel` Column that contains the button list (around line 160). The column currently has:
```python
self._engine_dd,
self._sr_engine_dd,
self._parallel_dd,
ft.Container(height=4),
self._convert_btn,
open_output_btn,
self._download_models_btn,
self._delete_models_btn,
```

Add after `self._delete_models_btn,`:

```python
                ft.Container(
                    height=1,
                    bgcolor=ft.Colors.OUTLINE_VARIANT,
                    margin=ft.Margin.symmetric(vertical=4),
                ),
                ft.Button(
                    content=ft.Row(
                        [ft.Icon(ft.Icons.DOCUMENT_SCANNER_OUTLINED, size=16),
                         ft.Text('简谱识别（实验性）')],
                        tight=True, spacing=6,
                    ),
                    on_click=lambda _: self._state.emit(Event.JIANPU_OCR_REQUESTED),
                    style=ft.ButtonStyle(
                        color=ft.Colors.ON_SURFACE_VARIANT,
                        side={ft.ControlState.DEFAULT: ft.BorderSide(
                            1, ft.Colors.OUTLINE_VARIANT)},
                        shape=ft.RoundedRectangleBorder(radius=8),
                    ),
                ),
```

- [ ] **Step 2: Add `_check_vlm_model()` to `app.py`**

In `app.py`, after the `_check_homr_models` function (after line ~164), add:

```python
def _check_vlm_model(state: AppState) -> None:
    """Check if both VLM GGUF files exist on disk. Sets state.vlm_available."""
    from pathlib import Path as _Path
    from core.app.backend import vlm_models_dir as _vlm_dir
    from core.config import VLM_MODEL_FILENAME, VLM_MMPROJ_FILENAME
    d = _vlm_dir()
    state.vlm_available = (
        (d / VLM_MODEL_FILENAME).exists()
        and (d / VLM_MMPROJ_FILENAME).exists()
    )
```

- [ ] **Step 3: Call `_check_vlm_model(state)` in `app.py`**

In `app.py` inside `async def main(page)`, after `_check_homr_models(state)` (around line 202), add:

```python
    _check_vlm_model(state)
```

- [ ] **Step 4: Import `JianpuOcrPage` in `app.py`**

After the existing page imports (around line 103), add:

```python
from gui.pages.jianpu_ocr_page import JianpuOcrPage
```

- [ ] **Step 5: Instantiate the page in `app.py`**

After `about_page = AboutPage()` (around line 225), add:

```python
    jianpu_ocr_page  = JianpuOcrPage(state)
```

- [ ] **Step 6: Add page container to `content_stack` in `app.py`**

Find `content_stack = ft.Stack(...)`. The existing controls list ends with `overlay`. Add the new container before overlay:

```python
            ft.Container(content=jianpu_ocr_page,    expand=True, visible=False),  # 6: jianpu_ocr (sub)
```

The complete controls list order must be:
```
0: landing, 1: jianpu_preview, 2: score_preview, 3: about,
4: editor (jianpu_edit sub), 5: transposer (sub), 6: jianpu_ocr (sub), overlay
```

- [ ] **Step 7: Update `_content_containers` slice and `_show_page` in `app.py`**

Change the `_content_containers` line from `[:6]` to `[:7]`:

```python
    _content_containers: list[ft.Container] = content_stack.controls[:7]
```

In `_show_page`, change the `else:  # i == 5` block to add handling for index 6:

```python
    def _show_page(name: str) -> None:
        for i, container in enumerate(_content_containers):
            if i < len(_NAV_NAMES):
                container.visible = (_NAV_NAMES[i] == name)
            elif i == 4:
                container.visible = (name == 'jianpu_edit')
            elif i == 5:
                container.visible = (name == 'transposer')
            elif i == 6:
                container.visible = (name == 'jianpu_ocr')
        state.current_page = name
        if name == 'editor':
            jianpu_preview_page.reload()
        if name == 'score_preview':
            score_preview_page.reload()
        if name == 'jianpu_ocr':
            jianpu_ocr_page.reload()
        try:
            content_stack.update()
        except Exception:
            pass
```

- [ ] **Step 8: Add event handlers in `app.py`**

After the existing event-handler registrations (`state.on(Event.SCORE_TRANSPOSER_BACK, ...)`), add:

```python
    def _on_jianpu_ocr_requested(**_) -> None:
        _show_page('jianpu_ocr')

    def _on_jianpu_ocr_done(**_) -> None:
        _show_page('score_preview')

    state.on(Event.JIANPU_OCR_REQUESTED, _on_jianpu_ocr_requested)
    state.on(Event.JIANPU_OCR_DONE,      _on_jianpu_ocr_done)
```

- [ ] **Step 9: Smoke-test — launch the app**

```
python app.py
```

Verify:
1. App launches without error
2. Landing page shows "简谱识别（实验性）" button in options panel
3. Clicking it shows the new OCR page
4. OCR page shows "模型未下载" status if GGUF files are absent
5. Clicking "下载模型权重" opens the VLM download dialog
6. After downloading model: "模型已就绪" appears, "开始识别" is enabled
7. Adding PNG/JPG/PDF files via picker shows them in the file list
8. After recognition completes: app navigates to score_preview page and calls `reload()`

- [ ] **Step 10: Commit**

```bash
git add gui/pages/landing_page.py app.py
git commit -m "feat(vlm): wire up jianpu OCR page into landing_page and app routing"
```

---

## Post-implementation: fill in SHA256 hashes

After downloading the models for the first time:

```powershell
certutil -hashfile "models\vlm\qwen2_vl-7b-instruct-q4_k_m.gguf" SHA256
certutil -hashfile "models\vlm\mmproj-qwen2_vl-7b-instruct-f16.gguf" SHA256
```

Update `core/config.py`:
```python
VLM_MODEL_HASH  = '<output from first certutil command>'
VLM_MMPROJ_HASH = '<output from second certutil command>'
```

Then commit:
```bash
git add core/config.py
git commit -m "feat(vlm): add SHA256 hashes for Qwen2-VL GGUF weights"
```

---

## Self-review checklist (for implementer)

- [ ] `llama-cpp-python` installed and `python -c "import llama_cpp"` succeeds
- [ ] Both GGUF files present in `models/vlm/` before testing recognition
- [ ] App navigates landing → OCR page → (after recognition) → score preview
- [ ] Cancelling mid-recognition returns to READY state (not stuck in RECOGNIZING)
- [ ] PDF rendering uses 2× scale matrix (legibility for VLM)
- [ ] `release_vlm()` called in `will_unmount()` so GPU memory is freed on page leave
