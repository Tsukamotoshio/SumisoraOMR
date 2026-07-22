# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all
import certifi
import os


def collect_tree(source, prefix):
    """Recursively collect files from a directory tree into PyInstaller datas."""
    for root, _, files in os.walk(source):
        for file in files:
            src = os.path.join(root, file)
            rel_root = os.path.relpath(root, source)
            dest = prefix if rel_root == '.' else os.path.join(prefix, rel_root).replace('\\', '/')
            datas.append((src, dest))


datas = []
binaries = []
hiddenimports = []

# ── SSL 证书：解决部分精简版系统或受限网络下 [SSL: CERTIFICATE_VERIFY_FAILED] 错误
datas += [(certifi.where(), 'certifi')]

# ── 核心依赖 ──────────────────────────────────────────────────────────────────
hiddenimports += collect_submodules('reportlab')
hiddenimports += collect_submodules('core')
tmp_ret = collect_all('music21')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('rich')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── ONNX Runtime（Homr 推理引擎 —— DLL + 数据文件）────────────────────────
# 仅保留 CPU/DirectML 推理所需文件，排除所有 CUDA/TensorRT 相关 DLL 以减小体积。
# 注意：不要把 `directml` 加进排除表——DirectML 分发（onnxruntime-directml）
# 依赖 DirectML.dll，误删会使 DmlExecutionProvider 失效，homr 退回 CPU。
import re as _re
_cuda_pat = _re.compile(
    r'(?:cublas|cufft|cudart|cudnn|nvrtc|nvblas|curand|cusparseLt|'
    r'onnxruntime_providers_cuda|onnxruntime_providers_tensorrt|'
    r'nvinfer|nvonnxparser)',
    _re.IGNORECASE
)
def _is_cuda_item(item):
    src = item[0]
    name = os.path.basename(src)
    if _cuda_pat.search(name):
        return True
    # 排除 nvidia CUDA 运行时包目录下的所有 DLL
    if (os.sep + 'nvidia' + os.sep) in src and src.lower().endswith('.dll'):
        return True
    return False
tmp_ret = collect_all('onnxruntime')
onnx_datas, onnx_binaries, onnx_hidden = tmp_ret
onnx_datas    = [item for item in onnx_datas    if not _is_cuda_item(item)]
onnx_binaries = [item for item in onnx_binaries if not _is_cuda_item(item)]
datas += onnx_datas; binaries += onnx_binaries; hiddenimports += onnx_hidden

# ── PyMuPDF（PDF → 图片转换，图片输入预处理使用）────────────────────────
tmp_ret = collect_all('fitz')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── Pillow（PIL，图像处理 / 二值化 / 质量评分）────────────────────────────
# core/image/ 的多处 try/except ImportError 块导入 PIL；
# Windows 上 Pillow 含额外 DLL（_imaging.pyd / pillow.libs/）必须显式收集，
# 否则打包版运行时图像处理功能会静默失败。
tmp_ret = collect_all('PIL')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── NumPy（Pillow / OpenCV / ONNX 的底层数值库）──────────────────────────
# 虽然 collect_all('cv2') 会间接触发 numpy hook，但显式声明更可靠。
tmp_ret = collect_all('numpy')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── homr 本地仓库、ONNX 模型权重和运行时代码（本项目扩展 OMR 引擎）─────────────────────
# 只打包 omr_engine/homr/homr（Python 包本身），跳过仓库根目录下的
# .git、training、tests、validation、figures、docs 等开发用目录和配置文件。
if os.path.isdir(r'omr_engine\homr'):
    _homr_pkg_dir = os.path.join(r'omr_engine\homr', 'homr')
    if os.path.isdir(_homr_pkg_dir):
        collect_tree(_homr_pkg_dir, 'omr_engine/homr/homr')
    else:
        # 回退：仓库结构不符合预期时打包整个仓库根目录
        collect_tree(r'omr_engine\homr', 'omr_engine/homr')
    # HOMR Python package is bundled; .onnx weights are intentionally excluded
    # (see the _is_homr_onnx filter below) and downloaded on demand at runtime
    # by core.omr.homr_downloader into <app_base_dir>/models/.
    # homr runtime deps: rapidocr (with its own ONNX models) and musicxml are
    # not found by PyInstaller static analysis because homr is collected as data,
    # so they must be declared explicitly here.
    tmp_ret = collect_all('rapidocr')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    tmp_ret = collect_all('musicxml')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── OpenCV（图像处理依赖）────────────────────────────────────────────
tmp_ret = collect_all('cv2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── 音频识别引擎（core/omr/audio_runner.py，ByteDance piano_transcription_inference）──
# torch / librosa 生态里多个包只在运行时才惰性导入（librosa 用 lazy_loader 延迟加载
# 子模块；实测跑一遍完整音频转录，sys.modules 里才会真正出现 numba / scipy /
# soundfile / soxr / resampy —— 仅 import 顶层包不会触发）。这类导入对 PyInstaller
# 的静态扫描不可见，必须显式列出才能让它发现并触发各自的收集 hook。
#   - torch / librosa / soundfile / numba / llvmlite / resampy：由
#     pyinstaller-hooks-contrib（已装在本环境）提供专用 hook，各自负责收集所需
#     的 DLL / 数据文件（如 soundfile 的 libsndfile*.dll、librosa 的滤波器系数
#     msgpack）——这里只需让 PyInstaller 发现模块名，不需要额外 collect_all。
#   - scipy：PyInstaller 内置 hook 只覆盖常规子包，实测打包版会漏
#     scipy._external.array_api_compat.numpy.fft（librosa 音频读取路径懒加载，
#     0.4.1.1 用真实 mp3 复现），改用 collect_submodules 完整收集规避同类问题。
#   - matplotlib 是 piano_transcription_inference 的顶层 import（非按需，已用
#     模拟排除 tkinter 的方式验证过：matplotlib 会正常回退到非交互的 Agg 后端，
#     不会崩溃），体积较大但无法避免。0.4.1 曾在 excludes 里误排除它导致音频识别
#     完全不可用（见 CHANGELOG 0.4.1.1）——不要把 matplotlib/mpl_toolkits 加回
#     excludes。
#   - piano_transcription_inference / torchlibrosa / pretty_midi / mido 没有
#     专用 hook，用 collect_submodules 显式收集子模块。
hiddenimports += [
    'torch', 'librosa', 'soundfile', 'numba', 'llvmlite', 'resampy', 'soxr',
    'matplotlib',
]
hiddenimports += collect_submodules('scipy')
# scipy._external 是隐式命名空间包（无 __init__.py）；collect_submodules('scipy')
# 从 scipy 顶层遍历时静默跳过命名空间子包，array_api_compat/array_api_extra/
# cobyqa/pyprima 等整棵子树（含 librosa 懒加载用到的 numpy.fft 适配层）完全收不到，
# 必须单独对命名空间包本身再收集一次。
hiddenimports += collect_submodules('scipy._external')
hiddenimports += collect_submodules('piano_transcription_inference')
hiddenimports += collect_submodules('torchlibrosa')
hiddenimports += collect_submodules('pretty_midi')
hiddenimports += collect_submodules('mido')

# ── Flet GUI 框架已于 M5-⑤ 移除：pywebview 壳完全替代，flet 运行时不再打包。
#    gui/ 仅保留 webui 复用的模块（strings/settings/worker_launcher/app_state），
#    已无 flet import，collect_submodules('gui') 安全。

# ── pywebview 壳（M5-④b）：webui 包 + 静态资源 + pywebview + pythonnet + truststore ─
# webui/*.py 作为模块进 PYZ；webui/static/** 作为数据（pdf.js / noteDigger / tinysynth）。
hiddenimports += collect_submodules('webui')
collect_tree('webui/static', 'webui/static')

# pywebview（WinForms + edgechromium 后端）。collect_all 带上 webview/lib 下的
# WebView2 interop DLL（Microsoft.Web.WebView2.Core/WinForms.dll、WebBrowserInterop
# x64/x86、runtimes/）。若打包版运行时报缺 DLL，再显式补 binaries。
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# pythonnet（webview 的 .NET 桥）。⚠️ edgechromium 设 PYTHONNET_RUNTIME=coreclr，
# 依赖 .NET (Core) 运行时——构建/目标机需有 .NET 或改用 netfx。此处先收集包本身，
# .NET runtime 的落地在 ④b 构建时按报错迭代（可能需 bundle 或切 netfx）。
tmp_ret = collect_all('pythonnet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('clr_loader')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
hiddenimports += ['clr']

# truststore（系统证书库；纯 Python）
hiddenimports += collect_submodules('truststore')

# ── GUI 层（gui/ 包 —— 页面、组件、主题、状态）────────────────────────────────
hiddenimports += collect_submodules('gui')
datas += [('gui', 'gui')]

# ── 应用图标及静态资源 ──────────────────────────────────────────────────────────
datas += [('assets', 'assets')]


a = Analysis(
    ['run_webui.py'],   # M5-④b：入口从 Flet 壳（app.py）切换到 pywebview 壳
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['build_hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # tkinter — Flet 用 Flutter，不用 Tk
        'tkinter', '_tkinter',
        # 注意：matplotlib 曾经在这里被排除（music21 只是可选依赖，本项目不用图形化
        # 显示），但 piano_transcription_inference（音频识别引擎）是顶层硬依赖
        # matplotlib，而 excludes 优先级高于下面的 hiddenimports —— 排除后打包版里
        # matplotlib 完全不存在，导致每次音频转录都以 "No module named 'matplotlib'"
        # 失败（实测 v0.4.1 打包版复现，见 CHANGELOG）。不要重新加回来。
        # 注意：unittest/doctest/pydoc/xmlrpc 不能排除，运行时依赖链会 import 它们
    ],
    noarchive=False,
    optimize=0,
)
# ── Analysis 后二次过滤：彻底移除所有 CUDA 运行时 DLL ──────────────────────────
# PyInstaller 的二进制依赖扫描器可能仍会通过 DLL import chain 把 CUDA 库拉进来，
# 此处对 a.binaries 和 a.datas 再做一次模式匹配过滤，确保不遗漏。
def _toc_exclude_cuda(toc):
    result = []
    for entry in toc:
        dest = entry[0]
        src  = entry[1] if len(entry) > 1 else ''
        dest_name = os.path.basename(dest)
        src_name  = os.path.basename(src)
        if _cuda_pat.search(dest_name) or _cuda_pat.search(src_name):
            continue
        if src and (os.sep + 'nvidia' + os.sep) in src and src.lower().endswith('.dll'):
            continue
        result.append(entry)
    return result

a.binaries = _toc_exclude_cuda(a.binaries)
a.datas    = _toc_exclude_cuda(a.datas)

# ── 排除 music21 corpus 语料库（约 50 MB 示例乐谱，运行时完全不需要）──────────────
def _is_music21_corpus(entry):
    dest = entry[0].replace('\\', '/')
    return dest.startswith('music21/corpus/') or '/music21/corpus/' in dest

a.datas = [e for e in a.datas if not _is_music21_corpus(e)]

# ── 排除 cv2 中本项目不用的大文件 ──────────────────────────────────────────────
# opencv_videoio_ffmpeg*.dll — 视频 I/O，本项目只处理图片（约 27 MB）
# haarcascade_*.xml          — 人脸/肢体检测分类器，本项目不用（约 8 MB）
import fnmatch as _fnmatch
def _is_unused_cv2_data(entry):
    name = os.path.basename(entry[0])
    return _fnmatch.fnmatch(name, 'opencv_videoio_ffmpeg*.dll') or \
           _fnmatch.fnmatch(name, 'haarcascade_*.xml')

a.binaries = [e for e in a.binaries if not _is_unused_cv2_data(e)]
a.datas    = [e for e in a.datas    if not _is_unused_cv2_data(e)]

# ── 排除 HOMR ONNX 权重 — 运行时按需下载，不再随安装包分发 ─────────────────────
# 6 个 .onnx 文件总计约 292 MB，由 core.omr.homr_downloader 在首次启动或用户
# 首次选择 HOMR/auto 引擎时下载到 <app_base_dir>/models/。
def _is_homr_onnx(entry):
    dest = entry[0].replace('\\', '/')
    name = os.path.basename(dest)
    return name.endswith('.onnx') and '/homr/' in dest

a.binaries = [e for e in a.binaries if not _is_homr_onnx(e)]
a.datas    = [e for e in a.datas    if not _is_homr_onnx(e)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SumisoraOMR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
    version='version_info.txt',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SumisoraOMR',
)
