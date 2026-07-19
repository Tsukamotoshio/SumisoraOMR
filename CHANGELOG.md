# Changelog

All notable changes to SumisoraOMR are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow the
`APP_VERSION` in `core/config.py` (the single source of truth — run
`python scripts/sync_version.py` after bumping it).

## [0.5.0] - 2026-07-19

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

### Notes
- The legacy Flet GUI (`app.py`, `gui/pages/`) is retained for this release
  and will be removed in a later version.

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
