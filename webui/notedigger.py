# webui/notedigger.py — noteDigger MIDI → jianpu bridge service (M5-③-3).
"""Convert a noteDigger-exported MIDI into a jianpu PDF, reusing the core
pipeline's MIDI→MusicXML→jianpu path. Runs in a background thread; progress and
the final result are pushed to the page via EventPusher (nd_jianpu_progress /
nd_jianpu_done). Output lands in Output/ so the jianpu-preview page picks it up.
"""
from __future__ import annotations

import base64
import binascii
import logging
import tempfile
import threading
from pathlib import Path

from core.app.backend import build_dir, editor_workspace_dir, output_dir, xml_scores_dir
from core.utils import log_message

from .events import EventPusher

_JIANPU_SUFFIX = '_jianpu.pdf'
_BAD_CHARS = '<>:"/\\|?*'


def _safe_stem(name: str) -> str:
    """Sanitize a noteDigger-supplied filename into a safe output stem."""
    stem = Path(name or 'notedigger').stem or 'notedigger'
    stem = ''.join('_' if c in _BAD_CHARS else c for c in stem).strip()
    return stem or 'notedigger'


class NoteDiggerService:
    """Bridge for noteDigger → jianpu conversion.

    Single conversion at a time (guarded by ``_busy``); the pipeline's
    module-level sub-progress hook is monkey-patched for the run's duration to
    surface live progress, which is safe because only one job runs at once.
    """

    def __init__(self, pusher: EventPusher) -> None:
        self._pusher = pusher
        self._busy = threading.Lock()

    def generate_jianpu(self, name: str, b64: str) -> dict:
        """Decode a base64 MIDI and convert it to a jianpu PDF in Output/.

        Returns ``{'started': True, 'name': ...}`` and runs in the background,
        or an error dict when the input is invalid / a job is already running.
        """
        if not self._busy.acquire(blocking=False):
            return {'started': False, 'error': 'busy'}
        try:
            raw = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            self._busy.release()
            return {'started': False, 'error': f'bad base64: {exc}'}
        if raw[:4] != b'MThd':   # 标准 MIDI 头
            self._busy.release()
            return {'started': False, 'error': 'not a MIDI file'}

        stem = _safe_stem(name)
        threading.Thread(target=self._work, args=(stem, raw), daemon=True,
                         name='nd-jianpu').start()
        return {'started': True, 'name': f'{stem}{_JIANPU_SUFFIX}'}

    def _work(self, stem: str, raw: bytes) -> None:
        import core.app.pipeline as _pipeline
        from core.app.pipeline import process_single_input_to_jianpu

        pdf_name = f'{stem}{_JIANPU_SUFFIX}'
        ok = False
        error = None
        _orig_report = _pipeline._report_subprogress
        try:
            _pipeline._report_subprogress = lambda v, m='': self._progress(v, m)
            self._progress(0.03, '正在保存 MIDI…')
            tmp = Path(tempfile.mkdtemp(prefix='nd_', dir=build_dir()))
            mid_src = tmp / f'{stem}.mid'
            mid_src.write_bytes(raw)
            out_pdf = output_dir(None) / pdf_name
            out_midi = output_dir(None) / f'{stem}.mid'
            ok = bool(process_single_input_to_jianpu(
                mid_src,
                file_temp_dir=tmp,
                output_pdf=out_pdf,
                output_midi=out_midi,
                editor_workspace_dir=editor_workspace_dir(),
                xml_scores_dir=xml_scores_dir(),
            )) and out_pdf.exists()
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            log_message(f'[webui] noteDigger→简谱 失败：{exc}', logging.WARNING)
        finally:
            _pipeline._report_subprogress = _orig_report
            self._busy.release()
        self._pusher.push('nd_jianpu_done', {'ok': ok, 'error': error, 'name': pdf_name})

    def _progress(self, value: float, msg: str) -> None:
        self._pusher.push('nd_jianpu_progress', {'value': value, 'message': msg})
