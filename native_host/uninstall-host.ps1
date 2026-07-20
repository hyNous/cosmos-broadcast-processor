param(
    [ValidateSet("Both", "Chrome", "Edge")]
    [string]$Browser = "Both"
)

$ErrorActionPreference = "Stop"
$hostName = "com.hynous.cosmos_broadcast_processor"
$installDir = Join-Path $env:LOCALAPPDATA "CosmosBroadcastProcessor"
$installedExe = Join-Path $installDir "cosmos-native-host.exe"
$installedTaskCenter = Join-Path $installDir "cosmos-task-center.exe"
$manifestPath = Join-Path $installDir "$hostName.json"
$jobsDir = Join-Path $installDir "jobs"

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

# Clear one-shot job state only; do not remove the install directory or user MP3s.
Clear-CosmosJobStateFiles -JobsDir $jobsDir

Write-Host "本地辅助程序已注销。"
Write-Host "已清理 jobs 目录下 32 位 hex 任务状态文件（.json/.cancel/.json.tmp）。"
Write-Host "未删除 MP3、下载目录或整个应用根目录。"
