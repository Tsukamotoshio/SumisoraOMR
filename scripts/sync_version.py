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


def _version_parts(v: str) -> tuple[int, int, int, int]:
    """Up to 4 dot-separated numeric segments (major, minor, patch, build).

    A 3-segment version ('0.4.1') pads the 4th with 0; a 4-segment version
    ('0.4.1.1', used for a packaging-only patch release) carries its own 4th
    part through instead of always forcing it to 0.
    """
    nums = [int(x) for x in v.split('.')[:4] if x.isdigit()]
    while len(nums) < 4:
        nums.append(0)
    return nums[0], nums[1], nums[2], nums[3]


def _sync_version_info(v: str, changed: list[str]) -> None:
    p = ROOT / 'version_info.txt'
    s = p.read_text(encoding='utf-8')
    ma, mi, pa, bu = _version_parts(v)
    quad = f'{ma}.{mi}.{pa}.{bu}'
    s2 = re.sub(r'filevers=\(\d+, \d+, \d+, \d+\)', f'filevers=({ma}, {mi}, {pa}, {bu})', s)
    s2 = re.sub(r'prodvers=\(\d+, \d+, \d+, \d+\)', f'prodvers=({ma}, {mi}, {pa}, {bu})', s2)
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


def _sync_iss(v: str, changed: list[str]) -> None:
    # convert_setup.iss is a private (gitignored) Inno Setup script; update it in
    # place when present so a local installer build stays in sync.
    p = ROOT / 'convert_setup.iss'
    if not p.is_file():
        return
    ma, mi, pa, bu = _version_parts(v)
    s = p.read_text(encoding='utf-8')
    s2 = re.sub(r'(#define\s+MyAppVersion\s+")[^"]+(")', rf'\g<1>{v}\g<2>', s)
    s2 = re.sub(r'(#define\s+MyAppVersionNumeric\s+")[^"]+(")', rf'\g<1>{ma}.{mi}.{pa}.{bu}\g<2>', s2)
    if s2 != s:
        p.write_text(s2, encoding='utf-8')
        changed.append(f'convert_setup.iss -> {v}')


def _sync_build_zip(v: str, changed: list[str]) -> None:
    # scripts/build_zip.bat is a private (gitignored) packaging script.
    p = ROOT / 'scripts' / 'build_zip.bat'
    if not p.is_file():
        return
    s = p.read_text(encoding='utf-8')
    s2 = re.sub(r'(?mi)^(set VERSION=).*$', rf'\g<1>{v}', s)
    if s2 != s:
        p.write_text(s2, encoding='utf-8')
        changed.append(f'build_zip.bat -> {v}')


def main() -> None:
    v = _read_app_version()
    changed: list[str] = []
    _sync_version_info(v, changed)
    _sync_readme(v, changed)
    _sync_iss(v, changed)
    _sync_build_zip(v, changed)
    print(f'APP_VERSION = {v}')
    if changed:
        for c in changed:
            print('  updated:', c)
    else:
        print('  all targets already in sync')


if __name__ == '__main__':
    main()
