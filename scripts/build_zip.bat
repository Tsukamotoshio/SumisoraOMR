@echo off

setlocal enabledelayedexpansion

chcp 65001 >nul



set "BASE_DIR=%~dp0"
cd /d "%BASE_DIR%.."
set "BASE_DIR=%CD%\"

echo [DEBUG] BASE_DIR=%BASE_DIR%




set "APP_VERSION=0.2.2-homr-experimental"



set "APP_NAME=ConvertTool"

set "ZIP_NAME=%APP_NAME%-Portable-%APP_VERSION%"

set "STAGE_DIR=%BASE_DIR%zip-stage\%ZIP_NAME%"

set "OUTPUT_ZIP=%BASE_DIR%installer-dist\%ZIP_NAME%.zip"



:: 鈹€鈹€ 姝ラ 1锛歱ackage-assets锛堝宸插瓨鍦ㄥ垯璺宠繃锛夆攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

:: 闇€鍚屾椂婊¤冻锛歭ilypond-runtime 鍜?waifu2x-runtime锛堝鏈湴鏈夋簮鏂囦欢锛夊潎宸插氨缁?
set "ASSETS_READY=1"

if not exist "%BASE_DIR%package-assets\lilypond-runtime\bin" set "ASSETS_READY=0"
if not exist "%BASE_DIR%package-assets\audiveris-runtime\bin\Audiveris.bat" set "ASSETS_READY=0"
if not exist "%BASE_DIR%package-assets\oemer-runtime\checkpoints\unet_big\model.onnx" set "ASSETS_READY=0"

if exist "%BASE_DIR%waifu2x-ncnn-vulkan\waifu2x-ncnn-vulkan.exe" (

    if not exist "%BASE_DIR%package-assets\waifu2x-runtime\waifu2x-ncnn-vulkan.exe" set "ASSETS_READY=0"

)

if "%ASSETS_READY%"=="1" (

    echo [璺宠繃] package-assets 宸插瓨鍦紝鏃犻渶閲嶆柊鍑嗗銆?
) else (

    set "AUDIVERIS_SOURCE=!BASE_DIR!omr_engine\audiveris"
    if not exist "!AUDIVERIS_SOURCE!\gradlew.bat" (
        set "AUDIVERIS_SOURCE=!BASE_DIR!audiveris-5.10.2"
    )

    set "AUDIVERIS_RUNTIME_SRC=!AUDIVERIS_SOURCE!\app\build\install\app"

    set "PACKAGE_ASSETS=!BASE_DIR!package-assets"

    if not exist "!AUDIVERIS_SOURCE!\gradlew.bat" (

        echo [ERROR] 鏈壘鍒?audiveris 源碼鐩綍銆?
        exit /b 1

    )

    echo [1/3] 姝ｅ湪鍑嗗杩愯鏃剁礌鏉?..

    if not exist "!AUDIVERIS_RUNTIME_SRC!\bin\Audiveris.bat" (

        call "!AUDIVERIS_SOURCE!\gradlew.bat" -p "!AUDIVERIS_SOURCE!" --console=plain :app:installDist

        if errorlevel 1 ( echo [ERROR] Audiveris 鏋勫缓澶辫触銆? exit /b 1 )

    )

    if exist "!PACKAGE_ASSETS!" rmdir /s /q "!PACKAGE_ASSETS!"

    mkdir "!PACKAGE_ASSETS!\audiveris-runtime"

    mkdir "!PACKAGE_ASSETS!\lilypond-runtime"

    mkdir "!PACKAGE_ASSETS!\oemer-runtime"

    robocopy "!AUDIVERIS_RUNTIME_SRC!" "!PACKAGE_ASSETS!\audiveris-runtime" /E /NFL /NDL /NJH /NJS /NC /NS >nul

    for %%D in (bin etc lib libexec licenses share) do (

        if exist "!BASE_DIR!lilypond-2.24.4\%%D" robocopy "!BASE_DIR!lilypond-2.24.4\%%D" "!PACKAGE_ASSETS!\lilypond-runtime\%%D" /E /NFL /NDL /NJH /NJS /NC /NS >nul

    )

    if exist "!AUDIVERIS_SOURCE!\app\dev\tessdata" robocopy "!AUDIVERIS_SOURCE!\app\dev\tessdata" "!PACKAGE_ASSETS!\tessdata" /E /NFL /NDL /NJH /NJS /NC /NS >nul

    if exist "!BASE_DIR!package-assets\waifu2x-runtime\waifu2x-ncnn-vulkan.exe" (

        echo [INFO] waifu2x-runtime 鍖呭凡灏辩华銆?
    ) else if exist "!BASE_DIR!waifu2x-ncnn-vulkan\waifu2x-ncnn-vulkan.exe" (

        mkdir "!PACKAGE_ASSETS!\waifu2x-runtime"

        robocopy "!BASE_DIR!waifu2x-ncnn-vulkan" "!PACKAGE_ASSETS!\waifu2x-runtime" /E /NFL /NDL /NJH /NJS /NC /NS >nul

        echo [INFO] waifu2x-runtime 宸插鍒躲€?
    ) else (

        echo [WARN] 鏈壘鍒?waifu2x-ncnn-vulkan 鐩綍锛岃烦杩囪秴鍒嗚鲸鐜囨ā鍧楁墦鍖呫€?
    )

)



:: 鈹€鈹€ 姝ラ 2锛歅yInstaller锛堝宸插瓨鍦ㄥ垯璺宠繃锛夆攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

if exist "%BASE_DIR%dist\%APP_NAME%\%APP_NAME%.exe" (

    echo [璺宠繃] dist\%APP_NAME% 宸插瓨鍦紝鏃犻渶閲嶆柊鎵撳寘銆?
) else (

    echo [2/3] 姝ｅ湪鍑嗗 oemer 妯″瀷鏉冮噸...



    set "PYTHON_CMD=%BASE_DIR%.venv\Scripts\python.exe"

    if not exist "%PYTHON_CMD%" (

        set "PYTHON_CMD=py -3"

        where py >nul 2>nul

        if errorlevel 1 set "PYTHON_CMD=python"

    )

    if exist "%BASE_DIR%package-assets\oemer-runtime\checkpoints\unet_big\model.onnx" (

        echo [INFO] 浣跨敤鏈湴 oemer 妯″瀷锛屾棤闇€鑱旂綉涓嬭浇銆?
        %PYTHON_CMD% "%BASE_DIR%scripts\_sync_oemer_to_venv.py" "%BASE_DIR%package-assets\oemer-runtime"

    ) else (

        echo [INFO] 姝ｅ湪閫氳繃鑴氭湰妫€鏌?鍚屾 oemer 妯"煷瓨鏁堜腑...

        %PYTHON_CMD% "%BASE_DIR%scripts\download_oemer_models.py"

    )

    if exist "%BASE_DIR%scripts\_sync_oemer_package_assets.py" (
        %PYTHON_CMD% "%BASE_DIR%scripts\_sync_oemer_package_assets.py"
        if errorlevel 1 ( echo [ERROR] oemer runtime sync to package-assets 澶辫触銆? exit /b 1 )
    )

    echo [2/3] 姝ｅ湪鏋勫缓鍙墽琛屾枃浠?..



    "%BASE_DIR%.venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean ConvertTool.spec

    if errorlevel 1 ( echo [ERROR] PyInstaller 鎵撳寘澶辫触銆? exit /b 1 )

)



:: 鈹€鈹€ 姝ラ 3锛氱粍瑁呬究鎼虹洰褰?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

echo [3/3] 姝ｅ湪缁勮渚挎惡鐩綍...

if exist "%BASE_DIR%zip-stage" rmdir /s /q "%BASE_DIR%zip-stage"

mkdir "%STAGE_DIR%"

mkdir "%STAGE_DIR%\Input"

mkdir "%STAGE_DIR%\Output"



robocopy "%BASE_DIR%dist\%APP_NAME%"               "%STAGE_DIR%"                            /E /NFL /NDL /NJH /NJS /NC /NS >nul

robocopy "%BASE_DIR%package-assets\lilypond-runtime" "%STAGE_DIR%\lilypond-runtime"         /E /NFL /NDL /NJH /NJS /NC /NS >nul

robocopy "%BASE_DIR%package-assets\audiveris-runtime" "%STAGE_DIR%\audiveris-runtime"       /E /NFL /NDL /NJH /NJS /NC /NS >nul

if exist "%BASE_DIR%package-assets\tessdata"        robocopy "%BASE_DIR%package-assets\tessdata"        "%STAGE_DIR%\tessdata"        /E /NFL /NDL /NJH /NJS /NC /NS >nul

:: oemer-runtime 不复制到 zip stage：oemer 模型已由 collect_all('oemer') 内嵌到
:: _internal/ 目录中，运行时直接从 Python 包路径加载，无需额外的 oemer-runtime 目录。
:: 避免 ~240 MB 的重复打包。

if exist "%BASE_DIR%package-assets\waifu2x-runtime" robocopy "%BASE_DIR%package-assets\waifu2x-runtime" "%STAGE_DIR%\waifu2x-runtime" /E /NFL /NDL /NJH /NJS /NC /NS >nul

if exist "%BASE_DIR%omr_engine\homr" (
    echo [INFO] homr 源碼鏃犳硶鍒╀簨杩?鏄?瑕佸垱寤烘ā鏉ャ€? 
) else (
    echo [WARN] omr_engine\homr 鏈纭鐩綍锛岃烦璇烽€? homr 閰嶇疆鍚堝苟涓嶈兘闇€瑕佸瓨鍦? 
)
:: oemer 妯″瀷锛堝凡鍐呭祵浜?PyInstaller 鍒嗗彂鍖呬腑锛屾澶勪粎渚涚绾垮弬鑰冿紱涓嶉噸澶嶅鍒跺ぇ鏂囦欢锛?
:: oemer checkpoints 鍜?sklearn_models 宸茬敱 collect_all('oemer') 鎵撳寘杩?ConvertTool 鐩綍銆?
if exist "%BASE_DIR%Input\Do_You_Hear_the_People_Sing.pdf"       copy /y "%BASE_DIR%Input\Do_You_Hear_the_People_Sing.pdf"       "%STAGE_DIR%\Input\" >nul

if exist "%BASE_DIR%Input\Sunset_Waltz_By_Yoko_Shimomura-Violin.pdf" copy /y "%BASE_DIR%Input\Sunset_Waltz_By_Yoko_Shimomura-Violin.pdf" "%STAGE_DIR%\Input\" >nul

copy /y "%BASE_DIR%scripts\jianpu-ly.py"            "%STAGE_DIR%\" >nul

copy /y "%BASE_DIR%README_EN.txt"           "%STAGE_DIR%\README.txt" >nul

copy /y "%BASE_DIR%璇绘垜.txt"               "%STAGE_DIR%\" >nul

copy /y "%BASE_DIR%THIRD_PARTY_NOTICES.md"  "%STAGE_DIR%\" >nul

copy /y "%BASE_DIR%LICENSE"                 "%STAGE_DIR%\" >nul



:: 鈹€鈹€ 姝ラ 4锛氬帇缂╀负 zip 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

if not exist "%BASE_DIR%installer-dist" mkdir "%BASE_DIR%installer-dist"

if exist "%OUTPUT_ZIP%" del /q "%OUTPUT_ZIP%"



if not exist "%STAGE_DIR%\%APP_NAME%.exe" (

  echo [ERROR] 渚挎惡鐩綍鏈纭粍瑁咃細%STAGE_DIR%\%APP_NAME%.exe 鏈壘鍒般€?
  exit /b 1

)



set "PYTHON_CMD=%BASE_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON_CMD%" (

  set "PYTHON_CMD=py -3"

  where py >nul 2>nul || set "PYTHON_CMD=python"

)



set "ZIP_PY=%TEMP%\convert_zip_stage_%RANDOM%.py"

powershell -NoProfile -Command "Set-Content -Path '%ZIP_PY%' -Value \"import pathlib`nimport zipfile`nimport sys`nsrc=pathlib.Path(r'%STAGE_DIR%')`ndst=pathlib.Path(r'%OUTPUT_ZIP%')`nif not src.exists(): sys.exit('missing stage dir: '+str(src))`nif dst.exists(): dst.unlink()`nz=zipfile.ZipFile(dst, 'w', compression=zipfile.ZIP_DEFLATED)`nfor p in sorted(src.rglob('*')):`n    z.write(p, p.relative_to(src.parent))`nz.close()`n\" -Encoding UTF8"

"%PYTHON_CMD%" "%ZIP_PY%"

set "PYTHON_EXIT=%ERRORLEVEL%"

del /q "%ZIP_PY%" >nul 2>nul

if "%PYTHON_EXIT%" neq "0" ( echo [ERROR] 鍘嬬缉澶辫触銆? exit /b 1 )



rmdir /s /q "%BASE_DIR%zip-stage"



echo.

echo [OK] 渚挎惡鍘嬬缉鍖呭凡鐢熸垚锛?OUTPUT_ZIP%

exit /b 0
