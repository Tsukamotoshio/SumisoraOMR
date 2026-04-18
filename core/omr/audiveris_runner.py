# core/audiveris_runner.py — Audiveris OMR 批处理
# 拆分自 convert.py
import hashlib
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from ..config import (
    LOGGER,
    MAX_AUDIVERIS_SECONDS,
)
from ..image.image_preprocess import (
    HAS_PILLOW,
    _measure_laplacian_stddev,
    fit_image_within_pixel_limit,
    preprocess_image_for_omr,
)
from ..app.runtime_finder import (
    ensure_audiveris_executable,
    find_java_executable,
    get_audiveris_required_java_version,
    run_subprocess_with_spinner,
)
from ..utils import (
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


def _has_cjk_chars(text: str) -> bool:
    """Return True if *text* contains any CJK Unified Ideographs (U+4E00–U+9FFF)."""
    return any('\u4e00' <= ch <= '\u9fff' for ch in text)


def _choose_ocr_lang(input_path: Path) -> str:
    """选择 Audiveris 的 Tesseract OCR 语言规格。

    逻辑
    ----
    包含中文字符的文件名  →  ``eng+chi_sim``（英文 + 简体中文）
    其他文件              →  ``eng``（拉丁/英文，避免中文 OCR 干扰西洋歌词识别）

    对于英语/法语/西班牙语等拉丁字母歌词丰富的乐谱，仅使用 ``eng`` 可显著减少
    Audiveris 文字识别子系统的干扰，有助于提高五线谱符号的识别准确率。
    """
    if _has_cjk_chars(input_path.stem):
        return 'eng+chi_sim'
    return 'eng'


def _copy_preprocessed_ref(src: Optional[Path], dest_dir: Path) -> None:
    """Copy the preprocessed reference image into *dest_dir* as ``_preprocessed_ref.png``.

    Called just before each successful return in :func:`run_audiveris_batch` so that
    the pipeline can offer the enhanced/deskewed image as the editor workspace reference
    instead of the raw original input.  Silently skips if *src* is ``None`` or missing.
    """
    if src is None or not src.exists():
        return
    try:
        shutil.copy2(str(src), str(dest_dir / '_preprocessed_ref.png'))
    except OSError:
        pass


def _maybe_merge_mvt_files(job_dir: Path) -> None:
    """若 *job_dir* 中存在多个 Audiveris mvt*.mxl 文件，将其合并为一个完整 MusicXML。

    Audiveris 对单张多谱系统图像的处理有时会将每个谱系统导出为独立 movement 文件
    （xxxx.mvt1.mxl, xxxx.mvt2.mxl…）。pipeline 通过 find_first_musicxml_file 只取
    第一个文件，会丢失后续系统的音符。此函数将所有 mvt 文件顺序合并为一个
    ``{stem}_merged.musicxml`` 文件，以便 pipeline 得到完整乐谱内容。

    若只有 1 个 mxl 文件或合并失败，保持不变（不影响后续流程）。
    """
    mvt_files = sorted(job_dir.glob('*.mvt*.mxl'))
    if len(mvt_files) < 2:
        return
    merged = _merge_mxl_files(mvt_files, job_dir, mvt_files[0].stem.split('.')[0])
    if merged is not None:
        log_message(f'  [mvt 合并] 已将 {len(mvt_files)} 个 mvt 文件合并 → {merged.name}')


def run_audiveris_batch(
    input_path: Path,
    output_dir: Optional[Path] = None,
    skip_preprocessing: bool = False,
) -> Optional[Path]:
    """
    Run Audiveris OMR on the input file; falls back to per-page processing for multi-page PDFs.
    Returns the output directory containing MusicXML, or None on failure.

    Parameters
    ----------
    input_path          : Input score file (PDF / PNG / JPG).
    output_dir          : Directory for Audiveris job outputs.
    skip_preprocessing  : When True, bypass the internal image preprocessing step
                          (waifu2x SR + Pillow enhancement + downscale).  Set this flag
                          when the caller has already run enhance_image() upstream.
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

    # Determine OCR language based on filename — avoids chi_sim interference for Western scores.
    ocr_lang = _choose_ocr_lang(input_path)
    log_message(f'[audiveris] OCR 语言规格: {ocr_lang}')

    # Pre-process raster images (PNG/JPG) to improve OMR accuracy before passing to Audiveris.
    # For PDF inputs Audiveris renders pages internally, so preprocessing is skipped.
    # When skip_preprocessing=True the caller has already run enhance_image(), so we skip.
    omr_preprocessed_path: Optional[Path] = None
    omr_rescaled_path: Optional[Path] = None
    if not skip_preprocessing and safe_input_path.suffix.lower() in {'.png', '.jpg', '.jpeg'}:
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
        use_lang_constant: bool = True,
        no_curves: bool = False,
    ) -> tuple[int, str, str, Optional[Path]]:
        """Run Audiveris once for a given output dir and optional sheet number; return (exit_code, stdout, stderr, mxl_path).

        Parameters
        ----------
        use_lang_constant : When True (default), passes the OCR language constant determined
                            by _choose_ocr_lang().  Set to False to skip the language constant
                            entirely, letting Audiveris use its own defaults — useful as a
                            fallback for PDFs where lyric text causes recognition failures.
        no_curves         : When True, adds ``-step TEXTS`` to stop processing before the
                            CURVES (slur/tie) step.  Used as a last-resort retry when
                            Audiveris crashes specifically during CURVES detection.
                            The resulting MXL will have no slur/tie markings but will
                            retain all note data.
        """
        effective_input = input_path_override if input_path_override is not None else safe_input_path
        target_dir.mkdir(parents=True, exist_ok=True)
        cmd = [str(exe), '-batch']
        if use_lang_constant:
            cmd.extend(['-constant', f'org.audiveris.omr.text.Language.defaultSpecification={ocr_lang}'])
        if no_curves:
            # Stop at TEXTS step (one step before CURVES) to avoid the crash.
            # -export is still passed so Audiveris outputs whatever it has.
            cmd.extend(['-step', 'TEXTS'])
        cmd.extend(['-export'])
        if sheet_number is not None:
            cmd.extend(['-sheets', str(sheet_number)])
        cmd.extend(['-output', str(target_dir), str(effective_input)])
        return_code, stdout, stderr = run_subprocess_with_spinner(cmd, cwd=str(exe.parent), java_exe=java_exe)
        exported_file = find_first_musicxml_file(target_dir, effective_input.stem)
        return return_code, stdout, stderr, exported_file

    try:
        if exe is None:
            log_message('[audiveris] 未能生成或定位启动器。根据官方文档，应先完成源码构建，再使用生成的 Audiveris.bat 运行。', logging.WARNING)
            log_message('[audiveris] 请确认 audiveris-5.10.2 已构建成功，或重新运行程序让其自动准备启动器。', logging.WARNING)
            return None

        log_message(f'[audiveris] 调用启动器: {exe}')
        return_code, stdout, stderr, exported_file = invoke_audiveris(safe_output_dir)
        if return_code == 0 or exported_file is not None:
            if return_code != 0:
                log_message('[audiveris] 返回了非零退出码，但已成功导出 MusicXML，继续后续处理。', logging.WARNING)
            log_message('[audiveris] 执行完成。')
            _maybe_merge_mvt_files(safe_output_dir)
            _copy_preprocessed_ref(omr_preprocessed_path, safe_output_dir)
            return safe_output_dir

        page_count = get_pdf_page_count(safe_input_path)
        if safe_input_path.suffix.lower() == '.pdf' and page_count > 1:
            log_message(f'[audiveris] 检测到多页 PDF（共 {page_count} 页），正在按页回退处理，跳过识别失败的页面...', logging.WARNING)
            success_pages: list[int] = []
            for page_number in range(1, page_count + 1):
                page_output_dir = safe_output_dir / f'sheet_{page_number:03d}'
                safe_remove_tree(page_output_dir)
                page_output_dir.mkdir(parents=True, exist_ok=True)
                page_code, page_stdout, page_stderr, page_exported = invoke_audiveris(page_output_dir, sheet_number=page_number)
                if page_code == 0 or page_exported is not None:
                    success_pages.append(page_number)
                    log_message(f'[audiveris] 第 {page_number} 页已成功导出。')
                else:
                    page_combined = (page_stderr or '') + '\n' + (page_stdout or '')
                    no_staves_kw = ['Created scores: []', 'Sheet flagged as invalid', 'No good interline', 'Too few black pixels', 'no staves']
                    page_reason = '（图像过于模糊或无法检测五线谱）' if any(k.lower() in page_combined.lower() for k in no_staves_kw) else ''
                    detail_parts = [part.strip() for part in (page_stderr, page_stdout) if part and part.strip()]
                    detail = '\n'.join(detail_parts)
                    log_message(f'[audiveris] 第 {page_number} 页无法识别为有效五线谱，已跳过。{page_reason}{detail[:240]}', logging.WARNING)
            if success_pages:
                log_message(f'[audiveris] 已按页完成导出，成功页: {success_pages}')
                _maybe_merge_mvt_files(safe_output_dir)
                _copy_preprocessed_ref(omr_preprocessed_path, safe_output_dir)
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
                    log_message('[audiveris] 重试时返回非零退出码，但已成功导出 MusicXML，继续后续处理。', logging.WARNING)
                log_message('[audiveris] 使用原始图像重试成功。')
                _maybe_merge_mvt_files(safe_output_dir)
                _copy_preprocessed_ref(omr_preprocessed_path, safe_output_dir)
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
                'Audiveris 识别了五线谱，但在连音线处理步骤（CURVES）中崩溃导出失败。',
                logging.WARNING,
            )

            # ── 新增重试：禁用 CURVES 步骤（-step TEXTS），跳过连音线检测后重新识别 ──
            # 当 CURVES 步骤稳定崩溃时，退一步仅运行到 TEXTS 步骤，
            # 让 Audiveris 导出一个不含连音线的 MXL——音符数据完整，可用于后续处理。
            log_message(
                '  [CURVES 重试] 正在以跳过 CURVES 步骤的方式重新运行 Audiveris（-step TEXTS）…',
                logging.WARNING,
            )
            safe_remove_tree(safe_output_dir)
            nc_code, nc_out, nc_err, nc_exported = invoke_audiveris(
                safe_output_dir, no_curves=True
            )
            if nc_exported is not None:
                log_message('  [CURVES 重试] 跳过 CURVES 步骤后成功导出，连音线标记已略去。')
                _maybe_merge_mvt_files(safe_output_dir)
                _copy_preprocessed_ref(omr_preprocessed_path, safe_output_dir)
                return safe_output_dir
            log_message('  [CURVES 重试] 跳过 CURVES 步骤后仍未导出，尝试 CURVES 救援…', logging.WARNING)

            # ── CURVES 救援：尝试从输出目录中找到任何已生成的 MXL 并删除连音线 ──
            # Audiveris 完成音符识别后，连音线导出步骤崩溃时仍可能在 safe_output_dir
            # 中生成了部分 MXL 文件。删除其中格式错误的 <slur>/<tied> 元素可以
            # 令后续 music21 解析正常完成。
            _partial_mxl = find_first_musicxml_file(safe_output_dir, safe_input_path.stem)
            if _partial_mxl is None:
                # 也可能在嵌套子目录中
                for _sub in safe_output_dir.rglob('*.mxl'):
                    _partial_mxl = _sub
                    break
                if _partial_mxl is None:
                    for _sub in safe_output_dir.rglob('*.musicxml'):
                        _partial_mxl = _sub
                        break
            if _partial_mxl is not None:
                log_message(
                    f'  [CURVES 救援] 发现部分导出文件 {_partial_mxl.name}，'
                    '正在删除连音线元素以恢复可用乐谱…',
                    logging.WARNING,
                )
                try:
                    from ..music.transposer import strip_slurs_ties_from_mxl
                    strip_slurs_ties_from_mxl(_partial_mxl, backup=True)
                    log_message('  [CURVES 救援] 连音线已删除，继续后续处理。')
                    _maybe_merge_mvt_files(safe_output_dir)
                    _copy_preprocessed_ref(omr_preprocessed_path, safe_output_dir)
                    return safe_output_dir
                except Exception as _e:
                    log_message(f'  [CURVES 救援] 删除连音线失败: {_e}', logging.WARNING)
            return None

        # ── Retry 2: PDF general failure → re-run without OCR language constraint ────────
        # When lyric-heavy PDFs (e.g. vocal parts with dense text) cause OMR errors,
        # removing the OCR language constant lets Audiveris fall back to its built-in
        # defaults, reducing text-recognition interference on note detection.
        # By the time we reach here, CURVES/export errors have already returned None above,
        # so this block only triggers for genuine unclassified OMR failures on PDFs.
        if safe_input_path.suffix.lower() == '.pdf':
            log_message(
                'Audiveris PDF 识别失败，尝试禁用 OCR 语言约束重试（可能是歌词文本干扰）...',
                logging.WARNING,
            )
            safe_remove_tree(safe_output_dir)
            r3_code, r3_out, r3_err, r3_exported = invoke_audiveris(
                safe_output_dir, use_lang_constant=False
            )
            if r3_code == 0 or r3_exported is not None:
                if r3_code != 0:
                    log_message(
                        '禁用 OCR 语言重试时 Audiveris 返回非零退出码，但已成功导出 MusicXML，继续后续处理。',
                        logging.WARNING,
                    )
                log_message('禁用 OCR 语言约束重试成功。')
                _maybe_merge_mvt_files(safe_output_dir)
                _copy_preprocessed_ref(omr_preprocessed_path, safe_output_dir)
                return safe_output_dir
            combined = (r3_out or '') + '\n' + (r3_err or '')

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
                f'Audiveris 未检测到有效的五线谱结构。{sharpness_hint}',
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


# ══════════════════════════════════════════════════════════════════════════════
# 切片 OMR 管道（Staff-row slicing + per-row Audiveris + MXL merge）
# ══════════════════════════════════════════════════════════════════════════════

def _merge_mxl_files(
    mxl_paths: 'list[Path]',
    output_dir: Path,
    stem: str,
) -> 'Optional[Path]':
    """用 music21 将多个 MusicXML 文件的小节顺序合并为单一乐谱文件。

    每个 MXL 文件取第一个声部（Part 0），按顺序将其所有小节追加至输出乐谱。
    小节编号连续重新排列，避免 music21 误判重复。

    Parameters
    ----------
    mxl_paths  : 按谱行顺序排列的 MXL 路径列表。
    output_dir : 合并结果输出目录。
    stem       : 输出文件名前缀（不含扩展名）。

    Returns
    -------
    Path  合并后的 musicxml 路径；失败时返回 None。
    """
    try:
        from music21 import converter as m21conv, stream as m21stream

        merged_part = m21stream.Part()
        measure_number = 1
        for mxl_path in mxl_paths:
            try:
                score = m21conv.parse(str(mxl_path))
                part = score.parts[0] if score.parts else score.flatten()
                for m in list(part.getElementsByClass('Measure')):
                    m.number = measure_number
                    measure_number += 1
                    merged_part.append(m)
            except Exception as exc:
                log_message(f'  [切片合并] 跳过 {mxl_path.name}: {exc}', logging.WARNING)

        if not list(merged_part.getElementsByClass('Measure')):
            return None

        merged_score = m21stream.Score()
        merged_score.append(merged_part)
        out_path = output_dir / f'{stem}_sliced_merged.musicxml'
        merged_score.write('musicxml', fp=str(out_path))
        log_message(
            f'  [切片合并] 已合并 {len(mxl_paths)} 个谱行 → {out_path.name}'
            f'（共 {measure_number - 1} 小节）'
        )
        return out_path
    except Exception as exc:
        log_message(f'  [切片合并] MXL 合并失败: {exc}', logging.WARNING)
        return None


def run_audiveris_sliced_batch(
    input_path: Path,
    output_dir: Optional[Path] = None,
) -> Optional[Path]:
    """切片增强版 Audiveris 批处理：双路预处理 → 切片 → 逐行旋转校正 → 逐行识别 → 合并。

    双路设计
    --------
    • **编辑器参考图路径**（重视可读性）：
      完整预处理（梯度修正 → 旋转校正 → 白边裁剪 → 去噪锐化 → SR → 降采样），
      结果保存为 ``_omr_reference.png``，供 editor-workspace 展示。

    • **Audiveris 输入路径**（重视识别率）：
      几何轻量预处理（梯度修正 → 旋转校正 → 白边裁剪，保留 RGB，不做去噪/锐化），
      让 Audiveris 自带的内部二值化器处理高质量原始信号。
      在此图像上做切片，每片再做旋转校正后送入 Audiveris。

    7 步流程
    --------
    1. 完整预处理 → ``_omr_reference.png``（编辑器参考）。
    2. 几何轻量预处理 → ``_geo_input.png``（Audiveris 用，RGB）。
    3. 在 **几何轻量图** 上切片（保持 cv2 线检测准确）。
    4. 若切片不足 2 行，对几何轻量图整张识别。
    5. 逐切片旋转校正。
    6. 对每片调用 ``run_audiveris_batch(..., skip_preprocessing=True)``。
    7. 收集 MXL → 合并；全部失败 → 回退整图识别。

    对 PDF 输入及非 PNG/JPG 输入，直接回退 ``run_audiveris_batch()``。
    """
    if input_path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        return run_audiveris_batch(input_path, output_dir)

    if output_dir is None:
        output_dir = get_app_base_dir() / 'audiveris-output'
    if any(ord(ch) > 127 for ch in str(output_dir)):
        safe_name = build_safe_ascii_name(output_dir.name, fallback='audiveris_sliced')
        tmp_dir = Path(tempfile.gettempdir()) / f'{safe_name}_{hashlib.sha1(str(output_dir).encode("utf-8")).hexdigest()[:8]}'
        tmp_dir.mkdir(parents=True, exist_ok=True)
        log_message(f'  [切片OMR] 输出目录含非 ASCII 字符，改用临时目录 {tmp_dir}')
        output_dir = tmp_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    from ..image.image_preprocess import (
        preprocess_image_for_omr,
        preprocess_geometry_for_omr,
    )
    from ..image.staff_slicer import (
        slice_staff_rows,
        correct_slice_rotation,
    )

    # ── Step A: 完整预处理 → 编辑器参考图 ────────────────────────────────
    full_preprocessed = preprocess_image_for_omr(input_path, output_dir)
    ref_dest = output_dir / '_omr_reference.png'
    if full_preprocessed is not None:
        try:
            shutil.copy2(str(full_preprocessed), str(ref_dest))
        except OSError:
            pass
    log_message('  [切片OMR] 编辑器参考图已生成（完整预处理）。')

    # ── Step B: 几何预处理 → Audiveris 输入 ──────────────────────────────
    geo_path = preprocess_geometry_for_omr(input_path, output_dir)
    if geo_path is None:
        log_message('  [切片OMR] 几何预处理失败，使用原图作为 Audiveris 输入。', logging.WARNING)
        geo_path = input_path

    # ── Step C: 在几何图上切片 ────────────────────────────────────────────
    slice_dir = output_dir / '_slices'
    slices = slice_staff_rows(geo_path, slice_dir)

    if len(slices) <= 1:
        log_message('  [切片OMR] 未检测到可信的多谱行系统，对原始图像整张识别（内部完整预处理）。')
        return run_audiveris_batch(input_path, output_dir, skip_preprocessing=False)

    log_message(f'  [切片OMR] 检测到 {len(slices)} 个谱行，逐行旋转校正后送入 Audiveris...')

    # ── Step D: 逐切片旋转校正 + Audiveris ───────────────────────────────
    collected_mxl: list[Path] = []
    for i, slice_path in enumerate(slices):
        corrected = correct_slice_rotation(slice_path, slice_dir)
        effective_slice = corrected if corrected is not None else slice_path

        log_message(f'  [切片OMR] 处理第 {i + 1}/{len(slices)} 行: {effective_slice.name}')
        row_out_dir = output_dir / f'_row_{i:03d}'
        result_dir = run_audiveris_batch(
            effective_slice, row_out_dir, skip_preprocessing=True
        )
        if result_dir is not None:
            mxl = find_first_musicxml_file(result_dir, effective_slice.stem)
            if mxl is not None:
                collected_mxl.append(mxl)
                log_message(f'  [切片OMR] 第 {i + 1} 行识别成功: {mxl.name}')
            else:
                log_message(
                    f'  [切片OMR] 第 {i + 1} 行：Audiveris 完成但未找到 MXL，跳过。',
                    logging.WARNING,
                )
        else:
            log_message(f'  [切片OMR] 第 {i + 1} 行识别失败，跳过。', logging.WARNING)

    if not collected_mxl:
        log_message('  [切片OMR] 所有谱行识别均失败，回退原始图像整张识别。', logging.WARNING)
        return run_audiveris_batch(input_path, output_dir, skip_preprocessing=False)

    if len(collected_mxl) == 1:
        return collected_mxl[0].parent

    # ── Step E: 合并所有 MXL ──────────────────────────────────────────────
    merged = _merge_mxl_files(collected_mxl, output_dir, input_path.stem)
    if merged is None:
        log_message('  [切片OMR] MXL 合并失败，回退原始图像整张识别。', logging.WARNING)
        return run_audiveris_batch(input_path, output_dir, skip_preprocessing=False)

    return output_dir
