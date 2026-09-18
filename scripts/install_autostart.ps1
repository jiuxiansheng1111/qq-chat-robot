[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot "run_bot.ps1"
$taskName = "QQChatRobot API"

if (-not (Test-Path -LiteralPath $runner)) {
    throw "Bot runner not found: $runner"
}

$arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $runner
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$taskSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $taskSettings `
    -Description "Start and supervise the qq-chatrobot FastAPI service at user logon." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
Write-Host "Installed and started scheduled task: $taskName" -ForegroundColor Green
Write-Host "Health URL: http://127.0.0.1:8000/health/ready"
