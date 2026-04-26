# core/lilypond_runner.py — LilyPond / jianpu-ly 工具查找与渲染
# 拆分自 runtime_finder.py
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional

# 在 Windows 上，作为 GUI 程序运行时防止子进程弹出新控制台窗口
_WIN_NO_WINDOW: int = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

from ..config import (
    JIANPU_LY_URLS,
    LILYPOND_RUNTIME_DIR_NAME,
    LOGGER,
)
from ..utils import (
    find_packaged_runtime_dir,
    get_app_base_dir,
    get_runtime_search_roots,
    log_message,
)


# ──────────────────────────────────────────────
# LilyPond
# ──────────────────────────────────────────────

def find_lilypond_executable() -> Optional[str]:
    """Locate the LilyPond executable via env vars, bundled runtime, or common install paths."""
    env_path = os.environ.get('LILYPOND_PATH') or os.environ.get('LILYPOND_HOME')
    candidates: list[str] = []
    if env_path:
        env_base = Path(env_path)
        candidates.extend([
            str(env_base),
            str(env_base / 'lilypond.exe'),
            str(env_base / 'usr' / 'bin' / 'lilypond.exe'),
            str(env_base / 'LilyPond' / 'usr' / 'bin' / 'lilypond.exe'),
        ])

    packaged_lilypond_dir = find_packaged_runtime_dir(LILYPOND_RUNTIME_DIR_NAME)
    if packaged_lilypond_dir is not None:
        candidates.extend([
            str(packaged_lilypond_dir / 'bin' / 'lilypond.exe'),
            str(packaged_lilypond_dir / 'usr' / 'bin' / 'lilypond.exe'),
        ])

    candidates.extend([
        str(get_app_base_dir() / 'lilypond-2.24.4' / 'bin' / 'lilypond.exe'),
        'lilypond',
        'lilypond.exe',
        r'C:\Program Files\LilyPond\usr\bin\lilypond.exe',
        r'C:\Program Files (x86)\LilyPond\usr\bin\lilypond.exe',
    ])

    checked: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        candidate_path = Path(candidate)
        if candidate_path.is_file():
            return str(candidate_path)
        found = shutil.which(candidate)
        if found:
            return found
        checked.append(candidate)

    log_message('未找到 LilyPond，可尝试以下路径或设置环境变量 LILYPOND_PATH / LILYPOND_HOME:', logging.WARNING)
    for candidate in checked:
        log_message(f'  - {candidate}', logging.WARNING)
    return None


def render_lilypond_pdf(ly_path: Path) -> Optional[Path]:
    """Invoke LilyPond to render a .ly file to PDF; return the PDF path or None on failure."""
    lilypond_exe = find_lilypond_executable()
    if lilypond_exe is None:
        return None
    ly_path = ly_path.resolve()
    # Suppress the LilyPond tagline by uncommenting the line jianpu-ly leaves in the .ly file,
    # or injecting \header { tagline = ##f } if it isn't already present.
    try:
        ly_content = ly_path.read_text(encoding='utf-8')
        if '% \\header { tagline="" }' in ly_content:
            ly_content = ly_content.replace('% \\header { tagline="" }', '\\header { tagline = ##f }')
        elif '\\header { tagline' not in ly_content:
            ly_content += '\n\\header { tagline = ##f }\n'
        ly_path.write_text(ly_content, encoding='utf-8')
    except Exception:
        pass
    log_message(f'使用 LilyPond 执行: {lilypond_exe}')
    try:
        subprocess.run([lilypond_exe, str(ly_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, cwd=str(ly_path.parent.resolve()), creationflags=_WIN_NO_WINDOW)
        pdf_path = ly_path.with_suffix('.pdf')
        return pdf_path if pdf_path.exists() else None
    except subprocess.CalledProcessError as exc:
        raw_stderr = exc.stderr.decode('utf-8', errors='ignore')
        # 过滤纯弃用警告行，仅保留实际错误行，防止数万行警告涌入日志
        error_lines = [
            ln for ln in raw_stderr.splitlines()
            if ln.strip() and not (
                '警告' in ln or 'warning' in ln.lower() or '已弃用' in ln
            )
        ]
        summary = '\n'.join(error_lines[:60]) if error_lines else raw_stderr[:2000]
        log_message('LilyPond 生成失败:\n' + summary, logging.WARNING)
        return None
    except OSError as exc:
        log_message(f'LilyPond 生成时出现异常: {exc}', logging.WARNING)
        return None


def _fix_deprecated_ly_syntax(text: str) -> str:
    r"""Convert deprecated LilyPond #'property syntax to .property dot notation.

    musicxml2ly may emit ``\set Staff #'instrumentName`` style syntax that is
    deprecated since LilyPond 2.24.  Safe to apply to any generated .ly file.
    """
    return re.sub(
        r'(\b[A-Z][A-Za-z]+)\s+#\'([a-z][a-z0-9-]*)',
        r'\1.\2',
        text,
    )


def _inject_metadata_to_lilypond(ly_path: Path, mxl_path: Path) -> None:
    """Append a \\header block with title/composer from MusicXML metadata at EOF.

    Appending is safe for any LilyPond file structure, including complex
    multi-voice/multi-staff output from musicxml2ly.  In LilyPond, later
    global \\header blocks override earlier ones for conflicting keys, so the
    appended block wins over the generic header musicxml2ly generates.
    """
    try:
        from ..music.transposer import extract_metadata_from_musicxml
        metadata = extract_metadata_from_musicxml(mxl_path)

        _GENERIC = {'', 'music21', 'composer', 'title', 'score', 'untitled', 'new score', 'unknown'}
        title = (metadata.get('title', '') or '').strip()
        composer = (metadata.get('composer', '') or '').strip()
        if title.lower() in _GENERIC:
            title = mxl_path.stem
        if composer.lower() in _GENERIC:
            composer = ''

        def _esc(s: str) -> str:
            return s.replace('\\', '\\\\').replace('"', '\\"')

        parts = [f'  title = "{_esc(title)}"']
        if composer:
            parts.append(f'  composer = "{_esc(composer)}"')

        ly_content = ly_path.read_text(encoding='utf-8', errors='ignore')
        header_block = '\\header {\n' + '\n'.join(parts) + '\n}\n'
        ly_path.write_text(ly_content.rstrip('\n') + '\n' + header_block, encoding='utf-8')
        LOGGER.debug('_inject_metadata_to_lilypond: 追加元数据头 title="%s"', title)
    except Exception as exc:
        LOGGER.debug('_inject_metadata_to_lilypond 失败: %s', exc)


def render_musicxml_staff_pdf(mxl_path: Path, out_dir: Path) -> Optional[Path]:
    """将 MusicXML 渲染为标准五线谱 PDF（不经简谱转换）。

    流程：musicxml2ly.py（LilyPond 附带）→ .ly → [注入元数据] → LilyPond → PDF。
    返回生成的 PDF 路径，失败返回 None。
    """
    lilypond_exe = find_lilypond_executable()
    if lilypond_exe is None:
        log_message('未找到 LilyPond，无法渲染五线谱预览。', logging.WARNING)
        return None

    # 找 musicxml2ly.py：优先取 lilypond.exe 同目录
    lilypond_bin = Path(lilypond_exe).parent
    musicxml2ly = lilypond_bin / 'musicxml2ly.py'
    if not musicxml2ly.exists():
        log_message('未找到 musicxml2ly.py，无法将 MusicXML 转换为 LilyPond 格式。', logging.WARNING)
        return None

    # 找可运行 musicxml2ly.py 的 Python（优先 LilyPond 捆绑版）
    python_exe: Optional[Path] = None
    for candidate in [
        lilypond_bin / 'python.exe',
        lilypond_bin / 'python',
    ]:
        if candidate.exists():
            python_exe = candidate
            break
    if python_exe is None:
        import sys as _sys
        python_exe = Path(_sys.executable)

    out_dir.mkdir(parents=True, exist_ok=True)
    mxl_path = mxl_path.resolve()
    ly_path = out_dir / (mxl_path.stem + '_staff.ly')

    # Step 1: musicxml2ly → .ly
    try:
        result = subprocess.run(
            [str(python_exe), str(musicxml2ly), '-o', str(ly_path), str(mxl_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(out_dir), timeout=60,
            creationflags=_WIN_NO_WINDOW,
        )
        if result.returncode != 0 or not ly_path.exists():
            err = (result.stderr or b'').decode('utf-8', errors='ignore').strip()
            log_message(f'musicxml2ly 转换失败: {err[:500]}', logging.WARNING)
            return None
    except Exception as exc:
        log_message(f'musicxml2ly 执行出错: {exc}', logging.WARNING)
        return None

    # Step 2: Fix deprecated #'property syntax emitted by older musicxml2ly builds
    try:
        raw = ly_path.read_text(encoding='utf-8', errors='ignore')
        fixed = _fix_deprecated_ly_syntax(raw)
        if fixed != raw:
            ly_path.write_text(fixed, encoding='utf-8')
    except Exception:
        pass

    # Step 3: Append title/composer from MusicXML metadata
    _inject_metadata_to_lilypond(ly_path, mxl_path)

    # Step 4: LilyPond → PDF（使用已有的 render_lilypond_pdf）
    return render_lilypond_pdf(ly_path)


# ──────────────────────────────────────────────
# jianpu-ly
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


def download_jianpu_ly_script(dest: Path) -> bool:
    """Download jianpu-ly.py from the fallback URL list and write it to dest."""
    for url in JIANPU_LY_URLS:
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                if resp.status != 200:
                    continue
                dest.write_bytes(resp.read())
            return True
        except Exception:
            continue
    return False


def _ensure_jianpu_script() -> Optional[Path]:
    """Ensure jianpu-ly.py is available, downloading it to the app dir or scripts/ if necessary."""
    script_path = find_jianpu_ly_script()
    if script_path is not None:
        return script_path
    script_path = get_app_base_dir() / 'scripts' / 'jianpu-ly.py'
    script_path.parent.mkdir(parents=True, exist_ok=True)
    if script_path.exists() or download_jianpu_ly_script(script_path):
        return script_path
    return None


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
    env = os.environ.copy()
    env['j2ly_sloppy_bars'] = '1'
    txt_path = txt_path.resolve()
    ly_path = ly_path.resolve()

    cmd = find_jianpu_ly_command()
    if cmd is not None:
        try:
            with ly_path.open('w', encoding='utf-8') as out:
                subprocess.run([cmd, str(txt_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(txt_path.parent), env=env, creationflags=_WIN_NO_WINDOW)
            return True
        except subprocess.CalledProcessError as exc:
            log_message(f'jianpu-ly 命令执行失败: {exc.stderr.decode("utf-8", errors="ignore").strip()}', logging.WARNING)

    if find_jianpu_ly_module():
        try:
            with ly_path.open('w', encoding='utf-8') as out:
                subprocess.run([sys.executable, '-m', 'jianpu_ly', str(txt_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(txt_path.parent), env=env, creationflags=_WIN_NO_WINDOW)
            return True
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
            subprocess.run([*python_cmd, str(script_path), str(txt_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(txt_path.parent), env=env, creationflags=_WIN_NO_WINDOW)
        return True
    except subprocess.CalledProcessError as exc:
        log_message(f'jianpu-ly 脚本执行失败: {exc.stderr.decode("utf-8", errors="ignore").strip()}', logging.WARNING)
        return False


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
                subprocess.run([cmd, str(mxl_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(mxl_path.parent), env=env, creationflags=_WIN_NO_WINDOW)
            return True
        except subprocess.CalledProcessError as exc:
            log_message(f'jianpu-ly 命令处理 MXL 失败: {exc.stderr.decode("utf-8", errors="ignore")}', logging.WARNING)

    if find_jianpu_ly_module():
        try:
            with ly_path.open('w', encoding='utf-8') as out:
                subprocess.run([sys.executable, '-m', 'jianpu_ly', str(mxl_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(mxl_path.parent), env=env, creationflags=_WIN_NO_WINDOW)
            return True
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
            subprocess.run([*python_cmd, str(script_path), str(mxl_path)], stdout=out, stderr=subprocess.PIPE, check=True, cwd=str(mxl_path.parent), env=env, creationflags=_WIN_NO_WINDOW)
        return True
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
    """Combine 2–4 jianpu ``RhythmicStaff`` section bodies into one polyphonic staff.

    Each *section_content* is the raw text between the ``BEGIN JIANPU STAFF``
    and ``END JIANPU STAFF`` markers (as captured by a regex group, so it does
    **not** include the markers themselves).

    The first section's ``\\new RhythmicStaff \\with { … }`` block is reused as
    the shared staff settings.  Each section's voice block is extracted, given a
    ``\\voiceOne`` / ``\\voiceTwo`` … command, and placed inside a LilyPond
    simultaneous-music construct::

        \\new RhythmicStaff \\with { … }
        {
            <<
                \\new Voice="v1" { \\voiceOne … }
                \\\\
                \\new Voice="v2" { \\voiceTwo … }
            >>
        }
    """
    staff_with_block: Optional[str] = None
    voice_inner_blocks: list[str] = []

    for i, content in enumerate(section_contents):
        # Find the boundary: }\n    { separates the \with block from the music block.
        m = _STAFF_SPLIT_RE.search(content)
        if not m:
            # Unexpected format — append raw and bail out
            voice_inner_blocks.append(content.strip())
            continue

        if i == 0:
            # Capture the shared staff settings (everything up to and including
            # the closing } of the \with block).
            staff_with_block = content[: m.start() + 1].strip()

        # The music block starts at the { that opens it.
        # content[m.start(1):] = "{ \\new Voice=... } }"
        music_block = content[m.start(1):]

        # Strip the outer braces of the music block:
        #   { \\new Voice=... } }
        # → \\new Voice=... }
        # (The trailing single } closes the Voice; the staff music block's } was
        #  the outermost brace we just removed.)
        inner = music_block.strip()
        if inner.startswith('{'):
            inner = inner[1:]
        inner = inner.rstrip()
        if inner.endswith('}'):
            inner = inner[:-1].rstrip()

        # Insert \\voiceX *after* the last per-voice setup override so it
        # takes precedence over any explicit \override Stem #'direction = #DOWN.
        tm = _TRANSPARENT_STEM_RE.search(inner)
        if tm:
            line_end = inner.find('\n', tm.end())
            if line_end >= 0:
                inner = (
                    inner[: line_end + 1]
                    + f'    {_VOICE_CMDS[i]}\n'
                    + inner[line_end + 1 :]
                )
            else:
                inner = inner + f'\n    {_VOICE_CMDS[i]}'
        else:
            # Fallback: insert right after the opening { of \\new Voice
            vm = _VOICE_OPEN_RE.search(inner)
            if vm:
                pos = vm.end()
                inner = inner[:pos] + f' {_VOICE_CMDS[i]}\n' + inner[pos:]

        voice_inner_blocks.append(inner)

    if not voice_inner_blocks or staff_with_block is None:
        # Could not parse sections — return them as separate staves (safe fallback)
        result = begin_marker
        for content in section_contents:
            result += content
        result += end_marker
        return result

    # Assemble the polyphonic staff.
    # Indent each voice block by 8 spaces; separate voices with \\ (the LilyPond
    # double-backslash simultaneous-voice separator).
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
        :func:`~core.music.jianpu_core.build_jianpu_ly_text` with
        ``_return_groups=True``.  Each inner list contains the 0-based indices
        of the sections that belong to the same musical Part.  Groups with only
        one member are left unchanged.  Groups with 2–4 members have their
        ``RhythmicStaff`` sections merged into a single polyphonic staff.
    """
    if not voice_groups or not any(len(g) > 1 for g in voice_groups):
        return  # nothing to do

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

    # Build a mapping: section index → group containing it
    section_to_group: dict[int, list[int]] = {}
    for group in voice_groups:
        for idx in group:
            section_to_group[idx] = group

    # Collect replacements (keyed by span in *content*) to apply from end to start.
    replacements: list[tuple[int, int, str]] = []  # (start, end, new_text)
    consumed: set[int] = set()

    for sec_idx, match in enumerate(sections):
        if sec_idx in consumed:
            continue
        group = section_to_group.get(sec_idx, [sec_idx])
        if len(group) <= 1:
            continue  # monophonic — no change

        # Gather all matches for this group
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

        # Replace from start of first section to end of last section
        replacements.append((first_m.start(), last_m.end(), merged))
        for i in group[1:]:
            consumed.add(i)

    if not replacements:
        return

    # Apply replacements from last to first to preserve string positions
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

