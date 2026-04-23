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
import re as _re
_cuda_pat = _re.compile(
    r'(?:cublas|cufft|cudart|cudnn|nvrtc|nvblas|curand|cusparseLt|'
    r'onnxruntime_providers_cuda|onnxruntime_providers_tensorrt|directml|'
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
    # homr 权重文件会随 omr_engine/homr/homr 一起打包。
    # 只要这些本地模型文件存在，运行时就不会再联网下载缺失权重。
    # homr 运行时依赖：rapidocr（含 ONNX 模型）与 musicxml（乐谱序列化）
    # 这两个包不会被 PyInstaller 静态分析到（homr 以数据文件方式收集），
    # 需在此手动声明，否则分发包中无法 import。
    tmp_ret = collect_all('rapidocr')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    tmp_ret = collect_all('musicxml')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── OpenCV（图像处理依赖）────────────────────────────────────────────
tmp_ret = collect_all('cv2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── Flet GUI 框架（桌面运行时 + 所有子模块）─────────────────────────────────────
tmp_ret = collect_all('flet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('flet_desktop')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
hiddenimports += collect_submodules('flet')
hiddenimports += collect_submodules('flet_desktop')

# ── GUI 层（gui/ 包 —— 页面、组件、主题、状态）────────────────────────────────
hiddenimports += collect_submodules('gui')
datas += [('gui', 'gui')]


a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ConvertTool',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ConvertTool',
)
