#!/usr/bin/env python
# scripts/sync_version.py — propagate core.config.APP_VERSION to the other files
# that must carry the version literally (P3-1 single-source-of-truth).
#
# core.config.APP_VERSION is the ONE source of truth. app.py already derives the
# exe VERSIONINFO tuple from it at runtime; this script updates the two remaining
# static files that a build/reader sees:
#   - version_info.txt  (PyInstaller VERSIONINFO: filevers/prodvers + version strings)
#   - README.md         (the shields.io version badge)
#
# Run before tagging/building a release:  python scripts/sync_version.py
# Exit code 1 (no writes) if anything is already out of the expected shape.
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read_app_version() -> str:
    text = (ROOT / 'core' / 'config.py').read_text(encoding='utf-8')
    m = re.search(r"^APP_VERSION\s*=\s*'([^']+)'", text, re.MULTILINE)
    if not m:
        sys.exit("APP_VERSION not found in core/config.py")
    return m.group(1)


def _version_parts(v: str) -> tuple[int, int, int]:
    nums = [int(x) for x in v.split('.')[:3] if x.isdigit()]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def _sync_version_info(v: str, changed: list[str]) -> None:
    p = ROOT / 'version_info.txt'
    s = p.read_text(encoding='utf-8')
    ma, mi, pa = _version_parts(v)
    quad = f'{ma}.{mi}.{pa}.0'
    s2 = re.sub(r'filevers=\(\d+, \d+, \d+, \d+\)', f'filevers=({ma}, {mi}, {pa}, 0)', s)
    s2 = re.sub(r'prodvers=\(\d+, \d+, \d+, \d+\)', f'prodvers=({ma}, {mi}, {pa}, 0)', s2)
    s2 = re.sub(r"(StringStruct\(u'FileVersion',\s*u')[^']+(')", rf'\g<1>{quad}\g<2>', s2)
    s2 = re.sub(r"(StringStruct\(u'ProductVersion',\s*u')[^']+(')", rf'\g<1>{quad}\g<2>', s2)
    if s2 != s:
        p.write_text(s2, encoding='utf-8')
        changed.append(f'version_info.txt -> {quad}')


def _sync_readme(v: str, changed: list[str]) -> None:
    p = ROOT / 'README.md'
    s = p.read_text(encoding='utf-8')
    # [![Version: v0.3.6](https://img.shields.io/badge/Version-v0.3.6-green.svg)]()
    s2 = re.sub(r'Version:\s*v[0-9][0-9.]*', f'Version: v{v}', s)
    s2 = re.sub(r'badge/Version-v[0-9][0-9.]*-', f'badge/Version-v{v}-', s2)
    if s2 != s:
        p.write_text(s2, encoding='utf-8')
        changed.append(f'README.md badge -> v{v}')


def main() -> None:
    v = _read_app_version()
    changed: list[str] = []
    _sync_version_info(v, changed)
    _sync_readme(v, changed)
    print(f'APP_VERSION = {v}')
    if changed:
        for c in changed:
            print('  updated:', c)
    else:
        print('  all targets already in sync')


if __name__ == '__main__':
    main()
