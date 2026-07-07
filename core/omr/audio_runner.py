# core/omr/audio_runner.py — audio transcription engine (basic-pitch → MIDI → MusicXML)
"""Audio-to-MusicXML transcription via Spotify basic-pitch (ONNX backend).

Pipeline
--------
audio (mp3/wav/flac/m4a/ogg) → basic-pitch ONNX → MIDI → music21 → MusicXML.

The output directory mirrors the shape of the OMR runners (Audiveris/Homr): it
contains ``<stem>.musicxml``, so ``pipeline.process_single_input_to_jianpu`` can
consume the result through ``find_first_musicxml_file()`` with zero downstream
changes — the jianpu / staff render chain is engine-agnostic below that point.

Backend
-------
basic-pitch is installed with ``--no-deps`` (see requirements notes); only
``onnxruntime`` is present, so its ``Model`` class loads the bundled ``nmp.onnx``
(~0.23 MB). basic-pitch hardcodes a CPU session, so GPU acceleration is opt-in via
``_build_gpu_model`` which swaps in a DirectML/CUDA ``InferenceSession`` — matching
the DirectML path Homr already uses.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from ..utils import log_message

# basic-pitch default thresholds, surfaced here as tuning knobs (see plan §5.3).
# Raising the thresholds suppresses spurious/short notes at the cost of recall.
_ONSET_THRESHOLD = 0.5
_FRAME_THRESHOLD = 0.3
_MIN_NOTE_LENGTH_MS = 127.70
# Frequency floor (Hz). basic-pitch on real recordings emits sparse sub-bass octave
# artifacts (e.g. spurious E1/G1 under a mid-register piano line) that jianpu cannot
# represent — they overflow jianpu-ly's octave-dot range ("Can't handle octave ,,,,").
# C2 (~65.4 Hz) drops these while keeping the normal melodic/bass range; tunable.
_MIN_FREQUENCY_HZ = 65.4  # C2


def _build_gpu_model(model_path):
    """Build a basic-pitch Model backed by a GPU onnxruntime session, or None.

    basic-pitch's ``Model.__init__`` always creates a ``CPUExecutionProvider``
    session. When a GPU provider is available we construct the Model, then replace
    its ONNX session with one bound to DirectML/CUDA (CPU kept as the fallback
    provider). Returns None to signal "use the default CPU path" on any problem.
    """
    try:
        import onnxruntime as ort

        avail = ort.get_available_providers()
        gpu_providers = [
            p for p in ('DmlExecutionProvider', 'CUDAExecutionProvider') if p in avail
        ]
        if not gpu_providers:
            return None

        from basic_pitch.inference import Model

        model = Model(model_path)
        if model.model_type != Model.MODEL_TYPES.ONNX:
            # Only the ONNX backend exposes a swappable ort session.
            return None
        session = ort.InferenceSession(
            str(model_path), providers=gpu_providers + ['CPUExecutionProvider']
        )
        # onnxruntime 不会为"请求了但加载失败"的 provider 抛异常，只会静默退回 CPU
        # （例如 CUDA DLL 依赖缺失）。核对实际启用的 provider，避免误报使用了 GPU。
        active = session.get_providers()
        if not any(p in active for p in gpu_providers):
            return None
        model.model = session
        return model
    except Exception as exc:
        log_message(f'  [basic-pitch] GPU 会话构建失败，回退 CPU：{exc}', logging.WARNING)
        return None


def run_audio_transcription(
    source_file: Path,
    output_dir: Path,
    *,
    use_gpu_inference: Optional[bool] = None,
    progress_fn: Optional[Callable[[float, str], None]] = None,
) -> Optional[Path]:
    """Transcribe an audio file to MusicXML via basic-pitch + music21.

    Parameters
    ----------
    source_file:        Input audio file (mp3/wav/flac/m4a/ogg).
    output_dir:         Directory to write ``<stem>.mid`` and ``<stem>.musicxml``.
    use_gpu_inference:  True/False to force GPU/CPU; None to auto-detect a GPU
                        provider and use it when present.
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

    # ── 1) audio → MIDI (basic-pitch) ───────────────────────────────────────
    _report(0.05, '[basic-pitch] 加载音频转录模型…')
    try:
        from basic_pitch import ICASSP_2022_MODEL_PATH
        from basic_pitch.inference import predict
    except Exception as exc:
        log_message(
            f'  ✗ basic-pitch 未安装或导入失败：{exc}\n'
            '    → 音频转录依赖需手动安装：\n'
            '      pip install --no-deps basic-pitch\n'
            '      pip install librosa onnxruntime pretty_midi mir_eval music21 "resampy==0.4.3"',
            logging.WARNING,
        )
        return None

    model = ICASSP_2022_MODEL_PATH
    if use_gpu_inference is False:
        log_message('  [basic-pitch] 按设置使用 CPU 推理。')
    else:
        gpu_model = _build_gpu_model(ICASSP_2022_MODEL_PATH)
        if gpu_model is not None:
            model = gpu_model
            log_message('  [basic-pitch] 使用 GPU 推理会话（DirectML/CUDA）。')
        elif use_gpu_inference is True:
            log_message('  [basic-pitch] 未找到可用 GPU 加速后端，回退 CPU 推理。', logging.WARNING)
        else:
            log_message('  [basic-pitch] GPU 不可用，使用 CPU 推理。')

    _report(0.15, '[basic-pitch] 正在转录音频为 MIDI…')
    try:
        _model_output, midi_data, note_events = predict(
            str(source_file),
            model,
            onset_threshold=_ONSET_THRESHOLD,
            frame_threshold=_FRAME_THRESHOLD,
            minimum_note_length=_MIN_NOTE_LENGTH_MS,
            minimum_frequency=_MIN_FREQUENCY_HZ,
        )
    except Exception as exc:
        log_message(f'  ✗ basic-pitch 音频转录失败：{exc}', logging.WARNING)
        return None

    if not note_events:
        log_message(
            f'  ✗ 音频中未识别到音符：{source_file.name}\n'
            '    → 可能原因：音频过短/过弱，或几乎无明确音高（纯打击乐或噪声）',
            logging.WARNING,
        )
        return None

    midi_path = output_dir / f'{stem}.mid'
    try:
        midi_data.write(str(midi_path))
    except Exception as exc:
        log_message(f'  ✗ MIDI 写入失败：{exc}', logging.WARNING)
        return None
    log_message(f'  ↳ basic-pitch 识别到 {len(note_events)} 个音符 → {midi_path.name}')

    # ── 2) MIDI → MusicXML (music21) ────────────────────────────────────────
    # 裸 MIDI 无小节线；music21 依据拍号/速度推断量化与小节划分，这一步的质量
    # 直接决定简谱可读性（详见开发计划 §5.1）。
    _report(0.55, '正在将 MIDI 转换为 MusicXML…')
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

    _report(0.65, '音频转录完成，正在生成乐谱…')
    log_message(f'  ✓ 音频转录完成 → {xml_path.name}')
    return output_dir
