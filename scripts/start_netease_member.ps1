[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 3010,
    [switch]$OpenLogin
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$bridgePath = Join-Path $projectRoot "integrations\netease\bridge.cjs"
$logRoot = Join-Path $projectRoot "logs"
$stdoutLog = Join-Path $logRoot "netease-member.out.log"
$stderrLog = Join-Path $logRoot "netease-member.error.log"
$installerPath = Join-Path $PSScriptRoot "install_netease_member.ps1"
$baseUrl = "http://127.0.0.1:$Port"

function Get-BridgeHealth {
    try {
        $result = Invoke-RestMethod -Uri "$baseUrl/health" -Method Get -TimeoutSec 1 -ErrorAction Stop
        return ([string]$result.provider -ceq "netease_member_bridge") -and ([string]$result.status -ceq "ok")
    }
    catch {
        return $false
    }
}

function Test-LocalPortInUse {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $asyncResult = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $asyncResult.AsyncWaitHandle.WaitOne(500)) {
            return $false
        }
        $client.EndConnect($asyncResult)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function ConvertTo-WindowsArgument {
    param([Parameter(Mandatory = $true)][string]$Value)

    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes++
            continue
        }
        if ($character -eq '"') {
            if ($backslashes -gt 0) {
                [void]$builder.Append(('\' * (2 * $backslashes)))
            }
            [void]$builder.Append('\')
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * (2 * $backslashes)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "网易云会员安装脚本缺失：$installerPath"
}
if (-not (Test-Path -LiteralPath $bridgePath -PathType Leaf)) {
    throw "网易云会员桥接脚本缺失：$bridgePath"
}

if (Get-BridgeHealth) {
    Write-Host "[NETEASE] Bridge is already ready at $baseUrl"
    if ($OpenLogin) {
        Write-Host "[NETEASE] Login page: $baseUrl/login"
    }
    return
}

& $installerPath
if (Get-BridgeHealth) {
    Write-Host "[NETEASE] Bridge is already ready at $baseUrl"
    if ($OpenLogin) {
        Write-Host "[NETEASE] Login page: $baseUrl/login"
    }
    return
}
if (Test-LocalPortInUse) {
    throw "端口 $Port 已被其他服务占用；拒绝启动网易云会员桥接服务。"
}

$nodeCommand = Get-Command -Name "node.exe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $nodeCommand) {
    throw "未找到 node.exe。请安装 Node.js 22 或更高版本后重试。"
}

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$argumentList = @(
    (ConvertTo-WindowsArgument $bridgePath)
    "--port"
    (ConvertTo-WindowsArgument ([string]$Port))
) -join " "

$startProcessParameters = @{
    FilePath = $nodeCommand.Source
    ArgumentList = $argumentList
    WorkingDirectory = $projectRoot
    RedirectStandardOutput = $stdoutLog
    RedirectStandardError = $stderrLog
    WindowStyle = "Hidden"
    PassThru = $true
}
try {
    $bridgeProcess = Start-Process @startProcessParameters
}
catch {
    throw "无法启动网易云会员桥接服务。请检查 Node.js 与日志目录权限。"
}

$deadline = (Get-Date).AddSeconds(15)
$ready = $false
while ((Get-Date).AddSeconds(1) -lt $deadline) {
    if (Get-BridgeHealth) {
        $ready = $true
        break
    }
    Start-Sleep -Milliseconds 250
}
if (-not $ready) {
    $portInUse = Test-LocalPortInUse
    if ($bridgeProcess -and -not $bridgeProcess.HasExited) {
        try {
            Stop-Process -Id $bridgeProcess.Id -Force -ErrorAction SilentlyContinue
        }
        catch {
        }
    }
    if ($portInUse) {
        throw "端口 $Port 已被其他服务占用或桥接服务未通过健康检查；请检查日志：$stderrLog"
    }
    throw "网易云会员桥接服务未能在 15 秒内就绪；请检查日志：$stderrLog"
}

Write-Host "[NETEASE] Bridge ready at $baseUrl"
if ($OpenLogin) {
    Write-Host "[NETEASE] Login page: $baseUrl/login"
}
