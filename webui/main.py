# webui/main.py — pywebview shell entry point (M0 skeleton + M1 conversion artery).
"""Run the pywebview shell.

    .venv/Scripts/python.exe -m webui.main             # interactive harness
    .venv/Scripts/python.exe -m webui.main --selftest  # automated M0+Gate1 checks, prints JSON, exits

M0: frameless window / bridge echo / drag-drop real paths / COOP+COEP isolation.
M1: batched event artery (EventPusher) + file tray + ConversionRunner wiring
    (progress / logs / cancel / crash surfacing / close-time clean sweep).
"""
from __future__ import annotations

import os
import sys


def _bootstrap_venv() -> None:
    """Re-exec with the project .venv interpreter unless already running from it.

    Mirrors app.py's bootstrap so ``python -m webui.main`` "just works" from any
    interpreter. Must run **before** ``import webview`` — pywebview lives in the
    .venv, so under a system Python the top-level import would crash first.

    Unlike app.py (a top-level script), this module uses relative imports, so it
    must be re-launched as ``-m webui.main`` — never as a bare file path, which
    would strip the package context and break ``from .bridge import ...``.
    """
    if getattr(sys, 'frozen', False):
        return  # 打包版：依赖已捆绑，无 venv 概念
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根 = webui/ 的上级
    for _rel in (('.venv', 'Scripts', 'python.exe'), ('.venv', 'bin', 'python')):
        _py = os.path.join(_root, *_rel)
        if not os.path.isfile(_py):
            continue
        if os.path.normcase(os.path.abspath(sys.executable)) == os.path.normcase(_py):
            return  # 已经在项目 venv 里
        import subprocess
        print('[启动] 检测到当前不是项目 .venv 解释器，切换到 .venv 运行（壳启动中，请稍候）…',
              file=sys.stderr, flush=True)
        try:
            # 以模块方式重启（保留 --selftest / --gateN 等参数），保住相对导入的包上下文
            sys.exit(subprocess.run([_py, '-m', 'webui.main'] + sys.argv[1:]).returncode)
        except KeyboardInterrupt:
            sys.exit(130)
    # 项目 .venv 不存在 —— 回退到依赖可用性检测（允许用户自备环境）
    try:
        import webview  # noqa: F401, PLC0415
        return
    except ImportError:
        print(
            '\n[错误] 未找到虚拟环境或 pywebview 未安装。\n'
            '  pip install -r requirements.txt\n',
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == '__main__':
    _bootstrap_venv()

# frozen 启动：certifi SSL 回退 + console=False 的 null 流保护 + ONNX/OpenMP 线程封顶。
# 必须在 import webview / onnxruntime 之前（与 app.py 的打包版行为一致）。
from core.app.startup import early_frozen_setup
early_frozen_setup()

import json
import threading
import time

import webview
from webview.dom import DOMEventHandler

from .bridge import Bridge
from .conversion import ConversionService
from .editor import EditorService
from .events import EventPusher
from .models import ModelsService
from .outputs import OutputsService, ScoresService
from .server import start_server
from .transpose import TransposeService

WINDOW_TITLE = 'SumisoraOMR — pywebview shell'

# Windows taskbar identity — same AppUserModelID as the Flet shell (app.py) so
# both share one taskbar group / pinned-shortcut identity during the migration.
APP_USER_MODEL_ID = 'Tsukamotoshio.SumisoraOMR'


def _asset_path(*parts: str) -> str:
    """Resolve an assets/ file for dev (repo root) and frozen (_MEIPASS) builds."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, 'assets', *parts)


def _setup_windows_identity() -> None:
    """Set the explicit AppUserModelID so the taskbar shows our app, not Python.

    pywebview hosts the window in *this* process (WinForms BrowserForm), so —
    unlike the Flet shell, which needed a whole flet.exe rename/resource patch —
    a single ctypes call plus the ``icon=`` passed to ``webview.start`` gives the
    correct taskbar icon and right-click app name. Dev mode still runs under
    python.exe, so the pinned-taskbar exe icon is Python's; the window/taskbar
    icon and grouping identity are ours. Release builds get the exe icon from the
    frozen SumisoraOMR.exe + Inno Setup shortcut (M5-④).
    """
    if sys.platform != 'win32':
        return
    try:
        import ctypes  # noqa: PLC0415
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass  # 身份设置失败不应阻断启动


def _on_drop(bridge: Bridge, conversion: ConversionService, e: dict) -> None:
    """DOM drop handler (Python side): real filesystem paths → file tray.

    Browsers never expose full paths to JS; pywebview adds ``pywebviewFullPath``
    to each dropped file, readable only from this Python-side DOM event.
    """
    try:
        files = (e.get('dataTransfer') or {}).get('files') or []
        paths = [f.get('pywebviewFullPath') for f in files if f.get('pywebviewFullPath')]
        bridge.push_event('files-dropped', paths)   # M0 验证卡片仍显示原始路径
        if paths:
            conversion.files_add(paths)             # M1: 直接进文件托盘
    except Exception as exc:  # 拖拽是交互路径，出错不应崩壳
        bridge.push_event('files-dropped-error', str(exc))


def _bind_dom(window: webview.Window, bridge: Bridge, conversion: ConversionService) -> None:
    """Attach drag/drop DOM handlers once the DOM is ready.

    dragover/dragenter must be prevent_default'ed or the drop event never fires.
    """
    # pywebview 的事件订阅就是 += DOMEventHandler（文档用法）；Pylance 对其注解误报
    window.dom.document.events.dragenter += DOMEventHandler(lambda _e: None, True, True)  # type: ignore[operator]
    window.dom.document.events.dragover += DOMEventHandler(lambda _e: None, True, True, debounce=500)  # type: ignore[operator]
    window.dom.document.events.drop += DOMEventHandler(  # type: ignore[operator]
        lambda e: _on_drop(bridge, conversion, e), True, True)


def _pid_alive(pid: int) -> bool:
    """True if *pid* is a live process (Win32 exit-code probe, no psutil)."""
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return code.value == STILL_ACTIVE


def _child_pids(pid: int) -> list[int]:
    """Direct child PIDs of *pid* (worker 树核验用)."""
    import subprocess
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             f'(Get-CimInstance Win32_Process -Filter "ParentProcessId={pid}").ProcessId'],
            capture_output=True, text=True, timeout=30,
        )
        return [int(x) for x in r.stdout.split() if x.strip().isdigit()]
    except Exception:
        return []


def _js_flags(window: webview.Window) -> dict:
    try:
        raw = window.evaluate_js('JSON.stringify(window.__uiFlags || null)')
        parsed = json.loads(raw) if raw else None
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _gate_driver(window: webview.Window, conversion, mode: str, file_path: str) -> None:
    """Drive gates 2/3/4 against a REAL conversion.

    Calls ConversionService directly (the same code the bridge dispatches to;
    JS→Python marshalling is already proven by echo/Gate1) and verifies both
    the process side (worker tree) and the frontend side (__uiFlags via JS).
    """
    result: dict = {'gate': mode, 'file': file_path}
    try:
        time.sleep(2)  # 等页面/pusher 就绪
        conversion.files_add([file_path])
        started = conversion.convert_start({'engine': 'auto'})
        result['start'] = started

        # 等 worker 进程出现 + 前端确实在收进度事件
        deadline = time.time() + 60
        pids: list[int] = []
        while time.time() < deadline:
            pids = conversion.worker_pids()
            if pids and _js_flags(window).get('progressEvents', 0) > 2:
                break
            time.sleep(1)
        result['worker_pids'] = pids
        if not pids:
            result['error'] = 'worker never appeared'
            raise SystemExit
        time.sleep(8)  # 让转换进入实质阶段（引擎已启动/子进程已派生）
        tree = pids + [c for p in pids for c in _child_pids(p)]
        result['tree_before'] = tree

        if mode == 'happy':
            # 快乐路径：等一次真实转换完整跑完，summary 应回流前端
            deadline = time.time() + 420
            flags: dict = {}
            while time.time() < deadline:
                flags = _js_flags(window)
                if flags.get('summary'):
                    break
                time.sleep(2)
            summary = flags.get('summary') or {}
            result['summary'] = summary
            result['log_lines_received'] = flags.get('progressEvents')
            result['ok'] = (summary.get('total', 0) >= 1
                            and flags.get('busy') is False)
        elif mode == 'gate2':
            conversion.convert_cancel()
            time.sleep(6)
            result['tree_alive_after'] = [p for p in tree if _pid_alive(p)]
            flags = _js_flags(window)
            result['ui_busy_after'] = flags.get('busy')
            result['ok'] = not result['tree_alive_after'] and flags.get('busy') is False
        elif mode == 'gate3':
            conversion.debug_kill_worker()
            deadline = time.time() + 20
            flags = {}
            while time.time() < deadline:
                flags = _js_flags(window)
                if flags.get('lastError'):
                    break
                time.sleep(1)
            result['ui_error'] = flags.get('lastError')
            result['ui_busy_after'] = flags.get('busy')
            result['ok'] = bool(flags.get('lastError')) and flags.get('busy') is False
        elif mode == 'gate4':
            # 转换进行中直接销毁窗口；closed 事件应触发清场。
            # 存活核验在 webview.start() 返回后的主线程做（见 main()）。
            window.destroy()
            result['ok'] = None  # 由 main() 收尾判定
    except SystemExit:
        result['ok'] = False
    except Exception as exc:
        result['error'] = str(exc)
        result['ok'] = False
    finally:
        _GATE_RESULT.update(result)
        if mode != 'gate4':
            try:
                conversion.shutdown()
                window.destroy()
            except Exception:
                pass


_GATE_RESULT: dict = {}


def _selftest(window: webview.Window) -> None:
    """Poll the page's self-check state (M0 four + Gate1 flood), print JSON, close."""
    deadline = time.time() + 40
    state = None
    while time.time() < deadline:
        try:
            state = window.evaluate_js('window.__m0state ? JSON.stringify(window.__m0state) : null')
        except Exception:
            state = None
        if state:
            parsed = json.loads(state)
            required = ('bridgeEcho', 'crossOriginIsolated', 'sharedArrayBuffer', 'workerSab', 'gate1Flood')
            if all(k in parsed for k in required):
                print('SELFTEST ' + json.dumps(parsed, ensure_ascii=False), flush=True)
                break
        time.sleep(0.5)
    else:
        print('SELFTEST {"error": "timeout waiting for __m0state"}', flush=True)
    window.destroy()


def main() -> None:
    # SSL：走系统证书库（truststore），修复 MITM 代理 / 本地根证书验证失败。
    # 必须在任何下载（模型权重等）创建 SSL 上下文之前注入。
    from core.app.ssl_setup import setup_system_ssl
    setup_system_ssl()

    # Windows 任务栏身份（图标 + 分组 + 右键应用名）—— 必须在建窗前设置
    _setup_windows_identity()

    selftest = '--selftest' in sys.argv
    gate_mode = next((a for a in sys.argv if a in ('--happy', '--gate2', '--gate3', '--gate4')), None)
    gate_file = sys.argv[sys.argv.index(gate_mode) + 1] if gate_mode else None

    # 启动时恢复持久化语言（与 Flet 版共用 ui-settings.json / gui.strings 状态）
    from gui.settings import get_saved_language
    from gui.strings import set_language, t
    set_language(get_saved_language())

    # 单实例：已有实例在运行则提示并退出（selftest/gate 自动化不受此限）
    if not (selftest or gate_mode):
        from core.app.startup import acquire_single_instance
        if not acquire_single_instance():
            if sys.platform == 'win32':
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, t('app.single_instance_body'), t('app.single_instance_title'), 0x30)
            sys.exit(0)

    _httpd, base_url, whitelist = start_server()
    pusher = EventPusher()
    conversion = ConversionService(pusher, whitelist)
    models = ModelsService(pusher)
    outputs = OutputsService(pusher, whitelist)
    scores = ScoresService(pusher, whitelist)
    transpose = TransposeService(pusher, whitelist)
    editor = EditorService(pusher, whitelist)
    bridge = Bridge(pusher, conversion, models, outputs, scores, transpose, editor)
    # selftest / gate 驱动跑在 M1 测试台（harness.html）上，正式 UI 在 index.html
    page = 'harness.html' if (selftest or gate_mode) else 'index.html'
    window = webview.create_window(
        WINDOW_TITLE,
        url=f'{base_url}/{page}',
        js_api=bridge,
        width=1180,
        height=800,
        frameless=True,
        easy_drag=False,          # 拖动只在标题栏（pywebview-drag-region class）
        background_color='#101418',
        min_size=(760, 520),
    )
    assert window is not None  # create_window 注解为 Optional，实际必返回实例
    bridge.attach(window)
    pusher.attach(window)

    _loaded_once = threading.Event()

    def _on_loaded() -> None:
        # loaded 可能触发多次（导航/刷新）；驱动线程与 DOM 绑定只做一次
        if _loaded_once.is_set():
            return
        _loaded_once.set()
        pusher.start()
        _bind_dom(window, bridge, conversion)
        if selftest:
            threading.Thread(target=_selftest, args=(window,), daemon=True).start()
        elif gate_mode:
            threading.Thread(
                target=_gate_driver, args=(window, conversion, gate_mode.lstrip('-'), gate_file),
                daemon=True).start()

    def _on_closed() -> None:
        # Gate 4：关窗时转换未结束 → 与 Flet 壳一致的清场（taskkill /F /T worker 树）
        conversion.shutdown()
        pusher.stop()

    window.events.loaded += _on_loaded
    window.events.closed += _on_closed
    # WebView2（Edge Chromium）为 Windows 唯一目标后端。
    # debug=True 保留浏览器快捷键（Ctrl+R 刷新）、右键菜单与 F12——pywebview 把这些
    # 全绑在 debug 上（AreBrowserAcceleratorKeysEnabled = debug）。但默认不自动打开
    # DevTools：DevTools 停靠打开时 WebView2 会在拖动 resize 时于右上角叠加视口像素
    # 浮层遮挡视线。需要一进来就开 DevTools 调试时设 WEBUI_DEVTOOLS=1。
    headless_run = selftest or bool(gate_mode)
    webview.settings['OPEN_DEVTOOLS_IN_DEBUG'] = os.environ.get('WEBUI_DEVTOOLS') == '1'
    _icon = _asset_path('icon.ico')
    webview.start(
        gui='edgechromium',
        debug=not headless_run,
        icon=_icon if os.path.isfile(_icon) else None,
    )

    if gate_mode:
        # gate4：窗口销毁后在这里核验 worker 树是否被 closed 清场杀干净
        if gate_mode == '--gate4':
            time.sleep(6)
            tree = _GATE_RESULT.get('tree_before', [])
            alive = [p for p in tree if _pid_alive(p)]
            _GATE_RESULT['tree_alive_after'] = alive
            _GATE_RESULT['ok'] = bool(tree) and not alive
        # ensure_ascii：结果可能含中文（错误文案），GBK 控制台直接打印会 UnicodeEncodeError
        print('GATE_RESULT ' + json.dumps(_GATE_RESULT, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
