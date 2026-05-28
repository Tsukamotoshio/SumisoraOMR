# gui/pages/jianpu_ocr_page.py — 简谱识别页（实验性）
# 左侧：文件列表（FilePicker / jianpu-Input/ 扫描）
# 右侧：模型状态 + 进度/日志 + 开始识别按钮
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

import flet as ft

_LOG = logging.getLogger('convert')

from ..app_state import AppState, Event
from core.app.backend import (
    editor_workspace_dir, jianpu_input_dir, output_dir,
    vlm_models_dir, xml_scores_dir,
)
from core.config import VLM_MODEL_FILENAME, VLM_MMPROJ_FILENAME
from ..components.vlm_download_dialog import VlmDownloadDialog
from ..theme import Palette

_ALLOWED_SUFFIXES = {'.png', '.jpg', '.jpeg', '.pdf'}


class JianpuOcrPage(ft.Row):
    """实验性简谱识别页：jianpu 图片/PDF → MusicXML → 跳转五线谱预览。"""

    def __init__(self, state: AppState):
        super().__init__(spacing=0, expand=True)
        self._state = state
        self._input_paths: list[Path] = []
        self._cancel_event = threading.Event()
        self._recognize_thread: Optional[threading.Thread] = None
        self._file_picker = ft.FilePicker()
        self._folder_picker = ft.FilePicker()
        self._build_ui()

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Sidebar: file list
        self._file_list_col = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)

        refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_size=16,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip='刷新 jianpu-Input 目录',
            on_click=lambda _: self._scan_input_dir(),
        )
        add_file_btn = ft.IconButton(
            icon=ft.Icons.ADD_ROUNDED,
            icon_size=16,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip='添加文件',
            on_click=self._on_add_file,
        )
        add_folder_btn = ft.IconButton(
            icon=ft.Icons.FOLDER_OPEN_ROUNDED,
            icon_size=16,
            icon_color=ft.Colors.ON_SURFACE_VARIANT,
            tooltip='添加文件夹',
            on_click=self._on_add_folder,
        )

        sidebar_header = ft.Container(
            content=ft.Row(
                [ft.Text('输入文件', size=13, weight=ft.FontWeight.W_600,
                          color=ft.Colors.ON_SURFACE, expand=True),
                 refresh_btn, add_file_btn, add_folder_btn],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            height=40,
            padding=ft.Padding(left=12, top=0, right=4, bottom=0),
            border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        sidebar = ft.Container(
            content=ft.Column([sidebar_header, self._file_list_col],
                              spacing=0, expand=True),
            width=240,
            bgcolor=ft.Colors.SURFACE,
            border=ft.Border(right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

        # Main area: model status + log + button
        self._model_status_row = ft.Column([], spacing=4)
        self._log_col = ft.Column(
            spacing=2, scroll=ft.ScrollMode.AUTO, expand=True,
            auto_scroll=True,
        )
        self._progress_bar = ft.ProgressBar(value=0, visible=False)

        self._start_btn = ft.ElevatedButton(
            content=ft.Row(
                [ft.Icon(ft.Icons.DOCUMENT_SCANNER_ROUNDED, size=18),
                 ft.Text('开始识别')],
                tight=True, spacing=6,
            ),
            bgcolor=Palette.PRIMARY,
            color='#FFFFFF',
            on_click=self._on_start_recognize,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                elevation={ft.ControlState.PRESSED: 0, ft.ControlState.DEFAULT: 2},
            ),
        )
        self._cancel_btn = ft.OutlinedButton(
            content=ft.Row(
                [ft.Icon(ft.Icons.STOP_ROUNDED, size=18), ft.Text('取消')],
                tight=True, spacing=6,
            ),
            on_click=self._on_cancel,
            visible=False,
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                side={ft.ControlState.DEFAULT: ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        main_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.IconButton(
                                    icon=ft.Icons.ARROW_BACK_ROUNDED,
                                    icon_size=18,
                                    icon_color=ft.Colors.ON_SURFACE_VARIANT,
                                    tooltip='返回',
                                    on_click=lambda _: self._state.emit(Event.JIANPU_OCR_BACK),
                                ),
                                ft.Text('简谱识别（实验性）', size=15,
                                        weight=ft.FontWeight.W_700,
                                        color=ft.Colors.ON_SURFACE),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=4,
                        ),
                        height=48,
                        padding=ft.Padding(left=4, top=0, right=16, bottom=0),
                        alignment=ft.Alignment(-1, 0),
                        border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
                    ),
                    ft.Container(
                        content=ft.Column(
                            [
                                self._model_status_row,
                                ft.Divider(height=1),
                                ft.Text('识别日志', size=12,
                                        color=ft.Colors.ON_SURFACE_VARIANT),
                                ft.Container(
                                    content=self._log_col,
                                    expand=True,
                                    bgcolor=ft.Colors.SURFACE_CONTAINER,
                                    border_radius=6,
                                    padding=ft.Padding(8, 8, 8, 8),
                                ),
                                self._progress_bar,
                                ft.Row([self._start_btn, self._cancel_btn], spacing=8),
                            ],
                            spacing=8,
                            expand=True,
                        ),
                        padding=ft.Padding(left=16, top=12, right=16, bottom=12),
                        expand=True,
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            expand=True,
            bgcolor=ft.Colors.SURFACE,
        )

        self.controls = [sidebar, main_panel]

    # ── Flet lifecycle ────────────────────────────────────────────────────────

    def did_mount(self) -> None:
        self.page._services.register_service(self._file_picker)    # type: ignore[attr-defined]
        self.page._services.register_service(self._folder_picker)  # type: ignore[attr-defined]
        self._scan_input_dir()
        self._refresh_model_status()

    def will_unmount(self) -> None:
        self._cancel_event.set()   # signal any running recognition to stop
        from core.vlm.jianpu_recognizer import release_vlm
        release_vlm()

    def reload(self) -> None:
        """Called by app.py when navigating to this page."""
        self._scan_input_dir()
        self._refresh_model_status()

    # ── File management ───────────────────────────────────────────────────────

    def _scan_input_dir(self) -> None:
        """Scan jianpu-Input/ and rebuild sidebar file list."""
        d = jianpu_input_dir()
        d.mkdir(parents=True, exist_ok=True)
        paths = sorted(
            p for p in d.iterdir()
            if p.is_file() and p.suffix.lower() in _ALLOWED_SUFFIXES
        )
        self._input_paths = paths
        self._rebuild_file_list()

    def _rebuild_file_list(self) -> None:
        # 所有 Flet 控件写操作必须在事件循环线程中执行，
        # 否则脏标记不会被触发，update() 不会发现变化。
        paths = list(self._input_paths)
        p = self.page
        if p is None:
            return

        async def _do():
            try:
                if not paths:
                    self._file_list_col.controls = [
                        ft.Container(
                            content=ft.Text('暂无文件\n请点击 + 添加',
                                            size=12, color=ft.Colors.OUTLINE,
                                            text_align=ft.TextAlign.CENTER),
                            padding=ft.Padding(16, 16, 16, 16),
                            alignment=ft.Alignment(0, 0),
                        )
                    ]
                else:
                    self._file_list_col.controls = [
                        self._make_file_row(fp) for fp in paths
                    ]
                self._file_list_col.update()
            except Exception as exc:
                _LOG.debug('[OCR] 列表刷新失败: %s', exc)

        p.run_task(_do)  # type: ignore[attr-defined]

    def _make_file_row(self, path: Path) -> ft.Container:
        remove_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_size=12,
            icon_color=ft.Colors.OUTLINE,
            width=24,
            height=24,
            tooltip='从列表移除',
            on_click=lambda _, p=path: self._remove_path(p),
        )
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.IMAGE_OUTLINED if path.suffix.lower() != '.pdf'
                            else ft.Icons.PICTURE_AS_PDF_OUTLINED,
                            size=14, color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Text(path.name, size=12, expand=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            color=ft.Colors.ON_SURFACE),
                    remove_btn,
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(left=8, top=4, right=8, bottom=4),
            border_radius=4,
        )

    def _remove_path(self, path: Path) -> None:
        if path in self._input_paths:
            self._input_paths.remove(path)
        self._rebuild_file_list()

    def _on_add_file(self, _) -> None:
        self.page.run_task(self._pick_files_async)  # type: ignore[attr-defined]

    def _on_add_folder(self, _) -> None:
        self.page.run_task(self._pick_folder_async)  # type: ignore[attr-defined]

    async def _pick_files_async(self) -> None:
        try:
            files = await self._file_picker.pick_files(
                allow_multiple=True,
                allowed_extensions=['png', 'jpg', 'jpeg', 'pdf'],
            )
        except Exception as exc:
            self._log(f'文件选择失败: {exc}')
            return
        if not files:
            return
        for f in files:
            if not f.path:
                continue
            p = Path(f.path)
            if p.suffix.lower() in _ALLOWED_SUFFIXES and p not in self._input_paths:
                self._input_paths.append(p)
        self._rebuild_file_list()

    async def _pick_folder_async(self) -> None:
        try:
            folder_path = await self._folder_picker.get_directory_path()
        except Exception as exc:
            self._log(f'文件夹选择失败: {exc}')
            return
        if not folder_path:
            return
        folder = Path(folder_path)
        try:
            entries = sorted(folder.iterdir())
        except PermissionError as exc:
            self._log(f'无法读取文件夹: {exc}')
            return
        for p in entries:
            if p.is_file() and p.suffix.lower() in _ALLOWED_SUFFIXES:
                if p not in self._input_paths:
                    self._input_paths.append(p)
        self._rebuild_file_list()

    # ── Model status ──────────────────────────────────────────────────────────

    def _refresh_model_status(self) -> None:
        from core.vlm.jianpu_recognizer import _LLAMA_AVAILABLE
        model_path = vlm_models_dir() / VLM_MODEL_FILENAME
        mmproj_path = vlm_models_dir() / VLM_MMPROJ_FILENAME
        both_present = model_path.exists() and mmproj_path.exists()
        ready = both_present and _LLAMA_AVAILABLE
        self._state.vlm_available = ready

        rows: list = []
        if ready:
            rows.append(ft.Row([
                ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, color=Palette.SUCCESS, size=16),
                ft.Text('模型已就绪', size=13, color=ft.Colors.ON_SURFACE),
            ], spacing=6))
        else:
            if not both_present:
                rows.append(ft.Row([
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=ft.Colors.ORANGE, size=16),
                    ft.Text('模型未下载', size=13, color=ft.Colors.ON_SURFACE),
                    ft.TextButton(
                        '下载模型权重',
                        icon=ft.Icons.DOWNLOAD_ROUNDED,
                        on_click=self._on_download_model,
                    ),
                ], spacing=6))
            if not _LLAMA_AVAILABLE:
                rows.append(ft.Row([
                    ft.Icon(ft.Icons.ERROR_ROUNDED, color=ft.Colors.ERROR, size=16),
                    ft.Text('未安装 llama-cpp-python', size=13, color=ft.Colors.ON_SURFACE),
                ], spacing=6))

        disabled = not ready
        p = self.page
        if p is None:
            return

        async def _do():
            try:
                self._model_status_row.controls = rows
                self._start_btn.disabled = disabled
                self._model_status_row.update()
                self._start_btn.update()
            except Exception as exc:
                _LOG.debug('[OCR] 状态刷新失败: %s', exc)

        p.run_task(_do)  # type: ignore[attr-defined]

    def _on_download_model(self, _) -> None:
        dlg = VlmDownloadDialog(
            self.page,
            self._state,
            on_complete=self._on_model_downloaded,
        )
        dlg.show()

    def _on_model_downloaded(self) -> None:
        self._refresh_model_status()
        self._log('模型下载完成，可以开始识别。')

    # ── Recognition ──────────────────────────────────────────────────────────

    def _on_start_recognize(self, _) -> None:
        if not self._input_paths:
            self._log('请先添加简谱图片或 PDF 文件。')
            return
        self._cancel_event = threading.Event()
        self._start_btn.visible = False
        self._cancel_btn.visible = True
        self._progress_bar.visible = True
        self._progress_bar.value = 0
        try:
            self._start_btn.update()
            self._cancel_btn.update()
            self._progress_bar.update()
        except Exception:
            pass
        paths_snapshot = list(self._input_paths)
        self._recognize_thread = threading.Thread(
            target=self._run_recognition, args=(paths_snapshot,), daemon=True
        )
        self._recognize_thread.start()

    def _on_cancel(self, _) -> None:
        self._cancel_event.set()
        self._log('正在取消…')

    def _run_recognition(self, paths: list) -> None:
        from core.vlm.jianpu_recognizer import recognize_image, recognize_pdf, release_vlm
        from core.vlm.json_to_musicxml import convert, to_jianpu_text

        model_path  = vlm_models_dir() / VLM_MODEL_FILENAME
        mmproj_path = vlm_models_dir() / VLM_MMPROJ_FILENAME
        out_dir     = xml_scores_dir()
        results: list[Path] = []

        total = len(paths)
        for i, src in enumerate(paths):
            if self._cancel_event.is_set():
                self._marshal(self._log, '已取消。')
                self._marshal(self._finish_recognition, False)
                return
            self._marshal(self._log, f'[{i + 1}/{total}] 识别 {src.name}…')
            self._marshal(self._set_progress, i / total)

            def _progress(elapsed: int) -> None:
                self._marshal(self._log, f'  LLM 推理中… 已等待 {elapsed} 秒')

            try:
                if src.suffix.lower() == '.pdf':
                    self._marshal(self._log, '  图像处理完成，LLM 推理中（可能需要 1-3 分钟）…')
                    data = recognize_pdf(src, model_path, mmproj_path)
                else:
                    self._marshal(self._log, '  图像处理完成，LLM 推理中（可能需要 1-3 分钟）…')
                    data = recognize_image(src, model_path, mmproj_path,
                                           on_progress=_progress)

                n_measures = len(data.get('measures', []))
                self._marshal(self._log, f'  识别到 {n_measures} 个小节，key={data.get("key")}, time={data.get("time_signature")}')
                out_path = out_dir / f'{src.stem}_ocr.musicxml'
                # MIDI 与现有流水线对齐：放 Output/，供预览页一键播放
                midi_path = output_dir(None) / f'{src.stem}_ocr.mid'
                convert(data, out_path, midi_path=midi_path)
                # jianpu.txt 让用户能在编辑器里校对
                try:
                    jianpu_txt = editor_workspace_dir() / f'{src.stem}_ocr.jianpu.txt'
                    jianpu_txt.parent.mkdir(parents=True, exist_ok=True)
                    jianpu_txt.write_text(to_jianpu_text(data, src.stem + '_ocr'),
                                          encoding='utf-8')
                except Exception as exc:
                    self._marshal(self._log, f'  ⚠ jianpu.txt 写入失败: {exc}')
                    jianpu_txt = None
                results.append(out_path)
                msg = f'  → 已保存: {out_path.name}'
                if midi_path.exists():
                    msg += f' + {midi_path.name}'
                if jianpu_txt and jianpu_txt.exists():
                    msg += f' + {jianpu_txt.name}'
                self._marshal(self._log, msg)
            except Exception as exc:
                self._marshal(self._log, f'  ✗ 失败: {exc}')

        self._marshal(self._log, f'完成。共 {len(results)}/{total} 个文件识别成功。')
        self._marshal(self._set_progress, 1.0)
        # 立即释放 GPU 显存（7B 模型约 4.4 GB + KV cache）
        try:
            release_vlm()
            self._marshal(self._log, '  已释放 GPU 显存')
        except Exception as exc:
            self._marshal(self._log, f'  释放显存失败: {exc}')
        self._marshal(self._finish_recognition, bool(results))

    def _finish_recognition(self, has_results: bool) -> None:
        self._start_btn.visible = True
        self._cancel_btn.visible = False
        self._progress_bar.visible = False
        try:
            self._start_btn.update()
            self._cancel_btn.update()
            self._progress_bar.update()
        except Exception:
            pass
        if has_results:
            self._state.emit(Event.JIANPU_OCR_DONE)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self._log_col.controls.append(
            ft.Text(msg, size=12, selectable=True,
                    color=ft.Colors.ON_SURFACE_VARIANT)
        )
        if len(self._log_col.controls) > 200:
            self._log_col.controls.pop(0)
        try:
            self._log_col.update()
        except Exception:
            pass

    def _set_progress(self, value: float) -> None:
        self._progress_bar.value = max(0.0, min(1.0, value))
        try:
            self._progress_bar.update()
        except Exception:
            pass

    def _marshal(self, fn, *args) -> None:
        p = self.page
        if p is None:
            return
        try:
            p.loop.call_soon_threadsafe(lambda f=fn, a=args: f(*a))
        except Exception:
            pass
