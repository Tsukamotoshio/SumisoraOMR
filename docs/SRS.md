# Software Requirements Specification (SRS)
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
2. [Overall Description](#2-overall-description)
3. [Specific Requirements](#3-specific-requirements)
4. [External Interface Requirements](#4-external-interface-requirements)
5. [Non-Functional Requirements](#5-non-functional-requirements)
6. [Constraints and Assumptions](#6-constraints-and-assumptions)
7. [Appendices](#7-appendices)

---

## 1. Introduction

### 1.1 Purpose

This document specifies the software requirements for the **OMR-to-Jianpu Conversion Tool**, a desktop application that automatically converts Western staff notation scores into **Jianpu** (numbered musical notation, 简谱). The intended readership includes the developer, academic supervisors, and evaluators assessing the project as a Computer Science undergraduate capstone.

### 1.2 Scope

The tool accepts scanned or digitally-produced sheet music files in PDF, PNG, or JPEG format and outputs Jianpu-format PDF documents. An optional MIDI file can also be generated. The system is designed to be self-contained on Microsoft Windows, requiring no prior musical knowledge or technical expertise from the end user.

The product is named **ConvertTool** in its packaged form and distributed as a Windows installer.

**In-scope:**
- Optical Music Recognition (OMR) of Western staff notation
- Automated conversion of recognised notation into Jianpu symbols
- High-quality PDF rendering of Jianpu scores
- Optional MIDI export
- Image pre-processing for low-quality scans
- A menu-driven terminal user interface (TUI)
- A text-based Jianpu editor for manual correction

**Out-of-scope:**
- Audio input (microphone, MIDI keyboard) or real-time transcription
- Playback of audio from within the application
- Recognition of non-Western notation systems other than Jianpu output
- Cloud-based processing or network functionality (beyond optional update checks planned for a future version)
- Guitar tablature, drum notation, or chord chart output

### 1.3 Definitions, Acronyms, and Abbreviations

| Term | Definition |
|------|------------|
| **Jianpu (简谱)** | A system of numbered musical notation widely used in China and other East Asian countries. Notes are represented by the numerals 1–7 corresponding to the solfège syllables do–si. |
| **OMR** | Optical Music Recognition — the process of automatically extracting musical information from images of sheet music, analogous to OCR for text. |
| **MXL / MusicXML** | An open XML-based file format for exchanging digital sheet music between applications. |
| **LilyPond** | An open-source music engraving programme that produces publication-quality PDF scores from a plain-text input language. |
| **Audiveris** | An open-source OMR engine written in Java that converts image-based scores into MusicXML. |
| **Oemer** | An experimental deep-learning-based OMR engine available as a Python package. |
| **waifu2x** | A GPU-accelerated super-resolution programme originally developed for anime artwork; used here to upscale low-resolution score images before OMR. |
| **music21** | A Python toolkit for computer-aided musicology, used here to parse MusicXML and extract note data. |
| **TUI** | Terminal User Interface — a text-based interactive interface rendered in a terminal window, here implemented with the *Rich* library. |
| **SRS** | Software Requirements Specification — this document. |
| **PDF** | Portable Document Format. |
| **MIDI** | Musical Instrument Digital Interface — a protocol for representing musical events. |
| **PyInstaller** | A tool that bundles Python applications and their dependencies into standalone executables. |

### 1.4 Overview

Section 2 gives a high-level description of the product and its context. Section 3 details functional requirements. Section 4 covers external interfaces. Section 5 defines non-functional requirements. Section 6 lists constraints and assumptions. Section 7 provides appendices including a glossary and a use-case summary.

---

## 2. Overall Description

### 2.1 Product Perspective

The project addresses a practical need within Chinese music communities: performers and educators who read Jianpu but not Western staff notation lack tooling to convert the large corpus of available PDF scores automatically. Existing manual or commercial solutions are either expensive, require expertise with professional notation software (e.g., Sibelius, MuseScore), or produce output unsuitable for Jianpu readers.

This tool sits at the intersection of music information retrieval, document processing, and user experience engineering. It integrates multiple existing open-source engines (Audiveris, LilyPond, jianpu-ly.py, waifu2x) into a single, zero-configuration Windows application.

The system is **standalone**: it ships all required runtimes (Java JDK, Audiveris, LilyPond, waifu2x). Users do not need to install any of these separately.

### 2.2 Product Functions

The following summarises the major functions the tool provides:

| ID | Function |
|----|----------|
| F-01 | Accept PDF, PNG, and JPEG input files via an `Input/` folder |
| F-02 | Pre-process images to improve OMR quality (noise reduction, sharpening, super-resolution) |
| F-03 | Run Audiveris OMR to convert score images to MusicXML |
| F-04 | Parse MusicXML and extract note sequences using music21 |
| F-05 | Convert note sequences to Jianpu notation and write `.jianpu.txt` intermediate files |
| F-06 | Render Jianpu PDF output via LilyPond (jianpu-ly pathway) or direct ReportLab canvas |
| F-07 | Optionally generate a MIDI file alongside the PDF |
| F-08 | Skip already-converted files using a SHA-256-based conversion history |
| F-09 | Provide a Rich TUI menu for engine selection, batch conversion, and editor access |
| F-10 | Provide a text-based Jianpu editor for manual correction of intermediate `.jianpu.txt` files |
| F-11 | Handle CJK (Chinese/Japanese/Korean) filenames without corruption |
| F-12 | Batch-process all eligible files in `Input/` in a single run |

### 2.3 User Characteristics

The primary user is a casual consumer who:
- Plays a musical instrument and reads Jianpu notation
- Has downloaded sheet music PDFs in Western staff notation format
- Has minimal programming or command-line experience
- Is running Microsoft Windows 10 or Windows 11

A secondary user (developer / researcher) may invoke the system via the provided Python source and may interact with intermediate files for debugging purposes.

### 2.4 Operating Environment

| Component | Requirement |
|-----------|-------------|
| Operating System | Windows 10 (64-bit) or Windows 11 |
| Processor | x86-64; GPU optional (waifu2x uses Vulkan via `waifu2x-ncnn-vulkan`) |
| RAM | ≥ 4 GB (≥ 8 GB recommended for large multi-page PDFs) |
| Disk Space | ≥ 600 MB for installation (includes JDK, Audiveris, LilyPond, waifu2x) |
| Display | Terminal-capable, minimum 43 × 30 character grid |
| Python (source mode only) | Python 3.10 or later |

### 2.5 Design and Implementation Constraints

- The packaged executable is Windows-only (PyInstaller target: Windows x86-64).
- Audiveris requires Java 11 or later; the bundled JDK ensures compatibility.
- OMR recognition accuracy is bounded by the quality of the upstream Audiveris and Oemer engines.
- The LilyPond version bundled is 2.24.4; future LilyPond releases may introduce incompatibilities.
- The `jianpu-ly.py` script (Annex A-3) is an external dependency licensed separately; the system downloads it on first use if not present.

---

## 3. Specific Requirements

### 3.1 Functional Requirements

#### 3.1.1 File Ingestion (F-01, F-11)

**FR-1.1** The system SHALL scan the `Input/` directory for files with extensions `.pdf`, `.png`, `.jpg`, and `.jpeg` at the start of each conversion run.

**FR-1.2** The system SHALL handle filenames containing Unicode characters (including CJK characters) on Windows without file-path errors or data corruption.

**FR-1.3** The system SHALL generate a SHA-256 hash of each input file upon ingestion and SHALL skip conversion if an identical hash is found in the conversion history and the corresponding output file exists.

**FR-1.4** The system SHALL warn the user if two or more input files share the same base name (ignoring extension) and SHALL disambiguate output filenames to prevent overwriting.

#### 3.1.2 Image Pre-Processing (F-02)

**FR-2.1** The system SHALL detect when an input image has a minimum dimension below 1200 pixels and, if `waifu2x-ncnn-vulkan` is available, SHALL upscale the image using 2× super-resolution before OMR.

**FR-2.2** The system SHALL measure image sharpness using the Laplacian standard deviation on a 500 × 500 pixel thumbnail. If the value is below 30.0, the system SHALL apply aggressive unsharp-mask sharpening following Gaussian pre-smoothing.

**FR-2.3** The system SHALL cap the total pixel count of images submitted to Audiveris at 20,000,000 pixels and SHALL downscale images exceeding this limit.

**FR-2.4** The system SHALL apply moderate noise-reduction (Gaussian blur, variance-adaptive thresholding) to all images prior to OMR.

**FR-2.5** Pre-processed images SHALL be stored as temporary files and SHALL be deleted after conversion completes or fails.

#### 3.1.3 Optical Music Recognition (F-03)

**FR-3.1** The system SHALL support Audiveris 5.x as the default OMR engine.

**FR-3.2** When the selected engine is Audiveris, the system SHALL invoke it as a sub-process using the bundled Java runtime and SHALL parse its MusicXML output.

**FR-3.3** The system SHALL impose a maximum timeout of 1800 seconds on Audiveris processing per input file and SHALL treat timeout as a failure.

**FR-3.4** The system SHALL support Oemer as an experimental alternative OMR engine when the `oemer` Python package is installed.

**FR-3.5** When the input is a PDF and the selected engine is Oemer (which only accepts images), the system SHALL render the first page of the PDF to a PNG prior to OMR.

**FR-3.6** The system SHALL create an isolated temporary directory per input file for Audiveris job artefacts and SHALL clean it up after processing.

**FR-3.7** The system SHALL sanitise input filenames to ASCII-safe equivalents (appending a 10-character SHA-1 prefix token) before passing them to Audiveris, to avoid OMR engine path-handling failures.

#### 3.1.4 Notation Parsing and Conversion (F-04, F-05)

**FR-4.1** The system SHALL use music21 to parse the MusicXML output produced by the OMR engine.

**FR-4.2** For each note in the parsed score, the system SHALL map the pitch class (C, D, E, F, G, A, B) to the corresponding Jianpu numeral (1–7) and SHALL encode octave displacements as superscript or subscript dots.

**FR-4.3** The system SHALL encode note durations in the Jianpu-ly text notation: dashes (−) for beats held beyond one beat, underlines (_) for subdivisions (eighth, sixteenth, etc.), and dots (.) for augmented durations.

**FR-4.4** The system SHALL quantise durations to the nearest value in the allowed set `{4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.375, 0.25, 0.1875, 0.125}` quarter note lengths.

**FR-4.5** The system SHALL write an intermediate `.jianpu.txt` file to the `editor-workspace/` directory for every successfully recognised score.

**FR-4.6** The system SHALL choose a measures-per-line layout automatically based on average measure width so that the rendered score fits within a standard A4 page.

**FR-4.7** Multiple voices or chords in the same measure SHALL be collapsed to the topmost (highest-pitch) voice; polyphonic content beyond this is explicitly a known limitation.

#### 3.1.5 PDF Rendering (F-06)

**FR-5.1** The system SHALL render Jianpu PDF output via one of two pathways:
  - **(a) LilyPond pathway**: build a `jianpu-ly.py`-compatible text file and invoke LilyPond to produce a PDF.
  - **(b) Direct ReportLab pathway**: draw Jianpu notation directly onto an A4 canvas using the ReportLab library.

**FR-5.2** The system SHALL remove the LilyPond watermark line (`Music engraving by LilyPond`) from generated `.ly` files before rendering.

**FR-5.3** The system SHALL embed a CJK-compatible font (selected from a priority list including Meiryo, Yu Gothic, MS Gothic, Microsoft YaHei, SimSun, SimHei) in the PDF when a CJK font is available on the system.

**FR-5.4** Output PDF files SHALL be saved to the `Output/` directory with the same base name as the corresponding input file.

**FR-5.5** The system SHALL open the `Output/` directory automatically in Windows Explorer after completing a batch run.

#### 3.1.6 MIDI Export (F-07)

**FR-6.1** Optionally (user-confirmed via TUI), the system SHALL use music21 to export a MIDI file corresponding to each input score.

**FR-6.2** MIDI files SHALL be saved alongside the PDF in the `Output/` directory.

#### 3.1.7 Conversion History (F-08)

**FR-7.1** The system SHALL persist conversion history as `conversion_history.json` in the application root directory.

**FR-7.2** The history record for each converted file SHALL include: input file SHA-256 hash, input filename, output filename, conversion timestamp, and a pipeline version number.

**FR-7.3** When the pipeline version recorded in history differs from the current `CONVERSION_PIPELINE_VERSION` constant, the system SHALL treat the file as unconverted and re-process it.

**FR-7.4** The system SHALL provide a TUI option to clear the conversion history and force re-conversion of all files.

#### 3.1.8 Terminal User Interface (F-09)

**FR-8.1** The system SHALL present a state-machine-based Rich TUI on launch, providing the following top-level menu options:
  1. Start batch conversion
  2. Open Jianpu text editor
  3. Clear conversion history
  4. Exit

**FR-8.2** The TUI SHALL confirm the user's MIDI generation preference before each batch run.

**FR-8.3** The TUI SHALL allow the user to select between Audiveris and Oemer OMR engines.

**FR-8.4** The TUI SHALL display a real-time spinner and progress indication during conversion.

**FR-8.5** The TUI SHALL resize the terminal window to 43 columns × 30 rows on launch for consistent layout (best-effort; silently ignored if resize fails).

**FR-8.6** All single-key navigation inputs SHALL work without requiring the Enter key (using `msvcrt.getwch()` on Windows).

#### 3.1.9 Jianpu Text Editor (F-10)

**FR-9.1** The system SHALL provide a text-based editor interface that lists all `.jianpu.txt` files in `editor-workspace/`, paginated at 10 entries per page.

**FR-9.2** The user SHALL be able to select any listed file and open it in the system default text editor (`notepad.exe` on Windows).

**FR-9.3** After the user closes the external editor, the system SHALL offer to regenerate the Jianpu PDF from the edited `.jianpu.txt` file without re-running OMR.

**FR-9.4** The `.jianpu.txt` format SHALL conform to the specification defined in `core/jianpu_txt_editor.py` (see Section 7.2).

#### 3.1.10 Batch Processing (F-12)

**FR-10.1** The system SHALL process all eligible files in `Input/` sequentially in a single batch run.

**FR-10.2** Failure of a single file SHALL not abort the batch; the system SHALL log the error and continue to the next file.

**FR-10.3** A conversion summary (total files attempted, succeeded, skipped, failed) SHALL be displayed after the batch completes.

---

### 3.2 Use Cases

#### UC-01: Batch Convert Scores to Jianpu

| Field | Detail |
|-------|--------|
| **Actor** | End user (casual) |
| **Precondition** | One or more PDF/PNG/JPG files are present in `Input/` |
| **Main Flow** | 1. User launches application. 2. TUI displays main menu. 3. User selects "Start conversion". 4. TUI asks whether to generate MIDI. 5. System pre-processes images, runs OMR, converts to Jianpu, renders PDF. 6. Output folder opens automatically. |
| **Alternative Flows** | A1: File already converted → system skips with a message. A2: OMR fails → system logs error and continues to next file. |
| **Postcondition** | Jianpu PDF (and optionally MIDI) exists in `Output/` for each successfully processed file. |

#### UC-02: Manually Correct Jianpu Notation

| Field | Detail |
|-------|--------|
| **Actor** | End user |
| **Precondition** | A `.jianpu.txt` intermediate file exists in `editor-workspace/` |
| **Main Flow** | 1. User opens Jianpu text editor from TUI. 2. Selects a file. 3. System opens file in Notepad. 4. User corrects notation and saves. 5. User returns to TUI. 6. System re-renders PDF from corrected text. |
| **Postcondition** | Updated Jianpu PDF is saved in `Output/`. |

#### UC-03: Re-process All Files After Upgrade

| Field | Detail |
|-------|--------|
| **Actor** | End user |
| **Precondition** | Conversion history exists from a prior version |
| **Main Flow** | 1. User selects "Clear conversion history". 2. System clears `conversion_history.json`. 3. User starts batch conversion. 4. All files are re-processed. |
| **Postcondition** | All outputs are regenerated. |

---

## 4. External Interface Requirements

### 4.1 User Interfaces

- The primary user interface is a **Rich TUI** rendered in a Windows terminal window, fixed at 43 × 30 characters.
- All interaction is via numeric key presses (no Enter required for single-key inputs) or line-based input for file paths.
- Colour and Unicode box-drawing characters are used for layout; the terminal must support ANSI/VT escape sequences (Windows Terminal and modern `conhost` both do).

### 4.2 Hardware Interfaces

- **GPU (optional)**: waifu2x-ncnn-vulkan uses the Vulkan API to accelerate super-resolution. A GPU capable of Vulkan 1.1 is needed for GPU-accelerated pre-processing. If absent, the image-pre-processing step is skipped or degraded.
- **Disk**: No special requirements beyond the ~600 MB installation footprint.

### 4.3 Software Interfaces

| Component | Version | Role |
|-----------|---------|------|
| Audiveris | 5.10.2 | OMR: image → MusicXML |
| Java JDK (bundled) | ≥ 11 (bundled JDK 21) | Runtime for Audiveris |
| LilyPond | 2.24.4 | Music engraving: .ly → PDF |
| jianpu-ly.py | Latest from ssb22.user.srcf.net | Jianpu-ly text → LilyPond |
| waifu2x-ncnn-vulkan | Bundled | GPU super-resolution |
| music21 | ≥ 9.9.1 | MusicXML → note data |
| Pillow | ≥ 12.2.0 | Image processing |
| ReportLab | ≥ 4.4.10 | Direct PDF rendering |
| Rich | ≥ 14.3.3 | TUI rendering |
| Oemer | Latest (optional) | Alternative OMR engine |

### 4.4 Communication Interfaces

The tool does not require network access during normal operation. The `jianpu-ly.py` script is downloaded over HTTPS from one of three fallback URLs on first use if not already bundled. This is the only network communication; downloading is non-mandatory and the system degrades gracefully if it fails.

---

## 5. Non-Functional Requirements

### 5.1 Performance

**NFR-P1** The system SHALL complete conversion of a single-page PDF score in under 3 minutes on a mid-range PC (Intel Core i5, 8 GB RAM, SSD) under normal operating conditions.

**NFR-P2** Image pre-processing (without super-resolution) SHALL complete in under 10 seconds per page.

**NFR-P3** The TUI SHALL render each screen transition within 200 ms to ensure a responsive user experience.

### 5.2 Reliability

**NFR-R1** A failure in any single-file conversion SHALL not crash the application; the system SHALL log the failure, update the summary counters, and proceed to the next file.

**NFR-R2** The system SHALL not corrupt or delete input files under any circumstances.

**NFR-R3** Temporary files SHALL be cleaned up after each conversion job, regardless of success or failure (barring abrupt process termination).

### 5.3 Usability

**NFR-U1** An end user with no prior familiarity with the tool SHALL be able to perform a successful batch conversion within 5 minutes of launching the application for the first time, without consulting documentation.

**NFR-U2** All error messages SHALL include a plain-language explanation of the probable cause and a suggested remediation action (e.g., "Ensure audiveris-runtime directory exists, or try switching to the oemer engine").

### 5.4 Portability

**NFR-Po1** The packaged executable SHALL run on Windows 10 (64-bit, build 1903 or later) and Windows 11 without requiring the user to install any additional software.

**NFR-Po2** When running from source on Windows, Linux, or macOS with the required dependencies installed, the core conversion pipeline SHALL function correctly (TUI keyboard handling degrades gracefully on non-Windows platforms).

### 5.5 Maintainability

**NFR-M1** The source code SHALL be organised into single-responsibility modules under the `core/` package, as defined in the architecture document.

**NFR-M2** No circular dependencies SHALL exist between modules.

**NFR-M3** All external dependencies SHALL be declared in `requirements.txt`.

### 5.6 Security

**NFR-S1** The system SHALL NOT execute arbitrary content from downloaded files beyond the designated `jianpu-ly.py` script. Any downloaded content SHALL be cached locally and its execution SHALL be limited to its defined purpose.

**NFR-S2** Input file path handling SHALL use Python `pathlib.Path` objects throughout; no string concatenation of file paths SHALL be used in subprocess calls in order to prevent path-traversal or command-injection vulnerabilities.

**NFR-S3** Subprocess calls to Audiveris, LilyPond, and waifu2x SHALL pass arguments as lists (never as shell-expanded strings) to prevent shell-injection.

### 5.7 Licensing

**NFR-L1** All third-party components used shall be under licences compatible with the project's redistribution model (GPL-compatible and permissive licences). Licence attributions SHALL be documented in `THIRD_PARTY_NOTICES.md`.

---

## 6. Constraints and Assumptions

### 6.1 Constraints

- **OMR accuracy ceiling**: Recognition accuracy is fundamentally limited by the quality of the Audiveris / Oemer engine. Blurry, handwritten, or non-standard layouts will produce errors that the tool cannot automatically resolve.
- **Single-voice output**: The current pipeline extracts only the topmost voice from polyphonic scores. Full polyphonic Jianpu output is a known out-of-scope limitation.
- **Lyrics**: Lyrics embedded in the score are not extracted or rendered in the Jianpu output (see `ENABLE_LYRICS_OUTPUT = False` in `config.py`). This is a deliberate simplification for the current version.
- **Windows packaging**: The PyInstaller build target is Windows x86-64 only.
- **LilyPond version lock**: The bundled LilyPond 2.24.4 is fixed; upgrading requires rebuilding the package.

### 6.2 Assumptions

- Users will place only well-formed, printable sheet music in the `Input/` directory.
- The Windows terminal in use supports ANSI VT escape codes (Windows Terminal or cmd.exe with VT enabled, which is the default on Windows 10 1903+).
- The installation path does not contain characters that break Java or LilyPond subprocess invocations (both tools have historically had trouble with non-ASCII paths; the tool mitigates this via ASCII-safe copying).
- For MIDI generation, the score contains valid tempo and time-signature information; otherwise music21 applies a default of 120 BPM, 4/4.

---

## 7. Appendices

### 7.1 Glossary

See Section 1.3 for core term definitions. Additional terms:

| Term | Definition |
|------|------------|
| **SHA-256** | A cryptographic hash function producing a 256-bit digest; used here as a content fingerprint to detect unchanged files. |
| **SHA-1** | A 160-bit hash function; used here to generate short filename tokens (not for security purposes). |
| **Batch run** | A single execution session in which all eligible files in `Input/` are processed sequentially. |
| **Pipeline version** | An integer constant incremented whenever the conversion logic changes significantly, triggering automatic re-conversion of previously processed files. |
| **`.jianpu.txt`** | The intermediate plain-text format used to store parsed Jianpu notation between the OMR step and the PDF rendering step, and to enable manual editing. |

### 7.2 `.jianpu.txt` Format Summary

The intermediate file format is defined in `core/jianpu_txt_editor.py`. Its key characteristics are:

- **Two sections**: `[meta]` (key:value metadata) and `[score]` (notation tokens).
- **Meta fields**: `title`, `composer`, `key` (e.g., `C`, `G`, `Bb`), `time` (e.g., `4/4`, `3/4`), `tempo` (BPM integer).
- **Token syntax**: numerals `1`–`7` for notes, `0` for rests; prefixes `#` (sharp) and `b` (flat); suffixes `'` (octave up), `,` (octave down), `-` (extend one beat), `_` (halve duration), `.` (augment).
- **Bar lines**: `|` character (cosmetic only, produces no note).
- Designed to be human-readable and editable in any plain-text editor.

### 7.3 Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | April 2026 | Tsukamotoshio | Initial release |
