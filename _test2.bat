@echo off
set "BASE_DIR=E:\Project_Convert\"
set "PYTHON_CMD=py -3"
if exist "%BASE_DIR%package-assets\oemer-runtime\checkpoints\unet_big\model.onnx" (
    echo TRUE: using local
    %PYTHON_CMD% -c "import oemer,shutil,os; src=os.path.join(r'%BASE_DIR%package-assets\oemer-runtime'); dst=oemer.MODULE_PATH; [shutil.copytree(os.path.join(src,d),os.path.join(dst,d),dirs_exist_ok=True) for d in ('checkpoints','sklearn_models') if os.path.isdir(os.path.join(src,d))]; print('done')"
) else (
    echo ELSE: would download
)
echo AFTER
