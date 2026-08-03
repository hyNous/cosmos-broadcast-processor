param(
    [ValidateSet("Both", "Chrome", "Edge")]
    [string]$Browser = "Both",
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"
$hostName = "com.hynous.cosmos_broadcast_processor"
if (-not $InstallDir) {
    $configuredInstallDir = [Environment]::GetEnvironmentVariable("COSMOS_APP_DATA_DIR", "User")
    if (-not $configuredInstallDir) {
        $configuredInstallDir = $env:COSMOS_APP_DATA_DIR
    }
    if ($configuredInstallDir) {
        $InstallDir = $configuredInstallDir
    } else {
        $InstallDir = Join-Path $env:LOCALAPPDATA "CosmosBroadcastProcessor"
    }
}
$installDir = [System.IO.Path]::GetFullPath($InstallDir)
$installedExe = Join-Path $installDir "cosmos-native-host.exe"
$installedTaskCenter = Join-Path $installDir "cosmos-task-center.exe"
$manifestPath = Join-Path $installDir "$hostName.json"
$jobsDir = Join-Path $installDir "jobs"
$installedExtensionDir = Join-Path $installDir "extension"
$extensionFiles = @(
    "job-state.js",
    "manifest.json",
    "service-worker.js",
    "sidepanel.css",
    "sidepanel.html",
    "sidepanel.js"
)

# Restricted: only exact 32-hex job state files under jobs\ (never MP3, downloads, or app root).
# Patterns: <32hex>.json | <32hex>.cancel | <32hex>.json.tmp (lowercase, matches runtime job_id).
function Clear-CosmosJobStateFiles {
    param(
        [Parameter(Mandatory = $true)]
        [string]$JobsDir
    )
    if (-not (Test-Path -LiteralPath $JobsDir)) {
        return
    }
    Get-ChildItem -LiteralPath $JobsDir -File -ErrorAction SilentlyContinue | Where-Object {
        $name = $_.Name
        ($name -cmatch '^[0-9a-f]{32}\.json$') -or
        ($name -cmatch '^[0-9a-f]{32}\.cancel$') -or
        ($name -cmatch '^[0-9a-f]{32}\.json\.tmp$')
    } | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
    }
}

$registryTargets = @()
if ($Browser -in @("Both", "Chrome")) {
    $registryTargets += "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$hostName"
}
if ($Browser -in @("Both", "Edge")) {
    $registryTargets += "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\$hostName"
}
foreach ($target in $registryTargets) {
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Force
    }
}
if (Test-Path -LiteralPath $installedExe) {
    Remove-Item -LiteralPath $installedExe -Force
}
if (Test-Path -LiteralPath $installedTaskCenter) {
    Remove-Item -LiteralPath $installedTaskCenter -Force
}
if (Test-Path -LiteralPath $manifestPath) {
    Remove-Item -LiteralPath $manifestPath -Force
}
foreach ($name in $extensionFiles) {
    $path = Join-Path $installedExtensionDir $name
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
    }
}
if ((Test-Path -LiteralPath $installedExtensionDir) -and
    -not (Get-ChildItem -LiteralPath $installedExtensionDir -Force | Select-Object -First 1)) {
    Remove-Item -LiteralPath $installedExtensionDir -Force
}

# Clear one-shot job state only; do not remove the install directory or user MP3s.
Clear-CosmosJobStateFiles -JobsDir $jobsDir

$configuredInstallDir = [Environment]::GetEnvironmentVariable("COSMOS_APP_DATA_DIR", "User")
if ($configuredInstallDir -and
    [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath($configuredInstallDir),
        $installDir
    )) {
    [Environment]::SetEnvironmentVariable("COSMOS_APP_DATA_DIR", $null, "User")
}

Write-Host "本地辅助程序已注销。"
Write-Host "已清理 jobs 目录下 32 位 hex 任务状态文件（.json/.cancel/.json.tmp）。"
Write-Host "已删除安装程序复制的浏览器扩展文件；请同时在浏览器扩展管理页移除本扩展。"
Write-Host "未删除 MP3、下载目录或整个应用根目录。"
