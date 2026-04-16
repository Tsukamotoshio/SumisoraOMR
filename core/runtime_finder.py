# core/runtime_finder.py — 外部工具查找与子进程调用
# 拆分自 convert.py
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Optional

# 在 Windows 上，作为 GUI 程序运行时防止子进程弹出新控制台窗口
_WIN_NO_WINDOW: int = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _win_assign_kill_on_close_job(proc: 'subprocess.Popen') -> None:
    """
    (Windows-only) 将 *proc* 加入一个设置了 KillOnJobClose 的 Job Object。
    当前进程（Worker）退出时，OS 自动关闭 Job 句柄，从而递归终止
    proc 及其所有子进程（如 Audiveris.bat 启动的 java.exe）。
    对非 Windows 或任何异常静默跳过。
    """
    if os.name != 'nt':
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9

        class _BasicLimit(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', ctypes.c_int64),
                ('PerJobUserTimeLimit',     ctypes.c_int64),
                ('LimitFlags',             wintypes.DWORD),
                ('MinimumWorkingSetSize',   ctypes.c_size_t),
                ('MaximumWorkingSetSize',   ctypes.c_size_t),
                ('ActiveProcessLimit',      wintypes.DWORD),
                ('Affinity',               ctypes.c_void_p),
                ('PriorityClass',          wintypes.DWORD),
                ('SchedulingClass',        wintypes.DWORD),
            ]

        class _IOCounters(ctypes.Structure):
            _fields_ = [
                ('ReadOperationCount',  ctypes.c_uint64),
                ('WriteOperationCount', ctypes.c_uint64),
                ('OtherOperationCount', ctypes.c_uint64),
                ('ReadTransferCount',   ctypes.c_uint64),
                ('WriteTransferCount',  ctypes.c_uint64),
                ('OtherTransferCount',  ctypes.c_uint64),
            ]

        class _ExtLimit(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', _BasicLimit),
                ('IoInfo',                _IOCounters),
                ('ProcessMemoryLimit',    ctypes.c_size_t),
                ('JobMemoryLimit',        ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed',     ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return
        info = _ExtLimit()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        PROCESS_ALL_ACCESS = 0x1FFFFF
        handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, proc.pid)
        if handle:
            kernel32.AssignProcessToJobObject(job, handle)
            kernel32.CloseHandle(handle)
        # 故意不 CloseHandle(job)：保持 Job 句柄开放直到本进程退出，
        # 届时 OS 自动关闭所有句柄并触发 KillOnJobClose。
    except Exception:
        pass

from .config import (
    AUDIVERIS_INSTALL_DIR_NAME,
    AUDIVERIS_MSI_NAMES,
    AUDIVERIS_RUNTIME_DIR_NAME,
    AUDIVERIS_SOURCE_DIR_NAMES,
    DEFAULT_AUDIVERIS_MIN_JAVA_VERSION,
    JIANPU_LY_URLS,
    LILYPOND_RUNTIME_DIR_NAME,
    LOGGER,
    MAX_AUDIVERIS_SECONDS,
    OMR_ENGINE_DIR_NAME,
)
from .utils import (
    find_local_tessdata_dir,
    find_packaged_runtime_dir,
    get_app_base_dir,
    get_runtime_search_roots,
    log_message,
)


# ──────────────────────────────────────────────
# Java
# ──────────────────────────────────────────────

def parse_java_major_version(version_output: str) -> Optional[int]:
    """Parse the Java major version number from `java -version` output."""
    match = re.search(r'version\s+"([^"]+)"', version_output)
    if not match:
        return None

    version_text = match.group(1)
    if version_text.startswith('1.'):
        parts = version_text.split('.')
        return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None

    major_text = version_text.split('.', 1)[0]
    return int(major_text) if major_text.isdigit() else None


def find_java_executable(min_major_version: int = DEFAULT_AUDIVERIS_MIN_JAVA_VERSION) -> Optional[Path]:
    """Find a Java executable meeting the minimum version (searches app dir, JAVA_HOME, common paths, PATH)."""
    candidates: list[Path] = []

    for base_dir in get_runtime_search_roots():
        candidates.extend([
            base_dir / 'jdk' / 'bin' / 'java.exe',
            base_dir / 'jdk' / 'bin' / 'java',
            base_dir / 'java' / 'bin' / 'java.exe',
            base_dir / 'java' / 'bin' / 'java',
            base_dir / 'jre' / 'bin' / 'java.exe',
            base_dir / 'jre' / 'bin' / 'java',
        ])
        if base_dir.exists() and base_dir.is_dir():
            for child in sorted(base_dir.iterdir(), reverse=True):
                if child.is_dir() and child.name.lower().startswith(('jdk', 'jre', 'java')):
                    candidates.extend([child / 'bin' / 'java.exe', child / 'bin' / 'java'])

    java_home = os.environ.get('JAVA_HOME')
    if java_home:
        java_base = Path(java_home)
        candidates.extend([java_base / 'bin' / 'java.exe', java_base / 'bin' / 'java'])

    local_app_data = os.environ.get('LOCALAPPDATA', '')
    common_roots = [
        Path(r'C:\Program Files\Java'),
        Path(r'C:\Program Files\Eclipse Adoptium'),
        Path(r'C:\Program Files\Microsoft'),
        Path(r'C:\Program Files\Zulu'),
        Path(local_app_data) / 'Programs' / 'Microsoft' if local_app_data else Path(),
        Path(local_app_data) / 'Programs' / 'Eclipse Adoptium' if local_app_data else Path(),
        Path(local_app_data) / 'Programs' / 'Zulu' if local_app_data else Path(),
    ]
    for root in common_roots:
        if root.exists() and root.is_dir():
            for child in sorted(root.iterdir(), reverse=True):
                if child.is_dir():
                    candidates.extend([child / 'bin' / 'java.exe', child / 'bin' / 'java'])

    for candidate_name in ('java.exe', 'java'):
        found = shutil.which(candidate_name)
        if found:
            candidates.append(Path(found))

    seen: set[str] = set()
    for candidate in candidates:
        candidate_str = str(candidate)
        if candidate_str in seen:
            continue
        seen.add(candidate_str)
        if not candidate.exists() or not candidate.is_file():
            continue

        try:
            result = subprocess.run(
                [str(candidate), '-version'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=10,
                check=False,
                creationflags=_WIN_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            continue

        version_text = (result.stderr or result.stdout or '').strip()
        major_version = parse_java_major_version(version_text)
        if major_version is not None and major_version >= min_major_version:
            return candidate

    return None


# ──────────────────────────────────────────────
# Audiveris
# ──────────────────────────────────────────────

def find_audiveris_source_dir() -> Optional[Path]:
    """Find the Audiveris source directory under the app root or omr_engine."""
    script_dir = get_app_base_dir()
    candidates: list[Path] = []
    for name in AUDIVERIS_SOURCE_DIR_NAMES:
        candidates.append(script_dir / name)
        candidates.append(script_dir / OMR_ENGINE_DIR_NAME / name)
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def get_audiveris_required_java_version(default: int = DEFAULT_AUDIVERIS_MIN_JAVA_VERSION) -> int:
    """Read theMinJavaVersion from Audiveris gradle.properties to determine the required Java version."""
    source_dir = find_audiveris_source_dir()
    if source_dir is None:
        return default

    properties_path = source_dir / 'gradle.properties'
    if not properties_path.exists():
        return default

    try:
        text = properties_path.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return default

    match = re.search(r'^\s*theMinJavaVersion\s*=\s*(\d+)\s*$', text, flags=re.M)
    if match:
        return int(match.group(1))
    return default


def find_audiveris_executable() -> Optional[Path]:
    """Locate the Audiveris launcher via env vars, bundled runtime, or source build paths."""
    script_dir = get_app_base_dir()
    env_path = os.environ.get('AUDIVERIS_EXE_PATH') or os.environ.get('AUDIVERIS_PATH')
    candidates: list[Path] = []
    if env_path:
        env_base = Path(env_path)
        candidates.extend([
            env_base,
            env_base / 'audiveris.exe',
            env_base / 'Audiveris.exe',
            env_base / 'audiveris.bat',
            env_base / 'Audiveris.bat',
            env_base / 'bin' / 'audiveris.exe',
            env_base / 'bin' / 'Audiveris.bat',
            env_base / 'bin' / 'audiveris.bat',
        ])

    packaged_audiveris_dir = find_packaged_runtime_dir(AUDIVERIS_RUNTIME_DIR_NAME)
    if packaged_audiveris_dir is not None:
        candidates.extend([
            packaged_audiveris_dir / 'bin' / 'Audiveris.bat',
            packaged_audiveris_dir / 'bin' / 'audiveris.bat',
            packaged_audiveris_dir / 'bin' / 'audiveris.exe',
        ])

    local_install_dir = script_dir / AUDIVERIS_INSTALL_DIR_NAME
    candidates.extend([
        script_dir / 'Audiveris.exe',
        script_dir / 'audiveris.exe',
        script_dir / 'Audiveris.bat',
        script_dir / 'audiveris.bat',
        local_install_dir / 'Audiveris.exe',
        local_install_dir / 'audiveris.exe',
        local_install_dir / 'bin' / 'audiveris.exe',
        local_install_dir / 'bin' / 'Audiveris.bat',
        local_install_dir / 'bin' / 'audiveris.bat',
    ])

    source_dir = find_audiveris_source_dir()
    if source_dir is not None:
        candidates.extend([
            source_dir / 'Audiveris.bat',
            source_dir / 'audiveris.bat',
            source_dir / 'app' / 'build' / 'install' / 'Audiveris' / 'bin' / 'Audiveris.bat',
            source_dir / 'app' / 'build' / 'install' / 'Audiveris' / 'bin' / 'audiveris.bat',
            source_dir / 'app' / 'build' / 'install' / 'app' / 'bin' / 'Audiveris.bat',
            source_dir / 'app' / 'build' / 'install' / 'app' / 'bin' / 'audiveris.bat',
            source_dir / 'app' / 'build' / 'install' / 'app' / 'bin' / 'app.bat',
            source_dir / 'build' / 'install' / 'Audiveris' / 'bin' / 'Audiveris.bat',
            source_dir / 'build' / 'install' / 'Audiveris' / 'bin' / 'audiveris.bat',
            source_dir / 'build' / 'install' / 'app' / 'bin' / 'Audiveris.bat',
            source_dir / 'build' / 'install' / 'app' / 'bin' / 'audiveris.bat',
            source_dir / 'build' / 'install' / 'app' / 'bin' / 'app.bat',
            source_dir / 'audiveris.exe',
            source_dir / 'bin' / 'audiveris.exe',
        ])

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    for candidate in ['audiveris.exe', 'Audiveris.bat', 'audiveris.bat', 'audiveris']:
        found = shutil.which(candidate)
        if found:
            return Path(found)
    return None


def find_audiveris_msi() -> Optional[Path]:
    """Look for an Audiveris MSI installer in the app base directory."""
    script_dir = get_app_base_dir()
    for name in AUDIVERIS_MSI_NAMES:
        candidate = script_dir / name
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def find_audiveris_wrapper() -> Optional[Path]:
    """Find the Gradle wrapper script (gradlew.bat / gradlew) in the Audiveris source directory."""
    source_dir = find_audiveris_source_dir()
    if source_dir is None:
        return None

    for candidate in [
        source_dir / 'gradlew.bat',
        source_dir / 'gradlew',
        source_dir / 'app' / 'gradlew.bat',
        source_dir / 'app' / 'gradlew',
    ]:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def prepare_subprocess_command(cmd: list[str]) -> list[str]:
    """On Windows, wrap .bat/.cmd commands with `cmd.exe /c` to ensure correct invocation."""
    if os.name == 'nt' and cmd:
        suffix = Path(cmd[0]).suffix.lower()
        if suffix in {'.bat', '.cmd'}:
            return ['cmd.exe', '/c', *cmd]
    return cmd


def _determine_audiveris_thread_limit() -> int:
    """Determine Audiveris thread limits based on the user's CPU core count."""
    cpu_count = os.cpu_count() or 1
    return 1 if cpu_count <= 2 else max(1, cpu_count - 2)


def run_subprocess_with_spinner(
    cmd: list[str],
    cwd: str,
    timeout: int = MAX_AUDIVERIS_SECONDS,
    java_exe: Optional[Path] = None,
) -> tuple[int, str, str]:
    """
    Run a subprocess with a spinner animation, setting Java env vars and TESSDATA_PREFIX.
    Returns (exit_code, stdout, stderr); returns -1 on timeout.
    """
    spinner = ['|', '/', '-', '\\']
    start_time = time.time()
    prepared_cmd = prepare_subprocess_command(cmd)
    env = os.environ.copy()
    thread_limit = _determine_audiveris_thread_limit()
    java_opts = env.get('JAVA_TOOL_OPTIONS', '-Dfile.encoding=UTF-8').strip()
    if '-XX:ActiveProcessorCount=' not in java_opts:
        java_opts = f"{java_opts} -XX:ActiveProcessorCount={thread_limit}".strip()
    env['JAVA_TOOL_OPTIONS'] = java_opts
    for thread_var in (
        'OMP_NUM_THREADS',
        'OPENBLAS_NUM_THREADS',
        'MKL_NUM_THREADS',
        'NUMEXPR_NUM_THREADS',
        'VECLIB_MAXIMUM_THREADS',
        'OMP_THREAD_LIMIT',
    ):
        env.setdefault(thread_var, str(thread_limit))
    log_message(
        f'已限制 Audiveris Java 线程数: {thread_limit}（CPU 核心数 {os.cpu_count() or 1} - 2，最小 1）。'
    )
    if java_exe is not None:
        java_home = java_exe.parent.parent
        env['JAVA_HOME'] = str(java_home)
        env['APP_JAVA_HOME'] = str(java_home)
        env['JAVACMD'] = str(java_exe)
        env['PATH'] = str(java_exe.parent) + os.pathsep + env.get('PATH', '')

    tessdata_dir = find_local_tessdata_dir()
    if tessdata_dir is not None and not env.get('TESSDATA_PREFIX'):
        env['TESSDATA_PREFIX'] = str(tessdata_dir)

    stdout_handle = tempfile.TemporaryFile(mode='w+t', encoding='utf-8', errors='ignore')
    stderr_handle = tempfile.TemporaryFile(mode='w+t', encoding='utf-8', errors='ignore')

    try:
        with subprocess.Popen(
            prepared_cmd,
            cwd=cwd,
            stdout=stdout_handle,
            stderr=stderr_handle,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='ignore',
            env=env,
            creationflags=_WIN_NO_WINDOW,
        ) as proc:
            _win_assign_kill_on_close_job(proc)
            while proc.poll() is None:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    proc.kill()
                    proc.wait(timeout=5)
                    stdout_handle.seek(0)
                    stderr_handle.seek(0)
                    stdout = stdout_handle.read()
                    stderr = stderr_handle.read()
                    if sys.stdout: sys.stdout.write('\r'); sys.stdout.flush()
                    return -1, stdout or '', stderr or 'Process timed out.'
                idx = int(elapsed) % len(spinner)
                if sys.stdout:
                    sys.stdout.write(f'\r{spinner[idx]} Audiveris 正在运行... 已用 {int(elapsed)}s')
                    sys.stdout.flush()
                time.sleep(0.25)

            return_code = proc.wait(timeout=5)
            stdout_handle.seek(0)
            stderr_handle.seek(0)
            stdout = stdout_handle.read()
            stderr = stderr_handle.read()
            if sys.stdout: sys.stdout.write('\r'); sys.stdout.flush()
            return return_code, stdout or '', stderr or ''
    finally:
        stdout_handle.close()
        stderr_handle.close()


def ensure_audiveris_executable() -> Optional[Path]:
    """Ensure Audiveris is available: try existing path, then Gradle build from source, then MSI unpack."""
    existing = find_audiveris_executable()
    if existing is not None:
        return existing

    source_dir = find_audiveris_source_dir()
    wrapper = find_audiveris_wrapper()
    required_java_version = get_audiveris_required_java_version()
    if source_dir is not None and wrapper is not None:
        java_exe = find_java_executable(required_java_version)
        if java_exe is None:
            log_message(f'检测到 Audiveris 源码目录，但未找到可用的 Java {required_java_version}+/JDK {required_java_version}+。请先安装匹配版本，或将便携版放到程序目录下的 jdk 文件夹。', logging.WARNING)
        else:
            log_message(f'检测到 Audiveris 源码目录: {source_dir}')
            log_message(f'检测到 Java {required_java_version}+: {java_exe}')
            log_message('正在根据源码准备 Audiveris 启动器，首次执行可能需要几分钟...')
            cmd = [str(wrapper), '--console=plain', ':app:installDist']
            return_code, stdout, stderr = run_subprocess_with_spinner(cmd, cwd=str(wrapper.parent), java_exe=java_exe)
            if return_code == 0:
                installed = find_audiveris_executable()
                if installed is not None:
                    log_message(f'已从源码准备 Audiveris 启动器: {installed}')
                    return installed
            else:
                detail = (stderr or stdout or '').strip()
                if detail:
                    log_message(f'从源码准备 Audiveris 失败: {detail}', logging.WARNING)

    msi_path = find_audiveris_msi()
    if msi_path is None:
        return None

    base_dir = get_app_base_dir()
    install_dir = base_dir / AUDIVERIS_INSTALL_DIR_NAME
    log_path = base_dir / 'audiveris-install.log'
    base_dir.mkdir(parents=True, exist_ok=True)

    log_message(f'未找到源码启动器，正在从 MSI 解包到: {install_dir}')
    cmd = [
        'msiexec',
        '/a',
        str(msi_path),
        '/qn',
        f'TARGETDIR={base_dir}',
        '/L*v',
        str(log_path),
    ]

    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=MAX_AUDIVERIS_SECONDS, check=False, creationflags=_WIN_NO_WINDOW)
    except OSError as exc:
        log_message(f'调用 MSI 解包 Audiveris 失败: {exc}', logging.WARNING)
        return None

    if result.returncode != 0:
        log_message(f'Audiveris 解包失败，退出码: {result.returncode}', logging.WARNING)
        if result.stderr:
            log_message(result.stderr.strip(), logging.WARNING)
        log_message(f'详细日志见: {log_path}', logging.WARNING)
        return None

    installed = find_audiveris_executable()
    if installed is not None:
        log_message(f'Audiveris 已就绪: {installed}')
        return installed

    log_message(f'解包过程已完成，但未找到 Audiveris 启动器，请检查日志: {log_path}', logging.WARNING)
    return None


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


def render_musicxml_staff_pdf(mxl_path: Path, out_dir: Path) -> Optional[Path]:
    """将 MusicXML 渲染为标准五线谱 PDF（不经简谱转换）。

    流程：musicxml2ly.py（LilyPond 附带）→ .ly → LilyPond → PDF。
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

    # Step 2: LilyPond → PDF（使用已有的 render_lilypond_pdf）
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
