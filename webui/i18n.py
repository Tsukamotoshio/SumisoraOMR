# webui/i18n.py — web shell string catalog (zh/en).
"""webui 自有文案目录。

- 键统一 ``w.`` 前缀；bridge 把本目录与 ``gui/strings.py`` 的 STRINGS 合并后
  整体交给前端（gui 目录里已有翻译的键——如 about.license_text——直接复用，
  避免重复维护）。M5 移除 Flet 后，把 gui/strings.py 仍被引用的键并入本文件
  即完成收编。
- 语言状态与 Flet 版共享：gui.strings.set_language（worker 汇总文案跟随）+
  gui.settings 持久化（ui-settings.json）。
"""
from __future__ import annotations

WEBUI_STRINGS: dict[str, dict[str, str]] = {
    # ── 标题栏 ───────────────────────────────────────────────────────────────
    "w.tb.theme": {"zh": "切换明暗主题", "en": "Toggle light/dark theme"},
    "w.tb.lang": {"zh": "切换语言 (EN/中文)", "en": "Switch language (EN/中文)"},
    "w.tb.min": {"zh": "最小化", "en": "Minimize"},
    "w.tb.max": {"zh": "最大化/还原", "en": "Maximize/Restore"},
    "w.tb.close": {"zh": "关闭", "en": "Close"},

    # ── 导航 ─────────────────────────────────────────────────────────────────
    "w.nav.score": {"zh": "乐谱识别", "en": "Score OMR"},
    "w.nav.audio": {"zh": "音频识别", "en": "Audio"},
    "w.nav.jianpu": {"zh": "简谱预览", "en": "Jianpu"},
    "w.nav.staff": {"zh": "五线谱预览", "en": "Staff"},
    "w.nav.about": {"zh": "关于", "en": "About"},

    # ── 乐谱识别页 ───────────────────────────────────────────────────────────
    "w.score.files_title": {"zh": "输入文件", "en": "Input Files"},
    "w.score.add": {"zh": "添加文件", "en": "Add Files"},
    "w.score.empty": {"zh": "拖入 PDF / PNG / JPG，或点「添加文件」",
                      "en": "Drop PDF / PNG / JPG here, or click \"Add Files\""},
    "w.score.preview_title": {"zh": "预览", "en": "Preview"},
    "w.score.preview_ph": {"zh": "选中文件后在此预览\n（PDF 预览将在 M3 接入 pdf.js）",
                           "en": "Select a file to preview it here\n(PDF preview lands with pdf.js)"},
    "w.score.opts_title": {"zh": "识别选项", "en": "Recognition Options"},
    "w.score.engine": {"zh": "OMR 引擎", "en": "OMR Engine"},
    "w.score.engine_auto": {"zh": "自动选择（推荐）", "en": "Auto (recommended)"},
    "w.score.parallel": {"zh": "并发处理", "en": "Parallelism"},
    "w.score.parallel_1": {"zh": "单文件顺序", "en": "Sequential"},
    "w.score.parallel_2": {"zh": "2 并发", "en": "2 workers"},
    "w.score.parallel_4": {"zh": "4 并发", "en": "4 workers"},
    "w.score.start": {"zh": "开始识别", "en": "Start Recognition"},
    "w.score.outdir": {"zh": "打开输出目录", "en": "Open Output Folder"},
    "w.score.homr_label": {"zh": "HOMR 模型", "en": "HOMR Model"},
    "w.score.checking": {"zh": "检查中…", "en": "Checking…"},
    "w.score.homr_ready": {"zh": "OMR 引擎 “Homr” · 已就绪", "en": "OMR engine \"Homr\" · ready"},
    "w.score.homr_missing": {"zh": "未就绪（{p}/{t} 个权重）", "en": "Not ready ({p}/{t} weights)"},
    "w.model.download": {"zh": "下载", "en": "Download"},
    "w.model.delete": {"zh": "删除", "en": "Delete"},

    # ── 音频识别页 ───────────────────────────────────────────────────────────
    "w.audio.files_title": {"zh": "音频文件", "en": "Audio Files"},
    "w.audio.empty": {"zh": "拖入 MP3 / WAV / FLAC / OGG，或点「添加文件」",
                      "en": "Drop MP3 / WAV / FLAC / OGG here, or click \"Add Files\""},
    "w.audio.listen_title": {"zh": "试听", "en": "Listen"},
    "w.audio.listen_ph": {"zh": "选中音频后在此试听\n（识别引擎仅支持钢琴独奏）",
                          "en": "Select audio to play it here\n(the engine supports solo piano only)"},
    "w.audio.melody_only": {"zh": "仅主旋律", "en": "Melody only"},
    "w.audio.notedigger": {"zh": "音频扒谱编辑器", "en": "Audio transcription editor"},
    "w.audio.notedigger_tip": {
        "zh": "在钢琴卷帘上按音频手动扒谱 / 校正，导出 MIDI",
        "en": "Transcribe / correct by ear on a piano roll; export MIDI",
    },
    "w.nd.title": {"zh": "音频扒谱编辑器（noteDigger）", "en": "Audio transcription editor (noteDigger)"},
    "w.nd.hint": {"zh": "在钢琴卷帘上按音频扒谱，导出 MIDI", "en": "Transcribe by ear on a piano roll; export MIDI"},
    "w.nd.export_hint": {"zh": "在 noteDigger 里导出 MIDI 后即可一键生成简谱",
                         "en": "Export MIDI in noteDigger, then generate jianpu in one click"},
    "w.nd.gen": {"zh": "生成简谱", "en": "Generate jianpu"},
    "w.nd.gen_tip": {"zh": "用刚导出的 MIDI 生成简谱", "en": "Generate jianpu from the just-exported MIDI"},
    "w.nd.captured": {"zh": "已捕获 {name} · 可生成简谱", "en": "Captured {name} · ready to generate"},
    "w.nd.generating": {"zh": "正在生成简谱…", "en": "Generating jianpu…"},
    "w.nd.gen_done": {"zh": "简谱已生成：{name}", "en": "Jianpu generated: {name}"},
    "w.nd.gen_failed": {"zh": "生成简谱失败：{e}", "en": "Failed to generate jianpu: {e}"},
    "w.audio.auto_title": {"zh": "自动识别", "en": "Automatic recognition"},
    "w.audio.auto_desc": {
        "zh": "用钢琴转录模型把音频自动识别为简谱 / MIDI（仅支持钢琴独奏）。",
        "en": "Auto-transcribe audio to jianpu / MIDI with the piano model (solo piano only).",
    },
    "w.audio.notedigger_desc": {
        "zh": "在钢琴卷帘上按音频手动扒谱 / 校正，AI 辅助，导出 MIDI。自动识别不理想时用它精修。",
        "en": "Transcribe / correct by ear on a piano roll (AI-assisted), export MIDI. Use it to refine when auto-recognition falls short.",
    },
    "w.audio.notedigger_open": {"zh": "打开扒谱编辑器", "en": "Open the editor"},
    "w.midi.synth_fail": {"zh": "MIDI 合成器加载失败", "en": "Failed to load the MIDI synth"},
    "w.midi.load_fail": {"zh": "MIDI 文件加载失败", "en": "Failed to load the MIDI file"},
    "w.audio.piano_label": {"zh": "钢琴转录模型", "en": "Piano Transcription Model"},
    "w.audio.piano_ready": {"zh": "钢琴转录模型 · 已就绪", "en": "Piano transcription model · ready"},
    "w.audio.piano_missing": {"zh": "未下载（约 172 MB，按需）", "en": "Not downloaded (~172 MB, on demand)"},

    # ── 列表工具条（简谱/五线谱共用）─────────────────────────────────────────
    "w.list.refresh": {"zh": "刷新", "en": "Refresh"},
    "w.list.refresh_tip": {"zh": "刷新列表", "en": "Refresh the list"},
    "w.list.selall": {"zh": "全选", "en": "All"},
    "w.list.selall_tip": {"zh": "全选/全不选", "en": "Select / deselect all"},
    "w.list.export": {"zh": "导出", "en": "Export"},
    "w.list.delete": {"zh": "删除", "en": "Delete"},
    "w.list.checked_count": {"zh": "勾选 {n} / {t}", "en": "{n} / {t} checked"},
    "w.list.selected_count": {"zh": "已选 {n} / {t}", "en": "{n} / {t} selected"},
    "w.list.remove_tip": {"zh": "移除", "en": "Remove"},
    "w.list.pick_first_export": {"zh": "先勾选要导出的文件", "en": "Check some files to export first"},
    "w.list.pick_first_delete": {"zh": "先勾选要删除的文件", "en": "Check some files to delete first"},

    # ── 简谱预览页 ───────────────────────────────────────────────────────────
    "w.jp.title": {"zh": "简谱文件", "en": "Jianpu Files"},
    "w.jp.export_tip": {"zh": "导出勾选到文件夹", "en": "Export checked files to a folder"},
    "w.jp.delete_tip": {"zh": "删除勾选（含 MIDI 与编辑文本）",
                        "en": "Delete checked (incl. MIDI & editor text)"},
    "w.jp.empty": {"zh": "Output/ 中还没有简谱 PDF", "en": "No jianpu PDFs in Output/ yet"},
    "w.jp.preview_ph": {"zh": "选中左侧文件预览简谱", "en": "Select a file on the left to preview"},
    "w.jp.midi_tip": {"zh": "播放 MIDI", "en": "Play MIDI"},
    "w.jp.rerender": {"zh": "重渲", "en": "Re-render"},
    "w.jp.rerender_tip": {"zh": "从简谱文本重新渲染", "en": "Re-render from the jianpu text"},
    "w.jp.delete_confirm": {"zh": "删除勾选的 {n} 个简谱（连同 MIDI 与编辑文本）？",
                            "en": "Delete the {n} checked jianpu files (incl. MIDI & editor text)?"},
    "w.jp.export_done": {"zh": "已导出 {n} 个文件到 {dest}", "en": "Exported {n} file(s) to {dest}"},
    "w.jp.rerender_started": {"zh": "正在从简谱文本重新渲染…", "en": "Re-rendering from jianpu text…"},
    "w.jp.rerender_done": {"zh": "重新渲染完成", "en": "Re-render finished"},
    "w.jp.rerender_failed": {"zh": "重新渲染失败：{e}", "en": "Re-render failed: {e}"},
    "w.jp.no_txt": {"zh": "未找到 {name}", "en": "{name} not found"},
    "w.jp.no_midi": {"zh": "未找到 {name}", "en": "{name} not found"},

    # ── 五线谱预览页 ─────────────────────────────────────────────────────────
    "w.st.title": {"zh": "五线谱（MusicXML）", "en": "Staff Scores (MusicXML)"},
    "w.st.export_tip": {"zh": "导出勾选为五线谱 PDF", "en": "Export checked as staff PDFs"},
    "w.st.delete_tip": {"zh": "删除勾选的 MusicXML", "en": "Delete checked MusicXML files"},
    "w.st.empty": {"zh": "xml-scores/ 中还没有 MusicXML", "en": "No MusicXML in xml-scores/ yet"},
    "w.st.preview_ph": {"zh": "选中左侧文件预览五线谱", "en": "Select a file on the left to preview"},
    "w.st.rendering": {"zh": "正在渲染 {name} …（LilyPond）", "en": "Rendering {name} … (LilyPond)"},
    "w.st.render_failed": {"zh": "渲染失败：{e}", "en": "Render failed: {e}"},
    "w.st.transpose": {"zh": "移调", "en": "Transpose"},
    "w.st.transpose_tip": {"zh": "对当前乐谱移调（按调 / 音程 / 度数）",
                           "en": "Transpose the current score (key / interval / degree)"},
    "w.st.midi_tip": {"zh": "播放 MIDI（缺失时可生成）", "en": "Play MIDI (generate if missing)"},
    "w.st.gen_midi_confirm": {"zh": "{name} 不存在。从当前乐谱生成 MIDI 并播放？",
                              "en": "{name} doesn't exist. Generate MIDI from this score and play it?"},
    "w.st.gen_midi_started": {"zh": "正在生成 MIDI…", "en": "Generating MIDI…"},
    "w.st.gen_midi_failed": {"zh": "MIDI 生成/播放失败：{e}", "en": "MIDI generation/playback failed: {e}"},
    "w.st.delete_confirm": {"zh": "删除勾选的 {n} 个 MusicXML？", "en": "Delete the {n} checked MusicXML files?"},
    "w.st.export_started": {"zh": "正在导出（未缓存的将逐个渲染，请稍候）…",
                            "en": "Exporting (uncached scores render one by one, please wait)…"},
    "w.st.export_done": {"zh": "已导出 {n} 个五线谱 PDF 到 {dest}", "en": "Exported {n} staff PDF(s) to {dest}"},
    "w.st.pick_score_first": {"zh": "先选中一个乐谱", "en": "Select a score first"},

    # ── 预览条（共用）────────────────────────────────────────────────────────
    "w.pv.page_info": {"zh": "第 {n} / {t} 页", "en": "Page {n} / {t}"},
    "w.pv.prev": {"zh": "上一页", "en": "Previous page"},
    "w.pv.next": {"zh": "下一页", "en": "Next page"},
    "w.pv.zoomin": {"zh": "放大", "en": "Zoom in"},
    "w.pv.zoomout": {"zh": "缩小", "en": "Zoom out"},
    "w.pv.zoomfit": {"zh": "适应页面", "en": "Fit page"},
    "w.pv.open_failed": {"zh": "PDF 打开失败：{e}", "en": "Failed to open PDF: {e}"},
    "w.pv.cannot_preview": {"zh": "无法预览：{e}", "en": "Cannot preview: {e}"},

    # ── 移调页 ───────────────────────────────────────────────────────────────
    "w.tp.back": {"zh": "返回", "en": "Back"},
    "w.tp.title": {"zh": "移调", "en": "Transpose"},
    "w.tp.mode": {"zh": "模式", "en": "Mode"},
    "w.tp.mode_key": {"zh": "按调（如 C → G）", "en": "By key (e.g. C → G)"},
    "w.tp.mode_interval": {"zh": "按音程（半音级）", "en": "By interval (chromatic)"},
    "w.tp.mode_diatonic": {"zh": "按度数（全音级）", "en": "By degree (diatonic)"},
    "w.tp.from": {"zh": "原调", "en": "From Key"},
    "w.tp.detect": {"zh": "检测", "en": "Detect"},
    "w.tp.detect_tip": {"zh": "从乐谱检测原调", "en": "Detect the key from the score"},
    "w.tp.to": {"zh": "目标调", "en": "To Key"},
    "w.tp.interval": {"zh": "音程", "en": "Interval"},
    "w.tp.degree": {"zh": "度数", "en": "Degree"},
    "w.tp.dir": {"zh": "方向", "en": "Direction"},
    "w.tp.dir_closest": {"zh": "就近", "en": "Closest"},
    "w.tp.dir_up": {"zh": "向上", "en": "Up"},
    "w.tp.dir_down": {"zh": "向下", "en": "Down"},
    "w.tp.keysig": {"zh": "同时移调号", "en": "Transpose key signature too"},
    "w.tp.run": {"zh": "开始移调", "en": "Transpose"},
    "w.tp.export_orig": {"zh": "导出原谱 PDF", "en": "Export Original PDF"},
    "w.tp.export_trans": {"zh": "导出移调 PDF", "en": "Export Transposed PDF"},
    "w.tp.orig_title": {"zh": "原调", "en": "Original"},
    "w.tp.trans_title": {"zh": "移调后", "en": "Transposed"},
    "w.tp.orig_ph": {"zh": "从五线谱预览页选择乐谱进入", "en": "Enter from the Staff page with a score selected"},
    "w.tp.trans_ph": {"zh": "移调后在此预览", "en": "The transposed score previews here"},
    "w.tp.rendering": {"zh": "正在渲染…（LilyPond）", "en": "Rendering… (LilyPond)"},
    "w.tp.rendering_trans": {"zh": "正在渲染移调结果…（LilyPond）", "en": "Rendering the transposed score… (LilyPond)"},
    "w.tp.detected": {"zh": "检测到原调：{key}", "en": "Detected key: {key}"},
    "w.tp.open_failed": {"zh": "打开失败：{e}", "en": "Failed to open: {e}"},
    "w.tp.running": {"zh": "移调中…", "en": "Transposing…"},
    "w.tp.done": {"zh": "完成：{name}", "en": "Done: {name}"},
    "w.tp.failed": {"zh": "移调失败：{e}", "en": "Transposition failed: {e}"},
    "w.tp.load_first": {"zh": "先载入乐谱", "en": "Load a score first"},
    "w.tp.cannot": {"zh": "无法移调：{e}", "en": "Cannot transpose: {e}"},
    "w.tp.exported": {"zh": "已导出：{dest}", "en": "Exported: {dest}"},
    "w.tp.export_failed": {"zh": "导出失败：{e}", "en": "Export failed: {e}"},

    # ── 关于页 ───────────────────────────────────────────────────────────────
    "w.about.subtitle": {"zh": "五线谱识别与简谱生成工具",
                         "en": "Staff Notation Recognition & Jianpu Generation Tool"},
    "w.about.author": {"zh": "作者", "en": "Author"},
    "w.about.homepage": {"zh": "项目主页", "en": "Project Homepage"},
    "w.about.license": {"zh": "开源许可证 (AGPL-3.0)", "en": "Open Source License (AGPL-3.0)"},
    "w.about.license_text": {
        "zh": (
            "GNU Affero General Public License v3.0\n\n"
            "Copyright (c) 2026 Tsukamotoshio\n\n"
            "本程序是自由软件：您可以根据自由软件基金会发布的 GNU Affero 通用公共许可证\n"
            "（第 3 版或更高版本）重新分发或修改它。\n\n"
            "本程序的发布是希望它有用，但不提供任何担保；甚至不提供对适销性或特定用途\n"
            "适用性的隐含担保。详情请参阅 GNU Affero 通用公共许可证。\n\n"
            "您应该已收到 GNU Affero 通用公共许可证的副本；如未收到，请访问：\n"
            "https://www.gnu.org/licenses/agpl-3.0.html"
        ),
        "en": (
            "GNU Affero General Public License v3.0\n\n"
            "Copyright (c) 2026 Tsukamotoshio\n\n"
            "This program is free software: you can redistribute it and/or modify it\n"
            "under the terms of the GNU Affero General Public License as published by\n"
            "the Free Software Foundation, either version 3 of the License, or (at your\n"
            "option) any later version.\n\n"
            "This program is distributed in the hope that it will be useful, but WITHOUT\n"
            "ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or\n"
            "FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License\n"
            "for more details.\n\n"
            "You should have received a copy of the GNU Affero General Public License\n"
            "along with this program. If not, see:\n"
            "https://www.gnu.org/licenses/agpl-3.0.html"
        ),
    },
    "w.about.diag": {"zh": "诊断信息", "en": "Diagnostics"},
    "w.about.diag_hint": {"zh": "报告问题时，请点击下方按钮复制环境信息一并附上。",
                          "en": "When reporting an issue, click below to copy your environment info and include it."},
    "w.about.diag_copy": {"zh": "复制诊断信息", "en": "Copy Diagnostics"},
    "w.about.diag_collecting": {"zh": "正在收集诊断信息…", "en": "Collecting diagnostics…"},
    "w.about.diag_copied": {"zh": "诊断信息已复制到剪贴板", "en": "Diagnostics copied to clipboard"},
    "w.about.diag_failed": {"zh": "复制失败：{e}", "en": "Copy failed: {e}"},

    # ── 转换流程 / 浮层 ──────────────────────────────────────────────────────
    "w.conv.running_score": {"zh": "正在识别乐谱（{n} 个文件）", "en": "Recognizing scores ({n} files)"},
    "w.conv.running_audio": {"zh": "正在识别音频（{n} 个文件）", "en": "Recognizing audio ({n} files)"},
    "w.conv.running": {"zh": "正在识别…", "en": "Recognizing…"},
    "w.conv.cancel": {"zh": "取消", "en": "Cancel"},
    "w.conv.cancelling": {"zh": "取消中…（正在终止 worker）", "en": "Cancelling… (terminating the worker)"},
    "w.conv.no_files": {"zh": "没有勾选的文件。", "en": "No files are checked."},
    "w.conv.busy": {"zh": "已有转换在进行中。", "en": "A conversion is already running."},
    "w.result.title": {"zh": "识别结果", "en": "Results"},
    "w.result.title_error": {"zh": "识别失败", "en": "Recognition Failed"},
    "w.result.success": {"zh": "成功", "en": "Succeeded"},
    "w.result.fallback": {"zh": "引擎回退", "en": "Engine fallback"},
    "w.result.failed": {"zh": "失败", "en": "Failed"},
    "w.result.engine": {"zh": "引擎 {name}", "en": "engine {name}"},
    "w.result.fallback_engine": {"zh": "引擎回退 {name}", "en": "fell back to {name}"},
    "w.result.close": {"zh": "关闭", "en": "Close"},
    "w.result.unknown": {"zh": "未知错误", "en": "Unknown error"},
    "w.model.dl_title_homr": {"zh": "下载 HOMR 模型权重", "en": "Download HOMR Weights"},
    "w.model.dl_title_piano": {"zh": "下载钢琴转录模型（约 172 MB）", "en": "Download Piano Model (~172 MB)"},
    "w.model.connecting": {"zh": "连接中…", "en": "Connecting…"},
    "w.model.dl_failed": {"zh": "模型下载失败：{e}", "en": "Model download failed: {e}"},
    "w.model.del_homr_confirm": {"zh": "删除 HOMR 模型权重？删除后图片识别将不可用，可随时重新下载。",
                                 "en": "Delete the HOMR weights? Image OMR will be unavailable until re-downloaded."},
    "w.model.del_piano_confirm": {"zh": "删除钢琴转录模型？可随时重新下载。",
                                  "en": "Delete the piano transcription model? You can re-download anytime."},
    "w.drop.error": {"zh": "拖拽处理异常：{e}", "en": "Drag-and-drop failed: {e}"},

    # ── 简谱编辑器 ───────────────────────────────────────────────────────────
    "w.ed.title": {"zh": "简谱编辑", "en": "Jianpu Editor"},
    "w.ed.open": {"zh": "打开", "en": "Open"},
    "w.ed.save": {"zh": "保存", "en": "Save"},
    "w.ed.save_tip": {"zh": "保存 (Ctrl+S)", "en": "Save (Ctrl+S)"},
    "w.ed.render": {"zh": "渲染预览", "en": "Render Preview"},
    "w.ed.render_tip": {"zh": "保存并渲染简谱预览（LilyPond）", "en": "Save and render the jianpu preview (LilyPond)"},
    "w.ed.export": {"zh": "导出到输出", "en": "Export to Output"},
    "w.ed.export_tip": {"zh": "把当前预览 PDF 覆写回 Output/", "en": "Overwrite the Output/ PDF with the current preview"},
    "w.ed.symbols": {"zh": "符号参考", "en": "Symbols"},
    "w.ed.ref_tab": {"zh": "参考图", "en": "Reference"},
    "w.ed.preview_tab": {"zh": "简谱预览", "en": "Preview"},
    "w.ed.empty": {"zh": "从「简谱预览」页对某个文件点「编辑」进入，\n或点上方「打开」选择 .jianpu.txt",
                   "en": "Enter via \"Edit\" on a file in the Jianpu page,\nor click \"Open\" to pick a .jianpu.txt"},
    "w.ed.no_source": {"zh": "未找到参考图（原始乐谱图像）", "en": "No reference image found"},
    "w.ed.no_preview_yet": {"zh": "点「渲染预览」查看当前文本的排版效果", "en": "Click \"Render Preview\" to typeset the current text"},
    "w.ed.saved": {"zh": "已保存 {name}", "en": "Saved {name}"},
    "w.ed.save_failed": {"zh": "保存失败：{e}", "en": "Save failed: {e}"},
    "w.ed.exported": {"zh": "已导出：{dest}", "en": "Exported: {dest}"},
    "w.ed.export_need_preview": {"zh": "先「渲染预览」成功后才能导出", "en": "Render a preview first, then export"},
    "w.ed.line_col": {"zh": "行 {l} · 列 {c}", "en": "Ln {l} · Col {c}"},
    "w.ed.dirty": {"zh": "未保存", "en": "Unsaved"},
    "w.ed.unsaved_confirm": {"zh": "有未保存的修改，离开将丢失。确定离开？",
                             "en": "You have unsaved changes that will be lost. Leave anyway?"},
}


def merged_catalog() -> dict[str, dict[str, str]]:
    """gui/strings.py 的 STRINGS + 本目录（w.* 优先）——交给前端的完整目录。"""
    from gui.strings import STRINGS
    return {**STRINGS, **WEBUI_STRINGS}
