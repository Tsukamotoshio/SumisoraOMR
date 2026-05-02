# core/omr/engine_router.py — OMR engine router
"""Selects the OMR processing strategy based on image quality score.

Routing logic
-------------
score >= QUALITY_THRESHOLD (6.0)  →  "audiveris"
    Good image quality: run Audiveris directly.

score <  QUALITY_THRESHOLD        →  "audiveris_with_dl_fix"
    Poor image quality: run Audiveris then apply a DL correction pass.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..image.quality_score import QualityResult

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

STRATEGY_DIRECT = "audiveris"
"""High-quality path: Audiveris direct recognition."""

STRATEGY_DL_FIX = "audiveris_with_dl_fix"
"""Low-quality path: Audiveris recognition + DL correction pass."""

QUALITY_THRESHOLD: float = 6.0
"""Score threshold that switches between the two strategies."""


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def route_engine(score_info: 'QualityResult') -> str:
    """Return the OMR strategy string for the given quality result.

    Parameters
    ----------
    score_info : QualityResult dict returned by score_sheet_quality().

    Returns
    -------
    "audiveris"               — score >= QUALITY_THRESHOLD
    "audiveris_with_dl_fix"   — score <  QUALITY_THRESHOLD
    """
    score = float(score_info.get('score', 0.0))  # type: ignore[attr-defined]
    if score >= QUALITY_THRESHOLD:
        return STRATEGY_DIRECT
    return STRATEGY_DL_FIX


def describe_route(strategy: str, score: float) -> str:
    """Return a human-readable routing decision string for log output."""
    if strategy == STRATEGY_DIRECT:
        return f'高质量图像（{score:.1f}/10）→ Audiveris 直接识别'
    return f'低质量图像（{score:.1f}/10）→ Audiveris 识别 + 深度学习辅助修复'


# ──────────────────────────────────────────────────────────────────────────────
# PDF type detection: vector vs bitmap
# ──────────────────────────────────────────────────────────────────────────────

def is_pdf_vector(pdf_path: Path) -> bool:
    """Return True if the first page of the PDF is primarily vector graphics.

    Heuristic
    ---------
    Notation-software PDFs (MuseScore / Sibelius / Finale) are vector:
      many drawing paths (staves, noteheads, beams), zero or few embedded bitmaps.
    Scanned/photographed score PDFs are bitmap:
      one or more large embedded images (w > 300 px AND h > 300 px), few paths.

    Strategy: if the first page contains at least one large embedded image
    (width > 300 and height > 300), treat as bitmap; otherwise treat as vector.

    Requires PyMuPDF (fitz); defaults to True (vector) on ImportError or any exception.
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
