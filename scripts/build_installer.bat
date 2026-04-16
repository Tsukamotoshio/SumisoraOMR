@echo off
setlocal
chcp 65001 >nul

set "BASE_DIR=%~dp0"
cd /d "%BASE_DIR%.."
set "BASE_DIR=%CD%\"
set "APP_VERSION=0.2.2-homr-experimental"

set "AUDIVERIS_SOURCE=%BASE_DIR%omr_engine\audiveris"
set "AUDIVERIS_RUNTIME_SRC=%AUDIVERIS_SOURCE%\app\build\install\app"
set "PACKAGE_ASSETS=%BASE_DIR%package-assets"

if not exist "%AUDIVERIS_SOURCE%\gradlew.bat" (
    set "AUDIVERIS_SOURCE=%BASE_DIR%audiveris-5.10.2"
    set "AUDIVERIS_RUNTIME_SRC=%AUDIVERIS_SOURCE%\app\build\install\app"
)

if not exist "%AUDIVERIS_SOURCE%\gradlew.bat" (
    echo [ERROR] audiveris source directory not found.
    echo Expected path: %AUDIVERIS_SOURCE%
    exit /b 1
)

if not exist "%BASE_DIR%lilypond-2.24.4\bin\lilypond.exe" (
    echo [ERROR] lilypond-2.24.4 runtime directory not found.
    exit /b 1
)

if not exist "%BASE_DIR%jdk\bin\java.exe" (
    echo [INFO] Checking for available JDK 17+ ...
    set "JDK_SOURCE="
    for /d %%I in ("%LOCALAPPDATA%\Programs\Microsoft\jdk-*") do if exist "%%~fI\bin\java.exe" set "JDK_SOURCE=%%~fI"
    if not defined JDK_SOURCE for /d %%I in ("%LOCALAPPDATA%\Programs\Eclipse Adoptium\jdk-*") do if exist "%%~fI\bin\java.exe" set "JDK_SOURCE=%%~fI"
    if not defined JDK_SOURCE for /d %%I in ("%ProgramFiles%\Microsoft\jdk-*") do if exist "%%~fI\bin\java.exe" set "JDK_SOURCE=%%~fI"
    if not defined JDK_SOURCE for /d %%I in ("%ProgramFiles%\Eclipse Adoptium\jdk-*") do if exist "%%~fI\bin\java.exe" set "JDK_SOURCE=%%~fI"
    if defined JDK_SOURCE (
        echo [INFO] Found JDK: %JDK_SOURCE%
        robocopy "%JDK_SOURCE%" "%BASE_DIR%jdk" /E /NFL /NDL /NJH /NJS /NC /NS >nul
    ) else (
        echo [WARN] No local JDK 17+ found; installer will require Java on the target machine.
    )
)

if exist "%BASE_DIR%jdk\bin\java.exe" (
    set "JAVA_HOME=%BASE_DIR%jdk"
    set "PATH=%BASE_DIR%jdk\bin;%PATH%"
)

echo [1/3] Preparing minimal runtime assets...
if not exist "%AUDIVERIS_RUNTIME_SRC%\bin\Audiveris.bat" (
    call "%AUDIVERIS_SOURCE%\gradlew.bat" -p "%AUDIVERIS_SOURCE%" --console=plain :app:installDist
    if errorlevel 1 (
        echo [ERROR] Audiveris runtime build failed.
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
    echo [INFO] waifu2x-runtime already exists, skipping.
    goto :waifu2x_done
)
if exist "%BASE_DIR%waifu2x-ncnn-vulkan\waifu2x-ncnn-vulkan.exe" (
    mkdir "%PACKAGE_ASSETS%\waifu2x-runtime"
    robocopy "%BASE_DIR%waifu2x-ncnn-vulkan" "%PACKAGE_ASSETS%\waifu2x-runtime" /E /NFL /NDL /NJH /NJS /NC /NS >nul
    echo [INFO] waifu2x-runtime copied.
    goto :waifu2x_done
)
:waifu2x_done

echo [DEBUG] skipping homr config check

echo [DEBUG] after homr config
set "PYTHON_CMD=%BASE_DIR%.venv\Scripts\python.exe"
echo [TRACE] PYTHON_CMD initial=%PYTHON_CMD%
if not exist "%PYTHON_CMD%" (
    echo [TRACE] python not found, fallback
    set "PYTHON_CMD=py -3"
    where py >nul 2>nul
    if errorlevel 1 set "PYTHON_CMD=python"
)
echo [TRACE] PYTHON_CMD final=%PYTHON_CMD%

echo [2/3] Building executable...
call %PYTHON_CMD% -m PyInstaller --noconfirm --clean ConvertTool.spec
if errorlevel 1 ( echo [ERROR] PyInstaller build failed. & exit /b 1 )

set "ISCC_EXE="
for /f "delims=" %%I in ('where iscc 2^>nul') do set "ISCC_EXE=%%I"
if not defined ISCC_EXE if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if not defined ISCC_EXE (
    echo [ERROR] Inno Setup compiler ISCC.exe not found.
    echo Please install first: winget install --id JRSoftware.InnoSetup -e
    exit /b 1
)

echo [3/3] Generating installer...
if not exist "%BASE_DIR%installer-dist" mkdir "%BASE_DIR%installer-dist"
"%ISCC_EXE%" "%BASE_DIR%convert_setup.iss"
if errorlevel 1 (
    echo [ERROR] Installer generation failed.
    exit /b 1
)

echo.
echo [OK] Installer created: %BASE_DIR%installer-dist\ConvertTool-Setup-%APP_VERSION%.exe
exit /b 0
