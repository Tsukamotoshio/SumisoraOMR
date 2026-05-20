# Jianpu VLM OCR — Design Spec

**Date:** 2026-05-20
**Branch:** `exp/jianpu-vlm-ocr`
**Pages affected:** `gui/pages/landing_page.py` (button), new `gui/pages/jianpu_ocr_page.py`

---

## Goal

Add an experimental "简谱识别" feature: users supply jianpu (numbered musical notation) images or PDFs, a local VLM (Qwen2-VL-7B GGUF) performs two-stage recognition (image → JSON → MusicXML), and the result is written to `xml-scores/` then handed off to the existing score preview page.

---

## Runtime Approach

**In-process lazy loading via llama-cpp-python.** The GGUF model is loaded once per process, cached as a module-level singleton, and called from a daemon thread. No Ollama binary required. Mirrors the HOMR runner pattern (`homr_runner.py` / `pdf_viewer.py`).

---

## Model

| Property | Value |
|----------|-------|
| Model | Qwen2-VL-7B-Instruct |
| Quantization | Q4_K_M |
| Filename | `qwen2_vl-7b-instruct-q4_k_m.gguf` |
| Approx size | ~5.0 GB |
| Storage path | `models/vlm/qwen2_vl-7b-instruct-q4_k_m.gguf` |
| SHA256 | *(verify from HuggingFace file page at implementation time)* |

**Download sources** (probe lowest-latency, same pattern as HOMR):
- ModelScope: `https://modelscope.cn/models/Qwen/Qwen2-VL-7B-Instruct-GGUF/resolve/master/qwen2_vl-7b-instruct-q4_k_m.gguf`
- HuggingFace: `https://huggingface.co/Qwen/Qwen2-VL-7B-Instruct-GGUF/resolve/main/qwen2_vl-7b-instruct-q4_k_m.gguf`

**New dependency:** `llama-cpp-python` (CUDA build for GPU offload). Must be added to requirements and documented in README.

Install command (CUDA 12.x):
```bash
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
```
For CPU-only fallback: `pip install llama-cpp-python` (no extra index needed).

---

## File Structure

### New files

```
core/vlm/
  __init__.py
  gguf_downloader.py      — GGUF download orchestrator (mirrors homr_downloader.py)
  jianpu_recognizer.py    — Stage 1: image/PDF → JSON note list
  json_to_musicxml.py     — Stage 2: JSON note list → MusicXML (.mxl)

gui/pages/
  jianpu_ocr_page.py      — recognition page

gui/components/
  vlm_download_dialog.py  — GGUF download progress dialog (mirrors model_download_dialog.py)
```

### Modified files

| File | Change |
|------|--------|
| `core/config.py` | Add `VLM_MODEL_DIR_NAME`, `VLM_MODEL_FILENAME`, `VLM_MODEL_HASH`, `VLM_WEIGHT_BASE_URLS` |
| `core/app/backend.py` | Add `jianpu_input_dir()` and `vlm_models_dir()` helpers |
| `gui/app_state.py` | Add `vlm_available: bool = False` field |
| `gui/pages/landing_page.py` | Add "简谱识别（实验性）" OutlinedButton bottom-right |
| `app.py` | Add `_check_vlm_model()` at startup (silent check only, no prompt) |

---

## End-to-End Data Flow

```
用户点"添加文件"/"添加文件夹"
        │  ft.FilePicker，过滤 .png/.jpg/.jpeg/.pdf
        ▼
  jianpu-Input/  （懒创建，首次加载页面时 mkdir）
        │  页面启动 / 刷新时 glob 扫描
        ▼
  _input_paths: list[Path]  （页面内部状态，不进 AppState）
        │
        ▼
  [开始识别] → daemon thread
        │
        ├─ PDF：PyMuPDF 按页渲染为 PNG（已有依赖）
        └─ 图片：直接使用
        │
        ▼
  jianpu_recognizer.recognize(image_path) → dict  [Stage 1]
        │  Llama singleton，chatml 格式，n_gpu_layers=-1
        │  prompt → 结构化 JSON
        ▼
  json_to_musicxml.convert(data) → Path   [Stage 2]
        │  music21 Stream → .mxl
        │  输出到 xml-scores/<stem>_ocr.mxl
        ▼
  state.set_page('score_preview')          [自动跳转]
```

---

## Recognition Pipeline Detail

### Stage 1 — VLM → JSON (`jianpu_recognizer.py`)

**Model singleton:**
```python
_vlm: Optional[Llama] = None

def get_vlm(model_path: Path) -> Llama:
    global _vlm
    if _vlm is None:
        _vlm = Llama(
            model_path=str(model_path),
            chat_format="chatml",
            n_gpu_layers=-1,
            n_ctx=4096,
        )
    return _vlm
```

**JSON schema** (prompt constrains model to output only this):
```json
{
  "time_signature": "4/4",
  "key": "C",
  "tempo": 120,
  "measures": [
    [
      {"p": "5", "oct": 0, "dur": "q", "dots": 0}
    ]
  ]
}
```

Field reference:
- `p`: `"1"–"7"` for pitched notes, `"r"` for rest
- `oct`: `1` = 高八度, `0` = 原位, `-1` = 低八度 (stacked dots above/below digit)
- `dur`: `"w"` whole, `"h"` half, `"q"` quarter, `"e"` eighth, `"s"` sixteenth
- `dots`: `0` or `1` (附点)

**Multi-page PDFs:** each page recognized independently; measures lists are concatenated in order.

**Error handling:** JSON parse failure → log the error, skip that page, continue. Do not abort the whole file.

### Stage 2 — JSON → MusicXML (`json_to_musicxml.py`)

Uses `music21` (already a project dependency) to build a `Stream`, then exports to `.mxl`.

Output path: `xml-scores/<original_stem>_ocr.mxl`

---

## New Page Layout (`jianpu_ocr_page.py`)

Left–right split, mirroring landing page proportions:

```
┌──────────────────────────────────────────────────────────┐
│  侧边栏（左）                   │  主区域（右）            │
│  [刷新] [添加文件] [添加文件夹] │  模型状态区              │
│  ─────────────────────────────  │  ● 模型已就绪            │
│  score1.pdf               [×]  │    或 [下载模型权重]      │
│  score2.png               [×]  │  ─────────────────────── │
│                                 │  进度 / 日志滚动区        │
│                                 │  ProgressBar             │
│                                 │  ─────────────────────── │
│                                 │  [开始识别] / [取消]      │
└──────────────────────────────────────────────────────────┘
```

**Page states:**

| State | 说明 |
|-------|------|
| `MODEL_MISSING` | GGUF 未下载；主区域显示"下载模型权重"按钮；"开始识别"禁用 |
| `READY` | 模型就绪；"开始识别"可用 |
| `RECOGNIZING` | 识别进行中；ProgressBar 按文件推进；按钮变为"取消"。点取消：设置 `threading.Event`，当前文件完成后停止，页面回到 `READY` 状态 |

On completion (all files done): `state.set_page('score_preview')`. `app.py:258` automatically calls `score_preview_page.reload()` when this page key is navigated to.

---

## Download Dialog (`vlm_download_dialog.py`)

Full mirror of `model_download_dialog.py`:

- States: `PICKER → DOWNLOADING → ERROR`
- Source options: 自动 / ModelScope / HuggingFace
- `_run_download()` calls `core.vlm.gguf_downloader.download_weight()` (single-file, no loop needed)
- Progress callback → `page.loop.call_soon_threadsafe` → ProgressBar update
- On success: `state.vlm_available = True`; dialog closes

---

## Directory Helpers (`core/app/backend.py`)

```python
def jianpu_input_dir() -> Path:
    return app_base_dir() / 'jianpu-Input'

def vlm_models_dir() -> Path:
    return models_dir() / 'vlm'
```

`jianpu-Input/` is created lazily on first page load (`mkdir(parents=True, exist_ok=True)`), not at app startup.

---

## AppState Changes

```python
# gui/app_state.py — AppState dataclass
vlm_available: bool = False   # GGUF downloaded and hash-verified
```

No new events needed: the OCR page manages its own thread + callback; completion triggers `state.set_page('score_preview')` which fires the existing `Event.PAGE_CHANGED`.

---

## Startup Check (`app.py`)

`_check_vlm_model()` runs at startup alongside `_check_homr_models()`:
- Check `vlm_models_dir() / VLM_MODEL_FILENAME` exists and passes SHA256.
- If yes: `state.vlm_available = True`.
- If no: do nothing (no prompt). Download is user-initiated from the OCR page.

---

## Module Dependency Graph

```
config.py  (VLM constants)
    │
core/vlm/
  gguf_downloader.py    ← requests (already present)
  jianpu_recognizer.py  ← llama-cpp-python (new), PyMuPDF (present)
  json_to_musicxml.py   ← music21 (present)
    │
gui/components/
  vlm_download_dialog.py  ← gguf_downloader + flet
    │
gui/pages/
  jianpu_ocr_page.py      ← core/vlm/* + vlm_download_dialog
    │
gui/pages/landing_page.py ← state.set_page only (no new imports)
app_state.py              ← vlm_available field
app.py                    ← _check_vlm_model()
```

**Strict dependency direction maintained**: `config → core/vlm → gui/components → gui/pages`. No circular imports.

---

## Out of Scope (this branch)

- Fine-tuning or accuracy improvement (accuracy is experimental-grade)
- Multi-voice / polyphony handling in JSON schema
- Lyrics recognition
- Batch parallel recognition (sequential only, same as HOMR)
- UI for adjusting model quantization or context size
