# core/omr/homr_downloader.py — HOMR weight download orchestrator
#
# Two responsibilities:
#   1. probe_sources(): pick the fastest reachable mirror by HEAD latency
#   2. download_all_weights() — added in Task 5
#
# Both are pure-Python (no Flet imports); the GUI layer (model_download_dialog.py)
# wraps these in a threaded worker with progress callbacks.

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import requests

# Make the homr submodule importable so we can reuse its constants.
_HOMR_SRC = Path(__file__).resolve().parent.parent.parent / 'omr_engine' / 'homr'
if str(_HOMR_SRC) not in sys.path:
    sys.path.insert(0, str(_HOMR_SRC))

from homr.main import _WEIGHT_BASE_URLS, _WEIGHT_FILES  # noqa: E402

# HEAD probe constants
_PROBE_TIMEOUT_SEC = 5.0
# Use the smallest weight (segnet fp16) as the probe target — its presence
# confirms the source actually has the weight set, not just a working domain.
# Picked by name pattern, not by index, so reordering _WEIGHT_FILES upstream
# can't silently change which file we probe.
_PROBE_TARGET_FILENAME = next(
    f for f in _WEIGHT_FILES if f.startswith('segnet_') and '_fp16.' in f
)


def _build_url_for(base_url: str, filename: str) -> str:
    """Build the actual URL where `filename` lives on `base_url`.

    Source layouts differ:
      - ModelScope mirrors weights into homr's repo subdirectories
        (`segmentation/<file>.onnx` for segnet_*, `transformer/<file>.onnx`
        for encoder_/decoder_*).
      - GitHub releases are flat .zip attachments where each .onnx is zipped
        under its own basename: `<base>/<filename_without_ext>.zip`.

    Used by both `probe_sources` (HEAD check) and the download orchestrator
    (actual GET) so URL construction stays in one place.

    Note: `homr/main.py:_download_from_any_source` has its own ModelScope URL
    logic that derives the subdir from the dest path's filesystem layout.
    That path-based approach assumes weights live in the legacy submodule
    tree; it does not work with the flat `<app_base_dir>/models/` layout we
    use now. Our orchestrator uses _build_url_for exclusively — do not mix
    the two strategies in the same call site.
    """
    base = base_url.rstrip('/')
    if 'modelscope.cn' in base_url:
        if filename.startswith('segnet_'):
            return f'{base}/segmentation/{filename}'
        if filename.startswith(('encoder_', 'decoder_')):
            return f'{base}/transformer/{filename}'
        raise ValueError(
            f'Unknown filename pattern for ModelScope: {filename!r} '
            f'(expected segnet_/encoder_/decoder_ prefix)'
        )
    # GitHub releases: flat .zip attachments named after the .onnx basename
    if not filename.endswith('.onnx'):
        raise ValueError(
            f'Unknown filename pattern for GitHub: {filename!r} (expected .onnx)'
        )
    basename = filename[:-len('.onnx')]
    return f'{base}/{basename}.zip'


def probe_sources(urls: Optional[list[str]] = None) -> Optional[str]:
    """HEAD-probe each base URL. Return the lowest-latency 2xx-returning one.

    Returns None if all sources fail.

    Note: ICMP ping is unreliable on Windows (firewalls block it, doesn't
    reflect the HTTPS CDN path). HEAD is the right HTTPS-layer proxy.
    """
    if urls is None:
        urls = _WEIGHT_BASE_URLS

    results: list[tuple[str, float]] = []
    for base in urls:
        probe_url = _build_url_for(base, _PROBE_TARGET_FILENAME)
        t0 = time.monotonic()
        try:
            r = requests.head(
                probe_url,
                timeout=_PROBE_TIMEOUT_SEC,
                allow_redirects=True,
            )
            elapsed = time.monotonic() - t0
            if 200 <= r.status_code < 300:
                results.append((base, elapsed))
        except requests.exceptions.RequestException:
            continue

    if not results:
        return None

    results.sort(key=lambda x: x[1])
    return results[0][0]


import threading
from typing import Callable

from homr.main import _WEIGHT_HASHES, verify_sha256  # noqa: E402
from homr.download_utils import unzip_file  # noqa: E402


# Progress callback signature:
#   on_progress(file_index, filename, bytes_downloaded, file_total_bytes,
#               overall_done_bytes, overall_total_bytes_estimate)
# - file_index: 0..len(_WEIGHT_FILES)-1 (relative to "files we still need")
# - bytes_downloaded: bytes for the current file's PRIMARY transfer (the .onnx
#   on ModelScope, or the .zip on GitHub). Resume-aware: counts from
#   resumed-byte-offset, not zero.
# - file_total_bytes: 0 if Content-Length unavailable; otherwise size of this file
# - overall_done_bytes: sum of bytes for completed files + current file's bytes_downloaded
# - overall_total_bytes_estimate: best-effort total; may be 0 until first HEAD
ProgressCallback = Callable[[int, str, int, int, int, int], None]


class DownloadCancelled(Exception):
    """Raised internally when cancel_event is set mid-transfer."""


class HashMismatch(Exception):
    """SHA256 of downloaded file did not match _WEIGHT_HASHES entry."""


class NoSourceAvailable(Exception):
    """All base URLs failed the HEAD probe."""


def _other_source(base_url: str, all_sources: list[str]) -> Optional[str]:
    """Return any source other than `base_url`, or None if no alternative."""
    for s in all_sources:
        if s != base_url:
            return s
    return None


def _file_size_at_source(base_url: str, filename: str) -> int:
    """HEAD the actual download URL and return Content-Length, or 0 if unknown."""
    try:
        r = requests.head(
            _build_url_for(base_url, filename),
            timeout=_PROBE_TIMEOUT_SEC,
            allow_redirects=True,
        )
        if 200 <= r.status_code < 300:
            return int(r.headers.get('content-length', 0))
    except requests.exceptions.RequestException:
        pass
    return 0


def _download_to_path(
    url: str,
    dest_path: Path,
    on_bytes: Callable[[int], None],
    cancel_event: threading.Event,
) -> None:
    """Download `url` to `dest_path` with HTTP Range resume + per-chunk callback.

    `on_bytes(bytes_so_far_in_file)` is called after each chunk is written. The
    callback may raise DownloadCancelled to stop the transfer; we also check
    `cancel_event` independently so the GUI can signal cancel without coupling
    the callback to event state.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    existing = dest_path.stat().st_size if dest_path.exists() else 0
    headers = {'Range': f'bytes={existing}-'} if existing else {}

    response = requests.get(url, stream=True, timeout=(15, 120), headers=headers)
    if response.status_code == 416:
        # Range Not Satisfiable → file already complete on disk
        return
    response.raise_for_status()

    mode = 'ab' if existing else 'wb'
    with open(dest_path, mode) as f:
        for chunk in response.iter_content(chunk_size=65536):
            if cancel_event.is_set():
                raise DownloadCancelled()
            if chunk:
                f.write(chunk)
                on_bytes(f.tell())


def _download_file_from_source(
    base_url: str,
    filename: str,
    models_dir: Path,
    on_bytes: Callable[[int], None],
    cancel_event: threading.Event,
) -> None:
    """Download one weight file (filename) from `base_url` into `models_dir`.

    Handles both source layouts:
      - ModelScope: GET the .onnx directly.
      - GitHub: GET the .zip into a tmp file, extract, move .onnx to dest,
        delete the .zip. Resume applies to the .zip transfer.
    """
    final_dest = models_dir / filename

    if 'modelscope.cn' in base_url:
        url = _build_url_for(base_url, filename)
        _download_to_path(url, final_dest, on_bytes, cancel_event)
        return

    # GitHub branch: zip download + extract.
    basename = filename[:-len('.onnx')]
    zip_dest = models_dir / f'{basename}.zip'
    url = _build_url_for(base_url, filename)
    _download_to_path(url, zip_dest, on_bytes, cancel_event)
    if cancel_event.is_set():
        raise DownloadCancelled()
    # Extract: the .zip contains a single .onnx (possibly nested under a folder).
    # `unzip_file` from homr/download_utils handles nested-root flattening if asked.
    unzip_file(str(zip_dest), str(models_dir), flatten_root_entry=True)
    # Verify the .onnx now exists at the expected path. If it's nested deeper
    # than `flatten_root_entry` could resolve, locate it and move into place.
    if not final_dest.exists():
        # Search for the .onnx anywhere we just extracted
        candidates = list(models_dir.rglob(filename))
        if not candidates:
            raise FileNotFoundError(
                f'Expected {filename} after unzipping {zip_dest.name}, none found'
            )
        # Move the first match to final_dest
        candidates[0].rename(final_dest)
        # Best-effort cleanup of any empty subdirs left behind
        for d in sorted({c.parent for c in candidates}, reverse=True):
            try:
                if d != models_dir:
                    d.rmdir()
            except OSError:
                pass
    zip_dest.unlink(missing_ok=True)


def base_url_for_name(name: str) -> Optional[str]:
    """Map a UI source-selection name to a base URL, or None for auto-probe.

    Recognised names:
      'auto'       → None  (caller should probe)
      'modelscope' → ModelScope mirror URL
      'github'     → GitHub releases URL
    Unknown names also return None.
    """
    if name == 'auto':
        return None
    for url in _WEIGHT_BASE_URLS:
        if name == 'modelscope' and 'modelscope' in url:
            return url
        if name == 'github' and 'github' in url:
            return url
    return None


def download_all_weights(
    models_dir: Path,
    on_progress: ProgressCallback,
    cancel_event: threading.Event,
    on_source_change: Optional[Callable[[str], None]] = None,
    forced_base_url: Optional[str] = None,
) -> None:
    """Download all missing HOMR weights into `models_dir`.

    Sequential: one file at a time, in the order of _WEIGHT_FILES.
    For each missing file:
      1. Skip if already exists with correct SHA256.
      2. Download from primary source (resume-aware via HTTP Range).
      3. Verify SHA256.
      4. On hash mismatch: delete, switch to alternate source, retry once
         (skipped when `forced_base_url` is set — user explicitly chose).
      5. On second hash mismatch: raise HashMismatch.

    `forced_base_url`: when set, skip probe and use this URL exclusively.
    Hash-mismatch fallback is also disabled — the caller asked for this source
    specifically, so we surface the failure rather than silently switching.

    Raises:
      NoSourceAvailable — if probe_sources() returns None (auto mode only).
      DownloadCancelled — if cancel_event is set during a transfer.
      HashMismatch — if a file fails hash check after the alternate-source retry.
      requests.exceptions.RequestException — for unrecoverable network errors.
    """
    models_dir.mkdir(parents=True, exist_ok=True)

    if forced_base_url is not None:
        primary = forced_base_url
    else:
        primary = probe_sources()
        if primary is None:
            raise NoSourceAvailable('All HOMR weight mirrors are unreachable.')
    if on_source_change:
        on_source_change(primary)

    # Determine which files are still needed.
    todo: list[str] = []
    for fname in _WEIGHT_FILES:
        target = models_dir / fname
        if target.exists():
            try:
                if verify_sha256(str(target), _WEIGHT_HASHES.get(fname, '')):
                    continue
            except FileNotFoundError:
                pass
            target.unlink(missing_ok=True)
        todo.append(fname)

    if not todo:
        return

    # Best-effort overall total via HEAD on each file (cheap; happens before any
    # byte transfer). Allows the dialog to show "X of Y MB" up front.
    file_sizes: dict[str, int] = {}
    overall_total = 0
    for fname in todo:
        if cancel_event.is_set():
            raise DownloadCancelled()
        sz = _file_size_at_source(primary, fname)
        file_sizes[fname] = sz
        overall_total += sz

    overall_done = 0
    for idx, fname in enumerate(todo):
        if cancel_event.is_set():
            raise DownloadCancelled()
        target = models_dir / fname
        file_total = file_sizes.get(fname, 0)
        bytes_so_far_holder = {'v': 0}

        def _per_chunk_cb(bytes_so_far: int,
                          fname=fname, idx=idx, file_total=file_total):
            bytes_so_far_holder['v'] = bytes_so_far
            on_progress(idx, fname, bytes_so_far, file_total,
                        overall_done + bytes_so_far, overall_total)

        # Primary attempt
        active_source = primary
        try:
            _download_file_from_source(active_source, fname, models_dir,
                                       _per_chunk_cb, cancel_event)
        except DownloadCancelled:
            raise

        # Hash check
        if verify_sha256(str(target), _WEIGHT_HASHES.get(fname, '')):
            overall_done += target.stat().st_size if target.exists() else 0
            continue

        # Hash mismatch → delete and try the alternate source once.
        # Skip the fallback if the caller forced a specific source — they
        # explicitly opted out of mirror-juggling, so surface the failure.
        target.unlink(missing_ok=True)
        if forced_base_url is not None:
            raise HashMismatch(f'{fname}: corrupt on forced source {active_source}')
        alt = _other_source(active_source, _WEIGHT_BASE_URLS)
        if alt is None:
            raise HashMismatch(f'{fname}: primary corrupt, no alternate source')
        if on_source_change:
            on_source_change(alt)
        bytes_so_far_holder['v'] = 0
        _download_file_from_source(alt, fname, models_dir,
                                   _per_chunk_cb, cancel_event)
        if not verify_sha256(str(target), _WEIGHT_HASHES.get(fname, '')):
            raise HashMismatch(f'{fname}: corrupt on both sources')
        overall_done += target.stat().st_size if target.exists() else 0
