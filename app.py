# app.py — Flet GUI 主程序入口
# OMR 转换工具 — 现代扁平化 GUI（深色模式优先）
#
# 启动方式：
#   python app.py
#   或（开发模式热重载）：flet run app.py
#
# 依赖：
#   pip install flet pymupdf music21 pillow
#   （其余核心依赖见 requirements.txt）

import sys
import os

# ─── SSL 证书：解决打包后部分环境证书验证失败问题 ─────────────────────────────
# 在所有网络请求（flet / urllib / requests / httpx）之前设置，
# 强制使用随包附带的 certifi 证书，避免旧版 Windows 根证书缺失或 SSL 中间人干扰。
#   SSL_CERT_FILE    — Python ssl 模块 / urllib / httpx
#   REQUESTS_CA_BUNDLE — requests 库
#   CURL_CA_BUNDLE   — libcurl 系库
if getattr(sys, 'frozen', False):
    _internal_cert = os.path.join(sys._MEIPASS, 'certifi', 'cacert.pem')
    if os.path.exists(_internal_cert):
        os.environ['SSL_CERT_FILE'] = _internal_cert
        os.environ['REQUESTS_CA_BUNDLE'] = _internal_cert
        os.environ['CURL_CA_BUNDLE'] = _internal_cert
    # console=False 时 sys.stdout/stderr 为 None，所有 print() 会崩溃
    # 替换为 null 流，将输出静默丢弃（日志仍写入 logs/ 文件）
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w', encoding='utf-8', errors='replace')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w', encoding='utf-8', errors='replace')

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
        '  pip install flet pymupdf music21 pillow\n',
        file=sys.stderr,
    )
    sys.exit(1)

_bootstrap_venv()
# ─────────────────────────────────────────────────────────────────────────────

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

def main(page: ft.Page) -> None:
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
        if name == 'editor' and hasattr(editor_page, 'reset_view'):
            editor_page.reset_view()
        if name == 'transposer' and hasattr(transposer_page, 'reset_view'):
            transposer_page.reset_view()
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
        title=ft.Text('OMR 乐谱转换工具  v0.2.0-preview', size=15, weight=ft.FontWeight.W_600),
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
        _show_snack(f'错误: {message}', Palette.ERROR)

    def _on_done(message: str = '完成', **_kw) -> None:
        _show_snack(message, Palette.SUCCESS)

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

    # ── 初始化完成 ────────────────────────────────────────────────────────────
    _show_page('landing')


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    ft.run(main)
