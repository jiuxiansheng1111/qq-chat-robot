[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceRoot = Join-Path $projectRoot "integrations\netease"
$runtimeRoot = Join-Path $projectRoot "data\netease\runtime"
$manifestNames = @("package.json", "package-lock.json")
$sourcePaths = @{}
$runtimePaths = @{}

foreach ($name in $manifestNames) {
    $sourcePath = Join-Path $sourceRoot $name
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "网易云会员桥接包清单缺失：$sourcePath"
    }
    $sourcePaths[$name] = $sourcePath
}

$nodeCommand = Get-Command -Name "node.exe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$npmCommand = Get-Command -Name "npm.cmd" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $nodeCommand -or -not $npmCommand) {
    throw "未找到 node.exe 或 npm.cmd。请安装 Node.js 22 或更高版本后重试。"
}

$nodeVersionOutput = & $nodeCommand.Source --version 2>$null
$nodeExitCode = $LASTEXITCODE
$nodeVersionText = ([string]$nodeVersionOutput).Trim()
$versionMatch = [regex]::Match($nodeVersionText, '^v?(\d+)\.(\d+)\.(\d+)')
if ($nodeExitCode -ne 0 -or -not $versionMatch.Success) {
    throw "无法读取 Node.js 版本。请安装 Node.js 22 或更高版本后重试。"
}
$nodeMajor = [int]$versionMatch.Groups[1].Value
if ($nodeMajor -lt 22) {
    throw "当前 Node.js 版本低于 22。请安装 Node.js 22 或更高版本后重试。"
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
foreach ($name in $manifestNames) {
    $runtimePaths[$name] = Join-Path $runtimeRoot $name
    Copy-Item -LiteralPath $sourcePaths[$name] -Destination $runtimePaths[$name] -Force
}

$sourceFingerprint = @(
    (Get-FileHash -LiteralPath $sourcePaths["package.json"] -Algorithm SHA256).Hash.ToLowerInvariant()
    (Get-FileHash -LiteralPath $sourcePaths["package-lock.json"] -Algorithm SHA256).Hash.ToLowerInvariant()
) -join ":"
$runtimeFingerprint = @(
    (Get-FileHash -LiteralPath $runtimePaths["package.json"] -Algorithm SHA256).Hash.ToLowerInvariant()
    (Get-FileHash -LiteralPath $runtimePaths["package-lock.json"] -Algorithm SHA256).Hash.ToLowerInvariant()
) -join ":"
$markerPath = Join-Path $runtimeRoot ".install-fingerprint"
$apiPackagePath = Join-Path $runtimeRoot "node_modules\@neteasecloudmusicapienhanced\api"
$installedFingerprint = ""
if (Test-Path -LiteralPath $markerPath -PathType Leaf) {
    $installedFingerprint = (Get-Content -LiteralPath $markerPath -Raw).Trim()
}
if (
    $runtimeFingerprint -eq $sourceFingerprint -and
    $installedFingerprint -eq $sourceFingerprint -and
    (Test-Path -LiteralPath $apiPackagePath -PathType Container)
) {
    Write-Host "[NETEASE] Node dependencies are already installed."
    return
}

Remove-Item -LiteralPath $markerPath -Force -ErrorAction SilentlyContinue
Push-Location -LiteralPath $runtimeRoot
$previousErrorPreference = $ErrorActionPreference
try {
    # Windows PowerShell 5 treats npm's ordinary stderr warnings as errors.
    # Decide success from the native exit code rather than that output stream.
    $ErrorActionPreference = "Continue"
    $null = & $npmCommand.Source ci --omit=dev --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org 2>&1
    $npmExitCode = $LASTEXITCODE
}
catch {
    throw "网易云会员桥接依赖安装失败。请检查网络和 npm 配置后重试。"
}
finally {
    $ErrorActionPreference = $previousErrorPreference
    Pop-Location
}
if ($npmExitCode -ne 0 -or -not (Test-Path -LiteralPath $apiPackagePath -PathType Container)) {
    throw "网易云会员桥接依赖安装失败。请检查网络和 npm 配置后重试。"
}

Set-Content -LiteralPath $markerPath -Value $sourceFingerprint -NoNewline -Encoding ASCII
Write-Host "[NETEASE] Node dependencies are ready."
