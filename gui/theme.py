# gui/theme.py — 扁平化深色/浅色主题常量
# 不依赖任何实例化的 Flet 对象，仅导出颜色字符串与 ft.Theme 工厂函数。

from __future__ import annotations
import flet as ft

# ─────────────────────────────────────────────────────────────────────────────
# 调色板
# ─────────────────────────────────────────────────────────────────────────────

class Palette:
    # 主色调（靛蓝-紫罗兰）
    PRIMARY        = '#7C4DFF'
    PRIMARY_DIM    = '#512DA8'
    PRIMARY_LIGHT  = '#B39DDB'

    # 背景（深色模式）
    BG_DARK        = '#121212'
    BG_SURFACE     = '#1E1E2E'
    BG_CARD        = '#252538'
    BG_INPUT       = '#2A2A3E'

    # 背景（浅色模式）
    BG_LIGHT       = '#F5F5FA'
    BG_SURFACE_L   = '#FFFFFF'
    BG_CARD_L      = '#F0F0F8'
    BG_INPUT_L     = '#EBEBF5'

    # 文字
    TEXT_PRIMARY   = '#E8E8F0'
    TEXT_SECONDARY = '#9E9EB8'
    TEXT_DISABLED  = '#555568'
    TEXT_DARK_PRI  = '#1A1A2E'
    TEXT_DARK_SEC  = '#4A4A6A'

    # 语义色
    SUCCESS        = '#4CAF50'
    WARNING        = '#FF9800'
    ERROR          = '#F44336'
    INFO           = '#2196F3'

    # 分隔线
    DIVIDER_DARK   = '#2E2E48'
    DIVIDER_LIGHT  = '#D0D0E0'

    # 高亮（简谱行选中）
    HIGHLIGHT      = '#7C4DFF44'    # 半透明主色
    MAGNIFIER_BG   = '#000000CC'    # 放大镜背景


# ─────────────────────────────────────────────────────────────────────────────
# Flet Theme 工厂
# ─────────────────────────────────────────────────────────────────────────────

def make_dark_theme(font_family: str = 'YaHei') -> ft.Theme:
    return ft.Theme(
        color_scheme_seed=Palette.PRIMARY,
        font_family=font_family,
        color_scheme=ft.ColorScheme(
            primary=Palette.PRIMARY,
            secondary=Palette.PRIMARY_LIGHT,
            surface=Palette.BG_SURFACE,
            on_primary='#FFFFFF',
            on_secondary='#FFFFFF',
            on_surface=Palette.TEXT_PRIMARY,
            error=Palette.ERROR,
        ),
    )


def make_light_theme(font_family: str = 'YaHei') -> ft.Theme:
    return ft.Theme(
        color_scheme_seed=Palette.PRIMARY,
        font_family=font_family,
        color_scheme=ft.ColorScheme(
            primary=Palette.PRIMARY,
            secondary=Palette.PRIMARY_DIM,
            surface=Palette.BG_SURFACE_L,
            on_primary='#FFFFFF',
            on_secondary='#FFFFFF',
            on_surface=Palette.TEXT_DARK_PRI,
            error=Palette.ERROR,
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 常用控件样式辅助
# ─────────────────────────────────────────────────────────────────────────────

def card_style(dark: bool = True) -> dict:
    """返回 ft.Container 的常用卡片样式参数字典。"""
    bg = Palette.BG_CARD if dark else Palette.BG_CARD_L
    return dict(
        bgcolor=bg,
        border_radius=ft.BorderRadius.all(8),
        padding=ft.Padding.all(12),
    )


def section_title(text: str, dark: bool = True) -> ft.Text:
    color = Palette.TEXT_SECONDARY if dark else Palette.TEXT_DARK_SEC
    return ft.Text(text, size=11, weight=ft.FontWeight.W_600,
                   color=color, style=ft.TextStyle(letter_spacing=1.2))
