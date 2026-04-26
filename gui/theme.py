# gui/theme.py — 扁平化深色/浅色主题常量
# 不依赖任何实例化的 Flet 对象，仅导出颜色字符串与 ft.Theme 工厂函数。

from __future__ import annotations
import flet as ft

# ─────────────────────────────────────────────────────────────────────────────
# 调色板 — 具名十六进制值，仅在 ColorScheme 定义和语义色中直接引用。
# UI 组件应优先使用 ft.Colors.* M3 语义 token，而非直接引用此类。
# ─────────────────────────────────────────────────────────────────────────────

def with_alpha(color: str, alpha: str) -> str:
    """Return an ARGB hex string for Flet/Flutter (#AARRGGBB)."""
    if color.startswith('#') and len(color) == 7 and len(alpha) == 2:
        return f'#{alpha}{color[1:]}'
    return color


class Palette:
    # 主色调（靛蓝-紫罗兰）
    PRIMARY        = '#7C4DFF'
    PRIMARY_DIM    = '#512DA8'
    PRIMARY_LIGHT  = '#B39DDB'

    # 语义色（不随主题变化）
    SUCCESS        = '#4CAF50'
    WARNING        = '#FF9800'
    ERROR          = '#F44336'
    INFO           = '#2196F3'

    # 控件边框（中调紫，在深浅两个主题的背景下均清晰可见）
    BORDER_PURPLE  = '#8870C4'

    # 行高亮（简谱）
    HIGHLIGHT      = with_alpha('#7C4DFF', '44')
    MAGNIFIER_BG   = with_alpha('#000000', 'CC')


# ─────────────────────────────────────────────────────────────────────────────
# Flet Theme 工厂
# ─────────────────────────────────────────────────────────────────────────────

def make_dark_theme(font_family: str = 'YaHei') -> ft.Theme:
    return ft.Theme(
        color_scheme_seed=Palette.PRIMARY,
        font_family=font_family,
        color_scheme=ft.ColorScheme(
            primary=Palette.PRIMARY,
            on_primary='#FFFFFF',
            secondary=Palette.PRIMARY_LIGHT,
            on_secondary='#FFFFFF',
            error=Palette.ERROR,
            on_error='#FFFFFF',
            surface='#1E1E2E',
            surface_dim='#121212',
            surface_bright='#2A2A3E',
            surface_container_lowest='#121212',
            surface_container_low='#1E1E2E',
            surface_container='#252538',
            surface_container_high='#2A2A3E',
            surface_container_highest='#323248',
            on_surface='#E8E8F0',
            on_surface_variant='#9E9EB8',
            outline='#555568',
            outline_variant='#2E2E48',
            inverse_surface='#E8E8F0',
            on_inverse_surface='#1E1E2E',
        ),
    )


def make_light_theme(font_family: str = 'YaHei') -> ft.Theme:
    return ft.Theme(
        color_scheme_seed=Palette.PRIMARY,
        font_family=font_family,
        color_scheme=ft.ColorScheme(
            primary=Palette.PRIMARY,
            on_primary='#FFFFFF',
            secondary=Palette.PRIMARY_DIM,
            on_secondary='#FFFFFF',
            error=Palette.ERROR,
            on_error='#FFFFFF',
            surface='#F8F7FF',
            surface_dim='#ECEAF5',
            surface_bright='#FDFCFF',
            surface_container_lowest='#FAF8FF',
            surface_container_low='#F3F0FF',
            surface_container='#EDE9FF',
            surface_container_high='#E6E1F5',
            surface_container_highest='#DDD8EF',
            on_surface='#1A1A2E',
            on_surface_variant='#4A4A6A',
            outline='#8080A0',
            outline_variant='#CCC8DF',
            inverse_surface='#1A1A2E',
            on_inverse_surface='#FFFFFF',
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 常用控件样式辅助
# ─────────────────────────────────────────────────────────────────────────────

def card_style(dark: bool = True) -> dict:
    """返回 ft.Container 的常用卡片样式参数字典。"""
    return dict(
        bgcolor=ft.Colors.SURFACE_CONTAINER,
        border_radius=ft.BorderRadius.all(8),
        padding=ft.Padding.all(12),
    )


def section_title(text: str, dark: bool = True) -> ft.Text:
    return ft.Text(
        text,
        size=11,
        weight=ft.FontWeight.W_600,
        color=ft.Colors.ON_SURFACE_VARIANT,
        style=ft.TextStyle(letter_spacing=1.2),
    )
