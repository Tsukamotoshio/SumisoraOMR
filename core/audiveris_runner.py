# core/audiveris_runner.py — Audiveris OMR 批处理
# 拆分自 convert.py
import hashlib
import logging
import shutil
from pathlib import Path
from typing import Optional

from .config import (
    LOGGER,
    MAX_AUDIVERIS_SECONDS,
)
from .image_preprocess import (
    HAS_PILLOW,
    _measure_laplacian_stddev,
    fit_image_within_pixel_limit,
    preprocess_image_for_omr,
)
from .runtime_finder import (
    ensure_audiveris_executable,
    find_java_executable,
    get_audiveris_required_java_version,
    run_subprocess_with_spinner,
)
from .utils import (
    build_safe_ascii_name,
    find_first_musicxml_file,
    get_app_base_dir,
    get_pdf_page_count,
    log_message,
    safe_remove_file,
    safe_remove_tree,
)


def prepare_audiveris_paths(input_path: Path, output_dir: Path) -> tuple[Path, Path, Optional[Path]]:
    """
    Copy the input to an ASCII-safe filename and create an isolated Audiveris job directory.
    Returns (safe_input_path, safe_output_dir, cleanup_copy).
    """
    input_path = input_path.resolve()
    output_dir = output_dir.resolve()
    token = hashlib.sha1(str(input_path).encode('utf-8', errors='ignore')).hexdigest()[:10]
    safe_parent = output_dir.parent.resolve()
    safe_parent.mkdir(parents=True, exist_ok=True)

    safe_stem = build_safe_ascii_name(input_path.stem, fallback='input')
    safe_input_path = safe_parent / f'{safe_stem}_{token}{input_path.suffix.lower()}'
    safe_output_dir = safe_parent / f'audiveris_job_{token}'

    if safe_output_dir.exists():
        shutil.rmtree(safe_output_dir, ignore_errors=True)
    safe_output_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(input_path, safe_input_path)
    return safe_input_path, safe_output_dir, safe_input_path


def run_audiveris_batch(input_path: Path, output_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Run Audiveris OMR on the input file; falls back to per-page processing for multi-page PDFs.
    Returns the output directory containing MusicXML, or None on failure.
    """
    required_java_version = get_audiveris_required_java_version()
    java_exe = find_java_executable(required_java_version)
    exe = ensure_audiveris_executable()
    if output_dir is None:
        output_dir = get_app_base_dir() / 'audiveris-output'
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_input_path, safe_output_dir, cleanup_input_copy = prepare_audiveris_paths(input_path, output_dir)
    # Keep a reference to the original (unprocessed) path so fallback retry can use it.
    original_safe_input_path = safe_input_path

    # Pre-process raster images (PNG/JPG) to improve OMR accuracy before passing to Audiveris.
    # For PDF inputs Audiveris renders pages internally, so preprocessing is skipped.
    omr_preprocessed_path: Optional[Path] = None
    omr_rescaled_path: Optional[Path] = None
    if safe_input_path.suffix.lower() in {'.png', '.jpg', '.jpeg'}:
        preprocessed = preprocess_image_for_omr(safe_input_path, safe_output_dir.parent)
        if preprocessed is not None:
            omr_preprocessed_path = preprocessed
            safe_input_path = preprocessed

        # Enforce Audiveris pixel limit (20 M px); downscale if needed regardless of
        # whether preprocessing was applied, since even an unmodified high-res original
        # can exceed the limit.
        rescaled = fit_image_within_pixel_limit(safe_input_path, safe_output_dir.parent)
        if rescaled is not None:
            omr_rescaled_path = rescaled
            safe_input_path = rescaled

    def invoke_audiveris(
        target_dir: Path,
        sheet_number: Optional[int] = None,
        input_path_override: Optional[Path] = None,
    ) -> tuple[int, str, str, Optional[Path]]:
        """Run Audiveris once for a given output dir and optional sheet number; return (exit_code, stdout, stderr, mxl_path)."""
        effective_input = input_path_override if input_path_override is not None else safe_input_path
        target_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(exe),
            '-batch',
            '-constant', 'org.audiveris.omr.text.Language.defaultSpecification=eng+chi_sim',
            '-export',
        ]
        if sheet_number is not None:
            cmd.extend(['-sheets', str(sheet_number)])
        cmd.extend(['-output', str(target_dir), str(effective_input)])
        return_code, stdout, stderr = run_subprocess_with_spinner(cmd, cwd=str(exe.parent), java_exe=java_exe)
        exported_file = find_first_musicxml_file(target_dir, effective_input.stem)
        return return_code, stdout, stderr, exported_file

    try:
        if exe is None:
            log_message('未能生成或定位 Audiveris 启动器。根据官方文档，应先完成源码构建，再使用生成的 Audiveris.bat 运行。', logging.WARNING)
            log_message('请确认 audiveris-5.10.2 已构建成功，或重新运行程序让其自动准备启动器。', logging.WARNING)
            return None

        log_message(f'调用 Audiveris 启动器: {exe}')
        return_code, stdout, stderr, exported_file = invoke_audiveris(safe_output_dir)
        if return_code == 0 or exported_file is not None:
            if return_code != 0:
                log_message('Audiveris 返回了非零退出码，但已成功导出 MusicXML，继续后续处理。', logging.WARNING)
            log_message('Audiveris 执行完成。')
            return safe_output_dir

        page_count = get_pdf_page_count(safe_input_path)
        if safe_input_path.suffix.lower() == '.pdf' and page_count > 1:
            log_message(f'检测到多页 PDF（共 {page_count} 页），正在按页回退处理，跳过识别失败的页面...', logging.WARNING)
            success_pages: list[int] = []
            for page_number in range(1, page_count + 1):
                page_output_dir = safe_output_dir / f'sheet_{page_number:03d}'
                safe_remove_tree(page_output_dir)
                page_output_dir.mkdir(parents=True, exist_ok=True)
                page_code, page_stdout, page_stderr, page_exported = invoke_audiveris(page_output_dir, sheet_number=page_number)
                if page_code == 0 or page_exported is not None:
                    success_pages.append(page_number)
                    log_message(f'第 {page_number} 页已成功导出。')
                else:
                    page_combined = (page_stderr or '') + '\n' + (page_stdout or '')
                    no_staves_kw = ['Created scores: []', 'Sheet flagged as invalid', 'No good interline', 'Too few black pixels', 'no staves']
                    page_reason = '（图像过于模糊或无法检测五线谱）' if any(k.lower() in page_combined.lower() for k in no_staves_kw) else ''
                    detail_parts = [part.strip() for part in (page_stderr, page_stdout) if part and part.strip()]
                    detail = '\n'.join(detail_parts)
                    log_message(f'第 {page_number} 页无法识别为有效五线谱，已跳过。{page_reason}{detail[:240]}', logging.WARNING)
            if success_pages:
                log_message(f'Audiveris 已按页完成导出，成功页: {success_pages}')
                return safe_output_dir

        combined = (stdout or '') + '\n' + (stderr or '')

        # ── Retry: CURVES crash → re-run with original (unprocessed) image ──────────
        # Audiveris fully recognises the score but its CURVES step (slur/tie detection)
        # sometimes crashes on preprocessed images.  Retry with the original file.
        is_curves_crash = (
            'Error processing stub' in combined
            and ('Error in export' in combined or 'transcription did not complete' in combined.lower())
        )
        preprocessing_was_applied = (
            omr_preprocessed_path is not None or omr_rescaled_path is not None
        )
        if is_curves_crash and preprocessing_was_applied:
            log_message(
                'Audiveris 在连音线处理步骤（CURVES）中发生错误，'
                '尝试使用原始图像跳过预处理后重试...', logging.WARNING
            )
            safe_remove_tree(safe_output_dir)
            r2_code, r2_out, r2_err, r2_exported = invoke_audiveris(
                safe_output_dir, input_path_override=original_safe_input_path
            )
            if r2_code == 0 or r2_exported is not None:
                if r2_code != 0:
                    log_message('重试时 Audiveris 返回非零退出码，但已成功导出 MusicXML，继续后续处理。', logging.WARNING)
                log_message('使用原始图像重试成功。')
                return safe_output_dir
            # Update combined for the next error-classification check
            stdout, stderr = r2_out, r2_err
            combined = (stdout or '') + '\n' + (stderr or '')

        # Report specific CURVES / export-error message if still failing
        if is_curves_crash or (
            'Error in export' in combined
            and 'Error processing stub' in combined
        ):
            log_message(
                '\n'
                '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
                '  ✗  Audiveris 识别了五线谱但导出失败\n'
                '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
                '  Audiveris 已检测到五线谱结构，但在曲线/连音线处理步骤（CURVES）\n'
                '  中发生内部崩溃，导致乐谱无法正常导出。\n'
                '\n'
                '  可能原因：\n'
                '  • 图像中包含复杂的连音线或延音线，触发了 Audiveris 的已知崩溃\n'
                '  • 手机拍摄图像中的阴影/倾斜线条被误识别为连音弧线\n'
                '  • 单张图像包含多页内容，使曲线跨页处理失败\n'
                '\n'
                '  建议操作：\n'
                '  • 优先使用 PDF 版本（推荐），PDF 版本不受此限制\n'
                '  • 如使用拍摄图像，可尝试每次只包含 1-2 个系统（裁剪后分批处理）\n'
                '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
                logging.WARNING,
            )
            return None

        # Check for "no staves found" - typically happens with blurry / low-contrast images
        no_staves_keywords = [
            'Created scores: []',
            'Sheet flagged as invalid',
            'No good interline',
            'Too few black pixels',
            'no staves',
        ]
        is_no_staves = any(kw.lower() in combined.lower() for kw in no_staves_keywords)
        if is_no_staves:
            sharpness_hint = ''
            if safe_input_path.suffix.lower() in {'.png', '.jpg', '.jpeg'} and HAS_PILLOW:
                try:
                    from PIL import Image
                    with Image.open(safe_input_path) as _img:
                        _val = _measure_laplacian_stddev(_img)
                    sharpness_hint = f'（当前图像锐度指数：{_val:.1f}，建议 ≥ 30）'
                except Exception:
                    pass
            log_message(
                '\n'
                '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
                '  ✗  无法识别图像中的五线谱\n'
                '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
                f'  Audiveris 无法在输入图像中检测到有效的五线谱结构。{sharpness_hint}\n'
                '\n'
                '  可能原因：\n'
                '  • 图像过于模糊或对比度过低，五线谱线条无法被识别\n'
                '  • 扫描分辨率不足（建议 ≥ 150 DPI，最短边 ≥ 1200 像素）\n'
                '  • 图像内容不是标准印刷五线谱（如手写谱、草稿等）\n'
                '\n'
                '  建议操作：\n'
                '  • 使用更高质量或分辨率更高的扫描版本重试\n'
                '  • 如使用手机拍摄，请确保拍摄时光线充足、图像清晰\n'
                '  • 如图像原本清晰但识别失败，请确认其为标准印刷五线谱\n'
                '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
                logging.WARNING,
            )
        else:
            detail_parts = [part.strip() for part in (stderr, stdout) if part and part.strip()]
            detail = '\n'.join(detail_parts)
            log_message('\nAudiveris 执行失败: ' + (detail or '未知错误。'), logging.WARNING)
        return None
    finally:
        if cleanup_input_copy is not None:
            safe_remove_file(cleanup_input_copy)
        if omr_preprocessed_path is not None:
            safe_remove_file(omr_preprocessed_path)
        if omr_rescaled_path is not None:
            safe_remove_file(omr_rescaled_path)
