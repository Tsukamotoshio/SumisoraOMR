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

from ..config import (
    AUDIVERIS_INSTALL_DIR_NAME,
    AUDIVERIS_MSI_NAMES,
    AUDIVERIS_RUNTIME_DIR_NAME,
    AUDIVERIS_SOURCE_DIR_NAMES,
    DEFAULT_AUDIVERIS_MIN_JAVA_VERSION,
    LOGGER,
    MAX_AUDIVERIS_SECONDS,
    OMR_ENGINE_DIR_NAME,
)
from ..utils import (
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

