# OMR-to-Jianpu

> Batch-convert Western staff notation (PDF / PNG / JPG) to Jianpu (numbered musical notation) PDFs, with optional MIDI output.

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
| **Homr** | Scans & photos | AI-powered engine for PNG/JPG images of printed or photographed sheet music. Handles noise and real-world imperfections; GPU-accelerated (CUDA / DirectML). |

> Auto mode picks **Audiveris** for PDF files and **Homr** for images (PNG/JPG) automatically.

### Super-Resolution (optional pre-processing)

| Engine | Notes |
|--------|-------|
| **waifu2x-ncnn-vulkan** | Default SR engine; Vulkan GPU-accelerated |
| **Real-ESRGAN** | Higher-fidelity upscaling; anime-optimized models; selectable in UI |

---

## Usage

1. Drop your staff notation files (`.pdf`, `.png`, `.jpg`) into the `Input` folder.
2. Launch the app — double-click the **简谱转换工具** shortcut or run `ConvertTool.exe`.
3. Follow the prompts to start conversion and choose whether to generate MIDI.
4. Converted files appear in the `Output` folder.

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
logs/                    # Runtime logs
THIRD_PARTY_NOTICES.md   # Third-party component licenses
```

---

## Known Limitations

- **Recognition accuracy** depends heavily on scan quality. Blurry or complex scores may produce wrong or missing notes.
- **Polyphony** — scores with many voices or dense chords may retain only the main melody.
- **No lyrics** — only note data is exported.
- **Processing speed** — Homr processes images in seconds; Audiveris startup is slow and multi-page PDFs can take several minutes.
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

This tool bundles third-party components (Audiveris, LilyPond, music21, waifu2x-ncnn-vulkan, Real-ESRGAN, and others). Their respective licenses are listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
