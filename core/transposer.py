# core/transposer.py — MusicXML 移调算法模块
# 独立的 XML / 音频移调，使用 music21 处理升降号补偿。
# 不依赖任何 GUI 库，可被任意调用方导入。
#
# 公开 API:
#   transpose_musicxml(src, dst, semitones=None, from_key=None, to_key=None) -> Path
#   get_transposition_semitones(from_key, to_key) -> int
#   detect_key_from_musicxml(mxl_path) -> str
#   strip_slurs_ties_from_xml(xml_path) -> Path  （CURVES 崩溃救援）

from __future__ import annotations

import logging
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

LOGGER = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 内部辅助
# ─────────────────────────────────────────────────────────────────────────────

def _parse_key_name(key_str: str) -> str:
    """将用户输入（如 'F', 'Bb', 'F#'）规范化为 music21 可识别的音名。

    jianpu 编辑器中常用 'b' 替代 '-' 表示降号。
    """
    # 将 'b' 后缀转为 music21 的 '-' 降号（仅处理 [A-G]b 格式）
    normalized = re.sub(r'^([A-Ga-g])b$', lambda m: m.group(1) + '-', key_str.strip())
    return normalized


def _semitone_diff(from_pitch_class: int, to_pitch_class: int) -> int:
    """返回从 from 到 to 的最短上行半音数（0-11）。"""
    diff = (to_pitch_class - from_pitch_class) % 12
    return diff


# ─────────────────────────────────────────────────────────────────────────────
# 公开 API
# ─────────────────────────────────────────────────────────────────────────────

def get_transposition_semitones(from_key: str, to_key: str) -> int:
    """计算从 from_key 到 to_key 的移调半音数（向上，0-11）。"""
    try:
        from music21 import pitch as m21pitch
        p_from = m21pitch.Pitch(_parse_key_name(from_key))
        p_to   = m21pitch.Pitch(_parse_key_name(to_key))
        return _semitone_diff(p_from.pitchClass, p_to.pitchClass)
    except Exception as exc:
        LOGGER.warning('get_transposition_semitones: 无法解析音名 %s → %s: %s', from_key, to_key, exc)
        return 0


def _major_key_from_fifths(fifths: int) -> str:
    mapping = {
        -7: 'Cb',
        -6: 'Gb',
        -5: 'Db',
        -4: 'Ab',
        -3: 'Eb',
        -2: 'Bb',
        -1: 'F',
         0: 'C',
         1: 'G',
         2: 'D',
         3: 'A',
         4: 'E',
         5: 'B',
         6: 'F#',
         7: 'C#',
    }
    return mapping.get(fifths, 'C')


def _parse_musicxml_key_signature(mxl_path: Path) -> Optional[str]:
    try:
        import zipfile
        if mxl_path.suffix.lower() == '.mxl':
            with zipfile.ZipFile(mxl_path, 'r') as z:
                xml_names = [n for n in z.namelist() if n.lower().endswith('.xml')]
                if not xml_names:
                    return None
                text = z.read(xml_names[0]).decode('utf-8', errors='ignore')
        else:
            text = mxl_path.read_text(encoding='utf-8', errors='ignore')
        import xml.etree.ElementTree as ET
        root = ET.fromstring(text)
        ns = ''
        if root.tag.startswith('{'):
            ns = root.tag.split('}')[0] + '}'
        attributes = root.find(f'.//{ns}attributes')
        if attributes is None:
            return None
        key_elem = attributes.find(f'{ns}key')
        if key_elem is None:
            return None
        fifths_elem = key_elem.find(f'{ns}fifths')
        mode_elem = key_elem.find(f'{ns}mode')
        if fifths_elem is None or fifths_elem.text is None:
            return None
        fifths = int(fifths_elem.text.strip())
        if mode_elem is None or not mode_elem.text:
            return _major_key_from_fifths(fifths)
        mode = mode_elem.text.strip().lower()
        if mode == 'major':
            return _major_key_from_fifths(fifths)
        if mode == 'minor':
            return _major_key_from_fifths(fifths)
        return _major_key_from_fifths(fifths)
    except Exception:
        return None


def detect_key_from_musicxml(mxl_path: Path) -> str:
    """从 MusicXML 文件中检测第一个调号；若无法检测则返回 'C'。"""
    try:
        explicit = _parse_musicxml_key_signature(mxl_path)
        if explicit is not None:
            return explicit
        from music21 import converter
        score = converter.parse(str(mxl_path))
        keys = score.flatten().getElementsByClass('Key')
        if keys:
            k = keys[0]
            tonic = k.tonic.name.replace('-', 'b')
            return tonic
        # 若没有显式 Key 元素，用 music21 分析
        analyzed = score.analyze('key')
        return analyzed.tonic.name.replace('-', 'b')
    except Exception as exc:
        LOGGER.warning('detect_key_from_musicxml 失败 (%s): %s', mxl_path.name, exc)
        return 'C'


def _strip_music21_creator(dst: Path) -> None:
    """移除 music21 在写出 MusicXML 时自动注入的 ``Music21`` 作曲者标记。

    music21 在原乐谱没有作曲者信息时会写出
    ``<creator type="composer">Music21</creator>``，这会出现在渲染后的 PDF 中。
    本函数对写出的文件（.musicxml 或 .mxl）做 XML 后处理，将其删除。
    """
    import zipfile

    def _clean_xml_bytes(raw: bytes) -> bytes:
        # 去掉 Music21 creator 标记（大小写不敏感匹配文本内容）
        return re.sub(
            rb'<creator[^>]*>\s*[Mm]usic21\s*</creator>\s*',
            b'',
            raw,
        )

    suffix = dst.suffix.lower()
    if suffix in ('.musicxml', '.xml'):
        raw = dst.read_bytes()
        cleaned = _clean_xml_bytes(raw)
        if cleaned != raw:
            dst.write_bytes(cleaned)
            LOGGER.debug('已移除 music21 creator 标记: %s', dst.name)
    elif suffix == '.mxl':
        # .mxl 是 ZIP 包，需要重建
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(dst, 'r') as zin, zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.lower().endswith('.xml'):
                    data = _clean_xml_bytes(data)
                zout.writestr(item, data)
        dst.write_bytes(buf.getvalue())
        LOGGER.debug('已移除 music21 creator 标记 (mxl): %s', dst.name)


def transpose_musicxml(
    src: Path,
    dst: Path,
    *,
    semitones: Optional[int] = None,
    from_key: Optional[str] = None,
    to_key: Optional[str] = None,
    progress_callback=None,
) -> Path:
    """将 MusicXML（.mxl / .musicxml）移调并写出到 dst。

    优先级：semitones > (from_key, to_key)。

    Parameters
    ----------
    src              : 原始 MusicXML 路径（.mxl 或 .musicxml）
    dst              : 输出路径（建议 .musicxml 后缀）
    semitones        : 直接指定半音偏移（正数向上移调，负数向下）
    from_key / to_key: 从哪个调移到哪个调，例如 from_key='C', to_key='F'
    progress_callback: 若提供，接受 (float 0.0-1.0) 的回调，用于进度更新

    Returns
    -------
    dst (Path)，如果成功写出。失败时抛出异常。
    """
    from music21 import converter, interval, pitch as m21pitch

    if semitones is None:
        if from_key is None or to_key is None:
            raise ValueError('transpose_musicxml: 须提供 semitones 或 (from_key, to_key)')
        semitones = get_transposition_semitones(from_key, to_key)

    if progress_callback:
        progress_callback(0.1)

    LOGGER.info('正在解析乐谱: %s', src)
    score = converter.parse(str(src))

    if progress_callback:
        progress_callback(0.5)

    LOGGER.info('移调 %+d 个半音…', semitones)
    transposed = score.transpose(semitones)

    if progress_callback:
        progress_callback(0.8)

    dst.parent.mkdir(parents=True, exist_ok=True)
    format_ = 'musicxml' if dst.suffix.lower() in ('.musicxml', '.xml') else 'mxl'
    transposed.write(format_, fp=str(dst))

    # 去掉 music21 自动注入的 "Music21" 作曲者标记，避免出现在 PDF 中
    _strip_music21_creator(dst)

    if progress_callback:
        progress_callback(1.0)

    LOGGER.info('移调完成 → %s', dst)
    return dst


# ─────────────────────────────────────────────────────────────────────────────
# CURVES 崩溃救援：删除连音线 / 延音线
# ─────────────────────────────────────────────────────────────────────────────

def strip_slurs_ties_from_xml(xml_path: Path, backup: bool = True) -> Path:
    """从 MusicXML 文件中移除所有连音线（slur）和延音线（tied/tie）元素。

    Audiveris 在 CURVES（连音线处理）步骤崩溃时，导出的 MusicXML 可能包含
    格式错误的 <slur> 或 <tied> 标签，导致后续解析失败。此函数通过纯 XML
    操作删除这些元素，不依赖 music21，以保证健壮性。

    Parameters
    ----------
    xml_path : 待处理的 .musicxml 或解压后的 XML 文件路径
    backup   : 是否在原地修改前备份（备份扩展名 .bak）

    Returns
    -------
    修改后（或未修改）的 xml_path（Path）
    """
    if not xml_path.exists():
        LOGGER.warning('strip_slurs_ties_from_xml: 文件不存在 %s', xml_path)
        return xml_path

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        # MusicXML 根元素带命名空间，需要 wildcard 匹配
        ns_prefix = ''
        if root.tag.startswith('{'):
            ns_prefix = root.tag.split('}')[0] + '}'

        removed = 0
        for parent in root.iter():
            # 需要先收集再删除，避免迭代中修改结构
            to_remove = []
            for child in list(parent):
                local = child.tag.replace(ns_prefix, '')
                if local in ('slur', 'tied', 'tie'):
                    to_remove.append(child)
            for child in to_remove:
                parent.remove(child)
                removed += 1

        if removed == 0:
            LOGGER.info('strip_slurs_ties_from_xml: 未找到连音线元素，文件未改变。')
            return xml_path

        if backup:
            shutil.copy2(xml_path, xml_path.with_suffix('.bak'))

        tree.write(xml_path, encoding='unicode', xml_declaration=True)
        LOGGER.info('strip_slurs_ties_from_xml: 已删除 %d 个连音线/延音线元素。', removed)
    except Exception as exc:
        LOGGER.warning('strip_slurs_ties_from_xml 失败: %s', exc)

    return xml_path


def strip_slurs_ties_from_mxl(mxl_path: Path, backup: bool = True) -> Path:
    """对 .mxl 压缩包执行连音线删除，原地替换压缩包。

    .mxl 是一个 ZIP 包，内含一个或多个 XML 文件。此函数对包内所有
    .xml 文件调用 :func:`strip_slurs_ties_from_xml`，然后重新打包。

    Returns
    -------
    修改后的 mxl_path（Path）
    """
    import zipfile
    import io

    if not mxl_path.exists():
        return mxl_path
    if mxl_path.suffix.lower() != '.mxl':
        # 直接处理 .musicxml
        return strip_slurs_ties_from_xml(mxl_path, backup=backup)

    try:
        tmp_dir = mxl_path.parent / f'_mxl_tmp_{mxl_path.stem}'
        tmp_dir.mkdir(exist_ok=True)

        with zipfile.ZipFile(mxl_path, 'r') as zin:
            zin.extractall(tmp_dir)

        for xml_file in tmp_dir.rglob('*.xml'):
            strip_slurs_ties_from_xml(xml_file, backup=False)

        if backup:
            shutil.copy2(mxl_path, mxl_path.with_suffix('.mxl.bak'))

        # 重新打包
        with zipfile.ZipFile(mxl_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for f in tmp_dir.rglob('*'):
                if f.is_file():
                    zout.write(f, f.relative_to(tmp_dir))

        shutil.rmtree(tmp_dir, ignore_errors=True)
        LOGGER.info('strip_slurs_ties_from_mxl: 已重新打包 %s', mxl_path.name)
    except Exception as exc:
        LOGGER.warning('strip_slurs_ties_from_mxl 失败: %s', exc)

    return mxl_path
