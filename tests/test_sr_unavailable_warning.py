# tests/test_sr_unavailable_warning.py — the "no SR engine at all" path must be loud.
#
# 背景：2026-08 两个 ncnn-vulkan 可执行文件同时丢失，而降级链路只在各自函数里
# 打了一条 WARNING（"Real-ESRGAN 失败，回退到 waifu2x…" / "未找到 waifu2x…"），
# 读起来像"已经切到另一个引擎"，实际两个都没跑成。缺失状态潜伏近一周，期间
# 所有转换都在用未放大的原图。这些用例锁住"两个都不可用时必须明确报错"。
import logging
from pathlib import Path

import pytest

from core.image import sr_upscale


@pytest.fixture(autouse=True)
def _reset_hint_flag():
    sr_upscale._sr_unavailable_hint_shown = False
    yield
    sr_upscale._sr_unavailable_hint_shown = False


def _capture(monkeypatch) -> list[tuple[str, int]]:
    records: list[tuple[str, int]] = []
    monkeypatch.setattr(
        sr_upscale, 'log_message',
        lambda msg, level=logging.INFO: records.append((str(msg), level)),
    )
    return records


def _no_engines(monkeypatch) -> None:
    monkeypatch.setattr(sr_upscale, 'find_realesrgan_executable', lambda: None)
    monkeypatch.setattr(sr_upscale, 'find_waifu2x_executable', lambda: None)
    monkeypatch.setattr(sr_upscale, '_find_realesrgan_python_script', lambda: None)


def test_reports_error_when_no_engine_is_available(monkeypatch, tmp_path):
    records = _capture(monkeypatch)
    _no_engines(monkeypatch)
    sr_upscale.set_sr_engine('realesrgan')

    assert sr_upscale.upscale_image(Path('in.png'), tmp_path / 'out.png') is False

    errors = [m for m, lvl in records if lvl == logging.ERROR]
    assert errors, f'两个引擎都不可用却没有 ERROR 级日志: {records}'
    assert any('超分辨率未生效' in m for m in errors)
    # 必须点名两个引擎，避免再次读成"已回退到另一个"
    joined = '\n'.join(errors)
    assert 'Real-ESRGAN' in joined and 'waifu2x' in joined


def test_hint_is_printed_only_once_per_process(monkeypatch, tmp_path):
    records = _capture(monkeypatch)
    _no_engines(monkeypatch)
    sr_upscale.set_sr_engine('realesrgan')

    for i in range(3):
        sr_upscale.upscale_image(Path('in.png'), tmp_path / f'out{i}.png')

    hints = [m for m, lvl in records if lvl == logging.ERROR and '未找到可执行文件' in m]
    # 两个引擎各一条提示，且只在第一次失败时输出
    assert len(hints) == 2, f'期望一次性提示 2 条，实际 {len(hints)}: {hints}'
    headlines = [m for m, lvl in records if lvl == logging.ERROR and '超分辨率未生效' in m]
    assert len(headlines) == 3, '每次失败都应有一条主报错'


def test_waifu2x_only_selection_still_reports(monkeypatch, tmp_path):
    """waifu2x 单独选中时缺失，也要走同一条明确报错路径。"""
    records = _capture(monkeypatch)
    _no_engines(monkeypatch)
    sr_upscale.set_sr_engine('waifu2x')

    assert sr_upscale.upscale_image(Path('in.png'), tmp_path / 'out.png') is False
    assert any('超分辨率未生效' in m for m, lvl in records if lvl == logging.ERROR)
