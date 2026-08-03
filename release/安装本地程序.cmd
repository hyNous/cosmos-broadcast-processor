@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "SCRIPT=%~dp0native_host\install-host.ps1"
if not exist "%SCRIPT%" (
    echo [错误] 未找到安装脚本：
    echo   %SCRIPT%
    echo 请确认已完整解压安装包，且本 CMD 与 native_host 文件夹在同一目录。
    echo.
    pause
    exit /b 1
)

echo 正在安装本地辅助程序（无需管理员权限）...
echo 脚本：%SCRIPT%
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set "EC=%ERRORLEVEL%"

echo.
if "%EC%"=="0" (
    echo [成功] 本地辅助程序和扩展文件已安装。
    echo 请在浏览器扩展管理页加载：
    echo   %LOCALAPPDATA%\CosmosBroadcastProcessor\extension
    echo 然后完全退出并重新打开 Chrome/Edge。
) else (
    echo [失败] 安装未成功，退出码：%EC%
    echo 请向上滚动查看详细错误。常见原因：缺少 EXE、PowerShell 被拦截、路径不完整。
)

echo.
pause
exit /b %EC%
