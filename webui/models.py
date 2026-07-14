# webui/models.py — model management service (HOMR weights + piano checkpoint).
"""Status / download / delete for the two on-demand model sets, reported to the
page through the EventPusher:

- ``model_download_progress`` {kind, value 0-1, message}
- ``model_download_done``     {kind, ok, error?}
- ``models_changed``          {status}   (after download/delete)

HOMR presence checking replicates app.py's approach: read _WEIGHT_FILES /
_WEIGHT_HASHES out of the homr submodule *source* via AST — importing
``homr.main`` would pull onnxruntime/cv2 (and possibly CUDA init) into the GUI
process, which is exactly what the worker subprocess exists to isolate.
"""
from __future__ import annotations

import ast
import hashlib
import logging
import threading
from pathlib import Path
from typing import Optional

from core.app.backend import models_dir
from core.omr.audio_runner import delete_piano_model, piano_model_available
from core.utils import log_message

from .events import EventPusher

_REPO_ROOT = Path(__file__).parent.parent


def _load_homr_weight_manifest() -> tuple[list[str], dict[str, str]]:
    """AST-parse _WEIGHT_FILES/_WEIGHT_HASHES from homr/main.py (no import).

    Same technique as app.py:_load_homr_weight_manifest (see there for the full
    rationale). Returns ([], {}) on failure — caller treats models as absent.
    """
    src = _REPO_ROOT / 'omr_engine' / 'homr' / 'homr' / 'main.py'
    files: list[str] = []
    hashes: dict[str, str] = {}
    try:
        tree = ast.parse(src.read_text(encoding='utf-8', errors='ignore'))
    except Exception:
        return files, hashes
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        else:
            continue
        if target not in ('_WEIGHT_FILES', '_WEIGHT_HASHES') or node.value is None:
            continue
        try:
            val = ast.literal_eval(node.value)
        except Exception:
            continue
        if target == '_WEIGHT_FILES' and isinstance(val, list):
            files = [str(x) for x in val]
        elif target == '_WEIGHT_HASHES' and isinstance(val, dict):
            hashes = {str(k): str(v) for k, v in val.items()}
    return files, hashes


def _sha256_ok(path: Path, expected: str) -> bool:
    if not expected:
        return True
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b''):
                h.update(chunk)
        return h.hexdigest() == expected
    except Exception:
        return False


class ModelsService:
    """Model status + on-demand download orchestration for the web shell."""

    def __init__(self, pusher: EventPusher) -> None:
        self._pusher = pusher
        self._cancel_events: dict[str, threading.Event] = {}
        self._busy: set[str] = set()
        self._lock = threading.Lock()

    # ── 状态 ─────────────────────────────────────────────────────────────────

    def status(self, verify_hash: bool = False) -> dict:
        """Presence status for both model sets.

        verify_hash=False keeps this cheap (existence only) for page loads;
        the post-download check uses hashes.
        """
        files, hashes = _load_homr_weight_manifest()
        target = models_dir()
        present = [
            f for f in files
            if (target / f).exists() and (not verify_hash or _sha256_ok(target / f, hashes.get(f, '')))
        ]
        return {
            'homr': {
                'available': bool(files) and len(present) == len(files),
                'files_present': len(present),
                'files_total': len(files),
            },
            'piano': {'available': piano_model_available()},
        }

    # ── 下载 ─────────────────────────────────────────────────────────────────

    def download(self, kind: str) -> dict:
        """Start downloading *kind* ('homr' | 'piano') on a background thread."""
        if kind not in ('homr', 'piano'):
            return {'ok': False, 'error': f'unknown kind: {kind}'}
        with self._lock:
            if kind in self._busy:
                return {'ok': False, 'error': 'busy'}
            self._busy.add(kind)
        cancel = threading.Event()
        self._cancel_events[kind] = cancel
        target = self._download_homr if kind == 'homr' else self._download_piano
        threading.Thread(target=target, args=(cancel,), daemon=True, name=f'dl-{kind}').start()
        return {'ok': True}

    def cancel_download(self, kind: str) -> dict:
        ev = self._cancel_events.get(kind)
        if ev is not None:
            ev.set()
        return {'ok': True}

    def _finish(self, kind: str, ok: bool, error: Optional[str] = None) -> None:
        with self._lock:
            self._busy.discard(kind)
        self._pusher.push('model_download_done', {'kind': kind, 'ok': ok, 'error': error})
        self._pusher.push('models_changed', {'status': self.status()})

    def _download_homr(self, cancel: threading.Event) -> None:
        from core.omr.homr_downloader import (
            DownloadCancelled, download_all_weights,
        )

        def on_progress(idx, fname, done, total, overall_done, overall_total, n_files):
            frac = (overall_done / overall_total) if overall_total else 0.0
            self._pusher.push('model_download_progress', {
                'kind': 'homr',
                'value': max(0.0, min(1.0, frac)),
                'message': f'[{idx + 1}/{n_files}] {fname} '
                           f'{done // (1024 * 1024)}/{(total or 0) // (1024 * 1024)} MB',
            })

        try:
            download_all_weights(models_dir(), on_progress, cancel)
            self._finish('homr', True)
        except DownloadCancelled:
            self._finish('homr', False, 'cancelled')
        except Exception as exc:
            log_message(f'[webui] HOMR 权重下载失败：{exc}', logging.WARNING)
            self._finish('homr', False, str(exc))

    def _download_piano(self, cancel: threading.Event) -> None:
        from core.omr.audio_runner import _ensure_piano_model

        def progress_fn(value: float, message: str) -> None:
            # _ensure_piano_model 的进度是为 worker 子进度设计的 0.05–0.25 区间，
            # 归一化到 0–1 供下载弹层使用。
            frac = (value - 0.05) / 0.20
            self._pusher.push('model_download_progress', {
                'kind': 'piano',
                'value': max(0.0, min(1.0, frac)),
                'message': message,
            })

        try:
            result = _ensure_piano_model(progress_fn, cancel_event=cancel)
            if result is not None:
                self._finish('piano', True)
            elif cancel.is_set():
                self._finish('piano', False, 'cancelled')
            else:
                self._finish('piano', False, '下载失败（全部来源均失败），详见日志')
        except Exception as exc:
            log_message(f'[webui] 钢琴模型下载失败：{exc}', logging.WARNING)
            self._finish('piano', False, str(exc))

    # ── 删除 ─────────────────────────────────────────────────────────────────

    def delete(self, kind: str) -> dict:
        if kind == 'piano':
            removed = delete_piano_model()
            self._pusher.push('models_changed', {'status': self.status()})
            return {'ok': True, 'removed': removed}
        if kind == 'homr':
            files, _ = _load_homr_weight_manifest()
            target = models_dir()
            removed = 0
            for f in files:
                p = target / f
                try:
                    if p.exists():
                        p.unlink()
                        removed += 1
                except Exception:
                    pass
            self._pusher.push('models_changed', {'status': self.status()})
            return {'ok': True, 'removed': removed}
        return {'ok': False, 'error': f'unknown kind: {kind}'}
