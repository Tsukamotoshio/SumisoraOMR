# app.py — Flet GUI 主程序入口
# OMR 转换工具 — 现代扁平化 GUI（深色模式优先）
#
# 启动方式：
#   python app.py
#   或（开发模式热重载）：flet run app.py
#
# 依赖：
#   pip install -r requirements.txt


import sys
import os
import warnings

warnings.filterwarnings(
    'ignore',
    message='Enable tracemalloc to get the object allocation traceback',
    category=RuntimeWarning,
)

# ─── SSL 证书：解决打包后部分环境证书验证失败问题 ─────────────────────────────
# 在所有网络请求（flet / urllib / requests / httpx）之前设置，
# 强制使用随包附带的 certifi 证书，避免旧版 Windows 根证书缺失或 SSL 中间人干扰。
#   SSL_CERT_FILE    — Python ssl 模块 / urllib / httpx
#   REQUESTS_CA_BUNDLE — requests 库
#   CURL_CA_BUNDLE   — libcurl 系库
if getattr(sys, 'frozen', False):
    _meipass = getattr(sys, '_MEIPASS', None)
    if _meipass is not None:
        _internal_cert = os.path.join(_meipass, 'certifi', 'cacert.pem')
        if os.path.exists(_internal_cert):
            os.environ['SSL_CERT_FILE'] = _internal_cert
            os.environ['REQUESTS_CA_BUNDLE'] = _internal_cert
            os.environ['CURL_CA_BUNDLE'] = _internal_cert
    # console=False 时 sys.stdout/stderr 为 None，所有 print() 会崩溃
    # 替换为 null 流，将输出静默丢弃（日志仍写入 logs/ 文件）
    # 注意：Worker 子进程需要保留 stdout 作为 IPC 管道，不能重定向到 devnull
    _is_worker_process = '--worker' in sys.argv
    if not _is_worker_process:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, 'w', encoding='utf-8', errors='replace')
        if sys.stderr is None:
            sys.stderr = open(os.devnull, 'w', encoding='utf-8', errors='replace')

# ─── ONNX / OpenMP 线程限制：为 asyncio 事件循环保留 CPU 资源 ────────────────
# homr 使用 ONNX Runtime 进行神经网络推理，默认会占满所有 CPU 核心。
# 在打包版中，CPU 满载导致 asyncio 事件循环长时间得不到调度，
# Flet WebSocket 心跳超时，Flutter 端显示 "Working..." 重连画面。
# 在首次 import onnxruntime / 初始化 OpenMP 线程池之前设置，保留 1 个核心给 asyncio。
_cpu_count = os.cpu_count() or 4
_onnx_threads = str(max(1, _cpu_count - 1))
os.environ.setdefault('OMP_NUM_THREADS',          _onnx_threads)
os.environ.setdefault('OPENBLAS_NUM_THREADS',     _onnx_threads)
os.environ.setdefault('MKL_NUM_THREADS',          _onnx_threads)
os.environ.setdefault('VECLIB_MAXIMUM_THREADS',   _onnx_threads)
os.environ.setdefault('NUMEXPR_NUM_THREADS',      _onnx_threads)

# ─── Bootstrap：确保在正确的虚拟环境中运行 ──────────────────────────────────
def _bootstrap_venv() -> None:
    try:
        import flet  # noqa: F401
        return
    except ImportError:
        pass
    _here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(_here, '.venv', 'Scripts', 'python.exe'),
        os.path.join(_here, '.venv', 'bin', 'python'),
    ]
    for _py in candidates:
        if os.path.isfile(_py):
            import subprocess
            sys.exit(subprocess.run([_py] + sys.argv).returncode)
    print(
        '\n[错误] 未找到虚拟环境或 flet 未安装。\n'
        '  pip install -r requirements.txt\n'
        '  pip install flet pymupdf music21 pillow opencv-python onnxruntime-directml\n',
        file=sys.stderr,
    )
    sys.exit(1)

_bootstrap_venv()
# ─────────────────────────────────────────────────────────────────────────────

# Worker 子进程早退出：在导入 flet / GUI 模块之前分流，节省内存和启动时间
if __name__ == '__main__' and '--worker' in sys.argv:
    import multiprocessing
    multiprocessing.freeze_support()
    from core.omr.worker_main import run_worker
    run_worker()
    import os as _os
    _os._exit(0)   # 强制退出：绕过 onnxruntime 等库遗留的非 daemon 线程

import flet as ft

from gui.app_state import AppState, Event
from gui.theme import Palette, make_dark_theme, make_light_theme
from gui.pages.landing_page import LandingPage
from gui.pages.editor_page import EditorPage
from gui.pages.transposer_page import TransposerPage
from gui.pages.about_page import AboutPage
from gui.components.progress_overlay import ProgressOverlay


# ─────────────────────────────────────────────────────────────────────────────
# 导航项定义
# ─────────────────────────────────────────────────────────────────────────────

_NAV_ITEMS = [
    ('landing',    ft.Icons.ARROW_CIRCLE_RIGHT_ROUNDED,  ft.Icons.ARROW_CIRCLE_RIGHT_OUTLINED,  '乐谱识别'),
    ('editor',     ft.Icons.EDIT_NOTE_ROUNDED,           ft.Icons.EDIT_NOTE_OUTLINED,            '简谱编辑'),
    ('transposer', ft.Icons.MUSIC_NOTE_ROUNDED,          ft.Icons.MUSIC_NOTE_OUTLINED,           '移调引擎'),
    ('about',      ft.Icons.INFO_ROUNDED,                ft.Icons.INFO_OUTLINE_ROUNDED,          '关于'),
]


# ─────────────────────────────────────────────────────────────────────────────
# 主应用
# ─────────────────────────────────────────────────────────────────────────────

async def main(page: ft.Page) -> None:
    # ── 页面基本配置 ──────────────────────────────────────────────────────────
    page.title       = 'OMR 乐谱转换工具'
    page.window.min_width  = 900
    page.window.min_height = 600
    page.window.width      = 1280
    page.window.height     = 820
    page.padding     = 0
    page.spacing     = 0

    # ── 字体配置 ──────────────────────────────────────────────────────────────
    # 注册微软雅黑（绝对路径），为 CJK 字符提供无衬线字体，避免回退到宋体
    import os as _os
    _yahe_path = r'C:\Windows\Fonts\msyh.ttc'
    _font_key  = 'YaHei'
    if _os.path.isfile(_yahe_path):
        page.fonts = {_font_key: _yahe_path}
    else:
        # macOS / Linux 回退：直接用系统字体名（无需 page.fonts 注册）
        _font_key = 'PingFang SC' if _os.path.isfile('/System/Library/Fonts/PingFang.ttc') else 'Noto Sans CJK SC'

    page.theme_mode  = ft.ThemeMode.DARK
    page.theme       = make_dark_theme(font_family=_font_key)
    page.dark_theme  = make_dark_theme(font_family=_font_key)

    # ── 全局状态 ──────────────────────────────────────────────────────────────
    state = AppState()

    # 将 core/utils.log_message 重定向到 GUI 日志流
    try:
        import logging as _logging
        import core.utils as _cutils
        _orig_log = _cutils.log_message
        def _gui_log(msg: str, level: int = _logging.INFO):
            state.append_log(msg)
            _orig_log(msg, level)
        _cutils.log_message = _gui_log
    except Exception:
        pass

    # ── 进度浮层（全局共享）──────────────────────────────────────────────────
    overlay = ProgressOverlay(state)

    # ── 四页面 ────────────────────────────────────────────────────────────────
    landing_page    = LandingPage(state, overlay)
    editor_page     = EditorPage(state)
    transposer_page = TransposerPage(state)
    about_page      = AboutPage()

    _pages: dict[str, ft.Control] = {
        'landing':    landing_page,
        'editor':     editor_page,
        'transposer': transposer_page,
        'about':      about_page,
    }

    # ── 内容区（ft.Stack 允许浮层覆盖）──────────────────────────────────────
    content_stack = ft.Stack(
        [
            ft.Container(content=landing_page,    expand=True, visible=True),
            ft.Container(content=editor_page,     expand=True, visible=False),
            ft.Container(content=transposer_page, expand=True, visible=False),
            ft.Container(content=about_page,      expand=True, visible=False),
            overlay,
        ],
        expand=True,
    )
    _content_containers: list[ft.Container] = content_stack.controls[:4]  # type: ignore[index]

    def _show_page(name: str) -> None:
        for i, (page_name, *_) in enumerate(_NAV_ITEMS):
            _content_containers[i].visible = (page_name == name)
        state.current_page = name
        if name == 'editor' and not getattr(editor_page, '_has_been_shown', False):
            editor_page.reset_view()
            editor_page._has_been_shown = True
        if name == 'transposer' and not getattr(transposer_page, '_has_been_shown', False):
            transposer_page.reset_view()
            transposer_page._has_been_shown = True
        try:
            content_stack.update()
        except Exception:
            pass

    # ── NavigationRail（左侧导航） ────────────────────────────────────────────

    def _on_nav_change(e) -> None:
        name = _NAV_ITEMS[e.control.selected_index][0]
        _show_page(name)
        # 同步导航状态事件
        state.emit(Event.PAGE_CHANGED, page=name)

    nav_rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.SELECTED,
        bgcolor=Palette.BG_SURFACE,
        indicator_color=Palette.PRIMARY + '44',
        on_change=_on_nav_change,
        destinations=[
            ft.NavigationRailDestination(
                icon=icon_out,
                selected_icon=icon_sel,
                label=label,
            )
            for _, icon_sel, icon_out, label in _NAV_ITEMS
        ],
        min_width=64,
        min_extended_width=150,
    )

    # ── 右上角：主题切换 ──────────────────────────────────────────────────────
    _theme_icon = ft.IconButton(
        icon=ft.Icons.DARK_MODE_ROUNDED,
        icon_color=Palette.TEXT_SECONDARY,
        tooltip='切换明暗主题',
        on_click=lambda _e: _toggle_theme(),
    )

    def _toggle_theme() -> None:
        state.toggle_theme()
        if state.dark_mode:
            page.theme_mode = ft.ThemeMode.DARK
            page.theme      = make_dark_theme(font_family=_font_key)
            page.dark_theme = make_dark_theme(font_family=_font_key)
            _theme_icon.icon = ft.Icons.DARK_MODE_ROUNDED
        else:
            page.theme_mode  = ft.ThemeMode.LIGHT
            page.theme       = make_light_theme(font_family=_font_key)
            _theme_icon.icon = ft.Icons.LIGHT_MODE_ROUNDED
        try:
            page.update()
        except Exception:
            pass

    # ── 顶部 AppBar ──────────────────────────────────────────────────────────
    page.appbar = ft.AppBar(
        title=ft.Text('OMR 乐谱转换工具  v0.2.4', size=15, weight=ft.FontWeight.W_600),
        center_title=False,
        bgcolor=Palette.BG_SURFACE,
        leading=ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, color=Palette.PRIMARY),
        actions=[
            ft.Container(width=8),
        ],
        elevation=0,
        toolbar_height=48,
    )

    # ── 通知（SnackBar）辅助 ─────────────────────────────────────────────────
    def _show_snack(msg: str, color: str = Palette.INFO) -> None:
        page.show_dialog(ft.SnackBar(
            content=ft.Text(msg, color='#FFFFFF'),
            bgcolor=color,
            duration=3500,
        ))

    def _on_error(message: str, **_kw) -> None:
        # 工作线程通过 state.emit() 同步调用此回调；
        # 用 run_task 调度到 asyncio 事件循环，避免与计时器 page.update() 竞争。
        async def _do():
            _show_snack(f'错误: {message}', Palette.ERROR)
        page.run_task(_do)

    def _on_done(message: str = '完成', **_kw) -> None:
        async def _do():
            _show_snack(message, Palette.SUCCESS)
        page.run_task(_do)

    state.on(Event.PROGRESS_ERROR, _on_error)
    state.on(Event.PROGRESS_DONE,  _on_done)

    # ── 页面布局 ─────────────────────────────────────────────────────────────
    left_rail_container = ft.Container(
        content=nav_rail,
        bgcolor=Palette.BG_SURFACE,
        border=ft.Border.only(right=ft.BorderSide(1, Palette.DIVIDER_DARK)),
    )

    page.add(
        ft.Row(
            [
                left_rail_container,
                ft.Container(content=content_stack, expand=True),
            ],
            spacing=0,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )
    )

    # ── 窗口关闭：终止进行中的 Worker 子进程 ────────────────────────────────
    # 不设置 prevent_close，让 Flutter 在用户点 X 后立即自然关闭窗口（无延迟、
    # 无 "Working..." 重连画面）。
    # page.on_close 在 Flutter 断开 WebSocket 后由 session.close() 异步触发，
    # 此时窗口已关闭，用户感知不到任何延迟，Python 在后台完成清理再退出。
    async def _on_app_close(e) -> None:
        import threading
        threading.Thread(target=landing_page.terminate_worker, daemon=True).start()
        if sys.platform == 'win32':
            import ctypes
            # TerminateProcess 跳过 DLL_PROCESS_DETACH，比 os._exit 更快
            ctypes.windll.kernel32.TerminateProcess(-1, 0)
        else:
            _os._exit(0)

    page.on_close = _on_app_close

    # ── 初始化完成 ────────────────────────────────────────────────────────────
    _show_page('landing')


# ─────────────────────────────────────────────────────────────────────────────
# 【开发模式 & 分发版】让任务管理器显示 "ConvertTool" 而非 "flet"
# ─────────────────────────────────────────────────────────────────────────────
# flet_desktop 通过 open_flet_view_async 启动 Flutter 窗口进程。
# 此函数确保使用重命名后的 ConvertTool.exe 作为 Flutter 运行时，
# 并 monkey-patch open_flet_view_async 直接指定该 exe，
# 不依赖 os.getcwd()/build/windows/ 发现机制。
#
# 开发模式：从 ~/.flet/client/flet-desktop-full-X.Y.Z/flet/ 复制并重命名
# 打包版  ：从 _MEIPASS/flet_desktop/app/flet-windows.zip 解压并重命名
#           目标目录均为 ~/.flet/ConvertTool/<version>/，首次运行后缓存，不重复操作。
def _setup_flet_view_name() -> None:
    if sys.platform != 'win32':
        return
    try:
        import shutil as _shutil
        import zipfile as _zipfile
        from pathlib import Path as _Path
        import flet_desktop as _fd
        import flet_desktop.version as _fdv
        from flet.utils.strings import random_string as _rstr

        _EXE   = 'ConvertTool.exe'
        _ver   = _fdv.version
        _dir   = _Path.home() / '.flet' / 'ConvertTool' / _ver
        _exe   = _dir / _EXE
        _stamp = _dir / '.stamp'

        if not (_stamp.exists() and _exe.exists()):
            _dir.mkdir(parents=True, exist_ok=True)
            # 清理旧文件（flet 版本升级时重新解压）
            for _f in list(_dir.iterdir()):
                if _f.name != '.stamp':
                    _shutil.rmtree(_f) if _f.is_dir() else _f.unlink(missing_ok=True)

            if getattr(sys, 'frozen', False):
                # 打包版：从随包附带的 flet-windows.zip 解压
                _meipass = getattr(sys, '_MEIPASS', None)
                if _meipass is None:
                    return
                _zip = _Path(_meipass) / 'flet_desktop' / 'app' / 'flet-windows.zip'
                if not _zip.exists():
                    return
                with _zipfile.ZipFile(_zip) as _zf:
                    # zip 内结构为 flet/flet.exe, flet/*.dll, flet/data/...
                    # 解压时去掉顶层 flet/ 目录，直接平铺到 _dir
                    for _m in _zf.namelist():
                        _parts = _m.split('/', 1)
                        if len(_parts) == 2 and _parts[0] == 'flet' and _parts[1]:
                            _dst = _dir / _parts[1]
                            if _m.endswith('/'):
                                _dst.mkdir(parents=True, exist_ok=True)
                            else:
                                _dst.parent.mkdir(parents=True, exist_ok=True)
                                _dst.write_bytes(_zf.read(_m))
            else:
                # 开发模式：从用户 flet 缓存复制
                _cache = (_Path.home() / '.flet' / 'client'
                          / f'flet-desktop-full-{_ver}' / 'flet')
                if not _cache.exists():
                    return
                for _item in _cache.iterdir():
                    _dst = _dir / _item.name
                    if _item.is_dir():
                        if _dst.exists():
                            _shutil.rmtree(_dst)
                        _shutil.copytree(_item, _dst)
                    else:
                        _shutil.copy2(_item, _dst)

            # flet.exe → ConvertTool.exe（两种来源均适用）
            _flet_exe = _dir / 'flet.exe'
            if _flet_exe.exists():
                _flet_exe.rename(_exe)
            _stamp.touch()

        if not _exe.exists():
            return  # 设置失败，回退到默认 flet.exe

        # ── monkey-patch open_flet_view_async ────────────────────────────────
        # 直接指定 ConvertTool.exe，跳过 flet_desktop 内部的路径发现逻辑，
        # 避免对 os.getcwd() / build/windows/ 的依赖
        _exe_str = str(_exe)

        async def _patched_open(page_url, assets_dir, hidden):
            import asyncio as _aio
            import tempfile as _tmp
            import os as _o
            _pid  = str(_Path(_tmp.gettempdir()) / _rstr(20))
            _args = [_exe_str, page_url, _pid]
            if assets_dir:
                _args.append(assets_dir)
            _env = {**_o.environ}
            if hidden:
                _env['FLET_HIDE_WINDOW_ON_START'] = 'true'
            return (await _aio.create_subprocess_exec(_args[0], *_args[1:], env=_env), _pid)

        _fd.open_flet_view_async = _patched_open

    except Exception:
        pass  # 设置失败不影响应用启动，回退到默认 flet.exe


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    _setup_flet_view_name()
    ft.run(main)
