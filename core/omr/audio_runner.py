# core/omr/audio_runner.py — audio transcription engine (ByteDance piano → MIDI → MusicXML)
"""Audio-to-MusicXML transcription via ByteDance high-resolution piano transcription.

Pipeline
--------
audio (mp3/wav/flac/m4a/ogg) → ByteDance piano transcription → MIDI → music21 → MusicXML.

The output directory mirrors the shape of the OMR runners (Audiveris/Homr): it
contains ``<stem>.musicxml``, so ``pipeline.process_single_input_to_jianpu`` can
consume the result through ``find_first_musicxml_file()`` with zero downstream
changes — the jianpu / staff render chain is engine-agnostic below that point.

Engine
------
`piano_transcription_inference` (Kong et al., ByteDance) is a PyTorch CRNN that is
state of the art for **solo piano** (MAESTRO note F1 > 0.97, with pedal). It is
piano-specific — not a general instrument/vocal transcriber. The ~172 MB checkpoint
is downloaded on demand into ``<app_base_dir>/models/piano_transcription/`` (the
upstream package downloads via ``wget``, which is absent on Windows, so we fetch it
ourselves). Audio is loaded and resampled to 16 kHz mono here because the package's
own ``load_audio`` relies on an audioread backend that is unreliable on Windows.
"""
from __future__ import annotations

import contextlib
import io
import logging
import os
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from ..utils import log_message

# ByteDance piano transcription checkpoint (Zenodo record 4034264).
_PIANO_MODEL_URL = (
    'https://zenodo.org/record/4034264/files/'
    'CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1'
)
_PIANO_MODEL_FILENAME = 'note_F1=0.9677_pedal_F1=0.9186.pth'
_PIANO_MODEL_MIN_BYTES = 150 * 1024 * 1024  # sanity floor (~172 MB expected)
_PIANO_SAMPLE_RATE = 16000

# Melody-only (skyline) reduction settings.
_MELODY_FRAME_HZ = 100.0     # timeline resolution for the per-frame skyline sweep
_MELODY_MIN_NOTE_MS = 80.0   # drop skyline fragments shorter than this


def _piano_model_path() -> Path:
    """Managed on-demand location for the ByteDance checkpoint."""
    from ..app.backend import app_base_dir

    return app_base_dir() / 'models' / 'piano_transcription' / _PIANO_MODEL_FILENAME


def _ensure_piano_model(progress_fn=None) -> Optional[Path]:
    """Return the local checkpoint path, downloading it on first use.

    Falls back to the upstream package's default home-dir copy if present, so a
    model already fetched by ``piano_transcription_inference`` is reused instead of
    re-downloaded. Returns None on failure.
    """
    dest = _piano_model_path()
    if dest.exists() and dest.stat().st_size >= _PIANO_MODEL_MIN_BYTES:
        return dest

    # Reuse the package's default cache (~/piano_transcription_inference_data) if any.
    home_copy = Path.home() / 'piano_transcription_inference_data' / _PIANO_MODEL_FILENAME
    if home_copy.exists() and home_copy.stat().st_size >= _PIANO_MODEL_MIN_BYTES:
        return home_copy

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.part')
    log_message('  [钢琴模型] 首次使用，正在下载模型（约 172 MB）…')
    if progress_fn is not None:
        try:
            progress_fn(0.05, '[钢琴模型] 正在下载模型（约 172 MB）…')
        except Exception:
            pass
    try:
        req = urllib.request.Request(_PIANO_MODEL_URL, headers={'User-Agent': 'SumisoraOMR'})
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, 'wb') as fh:
            total = int(resp.headers.get('Content-Length', 0) or 0)
            read = 0
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                fh.write(chunk)
                read += len(chunk)
                if progress_fn is not None and total:
                    try:
                        progress_fn(0.05 + 0.20 * (read / total),
                                    f'[钢琴模型] 下载中 {read // (1024*1024)}/{total // (1024*1024)} MB')
                    except Exception:
                        pass
        if tmp.stat().st_size < _PIANO_MODEL_MIN_BYTES:
            raise OSError(f'下载不完整（{tmp.stat().st_size} bytes）')
        tmp.replace(dest)
        log_message(f'  [钢琴模型] 下载完成 → {dest}')
        return dest
    except Exception as exc:
        log_message(f'  ✗ 钢琴转录模型下载失败：{exc}', logging.WARNING)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return None


def _load_audio_16k(source_file: Path):
    """Load *source_file* as mono float32 at 16 kHz (the model's input rate)."""
    import librosa
    import numpy as np
    import soundfile as sf

    try:
        y, sr = sf.read(str(source_file), dtype='float32')
    except Exception:
        # soundfile can't decode some containers (e.g. m4a); fall back to librosa/audioread.
        y, sr = librosa.load(str(source_file), sr=None, mono=False)
    y = np.asarray(y, dtype='float32')
    if y.ndim > 1:
        y = y.mean(axis=1) if y.shape[1] <= 8 else y.mean(axis=0)
    if sr != _PIANO_SAMPLE_RATE:
        y = librosa.resample(y, orig_sr=sr, target_sr=_PIANO_SAMPLE_RATE)
    return y


def _reduce_to_melody(note_events, tempo: float = 120.0):
    """Skyline melody reduction of transcribed note events → a monophonic MIDI.

    The transcription is polyphonic; a single-line jianpu wants just the melody.
    We assume the melody is the top voice (true for most piano material): sweep a
    fine timeline and keep the highest sounding pitch per frame (amplitude breaks
    exact-pitch ties), then merge equal-pitch runs into notes and drop fragments
    shorter than ``_MELODY_MIN_NOTE_MS``. Empirically this beat salience/Viterbi
    tracking on real piano audio (correct key, coherent line). Returns a PrettyMIDI
    or None. Each event is ``(start, end, pitch, amplitude)``.
    """
    import pretty_midi

    if not note_events:
        return None
    fps = _MELODY_FRAME_HZ
    hop = 1.0 / fps
    end_time = max(ev[1] for ev in note_events)
    n_frames = int(round(end_time * fps)) + 1
    top_pitch = [-1] * n_frames
    top_amp = [0.0] * n_frames
    for ev in note_events:
        start, end, pitch, amp = ev[0], ev[1], ev[2], ev[3]
        a = max(0, int(round(start * fps)))
        b = min(n_frames, int(round(end * fps)))
        for f in range(a, b):
            if pitch > top_pitch[f] or (pitch == top_pitch[f] and amp > top_amp[f]):
                top_pitch[f] = pitch
                top_amp[f] = amp

    min_frames = max(1, int(_MELODY_MIN_NOTE_MS / 1000.0 * fps))
    pm = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    inst = pretty_midi.Instrument(program=0)
    f = 0
    while f < n_frames:
        p = top_pitch[f]
        if p < 0:
            f += 1
            continue
        s = f
        while f < n_frames and top_pitch[f] == p:
            f += 1
        if f - s >= min_frames:
            inst.notes.append(
                pretty_midi.Note(velocity=90, pitch=int(p), start=s * hop, end=f * hop)
            )
    pm.instruments.append(inst)
    return pm


def run_audio_transcription(
    source_file: Path,
    output_dir: Path,
    *,
    use_gpu_inference: Optional[bool] = None,
    melody_only: bool = False,
    progress_fn: Optional[Callable[[float, str], None]] = None,
) -> Optional[Path]:
    """Transcribe a (piano) audio file to MusicXML via ByteDance + music21.

    Parameters
    ----------
    source_file:        Input audio file (mp3/wav/flac/m4a/ogg). Piano is expected.
    output_dir:         Directory to write ``<stem>.mid`` and ``<stem>.musicxml``.
    use_gpu_inference:  True/False to force GPU/CPU; None to auto-detect CUDA.
    melody_only:        When True, reduce to a single melody line (skyline).
    progress_fn:        Optional ``(fraction, message)`` sub-progress callback.

    Returns
    -------
    The ``output_dir`` (now containing ``<stem>.musicxml``) on success, or None on
    failure. The return contract matches the OMR runners so the caller can pass it
    straight to ``find_first_musicxml_file()``.
    """
    def _report(value: float, message: str = '') -> None:
        if progress_fn is not None:
            try:
                progress_fn(value, message)
            except Exception:
                pass

    stem = source_file.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 1) Import engine + resolve device ───────────────────────────────────────
    _report(0.03, '[钢琴转录] 加载引擎…')
    try:
        import torch
        from piano_transcription_inference import PianoTranscription
    except Exception as exc:
        log_message(
            f'  ✗ 钢琴转录引擎未安装或导入失败：{exc}\n'
            '    → 音频转录依赖需手动安装：\n'
            '      pip install torch --index-url https://download.pytorch.org/whl/cpu\n'
            '      pip install piano_transcription_inference torchlibrosa',
            logging.WARNING,
        )
        return None

    want_gpu = use_gpu_inference is not False
    device = 'cuda' if (want_gpu and torch.cuda.is_available()) else 'cpu'
    log_message(f'  [钢琴转录] 使用 {device.upper()} 推理。')

    checkpoint = _ensure_piano_model(progress_fn)
    if checkpoint is None:
        return None

    # ── 2) Load audio + transcribe ──────────────────────────────────────────────
    _report(0.30, '[钢琴转录] 正在读取音频…')
    try:
        audio = _load_audio_16k(source_file)
    except Exception as exc:
        log_message(f'  ✗ 音频读取失败：{exc}', logging.WARNING)
        return None
    if audio is None or len(audio) == 0:
        log_message(f'  ✗ 音频为空：{source_file.name}', logging.WARNING)
        return None

    midi_path = output_dir / f'{stem}.mid'
    _report(0.35, '[钢琴转录] 正在识别音符…')
    try:
        # 该库通过 print() 向 stdout 输出进度（"Segment x/y" 等）；worker 子进程用
        # stdout 传 JSON 协议，必须屏蔽，否则会污染协议。
        with contextlib.redirect_stdout(io.StringIO()):
            transcriptor = PianoTranscription(device=device, checkpoint_path=str(checkpoint))
            transcriptor.transcribe(audio, str(midi_path))
    except Exception as exc:
        log_message(f'  ✗ 钢琴转录失败：{exc}', logging.WARNING)
        return None

    if not midi_path.exists():
        log_message(f'  ✗ 未生成 MIDI：{source_file.name}', logging.WARNING)
        return None

    # ── 3) Optional melody-only reduction ───────────────────────────────────────
    import pretty_midi

    try:
        pm = pretty_midi.PrettyMIDI(str(midi_path))
        n_notes = sum(len(inst.notes) for inst in pm.instruments)
    except Exception as exc:
        log_message(f'  ✗ MIDI 读取失败：{exc}', logging.WARNING)
        return None

    if n_notes == 0:
        log_message(
            f'  ✗ 音频中未识别到音符：{source_file.name}\n'
            '    → 可能原因：音频过短/过弱，或不是钢琴（本引擎仅支持钢琴）',
            logging.WARNING,
        )
        return None

    if melody_only:
        events = [
            (n.start, n.end, n.pitch, n.velocity / 127.0)
            for inst in pm.instruments for n in inst.notes
        ]
        melody_midi = _reduce_to_melody(events)
        if melody_midi is not None and melody_midi.instruments and melody_midi.instruments[0].notes:
            melody_midi.write(str(midi_path))
            log_message(
                f'  ↳ 仅主旋律：{n_notes} 音符 → 约简为 '
                f'{len(melody_midi.instruments[0].notes)} 音符（天际线）'
            )
        else:
            log_message('  [仅主旋律] 约简结果为空，回退到完整转录。', logging.WARNING)

    log_message(f'  ↳ 钢琴转录识别到 {n_notes} 个音符 → {midi_path.name}')

    # ── 4) MIDI → MusicXML (music21) ────────────────────────────────────────────
    # 裸 MIDI 无小节线；music21 依据拍号/速度推断量化与小节划分，这一步的质量
    # 直接决定简谱可读性（详见开发计划 §5.1）。
    _report(0.60, '正在将 MIDI 转换为 MusicXML…')
    xml_path = output_dir / f'{stem}.musicxml'
    try:
        from music21 import converter

        score = converter.parse(str(midi_path))
        score.write('musicxml', fp=str(xml_path))
    except Exception as exc:
        log_message(f'  ✗ MIDI → MusicXML 转换失败：{exc}', logging.WARNING)
        return None

    if not xml_path.exists():
        log_message(f'  ✗ 未生成 MusicXML：{source_file.name}', logging.WARNING)
        return None

    _report(0.68, '音频转录完成，正在生成乐谱…')
    log_message(f'  ✓ 音频转录完成 → {xml_path.name}')
    return output_dir
