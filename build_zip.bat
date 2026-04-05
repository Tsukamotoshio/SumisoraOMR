@echo off
setlocal
chcp 65001 >nul

set "BASE_DIR=%~dp0"
cd /d "%BASE_DIR%"

set "APP_VERSION=0.2.1-experimental"
set "APP_NAME=ConvertTool"
set "ZIP_NAME=%APP_NAME%-Portable-%APP_VERSION%"
set "STAGE_DIR=%BASE_DIR%zip-stage\%ZIP_NAME%"
set "OUTPUT_ZIP=%BASE_DIR%installer-dist\%ZIP_NAME%.zip"

:: ── 步骤 1：package-assets（如已存在则跳过）──────────────────────────────────
:: 需同时满足：lilypond-runtime 和 waifu2x-runtime（如本地有源文件）均已就绪
set "ASSETS_READY=1"
if not exist "%BASE_DIR%package-assets\lilypond-runtime\bin" set "ASSETS_READY=0"
if exist "%BASE_DIR%waifu2x-ncnn-vulkan\waifu2x-ncnn-vulkan.exe" (
    if not exist "%BASE_DIR%package-assets\waifu2x-runtime\waifu2x-ncnn-vulkan.exe" set "ASSETS_READY=0"
)
if "%ASSETS_READY%"=="1" (
    echo [跳过] package-assets 已存在，无需重新准备。
) else (
    set "AUDIVERIS_SOURCE=%BASE_DIR%audiveris-5.10.2"
    set "AUDIVERIS_RUNTIME_SRC=%AUDIVERIS_SOURCE%\app\build\install\app"
    set "PACKAGE_ASSETS=%BASE_DIR%package-assets"

    if not exist "%AUDIVERIS_SOURCE%\gradlew.bat" (
        echo [ERROR] 未找到 audiveris-5.10.2 源码目录。
        exit /b 1
    )
    echo [1/3] 正在准备运行时素材...
    if not exist "%AUDIVERIS_RUNTIME_SRC%\bin\Audiveris.bat" (
        call "%AUDIVERIS_SOURCE%\gradlew.bat" -p "%AUDIVERIS_SOURCE%" --console=plain :app:installDist
        if errorlevel 1 ( echo [ERROR] Audiveris 构建失败。& exit /b 1 )
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
        echo [INFO] waifu2x-runtime 包已就绪。
    ) else if exist "%BASE_DIR%waifu2x-ncnn-vulkan\waifu2x-ncnn-vulkan.exe" (
        mkdir "%PACKAGE_ASSETS%\waifu2x-runtime"
        robocopy "%BASE_DIR%waifu2x-ncnn-vulkan" "%PACKAGE_ASSETS%\waifu2x-runtime" /E /NFL /NDL /NJH /NJS /NC /NS >nul
        echo [INFO] waifu2x-runtime 已复制。
    ) else (
        echo [WARN] 未找到 waifu2x-ncnn-vulkan 目录，跳过超分辨率模块打包。
    )
)

:: ── 步骤 2：PyInstaller（如已存在则跳过）────────────────────────────────────
if exist "%BASE_DIR%dist\%APP_NAME%\%APP_NAME%.exe" (
    echo [跳过] dist\%APP_NAME% 已存在，无需重新打包。
) else (
    echo [2/3] 正在准备 oemer 模型权重...
    set "PYTHON_CMD=%BASE_DIR%.venv\Scripts\python.exe"
    if not exist "%PYTHON_CMD%" (
        set "PYTHON_CMD=py -3"
        where py >nul 2>nul
        if errorlevel 1 set "PYTHON_CMD=python"
    )
    if exist "%BASE_DIR%package-assets\oemer-runtime\checkpoints\unet_big\model.onnx" (
        echo [INFO] 使用本地 oemer 模型，无需联网下载。
        %PYTHON_CMD% "%BASE_DIR%_sync_oemer_to_venv.py" "%BASE_DIR%package-assets\oemer-runtime"
    ) else (
        call %PYTHON_CMD% download_oemer_models.py
        if errorlevel 1 ( echo [ERROR] oemer 模型权重下载失败，请检查网络连接后重试。& exit /b 1 )
    )

    echo [2/3] 正在构建可执行文件...
    call %PYTHON_CMD% -m PyInstaller --noconfirm --clean ConvertTool.spec
    if errorlevel 1 ( echo [ERROR] PyInstaller 打包失败。& exit /b 1 )
)

:: ── 步骤 3：组装便携目录 ──────────────────────────────────────────────────────
echo [3/3] 正在组装便携目录...
if exist "%BASE_DIR%zip-stage" rmdir /s /q "%BASE_DIR%zip-stage"
mkdir "%STAGE_DIR%"
mkdir "%STAGE_DIR%\Input"
mkdir "%STAGE_DIR%\Output"

robocopy "%BASE_DIR%dist\%APP_NAME%"               "%STAGE_DIR%"                            /E /NFL /NDL /NJH /NJS /NC /NS >nul
robocopy "%BASE_DIR%package-assets\lilypond-runtime" "%STAGE_DIR%\lilypond-runtime"         /E /NFL /NDL /NJH /NJS /NC /NS >nul
robocopy "%BASE_DIR%package-assets\audiveris-runtime" "%STAGE_DIR%\audiveris-runtime"       /E /NFL /NDL /NJH /NJS /NC /NS >nul
if exist "%BASE_DIR%package-assets\tessdata"        robocopy "%BASE_DIR%package-assets\tessdata"        "%STAGE_DIR%\tessdata"        /E /NFL /NDL /NJH /NJS /NC /NS >nul
if exist "%BASE_DIR%package-assets\waifu2x-runtime" robocopy "%BASE_DIR%package-assets\waifu2x-runtime" "%STAGE_DIR%\waifu2x-runtime" /E /NFL /NDL /NJH /NJS /NC /NS >nul
if exist "%BASE_DIR%jdk\bin\java.exe"               robocopy "%BASE_DIR%jdk"                            "%STAGE_DIR%\jdk"             /E /NFL /NDL /NJH /NJS /NC /NS >nul
:: oemer 模型（已内嵌于 PyInstaller 分发包中，此处仅供离线参考；不重复复制大文件）
:: oemer checkpoints 和 sklearn_models 已由 collect_all('oemer') 打包进 ConvertTool 目录。
if exist "%BASE_DIR%Input\Do_You_Hear_the_People_Sing.pdf"       copy /y "%BASE_DIR%Input\Do_You_Hear_the_People_Sing.pdf"       "%STAGE_DIR%\Input\" >nul
if exist "%BASE_DIR%Input\Sunset_Waltz_By_Yoko_Shimomura-Violin.pdf" copy /y "%BASE_DIR%Input\Sunset_Waltz_By_Yoko_Shimomura-Violin.pdf" "%STAGE_DIR%\Input\" >nul
copy /y "%BASE_DIR%jianpu-ly.py"            "%STAGE_DIR%\" >nul
copy /y "%BASE_DIR%README_EN.txt"           "%STAGE_DIR%\README.txt" >nul
copy /y "%BASE_DIR%读我.txt"               "%STAGE_DIR%\" >nul
copy /y "%BASE_DIR%THIRD_PARTY_NOTICES.md"  "%STAGE_DIR%\" >nul
copy /y "%BASE_DIR%LICENSE"                 "%STAGE_DIR%\" >nul

:: ── 步骤 4：压缩为 zip ────────────────────────────────────────────────────────
if not exist "%BASE_DIR%installer-dist" mkdir "%BASE_DIR%installer-dist"
if exist "%OUTPUT_ZIP%" del /q "%OUTPUT_ZIP%"

powershell -NoProfile -Command ^
  "Compress-Archive -Path '%BASE_DIR%zip-stage\%ZIP_NAME%' -DestinationPath '%OUTPUT_ZIP%' -CompressionLevel Optimal"
if errorlevel 1 ( echo [ERROR] 压缩失败。& exit /b 1 )

rmdir /s /q "%BASE_DIR%zip-stage"

echo.
echo [OK] 便携压缩包已生成：%OUTPUT_ZIP%
exit /b 0
