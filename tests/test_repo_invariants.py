"""Repository rules that used to live only in CLAUDE.md, checked mechanically.

Each test guards a mistake that has already happened once or can only be
caught by reading the whole tree; see REVIEW.md.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_utf8_batch_scripts_are_ascii():
    """A .bat that switches to ``chcp 65001`` must stay pure ASCII.

    cmd 在 65001 代码页下按字节偏移追踪行位置，多字节字符会让后续行从
    命令中间被截断，脚本随即崩溃。
    """
    offenders = []
    for bat in sorted((ROOT / 'scripts').glob('*.bat')):
        data = bat.read_bytes()
        if b'chcp 65001' not in data:
            continue
        for lineno, line in enumerate(data.splitlines(), 1):
            if any(b > 0x7F for b in line):
                offenders.append(f'{bat.name}:{lineno}')
    assert not offenders, f'non-ASCII bytes in chcp 65001 scripts: {offenders}'


# 允许调用 evaluate_js 的位置：(文件, 所在函数)。events.py 是唯一的推送通道；
# main.py 的两处只在 --selftest / gate 自检驱动里运行。
_EVALUATE_JS_ALLOWED = {
    ('webui/events.py', '_flush'),
    ('webui/main.py', '_js_flags'),
    ('webui/main.py', '_selftest'),
}


def _evaluate_js_calls(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = func
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr == 'evaluate_js'):
                found.append((func, child.lineno))
            visit(child, name)

    visit(tree, '<module>')
    return found


def test_evaluate_js_only_through_event_pusher():
    """Python→JS traffic goes through ``EventPusher``; a stray ``evaluate_js``
    from a background thread races WebView2."""
    unexpected = []
    for path in sorted((ROOT / 'webui').rglob('*.py')):
        rel = path.relative_to(ROOT).as_posix()
        for func, lineno in _evaluate_js_calls(path):
            if (rel, func) not in _EVALUATE_JS_ALLOWED:
                unexpected.append(f'{rel}:{lineno} in {func}()')
    assert not unexpected, (
        'evaluate_js outside EventPusher; push an event via webui/events.py instead: '
        f'{unexpected}'
    )
