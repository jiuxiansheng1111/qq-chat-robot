[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$taskName = "QQChatRobot API"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'bot_runtime_mode.ps1')
if ((Get-QQChatRobotRuntime -ProjectRoot $projectRoot) -eq 'astrbot') {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'run_astrbot.ps1')
    exit $LASTEXITCODE
}

$logRoot = Join-Path $projectRoot "logs"
$watchdogLog = Join-Path $logRoot "watchdog.log"

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

try {
    $health = Invoke-WebRequest `
        -Uri "http://127.0.0.1:8000/health/live" `
        -UseBasicParsing `
        -TimeoutSec 5 `
        -ErrorAction Stop
    if ($health.StatusCode -eq 200) {
        exit 0
    }
}
catch {
    # 服务当前不可用；下方的常驻任务负责恢复进程。
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Add-Content -LiteralPath $watchdogLog -Value "$(Get-Date -Format o) Recovery task cannot find scheduled task '$taskName'."
    exit 1
}

if ($task.State -ne "Running") {
    Add-Content -LiteralPath $watchdogLog -Value "$(Get-Date -Format o) API health check failed; starting scheduled task '$taskName'."
    Start-ScheduledTask -TaskName $taskName
}
