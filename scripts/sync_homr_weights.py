#!/usr/bin/env python3
"""Sync HOMR ONNX weights to the local models/ dir and the ModelScope mirror.

Single source of truth for the canonical 367-model weight set. Keeps three
things consistent:

  1. Local ``<repo>/models/`` (what the dev app loads).
  2. The ModelScope mirror ``Tsukamotoshio/homr`` (what end users download).
  3. The SHA256 hashes declared in the homr submodule (``homr/main.py``).

Download source is the upstream GitHub release (``liebharc/homr`` tag
``onnx_checkpoints``), which is the authoritative origin of new weights; the
ModelScope mirror is the *upload* target. Layouts differ per mirror:

  - GitHub release: flat ``<basename>.zip`` attachments, one .onnx each.
  - ModelScope: ``segmentation/<file>`` for segnet_*, ``transformer/<file>``
    for encoder_/decoder_*.

Subcommands:
  download   Fetch + unzip + SHA256-verify each weight into models/.
  verify     Re-check SHA256 of the local files (no network).
  prune      Delete stale local weights (the old 331 model set).
  upload     Push the local files to the ModelScope mirror (needs token).
  all        download -> verify -> prune (no upload).

The ModelScope token is NEVER hardcoded or written to disk. Pass it via the
MODELSCOPE_API_TOKEN env var (preferred) or --token. Get one at:
  https://www.modelscope.cn/my/myaccesstoken  (SDK access token)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import requests

# --- Canonical weight set (367 model). Mirror of homr/main.py _WEIGHT_* ------

REPO_ID = "Tsukamotoshio/homr"
GITHUB_BASE = "https://github.com/liebharc/homr/releases/download/onnx_checkpoints/"

# filename -> (sha256, modelscope_subdir, is_new_367)
WEIGHTS: dict[str, tuple[str, str, bool]] = {
    "segnet_308-3296ccd40960f90ca6ab9c035cca945675d30a0f.onnx": (
        "6ed36640db4ef5d223098b6d5efe4eda97c66b24a2c72faab8a018c749003a8d",
        "segmentation", False),
    "segnet_308-3296ccd40960f90ca6ab9c035cca945675d30a0f_fp16.onnx": (
        "60f495496cb41473c0521d0811d8f44b9d5cff892d287974a8aebb3eaee2fa83",
        "segmentation", False),
    "encoder_pytorch_model_367-575b4737bca815d3a7b37169269fc548d7e945b9.onnx": (
        "1427f5144d2617184515ba60b50be94a0119a10510a0ff8d58fe5fc4555599c2",
        "transformer", True),
    "encoder_pytorch_model_367-575b4737bca815d3a7b37169269fc548d7e945b9_fp16.onnx": (
        "aa252963c934234d30faca0a2363903f6ec02e81f85f87f12dadad7035ec6495",
        "transformer", True),
    "decoder_pytorch_model_367-575b4737bca815d3a7b37169269fc548d7e945b9.onnx": (
        "331cef4a41f39e97b57d506951fa17fb7ae3345eaa6334107c71c1f6203372bd",
        "transformer", True),
    "decoder_pytorch_model_367-575b4737bca815d3a7b37169269fc548d7e945b9_fp16.onnx": (
        "60c037e2eb30a142d746051e5c3aa0aa23445b3563c44673f95b8b1c5102a6aa",
        "transformer", True),
}

# Stale weights from the previous (331) model — removed by `prune`.
STALE_PREFIXES = ("encoder_pytorch_model_331-", "decoder_pytorch_model_331-")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODELS_DIR = REPO_ROOT / "models"


# --- helpers ----------------------------------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def verify_one(path: Path, expected: str) -> bool:
    return path.is_file() and sha256_of(path) == expected.lower()


def download_stream(url: str, dest: Path) -> None:
    """Stream a URL to dest with a simple progress line."""
    with requests.get(url, stream=True, timeout=60, allow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    print(f"\r    {dest.name}: {pct:3d}% "
                          f"({done // (1024*1024)}/{total // (1024*1024)} MB)",
                          end="", flush=True)
        print()


def github_zip_url(filename: str) -> str:
    basename = filename[:-len(".onnx")]
    return f"{GITHUB_BASE}{basename}.zip"


# --- subcommands ------------------------------------------------------------

def cmd_download(models_dir: Path, only_new: bool, force: bool) -> int:
    models_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for filename, (sha, _subdir, is_new) in WEIGHTS.items():
        if only_new and not is_new:
            continue
        target = models_dir / filename
        if not force and verify_one(target, sha):
            print(f"[skip] {filename} (already present, sha ok)")
            continue
        url = github_zip_url(filename)
        print(f"[get ] {filename}\n    <- {url}")
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp_zip = Path(td) / "w.zip"
                download_stream(url, tmp_zip)
                with zipfile.ZipFile(tmp_zip) as z:
                    members = [m for m in z.namelist() if m.endswith(".onnx")]
                    # Prefer the member whose basename matches; else first .onnx.
                    pick = next((m for m in members
                                 if os.path.basename(m) == filename), None)
                    if pick is None and members:
                        pick = members[0]
                    if pick is None:
                        raise RuntimeError(f"no .onnx inside {url}")
                    z.extract(pick, td)
                    extracted = Path(td) / pick
                    # shutil.move handles cross-drive moves (temp on C:, models
                    # on E:); os.replace would raise WinError 17. Remove any
                    # existing target first so the move never hits a name clash.
                    if target.exists():
                        target.unlink()
                    shutil.move(str(extracted), str(target))
            if verify_one(target, sha):
                print(f"    OK sha256 verified")
            else:
                got = sha256_of(target) if target.is_file() else "<missing>"
                print(f"    !! SHA256 MISMATCH\n       expected {sha}\n"
                      f"       got      {got}")
                failures += 1
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"    !! download failed: {e}")
            failures += 1
    return failures


def cmd_verify(models_dir: Path) -> int:
    failures = 0
    for filename, (sha, _subdir, _new) in WEIGHTS.items():
        target = models_dir / filename
        if verify_one(target, sha):
            print(f"[ ok ] {filename}")
        elif target.is_file():
            print(f"[FAIL] {filename} (sha mismatch: {sha256_of(target)})")
            failures += 1
        else:
            print(f"[MISS] {filename} (not found)")
            failures += 1
    return failures


def cmd_prune(models_dir: Path) -> int:
    removed = 0
    for p in sorted(models_dir.glob("*.onnx")):
        if p.name.startswith(STALE_PREFIXES):
            print(f"[del ] {p.name}")
            p.unlink()
            removed += 1
    if not removed:
        print("No stale weights to remove.")
    return 0


def cmd_upload(models_dir: Path, token: str, retries: int = 5,
               only_new: bool = False) -> int:
    # Import lazily so download/verify work without modelscope installed.
    import time

    from modelscope.hub.api import HubApi

    api = HubApi()
    api.login(token)
    failures = 0
    for filename, (sha, subdir, _new) in WEIGHTS.items():
        if only_new and not _new:
            continue
        target = models_dir / filename
        if not verify_one(target, sha):
            print(f"[skip] {filename} (local file missing or sha mismatch — "
                  f"run download first)")
            failures += 1
            continue
        path_in_repo = f"{subdir}/{filename}"
        print(f"[push] {path_in_repo}")
        # ModelScope drops large-file connections intermittently ("远程主机
        # 关闭了连接"). Retry with exponential backoff; each retry re-uploads
        # the whole file (upload_file is idempotent — it overwrites the path).
        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                api.upload_file(
                    path_or_fileobj=str(target),
                    path_in_repo=path_in_repo,
                    repo_id=REPO_ID,
                    repo_type="model",
                    revision="master",
                    commit_message=f"Upload {filename} (367 model)",
                )
                print("    OK")
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                wait = min(2 ** attempt, 30)
                print(f"    attempt {attempt}/{retries} failed: {e}")
                if attempt < retries:
                    print(f"    retrying in {wait}s ...")
                    time.sleep(wait)
        if last_err is not None:
            print(f"    !! gave up on {filename}: {last_err}")
            failures += 1
    return failures


def resolve_token(arg_token: str | None) -> str:
    token = arg_token or os.environ.get("MODELSCOPE_API_TOKEN", "").strip()
    if not token:
        sys.exit(
            "ERROR: no ModelScope token. Set MODELSCOPE_API_TOKEN env var or pass "
            "--token.\n  Get one at https://www.modelscope.cn/my/myaccesstoken")
    return token


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command",
                   choices=["download", "verify", "prune", "upload", "all"])
    p.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR,
                   help=f"weights dir (default: {DEFAULT_MODELS_DIR})")
    p.add_argument("--only-new", action="store_true",
                   help="download: only the changed 367 encoder/decoder files")
    p.add_argument("--force", action="store_true",
                   help="download: re-fetch even if local sha already matches")
    p.add_argument("--token", default=None,
                   help="ModelScope token (prefer MODELSCOPE_API_TOKEN env var)")
    args = p.parse_args()

    md: Path = args.models_dir
    print(f"models dir: {md}\n")

    if args.command == "download":
        return cmd_download(md, args.only_new, args.force)
    if args.command == "verify":
        return cmd_verify(md)
    if args.command == "prune":
        return cmd_prune(md)
    if args.command == "upload":
        return cmd_upload(md, resolve_token(args.token), only_new=args.only_new)
    if args.command == "all":
        rc = cmd_download(md, args.only_new, args.force)
        rc += cmd_verify(md)
        if rc == 0:
            cmd_prune(md)
        else:
            print("\nSkipping prune because download/verify reported errors.")
        return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
