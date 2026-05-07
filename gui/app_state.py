# gui/app_state.py — Global application state + event bus
# No GUI library dependency; safe to import from any module.
# Observer pattern: subscribers register via on(event, callback).

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Event name constants (no magic strings)
# ─────────────────────────────────────────────────────────────────────────────

class Event:
    FILES_CHANGED       = 'files_changed'       # pinned file list changed
    FILES_CHECK_CHANGED = 'files_check_changed' # file check state changed (checked: set[Path])
    FILES_IMPORTED      = 'files_imported'      # files copied to Input/ (paths: list[Path])
    FILE_SELECTED       = 'file_selected'       # user selected a file
    PAGE_CHANGED        = 'page_changed'        # navigation page switched
    PROGRESS_UPDATE     = 'progress_update'     # progress update (value: float 0-1)
    PROGRESS_DONE       = 'progress_done'       # processing finished
    PROGRESS_ERROR      = 'progress_error'      # processing error (message: str)
    LOG_LINE            = 'log_line'            # new log line (line: str)
    MXL_READY           = 'mxl_ready'          # MusicXML ready (path: Path)
    TRANSPOSED_READY    = 'transposed_ready'    # transposition result ready (path: Path)
    JIANPU_TXT_SELECTED   = 'jianpu_txt_selected'   # editor selected a jianpu row (line_no: int)
    JIANPU_EDIT_REQUESTED = 'jianpu_edit_requested' # preview page requests edit sub-page (path: Path)
    JIANPU_PREVIEW_BACK   = 'jianpu_preview_back'   # edit sub-page requests back to preview
    SCORE_TRANSPOSER_REQUESTED = 'score_transposer_requested' # score_preview requests transposer (path: Path)
    SCORE_TRANSPOSER_BACK      = 'score_transposer_back'      # transposer requests back to score_preview
    THEME_CHANGED         = 'theme_changed'         # theme toggled (dark: bool)
    MODELS_DOWNLOADED     = 'models_downloaded'     # HOMR weights finished downloading


# ─────────────────────────────────────────────────────────────────────────────
# Application state
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AppState:
    """Thread-safe global application state.

    UI components subscribe to changes via state.on(Event.XXX, callback)
    and publish events via state.emit(Event.XXX, **kwargs).

    All fields are plain Python objects; Flet controls must not be stored here.
    """

    # File management
    pinned_files: list[Path]        = field(default_factory=list)
    checked_files: set[Path]        = field(default_factory=set)   # files checked for conversion
    current_file: Optional[Path]    = None

    # Intermediate output paths
    current_mxl:       Optional[Path] = None   # most recent OMR-recognised MusicXML
    transposed_mxl:    Optional[Path] = None   # transposition result
    current_jianpu_txt:  Optional[Path] = None  # jianpu text file open in editor
    output_pdf:         Optional[Path] = None   # generated jianpu PDF
    jianpu_edit_source: Optional[Path] = None   # jianpu PDF selected for editing from preview page

    # Transposition parameters
    transpose_from_key: str = 'C'
    transpose_to_key:   str = 'C'

    # UI state
    current_page:  str  = 'landing'  # 'landing' | 'editor' | 'transposer'
    dark_mode:     bool = False
    is_processing: bool = False
    progress:      float = 0.0

    # Engine availability (HOMR weights presence; set on startup + after download)
    homr_available: bool = False

    # Log
    log_lines: list[str] = field(default_factory=list)
    max_log_lines: int = 500

    # Conversion result record
    conversion_summary: dict[str, Any] = field(default_factory=dict)

    # Internal: event bus
    _listeners: dict[str, list[Callable]] = field(default_factory=dict)
    _lock: threading.Lock                 = field(default_factory=threading.Lock)

    # ── Event bus ───────────────────────────────────────────────────────────

    def on(self, event: str, callback: Callable, *, once: bool = False) -> None:
        """Subscribe to an event. The callback is called synchronously on emit
        (take care with UI updates from non-main threads)."""
        with self._lock:
            if event not in self._listeners:
                self._listeners[event] = []
            if once:
                # 包装成"只触发一次"的回调
                original = callback
                def _once_wrapper(**kw):
                    self.off(event, _once_wrapper)
                    original(**kw)
                _once_wrapper.__wrapped__ = original  # type: ignore[attr-defined]
                callback = _once_wrapper
            self._listeners[event].append(callback)

    def off(self, event: str, callback: Callable) -> None:
        with self._lock:
            handlers = self._listeners.get(event, [])
            self._listeners[event] = [
                h for h in handlers
                if h is not callback
                and getattr(h, '__wrapped__', None) is not callback
            ]

    def emit(self, event: str, **kwargs: Any) -> None:
        with self._lock:
            callbacks = list(self._listeners.get(event, []))
        for cb in callbacks:
            try:
                cb(**kwargs)
            except Exception:
                pass  # GUI 回调不应让核心逻辑崩溃

    # ── State mutation helpers ──────────────────────────────────────────────

    def add_file(self, path: Path) -> None:
        """Add a file to the pinned list (supported formats only)."""
        path = path.resolve()
        # 验证文件类型（只允许 pdf, png, jpg, jpeg）
        supported_suffixes = {'.pdf', '.png', '.jpg', '.jpeg'}
        if path.suffix.lower() not in supported_suffixes:
            return
        if path not in self.pinned_files:
            self.pinned_files.append(path)
            self.checked_files.add(path)
            self.emit(Event.FILES_CHANGED, files=list(self.pinned_files))

    def remove_file(self, path: Path) -> None:
        path = path.resolve()
        self.pinned_files = [f for f in self.pinned_files if f != path]
        self.checked_files.discard(path)
        if self.current_file == path:
            self.current_file = self.pinned_files[0] if self.pinned_files else None
        self.emit(Event.FILES_CHANGED, files=list(self.pinned_files))

    def toggle_check(self, path: Path) -> None:
        """Toggle the checked state of a single file."""
        path = path.resolve()
        if path in self.checked_files:
            self.checked_files.discard(path)
        else:
            self.checked_files.add(path)
        self.emit(Event.FILES_CHECK_CHANGED, checked=set(self.checked_files))

    def check_all(self) -> None:
        """Check all loaded files."""
        self.checked_files = set(self.pinned_files)
        self.emit(Event.FILES_CHECK_CHANGED, checked=set(self.checked_files))

    def uncheck_all(self) -> None:
        """Uncheck all files."""
        self.checked_files = set()
        self.emit(Event.FILES_CHECK_CHANGED, checked=set(self.checked_files))

    def select_file(self, path: Path) -> None:
        self.current_file = path.resolve()
        self.emit(Event.FILE_SELECTED, path=self.current_file)

    def set_page(self, page: str) -> None:
        self.current_page = page
        self.emit(Event.PAGE_CHANGED, page=page)

    def set_progress(self, value: float, message: str = '') -> None:
        self.progress = max(0.0, min(1.0, value))
        self.emit(Event.PROGRESS_UPDATE, value=self.progress, message=message)

    def set_done(self, message: str = '完成') -> None:
        self.is_processing = False
        self.progress = 1.0
        self.emit(Event.PROGRESS_DONE, message=message)

    def set_error(self, message: str) -> None:
        self.is_processing = False
        self.emit(Event.PROGRESS_ERROR, message=message)

    def append_log(self, line: str) -> None:
        self.log_lines.append(line)
        if len(self.log_lines) > self.max_log_lines:
            self.log_lines = self.log_lines[-self.max_log_lines:]
        self.emit(Event.LOG_LINE, line=line)

    def toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self.emit(Event.THEME_CHANGED, dark=self.dark_mode)


# Module-level singleton; app.py replaces this with the live instance at startup.
state = AppState()
