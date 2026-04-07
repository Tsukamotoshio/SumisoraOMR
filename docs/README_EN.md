OMR-to-Jianpu Conversion Tool
==============================

Author:  Tsukamotoshio
Version: 0.2.0-experimental

Batch-convert Western staff notation PDFs/images into Jianpu (numbered musical
notation) PDFs, with optional MIDI output.


What's New in 0.2.0-experimental
---------------------------------

- **Movable-Do (首调唱名法):** note numbers are now mapped relative to the
  key signature — `1` always represents the tonic, and accidentals use the
  key's natural tendency (sharps for sharp keys, flats for flat keys).
- Brand-new Rich TUI menu interface, replacing the old command-line prompts.
- Added Oemer deep-learning OMR engine (better for phone photos or
  uneven-lighting images; requires: pip install oemer).
- Automatic engine routing: PDF input → Audiveris; image input → Oemer.
- Built-in Jianpu editor workspace: intermediate files are preserved after
  conversion so you can proofread in Notepad and regenerate the PDF with
  one click.
- Improved editor reference image: white-border crop + rotation correction,
  full RGB color — easy to read at a glance.
- Smart Audiveris OCR language selection (CJK vs. eng) to reduce
  interference on scores with Western lyrics.
- Automatic Audiveris retry for PDFs on recognition failure.
- Installer upgrade: old installation folder is automatically removed after
  user data has been migrated to the new version directory.


Usage
-----

1. Place sheet music files (PDF / PNG / JPG) into the Input folder.
2. Double-click the "Jianpu Conversion Tool" desktop shortcut, or run
   ConvertTool.exe in the installation directory.
3. Select an OMR engine (Auto recommended), then confirm conversion and
   optional MIDI output.
4. Results are saved to the Output folder.

To proofread a converted score:
  Select option 4 "Jianpu Editor" from the main menu, choose a score,
  edit the .jianpu.txt in Notepad, then select "Generate Jianpu PDF".


Directory Layout
----------------

  Input\                    Place source sheet music files here
  Output\                   Converted Jianpu PDFs and MIDIs are saved here
  editor-workspace\         Intermediate .jianpu.txt files for proofreading
  logs\                     Runtime logs (auto-generated)
  THIRD_PARTY_NOTICES.md    Third-party component licenses


Supported Input Formats
-----------------------

  .pdf          Staff notation PDF (recommended, multi-page supported)
  .png          Sheet music image
  .jpg / .jpeg  Sheet music image


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
