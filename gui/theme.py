# gui/theme.py — Flat dark/light theme constants
# No instantiated Flet objects; exports only colour strings and ft.Theme factory functions.

from __future__ import annotations
import flet as ft

# ─────────────────────────────────────────────────────────────────────────────
# Colour palette — named hex values, referenced only in ColorScheme and semantic colours.
# UI components should prefer ft.Colors.* M3 semantic tokens over direct references here.
# ─────────────────────────────────────────────────────────────────────────────

def with_alpha(color: str, alpha: str) -> str:
    """Return an ARGB hex string for Flet/Flutter (#AARRGGBB)."""
    if color.startswith('#') and len(color) == 7 and len(alpha) == 2:
        return f'#{alpha}{color[1:]}'
    return color


class Palette:
    # Primary (sapphire-blue) — SumisoraOMR
    PRIMARY        = '#2979FF'   # Material Blue A400 — vivid electric blue
    PRIMARY_DIM    = '#1565C0'   # Material Blue 800  — deep ocean blue
    PRIMARY_LIGHT  = '#90CAF9'   # Material Blue 200  — sky blue

    # Semantic colours (theme-invariant)
    SUCCESS        = '#4CAF50'
    WARNING        = '#FF9800'
    ERROR          = '#F44336'
    INFO           = '#2196F3'

    # Widget border (mid-blue, legible on both dark and light backgrounds)
    BORDER_BLUE    = '#5590CC'

    # Row highlight (jianpu editor)
    HIGHLIGHT      = with_alpha('#2979FF', '44')
    MAGNIFIER_BG   = with_alpha('#000000', 'CC')


# ─────────────────────────────────────────────────────────────────────────────
# Flet Theme factories
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
            surface='#0D1829',
            surface_dim='#080F1A',
            surface_bright='#182338',
            surface_container_lowest='#080F1A',
            surface_container_low='#0D1829',
            surface_container='#152030',
            surface_container_high='#1A2840',
            surface_container_highest='#22334E',
            on_surface='#E0ECFF',
            on_surface_variant='#8AAAC8',
            outline='#3A5575',
            outline_variant='#1A2E48',
            inverse_surface='#E0ECFF',
            on_inverse_surface='#0D1829',
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
            surface='#F5F8FF',
            surface_dim='#E0EAFA',
            surface_bright='#FBFCFF',
            surface_container_lowest='#F5F8FF',
            surface_container_low='#EBF1FF',
            surface_container='#DEEAFF',
            surface_container_high='#D2E2F5',
            surface_container_highest='#C4D8EE',
            on_surface='#0A1929',
            on_surface_variant='#2A4060',
            outline='#507090',
            outline_variant='#B8CEDF',
            inverse_surface='#0A1929',
            on_inverse_surface='#FFFFFF',
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Common widget style helpers
# ─────────────────────────────────────────────────────────────────────────────

def card_style(dark: bool = True) -> dict:
    """Return a dict of common card-style kwargs for ft.Container."""
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
