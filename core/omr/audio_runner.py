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
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from ..utils import log_message

# ByteDance piano transcription checkpoint. Two mirrors, tried in order:
#   1. ModelScope — mirrors the HOMR weights' "Mirror invariant" pattern
#      (see CLAUDE.md): mainland-China-reachable, and its resolve/ endpoint
#      returns the file's SHA256 in the X-Linked-Etag header, confirmed to
#      match _PIANO_MODEL_SHA256 below.
#   2. Zenodo (the upstream package's own source; record 4034264) — fallback
#      for whenever the ModelScope mirror is unavailable.
_PIANO_MODEL_FILENAME = 'note_F1=0.9677_pedal_F1=0.9186.pth'
_PIANO_MODEL_URLS = [
    'https://modelscope.cn/models/Tsukamotoshio/piano-transcription/resolve/master/'
    'note_F1=0.9677_pedal_F1=0.9186.pth',
    'https://zenodo.org/record/4034264/files/'
    'CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1',
]
_PIANO_MODEL_SHA256 = 'c3fa9730725bf4a762f1c14bc80cd5986eacda01b026f5a4a2525cd607876141'
_PIANO_MODEL_MIN_BYTES = 150 * 1024 * 1024  # sanity floor (~172 MB expected)
_PIANO_SAMPLE_RATE = 16000

# Melody-only (skyline) reduction settings.
_MELODY_FRAME_HZ = 100.0     # timeline resolution for the per-frame skyline sweep
_MELODY_MIN_NOTE_MS = 80.0   # drop skyline fragments shorter than this

# Beat-grid quantization (noteDigger-style "align to measure lines" export).
# 真实演奏有 rubato，直接把秒域 MIDI 丢给 music21 会切出破碎的节奏（满谱休止符/
# 附点）。这里先用 librosa 检测拍点，把音符起止吸附到拍内细分网格，再以规整的
# 恒定速度重建 MIDI —— music21 便能切出干净的小节。
_QUANTIZE_TO_BEATS = True
_QUANTIZE_DIVISIONS = 4      # subdivisions per beat (4 = sixteenth-note grid)
_QUANTIZE_MIN_BEATS = 8      # skip quantization when beat tracking finds fewer beats


class PianoDownloadCancelled(Exception):
    """Raised internally when the caller's cancel_event is set mid-transfer."""


def _piano_model_path() -> Path:
    """Managed on-demand location for the ByteDance checkpoint."""
    from ..app.backend import app_base_dir

    return app_base_dir() / 'models' / 'piano_transcription' / _PIANO_MODEL_FILENAME


def _piano_model_home_copy() -> Path:
    """The upstream package's own default cache location, reused if present."""
    return Path.home() / 'piano_transcription_inference_data' / _PIANO_MODEL_FILENAME


def find_piano_model() -> Optional[Path]:
    """Return the local checkpoint path if already present (no download), else None.

    Checks the app-managed location first, then the upstream package's default
    home-dir cache (so a model already fetched by ``piano_transcription_inference``
    directly is recognised without re-downloading). Cheap — safe to call from the
    GUI process for status display (no torch import).
    """
    for candidate in (_piano_model_path(), _piano_model_home_copy()):
        if candidate.exists() and candidate.stat().st_size >= _PIANO_MODEL_MIN_BYTES:
            return candidate
    return None


def piano_model_available() -> bool:
    """True if the piano transcription checkpoint is already on disk."""
    return find_piano_model() is not None


def delete_piano_model() -> bool:
    """Remove the checkpoint so ``piano_model_available()`` goes back to False.

    Normal use always passes an explicit ``checkpoint_path`` to
    ``PianoTranscription`` (see ``run_audio_transcription``), so the upstream
    package's own auto-download-to-home-dir path is never triggered in this
    app — but if a home-dir copy exists anyway (e.g. carried over from manual
    testing, or from running the upstream package directly), leaving it behind
    would make the "删除模型" button a no-op (``find_piano_model()`` would still
    find it). Remove both locations. Returns True if anything was removed.
    """
    removed = False
    for candidate in (_piano_model_path(), _piano_model_home_copy()):
        if candidate.exists():
            try:
                candidate.unlink()
                log_message(f'  [钢琴模型] 已删除 → {candidate}')
                removed = True
            except Exception as exc:
                log_message(f'  ✗ 钢琴模型删除失败（{candidate}）：{exc}', logging.WARNING)
    return removed


def _download_piano_model_from(url: str, dest: Path, tmp: Path,
                                progress_fn=None, cancel_event=None) -> None:
    """Stream *url* into *tmp*, verifying SHA256 against _PIANO_MODEL_SHA256.

    Hashes incrementally during the single read pass (no second I/O pass over
    the ~172 MB file). Raises on any failure (network error, short read, hash
    mismatch) or ``PianoDownloadCancelled`` if *cancel_event* fires mid-transfer;
    the caller is responsible for cleaning up *tmp*.
    """
    import hashlib

    hasher = hashlib.sha256()
    req = urllib.request.Request(url, headers={'User-Agent': 'SumisoraOMR'})
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, 'wb') as fh:
        total = int(resp.headers.get('Content-Length', 0) or 0)
        read = 0
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise PianoDownloadCancelled()
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            fh.write(chunk)
            hasher.update(chunk)
            read += len(chunk)
            if progress_fn is not None and total:
                try:
                    progress_fn(0.05 + 0.20 * (read / total),
                                f'[钢琴模型] 下载中 {read // (1024*1024)}/{total // (1024*1024)} MB')
                except Exception:
                    pass
    if tmp.stat().st_size < _PIANO_MODEL_MIN_BYTES:
        raise OSError(f'下载不完整（{tmp.stat().st_size} bytes）')
    digest = hasher.hexdigest()
    if digest != _PIANO_MODEL_SHA256:
        raise ValueError(f'SHA256 校验失败（期望 {_PIANO_MODEL_SHA256[:12]}…，实际 {digest[:12]}…）')
    tmp.replace(dest)


def _ensure_piano_model(progress_fn=None, cancel_event=None) -> Optional[Path]:
    """Return the local checkpoint path, downloading it on first use.

    Falls back to the upstream package's default home-dir copy if present, so a
    model already fetched by ``piano_transcription_inference`` is reused instead of
    re-downloaded. Otherwise tries each mirror in ``_PIANO_MODEL_URLS`` in order
    (ModelScope, then Zenodo — see the comment above that list), verifying SHA256
    on each attempt; a hash mismatch or network failure on one mirror falls
    through to the next rather than failing outright. *cancel_event*
    (``threading.Event``) lets a caller (e.g. the GUI's download dialog) abort
    mid-transfer — cancellation is not retried against remaining mirrors.
    Returns None on failure or cancellation.
    """
    cached = find_piano_model()
    if cached is not None:
        return cached

    dest = _piano_model_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.part')
    log_message('  [钢琴模型] 首次使用，正在下载模型（约 172 MB）…')
    if progress_fn is not None:
        try:
            progress_fn(0.05, '[钢琴模型] 正在下载模型（约 172 MB）…')
        except Exception:
            pass

    last_exc: Optional[Exception] = None
    for i, url in enumerate(_PIANO_MODEL_URLS):
        try:
            _download_piano_model_from(url, dest, tmp, progress_fn, cancel_event)
            log_message(f'  [钢琴模型] 下载完成 → {dest}')
            return dest
        except PianoDownloadCancelled:
            log_message('  [钢琴模型] 下载已取消。')
            return None
        except Exception as exc:
            last_exc = exc
            more = i + 1 < len(_PIANO_MODEL_URLS)
            log_message(
                f'  [钢琴模型] 源 {i + 1}/{len(_PIANO_MODEL_URLS)} 下载失败：{exc}'
                + ('，尝试下一个源…' if more else ''),
                logging.WARNING,
            )
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    log_message(f'  ✗ 钢琴转录模型下载失败（全部来源均失败）：{last_exc}', logging.WARNING)
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


def _quantize_to_beat_grid(note_events, audio, sample_rate):
    """Beat-align note events (noteDigger-style measure quantization).

    真实录音的拍距不均匀（rubato），所以不能按固定 BPM 直接取整。做法与
    noteDigger 导出 MIDI 的「对齐小节线」模式一致：
      1. librosa 拍点跟踪得到逐拍时间点（非均匀网格）；
      2. 每个音符起止在"拍序号域"内线性插值（拍外用中位拍距外推）；
      3. 吸附到拍内 1/``_QUANTIZE_DIVISIONS`` 细分；
      4. 以检测到的速度按恒定拍长重建秒域时间 —— 得到节拍完全规整的
         事件序列，music21 便能切出干净的小节。

    Returns ``(quantized_events, tempo_bpm)`` or None when beat tracking is
    unreliable (too few beats). Each event is ``(start, end, pitch, amplitude)``.
    """
    import librosa
    import numpy as np

    tempo, beat_times = librosa.beat.beat_track(y=audio, sr=sample_rate, units='time')
    tempo = float(np.atleast_1d(tempo)[0])
    beat_times = np.asarray(beat_times, dtype=float)
    if len(beat_times) < _QUANTIZE_MIN_BEATS or tempo <= 0:
        return None
    med_int = float(np.median(np.diff(beat_times)))
    if med_int <= 0:
        return None

    beat_idx = np.arange(len(beat_times), dtype=float)

    def to_beats(t: float) -> float:
        # 拍点区间内线性插值；首拍前/末拍后用中位拍距线性外推
        if t <= beat_times[0]:
            return (t - beat_times[0]) / med_int
        if t >= beat_times[-1]:
            return beat_idx[-1] + (t - beat_times[-1]) / med_int
        return float(np.interp(t, beat_times, beat_idx))

    div = _QUANTIZE_DIVISIONS
    quantized = []
    for start, end, pitch, amp in note_events:
        qs = round(to_beats(start) * div) / div
        qe = round(to_beats(end) * div) / div
        if qe <= qs:
            qe = qs + 1.0 / div          # 至少保留一个细分时值
        quantized.append((qs, qe, pitch, amp))

    # 整体平移整数拍，使最早音符落在 0 拍附近（保持拍相位不变）
    shift = float(np.floor(min(q[0] for q in quantized)))
    tempo_out = float(round(tempo))
    spb = 60.0 / tempo_out               # seconds per beat（重建后的恒定拍长）
    result = [
        ((qs - shift) * spb, (qe - shift) * spb, pitch, amp)
        for qs, qe, pitch, amp in quantized
    ]
    return result, tempo_out


def _events_to_midi(note_events, tempo: float):
    """Build a single-track PrettyMIDI from ``(start, end, pitch, amplitude)`` events."""
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    inst = pretty_midi.Instrument(program=0)
    for start, end, pitch, amp in note_events:
        vel = min(127, max(1, int(round(amp * 127))))
        inst.notes.append(pretty_midi.Note(velocity=vel, pitch=int(pitch), start=start, end=end))
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

    events = [
        (n.start, n.end, n.pitch, n.velocity / 127.0)
        for inst in pm.instruments for n in inst.notes
    ]

    if melody_only:
        melody_midi = _reduce_to_melody(events)
        if melody_midi is not None and melody_midi.instruments and melody_midi.instruments[0].notes:
            events = [
                (n.start, n.end, n.pitch, n.velocity / 127.0)
                for n in melody_midi.instruments[0].notes
            ]
            log_message(f'  ↳ 仅主旋律：{n_notes} 音符 → 约简为 {len(events)} 音符（天际线）')
        else:
            log_message('  [仅主旋律] 约简结果为空，回退到完整转录。', logging.WARNING)

    # ── 3b) Beat-grid quantization（拍点对齐，节奏可读性的关键）────────────────
    tempo_bpm = 120.0
    if _QUANTIZE_TO_BEATS:
        _report(0.55, '正在检测节拍并对齐小节网格…')
        try:
            q = _quantize_to_beat_grid(events, audio, _PIANO_SAMPLE_RATE)
        except Exception as exc:
            q = None
            log_message(f'  [节拍量化] 检测异常，保留原始时间：{exc}', logging.WARNING)
        if q is not None:
            events, tempo_bpm = q
            log_message(f'  ↳ 节拍量化：♩={tempo_bpm:.0f}，已对齐 1/{_QUANTIZE_DIVISIONS} 拍网格')
        else:
            log_message('  [节拍量化] 拍点不足，保留原始时间。', logging.WARNING)

    try:
        _events_to_midi(events, tempo_bpm).write(str(midi_path))
    except Exception as exc:
        log_message(f'  ✗ MIDI 重建失败：{exc}', logging.WARNING)
        return None

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
