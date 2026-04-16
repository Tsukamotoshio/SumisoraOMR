# core/engine_router.py — 智能 OMR 引擎路由模块
"""根据图像质量评分决定 OMR 处理策略。

路由策略
--------
score >= QUALITY_THRESHOLD (6.0)  →  "audiveris"
    图像质量良好，直接使用 Audiveris 进行 OMR 识别。

score <  QUALITY_THRESHOLD        →  "audiveris_with_dl_fix"
    图像质量不足，使用 Audiveris 进行主要识别，
    再叠加深度学习辅助修复层校正可能的识别错误。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .quality_score import QualityResult

# ──────────────────────────────────────────────────────────────────────────────
# 公共常量
# ──────────────────────────────────────────────────────────────────────────────

STRATEGY_DIRECT = "audiveris"
"""高质量路径：Audiveris 直接识别。"""

STRATEGY_DL_FIX = "audiveris_with_dl_fix"
"""低质量路径：Audiveris 识别 + 深度学习辅助修复。"""

QUALITY_THRESHOLD: float = 6.0
"""路由切换阈值（综合质量分）。"""


# ──────────────────────────────────────────────────────────────────────────────
# 公共接口
# ──────────────────────────────────────────────────────────────────────────────

def route_engine(score_info: 'QualityResult') -> str:
    """根据质量评分返回 OMR 处理策略字符串。

    Parameters
    ----------
    score_info : score_sheet_quality() 返回的 QualityResult 字典。

    Returns
    -------
    "audiveris"               —— score >= QUALITY_THRESHOLD
    "audiveris_with_dl_fix"   —— score <  QUALITY_THRESHOLD
    """
    score = float(score_info.get('score', 0.0))  # type: ignore[attr-defined]
    if score >= QUALITY_THRESHOLD:
        return STRATEGY_DIRECT
    return STRATEGY_DL_FIX


def describe_route(strategy: str, score: float) -> str:
    """返回人类可读的路由决策描述（用于日志输出）。"""
    if strategy == STRATEGY_DIRECT:
        return f'高质量图像（{score:.1f}/10）→ Audiveris 直接识别'
    return f'低质量图像（{score:.1f}/10）→ Audiveris 识别 + 深度学习辅助修复'
