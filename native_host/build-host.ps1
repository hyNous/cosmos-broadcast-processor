param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$distDir = Join-Path $projectRoot "dist\native-host"
$workDir = Join-Path $projectRoot "build\native-host"
$specDir = Join-Path $projectRoot "build"

Push-Location $projectRoot
try {
    & $Python -m PyInstaller --version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "未安装 PyInstaller。请先运行：python -m pip install -r native_host\requirements.txt"
    }

    $commonArgs = @(
        "--noconfirm",
        "--clean",
        "--onefile",
        "--distpath", $distDir,
        "--workpath", $workDir,
        "--specpath", $specDir,
        "--paths", (Join-Path $projectRoot "native_host")
    )

    Write-Host "构建 Native Messaging host..."
    & $Python -m PyInstaller @commonArgs `
        --name "cosmos-native-host" `
        --console `
        (Join-Path $projectRoot "native_host\host.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Native Host 构建失败。"
    }

    Write-Host "构建本地任务中心..."
    & $Python -m PyInstaller @commonArgs `
        --name "cosmos-task-center" `
        --console `
        (Join-Path $projectRoot "native_host\task_center.py")
    if ($LASTEXITCODE -ne 0) {
        throw "任务中心构建失败。"
    }

    $hostExe = Join-Path $distDir "cosmos-native-host.exe"
    $centerExe = Join-Path $distDir "cosmos-task-center.exe"
    if (-not (Test-Path -LiteralPath $hostExe)) {
        throw "未生成 cosmos-native-host.exe"
    }
    if (-not (Test-Path -LiteralPath $centerExe)) {
        throw "未生成 cosmos-task-center.exe"
    }
    Write-Host "构建完成："
    Write-Host "  $hostExe"
    Write-Host "  $centerExe"
}
finally {
    Pop-Location
}
