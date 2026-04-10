# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# ── 核心依赖 ──────────────────────────────────────────────────────────────────
hiddenimports += collect_submodules('reportlab')
hiddenimports += collect_submodules('core')
tmp_ret = collect_all('music21')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('rich')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── oemer 深度学习 OMR 引擎（含 ONNX 模型权重、sklearn 分类器等数据文件）────────
# 需在构建前运行 download_oemer_models.py 预下载权重，才会被打包进分发包。
tmp_ret = collect_all('oemer')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── ONNX Runtime（oemer 推理引擎 —— DLL + 数据文件）────────────────────────────
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── PyMuPDF（PDF → 图片转换，oemer 处理 PDF 输入时使用）────────────────────────
tmp_ret = collect_all('fitz')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── OpenCV（oemer 图像处理依赖）──────────────────────────────────────────────
tmp_ret = collect_all('cv2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── scikit-learn（oemer 符号分类器依赖）──────────────────────────────────────
hiddenimports += collect_submodules('sklearn')

# ── scipy（oemer 数值计算依赖）───────────────────────────────────────────────
hiddenimports += collect_submodules('scipy')


a = Analysis(
    ['convert.py'],
    pathex=[],
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
