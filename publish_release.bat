@echo off
setlocal
chcp 65001 >nul

set "BASE_DIR=%~dp0"
cd /d "%BASE_DIR%"

set "APP_VERSION=0.1.3"
set "APP_NAME=ConvertTool"
set "INSTALLER=%BASE_DIR%installer-dist\%APP_NAME%-Setup-%APP_VERSION%.exe"
set "ZIP_FILE=%BASE_DIR%installer-dist\%APP_NAME%-Portable-%APP_VERSION%.zip"

:: ── 检查 gh CLI ────────────────────────────────────────────────────────────────
where gh >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未找到 GitHub CLI (gh)。
    echo 请先安装：winget install --id GitHub.cli -e
    echo 安装后运行：gh auth login
    exit /b 1
)

:: ── 检查待上传文件 ─────────────────────────────────────────────────────────────
if not exist "%INSTALLER%" (
    echo [ERROR] 未找到安装包：%INSTALLER%
    echo 请先运行 build_installer.bat
    exit /b 1
)
if not exist "%ZIP_FILE%" (
    echo [ERROR] 未找到压缩包：%ZIP_FILE%
    echo 请先运行 build_zip.bat
    exit /b 1
)

:: ── 创建/更新 Release 并上传 ──────────────────────────────────────────────────
set "TAG=v%APP_VERSION%"
set "TITLE=%APP_NAME% %APP_VERSION%"

echo [INFO] 正在创建 GitHub Release %TAG% ...
gh release create "%TAG%" ^
    --title "%TITLE%" ^
    --notes "### 简谱转换工具 v%APP_VERSION%^^  ^^**安装包（推荐）**：下载 %APP_NAME%-Setup-%APP_VERSION%.exe 运行安装向导即可。^^**便携版**：解压 %APP_NAME%-Portable-%APP_VERSION%.zip 后直接运行 ConvertTool.exe。" ^
    --draft
if errorlevel 1 (
    echo [WARN] Release 可能已存在，尝试仅上传文件...
)

echo [INFO] 正在上传 %APP_NAME%-Setup-%APP_VERSION%.exe ...
gh release upload "%TAG%" "%INSTALLER%" --clobber
if errorlevel 1 ( echo [ERROR] 安装包上传失败。& exit /b 1 )

echo [INFO] 正在上传 %APP_NAME%-Portable-%APP_VERSION%.zip ...
gh release upload "%TAG%" "%ZIP_FILE%" --clobber
if errorlevel 1 ( echo [ERROR] 压缩包上传失败。& exit /b 1 )

echo.
echo [OK] 已上传至 GitHub Release %TAG%（草稿状态）。
echo [INFO] 请前往 GitHub 检查发布说明后手动发布：
echo        gh release edit "%TAG%" --draft=false
echo        或在 GitHub 网页上点击 Publish release

exit /b 0
