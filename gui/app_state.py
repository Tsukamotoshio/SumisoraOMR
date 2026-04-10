# gui/app_state.py — 应用全局状态 + 事件总线
# 不依赖任何 GUI 库实现，可被任意模块安全导入。
# 使用观察者模式：订阅者通过 on(event, callback) 注册回调。

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# ─────────────────────────────────────────────────────────────────────────────
# 事件名称常量（避免魔法字符串）
# ─────────────────────────────────────────────────────────────────────────────

class Event:
    FILES_CHANGED       = 'files_changed'       # 钉选文件列表发生变化
    FILE_SELECTED       = 'file_selected'       # 用户选中某文件
    PAGE_CHANGED        = 'page_changed'        # 导航页切换
    PROGRESS_UPDATE     = 'progress_update'     # 进度更新 (value: float 0-1)
    PROGRESS_DONE       = 'progress_done'       # 处理完成
    PROGRESS_ERROR      = 'progress_error'      # 处理出错 (message: str)
    LOG_LINE            = 'log_line'            # 新日志行 (line: str)
    MXL_READY           = 'mxl_ready'          # MusicXML 文件就绪 (path: Path)
    TRANSPOSED_READY    = 'transposed_ready'    # 移调结果就绪 (path: Path)
    JIANPU_TXT_SELECTED = 'jianpu_txt_selected' # 编辑器选中某行简谱 (line_no: int)
    THEME_CHANGED       = 'theme_changed'       # 主题切换 (dark: bool)


# ─────────────────────────────────────────────────────────────────────────────
# 应用状态
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AppState:
    """线程安全的全局应用状态。

    UI 组件通过 state.on(Event.XXX, callback) 订阅变更通知，
    通过 state.emit(Event.XXX, **kwargs) 发布事件。

    所有字段都是普通 Python 对象；Flet 控件不应存入此类。
    """

    # 文件管理
    pinned_files: list[Path]        = field(default_factory=list)
    current_file: Optional[Path]    = None

    # 中间产物路径
    current_mxl:       Optional[Path] = None   # 最近识别到的 MusicXML
    transposed_mxl:    Optional[Path] = None   # 移调结果
    current_jianpu_txt: Optional[Path] = None  # 当前编辑的简谱文本
    output_pdf:        Optional[Path] = None   # 已生成的简谱 PDF

    # 移调参数
    transpose_from_key: str = 'C'
    transpose_to_key:   str = 'C'

    # UI 状态
    current_page:  str  = 'landing'  # 'landing' | 'editor' | 'transposer'
    dark_mode:     bool = True
    is_processing: bool = False
    progress:      float = 0.0

    # 日志
    log_lines: list[str] = field(default_factory=list)
    max_log_lines: int = 500

    # 内部：事件总线
    _listeners: dict[str, list[Callable]] = field(default_factory=dict)
    _lock: threading.Lock                 = field(default_factory=threading.Lock)

    # ── 事件总线 ────────────────────────────────────────────────────────────

    def on(self, event: str, callback: Callable, *, once: bool = False) -> None:
        """订阅事件。callback 在 emit 时同步调用（需注意在线程中 update UI）。"""
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

    # ── 状态变更辅助 ─────────────────────────────────────────────────────────

    def add_file(self, path: Path) -> None:
        path = path.resolve()
        if path not in self.pinned_files:
            self.pinned_files.append(path)
            self.emit(Event.FILES_CHANGED, files=list(self.pinned_files))

    def remove_file(self, path: Path) -> None:
        path = path.resolve()
        self.pinned_files = [f for f in self.pinned_files if f != path]
        if self.current_file == path:
            self.current_file = self.pinned_files[0] if self.pinned_files else None
        self.emit(Event.FILES_CHANGED, files=list(self.pinned_files))

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


# 全局单例（app.py 初始化后应替换为此实例）
state = AppState()
