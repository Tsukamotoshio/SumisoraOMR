# core/homr_runner.py — homr OMR 引擎封装
# 本模块将本地 homr 仓库目录作为可选 OMR 引擎，支持图片输入和 PDF 首页。

import logging
import io
import os
import shutil
import sys
import threading
from contextlib import contextmanager
import time
from pathlib import Path
from typing import Optional

from ..config import HOMR_SOURCE_DIR_NAME, MAX_HOMR_SECONDS, OMR_ENGINE_DIR_NAME
from ..image.image_preprocess import preprocess_image_for_omr
from ..utils import find_first_musicxml_file, get_app_base_dir, log_message, safe_remove_file

_PATH_LOCK = threading.Lock()


def _get_available_memory_mb() -> Optional[int]:
    """返回当前可用物理内存（MB）。仅 Windows 有效，其他平台返回 None。"""
    if os.name != 'nt':
        return None
    try:
        import ctypes

        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ('dwLength', ctypes.c_ulong),
                ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_uint64),
                ('ullAvailPhys', ctypes.c_uint64),
                ('ullTotalPageFile', ctypes.c_uint64),
                ('ullAvailPageFile', ctypes.c_uint64),
                ('ullTotalVirtual', ctypes.c_uint64),
                ('ullAvailVirtual', ctypes.c_uint64),
                ('ullAvailExtendedVirtual', ctypes.c_uint64),
            ]

        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
        return int(stat.ullAvailPhys // (1024 * 1024))
    except Exception:
        return None


def _compute_segnet_batch_size(avail_mb: Optional[int]) -> int:
    """根据可用内存确定 SegNet 推理批大小。

    内存越少，每批处理的 patch 越少，降低内存峰值。
    阈值经验来自：模型权重 ~150 MB + ONNX 运行时开销 ~200 MB + 批量 numpy 缓冲。
    """
    if avail_mb is None:
        return 8  # 无法检测，使用默认值
    if avail_mb >= 3000:
        return 8
    if avail_mb >= 2000:
        return 4
    if avail_mb >= 1200:
        return 2
    return 1


@contextmanager
def _suppress_homr_output():
    """将 homr 库的 stdout/stderr 重定向到 /dev/null，避免淹没应用日志。"""
    devnull = open(os.devnull, 'w', encoding='utf-8', errors='replace')
    old_stdout, old_stderr = sys.stdout, sys.stderr
    # 同时压制 homr 可能使用的 root logger handlers 产生的控制台输出
    root_logger = logging.getLogger()
    old_handlers = root_logger.handlers[:]
    # 暂时移除所有 StreamHandler（避免 homr 内部 logging 写到控制台）
    stream_handlers = [h for h in old_handlers if isinstance(h, logging.StreamHandler)
                       and not isinstance(h, logging.FileHandler)]
    for h in stream_handlers:
        root_logger.removeHandler(h)
    sys.stdout = devnull
    sys.stderr = devnull
    try:
        yield
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        devnull.close()
        for h in stream_handlers:
            root_logger.addHandler(h)


def _pdf_first_page_to_png(
    pdf_path: Path,
    output_dir: Path,
    engine_label: str = 'homr',
) -> Optional[Path]:
    """Convert the first page of a PDF to a PNG file using PyMuPDF.

    Returns the PNG path on success, or None if conversion fails.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        log_message(f'[{engine_label}] PyMuPDF (fitz) 未安装，无法处理 PDF 输入。', logging.WARNING)
        return None
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(str(pdf_path))
        page = doc.load_page(0)
        mat = fitz.Matrix(2.0, 2.0)  # 2× zoom → ~144 DPI from 72 DPI base
        pix = page.get_pixmap(matrix=mat, alpha=False)
        doc.close()
        png_path = output_dir / (pdf_path.stem + '_page1.png')
        pix.save(str(png_path))
        return png_path
    except Exception as exc:
        log_message(f'[{engine_label}] PDF 首页转 PNG 失败：{exc}', logging.WARNING)
        return None


def _add_nvidia_cuda_dll_dirs() -> None:
    """将 nvidia pip 包（nvidia-cuda-runtime-cu12 / nvidia-cublas-cu12 /
    nvidia-cudnn-cu12 等）安装的 DLL 目录预置到 PATH 环境变量，
    同时通过 os.add_dll_directory() 注册（供 ctypes 使用）。

    onnxruntime 的 C++ 层用 LoadLibrary 加载 CUDA Provider DLL，走的是
    标准 PATH 搜索顺序，因此必须在首次创建 CUDAExecutionProvider session
    前修改 PATH；os.add_dll_directory() 额外供 ctypes 检测用。
    仅在 Windows 上生效，其他平台无操作。
    """
    if os.name != 'nt':
        return
    import site
    dirs: list[str] = []
    for sp in site.getsitepackages():
        nvidia_dir = Path(sp) / 'nvidia'
        if not nvidia_dir.is_dir():
            continue
        for pkg_dir in nvidia_dir.iterdir():
            bin_dir = pkg_dir / 'bin'
            if bin_dir.is_dir():
                dirs.append(str(bin_dir))
                try:
                    os.add_dll_directory(str(bin_dir))
                except Exception:
                    pass
        break  # 只处理第一个 site-packages（venv 内）
    if dirs:
        os.environ['PATH'] = ';'.join(dirs) + ';' + os.environ.get('PATH', '')


# 模块导入时立即设置 PATH，确保在任何 onnxruntime CUDA session 创建之前 DLL 已可搜索。
# onnxruntime 第一次试图创建 CUDAExecutionProvider session 时才加载
# onnxruntime_providers_cuda.dll，若此前 PATH 不包含 cudnn64_9.dll 所在目录则失败。
_add_nvidia_cuda_dll_dirs()


def _run_with_heartbeat(
    fn,
    label: str,
    heartbeat_interval: int = 30,
    on_heartbeat=None,
) -> None:
    """在子线程中执行 fn()，主线程每隔 heartbeat_interval 秒输出一条进度日志。

    若 fn 抛出异常，异常会在主线程重新抛出。
    on_heartbeat: 可选回调，签名 on_heartbeat(elapsed_seconds: int)，每次心跳触发一次。
    """
    exc_holder: list[BaseException] = []

    def _worker():
        try:
            with _suppress_homr_output():
                fn()
        except BaseException as e:
            exc_holder.append(e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    start = time.monotonic()
    while t.is_alive():
        t.join(timeout=heartbeat_interval)
        if t.is_alive():
            elapsed = int(time.monotonic() - start)
            log_message(f'  {label}仍在识别中…已耗时 {elapsed} 秒，请耐心等待。')
            if on_heartbeat is not None:
                try:
                    on_heartbeat(elapsed)
                except Exception:
                    pass
    if exc_holder:
        raise exc_holder[0]


def find_homr_source_dir() -> Optional[Path]:
    """Find the local homr source directory under the app root or omr_engine.

    When frozen by PyInstaller only the ``homr/`` Python package is bundled at
    _MEIPASS/omr_engine/homr/homr (collected via collect_tree on the package
    subdirectory).  Adding _MEIPASS/omr_engine/homr to sys.path makes
    ``import homr.main`` resolve to the ``homr/`` sub-package inside it.
    """
    base_dir = get_app_base_dir()
    candidates = [
        base_dir / HOMR_SOURCE_DIR_NAME,
        base_dir / OMR_ENGINE_DIR_NAME / HOMR_SOURCE_DIR_NAME,
    ]
    # PyInstaller frozen: collect_tree bundles repo root at _MEIPASS/omr_engine/homr
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        meipass_path = Path(meipass)
        candidates.extend([
            meipass_path / HOMR_SOURCE_DIR_NAME,
            meipass_path / OMR_ENGINE_DIR_NAME / HOMR_SOURCE_DIR_NAME,
        ])
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _homr_api_available() -> bool:
    """Check whether homr can be imported from the local repository or installed package."""
    try:
        import homr.main  # noqa: F401
        return True
    except Exception:
        source_dir = find_homr_source_dir()
        if source_dir is None:
            return False
        with _PATH_LOCK:
            added = str(source_dir) not in sys.path
            if added:
                sys.path.insert(0, str(source_dir))
        try:
            import homr.main  # noqa: F401
            return True
        except Exception:
            return False
        finally:
            if added:
                with _PATH_LOCK:
                    if str(source_dir) in sys.path:
                        sys.path.remove(str(source_dir))


def check_homr_available() -> bool:
    """Return True when homr can be imported or found locally."""
    if _homr_api_available():
        return True
    if getattr(sys, 'frozen', False):
        # In the packaged distribution homr is bundled — if we reach here the
        # bundle is incomplete or the module failed to import for another reason.
        log_message(
            '[homr] homr 引擎模块加载失败。分发包可能不完整，请重新下载最新版本。',
        )
    else:
        log_message(
            '[homr] homr 引擎不可用。请确认已将 homr 仓库 clone 到 omr_engine/homr，'
            '或在当前环境安装可用的 homr Python 包。',
        )
    return False


def _ensure_homr_import_path() -> Optional[Path]:
    source_dir = find_homr_source_dir()
    if source_dir is None:
        return None
    with _PATH_LOCK:
        if str(source_dir) not in sys.path:
            sys.path.insert(0, str(source_dir))
            return source_dir
    return None


def _add_nvidia_cuda_dll_dirs() -> None:
    pass  # already called at module level above; kept for explicit call sites


def _cuda_dlls_available() -> bool:
    """检查 CUDA 和 cuDNN 核心 DLL 是否存在（文件级检查，不依赖 LoadLibrary）。"""
    import site, shutil
    # 检查 nvidia pip 包路径
    for sp in site.getsitepackages():
        cuda_rt = Path(sp) / 'nvidia' / 'cuda_runtime' / 'bin' / 'cudart64_12.dll'
        cudnn = Path(sp) / 'nvidia' / 'cudnn' / 'bin' / 'cudnn64_9.dll'
        if cuda_rt.exists() and cudnn.exists():
            return True
    # 检查系统 PATH（已安装 CUDA Toolkit 的情况）
    return bool(shutil.which('cudart64_12.dll') and shutil.which('cudnn64_9.dll'))


def _log_homr_gpu_mode() -> None:
    """记录 homr 实际将使用的计算设备（仅用于信息输出）。"""
    try:
        import onnxruntime as _rt
        _avail = _rt.get_available_providers()
        if 'DmlExecutionProvider' in _avail:
            log_message('[homr] GPU: DirectML（DmlExecutionProvider 可用，将尝试集显/独显加速）。')
        else:
            log_message('[homr] GPU: DirectML 不可用，将使用 CPU 推理。')
    except Exception:
        pass


def _homr_gpu_available() -> bool:
    """检查 DmlExecutionProvider 是否可用（集显/独显 DirectML 加速）。"""
    try:
        import onnxruntime as rt
        return 'DmlExecutionProvider' in rt.get_available_providers()
    except Exception:
        return False


def _preprocess_for_homr(image_path: Path, work_dir: Path) -> Optional[Path]:
    """Homr 专用预处理：几何校正 + 超分 - 激进锐化。

    不同于通用预处理，这个函数避免激进的去噪锐化（UnsharpMask radius=3, percent=300）
    可能会放大 artifacts，导致 Homr SegNet 误识别 staff。

    步骤：
    1. 白边裁剪（crop_white_border）
    2. 旋转校正（detect_and_correct_rotation）
    3. 梯度修正（correct_gradient）
    4. 超分辨率（waifu2x, 如果最短边 < 1200px）
    5. 降采样（如果超过像素上限）

    跳过：激进锐化（denoise_and_sharpen）
    """
    from pathlib import Path
    from ..image.image_preprocess import (
        crop_white_border, detect_and_correct_rotation, correct_gradient,
        fit_image_within_pixel_limit,
        LOW_RES_PIXEL_THRESHOLD, AUDIVERIS_MAX_PIXELS, _measure_laplacian_stddev,
    )
    from ..image.sr_upscale import upscale_image
    from PIL import Image

    try:
        work_dir.mkdir(parents=True, exist_ok=True)

        with Image.open(image_path) as img:
            working = img.convert('RGB')

        # 步骤 1: 白边裁剪
        working, border_ratio = crop_white_border(working)
        if border_ratio > 0.02:
            log_message(f'  [预处理] 白边裁剪 {border_ratio:.1%}')

        # 步骤 2: 旋转校正
        working, angle = detect_and_correct_rotation(working)
        if abs(angle) >= 0.5:
            log_message(f'  [预处理] 旋转校正 {angle:+.1f}°')

        # 步骤 3: 梯度修正
        working = correct_gradient(working)

        # 保存中间结果
        intermediate_path = work_dir / f'geo_{image_path.stem}.png'
        working.save(intermediate_path)
        current_path = intermediate_path

        # 步骤 4: 超分辨率（如果需要）
        with Image.open(current_path) as chk:
            min_dim = min(chk.size)
        if min_dim < LOW_RES_PIXEL_THRESHOLD:
            upscaled_path = work_dir / f'sr_{image_path.stem}.png'
            if upscale_image(current_path, upscaled_path, scale=2):
                safe_remove_file(current_path)
                current_path = upscaled_path
                log_message(
                    f'  [预处理] 最短边 {min_dim}px < {LOW_RES_PIXEL_THRESHOLD}px，'
                    '已执行 2× 超分辨率放大。'
                )

        # 步骤 5: 降采样（如果超过像素上限）
        rescaled = fit_image_within_pixel_limit(current_path, work_dir, max_pixels=AUDIVERIS_MAX_PIXELS)
        if rescaled is not None:
            safe_remove_file(current_path)
            current_path = rescaled

        # 重命名为规范输出
        final_path = work_dir / f'omr_ready_{image_path.stem}.png'
        if current_path != final_path:
            current_path.rename(final_path)

        return final_path

    except Exception as exc:
        log_message(f'[预处理] Homr 预处理失败: {exc}', logging.WARNING)
        return None


def run_homr_batch(
    source_image: Path,
    output_dir: Path,
    use_gpu_inference: Optional[bool] = None,
    progress_fn=None,
) -> Optional[Path]:
    """Run homr on a single image file or a PDF's first page, returning the output directory.

    Homr should receive the whole page after preprocessing, not per-line slices.
    progress_fn: 可选进度回调，签名 progress_fn(value: float 0.0-1.0, message: str)。
    """
    if source_image.suffix.lower() == '.pdf':
        image_path = _pdf_first_page_to_png(source_image, output_dir, engine_label='homr')
        if image_path is None:
            return None
    else:
        image_path = source_image

    if image_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        log_message(f'[homr] 仅支持图片输入或 PDF 首页，跳过: {source_image.name}', )
        return None

    output_dir.mkdir(parents=True, exist_ok=True)

    if use_gpu_inference is None:
        use_gpu_inference = _homr_gpu_available()

    preprocess_dir = output_dir / '_homr_preprocessed'
    log_message('[homr] 正在进行图像预处理…')
    # Homr 使用温和预处理：几何校正 + 超分 - 激进锐化
    # 激进锐化会放大 artifacts 导致 SegNet 误识别（如 416 个虚假小节）
    preprocessed_image = _preprocess_for_homr(image_path, preprocess_dir)
    if preprocessed_image is not None:
        image_path = preprocessed_image
        log_message(f'[homr] 温和预处理完成: {preprocessed_image.name}')
    else:
        log_message('[homr] 预处理未生成新文件，使用原始输入。')

    safe_image_path = output_dir / f'homr_input{image_path.suffix.lower()}'
    if not safe_image_path.exists() or safe_image_path.stat().st_size != image_path.stat().st_size:
        shutil.copy2(str(image_path), str(safe_image_path))
    image_path = safe_image_path
    log_message(f'[homr] 输入文件已准备: {safe_image_path.name}')

    # 写入编辑器参考图（与 homr 实际输入保持一致，每次覆盖）
    try:
        shutil.copy2(str(safe_image_path), str(output_dir / '_omr_reference.png'))
    except OSError:
        pass

    # ── 内存检测与 batch_size 自适应 ──────────────────────────────────────────
    _avail_mb = _get_available_memory_mb()
    _batch_size = _compute_segnet_batch_size(_avail_mb)
    if _avail_mb is not None:
        if _avail_mb < 1500:
            log_message(
                f'[homr] 警告：可用内存仅 {_avail_mb} MB，低于建议的 1.5 GB。'
                f' SegNet 批大小已自动降至 {_batch_size}，识别可能较慢或失败。',
                logging.WARNING,
            )
        else:
            log_message(f'[homr] 可用内存 {_avail_mb} MB，SegNet 批大小: {_batch_size}。')

    # ── 心跳进度回调（在识别等待期间推进 GUI 进度条）────────────────────────────
    # 将识别阶段进度从 0.10 线性推进到 0.60，基于 MAX_HOMR_SECONDS 做归一化。
    def _on_heartbeat(elapsed: int) -> None:
        if progress_fn is None:
            return
        frac = min(elapsed / MAX_HOMR_SECONDS, 1.0)
        value = 0.10 + frac * 0.50  # 0.10 → 0.60
        try:
            progress_fn(value, f'[homr] 识别中…已耗时 {elapsed}s')
        except Exception:
            pass

    source_dir = _ensure_homr_import_path()
    _add_nvidia_cuda_dll_dirs()
    try:
        import homr.main as homr_main
        # download_weights 需要联网，在弱网/限速环境下可能长时间挂起，限制为 120s
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as _TimeoutError
        with ThreadPoolExecutor(max_workers=1) as _dl_ex:
            # homr.download_weights 只会下载缺失的 ONNX 模型权重。
            # 如果本地已经存在对应 GPU/CPU 模型文件，则不会联网下载。
            log_message('[homr] 检查模型权重…')
            with _suppress_homr_output():
                _dl_future = _dl_ex.submit(homr_main.download_weights, use_gpu_inference)
                try:
                    _dl_future.result(timeout=120)
                except _TimeoutError:
                    log_message('[homr] 模型权重下载超时（>120s），请检查网络后重试。')
                    return None
                except Exception as _dl_exc:
                    log_message(f'[homr] 模型权重下载失败: {_dl_exc}')
                    return None
        config = homr_main.ProcessingConfig(
            enable_debug=False,
            enable_cache=False,
            write_staff_positions=False,
            read_staff_positions=False,
            selected_staff=-1,
            use_gpu_inference=use_gpu_inference,
            segnet_batch_size=_batch_size,
        )
        xml_args = homr_main.XmlGeneratorArguments(False, None, None)
        mode_label = 'GPU' if use_gpu_inference else 'CPU'
        log_message(f'[homr] 开始识别（{mode_label} 模式）…')
        _run_with_heartbeat(
            lambda: homr_main.process_image(str(image_path), config, xml_args),
            label='[homr] ',
            on_heartbeat=_on_heartbeat,
        )
        log_message('[homr] 识别完成。')
    except Exception as exc:
        _exc_str = str(exc)
        # CUDA / GPU 错误（session 创建成功但推理时失败）→ 回退 CPU 重试
        _is_gpu_err = use_gpu_inference and (
            'cuda' in _exc_str.lower()
            or 'CUDAExecutionProvider' in _exc_str
            or 'DmlExecutionProvider' in _exc_str
            or 'not active after session creation' in _exc_str
        )
        if _is_gpu_err:
            try:
                _cpu_config = homr_main.ProcessingConfig(
                    enable_debug=False,
                    enable_cache=False,
                    write_staff_positions=False,
                    read_staff_positions=False,
                    selected_staff=-1,
                    use_gpu_inference=False,
                    segnet_batch_size=_batch_size,
                )
                log_message('[homr] 回退到 CPU 模式重试…')
                _run_with_heartbeat(
                    lambda: homr_main.process_image(str(image_path), _cpu_config, xml_args),
                    label='[homr] ',
                    on_heartbeat=_on_heartbeat,
                )
                log_message('[homr] 识别完成（CPU 模式）。')
            except Exception as _cpu_exc:
                log_message(f'[homr] CPU 回退也失败: {_cpu_exc}')
                return None
        else:
            log_message(f'[homr] 识别失败: {exc}')
            return None
    finally:
        if source_dir is not None:
            with _PATH_LOCK:
                if str(source_dir) in sys.path:
                    sys.path.remove(str(source_dir))

    mxl = find_first_musicxml_file(output_dir, image_path.stem)
    if mxl is None:
        log_message(f'[homr] 未找到输出 MusicXML，可能识别失败。', )
        return None

    return output_dir
