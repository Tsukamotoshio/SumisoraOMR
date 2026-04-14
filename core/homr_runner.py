# core/homr_runner.py — homr OMR 引擎封装
# 本模块将本地 homr 仓库目录作为可选 OMR 引擎，支持图片输入和 PDF 首页。

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from .config import HOMR_SOURCE_DIR_NAME, OMR_ENGINE_DIR_NAME
from .image_preprocess import preprocess_geometry_for_omr
from .oemer_runner import _pdf_first_page_to_png
from .utils import find_first_musicxml_file, get_app_base_dir, log_message


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


def find_homr_source_dir() -> Optional[Path]:
    """Find the local homr source directory under the app root or omr_engine.

    When frozen by PyInstaller the homr repo tree is bundled at
    _MEIPASS/omr_engine/homr (collected via collect_tree).  This directory
    must be added to sys.path so that ``import homr.main`` resolves to the
    ``homr/`` sub-package inside it.
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
        if str(source_dir) not in sys.path:
            sys.path.insert(0, str(source_dir))
            added = True
        else:
            added = False
        try:
            import homr.main  # noqa: F401
            return True
        except Exception:
            return False
        finally:
            if added and str(source_dir) in sys.path:
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


def run_homr_batch(
    source_image: Path,
    output_dir: Path,
    use_gpu_inference: Optional[bool] = None,
) -> Optional[Path]:
    """Run homr on a single image file or a PDF's first page, returning the output directory.

    Homr should receive the whole page after preprocessing, not per-line slices.
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

    _log_homr_gpu_mode()

    preprocess_dir = output_dir / '_homr_preprocessed'
    preprocessed_image = preprocess_geometry_for_omr(image_path, preprocess_dir)
    if preprocessed_image is not None:
        log_message(f'[homr] 已完成整张图像几何预处理: {preprocessed_image.name}')
        image_path = preprocessed_image

    safe_image_path = output_dir / f'homr_input{image_path.suffix.lower()}'
    if not safe_image_path.exists() or safe_image_path.stat().st_size != image_path.stat().st_size:
        shutil.copy2(str(image_path), str(safe_image_path))
    image_path = safe_image_path

    source_dir = _ensure_homr_import_path()
    _add_nvidia_cuda_dll_dirs()
    try:
        import homr.main as homr_main
        # download_weights 需要联网，在弱网/限速环境下可能长时间挂起，限制为 120s
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as _TimeoutError
        with ThreadPoolExecutor(max_workers=1) as _dl_ex:
            _dl_future = _dl_ex.submit(homr_main.download_weights, use_gpu_inference)
            try:
                _dl_future.result(timeout=120)
            except _TimeoutError:
                log_message('[homr] 模型权重下载超时（>120s），请检查网络后重试。', )
                return None
            except Exception as _dl_exc:
                log_message(f'[homr] 模型权重下载失败: {_dl_exc}', )
                return None
        config = homr_main.ProcessingConfig(
            enable_debug=False,
            enable_cache=False,
            write_staff_positions=False,
            read_staff_positions=False,
            selected_staff=-1,
            use_gpu_inference=use_gpu_inference,
        )
        xml_args = homr_main.XmlGeneratorArguments(False, None, None)
        log_message('[homr] 开始神经网络推理，请稍候（可能需要数分钟，切换窗口后仍在后台运行）…')
        homr_main.process_image(str(image_path), config, xml_args)
        log_message('[homr] 推理完成，正在整理输出…')
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
            log_message(f'[homr] GPU 推理失败（{exc}），回退到 CPU 重试…')
            try:
                _cpu_config = homr_main.ProcessingConfig(
                    enable_debug=False,
                    enable_cache=False,
                    write_staff_positions=False,
                    read_staff_positions=False,
                    selected_staff=-1,
                    use_gpu_inference=False,
                )
                log_message('[homr] CPU 推理中，请稍候…')
                homr_main.process_image(str(image_path), _cpu_config, xml_args)
                log_message('[homr] CPU 推理完成，正在整理输出…')
            except Exception as _cpu_exc:
                log_message(f'[homr] CPU 回退也失败: {_cpu_exc}')
                return None
        else:
            log_message(f'[homr] 识别失败: {exc}')
            return None
    finally:
        if source_dir is not None and str(source_dir) in sys.path:
            sys.path.remove(str(source_dir))

    mxl = find_first_musicxml_file(output_dir, image_path.stem)
    if mxl is None:
        log_message(f'[homr] 未找到输出 MusicXML，可能识别失败。', )
        return None
    return output_dir
