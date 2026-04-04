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
) -> bool:
    """Process one input file through the chosen OMR engine → MXL → jianpu PDF.

    Parameters
    ----------
    source_file:   Input score file (PDF / PNG / JPG).
    file_temp_dir: Per-job temporary directory.
    output_pdf:    Destination jianpu PDF path.
    output_midi:   Destination MIDI path, or None to skip MIDI generation.
    engine:        OMR engine to use (AUDIVERIS or OEMER).
    """
    engine_name = engine.value.upper()

    if engine is OMREngine.OEMER:
        omr_out = run_oemer_batch(source_file, output_dir=file_temp_dir)
        engine_label = 'oemer'
    else:
        omr_out = run_audiveris_batch(source_file, output_dir=file_temp_dir)
        engine_label = 'Audiveris'

    if omr_out is None:
        log_message(f'跳过 {source_file.name}，{engine_label} 处理失败。', logging.WARNING)
        return False

    mxl_file = find_first_musicxml_file(omr_out, source_file.stem)
    if mxl_file is None:
        log_message(f'未找到 {engine_label} 输出的 MXL 文件，跳过 {source_file.name}。', logging.WARNING)
        return False

    return generate_jianpu_pdf_from_mxl(
        mxl_file,
        output_pdf,
        file_temp_dir,
        output_midi,
        preferred_title=source_file.stem,
        source_path=source_file,
    )


def process_single_pdf_to_jianpu(
    pdf_file: Path,
    file_temp_dir: Path,
    output_pdf: Path,
    output_midi: Optional[Path],
    engine: OMREngine = OMREngine.AUDIVERIS,
) -> bool:
    """Backward-compatible alias for process_single_input_to_jianpu."""
    return process_single_input_to_jianpu(pdf_file, file_temp_dir, output_pdf, output_midi, engine)


def process_bulk_input_to_jianpu(config: AppConfig | None = None) -> ConversionSummary:
    """
    Batch-process all supported files in Input/.
    Prompts the user to confirm and choose MIDI output, skips existing files, and cleans up temp files.
    """
    config = config or AppConfig()
    script_dir = get_app_base_dir()
    input_dir, output_dir, temp_dir = build_runtime_paths(script_dir, config)
    history = load_conversion_history(script_dir)
    summary = ConversionSummary()

    if not input_dir.exists() or not input_dir.is_dir():
        input_dir.mkdir(parents=True, exist_ok=True)
        log_message('已自动创建 Input 文件夹，请将 PDF/JPG/PNG 乐谱文件放入后重新运行。', logging.WARNING)
        return summary

    source_files = sorted([p for p in input_dir.iterdir() if is_supported_score_file(p)])
    if not source_files:
        log_message('Input 文件夹中未找到可处理的 PDF/JPG/PNG 文件。', logging.WARNING)
        return summary

    summary.total = len(source_files)
    log_message('待转换乐谱文件列表:')
    for source_file in source_files:
        log_message(f'  - {source_file.name}')
    log_message(f'总文件数: {len(source_files)}')

    answer = input('是否转换以上所有 PDF/JPG/PNG 乐谱为简谱 PDF?（Y/N） ').strip().upper()
    if answer != 'Y':
        log_message('已取消转换。')
        return summary

    midi_answer = input('是否同时生成 MIDI 文件?（Y/N） ').strip().upper()
    generate_midi = midi_answer == 'Y'

    # ── 引擎选择（experimental-v0.2.0 新增）──────────────────────────────
    engine = config.omr_engine
    if engine is OMREngine.AUDIVERIS:
        log_message('[引擎选择] 可选 OMR 引擎：1=Audiveris（默认）  2=oemer（实验性）')
        engine_answer = input('请输入引擎编号（直接回车使用默认 Audiveris）：').strip()
        if engine_answer == '2':
            if check_oemer_available():
                engine = OMREngine.OEMER
                log_message('[引擎选择] 使用 oemer 引擎。')
            else:
                log_message('[引擎选择] oemer 不可用，回退到 Audiveris。', logging.WARNING)
                engine = OMREngine.AUDIVERIS
        else:
            log_message('[引擎选择] 使用 Audiveris 引擎。')
    else:
        log_message(f'[引擎选择] 已由配置指定: {engine.value}')

    output_dir.mkdir(parents=True, exist_ok=True)
    duplicate_names = collect_duplicate_names(source_files, output_dir, generate_midi, history)
    skip_all_existing = confirm_skip_all_existing(duplicate_names)

    safe_remove_tree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    for index, source_file in enumerate(source_files, start=1):
        log_message(f'\n=== 正在处理: {source_file.name} ===')
        output_pdf = output_dir / f'{source_file.stem}.jianpu.pdf'
        output_midi = output_dir / f'{source_file.stem}.mid' if generate_midi else None

        if skip_all_existing and source_file.name in duplicate_names:
            log_message(f'已跳过: {source_file.name}')
            summary.skipped += 1
            summary.skipped_files.append(source_file.name)
            continue

        job_token = hashlib.sha1(str(source_file).encode('utf-8', errors='ignore')).hexdigest()[:8]
        file_temp_dir = temp_dir / f'job_{index:03d}_{job_token}'
        safe_remove_tree(file_temp_dir)
        file_temp_dir.mkdir(parents=True, exist_ok=True)

        if process_single_input_to_jianpu(source_file, file_temp_dir, output_pdf, output_midi, engine):
            log_message(f'已生成简谱 PDF: {output_pdf.name}')
            summary.success += 1
            summary.generated_pdfs.append(output_pdf.name)
            update_conversion_history(history, source_file, output_pdf, output_midi)
            save_conversion_history(script_dir, history)
        else:
            summary.failed += 1
            summary.failed_files.append(source_file.name)
            log_message(f'生成简谱 PDF 失败: {source_file.name}', logging.WARNING)

    log_message('\n开始删除中间文件...')
    safe_remove_tree(temp_dir)
    cleanup_output_directory(output_dir, generate_midi)
    print_conversion_summary(summary, generate_midi, output_dir)
    return summary


def process_bulk_pdf_to_jianpu(config: AppConfig | None = None) -> ConversionSummary:
    """Backward-compatible alias for process_bulk_input_to_jianpu."""
    return process_bulk_input_to_jianpu(config)


def wait_for_exit_key(prompt: str = 'Conversion complete. Press any key to exit...') -> None:
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
    """Entry point: initialise logging, run batch conversion, wait for keypress before exit."""
    setup_logging(get_app_base_dir())
    log_message('===========================================')
    log_message(f'批量乐谱(PDF/JPG/PNG) -> 简谱 PDF 转换工具 v{APP_VERSION}')
    log_message('版权所有 © 2026 Tsukamotoshio  保留所有权利')
    log_message('[实验版] 支持双 OMR 引擎：Audiveris / oemer')
    log_message('===========================================')
    try:
        process_bulk_input_to_jianpu(AppConfig())
    except EOFError:
        log_message('\n输入已结束，程序退出。', logging.WARNING)
        return
    except KeyboardInterrupt:
        log_message('\n已取消，程序退出。', logging.WARNING)
        return

    wait_for_exit_key()
