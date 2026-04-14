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
    echo [ERROR] 鏈壘鍒?audiveris 源碼鐩綍銆?    echo 鍙敭杈撳嚭鍒版槸 %AUDIVERIS_SOURCE%
    exit /b 1
)

if not exist "%BASE_DIR%lilypond-2.24.4\bin\lilypond.exe" (
    echo [ERROR] 鏈壘鍒?lilypond-2.24.4 杩愯鐩綍銆?    exit /b 1
)

if not exist "%BASE_DIR%jdk\bin\java.exe" (
    echo [INFO] 姝ｅ湪妫€鏌ュ彲鐢ㄧ殑 JDK 17+ ...
    set "JDK_SOURCE="
    for /d %%I in ("%LOCALAPPDATA%\Programs\Microsoft\jdk-*") do if exist "%%~fI\bin\java.exe" set "JDK_SOURCE=%%~fI"
    if not defined JDK_SOURCE for /d %%I in ("%LOCALAPPDATA%\Programs\Eclipse Adoptium\jdk-*") do if exist "%%~fI\bin\java.exe" set "JDK_SOURCE=%%~fI"
    if not defined JDK_SOURCE for /d %%I in ("%ProgramFiles%\Microsoft\jdk-*") do if exist "%%~fI\bin\java.exe" set "JDK_SOURCE=%%~fI"
    if not defined JDK_SOURCE for /d %%I in ("%ProgramFiles%\Eclipse Adoptium\jdk-*") do if exist "%%~fI\bin\java.exe" set "JDK_SOURCE=%%~fI"
    if defined JDK_SOURCE (
        echo [INFO] 宸叉壘鍒?JDK: %JDK_SOURCE%
        robocopy "%JDK_SOURCE%" "%BASE_DIR%jdk" /E /NFL /NDL /NJH /NJS /NC /NS >nul
    ) else (
        echo [WARN] 鏈娴嬪埌鏈湴 JDK 17+锛岀敓鎴愮殑瀹夎鍖呭皢渚濊禆鐩爣鏈哄櫒鑷瀹夎 Java銆?    )
)

if exist "%BASE_DIR%jdk\bin\java.exe" (
    set "JAVA_HOME=%BASE_DIR%jdk"
    set "PATH=%BASE_DIR%jdk\bin;%PATH%"
)

echo [1/3] 姝ｅ湪鍑嗗鏈€灏忚繍琛屾椂绱犳潗...
if not exist "%AUDIVERIS_RUNTIME_SRC%\bin\Audiveris.bat" (
    call "%AUDIVERIS_SOURCE%\gradlew.bat" -p "%AUDIVERIS_SOURCE%" --console=plain :app:installDist
    if errorlevel 1 (
        echo [ERROR] Audiveris 杩愯鏃舵瀯寤哄け璐ャ€?        exit /b 1
    )
)

if exist "%PACKAGE_ASSETS%" rmdir /s /q "%PACKAGE_ASSETS%"
mkdir "%PACKAGE_ASSETS%\audiveris-runtime"
mkdir "%PACKAGE_ASSETS%\lilypond-runtime"
mkdir "%PACKAGE_ASSETS%\oemer-runtime"
robocopy "%AUDIVERIS_RUNTIME_SRC%" "%PACKAGE_ASSETS%\audiveris-runtime" /E /NFL /NDL /NJH /NJS /NC /NS >nul
for %%D in (bin etc lib libexec licenses share) do (
    if exist "%BASE_DIR%lilypond-2.24.4\%%D" robocopy "%BASE_DIR%lilypond-2.24.4\%%D" "%PACKAGE_ASSETS%\lilypond-runtime\%%D" /E /NFL /NDL /NJH /NJS /NC /NS >nul
)
if exist "%AUDIVERIS_SOURCE%\app\dev\tessdata" robocopy "%AUDIVERIS_SOURCE%\app\dev\tessdata" "%PACKAGE_ASSETS%\tessdata" /E /NFL /NDL /NJH /NJS /NC /NS >nul
if exist "%BASE_DIR%package-assets\waifu2x-runtime\waifu2x-ncnn-vulkan.exe" (
    echo [INFO] waifu2x-runtime 鍖呭凡灏辩华锛屾棤闇€閲嶆柊澶嶅埗銆?) else if exist "%BASE_DIR%waifu2x-ncnn-vulkan\waifu2x-ncnn-vulkan.exe" (
    mkdir "%PACKAGE_ASSETS%\waifu2x-runtime"
    robocopy "%BASE_DIR%waifu2x-ncnn-vulkan" "%PACKAGE_ASSETS%\waifu2x-runtime" /E /NFL /NDL /NJH /NJS /NC /NS >nul
    echo [INFO] waifu2x-runtime 宸插鍒躲€?) else (
    echo [WARN] 鏈壘鍒?waifu2x-ncnn-vulkan 鐩綍锛岃烦杩囪秴鍒嗚鲸鐜囨ā鍧楁墦鍖呫€?)

if exist "%BASE_DIR%omr_engine\homr" (
    echo [INFO] homr 源碼鏃犳硶鍒╀簨杩?鏄?瑕佸垱寤烘ā鏉ャ€?
) else (
    echo [WARN] omr_engine\homr 鏈纭鐩綍锛岃烦璇烽€? homr 閰嶇疆鍚堝苟涓嶈兘闇€瑕佸瓨鍦?
)

set "PYTHON_CMD=%BASE_DIR%.venv\Scripts\python.exe"
if not exist "%PYTHON_CMD%" (
    set "PYTHON_CMD=py -3"
    where py >nul 2>nul
    if errorlevel 1 set "PYTHON_CMD=python"
)

:: 灏?package-assets\oemer-runtime 涓殑妯″瀷鍚屾鍒?venv锛堜緵 PyInstaller collect_all 鎵撳寘銆?
if exist "%BASE_DIR%package-assets\oemer-runtime\checkpoints\unet_big\model.onnx" (
    echo [INFO] 浣跨敤鏈湴 oemer 妯″瀷锛屾棤闇€鑱旂綉涓嬭浇銆?
    %PYTHON_CMD% "%BASE_DIR%scripts\_sync_oemer_to_venv.py" "%BASE_DIR%package-assets\oemer-runtime"
) else (
    echo [2/3] 姝ｅ湪棰勪笅杞?oemer 妯"煷瓨鏁堜腑锛堝凡涓嬭浇鍒欒烦杩囷級...
    call %PYTHON_CMD% "%BASE_DIR%scripts\download_oemer_models.py"
    if errorlevel 1 (
        echo [ERROR] oemer 妯"煷瓨鍦囨湁涓嬭浇澶辫触锛岃妫€鏌ョ綉缁滆繛鎺ュ悗閲嶈瘯銆?        exit /b 1
    )
)

if exist "%BASE_DIR%scripts\_sync_oemer_package_assets.py" (
    call %PYTHON_CMD% "%BASE_DIR%scripts\_sync_oemer_package_assets.py"
    if errorlevel 1 (
        echo [ERROR] oemer runtime sync to package-assets 澶辫触銆?        exit /b 1
    )
)

echo [2/3] 姝ｅ湪鏋勫缓鍙墽琛屾枃浠?..
call %PYTHON_CMD% -m PyInstaller --noconfirm --clean ConvertTool.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller 鎵撳寘澶辫触銆?    exit /b 1
)

set "ISCC_EXE="
for /f "delims=" %%I in ('where iscc 2^>nul') do set "ISCC_EXE=%%I"
if not defined ISCC_EXE if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if not defined ISCC_EXE (
    echo [ERROR] 鏈壘鍒?Inno Setup 缂栬瘧鍣?ISCC.exe銆?    echo 璇峰厛瀹夎锛歸inget install --id JRSoftware.InnoSetup -e
    exit /b 1
)

echo [3/3] 姝ｅ湪鐢熸垚瀹夎鍖?..
if not exist "%BASE_DIR%installer-dist" mkdir "%BASE_DIR%installer-dist"
"%ISCC_EXE%" "%BASE_DIR%convert_setup.iss"
if errorlevel 1 (
    echo [ERROR] 瀹夎鍖呯敓鎴愬け璐ャ€?    exit /b 1
)

echo.
echo [OK] 瀹夎鍖呭凡鐢熸垚锛?BASE_DIR%installer-dist\ConvertTool-Setup-%APP_VERSION%.exe
exit /b 0
