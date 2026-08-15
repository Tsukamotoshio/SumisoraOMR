# Changelog

All notable changes to SumisoraOMR are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow the
`APP_VERSION` in `core/config.py` (the single source of truth — run
`python scripts/sync_version.py` after bumping it).

## [0.5.2] - 2026-08-15

Fidelity and hardening release: lyrics finally reach the jianpu output,
Audiveris moves to 5.11.0, and the jianpu editor stops silently dropping
repeat barlines and merged voices on re-render. Underneath, the dependency
set was audited end to end and the CI gates were widened to type-checking
and the JavaScript front-end.

### Added
- **Lyrics are emitted into the jianpu score.** `JianpuNote` now carries
  per-note lyrics from music21's own `Lyric` objects, and the generated
  jianpu-ly text uses native `L:` / `H:` lines, so LilyPond aligns syllables
  to notes instead of gluing a text block under the staff. The pipeline had
  carried disabled scaffolding for this since early on; it is replaced rather
  than re-enabled, because the old path discarded the syllabic
  (begin/middle/end) information needed to hyphenate words.
  Real scores do not tag every note — a flute arrangement of Scarborough Fair
  tags 46 of 200 non-rest notes, with gaps up to 39 notes wide — so untagged
  notes emit LilyPond's `_` skip token to keep later syllables on the right
  note. A CJK (`H:`) verse containing a gap is dropped with a log line
  instead, since jianpu-ly's hanzi-spacing pass strips a bare `_`.

### Changed
- **Audiveris 5.10.2 → 5.11.0** (submodule moves 64 commits to the release
  tag). A/B'd on three vector PDFs — the input class this project actually
  aims Audiveris at — after first confirming both versions are deterministic
  across three consecutive runs. To Zanarkand recovers four bars of melody
  5.10.2 dropped outright, and loses the three empty bars it had padded with.
  Bakamitai loses seven spurious empty bars and gains four dynamics, at the
  cost of one sixteenth note in one bar. Do You Hear the People Sing gains 2
  tenuto, 2 volta endings and 2 barlines, and loses 1 trill and 1 dynamic.
  One visible regression on that last score — a newly-detected 3:2 triplet
  renders as padded sixteenths — is our own gap rather than Audiveris': the
  jianpu converter has no time-modification handling yet.
  The JDK floor does not move; it is 25 on both sides.
- **A failed upscale is now reported as an error rather than a warning.**
  When neither Real-ESRGAN nor waifu2x can run, the log says so explicitly
  and names both engines, plus a once-per-process hint distinguishing "no
  executable found" from "found it, the run failed". Previously the fallback
  chain logged one mild warning per engine, and the first line read as though
  the second engine had taken over — on this machine both binaries went
  missing for a week and every conversion silently ran on the un-upscaled
  image, which degrades recognition for Audiveris and HOMR alike.
- **PyMuPDF 1.27.2.3 → 1.28.2**, with every call site renamed from the
  deprecated `fitz` alias to `pymupdf`. As of 1.28 the alias prints a
  deprecation warning on stdout; the worker's JSON IPC was never at risk
  (`worker_main.py` reassigns `sys.stdout` before importing anything from
  `core`), but it did put one spurious line in the GUI log panel per worker
  start. Verified byte-identical rendering across the 16 PDFs in `Input/`.
- Both READMEs now state the real prerequisites: **JDK 25+** (not 17+, which
  `find_java_executable()` rejects) and **Python 3.12–3.14** (not 3.10+,
  which `requirements.lock.txt` does not resolve on).

### Fixed
- **The jianpu editor dropped repeat barlines and merged voices on
  re-render.** Both are computed from the MusicXML score, which the editor
  never sees — it only has the saved `.jianpu.txt` — so opening a multi-voice
  or repeat-containing score, changing nothing and hitting render silently
  stripped repeat marks and flattened merged voices onto separate staves,
  regardless of what was actually edited. The main pipeline now persists both
  into a `#__jianpu_meta__` header line that the editor parses back and
  replays. Verified end to end against a score with 6 real repeat barlines:
  the re-rendered LilyPond carries the identical `\bar ":|."` commands at the
  identical bar positions as the original conversion.
- **Uncaught exceptions failed silently.** The crash-log-and-notify safety net
  (`sys.excepthook` / `threading.excepthook`) was lost when `app.py` was
  deleted in the 0.5.0 rewrite and never reinstated in `webui/main.py`. Main
  -thread exceptions now log at CRITICAL and raise a native dialog; background
  -thread exceptions log at ERROR without a dialog, to avoid a dialog storm
  from one misbehaving worker thread.
- **The DirectML distribution lock was not reproducible.** Installing
  `requirements.lock.directml.txt` into a clean venv pulls in `sympy` and
  `mpmath`, which it never pinned — a packaging machine got whatever versions
  were current that day. The cause is that the file is derived from
  `requirements.lock.txt`, and the two ONNX Runtime flavours do not declare
  the same dependencies: `onnxruntime-directml` requires `sympy` outright
  while `onnxruntime-gpu` puts it behind an optional extra, so it was never
  in the venv the lock was frozen from. Both are now pinned and the header
  records that these two locks cannot be derived purely mechanically.
- **Both locks disagreed with the audio-transcription stack.** That stack
  (torch / librosa / numba / piano_transcription_inference) is installed by
  hand per `requirements.txt` and is deliberately *not* in either lock, so its
  constraints are invisible to any audit that reasons from the lock alone —
  which is how three wrong pins survived. `numpy` was pinned at 2.5.0 while
  numba 0.66.0 hard-requires `<2.5` (a plain `ImportError`), `setuptools` at
  82.0.1 while torch 2.12.1 requires `<82`, and `msgpack` had been dropped as
  Flet-era dead weight when it is in fact a hard dependency of librosa —
  removing it breaks `librosa.load` / `resample` / `beat_track`, and therefore
  audio→jianpu conversion entirely. librosa's `lazy_loader` means
  `import librosa` still succeeds, so the failure only surfaces at
  transcription time. All three are corrected, and both lock headers plus the
  audio section of `requirements.txt` now record that changing dependencies
  requires a `pip check` in a venv that *has* the audio stack installed.

### Internal
- **Dependency audit across the whole manifest.** Dropped `musicxml` (no
  import anywhere, including the homr submodule, and unupgradable — 1.5
  through 1.6.1 all cap at Python <3.13), `pypdfium2` (zero call sites since
  0.5.0 moved PDF preview to pdf.js in the WebView), `pypdf` (its only caller
  was a dead 75-line CJK-title overlay superseded by LilyPond `\markup`
  injection), and six Flet-era transitive packages that `pip freeze` had
  carried into the lock after Flet itself was removed. 14 patch/minor bumps
  alongside. On the JavaScript side, a devDependency refresh clears a
  high-severity `brace-expansion` advisory that reached the tree only through
  eslint's glob handling.
- **CI gains a pyright type-check job**, pinned to `pythonPlatform:
  "Windows"` so it analyses the same target as a local run — the CI runner is
  Linux, where typeshed hides `msvcrt` / `os.startfile` / `ctypes.windll` and
  six correctly-guarded Windows-only call sites looked like errors. A second
  CI failure is fixed alongside: `tests/test_homr_downloader_url.py` imported
  the homr submodule unconditionally, which CI deliberately never checks out,
  failing collection for the entire suite.
- **`webui/static/js/` gains lint and test tooling** (eslint flat config +
  `node --test`, two new CI jobs), and the 1806-line `app.js` is split into 12
  ES modules mirroring `webui/`'s Python service boundaries. The split
  surfaced and fixed a circular-import temporal-dead-zone crash between
  `core.js` and `notedigger.js`.
- **Test coverage** for three behaviours previously guarded only by prose:
  the golden-file corpus grows from 7 to 11 fixtures (adding tempo, lyrics, a
  second multi-voice shape, and anacrusis-plus-minor-key), the transposer's
  rest-position and `--nrp` constraints, and the HOMR flat-layout URL builder
  against all 6 real weight filenames on both mirrors.
- `THIRD_PARTY_NOTICES.md` brought current: version drift corrected on five
  entries, and the three web components vendored since 0.5.0 (noteDigger,
  pdf.js, webaudio-tinysynth) are now attributed.
- `scripts/sync_version.py` also updates `README.zh.md`'s badge, which had
  been a manual step at every release.

## [0.5.1] - 2026-07-25

Recognition-quality and GPU release: the HOMR engine moves to model 426,
whose dedicated slur channel makes tie reconstruction reliable for the
first time, and the Windows build now ships a working DirectML GPU path
(~3x faster than CPU on an integrated GPU).

### Added
- **DirectML GPU inference** for HOMR on Windows. `requirements.lock.directml.txt`
  pins the DirectML distribution target (onnxruntime-directml 1.24.4);
  `requirements.txt` documents the three mutually-exclusive ONNX Runtime
  backends (directml = Windows distribution default, gpu = dev/CI, cpu).
- **noteDigger help overlay** — a Help button on the transcription editor page
  opens a guide (basic flow, mouse, shortcuts, tips) adapted from noteDigger's
  own documentation, with attribution and a link to its project page.
- **Import MIDI into noteDigger** from the editor's header, with the file dialog
  defaulting to `Output/` (where auto-transcription MIDIs land) — the embedded
  editor's own file input cannot set a default directory.

### Changed
- **HOMR transcription model 367 → 426**, which emits a dedicated slur channel.
  Validated on a 5-score golden set: Scarborough Fair recovers 19/20 gold tie
  pairs (model 367 emitted no slurs at all, so tie reconstruction never fired);
  note counts stay stable across the set. Known trade-off: 426 reads one test
  score's time signature as 3/4 where Audiveris and 367 say 6/8.
  Upgrading users will be prompted to re-download the changed encoder/decoder
  weights (~220 MB; the segmentation weights are unchanged, and the superseded
  367 files are pruned automatically once the new set verifies).
- **Tie reconstruction now trusts HOMR's slur output as primary evidence.**
  HOMR's training pipelines encode MusicXML `<tied>` as a `<slur>` token, so a
  matched slur pair on adjacent same-pitch notes is direct evidence of a tie
  rather than the weak signal the old heuristic assumed. Measured on 9 real
  Audiveris-sourced scores (86 gold tie pairs): precision 0.448 → 1.000,
  F1 0.575 → 1.000 (full curve detection) / 0.918 (70% detection, closer to
  realistic HOMR recall).
- MIDI playback now stops when you select a different file in the jianpu or
  staff preview list, instead of playing over the newly opened score.

### Fixed
- **DirectML was silently falling back to CPU.** Two independent causes: the
  PyInstaller spec's DLL-exclusion regex stripped `DirectML.dll` from the
  bundle, and `ort.preload_dlls()` (a CUDA/cuDNN helper absent from
  onnxruntime-directml builds) raised on attribute lookup. Both fixed, so the
  DirectML execution provider actually engages in the shipped build.
- Hardened the local preview server's `/file` endpoint against a path-injection
  pattern flagged by CodeQL: the byte read now targets a whitelist-sourced
  canonical path rather than the request's raw query string, which also closes
  a resolved-vs-raw mismatch that symlinks could have exploited.
- Re-tracked `SumisoraOMR.spec` in git. It is the hand-maintained build
  authority (DLL exclusion, hooks, version binding) with no machine-specific
  paths, and had been swept into `.gitignore` by an earlier broad untrack of
  packaging files.

## [0.5.0] - 2026-07-20

Major release: the GUI is rebuilt from Flet to a pywebview shell
(HTML/CSS/JS + WebView2), unlocking a rich web front-end. The conversion
pipeline (`core/`) and the worker subprocess are unchanged. Adds the
noteDigger audio transcription editor, a built-in MIDI player, and MIDI
file input.

### Added
- **New GUI shell (`webui/`, pywebview + WebView2)** replacing the Flet UI:
  frameless custom titlebar, card-style pages, pdf.js preview, file
  drag-and-drop with real filesystem paths, and true CJK font weights.
- **noteDigger audio transcription editor** — an embedded piano-roll
  transcription/correction tool (vendored GPL-3.0 fork), reached from the
  audio page. Export a MIDI in noteDigger and generate jianpu in one click
  (MIDI → MusicXML → jianpu, reusing the pipeline).
- **Built-in MIDI player** in the jianpu/staff preview pages
  (WebAudioTinySynth): play/pause, stop, volume, and a seekable progress
  bar with current/total time — replaces the external OS player.
- **MIDI file input** to the conversion pipeline (`.mid`/`.midi` → music21
  → MusicXML → jianpu).
- **System trust store SSL** via truststore, fixing download failures
  behind MITM proxies whose root CA is absent from certifi's bundle.

### Changed
- Audio page redesigned into two focal cards (automatic recognition +
  transcription editor); transposer control bar compacted to a single row;
  the about page fully localizes (including the license text).
- Windows packaging switched to the pywebview entry (`run_webui.py`,
  PyInstaller). The installer detects the WebView2 Evergreen runtime and
  silently installs it when missing (Windows 11 ships it built in).

### Removed
- The legacy Flet GUI (`app.py`, `gui/pages/`, `gui/components/`,
  `gui/theme.py`, `core/app/win_exe_patch.py`) — the pywebview shell fully
  replaces it, and the Flet runtime (~40 MB) is dropped from the build. The
  shared `gui/` modules the new shell reuses (strings, settings, app_state,
  worker_launcher) are kept.

## [0.4.2] - 2026-07-10

Fixes an intermittent hang in packaged-build audio recognition. Rolls up
the 0.4.1.1 packaging fixes (audio recognition was never shipped working
before this). Source-level fixes only — no new features.

### Fixed
- **Audio recognition intermittently hung** in the installer/portable
  build (roughly 1 in 6 files), stuck at "正在识别音符" and never
  finishing. Root cause: in the packaged process the piano-transcription
  model (a CRNN) runs its GRU on torch's Intel OpenMP (`libiomp5md`),
  and torch defaults to one thread per *logical* core (16 threads on an
  8-core CPU — 2× oversubscribed); the GRU's per-timestep fork/join
  parallel regions occasionally deadlock under that oversubscription —
  the process pins several CPU cores at 100% but never returns (a
  native-level deadlock; py-spy in blocking mode can't even read its
  stack). Fixed by capping torch's CPU thread count to
  `min(4, cpu_count)` for the audio worker via `torch.set_num_threads()`
  — note that the `OMP_NUM_THREADS` environment variable does **not**
  limit torch's thread count in this build (measured: still 7+ cores
  with `OMP_NUM_THREADS=1`), so torch's own API is required; OpenBLAS is
  also pinned to one thread so its separate pool doesn't re-compete.
  Verified on the packaged build: the deadlock reproduced ~1/6 before
  the fix and 0 times across 10 post-fix runs at 4 threads. Four threads
  keeps transcription reasonably fast (~2–3 min/file) while staying
  under the oversubscription that triggers the deadlock. Applies only to
  the CPU audio path — GPU inference and image OMR (Homr/ONNX, which uses
  its own thread pool) are unaffected.

## [0.4.1.1] - 2026-07-10

Critical packaging fix for 0.4.1: audio recognition was completely
non-functional in the installer/portable build (worked fine in dev mode).
Source-level fixes only — no new features.

### Fixed
- **Packaging**: `piano_transcription_inference` (the audio-recognition
  engine) hard-imports `matplotlib`, but `SumisoraOMR.spec`'s `Analysis()`
  still had `matplotlib`/`mpl_toolkits` in `excludes` (left over from before
  the audio feature existed, when it was only music21's unused optional
  dependency). `excludes` overrides `hiddenimports`, so matplotlib was
  silently absent from every 0.4.1 build — every audio transcription failed
  immediately with `No module named 'matplotlib'`. Verified against a real
  packaged build with a real MP3 before and after the fix.
- **UI**: the audio recognition page showed only a transient toast on
  failure, with no reason, that disappeared on its own — the underlying
  per-file failure detail was already being sent over IPC but never read.
  Now shows a persistent results dialog listing each failed file and its
  reason (mirrors the score-recognition page's results dialog).
- **Worker**: `worker_main.py` hardcoded `"转换失败"` (generic "conversion
  failed") as the reason whenever a conversion returned failure without
  raising an exception, discarding the actual error already present in the
  log stream. It now surfaces the first `✗`-prefixed log line for that file
  as the real reason — this also improves failure messages on the
  score-recognition page, not just audio.
- `scripts/sync_version.py` and `app.py`'s VERSIONINFO derivation only read
  the first 3 dot-separated segments of `APP_VERSION`, silently dropping a
  4th (patch-of-patch) segment — needed for this release. Both now carry a
  4th segment through instead of always forcing it to 0.

## [0.4.1] - 2026-07-09

Headline feature: audio-to-notation transcription. Convert a piano
recording (mp3/wav/flac/m4a/ogg) straight to Jianpu, alongside the
existing image/PDF pipeline.

### Added
- **Audio recognition**: new dedicated "音频识别" / "Audio Recognition"
  page — drop in a piano recording and get Jianpu out, using ByteDance's
  `piano_transcription_inference` (PyTorch CRNN) for MIDI transcription,
  routed through the same MusicXML → Jianpu pipeline as OMR. The
  ~172 MB model weight is downloaded on demand (not bundled in the
  installer), with SHA256-verified download from a ModelScope mirror
  (China-reachable) with Zenodo as fallback — same pattern already used
  for HOMR weights.
- **Melody-only mode**: optional skyline-based reduction to the
  predominant melody line, for recordings where only the top voice
  matters.
- **Beat-grid quantization**: transcribed note timing is snapped to a
  detected beat grid (noteDigger-inspired) instead of raw wall-clock
  MIDI timing, producing much cleaner rhythm/rest notation on
  rubato-affected recordings.
- Duplicate-output detection on the audio page now matches the
  existing OMR page behavior (skip/overwrite prompt).
- Auto-scans `Input/` for existing audio files, same as the OMR page.
- Concurrent conversion now defaults to "auto" worker count
  (`max(1, min(4, cpu_count // 2))`) instead of always running
  sequentially.

### Fixed
- **Security**: bumped `msgpack` (transitive dependency of `flet`) to
  1.2.1, patching a known CVE (GHSA-6v7p-g79w-8964 — Unpacker reuse
  after error can crash or enable a DoS on untrusted input); removed
  unrelated `llama-cpp-python`-branch packages that had contaminated
  `requirements.lock.txt` and would have broken a fresh-clone install.
- Staff-notation (five-line) output for audio results is disabled by
  default — the auto-generated staff view for transcribed audio was
  visually noisy; Jianpu output is unaffected. Reversible via a single
  flag in `core/app/pipeline.py`.
- `ConversionRunner.terminate()`'s `taskkill` call now has a timeout, so
  a stuck process-tree kill can no longer delay app shutdown.
- Audio-page file sidebar now shows audio-appropriate labels ("音频文件"
  / "Audio Files", audio-format empty-state hint) instead of the
  score-page wording it inherited from the shared component; language
  switching now correctly refreshes the sidebar on this page.
- File-type filtering on the audio page no longer leaks PDF/image files
  into the picker; English localization completed for the audio page.

### Changed / Internal
- Renamed a few technical-sounding UI labels to be clearer to end users
  (e.g. "OMR 模型权重" → "OMR 引擎 Homr"; piano engine label now reads
  "字节跳动 Piano Transcription").
- `SumisoraOMR.spec` (local, gitignored packaging file) needs manual
  `hiddenimports` additions for the audio stack (torch/librosa/numba/
  etc., several of which are lazily imported and invisible to
  PyInstaller's static scan); the exact required block is documented in
  `requirements.txt` since the spec itself isn't tracked in the repo.

## [0.4.0] - 2026-07-06

A large stability, infrastructure, and hardening release: the full P0/P1
correctness backlog, plus CI, regression tests, a dependency lockfile, and
several UX fixes.

### Added
- Global uncaught-exception handling — main-thread crashes log the full
  traceback and show a dialog with the log path; background-thread and asyncio
  exceptions are logged instead of vanishing.
- Preview pane shows a loading spinner while a file renders.
- About page: one-click **Copy Diagnostics** (version / OS / Python / GPU
  providers / dependency versions / model state / latest log path) for bug reports.
- Preview play button generates MIDI on demand when none exists yet.
- Landing page shows the installed HOMR model-weight version.
- Logs older than 30 days are cleaned up automatically.

### Fixed
- **Hangs**: LilyPond, jianpu-ly, and Homr subprocess calls now have real
  timeouts; the previous Homr "timeout" was decorative and never interrupted a
  stuck run.
- **Security**: `jianpu-ly.py` is now vendored into the repo; the runtime
  download-and-execute path (no hash check, one plaintext `http://` fallback)
  was removed.
- **Data safety**: `conversion_history.json` and `ui-settings.json` are written
  atomically (temp + `os.replace`), so a crash mid-write no longer corrupts them.
- **Output cleanup** no longer deletes files or folders the user placed in
  `Output/` — it removes only this pipeline's own intermediates.
- **Startup**: the GUI process no longer imports onnxruntime/CUDA at startup (a
  cause of "the window never appears"); the venv bootstrap now re-execs by
  interpreter identity, so installing deps outside the venv can't hijack startup.
- SnackBar toasts now actually display (they were silently failing through a
  Flet API that does not exist in this build); the diagnostics clipboard copy
  uses the OS clipboard directly.
- jianpu extraction boundary/dedup fixes (zero-duration chunking, triplet-offset
  float dictionary keys) — verified byte-identical on 40 real scores.
- MXL repack hygiene: mimetype written first and stored, temp directory no
  longer leaks on error.
- Audiveris→Homr fallback attempts are no longer mislabeled as failures.

### Performance
- Parallel-batch conversion now genuinely speeds up: each worker's ONNX
  intra-op thread count is capped to its CPU share, so N workers no longer
  oversubscribe the cores (previously it was slower than sequential).

### Changed / Internal
- Refactor: worker-subprocess orchestration extracted to `gui/worker_launcher.py`;
  Windows EXE-resource patching to `core/app/win_exe_patch.py`; key-tonic
  detection deduplicated. Cross-platform file-manager open helper.
- Quality gates: `ruff` config + one-pass cleanup; dead code removed; stale docs
  aligned.
- Tests: golden-file regression suite for jianpu extraction (`tests/`).
- Reproducibility: `requirements.lock.txt` (exact pins); `requirements-ci.txt`.
- CI: GitHub Actions running `ruff check` + `pytest` on every push/PR to `main`.
- Tooling: `.pre-commit-config.yaml` (ruff), `CONTRIBUTING.md`, single-source
  version with `scripts/sync_version.py`.

[0.4.0]: https://github.com/Tsukamotoshio/SumisoraOMR/compare/v0.3.6...v0.4.0
