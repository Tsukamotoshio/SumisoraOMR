# gui/pages/tie_reconstruction_page.py — 延音线视觉重建页面
# 布局：左侧文件选择 + 控制区；右侧审核区（展示模糊切片供人工判断）。

from __future__ import annotations

import base64
import re
import threading
from pathlib import Path
from typing import Optional

import flet as ft

from ..app_state import AppState, Event
from core.app.backend import build_dir, output_dir, open_directory, editor_workspace_dir
from ..theme import Palette, section_title


class TieReconstructionPage(ft.Column):
    """延音线视觉重建页面：使用 oemer / CV 对 homr MusicXML 补写延音线。"""

    def __init__(self, state: AppState):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._mxl_path:   Optional[Path] = None
        self._image_path: Optional[Path] = None
        self._result = None           # TieReconstructionResult
        self._has_been_shown = False
        self._run_token: int = 0
        self._file_picker_mxl      = ft.FilePicker()
        self._file_picker_image    = ft.FilePicker()
        self._file_picker_jianpu   = ft.FilePicker()   # 导出简谱 PDF
        self._file_picker_sheet    = ft.FilePicker()   # 导出五线谱 PDF
        self._build_ui()
        state.on(Event.MXL_READY,      self._on_mxl_ready)
        state.on(Event.FILE_SELECTED,  self._on_file_selected)

    # ── did_mount ─────────────────────────────────────────────────────────────

    def did_mount(self):
        self.page._services.register_service(self._file_picker_mxl)
        self.page._services.register_service(self._file_picker_image)
        self.page._services.register_service(self._file_picker_jianpu)
        self.page._services.register_service(self._file_picker_sheet)

    def reset_view(self) -> None:
        pass  # 无需重置

    # ── 构建 UI ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # ── 文件选择区 ───────────────────────────────────────────────────────
        self._mxl_label = ft.Text(
            '未选择',
            size=12, color=Palette.TEXT_SECONDARY, expand=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._image_label = ft.Text(
            '未选择',
            size=12, color=Palette.TEXT_SECONDARY, expand=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._image_hint = ft.Text(
            '',
            size=11, color=Palette.TEXT_SECONDARY,
            visible=False,
        )

        mxl_row = ft.Row([
            ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, size=16, color=Palette.TEXT_SECONDARY),
            self._mxl_label,
            ft.TextButton(
                '浏览',
                on_click=lambda _e: self.page.run_task(self._pick_mxl_async),
                style=ft.ButtonStyle(color=Palette.PRIMARY),
            ),
        ], spacing=6)

        image_row = ft.Row([
            ft.Icon(ft.Icons.IMAGE_OUTLINED, size=16, color=Palette.TEXT_SECONDARY),
            self._image_label,
            ft.TextButton(
                '浏览',
                on_click=lambda _e: self.page.run_task(self._pick_image_async),
                style=ft.ButtonStyle(color=Palette.PRIMARY),
            ),
        ], spacing=6)

        # ── 操作按钮 ─────────────────────────────────────────────────────────
        self._start_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, size=18), ft.Text('开始延音线重建')],
                tight=True, spacing=6,
            ),
            bgcolor=Palette.PRIMARY,
            color='#FFFFFF',
            on_click=self._on_start,
            disabled=True,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                elevation={ft.ControlState.PRESSED: 0, ft.ControlState.DEFAULT: 2},
            ),
        )

        self._save_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.SAVE_ROUNDED, size=16), ft.Text('保存修改')],
                tight=True, spacing=6,
            ),
            on_click=self._on_save,
            disabled=True,
            style=ft.ButtonStyle(
                color=Palette.TEXT_SECONDARY,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, Palette.DIVIDER_DARK)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )
        self._export_jianpu_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.PICTURE_AS_PDF_ROUNDED, size=16), ft.Text('导出简谱')],
                tight=True, spacing=6,
            ),
            on_click=self._on_export_jianpu,
            disabled=True,
            style=ft.ButtonStyle(
                color=Palette.TEXT_SECONDARY,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, Palette.DIVIDER_DARK)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )
        self._export_sheet_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.PICTURE_AS_PDF_OUTLINED, size=16), ft.Text('导出五线谱')],
                tight=True, spacing=6,
            ),
            on_click=self._on_export_sheet,
            disabled=True,
            style=ft.ButtonStyle(
                color=Palette.TEXT_SECONDARY,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, Palette.DIVIDER_DARK)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        self._open_output_btn = ft.TextButton(
            '打开输出目录',
            icon=ft.Icons.FOLDER_OPEN_ROUNDED,
            on_click=lambda _: open_directory(build_dir()),
            style=ft.ButtonStyle(color=Palette.TEXT_SECONDARY),
        )

        # ── oemer 状态指示 ───────────────────────────────────────────────────
        self._oemer_status = ft.Text(
            self._get_oemer_status_text(),
            size=11, color=Palette.TEXT_SECONDARY,
        )

        # ── 统计摘要 ─────────────────────────────────────────────────────────
        self._stats_text = ft.Text(
            '',
            size=12, color=Palette.TEXT_SECONDARY,
        )

        # ── 日志区 ───────────────────────────────────────────────────────────
        self._log_view = ft.ListView(
            spacing=2,
            expand=True,
            auto_scroll=True,
        )

        log_container = ft.Container(
            content=self._log_view,
            bgcolor=Palette.BG_INPUT,
            border_radius=ft.BorderRadius.all(6),
            padding=ft.Padding.all(8),
            height=130,
            border=ft.Border.all(1, Palette.DIVIDER_DARK),
        )

        # ── 进度条 ───────────────────────────────────────────────────────────
        self._progress_bar = ft.ProgressBar(
            value=None,       # None = 不确定（动画）
            visible=False,
            bgcolor=Palette.BG_INPUT,
            color=Palette.PRIMARY,
        )
        self._progress_text = ft.Text('', size=11, color=Palette.TEXT_SECONDARY)

        # ── 左侧控制面板 ─────────────────────────────────────────────────────
        left_panel = ft.Container(
            content=ft.Column(
                [
                    section_title('输入文件'),
                    ft.Container(height=4),
                    ft.Text('MusicXML / MXL（homr 输出）', size=12, color=Palette.TEXT_PRIMARY),
                    mxl_row,
                    ft.Container(height=6),
                    ft.Text('预处理图像 / PDF（自动联动）', size=12, color=Palette.TEXT_PRIMARY),
                    image_row,
                    self._image_hint,
                    ft.Divider(height=1, color=Palette.DIVIDER_DARK),
                    section_title('识别引擎'),
                    ft.Container(height=4),
                    self._oemer_status,
                    ft.Divider(height=1, color=Palette.DIVIDER_DARK),
                    self._start_btn,
                    ft.Container(height=4),
                    ft.Row([self._save_btn, self._export_jianpu_btn, self._export_sheet_btn], spacing=6, wrap=True),
                    ft.Row([self._open_output_btn], spacing=8),
                    ft.Divider(height=1, color=Palette.DIVIDER_DARK),
                    self._progress_bar,
                    self._progress_text,
                    self._stats_text,
                    ft.Container(height=4),
                    section_title('日志'),
                    log_container,
                ],
                spacing=8,
                scroll=ft.ScrollMode.AUTO,
            ),
            bgcolor=Palette.BG_SURFACE,
            padding=ft.Padding.all(16),
            width=280,
            border=ft.Border.only(right=ft.BorderSide(1, Palette.DIVIDER_DARK)),
        )

        # ── 右侧审核面板 ─────────────────────────────────────────────────────
        self._review_header = ft.Text(
            '审核区',
            size=14, weight=ft.FontWeight.W_600, color=Palette.TEXT_PRIMARY,
        )
        self._review_subtitle = ft.Text(
            '所有检测到的候选均列在此处（含 oemer 已判断的）。绿色=已确认，红色=已拒绝，橙色=跨行，蓝色=模糊。点击按钮可覆盖 oemer 判断。',
            size=12, color=Palette.TEXT_SECONDARY,
        )
        self._review_list = ft.ListView(
            spacing=12,
            expand=True,
            padding=ft.Padding.all(16),
        )

        right_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Column(
                            [self._review_header, self._review_subtitle],
                            spacing=4,
                        ),
                        padding=ft.Padding.only(left=16, top=16, right=16, bottom=8),
                    ),
                    ft.Divider(height=1, color=Palette.DIVIDER_DARK),
                    self._review_list,
                ],
                spacing=0,
                expand=True,
            ),
            expand=True,
        )

        self.controls = [
            ft.Row(
                [left_panel, right_panel],
                spacing=0,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            )
        ]
        self.expand = True

    # ── 状态更新辅助 ──────────────────────────────────────────────────────────

    def _get_oemer_status_text(self) -> str:
        try:
            from core.music.oemer_tie_reconstruction import _oemer_models_available, _get_oemer_dir
            oemer_dir = _get_oemer_dir()
            if oemer_dir is None:
                return '✗ oemer 源码未找到，使用 CV 弧形检测'
            if _oemer_models_available():
                return '✓ oemer 神经网络模型已就绪'
            else:
                return '△ oemer 源码已就绪，模型权重（model.onnx）未下载\n  → 当前使用 CV 弧形检测回退'
        except Exception:
            return '△ oemer 状态检查失败，使用 CV 弧形检测'

    def _update_start_btn(self) -> None:
        can_start = self._mxl_path is not None and self._image_path is not None
        self._start_btn.disabled = not can_start
        try:
            self._start_btn.update()
        except Exception:
            pass

    def _append_log(self, msg: str) -> None:
        self._log_view.controls.append(
            ft.Text(msg, size=11, color=Palette.TEXT_SECONDARY, selectable=True)
        )
        try:
            self._log_view.update()
        except Exception:
            pass

    def _set_progress(self, visible: bool, value: Optional[float] = None, text: str = '') -> None:
        self._progress_bar.visible = visible
        self._progress_bar.value   = value
        self._progress_text.value  = text
        try:
            self._progress_bar.update()
            self._progress_text.update()
        except Exception:
            pass

    # ── 文件选择（Flet 0.84 async 模式）─────────────────────────────────────

    _IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.pdf']
    # 去除转调/增强前缀，提取原始文件基础名
    _STEM_STRIP_RE = re.compile(
        r'(_transposed_[A-Gb#]+|_trans_preview|_orig_preview|'
        r'(?:_page\d+)+|_staff|_tiefix)$',
        re.IGNORECASE,
    )

    def _candidate_bases(self, stem: str) -> list:
        """从 MXL stem 推导出可能的图像基础名列表（最精确的在前）。"""
        bases = [stem]
        s = stem
        while True:
            m = self._STEM_STRIP_RE.search(s)
            if not m:
                break
            s = s[:m.start()]
            if s and s not in bases:
                bases.append(s)
        return bases

    def _try_auto_match_image(self) -> None:
        """尝试在 MXL 同目录 + editor-workspace 下找同名图像/PDF，自动填充。

        搜索策略（按优先级）：
        1. 同目录：{stem}.source.{ext}
        2. editor-workspace：{base}.source.{ext}（剥离 _transposed 等后缀）
        3. 同目录：{stem}{ext}
        4. editor-workspace：{base}{ext}
        """
        if self._mxl_path is None:
            return
        stem = self._mxl_path.stem
        same_dir = self._mxl_path.parent
        try:
            ws_dir = editor_workspace_dir()
        except Exception:
            ws_dir = None

        bases = self._candidate_bases(stem)

        def _check(folder: Path, name_stem: str) -> Optional[Path]:
            for ext in self._IMAGE_EXTS:
                p = folder / (name_stem + '.source' + ext)
                if p.exists():
                    return p
            for ext in self._IMAGE_EXTS:
                p = folder / (name_stem + ext)
                if p.exists():
                    return p
            return None

        found: Optional[Path] = None
        for base in bases:
            found = _check(same_dir, base)
            if found:
                break
            if ws_dir and ws_dir.exists():
                found = _check(ws_dir, base)
                if found:
                    break

        if found:
            self._image_path = found
            self._image_label.value = found.name
            self._image_hint.value = f'✓ 已自动匹配（{found.parent.name}/）'
            self._image_hint.color = Palette.SUCCESS
            self._image_hint.visible = True
        else:
            self._image_hint.value = '⚠ 未找到同名文件，请手动选择'
            self._image_hint.color = Palette.TEXT_SECONDARY
            self._image_hint.visible = True
        try:
            self._image_label.update()
            self._image_hint.update()
        except Exception:
            pass
        self._update_start_btn()

    async def _pick_mxl_async(self) -> None:
        files = await self._file_picker_mxl.pick_files(
            dialog_title='选择 homr 输出的 MusicXML / MXL',
            allowed_extensions=['musicxml', 'mxl', 'xml'],
            allow_multiple=False,
        )
        if not files:
            return
        self._mxl_path = Path(files[0].path)
        self._mxl_label.value = self._mxl_path.name
        try:
            self._mxl_label.update()
        except Exception:
            pass
        self._try_auto_match_image()
        self._update_start_btn()

    async def _pick_image_async(self) -> None:
        files = await self._file_picker_image.pick_files(
            dialog_title='选择预处理后的原始图像 / PDF',
            allowed_extensions=['png', 'jpg', 'jpeg', 'pdf'],
            allow_multiple=False,
        )
        if not files:
            return
        self._image_path = Path(files[0].path)
        self._image_label.value = self._image_path.name
        self._image_hint.value = ''
        self._image_hint.visible = False
        try:
            self._image_label.update()
            self._image_hint.update()
        except Exception:
            pass
        self._update_start_btn()

    # ── 事件监听：接收来自 landing_page 的 MXL 就绪通知 ─────────────────────

    def _on_mxl_ready(self, path: Path, **_kw) -> None:
        self._mxl_path = path
        self._mxl_label.value = path.name
        try:
            self._mxl_label.update()
        except Exception:
            pass
        self._try_auto_match_image()
        self._update_start_btn()

    def _on_file_selected(self, path: Path, **_kw) -> None:
        """当用户在文件侧边栏选中图像文件时，自动填充图像路径。"""
        if path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.pdf'):
            self._image_path = path
            self._image_label.value = path.name
            self._image_hint.value = ''
            self._image_hint.visible = False
            try:
                self._image_label.update()
                self._image_hint.update()
            except Exception:
                pass
            self._update_start_btn()

    # ── 开始重建 ──────────────────────────────────────────────────────────────

    def _on_start(self, _e=None) -> None:
        if self._mxl_path is None or self._image_path is None:
            return
        self._run_token += 1
        token = self._run_token
        self._start_btn.disabled = True
        self._save_btn.disabled = True
        self._export_jianpu_btn.disabled = True
        self._export_sheet_btn.disabled = True
        self._result = None
        self._review_list.controls.clear()
        self._stats_text.value = ''
        self._log_view.controls.clear()
        self._set_progress(visible=True, value=None, text='正在分析…')
        try:
            self._start_btn.update()
            self._save_btn.update()
            self._export_jianpu_btn.update()
            self._export_sheet_btn.update()
            self._stats_text.update()
        except Exception:
            pass
        try:
            self.page.update()
        except Exception:
            pass

        threading.Thread(
            target=self._run_reconstruction,
            args=(token,),
            daemon=True,
        ).start()

    def _run_reconstruction(self, token: int) -> None:
        import tempfile, shutil
        from core.music.oemer_tie_reconstruction import run_oemer_tie_reconstruction

        mxl_path   = self._mxl_path
        image_path = self._image_path

        # ── 图像预处理（超分辨率 + 增强）────────────────────────────────────
        # 仅对光栅图像做预处理；PDF 由 _load_image_bgr 内部通过 PyMuPDF 读取。
        _preproc_tmp: Optional[str] = None
        if image_path is not None and image_path.suffix.lower() in ('.png', '.jpg', '.jpeg'):
            try:
                from core.image.image_preprocess import preprocess_image_for_omr
                async def _log_preproc():
                    self._append_log('[预处理] 正在执行超分辨率 + 图像增强…')
                    self._set_progress(True, None, '正在预处理图像…')
                try:
                    if self.page:
                        self.page.run_task(_log_preproc)
                except Exception:
                    pass
                _preproc_tmp = tempfile.mkdtemp(prefix='_tie_preproc_')
                enhanced = preprocess_image_for_omr(
                    image_path=image_path,
                    work_dir=Path(_preproc_tmp),
                    keep_color=True,
                )
                if enhanced is not None:
                    image_path = enhanced
                    async def _log_ok():
                        self._append_log(f'[预处理] 完成 → {enhanced.name}')
                    try:
                        if self.page:
                            self.page.run_task(_log_ok)
                    except Exception:
                        pass
                else:
                    async def _log_skip():
                        self._append_log('[预处理] 跳过（Pillow 不可用或格式不支持），使用原始图像')
                    try:
                        if self.page:
                            self.page.run_task(_log_skip)
                    except Exception:
                        pass
            except Exception as _exc:
                async def _log_err(_e=_exc):
                    self._append_log(f'[预处理] 失败（{_e}），使用原始图像')
                try:
                    if self.page:
                        self.page.run_task(_log_err)
                except Exception:
                    pass

        def _progress_cb(done: int, total: int) -> None:
            if token != self._run_token:
                return
            frac = done / total if total > 0 else 0.0
            async def _do():
                self._set_progress(True, frac, f'处理中 {done}/{total}…')
            try:
                if self.page:
                    self.page.run_task(_do)
            except Exception:
                pass

        try:
            result = run_oemer_tie_reconstruction(
                mxl_path=mxl_path,
                image_path=image_path,
                progress_cb=_progress_cb,
            )
        except Exception as exc:
            async def _show_error():
                self._append_log(f'[错误] {exc}')
                self._set_progress(False)
                self._start_btn.disabled = False
                try:
                    self._start_btn.update()
                except Exception:
                    pass
            try:
                if self.page and token == self._run_token:
                    self.page.run_task(_show_error)
            except Exception:
                pass
            finally:
                if _preproc_tmp:
                    shutil.rmtree(_preproc_tmp, ignore_errors=True)
            return

        # 清理预处理临时目录
        if _preproc_tmp:
            shutil.rmtree(_preproc_tmp, ignore_errors=True)
            _preproc_tmp = None

        if token != self._run_token:
            return

        async def _show_result():
            self._result = result
            self._set_progress(False, text='')
            s = result.stats
            self._stats_text.value = (
                f'候选: {s["total_candidates"]}  |  '
                f'确认: {s["confirmed_ties"]}  |  '
                f'拒绝: {s["rejected"]}  |  '
                f'待审: {s["linebreak"] + s["ambiguous"]}'
            )
            self._stats_text.update()

            self._append_log(
                f'分析完成 — '
                f'候选 {s["total_candidates"]} 对 / '
                f'确认 tie {s["confirmed_ties"]} / '
                f'拒绝 {s["rejected"]} / '
                f'换行 {s["linebreak"]} / '
                f'模糊 {s["ambiguous"]}'
            )

            # 填充审核区
            self._review_list.controls.clear()
            if not result.review_items:
                self._review_list.controls.append(
                    ft.Container(
                        content=ft.Text(
                            '此乐谱中未找到延音线候选音符对（可能无需补充延音线，或乐谱结构特殊）。',
                            size=13, color=Palette.TEXT_SECONDARY,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        alignment=ft.alignment.center,
                        expand=True,
                        padding=ft.padding.all(40),
                    )
                )
            else:
                for item in result.review_items:
                    try:
                        card = self._make_review_card(item)
                        self._review_list.controls.append(card)
                    except Exception as exc:
                        self._append_log(f'[警告] 卡片创建失败: {exc}')

            self._start_btn.disabled = False
            self._save_btn.disabled = False
            self._export_jianpu_btn.disabled = False
            self._export_sheet_btn.disabled = False
            try:
                self._start_btn.update()
                self._save_btn.update()
                self._export_jianpu_btn.update()
                self._export_sheet_btn.update()
            except Exception:
                pass
            try:
                self.page.update()
            except Exception:
                pass

        try:
            if self.page:
                self.page.run_task(_show_result)
        except Exception:
            pass

    # ── 审核卡片 ──────────────────────────────────────────────────────────────

    def _make_review_card(self, item) -> ft.Container:
        """为一个 TieReviewItem 构建审核卡片控件。"""
        from core.music.oemer_tie_reconstruction import TieDecisionKind

        cand = item.candidate

        # 标题
        title_text = ft.Text(
            cand.label,
            size=13, weight=ft.FontWeight.W_600, color=Palette.TEXT_PRIMARY,
        )

        # 状态标签
        if item.kind == TieDecisionKind.LINEBREAK:
            kind_label = ft.Container(
                content=ft.Text('换行', size=11, color='#FFFFFF'),
                bgcolor=Palette.WARNING,
                border_radius=ft.BorderRadius.all(4),
                padding=ft.Padding.symmetric(horizontal=6, vertical=2),
            )
            hint = '此候选跨越谱行换行，无法自动切片检测。请查看原乐谱确认是否为延音线。'
        elif item.kind == TieDecisionKind.CONFIRMED_TIE:
            kind_label = ft.Container(
                content=ft.Text('oemer 确认', size=11, color='#FFFFFF'),
                bgcolor=Palette.SUCCESS,
                border_radius=ft.BorderRadius.all(4),
                padding=ft.Padding.symmetric(horizontal=6, vertical=2),
            )
            hint = 'oemer 检测认为是延音线。可根据切片图像覆盖判断。'
        elif item.kind == TieDecisionKind.REJECTED:
            kind_label = ft.Container(
                content=ft.Text('oemer 拒绝', size=11, color='#FFFFFF'),
                bgcolor=Palette.ERROR,
                border_radius=ft.BorderRadius.all(4),
                padding=ft.Padding.symmetric(horizontal=6, vertical=2),
            )
            hint = 'oemer 检测认为不是延音线。可根据切片图像覆盖判断。'
        else:
            kind_label = ft.Container(
                content=ft.Text('模糊', size=11, color='#FFFFFF'),
                bgcolor=Palette.INFO,
                border_radius=ft.BorderRadius.all(4),
                padding=ft.Padding.symmetric(horizontal=6, vertical=2),
            )
            hint = '自动检测未能确定。请根据下方切片图像判断两音符之间是否有延音线弧。'

        # 音符详情
        detail_text = ft.Text(
            f'声部 {cand.voice_id} · 小节 {cand.measure_a+1}→{cand.measure_b+1} · '
            f'时值 A:{cand.duration_a} B:{cand.duration_b} ticks',
            size=11, color=Palette.TEXT_SECONDARY,
        )

        # 裁剪图像（若有）
        crop_widget: ft.Control
        if item.crop_bytes:
            b64 = base64.b64encode(item.crop_bytes).decode()
            crop_widget = ft.Container(
                content=ft.Image(
                    src=b64,          # Flet 0.84: src 接受 base64 字符串
                    fit=ft.BoxFit.CONTAIN,
                    height=120,
                    expand=True,
                ),
                bgcolor='#1A1A1A',
                border_radius=ft.BorderRadius.all(4),
                border=ft.Border.all(1, Palette.DIVIDER_DARK),
                height=120,
                expand=True,
            )
        else:
            crop_widget = ft.Container(
                content=ft.Text(
                    '（无切片图像，请参考原乐谱）',
                    size=11, color=Palette.TEXT_DISABLED,
                    text_align=ft.TextAlign.CENTER,
                ),
                bgcolor=Palette.BG_INPUT,
                border_radius=ft.BorderRadius.all(4),
                alignment=ft.Alignment(0, 0),
                height=60,
                expand=True,
            )

        # 决定指示器（动态更新）——根据预选状态初始化
        if item.user_decision is True:
            _init_ind_text  = '✓ 是延音线'
            _init_ind_color = Palette.SUCCESS
        elif item.user_decision is False:
            _init_ind_text  = '✗ 不是延音线'
            _init_ind_color = Palette.ERROR
        else:
            _init_ind_text  = '待判断'
            _init_ind_color = Palette.TEXT_SECONDARY
        decision_indicator = ft.Text(
            _init_ind_text,
            size=12, color=_init_ind_color,
        )

        def _on_yes(_e, _item=item, _ind=decision_indicator):
            _item.user_decision = True
            _ind.value = '✓ 是延音线'
            _ind.color = Palette.SUCCESS
            try:
                _ind.update()
            except Exception:
                pass

        def _on_no(_e, _item=item, _ind=decision_indicator):
            _item.user_decision = False
            _ind.value = '✗ 不是延音线'
            _ind.color = Palette.ERROR
            try:
                _ind.update()
            except Exception:
                pass

        btn_yes = ft.OutlinedButton(
            '是延音线',
            icon=ft.Icons.CHECK_ROUNDED,
            on_click=_on_yes,
            style=ft.ButtonStyle(
                color=Palette.SUCCESS,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, Palette.SUCCESS)},
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
        )
        btn_no = ft.OutlinedButton(
            '不是延音线',
            icon=ft.Icons.CLOSE_ROUNDED,
            on_click=_on_no,
            style=ft.ButtonStyle(
                color=Palette.ERROR,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, Palette.ERROR)},
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
        )

        card = ft.Container(
            content=ft.Column(
                [
                    ft.Row([title_text, kind_label], spacing=8),
                    detail_text,
                    ft.Text(hint, size=11, color=Palette.TEXT_SECONDARY),
                    crop_widget,
                    ft.Row(
                        [btn_yes, btn_no, ft.Container(expand=True), decision_indicator],
                        alignment=ft.MainAxisAlignment.START,
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=8,
            ),
            bgcolor=Palette.BG_CARD,
            border_radius=ft.BorderRadius.all(8),
            padding=ft.Padding.all(14),
            border=ft.Border.all(1, Palette.DIVIDER_DARK),
        )
        return card

    # ── 保存 / 导出 ───────────────────────────────────────────────────────────

    def _apply_decisions_to_xml(self, output_path: Path) -> int:
        """将审核区的最终决定写入 XML 并保存，返回写入 tie 对数。"""
        from core.music.oemer_tie_reconstruction import apply_confirmed_ties
        final_confirmed = [
            item.candidate
            for item in self._result.review_items
            if item.user_decision is True
        ]
        self._result.confirmed.clear()
        self._result.confirmed.extend(final_confirmed)
        return apply_confirmed_ties(
            result=self._result,
            output_path=output_path,
            extra_confirmed=None,
        )

    def _async_log_ok(self, msg: str) -> None:
        async def _ok():
            self._append_log(msg)
        try:
            if self.page:
                self.page.run_task(_ok)
        except Exception:
            pass

    def _async_log_err(self, msg: str) -> None:
        async def _err():
            self._append_log(f'[错误] {msg}')
        try:
            if self.page:
                self.page.run_task(_err)
        except Exception:
            pass

    # ── 保存修改（自动保存为 *_tiefix.musicxml） ─────────────────────────────

    def _on_save(self, _e=None) -> None:
        if self._result is None or self._mxl_path is None:
            return
        save_path = self._mxl_path.parent / (self._mxl_path.stem + '_tiefix.musicxml')
        threading.Thread(target=self._save_thread, args=(save_path,), daemon=True).start()

    def _save_thread(self, save_path: Path) -> None:
        try:
            n = self._apply_decisions_to_xml(save_path)
            self._async_log_ok(f'已保存: {save_path.name}（{n} 对延音线）')
        except Exception as exc:
            self._async_log_err(str(exc))

    # ── 导出简谱 PDF ─────────────────────────────────────────────────────────

    def _on_export_jianpu(self, _e=None) -> None:
        if self._result is None or self._mxl_path is None:
            return
        self.page.run_task(self._pick_jianpu_async)

    async def _pick_jianpu_async(self) -> None:
        stem = self._mxl_path.stem
        path_str = await self._file_picker_jianpu.save_file(
            dialog_title='导出简谱 PDF',
            file_name=f'{stem}_jianpu.pdf',
            allowed_extensions=['pdf'],
        )
        if not path_str:
            return
        pdf_path = Path(path_str)
        if not pdf_path.suffix:
            pdf_path = pdf_path.with_suffix('.pdf')
        threading.Thread(target=self._export_jianpu_thread, args=(pdf_path,), daemon=True).start()

    def _export_jianpu_thread(self, pdf_path: Path) -> None:
        import tempfile
        from core.render.renderer import generate_jianpu_pdf_from_mxl
        try:
            with tempfile.TemporaryDirectory(prefix='_tiefix_jianpu_') as tmpdir:
                tmp_xml  = Path(tmpdir) / 'tiefix.musicxml'
                tmp_work = Path(tmpdir) / 'work'
                tmp_work.mkdir()
                n = self._apply_decisions_to_xml(tmp_xml)
                ok = generate_jianpu_pdf_from_mxl(
                    mxl_path=tmp_xml,
                    output_pdf_path=pdf_path,
                    temp_dir=tmp_work,
                )
                if ok:
                    self._async_log_ok(f'已导出简谱 PDF: {pdf_path.name}（{n} 对延音线）')
                else:
                    self._async_log_err(f'简谱 PDF 生成失败，请检查 LilyPond 是否可用')
        except Exception as exc:
            self._async_log_err(f'导出简谱失败: {exc}')

    # ── 导出五线谱 PDF ───────────────────────────────────────────────────────

    def _on_export_sheet(self, _e=None) -> None:
        if self._result is None or self._mxl_path is None:
            return
        self.page.run_task(self._pick_sheet_async)

    async def _pick_sheet_async(self) -> None:
        stem = self._mxl_path.stem
        path_str = await self._file_picker_sheet.save_file(
            dialog_title='导出五线谱 PDF',
            file_name=f'{stem}_sheet.pdf',
            allowed_extensions=['pdf'],
        )
        if not path_str:
            return
        pdf_path = Path(path_str)
        if not pdf_path.suffix:
            pdf_path = pdf_path.with_suffix('.pdf')
        threading.Thread(target=self._export_sheet_thread, args=(pdf_path,), daemon=True).start()

    def _export_sheet_thread(self, pdf_path: Path) -> None:
        import shutil
        import tempfile
        from music21 import converter as m21converter, environment as m21env
        from core.render.lilypond_runner import find_lilypond_executable, render_lilypond_pdf
        try:
            lp = find_lilypond_executable()
            if not lp:
                self._async_log_err('未找到 LilyPond，无法导出五线谱 PDF')
                return

            # 配置 music21 的 LilyPond 路径
            try:
                m21env.set('lilypondPath', lp)
            except Exception:
                pass

            with tempfile.TemporaryDirectory(prefix='_tiefix_sheet_') as tmpdir:
                tmp_xml = Path(tmpdir) / 'tiefix.musicxml'
                n = self._apply_decisions_to_xml(tmp_xml)

                # music21 解析并导出为 LilyPond
                score = m21converter.parse(str(tmp_xml))
                ly_path = Path(tmpdir) / 'sheet.ly'
                score.write('lily', fp=str(ly_path))
                # music21 可能在文件名后追加扩展名
                if not ly_path.exists():
                    alts = list(Path(tmpdir).glob('sheet*.ly'))
                    if alts:
                        ly_path = alts[0]
                    else:
                        raise FileNotFoundError('music21 未生成 .ly 文件')

                rendered = render_lilypond_pdf(ly_path)
                if rendered and rendered.exists():
                    shutil.copy(str(rendered), str(pdf_path))
                    self._async_log_ok(f'已导出五线谱 PDF: {pdf_path.name}（{n} 对延音线）')
                else:
                    self._async_log_err('LilyPond 渲染五线谱失败')
        except Exception as exc:
            self._async_log_err(f'导出五线谱失败: {exc}')
