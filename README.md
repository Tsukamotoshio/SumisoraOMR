# SumisoraOMR

> Batch-convert Western staff notation (PDF / PNG / JPG) to Jianpu (numbered musical notation) PDFs, with a built-in Jianpu editor and transposer.

[![Version: v0.3.4](https://img.shields.io/badge/Version-v0.3.4-green.svg)]()
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)]()
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)]()

**[中文说明 →](README.zh.md)**

---

## Features

- Batch-processes files from the `Input` folder; results land in `Output`
- Skips already-converted files automatically (hash-based deduplication)
- Optional MIDI generation alongside each Jianpu PDF
- Handles Chinese, Japanese, and other non-ASCII filenames
- Optional super-resolution upscaling to improve recognition on low-quality scans

---

## Engines

### OMR (Optical Music Recognition)

| Engine | Best for | Notes |
|--------|----------|-------|
| **Audiveris** | Digital PDFs | Reads PDFs exported from notation software (MuseScore, Sibelius, Finale, etc.). Fast and accurate on clean vector files — no GPU needed. |
| **Homr** | Scans & photos | AI-powered engine for PNG/JPG images of printed or photographed sheet music. Handles noise and real-world imperfections; GPU-accelerated (CUDA / DirectML). Model weights (~290 MB) are downloaded automatically on first use. |

> Auto mode picks **Audiveris** for PDF files and **Homr** for images (PNG/JPG) automatically.

### Super-Resolution (optional pre-processing)

| Engine | Notes |
|--------|-------|
| **Real-ESRGAN** | Default SR engine; higher-fidelity upscaling; anime-optimized models; Vulkan GPU-accelerated |
| **waifu2x-ncnn-vulkan** | Alternative SR engine; Vulkan GPU-accelerated |

---

## Usage

### Conversion

1. Launch the app — double-click the **SumisoraOMR** shortcut or run
   `SumisoraOMR.exe`.
2. In the file sidebar, click **Add Files** (multi-select) or **Add Folder**
   to import sheet music files (`.pdf`, `.png`, `.jpg`). Files are
   automatically copied into the `Input` folder and appear in the list.
3. Check the files you want to convert.
4. Choose an OMR engine (or leave it on **Auto**), then click **Start Conversion**.
5. Confirm options in the dialog (MIDI generation, skip duplicates) and
   click **Start Conversion**.
6. Converted Jianpu PDFs appear in the `Output` folder.

### Jianpu Preview

After conversion, switch to the **Jianpu Preview** tab to browse all
generated PDFs. Check files using the checkboxes, then click the export
button in the sidebar header to copy them to a folder of your choice.
Click **Re-render** to regenerate the current PDF directly from its
`.jianpu.txt` source without re-running recognition. Click **Edit** to
open the currently previewed file in the editor.

### Staff Score Preview

Switch to the **Staff Score Preview** tab to browse re-engraved
staff-notation PDFs. Supports MIDI playback and batch export to a chosen
directory. Click **Transpose** to open the transposer sub-page.

### Jianpu Editor

The editor lets you inspect and manually correct `.jianpu.txt` source files.
The left pane shows the original score image for reference; the right pane
contains editable Jianpu text. After editing, click **Regenerate PDF** to
rebuild the output.

### Transposer

Accessible from the Staff Score Preview page. Reads a MusicXML score from
`xml-scores/` and renders it in a different key. Three modes are available:

- **By interval** — choose a named interval (perfect 4th, major 3rd, etc.)
  and direction.
- **By key** — specify the target key directly; the app calculates the offset.
- **Diatonic** — shift notes by scale degree within the current key.

Settings auto-preview on any change. The result can be exported as a
staff-notation PDF.

---

## Running from Source

**Prerequisites**

- Python 3.10+
- JDK 17+ on `PATH` (required only for PDF recognition via Audiveris)
- The following runtime directories alongside the repo root:

  | Directory | Purpose |
  |-----------|---------|
  | `omr_engine/audiveris/` | Audiveris OMR engine — used for PDF inputs |
  | `lilypond-2.24.4/` | LilyPond engraving engine |
  | `jdk/` | Java runtime for Audiveris |
  | `omr_engine/homr/` | Homr deep-learning OMR engine — used for PNG/JPG inputs |
  | `waifu2x-ncnn-vulkan/` *(optional)* | waifu2x SR binary |
  | `realesrgan-runtime/` *(optional)* | Real-ESRGAN binary and models |

**Install dependencies**

```bash
pip install -r requirements.txt
```

**Run**

```bash
python app.py
```

For hot-reload during development:

```bash
flet run app.py
```

---

## Directory Layout

```
Input/                   # Drop source files here
Output/                  # Converted Jianpu PDFs and MIDIs
editor-workspace/        # Intermediate .jianpu.txt files for manual editing
xml-scores/              # MusicXML archives (used by the transposer)
models/                  # HOMR ONNX weights (downloaded on first use)
logs/                    # Runtime logs
THIRD_PARTY_NOTICES.md   # Third-party component licenses
```

---

## Known Limitations

- **Recognition accuracy** depends heavily on scan quality. Blurry or complex scores may produce wrong or missing notes.
- **Polyphony** — up to 4 independent voices are rendered as separate staves in both Jianpu and staff previews; very dense chords or more than 4 simultaneous voices may lose some notes.
- **No lyrics** — only note data is exported.
- **Processing speed** — Recognizing a single score can take several minutes; multi-page PDFs take longer.
- **Edge cases** — uncommon time signatures or mid-piece key changes may yield inaccurate results.

---

## Attribution

Integration, scripting, feature development, and packaging by **Tsukamotoshio**.

When redistributing, please retain this notice and `THIRD_PARTY_NOTICES.md` to distinguish:

- **Integration & packaging**: Tsukamotoshio
- **Third-party component copyrights & licenses**: remain with their respective authors (see `THIRD_PARTY_NOTICES.md`)

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)** — see the [`LICENSE`](LICENSE) file for details.

This tool bundles third-party components (Audiveris, Homr, LilyPond, music21, waifu2x-ncnn-vulkan, Real-ESRGAN, and others). Their respective licenses are listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
