@echo off

setlocal enabledelayedexpansion

chcp 65001 >nul



set "BASE_DIR=%~dp0"
cd /d "%BASE_DIR%.."
set "BASE_DIR=%CD%\"

echo [DEBUG] BASE_DIR=%BASE_DIR%




set "APP_VERSION=0.2.4"



set "APP_NAME=ConvertTool"

set "ZIP_NAME=%APP_NAME%-Portable-%APP_VERSION%"

set "STAGE_DIR=%BASE_DIR%zip-stage\%ZIP_NAME%"

set "OUTPUT_ZIP=%BASE_DIR%installer-dist\%ZIP_NAME%.zip"



:: ---- Step 1: package-assets (skip if already prepared) ----

:: Requires: lilypond-runtime, audiveris-runtime, waifu2x-runtime (if local source exists)
set "ASSETS_READY=1"

if not exist "%BASE_DIR%package-assets\lilypond-runtime\bin" set "ASSETS_READY=0"
if not exist "%BASE_DIR%package-assets\audiveris-runtime\bin\Audiveris.bat" set "ASSETS_READY=0"

if exist "%BASE_DIR%waifu2x-ncnn-vulkan\waifu2x-ncnn-vulkan.exe" (

    if not exist "%BASE_DIR%package-assets\waifu2x-runtime\waifu2x-ncnn-vulkan.exe" set "ASSETS_READY=0"

)

if "%ASSETS_READY%"=="1" (

echo [SKIP] package-assets already exists, skipping preparation.
) else (

    set "AUDIVERIS_SOURCE=!BASE_DIR!omr_engine\audiveris"
    if not exist "!AUDIVERIS_SOURCE!\gradlew.bat" (
        set "AUDIVERIS_SOURCE=!BASE_DIR!audiveris-5.10.2"
    )

    set "AUDIVERIS_RUNTIME_SRC=!AUDIVERIS_SOURCE!\app\build\install\app"

    set "PACKAGE_ASSETS=!BASE_DIR!package-assets"

    if not exist "!AUDIVERIS_SOURCE!\gradlew.bat" (

echo [ERROR] Audiveris source directory not found.
        exit /b 1

    )

echo [1/3] Preparing runtime assets...

    if not exist "!AUDIVERIS_RUNTIME_SRC!\bin\Audiveris.bat" (

        call "!AUDIVERIS_SOURCE!\gradlew.bat" -p "!AUDIVERIS_SOURCE!" --console=plain :app:installDist

    if errorlevel 1 ( echo [ERROR] Audiveris build failed. ^& exit /b 1 )

    )

    if exist "!PACKAGE_ASSETS!" rmdir /s /q "!PACKAGE_ASSETS!"

    mkdir "!PACKAGE_ASSETS!\audiveris-runtime"

    mkdir "!PACKAGE_ASSETS!\lilypond-runtime"

    robocopy "!AUDIVERIS_RUNTIME_SRC!" "!PACKAGE_ASSETS!\audiveris-runtime" /E /NFL /NDL /NJH /NJS /NC /NS >nul

    for %%D in (bin etc lib libexec licenses share) do (

        if exist "!BASE_DIR!lilypond-2.24.4\%%D" robocopy "!BASE_DIR!lilypond-2.24.4\%%D" "!PACKAGE_ASSETS!\lilypond-runtime\%%D" /E /NFL /NDL /NJH /NJS /NC /NS >nul

    )

    if exist "!AUDIVERIS_SOURCE!\app\dev\tessdata" robocopy "!AUDIVERIS_SOURCE!\app\dev\tessdata" "!PACKAGE_ASSETS!\tessdata" /E /NFL /NDL /NJH /NJS /NC /NS >nul

    if exist "!BASE_DIR!package-assets\waifu2x-runtime\waifu2x-ncnn-vulkan.exe" (

        echo [INFO] waifu2x-runtime already exists, skipping.
    ) else if exist "!BASE_DIR!waifu2x-ncnn-vulkan\waifu2x-ncnn-vulkan.exe" (

        mkdir "!PACKAGE_ASSETS!\waifu2x-runtime"

        robocopy "!BASE_DIR!waifu2x-ncnn-vulkan" "!PACKAGE_ASSETS!\waifu2x-runtime" /E /NFL /NDL /NJH /NJS /NC /NS >nul

    echo [INFO] waifu2x-runtime copied.
    ) else (

echo [WARN] waifu2x-ncnn-vulkan not found, skipping super-resolution module.
    )

)



:: ---- Step 2: PyInstaller ----

    echo [2/3] Building executable...

    "%BASE_DIR%.venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean ConvertTool.spec

    if errorlevel 1 ( echo [ERROR] PyInstaller build failed. ^& exit /b 1 )



:: ---- Step 3: Assemble portable directory ----

echo [3/3] Assembling portable directory...

if exist "%BASE_DIR%zip-stage" rmdir /s /q "%BASE_DIR%zip-stage"

mkdir "%STAGE_DIR%"

mkdir "%STAGE_DIR%\Input"

mkdir "%STAGE_DIR%\Output"



robocopy "%BASE_DIR%dist\%APP_NAME%"               "%STAGE_DIR%"                            /E /NFL /NDL /NJH /NJS /NC /NS >nul

robocopy "%BASE_DIR%package-assets\lilypond-runtime" "%STAGE_DIR%\lilypond-runtime"         /E /NFL /NDL /NJH /NJS /NC /NS >nul

robocopy "%BASE_DIR%package-assets\audiveris-runtime" "%STAGE_DIR%\audiveris-runtime"       /E /NFL /NDL /NJH /NJS /NC /NS >nul

if exist "%BASE_DIR%package-assets\tessdata"        robocopy "%BASE_DIR%package-assets\tessdata"        "%STAGE_DIR%\tessdata"        /E /NFL /NDL /NJH /NJS /NC /NS >nul

if exist "%BASE_DIR%package-assets\waifu2x-runtime" robocopy "%BASE_DIR%package-assets\waifu2x-runtime" "%STAGE_DIR%\waifu2x-runtime" /E /NFL /NDL /NJH /NJS /NC /NS >nul

if exist "%BASE_DIR%jdk" (
    robocopy "%BASE_DIR%jdk" "%STAGE_DIR%\jdk" /E /NFL /NDL /NJH /NJS /NC /NS >nul
    echo [INFO] jdk 已复制到便携目录。
) else (
    echo [WARN] 未找到 jdk 目录，Audiveris 可能无法在便携版中运行。
)

if exist "%BASE_DIR%omr_engine\homr" (
    echo [INFO] homr source found ^(already bundled via PyInstaller^).
) else (
    echo [WARN] omr_engine\homr not found, skipping homr config.
)
if exist "%BASE_DIR%Input\Do_You_Hear_the_People_Sing.pdf"       copy /y "%BASE_DIR%Input\Do_You_Hear_the_People_Sing.pdf"       "%STAGE_DIR%\Input\" >nul

if exist "%BASE_DIR%Input\Sunset_Waltz_By_Yoko_Shimomura-Violin.pdf" copy /y "%BASE_DIR%Input\Sunset_Waltz_By_Yoko_Shimomura-Violin.pdf" "%STAGE_DIR%\Input\" >nul

copy /y "%BASE_DIR%scripts\jianpu-ly.py"            "%STAGE_DIR%\" >nul

copy /y "%BASE_DIR%README_EN.txt"           "%STAGE_DIR%\README.txt" >nul

copy /y "%BASE_DIR%读我.txt"               "%STAGE_DIR%\" >nul

copy /y "%BASE_DIR%THIRD_PARTY_NOTICES.md"  "%STAGE_DIR%\" >nul

copy /y "%BASE_DIR%LICENSE"                 "%STAGE_DIR%\" >nul



:: ---- Step 4: Compress to zip ----

if not exist "%BASE_DIR%installer-dist" mkdir "%BASE_DIR%installer-dist"

if exist "%OUTPUT_ZIP%" del /q "%OUTPUT_ZIP%"



if not exist "%STAGE_DIR%\%APP_NAME%.exe" (

  echo [ERROR] Assembly failed: %STAGE_DIR%\%APP_NAME%.exe not found.
  exit /b 1

)



:: ── 优先使用 7-Zip（支持超长路径，不会静默漏文件）；回退到 PowerShell Compress-Archive ──
set "SEVENZIP_EXE="
for /f "delims=" %%I in ('where 7z 2^>nul') do set "SEVENZIP_EXE=%%I"
if not defined SEVENZIP_EXE if exist "C:\Program Files\7-Zip\7z.exe" set "SEVENZIP_EXE=C:\Program Files\7-Zip\7z.exe"
if not defined SEVENZIP_EXE if exist "C:\Program Files (x86)\7-Zip\7z.exe" set "SEVENZIP_EXE=C:\Program Files (x86)\7-Zip\7z.exe"

if defined SEVENZIP_EXE (
    echo [4/4] Compressing with 7-Zip ...
    pushd "%BASE_DIR%zip-stage"
    "%SEVENZIP_EXE%" a -tzip -mx=5 -mmt=4 "%OUTPUT_ZIP%" "%ZIP_NAME%"
    set "ZIP_ERR=%ERRORLEVEL%"
    popd
    if "!ZIP_ERR!" neq "0" ( echo [ERROR] 7-Zip compression failed. & exit /b 1 )
) else (
    echo [4/4] 7-Zip not found, falling back to PowerShell Compress-Archive ...
    powershell -NoProfile -Command "Compress-Archive -LiteralPath '%STAGE_DIR%' -DestinationPath '%OUTPUT_ZIP%' -CompressionLevel Optimal -Force"
    if errorlevel 1 ( echo [ERROR] PowerShell compression failed. & exit /b 1 )
)



rmdir /s /q "%BASE_DIR%zip-stage"



echo.

echo [OK] Portable zip created: %OUTPUT_ZIP%

exit /b 0
