# gui/pages/landing_page.py — File management + PDF preview page (Landing Page).
# Left panel: pinned file sidebar; right panel: preview area + conversion button.

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import flet as ft

from ..app_state import AppState, Event
from ..worker_launcher import ConversionOptions, ConversionRunner
from core.app.backend import app_base_dir, output_dir, open_directory
from ..components.file_sidebar import FileSidebar
from ..components.pdf_viewer import PdfViewer
from ..components.progress_overlay import ProgressOverlay
from ..theme import Palette, section_title, FONT_EMPHASIS
from ..strings import t


class LandingPage(ft.Row):
    """Landing page: file management + PDF preview + "Start Conversion" button."""

    def __init__(self, state: AppState, overlay: ProgressOverlay):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._overlay = overlay
        self._scan_token: int = 0
        self._preloaded_paths: set[Path] = set()
        # 转换子进程编排全部委托给 ConversionRunner（gui/worker_launcher.py）；
        # 页面只提供两个回调：子进度 → 进度浮层，批次结束 → 结果对话框。
        self._runner = ConversionRunner(
            state,
            on_sub_progress=overlay.set_sub_progress,
            on_finished=self._schedule_show_results,
        )
        self._build_ui()
        state.on(Event.FILE_SELECTED,   self._on_file_selected)
        state.on(Event.FILES_CHANGED,   self._on_files_changed)
        state.on(Event.FILES_IMPORTED,  self._on_files_imported)
        state.on(Event.MODELS_DOWNLOADED, self._refresh_engine_labels)
        state.on(Event.FILES_CHANGED,       self._refresh_convert_label)
        state.on(Event.FILES_CHECK_CHANGED, self._refresh_convert_label)

    def _build_engine_options(self) -> list:
        suffix = '' if self._state.homr_available else t("landing.suffix_download_needed")
        return [
            ft.dropdown.Option('auto',      t("landing.option_auto", suffix=suffix)),
            ft.dropdown.Option('audiveris', t("landing.option_audiveris")),
            ft.dropdown.Option('homr',      t("landing.option_homr", suffix=suffix)),
        ]

    def _build_ui(self) -> None:
        self._sidebar = FileSidebar(self._state)
        self._viewer = PdfViewer()

        # 引擎选择 — 没有下载触发；触发改为「点击开始转换」时检查（_on_convert）。
        self._engine_dd = ft.Dropdown(
            label=t("landing.label_omr_engine"),
            value='auto',
            options=self._build_engine_options(),
            text_size=14,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            color=ft.Colors.ON_SURFACE,
            border_color=Palette.BORDER_BLUE,
            focused_border_color=Palette.PRIMARY,
            tooltip=t("landing.tooltip_omr_engine"),
        )

        # 超分辨率算法选择
        self._sr_engine_dd = ft.Dropdown(
            label=t("landing.label_sr_engine"),
            value='realesrgan',
            options=[
                ft.dropdown.Option('waifu2x',     t("landing.option_waifu2x")),
                ft.dropdown.Option('realesrgan',  t("landing.option_realesrgan")),
            ],
            text_size=14,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            color=ft.Colors.ON_SURFACE,
            border_color=Palette.BORDER_BLUE,
            focused_border_color=Palette.PRIMARY,
            tooltip=t("landing.tooltip_sr_engine"),
        )

        # 并发处理数（高端机加速；默认 auto = 按核数自动选择）
        self._parallel_dd = ft.Dropdown(
            label=t("landing.label_concurrency"),
            value='auto',
            options=[
                ft.dropdown.Option('1',    t("landing.option_concurrency_1")),
                ft.dropdown.Option('2',    t("landing.option_concurrency_2")),
                ft.dropdown.Option('4',    t("landing.option_concurrency_4")),
                ft.dropdown.Option('auto', t("landing.option_concurrency_auto")),
            ],
            text_size=14,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            color=ft.Colors.ON_SURFACE,
            border_color=Palette.BORDER_BLUE,
            focused_border_color=Palette.PRIMARY,
            tooltip=t("landing.tooltip_concurrency"),
        )

        self._convert_label = ft.Text(t("landing.button_start_convert"), size=15, font_family=FONT_EMPHASIS)
        self._convert_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, size=20), self._convert_label],
                tight=True, spacing=6,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            height=44,
            bgcolor=Palette.PRIMARY,
            color='#FFFFFF',
            on_click=self._on_convert,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                elevation={ft.ControlState.PRESSED: 0, ft.ControlState.DEFAULT: 2},
            ),
        )

        self._open_output_label = ft.Text(t("landing.button_open_output_dir"))
        open_output_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.FOLDER_OPEN_ROUNDED, size=16), self._open_output_label],
                tight=True, spacing=6,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            on_click=self._on_open_output_dir,
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        # HOMR 模型权重的显式管理按钮
        self._download_models_label = ft.Text(t("landing.button_download_models"))
        self._download_models_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.DOWNLOAD_ROUNDED, size=16), self._download_models_label],
                tight=True, spacing=6,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            on_click=self._on_download_models,
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )
        self._delete_models_label = ft.Text(t("landing.button_delete_models"))
        self._delete_models_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.DELETE_OUTLINE_ROUNDED, size=16), self._delete_models_label],
                tight=True, spacing=6,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            on_click=self._on_delete_models,
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )
        # 主界面模型权重版本小字提示（随 _WEIGHT_FILES 自动反映打包的子模块版本）
        self._homr_version = self._homr_pinned_version()
        self._model_version_label = ft.Text(
            '', size=11, color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip=t("landing.tooltip_model_version"),
        )
        self._refresh_model_buttons()

        options_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=(_sec_opts := ft.Text(t("landing.section_convert_options"), size=15, font_family=FONT_EMPHASIS,
                                        color=ft.Colors.ON_SURFACE)),
                        height=48,
                        padding=ft.Padding.only(left=16, right=16),
                        alignment=ft.Alignment(-1, 0),
                        border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
                    ),
                    ft.Container(
                        content=ft.Column(
                            [
                                self._engine_dd,
                                self._sr_engine_dd,
                                self._parallel_dd,
                                ft.Container(height=4),
                                self._convert_btn,
                                open_output_btn,
                                ft.Container(height=4),
                                ft.Divider(height=1, thickness=1, color=ft.Colors.OUTLINE_VARIANT),
                                (_sec_homr := section_title(t("landing.section_homr_models"))),
                                self._download_models_btn,
                                self._delete_models_btn,
                                ft.Container(
                                    content=self._model_version_label,
                                    padding=ft.Padding.only(left=4),
                                ),
                            ],
                            spacing=10,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                        padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                        expand=True,
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            bgcolor=ft.Colors.SURFACE,
            width=250,
            border=ft.Border.only(left=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        self._section_convert_options = _sec_opts
        self._section_homr_models = _sec_homr

        self.controls = [
            self._sidebar,
            ft.Container(content=self._viewer, expand=True),
            options_panel,
        ]
        self.expand = True
        self.vertical_alignment = ft.CrossAxisAlignment.STRETCH

    def retranslate(self) -> None:
        """Re-apply UI text in the active language (called on Event.LANGUAGE_CHANGED)."""
        self._engine_dd.label = t("landing.label_omr_engine")
        self._engine_dd.tooltip = t("landing.tooltip_omr_engine")
        self._sr_engine_dd.label = t("landing.label_sr_engine")
        self._sr_engine_dd.tooltip = t("landing.tooltip_sr_engine")
        self._sr_engine_dd.options = [
            ft.dropdown.Option('waifu2x',    t("landing.option_waifu2x")),
            ft.dropdown.Option('realesrgan', t("landing.option_realesrgan")),
        ]
        self._parallel_dd.label = t("landing.label_concurrency")
        self._parallel_dd.tooltip = t("landing.tooltip_concurrency")
        self._parallel_dd.options = [
            ft.dropdown.Option('1',    t("landing.option_concurrency_1")),
            ft.dropdown.Option('2',    t("landing.option_concurrency_2")),
            ft.dropdown.Option('4',    t("landing.option_concurrency_4")),
            ft.dropdown.Option('auto', t("landing.option_concurrency_auto")),
        ]
        self._open_output_label.value = t("landing.button_open_output_dir")
        self._download_models_label.value = t("landing.button_download_models")
        self._delete_models_label.value = t("landing.button_delete_models")
        self._section_convert_options.value = t("landing.section_convert_options")
        self._section_homr_models.value = t("landing.section_homr_models")
        # 模型权重版本小字提示随语言重译
        self._model_version_label.tooltip = t("landing.tooltip_model_version")
        _mv_key = "landing.model_version_ready" if self._state.homr_available else "landing.model_version_missing"
        self._model_version_label.value = t(_mv_key, ver=self._homr_version)
        # engine dropdown options（含「需下载」后缀）与转换按钮计数文案沿用已有刷新逻辑
        self._engine_dd.options = self._build_engine_options()
        self._refresh_convert_label()
        try:
            self.update()
        except Exception:
            pass
        # 子组件各自重译
        self._sidebar.retranslate()
        self._viewer.retranslate()

    # ── 引擎切换 / 模型下载触发 ───────────────────────────────────────────────

    def _prompt_homr_download(self, previous_value: str) -> None:
        page = self.page  # Flet Page (available after mount)
        if page is None:
            return

        def _on_yes(_):
            try:
                page.pop_dialog()
            except Exception:
                pass
            from ..components.model_download_dialog import ModelDownloadDialog
            dlg = ModelDownloadDialog(page, self._state)
            dlg.show()

        def _on_no(_):
            try:
                page.pop_dialog()
            except Exception:
                pass
            self._engine_dd.value = previous_value
            try:
                self._engine_dd.update()
            except Exception:
                pass

        confirm = ft.AlertDialog(
            modal=True,
            title=ft.Text(t("landing.download_dialog_title"),
                          size=16, font_family=FONT_EMPHASIS),
            content=ft.Text(
                t("landing.download_dialog_body"),
                size=13,
            ),
            actions=[
                ft.TextButton(t("landing.button_not_now"), on_click=_on_no),
                ft.ElevatedButton(t("landing.button_download_now"), on_click=_on_yes),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        try:
            page.show_dialog(confirm)
        except Exception:
            pass

    def _refresh_engine_labels(self, **_) -> None:
        self._engine_dd.options = self._build_engine_options()
        try:
            self._engine_dd.update()
        except Exception:
            pass
        self._refresh_model_buttons()

    def _homr_pinned_version(self) -> str:
        """Return the HOMR transcription model version (e.g. '367') for display.

        Parsed from the encoder weight filename
        (``encoder_pytorch_model_<ver>-<sha>.onnx``): first from an installed
        weight in ``models/``, else by text-scanning the bundled submodule's
        ``main.py``. Deliberately does NOT ``import homr.main`` — that pulls
        onnxruntime / cv2 / rapidocr (and triggers CUDA init) into the GUI
        process at startup, which belongs only in the worker subprocess.
        Returns '?' if it can't be determined.
        """
        import re as _re
        pat = _re.compile(r'encoder_pytorch_model_(\d+)-')
        # 1) 实际已安装的权重文件名（最能反映"当前装的是哪一版"）
        try:
            from core.app.backend import models_dir
            for p in models_dir().glob('encoder_pytorch_model_*.onnx'):
                m = pat.match(p.name)
                if m:
                    return m.group(1)
        except Exception:
            pass
        # 2) 子模块源码里钉住的版本（纯文本解析，不触发任何重导入）
        try:
            src = Path(__file__).parent.parent.parent / 'omr_engine' / 'homr' / 'homr' / 'main.py'
            m = pat.search(src.read_text(encoding='utf-8', errors='ignore'))
            if m:
                return m.group(1)
        except Exception:
            pass
        return '?'

    def _refresh_model_buttons(self) -> None:
        """Toggle the 下载/删除 buttons' enabled state based on weights presence."""
        if not hasattr(self, '_download_models_btn'):
            return
        present = self._state.homr_available
        # Download button: only enabled when models are absent
        self._download_models_btn.disabled = present
        # Delete button: only enabled when models exist
        self._delete_models_btn.disabled = not present
        # 同步版本小字提示的就绪状态
        if getattr(self, '_model_version_label', None) is not None:
            key = "landing.model_version_ready" if present else "landing.model_version_missing"
            self._model_version_label.value = t(key, ver=self._homr_version)
        try:
            self._download_models_btn.update()
            self._delete_models_btn.update()
            if getattr(self, '_model_version_label', None) is not None:
                self._model_version_label.update()
        except Exception:
            pass

    def _refresh_convert_label(self, **_) -> None:
        """在主按钮上显示已勾选文件数，让「将转换什么」一目了然。"""
        n = sum(1 for f in self._state.pinned_files if f in self._state.checked_files)
        label = t("landing.button_start_convert_with_count", n=n) if n else t("landing.button_start_convert")
        try:
            p = self.page
        except RuntimeError:
            p = None  # 控件尚未挂载
        if p is None:
            self._convert_label.value = label
            return

        # 事件可能由后台扫描线程触发；控件写操作调度到事件循环执行
        async def _do():
            self._convert_label.value = label
            try:
                self._convert_btn.update()
            except Exception:
                pass
        p.run_task(_do)  # type: ignore[attr-defined]

    def _on_download_models(self, _e) -> None:
        """Manually trigger the model download dialog from the Settings button."""
        if self._state.homr_available:
            return  # already have them; button should be disabled but guard anyway
        self._open_model_dialog()

    def _on_delete_models(self, _e) -> None:
        """Confirm + delete all HOMR weight files from the models/ directory."""
        if not self._state.homr_available:
            return
        page = self.page
        if page is None:
            return

        def _do_delete(_):
            try:
                page.pop_dialog()
            except Exception:
                pass
            import sys as _sys
            from core.app.backend import models_dir
            _homr_src = str(Path(__file__).parent.parent.parent / 'omr_engine' / 'homr')
            if _homr_src not in _sys.path:
                _sys.path.insert(0, _homr_src)
            from homr.main import _WEIGHT_FILES
            md = models_dir()
            removed = 0
            for fname in _WEIGHT_FILES:
                p = md / fname
                if p.exists():
                    try:
                        p.unlink()
                        removed += 1
                    except Exception:
                        pass
            self._state.homr_available = False
            self._refresh_engine_labels()
            self._show_snack(t("landing.models_deleted", n=removed), Palette.INFO)

        def _cancel_delete(_):
            try:
                page.pop_dialog()
            except Exception:
                pass

        confirm = ft.AlertDialog(
            modal=True,
            title=ft.Text(t("landing.delete_models_dialog_title"), size=16, font_family=FONT_EMPHASIS),
            content=ft.Text(
                t("landing.delete_models_dialog_body"),
                size=13,
            ),
            actions=[
                ft.TextButton(t("common.cancel"), on_click=_cancel_delete),
                ft.ElevatedButton(t("common.delete"), on_click=_do_delete),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        try:
            page.show_dialog(confirm)
        except Exception:
            pass

    def _open_model_dialog(self) -> None:
        page = self.page
        if page is None:
            return
        from ..components.model_download_dialog import ModelDownloadDialog
        dlg = ModelDownloadDialog(page, self._state)
        dlg.show()

    def did_mount(self):
        # 如果安装器留下 .pending_download 标志，并且模型尚未就绪，
        # 在下一个事件循环 tick 自动打开模型下载对话框（此时 self.page 已可用）。
        try:
            from core.app.backend import models_dir
            flag = models_dir() / '.pending_download'
            if flag.exists() and not self._state.homr_available:
                try:
                    self.page.loop.call_soon_threadsafe(self._open_model_dialog)
                except Exception:
                    pass
        except Exception:
            pass

        # 延迟扫描：让 did_mount 先返回、Flet 内部锁释放后再推送 UI 更新
        self._scan_token += 1
        current_token = self._scan_token
        import threading
        threading.Thread(target=self._scan_input_on_startup, args=(current_token,), daemon=True).start()

    def _scan_input_on_startup(self, token: int) -> None:
        """在后台线程中扫描 Input/ 并刷新侧边栏（page.update 在此线程中是线程安全的）。"""
        if token != self._scan_token:
            return
        try:
            if self.page is None:
                return
            input_dir = app_base_dir() / 'Input'
            if not input_dir.is_dir():
                return
            # 批量收集，避免每次 add_file 触发中间 update 事件
            newly_added: list[Path] = []
            for ext in ('*.pdf', '*.png', '*.jpg', '*.jpeg'):
                for f in sorted(input_dir.glob(ext)):
                    resolved = f.resolve()
                    if resolved not in self._state.pinned_files:
                        self._state.pinned_files.append(resolved)
                        self._state.checked_files.add(resolved)
                        newly_added.append(resolved)
            if not newly_added:
                return
            # 发出统一文件变化事件，刷新列表
            self._state.emit(Event.FILES_CHANGED, files=list(self._state.pinned_files))
            # 选中第一个文件并触发预览
            if not self._state.current_file and self._state.pinned_files:
                self._state.select_file(self._state.pinned_files[0])
            elif self._state.current_file and token == self._scan_token:
                self._viewer.load(self._state.current_file)
            # 预加载所有文件，减少后续切换延迟
            threading.Thread(target=self._preload_all_files, args=(list(self._state.pinned_files), token), daemon=True).start()
            # 一次性推送所有 UI 变更（通过 asyncio 事件循环，避免跨线程 page.update）
            async def _do_page_update():
                try:
                    self.page.update()
                except Exception:
                    pass
            self.page.run_task(_do_page_update)  # type: ignore[attr-defined]
        except Exception:
            pass

    def _preload_all_files(self, files: list[Path], token: int) -> None:
        if token != self._scan_token:
            return
        for path in files:
            if token != self._scan_token:
                return
            if path in self._preloaded_paths:
                continue
            try:
                self._viewer.preload(path)
                self._preloaded_paths.add(path)
            except Exception:
                pass

    # ── 事件 ─────────────────────────────────────────────────────────────────

    def _on_file_selected(self, path: Path, **_kw) -> None:
        self._viewer.load(path)

    def _on_files_changed(self, files: list[Path], **_kw) -> None:
        for path in files:
            try:
                self._viewer.preload(path)
            except Exception:
                pass

    def _on_files_imported(self, paths: list[Path], **_kw) -> None:
        for path in paths:
            try:
                self._viewer.preload(path)
            except Exception:
                pass

    def _show_snack(self, msg: str, color: str = Palette.INFO) -> None:
        if self.page is None:
            return
        try:
            self.page.show_dialog(ft.SnackBar(
                content=ft.Text(msg, color='#FFFFFF'),
                bgcolor=color,
                duration=3500,
            ))
        except Exception:
            pass

    def _on_open_output_dir(self, _e) -> None:
        try:
            output_dir_path = output_dir(None)
            open_directory(output_dir_path)
        except Exception as exc:
            self._show_snack(t("common.open_dir_failed", exc=exc), Palette.ERROR)

    def _on_convert(self, _e) -> None:
        checked = [f for f in self._state.pinned_files if f in self._state.checked_files]
        if not checked:
            self._show_snack(t("landing.error_select_at_least_one"), Palette.WARNING)
            return
        if self._state.is_processing:
            return

        # HOMR 模型权重缺失守卫：默认引擎是 auto（会用 HOMR），用户可能从未点过下拉框。
        # 转换前发现需要 HOMR 但权重不在，弹下载提示，由用户决定是先下载还是改走
        # 纯 Audiveris 路线。暂不下载 → 强制回退到 audiveris，避免重复弹窗。
        engine_val = self._engine_dd.value or 'auto'
        if engine_val in ('homr', 'auto') and not self._state.homr_available:
            self._prompt_homr_download(previous_value='audiveris')
            return

        # 计算输出目录，检测已存在文件
        output_path = output_dir(None)
        existing = [
            src.name for src in checked
            if (output_path / (src.stem + '_jianpu.pdf')).exists()
        ]

        # ── 对话框内容 ───────────────────────────────────────────────────
        self._skip_dup_cb: Optional[ft.Checkbox] = None
        warn_items: list[ft.Control] = []
        if existing:
            warn_items.append(
                ft.Row([
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED,
                            color=Palette.WARNING, size=16),
                    ft.Text(
                        t("landing.existing_outputs_warning", n=len(existing)),
                        color=Palette.WARNING, size=13,
                    ),
                ], spacing=4)
            )
            for name in existing[:5]:
                warn_items.append(
                    ft.Text(f'  • {name}', size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT)
                )
            if len(existing) > 5:
                warn_items.append(
                    ft.Text(t("landing.existing_outputs_more", n=len(existing) - 5),
                            size=12, color=ft.Colors.OUTLINE)
                )
            self._skip_dup_cb = ft.Checkbox(
                label=t("landing.checkbox_skip_duplicates"),
                value=True,
                active_color=Palette.PRIMARY,
            )
            warn_items.append(ft.Container(height=4))
            warn_items.append(self._skip_dup_cb)

        def _do_confirm(_ev) -> None:
            self.page.pop_dialog()
            skip = bool(self._skip_dup_cb.value) if self._skip_dup_cb is not None else False
            self._start_conversion(
                files=checked,
                gen_midi=True,
                skip_duplicates=skip,
                duplicate_files=set(existing),
            )

        self._confirm_dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(
                t("landing.convert_dialog_title", n=len(checked)),
                size=16, font_family=FONT_EMPHASIS,
            ),
            content=ft.Container(
                content=ft.Column(
                    warn_items,
                    tight=True,
                    spacing=8,
                ),
                padding=ft.Padding.only(top=6),
                width=400,
            ),
            actions=[
                ft.TextButton(
                    t("common.cancel"),
                    on_click=lambda _ev: self.page.pop_dialog(),
                ),
                ft.FilledButton(
                    t("landing.button_start_convert"),
                    on_click=_do_confirm,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(self._confirm_dlg)

    def _start_conversion(
        self,
        files: list[Path] | None = None,
        gen_midi: bool = True,
        skip_duplicates: bool = False,
        duplicate_files: set | None = None,
    ) -> None:
        self._conversion_files: list[Path] = files if files is not None else list(self._state.checked_files)
        self._gen_midi = gen_midi
        self._skip_dup = skip_duplicates
        self._dup_files: set = duplicate_files or set()
        self._state.is_processing = True
        self._overlay.show(t("landing.running_omr"))
        threading.Thread(target=self._run_conversion, daemon=True).start()

    # ── Worker 辅助 ────────────────────────────────────────────────────────────

    def _resolve_parallel(self, n_files: int) -> int:
        val = (self._parallel_dd.value or 'auto').strip()
        if val == 'auto':
            n = max(1, min(4, (os.cpu_count() or 2) // 2))
        else:
            try:
                n = max(1, int(val))
            except ValueError:
                n = 1
        return min(n, max(1, n_files))

    # ── 转换主入口（dispatcher）────────────────────────────────────────────────

    def _run_conversion(self) -> None:
        files = list(getattr(self, '_conversion_files', self._state.pinned_files))
        opts = ConversionOptions(
            engine=self._engine_dd.value or 'auto',
            sr_engine=self._sr_engine_dd.value or 'waifu2x',
            gen_midi=getattr(self, '_gen_midi', True),
            skip_dup=getattr(self, '_skip_dup', False),
            dup_files=list(getattr(self, '_dup_files', set())),
        )
        self._runner.run(files, self._resolve_parallel(len(files)), opts)

    def _schedule_show_results(self) -> None:
        """将结果对话框调度到 asyncio 事件循环，避免从 worker 线程直接调用 page.show_dialog()。"""
        p = self.page
        if p is not None:
            async def _do():
                self._show_conversion_results()
            p.run_task(_do)  # type: ignore[attr-defined]
        else:
            self._show_conversion_results()

    def _show_conversion_results(self) -> None:
        """显示转换结果详情（总览 + 每文件成功/失败信息，滚动列表）。"""
        try:
            if self.page is None:
                return

            summary = self._state.conversion_summary
            if not summary:
                return

            success_count  = summary.get('success_count', 0)
            fallback_count = summary.get('fallback_count', 0)
            failed_count   = summary.get('failed_count', 0)
            total          = summary.get('total', success_count + fallback_count + failed_count)
            success_files  = summary.get('success_files', [])
            fallback_files = summary.get('fallback_files', [])
            failed_files   = summary.get('failed_files', [])

            # ── 标题行：总共 X 文件，成功 Y，失败 Z ──
            def _stat_chip(label: str, color: str) -> ft.Container:
                return ft.Container(
                    content=ft.Text(label, size=13, color=color, font_family=FONT_EMPHASIS),
                    padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                    border_radius=12,
                    bgcolor=ft.Colors.with_opacity(0.12, color),
                )

            header_row = ft.Row(
                [
                    ft.Text(t("landing.stat_total", n=total), size=14, color=ft.Colors.ON_SURFACE),
                    _stat_chip(t("landing.stat_success", n=success_count), Palette.SUCCESS),
                    _stat_chip(t("landing.stat_fallback", n=fallback_count), Palette.WARNING) if fallback_count else ft.Container(),
                    _stat_chip(t("landing.stat_failed", n=failed_count), Palette.ERROR) if failed_count else ft.Container(),
                ],
                spacing=8,
                wrap=True,
            )

            list_items: list[ft.Control] = []

            # ── 成功条目 ──
            if success_files:
                list_items.append(
                    ft.Container(
                        ft.Text(t("landing.section_success"), size=12, font_family=FONT_EMPHASIS, color=Palette.SUCCESS),
                        padding=ft.Padding.only(top=8, bottom=2),
                    )
                )
                for item in success_files:
                    if isinstance(item, dict):
                        file_name   = item.get('file', t("landing.unknown_file"))
                        engine_used = item.get('engine_used', '')
                        image_type  = item.get('image_type', '')
                    else:
                        file_name, engine_used, image_type = item, '', ''

                    detail_parts: list[str] = []
                    if image_type:
                        detail_parts.append(t("landing.detail_image_type", kind=image_type))
                    if engine_used:
                        detail_parts.append(t("landing.detail_engine_used", engine=engine_used))
                    detail_str = '，'.join(detail_parts) if detail_parts else t("landing.detail_success_fallback")

                    list_items.append(
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Text(
                                        t("landing.list_item_success", name=file_name),
                                        size=12,
                                        color=ft.Colors.ON_SURFACE,
                                        no_wrap=False,
                                    ),
                                    ft.Text(
                                        t("landing.list_item_detail", detail=detail_str),
                                        size=11,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                spacing=1,
                                tight=True,
                            ),
                            padding=ft.Padding.symmetric(vertical=3),
                        )
                    )

            # ── 回退成功条目（例如 Audiveris 失败后自动改用 Homr 引擎，最终仍成功）──
            # 单独列出而不是并入"成功"，方便用户注意到这些文件走了非常规路径，
            # 值得多看一眼识别质量；但也不计入"失败"，因为最终确实生成了简谱。
            if fallback_files:
                list_items.append(
                    ft.Container(
                        ft.Text(t("landing.section_fallback"), size=12, font_family=FONT_EMPHASIS, color=Palette.WARNING),
                        padding=ft.Padding.only(top=10, bottom=2),
                    )
                )
                for item in fallback_files:
                    if isinstance(item, dict):
                        file_name   = item.get('file', t("landing.unknown_file"))
                        engine_used = item.get('engine_used', '')
                        image_type  = item.get('image_type', '')
                    else:
                        file_name, engine_used, image_type = item, '', ''

                    detail_parts: list[str] = []
                    if image_type:
                        detail_parts.append(t("landing.detail_image_type", kind=image_type))
                    if engine_used:
                        detail_parts.append(t("landing.detail_engine_used", engine=engine_used))
                    detail_str = '，'.join(detail_parts) if detail_parts else ''
                    detail_str = (
                        t("landing.detail_fallback_used", detail=detail_str)
                        if detail_str else t("landing.detail_fallback_used_bare")
                    )

                    list_items.append(
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Text(
                                        t("landing.list_item_fallback", name=file_name),
                                        size=12,
                                        color=ft.Colors.ON_SURFACE,
                                        no_wrap=False,
                                    ),
                                    ft.Text(
                                        t("landing.list_item_detail", detail=detail_str),
                                        size=11,
                                        color=Palette.WARNING,
                                    ),
                                ],
                                spacing=1,
                                tight=True,
                            ),
                            padding=ft.Padding.symmetric(vertical=3),
                        )
                    )

            # ── 失败条目 ──
            if failed_files:
                list_items.append(
                    ft.Container(
                        ft.Text(t("landing.section_failed"), size=12, font_family=FONT_EMPHASIS, color=Palette.ERROR),
                        padding=ft.Padding.only(top=10, bottom=2),
                    )
                )
                for item in failed_files:
                    file_name = (item.get('file', t("landing.unknown_file")) if isinstance(item, dict) else item)
                    reason    = (item.get('reason', '') if isinstance(item, dict) else '') or t("landing.unknown_reason")
                    list_items.append(
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Text(
                                        t("landing.list_item_failed", name=file_name),
                                        size=12,
                                        color=ft.Colors.ON_SURFACE,
                                        no_wrap=False,
                                    ),
                                    ft.Text(
                                        t("landing.list_item_reason", reason=reason),
                                        size=11,
                                        color=Palette.ERROR,
                                        no_wrap=False,
                                    ),
                                ],
                                spacing=1,
                                tight=True,
                            ),
                            padding=ft.Padding.symmetric(vertical=3),
                        )
                    )

            def _close_dialog(_ev=None):
                if self.page:
                    self.page.pop_dialog()

            def _goto_jianpu_preview(_ev=None):
                # 引导用户进入下一步：转换成功后直接跳到简谱预览页
                _close_dialog()
                self._state.emit(Event.NAVIGATE, name='editor')

            actions: list[ft.Control] = [
                ft.TextButton(t("landing.button_open_output_dir"), on_click=self._on_open_output_dir),
                ft.TextButton(t("common.close"), on_click=_close_dialog),
            ]
            if success_count > 0 or fallback_count > 0:
                actions.append(ft.FilledButton(t("landing.button_view_jianpu"), on_click=_goto_jianpu_preview))

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text(t("landing.result_dialog_title"), size=16, font_family=FONT_EMPHASIS),
                content=ft.Column(
                    [
                        header_row,
                        ft.Divider(height=1, thickness=1),
                        ft.Container(
                            content=ft.Column(
                                list_items,
                                tight=True,
                                spacing=0,
                                scroll=ft.ScrollMode.AUTO,
                            ),
                            height=320,
                            width=460,
                        ),
                    ],
                    tight=True,
                    spacing=8,
                ),
                actions=actions,
                actions_alignment=ft.MainAxisAlignment.END,
            )
            self.page.show_dialog(dialog)
        except Exception:
            pass

    def terminate_worker(self) -> None:
        """关闭 GUI 时强制终止所有 Worker 子进程及其子进程（如 java.exe）。"""
        self._runner.terminate()
