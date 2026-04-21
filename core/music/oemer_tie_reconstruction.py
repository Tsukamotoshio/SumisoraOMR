# core/music/oemer_tie_reconstruction.py — 基于图像的延音线视觉重建工作流
"""
使用 oemer 引擎（可选）或 CV 回退方法，对 homr 生成的 MusicXML 中的
相邻同音高音符进行视觉验证，重建缺失的延音线（tie）。

工作流程
--------
1. 解析 homr 生成的 MusicXML/MXL，找出相邻同音高音符对（候选 tie）
2. 加载原始预处理图像
2.5 [可选] 运行 homr SegNet 分割模型：
   - 仅使用 notehead 通道 → 提取音符头质心（精确像素坐标），用于 x 坐标吸附
   - staff 通道不用于谱行检测（对复杂背景图片会误合并成一个跨全图系统）
3. 使用传统 Otsu+形态学方法检测谱行（system）
4. 检测每个谱行内的小节线（barline），估算音符像素位置
   - 若 homr 音符头质心可用，将估算位置吸附到最近实际质心（精化）
5. 对每个候选对：
   a. 若两音符处于不同谱行（换行）→ 跳过切片，标记为需人工审核
   b. 若在同一谱行 → 切片图像（基于精化后的音符像素位置）
   c. 调用 oemer 推理（若模型可用）或 CV 弧检测，返回布尔值
6. 结果分为：confirmed_tie、rejected、review_required（人工审核）

公开 API
--------
run_oemer_tie_reconstruction(mxl_path, image_path) -> TieReconstructionResult
apply_confirmed_ties(result, output_path) -> int   写回 XML，返回写入 tie 对数
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from ..utils import get_app_base_dir, log_message

LOGGER = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 可选依赖
# ─────────────────────────────────────────────────────────────────────────────
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    import cv2 as _cv2
    _HAS_CV2 = True
except ImportError:
    _cv2 = None  # type: ignore[assignment]
    _HAS_CV2 = False

try:
    from PIL import Image as _PilImage
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

try:
    import fitz as _fitz  # PyMuPDF
    _HAS_FITZ = True
except ImportError:
    _fitz = None  # type: ignore[assignment]
    _HAS_FITZ = False

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

# oemer 引擎目录名（在 omr_engine/ 下）
OEMER_REPO_DIR_NAME = 'oemer'

# 图像裁剪时，在两音符范围之外额外增加的水平/垂直边距（像素）
_CROP_X_MARGIN = 30
# 垂直边距：40px 确保延音线弧（通常在谱表上方 1-2 个谱间距）完整可见
_CROP_Y_MARGIN = 40

# 弧形检测：弧的最小宽度与裁剪宽度之比
_MIN_ARC_WIDTH_RATIO = 0.25

# 弧形检测：弧的高宽比上限（弧宜较宽而短）
_MAX_ARC_ASPECT = 0.55

# 谱行间距阈值（大于此倍数中位谱线间距 → 谱行分界）
_SYSTEM_GAP_FACTOR = 2.5

# homr 引擎目录名（在 omr_engine/ 下）
HOMR_REPO_DIR_NAME = 'homr'

# homr SegNet 推理参数（用于音符头定位）
_SEGNET_STEP_SIZE  = 160   # 滑动窗口步长（比默认 320 更细，精度更高）
_SEGNET_BATCH_SIZE = 4     # CPU 推理批次大小

# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TieCandidate:
    """来自 homr MusicXML 的候选延音线对（相邻同音高音符）。"""
    part_id:     str
    voice_id:    str
    staff_id:    str       # 谱表 ID（1/2/...），多谱表时区分不同谱表
    pitch_midi:  int       # MIDI 音高 (C4=60)
    pitch_step:  str       # 音名 (C/D/E/F/G/A/B)
    pitch_alter: float     # 升降号 (-1/0/+1)
    pitch_octave:int       # 八度
    measure_a:   int       # 音符 A 所在小节索引（0-based）
    measure_b:   int       # 音符 B 所在小节索引（0-based）
    beat_a:      float     # 音符 A 在小节内的拍位（1-based 浮点，单位：四分音符）
    beat_b:      float     # 音符 B 在小节内的拍位（1-based 浮点，单位：四分音符）
    tick_a:      int       # 音符 A 绝对 tick
    tick_b:      int       # 音符 B 绝对 tick
    duration_a:  int       # 音符 A 时值（ticks）
    duration_b:  int       # 音符 B 时值（ticks）
    divisions:   int       # ticks/四分音符
    measure_start_tick_a: int  # 音符 A 所在小节的起始 tick
    measure_start_tick_b: int  # 音符 B 所在小节的起始 tick
    elem_a:      ET.Element = field(repr=False)  # XML 元素（用于写回）
    elem_b:      ET.Element = field(repr=False)
    beats_in_measure_qn: float = field(default=4.0)  # 每小节四分音符数（由拍号推算）

    @property
    def label(self) -> str:
        """可读标签，用于 UI 展示。"""
        note_name = f"{self.pitch_step}{'#' if self.pitch_alter > 0 else ('b' if self.pitch_alter < 0 else '')}{self.pitch_octave}"
        return f"{note_name}  小节 {self.measure_a+1}→{self.measure_b+1}"


class TieDecisionKind(Enum):
    CONFIRMED_TIE = 'tie'         # 确认为延音线
    REJECTED      = 'no_tie'      # 确认不是延音线
    LINEBREAK     = 'linebreak'   # 跨谱行，无法切片
    AMBIGUOUS     = 'ambiguous'   # 无法确定，需人工审核


@dataclass
class TieReviewItem:
    """需要人工审核的延音线候选。"""
    candidate:      TieCandidate
    kind:           TieDecisionKind  # LINEBREAK 或 AMBIGUOUS
    crop_bytes:     Optional[bytes]  # PNG 字节（LINEBREAK 时为 None）
    user_decision:  Optional[bool] = None  # True=是延音线，False=不是，None=未决定

    @property
    def is_linebreak(self) -> bool:
        return self.kind == TieDecisionKind.LINEBREAK


@dataclass
class TieReconstructionResult:
    """run_oemer_tie_reconstruction() 的返回结果。"""
    confirmed:     list[TieCandidate]     # 确认为 tie
    rejected:      list[TieCandidate]     # 确认不是 tie
    review_items:  list[TieReviewItem]    # 需要人工审核
    # 保存原始 XML 树，用于 apply_confirmed_ties() 写回
    _xml_tree:     Optional[ET.ElementTree] = field(default=None, repr=False)
    _xml_ns:       str = field(default='', repr=False)
    _source_path:  Optional[Path] = field(default=None, repr=False)

    @property
    def stats(self) -> dict:
        # review_items 现在包含所有候选（含 oemer 已确认/拒绝），以其为统一来源
        total = len(self.review_items)
        n_confirmed = sum(1 for r in self.review_items if r.kind == TieDecisionKind.CONFIRMED_TIE)
        n_rejected  = sum(1 for r in self.review_items if r.kind == TieDecisionKind.REJECTED)
        n_linebreak = sum(1 for r in self.review_items if r.kind == TieDecisionKind.LINEBREAK)
        n_ambiguous = sum(1 for r in self.review_items if r.kind == TieDecisionKind.AMBIGUOUS)
        return {
            'total_candidates': total,
            'confirmed_ties':   n_confirmed,
            'rejected':         n_rejected,
            'linebreak':        n_linebreak,
            'ambiguous':        n_ambiguous,
        }


# ─────────────────────────────────────────────────────────────────────────────
# MusicXML 解析工具（与 tie_reconstruction.py 独立，避免循环依赖）
# ─────────────────────────────────────────────────────────────────────────────

_STEP_SEMITONE: dict[str, int] = {
    'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11,
}


def _pitch_to_midi(step: str, alter: float, octave: int) -> int:
    return (octave + 1) * 12 + _STEP_SEMITONE.get(step.upper(), 0) + round(alter)


def _get_ns(root: ET.Element) -> str:
    tag = root.tag
    if tag.startswith('{'):
        return tag[: tag.index('}') + 1]
    return ''


def _txt(elem: Optional[ET.Element], default: str = '') -> str:
    if elem is None:
        return default
    return (elem.text or default).strip()


def _local(tag: str) -> str:
    return tag.split('}')[-1] if '}' in tag else tag


def _has_tie_start(note_elem: ET.Element, ns: str) -> bool:
    for tie_e in note_elem.findall(f'{ns}tie'):
        if tie_e.get('type') == 'start':
            return True
    notations_e = note_elem.find(f'{ns}notations')
    if notations_e is not None:
        for tied_e in notations_e.findall(f'{ns}tied'):
            if tied_e.get('type') == 'start':
                return True
    return False


def _add_tie_to_note(note_elem: ET.Element, tie_type: str, ns: str) -> None:
    """为 <note> 元素添加 <tie type="..."/> 及 <notations><tied type="..."/>。"""
    # 检查是否已存在
    for tie_e in note_elem.findall(f'{ns}tie'):
        if tie_e.get('type') == tie_type:
            return
    children_tags = [_local(c.tag) for c in note_elem]
    tie_elem = ET.Element(f'{ns}tie')
    tie_elem.set('type', tie_type)
    try:
        ins = children_tags.index('notations')
        note_elem.insert(ins, tie_elem)
    except ValueError:
        note_elem.append(tie_elem)
    notations_e = note_elem.find(f'{ns}notations')
    if notations_e is None:
        notations_e = ET.SubElement(note_elem, f'{ns}notations')
    for tied_e in notations_e.findall(f'{ns}tied'):
        if tied_e.get('type') == tie_type:
            return
    tied_elem = ET.Element(f'{ns}tied')
    tied_elem.set('type', tie_type)
    notations_e.insert(0, tied_elem)


# ─────────────────────────────────────────────────────────────────────────────
# 候选对解析
# ─────────────────────────────────────────────────────────────────────────────

def _parse_candidates_from_root(root: ET.Element, ns: str) -> list[TieCandidate]:
    """解析 score-partwise 根元素，找出所有相邻同音高候选 tie 对。

    条件：
    - 同 part、同声部、同 MIDI 音高
    - A.end_tick == B.start_tick（紧邻）
    - A 没有显式 tie start（避免重复写入）
    """
    @dataclass
    class _N:
        elem:               ET.Element
        part_id:            str
        voice_id:           str
        staff_id:           str
        pitch_midi:         int
        pitch_step:         str
        pitch_alter:        float
        pitch_octave:       int
        measure_idx:        int
        beat_pos:           float   # 1-based beat in quarter notes
        tick:               int     # absolute start tick
        duration:           int     # ticks
        divisions:          int
        measure_start_tick: int     # 所在小节起始 tick
        beats_in_measure_qn: float = 4.0  # from time signature

        @property
        def end_tick(self) -> int:
            return self.tick + self.duration

    notes: list[_N] = []

    for part_elem in root.findall(f'{ns}part'):
        part_id = part_elem.get('id', '')
        divisions = 1
        beats = 4
        beat_type = 4
        cumulative_tick = 0

        for meas_idx, meas_elem in enumerate(part_elem.findall(f'{ns}measure')):
            last_start_tick = cumulative_tick
            measure_start_tick = cumulative_tick
            measure_length = beats * 4 * divisions // beat_type

            for child in meas_elem:
                loc = _local(child.tag)
                if loc == 'attributes':
                    div_e = child.find(f'{ns}divisions')
                    if div_e is not None and div_e.text:
                        divisions = int(div_e.text)
                    time_e = child.find(f'{ns}time')
                    if time_e is not None:
                        b_e  = time_e.find(f'{ns}beats')
                        bt_e = time_e.find(f'{ns}beat-type')
                        if b_e  is not None and b_e.text:  beats      = int(b_e.text)
                        if bt_e is not None and bt_e.text: beat_type  = int(bt_e.text)
                    measure_length = beats * 4 * divisions // beat_type

                elif loc == 'note':
                    is_chord = child.find(f'{ns}chord') is not None
                    is_rest  = child.find(f'{ns}rest')  is not None
                    dur_e    = child.find(f'{ns}duration')
                    duration = int(_txt(dur_e, '0')) if dur_e is not None else 0

                    if is_chord:
                        note_tick = last_start_tick
                    else:
                        note_tick       = cumulative_tick
                        last_start_tick = cumulative_tick
                        cumulative_tick += duration

                    if is_rest or duration == 0:
                        continue

                    pitch_e = child.find(f'{ns}pitch')
                    if pitch_e is None:
                        continue

                    step   = _txt(pitch_e.find(f'{ns}step'),   'C')
                    alter  = float(_txt(pitch_e.find(f'{ns}alter'), '0') or '0')
                    octave = int(_txt(pitch_e.find(f'{ns}octave'), '4')  or '4')
                    midi   = _pitch_to_midi(step, alter, octave)
                    voice  = _txt(child.find(f'{ns}voice'), '1')

                    beat_pos = (note_tick - measure_start_tick) / max(divisions, 1) + 1.0
                    # 每小节四分音符数 = beats × (4/beat_type)
                    beats_in_measure_qn = beats * 4.0 / max(beat_type, 1)
                    staff_id = _txt(child.find(f'{ns}staff'), '1')

                    notes.append(_N(
                        elem=child, part_id=part_id, voice_id=voice, staff_id=staff_id,
                        pitch_midi=midi, pitch_step=step, pitch_alter=alter, pitch_octave=octave,
                        measure_idx=meas_idx, beat_pos=beat_pos,
                        tick=note_tick, duration=duration, divisions=divisions,
                        measure_start_tick=measure_start_tick,
                        beats_in_measure_qn=beats_in_measure_qn,
                    ))

                elif loc == 'backup':
                    dur_e = child.find(f'{ns}duration')
                    if dur_e is not None and dur_e.text:
                        cumulative_tick -= int(dur_e.text)
                elif loc == 'forward':
                    dur_e = child.find(f'{ns}duration')
                    if dur_e is not None and dur_e.text:
                        cumulative_tick += int(dur_e.text)

    # 分组：(part_id, voice_id, staff_id, pitch_midi)
    # 加入 staff_id 防止多谱表乐器跨谱表同音高假配对
    groups: dict[tuple, list[_N]] = defaultdict(list)
    for n in notes:
        groups[(n.part_id, n.voice_id, n.staff_id, n.pitch_midi)].append(n)

    candidates: list[TieCandidate] = []
    for grp in groups.values():
        grp.sort(key=lambda n: n.tick)
        for i in range(len(grp) - 1):
            a, b = grp[i], grp[i + 1]
            if a.end_tick != b.tick:
                continue
            if a.tick == b.tick:
                continue
            if _has_tie_start(a.elem, ns):
                continue  # 已有显式 tie，跳过
            candidates.append(TieCandidate(
                part_id=a.part_id, voice_id=a.voice_id, staff_id=a.staff_id,
                pitch_midi=a.pitch_midi, pitch_step=a.pitch_step,
                pitch_alter=a.pitch_alter, pitch_octave=a.pitch_octave,
                measure_a=a.measure_idx, measure_b=b.measure_idx,
                beat_a=a.beat_pos, beat_b=b.beat_pos,
                tick_a=a.tick, tick_b=b.tick,
                duration_a=a.duration, duration_b=b.duration,
                divisions=a.divisions,
                measure_start_tick_a=a.measure_start_tick,
                measure_start_tick_b=b.measure_start_tick,
                elem_a=a.elem, elem_b=b.elem,
                beats_in_measure_qn=a.beats_in_measure_qn,
            ))

    candidates.sort(key=lambda c: c.tick_a)
    return candidates


def parse_candidates(mxl_or_xml: Path) -> tuple[list[TieCandidate], ET.ElementTree, str, Path]:
    """加载 MusicXML/MXL 文件，返回 (candidates, tree, ns, working_xml_path)。

    对 .mxl 压缩包，解压到临时目录并返回首个 .xml 文件的路径。
    调用方负责清理临时目录（如有）。
    """
    if mxl_or_xml.suffix.lower() == '.mxl':
        tmp = tempfile.mkdtemp(prefix='_oemer_tiefix_')
        with zipfile.ZipFile(mxl_or_xml, 'r') as zf:
            zf.extractall(tmp)
        xml_files = sorted(Path(tmp).rglob('*.xml'))
        if not xml_files:
            raise ValueError(f'MXL 压缩包中没有找到 XML 文件: {mxl_or_xml}')
        xml_path = xml_files[0]
    else:
        tmp = None
        xml_path = mxl_or_xml

    tree = ET.parse(str(xml_path))
    root = tree.getroot()
    ns   = _get_ns(root)
    if ns:
        ET.register_namespace('', ns[1:-1])

    candidates = _parse_candidates_from_root(root, ns)
    return candidates, tree, ns, xml_path


# ─────────────────────────────────────────────────────────────────────────────
# 图像分析：谱行与小节线检测
# ─────────────────────────────────────────────────────────────────────────────

def _load_image_gray(image_path: Path) -> Optional['np.ndarray']:
    if not _HAS_CV2 or not _HAS_NUMPY:
        return None
    img = _load_image_bgr(image_path)
    if img is None:
        return None
    return _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY)


def _pdf_to_bgr(image_path: Path) -> Optional['np.ndarray']:
    """用 PyMuPDF 将 PDF 所有页面渲染并垂直拼接为一张 BGR 图像（300 dpi）。"""
    if not _HAS_FITZ or not _HAS_NUMPY:
        return None
    try:
        doc = _fitz.open(str(image_path))
        mat = _fitz.Matrix(300 / 72, 300 / 72)  # 300 dpi
        pages_bgr = []
        for page in doc:
            pix = page.get_pixmap(matrix=mat, colorspace=_fitz.csRGB)
            img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            if pix.n == 4:
                img_np = img_np[:, :, :3]
            pages_bgr.append(_cv2.cvtColor(img_np, _cv2.COLOR_RGB2BGR))
        doc.close()
        if not pages_bgr:
            return None
        # 如果多页宽度不一致，则补白至最大宽度
        max_w = max(p.shape[1] for p in pages_bgr)
        padded = []
        for p in pages_bgr:
            if p.shape[1] < max_w:
                pad = np.full((p.shape[0], max_w - p.shape[1], 3), 255, dtype=np.uint8)
                p = np.concatenate([p, pad], axis=1)
            padded.append(p)
        return np.concatenate(padded, axis=0)
    except Exception:
        return None


def _load_image_bgr(image_path: Path) -> Optional['np.ndarray']:
    """加载图像为 BGR ndarray，支持中文路径和 PDF。"""
    if not _HAS_CV2 or not _HAS_NUMPY:
        return None
    if image_path.suffix.lower() == '.pdf':
        return _pdf_to_bgr(image_path)
    # cv2.imread 在 Windows 上不支持含非 ASCII 字符的路径，
    # 改用 np.fromfile + cv2.imdecode 绕过该限制。
    try:
        buf = np.fromfile(str(image_path), dtype=np.uint8)
        img = _cv2.imdecode(buf, _cv2.IMREAD_COLOR)
        return img  # None if decode failed
    except Exception:
        return None


@dataclass
class StaffSystem:
    """图像中的一个谱行（多条五线谱线组成的一行）。"""
    y_top:       int    # 谱行顶部 y 坐标（含宽边距，用于切片）
    y_bottom:    int    # 谱行底部 y 坐标（含宽边距，用于切片）
    # 用于 barline 检测的紧边距范围（仅包含实际五线谱线区域，不含宽 margin）
    y_inner_top:    int = 0
    y_inner_bottom: int = 0
    barline_xs: list[int] = field(default_factory=list)  # 小节线 x 坐标（含左右边界）

    @property
    def height(self) -> int:
        return self.y_bottom - self.y_top


# ─────────────────────────────────────────────────────────────────────────────
# homr SegNet 音符头检测（可选，用于精化像素位置）
# ─────────────────────────────────────────────────────────────────────────────

def _get_homr_dir() -> Optional[Path]:
    """返回 homr 源码目录（omr_engine/homr），若不存在则返回 None。"""
    base = get_app_base_dir()
    homr_dir = base / 'omr_engine' / HOMR_REPO_DIR_NAME
    if homr_dir.is_dir():
        return homr_dir
    return None


def _run_homr_segnet(
    img_bgr: 'np.ndarray',
    image_path: Path,
) -> 'Optional[tuple[np.ndarray, np.ndarray]]':
    """运行 homr SegNet，返回 (notehead_mask, staff_mask)，尺寸与输入图像一致。

    步骤：
    1. 将图像缩放到 1920px 宽（homr 标准输入）
    2. 应用 CLAHE 增强（与 homr 预处理完全一致）
    3. 运行 SegNet 分割推理（step=160，比默认 320 更精细）
    4. 将输出 mask 缩放回原始图像尺寸

    如 homr 不可用或推理失败，返回 None。
    """
    if not _HAS_CV2 or not _HAS_NUMPY:
        return None

    homr_dir = _get_homr_dir()
    if homr_dir is None:
        return None

    homr_src = str(homr_dir)
    if homr_src not in sys.path:
        sys.path.insert(0, homr_src)

    try:
        from homr.resize import resize_image as _homr_resize            # type: ignore[import]
        from homr.color_adjust import apply_clahe as _homr_clahe        # type: ignore[import]
        from homr.segmentation.inference_segnet import inference as _segnet_inference  # type: ignore[import]

        orig_h, orig_w = img_bgr.shape[:2]

        # 缩放到 1920px 宽（homr 约定）
        img_1920 = _homr_resize(img_bgr)

        # CLAHE 预处理 (BGR → 灰度，经直方图自适应均衡)
        gray_clahe = _homr_clahe(img_1920)   # shape: (H_1920, W_1920) — 单通道

        # SegNet 推理
        log_message('[oemer-tie] 运行 homr SegNet 检测音符头位置…')
        staff_map, _symbols, _stems, notehead_map, _clefs = _segnet_inference(
            gray_clahe,
            use_gpu_inference=False,
            batch_size=_SEGNET_BATCH_SIZE,
            step_size=_SEGNET_STEP_SIZE,
            win_size=320,
        )

        # staff_map / notehead_map: shape (H_1920, W_1920), dtype uint8 (0 or 1)
        # 缩放回原图尺寸
        notehead_orig = _cv2.resize(
            notehead_map.astype(np.uint8) * 255, (orig_w, orig_h),
            interpolation=_cv2.INTER_NEAREST,
        )
        staff_orig = _cv2.resize(
            staff_map.astype(np.uint8) * 255, (orig_w, orig_h),
            interpolation=_cv2.INTER_NEAREST,
        )

        return (notehead_orig > 128).astype(np.uint8), (staff_orig > 128).astype(np.uint8)

    except Exception as exc:
        LOGGER.debug('[oemer-tie] homr SegNet 失败（回退到传统检测）: %s', exc)
        return None


def _extract_notehead_centroids(notehead_mask: 'np.ndarray') -> 'list[tuple[int, int]]':
    """从 notehead 二值 mask 提取音符头质心列表 [(x, y), ...]。

    使用 connectedComponentsWithStats 检测连通域，过滤过小或过大的 blob。
    返回列表按 (y, x) 阅读顺序排序（从上到下、从左到右）。
    """
    if not _HAS_CV2 or not _HAS_NUMPY:
        return []
    try:
        h, w = notehead_mask.shape
        # 轻度膨胀以合并碎片化的 notehead 像素
        kernel = _cv2.getStructuringElement(_cv2.MORPH_ELLIPSE, (3, 3))
        dilated = _cv2.dilate(notehead_mask, kernel, iterations=1)

        num_labels, _labels, stats, centroids = _cv2.connectedComponentsWithStats(
            dilated, connectivity=8,
        )

        # 面积阈值：根据图像大小动态调整
        min_area = max(6,  (h * w) // 80_000)   # 过滤噪点
        max_area = max(80, (h * w) // 600)       # 过滤谱线段、大块 blob

        result: list[tuple[int, int]] = []
        for i in range(1, num_labels):   # 跳过背景 (0)
            area = int(stats[i, _cv2.CC_STAT_AREA])
            if min_area <= area <= max_area:
                cx = int(centroids[i][0])
                cy = int(centroids[i][1])
                result.append((cx, cy))

        # 阅读顺序排序（先 y 后 x）
        result.sort(key=lambda p: (p[1], p[0]))
        return result

    except Exception as exc:
        LOGGER.debug('_extract_notehead_centroids 失败: %s', exc)
        return []


def _snap_to_nearest_notehead(
    x_est:     int,
    y_center:  int,
    centroids: 'list[tuple[int, int]]',
    radius_x:  int,
    radius_y:  int,
    exclude_cx: 'Optional[int]' = None,
) -> 'Optional[int]':
    """在给定范围内找到距估算位置最近的音符头质心，返回其 x 坐标。

    Parameters
    ----------
    x_est     : 估算的 x 坐标（来自小节线比例估算）
    y_center  : 谱行中心 y 坐标（纵向筛选基准）
    centroids : homr SegNet 检测到的音符头质心 [(x, y), ...]
    radius_x  : 横向搜索半径（像素）
    radius_y  : 纵向搜索半径（像素）
    exclude_cx: 排除此 x 坐标的质心（防止 A/B 两音符吸附到同一点）。

    Returns
    -------
    距离最近的质心的 x 坐标；若无满足条件的候选，返回 None。
    """
    best_dist: float = float('inf')
    best_x: Optional[int] = None
    for (cx, cy) in centroids:
        if exclude_cx is not None and cx == exclude_cx:
            continue
        if abs(cx - x_est) > radius_x:
            continue
        if abs(cy - y_center) > radius_y:
            continue
        # 横向距离权重更高（y 偏差在谱行内属于正常）
        dist = abs(cx - x_est) + abs(cy - y_center) * 0.3
        if dist < best_dist:
            best_dist = dist
            best_x = cx
    return best_x


def detect_staff_systems(gray: 'np.ndarray') -> list[StaffSystem]:
    """从灰度图检测五线谱谱行（system）的 y 范围。

    算法：
    1. Otsu 二值化（暗像素=谱线内容）
    2. 水平形态学开运算，提取横跨 ≥40% 宽度的连续暗线段（谱线）
    3. 聚类成质心，计算中位间距
    4. 将 5 条连续谱线归为一组（一套五线谱 = 一个谱行）
    5. 相邻谱行间距 > 2.5× 中位间距 → 系统分界
    6. 为每个系统加上上下边距
    """
    if not _HAS_CV2 or not _HAS_NUMPY:
        return []
    try:
        h, w = gray.shape
        _, binary = _cv2.threshold(gray, 0, 255, _cv2.THRESH_BINARY_INV + _cv2.THRESH_OTSU)

        kernel_len = max(3, int(w * 0.40))
        h_kernel   = _cv2.getStructuringElement(_cv2.MORPH_RECT, (kernel_len, 1))
        staff_only = _cv2.morphologyEx(binary, _cv2.MORPH_OPEN, h_kernel)
        row_sums   = np.sum(staff_only // 255, axis=1)
        is_line    = row_sums > 0

        # 聚类质心
        centroids: list[float] = []
        in_cluster = False
        cs = 0
        for r in range(h):
            if is_line[r] and not in_cluster:
                in_cluster = True
                cs = r
            elif not is_line[r] and in_cluster:
                in_cluster = False
                centroids.append((cs + r - 1) / 2.0)
        if in_cluster:
            centroids.append((cs + h - 1) / 2.0)

        if len(centroids) < 2:
            return [StaffSystem(y_top=0, y_bottom=h)]

        diffs   = [centroids[i+1] - centroids[i] for i in range(len(centroids)-1)]
        med_sp  = float(np.median(diffs))
        if med_sp < 1:
            med_sp = 1.0

        # 将质心分割成 system（每 5 条线一组，大间距为系统边界）
        systems: list[StaffSystem] = []
        sys_lines: list[float] = []
        for i, c in enumerate(centroids):
            if sys_lines and (c - sys_lines[-1]) > _SYSTEM_GAP_FACTOR * med_sp:
                # 开始新系统
                if sys_lines:
                    # margin = 2.5 倍谱线间距，留足延音线弧和表情记号的垂直空间
                    margin = int(med_sp * 2.5)
                    # inner_margin = 0.5 倍谱线间距：仅含实际谱线区域，供 barline 检测使用
                    inner_margin = max(1, int(med_sp * 0.5))
                    y_top    = max(0, int(sys_lines[0]) - margin)
                    y_bottom = min(h, int(sys_lines[-1]) + margin)
                    y_inner_top    = max(0, int(sys_lines[0]) - inner_margin)
                    y_inner_bottom = min(h, int(sys_lines[-1]) + inner_margin)
                    systems.append(StaffSystem(
                        y_top=y_top, y_bottom=y_bottom,
                        y_inner_top=y_inner_top, y_inner_bottom=y_inner_bottom,
                    ))
                sys_lines = [c]
            else:
                sys_lines.append(c)
        if sys_lines:
            margin = int(med_sp * 2.5)
            inner_margin = max(1, int(med_sp * 0.5))
            y_top    = max(0, int(sys_lines[0]) - margin)
            y_bottom = min(h, int(sys_lines[-1]) + margin)
            y_inner_top    = max(0, int(sys_lines[0]) - inner_margin)
            y_inner_bottom = min(h, int(sys_lines[-1]) + inner_margin)
            systems.append(StaffSystem(
                y_top=y_top, y_bottom=y_bottom,
                y_inner_top=y_inner_top, y_inner_bottom=y_inner_bottom,
            ))

        return systems if systems else [StaffSystem(y_top=0, y_bottom=h)]
    except Exception as exc:
        LOGGER.warning('detect_staff_systems 失败: %s', exc)
        return []


def detect_barlines(gray: 'np.ndarray', system: StaffSystem, n_measures: int = 0) -> list[int]:
    """在给定谱行中检测小节线 x 坐标（含左右边界）。

    算法：对谱行条带做垂直形态学开运算，保留跨越 ≥70% 谱行高度的竖线。
    结果始终以图像左边界 (0) 开头、右边界 (w-1) 结尾。
    """
    if not _HAS_CV2 or not _HAS_NUMPY:
        return [0, gray.shape[1] - 1]
    try:
        # 用 inner strip（紧边距）做 barline 检测：
        # 2.5× margin 的宽 strip 中小节线仅占 44%，符干也类似高度，互相混淆；
        # 0.5× margin 的窄 strip 中小节线占 ~80%，可用高内核安全过滤符干。
        inner_top    = system.y_inner_top    if system.y_inner_top    else system.y_top
        inner_bottom = system.y_inner_bottom if system.y_inner_bottom else system.y_bottom
        strip = gray[inner_top: inner_bottom]
        if strip.size == 0:
            return [0, gray.shape[1] - 1]
        sh, sw = strip.shape

        _, binary = _cv2.threshold(strip, 0, 255, _cv2.THRESH_BINARY_INV + _cv2.THRESH_OTSU)

        # 内核高度 65%：小节线在紧 strip 中约占 80%，65% 能保留小节线且过滤大多数符干
        kernel_h = max(3, int(sh * 0.65))
        v_kernel = _cv2.getStructuringElement(_cv2.MORPH_RECT, (1, kernel_h))
        v_lines  = _cv2.morphologyEx(binary, _cv2.MORPH_OPEN, v_kernel)

        col_sums = np.sum(v_lines // 255, axis=0)
        # 阈值 45%：小节线（~80%）pass，短元素 fail
        threshold = sh * 0.45
        is_barline = col_sums >= threshold

        # 聚类成 x 质心
        xs: list[int] = []
        in_bar = False
        bs = 0
        for x in range(sw):
            if is_barline[x] and not in_bar:
                in_bar = True
                bs = x
            elif not is_barline[x] and in_bar:
                in_bar = False
                xs.append((bs + x - 1) // 2)
        if in_bar:
            xs.append((bs + sw - 1) // 2)

        # 若已知每行小节数，从候选中选取最靠近等距网格的 n_measures-1 个内部 barline
        if n_measures > 1 and len(xs) > n_measures + 1:
            target_spacing = sw / n_measures
            # 允许捕捉到候选点的最大偏差：0.45 倍小节宽度；超过则用等间距回退
            max_snap = target_spacing * 0.45
            selected: list[int] = []
            for i in range(1, n_measures):
                expected = int(i * target_spacing)
                best = min(xs, key=lambda x: abs(x - expected))
                selected.append(best if abs(best - expected) <= max_snap else expected)
            xs = sorted(set(selected))

        # 确保左右边界
        result = sorted(set([0] + xs + [sw - 1]))
        return result
    except Exception as exc:
        LOGGER.warning('detect_barlines 失败: %s', exc)
        return [0, gray.shape[1] - 1]


# ─────────────────────────────────────────────────────────────────────────────
# 音符像素位置估算
# ─────────────────────────────────────────────────────────────────────────────

def _estimate_note_x(
    measure_in_system: int,       # 0-based, 在本 system 内的小节编号
    beat_fraction: float,         # 0.0–1.0（拍位占小节比例）
    barlines: list[int],          # 小节线 x 坐标（含左右边界）
    measures_in_system: int,      # 本 system 内总小节数
) -> int:
    """估算音符在图像中的 x 坐标（像素）。"""
    # barlines 应有 measures_in_system+1 条（每小节左右两端）
    n_segs = len(barlines) - 1
    seg_idx = min(measure_in_system, n_segs - 1) if n_segs > 0 else 0

    if n_segs <= 0:
        return barlines[0]

    x_left  = barlines[min(seg_idx,     len(barlines)-1)]
    x_right = barlines[min(seg_idx + 1, len(barlines)-1)]
    x = x_left + int((x_right - x_left) * max(0.0, min(1.0, beat_fraction)))
    return x


# ─────────────────────────────────────────────────────────────────────────────
# CV 弧形检测
# ─────────────────────────────────────────────────────────────────────────────

def _has_tie_arc_cv(crop_bgr: 'np.ndarray') -> Optional[bool]:
    """在 BGR 裁剪图像中用 CV 检测延音线弧。

    延音线特征：
    - 薄弯弧（宽度远大于高度）
    - 跨越裁剪宽度的 ≥25%
    - 位于谱行中间位置（非谱线区域）
    - 非实心填充（轮廓面积 << 外接矩形面积）

    返回 True（检测到弧）、False（无弧）、None（无法判断）。
    """
    if not _HAS_CV2 or not _HAS_NUMPY:
        return None
    try:
        h, w = crop_bgr.shape[:2]
        if h < 5 or w < 10:
            return None

        gray   = _cv2.cvtColor(crop_bgr, _cv2.COLOR_BGR2GRAY)
        _, bin_img = _cv2.threshold(gray, 0, 255, _cv2.THRESH_BINARY_INV + _cv2.THRESH_OTSU)

        # 去除谱线（水平形态学腐蚀）
        h_len   = max(3, int(w * 0.35))
        h_kern  = _cv2.getStructuringElement(_cv2.MORPH_RECT, (h_len, 1))
        no_staff = _cv2.morphologyEx(bin_img, _cv2.MORPH_OPEN, h_kern)
        without_staff = cv2_bitwise_xor_approx(bin_img, no_staff)

        # 去除竖直元素（茎线 stem）
        v_kern  = _cv2.getStructuringElement(_cv2.MORPH_RECT, (1, max(3, h // 4)))
        no_stem = _cv2.morphologyEx(without_staff, _cv2.MORPH_OPEN, v_kern)
        remainder = _cv2.subtract(without_staff, no_stem)

        # 膨胀后找轮廓
        dilate_k = _cv2.getStructuringElement(_cv2.MORPH_RECT, (3, 3))
        dilated  = _cv2.dilate(remainder, dilate_k, iterations=1)

        contours, _ = _cv2.findContours(dilated, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)

        min_arc_w = max(5, int(w * _MIN_ARC_WIDTH_RATIO))

        for cnt in contours:
            bx, by, bw, bh = _cv2.boundingRect(cnt)
            if bw < min_arc_w:
                continue
            if bh == 0:
                continue
            aspect = bw / bh
            if aspect < (1 / _MAX_ARC_ASPECT):
                continue
            area = float(_cv2.contourArea(cnt))
            rect_area = float(bw * bh)
            if rect_area < 1:
                continue
            fill_ratio = area / rect_area
            # 弧轮廓的填充率应 < 0.6（弧线本身区域远小于外接矩形）
            if fill_ratio > 0.60:
                continue
            # y 位置：不应过于靠近图像顶部或底部（谱线区域）
            center_y = by + bh / 2
            if center_y < h * 0.10 or center_y > h * 0.90:
                continue
            LOGGER.debug('CV 弧检测命中: bw=%d bh=%d aspect=%.2f fill=%.2f', bw, bh, aspect, fill_ratio)
            return True

        return False
    except Exception as exc:
        LOGGER.warning('_has_tie_arc_cv 失败: %s', exc)
        return None


def cv2_bitwise_xor_approx(a: 'np.ndarray', b: 'np.ndarray') -> 'np.ndarray':
    """a AND NOT b（模拟去除 b 的部分）。"""
    not_b = _cv2.bitwise_not(b)
    return _cv2.bitwise_and(a, not_b)


# ─────────────────────────────────────────────────────────────────────────────
# oemer 推理接口（可选）
# ─────────────────────────────────────────────────────────────────────────────

def _get_oemer_dir() -> Optional[Path]:
    """返回 oemer 源码目录（omr_engine/oemer），若不存在则返回 None。"""
    base = get_app_base_dir()
    oemer_dir = base / 'omr_engine' / OEMER_REPO_DIR_NAME
    if oemer_dir.is_dir():
        return oemer_dir
    return None


def _oemer_models_available() -> bool:
    """检查 oemer 的 ONNX 模型文件是否已下载就绪。"""
    oemer_dir = _get_oemer_dir()
    if oemer_dir is None:
        return False
    ckpt = oemer_dir / 'oemer' / 'checkpoints'
    unet_model  = ckpt / 'unet_big' / 'model.onnx'
    seg_model   = ckpt / 'seg_net'  / 'model.onnx'
    return unet_model.exists() and seg_model.exists()


def _run_oemer_on_crop(crop_bgr: 'np.ndarray') -> Optional[bool]:
    """将裁剪图像保存为临时 PNG，使用 oemer 的 unet_big 分割模型检测弧形符号。

    策略：
    - unet_big 的 class_map: 0=背景, 1=谱线, 2=符号（包含延音线、符头、小节线等）
    - 在 'symbols'（class==2）区域中查找横向弧形轮廓
    - 若找到满足条件的弧形 → 返回 True
    - 若无符号像素或无弧形 → 返回 False
    - 若推理失败 → 返回 None（回退到 CV）
    """
    if not _oemer_models_available() or not _HAS_CV2 or not _HAS_NUMPY:
        return None

    oemer_dir = _get_oemer_dir()
    if oemer_dir is None:
        return None

    # 加入 oemer 路径
    oemer_src = str(oemer_dir)
    if oemer_src not in sys.path:
        sys.path.insert(0, oemer_src)

    try:
        # 抑制 oemer 内部日志
        old_env = os.environ.get('TF_CPP_MIN_LOG_LEVEL')
        os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

        import onnxruntime as _rt
        import pickle

        ckpt_dir  = oemer_dir / 'oemer' / 'checkpoints' / 'unet_big'
        onnx_path = ckpt_dir / 'model.onnx'
        meta_path = ckpt_dir / 'metadata.pkl'

        with open(str(meta_path), 'rb') as f:
            meta = pickle.load(f)

        input_shape  = meta['input_shape']
        output_shape = meta['output_shape']
        win_size     = input_shape[1]

        # 将裁剪图转换为 PIL 兼容格式
        h, w = crop_bgr.shape[:2]
        # 如果裁剪图太小，补边到 win_size × win_size
        pad_h = max(win_size, h)
        pad_w = max(win_size, w)
        canvas = np.ones((pad_h, pad_w, 3), dtype=np.uint8) * 255
        canvas[:h, :w] = crop_bgr

        # 保存到临时文件供 oemer 读取
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tf:
            tmp_png = tf.name
        _cv2.imwrite(tmp_png, canvas)

        try:
            providers = [
                ('CUDAExecutionProvider', {'device_id': 0}),
                'CPUExecutionProvider',
            ]
            sess = _rt.InferenceSession(str(onnx_path), providers=providers)
            output_names = meta['output_names']

            from PIL import Image as _PIL
            img_pil = _PIL.open(tmp_png).convert('RGB')
            img_arr = np.array(img_pil)[:pad_h, :pad_w]

            # 单 patch 推理
            patch = img_arr[:win_size, :win_size].astype(np.float32)
            patch = patch[np.newaxis, ...]  # shape (1, win, win, 3)
            out   = sess.run(output_names, {'input': patch})[0]  # (1, win, win, C)
            class_map = np.argmax(out[0], axis=-1)               # (win, win)

            # class==2 是"符号"层（包含延音线）
            sym_mask = (class_map == 2).astype(np.uint8) * 255

            # 在符号掩码中检测横向弧形
            # 将掩码扩展回原始 BGR 格式供 CV 弧检测
            sym_bgr = np.stack([sym_mask[:h, :w]] * 3, axis=-1)
            result  = _has_tie_arc_cv(sym_bgr)
            return result

        finally:
            try:
                os.remove(tmp_png)
            except OSError:
                pass

        if old_env is None:
            os.environ.pop('TF_CPP_MIN_LOG_LEVEL', None)
        else:
            os.environ['TF_CPP_MIN_LOG_LEVEL'] = old_env

    except Exception as exc:
        LOGGER.debug('_run_oemer_on_crop 失败（回退到 CV）: %s', exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 综合检测入口
# ─────────────────────────────────────────────────────────────────────────────

def detect_tie_in_crop(crop_bgr: 'np.ndarray') -> Optional[bool]:
    """检测裁剪图像中是否存在延音线弧。

    优先使用 oemer unet_big 推理（若模型可用），否则回退到 CV 弧检测。

    Returns
    -------
    True     确认存在弧形（延音线）
    False    确认不存在弧形
    None     无法判断（置信度不足）
    """
    # 优先 oemer
    oemer_result = _run_oemer_on_crop(crop_bgr)
    if oemer_result is not None:
        LOGGER.debug('detect_tie_in_crop: oemer 结果 = %s', oemer_result)
        return oemer_result

    # 回退 CV
    cv_result = _has_tie_arc_cv(crop_bgr)
    LOGGER.debug('detect_tie_in_crop: CV 结果 = %s', cv_result)
    return cv_result


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def run_oemer_tie_reconstruction(
    mxl_path:   Path,
    image_path: Path,
    progress_cb: Optional[callable] = None,   # progress_cb(done: int, total: int)
) -> TieReconstructionResult:
    """对 homr 生成的 MusicXML 进行视觉 tie 重建。

    Parameters
    ----------
    mxl_path    : homr 生成的 .musicxml 或 .mxl 文件路径。
    image_path  : 原始预处理图像路径（PNG/JPG）。
    progress_cb : 进度回调，签名 (done: int, total: int)。

    Returns
    -------
    TieReconstructionResult
        confirmed     — 视觉确认为 tie 的候选（可直接写回 XML）
        rejected      — 视觉确认不是 tie 的候选
        review_items  — 需要人工审核的候选（跨行 / 无法确定）
    """
    log_message(f'[oemer-tie] 开始分析: {mxl_path.name}')

    # ── 1. 解析候选对 ────────────────────────────────────────────────────────
    try:
        candidates, tree, ns, xml_path = parse_candidates(mxl_path)
    except Exception as exc:
        log_message(f'[oemer-tie] MusicXML 解析失败: {exc}', logging.ERROR)
        raise

    log_message(f'[oemer-tie] 找到 {len(candidates)} 个候选 tie 对')

    if not candidates:
        return TieReconstructionResult(
            confirmed=[], rejected=[], review_items=[],
            _xml_tree=tree, _xml_ns=ns, _source_path=xml_path,
        )

    # ── 2. 加载图像 ──────────────────────────────────────────────────────────
    img_bgr  = _load_image_bgr(image_path)
    img_gray = (_cv2.cvtColor(img_bgr, _cv2.COLOR_BGR2GRAY)
                if img_bgr is not None and _HAS_CV2 else None)

    if img_gray is None:
        reason = '缺少 opencv-python' if not _HAS_CV2 else f'无法读取文件 {image_path.name}'
        log_message(f'[oemer-tie] 无法加载图像（{reason}），所有候选设为人工审核',
                    logging.WARNING)
        review_items = [
            TieReviewItem(candidate=c, kind=TieDecisionKind.AMBIGUOUS, crop_bytes=None)
            for c in candidates
        ]
        return TieReconstructionResult(
            confirmed=[], rejected=[], review_items=review_items,
            _xml_tree=tree, _xml_ns=ns, _source_path=xml_path,
        )

    img_h, img_w = img_gray.shape

    # ── 2.5. homr SegNet 音符头检测（可选，精化像素位置）────────────────────
    # 仅使用 notehead 通道获取精确的音符头质心，用于后续 x 坐标吸附。
    # staff 通道不用于谱行检测（实测对复杂背景图片会将全图误识别为单一系统）。
    _homr_centroids: list[tuple[int, int]] = []
    if img_bgr is not None:
        _segnet_result = _run_homr_segnet(img_bgr, image_path)
        if _segnet_result is not None:
            _notehead_mask, _homr_staff_mask = _segnet_result
            _homr_centroids = _extract_notehead_centroids(_notehead_mask)
            log_message(
                f'[oemer-tie] homr SegNet 就绪，检测到 {len(_homr_centroids)} 个音符头'
            )

    # ── 3. 统计总小节数（用于 barline 检测 mps 参数） ───────────────────────
    root = tree.getroot()
    total_measures = max(
        (len(list(part.findall(f'{ns}measure')))
         for part in root.findall(f'{ns}part')),
        default=1,
    )
    total_measures = max(total_measures, 1)

    # ── 4. 检测谱行（system）与小节线 ────────────────────────────────────────
    # 始终使用传统 Otsu+形态学方法检测谱行。
    # homr staff_mask 虽经 NN 训练，但对背景复杂的照片（木纹、手机拍摄等）
    # 会将大量背景噪点误判为谱线，导致所有线段聚合成一个跨全图的巨型"系统"，
    # 反而比传统方法更差（已实测 image_test.jpg：homr=1 系统 3588px，传统=4 系统各约 200px）。
    systems = detect_staff_systems(img_gray)
    if not systems:
        systems = [StaffSystem(y_top=0, y_bottom=img_h)]
    log_message(f'[oemer-tie] 检测到 {len(systems)} 个谱行')

    n_systems             = len(systems)
    measures_per_system_f = total_measures / n_systems  # 浮点，每行平均小节数

    # 为每个谱行检测小节线（传入 mps 以启用网格筛选，消除符干等误检）
    for si, sys in enumerate(systems):
        mps_si = max(int((si + 1) * measures_per_system_f) - int(si * measures_per_system_f), 1)
        sys.barline_xs = detect_barlines(img_gray, sys, n_measures=mps_si)

    def _measure_to_system(m_idx: int) -> int:
        return min(int(m_idx / measures_per_system_f), n_systems - 1)

    def _measure_in_system(m_idx: int, sys_idx: int) -> int:
        offset = int(sys_idx * measures_per_system_f)
        return m_idx - offset

    def _measures_count_in_system(sys_idx: int) -> int:
        start = int(sys_idx       * measures_per_system_f)
        end   = int((sys_idx + 1) * measures_per_system_f)
        return max(end - start, 1)

    # 音符头吸附搜索半径（在候选对处理中使用）
    # 横向：平均小节宽度的 1/4，最小 50px；过大会跨小节抓到错误音符头
    _avg_meas_w = max(img_w // max(total_measures, 1), 60)
    _snap_rx = max(_avg_meas_w // 4, 50)     # 横向搜索半径
    _snap_ry_factor = 0.6                    # 纵向：0.6 倍谱行高度

    # ── 5. 处理每个候选对 ────────────────────────────────────────────────────
    confirmed:    list[TieCandidate] = []
    rejected:     list[TieCandidate] = []
    review_items: list[TieReviewItem] = []

    total = len(candidates)
    for done, cand in enumerate(candidates):
        if progress_cb:
            try:
                progress_cb(done, total)
            except Exception:
                pass

        sys_a = _measure_to_system(cand.measure_a)
        sys_b = _measure_to_system(cand.measure_b)

        # ── 换行检测 ──────────────────────────────────────────────────────────
        if sys_a != sys_b:
            log_message(
                f'[oemer-tie] {cand.label}: 跨谱行 (system {sys_a}→{sys_b})，标记为人工审核',
                logging.DEBUG,
            )
            review_items.append(TieReviewItem(
                candidate=cand,
                kind=TieDecisionKind.LINEBREAK,
                crop_bytes=None,
            ))
            continue

        # ── 同谱行：估算音符位置并切片 ──────────────────────────────────────
        sys_obj = systems[sys_a]
        barlines = sys_obj.barline_xs
        mps = _measures_count_in_system(sys_a)

        local_a = _measure_in_system(cand.measure_a, sys_a)
        local_b = _measure_in_system(cand.measure_b, sys_a)

        # 音符 A/B 在小节内的拍位比例
        # 始终使用音符"头部"tick 偏移（tick - measure_start）/ 小节理论长度
        # 不使用 beat_end_a（音符尾/小节线位置），否则跨小节时 xa ≈ xb 在小节线处
        beats_per_measure = max(cand.beats_in_measure_qn, 1.0)
        meas_ticks = beats_per_measure * max(cand.divisions, 1)
        beat_frac_a = max(0.0, min(0.99, (cand.tick_a - cand.measure_start_tick_a) / meas_ticks))
        beat_frac_b = max(0.0, min(0.99, (cand.tick_b - cand.measure_start_tick_b) / meas_ticks))

        x_a = _estimate_note_x(local_a, beat_frac_a, barlines, mps)
        x_b = _estimate_note_x(local_b, beat_frac_b, barlines, mps)

        # 等宽回退判断：
        # 1. 跨小节且 xa >= xb（位置倒置，barline 检测错误）
        # 2. 任意 local 索引超出检测到的段数（被截断到错误段位）
        #    → 两种情况下 _estimate_note_x 的结果都不可信，改用等距小节宽度估算
        n_segs = len(barlines) - 1
        _needs_eqw = (
            (cand.measure_a != cand.measure_b and x_a >= x_b)
            or (n_segs > 0 and (local_a >= n_segs or local_b >= n_segs))
        )
        if _needs_eqw:
            sys_left  = barlines[0]  if barlines else 0
            sys_right = barlines[-1] if barlines else img_w
            seg_w = max((sys_right - sys_left) // max(mps, 1), 2 * _CROP_X_MARGIN)
            x_a = sys_left + local_a * seg_w + max(0, int(seg_w * beat_frac_a))
            x_b = sys_left + local_b * seg_w + max(0, int(seg_w * beat_frac_b))
            x_a = max(sys_left, min(sys_right - 1, x_a))
            x_b = max(x_a,      min(sys_right,     x_b))
            # 跨小节时若等宽估算仍倒置：令 A 在段末、B 在段头
            if cand.measure_a != cand.measure_b and x_a >= x_b:
                x_a = max(sys_left, sys_left + (local_a + 1) * seg_w - _CROP_X_MARGIN)
                x_b = min(sys_right, sys_left + local_b * seg_w + _CROP_X_MARGIN)
                x_a = max(sys_left, min(sys_right - 1, x_a))
                x_b = max(x_a + 1,  min(sys_right, x_b))

        # ── homr 音符头位置吸附（可选精化）────────────────────────────────────
        # 若 homr SegNet 可用，将估算的 x_a/x_b 吸附到附近最近的实际音符头质心。
        # 安全约束：
        #   1. B 搜索时排除 A 已吸附的质心 x，防止两者坍缩到同一点
        #   2. snap 后若间距 < 原始间距的一半（或 < _CROP_X_MARGIN），则回退到原始估算值
        if _homr_centroids:
            x_a_orig = x_a
            x_b_orig = x_b
            orig_sep = abs(x_b_orig - x_a_orig)

            y_center = (sys_obj.y_top + sys_obj.y_bottom) // 2
            _snap_ry = max(int(sys_obj.height * _snap_ry_factor), 60)
            snapped_a = _snap_to_nearest_notehead(x_a, y_center, _homr_centroids, _snap_rx, _snap_ry)
            # B 排除 A 吸附的 x 坐标，防止两音符映射到同一质心
            snapped_b = _snap_to_nearest_notehead(x_b, y_center, _homr_centroids, _snap_rx, _snap_ry,
                                                   exclude_cx=snapped_a)

            _xa_new = snapped_a if snapped_a is not None else x_a
            _xb_new = snapped_b if snapped_b is not None else x_b
            # 修正方向
            if _xa_new > _xb_new and cand.measure_a == cand.measure_b:
                _xa_new, _xb_new = _xb_new, _xa_new

            new_sep = abs(_xb_new - _xa_new)
            # 最小间距保护：snap 后间距过小则回退
            _min_snap_sep = max(orig_sep // 2, _CROP_X_MARGIN)
            if new_sep >= _min_snap_sep:
                if snapped_a is not None:
                    LOGGER.debug('[oemer-tie] %s A: x_est=%d → snap=%d', cand.label, x_a, _xa_new)
                if snapped_b is not None:
                    LOGGER.debug('[oemer-tie] %s B: x_est=%d → snap=%d', cand.label, x_b, _xb_new)
                x_a, x_b = _xa_new, _xb_new
            else:
                LOGGER.debug('[oemer-tie] %s snap 回退：orig_sep=%d new_sep=%d < min=%d',
                             cand.label, orig_sep, new_sep, _min_snap_sep)

        # 裁剪窗口：[min(xa,xb)-margin, max(xa,xb)+margin]
        # 最大宽度限制为 2 倍估算小节宽度，防止 barline 误检时切出整谱行（包含无关音符）
        x_lo = min(x_a, x_b)
        x_hi = max(x_a, x_b)
        x1 = max(0, x_lo - _CROP_X_MARGIN)
        x2 = min(img_w, x_hi + _CROP_X_MARGIN)
        if len(barlines) >= 2:
            est_meas_w = max((barlines[-1] - barlines[0]) // max(mps, 1), 2 * _CROP_X_MARGIN)
        else:
            est_meas_w = max(img_w // max(mps, 1), 2 * _CROP_X_MARGIN)
        max_crop_w = max(2 * est_meas_w + 2 * _CROP_X_MARGIN, 4 * _CROP_X_MARGIN)
        if x2 - x1 > max_crop_w:
            cx = (x_a + x_b) // 2
            x1 = max(0, cx - max_crop_w // 2)
            x2 = min(img_w, cx + max_crop_w // 2)
        y1 = max(0, sys_obj.y_top    - _CROP_Y_MARGIN)
        y2 = min(img_h, sys_obj.y_bottom + _CROP_Y_MARGIN)

        if x2 <= x1 or y2 <= y1:
            review_items.append(TieReviewItem(
                candidate=cand,
                kind=TieDecisionKind.AMBIGUOUS,
                crop_bytes=None,
            ))
            continue

        crop = img_bgr[y1:y2, x1:x2]
        crop_bytes = _encode_png(crop)

        # ── 调用 tie 检测 ────────────────────────────────────────────────────
        result = detect_tie_in_crop(crop)

        if result is True:
            LOGGER.debug('[oemer-tie] %s → CONFIRMED TIE', cand.label)
            confirmed.append(cand)
            review_items.append(TieReviewItem(
                candidate=cand,
                kind=TieDecisionKind.CONFIRMED_TIE,
                crop_bytes=crop_bytes,
                user_decision=True,   # 预选：是延音线
            ))
        elif result is False:
            LOGGER.debug('[oemer-tie] %s → REJECTED', cand.label)
            rejected.append(cand)
            review_items.append(TieReviewItem(
                candidate=cand,
                kind=TieDecisionKind.REJECTED,
                crop_bytes=crop_bytes,
                user_decision=False,  # 预选：不是延音线
            ))
        else:
            LOGGER.debug('[oemer-tie] %s → AMBIGUOUS（人工审核）', cand.label)
            review_items.append(TieReviewItem(
                candidate=cand,
                kind=TieDecisionKind.AMBIGUOUS,
                crop_bytes=crop_bytes,
            ))

    if progress_cb:
        try:
            progress_cb(total, total)
        except Exception:
            pass

    log_message(
        f'[oemer-tie] 完成: 确认 {len(confirmed)} 对, '
        f'拒绝 {len(rejected)} 对, '
        f'待审核 {len(review_items)} 对'
    )

    return TieReconstructionResult(
        confirmed=confirmed,
        rejected=rejected,
        review_items=review_items,
        _xml_tree=tree,
        _xml_ns=ns,
        _source_path=xml_path,
    )


def _encode_png(bgr: 'np.ndarray') -> Optional[bytes]:
    """将 BGR 图像数组编码为 PNG 字节串。"""
    if not _HAS_CV2 or bgr is None or bgr.size == 0:
        return None
    ok, buf = _cv2.imencode('.png', bgr)
    if ok:
        return bytes(buf)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 写回 XML
# ─────────────────────────────────────────────────────────────────────────────

def apply_confirmed_ties(
    result:      TieReconstructionResult,
    output_path: Path,
    extra_confirmed: Optional[list[TieCandidate]] = None,
) -> int:
    """将确认的延音线写入 XML 并保存到 output_path。

    Parameters
    ----------
    result          : run_oemer_tie_reconstruction() 的返回值。
    output_path     : 输出 .musicxml 文件路径。
    extra_confirmed : 人工审核后额外确认的候选列表（可选）。

    Returns
    -------
    int  写入的 tie 对数。
    """
    if result._xml_tree is None:
        raise ValueError('TieReconstructionResult 不包含有效的 XML 树')

    ns = result._xml_ns

    all_confirmed = list(result.confirmed)
    if extra_confirmed:
        all_confirmed.extend(extra_confirmed)

    tie_count = 0
    for cand in all_confirmed:
        _add_tie_to_note(cand.elem_a, 'start', ns)
        _add_tie_to_note(cand.elem_b, 'stop',  ns)
        tie_count += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result._xml_tree.write(str(output_path), encoding='unicode', xml_declaration=True)

    log_message(f'[oemer-tie] 写入 {tie_count} 对延音线 → {output_path.name}')
    return tie_count
