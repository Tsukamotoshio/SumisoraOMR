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

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..image.quality_score import QualityResult

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


# ──────────────────────────────────────────────────────────────────────────────
# PDF 类型检测：矢量 vs 位图
# ──────────────────────────────────────────────────────────────────────────────

def is_pdf_vector(pdf_path: Path) -> bool:
    """检测 PDF 第一页是否以矢量图形为主（适合 Audiveris 直接处理）。

    判断依据
    --------
    乐谱软件（MuseScore / Sibelius / Finale）导出的矢量 PDF：
      - 页面含大量绘图路径（staves、noteheads、beams 等 SVG-like paths）
      - 零个或极少数嵌入式位图
    扫描/拍照乐谱保存的位图 PDF：
      - 页面含 1 张（或数张）大尺寸嵌入位图（宽/高均 > 300 px）
      - 绘图路径极少

    策略：若第一页存在至少一张大型嵌入图像（宽度 > 300 且高度 > 300 像素），
    则视为位图 PDF；否则视为矢量 PDF。

    依赖 PyMuPDF（fitz）；若未安装或发生任何异常，默认返回 True（矢量路径）。
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logging.getLogger('convert').warning(
            '[engine_router] PyMuPDF (fitz) 未安装，无法检测 PDF 类型，默认按矢量处理。'
        )
        return True

    try:
        doc = fitz.open(str(pdf_path))
        if doc.page_count == 0:
            doc.close()
            return True
        page = doc[0]
        images = page.get_images(full=True)
        doc.close()

        # images 元素格式：(xref, smask, width, height, bpc, colorspace, ...)
        for img in images:
            img_w = img[2]
            img_h = img[3]
            if img_w > 300 and img_h > 300:
                # 发现大尺寸嵌入图像 → 位图 PDF
                return False

        # 无大尺寸嵌入图像 → 矢量 PDF
        return True

    except Exception as exc:
        logging.getLogger('convert').warning(
            f'[engine_router] PDF 类型检测异常，默认按矢量处理：{exc}'
        )
        return True
