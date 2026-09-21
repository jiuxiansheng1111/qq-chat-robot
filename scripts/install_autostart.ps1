[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot "run_bot.ps1"
$hiddenRunner = Join-Path $PSScriptRoot "run_bot_hidden.vbs"
$taskName = "QQChatRobot API"
$healthRunner = Join-Path $PSScriptRoot "check_bot_task.ps1"
$hiddenHealthRunner = Join-Path $PSScriptRoot "check_bot_task_hidden.vbs"
$healthTaskName = "QQChatRobot Health Check"

if (-not (Test-Path -LiteralPath $runner)) {
    throw "Bot runner not found: $runner"
}
if (-not (Test-Path -LiteralPath $hiddenRunner)) {
    throw "Hidden bot runner not found: $hiddenRunner"
}
if (-not (Test-Path -LiteralPath $healthRunner)) {
    throw "Bot health checker not found: $healthRunner"
}
if (-not (Test-Path -LiteralPath $hiddenHealthRunner)) {
    throw "Hidden health checker not found: $hiddenHealthRunner"
}

$arguments = '"{0}"' -f $hiddenRunner
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$taskSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $taskSettings `
    -Description "Start and supervise the qq-chatrobot FastAPI service at user logon." `
    -Force | Out-Null

$healthArguments = '"{0}"' -f $hiddenHealthRunner
$healthAction = New-ScheduledTaskAction -Execute "wscript.exe" -Argument $healthArguments -WorkingDirectory $projectRoot
$healthTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$healthSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $healthTaskName `
    -Action $healthAction `
    -Trigger $healthTrigger `
    -Settings $healthSettings `
    -Description "Restart the qq-chatrobot supervisor if its local health endpoint is unavailable." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
Write-Host "Installed and started scheduled task: $taskName" -ForegroundColor Green
Write-Host "Installed recovery task: $healthTaskName" -ForegroundColor Green
Write-Host "Health URL: http://127.0.0.1:8000/health/ready"
