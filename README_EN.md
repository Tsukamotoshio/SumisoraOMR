OMR-to-Jianpu Conversion Tool
==============================

Author:  Tsukamotoshio
Version: 0.1.3

Batch-convert Western staff notation PDFs into Jianpu (numbered musical
notation) PDFs, with optional MIDI output.
Features a Rich TUI interface and a built-in Jianpu text editor for
correcting OMR recognition errors before final rendering.


Usage
-----

1. Place sheet music files (PDF / PNG / JPG) into the Input folder.
2. Double-click the "Jianpu Conversion Tool" desktop shortcut, or run
   ConvertTool.exe in the installation directory.
3. Use the numbered menu to start conversion (option 2) or open the
   Jianpu editor (option 4).
4. Results are saved to the Output folder.

Jianpu Text Editor
------------------

After conversion, intermediate .jianpu.txt files are saved to the
editor-workspace folder.  To manually correct OMR errors:

1. Select option 4 "Open Jianpu Editor" from the main menu.
2. Choose a score from the list.  Notepad opens the .jianpu.txt file
   and the source image opens alongside for reference.
3. Edit and save the text file, then close Notepad.
4. Select option 1 "Generate Jianpu PDF" to re-render the corrected score.


Directory Layout
----------------

  Input\                    Place source sheet music files here
  Output\                   Converted Jianpu PDFs and MIDIs are saved here
  editor-workspace\         Intermediate .jianpu.txt files for manual correction
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


Changelog
---------

0.1.3 (current)
  - Rich TUI state-machine interface with numbered menu
  - Jianpu text editor: correct OMR output via .jianpu.txt, re-render PDF
  - Help screen with usage guide and troubleshooting tips
  - Fixed waifu2x super-resolution not running correctly
  - Installer opens the locale-appropriate README automatically
  - Installation folder renamed to ConvertTool-0.1.3; user data migrated
    automatically on upgrade
  - Major code refactor into modular core/ package

0.1.2
  - Image pre-processing: denoise, sharpen, waifu2x super-resolution
  - Optimized for low-resolution scanned scores

0.1.1
  - Auto-open Output folder after conversion
  - Added PNG / JPG / JPEG input support
  - Fixed LilyPond watermark appearing at end of output PDF


License
-------

Licensed under the MIT License. See the LICENSE file for details.
This tool bundles third-party components (Audiveris, LilyPond, music21,
etc.). Their copyrights and licenses are listed in THIRD_PARTY_NOTICES.md.
