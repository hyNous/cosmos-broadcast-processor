param(
    [ValidateSet("Both", "Chrome", "Edge")]
    [string]$Browser = "Both",
    [string]$HostExe = "",
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"
$hostName = "com.hynous.cosmos_broadcast_processor"
$extensionId = "hjccjnbenicffglhjkjgoecbfdjfmafh"
$projectRoot = Split-Path -Parent $PSScriptRoot
$extensionSource = Join-Path $projectRoot "extension"
$extensionFiles = @(
    "job-state.js",
    "manifest.json",
    "service-worker.js",
    "sidepanel.css",
    "sidepanel.html",
    "sidepanel.js"
)

# Restricted cleanup of one-shot job state files only (never MP3 / user output dirs).
# Non-recursive: only exact runtime job_id-derived names under jobs\ (lowercase 32 hex).
# Patterns: <32hex>.json | <32hex>.cancel | <32hex>.json.tmp
function Clear-CosmosJobStateFiles {
    param(
        [Parameter(Mandatory = $true)]
        [string]$JobsDir
    )
    if (-not (Test-Path -LiteralPath $JobsDir)) {
        return
    }
    # Non-recursive: only the jobs folder itself. Never delete keep.json/notes.tmp/etc.
    Get-ChildItem -LiteralPath $JobsDir -File -ErrorAction SilentlyContinue | Where-Object {
        $name = $_.Name
        ($name -cmatch '^[0-9a-f]{32}\.json$') -or
        ($name -cmatch '^[0-9a-f]{32}\.cancel$') -or
        ($name -cmatch '^[0-9a-f]{32}\.json\.tmp$')
    } | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
    }
}

# Default Host path when -HostExe is omitted:
# 1) Same directory as this script (flat release ZIP layout)
# 2) Repo layout: <repo>/dist/native-host/cosmos-native-host.exe
if (-not $HostExe) {
    $besideScript = Join-Path $PSScriptRoot "cosmos-native-host.exe"
    $repoLayout = Join-Path $projectRoot "dist\native-host\cosmos-native-host.exe"
    if (Test-Path -LiteralPath $besideScript) {
        $HostExe = $besideScript
    } else {
        $HostExe = $repoLayout
    }
}
$resolvedExe = (Resolve-Path -LiteralPath $HostExe -ErrorAction Stop).Path
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
$taskCenterSource = Join-Path (Split-Path -Parent $resolvedExe) "cosmos-task-center.exe"
$installedTaskCenter = Join-Path $installDir "cosmos-task-center.exe"
$manifestPath = Join-Path $installDir "$hostName.json"
$jobsDir = Join-Path $installDir "jobs"
$installedExtensionDir = Join-Path $installDir "extension"

# Require task-center beside the host before any install copy or registry write.
# Missing EXE used to be silently skipped, leaving frozen host unable to launch it.
if (-not (Test-Path -LiteralPath $taskCenterSource)) {
    throw "未找到任务中心可执行文件：$taskCenterSource。请先运行 build-host.ps1 生成 cosmos-task-center.exe，再安装。不会写入注册表。"
}
foreach ($name in $extensionFiles) {
    $source = Join-Path $extensionSource $name
    if (-not (Test-Path -LiteralPath $source)) {
        throw "浏览器扩展文件不完整：$source。请完整解压发布包后重试。不会写入注册表。"
    }
}

# Before copying/registering: clear legacy one-shot job state under jobs\ only.
# Never deletes jobs-dir exterior files, user output directories, or MP3s.
# Upgrade tip: wait for any running download to finish before installing.
Clear-CosmosJobStateFiles -JobsDir $jobsDir

New-Item -ItemType Directory -Path $installDir -Force | Out-Null
New-Item -ItemType Directory -Path $installedExtensionDir -Force | Out-Null
$resolvedDestination = [System.IO.Path]::GetFullPath($installedExe)
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($resolvedExe, $resolvedDestination)) {
    Copy-Item -LiteralPath $resolvedExe -Destination $installedExe -Force
}

# Keep task center beside the host so frozen host can locate it. Never starts it.
Copy-Item -LiteralPath $taskCenterSource -Destination $installedTaskCenter -Force
foreach ($name in $extensionFiles) {
    Copy-Item -LiteralPath (Join-Path $extensionSource $name) -Destination (Join-Path $installedExtensionDir $name) -Force
}

$manifest = [ordered]@{
    name = $hostName
    description = "Native FFmpeg helper for Cosmos Broadcast Processor"
    path = $installedExe
    type = "stdio"
    allowed_origins = @("chrome-extension://$extensionId/")
}
[System.IO.File]::WriteAllText(
    $manifestPath,
    ($manifest | ConvertTo-Json -Depth 4),
    [System.Text.UTF8Encoding]::new($false)
)

$registryTargets = @()
if ($Browser -in @("Both", "Chrome")) {
    $registryTargets += "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$hostName"
}
if ($Browser -in @("Both", "Edge")) {
    $registryTargets += "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\$hostName"
}
foreach ($target in $registryTargets) {
    New-Item -Path $target -Force | Out-Null
    Set-Item -Path $target -Value $manifestPath
}
[Environment]::SetEnvironmentVariable("COSMOS_APP_DATA_DIR", $installDir, "User")
$env:COSMOS_APP_DATA_DIR = $installDir

Write-Host "本地辅助程序安装完成。"
Write-Host "扩展固定 ID：$extensionId"
Write-Host "安装前已清理 jobs 目录下旧任务状态文件（.json/.cancel/.tmp），未删除 MP3 或输出目录。"
Write-Host "升级前请等待进行中的下载任务结束。"
Write-Host "仅注册 Native Messaging host，不会启动任务中心，不会递归删除应用目录。"
Write-Host "任务状态目录：$installDir\jobs"
Write-Host "浏览器扩展已复制到稳定目录：$installedExtensionDir"
Write-Host "接下来在 chrome://extensions 或 edge://extensions 中加载该目录；之后可删除下载的 ZIP 和解压目录。"
