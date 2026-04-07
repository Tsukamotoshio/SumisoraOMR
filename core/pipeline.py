# core/pipeline.py — 批处理管道与入口点
# 拆分自 convert.py
import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from .audiveris_runner import run_audiveris_batch, run_audiveris_sliced_batch
from .config import (
    APP_VERSION,
    LOGGER,
    AppConfig,
    ConversionSummary,
    OMREngine,
)
from .oemer_runner import check_oemer_available, run_oemer_batch
from .renderer import generate_jianpu_pdf_from_mxl
from .utils import (
    build_runtime_paths,
    cleanup_output_directory,
    collect_duplicate_names,
    confirm_skip_all_existing,
    find_first_musicxml_file,
    get_app_base_dir,
    is_supported_score_file,
    load_conversion_history,
    log_message,
    print_conversion_summary,
    safe_remove_tree,
    save_conversion_history,
    setup_logging,
    update_conversion_history,
)


def process_single_input_to_jianpu(
    source_file: Path,
    file_temp_dir: Path,
    output_pdf: Path,
    output_midi: Optional[Path],
    engine: OMREngine = OMREngine.AUDIVERIS,
    editor_workspace_dir: Optional[Path] = None,
) -> bool:
    """Process one input file through the chosen OMR engine → MXL → jianpu PDF.

    Routing strategy
    ----------------
    AUTO (default)  :  PDF → Audiveris;  image (PNG/JPG) → Oemer.
    AUDIVERIS       :  Always use Audiveris (PDF and images).
    OEMER           :  Always use Oemer (images only; PDF is not supported by Oemer).

    For all image inputs a display-friendly reference image (white-border crop +
    rotation correction + light contrast, RGB color) is saved to the editor
    workspace so the user can proofread with a clear, readable image.


    Parameters
    ----------
    source_file:          Input score file (PDF / PNG / JPG).
    file_temp_dir:        Per-job temporary directory.
    output_pdf:           Destination jianpu PDF path.
    output_midi:          Destination MIDI path, or None to skip MIDI generation.
    engine:               OMR engine to use.
    editor_workspace_dir: When provided, intermediate .jianpu.txt and source file
                          are preserved there for manual proofreading.
    """
    _IS_IMAGE = source_file.suffix.lower() in {'.png', '.jpg', '.jpeg'}
    _is_auto = engine is OMREngine.AUTO

    # AUTO 路由策略：图片 → Oemer（深度学习，对拍摄/扫描图像更健壮）；
    #                PDF  → Audiveris（规则引擎，PDF 矢量精度高）；
    #                任一失败时自动回退另一引擎。
    if _is_auto:
        if _IS_IMAGE:
            effective_engine = OMREngine.OEMER
            log_message('  [自动路由] 图片 → Oemer（失败时回退 Audiveris）')
        else:
            effective_engine = OMREngine.AUDIVERIS
            log_message('  [自动路由] PDF → Audiveris（失败时回退 Oemer）')
    else:
        effective_engine = engine

    # ── Oemer (images) ────────────────────────────────────────────────────────
    if effective_engine is OMREngine.OEMER:
        if not _IS_IMAGE:
            log_message(
                f'  ✗ Oemer 不支持 PDF 格式，跳过 {source_file.name}。\n'
                '    → 请使用 Audiveris 引擎处理 PDF 文件，或改用图片格式输入。',
                logging.WARNING,
            )
            return False
        omr_out = run_oemer_batch(source_file, output_dir=file_temp_dir)
        engine_label = 'Oemer'

    # ── Audiveris (PDF and images) ────────────────────────────────────────────

    else:
        # 对栅格图像启用谱行切片识别以降低引擎全局布局压力；PDF/其他格式自动回退整图处理
        omr_out = run_audiveris_sliced_batch(source_file, output_dir=file_temp_dir)
        engine_label = 'Audiveris'

    # ── Error / fallback ────────────────────────────────────────────────────────
    if omr_out is None:
        if _is_auto and effective_engine is OMREngine.OEMER:
            # AUTO 图片：Oemer 失败 → 回退 Audiveris
            log_message('  [自动回退] Oemer 识别失败，尝试 Audiveris…', logging.WARNING)
            omr_out = run_audiveris_sliced_batch(source_file, output_dir=file_temp_dir)
            engine_label = 'Audiveris (Oemer 回退)'
        elif effective_engine is OMREngine.OEMER:
            # 显式 Oemer 失败，不回退
            log_message(f'  ✗ Oemer 处理失败，跳过 {source_file.name}。', logging.WARNING)
            return False
        else:
            # Audiveris 失败（AUTO PDF 或显式 Audiveris）→ 回退 Oemer
            log_message('  [自动回退] Audiveris 识别失败，尝试 Oemer…', logging.WARNING)
            omr_out = run_oemer_batch(source_file, output_dir=file_temp_dir)
            engine_label = 'Oemer (Audiveris 回退)'
        if omr_out is None:
            log_message(f'  ✗ 两个引擎均处理失败，跳过 {source_file.name}。', logging.WARNING)
            return False

    # ── Resolve MusicXML path ──────────────────────────────────────────────────
    mxl_file = find_first_musicxml_file(omr_out, source_file.stem)

    if mxl_file is None:
        log_message(
            f'  ✗ 未找到 {engine_label} 输出的 MXL 文件，跳过 {source_file.name}。\n'
            '    → 可能原因：乐谱无法被识别（图像质量过低或版式过于复杂）\n'
            '    → 解决方案：尝试使用更高分辨率的扫描件，或手动检查 omr-temp 目录',

            logging.WARNING,
        )
        return False

    # For image inputs, use the OMR-preprocessed image saved by run_audiveris_sliced_batch
    # as the editor-workspace reference (shows exactly what Audiveris saw: gradient-corrected,
    # deskewed, cropped, denoised).  Falls back to create_display_reference() then raw file.
    # NOTE: reference image is always saved in file_temp_dir (the output_dir passed to
    # run_audiveris_sliced_batch), regardless of where the MXL result ends up.
    if _IS_IMAGE:
        omr_ref = file_temp_dir / '_omr_reference.png'
        if not omr_ref.exists():
            omr_ref = omr_out / '_omr_reference.png'  # fallback: try the returned dir
        if omr_ref.exists():
            effective_source = omr_ref
        else:
            from .image_preprocess import create_display_reference
            effective_source = create_display_reference(source_file, file_temp_dir) or source_file
    else:
        effective_source = source_file


    return generate_jianpu_pdf_from_mxl(
        mxl_file,
        output_pdf,
        file_temp_dir,
        output_midi,
        preferred_title=source_file.stem,
        source_path=effective_source,
        editor_workspace_dir=editor_workspace_dir,
    )


def process_single_pdf_to_jianpu(
    pdf_file: Path,
    file_temp_dir: Path,
    output_pdf: Path,
    output_midi: Optional[Path],
    engine: OMREngine = OMREngine.AUDIVERIS,
    editor_workspace_dir: Optional[Path] = None,
) -> bool:
    """Backward-compatible alias for process_single_input_to_jianpu."""
    return process_single_input_to_jianpu(
        pdf_file, file_temp_dir, output_pdf, output_midi, engine, editor_workspace_dir
    )


def _prompt(question: str, *, valid_yes: tuple[str, ...] = ('Y',),
            valid_no: tuple[str, ...] = ('N',)) -> bool | None:
    """Print *question*, read a line, handle H/? (help) and Q (quit).

    Returns True for yes, False for no.
    Raises SystemExit on Q.
    Re-prompts on unrecognised input or H/? (after printing help).
    """
    while True:
        try:
            raw = input(question + ' ').strip().upper()
        except (EOFError, KeyboardInterrupt):
            raise
        if raw in valid_yes:
            return True
        if raw in valid_no:
            return False
        if raw in ('Q', 'QUIT', 'EXIT'):
            log_message('\n已退出。', logging.WARNING)
            raise SystemExit(0)
        if raw in ('H', '?', 'HELP'):
            _print_help()
            continue
        log_message(f'  → 请输入 Y 或 N（输入 H 查看帮助，输入 Q 退出）。')


def _print_help() -> None:
    """Print an in-app help/usage guide."""
    log_message(
        '\n'
        '╔══════════════════════════════════════════════════════╗\n'
        '║        简谱转换工具  操作指引                        ║\n'
        '╠══════════════════════════════════════════════════════╣\n'
        '║  Y / N      确认 / 拒绝当前操作                      ║\n'
        '║  H 或 ?     显示本帮助信息                           ║\n'
        '║  Q          退出程序                                 ║\n'
        '╠══════════════════════════════════════════════════════╣\n'
        '║  使用步骤：                                          ║\n'
        '║  1. 将五线谱文件放入 Input 文件夹                    ║\n'
        '║     支持格式：PDF  PNG  JPG / JPEG                   ║\n'
        '║  2. 运行本程序，按提示回答 Y/N                       ║\n'
        '║  3. 转换结果（简谱 PDF / MIDI）保存在 Output 文件夹  ║\n'
        '║  4. 日志保存在 logs 文件夹                           ║\n'
        '╠══════════════════════════════════════════════════════╣\n'
        '║  常见问题：                                          ║\n'
        '║  • 转换失败 → 检查 logs 目录中的日志文件             ║\n'
        '║  • 没有输出 → 确认 Input 文件夹中有支持的文件        ║\n'
        '║  • 程序缓慢 → 多页 PDF 识别需要数分钟，请耐心等待    ║\n'
        '╚══════════════════════════════════════════════════════╝'
    )


def process_bulk_input_to_jianpu(
    config: AppConfig | None = None,
    editor_workspace_dir: Optional[Path] = None,
) -> ConversionSummary:
    """
    Batch-process all supported files in Input/.
    Prompts the user to confirm and choose MIDI output, skips existing files, and cleans up temp files.

    If *editor_workspace_dir* is provided, each successfully converted score will
    have its .jianpu.txt and source file preserved there for the editor workflow.
    """
    config = config or AppConfig()
    script_dir = get_app_base_dir()
    input_dir, output_dir, temp_dir = build_runtime_paths(script_dir, config)
    history = load_conversion_history(script_dir)
    summary = ConversionSummary()

    if not input_dir.exists() or not input_dir.is_dir():
        input_dir.mkdir(parents=True, exist_ok=True)
        log_message(
            '\n[提示] Input 文件夹已自动创建。\n'
            '  → 请将 PDF / JPG / PNG 乐谱文件放入 Input 文件夹，然后重新运行程序。\n'
            f'  → 文件夹位置：{input_dir}',
            logging.WARNING,
        )
        return summary

    source_files = sorted([p for p in input_dir.iterdir() if is_supported_score_file(p)])
    if not source_files:
        log_message(
            '\n[提示] Input 文件夹中未找到可处理的文件。\n'
            '  → 支持的格式：.pdf  .png  .jpg  .jpeg\n'
            f'  → 请将文件放入：{input_dir}',
            logging.WARNING,
        )
        return summary

    summary.total = len(source_files)
    log_message('\n待转换乐谱文件列表：')
    for source_file in source_files:
        log_message(f'  - {source_file.name}')
    log_message(f'共 {len(source_files)} 个文件  （输入 H 查看帮助）')

    try:
        if not _prompt('是否转换以上所有乐谱为简谱 PDF？（Y/N）'):
            log_message('已取消转换。')
            return summary

        generate_midi = _prompt('是否同时生成 MIDI 文件？（Y/N）')
    except SystemExit:
        return summary

    engine = config.omr_engine
    if engine is OMREngine.AUTO:
        log_message('[引擎选择] 自动模式（PDF → Audiveris，图片 → Oemer）。')
    else:
        log_message(f'[引擎选择] 使用 {engine.value.capitalize()} 引擎。')



    output_dir.mkdir(parents=True, exist_ok=True)
    duplicate_names = collect_duplicate_names(source_files, output_dir, generate_midi, history)
    skip_all_existing = confirm_skip_all_existing(duplicate_names)

    safe_remove_tree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    for index, source_file in enumerate(source_files, start=1):
        log_message(f'\n[{index}/{summary.total}] 正在处理：{source_file.name}')
        output_pdf = output_dir / f'{source_file.stem}.jianpu.pdf'
        output_midi = output_dir / f'{source_file.stem}.mid' if generate_midi else None

        if skip_all_existing and source_file.name in duplicate_names:
            log_message(f'  已跳过（输出文件已存在）：{source_file.name}')
            summary.skipped += 1
            summary.skipped_files.append(source_file.name)
            continue

        # File will be (re-)processed — remove any stale editor-workspace entries for
        # this title so the workspace always reflects the latest recognition result.
        # Files that were *skipped* above are intentionally left untouched.
        if editor_workspace_dir is not None and editor_workspace_dir.exists():
            safe_title = source_file.stem
            for _ch in r'\/:*?"<>|':
                safe_title = safe_title.replace(_ch, '_')
            for _stale in editor_workspace_dir.glob(f'{safe_title}.*'):
                try:
                    _stale.unlink()
                except OSError:
                    pass

        job_token = hashlib.sha1(str(source_file).encode('utf-8', errors='ignore')).hexdigest()[:8]
        file_temp_dir = temp_dir / f'job_{index:03d}_{job_token}'
        safe_remove_tree(file_temp_dir)
        file_temp_dir.mkdir(parents=True, exist_ok=True)

        if process_single_input_to_jianpu(
            source_file, file_temp_dir, output_pdf, output_midi, engine,
            editor_workspace_dir=editor_workspace_dir,
        ):
            log_message(f'  ✓ 已生成简谱 PDF：{output_pdf.name}')
            summary.success += 1
            summary.generated_pdfs.append(output_pdf.name)
            update_conversion_history(history, source_file, output_pdf, output_midi)
            save_conversion_history(script_dir, history)
        else:
            summary.failed += 1
            summary.failed_files.append(source_file.name)
            log_message(
                f'  ✗ 转换失败：{source_file.name}\n'
                '    → 可能原因：乐谱扫描质量较低、格式不支持或引擎异常\n'
                f'    → 请查阅日志文件了解详情（logs 文件夹）',
                logging.WARNING,
            )

    log_message('\n正在清理临时文件...')
    safe_remove_tree(temp_dir)
    cleanup_output_directory(output_dir, generate_midi)
    print_conversion_summary(summary, generate_midi, output_dir)
    return summary


def process_bulk_pdf_to_jianpu(config: AppConfig | None = None) -> ConversionSummary:
    """Backward-compatible alias for process_bulk_input_to_jianpu."""
    return process_bulk_input_to_jianpu(config)


def wait_for_exit_key(prompt: str = '按任意键退出...') -> None:
    """Wait for a keypress before exiting (msvcrt on Windows, falls back to input())."""
    try:
        import msvcrt

        print(prompt, end='', flush=True)
        msvcrt.getwch()
        print()
    except Exception:
        try:
            input(prompt)
        except EOFError:
            print('\n程序已退出。')


def main() -> None:
    """Entry point: launch the Rich TUI state machine."""
    from .tui import main_tui
    setup_logging(get_app_base_dir())
    main_tui(AppConfig())
