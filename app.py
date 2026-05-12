# app.py — Flet GUI entry point
# OMR Sheet Music Conversion Tool — modern flat GUI (dark-first)
#
# Usage:
#   python app.py
#   or (dev mode with hot-reload): flet run app.py
#
# Dependencies:
#   pip install -r requirements.txt


import sys
import os
import warnings

warnings.filterwarnings(
    'ignore',
    message='Enable tracemalloc to get the object allocation traceback',
    category=RuntimeWarning,
)

# ─── SSL certificates: fix cert-validation failures in packaged builds ───────
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

# ─── ONNX / OpenMP thread cap: reserve CPU headroom for asyncio event loop ───
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

# ─── Bootstrap: ensure correct virtual environment ───────────────────────────
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
if sys.platform == 'win32' and '--worker' not in sys.argv:
    os.environ.setdefault('FLET_APP_USER_MODEL_ID', 'Tsukamotoshio.SumisoraOMR')
    os.environ.setdefault('FLET_HIDE_WINDOW_ON_START', 'true')
# ─────────────────────────────────────────────────────────────────────────────

# Worker subprocess early exit: branch before flet/GUI imports, saving memory and startup time
if __name__ == '__main__' and '--worker' in sys.argv:
    import multiprocessing
    multiprocessing.freeze_support()
    from core.omr.worker_main import run_worker
    run_worker()
    import os as _os
    _os._exit(0)   # 强制退出：绕过 onnxruntime 等库遗留的非 daemon 线程

import flet as ft

from gui.app_state import AppState, Event
from gui.theme import Palette, with_alpha, make_dark_theme, make_light_theme
from gui.pages.landing_page import LandingPage
from gui.pages.jianpu_preview_page import JianpuPreviewPage
from gui.pages.editor_page import EditorPage
from gui.pages.transposer_page import TransposerPage
from gui.pages.score_preview_page import ScorePreviewPage
from gui.pages.about_page import AboutPage
from gui.components.progress_overlay import ProgressOverlay
from core.config import APP_VERSION


# ─────────────────────────────────────────────────────────────────────────────
# Navigation destinations
# ─────────────────────────────────────────────────────────────────────────────

_NAV_ITEMS = [
    ('landing',       ft.Icons.ARROW_CIRCLE_RIGHT_ROUNDED,  ft.Icons.ARROW_CIRCLE_RIGHT_OUTLINED,  '乐谱识别'),
    ('editor',        ft.Icons.EDIT_NOTE_ROUNDED,           ft.Icons.EDIT_NOTE_OUTLINED,           '简谱预览'),
    ('score_preview', ft.Icons.LIBRARY_MUSIC_ROUNDED,       ft.Icons.LIBRARY_MUSIC_OUTLINED,       '五线谱预览'),
    ('about',         ft.Icons.INFO_ROUNDED,                ft.Icons.INFO_OUTLINE_ROUNDED,         '关于'),
]


def _check_homr_models(state: AppState) -> None:
    """Migrate legacy weights and update state.homr_available.

    Migration: weights bundled by v0.3.2 installers lived under the homr
    submodule's `homr/segmentation/` and `homr/transformer/` subdirectories.
    Move any pre-existing .onnx files there into <app_base_dir>/models/ so
    the v0.3.2 → v0.3.3 upgrade doesn't re-download what's already on disk.

    Verification: after migration, check all 6 expected files exist and pass
    SHA256. Set state.homr_available accordingly.
    """
    import shutil
    from pathlib import Path
    from core.app.backend import models_dir
    _homr_src = Path(__file__).parent / 'omr_engine' / 'homr'
    if str(_homr_src) not in sys.path:
        sys.path.insert(0, str(_homr_src))
    from homr.main import _WEIGHT_FILES, _WEIGHT_HASHES, verify_sha256  # type: ignore[import-not-found]

    target_dir = models_dir()

    # Migration: scan legacy submodule paths for any pre-existing .onnx files.
    legacy_dirs = [
        _homr_src / 'homr' / 'segmentation',
        _homr_src / 'homr' / 'transformer',
    ]
    for legacy in legacy_dirs:
        if not legacy.is_dir():
            continue
        for onnx in legacy.glob('*.onnx'):
            destination = target_dir / onnx.name
            if destination.exists():
                continue  # already migrated or downloaded into the new location
            try:
                shutil.move(str(onnx), str(destination))
            except Exception:
                pass  # best-effort; runtime download path will catch missing files

    # Verify all 6 are present and (if hashes are filled in) valid.
    state.homr_available = all(
        (target_dir / fname).exists()
        and verify_sha256(str(target_dir / fname), _WEIGHT_HASHES.get(fname, ''))
        for fname in _WEIGHT_FILES
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main application
# ─────────────────────────────────────────────────────────────────────────────

async def main(page: ft.Page) -> None:
    # ── Page base configuration ───────────────────────────────────────────────
    page.title       = 'SumisoraOMR'
    _base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    _ico_path = os.path.join(_base_dir, 'assets', 'icon.ico')
    if os.path.isfile(_ico_path):
        page.window.icon = _ico_path
    page.window.min_width        = 900
    page.window.min_height       = 600
    page.window.width            = 1280
    page.window.height           = 820
    page.window.title_bar_hidden = True
    page.padding = 0
    page.spacing = 0

    # ── Font configuration ────────────────────────────────────────────────────
    # 使用系统字体名，直接由 Flutter DirectWrite 解析，无需注册文件
    import os as _os
    if _os.path.isfile(r'C:\Windows\Fonts\msyh.ttc'):
        _font_key = 'Microsoft YaHei UI'
    elif _os.path.isfile('/System/Library/Fonts/PingFang.ttc'):
        _font_key = 'PingFang SC'
    else:
        _font_key = 'Noto Sans CJK SC'

    page.theme_mode  = ft.ThemeMode.LIGHT
    page.theme       = make_light_theme(font_family=_font_key)
    page.dark_theme  = make_dark_theme(font_family=_font_key)

    # ── Global state ──────────────────────────────────────────────────────────
    state = AppState()
    _check_homr_models(state)

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

    # ── Progress overlay (shared) ─────────────────────────────────────────────
    overlay = ProgressOverlay(state)

    # ── Pages ─────────────────────────────────────────────────────────────────
    landing_page         = LandingPage(state, overlay)
    jianpu_preview_page  = JianpuPreviewPage(state)
    editor_page          = EditorPage(state)
    score_preview_page   = ScorePreviewPage(state)
    transposer_page      = TransposerPage(state)
    about_page           = AboutPage()

    # ── Content area (ft.Stack with overlay support) ──────────────────────────
    # 前 4 个容器对应导航栏 4 项；
    # 第 5 个（index 4）是 jianpu_edit 子页；第 6 个（index 5）是 transposer 子页
    content_stack = ft.Stack(
        [
            ft.Container(content=landing_page,        expand=True, visible=True),   # 0: landing
            ft.Container(content=jianpu_preview_page, expand=True, visible=False),  # 1: editor
            ft.Container(content=score_preview_page,  expand=True, visible=False),  # 2: score_preview
            ft.Container(content=about_page,          expand=True, visible=False),  # 3: about
            ft.Container(content=editor_page,         expand=True, visible=False),  # 4: jianpu_edit (sub)
            ft.Container(content=transposer_page,     expand=True, visible=False),  # 5: transposer (sub)
            overlay,
        ],
        expand=True,
    )
    _content_containers: list[ft.Container] = content_stack.controls[:6]  # type: ignore[index]

    _NAV_NAMES = [item[0] for item in _NAV_ITEMS]  # ['landing', 'editor', 'score_preview', 'about']

    def _show_page(name: str) -> None:
        for i, container in enumerate(_content_containers):
            if i < len(_NAV_NAMES):
                container.visible = (_NAV_NAMES[i] == name)
            elif i == 4:
                container.visible = (name == 'jianpu_edit')
            else:  # i == 5
                container.visible = (name == 'transposer')
        state.current_page = name
        if name == 'editor':
            jianpu_preview_page.reload()
        if name == 'score_preview':
            score_preview_page.reload()
        try:
            content_stack.update()
        except Exception:
            pass

    def _on_jianpu_edit_requested(**_) -> None:
        _show_page('jianpu_edit')

    def _on_jianpu_preview_back(**_) -> None:
        _show_page('editor')

    def _on_score_transposer_requested(path=None, **_) -> None:
        if path is not None:
            transposer_page.load_mxl(path)
        _show_page('transposer')

    def _on_score_transposer_back(**_) -> None:
        _show_page('score_preview')

    state.on(Event.JIANPU_EDIT_REQUESTED,      _on_jianpu_edit_requested)
    state.on(Event.JIANPU_PREVIEW_BACK,        _on_jianpu_preview_back)
    state.on(Event.SCORE_TRANSPOSER_REQUESTED, _on_score_transposer_requested)
    state.on(Event.SCORE_TRANSPOSER_BACK,      _on_score_transposer_back)

    # ── NavigationRail (left sidebar) ────────────────────────────────────────

    def _on_nav_change(e) -> None:
        name = _NAV_ITEMS[e.control.selected_index][0]
        _show_page(name)
        state.emit(Event.PAGE_CHANGED, page=name)
        try:
            nav_rail.update()
        except Exception:
            pass

    _nav_label_toggle_btn = ft.IconButton(
        icon=ft.Icons.CHEVRON_LEFT_ROUNDED,
        icon_size=18,
        icon_color=ft.Colors.ON_SURFACE_VARIANT,
        tooltip='隐藏标签',
    )

    nav_rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        bgcolor=ft.Colors.SURFACE,
        indicator_color=with_alpha(Palette.PRIMARY, '44'),
        on_change=_on_nav_change,
        destinations=[
            ft.NavigationRailDestination(
                icon=icon_out,
                selected_icon=icon_sel,
                label=label,
            )
            for _, icon_sel, icon_out, label in _NAV_ITEMS
        ],
        min_width=80,
        min_extended_width=150,
        expand=True,
    )

    _nav_labels_shown = [True]

    def _toggle_nav_labels(_=None) -> None:
        _nav_labels_shown[0] = not _nav_labels_shown[0]
        if _nav_labels_shown[0]:
            nav_rail.label_type = ft.NavigationRailLabelType.ALL
            _nav_label_toggle_btn.icon = ft.Icons.CHEVRON_LEFT_ROUNDED
            _nav_label_toggle_btn.tooltip = '隐藏标签'
        else:
            nav_rail.label_type = ft.NavigationRailLabelType.SELECTED
            _nav_label_toggle_btn.icon = ft.Icons.CHEVRON_RIGHT_ROUNDED
            _nav_label_toggle_btn.tooltip = '显示标签'
        try:
            nav_rail.update()
            _nav_label_toggle_btn.update()
        except Exception:
            pass

    _nav_label_toggle_btn.on_click = _toggle_nav_labels

    # ── Theme toggle (top-right) ──────────────────────────────────────────────
    _theme_icon = ft.IconButton(
        icon=ft.Icons.LIGHT_MODE_ROUNDED,
        icon_color=ft.Colors.ON_SURFACE_VARIANT,
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

    # ── Custom title bar (borderless) ────────────────────────────────────────
    def _do_minimize():
        page.window.minimized = True
        page.window.update()

    def _do_maximize_toggle():
        page.window.maximized = not page.window.maximized
        page.window.update()

    _max_btn = ft.IconButton(
        icon=ft.Icons.CROP_SQUARE,
        icon_size=14,
        icon_color=ft.Colors.ON_SURFACE_VARIANT,
        tooltip='最大化 / 还原',
        width=32,
        height=32,
        style=ft.ButtonStyle(
            padding=ft.Padding.all(0),
            shape=ft.RoundedRectangleBorder(radius=4),
            overlay_color={
                ft.ControlState.HOVERED: ft.Colors.SURFACE_CONTAINER_HIGH,
                ft.ControlState.PRESSED: ft.Colors.OUTLINE_VARIANT,
            },
        ),
        on_click=lambda _: _do_maximize_toggle(),
    )

    def _on_window_event(e):
        if e.type == ft.WindowEventType.MAXIMIZE:
            _max_btn.icon = ft.Icons.FILTER_NONE
            try:
                _max_btn.update()
            except Exception:
                pass
        elif e.type in (ft.WindowEventType.UNMAXIMIZE, ft.WindowEventType.RESTORE):
            _max_btn.icon = ft.Icons.CROP_SQUARE
            try:
                _max_btn.update()
            except Exception:
                pass

    page.window.on_event = _on_window_event

    _wc_btn_style = ft.ButtonStyle(
        padding=ft.Padding.all(0),
        shape=ft.RoundedRectangleBorder(radius=4),
        overlay_color={
            ft.ControlState.HOVERED: ft.Colors.SURFACE_CONTAINER_HIGH,
            ft.ControlState.PRESSED: ft.Colors.OUTLINE_VARIANT,
        },
    )

    _titlebar = ft.Container(
        content=ft.Row(
            controls=[
                ft.WindowDragArea(
                    content=ft.Row(
                        controls=[
                            ft.Container(width=8),
                            ft.Image(src='Sumisora.png', width=18, height=18),
                            ft.Container(width=6),
                            ft.Text(
                                f'SumisoraOMR  v{APP_VERSION}',
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color=ft.Colors.ON_SURFACE,
                            ),
                        ],
                        spacing=0,
                    ),
                    expand=True,
                    maximizable=True,
                ),
                _theme_icon,
                ft.Container(width=2),
                ft.IconButton(
                    icon=ft.Icons.REMOVE,
                    icon_size=14,
                    icon_color=ft.Colors.ON_SURFACE_VARIANT,
                    tooltip='最小化',
                    width=32,
                    height=32,
                    style=_wc_btn_style,
                    on_click=lambda _: _do_minimize(),
                ),
                _max_btn,
                ft.IconButton(
                    icon=ft.Icons.CLOSE,
                    icon_size=14,
                    icon_color=ft.Colors.ON_SURFACE_VARIANT,
                    tooltip='关闭',
                    width=32,
                    height=32,
                    style=ft.ButtonStyle(
                        padding=ft.Padding.all(0),
                        shape=ft.RoundedRectangleBorder(radius=4),
                        overlay_color={
                            ft.ControlState.HOVERED: '#55F44336',
                            ft.ControlState.PRESSED: '#AAF44336',
                        },
                    ),
                    on_click=lambda _: page.run_task(page.window.close),
                ),
                ft.Container(width=4),
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        height=40,
        bgcolor=ft.Colors.SURFACE,
        border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
    )

    # ── Notification helpers (SnackBar) ──────────────────────────────────────
    def _show_snack(msg: str, color: str = Palette.INFO) -> None:
        page.show_dialog(ft.SnackBar(  # type: ignore[call-arg]
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

    # ── Page layout ───────────────────────────────────────────────────────────
    left_rail_container = ft.Container(
        content=ft.Column(
            [
                nav_rail,
                ft.Container(
                    content=_nav_label_toggle_btn,
                    alignment=ft.Alignment(0, 0),
                    height=40,
                    bgcolor=ft.Colors.SURFACE,
                    border=ft.Border.only(top=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
                ),
            ],
            spacing=0,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        width=80,
        bgcolor=ft.Colors.SURFACE,
        border=ft.Border.only(right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
    )

    page.add(
        ft.Column(
            controls=[
                _titlebar,
                ft.Row(
                    [
                        left_rail_container,
                        ft.Container(content=content_stack, expand=True),
                    ],
                    spacing=0,
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            ],
            spacing=0,
            expand=True,
        )
    )

    # ── Window close: terminate active Worker subprocess ─────────────────────
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

    # ── Initialisation complete ───────────────────────────────────────────────
    _show_page('landing')


# ─────────────────────────────────────────────────────────────────────────────
# PE resource helpers: patch icon + VERSIONINFO in the child Flutter exe
# ─────────────────────────────────────────────────────────────────────────────

def _build_versioninfo_bytes(
    major: int, minor: int, patch_: int, build: int,
    file_desc: str, product_name: str, company: str, copyright_str: str,
) -> bytes:
    """Build a binary RT_VERSION resource from scratch (no external deps)."""
    import struct as _s

    def _enc(s: str) -> bytes:
        return s.encode('utf-16-le') + b'\x00\x00'

    def _kpad(k: bytes) -> bytes:
        r = (6 + len(k)) % 4
        return b'\x00' * ((4 - r) % 4)

    def _str_entry(key: str, value: str) -> bytes:
        k, v = _enc(key), _enc(value)
        body = k + _kpad(k) + v
        n = 6 + len(body)
        tail = (4 - n % 4) % 4
        return _s.pack('<HHH', n + tail, len(v) // 2, 1) + body + b'\x00' * tail

    def _node(key: str, vbytes: bytes, wtype: int, children: bytes) -> bytes:
        k = _enc(key)
        body = k + _kpad(k) + vbytes + children
        n = 6 + len(body)
        tail = (4 - n % 4) % 4
        wval = len(vbytes) if wtype == 0 else len(vbytes) // 2
        return _s.pack('<HHH', n + tail, wval, wtype) + body + b'\x00' * tail

    ffi = _s.pack('<IIIIIIIIIIIII',
        0xFEEF04BD, 0x00010000,
        (major << 16) | minor,  (patch_ << 16) | build,
        (major << 16) | minor,  (patch_ << 16) | build,
        0x3F, 0, 0x00040004, 1, 0, 0, 0,
    )
    vs = f'{major}.{minor}.{patch_}.{build}'
    str_data = b''.join([
        _str_entry('CompanyName',      company),
        _str_entry('FileDescription',  file_desc),
        _str_entry('FileVersion',      vs),
        _str_entry('InternalName',     file_desc),
        _str_entry('LegalCopyright',   copyright_str),
        _str_entry('OriginalFilename', file_desc + '.exe'),
        _str_entry('ProductName',      product_name),
        _str_entry('ProductVersion',   vs),
    ])
    str_fi = _node('StringFileInfo', b'', 1, _node('040904b0', b'', 1, str_data))
    import struct as _s2
    var_fi = _node('VarFileInfo', b'', 1,
                   _node('Translation', _s2.pack('<HH', 0x0409, 0x04B0), 0, b''))
    root_k   = _enc('VS_VERSION_INFO')
    root_pad = _kpad(root_k)
    body     = root_k + root_pad + ffi + str_fi + var_fi
    n        = 6 + len(body)
    tail     = (4 - n % 4) % 4
    return _s.pack('<HHH', n + tail, len(ffi), 0) + body + b'\x00' * tail


def _patch_exe_resources(
    exe_path: str, ico_path: str,
    file_desc: str, product_name: str, company: str, copyright_str: str,
    major: int, minor: int, patch_: int, build: int,
) -> bool:
    """Replace icon (RT_ICON/RT_GROUP_ICON) and VERSIONINFO in a PE exe."""
    import struct as _s, ctypes as _ct
    from ctypes import wintypes as _wt

    if not os.path.isfile(ico_path):
        return False

    with open(ico_path, 'rb') as _f:
        ico = _f.read()
    _, _, cnt = _s.unpack_from('<HHH', ico, 0)
    icons = []
    for _i in range(cnt):
        w, h, cc, _, pl, bpp, sz, off = _s.unpack_from('<BBBBHHII', ico, 6 + _i * 16)
        icons.append((w or 256, h or 256, cc, pl, bpp, ico[off:off + sz]))

    grp = _s.pack('<HHH', 0, 1, cnt)
    for _i, (w, h, cc, pl, bpp, data) in enumerate(icons):
        grp += _s.pack('<BBBBHHiH',
                       0 if w == 256 else w, 0 if h == 256 else h,
                       cc, 0, pl, bpp, len(data), _i + 1)

    ver = _build_versioninfo_bytes(major, minor, patch_, build,
                                    file_desc, product_name, company, copyright_str)

    k32 = _ct.windll.kernel32
    k32.BeginUpdateResourceW.restype  = _wt.HANDLE
    k32.BeginUpdateResourceW.argtypes = [_wt.LPCWSTR, _wt.BOOL]
    # lpType / lpName accept either a string pointer or MAKEINTRESOURCE
    # (an integer ID stuffed into the low word of a "fake" pointer).
    # Declare them as c_void_p so we can pass either; declaring LPCWSTR makes
    # Python 3.14 ctypes reject the integer-pointer cast with a TypeError.
    k32.UpdateResourceW.restype       = _wt.BOOL
    k32.UpdateResourceW.argtypes      = [_wt.HANDLE, _ct.c_void_p, _ct.c_void_p,
                                          _wt.WORD, _ct.c_void_p, _wt.DWORD]
    k32.EndUpdateResourceW.restype    = _wt.BOOL
    k32.EndUpdateResourceW.argtypes   = [_wt.HANDLE, _wt.BOOL]

    def _mir(n: int) -> _ct.c_void_p:
        # MAKEINTRESOURCE(n): integer ID as a pointer-shaped value.
        return _ct.c_void_p(n)

    h = k32.BeginUpdateResourceW(exe_path, False)
    if not h:
        return False
    try:
        for _i, (_, _, _, _, _, data) in enumerate(icons):
            _buf = (_ct.c_char * len(data)).from_buffer_copy(data)
            k32.UpdateResourceW(h, _mir(3), _mir(_i + 1), 0x0409, _buf, len(data))
        _gb = (_ct.c_char * len(grp)).from_buffer_copy(grp)
        k32.UpdateResourceW(h, _mir(14), _mir(1), 0x0409, _gb, len(grp))
        _vb = (_ct.c_char * len(ver)).from_buffer_copy(ver)
        k32.UpdateResourceW(h, _mir(16), _mir(1), 0x0409, _vb, len(ver))
        return bool(k32.EndUpdateResourceW(h, False))
    except Exception:
        k32.EndUpdateResourceW(h, True)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# [Dev & packaged] Make Task Manager show "SumisoraOMR" instead of "flet"
# ─────────────────────────────────────────────────────────────────────────────
# flet_desktop 通过 open_flet_view_async 启动 Flutter 窗口进程。
# 此函数确保使用重命名后的 SumisoraOMR.exe 作为 Flutter 运行时，
# 并 monkey-patch open_flet_view_async 直接指定该 exe，
# 不依赖 os.getcwd()/build/windows/ 发现机制。
#
# 开发模式：从 ~/.flet/client/flet-desktop-full-X.Y.Z/flet/ 复制并重命名
# 打包版  ：从 _MEIPASS/flet_desktop/app/flet-windows.zip 解压并重命名
#           目标目录均为 ~/.flet/SumisoraOMR/<version>/，首次运行后缓存，不重复操作。
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

        _EXE    = 'SumisoraOMR.exe'
        _ver    = _fdv.version
        _dir    = _Path.home() / '.flet' / 'SumisoraOMR' / _ver
        _exe    = _dir / _EXE
        _stamp  = _dir / '.stamp'
        _rstamp = _dir / '.rstamp'   # 资源补丁戳（图标 + 版本信息）

        def _do_patch():
            _base = _Path(getattr(sys, '_MEIPASS',
                          os.path.dirname(os.path.abspath(__file__))))
            _ico  = _base / 'assets' / 'icon.ico'
            ok = _patch_exe_resources(
                str(_exe), str(_ico),
                'SumisoraOMR', 'SumisoraOMR',
                'Tsukamotoshio', 'Copyright (C) 2026 Tsukamotoshio',
                0, 3, 3, 0,
            )
            # Only mark "patched" on success — otherwise leave the stamp absent
            # so the next launch can retry (e.g. after a code fix).
            if ok:
                _rstamp.touch()

        if not (_stamp.exists() and _exe.exists()):
            _dir.mkdir(parents=True, exist_ok=True)
            # 清理旧文件（flet 版本升级时重新解压）
            for _f in list(_dir.iterdir()):
                if _f.name not in ('.stamp', '.rstamp'):
                    _shutil.rmtree(_f) if _f.is_dir() else _f.unlink(missing_ok=True)

            if getattr(sys, 'frozen', False):
                # Packaged build: extract bundled flet-windows.zip
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
                # Dev mode: copy from user flet cache
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

            # flet.exe → SumisoraOMR.exe（两种来源均适用）
            _flet_exe = _dir / 'flet.exe'
            if _flet_exe.exists():
                _flet_exe.rename(_exe)
            _do_patch()   # 替换图标与版本信息
            _stamp.touch()
        elif not _rstamp.exists() and _exe.exists():
            # 已有安装但尚未打补丁（首次运行新版本代码）
            _do_patch()

        if not _exe.exists():
            return  # 设置失败，回退到默认 flet.exe

        # ── monkey-patch open_flet_view_async ────────────────────────────────
        # 直接指定 SumisoraOMR.exe，跳过 flet_desktop 内部的路径发现逻辑，
        # 避免对 os.getcwd() / build/windows/ 的依赖
        _exe_str = str(_exe)
        _orig_open = _fd.open_flet_view_async

        async def _patched_open(page_url, assets_dir, hidden):
            import asyncio as _aio
            import tempfile as _tmp
            import os as _o
            _pid  = str(_Path(_tmp.gettempdir()) / _rstr(20))
            _env = {**_o.environ}
            _env['FLET_APP_USER_MODEL_ID'] = 'Tsukamotoshio.SumisoraOMR'
            _env['FLET_HIDE_WINDOW_ON_START'] = 'true'
            if _exe.exists():
                _args = [_exe_str, page_url, _pid]
                if assets_dir:
                    _args.append(assets_dir)
                return (await _aio.create_subprocess_exec(_args[0], *_args[1:], env=_env), _pid)
            return await _orig_open(page_url, assets_dir, True)

        _fd.open_flet_view_async = _patched_open

    except Exception:
        pass  # 设置失败不影响应用启动，回退到默认 flet.exe


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if sys.platform == 'win32' and '--worker' not in sys.argv:
        import ctypes as _ctypes
        _ctypes.windll.kernel32.CreateMutexW(None, False, 'SumisoraOMR_RunningMutex')
        if _ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            _ctypes.windll.user32.MessageBoxW(
                0,
                'Sumisora OMR 已在运行中。\n\n请查看任务栏。',
                'Sumisora OMR',
                0x30,  # MB_ICONWARNING | MB_OK
            )
            sys.exit(0)

        # Explicit AppUserModelID — Windows uses this for taskbar grouping and
        # for the right-click menu's app-name entry. Without it, the Flutter
        # window inherits Flet's default identity and shows "Flet description".
        # Set on the Python parent; flet_desktop's subprocess inherits via env
        # (we copy os.environ into the subprocess env in _patched_open).
        try:
            _ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                'Tsukamotoshio.SumisoraOMR'
            )
            os.environ['FLET_APP_USER_MODEL_ID'] = 'Tsukamotoshio.SumisoraOMR'
            os.environ['FLET_HIDE_WINDOW_ON_START'] = 'true'
        except Exception:
            pass

    _setup_flet_view_name()
    _assets_dir = os.path.join(
        getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
        'assets',
    )
    ft.run(
        main,
        assets_dir=_assets_dir,
        view=ft.AppView.FLET_APP_HIDDEN,
    )
