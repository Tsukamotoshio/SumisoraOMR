#!/usr/bin/env python3
# download_oemer_models.py — 预下载 oemer 神经网络模型权重
#
# oemer 首次运行时会自动联网下载约 500MB 的 ONNX 模型权重文件。
# 打包分发（build_installer.bat / build_zip.bat）前请先运行本脚本，
# 以便 PyInstaller 能将权重文件一并打包进分发包。
#
# 用法：
#   python download_oemer_models.py
#
# 模型来源：https://github.com/BreezeWhite/oemer/releases/tag/checkpoints
import os
import sys
import urllib.request
from pathlib import Path

try:
    from oemer import MODULE_PATH
except ImportError:
    print('[ERROR] oemer 未安装，请先执行：pip install oemer', file=sys.stderr)
    sys.exit(1)

BASE_URL = 'https://github.com/BreezeWhite/oemer/releases/download/checkpoints'

# 映射：下载文件名 → (子目录, 保存文件名)
CHECKPOINTS: dict[str, tuple[str, str]] = {
    '1st_model.onnx': ('unet_big', 'model.onnx'),
    '1st_weights.h5': ('unet_big', 'weights.h5'),
    '2nd_model.onnx': ('seg_net',  'model.onnx'),
    '2nd_weights.h5': ('seg_net',  'weights.h5'),
}


def _reporthook(block_num: int, block_size: int, total_size: int) -> None:
    downloaded = block_num * block_size
    if total_size > 0:
        pct = min(100, downloaded * 100 // total_size)
        mb = downloaded / 1_048_576
        total_mb = total_size / 1_048_576
        print(f'\r    {pct:3d}%  {mb:.1f} / {total_mb:.1f} MB', end='', flush=True)


def main() -> None:
    print(f'oemer 模型权重目录: {MODULE_PATH}/checkpoints')
    print()
    all_present = True
    for filename, (subdir, save_name) in CHECKPOINTS.items():
        save_dir = Path(MODULE_PATH) / 'checkpoints' / subdir
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / save_name
        if save_path.exists() and save_path.stat().st_size > 1_000_000:
            print(f'  [已存在] {subdir}/{save_name}')
            continue
        all_present = False
        url = f'{BASE_URL}/{filename}'
        print(f'  [下载中] {filename}  →  {subdir}/{save_name}')
        try:
            urllib.request.urlretrieve(url, str(save_path), reporthook=_reporthook)
            print(f'\r  [完成]   {subdir}/{save_name} ({save_path.stat().st_size / 1_048_576:.1f} MB)')
        except Exception as exc:
            print(f'\r  [ERROR]  下载失败: {exc}', file=sys.stderr)
            # 删除不完整文件
            if save_path.exists():
                save_path.unlink()
            sys.exit(1)

    print()
    if all_present:
        print('所有 oemer 模型权重已就绪，无需下载。')
    else:
        print('所有 oemer 模型权重下载完成。')


if __name__ == '__main__':
    main()
