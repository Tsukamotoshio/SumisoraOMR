# Changelog

All notable changes to SumisoraOMR are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow the
`APP_VERSION` in `core/config.py` (the single source of truth — run
`python scripts/sync_version.py` after bumping it).

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
