@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "SCRIPT=%~dp0native_host\uninstall-host.ps1"
if not exist "%SCRIPT%" (
    echo [错误] 未找到卸载脚本：
    echo   %SCRIPT%
    echo 请确认已完整解压安装包，且本 CMD 与 native_host 文件夹在同一目录。
    echo.
    pause
    exit /b 1
)

echo 正在卸载本地辅助程序...
echo 脚本：%SCRIPT%
echo 说明：不会删除你已下载的 MP3 或输出目录。
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set "EC=%ERRORLEVEL%"

echo.
if "%EC%"=="0" (
    echo [成功] 本地辅助程序和安装的扩展文件已卸载。
    echo 如果浏览器扩展管理页仍有本扩展，请将它移除。
) else (
    echo [失败] 卸载未成功，退出码：%EC%
    echo 请向上滚动查看详细错误。
)

echo.
pause
exit /b %EC%
