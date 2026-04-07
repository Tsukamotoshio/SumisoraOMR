@echo off
setlocal
chcp 65001 >nul

set "BASE_DIR=%~dp0"
cd /d "%BASE_DIR%"

set "AUDIVERIS_SOURCE=%BASE_DIR%audiveris-5.10.2"
set "AUDIVERIS_RUNTIME_SRC=%AUDIVERIS_SOURCE%\app\build\install\app"
set "PACKAGE_ASSETS=%BASE_DIR%package-assets"

if not exist "%AUDIVERIS_SOURCE%\gradlew.bat" (
    echo [ERROR] 未找到 audiveris-5.10.2 源码目录，请先将完整源码放到项目根目录。
    exit /b 1
)

if not exist "%BASE_DIR%lilypond-2.24.4\bin\lilypond.exe" (
    echo [ERROR] 未找到 lilypond-2.24.4 运行目录。
    exit /b 1
)

if not exist "%BASE_DIR%jdk\bin\java.exe" (
    echo [INFO] 正在检查可用的 JDK 17+ ...
    set "JDK_SOURCE="
    for /d %%I in ("%LOCALAPPDATA%\Programs\Microsoft\jdk-*") do if exist "%%~fI\bin\java.exe" set "JDK_SOURCE=%%~fI"
    if not defined JDK_SOURCE for /d %%I in ("%LOCALAPPDATA%\Programs\Eclipse Adoptium\jdk-*") do if exist "%%~fI\bin\java.exe" set "JDK_SOURCE=%%~fI"
    if not defined JDK_SOURCE for /d %%I in ("%ProgramFiles%\Microsoft\jdk-*") do if exist "%%~fI\bin\java.exe" set "JDK_SOURCE=%%~fI"
    if not defined JDK_SOURCE for /d %%I in ("%ProgramFiles%\Eclipse Adoptium\jdk-*") do if exist "%%~fI\bin\java.exe" set "JDK_SOURCE=%%~fI"
    if defined JDK_SOURCE (
        echo [INFO] 已找到 JDK: %JDK_SOURCE%
        robocopy "%JDK_SOURCE%" "%BASE_DIR%jdk" /E /NFL /NDL /NJH /NJS /NC /NS >nul
    ) else (
        echo [WARN] 未检测到本地 JDK 17+，生成的安装包将依赖目标机器自行安装 Java。
    )
)

if exist "%BASE_DIR%jdk\bin\java.exe" (
    set "JAVA_HOME=%BASE_DIR%jdk"
    set "PATH=%BASE_DIR%jdk\bin;%PATH%"
)

echo [1/3] 正在准备最小运行时素材...
if not exist "%AUDIVERIS_RUNTIME_SRC%\bin\Audiveris.bat" (
    call "%AUDIVERIS_SOURCE%\gradlew.bat" -p "%AUDIVERIS_SOURCE%" --console=plain :app:installDist
    if errorlevel 1 (
        echo [ERROR] Audiveris 运行时构建失败。
        exit /b 1
    )
)

if exist "%PACKAGE_ASSETS%" rmdir /s /q "%PACKAGE_ASSETS%"
mkdir "%PACKAGE_ASSETS%\audiveris-runtime"
mkdir "%PACKAGE_ASSETS%\lilypond-runtime"
robocopy "%AUDIVERIS_RUNTIME_SRC%" "%PACKAGE_ASSETS%\audiveris-runtime" /E /NFL /NDL /NJH /NJS /NC /NS >nul
for %%D in (bin etc lib libexec licenses share) do (
    if exist "%BASE_DIR%lilypond-2.24.4\%%D" robocopy "%BASE_DIR%lilypond-2.24.4\%%D" "%PACKAGE_ASSETS%\lilypond-runtime\%%D" /E /NFL /NDL /NJH /NJS /NC /NS >nul
)
if exist "%AUDIVERIS_SOURCE%\app\dev\tessdata" robocopy "%AUDIVERIS_SOURCE%\app\dev\tessdata" "%PACKAGE_ASSETS%\tessdata" /E /NFL /NDL /NJH /NJS /NC /NS >nul
if exist "%BASE_DIR%package-assets\waifu2x-runtime\waifu2x-ncnn-vulkan.exe" (
    echo [INFO] waifu2x-runtime 包已就绪，无需重新复制。
) else if exist "%BASE_DIR%waifu2x-ncnn-vulkan\waifu2x-ncnn-vulkan.exe" (
    mkdir "%PACKAGE_ASSETS%\waifu2x-runtime"
    robocopy "%BASE_DIR%waifu2x-ncnn-vulkan" "%PACKAGE_ASSETS%\waifu2x-runtime" /E /NFL /NDL /NJH /NJS /NC /NS >nul
    echo [INFO] waifu2x-runtime 已复制。
) else (
    echo [WARN] 未找到 waifu2x-ncnn-vulkan 目录，跳过超分辨率模块打包。
)

set "PYTHON_CMD=%BASE_DIR%.venv\Scripts\python.exe"
if not exist "%PYTHON_CMD%" (
    set "PYTHON_CMD=py -3"
    where py >nul 2>nul
    if errorlevel 1 set "PYTHON_CMD=python"
)

:: 将 package-assets\oemer-runtime 中的模型同步到 venv（供 PyInstaller collect_all 打包）
if exist "%BASE_DIR%package-assets\oemer-runtime\checkpoints\unet_big\model.onnx" (
    echo [INFO] 使用本地 oemer 模型，无需联网下载。
    %PYTHON_CMD% "%BASE_DIR%_sync_oemer_to_venv.py" "%BASE_DIR%package-assets\oemer-runtime"
) else (
    echo [2/3] 正在预下载 oemer 模型权重（已下载则跳过）...
    call %PYTHON_CMD% download_oemer_models.py
    if errorlevel 1 (
        echo [ERROR] oemer 模型权重下载失败，请检查网络连接后重试。
        exit /b 1
    )
)

echo [2/3] 正在构建可执行文件...
call %PYTHON_CMD% -m PyInstaller --noconfirm --clean ConvertTool.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller 打包失败。
    exit /b 1
)

set "ISCC_EXE="
for /f "delims=" %%I in ('where iscc 2^>nul') do set "ISCC_EXE=%%I"
if not defined ISCC_EXE if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if not defined ISCC_EXE (
    echo [ERROR] 未找到 Inno Setup 编译器 ISCC.exe。
    echo 请先安装：winget install --id JRSoftware.InnoSetup -e
    exit /b 1
)

echo [3/3] 正在生成安装包...
"%ISCC_EXE%" "%BASE_DIR%convert_setup.iss"
if errorlevel 1 (
    echo [ERROR] 安装包生成失败。
    exit /b 1
)

echo.
echo [OK] 安装包已生成：%BASE_DIR%installer-dist\ConvertTool-Setup-0.1.1.exe
exit /b 0
