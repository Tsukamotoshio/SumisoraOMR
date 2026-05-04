# core/omr/homr_runner.py — Homr deep-learning OMR engine wrapper.
# Supports image input and multi-page PDF input.

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
    """Return the available physical memory in MB (Windows only; other platforms return None)."""
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
    """Determine the SegNet inference batch size based on available memory.

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
    """Redirect homr's stdout/stderr to /dev/null to prevent flooding the application log.

    同时压制 homr 可能使用的 root logger handlers 产生的控制台输出。
    """
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


def _pdf_pages_to_png(
    pdf_path: Path,
    output_dir: Path,
    engine_label: str = 'homr',
) -> list[Path]:
    """Convert all pages of a PDF to PNG files using PyMuPDF.

    Returns a list of PNG paths (one per page) on success, or an empty list on failure.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        log_message(f'[{engine_label}] PyMuPDF (fitz) 未安装，无法处理 PDF 输入。', logging.WARNING)
        return []
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(str(pdf_path))
        page_count = doc.page_count
        png_paths: list[Path] = []
        for i in range(page_count):
            page = doc.load_page(i)
            mat = fitz.Matrix(2.0, 2.0)  # 2× zoom → ~144 DPI from 72 DPI base
            pix = page.get_pixmap(matrix=mat, alpha=False)
            png_path = output_dir / f'{pdf_path.stem}_page{i + 1}.png'
            pix.save(str(png_path))
            png_paths.append(png_path)
        doc.close()
        log_message(f'[{engine_label}] PDF 共 {page_count} 页，已导出为 PNG。')
        return png_paths
    except Exception as exc:
        log_message(f'[{engine_label}] PDF 转 PNG 失败：{exc}', logging.WARNING)
        return []


def _add_nvidia_cuda_dll_dirs() -> None:
    """Prepend DLL directories from nvidia pip packages to PATH and register via os.add_dll_directory().

    onnxruntime 的 C++ 层用 LoadLibrary 加载 CUDA Provider DLL，走的是标准 PATH 搜索顺序，
    因此必须在首次创建 CUDAExecutionProvider session 前修改 PATH；
    os.add_dll_directory() 额外供 ctypes 检测用。仅在 Windows 上生效。
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
    """Run *fn* in a worker thread while emitting a progress log line every *heartbeat_interval* seconds.

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
        import homr.main  # noqa: F401  # type: ignore[import-not-found]
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
            import homr.main  # noqa: F401  # type: ignore[import-not-found]
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


def _cuda_dlls_available() -> bool:
    """Check for CUDA and cuDNN core DLLs via file-system inspection (does not call LoadLibrary)."""
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
    """Log the compute device homr will use (informational only)."""
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
    """Return True if DmlExecutionProvider is available (integrated/discrete GPU via DirectML)."""
    try:
        import onnxruntime as rt
        return 'DmlExecutionProvider' in rt.get_available_providers()
    except Exception:
        return False


def _preprocess_for_homr(image_path: Path, work_dir: Path) -> Optional[Path]:
    """Homr-specific preprocessing: geometry correction + super-resolution, without aggressive sharpening.

    不同于通用预处理，跳过激进去噪锐化（UnsharpMask radius=3, percent=300）以避免
    放大 artifacts 导致 Homr SegNet 误识别 staff。

    步骤：白边裁剪 → 旋转校正 → 梯度修正 → 超分辨率（如需）→ 降采样（如需）
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


def _merge_homr_musicxml_pages(page_mxl_files: list[Path], output_path: Path) -> bool:
    """Merge multiple per-page MusicXML files into a single MusicXML file.

    使用 music21 解析每页，将后续页的小节（deepcopy）追加到第一页对应声部末尾，
    并按页偏移小节编号。
    """
    import copy
    try:
        from music21 import converter as m21conv, stream as m21stream

        merged = m21conv.parse(str(page_mxl_files[0]))

        for mxl_path in page_mxl_files[1:]:
            page_score = m21conv.parse(str(mxl_path))
            num_parts = min(len(merged.parts), len(page_score.parts))  # type: ignore[union-attr]
            for part_i in range(num_parts):
                target_part = merged.parts[part_i]  # type: ignore[union-attr]
                page_part = page_score.parts[part_i]  # type: ignore[union-attr]

                existing_measures = list(target_part.getElementsByClass(m21stream.Measure))
                m_offset = max((m.number for m in existing_measures), default=0)

                for measure in page_part.getElementsByClass(m21stream.Measure):
                    new_m = copy.deepcopy(measure)
                    new_m.number = m_offset + measure.number
                    target_part.append(new_m)

        merged.write('musicxml', fp=str(output_path))
        return True
    except Exception as exc:
        log_message(f'[homr] 多页 MusicXML 合并失败: {exc}', logging.WARNING)
        return False


def _run_homr_multipage_pdf(
    source_pdf: Path,
    page_png_paths: list[Path],
    output_dir: Path,
    use_gpu_inference: Optional[bool] = None,
    progress_fn=None,
) -> Optional[Path]:
    """Run Homr inference page by page and merge results for multi-page PDF input.

    模型权重仅下载一次；GPU→CPU 回退后，后续页也切换为 CPU 模式。
    """
    total_pages = len(page_png_paths)
    log_message(f'[homr] 多页 PDF，共 {total_pages} 页，逐页识别…')

    if use_gpu_inference is None:
        use_gpu_inference = _homr_gpu_available()

    _avail_mb = _get_available_memory_mb()
    _batch_size = _compute_segnet_batch_size(_avail_mb)
    if _avail_mb is not None:
        log_message(f'[homr] 可用内存 {_avail_mb} MB，SegNet 批大小: {_batch_size}。')

    # ── 一次性初始化：导入、下载权重 ─────────────────────────────────────────
    source_dir = _ensure_homr_import_path()
    _add_nvidia_cuda_dll_dirs()
    try:
        import homr.main as homr_main  # type: ignore[import-not-found]
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as _TimeoutError
        with ThreadPoolExecutor(max_workers=1) as _dl_ex:
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
    except Exception as exc:
        log_message(f'[homr] homr 模块初始化失败: {exc}')
        if source_dir is not None:
            with _PATH_LOCK:
                if str(source_dir) in sys.path:
                    sys.path.remove(str(source_dir))
        return None

    page_mxl_files: list[Path] = []
    _gpu_mode = use_gpu_inference  # 可能在首次 GPU 失败后切换为 False

    try:
        for page_idx, page_png in enumerate(page_png_paths):
            page_num = page_idx + 1
            log_message(f'[homr] 正在处理第 {page_num}/{total_pages} 页…')

            if progress_fn:
                frac = page_idx / total_pages
                progress_fn(0.05 + frac * 0.55, f'[homr] 正在处理第 {page_num}/{total_pages} 页…')

            page_out_dir = output_dir / f'_page_{page_num}'
            page_out_dir.mkdir(parents=True, exist_ok=True)

            # 预处理
            preprocess_dir = page_out_dir / '_homr_preprocessed'
            log_message(f'[homr] 第 {page_num} 页：正在进行图像预处理…')
            preprocessed = _preprocess_for_homr(page_png, preprocess_dir)
            image_path = preprocessed if preprocessed is not None else page_png

            safe_image_path = page_out_dir / f'homr_input{image_path.suffix.lower()}'
            if not safe_image_path.exists() or safe_image_path.stat().st_size != image_path.stat().st_size:
                shutil.copy2(str(image_path), str(safe_image_path))
            image_path = safe_image_path

            # 第 1 页参考图写入顶层 output_dir（供 editor-workspace 展示）
            if page_num == 1:
                try:
                    shutil.copy2(str(safe_image_path), str(output_dir / '_omr_reference.png'))
                except OSError:
                    pass

            # 进度心跳回调（按页分配进度区间）
            _pn, _tf, _pfn = page_num, total_pages, progress_fn

            def _make_heartbeat(pn, tf, pfn):
                def _on_heartbeat(elapsed: int) -> None:
                    if pfn is None:
                        return
                    base = 0.05 + ((pn - 1) / tf) * 0.55
                    per_page = 0.55 / tf
                    frac = min(elapsed / MAX_HOMR_SECONDS, 1.0)
                    try:
                        pfn(base + frac * per_page, f'[homr] 第 {pn} 页识别中…已耗时 {elapsed}s')
                    except Exception:
                        pass
                return _on_heartbeat

            _heartbeat_fn = _make_heartbeat(_pn, _tf, _pfn)

            def _make_config(gpu):
                return homr_main.ProcessingConfig(
                    enable_debug=False, enable_cache=False,
                    write_staff_positions=False, read_staff_positions=False,
                    selected_staff=-1, use_gpu_inference=gpu,
                    segnet_batch_size=_batch_size,
                )

            xml_args = homr_main.XmlGeneratorArguments(False, None, None)
            mode_label = 'GPU' if _gpu_mode else 'CPU'
            log_message(f'[homr] 第 {page_num} 页：开始识别（{mode_label} 模式）…')

            _captured_image_path = image_path  # 闭包捕获当前页的路径
            _page_ok = False
            try:
                _cfg = _make_config(_gpu_mode)
                _run_with_heartbeat(
                    lambda p=_captured_image_path, c=_cfg: homr_main.process_image(str(p), c, xml_args),
                    label=f'[homr] 第 {page_num}/{total_pages} 页 ',
                    on_heartbeat=_heartbeat_fn,
                )
                _page_ok = True
                log_message(f'[homr] 第 {page_num} 页识别完成。')
            except Exception as exc:
                _exc_str = str(exc)
                _is_gpu_err = _gpu_mode and (
                    'cuda' in _exc_str.lower()
                    or 'CUDAExecutionProvider' in _exc_str
                    or 'DmlExecutionProvider' in _exc_str
                    or 'not active after session creation' in _exc_str
                )
                if _is_gpu_err:
                    log_message(f'[homr] 第 {page_num} 页 GPU 失败，切换为 CPU 模式（后续页同步切换）…')
                    _gpu_mode = False
                    try:
                        _cpu_cfg = _make_config(False)
                        _run_with_heartbeat(
                            lambda p=_captured_image_path, c=_cpu_cfg: homr_main.process_image(str(p), c, xml_args),
                            label=f'[homr] 第 {page_num}/{total_pages} 页(CPU) ',
                            on_heartbeat=_heartbeat_fn,
                        )
                        _page_ok = True
                        log_message(f'[homr] 第 {page_num} 页识别完成（CPU 模式）。')
                    except Exception as cpu_exc:
                        log_message(f'[homr] 第 {page_num} 页 CPU 回退也失败: {cpu_exc}', logging.WARNING)
                else:
                    log_message(f'[homr] 第 {page_num} 页识别失败: {exc}', logging.WARNING)

            if _page_ok:
                mxl = find_first_musicxml_file(page_out_dir, image_path.stem)
                if mxl is not None:
                    page_mxl_files.append(mxl)
                    log_message(f'[homr] 第 {page_num} 页输出: {mxl.name}')
                else:
                    log_message(f'[homr] 第 {page_num} 页未找到 MusicXML。', logging.WARNING)
    finally:
        if source_dir is not None:
            with _PATH_LOCK:
                if str(source_dir) in sys.path:
                    sys.path.remove(str(source_dir))

    if not page_mxl_files:
        log_message('[homr] 所有页面识别均失败。')
        return None

    # ── 合并所有页的 MusicXML ──────────────────────────────────────────────────
    merged_mxl_path = output_dir / f'{source_pdf.stem}.musicxml'
    if len(page_mxl_files) == 1:
        shutil.copy2(str(page_mxl_files[0]), str(merged_mxl_path))
        log_message(f'[homr] 单页结果输出: {merged_mxl_path.name}')
    else:
        log_message(f'[homr] 正在合并 {len(page_mxl_files)} 页 MusicXML…')
        if not _merge_homr_musicxml_pages(page_mxl_files, merged_mxl_path):
            shutil.copy2(str(page_mxl_files[0]), str(merged_mxl_path))
            log_message('[homr] 合并失败，降级使用第 1 页结果。', logging.WARNING)
        else:
            log_message(f'[homr] 合并完成: {merged_mxl_path.name}')

    return output_dir


def run_homr_batch(
    source_image: Path,
    output_dir: Path,
    use_gpu_inference: Optional[bool] = None,
    progress_fn=None,
) -> Optional[Path]:
    """Run homr on a single image file or a multi-page PDF, returning the output directory.

    Homr should receive the whole page after preprocessing, not per-line slices.
    progress_fn: 可选进度回调，签名 progress_fn(value: float 0.0-1.0, message: str)。
    """
    if source_image.suffix.lower() == '.pdf':
        pages_dir = output_dir / '_pdf_pages'
        page_png_paths = _pdf_pages_to_png(source_image, pages_dir, engine_label='homr')
        if not page_png_paths:
            return None
        if len(page_png_paths) > 1:
            return _run_homr_multipage_pdf(
                source_image, page_png_paths, output_dir, use_gpu_inference, progress_fn
            )
        # 单页 PDF：沿用原始单图流程
        image_path = page_png_paths[0]
    else:
        image_path = source_image

    if image_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        log_message(f'[homr] 仅支持图片或 PDF 输入，跳过: {source_image.name}')
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
        import homr.main as homr_main  # type: ignore[import-not-found]
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
