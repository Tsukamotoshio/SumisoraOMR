# core/render/jianpu_runner.py — jianpu-ly 工具查找、运行、多声部合并、反复小节注入
# 拆分自 lilypond_runner.py
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# 在 Windows 上，作为 GUI 程序运行时防止子进程弹出新控制台窗口
_WIN_NO_WINDOW: int = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

from ..config import (
    LILYPOND_RUNTIME_DIR_NAME,
    LOGGER,
    MAX_JIANPU_LY_SECONDS,
)
from ..utils import (
    find_packaged_runtime_dir,
    get_app_base_dir,
    get_runtime_search_roots,
    log_message,
)


# ──────────────────────────────────────────────
# jianpu-ly 工具查找与运行
# ──────────────────────────────────────────────

def find_jianpu_ly_command() -> Optional[str]:
    """Look for a jianpu-ly command on PATH."""
    for candidate in ['jianpu-ly', 'jianpu-ly.py']:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def find_jianpu_ly_module() -> bool:
    """Check whether jianpu_ly is installed as a Python module."""
    try:
        return importlib.util.find_spec('jianpu_ly') is not None
    except Exception:
        return False


def find_jianpu_ly_script() -> Optional[Path]:
    """Look for jianpu-ly.py in cwd, the app base directory, and scripts/."""
    script_dir = get_app_base_dir()
    for base in [Path.cwd(), script_dir, script_dir / 'scripts']:
        path = base / 'jianpu-ly.py'
        if path.exists():
            return path
    return None


def _ensure_jianpu_script() -> Optional[Path]:
    """Locate the vendored jianpu-ly.py; no runtime download.

    jianpu-ly.py 随仓库/安装包分发（scripts/jianpu-ly.py，固定版本 + 本地补丁）。
    早期实现会在缺失时联网下载并直接执行，无任何哈希校验——源站被篡改或中间人
    攻击即任意代码执行，且下载到的上游版不含本地补丁，行为与开发机不一致，故移除。
    """
    script_path = find_jianpu_ly_script()
    if script_path is None:
        log_message(
            '未找到 jianpu-ly.py（应随应用分发，位于程序目录或 scripts/ 下）。',
            logging.WARNING,
        )
    return script_path


def find_python_script_command() -> Optional[list[str]]:
    """Find a usable Python interpreter command, preferring the bundled one."""
    candidates: list[list[str]] = []
    packaged_lilypond_dir = find_packaged_runtime_dir(LILYPOND_RUNTIME_DIR_NAME)
    if packaged_lilypond_dir is not None:
        candidates.append([str(packaged_lilypond_dir / 'bin' / 'python.exe')])

    for base_dir in get_runtime_search_roots():
        candidates.extend([
            [str(base_dir / 'python.exe')],
            [str(base_dir / 'Python' / 'python.exe')],
            [str(base_dir / '_internal' / 'python.exe')],
        ])

    sys_executable_path = Path(sys.executable)
    if sys_executable_path.name.lower().startswith('python'):
        candidates.insert(0, [str(sys_executable_path)])

    seen: set[str] = set()
    for candidate in candidates:
        candidate_path = Path(candidate[0])
        candidate_key = str(candidate_path).lower()
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        if candidate_path.exists() and candidate_path.is_file():
            return candidate

    for command_name in ('python.exe', 'python'):
        found = shutil.which(command_name)
        if found:
            return [found]

    py_launcher = shutil.which('py')
    if py_launcher:
        return [py_launcher, '-3']

    return None


def render_jianpu_ly(txt_path: Path, ly_path: Path) -> bool:
    """Convert a jianpu-ly text file to a LilyPond .ly file (tries command > module > local script)."""
    import tempfile as _tempfile

    env = os.environ.copy()
    env['j2ly_sloppy_bars'] = '1'
    txt_path = txt_path.resolve()
    ly_path = ly_path.resolve()

    # jianpu-ly 不支持 # 开头的注释行（会报 "Unrecognised command #"），
    # 预处理：将原文件的 # 注释行过滤掉，传一个临时干净版本给 jianpu-ly。
    # 注意：简谱中 `#` 紧跟数字（例如 `#6`、`#1'`）表示升号音符，必须保留；
    # 仅当 `#` 后是空白/字母（真正的注释行）时才剥离。
    _comment_line_re = re.compile(r'^\s*#(?!\d)')
    _clean_path = txt_path
    _tmp_to_delete: Optional[Path] = None
    try:
        raw = txt_path.read_text(encoding='utf-8-sig', errors='replace')
        cleaned = '\n'.join(
            line for line in raw.splitlines()
            if not _comment_line_re.match(line)
        )
        _tmp = _tempfile.NamedTemporaryFile(
            mode='w', suffix='.jianpu.txt', delete=False,
            encoding='utf-8', dir=str(txt_path.parent),
        )
        _tmp.write(cleaned)
        _tmp.close()
        _clean_path = Path(_tmp.name)
        _tmp_to_delete = _clean_path
    except Exception as exc:
        log_message(f'预处理 jianpu.txt 失败，使用原文件: {exc}', logging.WARNING)

    try:
        cmd = find_jianpu_ly_command()
        if cmd is not None:
            try:
                with ly_path.open('w', encoding='utf-8') as out:
                    subprocess.run([cmd, str(_clean_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(txt_path.parent), env=env, creationflags=_WIN_NO_WINDOW, timeout=MAX_JIANPU_LY_SECONDS)
                return True
            except subprocess.TimeoutExpired:
                # 超时直接失败：三条 fallback 路径运行的是同一个 jianpu-ly 程序，换路径只会再挂一次
                log_message(f'jianpu-ly 命令执行超时（>{MAX_JIANPU_LY_SECONDS}s），已终止。', logging.WARNING)
                return False
            except subprocess.CalledProcessError as exc:
                log_message(f'jianpu-ly 命令执行失败: {exc.stderr.decode("utf-8", errors="ignore").strip()}', logging.WARNING)

        if find_jianpu_ly_module():
            try:
                with ly_path.open('w', encoding='utf-8') as out:
                    subprocess.run([sys.executable, '-m', 'jianpu_ly', str(_clean_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(txt_path.parent), env=env, creationflags=_WIN_NO_WINDOW, timeout=MAX_JIANPU_LY_SECONDS)
                return True
            except subprocess.TimeoutExpired:
                log_message(f'jianpu_ly 模块执行超时（>{MAX_JIANPU_LY_SECONDS}s），已终止。', logging.WARNING)
                return False
            except subprocess.CalledProcessError as exc:
                log_message(f'jianpu_ly 模块执行失败: {exc.stderr.decode("utf-8", errors="ignore").strip()}', logging.WARNING)

        script_path = _ensure_jianpu_script()
        if script_path is None:
            return False

        python_cmd = find_python_script_command()
        if python_cmd is None:
            log_message('未找到可用于执行 jianpu-ly.py 的 Python 解释器。', logging.WARNING)
            return False

        try:
            with ly_path.open('w', encoding='utf-8') as out:
                subprocess.run([*python_cmd, str(script_path), str(_clean_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(txt_path.parent), env=env, creationflags=_WIN_NO_WINDOW, timeout=MAX_JIANPU_LY_SECONDS)
            return True
        except subprocess.TimeoutExpired:
            log_message(f'jianpu-ly 脚本执行超时（>{MAX_JIANPU_LY_SECONDS}s），已终止。', logging.WARNING)
            return False
        except subprocess.CalledProcessError as exc:
            log_message(f'jianpu-ly 脚本执行失败: {exc.stderr.decode("utf-8", errors="ignore").strip()}', logging.WARNING)
            return False
    finally:
        if _tmp_to_delete is not None:
            try:
                _tmp_to_delete.unlink(missing_ok=True)
            except Exception:
                pass


def render_jianpu_ly_from_mxl(mxl_path: Path, ly_path: Path) -> bool:
    """Convert a MusicXML file directly to a LilyPond .ly file via jianpu-ly."""
    env = os.environ.copy()
    env['j2ly_sloppy_bars'] = '1'
    mxl_path = mxl_path.resolve()
    ly_path = ly_path.resolve()

    cmd = find_jianpu_ly_command()
    if cmd is not None:
        try:
            with ly_path.open('w', encoding='utf-8') as out:
                subprocess.run([cmd, str(mxl_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(mxl_path.parent), env=env, creationflags=_WIN_NO_WINDOW, timeout=MAX_JIANPU_LY_SECONDS)
            return True
        except subprocess.TimeoutExpired:
            log_message(f'jianpu-ly 命令处理 MXL 超时（>{MAX_JIANPU_LY_SECONDS}s），已终止。', logging.WARNING)
            return False
        except subprocess.CalledProcessError as exc:
            log_message(f'jianpu-ly 命令处理 MXL 失败: {exc.stderr.decode("utf-8", errors="ignore")}', logging.WARNING)

    if find_jianpu_ly_module():
        try:
            with ly_path.open('w', encoding='utf-8') as out:
                subprocess.run([sys.executable, '-m', 'jianpu_ly', str(mxl_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(mxl_path.parent), env=env, creationflags=_WIN_NO_WINDOW, timeout=MAX_JIANPU_LY_SECONDS)
            return True
        except subprocess.TimeoutExpired:
            log_message(f'jianpu_ly 模块处理 MXL 超时（>{MAX_JIANPU_LY_SECONDS}s），已终止。', logging.WARNING)
            return False
        except subprocess.CalledProcessError as exc:
            log_message(f'jianpu_ly 模块处理 MXL 失败: {exc.stderr.decode("utf-8", errors="ignore")}', logging.WARNING)

    script_path = _ensure_jianpu_script()
    if script_path is None:
        return False

    python_cmd = find_python_script_command()
    if python_cmd is None:
        log_message('未找到可用于执行 jianpu-ly.py 的 Python 解释器。', logging.WARNING)
        return False

    try:
        with ly_path.open('w', encoding='utf-8') as out:
            subprocess.run([*python_cmd, str(script_path), str(mxl_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(mxl_path.parent), env=env, creationflags=_WIN_NO_WINDOW, timeout=MAX_JIANPU_LY_SECONDS)
        return True
    except subprocess.TimeoutExpired:
        log_message(f'jianpu-ly 脚本处理 MXL 超时（>{MAX_JIANPU_LY_SECONDS}s），已终止。', logging.WARNING)
        return False
    except subprocess.CalledProcessError as exc:
        log_message(f'jianpu-ly 脚本处理 MXL 失败: {exc.stderr.decode("utf-8", errors="ignore")}', logging.WARNING)
        return False


# ──────────────────────────────────────────────
# Polyphonic jianpu stave merging
# ──────────────────────────────────────────────

# Patterns used by _merge_jianpu_voices (compiled once at module level for speed).
_STAFF_SPLIT_RE = re.compile(r'\}\s*\n(\s*\{)', re.DOTALL)
_TRANSPARENT_STEM_RE = re.compile(
    r"(\\override\s+Staff\.Stem\s+#'transparent\s*=\s*##t[^\n]*)"
)
_VOICE_OPEN_RE = re.compile(r'(\\new\s+Voice\s*=\s*"[^"]*"\s*\{)')

_VOICE_CMDS = ['\\voiceOne', '\\voiceTwo', '\\voiceThree', '\\voiceFour']


def _merge_jianpu_voices(
    section_contents: list[str],
    begin_marker: str,
    end_marker: str,
) -> str:
    """Combine 2–4 jianpu ``RhythmicStaff`` section bodies into one polyphonic staff."""
    staff_with_block: Optional[str] = None
    voice_inner_blocks: list[str] = []

    for i, content in enumerate(section_contents):
        m = _STAFF_SPLIT_RE.search(content)
        if not m:
            voice_inner_blocks.append(content.strip())
            continue

        if i == 0:
            staff_with_block = content[: m.start() + 1].strip()

        music_block = content[m.start(1):]
        inner = music_block.strip()
        if inner.startswith('{'):
            inner = inner[1:]
        inner = inner.rstrip()
        if inner.endswith('}'):
            inner = inner[:-1].rstrip()

        _rest_fix = '    \\override Rest.staff-position = #0\n'
        if i > 0:
            _rest_fix += '    \\override Rest.stencil = ##f\n'
        tm = _TRANSPARENT_STEM_RE.search(inner)
        if tm:
            line_end = inner.find('\n', tm.end())
            if line_end >= 0:
                inner = (
                    inner[: line_end + 1]
                    + f'    {_VOICE_CMDS[i]}\n'
                    + _rest_fix
                    + inner[line_end + 1 :]
                )
            else:
                inner = inner + f'\n    {_VOICE_CMDS[i]}\n' + _rest_fix
        else:
            vm = _VOICE_OPEN_RE.search(inner)
            if vm:
                pos = vm.end()
                inner = inner[:pos] + f' {_VOICE_CMDS[i]}\n' + _rest_fix + inner[pos:]

        voice_inner_blocks.append(inner)

    if not voice_inner_blocks or staff_with_block is None:
        result = begin_marker
        for content in section_contents:
            result += content
        result += end_marker
        return result

    voice_parts: list[str] = []
    for j, block in enumerate(voice_inner_blocks):
        if j > 0:
            voice_parts.append('        \\\\')
        indented = '\n'.join(
            ('        ' + line) if line.strip() else line
            for line in block.split('\n')
        )
        voice_parts.append(indented)

    combined = (
        begin_marker + '\n'
        + '    ' + staff_with_block + '\n'
        + '    {\n'
        + '        <<\n'
        + '\n'.join(voice_parts) + '\n'
        + '        >>\n'
        + '    }\n'
        + end_marker
    )
    return combined


def merge_polyphonic_jianpu_staves(
    ly_path: Path,
    voice_groups: list[list[int]],
) -> None:
    """Post-process a jianpu-ly-generated ``.ly`` file to render polyphonic voices
    on a single jianpu staff instead of separate staves.

    Parameters
    ----------
    ly_path
        Path to the ``.ly`` file to modify **in-place**.
    voice_groups
        A list of section-index groups returned by
        :func:`~core.notation.jianpu.build_jianpu_ly_text` with
        ``_return_groups=True``.  Each inner list contains the 0-based indices
        of the sections that belong to the same musical Part.  Groups with only
        one member are left unchanged.  Groups with 2–4 members have their
        ``RhythmicStaff`` sections merged into a single polyphonic staff.
    """
    if not voice_groups or not any(len(g) > 1 for g in voice_groups):
        return

    try:
        content = ly_path.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return

    SECTION_RE = re.compile(
        r'(%+\s*===\s*BEGIN JIANPU STAFF\s*===)(.*?)(%+\s*===\s*END JIANPU STAFF\s*===)',
        re.DOTALL,
    )
    sections = list(SECTION_RE.finditer(content))
    if not sections:
        return

    section_to_group: dict[int, list[int]] = {}
    for group in voice_groups:
        for idx in group:
            section_to_group[idx] = group

    replacements: list[tuple[int, int, str]] = []
    consumed: set[int] = set()

    for sec_idx, _match in enumerate(sections):
        if sec_idx in consumed:
            continue
        group = section_to_group.get(sec_idx, [sec_idx])
        if len(group) <= 1:
            continue

        group_matches = [sections[i] for i in group if i < len(sections)]
        if len(group_matches) < 2:
            continue

        first_m = group_matches[0]
        last_m = group_matches[-1]

        merged = _merge_jianpu_voices(
            [m.group(2) for m in group_matches],
            first_m.group(1),
            last_m.group(3),
        )

        replacements.append((first_m.start(), last_m.end(), merged))
        for i in group[1:]:
            consumed.add(i)

    if not replacements:
        return

    result = content
    for start, end, new_text in sorted(replacements, key=lambda t: t[0], reverse=True):
        result = result[:start] + new_text + result[end:]

    try:
        ly_path.write_text(result, encoding='utf-8')
        log_message(
            f'[jianpu] 已合并 {sum(len(g) for g in voice_groups if len(g) > 1)} 个声道为'
            f' {sum(1 for g in voice_groups if len(g) > 1)} 个多声部谱表',
            logging.DEBUG,
        )
    except OSError as exc:
        log_message(f'[jianpu] 多声部合并写入失败: {exc}', logging.WARNING)


# ──────────────────────────────────────────────────────────────────────────────
# Repeat barline injection
# ──────────────────────────────────────────────────────────────────────────────

def inject_repeat_barlines_to_ly(
    ly_path: Path,
    repeat_info: 'dict[int, dict[str, bool]]',
) -> None:
    """Inject \\bar repeat commands into the first Voice block of a LilyPond file.

    repeat_info maps measure index → {'start': bool, 'end': bool}.
    'start' inserts \\bar ".|:" before the measure's first note.
    'end'   inserts \\bar ":|." after  the measure's last note.
    Adjacent end+start across a barline becomes \\bar ":|.|:".
    Only the first \\new Voice block is modified; LilyPond propagates the
    barline change to the shared staff automatically.
    """
    if not repeat_info:
        return
    try:
        content = ly_path.read_text(encoding='utf-8', errors='ignore')
        result = _insert_repeat_bar_commands(content, repeat_info)
        if result != content:
            ly_path.write_text(result, encoding='utf-8')
            LOGGER.debug('inject_repeat_barlines_to_ly: injected %d repeat markers', len(repeat_info))
    except Exception as exc:
        LOGGER.warning('inject_repeat_barlines_to_ly failed: %s', exc)


def _first_voice_block_span(content: str) -> 'tuple[int, int] | None':
    """Return the (start, end) offsets of the body of the first \\new Voice block.

    Shared by both injectors on purpose: repeat barlines and volta brackets are
    placed against the same bar markers, so they must be looking at the same
    block — two copies of this search could quietly drift apart.
    """
    voice_re = re.compile(r'\\new\s+Voice\s*(?:=\s*"[^"]*")?\s*\{')
    m = voice_re.search(content)
    if not m:
        return None

    start = m.end()
    depth = 1
    pos = start
    while pos < len(content) and depth > 0:
        if content[pos] == '{':
            depth += 1
        elif content[pos] == '}':
            depth -= 1
        pos += 1
    return start, pos - 1


def _insert_repeat_bar_commands(content: str, repeat_info: 'dict[int, dict[str, bool]]') -> str:
    """Locate the first \\new Voice block and insert \\bar commands at measure boundaries."""
    span = _first_voice_block_span(content)
    if span is None:
        return content
    start, end = span
    block = content[start:end]
    modified = _inject_barlines_into_voice_block(block, repeat_info)
    return content[:start] + modified + content[end:]


def _inject_barlines_into_voice_block(block: str, repeat_info: 'dict[int, dict[str, bool]]') -> str:
    """Insert \\bar commands into a voice block using %{ bar N: %} bar-comment markers.

    jianpu-ly outputs the pattern:
        (notes) | (optional tie overrides) | %{ bar N: %} (next measure notes)

    N is the 1-based bar number of the measure STARTING after the marker.
    Therefore:  end_mi  = N - 2  (0-based index of measure that just ended)
                start_mi = N - 1  (0-based index of measure that's starting)
    """
    _START = r'\bar ".|:"'
    _END   = r'\bar ":|."'
    _BOTH  = r'\bar ":|.|:"'

    boundary_re = re.compile(r'\|\s*%\{\s*bar\s+(\d+)\s*:\s*%\}')
    matches = list(boundary_re.finditer(block))

    injections: list[tuple[int, str]] = []

    for m in matches:
        N = int(m.group(1))
        end_mi   = N - 2
        start_mi = N - 1

        has_end   = repeat_info.get(end_mi, {}).get('end', False)
        has_start = repeat_info.get(start_mi, {}).get('start', False)

        if has_end and has_start:
            injections.append((m.start(), f'{_BOTH} '))
        elif has_end:
            injections.append((m.start(), f'{_END} '))
        elif has_start:
            injections.append((m.end(), f' {_START}'))

    if matches:
        last_N = max(int(m.group(1)) for m in matches)
        last_mi = last_N - 1
        if repeat_info.get(last_mi, {}).get('end', False):
            final_m = re.search(r'\|\s*\\bar\s*"\|\."\s*$', block.rstrip())
            if final_m:
                injections.append((final_m.start(), f'{_END} '))

    result = block
    for pos, text in sorted(injections, key=lambda x: x[0], reverse=True):
        result = result[:pos] + text + result[pos:]

    if repeat_info.get(0, {}).get('start'):
        result = f'{_START} ' + result

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Volta (1./2. ending) bracket injection — stage 6.2a-2
# ──────────────────────────────────────────────────────────────────────────────

_BAR_MARKER_RE = re.compile(r'\|\s*%\{\s*bar\s+(\d+)\s*:\s*%\}')
_FINAL_BARLINE_RE = re.compile(r'\|\s*\\bar\s*"\|\."\s*$')
_VOLTA_LABEL_UNSAFE_RE = re.compile(r'[^0-9,.\- ]')


def _volta_label(number: str) -> str:
    """Turn a MusicXML ending number into a bracket label: ``'1'`` → ``'1.'``.

    MusicXML allows ``"1, 2"`` for an ending taken on several passes, so commas
    and spaces survive; anything else is dropped rather than written into a
    LilyPond string literal. Returns ``''`` when nothing printable is left.
    """
    label = _VOLTA_LABEL_UNSAFE_RE.sub('', str(number or '')).strip()
    if not label:
        return ''
    return label if label.endswith('.') else f'{label}.'


def inject_volta_brackets_to_ly(ly_path: Path, volta_brackets: 'list[dict]') -> None:
    """Draw volta (1./2. ending) brackets into the first Voice block of a .ly file.

    *volta_brackets* is ``[{'number': '1', 'first': i, 'last': j}, ...]`` with
    0-based measure indices, as produced by
    ``core.notation.jianpu.extract._extract_part_volta_brackets``.

    Uses LilyPond's manual ``\\set Score.repeatCommands`` rather than
    restructuring the music into ``\\repeat volta { } \\alternative { }``. That
    choice is forced by the data, not taste: OMR reads closing repeat dots and
    volta brackets far more reliably than the opening ``|:`` that would balance
    them (see render_midi_from_score), so a bracket with no matching start
    repeat is the normal shape of a recognised score. ``\\repeat volta`` needs a
    balanced structure and would reject exactly those scores; a manual bracket
    only draws what was recognised. The repeat *barlines* are still drawn by
    inject_repeat_barlines_to_ly, which this is meant to run after.

    Measure indices are placed against jianpu-ly's ``| %{ bar N: %}`` markers,
    the same convention inject_repeat_barlines_to_ly relies on: marker N sits
    immediately before 0-based measure ``N - 1``.
    """
    if not volta_brackets:
        return
    try:
        content = ly_path.read_text(encoding='utf-8', errors='ignore')
        result = _insert_volta_commands(content, volta_brackets)
        if result != content:
            ly_path.write_text(result, encoding='utf-8')
            LOGGER.debug('inject_volta_brackets_to_ly: injected %d volta brackets', len(volta_brackets))
    except Exception as exc:
        LOGGER.warning('inject_volta_brackets_to_ly failed: %s', exc)


def _insert_volta_commands(content: str, volta_brackets: 'list[dict]') -> str:
    span = _first_voice_block_span(content)
    if span is None:
        return content
    start, end = span
    block = content[start:end]
    modified = _inject_voltas_into_voice_block(block, volta_brackets)
    return content[:start] + modified + content[end:]


def _inject_voltas_into_voice_block(block: str, volta_brackets: 'list[dict]') -> str:
    """Insert one ``\\set Score.repeatCommands`` per affected bar boundary.

    关键在"同一个边界只发一条命令"：第一房结束、第二房开始往往落在同一根小节线
    上。\\set 是**赋值**，同一时刻连写两条，后一条会覆盖前一条——于是第一房永远
    关不上。LilyPond 文档给的正确写法是把两件事并进一个列表：
    ``#'((volta #f) (volta "2."))``，而且关闭必须排在开启之前。
    """
    markers = {int(m.group(1)): m for m in _BAR_MARKER_RE.finditer(block)}
    last_marker = max(markers) if markers else 0

    # 位置 → 这个时刻要做的 repeatCommands 列表项
    at_pos: 'dict[int, list[str]]' = {}
    at_head: 'list[str]' = []

    def _add(pos: 'int | None', item: str) -> None:
        if pos is None:
            at_head.append(item)
        else:
            at_pos.setdefault(pos, []).append(item)

    # 先逐条校验、再排序。反过来的话，排序键会先去比较一个坏条目里的字符串和
    # 别人的整数，直接抛 TypeError——外层 try 兜住了不会崩，但结果是**一条坏
    # 数据让整首曲子的括号全部静默消失**。元数据行是 # 注释，用户手改过就可能
    # 出现这种条目；逐条校验的本意是跳过坏的、保留好的。
    valid: 'list[tuple[int, int, str]]' = []
    for bracket in volta_brackets:
        try:
            first, last = int(bracket['first']), int(bracket['last'])
        except (KeyError, TypeError, ValueError):
            continue
        label = _volta_label(bracket.get('number', ''))
        if not label or first < 0 or last < first:
            continue
        valid.append((first, last, label))

    for first, last, label in sorted(valid):

        # 开：第 first 小节的第一个音符之前
        if first == 0:
            start_pos = None
        elif (first + 1) in markers:
            start_pos = markers[first + 1].end()
        else:
            continue      # 定位不到起点就整个不画——画一个起点错位的括号比不画更糟

        # 关：第 last 小节的最后一个音符之后，即下一小节的标记处
        if (last + 2) in markers:
            end_pos: 'int | None' = markers[last + 2].end()
        elif last + 1 >= last_marker:
            # 括号落在最后一小节上：它后面没有标记了，关在终止线之前
            final = _FINAL_BARLINE_RE.search(block.rstrip())
            end_pos = final.start() if final else len(block.rstrip())
        else:
            end_pos = -1  # 定位不到终点：只开不关，LilyPond 会一直画到曲末

        _add(start_pos, f'(volta "{label}")')
        if end_pos != -1:
            _add(end_pos, '(volta #f)')

    def _command(items: 'list[str]') -> str:
        # 关闭排在开启之前（见上面的说明）
        ordered = sorted(items, key=lambda it: 0 if it == '(volta #f)' else 1)
        return "\\set Score.repeatCommands = #'(" + ' '.join(ordered) + ')'

    result = block
    for pos in sorted(at_pos, reverse=True):
        result = result[:pos] + f' {_command(at_pos[pos])} ' + result[pos:]
    if at_head:
        result = f'{_command(at_head)} ' + result
    return result
