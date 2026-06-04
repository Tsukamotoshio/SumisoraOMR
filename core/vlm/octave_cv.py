"""Deterministic CV octave-dot detection for jianpu measures.

The VLM reads digits/durations reliably but frequently mis-reads the small octave
dots above/below digits (and hallucinates them under prompt pressure). This module
detects octave dots with plain image processing instead, then the caller overrides
the VLM's `oct` field.

Anchoring: the caller passes `n_expected` (the VLM's note count for the measure).
We pick exactly that many of the most digit-like glyphs, which sidesteps the
unstable measure→glyph segmentation (accidentals, duration dots, beams, dashes,
barline slivers all confound naive projection).

Tuned on image_test0; thresholds are mostly relative (per-measure percentiles) so
they adapt to scale, but may need revisiting for very different jianpu renderings.
"""
from __future__ import annotations

import numpy as np

_DARK_THRESH = 160     # 灰度 < 此值视为内容（暗）。黑字白底
_COL_GAP_MIN = 2       # 列方向空白 >= 此宽度视为字形分隔
_ROW_GAP_MIN = 2       # 行方向空白 >= 此高度视为上下点与主体分隔
_DOT_MIN_PX = 3        # 一个点至少这么多暗像素，过滤噪点
_BARLINE_AR = 4.0      # 高/宽 > 此值且接近满高 → 竖线，跳过


def _row_clusters(rows: np.ndarray, gap_min: int) -> list[tuple[int, int]]:
    """把有暗像素的行号聚成连续簇（簇间空白 >= gap_min）。"""
    if len(rows) == 0:
        return []
    out = []
    start = prev = rows[0]
    for r in rows[1:]:
        if r - prev > gap_min:
            out.append((start, prev))
            start = r
        prev = r
    out.append((start, prev))
    return out


def _body_xcentroid(dark, x0, x1, body) -> tuple[float, float]:
    """主体行簇的水平质心与宽度（判断点是否居中对齐数字主体）。"""
    sub = dark[body[0]:body[1] + 1, x0:x1]
    colsum = sub.sum(axis=0)
    cols = np.where(colsum > 0)[0]
    if len(cols) == 0:
        return (x0 + x1) / 2.0, float(x1 - x0)
    cx = x0 + float((cols * colsum[cols]).sum() / colsum[cols].sum())
    return cx, float(cols[-1] - cols[0] + 1)


def _dots_octave(dark, x0, x1, clusters, body, ref_h, cx, body_w) -> int:
    """对一个数字块，返回八度偏移（上点数 - 下点数，各封顶 ±2）。"""
    up = down = 0
    for (c0, c1) in clusters:
        if (c1 - c0 + 1) > 0.4 * ref_h:
            continue                       # 太大，是主体，不是点
        sub = dark[c0:c1 + 1, x0:x1]
        if sub.sum() < _DOT_MIN_PX:
            continue
        cols = np.where(sub.sum(axis=0) > 0)[0]
        dot_cx = x0 + float(cols.mean())
        if abs(dot_cx - cx) > 0.45 * body_w:
            continue                       # 偏离主体中心，多半是升降号(在左侧)
        if c1 < body[0]:
            up += 1
        elif c0 > body[1]:
            down += 1
    return min(up, 2) - min(down, 2)


def detect_octaves(img, n_expected: int | None = None) -> list[int]:
    """Detect the octave offset of each note in a single-measure jianpu image.

    Args:
        img: PIL image of ONE measure (already cropped + upscaled).
        n_expected: the VLM's note count (rests included, sustain dashes excluded).
            When given, only the `n_expected` most digit-like glyphs are kept, in
            left-to-right order — this is the anchoring that makes detection stable.

    Returns a list of octave offsets (e.g. +1, -1, 0), one per kept glyph, left→right.
    Returns [] if nothing usable is found (caller should keep the VLM's octaves).
    """
    g = np.array(img.convert('L'))
    H, W = g.shape
    dark = g < _DARK_THRESH

    # 1) 列投影切字形块（允许 < _COL_GAP_MIN 的小缝隙不断开）
    colmask = dark.sum(axis=0) > 0
    blocks = []
    x = 0
    while x < W:
        if colmask[x]:
            x0 = x
            gap = 0
            while x < W and (colmask[x] or gap < _COL_GAP_MIN):
                gap = 0 if colmask[x] else gap + 1
                x += 1
            blocks.append((x0, x - gap))
        else:
            x += 1

    # 2) 每块：行簇 + 主体（最高簇）+ 几何；过滤竖线
    parsed = []
    for (x0, x1) in blocks:
        rc = np.where(dark[:, x0:x1].sum(axis=1) > 0)[0]
        clusters = _row_clusters(rc, _ROW_GAP_MIN)
        if not clusters:
            continue
        body = max(clusters, key=lambda c: c[1] - c[0])
        bh = body[1] - body[0] + 1
        bw = x1 - x0
        if bw > 0 and bh / bw > _BARLINE_AR and bh > 0.8 * H:
            continue
        cx, body_w = _body_xcentroid(dark, x0, x1, body)
        parsed.append((x0, x1, clusters, body, bh, body_w, cx))

    if not parsed:
        return []
    ref_h = sorted(p[4] for p in parsed)[int(0.8 * (len(parsed) - 1))]

    # 3) 选数字块：给定 N 取 digit-likeness(高×宽) 最高的 N 个；否则按尺寸阈值
    if n_expected is not None and n_expected > 0:
        ranked = sorted(parsed, key=lambda p: p[4] * p[5], reverse=True)[:n_expected]
        chosen = sorted(ranked, key=lambda p: p[0])
    else:
        ref_w = sorted(p[5] for p in parsed)[int(0.8 * (len(parsed) - 1))]
        chosen = [p for p in parsed if p[4] >= 0.5 * ref_h and p[5] >= 0.45 * ref_w]

    return [_dots_octave(dark, p[0], p[1], p[2], p[3], ref_h, p[6], p[5]) for p in chosen]


def override_octaves(measure_notes: list, img) -> None:
    """In-place: replace each note's `oct` with the CV-detected octave.

    `measure_notes` is one measure's note list (dicts). Sustain-dash tokens
    ({"p":"-"}) are skipped. Safe no-op if CV finds nothing or on any error.
    """
    try:
        n = sum(1 for nt in measure_notes
                if isinstance(nt, dict) and str(nt.get('p', '')).strip() != '-')
        if n == 0:
            return
        octs = detect_octaves(img, n)
        if not octs:
            return
        k = 0
        for nt in measure_notes:
            if not isinstance(nt, dict) or str(nt.get('p', '')).strip() == '-':
                continue
            if k < len(octs):
                nt['oct'] = octs[k]
            k += 1
    except Exception:
        pass
