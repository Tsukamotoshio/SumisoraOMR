# core/vlm/gguf_downloader.py — Qwen2-VL GGUF weight download orchestrator
# Downloads two files: main model GGUF + multimodal projector (mmproj) GGUF.
# Mirrors the pattern of core/omr/homr_downloader.py.
from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

import requests

from core.config import (
    VLM_WEIGHT_BASE_URLS,
    VLM_MODEL_FILENAME,
    VLM_MODEL_HASH,
    VLM_MMPROJ_FILENAME,
    VLM_MMPROJ_HASH,
)

_PROBE_TIMEOUT_SEC = 5.0
_VLM_FILES   = [VLM_MMPROJ_FILENAME, VLM_MODEL_FILENAME]   # mmproj first (smaller → probe target)
_VLM_HASHES  = {VLM_MODEL_FILENAME: VLM_MODEL_HASH, VLM_MMPROJ_FILENAME: VLM_MMPROJ_HASH}

# Progress callback: (file_index, filename, bytes_done, file_total,
#                     overall_done, overall_total, total_files)
ProgressCallback = Callable[[int, str, int, int, int, int, int], None]


class DownloadCancelled(Exception):
    """Raised when cancel_event is set mid-transfer."""


class HashMismatch(Exception):
    """SHA256 of downloaded file did not match expected hash."""


class NoSourceAvailable(Exception):
    """All base URLs failed the HEAD probe."""


def _build_url(base: str, filename: str) -> str:
    return base.rstrip('/') + '/' + filename


def verify_sha256(path: str, expected: str) -> bool:
    """Return True if file at `path` matches `expected` SHA256. Empty expected → always True."""
    if not expected:
        return True
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest().lower() == expected.lower()


def _probe_one(base: str) -> Optional[tuple[str, float]]:
    url = _build_url(base, VLM_MMPROJ_FILENAME)   # probe with smaller file
    t0 = time.monotonic()
    try:
        r = requests.head(url, timeout=_PROBE_TIMEOUT_SEC, allow_redirects=True)
        if 200 <= r.status_code < 300:
            return (base, time.monotonic() - t0)
    except requests.exceptions.RequestException:
        pass
    return None


def probe_sources(urls: Optional[list[str]] = None) -> Optional[str]:
    """Concurrent HEAD probe; returns lowest-latency reachable base URL."""
    if urls is None:
        urls = VLM_WEIGHT_BASE_URLS
    results: list[tuple[str, float]] = []
    with ThreadPoolExecutor(max_workers=len(urls)) as ex:
        for r in ex.map(_probe_one, urls):
            if r is not None:
                results.append(r)
    return min(results, key=lambda x: x[1])[0] if results else None


def _file_size_at(base: str, filename: str) -> int:
    """Return file size in bytes from HEAD (or GET-stream fallback), or 0 if unknown."""
    url = _build_url(base, filename)
    for method in ('head', 'get'):
        try:
            if method == 'head':
                r = requests.head(url, timeout=_PROBE_TIMEOUT_SEC, allow_redirects=True)
            else:
                r = requests.get(url, stream=True, timeout=_PROBE_TIMEOUT_SEC, allow_redirects=True)
            try:
                if 200 <= r.status_code < 300:
                    cl = r.headers.get('content-length')
                    if cl:
                        return int(cl)
            finally:
                if method == 'get':
                    r.close()
        except requests.exceptions.RequestException:
            pass
    return 0


def _download_to_path(
    url: str,
    dest: Path,
    on_bytes: Callable[[int], None],
    cancel: threading.Event,
) -> None:
    """Resume-capable streaming download. Calls on_bytes(bytes_written) after each chunk."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = dest.stat().st_size if dest.exists() else 0
    headers = {'Range': f'bytes={existing}-'} if existing else {}
    r = requests.get(url, stream=True, timeout=(15, 120), headers=headers)
    if r.status_code == 416:
        return   # file already complete
    r.raise_for_status()
    mode = 'ab' if existing else 'wb'
    with open(dest, mode) as f:
        for chunk in r.iter_content(chunk_size=65536):
            if cancel.is_set():
                raise DownloadCancelled()
            if chunk:
                f.write(chunk)
                on_bytes(f.tell())


def base_url_for_name(name: str) -> Optional[str]:
    """Map UI source-selection name → base URL, or None for auto-probe."""
    if name == 'auto':
        return None
    for url in VLM_WEIGHT_BASE_URLS:
        if name == 'modelscope' and 'modelscope' in url:
            return url
        if name == 'huggingface' and 'huggingface' in url:
            return url
    return None


def download_all_weights(
    models_dir: Path,
    on_progress: ProgressCallback,
    cancel_event: threading.Event,
    on_source_change: Optional[Callable[[str], None]] = None,
    forced_base_url: Optional[str] = None,
) -> None:
    """Download missing VLM weights into `models_dir`.

    Downloads mmproj first (smaller, faster indicator of working connection),
    then main model. Resume-aware via HTTP Range. Hash check after each file.
    On hash mismatch: delete, retry on alternate source once (unless forced source).
    """
    models_dir.mkdir(parents=True, exist_ok=True)
    primary = forced_base_url if forced_base_url else probe_sources()
    if primary is None:
        raise NoSourceAvailable('All VLM weight mirrors are unreachable.')
    if on_source_change:
        on_source_change(primary)

    todo = []
    for fname in _VLM_FILES:
        target = models_dir / fname
        if target.exists() and verify_sha256(str(target), _VLM_HASHES.get(fname, '')):
            continue
        target.unlink(missing_ok=True)
        todo.append(fname)

    if not todo:
        return

    # Pre-fetch sizes for progress display (concurrent HEAD requests)
    file_sizes: dict[str, int] = {}
    overall_total = 0
    with ThreadPoolExecutor(max_workers=min(4, len(todo))) as ex:
        future_to = {ex.submit(_file_size_at, primary, f): f for f in todo}
        for fut in as_completed(future_to):
            if cancel_event.is_set():
                raise DownloadCancelled()
            f = future_to[fut]
            sz = fut.result()
            file_sizes[f] = sz
            overall_total += sz

    overall_done = 0
    for idx, fname in enumerate(todo):
        if cancel_event.is_set():
            raise DownloadCancelled()
        target = models_dir / fname
        file_total = file_sizes.get(fname, 0)
        bytes_holder = {'v': 0}

        def _cb(b: int, _fname=fname, _idx=idx, _ft=file_total) -> None:
            bytes_holder['v'] = b
            on_progress(_idx, _fname, b, _ft, overall_done + b, overall_total, len(todo))

        _download_to_path(_build_url(primary, fname), target, _cb, cancel_event)

        if verify_sha256(str(target), _VLM_HASHES.get(fname, '')):
            overall_done += file_sizes.get(fname, 0)
            continue

        # Hash mismatch — try alternate source
        target.unlink(missing_ok=True)
        if forced_base_url:
            raise HashMismatch(f'{fname}: hash mismatch on forced source')
        alts = [u for u in VLM_WEIGHT_BASE_URLS if u != primary]
        if not alts:
            raise HashMismatch(f'{fname}: hash mismatch, no alternate source')
        alt = alts[0]
        if on_source_change:
            on_source_change(alt)
        bytes_holder['v'] = 0
        _download_to_path(_build_url(alt, fname), target, _cb, cancel_event)
        if not verify_sha256(str(target), _VLM_HASHES.get(fname, '')):
            raise HashMismatch(f'{fname}: hash mismatch on both sources')
        overall_done += file_sizes.get(fname, 0)
