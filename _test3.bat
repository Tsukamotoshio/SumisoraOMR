@echo on
set "BASE_DIR=E:\Project_Convert\"
set "PACKAGE_ASSETS=E:\Project_Convert\package-assets"
set "PYTHON_CMD=py -3"
echo ABOUT-TO-SET
set "PYTHON_CMD=py -3"
echo SET-DONE
where py >nul 2>nul
if errorlevel 1 set "PYTHON_CMD=python"
echo PYTHON_CMD-SET
if exist "%BASE_DIR%package-assets\oemer-runtime\checkpoints\unet_big\model.onnx" (
    echo TRUE branch
) else (
    echo ELSE branch
    call %PYTHON_CMD% E:\Project_Convert\download_oemer_models.py
    if errorlevel 1 (
        echo download failed
        exit /b 1
    )
)
echo AFTER-ONNX-BLOCK
