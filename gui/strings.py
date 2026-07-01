# gui/strings.py — Centralized bilingual UI text for the Flet GUI.
#
# All user-facing literals live here so gui/pages and gui/components contain
# layout/logic, not text. `set_language()` switches the active language;
# `t()` always looks up the current one, so any code path that calls `t()`
# at render time (dialogs, SnackBars, log lines built inside handlers) reflects
# the active language automatically with no extra wiring.
#
# Widgets whose text is set once at construction time and never re-evaluated
# (persistent tooltips, headers, dropdown option lists, etc.) do NOT update
# automatically — those pages implement a `retranslate()` method that
# re-assigns such properties and is called when Event.LANGUAGE_CHANGED fires.
#
# Usage:
#   from ..strings import t            # from gui/pages/*.py or gui/components/*.py
#   from gui.strings import t          # from app.py
#   ft.Text(t("landing.button_start_convert"))
#   ft.Text(t("common.loaded_file", name=path.name))

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    # ── common / shared across pages ────────────────────────────────────────
    "common.cancel": {"zh": "取消", "en": "Cancel"},
    "common.delete": {"zh": "删除", "en": "Delete"},
    "common.close": {"zh": "关闭", "en": "Close"},
    "common.confirm": {"zh": "确认", "en": "Confirm"},
    "common.open_score": {"zh": "打开乐谱", "en": "Open Score"},
    "common.jianpu_edit": {"zh": "简谱编辑", "en": "Edit Jianpu"},
    "common.tooltip_play_midi": {"zh": "播放 MIDI", "en": "Play MIDI"},
    "common.tooltip_refresh_list": {"zh": "刷新列表", "en": "Refresh List"},
    "common.tooltip_toggle_select_all": {"zh": "全选 / 全不选", "en": "Select All / Deselect All"},
    "common.tooltip_delete_file": {"zh": "删除此文件", "en": "Delete This File"},
    "common.tooltip_zoom_in": {"zh": "放大", "en": "Zoom In"},
    "common.tooltip_zoom_out": {"zh": "缩小", "en": "Zoom Out"},
    "common.empty_score_hint": {"zh": "请先识别乐谱文件\n或点击刷新按钮", "en": "Recognize a score first\nor click the refresh button"},
    "common.loaded_file": {"zh": "已加载: {name}", "en": "Loaded: {name}"},
    "common.export_failed_exc": {"zh": "导出失败: {exc}", "en": "Export failed: {exc}"},
    "common.open_dir_failed": {"zh": "无法打开目录: {exc}", "en": "Failed to open directory: {exc}"},
    "common.midi_open_failed": {"zh": "无法打开 MIDI 文件: {exc}", "en": "Failed to open MIDI file: {exc}"},
    "common.midi_not_found": {"zh": "未找到 MIDI 文件：{name}", "en": "MIDI file not found: {name}"},

    # ── app.py ───────────────────────────────────────────────────────────────
    # NOTE: nav-rail labels sit under an icon in a ~80px-wide column, so the
    # English text must stay short (single word) to avoid ugly wrapping.
    "app.nav_landing": {"zh": "乐谱识别", "en": "Recognize"},
    "app.nav_editor": {"zh": "简谱预览", "en": "Jianpu"},
    "app.nav_score_preview": {"zh": "五线谱预览", "en": "Staff"},
    "app.nav_about": {"zh": "关于", "en": "About"},
    "app.window_title": {"zh": "SumisoraOMR", "en": "SumisoraOMR"},
    "app.tooltip_hide_label": {"zh": "隐藏标签", "en": "Hide Labels"},
    "app.tooltip_show_label": {"zh": "显示标签", "en": "Show Labels"},
    "app.tooltip_theme_toggle": {"zh": "切换明暗主题", "en": "Toggle Light / Dark Theme"},
    "app.tooltip_language_toggle": {"zh": "切换语言 / Switch Language", "en": "Switch Language / 切换语言"},
    "app.tooltip_language_disabled": {"zh": "请先退出简谱编辑页面再切换语言", "en": "Leave the Jianpu editor page before switching language"},
    "app.tooltip_maximize": {"zh": "最大化 / 还原", "en": "Maximize / Restore"},
    "app.tooltip_minimize": {"zh": "最小化", "en": "Minimize"},
    "app.tooltip_close": {"zh": "关闭", "en": "Close"},
    "app.title_bar": {"zh": "SumisoraOMR  v{version}", "en": "SumisoraOMR  v{version}"},
    "app.snack_error": {"zh": "错误: {message}", "en": "Error: {message}"},
    "app.snack_done_default": {"zh": "完成", "en": "Done"},
    "app.single_instance_title": {"zh": "Sumisora OMR", "en": "Sumisora OMR"},
    "app.single_instance_body": {"zh": "Sumisora OMR 已在运行中。\n\n请查看任务栏。", "en": "Sumisora OMR is already running.\n\nCheck the taskbar."},

    # ── gui/pages/about_page.py ─────────────────────────────────────────────
    "about.subtitle": {"zh": "五线谱识别与简谱生成工具", "en": "Staff Notation Recognition & Jianpu Generation Tool"},
    "about.author_label": {"zh": "作者", "en": "Author"},
    "about.homepage_label": {"zh": "项目主页", "en": "Project Homepage"},
    "about.license_label": {"zh": "开源许可证 (AGPL-3.0)", "en": "Open Source License (AGPL-3.0)"},
    "about.license_text": {
        "zh": (
            "GNU Affero General Public License v3.0\n\n"
            "Copyright (c) 2026 Tsukamotoshio\n\n"
            "本程序是自由软件：您可以根据自由软件基金会发布的 GNU Affero 通用公共许可证\n"
            "（第 3 版或更高版本）重新分发或修改它。\n\n"
            "本程序的发布是希望它有用，但不提供任何担保；甚至不提供对适销性或特定用途\n"
            "适用性的隐含担保。详情请参阅 GNU Affero 通用公共许可证。\n\n"
            "您应该已收到 GNU Affero 通用公共许可证的副本；如未收到，请访问：\n"
            "https://www.gnu.org/licenses/agpl-3.0.html\n\n"
            "附加条款：若您通过网络向用户提供此程序的修改版本，您必须向所有与之交互的\n"
            "用户提供获取对应源代码的途径（AGPL-3.0 第 13 条）。\n\n"
            "完整许可证文本：https://github.com/Tsukamotoshio/SumisoraOMR/blob/main/LICENSE"
        ),
        "en": (
            "GNU Affero General Public License v3.0\n\n"
            "Copyright (c) 2026 Tsukamotoshio\n\n"
            "This program is free software: you can redistribute it and/or modify it\n"
            "under the terms of the GNU Affero General Public License as published by\n"
            "the Free Software Foundation, either version 3 of the License, or (at your\n"
            "option) any later version.\n\n"
            "This program is distributed in the hope that it will be useful, but WITHOUT\n"
            "ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS\n"
            "FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more\n"
            "details.\n\n"
            "You should have received a copy of the GNU Affero General Public License\n"
            "along with this program. If not, see:\n"
            "https://www.gnu.org/licenses/agpl-3.0.html\n\n"
            "Additional term: If you make a modified version of this program available to\n"
            "users over a network, you must give all interacting users access to the\n"
            "corresponding source code (AGPL-3.0 Section 13).\n\n"
            "Full license text: https://github.com/Tsukamotoshio/SumisoraOMR/blob/main/LICENSE"
        ),
    },

    # ── gui/pages/editor_page.py ────────────────────────────────────────────
    "editor.error_jianpu_ly_failed": {"zh": "jianpu-ly 转换失败，请确认安装", "en": "jianpu-ly conversion failed — please confirm it's installed"},
    "editor.error_lilypond_render_failed": {"zh": "LilyPond 渲染失败，请检查文件语法", "en": "LilyPond rendering failed — please check the file syntax"},
    "editor.error_pdf_unparseable": {"zh": "PDF 无法解析", "en": "Unable to parse the PDF"},
    "editor.error_render_exc": {"zh": "渲染出错: {exc}", "en": "Render error: {exc}"},
    "editor.tooltip_re_render": {"zh": "重新渲染简谱", "en": "Re-render Jianpu"},
    "editor.tooltip_fit": {"zh": "适应", "en": "Fit to View"},
    "editor.placeholder_select_file": {"zh": "请先在首页选择并转换文件，或在此页打开对应图像/简谱文件", "en": "Select and convert a file on the home page first, or open a matching image/Jianpu file here"},
    "editor.rendering": {"zh": "渲染中…", "en": "Rendering…"},
    "editor.placeholder_click_jianpu": {"zh": "点击「简谱」生成渲染预览", "en": "Click \"Jianpu\" to generate a render preview"},
    "editor.error_pdf_render_failed": {"zh": "PDF 无法渲染", "en": "Unable to render the PDF"},
    "editor.error_load_failed_exc": {"zh": "加载失败: {exc}", "en": "Load failed: {exc}"},
    "editor.tooltip_back_to_preview": {"zh": "返回简谱预览", "en": "Back to Jianpu Preview"},
    "editor.file_picker_open_image": {"zh": "打开图像文件（PDF / PNG / JPG）", "en": "Open Image File (PDF / PNG / JPG)"},
    "editor.error_no_jianpu_loaded": {"zh": "未加载简谱文件", "en": "No Jianpu file loaded"},
    "editor.render_failed_default": {"zh": "渲染失败", "en": "Render failed"},

    # ── gui/pages/jianpu_preview_page.py ────────────────────────────────────
    "jianpu_preview.tooltip_export_checked": {"zh": "导出已勾选的简谱", "en": "Export Checked Jianpu Files"},
    "jianpu_preview.tooltip_delete_checked": {"zh": "删除已勾选的简谱", "en": "Delete Checked Jianpu Files"},
    "jianpu_preview.section_title": {"zh": "简谱文件", "en": "Jianpu Files"},
    "jianpu_preview.button_export": {"zh": "导出简谱", "en": "Export Jianpu"},
    "jianpu_preview.button_re_render": {"zh": "重新渲染", "en": "Re-render"},
    "jianpu_preview.tooltip_re_render_from_text": {"zh": "从编辑器文本重新生成简谱 PDF（无需重跑识别）", "en": "Regenerate the Jianpu PDF from the editor text (no need to re-run recognition)"},
    "jianpu_preview.delete_dialog_title": {"zh": "删除简谱文件", "en": "Delete Jianpu File"},
    "jianpu_preview.delete_dialog_body": {"zh": "将永久删除：{name} 及其关联的 MIDI 和编辑文本。", "en": "This will permanently delete {name} and its associated MIDI and edit text."},
    "jianpu_preview.batch_delete_dialog_title": {"zh": "批量删除简谱文件", "en": "Batch Delete Jianpu Files"},
    "jianpu_preview.batch_delete_dialog_body": {"zh": "将永久删除已勾选的 {n} 个简谱文件及其关联文件。", "en": "This will permanently delete the {n} checked Jianpu file(s) and their associated files."},
    "jianpu_preview.error_select_for_render": {"zh": "请先选择要重新渲染的简谱文件", "en": "Select a Jianpu file to re-render first"},
    "jianpu_preview.error_txt_not_found": {"zh": "未找到对应的简谱文本文件：{name}", "en": "Matching Jianpu text file not found: {name}"},
    "jianpu_preview.error_re_render_jianpu_ly": {"zh": "重新渲染失败：jianpu-ly 转换出错", "en": "Re-render failed: jianpu-ly conversion error"},
    "jianpu_preview.error_re_render_lilypond": {"zh": "重新渲染失败：LilyPond 未能生成 PDF", "en": "Re-render failed: LilyPond failed to generate the PDF"},
    "jianpu_preview.re_render_done": {"zh": "重新渲染完成 → {name}", "en": "Re-render complete → {name}"},
    "jianpu_preview.re_render_error": {"zh": "重新渲染出错: {exc}", "en": "Re-render error: {exc}"},
    "jianpu_preview.error_select_for_export": {"zh": "请先选择要导出的简谱文件", "en": "Select a Jianpu file to export first"},
    "jianpu_preview.error_checked_for_export": {"zh": "请先勾选要导出的简谱文件", "en": "Check at least one Jianpu file to export first"},
    "jianpu_preview.export_single_done": {"zh": "已导出 → {name}", "en": "Exported → {name}"},
    "jianpu_preview.file_picker_export": {"zh": "导出简谱 PDF", "en": "Export Jianpu PDF"},
    "jianpu_preview.dir_picker_export": {"zh": "选择导出目标目录", "en": "Choose Export Destination Folder"},
    "jianpu_preview.export_done_counts": {"zh": "导出完成：{success} 个成功，{fail} 个失败", "en": "Export complete: {success} succeeded, {fail} failed"},
    "jianpu_preview.export_done_summary": {"zh": "已导出 {n} 个简谱至 {dest}", "en": "Exported {n} Jianpu file(s) to {dest}"},

    # ── gui/pages/landing_page.py ───────────────────────────────────────────
    "landing.suffix_download_needed": {"zh": "（需下载）", "en": " (download required)"},
    "landing.option_auto": {"zh": "自动选择（推荐）{suffix}", "en": "Auto-select (Recommended){suffix}"},
    "landing.option_audiveris": {"zh": "Audiveris（启发式算法）", "en": "Audiveris (Heuristic Algorithm)"},
    "landing.option_homr": {"zh": "Homr（深度学习）{suffix}", "en": "Homr (Deep Learning){suffix}"},
    "landing.label_omr_engine": {"zh": "OMR 引擎", "en": "OMR Engine"},
    "landing.tooltip_omr_engine": {"zh": "自动选择会根据输入格式与图像质量决定使用 Audiveris 还是 Homr", "en": "Auto-select decides between Audiveris and Homr based on input format and image quality"},
    "landing.label_sr_engine": {"zh": "超分辨率算法", "en": "Super-Resolution Algorithm"},
    "landing.option_waifu2x": {"zh": "waifu2x（线条画）", "en": "waifu2x (Line Art)"},
    "landing.option_realesrgan": {"zh": "Real-ESRGAN（anime，默认）", "en": "Real-ESRGAN (Anime, Default)"},
    "landing.tooltip_sr_engine": {"zh": "低分辨率图片在识别前会先放大增强；两种算法适合不同风格的图源", "en": "Low-resolution images are upscaled and enhanced before recognition; the two algorithms suit different source styles"},
    "landing.label_concurrency": {"zh": "并发处理数", "en": "Concurrency"},
    "landing.option_concurrency_1": {"zh": "1（顺序，低配推荐）", "en": "1 (Sequential, recommended for low-end hardware)"},
    "landing.option_concurrency_2": {"zh": "2 个并行", "en": "2 in Parallel"},
    "landing.option_concurrency_4": {"zh": "4 个并行", "en": "4 in Parallel"},
    "landing.option_concurrency_auto": {"zh": "自动（按 CPU 核数）", "en": "Auto (Based on CPU Core Count)"},
    "landing.tooltip_concurrency": {"zh": "同时转换多个文件可加速批量处理；内存或显存不足时请保持 1", "en": "Converting multiple files at once speeds up batch processing; keep this at 1 if RAM or VRAM is limited"},
    "landing.button_start_convert": {"zh": "开始转换", "en": "Start Conversion"},
    "landing.button_open_output_dir": {"zh": "打开输出目录", "en": "Open Output Folder"},
    "landing.button_download_models": {"zh": "下载模型文件", "en": "Download Model Files"},
    "landing.button_delete_models": {"zh": "删除模型文件", "en": "Delete Model Files"},
    "landing.section_convert_options": {"zh": "转换选项", "en": "Conversion Options"},
    "landing.section_homr_models": {"zh": "HOMR 模型管理", "en": "HOMR Model Management"},
    "landing.download_dialog_title": {"zh": "需要下载 HOMR 模型权重", "en": "HOMR Model Weights Required"},
    "landing.download_dialog_body": {"zh": "使用 HOMR 引擎可以支持图片格式的乐谱识别。\n需要下载约 292 MB 模型权重。", "en": "The HOMR engine enables recognition of image-format scores.\nIt requires downloading about 292 MB of model weights."},
    "landing.button_not_now": {"zh": "暂不下载", "en": "Not Now"},
    "landing.button_download_now": {"zh": "立即下载", "en": "Download Now"},
    "landing.button_start_convert_with_count": {"zh": "开始转换（{n}）", "en": "Start Conversion ({n})"},
    "landing.models_deleted": {"zh": "已删除 {n} 个模型文件。", "en": "Deleted {n} model file(s)."},
    "landing.delete_models_dialog_title": {"zh": "确认删除模型文件", "en": "Confirm Delete Model Files"},
    "landing.delete_models_dialog_body": {"zh": "将删除全部 HOMR 模型权重文件（约 292 MB）。\n下次使用 HOMR 引擎时需要重新下载。", "en": "This will delete all HOMR model weight files (about 292 MB).\nThey will need to be re-downloaded the next time you use the HOMR engine."},
    "landing.error_select_at_least_one": {"zh": "请先勾选至少一个乐谱文件。", "en": "Check at least one score file first."},
    "landing.existing_outputs_warning": {"zh": "以下 {n} 个文件已存在输出：", "en": "The following {n} file(s) already have output:"},
    "landing.existing_outputs_more": {"zh": "  …等另外 {n} 个", "en": "  ...and {n} more"},
    "landing.checkbox_skip_duplicates": {"zh": "跳过重复文件（不重新识别）", "en": "Skip Duplicate Files (Don't Re-recognize)"},
    "landing.convert_dialog_title": {"zh": "转换 {n} 个文件", "en": "Convert {n} File(s)"},
    "landing.running_omr": {"zh": "正在运行 OMR 识别…", "en": "Running OMR Recognition…"},
    "landing.log_gpu_crash_retry": {"zh": "[homr] GPU 模式发生崩溃，正在以 GPU 模式重试…", "en": "[homr] GPU mode crashed, retrying in GPU mode…"},
    "landing.log_gpu_crash_fallback_cpu": {"zh": "[homr] GPU 模式再次崩溃，已回退到 CPU 模式重试…", "en": "[homr] GPU mode crashed again, falling back to CPU mode…"},
    "landing.worker_crash_error": {"zh": "Worker 进程异常退出（{code}）：{detail}", "en": "Worker process exited unexpectedly ({code}): {detail}"},
    "landing.worker_crash_no_detail": {"zh": "无详情", "en": "No details"},
    "landing.done_message": {"zh": "完成。", "en": "Done."},
    "landing.log_worker_assign": {"zh": "[W{worker}] 分配: {names}", "en": "[W{worker}] Assigned: {names}"},
    "landing.log_error_prefix": {"zh": "{prefix}错误: {err}", "en": "{prefix}Error: {err}"},
    "landing.log_worker_crash": {"zh": "[W{worker}] 进程异常退出 {hint}", "en": "[W{worker}] Process exited unexpectedly {hint}"},
    "landing.all_workers_failed": {"zh": "所有 Worker 进程均失败，请检查日志。", "en": "All worker processes failed — check the log."},
    "landing.summary_success_part": {"zh": "{n} 个成功", "en": "{n} succeeded"},
    "landing.summary_fail_part": {"zh": "{n} 个失败", "en": "{n} failed"},
    "landing.summary_skipped_part": {"zh": "{n} 个已跳过", "en": "{n} skipped"},
    "landing.summary_done_prefix": {"zh": "完成：", "en": "Done: "},
    "landing.summary_done_fallback": {"zh": "完成", "en": "Done"},
    "landing.stat_success": {"zh": "✓ 成功 {n}", "en": "✓ Succeeded {n}"},
    "landing.stat_fallback": {"zh": "⚠ 回退成功 {n}", "en": "⚠ Fallback {n}"},
    "landing.stat_failed": {"zh": "✗ 失败 {n}", "en": "✗ Failed {n}"},
    "landing.stat_total": {"zh": "总共 {n} 个文件", "en": "{n} file(s) total"},
    "landing.section_success": {"zh": "成功", "en": "Succeeded"},
    "landing.detail_image_type": {"zh": "识别为{kind}", "en": "Recognized as {kind}"},
    "landing.detail_engine_used": {"zh": "使用 {engine} 引擎", "en": "Used the {engine} engine"},
    "landing.detail_success_fallback": {"zh": "转换成功", "en": "Conversion succeeded"},
    "landing.list_item_success": {"zh": "  ✓  {name}", "en": "  ✓  {name}"},
    "landing.list_item_detail": {"zh": "       {detail}", "en": "       {detail}"},
    "landing.section_fallback": {"zh": "回退成功（建议核对识别质量）", "en": "Fallback succeeded (worth double-checking)"},
    "landing.list_item_fallback": {"zh": "  ⚠  {name}", "en": "  ⚠  {name}"},
    "landing.detail_fallback_used": {"zh": "首选引擎识别失败，已自动回退：{detail}", "en": "Primary engine failed, auto-fell back: {detail}"},
    "landing.detail_fallback_used_bare": {"zh": "首选引擎识别失败，已自动回退到备用引擎", "en": "Primary engine failed, auto-fell back to a backup engine"},
    "landing.section_failed": {"zh": "失败", "en": "Failed"},
    "landing.list_item_failed": {"zh": "  ✗  {name}", "en": "  ✗  {name}"},
    "landing.list_item_reason": {"zh": "       原因：{reason}", "en": "       Reason: {reason}"},
    "landing.result_dialog_title": {"zh": "识别结果", "en": "Recognition Results"},
    "landing.button_view_jianpu": {"zh": "查看简谱", "en": "View Jianpu"},
    "landing.unknown_reason": {"zh": "未知原因", "en": "Unknown reason"},
    "landing.unknown_error": {"zh": "未知错误", "en": "Unknown error"},
    "landing.unknown_file": {"zh": "未知", "en": "Unknown"},
    "landing.names_preview_more": {"zh": " 等 {n} 个", "en": " and {n} more"},
    "landing.process_error": {"zh": "进程错误", "en": "Process error"},
    "landing.crash_hint_code": {"zh": "（代码 {rc}）", "en": " (code {rc})"},
    "landing.crash_hint_tail": {"zh": "：{tail}", "en": ": {tail}"},

    # ── gui/pages/score_preview_page.py ─────────────────────────────────────
    "score_preview.tooltip_export_checked": {"zh": "导出已勾选的五线谱 PDF", "en": "Export Checked Staff Notation PDFs"},
    "score_preview.tooltip_delete_checked": {"zh": "删除已勾选的五线谱", "en": "Delete Checked Staff Notation Files"},
    "score_preview.section_title": {"zh": "五线谱文件", "en": "Staff Notation Files"},
    "score_preview.button_export": {"zh": "导出乐谱", "en": "Export Score"},
    "score_preview.button_transpose": {"zh": "乐谱移调", "en": "Transpose Score"},
    "score_preview.delete_dialog_title": {"zh": "删除五线谱文件", "en": "Delete Staff Notation File"},
    "score_preview.delete_dialog_body": {"zh": "将永久删除：{name}", "en": "This will permanently delete: {name}"},
    "score_preview.batch_delete_dialog_title": {"zh": "批量删除五线谱文件", "en": "Batch Delete Staff Notation Files"},
    "score_preview.batch_delete_dialog_body": {"zh": "将永久删除已勾选的 {n} 个五线谱文件。", "en": "This will permanently delete the {n} checked staff notation file(s)."},
    "score_preview.status_rendering": {"zh": "渲染中: {name}...", "en": "Rendering: {name}..."},
    "score_preview.error_preview_render_failed": {"zh": "预览渲染失败：LilyPond 不可用或文件有误", "en": "Preview render failed: LilyPond unavailable or the file is invalid"},
    "score_preview.error_render_failed_exc": {"zh": "渲染失败: {exc}", "en": "Render failed: {exc}"},
    "score_preview.error_select_score_first": {"zh": "请先选择乐谱文件。", "en": "Select a score file first."},
    "score_preview.file_picker_export": {"zh": "导出五线谱 PDF", "en": "Export Staff Notation PDF"},
    "score_preview.export_done": {"zh": "导出完成 → {dest}", "en": "Export complete → {dest}"},
    "score_preview.error_export_failed": {"zh": "导出失败：无法生成五线谱 PDF，请检查 LilyPond 是否可用。", "en": "Export failed: unable to generate the staff notation PDF — check that LilyPond is available."},
    "score_preview.error_select_for_export": {"zh": "请先勾选要导出的五线谱文件。", "en": "Check at least one staff notation file to export first."},
    "score_preview.dir_picker_export": {"zh": "选择导出目标目录", "en": "Choose Export Destination Folder"},
    "score_preview.batch_export_start": {"zh": "批量导出 {n} 个文件...", "en": "Batch exporting {n} file(s)..."},
    "score_preview.batch_export_progress": {"zh": "导出中... {done}/{total}", "en": "Exporting... {done}/{total}"},
    "score_preview.batch_export_done_counts": {"zh": "导出完成：{success} 个成功，{fail} 个失败", "en": "Export complete: {success} succeeded, {fail} failed"},
    "score_preview.batch_export_done_summary": {"zh": "已导出 {n} 个五线谱 PDF 至 {dest}", "en": "Exported {n} staff notation PDF(s) to {dest}"},

    # ── gui/pages/transposer_page.py ────────────────────────────────────────
    "transposer.dir_recent": {"zh": "最近", "en": "Nearest"},
    "transposer.dir_up": {"zh": "向上", "en": "Up"},
    "transposer.dir_down": {"zh": "向下", "en": "Down"},
    # NOTE: interval_default / degree_default / fallback_interval are NOT display
    # chrome — they are *values* that must match a core INTERVALS / DIATONIC_DEGREES
    # option name (those vocabularies stay Chinese). Keep en == zh so the dropdown
    # selection and the value passed to the core transposer never break on switch.
    "transposer.interval_default": {"zh": "纯一度", "en": "纯一度"},
    "transposer.degree_default": {"zh": "三度", "en": "三度"},
    "transposer.label_interval": {"zh": "音程", "en": "Interval"},
    "transposer.label_from_key": {"zh": "原调", "en": "Original Key"},
    "transposer.label_to_key": {"zh": "目标调", "en": "Target Key"},
    "transposer.arrow": {"zh": "→", "en": "→"},
    "transposer.label_degree": {"zh": "度数", "en": "Degree"},
    "transposer.mode_interval": {"zh": "按音程", "en": "By Interval"},
    "transposer.mode_key": {"zh": "按调", "en": "By Key"},
    "transposer.mode_chromatic": {"zh": "全音移调", "en": "Chromatic Transpose"},
    "transposer.advanced_options_title": {"zh": "高级选项", "en": "Advanced Options"},
    "transposer.fallback_interval": {"zh": "纯四度", "en": "纯四度"},
    "transposer.quick_label_direction": {"zh": "方向", "en": "Direction"},
    "transposer.checkbox_transpose_key_signature": {"zh": "移调调号", "en": "Transpose Key Signature"},
    "transposer.checkbox_transpose_key_signature_adv": {"zh": "同时移调调号", "en": "Also Transpose Key Signature"},
    "transposer.button_export_transposed": {"zh": "导出移调乐谱", "en": "Export Transposed Score"},
    "transposer.button_export_original": {"zh": "导出原调乐谱", "en": "Export Original-Key Score"},
    "transposer.tooltip_back_to_score_preview": {"zh": "返回五线谱预览", "en": "Back to Staff Preview"},
    "transposer.button_open_score_dir": {"zh": "打开乐谱目录", "en": "Open Score Folder"},
    "transposer.section_title": {"zh": "移调功能", "en": "Transposition"},
    "transposer.label_transposed_col": {"zh": "移调后", "en": "Transposed"},
    "transposer.status_open_score_first": {"zh": "请先打开乐谱文件。", "en": "Open a score file first."},
    "transposer.status_wait_preview_save": {"zh": "请先等待移调预览生成后再保存。", "en": "Wait for the transposed preview to finish generating before saving."},
    "transposer.status_saved": {"zh": "已保存移调版乐谱 → {name}", "en": "Saved transposed score → {name}"},
    "transposer.status_save_failed": {"zh": "保存失败: {exc}", "en": "Save failed: {exc}"},
    "transposer.file_picker_open_musicxml": {"zh": "打开乐谱文件（MusicXML）", "en": "Open Score File (MusicXML)"},
    "transposer.status_preview_only": {"zh": "预览: {name}（仅预览，移调需 MXL 格式）", "en": "Preview: {name} (preview only — transposition requires MXL format)"},
    "transposer.status_detected_key": {"zh": "检测到原调: {key}", "en": "Detected original key: {key}"},
    "transposer.status_key_detect_failed": {"zh": "调号检测失败: {exc}", "en": "Key detection failed: {exc}"},
    "transposer.status_convert_or_open_first": {"zh": "请先在首页转换文件或打开乐谱。", "en": "Convert a file on the home page or open a score first."},
    "transposer.status_transpose_done": {"zh": "移调完成 → {name}", "en": "Transposition complete → {name}"},
    "transposer.status_transpose_failed": {"zh": "移调失败: {exc}", "en": "Transposition failed: {exc}"},
    "transposer.status_wait_preview_export": {"zh": "请先等待移调预览生成后再导出。", "en": "Wait for the transposed preview to finish generating before exporting."},
    "transposer.file_picker_export_transposed": {"zh": "导出移调版 PDF", "en": "Export Transposed PDF"},
    "transposer.status_open_original_first": {"zh": "请先加载原调乐谱后再导出。", "en": "Load the original-key score before exporting."},
    "transposer.file_picker_export_original": {"zh": "导出原调乐谱 PDF", "en": "Export Original-Key Score PDF"},
    "transposer.export_done": {"zh": "导出完成 → {dest}", "en": "Export complete → {dest}"},
    "transposer.export_failed_transposed": {"zh": "导出失败：无法生成五线谱 PDF，请检查 LilyPond / musicxml2ly 是否可用。", "en": "Export failed: unable to generate the staff notation PDF — check that LilyPond / musicxml2ly are available."},
    "transposer.export_failed_original": {"zh": "导出失败：无法生成原调乐谱 PDF，请检查 LilyPond / musicxml2ly 是否可用。", "en": "Export failed: unable to generate the original-key score PDF — check that LilyPond / musicxml2ly are available."},
    "transposer.status_preview_render_failed": {"zh": "预览渲染失败: {exc}", "en": "Preview render failed: {exc}"},

    # ── gui/components/pdf_viewer.py ────────────────────────────────────────
    "pdf_viewer.page_placeholder": {"zh": "—", "en": "—"},
    "pdf_viewer.tooltip_prev_page": {"zh": "上一页", "en": "Previous Page"},
    "pdf_viewer.tooltip_next_page": {"zh": "下一页", "en": "Next Page"},
    "pdf_viewer.tooltip_reset_zoom": {"zh": "复位缩放", "en": "Reset Zoom"},
    "pdf_viewer.no_file": {"zh": "暂无文件", "en": "No File"},
    "pdf_viewer.error_render_failed": {"zh": "PDF 无法渲染（文件可能已损坏或格式不受支持）", "en": "Unable to render the PDF (the file may be corrupted or unsupported)"},
    "pdf_viewer.page_label": {"zh": "{current} / {total}", "en": "{current} / {total}"},

    # ── gui/components/file_sidebar.py ──────────────────────────────────────
    "file_sidebar.tooltip_delete_checked": {"zh": "删除已勾选的文件", "en": "Delete Checked Files"},
    "file_sidebar.section_title": {"zh": "文件列表", "en": "File List"},
    "file_sidebar.tooltip_add_folder": {"zh": "添加文件夹", "en": "Add Folder"},
    "file_sidebar.tooltip_add_file": {"zh": "添加文件", "en": "Add File"},
    "file_sidebar.empty_hint": {"zh": "还没有乐谱文件", "en": "No score files yet"},
    "file_sidebar.button_add_file": {"zh": "添加文件", "en": "Add File"},
    "file_sidebar.empty_supported_formats": {"zh": "支持 PDF / PNG / JPG\n也可直接放入 Input 文件夹", "en": "Supports PDF / PNG / JPG\nor drop files directly into the Input folder"},
    "file_sidebar.delete_dialog_title": {"zh": "删除文件", "en": "Delete File"},
    "file_sidebar.delete_dialog_body": {"zh": "将从 Input 文件夹中永久删除：\n{name}", "en": "This will permanently delete from the Input folder:\n{name}"},
    "file_sidebar.tooltip_remove_from_input": {"zh": "从 Input 文件夹删除", "en": "Remove from Input Folder"},
    "file_sidebar.batch_delete_body_mixed": {"zh": "将永久删除 {m} 个 Input 文件夹中的文件，另有 {n} 个仅从列表移除。", "en": "This will permanently delete {m} file(s) in the Input folder; {n} more will only be removed from the list."},
    "file_sidebar.batch_delete_body_all_input": {"zh": "将永久删除 {n} 个 Input 文件夹中的文件。", "en": "This will permanently delete {n} file(s) in the Input folder."},
    "file_sidebar.batch_delete_body_list_only": {"zh": "将从列表移除 {n} 个文件（不删除磁盘文件）。", "en": "This will remove {n} file(s) from the list (no files will be deleted from disk)."},
    "file_sidebar.batch_delete_dialog_title": {"zh": "批量删除文件", "en": "Batch Delete Files"},
    "file_sidebar.file_picker_select_score": {"zh": "选择乐谱文件", "en": "Select Score Files"},
    "file_sidebar.folder_picker_select": {"zh": "选择包含乐谱文件的文件夹", "en": "Select a Folder Containing Score Files"},
    "file_sidebar.import_preparing": {"zh": "准备中…", "en": "Preparing…"},
    "file_sidebar.import_dialog_title": {"zh": "导入 {n} 个文件", "en": "Importing {n} File(s)"},
    "file_sidebar.import_progress": {"zh": "({index}/{total})  {name}", "en": "({index}/{total})  {name}"},
    "file_sidebar.import_organizing": {"zh": "正在整理 {n} 个文件…", "en": "Organizing {n} file(s)…"},

    # ── gui/components/model_download_dialog.py ────────────────────────────
    "model_download.source_auto_label": {"zh": "自动选择", "en": "Auto-select"},
    "model_download.source_auto_desc": {"zh": "自动检测延迟最低的可用源（推荐）", "en": "Automatically detect the lowest-latency available source (Recommended)"},
    "model_download.source_modelscope_label": {"zh": "ModelScope", "en": "ModelScope"},
    "model_download.source_modelscope_desc": {"zh": "ModelScope CDN — 大陆访问优先", "en": "ModelScope CDN — preferred for mainland China access"},
    "model_download.source_github_label": {"zh": "GitHub Releases", "en": "GitHub Releases"},
    "model_download.source_github_desc": {"zh": "GitHub — 海外访问优先", "en": "GitHub — preferred for access outside mainland China"},
    "model_download.dialog_title": {"zh": "HOMR 模型权重", "en": "HOMR Model Weights"},
    "model_download.label_source": {"zh": "下载源", "en": "Download Source"},
    "model_download.intro_text": {"zh": "选择下载源后点击「开始下载」。HOMR 模型约 292 MB（GitHub 源以压缩包分发，实际下载约 265 MB），下载到 models/ 目录。", "en": "Choose a download source, then click \"Start Download.\" The HOMR model is about 292 MB (the GitHub source distributes it as an archive, so the actual download is about 265 MB) and is saved to the models/ folder."},
    "model_download.picker_dialog_title": {"zh": "下载 HOMR 模型权重", "en": "Download HOMR Model Weights"},
    "model_download.button_start_download": {"zh": "开始下载", "en": "Start Download"},
    "model_download.source_status_testing": {"zh": "当前源: 测试中…", "en": "Current source: testing…"},
    "model_download.file_status_preparing": {"zh": "准备中…", "en": "Preparing…"},
    "model_download.progress_initial": {"zh": "0 / ? MB", "en": "0 / ? MB"},
    "model_download.downloading_dialog_title": {"zh": "正在下载 HOMR 模型权重", "en": "Downloading HOMR Model Weights"},
    "model_download.error_dialog_title": {"zh": "下载出错", "en": "Download Error"},
    "model_download.button_retry": {"zh": "重试", "en": "Retry"},
    "model_download.file_progress": {"zh": "下载中 ({index}/{total}): {name}", "en": "Downloading ({index}/{total}): {name}"},
    "model_download.file_size_progress": {"zh": "{done} / {total} MB ({pct}%)", "en": "{done} / {total} MB ({pct}%)"},
    "model_download.file_size_progress_no_total": {"zh": "{done} MB", "en": "{done} MB"},
    "model_download.overall_progress": {"zh": "{done} / {total} MB", "en": "{done} / {total} MB"},
    "model_download.source_auto_modelscope": {"zh": "ModelScope", "en": "ModelScope"},
    "model_download.source_auto_github_fallback": {"zh": "GitHub（备用）", "en": "GitHub (Fallback)"},
    "model_download.source_label_modelscope": {"zh": "ModelScope", "en": "ModelScope"},
    "model_download.source_label_github": {"zh": "GitHub", "en": "GitHub"},
    "model_download.source_status": {"zh": "当前源: {label}", "en": "Current source: {label}"},
    "model_download.error_no_source": {"zh": "下载失败 — 请检查网络连接", "en": "Download failed — check your network connection"},
    "model_download.error_hash_mismatch": {"zh": "权重文件校验失败：{exc}", "en": "Weight file verification failed: {exc}"},
    "model_download.error_generic": {"zh": "下载失败：{exc}", "en": "Download failed: {exc}"},

    # ── gui/components/progress_overlay.py ──────────────────────────────────
    "progress_overlay.button_show_log": {"zh": "显示详细日志", "en": "Show Detailed Log"},
    "progress_overlay.default_message": {"zh": "处理中…", "en": "Processing…"},
    "progress_overlay.elapsed_initial": {"zh": "00:00", "en": "00:00"},
    "progress_overlay.button_hide_log": {"zh": "隐藏详细日志", "en": "Hide Detailed Log"},
    "progress_overlay.error_status": {"zh": "错误：{message}", "en": "Error: {message}"},
    "progress_overlay.done_default": {"zh": "完成", "en": "Done"},

    # ── gui/components/jianpu_editor.py ─────────────────────────────────────
    "jianpu_editor.title": {"zh": "简谱编辑器", "en": "Jianpu Editor"},
    "jianpu_editor.tooltip_undo": {"zh": "撤销 (Ctrl+Z)", "en": "Undo (Ctrl+Z)"},
    "jianpu_editor.tooltip_redo": {"zh": "重做 (Ctrl+Y)", "en": "Redo (Ctrl+Y)"},
    "jianpu_editor.button_save": {"zh": "保存", "en": "Save"},
    "jianpu_editor.button_export_pdf": {"zh": "导出PDF", "en": "Export PDF"},
    "jianpu_editor.tooltip_export_pdf": {"zh": "将当前简谱文件通过 LilyPond 渲染为 PDF", "en": "Render the current Jianpu file to PDF via LilyPond"},
    "jianpu_editor.tooltip_symbol_panel": {"zh": "简谱符号速查", "en": "Jianpu Symbol Reference"},
    "jianpu_editor.toggle_jianpu": {"zh": "简谱", "en": "Jianpu"},
    "jianpu_editor.toggle_original": {"zh": "原图", "en": "Original Image"},
    "jianpu_editor.hint_no_file": {"zh": "尚未加载简谱文件…", "en": "No Jianpu file loaded…"},
    "jianpu_editor.symbol_section_notes": {"zh": "── 音符", "en": "── Notes"},
    "jianpu_editor.symbol_notes_row": {"zh": "1 2 3 4 5 6 7", "en": "1 2 3 4 5 6 7"},
    "jianpu_editor.symbol_notes_desc": {"zh": "Do Re Mi Fa Sol La Si", "en": "Do Re Mi Fa Sol La Si"},
    "jianpu_editor.symbol_rest_row": {"zh": "0", "en": "0"},
    "jianpu_editor.symbol_rest_desc": {"zh": "休止符", "en": "Rest"},
    "jianpu_editor.symbol_accidental_row": {"zh": "#1  b3", "en": "#1  b3"},
    "jianpu_editor.symbol_accidental_desc": {"zh": "升 Do / 降 Mi（写在数字前）", "en": "Sharp Do / Flat Mi (written before the digit)"},
    "jianpu_editor.symbol_high_octave_row": {"zh": "1'  1''", "en": "1'  1''"},
    "jianpu_editor.symbol_high_octave_desc": {"zh": "高八度 / 超高八度", "en": "One octave up / Two octaves up"},
    "jianpu_editor.symbol_low_octave_row": {"zh": "1,  1,,", "en": "1,  1,,"},
    "jianpu_editor.symbol_low_octave_desc": {"zh": "低八度 / 超低八度", "en": "One octave down / Two octaves down"},
    "jianpu_editor.symbol_section_duration": {"zh": "── 时值", "en": "── Duration"},
    "jianpu_editor.symbol_duration_row_1": {"zh": "1", "en": "1"},
    "jianpu_editor.symbol_duration_desc_1": {"zh": "四分音符（1 拍）", "en": "Quarter note (1 beat)"},
    "jianpu_editor.symbol_duration_row_2": {"zh": "1 -", "en": "1 -"},
    "jianpu_editor.symbol_duration_desc_2": {"zh": "二分音符（2 拍）", "en": "Half note (2 beats)"},
    "jianpu_editor.symbol_duration_row_3": {"zh": "1 - - -", "en": "1 - - -"},
    "jianpu_editor.symbol_duration_desc_3": {"zh": "全音符（4 拍）", "en": "Whole note (4 beats)"},
    "jianpu_editor.symbol_duration_row_4": {"zh": "q1", "en": "q1"},
    "jianpu_editor.symbol_duration_desc_4": {"zh": "八分音符（½ 拍）", "en": "Eighth note (½ beat)"},
    "jianpu_editor.symbol_duration_row_5": {"zh": "s1", "en": "s1"},
    "jianpu_editor.symbol_duration_desc_5": {"zh": "十六分音符（¼ 拍）", "en": "Sixteenth note (¼ beat)"},
    "jianpu_editor.symbol_duration_row_6": {"zh": "d1", "en": "d1"},
    "jianpu_editor.symbol_duration_desc_6": {"zh": "三十二分音符（⅛ 拍）", "en": "Thirty-second note (⅛ beat)"},
    "jianpu_editor.symbol_duration_row_7": {"zh": "1.", "en": "1."},
    "jianpu_editor.symbol_duration_desc_7": {"zh": "附点四分（1.5 拍）", "en": "Dotted quarter (1.5 beats)"},
    "jianpu_editor.symbol_duration_row_8": {"zh": "q1.", "en": "q1."},
    "jianpu_editor.symbol_duration_desc_8": {"zh": "附点八分（0.75 拍）", "en": "Dotted eighth (0.75 beat)"},
    "jianpu_editor.symbol_section_structure": {"zh": "── 结构", "en": "── Structure"},
    "jianpu_editor.symbol_structure_row_1": {"zh": "|", "en": "|"},
    "jianpu_editor.symbol_structure_desc_1": {"zh": "小节线", "en": "Barline"},
    "jianpu_editor.symbol_structure_row_2": {"zh": "title=曲名", "en": "title=Song Title"},
    "jianpu_editor.symbol_structure_desc_2": {"zh": "标题（文件头部）", "en": "Title (file header)"},
    "jianpu_editor.symbol_structure_row_3": {"zh": "1=C  1=G", "en": "1=C  1=G"},
    "jianpu_editor.symbol_structure_desc_3": {"zh": "调号（大调，1=主音）", "en": "Key signature (major, 1=tonic)"},
    "jianpu_editor.symbol_structure_row_4": {"zh": "6=A  6=D", "en": "6=A  6=D"},
    "jianpu_editor.symbol_structure_desc_4": {"zh": "调号（小调，6=主音）", "en": "Key signature (minor, 6=tonic)"},
    "jianpu_editor.symbol_structure_row_5": {"zh": "4/4,8", "en": "4/4,8"},
    "jianpu_editor.symbol_structure_desc_5": {"zh": "拍号（分母=最小时值）", "en": "Time signature (denominator = shortest note value)"},
    "jianpu_editor.symbol_section_polyphony": {"zh": "── 多声部", "en": "── Multi-Voice"},
    "jianpu_editor.symbol_polyphony_row_1": {"zh": "NextPart", "en": "NextPart"},
    "jianpu_editor.symbol_polyphony_desc_1": {"zh": "分隔声部；每个声部单独渲染", "en": "Separates voices; each voice is rendered separately"},
    "jianpu_editor.symbol_polyphony_row_2": {"zh": "NextPart  4/4,8  ...", "en": "NextPart  4/4,8  ..."},
    "jianpu_editor.symbol_polyphony_desc_2": {"zh": "换声部后必须立即重写拍号", "en": "The time signature must be rewritten immediately after switching voices"},
    "jianpu_editor.symbol_polyphony_row_3": {"zh": "声部1 / NextPart\n4/4,8 / 声部2", "en": "Voice 1 / NextPart\n4/4,8 / Voice 2"},
    "jianpu_editor.symbol_polyphony_desc_3": {"zh": "同组声部并排显示在同一行谱上", "en": "Voices in the same group are shown side by side on the same staff line"},
    "jianpu_editor.title_dirty_suffix": {"zh": " *", "en": " *"},
    "jianpu_editor.error_read_failed": {"zh": "# 文件读取失败: {exc}", "en": "# Failed to read file: {exc}"},
    "jianpu_editor.log_saved": {"zh": "已保存: {name}", "en": "Saved: {name}"},
    "jianpu_editor.log_save_failed": {"zh": "保存失败: {exc}", "en": "Save failed: {exc}"},
    "jianpu_editor.error_export_no_file": {"zh": "导出失败：请先加载简谱文件", "en": "Export failed: load a Jianpu file first"},
    "jianpu_editor.log_save_failed_pre_export": {"zh": "保存失败（导出前）: {exc}", "en": "Save failed (before export): {exc}"},
    "jianpu_editor.dir_picker_export_pdf": {"zh": "选择 PDF 导出目录", "en": "Choose PDF Export Folder"},
    "jianpu_editor.log_generating_ly": {"zh": "正在生成 LilyPond 中间文件: {name}", "en": "Generating LilyPond intermediate file: {name}"},
    "jianpu_editor.error_jianpu_ly_failed": {"zh": "jianpu-ly 转换失败，请确认 LilyPond 与 jianpu-ly.py 已安装", "en": "jianpu-ly conversion failed — please confirm LilyPond and jianpu-ly.py are installed"},
    "jianpu_editor.log_rendering_pdf": {"zh": "正在渲染 PDF…", "en": "Rendering PDF…"},
    "jianpu_editor.log_pdf_exported": {"zh": "PDF 已导出: {path}", "en": "PDF exported: {path}"},
    "jianpu_editor.error_pdf_render_failed": {"zh": "PDF 渲染失败，请检查 LilyPond 安装", "en": "PDF rendering failed — please check your LilyPond installation"},
    "jianpu_editor.error_export_exc": {"zh": "导出 PDF 出错: {exc}", "en": "Export PDF error: {exc}"},
}

_LANG = "zh"


def set_language(lang: str) -> None:
    """Switch the active language for all subsequent t() calls."""
    global _LANG
    if lang not in ("zh", "en"):
        raise ValueError(f"unsupported language: {lang!r}")
    _LANG = lang


def get_language() -> str:
    return _LANG


def t(key: str, **kwargs: object) -> str:
    """Look up a UI string by key in the active language and substitute placeholders."""
    text = STRINGS[key][_LANG]
    return text.format(**kwargs) if kwargs else text
