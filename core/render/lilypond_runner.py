# core/render/lilypond_runner.py — LilyPond / jianpu-ly tool discovery and rendering.
# Split from runtime_finder.py.
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
    resolve_font_path,
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


def _fix_adjacent_backward_repeats_in_mxl(mxl_path: Path, out_dir: Optional[Path] = None) -> Path:
    """Return a path to a MusicXML file with adjacent backward-repeat barlines fixed.

    Some OMR engines (Homr, Audiveris) split a combined ``:|.|:`` barline into
    two consecutive backward-repeat (``direction="backward"``) barlines — one
    on the right side of measure N, one on the right side of measure N+1.
    This is invalid: two adjacent end-repeats without a start-repeat between
    them cannot logically occur in standard notation.

    This function detects such pairs and converts the second measure's right-side
    backward-repeat into a left-side forward-repeat, restoring the intended
    ``:|.|:`` semantics.

    A ``direction="forward"`` on a RIGHT barline is ignored by both music21 and
    musicxml2ly; it must appear as a LEFT barline of the following measure to be
    recognised as a start-repeat by downstream tools.  The corrected XML is:

    Before (measure N+1):
        <barline location="right"><repeat direction="backward"/></barline>

    After (measure N+1):
        <barline location="left"><repeat direction="forward"/></barline>

    If no fix is needed the original path is returned unchanged.  If a fix is
    applied, the corrected file is written to *out_dir* when provided (safe for
    concurrent callers — each uses its own isolated temp directory), or to a
    sibling ``_staff_fixed.musicxml`` in ``mxl_path.parent`` as a fallback.
    """
    try:
        content = mxl_path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return mxl_path

    measure_re = re.compile(
        r'(<measure\b[^>]*>)(.*?)(</measure>)',
        re.DOTALL,
    )
    right_backward_re = re.compile(
        r'<barline\s+location=["\']right["\'][^>]*>.*?<repeat\s+direction=["\']backward["\'].*?</barline>',
        re.DOTALL,
    )
    left_any_re = re.compile(
        r'<barline\s+location=["\']left["\']',
        re.DOTALL,
    )

    measures = list(measure_re.finditer(content))
    replacements: list[tuple[int, int, str]] = []  # (start, end, new_text)

    for i in range(len(measures) - 1):
        ma, mb = measures[i], measures[i + 1]
        body_a, body_b = ma.group(2), mb.group(2)
        # Both measures must have a right-side backward-repeat, and the second
        # must not already have a left-side barline (to avoid double-insertion).
        if (
            right_backward_re.search(body_a)
            and right_backward_re.search(body_b)
            and not left_any_re.search(body_b)
        ):
            # Replace the right-side backward-repeat in measure N+1 with a
            # left-side forward-repeat so both music21 and musicxml2ly see a
            # valid start-repeat at the beginning of that measure.
            new_body_b = right_backward_re.sub(
                '<barline location="left"><repeat direction="forward"/></barline>',
                body_b,
                count=1,
            )
            full_new = mb.group(1) + new_body_b + mb.group(3)
            replacements.append((mb.start(), mb.end(), full_new))

    if not replacements:
        return mxl_path

    # Apply replacements in reverse order to preserve string offsets.
    new_content = content
    for start, end, new_text in sorted(replacements, key=lambda x: x[0], reverse=True):
        new_content = new_content[:start] + new_text + new_content[end:]

    # Write to the caller-supplied isolated temp dir when available, so
    # concurrent render threads each write their own copy and never clobber
    # each other.  Fall back to a sibling path only when out_dir is not given.
    if out_dir is not None:
        fixed_path = out_dir / '_staff_fixed.musicxml'
    else:
        fixed_path = mxl_path.parent / '_staff_fixed.musicxml'
    try:
        fixed_path.write_text(new_content, encoding='utf-8')
        LOGGER.debug(
            '_fix_adjacent_backward_repeats_in_mxl: fixed %d pair(s) in %s',
            len(replacements), mxl_path.name,
        )
        return fixed_path
    except Exception:
        return mxl_path


def _fix_omr_artifacts_in_mxl(mxl_path: Path, out_dir: Optional[Path] = None) -> Path:
    """Remove OMR artifacts that cause messy LilyPond staff rendering.

    Four classes of fix are applied in order before passing the file to musicxml2ly:

    1. *Phantom-position triplets*: ``<forward> + <note><rest measure="yes"/></note>
       + <backup>`` sequences injected by music21 as whole-measure placeholder rests
       for staves that have no content at that cursor position.  musicxml2ly treats
       each such rest as an extra voice, producing spacer bloat (``s1*13/24``) and
       phantom voice containers (VoiceSix, VoiceSeven, etc.) that make the final
       score unreadable.  These triplets are safe to remove because the surrounding
       ``<backup>`` elements already handle resetting the cursor to measure-start for
       the real voices that follow.

    2. *Overfull voices*: when a (staff, voice) pair's non-chord note durations sum
       to more than the measure's expected duration (derived from the time signature),
       trailing notes that push over the limit are removed.  This mirrors music21's
       own offset-clipping behaviour (notes whose offset >= measure_length are skipped)
       and prevents musicxml2ly from creating overflow voice containers.
       Only untied trailing notes are removed; tie-stop notes are preserved.

    3. *All-rest phantom voices*: voices that carry only ``<rest>`` elements and no
       ``<pitch>`` across the entire part are OMR detection artifacts.  They are
       removed together with the ``<backup>`` that precedes each group so that the
       cursor remains consistent for the real voices that follow.  musicxml2ly would
       otherwise create a spurious voice container (e.g. ``PartPOneVoiceSeven``)
       whose ``\\voiceN`` directive scatters rest glyphs to wrong staff positions.

    4. *OMR-split chords*: when two voices within the same staff have ALL their
       pitched notes at identical ``(onset, duration)`` positions, the OMR engine
       split what should be a chord into separate voice streams.  The secondary voice
       is merged into the primary (lower-numbered) voice by inserting its notes as
       ``<chord>`` elements immediately after their matching primaries and removing the
       ``<backup>`` that introduced the secondary section.

    A final cursor-consistency pass clamps any ``<backup>`` values that exceed the
    current cursor position after the above removals.

    If no fixes are needed, the original path is returned unchanged.
    """
    try:
        content = mxl_path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return mxl_path

    import xml.etree.ElementTree as ET

    # Preserve XML declaration + DOCTYPE as raw preamble (ElementTree drops them).
    preamble_lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith('<score-partwise') or stripped.startswith('<score-timewise'):
            break
        preamble_lines.append(line)
    preamble = '\n'.join(preamble_lines) + '\n' if preamble_lines else ''

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return mxl_path

    # Determine divisions from the first <attributes> block (divisions are
    # constant for a MusicXML file; time signature is tracked per measure).
    divisions = 10080
    first_attrs = root.find('.//attributes')
    if first_attrs is not None:
        divs_el = first_attrs.find('divisions')
        if divs_el is not None and divs_el.text:
            try:
                divisions = int(divs_el.text)
            except ValueError:
                pass

    # Current time signature state — updated whenever a <time> element is
    # encountered inside a measure's <attributes> block.  This ensures Fix 2
    # uses the correct expected duration for each measure even when the piece
    # changes time signatures mid-score (e.g. 3/4 → 4/4).
    cur_beats = 4
    cur_beat_type = 4

    # ── Pre-pass: identify globally all-rest voices ──────────────────────────
    # A (part_id, voice_num) pair is "all-rest" if every note in the entire
    # part carries a <rest> child and no <pitch> element.  These are phantom
    # OMR voices — injected as placeholder rests that produce spurious LilyPond
    # voice containers (e.g. PartPOneVoiceSeven) whose \voiceN directives
    # scatter rest glyphs to wrong staff positions.
    _voice_has_pitch: dict[tuple[str, str], bool] = {}
    for _part in root.findall('part'):
        _pid = _part.get('id', '')
        for _m in _part.findall('measure'):
            for _n in _m.findall('note'):
                _v = _n.find('voice')
                if _v is None or not _v.text:
                    continue
                _key = (_pid, _v.text)
                if _key not in _voice_has_pitch:
                    _voice_has_pitch[_key] = False
                if _n.find('rest') is None:   # has a pitch element
                    _voice_has_pitch[_key] = True
    _all_rest_voices: frozenset[tuple[str, str]] = frozenset(
        k for k, has_pitch in _voice_has_pitch.items() if not has_pitch
    )

    fixes = 0

    for part in root.findall('part'):
        for measure in part.findall('measure'):
            # Update time signature from this measure's <attributes>, if present.
            for attrs in measure.findall('attributes'):
                time_el = attrs.find('time')
                if time_el is not None:
                    b_el = time_el.find('beats')
                    bt_el = time_el.find('beat-type')
                    if b_el is not None and b_el.text and bt_el is not None and bt_el.text:
                        try:
                            cur_beats = int(b_el.text)
                            cur_beat_type = int(bt_el.text)
                        except ValueError:
                            pass
            expected = divisions * cur_beats * 4 // cur_beat_type

            children = list(measure)
            to_remove: set[int] = set()  # indices in `children`

            # ── Fix 1: remove forward + measure-rest + backup triplets ───────
            for idx, child in enumerate(children):
                if child.tag != 'note':
                    continue
                rest_el = child.find('rest')
                if rest_el is None or rest_el.get('measure') != 'yes':
                    continue
                # Only remove when sandwiched by <forward> … <backup> AND the
                # backup value equals forward + rest duration (music21 invariant).
                if idx == 0 or children[idx - 1].tag != 'forward':
                    continue
                if idx + 1 >= len(children) or children[idx + 1].tag != 'backup':
                    continue
                fwd_dur_el = children[idx - 1].find('duration')
                rest_dur_el = child.find('duration')
                bak_dur_el = children[idx + 1].find('duration')
                if (fwd_dur_el is None or rest_dur_el is None or bak_dur_el is None
                        or not fwd_dur_el.text or not rest_dur_el.text or not bak_dur_el.text):
                    continue
                try:
                    fwd_dur = int(fwd_dur_el.text)
                    rest_dur = int(rest_dur_el.text)
                    bak_dur = int(bak_dur_el.text)
                except ValueError:
                    continue
                # Backup must exactly undo the forward + rest movement.
                if bak_dur != fwd_dur + rest_dur:
                    continue
                to_remove.update({idx - 1, idx, idx + 1})
                fixes += 1

            for i in sorted(to_remove, reverse=True):
                measure.remove(children[i])

            # ── Fix 2: trim overfull voices ───────────────────────────────────
            children = list(measure)
            # Group non-chord notes by (staff, voice).
            voice_notes: dict[tuple[str, str], list] = {}
            for child in children:
                if child.tag != 'note':
                    continue
                v_el = child.find('voice')
                s_el = child.find('staff')
                if v_el is None or not v_el.text:
                    continue
                key = ((s_el.text or '1') if s_el is not None else '1', v_el.text)
                voice_notes.setdefault(key, []).append(child)

            for key, notes in voice_notes.items():
                total = sum(
                    int(n.find('duration').text)
                    for n in notes
                    if n.find('chord') is None and n.find('duration') is not None and n.find('duration').text
                )
                if total <= expected:
                    continue
                for note_el in reversed(notes):
                    if total <= expected:
                        break
                    if note_el.find('chord') is not None:
                        continue
                    dur_el = note_el.find('duration')
                    if dur_el is None or not dur_el.text:
                        continue
                    # Preserve tie-stop notes (they continue a tie from the prev measure).
                    tie_el = note_el.find('tie')
                    if tie_el is not None and tie_el.get('type') == 'stop':
                        continue
                    measure.remove(note_el)
                    total -= int(dur_el.text)
                    fixes += 1

            # ── Fix 3: remove globally all-rest voices ────────────────────────
            # Notes whose voice is in _all_rest_voices carry no pitch and exist
            # only as phantom OMR placeholders.  Remove them along with the
            # <backup> that immediately precedes each group so the cursor stays
            # consistent for the real voices that follow.
            if _all_rest_voices:
                children = list(measure)
                to_remove_4: set[int] = set()
                for idx, child in enumerate(children):
                    if child.tag != 'note':
                        continue
                    v_el = child.find('voice')
                    if v_el is None or not v_el.text:
                        continue
                    if (part.get('id', ''), v_el.text) not in _all_rest_voices:
                        continue
                    to_remove_4.add(idx)
                    # Remove the immediately preceding <backup> so the cursor
                    # position is not disturbed for subsequent voices.
                    if (idx > 0
                            and children[idx - 1].tag == 'backup'
                            and (idx - 1) not in to_remove_4):
                        to_remove_4.add(idx - 1)
                if to_remove_4:
                    for i in sorted(to_remove_4, reverse=True):
                        measure.remove(children[i])
                    fixes += len(to_remove_4)

            # ── Fix 4: merge chord-identical voices into single-voice chords ─────
            # When two voices in the same staff have ALL their pitched notes at
            # IDENTICAL (onset, duration) positions, the OMR engine split what
            # should be a chord into separate voice streams.  Consolidate by
            # converting secondary voice notes to <chord> notes and inserting
            # them immediately after the corresponding primary note, then
            # removing the <backup> that introduced the secondary voice section.
            # Only pitched notes are compared (rests are never merged as chords).
            children = list(measure)

            # Compute note onset ticks by simulating cursor movement.
            _cur5 = 0
            _last_onset = 0
            _note_onset: dict[int, int] = {}
            for _i5, _ch5 in enumerate(children):
                if _ch5.tag == 'note':
                    if _ch5.find('chord') is not None:
                        _note_onset[_i5] = _last_onset
                    else:
                        _note_onset[_i5] = _cur5
                        _last_onset = _cur5
                        _d5 = _ch5.find('duration')
                        if _d5 is not None and _d5.text:
                            try:
                                _cur5 += int(_d5.text)
                            except ValueError:
                                pass
                elif _ch5.tag == 'forward':
                    _d5 = _ch5.find('duration')
                    if _d5 is not None and _d5.text:
                        try:
                            _cur5 += int(_d5.text)
                        except ValueError:
                            pass
                elif _ch5.tag == 'backup':
                    _d5 = _ch5.find('duration')
                    if _d5 is not None and _d5.text:
                        try:
                            _cur5 = max(0, _cur5 - int(_d5.text))
                        except ValueError:
                            pass

            # Build pitch-note timeline per (staff, voice):
            # [(onset, duration, pitch_key, child_index), ...]
            # pitch_key 格式："{step}{alter}{octave}"，用于区分同节奏不同音高的声部（如卡农）。
            # 只有 onset+duration+pitch 三者完全相同的声部才视为 OMR 误拆的和弦。
            _tl5: dict[tuple[str, str], list[tuple[int, int, str, int]]] = {}
            for _i5, _ch5 in enumerate(children):
                if _ch5.tag != 'note':
                    continue
                if _ch5.find('chord') is not None:
                    continue
                if _ch5.find('rest') is not None:
                    continue  # rests are never chord candidates
                _v5 = _ch5.find('voice')
                _s5 = _ch5.find('staff')
                if _v5 is None or not _v5.text:
                    continue
                _sn5 = (_s5.text or '1') if _s5 is not None else '1'
                _d5 = _ch5.find('duration')
                if _d5 is None or not _d5.text:
                    continue
                try:
                    _dur5 = int(_d5.text)
                except ValueError:
                    continue
                _pt5 = _ch5.find('pitch')
                if _pt5 is not None:
                    _pkey5 = (
                        f"{_pt5.findtext('step', '?')}"
                        f"{_pt5.findtext('alter', '')}"
                        f"{_pt5.findtext('octave', '0')}"
                    )
                else:
                    _pkey5 = '?'
                _tl5.setdefault((_sn5, _v5.text), []).append(
                    (_note_onset.get(_i5, 0), _dur5, _pkey5, _i5)
                )

            # Find (secondary, primary) voice pairs with identical timelines.
            # 必须 onset、duration、pitch 三者全部相同才合并，避免将卡农等真实多声部误判为 OMR 误拆和弦。
            _stv5: dict[str, list[str]] = {}
            for _sn5, _vn5 in _tl5:
                _stv5.setdefault(_sn5, []).append(_vn5)

            _mmap5: dict[tuple[str, str], tuple[str, str]] = {}
            for _sn5, _vns5 in _stv5.items():
                _sorted5 = sorted(_vns5, key=lambda x: int(x) if x.isdigit() else x)
                for _pi5, _vp5 in enumerate(_sorted5):
                    _pk5 = (_sn5, _vp5)
                    _ptl5 = [(_o, _d, _p) for _o, _d, _p, _ in _tl5[_pk5]]
                    for _vs5 in _sorted5[_pi5 + 1:]:
                        _sk5 = (_sn5, _vs5)
                        if _sk5 in _mmap5:
                            continue
                        if [(_o, _d, _p) for _o, _d, _p, _ in _tl5[_sk5]] == _ptl5:
                            _mmap5[_sk5] = _pk5

            if _mmap5:
                # Build {secondary_key: {onset: [note_element, ...]}} lookup.
                _snotes5: dict[tuple[str, str], dict[int, list]] = {}
                for _sk5 in _mmap5:
                    _snotes5[_sk5] = {}
                    for _o, _d, _p, _idx in _tl5[_sk5]:
                        _snotes5[_sk5].setdefault(_o, []).append(children[_idx])

                # Indices of secondary notes to skip in normal output (they are
                # reinserted as chord notes immediately after their primary).
                _skip5: set[int] = {
                    _idx for _sk5 in _mmap5 for _, _, _, _idx in _tl5[_sk5]
                }

                # Mark backups immediately preceding a secondary voice section.
                for _i5, _ch5 in enumerate(children):
                    if _ch5.tag != 'backup':
                        continue
                    for _j5 in range(_i5 + 1, len(children)):
                        _nx5 = children[_j5]
                        if _nx5.tag in ('barline', 'attributes', 'direction', 'forward'):
                            continue
                        if _nx5.tag == 'note' and _nx5.find('chord') is None:
                            _v5 = _nx5.find('voice')
                            _s5 = _nx5.find('staff')
                            if _v5 is not None and _v5.text:
                                _snc5 = (_s5.text if _s5 is not None else '1', _v5.text)
                                if _snc5 in _mmap5:
                                    _skip5.add(_i5)
                        break

                # Rebuild measure children with chord notes interleaved.
                _new5: list = []
                for _i5, _ch5 in enumerate(children):
                    if _i5 in _skip5:
                        continue
                    _new5.append(_ch5)
                    if _ch5.tag == 'note' and _ch5.find('chord') is None:
                        _v5 = _ch5.find('voice')
                        _s5 = _ch5.find('staff')
                        if _v5 is not None and _v5.text:
                            _sn5 = _s5.text if _s5 is not None else '1'
                            _on5 = _note_onset.get(_i5, -1)
                            for _sk5, _pk5 in _mmap5.items():
                                if _pk5 != (_sn5, _v5.text):
                                    continue
                                for _cn5 in _snotes5[_sk5].get(_on5, []):
                                    if _cn5.find('chord') is None:
                                        _cn5.insert(0, ET.Element('chord'))
                                    _cv5 = _cn5.find('voice')
                                    if _cv5 is not None:
                                        _cv5.text = _v5.text
                                    _new5.append(_cn5)
                                    fixes += 1

                for _old5 in list(measure):
                    measure.remove(_old5)
                for _nc5 in _new5:
                    measure.append(_nc5)

            # ── Fix 3: correct <backup> values that now exceed cursor position ─
            # After note removal, backups may over-shoot; clamp to actual cursor.
            children = list(measure)
            cursor = 0
            for child in children:
                if child.tag == 'note':
                    if child.find('chord') is None:
                        dur_el = child.find('duration')
                        if dur_el is not None and dur_el.text:
                            cursor += int(dur_el.text)
                elif child.tag == 'forward':
                    dur_el = child.find('duration')
                    if dur_el is not None and dur_el.text:
                        cursor += int(dur_el.text)
                elif child.tag == 'backup':
                    dur_el = child.find('duration')
                    if dur_el is not None and dur_el.text:
                        orig = int(dur_el.text)
                        clamped = min(orig, cursor)
                        if clamped != orig:
                            dur_el.text = str(clamped)
                            fixes += 1
                        cursor = max(0, cursor - orig)

    if not fixes:
        return mxl_path

    try:
        modified_xml = ET.tostring(root, encoding='unicode', xml_declaration=False)
        if out_dir is not None:
            fixed_path = out_dir / '_staff_fixed.musicxml'
        else:
            fixed_path = mxl_path.parent / '_staff_fixed.musicxml'
        fixed_path.write_text(preamble + modified_xml, encoding='utf-8')
        LOGGER.debug(
            '_fix_omr_artifacts_in_mxl: applied %d fix(es) in %s',
            fixes, mxl_path.name,
        )
        return fixed_path
    except Exception:
        return mxl_path


def _fix_rest_positions_in_ly(ly_path: Path) -> None:
    r"""Center rests in all \voiceN contexts of a musicxml2ly-generated .ly file.

    musicxml2ly assigns ``\voiceOne``, ``\voiceTwo`` … ``\voiceFour`` directives
    to voices beyond the first in multi-voice staves.  These directives force
    rest glyphs to fixed above/below-staff positions suited for hand-engraved
    genuine polyphony.  OMR-generated scores often carry multiple voice numbers
    due to recognition fragmentation rather than true simultaneous independent
    voices, so the displaced rests look wrong.

    Inserts ``\override Rest.staff-position = #0`` and
    ``\override MultiMeasureRest.staff-position = #0`` immediately after each
    ``\voiceN`` directive that precedes a variable reference (``\PartXxx``).
    Stem direction and note-head placement from ``\voiceN`` are preserved;
    only rest glyphs are centred on the middle staff line.
    """
    try:
        text = ly_path.read_text(encoding='utf-8', errors='ignore')
        fixed = re.sub(
            r'(\\voice(?:One|Two|Three|Four|Five|Six|Seven|Eight))'
            r'(\s+)(\\[A-Z])',
            r'\1\2'
            r'\\override Rest.staff-position = #0 '
            r'\\override MultiMeasureRest.staff-position = #0 '
            r'\3',
            text,
        )
        if fixed != text:
            ly_path.write_text(fixed, encoding='utf-8')
            LOGGER.debug('_fix_rest_positions_in_ly: centred rests in %s', ly_path.name)
    except Exception as exc:
        LOGGER.debug('_fix_rest_positions_in_ly: %s', exc)


def _convert_spacer_rests_to_visible_rests(ly_path: Path) -> None:
    r"""Replace LilyPond spacer rests with visible regular rests.

    music21's voicesToParts() exports empty measures as spacer rests (``s``).
    musicxml2ly converts these to ``s4``, ``s1.``, ``s8*15`` etc. which are
    invisible — they occupy time but show no glyph, leaving the staff blank
    where rest symbols should appear.

    This function converts every standalone spacer duration token to the
    equivalent regular rest token so all measures show visible rest symbols.
    """
    try:
        text = ly_path.read_text(encoding='utf-8', errors='ignore')
        # Match 's' followed by a duration number (and optional dot / multiplier),
        # preceded by a word boundary and followed by whitespace, bar-line, or
        # structural punctuation.  Does not touch '\set', '\skip', or similar.
        fixed = re.sub(
            r'(?<![\\a-zA-Z])s(\d+\.?)(\*\d+)?(?=[\s\|\[\]<>{}\\]|$)',
            r'r\1\2',
            text,
        )
        if fixed != text:
            ly_path.write_text(fixed, encoding='utf-8')
            LOGGER.debug('_convert_spacer_rests_to_visible_rests: converted spacers in %s', ly_path.name)
    except Exception as exc:
        LOGGER.debug('_convert_spacer_rests_to_visible_rests: %s', exc)


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


def _ascii_only(s: str) -> str:
    """仅保留可打印 ASCII 字符（0x20-0x7E），剥离 CJK 等非 ASCII 内容。

    LilyPond 默认字体不含 CJK 字形，注入含汉字的 title 会导致 PDF 出现方块
    和排版溢出。此函数在写入 \\header 前对 title/composer 做净化。
    """
    return ''.join(c for c in s if 0x20 <= ord(c) <= 0x7E).strip()


def _has_cjk(s: str) -> bool:
    """Return True if *s* contains any character outside the ASCII printable range."""
    return any(ord(c) > 0x7E for c in s)


def _find_cjk_font_for_overlay() -> Optional[Path]:
    """Locate a CJK-capable TrueType/OpenType font file for text overlay.

    Preference order:
    1. Bundled NotoSansSC font in assets/fonts/ (always available in this repo).
    2. First available system CJK font via :func:`resolve_font_path`.
    """
    bundled = get_app_base_dir() / 'assets' / 'fonts' / 'NotoSansSC-VariableFont_wght.ttf'
    if bundled.exists():
        return bundled
    return resolve_font_path()


def _overlay_cjk_title_on_staff_pdf(pdf_path: Path, title: str) -> None:
    """Overlay a Unicode/CJK title onto the first page of a LilyPond staff PDF.

    When the score title is CJK-only, musicxml2ly emits raw CJK bytes into the
    LilyPond \\header block which LilyPond's default font renders as empty boxes.
    This function blanks out the garbled title area with a white rectangle and
    draws the correct CJK text centered above it using ReportLab + pypdf.

    Strategy: create a single-page overlay PDF (white rect + CJK title) with
    ReportLab, then merge it on top of the original PDF with pypdf.  LilyPond
    always reserves title-area space via the "." placeholder injected by
    _inject_metadata_to_lilypond, so a fixed cover band (y ≈ 5–40 pt from top)
    reliably blanks the right region without needing text-position detection.
    """
    if not title:
        return
    font_path = _find_cjk_font_for_overlay()
    if font_path is None:
        LOGGER.debug('_overlay_cjk_title_on_staff_pdf: 未找到 CJK 字体，跳过叠加')
        return
    try:
        import io as _io
        import pypdf
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.units import pt

        # ── 读取原始 PDF，取第一页尺寸 ──────────────────────────────────────
        reader = pypdf.PdfReader(str(pdf_path))
        if len(reader.pages) == 0:
            return
        first_page = reader.pages[0]
        pw = float(first_page.mediabox.width)   # points
        ph = float(first_page.mediabox.height)  # points

        # ── 用 ReportLab 生成叠加层（白色遮盖矩形 + CJK 标题文字）──────────
        # LilyPond 坐标系原点在页面左下角；标题区在顶部，
        # 即 y ∈ [ph-40, ph-5]（pt）
        cover_top    = ph - 5.0
        cover_bottom = ph - 40.0

        font_name = 'NotoSansSC'
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))

        overlay_buf = _io.BytesIO()
        c = rl_canvas.Canvas(overlay_buf, pagesize=(pw, ph))
        # 白色遮盖矩形
        c.setFillColorRGB(1, 1, 1)
        c.setStrokeColorRGB(1, 1, 1)
        c.rect(0, cover_bottom, pw, cover_top - cover_bottom, fill=1, stroke=0)
        # CJK 标题文字（居中，字号 15pt）
        c.setFillColorRGB(0, 0, 0)
        c.setFont(font_name, 15)
        text_y = cover_bottom + (cover_top - cover_bottom - 15) / 2
        c.drawCentredString(pw / 2, text_y, title)
        c.save()
        overlay_buf.seek(0)

        # ── 用 pypdf 把叠加层合并到原始 PDF 第一页上 ────────────────────────
        overlay_reader = pypdf.PdfReader(overlay_buf)
        overlay_page  = overlay_reader.pages[0]
        first_page.merge_page(overlay_page)

        writer = pypdf.PdfWriter()
        writer.add_page(first_page)
        for p in reader.pages[1:]:
            writer.add_page(p)

        tmp_path = pdf_path.with_suffix('.tmp.pdf')
        with open(str(tmp_path), 'wb') as f:
            writer.write(f)
        tmp_path.replace(pdf_path)
        LOGGER.debug('_overlay_cjk_title_on_staff_pdf: 已叠加标题 "%s"', title)
    except Exception as exc:
        LOGGER.debug('_overlay_cjk_title_on_staff_pdf: 叠加失败: %s', exc)

def _inject_metadata_to_lilypond(ly_path: Path, mxl_path: Path) -> tuple[str, str]:
    """Append a \\header block with title/composer from MusicXML metadata at EOF.

    Returns ``(raw_title, raw_composer)`` — the pre-ASCII-stripping values so
    callers can pass the full Unicode text to the markup-injection step without
    a second metadata parse.  The appended header always sets ``title = ""``;
    the actual title display is handled by _apply_title_markup_to_staff_ly().

    Appending is safe for any LilyPond file structure, including complex
    multi-voice/multi-staff output from musicxml2ly.  In LilyPond, later
    global \\header blocks override earlier ones for conflicting keys, so the
    appended block wins over the generic header musicxml2ly generates.
    """
    _GENERIC = {'', 'music21', 'composer', 'title', 'score', 'untitled', 'new score', 'unknown'}
    raw_title: str = mxl_path.stem
    raw_composer: str = ''
    try:
        from ..notation.transposer import extract_metadata_from_musicxml
        metadata = extract_metadata_from_musicxml(mxl_path)

        # Raw title/composer (before ASCII stripping) — returned for markup use
        _raw = (metadata.get('title', '') or '').strip()
        if _raw and _raw.lower() not in _GENERIC:
            raw_title = _raw

        raw_composer = (metadata.get('composer', '') or '').strip()
        if raw_composer.lower() in _GENERIC:
            raw_composer = ''

        # ASCII-only composer for the header block (font block will handle CJK
        # in the markup; the header composer is only a fallback).
        composer_ascii = _ascii_only(raw_composer)

        def _esc(s: str) -> str:
            return s.replace('\\', '\\\\').replace('"', '\\"')

        # Build header override block:
        # - title: always "" — _apply_title_markup_to_staff_ly() injects a
        #   \markup block with font support before \score {}, which both
        #   reserves the title area and renders CJK correctly.
        # - subtitle: always cleared (musicxml2ly mirrors title → subtitle).
        # - composer: ASCII-stripped fallback; the markup block shows the full text.
        parts: list[str] = [
            '  title = ""',
            '  subtitle = ""',
            f'  composer = "{_esc(composer_ascii)}"',
            '  tagline = ##f',
        ]

        ly_content = ly_path.read_text(encoding='utf-8', errors='ignore')
        header_block = '\\header {\n' + '\n'.join(parts) + '\n}\n'
        ly_path.write_text(ly_content.rstrip('\n') + '\n' + header_block, encoding='utf-8')
        LOGGER.debug('_inject_metadata_to_lilypond: 追加元数据头 title="%s"', raw_title)
    except Exception as exc:
        LOGGER.debug('_inject_metadata_to_lilypond 失败: %s', exc)
    return raw_title, raw_composer


def _apply_title_markup_to_staff_ly(ly_path: Path, raw_title: str, raw_composer: str = '') -> None:
    """Inject a CJK-capable font block and \\markup title into a staff .ly file.

    Mirrors the title-injection logic in sanitize_generated_lilypond_file() for
    musicxml2ly-generated staff files: inserts a LilyPond \\markup block (with
    the bundled CJK font) before the first \\score {}, so both CJK and ASCII
    titles render correctly without any post-render overlay.
    """
    try:
        from .renderer import ensure_lilypond_font_block, build_lilypond_title_markup
        text = ly_path.read_text(encoding='utf-8', errors='ignore')
        text = ensure_lilypond_font_block(text)
        title_markup = build_lilypond_title_markup(raw_title, raw_composer)
        if (
            title_markup
            and '\\score {' in text
            and '\\fill-line { \\fontsize #3 \\bold' not in text.split('\\score {', 1)[0]
        ):
            text = text.replace('\\score {', title_markup + '\\score {', 1)
        ly_path.write_text(text, encoding='utf-8')
        LOGGER.debug('_apply_title_markup_to_staff_ly: 已注入标题标记 "%s"', raw_title)
    except Exception as exc:
        LOGGER.debug('_apply_title_markup_to_staff_ly 失败: %s', exc)


def _split_multivoice_parts_in_mxl(mxl_path: Path, out_dir: Optional[Path] = None) -> Path:
    """将含多声部（voice）的 part 拆分为多个单声部 part。

    使用 music21 的 voicesToParts() 完成拆分，与简谱管线的声部识别逻辑保持一致，
    避免手工 XML 操作对复杂小节结构（含 backup/forward/chord）的误判。

    - 若某 part 的任一小节含 Voice 对象，则对整个 part 调用 voicesToParts()
    - 无 Voice 的 part 原样保留
    - 若无任何 part 需要拆分则原样返回
    """
    try:
        import music21
    except ImportError:
        LOGGER.debug('_split_multivoice_parts_in_mxl: music21 not available, skipping')
        return mxl_path

    try:
        score = music21.converter.parse(str(mxl_path))
    except Exception as exc:
        LOGGER.debug('_split_multivoice_parts_in_mxl: music21 parse failed: %s', exc)
        return mxl_path

    def _part_has_voices(part: 'music21.stream.Part') -> bool:
        return any(
            list(m.getElementsByClass(music21.stream.Voice))
            for m in part.getElementsByClass(music21.stream.Measure)
        )

    if not any(_part_has_voices(p) for p in score.parts):
        return mxl_path  # 无需拆分

    new_score = music21.stream.Score()
    for part in score.parts:
        if _part_has_voices(part):
            split = part.voicesToParts()
            for p in split.parts:
                new_score.append(p)
        else:
            new_score.append(part)

    try:
        out_path = (out_dir if out_dir else mxl_path.parent) / '_staff_m21_split.musicxml'
        new_score.write('musicxml', fp=str(out_path))
        LOGGER.debug('_split_multivoice_parts_in_mxl: music21 split → %s', out_path.name)
        return out_path
    except Exception as exc:
        LOGGER.debug('_split_multivoice_parts_in_mxl: write failed: %s', exc)
        return mxl_path


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

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    mxl_path = mxl_path.resolve()
    # Use a fixed ASCII-only filename for the .ly file.  musicxml2ly (LilyPond's
    # bundled Python 3.10) cannot open output files whose absolute path contains
    # non-ASCII (CJK) characters on Windows — open() raises FileNotFoundError.
    # The out_dir already uniquely identifies the score, so a constant basename
    # is safe.  The final PDF is renamed to include the stem at the end.
    ly_path = out_dir / '_staff.ly'

    # Step 0a: Pre-fix adjacent backward-repeat barlines in the MusicXML (OMR
    # artefact: combined :|.|: barline split into two backward-repeats).
    # Pass out_dir so the fixed file is isolated to this call's temp directory,
    # preventing concurrent render threads from clobbering each other's copy.
    mxl_for_ly = _fix_adjacent_backward_repeats_in_mxl(mxl_path, out_dir)

    # Step 0b: Remove OMR rendering artefacts — phantom-position triplets and
    # overfull voice durations that cause musicxml2ly to emit excessive spacers
    # and ghost voice containers (VoiceSix, VoiceSeven, etc.).
    mxl_for_ly = _fix_omr_artifacts_in_mxl(mxl_for_ly, out_dir)

    # Step 0c: Split multi-voice parts into separate single-voice parts.
    # musicxml2ly renders one staff per part; voices within a single part appear
    # as polyphonic layers on the same staff.  For independent voice lines (e.g.
    # canons), splitting ensures each voice gets its own staff.
    mxl_for_ly = _split_multivoice_parts_in_mxl(mxl_for_ly, out_dir)

    # Step 1: musicxml2ly → .ly
    try:
        result = subprocess.run(
            [str(python_exe), str(musicxml2ly), '-o', str(ly_path), str(mxl_for_ly)],
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

    # Step 1.5: Centre rests in all \voiceN contexts.  OMR-fragmented voices
    # produce displaced rest glyphs; centering them via staff-position overrides
    # avoids rest scatter without affecting note stems or head placement.
    _fix_rest_positions_in_ly(ly_path)

    # Step 1.6: Convert spacer rests to visible rests.  music21's voicesToParts()
    # exports empty measures as 's' spacers; these are invisible in LilyPond.
    _convert_spacer_rests_to_visible_rests(ly_path)

    # Step 2: Fix deprecated #'property syntax emitted by older musicxml2ly builds
    try:
        raw = ly_path.read_text(encoding='utf-8', errors='ignore')
        fixed = _fix_deprecated_ly_syntax(raw)
        if fixed != raw:
            ly_path.write_text(fixed, encoding='utf-8')
    except Exception:
        pass

    # Step 3: Append title/composer from MusicXML metadata.
    raw_title, raw_composer = _inject_metadata_to_lilypond(ly_path, mxl_path)

    # Step 3b: Inject font block + \markup title block — same approach as the
    # jianpu rendering pipeline.  This handles both CJK and ASCII titles via
    # the bundled NotoSansSC font, replacing the previous ReportLab overlay hack.
    _apply_title_markup_to_staff_ly(ly_path, raw_title, raw_composer)

    # Step 4: LilyPond → PDF（使用已有的 render_lilypond_pdf）
    pdf_path = render_lilypond_pdf(ly_path)

    return pdf_path




# ──────────────────────────────────────────────
# 向后兼容再导出（jianpu 相关功能已移至 jianpu_runner.py）
# ──────────────────────────────────────────────
from .jianpu_runner import (  # noqa: E402, F401
    find_jianpu_ly_command,
    find_jianpu_ly_module,
    find_jianpu_ly_script,
    download_jianpu_ly_script,
    find_python_script_command,
    render_jianpu_ly,
    render_jianpu_ly_from_mxl,
    merge_polyphonic_jianpu_staves,
    inject_repeat_barlines_to_ly,
)
