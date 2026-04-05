OMR-to-Jianpu Conversion Tool
==============================

Author:  Tsukamotoshio
Version: 0.2.0-experimental

Batch-convert Western staff notation PDFs into Jianpu (numbered musical
notation) PDFs, with optional MIDI output.

Tip: Type H or ? at the prompt for in-app help. Type Q to quit at any time.


What's New
----------

0.2.0-experimental (current)
  - Brand-new Rich TUI menu interface, replacing the old command-line prompts.
  - Added Oemer deep-learning OMR engine (better for phone photos or
    uneven-lighting images).
  - Automatic engine routing: PDF input → Audiveris; image input → Oemer.
  - Built-in Jianpu editor workspace: intermediate files are preserved after
    conversion so you can proofread and regenerate the PDF with one click.
  - Improved editor reference image: white-border crop + rotation correction,
    full RGB color — easy to read at a glance.
  - Smart Audiveris OCR language selection: CJK filenames use eng+chi_sim;
    all others use eng, reducing interference on Western lyric recognition.
  - Automatic Audiveris retry for PDFs: on first-attempt failure, retries
    without the OCR language constraint to reduce text-recognition noise.

0.1.3
  - After installation the correct README is now opened automatically
    according to your system language.
  - The installation folder is now versioned (ConvertTool-0.1.3).
    Upgrading from any previous version automatically migrates your
    Input, Output, and conversion history to the new directory.
  - Fixed: installer could not detect installations from version 0.1.x.
  - Improved CLI: added usage guide and help prompts, and more
    actionable error messages.
  - Added Jianpu TXT editor foundation: author or edit scores using
    plain .jianpu.txt files.

0.1.2: Image pre-processing (noise reduction, sharpening, waifu2x upscale).
0.1.1: PNG/JPG input support; auto-open Output folder after conversion.


Usage
-----

1. Place sheet music files (PDF / PNG / JPG) into the Input folder.
2. Double-click the "Jianpu Conversion Tool" desktop shortcut, or run
   ConvertTool.exe in the installation directory.
3. Follow the prompts to confirm conversion and optional MIDI output.
4. Results are saved to the Output folder.

At any prompt you may also type:
  H or ?   — show in-app help and usage guide
  Q        — quit the program


Directory Layout
----------------

  Input\                    Place source sheet music files here
  Output\                   Converted Jianpu PDFs and MIDIs are saved here
  logs\                     Runtime logs (auto-generated)
  THIRD_PARTY_NOTICES.md    Third-party component licenses


Supported Input Formats
-----------------------

  .pdf          Staff notation PDF (recommended, multi-page supported)
  .png          Sheet music image
  .jpg / .jpeg  Sheet music image


Jianpu TXT Editor (New Feature)
--------------------------------

You can write or edit scores as plain-text .jianpu.txt files:

  [meta]
  title: My Song
  composer: Unknown
  key: C
  time: 4/4
  tempo: 120

  [score]
  1 2 3 4 | 5 6 7 1'

Note tokens:
  1-7       scale degrees (1=do, 2=re, ... 7=ti); 0 = rest
  #4 / b7   sharp / flat prefix
  1' / 1''  raise one / two octaves
  1, / 1,,  lower one / two octaves
  1-        half note (one dash per extra beat)
  1_        eighth note (one underscore per halving)
  1.        dotted note (adds half the base value)
  | or ---  bar line (optional, for readability only)


Known Limitations
-----------------

- Recognition accuracy depends on score quality. Blurry scans or complex
  layouts may produce wrong or missing notes.
- Limited polyphony support. Scores with many voices or chords may only
  retain the main melody; some notes may be lost.
- No lyrics output. Only notes are exported; lyrics are not included.
- Slow processing. Audiveris startup takes time; multi-page PDFs may
  take several minutes to convert.
- Edge cases in key/time signatures. Uncommon time signatures or
  key changes may yield inaccurate results.


License
-------

Licensed under the MIT License. See the LICENSE file for details.
This tool bundles third-party components (Audiveris, LilyPond, music21,
waifu2x-ncnn-vulkan, etc.). Their copyrights and licenses are listed in
THIRD_PARTY_NOTICES.md.
