# core/tui.py — Rich TUI 状态机主界面
# 使用 Rich 库实现类 TUI 的菜单跳转效果（状态机模式）
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

from .config import APP_VERSION, AppConfig
from .utils import get_app_base_dir, log_message, setup_logging

# ──────────────────────────────────────────────────────────────────────────────
EDITOR_WORKSPACE_DIR_NAME = 'editor-workspace'
PAGE_SIZE = 10


def _is_base_dir_writable(base_dir: Path) -> bool:
    """Return True if the application base directory is writable by the current user."""
    test_path = base_dir / '.write_test'
    try:
        test_path.write_bytes(b'')
        test_path.unlink(missing_ok=True)
        return True
    except (PermissionError, OSError):
        return False

# ──────────────────────────────────────────────────────────────────────────────
# 低层输入辅助
# ──────────────────────────────────────────────────────────────────────────────

def _flush_console_input_buffer() -> None:
    """Discard any pending characters in the Windows console input buffer.

    This prevents keystroke "ghosts" from a previous TUI session (or from the
    Enter key that launched the program) from being read by the first
    _read_single_key() call.  No-op on non-Windows or if the handle is invalid.
    """
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        STD_INPUT_HANDLE = -10
        handle = ctypes.windll.kernel32.GetStdHandle(STD_INPUT_HANDLE)
        if handle and handle != ctypes.c_void_p(-1).value:
            ctypes.windll.kernel32.FlushConsoleInputBuffer(handle)
    except Exception:  # noqa: BLE001
        pass


def _read_single_key() -> str:
    """Read exactly one keypress (no Enter required) on Windows.

    Returns ESC as ``'\\x1b'``, ignores extended keys (arrows etc.).
    Falls back to ``input()`` when msvcrt is unavailable or stdin is not a
    real console (e.g. VS Code integrated terminal, piped stdin).
    """
    if sys.platform == 'win32':
        try:
            import msvcrt
            while True:
                ch = msvcrt.getwch()
                if ch in ('\x00', '\xe0'):
                    msvcrt.getwch()  # discard second byte of extended key
                    continue
                return ch
        except (OSError, IOError):
            pass  # fall through to input() fallback
    elif sys.platform != 'win32':
        try:
            import termios
            import tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            return ch
        except (OSError, IOError, AttributeError):
            pass  # fall through to input() fallback
    # Fallback: read a full line; treat first character as the keystroke
    try:
        line = input().strip()
        return line[0].lower() if line else '\n'
    except (EOFError, KeyboardInterrupt):
        return '\x1b'  # treat EOF / Ctrl-C as ESC / exit


def _open_directory(path: Path) -> None:
    """Open *path* in the OS file manager (creates it first if needed)."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == 'win32':
        subprocess.Popen(['explorer', str(path)])
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(path)])
    else:
        subprocess.Popen(['xdg-open', str(path)])


def _open_file_default(path: Path) -> None:
    """Open *path* with its default associated application (non-blocking)."""
    if sys.platform == 'win32':
        os.startfile(str(path))  # noqa: S606 — user-chosen file path
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(path)])
    else:
        subprocess.Popen(['xdg-open', str(path)])


# ──────────────────────────────────────────────────────────────────────────────
# TUI 状态机
# ──────────────────────────────────────────────────────────────────────────────

class TUI:
    """Rich-based TUI state machine for the jianpu conversion tool.

    States
    ------
    main      : top-level menu (1-7)
    convert   : batch conversion screen
    editor    : jianpu editor — score list + editing loop
    help      : help/usage screen
    """

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        self.console = Console()
        self.config = config or AppConfig()
        self.running = True
        self.current_screen = 'main'
        self._screen_stack: list[str] = []

    # ── Navigation ────────────────────────────────────────────────────────────

    def _push_screen(self, screen: str) -> None:
        self._screen_stack.append(self.current_screen)
        self.current_screen = screen

    def _pop_screen(self) -> None:
        self.current_screen = self._screen_stack.pop() if self._screen_stack else 'main'

    # ── Layout primitives ─────────────────────────────────────────────────────

    def _header(self, subtitle: str = '主菜单') -> None:
        """Clear screen and render the title panel."""
        self.console.clear()
        self.console.print(Panel(
            f'[bold cyan]简谱转换工具  v{APP_VERSION}[/bold cyan]'
            f'  [dim]│[/dim]  [white]{subtitle}[/white]',
            expand=True,
            border_style='bright_blue',
        ))

    def _status_bar(self, text: str) -> None:
        self.console.print(Rule(f'[dim]{text}[/dim]', style='dim'))

    # ── Permission error screen ───────────────────────────────────────────────


    def _screen_permission_error(self, base_dir: Path) -> None:
        """Show a persistent permission error screen until the user presses a key."""
        while True:
            self.console.clear()
            self.console.print(Panel(
                f'[yellow]程序安装路径：[/yellow]{base_dir}\n\n'
                '[bold white]解决方法：[/bold white]\n'
                '  右键点击 [cyan]ConvertTool.exe[/cyan]\n'
                '  选择 [bold cyan]"以管理员身份运行"[/bold cyan]\n\n'
                '[dim]按任意键关闭...[/dim]',
                title='[bold red]  权限不足，无法写入程序目录  [/bold red]',
                border_style='red',
                padding=(1, 3),
            ))
            _read_single_key()
            return

    # ── Main loop ─────────────────────────────────────────────────────────────


    def run(self) -> None:
        base_dir = get_app_base_dir()
        setup_logging(base_dir)

        # Detect permission problems before entering the main loop.
        # Programs installed under C:\Program Files cannot write to the install
        # directory without Administrator privileges; detect this early so the
        # user sees a clear, persistent message instead of a brief crash.

        if not _is_base_dir_writable(base_dir):
            self._screen_permission_error(base_dir)
            return

        # Clear any keystrokes that collected in the console input buffer
        # before the TUI appeared (e.g. the Enter press that launched the
        # program, or residual input from a previous session in the same
        # terminal window).
        _flush_console_input_buffer()

        while self.running:
            try:
                if self.current_screen == 'main':
                    self._screen_main()
                elif self.current_screen == 'convert':
                    self._screen_convert()
                elif self.current_screen == 'editor':
                    self._screen_editor()
                elif self.current_screen == 'help':
                    self._screen_help()
                else:
                    self.current_screen = 'main'
            except KeyboardInterrupt:
                self._header('退出')
                self.console.print('\n[yellow]已取消，程序退出。[/yellow]\n')
                break
            except SystemExit:
                break
            except PermissionError:
                self._screen_permission_error(get_app_base_dir())
                break
            except Exception as exc:  # noqa: BLE001
                # Catch unexpected exceptions so the program exits gracefully
                # rather than showing a raw Python traceback to the user.
                import traceback
                self.console.clear()
                self.console.print(f'[bold red]程序遇到意外错误，已退出：[/bold red]\n{exc}')
                self.console.print('[dim](详细信息已写入 logs 目录)[/dim]')
                log_message(f'TUI 意外崩溃:\n{traceback.format_exc()}', logging.ERROR)
                break

    # ── Screen: Main Menu ─────────────────────────────────────────────────────

    def _screen_main(self) -> None:
        base_dir = get_app_base_dir()
        input_dir = base_dir / self.config.input_dir_name
        output_dir = base_dir / self.config.output_dir_name
        logs_dir = base_dir / self.config.logs_dir_name

        self._header('主菜单')
        self.console.print()
        self.console.print('  [bold]1[/bold]  打开五线谱输入目录')
        self.console.print('  [bold]2[/bold]  开始五线谱转换')
        self.console.print('  [bold]3[/bold]  打开简谱输出目录')
        self.console.print('  [bold]4[/bold]  打开简谱编辑器')
        self.console.print('  [bold]5[/bold]  打开日志目录')
        self.console.print('  [bold]6[/bold]  帮助')
        self.console.print('  [bold]7[/bold]  退出')
        self.console.print()
        self.console.print('  [dim](c) 2026 Tsukamotoshio. All rights reserved.[/dim]')
        self._status_bar('按数字键选择功能  │  ESC 或 7 退出')

        key = _read_single_key()
        if key == '1':
            _open_directory(input_dir)
        elif key == '2':
            self._push_screen('convert')
        elif key == '3':
            _open_directory(output_dir)
        elif key == '4':
            self._push_screen('editor')
        elif key == '5':
            _open_directory(logs_dir)
        elif key == '6':
            self._push_screen('help')
        elif key in ('7', '\x1b', 'q', 'Q'):
            self.running = False

    # ── Screen: Conversion ────────────────────────────────────────────────────

    def _screen_convert(self) -> None:
        import dataclasses
        from .config import AppConfig, OMREngine
        from .homr_runner import _homr_gpu_available

        from .pipeline import process_bulk_input_to_jianpu

        base_dir = get_app_base_dir()
        editor_workspace_dir = base_dir / EDITOR_WORKSPACE_DIR_NAME

        # ── Engine selection ──────────────────────────────────────────────────

        self._header('选择识别引擎')
        self.console.print()
        self.console.print('  请选择 OMR（光学乐谱识别）引擎：')
        self.console.print()
        self.console.print(
            '  [bold]1[/bold]  [green]自动（按格式选择）[/green]  [bold green]← 推荐[/bold green]'
        )
        self.console.print(
            '         [dim]PDF 输入 → Audiveris；图片（PNG/JPG）输入 → Audiveris[/dim]'
        )
        self.console.print()
        self.console.print(
            '  [bold]2[/bold]  [cyan]Audiveris[/cyan]  [dim]（手动指定，强制用于所有格式）[/dim]'
        )
        self.console.print(
            '         [dim]对印刷扫描件（高分辨率、高对比度 PDF）效果最好[/dim]'
        )
        self.console.print(
            '         [dim]基于传统启发式算法，不依赖 GPU，任何机器均可运行[/dim]'
        )
        self.console.print()
        gpu_available = _homr_gpu_available()
        gpu_info = '已检测到 GPU（DirectML/CUDA）' if gpu_available else 'CPU（未检测到可用 GPU）'
        self.console.print(
            '  [bold]3[/bold]  [cyan]Homr[/cyan]  [dim]（实验性，手动指定，强制用于所有格式）[/dim]'
        )
        self.console.print(
            '         [dim]对拍照乐谱（手机拍摄、光线不均匀、低对比度图像）效果更好[/dim]'
        )
        self.console.print(
            f'         [dim]识别速度取决于 GPU 性能（当前计算设备：{gpu_info}[/dim]'
        )
        self.console.print(
            '         [dim]GPU 优先级：DirectML (任意 GPU) > CUDA+cuDNN > CPU 回退[/dim]'

        )
        self.console.print()
        self._status_bar('按数字键选择引擎  │  ESC / b = 返回')

        engine_key = _read_single_key()
        if engine_key in ('\x1b', 'b', 'B'):
            self._pop_screen()
            return

        if engine_key == '2':
            selected_engine = OMREngine.AUDIVERIS
            engine_display = 'Audiveris（强制）'
        elif engine_key == '3':
            selected_engine = OMREngine.HOMR
            engine_display = 'Homr（强制）'
        else:
            # '1' 或任意其他键 → 自动按格式选择（默认）
            selected_engine = OMREngine.AUTO
            engine_display = '自动（Audiveris）'

        config_with_engine = dataclasses.replace(self.config, omr_engine=selected_engine)

        # ── Conversion ────────────────────────────────────────────────────────

        self._header(f'开始五线谱转换  [dim]（{engine_display}）[/dim]')
        self.console.print()
        self.console.print(
            '[dim]  提示：转换完成后会保留简谱编辑中间文件，'
            '可通过「打开简谱编辑器」进行手动校对。[/dim]'
        )
        self.console.print()

        try:
            process_bulk_input_to_jianpu(
                config_with_engine,
                editor_workspace_dir=editor_workspace_dir,
            )
        except SystemExit:
            pass
        except (EOFError, KeyboardInterrupt):
            self.console.print('\n[yellow]  已取消。[/yellow]')

        self.console.print()
        self._status_bar('按任意键返回主菜单...')
        _read_single_key()
        self._pop_screen()

    # ── Screen: Editor — Score List ───────────────────────────────────────────

    def _screen_editor(self) -> None:
        base_dir = get_app_base_dir()
        editor_workspace_dir = base_dir / EDITOR_WORKSPACE_DIR_NAME

        # Collect all .jianpu.txt files in editor workspace
        txt_files: list[Path] = []
        if editor_workspace_dir.exists():
            txt_files = sorted(editor_workspace_dir.glob('*.jianpu.txt'))

        if not txt_files:
            self._header('简谱编辑器')
            self.console.print()
            self.console.print('[yellow]  暂无可编辑的简谱文件。[/yellow]')
            self.console.print()
            self.console.print(
                '  提示：请先通过「2. 开始五线谱转换」完成至少一次转换，\n'
                '        工具将自动保留 OMR 识别的中间文件供您手动校对。'
            )
            self.console.print()
            self._status_bar('按任意键返回...')
            _read_single_key()
            self._pop_screen()
            return

        page = 0
        total_pages = (len(txt_files) + PAGE_SIZE - 1) // PAGE_SIZE

        while True:
            start = page * PAGE_SIZE
            end = min(start + PAGE_SIZE, len(txt_files))
            page_files = txt_files[start:end]

            page_info = f'({page + 1}/{total_pages} 页)' if total_pages > 1 else ''
            self._header(f'简谱编辑器  [dim]{page_info}[/dim]')
            self.console.print()
            self.console.print(f'  共 [cyan]{len(txt_files)}[/cyan] 个可编辑乐谱：')
            self.console.print()

            for idx, f in enumerate(page_files, start=1):
                stem = f.stem  # e.g. "Scarborough Fair.jianpu"
                title = stem[:-len('.jianpu')] if stem.endswith('.jianpu') else stem
                self.console.print(f'  [bold]{idx}[/bold]  {title}')

            self.console.print()

            nav_parts = ['按数字键选择乐谱']
            if total_pages > 1:
                if page > 0:
                    nav_parts.append('[bold]p[/bold]=上一页')
                if page < total_pages - 1:
                    nav_parts.append('[bold]n[/bold]=下一页')
            nav_parts.append('[bold]b[/bold]/ESC=返回')
            self._status_bar('  │  '.join(nav_parts))

            key = _read_single_key().lower()

            if key in ('\x1b', 'b'):
                self._pop_screen()
                return
            if key == 'n' and page < total_pages - 1:
                page += 1
                continue
            if key == 'p' and page > 0:
                page -= 1
                continue

            if key.isdigit():
                num = int(key)
                item_count = end - start
                if 1 <= num <= item_count:
                    self._screen_editor_review(page_files[num - 1])

    # ── Screen: Editor — Review / Re-generate ────────────────────────────────

    def _screen_editor_review(self, txt_path: Path) -> None:
        """Editing loop for a single score: open Notepad + source image, then re-generate."""
        base_dir = get_app_base_dir()
        output_dir = base_dir / self.config.output_dir_name

        stem = txt_path.stem  # e.g. "Scarborough Fair.jianpu"
        title = stem[:-len('.jianpu')] if stem.endswith('.jianpu') else stem

        # Find companion source image / PDF
        source_file: Optional[Path] = None
        for ext in ('.pdf', '.png', '.jpg', '.jpeg'):
            candidate = txt_path.parent / f'{title}.source{ext}'
            if candidate.exists():
                source_file = candidate
                break

        while True:
            self._header(f'简谱编辑器 — {title}')
            self.console.print()
            self.console.print(f'  待校对乐谱：[cyan]{title}[/cyan]')
            if source_file is not None:
                self.console.print(f'  参考图像：  [dim]{source_file.name}[/dim]')
            else:
                self.console.print('  [dim]  （未找到参考图像）[/dim]')
            self.console.print()
            self.console.print('  即将自动打开记事本供您校对 ...')
            self.console.print('  [dim]（将同时打开参考图像，请对照修改文本内容）[/dim]')
            self.console.print()
            self._status_bar('保存并关闭记事本后，工具将等待您的下一步选择')

            # Open reference image (non-blocking, best-effort)
            if source_file is not None:
                try:
                    _open_file_default(source_file)
                except OSError:
                    pass

            # Open Notepad and wait for it to close
            notepad_proc: Optional[subprocess.Popen] = None
            try:
                notepad_proc = subprocess.Popen(['notepad.exe', str(txt_path)])
            except OSError:
                self.console.print('[red]  无法启动记事本，请手动编辑以下文件：[/red]')
                self.console.print(f'    [dim]{txt_path}[/dim]')
                self.console.print()
                self._status_bar('手动编辑并保存后，按任意键继续...')
                _read_single_key()

            if notepad_proc is not None:
                self._header(f'简谱编辑器 — {title}  [dim][等待记事本关闭 ...][/dim]')
                self.console.print()
                self.console.print('  [dim]正在等待记事本关闭，请完成校对后保存文件并关闭窗口 ...[/dim]')
                notepad_proc.wait()

            # Post-edit action menu
            self._header(f'简谱编辑器 — {title}')
            self.console.print()
            self.console.print('  校对完成，请选择下一步操作：')
            self.console.print()
            self.console.print('  [bold]1[/bold]  开始生成简谱 PDF')
            self.console.print('  [bold]2[/bold]  再次打开编辑器（继续修改）')
            self.console.print('  [bold]0[/bold]  返回乐谱列表')
            self.console.print()
            self._status_bar('按数字键选择  │  ESC / b = 返回列表')

            choice = _read_single_key()
            if choice in ('0', 'b', '\x1b'):
                return
            if choice == '2':
                continue  # loop back to re-open Notepad
            if choice == '1':
                self._do_regenerate(txt_path, title, output_dir)
                return

    def _do_regenerate(self, txt_path: Path, title: str, output_dir: Path) -> None:
        """Regenerate jianpu PDF from an edited .jianpu.txt via jianpu-ly → LilyPond.

        The editor workspace file has a human-readable ``#``-prefixed comment
        header prepended by ``_build_editor_header()``.  jianpu-ly.py does NOT
        treat ``#`` as a line comment inside score content — it only skips them
        inside its own documentation output routine.  We therefore write a
        temporary copy of the file with all ``#`` comment lines stripped before
        passing it to ``render_jianpu_ly``.
        """
        import tempfile
        from .renderer import sanitize_generated_lilypond_file
        from .runtime_finder import render_jianpu_ly, render_lilypond_pdf
        from .utils import safe_remove_file

        self._header('简谱编辑器 — 生成中 ...')
        self.console.print()
        self.console.print(f'  正在从校对文件生成简谱 PDF：[cyan]{title}[/cyan]')
        self.console.print()

        ly_path = txt_path.with_suffix('.ly')
        output_dir.mkdir(parents=True, exist_ok=True)
        success = False
        out_pdf: Optional[Path] = None
        clean_txt_path: Optional[Path] = None

        try:
            # Strip "# comment" header lines — jianpu-ly parses # as a sharp
            # accidental token, causing "Unrecognised command # in score" errors.
            raw = txt_path.read_text(encoding='utf-8', errors='ignore')
            clean = '\n'.join(
                line for line in raw.splitlines() if not line.startswith('#')
            )
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', suffix='.jianpu.txt',
                dir=txt_path.parent, delete=False,
            ) as tmp:
                tmp.write(clean)
                clean_txt_path = Path(tmp.name)

            if render_jianpu_ly(clean_txt_path, ly_path):
                sanitize_generated_lilypond_file(ly_path, title)
                pdf_result = render_lilypond_pdf(ly_path)
                if pdf_result is not None and pdf_result.exists():
                    # Output filename mirrors the main pipeline convention
                    out_pdf = output_dir / f'{txt_path.stem}.pdf'
                    shutil.copy(str(pdf_result), str(out_pdf))
                    success = True
        except Exception as exc:
            log_message(f'重新生成 PDF 时发生错误: {exc}', logging.WARNING)
        finally:
            if clean_txt_path is not None:
                safe_remove_file(clean_txt_path)
            safe_remove_file(ly_path)

        self._header(f'简谱编辑器 — 生成结果')
        self.console.print()
        if success and out_pdf is not None:
            self.console.print(f'  [bold green]✓ 简谱 PDF 已生成：[/bold green]')
            self.console.print(f'    [dim]{out_pdf}[/dim]')
            try:
                _open_file_default(out_pdf)
            except OSError:
                pass
        else:
            self.console.print('  [bold red]✗ PDF 生成失败，请检查文本格式后重试。[/bold red]')
            self.console.print()
            self.console.print('  [dim]常见问题：[/dim]')
            self.console.print('    [dim]• 音符数字或时值符号写错（参考文件顶部的注释说明）[/dim]')
            self.console.print('    [dim]• 小节总时值与拍号不一致[/dim]')
            self.console.print('    [dim]• 删除了必要的 1=C / 4/4 等谱号行[/dim]')
        self.console.print()
        self._status_bar('按任意键返回...')
        _read_single_key()

    # ── Screen: Help ──────────────────────────────────────────────────────────

    def _screen_help(self) -> None:
        self._header('帮助')
        self.console.print()
        self.console.print(Panel(
            '[bold]使用步骤[/bold]\n'
            '  1. 将 PDF / PNG / JPG 五线谱文件放入 [cyan]Input[/cyan] 文件夹\n'
            '  2. 选择「2. 开始五线谱转换」，选择识别引擎后按提示回答 Y/N\n'
            '  3. 转换结果（简谱 PDF / MIDI）保存在 [cyan]Output[/cyan] 文件夹\n'
            '  4. 如需手动校对，选择「4. 打开简谱编辑器」\n\n'
            '[bold]引擎选择说明[/bold]\n'
            '  • [green]自动（推荐）[/green] — PDF + 图片（PNG/JPG）→ Audiveris\n'
            '  • [cyan]Audiveris[/cyan] — 手动指定，强制用于所有格式\n'
            '    基于规则的传统 OMR 引擎，对高对比度印刷乐谱和 PDF 效果最佳\n'
            '  • [cyan]Homr[/cyan]      — 实验性，基于深度学习\n'
            '    对手机拍摄或光线不均匀图像效果更好，使用前请确认 homr 运行环境安装完成\n\n'
            '[bold]简谱编辑器说明[/bold]\n'
            '  • 每次转换后，工具自动保留 OMR 识别的中间文件到 [cyan]editor-workspace[/cyan] 目录\n'
            '  • 在编辑器中选择乐谱，记事本将自动打开供您修改简谱文本\n'
            '  • 同时会打开预处理后的乐谱图像作为参考\n'
            '  • 保存并关闭记事本后，选择「开始生成简谱 PDF」即可输出校正后的版本\n\n'
            '[bold]支持格式[/bold]\n'
            '  PDF  ·  PNG  ·  JPG / JPEG\n\n'
            '[bold]常见问题[/bold]\n'
            '  • 转换失败     →  查看「5. 打开日志目录」中的 .log 文件\n'
            '  • 没有输出     →  确认 Input 文件夹中有支持的文件\n'
            '  • 程序缓慢     →  多页 PDF 识别需要数分钟，请耐心等待\n'
            '  • Audiveris 失败  →  检查 audiveris-runtime 目录是否存在\n'
            '  • Homr 失败      →  检查 omr_engine/homr 运行环境',
            title='[bold cyan]简谱转换工具  帮助[/bold cyan]',
            padding=(1, 2),
        ))
        self.console.print()
        self._status_bar('按 b 或 ESC 返回主菜单')
        while True:
            key = _read_single_key().lower()
            if key in ('b', '\x1b', '\r', '\n', ' '):
                self._pop_screen()
                return


# ──────────────────────────────────────────────────────────────────────────────
# 入口点
# ──────────────────────────────────────────────────────────────────────────────

def main_tui(config: Optional[AppConfig] = None) -> None:
    """Launch the Rich TUI state machine. Called from pipeline.main()."""
    tui = TUI(config or AppConfig())
    tui.run()
