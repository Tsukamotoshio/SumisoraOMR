# core/oemer_runner.py — oemer OMR 引擎封装（experimental-v0.2.0）
#
# oemer 是一款基于深度学习的端到端 OMR 引擎：
#   pip install oemer
#   oemer <img_path> -o <output_dir>  → 输出 <stem>.musicxml
#
# 局限：
#   - 仅支持图片输入（PNG/JPG），不支持 PDF。
#     本模块针对 PDF 输入会先将首页渲染为 PNG，再交给 oemer 处理。
#   - 需要在当前 Python 环境中安装 oemer（不依赖 Java）。
#
import io
import logging
import re
import shutil
import subprocess

# 防止子进程在 Windows 上弹出控制台窗口（GUI 分发版）
_WIN_NO_WINDOW: int = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import List, Optional

from .config import (
    LOGGER,
    MAX_OEMER_SECONDS,
)
from .image_preprocess import (
    OEMER_MAX_PIXELS,
    fit_image_within_pixel_limit,
    preprocess_image_for_oemer,
)
from .utils import (
    find_first_musicxml_file,
    get_app_base_dir,
    log_message,
    safe_remove_file,
)


# ──────────────────────────────────────────────
# oemer 可用性检查
# ──────────────────────────────────────────────

def find_oemer_executable() -> Optional[str]:
    """返回 oemer 命令路径，若未安装则返回 None。

    搜索顺序：
    1. 当前 Python 可执行文件同目录（PyInstaller 分发包中的 oemer.exe）。
    2. 当前 Python 环境的 Scripts/bin 目录（venv 中的 oemer.exe）。
    3. PATH 全局搜索。
    """
    # 路径 1：主程序同目录（分发包场景）
    main_dir = Path(sys.executable).parent
    for candidate in ('oemer', 'oemer.exe'):
        p = main_dir / candidate
        if p.is_file():
            return str(p)

    # 路径 2：venv Scripts/bin 目录（开发环境）
    scripts_dir = main_dir  # 已扫描过，跳过重复搜索
    venv_parent = main_dir.parent  # .venv/
    for scripts_subdir in ('Scripts', 'bin'):
        for candidate in ('oemer', 'oemer.exe'):
            p = venv_parent / scripts_subdir / candidate
            if p.is_file():
                return str(p)

    # 路径 3：PATH 全局查找
    found = shutil.which('oemer')
    return found


def _oemer_api_available() -> bool:
    """检查 oemer Python 包是否可直接导入（进程内调用路径）。"""
    try:
        import oemer.ete  # noqa: F401
        return True
    except ImportError:
        return False


def check_oemer_available() -> bool:
    """若 oemer 可被调用（subprocess 或进程内 API）则返回 True。"""
    if find_oemer_executable() is not None:
        return True
    if _oemer_api_available():
        return True
    log_message(
        'oemer 未安装或不在 PATH 中。\n'
        '请执行以下命令安装：\n'
        '  pip install oemer\n'
        '安装后重新运行程序即可使用 oemer 引擎。',
        logging.ERROR,
    )
    return False


# ──────────────────────────────────────────────
# PDF → 图片（oemer 不支持 PDF，需先转换）
# ──────────────────────────────────────────────

def _pdf_first_page_to_png(pdf_path: Path, output_dir: Path) -> Optional[Path]:
    """将 PDF 首页渲染为 PNG，返回生成的图片路径；失败返回 None。

    优先使用 Pillow + pdf2image（需依赖 poppler），若不可用则尝试 PyMuPDF (fitz)。
    """
    png_path = output_dir / f'{pdf_path.stem}_page1.png'
    output_dir.mkdir(parents=True, exist_ok=True)

    # 方案 A：pdf2image (poppler)
    try:
        from pdf2image import convert_from_path  # type: ignore
        images = convert_from_path(str(pdf_path), first_page=1, last_page=1, dpi=300)
        if images:
            images[0].save(str(png_path), 'PNG')
            log_message(f'[oemer] PDF 首页已转换为图片: {png_path.name}')
            return png_path
    except ImportError:
        pass
    except Exception as exc:
        log_message(f'[oemer] pdf2image 转换失败: {exc}', logging.WARNING)

    # 方案 B：PyMuPDF (fitz)
    try:
        import fitz  # type: ignore  # PyMuPDF
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        mat = fitz.Matrix(300 / 72, 300 / 72)
        pix = page.get_pixmap(matrix=mat)
        pix.save(str(png_path))
        doc.close()
        log_message(f'[oemer] PDF 首页已转换为图片 (PyMuPDF): {png_path.name}')
        return png_path
    except ImportError:
        pass
    except Exception as exc:
        log_message(f'[oemer] PyMuPDF 转换失败: {exc}', logging.WARNING)

    log_message(
        '[oemer] PDF 转图片失败：请安装 pdf2image（需 poppler）或 PyMuPDF：\n'
        '  pip install pdf2image   # 还需安装 poppler-utils\n'
        '  pip install pymupdf',
        logging.ERROR,
    )
    return None


# ──────────────────────────────────────────────
# 进程内 oemer API 调用（PyInstaller 分发包备用路径）
# ──────────────────────────────────────────────

def _run_oemer_inprocess(image_path: Path, output_dir: Path) -> Optional[Path]:
    """直接调用 oemer Python API（进程内），带以下关键增强：

    1. ONNX 会话缓存：首次调用后，后续文件直接复用已加载的模型（毫秒级返回），
       彻底消除每文件 3-5 分钟的重复初始化开销。
    2. 非 ASCII 路径兼容：自动将含中文/特殊字符的文件名复制为 ASCII 安全路径，
       避免 OpenCV cv2.imread 在 Windows 上无法读取非 ASCII 路径的问题。
    3. 噪声抑制：suppressing sklearn/onnxruntime 警告，保持日志清洁。
    """
    import warnings
    try:
        from argparse import Namespace
        import oemer.ete as _oemer_ete
    except ImportError as exc:
        log_message(f'[oemer] 无法导入 oemer 包: {exc}', logging.ERROR)
        return None

    # ── 首次调用时完成 ONNX Runtime 提供程序初始化（后续调用为 no-op）──────────
    _patch_ort_session_caching()
    # ── 打 build_system 容错补丁（防止 track 越界 IndexError）────────────────
    _patch_oemer_build_system_resilience()
    # ── 打 bbox.merge_nearby_bbox 容错补丁（防止空列表导致 IndexError）────────
    _patch_oemer_bbox_merge()

    # ── 将非 ASCII 路径重命名为 ASCII 安全路径（避免 OpenCV 崩溃）──────────────
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_image_path = _ensure_ascii_path(image_path, output_dir)

    # ── 清理上一张图片残留的 oemer 层数据（ONNX 会话不在 layers 中，不受影响）──
    try:
        _oemer_ete.clear_data()
    except Exception:
        pass

    # without_deskew=True：禁用 oemer 内置反畸变步骤。
    # 依据 oemer 官方 README（issue #9）：遇到识别报错时首先尝试 --without-deskew。
    # 本工具输入的扫描件/PDF 已基本水平对齐，deskew 步骤在复杂版面下易触发
    # IndexError / AssertionError，禁用后可显著提升图片格式的识别成功率。
    args = Namespace(
        img_path=str(safe_image_path),
        output_path=str(output_dir),
        use_tf=False,
        save_cache=False,
        without_deskew=True,
    )

    log_message(f'[oemer] 进程内 API 调用: {image_path.name}')
    buf = io.StringIO()
    try:
        # 抑制 sklearn InconsistentVersionWarning 等无关警告
        with warnings.catch_warnings(), redirect_stdout(buf):
            warnings.simplefilter('ignore')
            out_path_str = _oemer_ete.extract(args)
    except (AssertionError, IndexError) as exc:
        # build_system.py 对非 1/2 轨道谱面有 assert；
        # IndexError 通常为谱面结构解析异常，记录后尝试定位已生成的 MusicXML。
        import traceback as _tb
        log_message(
            f'[oemer] 识别时遇到结构异常（可能是多轨道或版式过于复杂的谱面）: {exc}',
            logging.WARNING,
        )
        log_message(
            f'[oemer] 详细错误位置（供调试）:\n{_tb.format_exc()}',
            logging.WARNING,
        )
        out_path_str = None
    except Exception as exc:
        log_message(f'[oemer] 进程内识别失败: {exc}', logging.ERROR)
        out_path_str = None
    finally:
        # 将 oemer 的 print 进度写入 DEBUG 日志（不污染 INFO 级别）
        for line in buf.getvalue().splitlines():
            stripped = line.strip()
            if stripped and not any(n in stripped for n in _ONNX_CUDA_NOISE):
                log_message(f'[oemer] {stripped}', logging.DEBUG)
        # 清理本次图片的层数据（ONNX 会话已随识别完成释放，不保留跨文件缓存）
        try:
            _oemer_ete.clear_data()
        except Exception:
            pass

    # ── 定位输出的 MusicXML 文件 ────────────────────────────────────────────
    # oemer 以 safe_image_path 的 stem 命名输出文件，需用 stem 搜索
    search_stem = safe_image_path.stem

    if out_path_str and Path(out_path_str).exists():
        mxl = Path(out_path_str)
        log_message(f'[oemer] 输出 MusicXML: {mxl.name}')
        return mxl

    mxl = find_first_musicxml_file(output_dir, search_stem)
    if mxl is None:
        fallback = output_dir / 'result.musicxml'
        if fallback.exists():
            mxl = fallback
    if mxl is None:
        log_message('[oemer] 识别完毕，但未找到输出的 MusicXML 文件。', logging.ERROR)
        return None

    log_message(f'[oemer] 输出 MusicXML: {mxl.name}')
    return mxl


# ──────────────────────────────────────────────
# GPU / ONNX Runtime 提供程序检测与会话缓存
# ──────────────────────────────────────────────

# 用于剥离 ANSI/VT100 转义序列（oemer/onnxruntime 使用带颜色的终端输出）
_ANSI_ESCAPE = re.compile(r'\x1b(?:\[[0-9;]*[a-zA-Z]|\][^\x07]*(?:\x07|\x1b\\))')

# 用于过滤 onnxruntime CUDA 噪声与 sklearn 警告行（这些信息对普通用户无意义）
_ONNX_CUDA_NOISE = (
    'onnxruntime_providers_cuda',
    'CUDAExecutionProvider',
    'cudnn64_',
    'provider_bridge_ort',
    'onnxruntime_pybind',
    'TryGetProviderInfo',
    'CreateExecutionProviderFactory',
    'onnxruntime_providers_shared',
    'InconsistentVersionWarning',
    'sklearn',
    'RuntimeWarning',
    'warnings.warn(',
)

# oemer 输出中代表处理阶段的关键词，用于 spinner 步骤显示
_OEMER_STEP_HINTS = [
    ('Extracting staffline', '提取谱线与符头'),
    ('Parsing rhythm',       '解析节奏'),
    ('Generating MusicXML',  '生成 MusicXML'),
    ('Building system',      '构建乐谱系统'),
    ('Inferenc',             '深度学习推理中'),
    ('Loading model',        '加载模型'),
    ('OMR extracted',        '识别完成'),
]


def _cuda_cudnn_available() -> bool:
    """检查 cuDNN 动态库是否在系统中可用（CUDA EP 的必要依赖）。"""
    import ctypes
    for dll_name in ('cudnn64_9.dll', 'cudnn64_8.dll', 'cudnn_ops_infer64_8.dll'):
        try:
            ctypes.CDLL(dll_name)
            return True
        except OSError:
            pass
    return False


def _pick_ort_providers() -> list:
    """返回实际可用的 ONNX Runtime 执行提供程序列表。

    优先级: DirectML > CUDA（cuDNN 可用时）> CPU。
    跳过无法使用的提供程序，避免漫长的探测超时。
    """
    try:
        import onnxruntime as rt
        available = rt.get_available_providers()
        if 'DmlExecutionProvider' in available:
            # disable_metacommands=true：禁止 DirectML MetaCommands（实验性高性能 GPU 算子）。
            # 在部分 NVIDIA 驱动版本下，MetaCommands 会触发 GPU TDR（超时检测与恢复失败），
            # 导致驱动崩溃甚至系统蓝屏。禁用后退回着色器路径，稳定性显著提升。
            dml_options = {
                'device_id': '0',
                'disable_metacommands': 'true',
            }
            return [('DmlExecutionProvider', dml_options), 'CPUExecutionProvider']
        if 'CUDAExecutionProvider' in available and _cuda_cudnn_available():
            return [('CUDAExecutionProvider', {'device_id': 0}), 'CPUExecutionProvider']
    except Exception:
        pass
    return ['CPUExecutionProvider']


def _detect_gpu_provider() -> str:
    """检测 oemer 实际可用的计算设备（基于 cuDNN 可用性，而非仅注册状态）。"""
    try:
        import onnxruntime as ort
        available = ort.get_available_providers()
        if 'DmlExecutionProvider' in available:
            return 'DirectML GPU 加速（支持 NVIDIA / AMD / Intel 显卡）'
        if 'CUDAExecutionProvider' in available or 'TensorrtExecutionProvider' in available:
            if _cuda_cudnn_available():
                return 'CUDA (NVIDIA 独立显卡)'
            return 'CPU（NVIDIA 驱动已就绪，但缺少 cuDNN 9.x）'
        return 'CPU（无 GPU 加速）'
    except ImportError:
        return '未知（onnxruntime 未安装）'


# ── ONNX Runtime 执行提供程序初始化（仅执行一次，每次识别均完整重新加载模型）──────────────
# 注意：不复用 InferenceSession；每次识别都创建全新会话以避免跨文件状态污染。
# Monkey-patch 的目的仅限于：
#   1. 将 oemer 硬编码的 CUDA provider 替换为实际可用的 provider；
#   2. 注入稳定性优先的 SessionOptions（禁止内存预分配、顺序执行）。

_ORT_PATCHED: bool = False      # 是否已完成 provider 初始化 patch


def _patch_ort_session_caching() -> None:
    """初始化 ONNX Runtime 执行提供程序并注入稳定性选项（进程生命期内一次性操作）。

    保留函数名以避免修改外部调用点。每次进程内识别均创建全新会话（无缓存），
    确保文件间模型状态完全隔离。
    """
    global _ORT_PATCHED
    if _ORT_PATCHED:
        return
    try:
        import onnxruntime as rt
        _real_cls = rt.InferenceSession
        _preferred = _pick_ort_providers()
        gpu_label = (
            _preferred[0] if isinstance(_preferred[0], str)
            else _preferred[0][0]
        )
        log_message(f'[oemer] ONNX Runtime 执行提供程序: {gpu_label}')
        if gpu_label == 'CPUExecutionProvider':
            log_message(
                '[oemer] 提示：未检测到可用 GPU 加速（cuDNN 9.x 缺失）。\n'
                '         如需 GPU 加速：\n'
                '           · NVIDIA：安装 CUDA 12.x + cuDNN 9.x\n'
                '           · 任意 GPU (AMD/Intel/NVIDIA)：pip install onnxruntime-directml',
                logging.WARNING,
            )

        def _fresh_session(model_path_or_bytes, sess_options=None, providers=None, **kwargs):
            """Session factory — creates a fresh InferenceSession each call (no caching)."""
            log_message(f'[oemer] 加载 ONNX 模型: {Path(str(model_path_or_bytes)).name}')
            # 构建稳定性优先的会话选项，防止 GPU 过载导致系统冻结或蓝屏：
            #   enable_mem_pattern=False  — 禁止 DML/CUDA 按历史模式预分配 GPU 内存，
            #                              避免初始化时一次性耗尽显存。
            #   execution_mode=SEQUENTIAL — 顺序执行图中算子，降低瞬时 GPU 计算峰值。
            stable_opts = rt.SessionOptions()
            stable_opts.enable_mem_pattern = False
            stable_opts.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL
            return _real_cls(model_path_or_bytes, sess_options=stable_opts,
                             providers=_preferred)

        rt.InferenceSession = _fresh_session
        _ORT_PATCHED = True
        log_message('[oemer] ONNX Runtime 提供程序已初始化（每次识别均完整重新加载模型）')
    except Exception as exc:
        log_message(f'[oemer] ONNX Runtime 初始化失败，将使用默认设置: {exc}', logging.WARNING)


# ── oemer build_system 容错补丁（防止 track 越界 IndexError）─────────────────────────────
# 根因：oemer 的 further_infer_track_nums() 有时低估轨道数（如把 2 轨道谱弄成 1），
# 导致 sfn/notehead 被分配到比 track_nums 更高的 track 索引，进而在 build_system.py 中
# 触发 "list index out of range"（sfn_counts[sfn.track]、track_dura[sym.track] 等处）。
# 补丁对三个最易崩溃的方法做最小化包装，保证 build 流程可以走完并生成 MusicXML。

_Oemer_Build_Patched: bool = False


def _patch_oemer_build_system_resilience() -> None:
    """Monkey-patch oemer build_system 中三个易崩方法（幂等，全程序生命期仅执行一次）。

    Patch 1 — Measure.get_key(): sfn_counts[sfn.track] 越界 → 回退到 C 大调（Key(0)）。
    Patch 2 — Measure.align_symbols(): track_dura[sym.track] 越界 → 为每个符号分配独立
               time slot，slot_duras 列数设为 4（足以覆盖任何合理的 track 索引）。
    Patch 3 — AddNote.perform(): clefs[note.track] 越界 → 跳过该音符（返回 None）。
    """
    global _Oemer_Build_Patched
    if _Oemer_Build_Patched:
        return
    try:
        import numpy as np
        import oemer.build_system as _bs

        # Patch 1: Measure.get_key
        _orig_get_key = _bs.Measure.get_key

        def _safe_get_key(self):
            try:
                return _orig_get_key(self)
            except (IndexError, TypeError, AttributeError) as exc:
                log_message(
                    f'[oemer] get_key track 越界（已容错，使用 C 大调）: {exc}',
                    logging.DEBUG,
                )
                return _bs.Key(0)

        _bs.Measure.get_key = _safe_get_key

        # Patch 2: Measure.align_symbols
        _orig_align = _bs.Measure.align_symbols

        def _safe_align_symbols(self):
            try:
                return _orig_align(self)
            except (IndexError, AssertionError, ValueError, TypeError) as exc:
                log_message(
                    f'[oemer] align_symbols 异常（已容错）: {exc}',
                    logging.DEBUG,
                )
                # 为每个非注释符号分配独立 time slot；
                # slot_duras 使用 4 列确保任意 track 索引都可安全访问。
                _NON_ANN = (_bs.Clef, _bs.Sfn)
                symbols = [s for s in self.symbols if not isinstance(s, _NON_ANN)]
                self.time_slots = [[s] for s in symbols] if symbols else [[]]
                self.slot_duras = np.zeros(
                    (max(1, len(self.time_slots)), 4), dtype=np.uint16
                )
                return None

        _bs.Measure.align_symbols = _safe_align_symbols

        # Patch 3: AddNote.perform
        _orig_add_note = _bs.AddNote.perform

        def _safe_add_note(self, parent_elem=None):
            try:
                return _orig_add_note(self, parent_elem)
            except (IndexError, TypeError, AttributeError) as exc:
                log_message(
                    f'[oemer] AddNote.perform track 越界（已跳过该音符）: {exc}',
                    logging.DEBUG,
                )
                return None

        _bs.AddNote.perform = _safe_add_note

        _Oemer_Build_Patched = True
        log_message('[oemer] build_system 容错补丁已应用（防止 track 越界崩溃）', logging.DEBUG)
    except Exception as exc:
        log_message(
            f'[oemer] build_system 容错补丁应用失败（不影响正常运行）: {exc}',
            logging.DEBUG,
        )


# ── oemer bbox.merge_nearby_bbox 容错补丁（防止空/单元素列表 IndexError）──────────────────

_Oemer_Bbox_Patched: bool = False


def _patch_oemer_bbox_merge() -> None:
    """Monkey-patch oemer.bbox.merge_nearby_bbox 以防止空列表导致的 IndexError。

    根因：
        merge_nearby_bbox 中 ``centers = np.array([...]) / 2`` 当 bboxes 为空列表时
        会产生形状 (0,) 的一维数组，后续 ``centers[:, 0]`` 二维索引操作触发
        IndexError: too many indices for array。
        常见于 parse_clefs_keys 调用时谱面无调号符号（如 C 大调）。

    补丁策略：
        当 bboxes 长度 < 2 时直接返回原列表（0 或 1 个元素无需合并）。
    """
    global _Oemer_Bbox_Patched
    if _Oemer_Bbox_Patched:
        return
    try:
        import oemer.bbox as _bbox
        _orig_merge = _bbox.merge_nearby_bbox

        def _safe_merge_nearby_bbox(bboxes, distance, x_factor=1, y_factor=1):
            if len(bboxes) < 2:
                return list(bboxes)  # 0 或 1 个元素无需聚类合并
            return _orig_merge(bboxes, distance, x_factor=x_factor, y_factor=y_factor)

        _bbox.merge_nearby_bbox = _safe_merge_nearby_bbox
        # 同步更新已导入此函数的 symbol_extraction 模块引用
        try:
            import oemer.symbol_extraction as _se
            if hasattr(_se, 'merge_nearby_bbox'):
                _se.merge_nearby_bbox = _safe_merge_nearby_bbox
        except Exception:
            pass
        _Oemer_Bbox_Patched = True
        log_message('[oemer] bbox.merge_nearby_bbox 容错补丁已应用（防止空列表 IndexError）', logging.DEBUG)
    except Exception as exc:
        log_message(
            f'[oemer] bbox 补丁应用失败（不影响正常运行）: {exc}',
            logging.DEBUG,
        )


def _ensure_ascii_path(img_path: Path, temp_dir: Path) -> Path:
    """若完整路径（含父目录）含非 ASCII 字符，则复制到 ASCII 安全路径后返回。

    oemer 内部使用 OpenCV cv2.imread()，在 Windows 上无法读取完整路径中含非 ASCII
    字符（如中文目录名）的文件，会返回 None 并导致 AttributeError 崩溃。
    本函数检查完整绝对路径（不只是文件名），必要时将文件复制到系统 TEMP 目录
    （Windows 上 %TEMP% 路径永远是 ASCII）。
    """
    try:
        str(img_path.resolve()).encode('ascii')
        return img_path  # 完整路径全 ASCII，无需复制
    except UnicodeEncodeError:
        pass
    import hashlib
    name_hash = hashlib.sha1(str(img_path.resolve()).encode('utf-8')).hexdigest()[:8]
    safe_name = f'oemer_in_{name_hash}{img_path.suffix.lower()}'
    # 若 temp_dir 本身含非 ASCII 路径（如父目录有中文），回退到系统 TEMP 目录
    try:
        str(temp_dir.resolve()).encode('ascii')
        safe_dir = temp_dir
    except UnicodeEncodeError:
        safe_dir = Path(tempfile.gettempdir())
    safe_path = safe_dir / safe_name
    try:
        safe_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(img_path), str(safe_path))
        log_message(
            f'[oemer] 路径含非 ASCII 字符，临时复制到 ASCII 安全位置以支持 OpenCV 读取：'
            f'... → {safe_path}'
        )
        return safe_path
    except Exception as exc:
        log_message(f'[oemer] 临时复制失败，继续尝试原始路径: {exc}', logging.WARNING)
        return img_path


def _run_oemer_subprocess(cmd: List[str], timeout_seconds: int):
    """运行 oemer 子进程，显示 spinner 动画，完成后写入精简日志。

    将 oemer 的 stdout / stderr 重定向到临时文件，主线程同时展示
    带进度步骤的 spinner，避免 ANSI 转义码与 onnxruntime 噪声污染
    控制台输出。

    Returns:
        (returncode, had_error):
            returncode — 进程退出码；None 表示超时被杀。
            had_error  — 输出中是否出现过非警告级错误。
    """
    stdout_f = tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='replace')
    stderr_f = tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='replace')
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=stdout_f,
            stderr=stderr_f,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=_WIN_NO_WINDOW,
        )
    except Exception as exc:
        stdout_f.close()
        stderr_f.close()
        log_message(f'[oemer] 启动子进程失败: {exc}', logging.ERROR)
        return 1, True

    spinner = ['|', '/', '-', '\\']
    start_time = time.time()
    current_step = '初始化...'
    had_error = False

    try:
        while proc.poll() is None:
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                proc.kill()
                proc.wait(timeout=5)
                if sys.stdout: sys.stdout.write('\r' + ' ' * 72 + '\r'); sys.stdout.flush()
                return None, had_error

            # 从临时文件中实时读取 stdout 以更新步骤标签
            stdout_f.seek(0)
            so_far = stdout_f.read()
            for line in so_far.splitlines():
                line_c = _ANSI_ESCAPE.sub('', line)
                for kw, label in _OEMER_STEP_HINTS:
                    if kw in line_c:
                        current_step = label
                        break

            idx = int(elapsed) % len(spinner)
            if sys.stdout:
                sys.stdout.write(
                    f'\r{spinner[idx]} oemer 正在识别... {int(elapsed)}s  [{current_step}]   '
                )
                sys.stdout.flush()
            time.sleep(0.25)
    except KeyboardInterrupt:
        proc.kill()
        proc.wait(timeout=5)
        if sys.stdout: sys.stdout.write('\r' + ' ' * 72 + '\r'); sys.stdout.flush()
        return None, had_error
    finally:
        if sys.stdout: sys.stdout.write('\r' + ' ' * 72 + '\r'); sys.stdout.flush()

    returncode = proc.returncode

    # 处理完成后，扫描完整输出并精简写入日志
    stdout_f.seek(0)
    stderr_f.seek(0)
    combined = stdout_f.read() + '\n' + stderr_f.read()
    stdout_f.close()
    stderr_f.close()

    for raw_line in combined.splitlines():
        line = _ANSI_ESCAPE.sub('', raw_line).strip()
        if not line:
            continue
        # 过滤 onnxruntime/CUDA 噪声行
        if any(noise in line for noise in _ONNX_CUDA_NOISE):
            continue
        # 过滤乱码行（无任何字母数字字符）
        if not any(c.isalnum() for c in line):
            continue
        # 按级别写入日志
        if 'error' in line.lower() or 'traceback' in line.lower():
            had_error = True
            log_message(f'[oemer] {line}', logging.ERROR)
        elif 'warning' in line.lower() or 'warn' in line.lower():
            log_message(f'[oemer] {line}', logging.WARNING)
        else:
            log_message(f'[oemer] {line}', logging.DEBUG)

    return returncode, had_error


def run_oemer(image_path: Path, output_dir: Path) -> Optional[Path]:
    """对单张图片调用 oemer，返回生成的 MusicXML 文件路径；失败返回 None。

    调用策略（优先进程内 API 以实现跨文件会话缓存）：
    1. 若 oemer Python 包可导入 → 进程内 API（ONNX 会话缓存，相同批次内模型只加载一次）。
    2. 若仅有 oemer.exe（PyInstaller 或独立安装）→ 子进程调用。

    oemer 输出约定：命令行传入 ``-o <output_dir>``，oemer 在该目录下生成
    ``<image_stem>.musicxml``（部分版本为 ``result.musicxml``）。
    """
    # ── 路径 1：进程内 API（ONNX 会话跨文件缓存，批量处理时 2-4 文件后即零等待）
    if _oemer_api_available():
        return _run_oemer_inprocess(image_path, output_dir)

    # ── 路径 2：subprocess（进程隔离，但每文件重新初始化）────────────────────
    exe = find_oemer_executable()
    if exe is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        gpu_info = _detect_gpu_provider()
        log_message(f'[oemer] 计算设备：{gpu_info}')
        # 非 ASCII 路径在 subprocess 中同样会导致 OpenCV 崩溃
        safe_image_path = _ensure_ascii_path(image_path, output_dir)
        cmd = [exe, str(safe_image_path), '-o', str(output_dir)]
        log_message(f'[oemer] 调用 subprocess: {" ".join(cmd)}')

        returncode, had_error = _run_oemer_subprocess(cmd, MAX_OEMER_SECONDS)
        if returncode is None:
            log_message(f'[oemer] 超时（>{MAX_OEMER_SECONDS}s），识别已中断。', logging.ERROR)
            return None
        if returncode != 0:
            log_message(f'[oemer] 退出码 {returncode}，识别失败。', logging.ERROR)
            return None

        search_stem = safe_image_path.stem
        mxl = find_first_musicxml_file(output_dir, search_stem)
        if mxl is None:
            fallback = output_dir / 'result.musicxml'
            if fallback.exists():
                mxl = fallback
        if mxl is None:
            log_message('[oemer] 识别完毕，但未找到输出的 MusicXML 文件。', logging.ERROR)
            return None

        log_message(f'[oemer] 输出 MusicXML: {mxl.name}')
        return mxl

    log_message('[oemer] 未找到 oemer 可执行文件，且无法导入 oemer 包。', logging.ERROR)
    return None


def run_oemer_batch(input_path: Path, output_dir: Optional[Path] = None) -> Optional[Path]:
    """oemer 批处理入口，接口与 ``run_audiveris_batch`` 对齐。

    - 若输入为 PDF，自动将首页转换为 PNG 再送入 oemer。
    - 返回包含 MusicXML 的目录（与 Audiveris 接口对齐），失败返回 None。

    注意：oemer 目前每次只处理单张图片；多页 PDF 仅处理第 1 页。
    后续版本可扩展为逐页处理并合并结果。
    """
    input_path = input_path.resolve()
    if output_dir is None:
        output_dir = get_app_base_dir() / 'oemer-output'
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # oemer 仅接受图片：若为 PDF，先转换首页
    if input_path.suffix.lower() == '.pdf':
        log_message('[oemer] 检测到 PDF 输入，正在将首页转换为图片…')
        img_path = _pdf_first_page_to_png(input_path, output_dir)
        if img_path is None:
            return None
    else:
        img_path = input_path

    # ── 图像预处理（oemer 专用预设：内容裁剪 + 梯度修正 + 低分辨率 SR 放大）─────────────────
    # 使用 preprocess_image_for_oemer：
    #   · 保留 RGB 彩色（oemer 深度学习模型利用颜色信息，不做灰度转换）
    #   · 短边 < 1200px 时自动调用 waifu2x 2× 超分辨率，防止模型只检出首行谱线
    #   · 不做去噪/锐化，减少引入影响深度学习模型感知的伪影
    omr_input_path = img_path
    omr_preprocessed_path: Optional[Path] = None
    if img_path.suffix.lower() in {'.png', '.jpg', '.jpeg'}:
        preprocessed = preprocess_image_for_oemer(
            img_path, output_dir, max_pixels=OEMER_MAX_PIXELS
        )
        if preprocessed is not None:
            omr_preprocessed_path = preprocessed
            omr_input_path = preprocessed
        else:
            # 预处理失败时仍执行像素上限检查
            rescaled = fit_image_within_pixel_limit(img_path, output_dir, max_pixels=OEMER_MAX_PIXELS)
            if rescaled is not None:
                omr_preprocessed_path = rescaled
                omr_input_path = rescaled

    # 将预处理后的图像保存为编辑器参考图（pipeline 通过 _preprocessed_ref.png 定位）
    ref_dest = output_dir / '_preprocessed_ref.png'
    try:
        shutil.copy2(str(omr_input_path), str(ref_dest))
    except OSError:
        pass

    mxl_file = run_oemer(omr_input_path, output_dir)

    # ── 若预处理后仍失败，以原始图像重试（容错兜底）──────────────────────────
    # 某些谱面经预处理后图像特征变化导致 oemer 轨道/谱线检测出错；
    # 回退到原始图像（仅做像素上限缩放）有时可以识别成功。
    if mxl_file is None and omr_input_path != img_path:
        log_message('[oemer] 预处理图像识别失败，正在用原始图像重试…', logging.WARNING)
        # 对原始图像仅做像素上限控制，不做其他增强
        raw_input = img_path
        raw_rescaled: Optional[Path] = None
        rescaled = fit_image_within_pixel_limit(img_path, output_dir, max_pixels=OEMER_MAX_PIXELS)
        if rescaled is not None:
            raw_input = rescaled
            raw_rescaled = rescaled
        mxl_file = run_oemer(raw_input, output_dir)
        # 清理降采样副本
        if raw_rescaled is not None:
            safe_remove_file(raw_rescaled)

    # 清理仅用于传给 oemer 的临时预处理文件（_preprocessed_ref.png 已另存，不受影响）
    if omr_preprocessed_path is not None and omr_preprocessed_path != ref_dest:
        safe_remove_file(omr_preprocessed_path)

    if mxl_file is None:
        return None

    # 返回包含 MusicXML 的目录（与 run_audiveris_batch 返回值对齐）
    return mxl_file.parent
