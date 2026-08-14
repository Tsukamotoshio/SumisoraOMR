# core/image/sr_upscale.py — SR engine discovery and upscaling
# GPU detection, waifu2x-ncnn-vulkan, Real-ESRGAN (ncnn binary + Python script fallback)
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

_WIN_NO_WINDOW: int = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

from ..config import (
    REALESRGAN_RUNTIME_DIR_NAME,
    RUNTIME_ASSETS_DIR_NAME,
    SREngine,
    WAIFU2X_RUNTIME_DIR_NAME,
)
from ..utils import (
    find_packaged_runtime_dir,
    get_runtime_search_roots,
    log_message,
)


# Current SR engine — injected by the worker process at startup via set_sr_engine()
_current_sr_engine: str = SREngine.WAIFU2X.value


def set_sr_engine(engine: str) -> None:
    """Set the active SR engine ('waifu2x' or 'realesrgan'). Called by worker_main at startup."""
    global _current_sr_engine
    _current_sr_engine = engine


# Cached Vulkan GPU index — resolved once, reused across calls
_NCNN_GPU_ID: Optional[int] = None
_NCNN_GPU_RESOLVED: bool = False


def _detect_best_ncnn_gpu(exe: Path) -> Optional[int]:
    """Probe ncnn-vulkan device list and return the index of the first discrete GPU.

    Device 0 is typically an Intel iGPU. Parses the ``[N DeviceName] queueC`` lines
    from the binary's -v output and picks the first entry matching discrete-GPU keywords.
    Returns None on failure or when no discrete GPU is found (ncnn picks automatically).
    """
    global _NCNN_GPU_ID, _NCNN_GPU_RESOLVED
    if _NCNN_GPU_RESOLVED:
        return _NCNN_GPU_ID
    _NCNN_GPU_RESOLVED = True

    import re
    import tempfile
    try:
        from PIL import Image as _PilImage
    except ImportError:
        return None

    with tempfile.TemporaryDirectory() as td:
        probe_in = Path(td) / '_probe.png'
        probe_out = Path(td) / '_probe_out.png'
        try:
            _PilImage.new('RGB', (32, 32)).save(probe_in)
            result = subprocess.run(
                [str(exe), '-i', str(probe_in), '-o', str(probe_out), '-v'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
                creationflags=_WIN_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    text = (result.stderr or '') + '\n' + (result.stdout or '')
    devices: list[tuple[int, str]] = []
    for m in re.finditer(r'\[(\d+)\s+([^\]]+?)\]\s+queueC', text):
        devices.append((int(m.group(1)), m.group(2).strip()))
    if not devices:
        return None

    seen: set[int] = set()
    unique: list[tuple[int, str]] = []
    for idx, name in devices:
        if idx in seen:
            continue
        seen.add(idx)
        unique.append((idx, name))

    discrete_keywords = ('NVIDIA', 'GeForce', 'RTX', 'Radeon RX', 'Radeon Pro', 'Arc')
    integrated_keywords = ('Intel(R) Graphics', 'UHD', 'Iris', 'Vega', 'AMD Radeon Graphics')
    for idx, name in unique:
        if any(k in name for k in discrete_keywords) and not any(k in name for k in integrated_keywords):
            _NCNN_GPU_ID = idx
            log_message(f'ncnn-vulkan: 选用独显 GPU {idx} ({name})')
            return idx
    return None


def _ncnn_gpu_args(exe: Path) -> list[str]:
    """Return ``['-g', '<id>']`` for the detected discrete GPU, or ``[]``."""
    gpu_id = _detect_best_ncnn_gpu(exe)
    return ['-g', str(gpu_id)] if gpu_id is not None else []


def _find_ncnn_executable(exe_stem: str, runtime_dir_name: str) -> Optional[Path]:
    """Search for an ncnn-vulkan binary in app directories, common install paths, and PATH."""
    import os
    import shutil

    exe_names = [f'{exe_stem}.exe', exe_stem]
    candidates: list[Path] = []

    for base_dir in get_runtime_search_roots():
        for exe_name in exe_names:
            candidates.extend([
                base_dir / exe_name,
                base_dir / runtime_dir_name / exe_name,
                base_dir / RUNTIME_ASSETS_DIR_NAME / runtime_dir_name / exe_name,
                base_dir / exe_stem / exe_name,
                base_dir / 'tools' / exe_name,
            ])

    packaged = find_packaged_runtime_dir(runtime_dir_name)
    if packaged is not None:
        for exe_name in exe_names:
            candidates.append(packaged / exe_name)

    program_files = os.environ.get('PROGRAMFILES', r'C:\Program Files')
    local_app_data = os.environ.get('LOCALAPPDATA', '')
    for root in [Path(program_files), Path(local_app_data) / 'Programs']:
        for exe_name in exe_names:
            candidates.append(root / exe_stem / exe_name)

    for exe_name in exe_names:
        found = shutil.which(exe_name)
        if found:
            candidates.append(Path(found))

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def find_waifu2x_executable() -> Optional[Path]:
    """Search for waifu2x-ncnn-vulkan in app directory, common install paths, and PATH."""
    return _find_ncnn_executable('waifu2x-ncnn-vulkan', WAIFU2X_RUNTIME_DIR_NAME)


def find_realesrgan_executable() -> Optional[Path]:
    """Search for realesrgan-ncnn-vulkan binary in app directory and PATH."""
    return _find_ncnn_executable('realesrgan-ncnn-vulkan', REALESRGAN_RUNTIME_DIR_NAME)


def _find_realesrgan_python_script() -> Optional[Path]:
    """Find inference_realesrgan.py from the cloned Real-ESRGAN repo at upscaling_engine/realesrgan/."""
    for base_dir in get_runtime_search_roots():
        candidate = base_dir / 'upscaling_engine' / 'realesrgan' / 'inference_realesrgan.py'
        if candidate.exists():
            return candidate
    return None


def upscale_with_realesrgan(input_path: Path, output_path: Path, scale: int = 4) -> bool:
    """Upscale using Real-ESRGAN anime model (4×). Tries ncnn-vulkan binary first, Python script fallback.

    Always uses the anime/line-art model which is best suited for sheet music.
    The scale parameter is accepted for API compatibility but Real-ESRGAN always runs at 4×;
    callers that request 2× will get a 4× result which fit_image_within_pixel_limit can downsample.
    """
    # ── 优先：realesrgan-ncnn-vulkan 二进制（无 Python 依赖，Vulkan 加速）
    exe = find_realesrgan_executable()
    if exe is not None:
        models_dir = exe.parent / 'models'
        cmd = [
            str(exe),
            '-i', str(input_path),
            '-o', str(output_path),
            '-n', 'realesrgan-x4plus-anime',
            '-s', '4',
        ]
        if models_dir.is_dir():
            cmd += ['-m', str(models_dir)]
        cmd += _ncnn_gpu_args(exe)
        log_message('使用 realesrgan-ncnn-vulkan 进行 4× 超分辨率处理 (anime 模型)...')
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600,
                check=False,
                creationflags=_WIN_NO_WINDOW,
            )
            if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                log_message(f'Real-ESRGAN (ncnn) 超分辨率完成: {output_path.name}')
                return True
            detail = (result.stderr or result.stdout or '').strip()[:200]
            log_message(f'realesrgan-ncnn-vulkan 失败（退出码 {result.returncode}）: {detail}', logging.WARNING)
        except (OSError, subprocess.SubprocessError) as exc:
            log_message(f'realesrgan-ncnn-vulkan 调用异常: {exc}', logging.WARNING)

    # ── 回退：Python 脚本（需 pip install realesrgan 及 PyTorch）
    script = _find_realesrgan_python_script()
    if script is not None:
        import shutil as _shutil
        import tempfile as _tempfile
        with _tempfile.TemporaryDirectory() as tmp_dir:
            cmd = [
                sys.executable, str(script),
                '-n', 'RealESRGAN_x4plus_anime_6B',
                '-i', str(input_path),
                '-o', tmp_dir,
                '-s', '4',
                '--fp32',
            ]
            log_message('使用 Real-ESRGAN Python 脚本进行 4× 超分辨率处理 (anime 模型)...')
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=600,
                    check=False,
                )
                out_file = Path(tmp_dir) / (input_path.stem + '.png')
                if not out_file.exists():
                    out_file = Path(tmp_dir) / input_path.name
                if result.returncode == 0 and out_file.exists() and out_file.stat().st_size > 0:
                    _shutil.copy2(str(out_file), str(output_path))
                    log_message(f'Real-ESRGAN (Python) 超分辨率完成: {output_path.name}')
                    return True
                detail = (result.stderr or result.stdout or '').strip()[:300]
                log_message(f'Real-ESRGAN Python 脚本失败: {detail}', logging.WARNING)
            except (OSError, subprocess.SubprocessError) as exc:
                log_message(f'Real-ESRGAN Python 脚本调用异常: {exc}', logging.WARNING)

    log_message(
        '未找到 Real-ESRGAN 可执行文件。\n'
        '  → 方案 A（推荐）：下载 realesrgan-ncnn-vulkan 二进制，\n'
        '    解压至 realesrgan-runtime/ 目录\n'
        '  → 方案 B：在 venv 中执行 pip install realesrgan，\n'
        '    并确保 upscaling_engine/realesrgan/ 子模块已克隆。',
        logging.WARNING,
    )
    return False


def upscale_image_with_waifu2x(input_path: Path, output_path: Path, scale: int = 2) -> bool:
    """Upscale an image using waifu2x-ncnn-vulkan (Vulkan GPU, scale 2× or 4×).
    Falls back gracefully if waifu2x-ncnn-vulkan is not installed."""
    waifu2x = find_waifu2x_executable()
    if waifu2x is None:
        log_message(
            '未找到 waifu2x-ncnn-vulkan，跳过超分辨率放大。'
            '（可从 https://github.com/nihui/waifu2x-ncnn-vulkan 下载）',
            logging.WARNING,
        )
        return False
    cmd = [str(waifu2x), '-i', str(input_path), '-o', str(output_path), '-s', str(scale), '-n', '1']
    cmd += _ncnn_gpu_args(waifu2x)
    log_message(f'使用 waifu2x-ncnn-vulkan 进行 {scale}x GPU 超分辨率处理...')
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
            check=False,
            creationflags=_WIN_NO_WINDOW,
        )
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            log_message(f'waifu2x 超分辨率完成: {output_path.name}')
            return True
        detail = (result.stderr or result.stdout or '').strip()[:200]
        log_message(f'waifu2x 处理失败（退出码 {result.returncode}）: {detail}', logging.WARNING)
        return False
    except (OSError, subprocess.SubprocessError) as exc:
        log_message(f'waifu2x 调用异常: {exc}', logging.WARNING)
        return False


def upscale_image(input_path: Path, output_path: Path, scale: int = 2) -> bool:
    """Route SR upscale to the configured engine (waifu2x or Real-ESRGAN).

    Falls back to waifu2x if Real-ESRGAN is selected but unavailable.
    Falls back to bicubic resize (returns False) if neither tool is found.

    当两个引擎**都**不可用时会明确报错。此前每个引擎各自只打一条 WARNING
    （"Real-ESRGAN 失败，回退到 waifu2x…" / "未找到 waifu2x-ncnn-vulkan…"），
    读起来像"已经回退到另一个引擎了"，而实际上两个都没跑成——2026-08 就是这样
    让两个 .exe 同时丢失的状态潜伏了近一周，期间所有转换都在用未放大的原图，
    识别质量整体下降却无人察觉。调用方只在确实需要超分时才进来（见
    image_preprocess 的 LOW_RES_PIXEL_THRESHOLD 判断），所以这里不存在误报。
    """
    attempted: list[str] = []
    if _current_sr_engine == SREngine.REALESRGAN.value:
        if upscale_with_realesrgan(input_path, output_path, scale):
            return True
        attempted.append('Real-ESRGAN')
        log_message('Real-ESRGAN 失败，回退到 waifu2x...', logging.WARNING)
    if upscale_image_with_waifu2x(input_path, output_path, scale):
        return True
    attempted.append('waifu2x')

    log_message(
        f'  ✗ 超分辨率未生效：{" 与 ".join(attempted)} 均不可用，'
        f'本次将使用未放大的原图，识别质量会明显下降。',
        logging.ERROR,
    )
    _warn_sr_unavailable_once()
    return False


_sr_unavailable_hint_shown = False


def _warn_sr_unavailable_once() -> None:
    """Print the actionable "how to restore SR" hint once per process."""
    global _sr_unavailable_hint_shown
    if _sr_unavailable_hint_shown:
        return
    _sr_unavailable_hint_shown = True
    # 直接问发现函数，而不是拼一条猜测的路径：_find_ncnn_executable 会依次搜
    # 应用目录、package-assets、常见安装位置和 PATH，硬编码单条路径会报错地方。
    for label, found, dir_name, url in (
        ('Real-ESRGAN', find_realesrgan_executable(), REALESRGAN_RUNTIME_DIR_NAME,
         'https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan/releases'),
        ('waifu2x', find_waifu2x_executable(), WAIFU2X_RUNTIME_DIR_NAME,
         'https://github.com/nihui/waifu2x-ncnn-vulkan/releases'),
    ):
        if found is not None:
            log_message(f'    {label}: 已找到 {found}（是运行失败，不是缺文件）', logging.ERROR)
        else:
            log_message(
                f'    {label}: 未找到可执行文件。请从 {url} 下载，'
                f'解压后放进 {dir_name}/ 目录（模型文件保持原样）。',
                logging.ERROR,
            )
