# core/pipeline.py — 批处理管道与入口点
# 拆分自 convert.py
import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

from .audiveris_runner import run_audiveris_batch
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

    Parameters
    ----------
    source_file:          Input score file (PDF / PNG / JPG).
    file_temp_dir:        Per-job temporary directory.
    output_pdf:           Destination jianpu PDF path.
    output_midi:          Destination MIDI path, or None to skip MIDI generation.
    engine:               OMR engine to use (AUDIVERIS only in v0.1.3).
    editor_workspace_dir: When provided, intermediate .jianpu.txt and source file
                          are preserved there for manual proofreading.
    """

    if engine is OMREngine.OEMER:
        omr_out = run_oemer_batch(source_file, output_dir=file_temp_dir)
        engine_label = 'oemer'
    else:
        omr_out = run_audiveris_batch(source_file, output_dir=file_temp_dir)
        engine_label = 'Audiveris'

    if omr_out is None:
        log_message(
            f'  ✗ {engine_label} 处理失败，跳过 {source_file.name}。\n'
            '    → 可能原因：Java/Audiveris 未正确安装，或乐谱格式不受支持\n'
            '    → 解决方案：确认 audiveris-runtime 目录存在，或尝试切换到 oemer 引擎',
            logging.WARNING,
        )
        return False

    mxl_file = find_first_musicxml_file(omr_out, source_file.stem)
    if mxl_file is None:
        log_message(
            f'  ✗ 未找到 {engine_label} 输出的 MXL 文件，跳过 {source_file.name}。\n'
            '    → 可能原因：乐谱无法被识别（图像质量过低或版式过于复杂）\n'
            '    → 解决方案：尝试使用更高分辨率的扫描件，或手动检查 omr-temp 目录',
            logging.WARNING,
        )
        return False

    # Prefer the preprocessed (enhanced/deskewed) image saved by audiveris_runner as the
    # editor workspace reference.  Falls back to the original source file for PDFs or when
    # preprocessing was skipped.
    _ref_candidate = omr_out / '_preprocessed_ref.png'
    effective_source = _ref_candidate if _ref_candidate.exists() else source_file

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

    # v0.1.3: Audiveris only — oemer engine entry point is closed pending full implementation.
    engine = config.omr_engine
    if engine is OMREngine.OEMER:
        log_message(
            '[引擎选择] oemer 引擎在 v0.1.3 中尚未实装，已自动回退到 Audiveris。',
            logging.WARNING,
        )
        engine = OMREngine.AUDIVERIS
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
