# tests/test_jianpu_lint_toasts.py — the editor's error toasts and export block
# (webui/static/js/editor.js ERROR_TOASTS) against the i18n catalog and the
# error codes webui/static/js/jianpu-lint.js actually produces (stage L1).
import pathlib
import re

from webui.i18n import merged_catalog

_JS = pathlib.Path(__file__).resolve().parent.parent / 'webui' / 'static' / 'js'


def _error_toasts():
    src = (_JS / 'editor.js').read_text(encoding='utf-8')
    block = re.search(r'const ERROR_TOASTS = \{(.*?)\n\};', src, re.S)
    assert block, 'ERROR_TOASTS not found in editor.js -- renamed? update this test'
    entries = {}
    for m in re.finditer(r"(?:'([a-z-]+)'|(generic)):\s*\{(.*?)\}", block.group(1), re.S):
        entries[m.group(1) or m.group(2)] = dict(re.findall(r"(one|many|blocked): '([^']+)'", m.group(3)))
    return entries


def test_every_error_toast_renders_in_both_languages():
    # A key missing from the catalog shows the raw key; a placeholder left
    # unfilled shows "{token}". The browser harness serves no strings table,
    # so the wording is pinned here.
    entries = _error_toasts()
    assert {'generic', 'crosses-barline'} <= entries.keys(), entries.keys()
    catalog = merged_catalog()
    params = {'one': {'line': 7, 'token': '4.'}, 'many': {'n': 2}, 'blocked': {}}
    for code, keys in entries.items():
        assert set(keys) == {'one', 'many', 'blocked'}, (code, keys)
        for kind, key in keys.items():
            assert key in catalog, f'{key} missing from the catalog'
            for lang in ('zh', 'en'):
                out = catalog[key][lang]
                for name, value in params[kind].items():
                    out = out.replace('{' + name + '}', str(value))
                assert '{' not in out, f'{key} [{lang}] left a placeholder: {out}'


def test_every_error_the_linter_raises_has_fitting_words():
    # Codes with their own entry get their own wording. The rest fall back to
    # "invalid token", which is only true of tokens the linter does not know --
    # so any other error code must be registered, or it would be described wrongly.
    lint_src = (_JS / 'jianpu-lint.js').read_text(encoding='utf-8')
    codes = set(re.findall(r"severity: 'error',\s*code: '([a-z-]+)'", lint_src))
    codes |= set(re.findall(r"pushAt\(w, 'error', '([a-z-]+)'", lint_src))
    assert 'crosses-barline' in codes, codes
    registered = _error_toasts().keys()
    invalid_token_kinds = {'bad-token', 'excluded-token'}
    for code in codes:
        assert code in registered or code in invalid_token_kinds, \
            f'{code} would be shown as "invalid token"; register it in ERROR_TOASTS'
