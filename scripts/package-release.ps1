#Requires -Version 5.1
# Build unified Windows end-user release ZIP for v1.0.1.
# Only overwrites dist\cosmos-broadcast-processor-windows-v1.0.1.zip and dist\SHA256SUMS.txt.
# Uses a private temp staging dir; never deletes repo root or entire dist/.

$ErrorActionPreference = "Stop"

$Version = "1.0.1"
$PackageName = "cosmos-broadcast-processor-windows-v$Version"
$ZipFileName = "$PackageName.zip"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "extension\manifest.json"))) {
    throw "Cannot locate repo root (missing extension\manifest.json): $RepoRoot"
}

$DistDir = Join-Path $RepoRoot "dist"
$NativeHostDir = Join-Path $DistDir "native-host"
$HostExe = Join-Path $NativeHostDir "cosmos-native-host.exe"
$TaskCenterExe = Join-Path $NativeHostDir "cosmos-task-center.exe"
$OutZip = Join-Path $DistDir $ZipFileName
$OutSums = Join-Path $DistDir "SHA256SUMS.txt"

$ReleaseSrc = Join-Path $RepoRoot "release"
$ExtensionSrc = Join-Path $RepoRoot "extension"
$InstallPs1 = Join-Path $RepoRoot "native_host\install-host.ps1"
$UninstallPs1 = Join-Path $RepoRoot "native_host\uninstall-host.ps1"

function Assert-File {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Hint
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing required file: $Path. $Hint"
    }
}

Assert-File -Path $HostExe -Hint "Run native_host\build-host.ps1 first (cosmos-native-host.exe)."
Assert-File -Path $TaskCenterExe -Hint "Run native_host\build-host.ps1 first (cosmos-task-center.exe)."
Assert-File -Path $InstallPs1 -Hint "install-host.ps1 missing in repo."
Assert-File -Path $UninstallPs1 -Hint "uninstall-host.ps1 missing in repo."

$quickStartName = [string]([char]0x5FEB) + [string]([char]0x901F) + [string]([char]0x5F00) + [string]([char]0x59CB) + ".txt"
$installCmdName = [string]([char]0x5B89) + [string]([char]0x88C5) + [string]([char]0x672C) + [string]([char]0x5730) + [string]([char]0x7A0B) + [string]([char]0x5E8F) + ".cmd"
$uninstallCmdName = [string]([char]0x5378) + [string]([char]0x8F7D) + [string]([char]0x672C) + [string]([char]0x5730) + [string]([char]0x7A0B) + [string]([char]0x5E8F) + ".cmd"

# Resolve Chinese filenames via codepoints so this script stays ASCII-safe under PS 5.1.
$quickStartPath = Join-Path $ReleaseSrc $quickStartName
$installCmdPath = Join-Path $ReleaseSrc $installCmdName
$uninstallCmdPath = Join-Path $ReleaseSrc $uninstallCmdName

Assert-File -Path $quickStartPath -Hint "Missing release quick-start txt."
Assert-File -Path $installCmdPath -Hint "Missing release install cmd."
Assert-File -Path $uninstallCmdPath -Hint "Missing release uninstall cmd."

$ExtensionFiles = @(
    "job-state.js",
    "manifest.json",
    "service-worker.js",
    "sidepanel.css",
    "sidepanel.html",
    "sidepanel.js"
)
foreach ($name in $ExtensionFiles) {
    Assert-File -Path (Join-Path $ExtensionSrc $name) -Hint "Extension file incomplete."
}

if (-not (Test-Path -LiteralPath $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
}

$stamp = Get-Date -Format "yyyyMMddHHmmss"
$guidPart = [guid]::NewGuid().ToString("N").Substring(0, 8)
$stageRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("cosmos-release-stage-" + $stamp + "-" + $guidPart)
$stagePkg = Join-Path $stageRoot $PackageName

try {
    New-Item -ItemType Directory -Path $stagePkg -Force | Out-Null
    $stageExt = Join-Path $stagePkg "extension"
    $stageNh = Join-Path $stagePkg "native_host"
    New-Item -ItemType Directory -Path $stageExt -Force | Out-Null
    New-Item -ItemType Directory -Path $stageNh -Force | Out-Null

    Copy-Item -LiteralPath $quickStartPath -Destination (Join-Path $stagePkg $quickStartName) -Force
    Copy-Item -LiteralPath $installCmdPath -Destination (Join-Path $stagePkg $installCmdName) -Force
    Copy-Item -LiteralPath $uninstallCmdPath -Destination (Join-Path $stagePkg $uninstallCmdName) -Force

    foreach ($name in $ExtensionFiles) {
        Copy-Item -LiteralPath (Join-Path $ExtensionSrc $name) -Destination (Join-Path $stageExt $name) -Force
    }

    Copy-Item -LiteralPath $HostExe -Destination (Join-Path $stageNh "cosmos-native-host.exe") -Force
    Copy-Item -LiteralPath $TaskCenterExe -Destination (Join-Path $stageNh "cosmos-task-center.exe") -Force
    Copy-Item -LiteralPath $InstallPs1 -Destination (Join-Path $stageNh "install-host.ps1") -Force
    Copy-Item -LiteralPath $UninstallPs1 -Destination (Join-Path $stageNh "uninstall-host.ps1") -Force

    $forbidden = Get-ChildItem -LiteralPath $stagePkg -Recurse -Force -File | Where-Object {
        $n = $_.Name.ToLowerInvariant()
        $rel = $_.FullName.Substring($stagePkg.Length).ToLowerInvariant()
        ($n.EndsWith(".log")) -or
        ($n.EndsWith(".jsonl")) -or
        ($n.EndsWith(".pyc")) -or
        ($n -eq "pyproject.toml") -or
        ($rel.Contains("\.handoff\")) -or
        ($rel.Contains("\.git\")) -or
        ($rel.Contains("\.grok\")) -or
        ($rel.Contains("\tests\")) -or
        ($n -eq "host.py") -or
        ($n -eq "processor.py") -or
        ($n -eq "task_center.py") -or
        ($n -eq "requirements.txt")
    }
    if ($forbidden) {
        $list = ($forbidden | ForEach-Object { $_.FullName }) -join "; "
        throw "Staging contains forbidden files: $list"
    }

    if (Test-Path -LiteralPath $OutZip) {
        Remove-Item -LiteralPath $OutZip -Force
    }

    Compress-Archive -Path $stagePkg -DestinationPath $OutZip -CompressionLevel Optimal -Force

    if (-not (Test-Path -LiteralPath $OutZip)) {
        throw "ZIP was not created: $OutZip"
    }

    $hash = (Get-FileHash -LiteralPath $OutZip -Algorithm SHA256).Hash.ToLowerInvariant()
    $size = (Get-Item -LiteralPath $OutZip).Length
    $sumsBody = "$hash  $ZipFileName`r`n"
    [System.IO.File]::WriteAllText($OutSums, $sumsBody, (New-Object System.Text.UTF8Encoding $false))

    Write-Host "OK: $OutZip"
    Write-Host "Size: $size bytes"
    Write-Host "SHA-256: $hash"
    Write-Host "Sums: $OutSums"
}
finally {
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
