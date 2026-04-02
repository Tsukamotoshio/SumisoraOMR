OMR-to-Jianpu Conversion Tool
==============================

Author:  Tsukamotoshio
Version: 0.1.1

Batch-convert Western staff notation PDFs into Jianpu (numbered musical
notation) PDFs, with optional MIDI output.


Usage
-----

1. Place sheet music files (PDF / PNG / JPG) into the Input folder.
2. Double-click the "Jianpu Conversion Tool" desktop shortcut, or run
   ConvertTool.exe in the installation directory.
3. Follow the prompts to confirm conversion and optional MIDI output.
4. Results are saved to the Output folder.


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
etc.). Their copyrights and licenses are listed in THIRD_PARTY_NOTICES.md.
