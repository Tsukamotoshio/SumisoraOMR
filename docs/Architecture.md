# Architecture and System Design Document
## OMR-to-Jianpu Conversion Tool

---

| Document Attribute | Value |
|--------------------|-------|
| **Project Title**  | OMR-to-Jianpu Conversion Tool |
| **Document Version** | 1.0 |
| **Date** | April 2026 |
| **Author** | Tsukamotoshio |
| **Status** | Draft |

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Architectural Goals and Constraints](#2-architectural-goals-and-constraints)
3. [System Overview](#3-system-overview)
4. [High-Level Architecture](#4-high-level-architecture)
5. [Module Design](#5-module-design)
6. [Data Design](#6-data-design)
7. [Technology Stack](#7-technology-stack)
8. [Deployment Architecture](#8-deployment-architecture)
9. [Quality Attribute Scenarios](#9-quality-attribute-scenarios)
10. [Key Design Decisions](#10-key-design-decisions)
11. [Known Limitations and Future Work](#11-known-limitations-and-future-work)

---

## 1. Introduction

### 1.1 Purpose

This document describes the software architecture and detailed design of the **OMR-to-Jianpu Conversion Tool**. It is intended for the developer, academic supervisors, and reviewers evaluating the project as an undergraduate Computer Science capstone. The document covers high-level structural decisions, module responsibilities, data flows, inter-module interfaces, and the rationale for key design choices.

### 1.2 Scope

The document covers the current production-ready version (v0.1.3) of the system, which is a Windows-desktop batch-conversion tool. It does not cover planned future components such as the GUI (v0.2.0), the interactive graphical Jianpu editor (v0.3.0), or the auto-update subsystem (v0.4.0).

### 1.3 Relationship to the SRS

This document complements the Software Requirements Specification (`docs/SRS.md`). The SRS defines *what* the system does; this document explains *how* it does it.

---

## 2. Architectural Goals and Constraints

The following quality attributes and constraints shaped the architectural decisions:

| Goal | Description |
|------|-------------|
| **Modularity** | The code was extracted from a single-file prototype (`convert.py`) into a `core/` package of single-responsibility modules without circular dependencies, to support future GUI and extensibility work. |
| **Zero-configuration deployment** | All external runtimes (JDK, Audiveris, LilyPond, waifu2x) are bundled. Users should not need to install anything beyond the installer package. |
| **Resilience** | A failure in one file's conversion should not abort the whole batch. Error handling is comprehensive and per-file. |
| **Extensibility** | A new OMR engine (Oemer) was added with minimal change to the pipeline by defining a common `OMREngine` enum and a common sub-process entry point. The same pattern allows further engines to be plugged in. |
| **Windows compatibility** | Unicode filename handling, ANSI-escape terminal resizing, and `msvcrt`-based single-keypress input are explicitly designed for the Windows environment while degrading gracefully on POSIX. |
| **Security** | All subprocess calls use list-form arguments (no shell string expansion). File path operations use `pathlib.Path` throughout. |

---

## 3. System Overview

The tool implements a **linear processing pipeline**: a score image enters at one end and a Jianpu PDF exits at the other. The pipeline consists of five conceptual stages:

```
┌──────────────┐     ┌────────────────────┐      ┌─────────────────┐
│  Image Input │────▶│  Pre-Processing   │────▶│  OMR Engine     │
│  (PDF/PNG/   │     │  (waifu2x, Pillow) │      │  (Audiveris /   │
│   JPG)       │     │                    │      │   Oemer)        │
└──────────────┘     └────────────────────┘      └────────┬────────┘
                                                          │ MusicXML
                                                          ▼
                                                 ┌─────────────────┐
                                                 │  Notation       │
                                                 │  Parsing        │
                                                 │  (music21)      │
                                                 └────────┬────────┘
                                                          │ JianpuNote[]
                                                          ▼
                                       ┌──────────────────────────────┐
                                       │  Jianpu Conversion & Render  │
                                       │  (jianpu_core + renderer)    │
                                       └──────────────┬───────────────┘
                                                      │
                                         ┌────────────┴────────────┐
                                         │                         │
                                         ▼                         ▼
                                   Jianpu PDF               MIDI (optional)
                                   (Output/)                 (Output/)
```

A second, independent workflow allows users to **manually correct** the intermediate `.jianpu.txt` representation and regenerate the PDF without re-running OMR:

```
editor-workspace/*.jianpu.txt  ──▶  Jianpu Editor (TUI)  ──▶  PDF re-render
```

---

## 4. High-Level Architecture

### 4.1 Package Structure

```
Project_Convert/
├── convert.py                  # Entry-point shim: imports core.pipeline.main
│
└── core/                       # Core package (all business logic)
    ├── __init__.py
    ├── config.py               # Constants, enumerations, dataclasses, logger
    ├── utils.py                # Foundation utilities, file ops, history, font
    ├── image_preprocess.py     # Image enhancement: waifu2x + Pillow
    ├── runtime_finder.py       # External-tool discovery, subprocess helpers
    ├── audiveris_runner.py     # Audiveris OMR subprocess management
    ├── oemer_runner.py         # Oemer OMR subprocess management (experimental)
    ├── jianpu_core.py          # Note/measure conversion, .jianpu.txt builder
    ├── jianpu_txt_editor.py    # .jianpu.txt parse/serialise/edit data model
    ├── renderer.py             # LilyPond / ReportLab PDF rendering, MIDI export
    ├── pipeline.py             # Batch orchestration, main() entry point
    └── tui.py                  # Rich TUI state machine
```

`convert.py` is a single-line entry point that delegates entirely to `core.pipeline.main()`. This design allows PyInstaller to create an executable from `convert.py` while keeping all logic in the testable `core/` package.

### 4.2 Module Dependency Graph

The import hierarchy forms a strict DAG (directed acyclic graph) with no circular dependencies:

```
                      ┌──────────────┐
                      │  config.py   │  (no intra-package imports)
                      └──────┬───────┘
                             │
                      ┌──────▼───────┐
                      │   utils.py   │  (imports config only)
                      └──────┬───────┘
                    ┌─────────┴──────────┐
                    │                    │
           ┌────────▼───────┐   ┌────────▼──────────┐
           │image_preprocess│   │  runtime_finder   │
           │     .py        │   │      .py          │
           └────────┬───────┘   └────────┬──────────┘
                    └──────┬─────────────┘
                           │
                  ┌────────▼────────┐
                  │audiveris_runner │
                  │   / oemer_runner│
                  └────────┬────────┘
                           │
                  ┌────────▼────────┐
                  │  jianpu_core.py │
                  └────────┬────────┘
                           │
                  ┌────────▼────────┐
                  │  renderer.py    │
                  └────────┬────────┘
                           │
                  ┌────────▼────────┐
                  │  pipeline.py    │◀───── tui.py
                  └─────────────────┘
```

`tui.py` imports `pipeline.py` to trigger batch runs and `jianpu_txt_editor.py` / `renderer.py` for the editor workflow.

---

## 5. Module Design

### 5.1 `core/config.py` — Configuration and Data Model

**Responsibility**: Define all application-wide constants, the shared `LOGGER` instance, and the core data structures used throughout the pipeline.

**Key exports**:

| Symbol | Type | Description |
|--------|------|-------------|
| `APP_VERSION` | `str` | Semantic version string (e.g., `'0.1.3'`) |
| `CONVERSION_PIPELINE_VERSION` | `int` | Incremented on breaking pipeline changes; drives cache invalidation |
| `JIANPU_MAP` | `dict[str, str]` | Maps pitch class letters (C–B) to Jianpu numerals (1–7) |
| `SUPPORTED_INPUT_SUFFIXES` | `set[str]` | `{'.pdf', '.png', '.jpg', '.jpeg'}` |
| `ALLOWED_JIANPU_DURATIONS` | `list[float]` | Valid quarter-note lengths; used for duration quantisation |
| `AppConfig` | `dataclass` | Runtime configuration (paths, flags, engine choice) assembled by `pipeline.py` |
| `ConversionSummary` | `dataclass` | Mutable counters (total, success, skip, fail) for post-run reporting |
| `JianpuNote` | `dataclass` | Intermediate representation of a single parsed note |
| `OMREngine` | `Enum` | `AUDIVERIS` \| `OEMER` |

**Design note**: `config.py` has no intra-package imports. This makes it the safe root of the dependency graph and allows other modules to import it without risk of circular references.

---

### 5.2 `core/utils.py` — Foundation Utilities

**Responsibility**: Provide reusable file operations, Unicode-safe filename handling, logging setup, conversion history persistence, font detection, and path-finding helpers.

**Key functions**:

| Function | Purpose |
|----------|---------|
| `get_app_base_dir()` | Return the application root (parent of `core/`); works in both source and PyInstaller packaged modes |
| `build_runtime_paths(base)` | Construct an `AppConfig` by locating `Input/`, `Output/`, `editor-workspace/`, `logs/`, and runtime directories |
| `setup_logging(log_dir)` | Configure `logging.FileHandler` with rotating output to `logs/` |
| `load_conversion_history()` / `save_conversion_history()` | Read/write `conversion_history.json` |
| `update_conversion_history(record)` | Append or update a single record in the history |
| `confirm_skip_all_existing()` | Return `True` if all input files are already in history with valid outputs |
| `build_safe_ascii_name(stem)` | Transliterate non-ASCII characters to underscores for safe subprocess use |
| `safe_remove_file()` / `safe_remove_tree()` | Delete files/directories silently ignoring permission errors |
| `register_pdf_font()` | Register a TrueType font with ReportLab |
| `resolve_lilypond_font_name()` | Return the first available CJK font from `CJK_FONT_CANDIDATES` |

**Persistent state**: `LOG_FILE_PATH` is a module-level mutable global (set once at startup) rather than a constant in `config.py`, because its value depends on the runtime-discovered application directory.

---

### 5.3 `core/image_preprocess.py` — Image Enhancement

**Responsibility**: Improve OMR success rate for low-quality or low-resolution scans by applying a sequence of image processing operations.

**Processing pipeline** (per image):

```
Input image
    │
    ├─[if short side < 1200px AND waifu2x available]──▶ waifu2x 2× super-resolution
    │                                                        │
    │◀───────────────────────────────────────────────────────┘
    │
    ├─[Gaussian blur to reduce scanning noise]
    │
    ├─[Laplacian stddev < 30.0?]──▶ aggressive Unsharp Mask sharpening
    │                                
    ├─[Brightness / contrast auto-normalisation (CLAHE-style)]
    │
    ├─[if total pixels > 20,000,000]──▶ downscale to fit Audiveris limit
    │
    └──▶ processed image (PNG, temporary file)
```

**Key design decisions**:
- waifu2x is invoked as an external sub-process; its absence is gracefully handled.
- Sharpness is measured from a 500 × 500 thumbnail (fast) rather than the full image.
- The Audiveris pixel cap (20 MP) is enforced here rather than relying on Audiveris's own error handling, which is less informative.
- `HAS_PILLOW` and `HAS_NUMPY` flags allow the module to degrade gracefully when optional libraries are absent.

---

### 5.4 `core/runtime_finder.py` — External Tool Discovery

**Responsibility**: Locate all required external executables (Java, Audiveris, LilyPond, waifu2x) at runtime; run subprocesses with a progress spinner.

**Search strategy for each tool**:

1. Application bundle directories (`package-assets/<tool>-runtime/`, `<tool>/`)
2. Well-known Windows install paths (e.g., `%ProgramFiles%\Audiveris\`)
3. `JAVA_HOME` / `PATH` environment variables

The multi-location search allows the packaged installer layout and the developer source layout to coexist without hardcoded paths.

**`run_subprocess_with_spinner(cmd, timeout)`**: Launches a subprocess and renders an animated terminal spinner while waiting. Returns `(return_code, stdout, stderr)`. Enforces the timeout specified in config.

**`render_jianpu_ly(ly_text, output_pdf)`**: Invokes the `jianpu-ly.py` script (downloading it on first use) and then LilyPond to produce a PDF. Both are invoked as separate sub-processes.

---

### 5.5 `core/audiveris_runner.py` — Audiveris OMR

**Responsibility**: Prepare a safe working environment for Audiveris and execute it as a Java sub-process.

**Key steps**:

1. **`prepare_audiveris_paths(input_path, output_dir)`**: Copies input to an ASCII-safe filename (`<stem>_<sha1token><ext>`) in an isolated job directory (`audiveris_job_<token>/`). This prevents Audiveris from failing on non-ASCII paths.

2. **`run_audiveris_batch(source_file, output_dir)`**: Optionally pre-processes the image, then invokes Audiveris via Java:
   ```
   java -jar audiveris.jar -batch -export -output <dir> -- <input_file>
   ```
   Returns the path to the output directory containing MusicXML, or `None` on failure.

3. A copy of the pre-processed reference image is stored in the job directory for diagnostic inspection.

**Timeout handling**: Audiveris is given up to `MAX_AUDIVERIS_SECONDS` (1800 s) before the process is killed and the file is marked as failed.

---

### 5.6 `core/oemer_runner.py` — Oemer OMR (Experimental)

**Responsibility**: Wrap the `oemer` command-line tool as an alternative OMR engine.

Oemer only accepts image inputs. When given a PDF, the runner renders the first page to a temporary PNG using `pdftoppm` or Pillow's PDF backend before invoking Oemer.

The module exposes the same contract as `audiveris_runner`: return the output directory path on success or `None` on failure, allowing `pipeline.py` to remain engine-agnostic.

---

### 5.7 `core/jianpu_core.py` — Notation Conversion

**Responsibility**: Convert music21 note objects into Jianpu tokens and lay out the score in measures.

**Core algorithms**:

- **`duration_suffix(q_len, dots)`**: Maps a quarter-note-length float to the jianpu-ly text suffix notation (dashes for beats held, underscores for subdivisions, dots for augmentation).
- **`get_duration_render(duration, dots)`**: Returns `(dashes, underlines, right_dots)` as integers for direct PDF drawing.
- **`parse_score_to_jianpu(score)`**: Iterates over music21 `Part` and `Measure` objects. For each note, creates a `JianpuNote` dataclass; for chords, selects the highest pitch.
- **`choose_measures_per_line(measures, page_width_pts)`**: Estimates the rendered width of each measure and packs measures into lines to fit within an A4 page width.
- **`build_jianpu_ly_text(measures, meta)`**: Serialises parsed measures into the jianpu-ly input text format for the LilyPond pathway.
- **`extract_jianpu_measures(score)`**: Higher-level function returning a list of measure lists for the direct-render pathway.

---

### 5.8 `core/jianpu_txt_editor.py` — Intermediate Text Format

**Responsibility**: Define the `.jianpu.txt` data model and provide parse/serialise operations.

**Data model**:

```
JianpuDocument
├── meta: dict[str, str]         ← title, composer, key, time, tempo
└── measures: list[JianpuMeasure]
    └── notes: list[JianpuTxtNote]
        ├── symbol: str          ← '1'–'7' | '0'
        ├── accidental: str      ← '' | '#' | 'b'
        ├── upper_dots: int      ← octave-up count (')
        ├── lower_dots: int      ← octave-down count (,)
        ├── dashes: int          ← beat-extension count (-)
        ├── underlines: int      ← subdivision count (_)
        └── aug_dot: bool        ← augmentation dot (.)
```

The `JianpuDocument` model is designed to be directly serialisable to JSON and is intended as the data transfer object for the planned GUI editor in v0.3.0.

**Parser**: A single-pass tokeniser scans the `[score]` section, classifying each whitespace-separated token against the grammar defined in the `.jianpu.txt` format specification.

---

### 5.9 `core/renderer.py` — PDF and MIDI Rendering

**Responsibility**: Take the parsed Jianpu data and produce final output files.

**Two rendering pathways**:

#### 5.9.1 LilyPond Pathway

```
JianpuNote[] → build_jianpu_ly_text() → .ly text
                                           │
                                    jianpu-ly.py (converts to LilyPond format)
                                           │
                                    LilyPond subprocess
                                           │
                                    Output PDF
```

LilyPond produces the highest publication-quality output. The intermediate `.ly` file is sanitised to strip the `Music engraving by LilyPond` footer line before rendering.

#### 5.9.2 Direct ReportLab Pathway

```
JianpuNote[] → extract_jianpu_measures() → layout algorithm
                                                │
                                         ReportLab canvas (A4)
                                                │
                                         Output PDF
```

This pathway does not require LilyPond and is used as a fallback if LilyPond is unavailable, or for programmatic rendering from the text editor workflow.

**MIDI export** (`render_midi_from_score`): Delegates entirely to `music21.stream.Stream.write('midi', ...)`.

---

### 5.10 `core/pipeline.py` — Batch Orchestration

**Responsibility**: Orchestrate the full conversion pipeline for a batch of input files. This is the central coordinator module.

**`process_single_input_to_jianpu(source_file, ...)`**:

```
1. Select engine (AUDIVERIS or OEMER)
2. Invoke engine → MXL output directory
3. find_first_musicxml_file() → locate .mxl file
4. music21.converter.parse() → Score object
5. [optional] render MIDI
6. generate_jianpu_pdf_from_mxl() → PDF
7. [if editor_workspace_dir] copy .jianpu.txt and source to editor-workspace/
8. Return True (success) or False (failure)
```

**`main()`**:

```
1. get_app_base_dir() + build_runtime_paths()
2. setup_logging()
3. Read AppConfig (engine, MIDI flag, etc.)
4. collect_duplicate_names() — warn on name conflicts
5. Load conversion_history
6. For each file in Input/:
     a. is_supported_score_file() check
     b. SHA-256 hash → check history → skip if current
     c. Create per-file temp directory
     d. process_single_input_to_jianpu()
     e. update_conversion_history()
     f. safe_remove_tree(temp_dir)
7. print_conversion_summary()
8. os.startfile(output_dir)  — open Output/ in Explorer
```

---

### 5.11 `core/tui.py` — Terminal User Interface

**Responsibility**: Implement the interactive user interface as a state machine rendered with the Rich library.

**State machine**:

```
             ┌──────────────┐
             │  MAIN_MENU   │◀──────────────────────┐
             └──────┬───────┘                        │
         ┌──────────┼──────────┬────────────┐        │
         ▼          ▼          ▼            ▼        │
   [1] CONVERT  [2] EDITOR  [3] CLEAR  [4] EXIT      │
         │          │          │                     │
         │          │          └──── clear history ──┤
         │          │                                │
         │     ┌────▼──────┐                         │
         │     │ FILE_LIST │                         │
         │     └────┬──────┘                         │
         │          │ (select file)                  │
         │     ┌────▼──────┐                         │
         │     │EDIT_PROMPT│──── re-render PDF ──────┤
         │     └───────────┘
         │
    ┌────▼─────────────────────┐
    │ ENGINE_SELECT → MIDI?    │
    │ → calls pipeline.main()  │
    │ → shows summary          │
    └──────────────────────────┘
```

**Key implementation details**:
- Each "screen" is a dedicated method (e.g., `_screen_main_menu()`, `_screen_editor_list()`).
- Terminal size is fixed to 43 × 30 on launch via both VT escape sequence and Win32 `mode con` command.
- Single-key input uses `msvcrt.getwch()` (Windows) or `termios`/`tty.setraw()` (POSIX), with a line-input fallback for piped stdin.
- Rich `Panel`, `Rule`, and styled text provide consistent visual framing without external CSS or HTML.

---

## 6. Data Design

### 6.1 Intermediate Representations

The pipeline passes data between stages through the following intermediate forms:

```
Input file (binary)
    ↓ [image_preprocess]
Pre-processed image (PNG, temp file)
    ↓ [audiveris_runner / oemer_runner]
MusicXML / .mxl file (temp directory)
    ↓ [music21 parser in renderer.py]
music21 Score object (in-memory)
    ↓ [jianpu_core.parse_score_to_jianpu]
list[list[JianpuNote]]  (measures → notes, in-memory)
    ↓ [jianpu_core.build_jianpu_ly_text or extract_jianpu_measures]
JianpuDocument (.jianpu.txt, persisted to editor-workspace/)
    ↓ [renderer.py]
Output PDF (persisted to Output/)
```

### 6.2 `conversion_history.json`

The history file is a JSON object keyed by input SHA-256 hash:

```json
{
  "<sha256-hex>": {
    "input_filename": "beethoven_op27.pdf",
    "output_filename": "beethoven_op27.pdf",
    "timestamp": "2026-04-05T14:23:01",
    "pipeline_version": 5
  }
}
```

### 6.3 `JianpuNote` Dataclass (`core/config.py`)

```python
@dataclass
class JianpuNote:
    numeral: str          # '1'–'7' or '0' (rest)
    octave_shift: int     # negative = lower octave, positive = upper octave
    duration: float       # quarter-note length (quantised)
    dots: int             # augmentation dot count (0 or 1)
    accidental: str       # '' | '#' | 'b'
```

### 6.4 `AppConfig` Dataclass (`core/config.py`)

```python
@dataclass
class AppConfig:
    base_dir: Path
    input_dir: Path
    output_dir: Path
    editor_workspace_dir: Path
    log_dir: Path
    engine: OMREngine
    generate_midi: bool
    audiveris_jar: Optional[Path]
    java_exe: Optional[Path]
    lilypond_exe: Optional[Path]
```

---

## 7. Technology Stack

### 7.1 Core Dependencies

| Library / Tool | Version | Role |
|----------------|---------|------|
| **Python** | ≥ 3.10 | Application language |
| **music21** | ≥ 9.9.1 | MusicXML parsing, MIDI export |
| **Pillow** | ≥ 12.2.0 | Image processing (blur, sharpen, resize) |
| **ReportLab** | ≥ 4.4.10 | Direct PDF canvas drawing |
| **Rich** | ≥ 14.3.3 | TUI (panels, spinners, styled text) |
| **Audiveris** | 5.10.2 | OMR engine (Java, bundled) |
| **LilyPond** | 2.24.4 | Music engraving → PDF (bundled) |
| **jianpu-ly.py** | Latest | Jianpu text → LilyPond input converter |
| **waifu2x-ncnn-vulkan** | Bundled | GPU super-resolution (image pre-processing) |
| **OpenJDK** | 21 (bundled) | Java runtime for Audiveris |
| **PyInstaller** | ≥ 6.19.0 | Windows packaging (convert.py → ConvertTool.exe) |

### 7.2 Build and Packaging

| Tool | Purpose |
|------|---------|
| **PyInstaller** | Bundles Python + `core/` + dependencies into a standalone `.exe` |
| **Inno Setup** (`convert_setup.iss`) | Creates the Windows installer (.exe) from the PyInstaller output |
| **scripts/build_installer.bat** | Orchestrates the full PyInstaller → Inno Setup build pipeline |
| **scripts/build_zip.bat** | Creates a portable zip distribution |

### 7.3 Technology Selection Rationale

**Why Audiveris?** Audiveris is the most mature open-source OMR engine, with active maintenance and explicit support for exporting MusicXML. Its major limitation is requiring Java and its slow startup time (~15 s). The decision to bundle the JDK eliminates the "install Java" barrier for end users.

**Why LilyPond?** LilyPond produces publication-quality typeset music. The `jianpu-ly.py` script (by Alex Reeves / Silas Brown) provides a well-maintained Jianpu-to-LilyPond converter. Using it avoids reimplementing the complex typography rules for Jianpu (beam groupings, slurs, accidental placement).

**Why Rich for TUI?** Rich provides cross-platform ANSI rendering, Unicode box-drawing, and a well-tested spinner implementation with a high-level API. This allowed rapid development of a professional-looking interface without reimplementing terminal control codes.

**Why music21?** music21 is the standard Python library for music analysis. It supports MusicXML parsing, pitch/duration extraction, and MIDI export, covering all the functionality needed by the pipeline with a single dependency.

**Why PyInstaller + Inno Setup?** PyInstaller is the most widely used Python packager; Inno Setup is the industry-standard Windows installer builder. Both are free and open-source, making the build reproducible without commercial tooling.

---

## 8. Deployment Architecture

### 8.1 Packaged Distribution Layout

After installation, the application directory has the following structure:

```
ConvertTool-0.2.0/
├── ConvertTool.exe              ← PyInstaller-bundled executable (or app.py for source)
├── Input/                       ← User places input files here
├── Output/                      ← Converted outputs are saved here
├── xml-scores/                  ← MusicXML files from recognition (auto-created; used by Transposer)
├── editor-workspace/            ← Intermediate .jianpu.txt / .ly / .pdf files
├── logs/                        ← Runtime log files
├── conversion_history.json      ← Persistent conversion cache
├── package-assets/
│   ├── audiveris-runtime/       ← Audiveris JAR + Tesseract OCR data
│   ├── lilypond-runtime/        ← LilyPond binaries
│   ├── waifu2x-runtime/         ← waifu2x-ncnn-vulkan executable + models
│   └── tessdata/                ← Tesseract language data (used by Audiveris)
├── jdk/                         ← Bundled OpenJDK 21
└── 读我.md / README_EN.txt      ← User documentation
```

### 8.2 Runtime Search Strategy

When locating external tools at runtime, `runtime_finder.py` searches the following roots in order:

1. `sys._MEIPASS` (PyInstaller extraction temp directory, for bundled assets)
2. `app_base_dir / 'package-assets'`  (post-install layout)
3. Common Windows install paths (e.g., `%ProgramFiles%\Audiveris`)
4. `JAVA_HOME` environment variable
5. System `PATH`

This strategy ensures the same code works in both the packaged release and the developer source checkout.

### 8.3 Deployment Diagram

```
Windows Machine
┌─────────────────────────────────────────────────┐
│                                                 │
│  ConvertTool.exe (PyInstaller bundle)           │
│  ┌───────────────────────────────────────────┐  │
│  │  Python runtime (embedded)                │  │
│  │  core/ package                            │  │
│  │  music21, Pillow, ReportLab, Rich         │  │
│  └───────────────────────────────────────────┘  │
│                                                 │
│  External processes (launched as sub-processes):│
│  ┌──────────────┐  ┌──────────┐  ┌──────────┐   │
│  │  jdk/java    │  │LilyPond  │  │ waifu2x  │   │
│  │  + Audiveris │  │ 2.24.4   │  │-ncnn-    │   │
│  │    5.10.2    │  │          │  │ vulkan   │   │
│  └──────────────┘  └──────────┘  └──────────┘   │
│                                                 │
│  File system:                                   │
│  Input/ → [pipeline] → Output/                  │
│  editor-workspace/ ← intermediate .jianpu.txt   │
│  logs/  conversion_history.json                 │
└─────────────────────────────────────────────────┘
```

---

## 9. Quality Attribute Scenarios

### 9.1 Performance

| Scenario | Condition | Required Response |
|----------|-----------|-------------------|
| Single-page PDF conversion | Mid-range PC (i5, 8 GB RAM, SSD) | < 3 minutes end-to-end |
| Image pre-processing (no waifu2x) | Any supported image | < 10 seconds per page |
| TUI screen transition | Any state change | < 200 ms visual response |
| Batch of 10 files | Sequential processing | No memory leak; stable RSS |

### 9.2 Reliability

| Scenario | Fault | Expected Behaviour |
|----------|-------|--------------------|
| Audiveris produces empty output | Score unrecognisable | Warning logged; batch continues |
| LilyPond not found | Missing runtime | Fallback to ReportLab pathway |
| waifu2x GPU unavailable | No Vulkan GPU | Pre-processing skipped; OMR still attempted |
| Input file removed mid-run | Race condition | Graceful skip with warning |
| JSON history file corrupted | Malformed JSON | History treated as empty; re-conversion proceeds |

### 9.3 Maintainability

| Scenario | Change | Impact |
|----------|--------|--------|
| Add new OMR engine | New `_runner.py` module | Only `OMREngine` enum and `pipeline.py`'s dispatch need updating |
| Change LilyPond version | Update bundled binary | No source code change required |
| Bump pipeline version | Change `CONVERSION_PIPELINE_VERSION` | All cached outputs automatically invalidated |
| Add new metadata field to `.jianpu.txt` | Add field to `DEFAULT_META` | Parser tolerates unknown fields; backward-compatible |

---

## 10. Key Design Decisions

### 10.1 Pipeline Version–Driven Cache Invalidation

Instead of a timestamp-based or manual "clear cache" policy, the system uses a `CONVERSION_PIPELINE_VERSION` integer. Any time the conversion logic changes in a way that would produce different output (e.g., improved duration quantisation, new Jianpu notation rules), the developer bumps this integer. On next run, all files whose cached pipeline version is less than the current value are automatically re-processed. This provides a simple, deterministic way to ensure output stays consistent with the current algorithm.

### 10.2 ASCII-Safe Filename Copying

Both Audiveris and LilyPond have historically failed silently or erroneously when given non-ASCII file paths on Windows. Rather than patching the upstream tools, the system copies input files to ASCII-safe filenames before processing (using a SHA-1 token to avoid name collisions) and discards the copies after processing. This approach is robust to future tool upgrades.

### 10.3 Dual Rendering Pathways

The LilyPond rendering pathway produces higher-quality typeset output but requires three external programs (jianpu-ly.py, Python, LilyPond) to be invoked correctly. The ReportLab pathway is simpler and more controllable but produces lower-quality layout. Maintaining both allows the system to remain functional even if the LilyPond pathway fails, and gives the future GUI editor a fast rendering path that does not spin up LilyPond for every keystroke.

### 10.4 State Machine TUI

The TUI is implemented as an explicit state machine rather than a linear input-prompt loop. Each state has a dedicated screen-rendering method and transitions are modelled as method calls returning new state names. This makes adding new screens straightforward and avoids deeply nested conditional input-handling logic.

### 10.5 Separation of `jianpu_txt_editor.py` from `jianpu_core.py`

`jianpu_core.py` depends on `music21` (a large dependency) and operates on `music21.stream.Stream` objects produced by the OMR pipeline. `jianpu_txt_editor.py` operates solely on plain text and has no dependency on music21. This separation means the GUI editor in v0.3.0 can load, display, and modify notation independently of the OMR pipeline, which is important for a smooth editing experience.

---

## 11. Known Limitations and Future Work

### 11.1 Current Limitations

| Limitation | Technical Cause | Planned Fix |
|------------|----------------|-------------|
| Single-voice / top-voice only | music21 polyphonic extraction is non-trivial; Jianpu convention for multi-voice is complex | Addressed in v0.3 editor |
| No lyrics | `ENABLE_LYRICS_OUTPUT = False` in config | Planned for a future version |
| Slow startup (Audiveris / JVM) | JVM cold-start overhead | Persistent Audiveris server mode explored for GUI |
| Windows only (packaged) | PyInstaller target | Linux/macOS source mode works; cross-platform installer not planned |

### 11.2 Roadmap Summary

| Version | Focus | Key Additions |
|---------|-------|---------------|
| v0.1.3 | Stability | TUI polish, waifu2x fix, text editor foundation |
| v0.2.0 | Usability | Full GUI (Tkinter/Swing-inspired); Oemer engine promotion |
| v0.2.2 | Lifecycle | GitHub Releases update checker |
| v0.3.0 | Editing | Graphical Jianpu editor (click-to-edit notes) |
| v0.4.0 | Auto-update | Zip-based self-update with hash verification |

---

## Appendix A — Data Flow Diagram (Level 1)

```
┌─────────────┐     Files      ┌────────────────────────────────────────────────┐
│  Input/     │───────────────▶│                 pipeline.main()               │
│  (PDF/PNG/  │                │                                                │
│   JPG)      │                │  ┌──────────┐    ┌─────────────┐               │
└─────────────┘                │  │  Hash    │──▶│  History    │               │
                               │  │  check   │    │  lookup     │               │
                               │  └──────────┘    └──────┬──────┘               │
                               │                        │ [miss]                │
                               │               ┌────────▼──────────────────┐    │
                               │               │  image_preprocess.py      │    │
                               │               │  (waifu2x + Pillow)       │    │
                               │               └────────────┬──────────────┘    │
                               │                            │ processed image   │
                               │               ┌────────────▼──────────────┐    │
                               │               │  audiveris_runner.py /    │    │
                               │               │  oemer_runner.py (OMR)    │    │
                               │               └────────────┬──────────────┘    │
                               │                            │ MusicXML          │
                               │               ┌────────────▼──────────────┐    │
                               │               │  renderer.py               │   │
                               │               │  (music21 parse →          │   │
                               │               │   jianpu_core →            │   │
                               │               │   LilyPond / ReportLab)    │   │
                               │               └──────┬────────────┬────────┘   │
                               │                      │            │            │
                               │                      ▼            ▼            │
                               │               .jianpu.txt      PDF + MIDI      │
                               │               (editor-         (Output/)       │
                               │                workspace/)                     │
                               └────────────────────────────────────────────────┘
```

---

## Appendix B — Module Interface Summary

| Module | Public Interface (key functions) | Called By |
|--------|----------------------------------|-----------|
| `config.py` | Constants, `AppConfig`, `JianpuNote`, `OMREngine` | All modules |
| `utils.py` | `get_app_base_dir`, `build_runtime_paths`, `load/save_conversion_history`, `build_safe_ascii_name` | All modules |
| `image_preprocess.py` | `preprocess_image_for_omr`, `find_waifu2x_executable`, `fit_image_within_pixel_limit` | `audiveris_runner` |
| `runtime_finder.py` | `find_java_executable`, `ensure_audiveris_executable`, `find_lilypond_executable`, `run_subprocess_with_spinner`, `render_jianpu_ly` | `audiveris_runner`, `renderer` |
| `audiveris_runner.py` | `run_audiveris_batch`, `prepare_audiveris_paths` | `pipeline` |
| `oemer_runner.py` | `run_oemer_batch`, `check_oemer_available` | `pipeline` |
| `jianpu_core.py` | `parse_score_to_jianpu`, `build_jianpu_ly_text`, `choose_measures_per_line`, `extract_jianpu_measures` | `renderer` |
| `jianpu_txt_editor.py` | `JianpuDocument`, `parse_jianpu_txt`, `serialise_jianpu_txt`, `JianpuTxtNote` | `tui`, `renderer` |
| `renderer.py` | `generate_jianpu_pdf_from_mxl`, `create_pdf`, `render_midi_from_score` | `pipeline`, `tui` |
| `pipeline.py` | `main`, `process_single_input_to_jianpu` | `convert.py`, `tui` |
| `tui.py` | `run_tui` | `convert.py` |

---

*End of Architecture and System Design Document*
