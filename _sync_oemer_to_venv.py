"""Sync oemer model files from package-assets/oemer-runtime into the venv."""
import os
import sys
import shutil
from pathlib import Path

src_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("package-assets/oemer-runtime")

try:
    from oemer import MODULE_PATH
    dst_root = Path(MODULE_PATH)
except ImportError:
    print("[ERROR] oemer not installed", file=sys.stderr)
    sys.exit(1)

for subdir in ("checkpoints", "sklearn_models"):
    src = src_root / subdir
    dst = dst_root / subdir
    if src.is_dir():
        shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
        print(f"  synced {subdir}")
    else:
        print(f"  [skip] {subdir} not found in source")

print("oemer models synced to venv")
