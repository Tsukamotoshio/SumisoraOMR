# gui/pages/landing_page.py — File management + PDF preview page (Landing Page).
# Left panel: pinned file sidebar; right panel: preview area + conversion button.

from __future__ import annotations

import json
import os
import queue as _queue_mod
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

import flet as ft

from ..app_state import AppState, Event
from core.app.backend import app_base_dir, output_dir, open_directory
from ..components.file_sidebar import FileSidebar
from ..components.pdf_viewer import PdfViewer
from ..components.progress_overlay import ProgressOverlay
from ..theme import Palette, with_alpha, section_title, FONT_EMPHASIS
from ..strings import t


_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


class LandingPage(ft.Row):
    """Landing page: file management + PDF preview + "Start Conversion" button."""

    def __init__(self, state: AppState, overlay: ProgressOverlay):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._overlay = overlay
        self._scan_token: int = 0
        self._preloaded_paths: set[Path] = set()
        self._worker_procs: list[subprocess.Popen] = []
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

        # 并发处理数（高端机加速；默认 1 = 顺序，低配安全）
        self._parallel_dd = ft.Dropdown(
            label=t("landing.label_concurrency"),
            value='1',
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
        """Return the pinned HOMR transcription model version (e.g. '367').

        Parsed from the bundled submodule's ``_WEIGHT_FILES``, so it tracks the
        release automatically (the encoder filename is
        ``encoder_pytorch_model_<ver>-<sha>.onnx``). Returns '?' if the homr
        package can't be imported.
        """
        try:
            _homr_src = str(Path(__file__).parent.parent.parent / 'omr_engine' / 'homr')
            if _homr_src not in sys.path:
                sys.path.insert(0, _homr_src)
            from homr.main import _WEIGHT_FILES  # type: ignore[import-not-found]
            prefix = 'encoder_pytorch_model_'
            for fname in _WEIGHT_FILES:
                if fname.startswith(prefix):
                    return fname[len(prefix):].split('-')[0]
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

    def _build_worker_cmd(self) -> list[str]:
        if getattr(sys, 'frozen', False):
            return [sys.executable, '--worker']
        return [sys.executable, str(Path(__file__).parent.parent.parent / 'app.py'), '--worker']

    @staticmethod
    def _split_file_chunks(files: list, n: int) -> list[list]:
        if n <= 1:
            return [list(files)]
        chunk_size = max(1, (len(files) + n - 1) // n)
        return [files[i:i + chunk_size] for i in range(0, len(files), chunk_size)]

    def _resolve_parallel(self, n_files: int) -> int:
        val = (self._parallel_dd.value or '1').strip()
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
        n = self._resolve_parallel(len(files))
        if n <= 1:
            self._run_single_worker(files)
        else:
            self._run_parallel_workers(files, n)

    # ── 单 Worker 路径（含 GPU 崩溃重试，低配默认路径）────────────────────────

    def _run_single_worker(self, files: list[Path]) -> None:
        _done_or_error_received = False
        _conversion_results = {'success': [], 'fallback': [], 'failed': []}

        try:
            base_dir = app_base_dir()
            output_path = output_dir(None)

            engine_val = self._engine_dd.value or 'auto'
            gen_midi = getattr(self, '_gen_midi', True)
            _gpu_crash_count = 0
            _total_files_orig = len(files)
            _files_done_total = 0

            while True:
                task = {
                    'files': [str(f) for f in files],
                    'engine': engine_val,
                    'sr_engine': self._sr_engine_dd.value or 'waifu2x',
                    'output_dir': str(output_path),
                    'gen_midi': gen_midi,
                    'skip_dup': getattr(self, '_skip_dup', False),
                    'dup_files': list(getattr(self, '_dup_files', set())),
                    'base_dir': str(base_dir),
                    'use_gpu': engine_val in ('homr', 'auto') and _gpu_crash_count < 2,
                    'files_offset': _files_done_total,
                    'total_files_orig': _total_files_orig,
                }

                extra_kwargs: dict = {}
                if sys.platform == 'win32':
                    extra_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

                # Inject HOMR_MODELS_DIR so the worker subprocess loads weights
                # from <app_base_dir>/models/ (where on-demand downloads land)
                # rather than the legacy submodule paths.
                from core.app.backend import models_dir as _models_dir
                _env = os.environ.copy()
                _env['HOMR_MODELS_DIR'] = str(_models_dir())
                if 'env' not in extra_kwargs:
                    extra_kwargs['env'] = _env

                proc = subprocess.Popen(
                    self._build_worker_cmd(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **extra_kwargs,
                )
                self._worker_procs = [proc]

                err_lines: list[str] = []
                _current_processing_file: Optional[str] = None

                def _read_stderr() -> None:
                    try:
                        for raw_line in proc.stderr:  # type: ignore[union-attr]
                            if not self._state.is_processing:
                                break
                            line_str = raw_line.decode('utf-8', errors='replace')  # type: ignore[attr-defined]
                            line_str = _ANSI_ESCAPE_RE.sub('', line_str).strip()
                            if line_str:
                                err_lines.append(line_str)
                    except Exception:
                        pass

                threading.Thread(target=_read_stderr, daemon=True).start()

                proc.stdin.write((json.dumps(task, ensure_ascii=False) + '\n').encode('utf-8'))  # type: ignore[union-attr, arg-type]
                proc.stdin.flush()  # type: ignore[union-attr]
                proc.stdin.close()  # type: ignore[union-attr]

                _files_done_this_run = 0
                for raw_line in proc.stdout:  # type: ignore[union-attr]
                    if not self._state.is_processing:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        break
                    line_str = raw_line.decode('utf-8', errors='replace').strip()  # type: ignore[attr-defined]
                    if not line_str:
                        continue
                    try:
                        msg = json.loads(line_str)
                    except json.JSONDecodeError:
                        continue

                    mtype = msg.get('type', '')
                    if mtype == 'progress':
                        self._state.set_progress(msg.get('value', 0.0), msg.get('message', ''))
                        msg_text = msg.get('message', '')
                        if msg_text and ']' in msg_text:
                            _current_processing_file = msg_text.split('] ', 1)[-1]
                    elif mtype == 'sub_progress':
                        self._overlay.set_sub_progress(msg.get('value', 0.0), msg.get('message', ''))
                    elif mtype == 'log':
                        text = msg.get('text', '').strip()
                        self._state.append_log(text)
                        if '✗' in text and _current_processing_file:
                            reason = text.replace('✗', '').strip()
                            if reason.startswith('['):
                                if '：' in reason or ':' in reason:
                                    reason = reason.split('：' if '：' in reason else ':', 1)[-1].strip()
                            _conversion_results['failed'].append({
                                'file': _current_processing_file,
                                'reason': reason or t("landing.unknown_reason")
                            })
                    elif mtype == 'result':
                        _files_done_this_run += 1
                        if msg.get('success'):
                            self._state.output_pdf = Path(msg['output_pdf'])
                            if msg.get('archived_mxl'):
                                archived = Path(msg['archived_mxl'])
                                self._state.current_mxl = archived
                                self._state.emit(Event.MXL_READY, path=archived)
                            if _current_processing_file:
                                entry = {
                                    'file': _current_processing_file,
                                    'engine_used': msg.get('engine_used', ''),
                                    'image_type': msg.get('image_type', ''),
                                }
                                # 识别过程中发生过引擎回退（例如 Audiveris 失败后改用
                                # Homr）：单独归入 fallback 列表，不算失败，也不与
                                # 干净成功的文件混在一起，方便用户知晓需要留意质量。
                                if msg.get('fallback_used'):
                                    _conversion_results['fallback'].append(entry)
                                else:
                                    _conversion_results['success'].append(entry)
                        else:
                            if _current_processing_file:
                                if not any(f['file'] == _current_processing_file for f in _conversion_results['failed']):
                                    _conversion_results['failed'].append({
                                        'file': _current_processing_file,
                                        'reason': msg.get('reason') or t("landing.unknown_reason"),
                                    })
                    elif mtype == 'done':
                        _done_or_error_received = True
                        self._state.conversion_summary = {
                            'success_count': len(_conversion_results['success']),
                            'fallback_count': len(_conversion_results['fallback']),
                            'failed_count': len(_conversion_results['failed']),
                            'success_files': _conversion_results['success'],
                            'fallback_files': _conversion_results['fallback'],
                            'failed_files': _conversion_results['failed'],
                            'message': msg.get('message', t("landing.done_message")),
                            'total': len(_conversion_results['success']) + len(_conversion_results['fallback']) + len(_conversion_results['failed']),
                        }
                        self._state.set_done(msg.get('message', t("landing.done_message")))
                    elif mtype == 'error':
                        _done_or_error_received = True
                        self._state.set_error(msg.get('message', t("landing.unknown_error")))

                proc.wait()

                if not _done_or_error_received:
                    err_text = '\n'.join(err_lines).strip()
                    if proc.returncode != 0:
                        gpu_access_violation_codes = {-1073741819, 3221225477}
                        if engine_val in ('homr', 'auto') and task.get('use_gpu') and proc.returncode in gpu_access_violation_codes:
                            _files_done_total += _files_done_this_run
                            files = files[_files_done_this_run:]
                            _gpu_crash_count += 1
                            if _gpu_crash_count < 2:
                                self._state.append_log(t("landing.log_gpu_crash_retry"))
                            else:
                                self._state.append_log(t("landing.log_gpu_crash_fallback_cpu"))
                            _done_or_error_received = False
                            continue
                        self._state.set_error(
                            t(
                                "landing.worker_crash_error",
                                code=proc.returncode,
                                detail=err_text[:300] if err_text else t("landing.worker_crash_no_detail"),
                            )
                        )
                    else:
                        self._state.set_done(t("landing.done_message"))
                    break
                else:
                    break

        except Exception as exc:
            if not _done_or_error_received:
                self._state.set_error(str(exc))
        finally:
            procs = list(self._worker_procs)
            self._worker_procs = []
            for _p in procs:
                if _p.poll() is None:
                    try:
                        _p.kill()
                        _p.wait(timeout=3)
                    except Exception:
                        pass
            if self._state.is_processing:
                self._state.is_processing = False
            self._schedule_show_results()

    # ── 并行 Worker 路径（高端机加速）─────────────────────────────────────────

    def _run_parallel_workers(self, files: list[Path], n: int) -> None:
        """将文件列表分片，同时启动 n 个 Worker 子进程，聚合进度和结果。"""
        _all_results: dict = {'success': [], 'fallback': [], 'failed': []}
        _any_error = False

        try:
            base_dir = app_base_dir()
            output_path = output_dir(None)
            engine_val = self._engine_dd.value or 'auto'
            gen_midi = getattr(self, '_gen_midi', True)
            total = len(files)

            chunks = self._split_file_chunks(files, n)
            n_actual = len(chunks)

            q: _queue_mod.Queue = _queue_mod.Queue()

            # 每个 worker 覆盖总进度条中不重叠的区间；记录各 worker 起始值以计算增量
            _w_progress: list[float] = [0.0] * n_actual
            _w_start: list[Optional[float]] = [None] * n_actual
            _current_files: list[str] = [''] * n_actual
            _worker_done: list[bool] = [False] * n_actual  # 是否已收到该 worker 的 done 消息

            extra_kwargs: dict = {}
            if sys.platform == 'win32':
                extra_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

            # Inject HOMR_MODELS_DIR so the worker subprocess loads weights
            # from <app_base_dir>/models/ (where on-demand downloads land)
            # rather than the legacy submodule paths.
            from core.app.backend import models_dir as _models_dir
            _env = os.environ.copy()
            _env['HOMR_MODELS_DIR'] = str(_models_dir())
            # 并行 worker 会各自把 ONNX intra-op 线程开到 (核数-2)，n 个 worker 一起
            # 就是 n×(核数-2) 抢占核数个核 → 超额订阅、上下文切换抖动，实测比顺序还慢。
            # 按并发数把每个 worker 的线程上限压到约 核数/n，让总线程数不超过核数。
            _cpu = os.cpu_count() or 4
            _env['HOMR_ORT_INTRA_THREADS'] = str(max(1, _cpu // max(1, n_actual) - 1))
            if 'env' not in extra_kwargs:
                extra_kwargs['env'] = _env

            worker_cmd = self._build_worker_cmd()
            procs: list[subprocess.Popen] = []

            # 捕获每个 worker 的最后 stderr 行，用于崩溃时诊断
            _stderr_tails: list[list[str]] = [[] for _ in range(n_actual)]

            def _read_worker(worker_id: int, proc: subprocess.Popen) -> None:
                try:
                    for raw_line in proc.stdout:  # type: ignore[union-attr]
                        line_str = raw_line.decode('utf-8', errors='replace').strip()  # type: ignore[attr-defined]
                        if not line_str:
                            continue
                        try:
                            q.put((worker_id, json.loads(line_str)))
                        except json.JSONDecodeError:
                            pass
                except Exception:
                    pass
                finally:
                    q.put((worker_id, None))  # sentinel：此 worker 读取完毕

            def _capture_stderr(worker_id: int, proc: subprocess.Popen) -> None:
                try:
                    for raw_line in proc.stderr:  # type: ignore[union-attr]
                        line_str = raw_line.decode('utf-8', errors='replace').rstrip()  # type: ignore[attr-defined]
                        if line_str:
                            tail = _stderr_tails[worker_id]
                            tail.append(line_str)
                            if len(tail) > 20:
                                tail.pop(0)
                except Exception:
                    pass

            offset = 0
            for i, chunk in enumerate(chunks):
                task = {
                    'files': [str(f) for f in chunk],
                    'engine': engine_val,
                    'sr_engine': self._sr_engine_dd.value or 'waifu2x',
                    'output_dir': str(output_path),
                    'gen_midi': gen_midi,
                    'skip_dup': getattr(self, '_skip_dup', False),
                    'dup_files': list(getattr(self, '_dup_files', set())),
                    'base_dir': str(base_dir),
                    # 多 Worker 并行时禁用 GPU 推理，防止各 Worker 同时抢占显存导致 OOM 崩溃。
                    # 单 Worker 路径（_run_single_worker）仍保留 GPU + 崩溃重试逻辑。
                    'use_gpu': False,
                    'files_offset': offset,
                    'total_files_orig': total,
                }
                offset += len(chunk)

                proc = subprocess.Popen(
                    worker_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **extra_kwargs,
                )
                procs.append(proc)
                proc.stdin.write((json.dumps(task, ensure_ascii=False) + '\n').encode('utf-8'))  # type: ignore[union-attr, arg-type]
                proc.stdin.flush()  # type: ignore[union-attr]
                proc.stdin.close()  # type: ignore[union-attr]

                # 每个 Worker 启动后立即开启 reader/stderr 线程，
                # 避免多个 Worker 同时积压输出导致管道缓冲区溢出而阻塞。
                threading.Thread(target=_read_worker, args=(i, proc), daemon=True).start()
                threading.Thread(target=_capture_stderr, args=(i, proc), daemon=True).start()

                names_preview = ', '.join(Path(f).name for f in chunk[:3])
                if len(chunk) > 3:
                    names_preview += t("landing.names_preview_more", n=len(chunk))
                self._state.append_log(t("landing.log_worker_assign", worker=i + 1, names=names_preview))

            self._worker_procs = procs

            done_workers = 0
            _total_success = 0
            _total_fail = 0
            _total_skipped = 0

            while done_workers < n_actual:
                if not self._state.is_processing:
                    for proc in procs:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    break

                try:
                    worker_id, msg = q.get(timeout=0.1)
                except _queue_mod.Empty:
                    continue

                if msg is None:
                    done_workers += 1
                    continue

                prefix = f'[W{worker_id + 1}] ' if n_actual > 1 else ''
                mtype = msg.get('type', '')

                if mtype == 'progress':
                    v = msg.get('value', 0.0)
                    if _w_start[worker_id] is None:
                        _w_start[worker_id] = v
                    _w_progress[worker_id] = v
                    # 整体进度 = 各 worker 相对其起始值的增量之和
                    overall = sum(
                        _w_progress[i] - float(_w_start[i])  # type: ignore[arg-type]
                        for i in range(n_actual)
                        if _w_start[i] is not None
                    )
                    msg_text = msg.get('message', '')
                    if msg_text and ']' in msg_text:
                        _current_files[worker_id] = msg_text.split('] ', 1)[-1]
                    self._state.set_progress(overall, prefix + msg_text)

                elif mtype == 'sub_progress':
                    self._overlay.set_sub_progress(msg.get('value', 0.0), msg.get('message', ''))

                elif mtype == 'log':
                    text = msg.get('text', '').strip()
                    self._state.append_log(prefix + text)
                    if '✗' in text and _current_files[worker_id]:
                        reason = text.replace('✗', '').strip()
                        if reason.startswith('[') and ('：' in reason or ':' in reason):
                            reason = reason.split('：' if '：' in reason else ':', 1)[-1].strip()
                        _all_results['failed'].append({
                            'file': _current_files[worker_id],
                            'reason': reason or t("landing.unknown_reason"),
                        })

                elif mtype == 'result':
                    if msg.get('success'):
                        self._state.output_pdf = Path(msg['output_pdf'])
                        if msg.get('archived_mxl'):
                            archived = Path(msg['archived_mxl'])
                            self._state.current_mxl = archived
                            self._state.emit(Event.MXL_READY, path=archived)
                        if _current_files[worker_id]:
                            entry = {
                                'file': _current_files[worker_id],
                                'engine_used': msg.get('engine_used', ''),
                                'image_type': msg.get('image_type', ''),
                            }
                            if msg.get('fallback_used'):
                                _all_results['fallback'].append(entry)
                            else:
                                _all_results['success'].append(entry)
                    else:
                        cf = _current_files[worker_id]
                        if cf and not any(f['file'] == cf for f in _all_results['failed']):
                            _all_results['failed'].append({
                                'file': cf,
                                'reason': msg.get('reason') or t("landing.unknown_reason"),
                            })

                elif mtype == 'done':
                    _worker_done[worker_id] = True
                    _total_success += msg.get('success_count', 0)
                    _total_fail += msg.get('fail_count', 0)
                    _total_skipped += msg.get('skip_count', 0)

                elif mtype == 'error':
                    _any_error = True
                    err_text = msg.get('message', '')
                    self._state.append_log(t("landing.log_error_prefix", prefix=prefix, err=err_text))
                    # 该 worker 整批文件均失败（task 级别异常，未进入文件循环）
                    for _f in chunks[worker_id]:
                        _fname = Path(_f).name
                        if not any(r.get('file') == _fname for r in _all_results['failed']):
                            _all_results['failed'].append({'file': _fname, 'reason': err_text[:80] or t("landing.process_error")})
                    _total_fail += len(chunks[worker_id])

            # 等待所有子进程完全退出，同时检测无声崩溃（无 done/error 消息）
            for i, proc in enumerate(procs):
                rc: Optional[int] = None
                try:
                    rc = proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # 超时后强制终止，再等 5 秒取 returncode
                    try:
                        proc.kill()
                        rc = proc.wait(timeout=5)
                    except Exception:
                        pass
                except Exception:
                    pass

                if not _worker_done[i] and rc != 0:
                    # rc=None（仍在运行/无法取到）或非零均视为崩溃
                    _tail = ' | '.join(_stderr_tails[i][-3:]) if _stderr_tails[i] else ''
                    _crash_hint = t("landing.crash_hint_code", rc=rc) + (t("landing.crash_hint_tail", tail=_tail[:120]) if _tail else '')
                    self._state.append_log(t("landing.log_worker_crash", worker=i + 1, hint=_crash_hint))
                    _any_error = True
                    for _f in chunks[i]:
                        _fname = Path(_f).name
                        already = (
                            any(r.get('file') == _fname for r in _all_results['failed'])
                            or _fname in _all_results['success']
                        )
                        if not already:
                            _all_results['failed'].append({
                                'file': _fname,
                                'reason': _crash_hint,
                            })
                            _total_fail += 1

            if self._state.is_processing:
                n_success = len(_all_results['success'])
                n_fallback = len(_all_results['fallback'])
                n_failed = len(_all_results['failed'])
                self._state.conversion_summary = {
                    'success_count': n_success,
                    'fallback_count': n_fallback,
                    'failed_count': n_failed,
                    'success_files': _all_results['success'],
                    'fallback_files': _all_results['fallback'],
                    'failed_files': _all_results['failed'],
                    'total': n_success + n_fallback + n_failed,
                    'message': '',
                }
                if n_success == 0 and n_fallback == 0 and _any_error:
                    # 全部失败——保持 overlay 打开，让用户看到错误信息
                    self._state.conversion_summary['message'] = t("landing.all_workers_failed")
                    self._state.set_error(t("landing.all_workers_failed"))
                else:
                    _parts: list[str] = []
                    if _total_success > 0:
                        _parts.append(t("landing.summary_success_part", n=_total_success))
                    if _total_fail > 0:
                        _parts.append(t("landing.summary_fail_part", n=_total_fail))
                    if _total_skipped > 0:
                        _parts.append(t("landing.summary_skipped_part", n=_total_skipped))
                    msg_text = t("landing.summary_done_prefix") + '，'.join(_parts) if _parts else t("landing.summary_done_fallback")
                    self._state.conversion_summary['message'] = msg_text + '。'
                    self._state.set_done(msg_text + '。')

        except Exception as exc:
            if not _any_error:
                self._state.set_error(str(exc))
        finally:
            self._worker_procs = []
            if self._state.is_processing:
                self._state.is_processing = False
            self._schedule_show_results()

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
        self._state.is_processing = False
        procs = list(self._worker_procs)
        self._worker_procs = []
        if not procs:
            return
        if sys.platform == 'win32':
            for p in procs:
                try:
                    subprocess.run(
                        ['taskkill', '/F', '/T', '/PID', str(p.pid)],
                        capture_output=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except Exception:
                    pass
        else:
            for p in procs:
                try:
                    import os as _os2
                    import signal as _sig
                    _os2.killpg(_os2.getpgid(p.pid), _sig.SIGKILL)
                except Exception:
                    pass
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
