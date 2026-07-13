# webui/main.py — M0 skeleton entry point.
"""Run the pywebview M0 verification shell.

    .venv/Scripts/python.exe -m webui.main             # interactive
    .venv/Scripts/python.exe -m webui.main --selftest  # automated checks, prints JSON, exits

Verifies the four M0 gate items from docs/pywebview-migration-plan.md §5:
frameless window + custom title bar, bridge echo, drag-drop full paths,
COOP/COEP isolation (SharedArrayBuffer available → noteDigger precondition).
"""
from __future__ import annotations

import json
import sys
import threading
import time

import webview
from webview.dom import DOMEventHandler

from .bridge import Bridge
from .server import start_server

WINDOW_TITLE = 'SumisoraOMR — pywebview M0'


def _on_drop(bridge: Bridge, e: dict) -> None:
    """DOM drop handler (Python side): extract real filesystem paths.

    Browsers never expose full paths to JS; pywebview adds ``pywebviewFullPath``
    to each dropped file, readable only from this Python-side DOM event. We
    forward the paths to the frontend as a CustomEvent for display / later
    routing into the Input/ flow.
    """
    try:
        files = (e.get('dataTransfer') or {}).get('files') or []
        paths = [f.get('pywebviewFullPath') for f in files if f.get('pywebviewFullPath')]
        bridge.push_event('files-dropped', paths)
    except Exception as exc:  # 拖拽是交互路径，出错不应崩壳
        bridge.push_event('files-dropped-error', str(exc))


def _bind_dom(window: webview.Window, bridge: Bridge) -> None:
    """Attach drag/drop DOM handlers once the DOM is ready.

    dragover/dragenter must be prevent_default'ed or the drop event never fires.
    """
    window.dom.document.events.dragenter += DOMEventHandler(lambda e: None, True, True)
    window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True, debounce=500)
    window.dom.document.events.drop += DOMEventHandler(lambda e: _on_drop(bridge, e), True, True)


def _selftest(window: webview.Window) -> None:
    """Poll the page's self-check state, print it as JSON, then close.

    The page writes ``window.__m0state`` after running its checks (bridge echo,
    crossOriginIsolated, SharedArrayBuffer, worker+SAB round-trip). Interactive
    items (drag-drop, title-bar drag/snap) can't be automated — human checklist.
    """
    deadline = time.time() + 20
    state = None
    while time.time() < deadline:
        try:
            state = window.evaluate_js('window.__m0state ? JSON.stringify(window.__m0state) : null')
        except Exception:
            state = None
        if state:
            parsed = json.loads(state)
            if all(k in parsed for k in ('bridgeEcho', 'crossOriginIsolated', 'sharedArrayBuffer', 'workerSab')):
                print('M0_SELFTEST ' + json.dumps(parsed, ensure_ascii=False), flush=True)
                break
        time.sleep(0.5)
    else:
        print('M0_SELFTEST {"error": "timeout waiting for __m0state"}', flush=True)
    window.destroy()


def main() -> None:
    selftest = '--selftest' in sys.argv

    _httpd, base_url = start_server()
    bridge = Bridge()
    window = webview.create_window(
        WINDOW_TITLE,
        url=f'{base_url}/index.html',
        js_api=bridge,
        width=1100,
        height=760,
        frameless=True,
        easy_drag=False,          # 拖动只在标题栏（pywebview-drag-region class）
        background_color='#101418',
        min_size=(760, 520),
    )
    bridge.attach(window)

    def _on_loaded() -> None:
        _bind_dom(window, bridge)
        if selftest:
            threading.Thread(target=_selftest, args=(window,), daemon=True).start()

    window.events.loaded += _on_loaded
    # WebView2（Edge Chromium）为 Windows 唯一目标后端；debug 开发期开启 F12。
    webview.start(gui='edgechromium', debug=not selftest)


if __name__ == '__main__':
    main()
