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
    """Re-exec with the project .venv interpreter unless already running from it.

    判断依据是解释器身份，而不是"能否 import flet"：一旦系统 Python 里也装了
    flet（例如在 venv 之外运行了 pip install -r requirements.txt），哨兵式检测
    就被击穿——应用会在依赖版本不受控的系统环境里运行，窗口静默启动失败。
    只要项目 .venv 存在且当前解释器不是它，就无条件切换过去。
    """
    if getattr(sys, 'frozen', False):
        return  # 打包版：依赖已捆绑，无 venv 概念
    _here = os.path.dirname(os.path.abspath(__file__))
    for _rel in (('.venv', 'Scripts', 'python.exe'), ('.venv', 'bin', 'python')):
        _py = os.path.join(_here, *_rel)
        if not os.path.isfile(_py):
            continue
        if os.path.normcase(os.path.abspath(sys.executable)) == os.path.normcase(_py):
            return  # 已经在项目 venv 里
        import subprocess
        # 用 .venv 的解释器重新启动自己。父进程（系统 Python）只是转发等待，
        # 这期间终端不会返回提示符——这是正常的，GUI 正在子进程里运行。
        print('[启动] 检测到当前不是项目 .venv 解释器，切换到 .venv 运行（GUI 启动中，请稍候）…',
              file=sys.stderr, flush=True)
        try:
            sys.exit(subprocess.run([_py] + sys.argv).returncode)
        except KeyboardInterrupt:
            # 用户在终端 Ctrl+C：子进程已随进程组一并收到信号，
            # 这里安静退出，避免向用户抛出无意义的 subprocess 等待堆栈。
            sys.exit(130)
    # 项目 .venv 不存在 —— 回退到依赖可用性检测（允许用户自备环境）
    try:
        import flet  # noqa: F401
        return
    except ImportError:
        pass
    print(
        '\n[错误] 未找到虚拟环境或 flet 未安装。\n'
        '  pip install -r requirements.txt\n'
        '  pip install flet pymupdf music21 pillow opencv-python onnxruntime-directml\n',
        file=sys.stderr,
    )
    sys.exit(1)

_bootstrap_venv()
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
from gui.strings import t, set_language
from gui.settings import get_saved_language, set_saved_language
from gui.theme import Palette, with_alpha, make_dark_theme, make_light_theme, FONT_BODY, FONT_EMPHASIS
from gui.pages.landing_page import LandingPage
from gui.pages.audio_page import AudioPage
from gui.pages.jianpu_preview_page import JianpuPreviewPage
from gui.pages.editor_page import EditorPage
from gui.pages.transposer_page import TransposerPage
from gui.pages.score_preview_page import ScorePreviewPage
from gui.pages.about_page import AboutPage
from gui.components.progress_overlay import ProgressOverlay
from core.app.win_exe_patch import patch_exe_resources
from core.config import APP_VERSION


def _app_version_tuple() -> tuple[int, int, int, int]:
    """(major, minor, patch, build) derived from APP_VERSION for the exe VERSIONINFO.

    Single source of truth: core.config.APP_VERSION. Prevents the stale hardcoded
    version this call site used to carry drifting from the real app version.
    A 3-segment version pads build=0; a 4-segment version ('0.4.1.1', used for a
    packaging-only patch release) carries its own 4th part through.
    """
    parts = [int(x) for x in APP_VERSION.split('.')[:4] if x.isdigit()]
    while len(parts) < 4:
        parts.append(0)
    return parts[0], parts[1], parts[2], parts[3]


# ─────────────────────────────────────────────────────────────────────────────
# Navigation destinations
# ─────────────────────────────────────────────────────────────────────────────

_NAV_ITEMS = [
    ('landing',       ft.Icons.DOCUMENT_SCANNER_ROUNDED,    ft.Icons.DOCUMENT_SCANNER_OUTLINED,    t('app.nav_landing')),
    ('audio',         ft.Icons.GRAPHIC_EQ_ROUNDED,          ft.Icons.GRAPHIC_EQ_ROUNDED,           t('app.nav_audio')),
    ('editor',        ft.Icons.EDIT_NOTE_ROUNDED,           ft.Icons.EDIT_NOTE_OUTLINED,           t('app.nav_editor')),
    ('score_preview', ft.Icons.LIBRARY_MUSIC_ROUNDED,       ft.Icons.LIBRARY_MUSIC_OUTLINED,       t('app.nav_score_preview')),
    ('about',         ft.Icons.INFO_ROUNDED,                ft.Icons.INFO_OUTLINE_ROUNDED,         t('app.nav_about')),
]


# HOMR weight filenames are content-addressed: the model number and a hash are
# baked into the name (e.g. encoder_pytorch_model_367-<sha>.onnx). The pinned
# _WEIGHT_FILES set therefore fully identifies the current release's weights;
# any other file matching this naming scheme is a leftover from a previous
# model version and is safe to delete.
_WEIGHT_NAME_PREFIXES = (
    'segnet_', 'encoder_pytorch_model_', 'decoder_pytorch_model_',
)


def _prune_orphan_weights(target_dir, expected_files) -> None:
    """Delete stale HOMR weights left over from a previous model version.

    Called only after every pinned weight is verified present, so the active
    model is never left without usable weights if this runs. Restricted to the
    HOMR weight naming scheme and to *.onnx directly inside models/ (glob does
    not recurse into siblings like models/vlm/), so unrelated files are never
    touched. Best-effort: a locked or unremovable old file lingering is harmless.
    """
    expected = set(expected_files)
    for onnx in target_dir.glob('*.onnx'):
        if onnx.name in expected:
            continue
        if not onnx.name.startswith(_WEIGHT_NAME_PREFIXES):
            continue
        try:
            onnx.unlink()
            print(f'Removed stale HOMR weight: {onnx.name}', file=sys.stderr)
        except OSError:
            pass


def _load_homr_weight_manifest() -> tuple[list[str], dict[str, str]]:
    """Read _WEIGHT_FILES / _WEIGHT_HASHES from the homr submodule *source* via AST.

    Importing ``homr.main`` would pull onnxruntime / cv2 / rapidocr (and trigger
    CUDA initialisation) into the GUI process at startup — the exact heavy work
    the worker subprocess exists to isolate. On some GPUs that init hangs the
    launch, which then keeps the single-instance mutex held so every later launch
    silently exits ("main window won't show"). These two symbols are plain
    module-level literals, so parse them out of the source without executing it.
    Returns ([], {}) on any failure (caller treats models as absent).
    """
    import ast
    from pathlib import Path
    src = Path(__file__).parent / 'omr_engine' / 'homr' / 'homr' / 'main.py'
    files: list[str] = []
    hashes: dict[str, str] = {}
    try:
        tree = ast.parse(src.read_text(encoding='utf-8', errors='ignore'))
    except Exception:
        return files, hashes
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        else:
            continue
        if target not in ('_WEIGHT_FILES', '_WEIGHT_HASHES') or node.value is None:
            continue
        try:
            val = ast.literal_eval(node.value)
        except Exception:
            continue
        if target == '_WEIGHT_FILES' and isinstance(val, list):
            files = [str(x) for x in val]
        elif target == '_WEIGHT_HASHES' and isinstance(val, dict):
            hashes = {str(k): str(v) for k, v in val.items()}
    return files, hashes


def _verify_weight_sha256(path, expected: str) -> bool:
    """Local copy of homr.main.verify_sha256 (empty ``expected`` → True)."""
    if not expected:
        return True
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
    except OSError:
        return False
    return h.hexdigest().lower() == expected.lower()


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
    # Read the weight manifest from source (no `import homr.main` → no onnxruntime
    # / CUDA in the GUI process; see _load_homr_weight_manifest for why).
    _WEIGHT_FILES, _WEIGHT_HASHES = _load_homr_weight_manifest()
    if not _WEIGHT_FILES:
        state.homr_available = False
        return

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
        and _verify_weight_sha256(str(target_dir / fname), _WEIGHT_HASHES.get(fname, ''))
        for fname in _WEIGHT_FILES
    )

    # Prune-on-success: once the current release's pinned weights are all
    # verified present, delete leftovers from a previous model version (e.g.
    # the 331 set after a 367 upgrade). This is static and release-gated — it
    # only fires when _WEIGHT_FILES changes between releases (i.e. when the
    # bundled homr submodule is bumped), never on a routine launch.
    if state.homr_available:
        _prune_orphan_weights(target_dir, _WEIGHT_FILES)


def _check_piano_model(state: AppState) -> None:
    """Set state.piano_model_available from disk (no download, no torch import).

    Mirrors _check_homr_models's "cheap presence check at startup" role for the
    audio-recognition engine (core/omr/audio_runner.py's ByteDance checkpoint).
    """
    try:
        from core.omr.audio_runner import piano_model_available
        state.piano_model_available = piano_model_available()
    except Exception:
        state.piano_model_available = False


# ─────────────────────────────────────────────────────────────────────────────
# Global exception handlers (P1-1): uncaught exception = log + tell the user
# ─────────────────────────────────────────────────────────────────────────────

def _log_uncaught_exception(prefix: str, exc_type, exc_value, exc_tb):
    """Write an uncaught exception with full traceback to the app log.

    Returns the log file path (or None). Goes through the core.utils module
    attribute so it picks up the GUI's log redirect once installed, and
    initialises the log file lazily on first use.
    """
    import traceback as _traceback
    import logging as _logging
    try:
        import core.utils as _cu
        text = prefix
        if exc_type is not None:
            text += '\n' + ''.join(
                _traceback.format_exception(exc_type, exc_value, exc_tb)
            ).rstrip()
        _cu.log_message(text, _logging.ERROR)
        return getattr(_cu, 'LOG_FILE_PATH', None)
    except Exception:
        return None


def _install_global_exception_handlers() -> None:
    """Install sys/threading exception hooks for the GUI process.

    未捕获异常此前是静默陷阱：主线程崩溃只在（通常不可见的）stderr 打印，
    后台线程崩溃连打印都没人看。现在统一写入 logs/ 日志；主线程致命崩溃
    额外用原生 MessageBox 告知用户日志路径（此时 Flet UI 可能已死，不能
    依赖页内对话框）。后台线程只记日志不弹窗——应用往往仍在运行，且可能
    连续触发，弹窗会形成轰炸。asyncio 循环的处理器在 main() 里另行安装。
    设 SUMISORA_NO_CRASH_DIALOG=1 可抑制弹窗（自动化测试/CI 用）。
    """
    import threading as _threading

    _orig_excepthook = sys.excepthook

    def _gui_excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            _orig_excepthook(exc_type, exc_value, exc_tb)
            return
        log_path = _log_uncaught_exception(
            '未捕获异常（主线程），程序即将退出：', exc_type, exc_value, exc_tb
        )
        _orig_excepthook(exc_type, exc_value, exc_tb)
        if sys.platform == 'win32' and os.environ.get('SUMISORA_NO_CRASH_DIALOG') != '1':
            try:
                import ctypes as _ct
                _ct.windll.user32.MessageBoxW(
                    0,
                    t('app.crash_dialog_body', path=str(log_path or '?')),
                    t('app.crash_dialog_title'),
                    0x10,  # MB_ICONERROR | MB_OK
                )
            except Exception:
                pass

    sys.excepthook = _gui_excepthook

    _orig_threading_hook = _threading.excepthook

    def _thread_excepthook(args):
        if args.exc_type is SystemExit:
            return
        _log_uncaught_exception(
            f'未捕获异常（后台线程 {args.thread.name if args.thread else "?"}）：',
            args.exc_type, args.exc_value, args.exc_traceback,
        )
        _orig_threading_hook(args)

    _threading.excepthook = _thread_excepthook


# ─────────────────────────────────────────────────────────────────────────────
# Main application
# ─────────────────────────────────────────────────────────────────────────────

async def main(page: ft.Page) -> None:
    # ── Page base configuration ───────────────────────────────────────────────
    page.title       = t('app.window_title')
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
    # 打包的 Noto Sans SC 静态子集（Regular 正文 + Medium 强调），所有机器渲染一致，
    # 不再依赖系统是否安装中文字体。子集外的生僻字由 Flutter 逐字回落到系统字体。
    import os as _os
    page.fonts = {
        FONT_BODY:     '/fonts/NotoSansSC-UI-Regular.ttf',
        FONT_EMPHASIS: '/fonts/NotoSansSC-UI-Medium.ttf',
    }

    page.theme_mode  = ft.ThemeMode.LIGHT
    page.theme       = make_light_theme(font_family=FONT_BODY)
    page.dark_theme  = make_dark_theme(font_family=FONT_BODY)

    # ── Global state ──────────────────────────────────────────────────────────
    state = AppState()
    _check_homr_models(state)
    _check_piano_model(state)

    # ── asyncio 未捕获异常 → 日志 + GUI 日志面板（P1-1）─────────────────────────
    # Flet 事件循环里未被 await 的任务异常此前会被静默吞掉；记录到日志文件，
    # 并回显到应用内日志面板。保留默认处理器的 stderr 输出便于开发期观察。
    try:
        import asyncio as _asyncio
        _loop = _asyncio.get_running_loop()

        def _on_loop_exception(loop, context):
            exc = context.get('exception')
            if exc is not None:
                _log_uncaught_exception(
                    f'未捕获异常（asyncio 事件循环）：{context.get("message", "")}',
                    type(exc), exc, exc.__traceback__,
                )
            else:
                _log_uncaught_exception(
                    f'asyncio 事件循环错误：{context.get("message", "")}',
                    None, None, None,
                )
            try:
                loop.default_exception_handler(context)
            except Exception:
                pass

        _loop.set_exception_handler(_on_loop_exception)
    except Exception:
        pass

    # 应用上次保存的界面语言（必须在构建任何页面/控件之前，因为各控件在构造时
    # 即调用 t() 取当前语言的文案）。
    _saved_lang = get_saved_language()
    state.language = _saved_lang
    set_language(_saved_lang)

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
    audio_page           = AudioPage(state, overlay)
    jianpu_preview_page  = JianpuPreviewPage(state)
    editor_page          = EditorPage(state)
    score_preview_page   = ScorePreviewPage(state)
    transposer_page      = TransposerPage(state)
    about_page           = AboutPage()

    # ── Content area (ft.Stack with overlay support) ──────────────────────────
    # 页面按名字映射到容器，避免依赖导航项/子页的硬编码索引。前若干项对应导航栏，
    # 'jianpu_edit' 与 'transposer' 是没有导航项的子页（通过事件跳转）。
    _page_containers: dict[str, ft.Container] = {
        'landing':       ft.Container(content=landing_page,        expand=True, visible=True),
        'audio':         ft.Container(content=audio_page,          expand=True, visible=False),
        'editor':        ft.Container(content=jianpu_preview_page, expand=True, visible=False),
        'score_preview': ft.Container(content=score_preview_page,  expand=True, visible=False),
        'about':         ft.Container(content=about_page,          expand=True, visible=False),
        'jianpu_edit':   ft.Container(content=editor_page,         expand=True, visible=False),  # sub
        'transposer':    ft.Container(content=transposer_page,     expand=True, visible=False),  # sub
    }
    content_stack = ft.Stack([*_page_containers.values(), overlay], expand=True)

    _NAV_NAMES = [item[0] for item in _NAV_ITEMS]  # nav-rail page names, in order

    def _show_page(name: str) -> None:
        for key, container in _page_containers.items():
            container.visible = (key == name)
        state.current_page = name
        # 简谱编辑子页禁止语言切换（避免编辑期间整页重译干扰）；离开后恢复。
        _lang_icon.disabled = (name == 'jianpu_edit')
        try:
            _lang_icon.update()
        except Exception:
            pass
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

    # 程序化页面跳转（如转换结果对话框的「查看简谱」按钮）。
    # nav_rail 在下方才创建，回调通过 run_task 延后到事件循环执行，届时已可用。
    def _on_navigate(name: str, **_) -> None:
        async def _do():
            _show_page(name)
            if name in _NAV_NAMES:
                nav_rail.selected_index = _NAV_NAMES.index(name)
                try:
                    nav_rail.update()
                except Exception:
                    pass
            state.emit(Event.PAGE_CHANGED, page=name)
        page.run_task(_do)

    state.on(Event.NAVIGATE, _on_navigate)

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
        tooltip=t('app.tooltip_hide_label'),
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
                # 用 t() 取当前语言文案，而非 _NAV_ITEMS 里在 import 时冻结的中文标签，
                # 保证以保存的英文语言启动时导航栏也正确显示。
                label=t(f'app.nav_{name}'),
            )
            for name, icon_sel, icon_out, _label in _NAV_ITEMS
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
            _nav_label_toggle_btn.tooltip = t('app.tooltip_hide_label')
        else:
            nav_rail.label_type = ft.NavigationRailLabelType.SELECTED
            _nav_label_toggle_btn.icon = ft.Icons.CHEVRON_RIGHT_ROUNDED
            _nav_label_toggle_btn.tooltip = t('app.tooltip_show_label')
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
        tooltip=t('app.tooltip_theme_toggle'),
        on_click=lambda _e: _toggle_theme(),
    )

    def _toggle_theme() -> None:
        state.toggle_theme()
        if state.dark_mode:
            page.theme_mode = ft.ThemeMode.DARK
            page.theme      = make_dark_theme(font_family=FONT_BODY)
            page.dark_theme = make_dark_theme(font_family=FONT_BODY)
            _theme_icon.icon = ft.Icons.DARK_MODE_ROUNDED
        else:
            page.theme_mode  = ft.ThemeMode.LIGHT
            page.theme       = make_light_theme(font_family=FONT_BODY)
            _theme_icon.icon = ft.Icons.LIGHT_MODE_ROUNDED
        try:
            page.update()
        except Exception:
            pass

    # ── Language toggle (top-right) ───────────────────────────────────────────
    _lang_icon = ft.IconButton(
        icon=ft.Icons.TRANSLATE_ROUNDED,
        icon_color=ft.Colors.ON_SURFACE_VARIANT,
        tooltip=t('app.tooltip_language_toggle'),
        on_click=lambda _e: _toggle_language(),
    )

    def _toggle_language() -> None:
        # 在简谱编辑子页时禁止切换（按钮已 disabled，这里再兜底一次）。
        if state.current_page == 'jianpu_edit':
            return
        state.toggle_language()  # 更新 state.language 并切换 gui.strings 的活动语言
        set_saved_language(state.language)  # 持久化，下次启动沿用
        # 重新本地化所有持久化控件：导航栏标签、标题栏 tooltip、各页面 retranslate()。
        for i, item in enumerate(_NAV_ITEMS):
            nav_rail.destinations[i].label = t(f'app.nav_{item[0]}')
        _theme_icon.tooltip = t('app.tooltip_theme_toggle')
        _lang_icon.tooltip = t('app.tooltip_language_toggle')
        _min_btn.tooltip = t('app.tooltip_minimize')
        _max_btn.tooltip = t('app.tooltip_maximize')
        _close_btn.tooltip = t('app.tooltip_close')
        _nav_label_toggle_btn.tooltip = (
            t('app.tooltip_hide_label') if _nav_labels_shown[0] else t('app.tooltip_show_label')
        )
        try:
            nav_rail.update()
        except Exception:
            pass
        for pg in (landing_page, audio_page, jianpu_preview_page, editor_page,
                   score_preview_page, transposer_page, about_page):
            try:
                pg.retranslate()
            except Exception:
                pass
        try:
            overlay.retranslate()
        except Exception:
            pass
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
        tooltip=t('app.tooltip_maximize'),
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

    _min_btn = ft.IconButton(
        icon=ft.Icons.REMOVE,
        icon_size=14,
        icon_color=ft.Colors.ON_SURFACE_VARIANT,
        tooltip=t('app.tooltip_minimize'),
        width=32,
        height=32,
        style=_wc_btn_style,
        on_click=lambda _: _do_minimize(),
    )
    _close_btn = ft.IconButton(
        icon=ft.Icons.CLOSE,
        icon_size=14,
        icon_color=ft.Colors.ON_SURFACE_VARIANT,
        tooltip=t('app.tooltip_close'),
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
        on_click=lambda _: page.run_task(_close_window),
    )

    async def _close_window() -> None:
        # 关闭窗口会让 Flutter 断开 WebSocket、销毁会话；若 close() 的远程调用
        # 在会话销毁之后才落地，invoke_method 会抛 "Session closed"。这是关闭时的
        # 良性竞态（窗口照常关闭），吞掉异常即可，避免退出时打印无意义的 traceback。
        try:
            await page.window.close()
        except Exception:
            pass

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
                                t('app.title_bar', version=APP_VERSION),
                                size=13,
                                font_family=FONT_EMPHASIS,
                                color=ft.Colors.ON_SURFACE,
                            ),
                        ],
                        spacing=0,
                    ),
                    expand=True,
                    maximizable=True,
                ),
                _lang_icon,
                _theme_icon,
                ft.Container(width=2),
                _min_btn,
                _max_btn,
                _close_btn,
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
            _show_snack(t('app.snack_error', message=message), Palette.ERROR)
        page.run_task(_do)

    def _on_done(message: str = t('app.snack_done_default'), **_kw) -> None:
        async def _do():
            _show_snack(message, Palette.SUCCESS)
            # 转换完成后刷新两个预览页（mtime 缓存键确保新 PDF 不会命中旧缓存）
            jianpu_preview_page.reload()
            score_preview_page.reload()
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
        threading.Thread(target=audio_page.terminate_worker, daemon=True).start()
        if sys.platform == 'win32':
            import ctypes
            # TerminateProcess 跳过 DLL_PROCESS_DETACH，比 os._exit 更快
            ctypes.windll.kernel32.TerminateProcess(-1, 0)
        else:
            _os._exit(0)

    page.on_close = _on_app_close

    # ── Initialisation complete ───────────────────────────────────────────────
    _show_page('landing')

    # 等待 Flutter 完成首帧绘制后再显示窗口，防止控件尚未渲染时窗口已可见。
    # FLET_HIDE_WINDOW_ON_START 在 Dart 层处理，50ms 确保 layout/paint 已完成。
    import asyncio as _asyncio
    await _asyncio.sleep(0.05)
    page.window.visible = True
    page.window.update()


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
            ok = patch_exe_resources(
                str(_exe), str(_ico),
                'SumisoraOMR', 'SumisoraOMR',
                'Tsukamotoshio', 'Copyright (C) 2026 Tsukamotoshio',
                *_app_version_tuple(),
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
            import subprocess as _sp
            _pid  = str(_Path(_tmp.gettempdir()) / _rstr(20))
            _env = {**_o.environ}
            _env['FLET_APP_USER_MODEL_ID'] = 'Tsukamotoshio.SumisoraOMR'
            _env['FLET_HIDE_WINDOW_ON_START'] = 'true'
            if _exe.exists():
                _args = [_exe_str, page_url, _pid]
                if assets_dir:
                    _args.append(assets_dir)
                # STARTUPINFO.wShowWindow = SW_HIDE: Win32 层面在 Dart 代码运行前
                # 即隐藏窗口，防止 Flutter runner 执行 ShowWindow(nCmdShow) 时白屏。
                _si = _sp.STARTUPINFO()
                _si.dwFlags = _sp.STARTF_USESHOWWINDOW
                _si.wShowWindow = 0  # SW_HIDE
                return (await _aio.create_subprocess_exec(
                    _args[0], *_args[1:], env=_env, startupinfo=_si,
                ), _pid)
            return await _orig_open(page_url, assets_dir, True)

        _fd.open_flet_view_async = _patched_open

    except Exception:
        pass  # 设置失败不影响应用启动，回退到默认 flet.exe


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    # 尽早安装全局异常钩子：任何后续启动步骤（互斥量、EXE 修补、ft.run）崩溃
    # 都能落日志并弹窗告知，而不是静默消失。
    _install_global_exception_handlers()

    if sys.platform == 'win32' and '--worker' not in sys.argv:
        import ctypes as _ctypes
        _ctypes.windll.kernel32.CreateMutexW(None, False, 'SumisoraOMR_RunningMutex')
        if _ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            _ctypes.windll.user32.MessageBoxW(
                0,
                t('app.single_instance_body'),
                t('app.single_instance_title'),
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
