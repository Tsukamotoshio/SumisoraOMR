# gui/settings.py — Lightweight persistence for GUI user preferences.
#
# Stores small UI preferences (currently just the chosen language) as JSON next
# to the other app-data files in app_base_dir(). Mirrors the conversion-history
# helpers in core/utils.py: best-effort, never raises on read/write failure.

from __future__ import annotations

import json
from typing import Any

from core.app.backend import app_base_dir
from core.utils import atomic_write_text

_SETTINGS_FILE = 'ui-settings.json'


def load_ui_settings() -> dict[str, Any]:
    """Return the saved UI preferences, or an empty dict if none/unreadable."""
    path = app_base_dir() / _SETTINGS_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_ui_settings(settings: dict[str, Any]) -> None:
    """Persist the UI preferences dict to disk (best-effort, silent on failure)."""
    path = app_base_dir() / _SETTINGS_FILE
    try:
        atomic_write_text(path, json.dumps(settings, ensure_ascii=False, indent=2))
    except OSError:
        pass


def get_saved_language(default: str = 'zh') -> str:
    """Return the persisted language ('zh' | 'en'), falling back to default."""
    lang = load_ui_settings().get('language')
    return lang if lang in ('zh', 'en') else default


def set_saved_language(language: str) -> None:
    """Persist the chosen language, preserving any other saved preferences."""
    settings = load_ui_settings()
    settings['language'] = language
    save_ui_settings(settings)
