# gui/pages/about_page.py — 关于页面
# 展示作者信息、项目主页链接与 AGPL-3.0 许可证文本。

from __future__ import annotations

import threading
import webbrowser

import flet as ft

from ..theme import Palette, with_alpha
from core.config import APP_VERSION


_AGPL_LICENSE = """\
GNU Affero General Public License v3.0

Copyright (c) 2026 Tsukamotoshio

本程序是自由软件：您可以根据自由软件基金会发布的 GNU Affero 通用公共许可证
（第 3 版或更高版本）重新分发或修改它。

本程序的发布是希望它有用，但不提供任何担保；甚至不提供对适销性或特定用途
适用性的隐含担保。详情请参阅 GNU Affero 通用公共许可证。

您应该已收到 GNU Affero 通用公共许可证的副本；如未收到，请访问：
https://www.gnu.org/licenses/agpl-3.0.html

附加条款：若您通过网络向用户提供此程序的修改版本，您必须向所有与之交互的
用户提供获取对应源代码的途径（AGPL-3.0 第 13 条）。

完整许可证文本：https://github.com/Tsukamotoshio/OMR-to-Jianpu-Conversion-Tool/blob/main/LICENSE"""


class AboutPage(ft.Column):
    """关于页面：作者、项目地址、许可证。"""

    _GITHUB_URL = 'https://github.com/Tsukamotoshio/OMR-to-Jianpu-Conversion-Tool'

    def __init__(self):
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
                bgcolor=ft.Colors.SURFACE,
                border_radius=ft.BorderRadius.all(12),
                padding=ft.Padding.all(24),
                width=640,
                shadow=ft.BoxShadow(
                    blur_radius=16,
                    color='#33000000',
                    offset=ft.Offset(0, 4),
                ),
            )

        # ── Logo & 标题 ──────────────────────────────────────────────────────
        logo_card = _card(
            ft.Column(
                [
                    ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, size=56, color=Palette.PRIMARY),
                    ft.Text('OMR 乐谱转换工具', size=22,
                            weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE),
                    ft.Text('将五线谱 PDF 智能转换为简谱', size=14, color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Container(
                        content=ft.Text(f'v{APP_VERSION}', size=12, color=Palette.PRIMARY,
                                        weight=ft.FontWeight.W_500),
                        bgcolor=with_alpha(Palette.PRIMARY, '22'),
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
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.PERSON_ROUNDED, size=18, color=Palette.PRIMARY),
                            ft.Text('Tsukamotoshio', size=15, color=ft.Colors.ON_SURFACE),
                        ],
                        spacing=8,
                    ),
                    ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                    ft.Text('项目主页', size=13, weight=ft.FontWeight.W_600,
                            color=ft.Colors.ON_SURFACE_VARIANT),
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
                    ft.Text('开源许可证 (AGPL-3.0)', size=13, weight=ft.FontWeight.W_600,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Container(
                        content=ft.Text(
                            _AGPL_LICENSE,
                            size=11,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            font_family='Consolas',
                            selectable=True,
                        ),
                        bgcolor=ft.Colors.SURFACE_DIM,
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
        ]
