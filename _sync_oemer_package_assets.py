#!/usr/bin/env python3
"""Sync oemer model files from the installed oemer package into package-assets/oemer-runtime."""
import sys
import shutil
from pathlib import Path

try:
    from oemer import MODULE_PATH
except ImportError:
    print('[ERROR] oemer not installed', file=sys.stderr)
    sys.exit(1)

src_root = Path(MODULE_PATH)
dst_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('package-assets/oemer-runtime')
dst_root.mkdir(parents=True, exist_ok=True)

for subdir in ('checkpoints', 'sklearn_models'):
    src = src_root / subdir
    dst = dst_root / subdir
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
        print(f'  synced {subdir}')
    else:
        print(f'  [skip] {subdir} not found in installed oemer package')

print('oemer package-assets sync complete')
