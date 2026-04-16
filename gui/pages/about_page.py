# gui/pages/about_page.py — 关于页面
# 展示作者信息、项目主页链接与 MIT 许可证文本。

from __future__ import annotations

import threading
import webbrowser

import flet as ft

from ..theme import Palette


_MIT_LICENSE = """\
MIT License

Copyright (c) 2026 Tsukamotoshio

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""


class AboutPage(ft.Column):
    """关于页面：作者、项目地址、许可证。"""

    _GITHUB_URL = 'https://github.com/Tsukamotoshio/OMR-to-Jianpu-Conversion-Tool'
    VERSION = 'v0.2.2-homr-experimental'

    def __init__(self):
        self._url_launcher = ft.UrlLauncher()
        self._opening = False
        super().__init__(spacing=0, expand=True,
                         horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                         scroll=ft.ScrollMode.AUTO)
        self._build_ui()

    def _open_url(self, _e=None) -> None:
        if self._opening:
            return
        self._opening = True
        try:
            try:
                threading.Thread(
                    target=webbrowser.open_new_tab,
                    args=(self._GITHUB_URL,),
                    daemon=True,
                ).start()
            except Exception:
                pass
        finally:
            self._opening = False

    def _build_ui(self) -> None:

        def _card(content: ft.Control) -> ft.Container:
            return ft.Container(
                content=content,
                bgcolor=Palette.BG_SURFACE,
                border_radius=ft.BorderRadius.all(12),
                padding=ft.Padding.all(24),
                width=640,
                shadow=ft.BoxShadow(
                    blur_radius=16,
                    color='#00000033',
                    offset=ft.Offset(0, 4),
                ),
            )

        # ── Logo & 标题 ──────────────────────────────────────────────────────
        logo_card = _card(
            ft.Column(
                [
                    ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, size=56, color=Palette.PRIMARY),
                    ft.Text('OMR 乐谱转换工具', size=22,
                            weight=ft.FontWeight.BOLD, color=Palette.TEXT_PRIMARY),
                    ft.Text('将五线谱 PDF 智能转换为简谱', size=14, color=Palette.TEXT_SECONDARY),
                    ft.Container(
                        content=ft.Text(self.VERSION, size=12, color=Palette.PRIMARY,
                                        weight=ft.FontWeight.W_500),
                        bgcolor=Palette.PRIMARY + '22',
                        border_radius=ft.BorderRadius.all(6),
                        padding=ft.Padding.symmetric(horizontal=10, vertical=3),
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            )
        )

        # ── 作者信息 ─────────────────────────────────────────────────────────
        author_card = _card(
            ft.Column(
                [
                    ft.Text('作者', size=13, weight=ft.FontWeight.W_600,
                            color=Palette.TEXT_SECONDARY),
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.PERSON_ROUNDED, size=18, color=Palette.PRIMARY),
                            ft.Text('Tsukamotoshio', size=15, color=Palette.TEXT_PRIMARY),
                        ],
                        spacing=8,
                    ),
                    ft.Divider(height=1, color=Palette.DIVIDER_DARK),
                    ft.Text('项目主页', size=13, weight=ft.FontWeight.W_600,
                            color=Palette.TEXT_SECONDARY),
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.LINK_ROUNDED, size=18, color=Palette.PRIMARY),
                            ft.GestureDetector(
                                content=ft.Text(
                                    self._GITHUB_URL,
                                    size=13,
                                    color=Palette.PRIMARY,
                                    weight=ft.FontWeight.W_500,
                                ),
                                on_tap=self._open_url,
                                mouse_cursor=ft.MouseCursor.CLICK,
                            ),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=10,
            )
        )

        # ── 许可证 ───────────────────────────────────────────────────────────
        license_card = _card(
            ft.Column(
                [
                    ft.Text('开源许可证 (MIT)', size=13, weight=ft.FontWeight.W_600,
                            color=Palette.TEXT_SECONDARY),
                    ft.Container(
                        content=ft.Text(
                            _MIT_LICENSE,
                            size=11,
                            color=Palette.TEXT_SECONDARY,
                            font_family='Consolas',
                            selectable=True,
                        ),
                        bgcolor=Palette.BG_DARK,
                        border_radius=ft.BorderRadius.all(6),
                        padding=ft.Padding.all(12),
                    ),
                ],
                spacing=10,
            )
        )

        self.controls = [
            ft.Container(height=32),
            logo_card,
            ft.Container(height=16),
            author_card,
            ft.Container(height=16),
            license_card,
            ft.Container(height=32),
            self._url_launcher,
        ]
