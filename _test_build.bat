@echo off
set "PYTHON_CMD=py -3"
set "BASE_DIR=E:\Project_Convert\"
if exist "%BASE_DIR%package-assets\oemer-runtime\checkpoints\unet_big\model.onnx" (
    echo TRUE branch
    %PYTHON_CMD% -c "import os; dst=os.getcwd(); print(dst)"
) else (
    echo ELSE branch - calling download_oemer_models.py
)
echo DONE
