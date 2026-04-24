OMR-to-Jianpu Conversion Tool
==============================

Author:  Tsukamotoshio
Version: v0.2.4

Batch-convert Western staff notation PDFs into Jianpu (numbered musical
notation) PDFs, with optional MIDI output.


What's New
----------

0.2.4 (current)
  - OMR engine dropdown: a new "Auto (Recommended)" option automatically
    routes each file to Audiveris or Homr based on input format and image
    quality; if Homr crashes in GPU mode it falls back to CPU and retries
    without user intervention.
  - Transposition engine upgrade: key dropdown now follows the circle of
    fifths with ♯/♭ symbols; a new Direction option (Closest/Up/Down,
    matching MuseScore behavior) updates the semitone count in real time
    without extra clicks; the "Compute offset" button is replaced by
    "Detect key"; a new "Export original score" button renders the
    unmodified score as a staff-notation PDF.
  - File-list multi-select delete: sidebar file entries now use checkboxes,
    supporting batch selection and bulk deletion.
  - Pickup-bar (anacrusis) support: when the first bar is incomplete, Jianpu
    barline alignment is now correct — no extra rests or truncated notes.

0.2.3
  - License changed from MIT to GNU Affero General Public License v3
    (AGPL-3.0).
  - Oemer engine temporarily removed: replaced by Homr (lighter weight,
    no GPU driver dependency); Oemer may return in a future release.
  - Homr low-memory adaptation: SegNet batch size is now automatically
    scaled based on available RAM to prevent OOM errors on low-spec machines.
  - Homr processing progress: heartbeat and progress callbacks keep the
    GUI responsive during long Homr recognition runs.
  - Jianpu phantom rest fix: full-measure rests from secondary voices no
    longer consume the bar budget, preventing melody note truncation in
    multi-voice scores.
  - GUI fixes: resolved several known display and interaction issues in the
    Rich TUI interface.
  - Transposition engine fixes: corrected known edge-case errors in the
    key-transposition pipeline.

0.2.2-homr-experimental
  - Added GitHub Releases update checking; users can open the download page
    directly from within the app.
  - Permission error fix: no longer crashes when running as a standard user
    under C:\Program Files; shows a friendly prompt to re-run as administrator.
  - MIDI/PDF consistency fix: generated MIDI now matches the Jianpu PDF
    exactly, both derived from the same melody voice (Movable-Do line);
    multi-voice mixing and chords are excluded.

0.2.1
  - Stable release consolidating v0.2.0-preview and v0.2.0-oemer-experimental;
    minor packaging, installer, and version-display updates.

0.2.0-oemer-experimental
  - Added Oemer deep-learning OMR engine (better for phone photos or
    uneven-lighting images).
  - Automatic engine routing: PDF input → Audiveris; image input → Oemer.

0.2.0-preview
  - Movable-Do (首调唱名法): note numbers are now mapped relative to the key
    signature — '1' always represents the tonic of the current key, and
    accidentals follow the key's natural tendency (sharps for sharp keys,
    flats for flat keys).
  - Brand-new Rich TUI menu interface, replacing the old command-line prompts.
  - Built-in Jianpu editor workspace: intermediate files are preserved after
    conversion so you can proofread and regenerate the PDF with one click.
  - Improved editor reference image: white-border crop + rotation correction,
    full RGB color — easy to read at a glance.
  - Smart Audiveris OCR language selection: CJK filenames use eng+chi_sim;
    all others use eng, reducing interference on Western lyric recognition.
  - Automatic Audiveris retry for PDFs: on first-attempt failure, retries
    without the OCR language constraint to reduce text-recognition noise.
  - Installer upgrade: the old installation folder is now automatically removed
    after user data has been migrated to the new version directory.

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

0.1.2
  - Image pre-processing: noise reduction, sharpening, waifu2x super-resolution
    upscale.
  - Optimized handling of low-resolution scans.

0.1.1
  - Auto-open Output folder after conversion completes.
  - PNG / JPG / JPEG input support added.
  - Fixed: LilyPond watermark text appearing at the end of output PDFs.


Usage
-----

1. Place sheet music files (PDF / PNG / JPG) into the Input folder, or drag
   them directly into the file sidebar.
2. Double-click the "Jianpu Conversion Tool" desktop shortcut, or run
   ConvertTool.exe in the installation directory.
3. In the file sidebar, select the files you want to convert.
4. Choose the OMR engine:
     Auto (Recommended)  — the app decides automatically; vector PDFs go
                           to Audiveris, images / low-quality scans go to Homr
     Audiveris           — heuristic engine, best for clean typeset PDFs
     Homr (Deep Learning)— better for phone photos or low-quality scans
5. Optionally set a custom output directory, then click "Start Conversion".
6. Confirm options in the dialog (MIDI generation, skip duplicates), then
   click "Start Conversion" to begin.
7. Results are saved to the Output folder.

The app also includes:
  Transposer  — transpose a MusicXML score to a different key with live preview
  Editor      — open and edit .jianpu.txt files with a binarized image reference


Directory Layout
----------------

  Input\                    Place source sheet music files here
  Output\                   Converted Jianpu PDFs and MIDIs are saved here
  editor-workspace\         Intermediate files preserved after conversion
                            (for Jianpu editor inspection and re-export)
  logs\                     Runtime logs (auto-generated)
  THIRD_PARTY_NOTICES.md    Third-party component licenses


Supported Input Formats
-----------------------

  .pdf          Staff notation PDF (recommended, multi-page supported)
  .png          Sheet music image
  .jpg / .jpeg  Sheet music image


Jianpu TXT Editor
-----------------

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
- Slow processing. Audiveris/Homr startup takes time; multi-page PDFs may
  take several minutes to convert.
- Edge cases in key/time signatures. Uncommon time signatures or
  key changes may yield inaccurate results.
- Automatic OMR engine routing is temporarily unavailable. The engine must
  be selected manually (Audiveris or Homr).
- Homr is experimental. Recognition quality may be lower than Audiveris
  for printed scores.
- Homr only captures pitch and rhythm on treble/bass clef. Dynamics,
  articulation, double sharps/flats, and other musical symbols are ignored.


License
-------

Licensed under the GNU Affero General Public License v3 (AGPL-3.0).
See the LICENSE file for details.
This tool bundles third-party components (Audiveris, LilyPond, music21,
waifu2x-ncnn-vulkan, etc.). Their copyrights and licenses are listed in
THIRD_PARTY_NOTICES.md.
