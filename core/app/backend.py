from pathlib import Path
import os

from ..utils import get_app_base_dir


def app_base_dir() -> Path:
    return get_app_base_dir()


def editor_workspace_dir() -> Path:
    return ensure_dir(app_base_dir() / 'editor-workspace')


def xml_scores_dir() -> Path:
    return ensure_dir(app_base_dir() / 'xml-scores')


def models_dir() -> Path:
    """HOMR ONNX model files directory (created on demand)."""
    return ensure_dir(app_base_dir() / 'models')


def output_dir(output_text: str | None) -> Path:
    if output_text and output_text != '未指定（默认 Output/）':
        return Path(output_text)
    return ensure_dir(app_base_dir() / 'Output')


def build_dir() -> Path:
    return ensure_dir(app_base_dir() / 'build')


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(str(path))
