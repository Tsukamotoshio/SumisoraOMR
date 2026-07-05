# gui/pages/about_page.py — About page
# Displays author info, project URL and AGPL-3.0 license text.

from __future__ import annotations

import threading
import webbrowser

import flet as ft

from ..theme import Palette, with_alpha, FONT_EMPHASIS
from ..strings import t
from core.config import APP_VERSION


class AboutPage(ft.Column):
    """About page: author, project URL, license."""

    _GITHUB_URL = 'https://github.com/Tsukamotoshio/SumisoraOMR'

    def __init__(self):
        self._opening = False
        super().__init__(spacing=0, expand=True,
                         horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                         scroll=ft.ScrollMode.AUTO)
        self._build_ui()

    def retranslate(self) -> None:
        """Re-apply UI text in the active language (called on Event.LANGUAGE_CHANGED).

        AboutPage is purely static (no state, no event subscriptions), so a full
        widget-tree rebuild is safe and simplest here.
        """
        self._build_ui()
        try:
            self.update()
        except Exception:
            pass

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

    def _copy_diagnostics(self, _e=None) -> None:
        """收集环境诊断信息并复制到系统剪贴板（后台线程；收集可能触发 onnxruntime 导入）。"""
        page = self.page

        def _work() -> None:
            try:
                from core.app.diagnostics import collect_diagnostics, copy_to_clipboard
                report = collect_diagnostics()
                ok = copy_to_clipboard(report)
                msg = t('about.diagnostics_copied') if ok else t('about.diagnostics_failed', exc='clipboard')
            except Exception as exc:
                msg = t('about.diagnostics_failed', exc=exc)
            if page is not None:
                page.run_task(self._snack_async, msg)

        threading.Thread(target=_work, daemon=True).start()

    async def _snack_async(self, msg: str) -> None:
        # 本 Flet 版本 page.open 不存在（会静默抛错），SnackBar 须走 show_dialog。
        try:
            self.page.show_dialog(ft.SnackBar(content=ft.Text(msg, size=14), duration=3000))
        except Exception:
            pass

    def _build_ui(self) -> None:

        def _card(content: ft.Control) -> ft.Container:
            # 暗色主题下 SURFACE 与页面背景同色，仅靠阴影无法区分卡片边界，
            # 因此叠加 1px 描边 + 略高一级的容器色。
            return ft.Container(
                content=content,
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=ft.BorderRadius.all(12),
                padding=ft.Padding.all(24),
                width=640,
                shadow=ft.BoxShadow(
                    blur_radius=16,
                    color='#33000000',
                    offset=ft.Offset(0, 4),
                ),
            )

        # Logo & title
        logo_card = _card(
            ft.Column(
                [
                    ft.Image(src='Sumisora.png', width=64, height=64,
                             border_radius=ft.BorderRadius.all(12)),
                    ft.Text('SumisoraOMR', size=24,
                            font_family=FONT_EMPHASIS, color=ft.Colors.ON_SURFACE),
                    ft.Text(t('about.subtitle'), size=15, color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Container(
                        content=ft.Text(f'v{APP_VERSION}', size=13, color=Palette.PRIMARY,
                                        font_family=FONT_EMPHASIS),
                        bgcolor=with_alpha(Palette.PRIMARY, '22'),
                        border_radius=ft.BorderRadius.all(6),
                        padding=ft.Padding.symmetric(horizontal=10, vertical=3),
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            )
        )

        # Author info
        author_card = _card(
            ft.Column(
                [
                    ft.Text(t('about.author_label'), size=14, font_family=FONT_EMPHASIS,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.PERSON_ROUNDED, size=18, color=Palette.PRIMARY),
                            ft.Text('Tsukamotoshio', size=16, color=ft.Colors.ON_SURFACE),
                        ],
                        spacing=8,
                    ),
                    ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                    ft.Text(t('about.homepage_label'), size=14, font_family=FONT_EMPHASIS,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.LINK_ROUNDED, size=18, color=Palette.PRIMARY),
                            ft.GestureDetector(
                                content=ft.Text(
                                    self._GITHUB_URL,
                                    size=14,
                                    color=Palette.PRIMARY,
                                    font_family=FONT_EMPHASIS,
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

        # Diagnostics (P3-2): one-click copy of environment info for bug reports
        diagnostics_card = _card(
            ft.Column(
                [
                    ft.Text(t('about.diagnostics_label'), size=14, font_family=FONT_EMPHASIS,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Text(t('about.diagnostics_hint'), size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.OutlinedButton(
                        content=ft.Row(
                            [ft.Icon(ft.Icons.CONTENT_COPY_ROUNDED, size=16),
                             ft.Text(t('about.copy_diagnostics_button'))],
                            tight=True, spacing=6,
                        ),
                        on_click=self._copy_diagnostics,
                    ),
                ],
                spacing=10,
            )
        )

        # License
        license_card = _card(
            ft.Column(
                [
                    ft.Text(t('about.license_label'), size=14, font_family=FONT_EMPHASIS,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Container(
                        content=ft.Text(
                            t('about.license_text'),
                            size=12,
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
            diagnostics_card,
            ft.Container(height=16),
            license_card,
            ft.Container(height=32),
        ]
