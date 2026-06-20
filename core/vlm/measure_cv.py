"""Deterministic CV analysis of a single-measure jianpu chunk.

The VLM reads digit identities (and accidentals) reliably but mis-reads the
small geometric marks: octave dots, duration underlines, dotted-note dots and
sustain dashes. All of those are plain geometry on a LilyPond-rendered jianpu
image, so this module detects them with connected-component analysis and the
caller overrides the VLM fields / rebuilds the in-measure token sequence.

Detected per measure:
  - digit glyphs (incl. rest '0'), left-to-right, with accidentals merged in
  - octave dots above/below each digit  → oct offset
  - underline count below each digit    → duration q/e/s
  - small mid-height dot right of digit → dotted note
  - sustain dashes at body mid-height   → '-' tokens with x order preserved

Anchoring: the caller passes `n_expected` (the VLM's digit count for the
measure). We keep exactly that many of the most digit-like glyphs, which
sidesteps unstable segmentation. If CV cannot find that many digits the caller
falls back to the VLM's own fields.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

_DARK_THRESH = 160       # 灰度 < 此值视为内容（黑字白底）
_FLAT_AR = 3.0           # w/h >= 此值且矮 → 水平线（下划线或延音线）
_FLAT_MAX_H_FRAC = 0.10  # 水平线厚度上限（占图高比例）
_BARLINE_H_FRAC = 0.40   # 组件高占块高 >= 此比例 → 疑似含小节竖线
                         # （实测低音区小节的竖线 h≈0.44*H，0.45 阈值会漏擦，
                         #  残段把 ref_h 抬高、挤掉真数字 → 误判音符数）
_DOT_MAX_FRAC = 0.38     # 点的宽高上限（相对参考数字高）
_DOT_AR_LO, _DOT_AR_HI = 0.4, 2.6   # 点的宽高比范围（圆点≈1）
_MIN_COMP_PX = 4         # 连通域最少像素，过滤噪点
_ACC_MAX_W = 0.90        # 升降号宽度上限（相对参考数字宽）
_BAND_TOL = 0.62         # 数字主体 y 中心偏离带中心 <= 此比例*参考高 才算同一行


@dataclass
class _Comp:
    x0: int
    y0: int
    w: int
    h: int
    area: int

    @property
    def x1(self) -> int:
        return self.x0 + self.w

    @property
    def y1(self) -> int:
        return self.y0 + self.h

    @property
    def cx(self) -> float:
        return self.x0 + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y0 + self.h / 2.0


@dataclass
class DigitGlyph:
    """一个数字（含合并进来的升降号）及其检测出的记号。

    acc_char: CV 对左侧紧贴字形的升降号分类——'#'(升)、'b'(降)、
    ''(无或还原号 ♮；还原号在 jianpu 文本里就是裸数字)。
    """
    x0: int
    y0: int
    x1: int
    y1: int
    oct: int = 0
    underlines: int = 0
    dotted: bool = False
    acc_char: str = ''
    acc_w: int = 0          # 升降号组件宽（调试/调参用）
    acc_fill: float = 0.0   # 升降号填充率 area/(w*h)（调试/调参用）

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0


@dataclass
class MeasureCV:
    """单小节 CV 分析结果。events 按 x 升序：('note', DigitGlyph) / ('dash', x)。"""
    digits: list[DigitGlyph] = field(default_factory=list)
    dash_xs: list[float] = field(default_factory=list)

    def events(self) -> list[tuple[str, object]]:
        evs: list[tuple[float, str, object]] = []
        for d in self.digits:
            evs.append((d.cx, 'note', d))
        for x in self.dash_xs:
            evs.append((x, 'dash', x))
        evs.sort(key=lambda t: t[0])
        return [(kind, obj) for (_, kind, obj) in evs]


def _col_max_run(col: np.ndarray) -> int:
    """一列里最长连续 True 游程。"""
    if not col.any():
        return 0
    idx = np.flatnonzero(np.diff(np.concatenate(([0], col.view(np.int8), [0]))))
    runs = idx[1::2] - idx[0::2]
    return int(runs.max())


def _erase_barlines(dark: np.ndarray) -> np.ndarray:
    """组件级抹掉小节竖线，返回新 mask。

    两类：
      1. 纯竖线组件（很高且窄）→ 整个组件清除。
      2. 混合组件：时值下划线一直延伸到竖线旁并相连，连通域把
         「下划线+竖线」合成一个又高又宽的组件 → 只清除其中
         垂直游程接近组件全高的列，下划线即与竖线分离。
    高度阈值用块高 H 的比例：数字主体 <0.35H，竖线（含上下余量）≥0.45H。
    """
    import cv2
    H, W = dark.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats(
        dark.astype(np.uint8), connectivity=8)
    out = dark.copy()
    for i in range(1, n):
        x, y, w, h = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                      int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]))
        if h < _BARLINE_H_FRAC * H:
            continue
        mask = labels[y:y + h, x:x + w] == i
        if w <= max(10, 0.12 * h):
            out[y:y + h, x:x + w][mask] = False          # 纯竖线
            continue
        # 混合组件：逐列查游程，抹掉接近全高的列
        sub = out[y:y + h, x:x + w]
        for cx in range(w):
            col = np.where(mask[:, cx], sub[:, cx], False)
            if _col_max_run(col) >= 0.8 * h:
                sub[:, cx][mask[:, cx]] = False
    return out


def _components(dark: np.ndarray) -> list[_Comp]:
    import cv2
    n, _, stats, _ = cv2.connectedComponentsWithStats(
        dark.astype(np.uint8), connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = (int(stats[i, cv2.CC_STAT_LEFT]),
                            int(stats[i, cv2.CC_STAT_TOP]),
                            int(stats[i, cv2.CC_STAT_WIDTH]),
                            int(stats[i, cv2.CC_STAT_HEIGHT]),
                            int(stats[i, cv2.CC_STAT_AREA]))
        if area >= _MIN_COMP_PX:
            out.append(_Comp(x, y, w, h, area))
    return out


def _classify_accidental(acc: _Comp, denom_w: float) -> str:
    """把紧贴数字左侧的升降号组件分类为 '#'（升）或 'n'（还原 ♮）。

    LilyPond 简谱字体里宽度差异稳定（image_test0 实测，相对数字宽）：
      ♯  0.65–0.69（两竖两横，宽）
      ♮  0.45–0.47（两竖错位，窄）
    TODO: ♭（降号）在现有测试谱中未出现，暂归入 'n'；遇到降号谱
    需要补充形状特征（♭ 上半窄下半宽）。
    """
    rel_w = acc.w / max(1.0, denom_w)
    return '#' if rel_w >= 0.55 else 'n'


def _detect_timesig_cutoff(cands: list[_Comp], H: int) -> float:
    """块 0 含拍号（上下叠放的两个大数字）和 '1=X' 调号文本。

    找垂直堆叠对：x 重叠 >= 60%、两者都较大、合计高度接近内容高 → 拍号。
    返回其右边缘 + 余量；CV 忽略其左侧的一切（调号文本在拍号左上方）。
    """
    big = [c for c in cands if c.h >= 0.22 * H]
    for a in big:
        for b in big:
            if b.y0 <= a.y0 or b is a:
                continue
            ox = min(a.x1, b.x1) - max(a.x0, b.x0)
            if ox < 0.6 * min(a.w, b.w):
                continue
            if b.y0 - a.y1 > 0.25 * H:
                continue
            # 用堆叠对的总纵跨（上字形顶 → 下字形底）判定，而非两高之和：
            # 实测 4/4 拍号两个 '4' 总跨 ~0.45*H；旧的 0.55*H 阈值漏判，
            # 导致 '1=G' 调号文本与拍号数字漏进首小节被当成音符。
            if (b.y1 - a.y0) >= 0.40 * H:
                return max(a.x1, b.x1) + 0.02 * H
    return -1.0


def analyze_measure(img, n_expected: int) -> MeasureCV | None:
    """Analyze ONE measure image. Returns None when CV cannot anchor reliably.

    Args:
        img: PIL image of one measure chunk (cropped + upscaled by caller).
        n_expected: VLM's digit count for this measure (rests included,
            sustain dashes excluded). CV keeps exactly this many digit glyphs;
            returns None if it cannot. Pass -1 to keep ALL in-band glyphs
            (unanchored mode, used by count_digits).
    """
    if n_expected == 0 or n_expected < -1:
        return None
    g = np.array(img.convert('L'))
    H, W = g.shape
    dark = _erase_barlines(g < _DARK_THRESH)
    comps = _components(dark)
    if not comps:
        return None

    # ── 1. 粗分类：水平线（flat）/ 字形候选（竖线已在 mask 阶段抹掉） ────
    flats: list[_Comp] = []
    glyphs: list[_Comp] = []
    for c in comps:
        if c.x0 <= 1 or c.x1 >= W - 2:
            continue   # 贴着块边缘 → 邻小节字形被裁剪的残片
        if c.h >= _BARLINE_H_FRAC * H and c.w <= 0.12 * c.h:
            continue   # 竖线残段兜底
        if (c.h <= _FLAT_MAX_H_FRAC * H and c.w >= _FLAT_AR * c.h):
            flats.append(c)
        else:
            glyphs.append(c)
    if not glyphs:
        return None

    # ── 2. 块 0 的拍号/调号区域：忽略其左侧的所有内容 ────────────────────
    cutoff = _detect_timesig_cutoff(glyphs, H)
    if cutoff > 0:
        glyphs = [c for c in glyphs if c.cx > cutoff]
        flats = [c for c in flats if c.cx > cutoff]
        if not glyphs:
            return None

    # ── 3. 参考尺寸：用较大的字形估计数字的典型高/宽 ────────────────────
    #     80 分位向上取整：只有 2 个字形（数字+八度点）时必须取大的那个；
    #     宽度同理——窄数字"1"和升降号会把中位数拉得过低
    hs = sorted(c.h for c in glyphs)
    ref_h = hs[min(len(hs) - 1, math.ceil(0.8 * (len(hs) - 1)))]
    big = [c for c in glyphs if c.h >= 0.7 * ref_h]
    ws = sorted(c.w for c in big) or [ref_h // 2]
    ref_w = ws[min(len(ws) - 1, math.ceil(0.8 * (len(ws) - 1)))]

    # ── 4. 合并升降号：#/b/♮ 紧贴其右侧数字（间距 ~0.3*数字宽），
    #     远小于音符之间的排版间距（>= ~0.5*数字宽）
    big_sorted = sorted(big, key=lambda c: c.x0)
    merge_gap = 0.45 * ref_w
    merged: list[list[_Comp]] = []
    for c in big_sorted:
        if merged:
            prev = merged[-1]
            gap = c.x0 - max(p.x1 for p in prev)
            prev_w = max(p.x1 for p in prev) - min(p.x0 for p in prev)
            # 前一组是较窄字形（疑似升降号）且与当前字形几乎相接 → 合并
            if gap <= merge_gap and prev_w <= _ACC_MAX_W * ref_w:
                prev.append(c)
                continue
        merged.append([c])

    # 组内面积最大的组件是数字本体；bbox 用本体而非整组——
    # 升降号比数字高，若把它并入 bbox，body_top 会被抬到八度点之上，
    # 导致"点在主体上方"的判断失败。
    cands: list[DigitGlyph] = []
    for grp in merged:
        body = max(grp, key=lambda c: c.area)
        dg = DigitGlyph(body.x0, body.y0, body.x1, body.y1)
        accs = [c for c in grp if c is not body and c.x1 <= body.x0 + 2]
        if accs:
            acc = max(accs, key=lambda c: c.area)
            dg.acc_char = _classify_accidental(acc, max(body.w, ref_w))
            dg.acc_w = acc.w
            dg.acc_fill = acc.area / max(1, acc.w * acc.h)
        cands.append(dg)

    # ── 5. 选数字：先用全体大字形的 y 中位数定主体带，剔除带外字形
    #     （上标小节号、力度记号等），再按面积取 top-N ────────────────────
    band_cy = float(np.median([d.cy for d in cands]))
    in_band = [d for d in cands if abs(d.cy - band_cy) <= _BAND_TOL * ref_h]
    if n_expected == -1:
        sel = in_band                       # 无锚模式：保留全部带内字形
    else:
        ranked = sorted(in_band, key=lambda d: d.w * d.h, reverse=True)
        sel = ranked[:n_expected]
        if len(sel) != n_expected:
            return None
    digits = sorted(sel, key=lambda d: d.x0)

    # 数字主体 y 带（用于区分延音线 / 下划线 / 八度点）
    body_top = float(np.median([d.y0 for d in digits]))
    body_bot = float(np.median([d.y1 for d in digits]))

    # ── 6. 小字形（点）：分配八度点 / 附点 ──────────────────────────────
    dot_max = _DOT_MAX_FRAC * ref_h
    small = [c for c in glyphs
             if c.h <= dot_max and c.w <= dot_max
             and _DOT_AR_LO <= (c.w / max(1, c.h)) <= _DOT_AR_HI
             and not any(d.x0 <= c.cx <= d.x1 and d.y0 <= c.cy <= d.y1
                         for d in digits)]
    for c in small:
        # 附点：y 在主体带内，且紧跟某数字右侧
        if body_top - 0.1 * ref_h <= c.cy <= body_bot + 0.1 * ref_h:
            left = [d for d in digits if d.x1 <= c.cx]
            if left:
                d = max(left, key=lambda d: d.x1)
                if c.x0 - d.x1 <= 0.9 * ref_w:
                    d.dotted = True
            continue
        # 八度点：x 居中对齐某数字，y 在其上方或下方
        # 窗口取 1.7*ref_h：双低八度（oct=-2）的两个堆叠点中，第二个点会被
        # 数字下方的时值下划线往下挤，实测落在 ~1.24*ref_h 处；窗口必须够宽
        # 才能把它算进来（旧的 1.2 阈值会漏掉带下划线低音的第二个点 → 误判为
        # -1）。1.7*ref_h 在 280px 单小节块内仍不会触及相邻行。
        for d in digits:
            if abs(c.cx - d.cx) > 0.55 * max(d.w, ref_w * 0.5):
                continue
            if c.cy < body_top and (body_top - c.cy) <= 1.7 * ref_h:
                d.oct += 1
            elif c.cy > body_bot and (c.cy - body_bot) <= 1.7 * ref_h:
                d.oct -= 1
            break
    for d in digits:
        d.oct = max(-2, min(2, d.oct))

    # ── 7. 水平线：主体带中间 → 延音线；主体带下方 → 时值下划线 ──────────
    dash_xs: list[float] = []
    for c in flats:
        if c.cy <= body_bot - 0.15 * ref_h:
            # 主体带内（中线附近）→ 延音线
            if c.cy >= body_top - 0.1 * ref_h:
                dash_xs.append(c.cx)
            continue
        # 下划线：给 x 区间有重叠的每个数字 +1
        for d in digits:
            if min(c.x1, d.x1) - max(c.x0, d.x0) > 0.3 * d.w:
                d.underlines += 1
    for d in digits:
        d.underlines = min(d.underlines, 2)

    return MeasureCV(digits=digits, dash_xs=sorted(dash_xs))


_UL_TO_DUR = {0: 'q', 1: 'e', 2: 's'}

_P_SANITIZE = re.compile(r"^([#b♯♭]?)([0-7iríRI])[',’.]*$")


def _sanitize_digit(p: str) -> str:
    """从 VLM 的音高 token 提取纯数字身份（八度/附点/升降号全部剥掉）。

    八度、附点由 CV 几何独管；升降号由 CV 的字形分类 + 小节内状态机
    决定（VLM 在小块图上经常漏报/幻觉/错挂 #）。
    'i'/'í' 是字体把「1+上方点」渲染的字形 → 还原为 '1'。
    无法解析时原样返回（调用方原样使用）。
    """
    s = p.strip()
    m = _P_SANITIZE.match(s)
    if not m:
        # 兜底：VLM 偶尔输出 "h2"（德式还原记法）/"natural 2" 等变体，
        # 捞出第一个数字字符即可——升降号由 CV 重建，前缀无须保留。
        for ch in s:
            if ch in '1234567':
                return ch
            if ch in '0oO':   # 'o'/'O' 是 VLM 对休止符 '0' 的常见误读
                return 'r'
        return p
    ch = m.group(2)
    if ch in ('i', 'í', 'I'):
        return '1'
    if ch in ('r', 'R', '0'):
        return 'r'
    return ch


def _apply_accidentals(digits: list[DigitGlyph], p_digits: list[str]) -> list[str]:
    """按记谱惯例从 CV 升降号字形重建每个音符的 #/b 前缀。

    LilyPond 同小节内同音高的升降号只画第一次：
      - 数字带 ♯/♭ 字形 → 加前缀，并记入小节内状态
      - 数字带 ♮ 字形   → 无前缀，清除状态（升号后的还原音）
      - 无字形          → 继承同 (数字, 八度) 的已有状态
    """
    state: dict[tuple[str, int], str] = {}
    out: list[str] = []
    for d, pd in zip(digits, p_digits):
        if pd == 'r' or pd not in '1234567':
            out.append(pd)
            continue
        key = (pd, d.oct)
        if d.acc_char in ('#', 'b'):
            state[key] = d.acc_char
            out.append(d.acc_char + pd)
        elif d.acc_char == 'n':
            state[key] = ''
            out.append(pd)
        else:
            out.append(state.get(key, '') + pd)
    return out


_HOLE_DIGITS = set('046')      # 这套字体里带封闭洞的数字
_OPEN_DIGITS = set('12357')    # 无洞数字


def _count_holes(img, d: DigitGlyph) -> int:
    """数字字形 bbox 内的封闭洞数（拓扑特征：0/4/6 有洞，1/2/3/5/7 无洞）。"""
    import cv2
    g = np.array(img.convert('L'))
    sub = (g[d.y0:d.y1, d.x0:d.x1] < _DARK_THRESH).astype(np.uint8)
    if sub.size == 0:
        return 0
    inv = np.pad(1 - sub, 1, constant_values=1)
    n, labels = cv2.connectedComponents(inv, connectivity=4)
    border = set(labels[0, :]) | set(labels[-1, :]) | set(labels[:, 0]) | set(labels[:, -1])
    return sum(1 for i in range(1, n) if i not in border)


def verify_identities(img, notes: list) -> list[int]:
    """用洞数拓扑交叉校验音符身份，返回可疑音符的下标列表。

    VLM 偶尔把 '1' 认成 '0'、'0' 认成 '4' 等——这类混淆跨越了
    有洞/无洞的拓扑边界，CV 可以确定性地发现（但无法替它改正，
    调用方应对可疑字形做逐字形重识别）。
    """
    try:
        digit_notes = [nt for nt in notes
                       if isinstance(nt, dict) and str(nt.get('p', '')).strip() != '-']
        cv = analyze_measure(img, len(digit_notes))
        if cv is None or len(cv.digits) != len(digit_notes):
            return []
        suspects = []
        for k, (nt, d) in enumerate(zip(digit_notes, cv.digits)):
            # 与 reconcile 同源的身份提取（VLM 可能输出 "q0"/"#2'" 等变体）
            ch = _sanitize_digit(str(nt.get('p', '')))
            if ch == 'r':
                ch = '0'
            if ch not in _HOLE_DIGITS | _OPEN_DIGITS:
                continue
            holes = _count_holes(img, d)
            if (ch in _HOLE_DIGITS and holes == 0) or \
                    (ch in _OPEN_DIGITS and holes >= 1):
                suspects.append(k)
        return suspects
    except Exception:
        return []


_SIG_SIZE = 24          # 模板签名图边长
_TEMPLATE_MIN_SIM = 0.95  # 模板命中阈值（同页同字形余弦相似度实测 >= 0.993）


def _glyph_signature(img, d: DigitGlyph) -> np.ndarray:
    """数字字形的归一化模板签名（bbox 二值图缩放到固定尺寸后单位化）。"""
    from PIL import Image
    g = np.array(img.convert('L'))
    sub = (g[d.y0:d.y1, d.x0:d.x1] < _DARK_THRESH).astype(np.float32)
    im = Image.fromarray((sub * 255).astype(np.uint8)).resize(
        (_SIG_SIZE, _SIG_SIZE), Image.BILINEAR)
    v = np.asarray(im, dtype=np.float32).ravel()
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def harvest_glyph_templates(img, notes: list) -> list[tuple[np.ndarray, str]]:
    """从一个「可信小节」收集 (签名, 数字字符) 模板对。

    可信 = VLM 计数与 CV 一致且拓扑校验无冲突的小节（错误率极低）。
    同一页的数字字体字号一致，签名余弦相似度同字 >= 0.99，可用作
    最近邻分类的标注库。休止符 '0' 也收（字符 '0'）。
    """
    try:
        digit_notes = [nt for nt in notes
                       if isinstance(nt, dict) and str(nt.get('p', '')).strip() != '-']
        cv = analyze_measure(img, len(digit_notes))
        if cv is None or len(cv.digits) != len(digit_notes):
            return []
        out = []
        for nt, d in zip(digit_notes, cv.digits):
            ch = _sanitize_digit(str(nt.get('p', '')))
            ch = '0' if ch == 'r' else ch
            if ch in '01234567':
                out.append((_glyph_signature(img, d), ch))
        return out
    except Exception:
        return []


def reclassify_by_templates(img, notes: list,
                            library: list[tuple[np.ndarray, str]]) -> list | None:
    """用同页模板库对一个「存疑小节」的数字身份做最近邻重分类。

    每个字形取库内余弦相似度最高的标签；低于阈值的保留原身份。
    任何身份被修正时返回重建后的音符列表（几何字段照常由 CV 提供），
    否则返回 None（无需改动）。
    """
    try:
        if not library:
            return None
        digit_notes = [nt for nt in notes
                       if isinstance(nt, dict) and str(nt.get('p', '')).strip() != '-']
        cv = analyze_measure(img, len(digit_notes))
        if cv is None or len(cv.digits) != len(digit_notes):
            return None
        mat = np.stack([v for v, _ in library])      # (N, S*S)
        idents: list[str] = []
        changed = False
        for nt, d in zip(digit_notes, cv.digits):
            cur = _sanitize_digit(str(nt.get('p', '')))
            cur = '0' if cur == 'r' else cur
            sims = mat @ _glyph_signature(img, d)
            k = int(np.argmax(sims))
            if float(sims[k]) >= _TEMPLATE_MIN_SIM:
                best = library[k][1]
                if best != cur:
                    changed = True
                idents.append(best)
            else:
                idents.append(cur)
        if not changed:
            return None
        return build_notes_from_cv(img, idents)
    except Exception:
        return None


def count_digits(img) -> int:
    """不依赖 VLM 锚点，独立数出小节里的数字字形个数（休止符 0 计入，
    延音线不计）。返回 0 表示无法分析。用于交叉校验 VLM 的音符数。"""
    try:
        cv = analyze_measure(img, n_expected=-1)
        return len(cv.digits) if cv else 0
    except Exception:
        return 0


def glyph_crops(img, pad_frac: float = 0.15) -> list:
    """无锚分析小节图，返回每个数字字形的小裁剪图（含左侧升降号区域）。

    用于逐字形识别兜底：VLM 对整小节漏读/多读时，把每个字形单独裁出来
    问"这是什么数字"，错误模式大幅简化。裁剪不含上下八度点——八度由
    CV 独管，露出点反而诱导 VLM 输出 'i' 等组合字形。
    返回 [(DigitGlyph, PIL.Image), ...] 按 x 升序；分析失败返回 []。
    """
    cv = analyze_measure(img, n_expected=-1)
    if cv is None or not cv.digits:
        return []
    W, H = img.size
    out = []
    digs = cv.digits
    for i, d in enumerate(digs):
        # 左边界：向左扩一个字宽以包含升降号，但不越过前一个字形
        left_lim = digs[i - 1].x1 + 2 if i > 0 else max(0, d.x0 - int(1.2 * d.w))
        x0 = max(left_lim, d.x0 - int(1.0 * d.w))
        x1 = min(W, d.x1 + int(0.25 * d.w))
        y0 = max(0, d.y0 - int(pad_frac * d.h))
        y1 = min(H, d.y1 + int(pad_frac * d.h))
        out.append((d, img.crop((x0, y0, x1, y1))))
    return out


def build_notes_from_cv(img, identities: list[str]) -> list | None:
    """用 CV 几何 + 逐字形识别出的音高身份，拼出完整小节音符列表。

    identities: 与 analyze_measure(img, -1).digits 一一对应的 p 值
    （"5"/"#2"/"r" 等，已 sanitize）。数量不符返回 None。
    """
    cv = analyze_measure(img, n_expected=-1)
    if cv is None or len(cv.digits) != len(identities):
        return None
    p_digits = _apply_accidentals(cv.digits,
                                  [_sanitize_digit(s) for s in identities])
    by_digit = dict(zip((id(d) for d in cv.digits), p_digits))
    out: list = []
    for kind, obj in cv.events():
        if kind == 'dash':
            out.append({'p': '-'})
            continue
        d: DigitGlyph = obj   # type: ignore[assignment]
        out.append({'p': by_digit[id(d)],
                    'oct': d.oct,
                    'dur': _UL_TO_DUR.get(d.underlines, 'q'),
                    'dots': 1 if d.dotted else 0})
    return out


def reconcile(measure_notes: list, img) -> list:
    """Merge VLM note identities with CV geometry for one measure.

    VLM contributes digit identity + accidental (note order, p field);
    CV contributes octave, duration (underlines), dotted flag and the
    position/count of sustain dashes. On any CV failure the input list is
    returned unchanged.
    """
    try:
        vlm_notes = [nt for nt in measure_notes
                     if isinstance(nt, dict) and str(nt.get('p', '')).strip() != '-']
        if not vlm_notes:
            return measure_notes
        cv = analyze_measure(img, len(vlm_notes))
        if cv is None:
            return measure_notes
        p_digits = _apply_accidentals(
            cv.digits,
            [_sanitize_digit(str(nt.get('p', 'r'))) for nt in vlm_notes])
        out: list = []
        k = 0
        for kind, obj in cv.events():
            if kind == 'dash':
                out.append({'p': '-'})
                continue
            d: DigitGlyph = obj   # type: ignore[assignment]
            nt = dict(vlm_notes[k])
            nt['p'] = p_digits[k]
            k += 1
            nt['oct'] = d.oct
            nt['dur'] = _UL_TO_DUR.get(d.underlines, 'q')
            nt['dots'] = 1 if d.dotted else 0
            out.append(nt)
        return out
    except Exception:
        return measure_notes
