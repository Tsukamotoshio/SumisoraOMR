@echo off
setlocal

set "BASE_DIR=%~dp0"
set "MSI_FILE=%BASE_DIR%Audiveris-5.10.2-windows-x86_64.msi"
set "TARGET_DIR=%BASE_DIR%Audiveris"
set "LOG_FILE=%BASE_DIR%audiveris-install.log"

if not exist "%MSI_FILE%" (
    echo [ERROR] 未找到安装包: %MSI_FILE%
    exit /b 1
)

echo 正在解包 Audiveris 到: %TARGET_DIR%
msiexec /a "%MSI_FILE%" /qn TARGETDIR="%BASE_DIR%" /L*v "%LOG_FILE%"

if errorlevel 1 (
    echo [ERROR] 解包失败，请查看日志: %LOG_FILE%
    exit /b 1
)

if exist "%TARGET_DIR%\Audiveris.exe" (
    echo [OK] Audiveris 已就绪: %TARGET_DIR%\Audiveris.exe
    exit /b 0
)

echo [WARN] 解包结束，但未找到 Audiveris.exe，请查看日志: %LOG_FILE%
exit /b 1
