# Contributing to SumisoraOMR

Developer setup and workflow notes. For architecture, see
[`docs/Architecture.md`](docs/Architecture.md); for coding conventions and the
component map, see [`CLAUDE.md`](CLAUDE.md).

## Prerequisites

- **Python 3.14** (the project uses parenthesized `with` and other 3.10+ syntax).
- **Windows** is the primary target (Unicode filenames, taskbar identity, process-tree
  termination are Windows-tuned). Core notation logic is cross-platform.
- For packaging only: **JDK 17+** on `PATH` (Audiveris), **Inno Setup** (installer),
  and the Audiveris source (submodule).

## Environment

```bash
# 1. Clone with submodules (Audiveris / Homr / waifu2x / Real-ESRGAN live in submodules)
git submodule update --init --recursive

# 2. Virtual env
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS/Linux

# 3. Dependencies — pick ONE:
pip install -r requirements.lock.txt   # exact pins, reproducible (use this for parity)
pip install -r requirements.txt        # loose >= intent, resolves latest
```

`requirements.txt` expresses intent (loose `>=`); `requirements.lock.txt` is the
frozen, regression-tested set and is what CI and packaging use. Prefer the lock for
a working environment — resolving "latest" has bitten us before (a newer Flet build
broke GUI startup). To upgrade a dependency: bump it, run the full test + a real
conversion, then `pip freeze > requirements.lock.txt` (keep the header).

### ONNX Runtime variant (HOMR engine)

Three mutually-exclusive packages — install exactly one for your target:

| Package | Use |
|---|---|
| `onnxruntime-gpu` | NVIDIA GPU (CUDA) + CPU fallback — dev/CI default (in the lock) |
| `onnxruntime-directml` | any Windows GPU (DirectML) + CPU fallback — Windows distribution |
| `onnxruntime` | pure CPU — Linux / macOS |

HOMR ONNX weights are **not** in the repo; they download on demand into
`models/` on first use of the HOMR engine (or via the GUI's model-management buttons).

## Running

```bash
python app.py            # GUI (primary entry point)
flet run app.py          # GUI with hot-reload
python convert.py        # DEPRECATED headless/CLI entry — debugging/CI only, may lag the GUI
```

The GUI spawns `python run_webui.py --worker` subprocesses for each conversion batch and
talks to them via JSON-over-stdout. Heavy ML deps (onnxruntime, music21) load only in
the worker — keep them out of the GUI-process startup path.

## Tests

```bash
python -m pytest tests/ -q
```

`tests/` holds golden-file regression tests for the jianpu extraction logic (real
MusicXML fixtures under `tests/fixtures/`, byte-compared outputs). When a change to
`core/notation/` intentionally alters output, regenerate and **review the diff**:

```bash
REGEN_GOLDEN=1 python -m pytest tests/test_golden_jianpu.py   # then git diff the goldens
```

Any change touching conversion output should be validated against these goldens before
committing.

## Lint

```bash
ruff check .              # config in ruff.toml; must be clean
```

Optional local pre-commit gate (mirrors CI):

```bash
pip install pre-commit && pre-commit install
```

## CI

`.github/workflows/ci.yml` runs `ruff check` + `pytest tests/` on every push/PR to
`main` (ubuntu, Python 3.13, no submodules needed for the notation tests).

## Packaging (Windows)

```bash
python -m PyInstaller --noconfirm --clean SumisoraOMR.spec   # PyInstaller build
scripts/build_zip.bat                                        # portable zip
```

The full installer build (`scripts/build_installer.bat` + the Inno Setup script) is
a private distribution config kept out of the public repo; it additionally needs
Inno Setup, a JDK, and the Audiveris source. When releasing, bump `APP_VERSION` in
`core/config.py` and keep `version_info.txt` and the README badge in sync.

## Conventions

- **Commits**: Conventional Commits (`feat:`, `fix:`, `refactor:`, `chore:`, …).
- **Comment language**: English for public interfaces/docstrings and docs; 中文 is fine
  for complex-logic explanations and TODOs (see the table in `CLAUDE.md`).
- **Dependency direction** (no cycles): `config → utils → image/omr/notation/render → app → gui`.
- **Flet 0.85 gotchas**: `Dropdown` uses `on_select=`; modal dialogs and SnackBars use
  `page.show_dialog()` / `page.pop_dialog()` (not `page.open`); emphasis text uses
  `font_family=FONT_EMPHASIS`, never `weight=`. More in `CLAUDE.md`.
